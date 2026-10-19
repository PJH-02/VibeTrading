from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


QUERIES = {
    "legacy_delivery_pk": (
        "SELECT 1 FROM parquet_delivery "
        "WHERE stream_name=? AND stream_generation=? AND stream_sequence=? LIMIT 1",
        ("CRYPTO_DATA", "generation", 1),
    ),
    "legacy_event_id": (
        "SELECT 1 FROM parquet_delivery WHERE event_id=? LIMIT 1",
        ("event-id",),
    ),
    "compact_delivery_pk": (
        "SELECT 1 FROM parquet_delivery_compact "
        "WHERE stream_id=? AND stream_sequence=? LIMIT 1",
        (1, 1),
    ),
    "compact_event_key": (
        "SELECT 1 FROM parquet_delivery_compact WHERE event_key=? LIMIT 1",
        (b"x" * 32,),
    ),
    "compact_prune_order": (
        "SELECT stream_id,stream_sequence FROM parquet_delivery_compact "
        "WHERE persisted_ts_ns<? ORDER BY persisted_ts_ns,stream_id,stream_sequence LIMIT ?",
        (1, 100),
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Release 2 SQLite cost baseline")
    parser.add_argument("database", type=Path)
    parser.add_argument("--dbstat", action="store_true", help="scan dbstat pages; use only during an approved low-I/O window")
    args = parser.parse_args()
    path = args.database.resolve(strict=True)
    uri = f"file:{path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA cache_size=-8192")
    try:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = [
            {"name": row[0], "table": row[1], "sql": row[2]}
            for row in connection.execute(
                "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='index' ORDER BY tbl_name,name"
            )
        ]
        plans = {}
        for name, (sql, parameters) in QUERIES.items():
            table = "parquet_delivery_compact" if name.startswith("compact_") else "parquet_delivery"
            if table not in tables:
                plans[name] = "table absent"
                continue
            plans[name] = [
                str(row[3]) for row in connection.execute("EXPLAIN QUERY PLAN " + sql, parameters)
            ]
        result: dict[str, object] = {
            "database": str(path),
            "file_bytes": path.stat().st_size,
            "page_size": connection.execute("PRAGMA page_size").fetchone()[0],
            "page_count": connection.execute("PRAGMA page_count").fetchone()[0],
            "freelist_count": connection.execute("PRAGMA freelist_count").fetchone()[0],
            "tables": sorted(tables),
            "indexes": indexes,
            "query_plans": plans,
        }
        if args.dbstat:
            result["dbstat"] = [
                {"name": row[0], "pages": row[1], "bytes": row[2], "unused_bytes": row[3]}
                for row in connection.execute(
                    "SELECT name,count(*),sum(pgsize),sum(unused) FROM dbstat GROUP BY name ORDER BY sum(pgsize) DESC"
                )
            ]
        print(json.dumps(result, sort_keys=True))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
