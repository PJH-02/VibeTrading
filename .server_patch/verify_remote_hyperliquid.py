from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow.parquet as pq

from vibetrading.parquet_store import file_sha256
from vibetrading.settings import get_settings
from vibetrading.sqlite_store import RuntimeStore
from vibetrading.storage_backend import archive_backend


settings = get_settings()
store = RuntimeStore(settings)
backend = archive_backend(settings)
rows = [
    row for row in store.list_manifest(active_only=True)
    if row["provider"] == "hyperliquid" and row["upload_status"] == "remote_verified"
]
counts = Counter()
symbols = Counter()
event_ids = Counter()
open_bars = 0
trade_or_book = 0
bytes_downloaded = 0

with TemporaryDirectory(prefix="hyperliquid-remote-verify-") as temp_dir:
    for manifest in rows:
        destination = Path(temp_dir) / f"{manifest['file_id']}.parquet"
        backend.download_file(manifest["remote_key"], destination)
        if destination.stat().st_size != int(manifest["byte_size"]):
            raise RuntimeError(f"size mismatch file_id={manifest['file_id']}")
        if file_sha256(destination) != manifest["file_sha256"]:
            raise RuntimeError(f"sha256 mismatch file_id={manifest['file_id']}")
        bytes_downloaded += destination.stat().st_size
        for batch in pq.ParquetFile(destination).iter_batches(batch_size=4096):
            for event in batch.to_pylist():
                event_type = str(event.get("event_type"))
                counts[event_type] += 1
                symbols[str(event.get("instrument_std_symbol"))] += 1
                event_ids[str(event.get("event_id"))] += 1
                if event_type in {"trade", "order_book"}:
                    trade_or_book += 1
                if event_type == "bar_1m" and not event.get("closed", False):
                    open_bars += 1

print({
    "remote_files": len(rows),
    "bytes_downloaded": bytes_downloaded,
    "rows": sum(counts.values()),
    "type_counts": dict(counts),
    "symbol_counts": dict(symbols),
    "duplicate_event_ids": sum(value - 1 for value in event_ids.values() if value > 1),
    "open_bars": open_bars,
    "trade_or_book": trade_or_book,
})
