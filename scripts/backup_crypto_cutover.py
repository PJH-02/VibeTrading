"""One-shot consistent backup for the stopped pre-R2 crypto store.

Mount the original crypto_state read-only at /source and reserved scratch
at /backup. This never removes or checkpoints the original DB/WAL.
After separately authorizing export, --archive-existing reuses a completed
--local-only snapshot; it does not repeat the SQLite backup or quick_check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time

from vibetrading.settings import get_settings
from storage.storage_backend import archive_backend

SOURCE = Path('/source/vibetrading.sqlite3')
TARGET = Path('/backup/current-crypto-pre-r2.sqlite3')
RATE = 8 * 1024 * 1024
MIN_FREE = max(512 * 1024 * 1024, int(os.environ.get('BACKUP_SCRATCH_RESERVE_BYTES', str(512 * 1024 * 1024))))


def identity():
    return {p.name: [p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino]
            for p in (SOURCE, Path(str(SOURCE) + '-wal'))}


def completed_local_snapshot(before):
    """Validate the prior receipt before opening any archive connection."""
    receipt = TARGET.with_suffix('.local.receipt.json')
    if TARGET.is_symlink() or receipt.is_symlink():
        raise RuntimeError('snapshot/receipt symlink rejected')
    result = json.loads(receipt.read_text())
    if (result.get('consistent_current_db_backup') is not True
            or result.get('scope') != 'same-server local snapshot only'
            or result.get('quick_check') != 'ok'
            or result.get('source_deleted') is not False
            or result.get('remote_full_sha256_verified') is not False
            or result.get('snapshot') != str(TARGET)
            or result.get('source_identity') != before
            or type(result.get('snapshot_bytes')) is not int
            or result.get('snapshot_bytes', 0) <= 0
            or result.get('snapshot_bytes') != TARGET.stat().st_size
            or not re.fullmatch(r'[0-9a-f]{64}', str(result.get('sha256', '')))):
        raise RuntimeError('completed local snapshot receipt mismatch')
    return result


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--local-only', action='store_true', help='create a same-server snapshot; never initialize archive or export data')
    mode.add_argument('--archive-existing', action='store_true', help='export a verified local snapshot after separate authorization; no new SQLite backup')
    args = parser.parse_args()
    if TARGET.exists() and not args.archive_existing:
        raise RuntimeError('backup target already exists; inspect prior operation before retry')
    if not args.local_only and TARGET.with_suffix('.receipt.json').exists():
        raise RuntimeError('remote receipt already exists; inspect completed export before retry')
    settings = None
    if not args.local_only and not args.archive_existing:
        settings = get_settings()
        if settings.archive.backend != 'ncloud_storage' or settings.archive.prefix != 'vibetrading/crypto':
            raise RuntimeError('existing Ncloud crypto archive configuration required')
    before = identity()
    existing = completed_local_snapshot(before) if args.archive_existing else None
    start = time.monotonic()
    phase_start = start
    phase_bytes = 0
    last_report = start

    def pace(count, phase):
        nonlocal phase_bytes, last_report
        phase_bytes += count
        if time.monotonic() - start > 2400:
            raise RuntimeError('bounded backup time exceeded')
        delay = phase_start + phase_bytes / RATE - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        while True:
            line = next(s for s in Path('/proc/pressure/io').read_text().splitlines() if s.startswith('full '))
            pressure = float(dict(p.split('=', 1) for p in line.split()[1:])['avg10'])
            if pressure <= 5:
                break
            if time.monotonic() - start > 2400:
                raise RuntimeError('I/O pressure did not recover')
            time.sleep(1)
        if shutil.disk_usage(TARGET.parent).free < MIN_FREE:
            raise RuntimeError('scratch reserve reached')
        if time.monotonic() - last_report >= 20:
            print(json.dumps({'phase': phase, 'bytes': phase_bytes, 'io_full_avg10': pressure}), flush=True)
            last_report = time.monotonic()

    if not args.archive_existing:
        with sqlite3.connect(SOURCE.as_uri() + '?mode=ro', uri=True) as source:
            source.execute('PRAGMA query_only=ON')
            page_size = source.execute('PRAGMA page_size').fetchone()[0]
            pages = source.execute('PRAGMA page_count').fetchone()[0]
            if shutil.disk_usage(TARGET.parent).free < pages * page_size + MIN_FREE:
                raise RuntimeError('insufficient scratch space')
            previous_done = 0

            def progress(status, remaining, total):
                nonlocal previous_done
                done = total - remaining
                pace(max(0, done - previous_done) * page_size, 'consistent_sqlite_backup')
                previous_done = done

            with sqlite3.connect(TARGET) as target:
                source.backup(target, pages=256, progress=progress)
                target.set_progress_handler(lambda: (pace(0, 'backup_quick_check'), 0)[1], 10000)
                if target.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise RuntimeError('backup quick_check failed')
                target.set_progress_handler(None, 0)
                # A standalone rollback file must not depend on a destination WAL.
                target.execute('PRAGMA journal_mode=DELETE')
            target.close()
        source.close()
        with TARGET.open('rb') as handle:
            os.fsync(handle.fileno())
    if before != identity():
        raise RuntimeError('source changed during backup; collector/storage stop gate violated')
    target_identity = [TARGET.stat().st_size, TARGET.stat().st_mtime_ns, TARGET.stat().st_ino]
    digest = hashlib.sha256()
    phase_start, phase_bytes = time.monotonic(), 0
    with TARGET.open('rb') as handle:
        while chunk := handle.read(256 * 1024):
            pace(len(chunk), 'hash_consistent_backup')
            digest.update(chunk)
    sha = digest.hexdigest()
    size = TARGET.stat().st_size
    if target_identity != [TARGET.stat().st_size, TARGET.stat().st_mtime_ns, TARGET.stat().st_ino]:
        raise RuntimeError('snapshot changed during verification')
    if existing is not None and sha != existing['sha256']:
        raise RuntimeError('snapshot checksum differs from completed local receipt')
    if before != identity():
        raise RuntimeError('source changed during snapshot verification')
    if args.local_only:
        if before != identity():
            raise RuntimeError('source changed during local snapshot verification')
        result = {'consistent_current_db_backup': True, 'scope': 'same-server local snapshot only',
                  'source_identity': before, 'snapshot': str(TARGET), 'snapshot_bytes': size,
                  'sha256': sha, 'quick_check': 'ok', 'source_deleted': False,
                  'remote_full_sha256_verified': False, 'scratch_reserve_bytes': MIN_FREE,
                  'elapsed_seconds': round(time.monotonic() - start, 1)}
        receipt = TARGET.with_suffix('.local.receipt.json')
        with receipt.open('xb') as handle:
            handle.write((json.dumps(result, indent=2, sort_keys=True) + '\n').encode())
            handle.flush()
            os.fsync(handle.fileno())
        directory = os.open(str(TARGET.parent), os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        print(json.dumps(result), flush=True)
        return
    if settings is None:
        settings = get_settings()
    if settings.archive.backend != 'ncloud_storage' or settings.archive.prefix != 'vibetrading/crypto':
        raise RuntimeError('existing Ncloud crypto archive configuration required')
    key = f'{settings.archive.prefix}/recovery/pre-r2-20260930/current-crypto-{sha}.sqlite3'
    backend = archive_backend(settings)
    phase_start, phase_bytes = time.monotonic(), 0
    backend._client.upload_file(str(TARGET), backend.bucket, key,
        ExtraArgs={'Metadata': {'sha256': sha}}, Config=backend._upload_config,
        Callback=lambda count: pace(count, 'upload_consistent_backup'))
    phase_start, phase_bytes = time.monotonic(), 0
    response = backend._client.get_object(Bucket=backend.bucket, Key=key)
    remote_hash = hashlib.sha256()
    try:
        while chunk := response['Body'].read(256 * 1024):
            pace(len(chunk), 'verify_remote_backup')
            remote_hash.update(chunk)
    finally:
        response['Body'].close()
    if (phase_bytes != size or remote_hash.hexdigest() != sha or before != identity()
            or target_identity != [TARGET.stat().st_size, TARGET.stat().st_mtime_ns, TARGET.stat().st_ino]):
        raise RuntimeError('remote checksum/length or source identity mismatch')
    result = {'consistent_current_db_backup': True, 'source_identity': before,
              'snapshot': str(TARGET), 'snapshot_bytes': size, 'sha256': sha,
              'remote_key': key, 'remote_full_sha256_verified': True,
              'quick_check': 'ok', 'source_deleted': False,
              'reused_local_snapshot': args.archive_existing,
              'elapsed_seconds': round(time.monotonic() - start, 1)}
    payload = json.dumps(result, indent=2, sort_keys=True).encode()
    backend.put_bytes(payload, key + '.receipt.json', sha256=hashlib.sha256(payload).hexdigest())
    receipt = TARGET.with_suffix('.receipt.json')
    with receipt.open('xb') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    directory = os.open(str(TARGET.parent), os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
