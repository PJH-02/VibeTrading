from __future__ import annotations

import argparse
import json
import os
import sqlite3


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only crypto delivery ledger verification")
    parser.add_argument("database")
    parser.add_argument("--stream-only", action="store_true")
    args = parser.parse_args()

    database = os.path.abspath(args.database)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        result = {
            "database": database,
            "bytes": os.path.getsize(database),
            "streams": [
                {
                    "stream_id": row[0],
                    "stream_name": row[1],
                    "stream_generation": row[2],
                    "ack_floor": row[3],
                }
                for row in connection.execute(
                    "SELECT stream_id, stream_name, stream_generation, ack_floor FROM delivery_stream"
                )
            ],
        }
        if not args.stream_only:
            result.update(
                {
                    "quick_check": connection.execute("PRAGMA quick_check").fetchone()[0],
                    "compact_rows": connection.execute(
                        "SELECT count(*) FROM parquet_delivery_compact"
                    ).fetchone()[0],
                    "legacy_rows": (
                        connection.execute("SELECT count(*) FROM parquet_delivery").fetchone()[0]
                        if "parquet_delivery" in tables
                        else 0
                    ),
                }
            )
    finally:
        connection.close()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
