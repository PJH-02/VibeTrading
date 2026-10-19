"""Compare NEW disposable clones at production size; no source writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from scripts.compare_ledger_layouts import wal_state
from vibetrading.event_index_runs import ExactEventRuns, append_runs
from vibetrading.delivery_recovery import append_delivery_runs


def compare(source: Path, destination: Path, *, runs: bool, incoming: int, recovery: bool = False) -> dict:
    if destination.exists():
        raise ValueError('cost comparison refuses to overwrite an existing artifact')
    original = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    connection = sqlite3.connect(destination, isolation_level=None)
    try:
        original.backup(connection, pages=1024)
        original.close()
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('PRAGMA cache_size=-65536')
        connection.execute('PRAGMA wal_autocheckpoint=1000')
        connection.execute("INSERT OR REPLACE INTO runtime_metadata VALUES('benchmark_only','true',?)", (time.time_ns(),))
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        index = ExactEventRuns(connection) if runs else None
        started = time.monotonic()
        if index is not None:
            assert not index.lookup([hashlib.sha256(b'benchmark-prewarm-new-key').digest()])
        startup_seconds = time.monotonic() - started
        sequence = connection.execute('SELECT max(stream_sequence) FROM parquet_delivery_compact WHERE stream_id=1').fetchone()[0]
        file_rowid = connection.execute('SELECT max(rowid) FROM parquet_manifest').fetchone()[0]
        previous = (b'', 0)
        frames = 0
        timings = []
        for first in range(0, incoming, 8192):
            keys = [hashlib.sha256(f'new-event-run-cost:{i}'.encode()).digest()
                    for i in range(first, min(first + 8192, incoming))]
            begin = time.monotonic()
            for offset in range(0, len(keys), 128):
                batch = keys[offset:offset + 128]
                if index is not None:
                    assert not index.lookup(batch)
                else:
                    marks = ','.join('?' for _ in batch)
                    assert not connection.execute(
                        f'SELECT event_key FROM parquet_delivery_compact WHERE event_key IN ({marks})', batch).fetchall()
            lookup_seconds = time.monotonic() - begin
            begin = time.monotonic()
            connection.execute('BEGIN IMMEDIATE')
            deliveries = [(1, sequence + first + i + 1, key, file_rowid, 1790680000000000000)
                          for i, key in enumerate(keys)]
            connection.executemany('INSERT INTO parquet_delivery_compact VALUES(?,?,?,?,?)',
                                   deliveries)
            if index is not None:
                if recovery:
                    append_delivery_runs(connection, deliveries)
                else:
                    append_runs(connection, ((key, file_rowid) for key in keys))
            connection.execute('COMMIT')
            commit_seconds = time.monotonic() - begin
            current = wal_state(destination)
            frames += current[1] - previous[1] if current[0] == previous[0] else current[1]
            previous = current
            timings.append({'rows': len(keys), 'lookup_seconds': lookup_seconds, 'commit_seconds': commit_seconds})
        started = time.monotonic()
        checkpoint = list(connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone())
        checkpoint_seconds = time.monotonic() - started
        if index is not None:
            assert index.lookup(keys[:16]) == {key: file_rowid for key in keys[:16]}
        return {'format': 'immutable-runs-v1' if runs else 'btree', 'incoming_rows': incoming,
                'sqlite_version': sqlite3.sqlite_version, 'cache_kib': 65536, 'autocheckpoint_pages': 1000,
                'synchronous': 'FULL', 'startup_seconds': startup_seconds, 'batches': timings,
                'wal_frames': frames, 'wal_bytes_per_delivery': frames * 4120 / incoming,
                'final_checkpoint': checkpoint, 'checkpoint_seconds': checkpoint_seconds,
                'final_bytes': destination.stat().st_size,
                'global_filter_bytes': index.global_filter_bytes if index else 0,
                'exact_recovery_coverage': recovery,
                'limitations': 'Local clones, logical WAL cost; not physical production IOPS or full NATS gate.'}
    finally:
        connection.close()
        original.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--btree', type=Path, required=True)
    parser.add_argument('--runs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rows', type=int, default=32768)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for source, use_runs in ((args.btree, False), (args.runs, True)):
        name = 'runs' if use_runs else 'btree'
        result = compare(source, args.output / (name + '-benchmark-only.db'), runs=use_runs, incoming=args.rows)
        (args.output / (name + '.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
