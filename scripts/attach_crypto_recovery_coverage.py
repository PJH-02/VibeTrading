"""Add exact recovery blocks to an OFFLINE verified pre-coverage candidate.

Only the separately prepared local candidate is eligible. Existing blocks are
validated and reused; neither delivery nor manifest rows are modified.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

from vibetrading.delivery_recovery import attach_coverage, decode_records, ensure_schema
from vibetrading.event_index_runs import MAX_RUN_ENTRIES, RECORD


def attach(path: Path, progress=None) -> dict:
    conn = sqlite3.connect(path.resolve(strict=True).as_uri() + '?mode=rw', uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    started = time.monotonic()
    try:
        proof = conn.execute("SELECT value FROM runtime_metadata WHERE key='event_index_migration_delivery_sha256'").fetchone()
        if proof is None:
            raise RuntimeError('a verified offline migration candidate is required')
        if conn.execute("SELECT 1 FROM runtime_metadata WHERE key='benchmark_only'").fetchone():
            raise RuntimeError('benchmark databases cannot become deployment candidates')
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('PRAGMA cache_size=-65536')
        conn.execute('PRAGMA wal_autocheckpoint=1000')
        ensure_schema(conn)
        runs = list(conn.execute('SELECT run_id FROM event_index_runs ORDER BY run_id'))
        after = count = created = 0
        for run in runs:
            conn.execute('BEGIN IMMEDIATE')
            try:
                rows = conn.execute('''SELECT rowid,stream_id,stream_sequence,event_key,file_rowid,persisted_ts_ns
                  FROM parquet_delivery_compact WHERE rowid>? ORDER BY rowid LIMIT ?''', (after, MAX_RUN_ENTRIES)).fetchall()
                if not rows:
                    raise RuntimeError('candidate changed after its offline migration')
                payload = conn.execute('SELECT payload FROM event_index_run_payload WHERE run_id=?', (run[0],)).fetchone()[0]
                exact = {(row[3], row[4]) for row in rows}
                if set(RECORD.iter_unpack(payload)) != exact:
                    raise RuntimeError('candidate delivery chunk differs from its original exact run')
                coverage = conn.execute('SELECT * FROM crypto_delivery_recovery WHERE run_id=?', (run[0],)).fetchone()
                expected = sorted((tuple(row[1:]) for row in rows), key=lambda row: row[:2])
                if coverage is None:
                    attach_coverage(conn, run[0], expected)
                    created += 1
                elif decode_records(coverage['payload'], coverage['payload_sha256'], coverage['row_count']) != expected:
                    raise RuntimeError('existing recovery coverage is inconsistent')
                conn.execute('COMMIT')
            except Exception:
                conn.execute('ROLLBACK')
                raise
            after = rows[-1][0]
            count += len(rows)
            if progress and (int(run[0]) % 16 == 0):
                progress({'rows_verified': count, 'run_id': run[0], 'blocks_created': created})
        if conn.execute('SELECT 1 FROM parquet_delivery_compact WHERE rowid>? LIMIT 1', (after,)).fetchone():
            raise RuntimeError('candidate has uncovered delivery rows')
        conn.execute("INSERT OR REPLACE INTO runtime_metadata VALUES('event_recovery_migration_complete',?,?)",
                     (json.dumps({'delivery_rows': count, 'blocks': len(runs), 'delivery_sha256': proof[0]}), time.time_ns()))
        checkpoint = list(conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone())
        if checkpoint[0] != 0:
            raise RuntimeError('offline recovery coverage checkpoint incomplete')
        return {'rows_verified': count, 'blocks': len(runs), 'blocks_created': created,
                'checkpoint': checkpoint, 'bytes': path.stat().st_size,
                'seconds': round(time.monotonic()-started, 3), 'production_deployed': False}
    finally:
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = attach(args.candidate, progress=lambda value: print(json.dumps(value), flush=True))
    args.report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)
