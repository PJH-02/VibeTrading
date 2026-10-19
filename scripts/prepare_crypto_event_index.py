"""Prepare and verify a NEW offline catalogue; never modifies the source DB.

This is not a server deployment or retention command. The operator must still
validate backup, disk budgets, NATS drainage, and exclusive writer ownership.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import struct
import time
from pathlib import Path

from vibetrading.event_index_runs import RECORD, MAX_RUN_ENTRIES, append_runs, ensure_schema
from vibetrading.delivery_recovery import attach_coverage, ensure_schema as ensure_recovery_schema


DELIVERY_SQL = ('SELECT rowid,stream_id,stream_sequence,event_key,file_rowid,persisted_ts_ns '
                'FROM parquet_delivery_compact ORDER BY rowid')
DELIVERY_RECORD = struct.Struct('>QQQ32sQQ')


def fingerprint_rows(rows) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(DELIVERY_RECORD.pack(*row))
    return digest.hexdigest()


def manifest_fingerprint(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for row in connection.execute('SELECT rowid,* FROM parquet_manifest ORDER BY rowid'):
        digest.update(json.dumps(tuple(row), ensure_ascii=True, separators=(',', ':')).encode())
        digest.update(b'\n')
    return digest.hexdigest()


def prepare(source_path: Path, target_path: Path, *, progress=None) -> dict:
    source_path = source_path.resolve(strict=True)
    target_path = target_path.resolve()
    if source_path == target_path or target_path.exists():
        raise ValueError('target must be a new file distinct from the source')
    target_path.parent.mkdir(parents=True, exist_ok=True)
    required = source_path.stat().st_size + 1024 * 1024 * 1024
    if shutil.disk_usage(target_path.parent).free < required:
        raise RuntimeError('offline preparation space budget is insufficient')
    started = time.monotonic()
    source = sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True, isolation_level=None)
    source.execute('PRAGMA query_only=ON')
    target = sqlite3.connect(target_path, isolation_level=None)
    try:
        if source.execute("SELECT 1 FROM sqlite_master WHERE name='event_index_runs'").fetchone():
            raise ValueError('source is already converted; reuse its verified artifact')
        if progress:
            progress({'phase': 'consistent_backup'})
        # Backup preserves manifest rowids. VACUUM is intentionally not used:
        # delivery.file_rowid refers to the manifest's implicit rowid.
        source.backup(target, pages=1024)
        target.execute('PRAGMA journal_mode=WAL')
        target.execute('PRAGMA synchronous=FULL')
        target.execute('PRAGMA cache_size=-65536')
        target.execute('PRAGMA wal_autocheckpoint=1000')
        before_manifest = manifest_fingerprint(source)
        if target.execute('''SELECT 1 FROM parquet_delivery_compact d
            LEFT JOIN parquet_manifest m ON m.rowid=d.file_rowid WHERE m.rowid IS NULL LIMIT 1''').fetchone():
            raise RuntimeError('source contains a missing canonical manifest reference')
        target.execute('DROP INDEX idx_delivery_compact_event')
        ensure_schema(target)
        ensure_recovery_schema(target)
        digest = hashlib.sha256()
        count = runs = 0
        cursor = source.execute(DELIVERY_SQL)
        while True:
            rows = cursor.fetchmany(MAX_RUN_ENTRIES)
            if not rows:
                break
            expected = {(bytes(row[3]), int(row[4])) for row in rows}
            for row in rows:
                digest.update(DELIVERY_RECORD.pack(*row))
            target.execute('BEGIN IMMEDIATE')
            try:
                created = append_runs(target, expected)
                if len(created) != 1:
                    raise RuntimeError('migration chunk must create exactly one run')
                attach_coverage(target, created[0], (tuple(row[1:]) for row in rows))
                target.execute('COMMIT')
            except Exception:
                target.execute('ROLLBACK')
                raise
            actual = set()
            for run_id in created:
                payload = bytes(target.execute('SELECT payload FROM event_index_run_payload WHERE run_id=?', (run_id,)).fetchone()[0])
                actual.update(RECORD.iter_unpack(payload))
            if actual != expected:
                raise RuntimeError('exact event/reference set differs after run commit')
            count += len(rows)
            runs += len(created)
            if progress and (runs % 16 == 0):
                progress({'phase': 'build_exact_runs', 'deliveries_verified': count, 'runs': runs})
        if progress:
            progress({'phase': 'verify_delivery_and_manifest_fingerprints', 'deliveries': count})
        after_digest = fingerprint_rows(target.execute(DELIVERY_SQL))
        if digest.hexdigest() != after_digest or before_manifest != manifest_fingerprint(target):
            raise RuntimeError('delivery rows or canonical manifest rowids changed')
        check = [row[0] for row in target.execute('PRAGMA quick_check')]
        if check != ['ok']:
            raise RuntimeError(f'prepared catalogue failed quick_check: {check}')
        # Only a fully verified artifact receives the format marker. Startup
        # refuses a populated run catalogue without this marker.
        target.execute('BEGIN IMMEDIATE')
        target.execute("INSERT OR REPLACE INTO runtime_metadata VALUES('event_index_format','immutable-runs-v1',?)", (time.time_ns(),))
        target.execute("INSERT OR REPLACE INTO runtime_metadata VALUES('event_index_migration_delivery_sha256',?,?)", (after_digest, time.time_ns()))
        target.execute('COMMIT')
        checkpoint = list(target.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone())
        if checkpoint[0] != 0:
            raise RuntimeError('offline candidate checkpoint incomplete')
        return {'source': str(source_path), 'target': str(target_path), 'delivery_rows': count,
                'runs': runs, 'delivery_sha256': after_digest, 'manifest_sha256': before_manifest,
                'quick_check': check, 'checkpoint': checkpoint, 'bytes': target_path.stat().st_size,
                'elapsed_seconds': round(time.monotonic() - started, 3),
                'status': 'offline_candidate_verified_not_deployed'}
    finally:
        target.close()
        source.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = prepare(args.source, args.target, progress=lambda value: print(json.dumps(value), flush=True))
    if args.report:
        args.report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
