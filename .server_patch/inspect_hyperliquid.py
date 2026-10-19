from collections import Counter
from pathlib import Path
import sqlite3

import pyarrow.parquet as pq


root = Path("/data/market_data")
connection = sqlite3.connect("/data/state/vibetrading.sqlite3")
files = [
    Path(row[0])
    for row in connection.execute(
        """
        SELECT path FROM parquet_manifest
        WHERE provider='hyperliquid'
          AND lifecycle_state='active'
          AND local_present=1
        ORDER BY path
        """
    )
    if Path(row[0]).exists()
]
counts = Counter()
symbols = Counter()
ids = Counter()
open_bars = 0
trade_or_book = 0
rows = 0
samples = []

for path in files:
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=4096):
        for row in batch.to_pylist():
            rows += 1
            event_type = str(row.get("event_type"))
            counts[event_type] += 1
            symbols[str(row.get("instrument_std_symbol"))] += 1
            event_id = str(row.get("event_id"))
            ids[event_id] += 1
            if event_type in {"trade", "order_book"}:
                trade_or_book += 1
            if event_type == "bar_1m" and not row.get("closed", False):
                open_bars += 1
            if len(samples) < 8:
                samples.append({
                    "event_type": event_type,
                    "symbol": row.get("instrument_std_symbol"),
                    "raw_symbol": row.get("instrument_raw_symbol"),
                    "source_ts_ns": row.get("source_ts_ns"),
                    "open": row.get("open"),
                    "close": row.get("close"),
                    "funding_rate": row.get("funding_rate"),
                })

print({
    "files": [(str(path), path.stat().st_size) for path in files],
    "rows": rows,
    "type_counts": dict(counts),
    "symbol_counts": dict(symbols),
    "duplicate_event_ids": sum(value - 1 for value in ids.values() if value > 1),
    "open_bars": open_bars,
    "trade_or_book": trade_or_book,
    "samples": samples,
})
