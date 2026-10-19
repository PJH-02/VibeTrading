import json
import time

from vibetrading.archive import ArchiveMaintenance


maintenance = ArchiveMaintenance()
rows = maintenance.store.deletion_candidates(
    older_than_ns=time.time_ns() - 24 * 3600 * 1_000_000_000,
    force=True,
    limit=10_000,
)
print(json.dumps({
    "deletion_candidates": len(rows),
    "bytes": sum(int(row["byte_size"]) for row in rows),
    "states": sorted({(row["lifecycle_state"], row["upload_status"]) for row in rows}),
}))
