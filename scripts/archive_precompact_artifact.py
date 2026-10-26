"""Preserve one inactive precompact artifact; never opens SQLite or deletes it.

This preserves exact bytes, not a claim that a lone historical database has a
consistent snapshot. The current DB/WAL and prepared candidate remain separate.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from vibetrading.settings import get_settings
from storage.storage_backend import archive_backend


SOURCE = Path('/data/crypto_state/vibetrading.sqlite3.precompact-20260928T021539Z')
EXPECTED_BYTES = 1763291136
RATE = 8 * 1024 * 1024


def main():
    settings = get_settings()
    if settings.archive.backend != 'ncloud_storage' or settings.archive.prefix != 'vibetrading/crypto':
        raise RuntimeError('expected existing Ncloud crypto archive configuration')
    before = SOURCE.stat()
    if before.st_size != EXPECTED_BYTES:
        raise RuntimeError('inactive artifact identity changed')
    started = time.monotonic()
    phase_start = started
    phase_bytes = 0
    last_report = started

    def pace(count, phase):
        nonlocal phase_bytes, last_report
        phase_bytes += count
        now = time.monotonic()
        if now-started > 1800:
            raise RuntimeError('archive operation exceeded its bounded time budget')
        expected = phase_start + phase_bytes / RATE
        if expected > now:
            time.sleep(min(1.0, expected-now))
        while True:
            full = next(line for line in Path('/proc/pressure/io').read_text().splitlines() if line.startswith('full '))
            pressure = float(dict(part.split('=', 1) for part in full.split()[1:])['avg10'])
            if pressure <= 5.0:
                break
            if time.monotonic()-started > 1800:
                raise RuntimeError('archive yielded too long to host I/O pressure')
            time.sleep(1)
        if time.monotonic()-last_report >= 20:
            print(json.dumps({'phase': phase, 'bytes': phase_bytes, 'total_bytes': before.st_size,
                              'io_full_avg10': pressure}), flush=True)
            last_report = time.monotonic()

    digest = hashlib.sha256()
    with SOURCE.open('rb') as handle:
        while chunk := handle.read(256 * 1024):
            pace(len(chunk), 'hash_inactive_artifact')
            digest.update(chunk)
    sha = digest.hexdigest()
    key = f'{settings.archive.prefix}/recovery/pre-r2-20260930/{SOURCE.name}-{sha}.bytes'
    backend = archive_backend(settings)
    # Reuse a completed upload after interruption, but verify every remote byte.
    try:
        head = backend._client.head_object(Bucket=backend.bucket, Key=key)
    except backend._client.exceptions.ClientError as exc:
        if str(exc.response.get('Error', {}).get('Code')) not in {'404', 'NoSuchKey', 'NotFound'}:
            raise
        head = None
    if head is None:
        phase_start = time.monotonic()
        phase_bytes = 0
        backend._client.upload_file(str(SOURCE), backend.bucket, key,
            ExtraArgs={'Metadata': {'sha256': sha}}, Config=backend._upload_config,
            Callback=lambda count: pace(count, 'upload_inactive_artifact'))
    phase_start = time.monotonic()
    phase_bytes = 0
    response = backend._client.get_object(Bucket=backend.bucket, Key=key)
    remote_hash = hashlib.sha256()
    try:
        while chunk := response['Body'].read(256 * 1024):
            pace(len(chunk), 'verify_remote_bytes')
            remote_hash.update(chunk)
    finally:
        response['Body'].close()
    after = SOURCE.stat()
    if (after.st_size, after.st_mtime_ns, after.st_ino) != (before.st_size, before.st_mtime_ns, before.st_ino):
        raise RuntimeError('inactive source changed during archival')
    if phase_bytes != before.st_size or remote_hash.hexdigest() != sha:
        raise RuntimeError('remote artifact checksum or size mismatch')
    result = {'artifact': str(SOURCE), 'source_bytes': before.st_size, 'source_mtime_ns': before.st_mtime_ns,
              'remote_key': key, 'sha256': sha, 'remote_full_sha256_verified': True,
              'source_deleted': False, 'consistent_current_db_backup': False,
              'elapsed_seconds': round(time.monotonic()-started, 1)}
    payload = json.dumps(result, sort_keys=True, indent=2).encode()
    backend.put_bytes(payload, key+'.receipt.json', sha256=hashlib.sha256(payload).hexdigest())
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
