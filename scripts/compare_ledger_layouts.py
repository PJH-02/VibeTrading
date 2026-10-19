"""Local synthetic cost attribution; never accepts or opens a production DB.

The no-event-index case is an ablation, NOT a deployable deduplication design.
WAL frames measure SQLite logical page writes, not physical device IOPS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import struct
import time
from pathlib import Path


def wal_state(path: Path) -> tuple[bytes, int]:
    with Path(str(path) + "-wal").open("rb") as handle:
        header = handle.read(32)
    with Path(str(path) + "-shm").open("rb") as handle:
        shm = handle.read(48)
    return header[16:24], struct.unpack_from("=I", shm, 16)[0]


def rows(start: int, end: int):
    for seq in range(start, end):
        yield (1, seq, hashlib.sha256(f"synthetic:{seq}".encode()).digest(),
               seq // 2048 + 1, 1_790_000_000_000_000_000 + seq // 8192 * 1_000_000_000)


def measure(directory: Path, layout: str, seed: int, incoming: int, reuse_seed: bool = False) -> dict:
    path = directory / (layout + ".sqlite3")
    existing = path.exists()
    if existing and not reuse_seed:
        raise RuntimeError(f"refusing to overwrite existing benchmark: {path}")
    con = sqlite3.connect(path, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.execute("PRAGMA cache_size=-65536")
    con.execute("PRAGMA wal_autocheckpoint=1000")
    if existing and con.execute('SELECT count(*) FROM delivery').fetchone()[0] != seed:
        raise RuntimeError('existing synthetic seed row count differs')
    con.execute("CREATE TABLE IF NOT EXISTS delivery(stream_id INTEGER NOT NULL, stream_sequence INTEGER NOT NULL, "
                "event_key BLOB NOT NULL, file_rowid INTEGER NOT NULL, persisted_ts_ns INTEGER NOT NULL, "
                "PRIMARY KEY(stream_id,stream_sequence))" +
                (" WITHOUT ROWID" if layout == "without_rowid" else ""))
    if layout != "no_event_index_ablation":
        con.execute("CREATE INDEX IF NOT EXISTS event_lookup ON delivery(event_key)")
    con.execute("CREATE INDEX IF NOT EXISTS prune_order ON delivery(persisted_ts_ns,stream_id,stream_sequence)")
    started = time.monotonic()
    for first in range(seed + 1 if existing else 1, seed + 1, 8192):
        con.execute("BEGIN IMMEDIATE")
        con.executemany("INSERT INTO delivery VALUES(?,?,?,?,?)", rows(first, min(first + 8192, seed + 1)))
        con.execute("COMMIT")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    seed_seconds = time.monotonic() - started
    base_bytes = path.stat().st_size
    writes = 0
    previous = (b"", 0)
    commits = []
    for first in range(seed + 1, seed + incoming + 1, 8192):
        batch = list(rows(first, min(first + 8192, seed + incoming + 1)))
        started = time.monotonic()
        con.execute("BEGIN IMMEDIATE")
        con.executemany("INSERT INTO delivery VALUES(?,?,?,?,?)", batch)
        con.execute("COMMIT")
        commits.append(time.monotonic() - started)
        current = wal_state(path)
        writes += current[1] - previous[1] if current[0] == previous[0] else current[1]
        previous = current
    started = time.monotonic()
    checkpoint = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    checkpoint_seconds = time.monotonic() - started
    pages = [dict(zip(("name", "pages", "bytes"), row)) for row in con.execute(
        "SELECT name,count(*),sum(pgsize) FROM dbstat GROUP BY name")]
    result = dict(layout=layout, seed_rows=seed, appended_rows=incoming, reused_seed=existing,
                  sqlite_version=sqlite3.sqlite_version,
                  cache_kib=65536, autocheckpoint_pages=1000, synchronous="FULL",
                  seed_seconds=seed_seconds, base_bytes=base_bytes,
                  append_commit_seconds=commits, wal_frames=writes,
                  wal_bytes_per_delivery=writes * 4120 / incoming,
                  checkpoint_result=list(checkpoint), checkpoint_seconds=checkpoint_seconds,
                  final_bytes=path.stat().st_size, dbstat=pages,
                  limitations="Synthetic local disk; not a production throughput or safety gate.")
    con.close()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--seed-rows", type=int, default=2_000_000)
    parser.add_argument("--append-rows", type=int, default=32768)
    parser.add_argument("--reuse-seed", action="store_true")
    args = parser.parse_args()
    args.output_directory.mkdir(parents=True, exist_ok=True)
    for layout in ("rowid", "without_rowid", "no_event_index_ablation"):
        result = measure(args.output_directory, layout, args.seed_rows, args.append_rows, args.reuse_seed)
        (args.output_directory / (layout + ".json")).write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
