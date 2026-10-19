import json
import os
import sqlite3
from collections import Counter
from pathlib import Path


root = Path("/data/market_data")
connection = sqlite3.connect("/data/state/vibetrading.sqlite3")
connection.row_factory = sqlite3.Row

groups = [
    dict(row)
    for row in connection.execute(
        """
        SELECT lifecycle_state, upload_status, local_present,
               COUNT(*) AS files, COALESCE(SUM(byte_size), 0) AS bytes
        FROM parquet_manifest
        GROUP BY lifecycle_state, upload_status, local_present
        ORDER BY bytes DESC
        """
    )
]
replacement_verified = dict(
    connection.execute(
        """
        SELECT COUNT(*) AS files, COALESCE(SUM(source.byte_size), 0) AS bytes
        FROM parquet_manifest AS source
        JOIN parquet_manifest AS replacement ON replacement.file_id = source.superseded_by
        WHERE source.lifecycle_state = 'superseded'
          AND source.local_present = 1
          AND replacement.upload_status = 'remote_verified'
          AND replacement.remote_manifest_key IS NOT NULL
        """
    ).fetchone()
)

table_rows = {}
for table in ("parquet_manifest", "parquet_delivery", "universe_manifest"):
    table_rows[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

registered = {str(row[0]) for row in connection.execute("SELECT path FROM parquet_manifest")}
physical = Counter()
for path in root.glob("**/*"):
    if not path.is_file():
        continue
    size = path.stat().st_size
    physical["files"] += 1
    physical["bytes"] += size
    if str(path) not in registered:
        physical["unregistered_files"] += 1
        physical["unregistered_bytes"] += size
    if path.name.endswith(".orphan"):
        physical["orphan_files"] += 1
        physical["orphan_bytes"] += size
    if path.name.endswith(".tmp"):
        physical["tmp_files"] += 1
        physical["tmp_bytes"] += size

print(json.dumps({
    "database_bytes": os.path.getsize("/data/state/vibetrading.sqlite3"),
    "table_rows": table_rows,
    "manifest_groups": groups,
    "superseded_sources_with_verified_replacement": replacement_verified,
    "physical": dict(physical),
}, indent=2))
