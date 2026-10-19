"""Read a bounded set of fresh committed Parquet files, never prices."""
from collections import Counter
import datetime
import json
from pathlib import Path
import sqlite3
import pyarrow.parquet as pq

cutoff = int(datetime.datetime(2026, 10, 2, 5, 55, 12, tzinfo=datetime.timezone.utc).timestamp() * 1e9)
db = sqlite3.connect('file:/data/crypto_state/vibetrading.sqlite3?mode=ro', uri=True, timeout=3)
db.execute('PRAGMA query_only=ON')
streams = db.execute('SELECT stream_name,stream_generation FROM delivery_stream').fetchall()
floors = db.execute('SELECT stream_name,stream_generation,durable,ack_floor FROM delivery_confirmed_floor').fetchall()
files = db.execute('SELECT path,created_ts_ns FROM parquet_manifest WHERE local_present=1 AND created_ts_ns>=? ORDER BY created_ts_ns DESC LIMIT 128', (cutoff,)).fetchall()
db.close()
counts = Counter()
read = missing = 0
file_timestamps = []
venues = {'BINANCE', 'BITGET', 'BYBIT'}
symbols = {'BTCUSDT', 'ETHUSDT', 'SOLUSDT'}
types = {'trade', 'order_book', 'funding_rate', 'open_interest'}
for path, created in files:
    try:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=2048, columns=['venue', 'instrument_std_symbol', 'event_type']):
            for row in batch.to_pylist():
                counts[(row['venue'], row['instrument_std_symbol'], row['event_type'])] += 1
        file_timestamps.append(created)
        read += 1
    except FileNotFoundError:
        missing += 1  # An archive worker may reclaim an already-verified file.
unexpected = [(v, s, t) for v, s, t in counts if v.upper() not in venues or s not in symbols or t not in types]
present = {(v.upper(), s, t) for v, s, t in counts}
absent = [(v, s, t) for v in sorted(venues) for s in sorted(symbols) for t in sorted(types) if (v, s, t) not in present]
result = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'cutoff_ns': cutoff,
          'files_read': read, 'files_reclaimed_during_read': missing,
          'file_created_range_ns': [min(file_timestamps), max(file_timestamps)] if file_timestamps else [],
          'streams': streams, 'confirmed_floors': floors,
          'counts': [dict(venue=v, symbol=s, event_type=t, rows=n) for (v,s,t), n in sorted(counts.items())],
          'missing_combinations': absent, 'unexpected_combinations': unexpected}
print(json.dumps(result), flush=True)
assert read > 0 and not absent and not unexpected, 'Fresh stream coverage incomplete'
