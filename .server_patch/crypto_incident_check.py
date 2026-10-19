import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

root = Path('/srv/vibetrading-data/runtime_data')
metrics = root / 'reports/crypto_capacity_20260926.jsonl'
last = json.loads(metrics.read_text().splitlines()[-1])
print('time_utc', last['time_utc'])
print('disk_free_gib', round(last['disk']['free'] / 1024**3, 2))
print('host_available_gib', round(last['host_memory']['MemAvailable'] / 1024**3, 2))
print('crypto_nats', last['nats'].get('CRYPTO_DATA'))
for name in ('binance-perp', 'bitget-perp', 'bybit-perp', 'storage', 'maintenance'):
    print('health', name, last['health'].get(name))

gap_db = root / 'state/collection_gaps.sqlite3'
with sqlite3.connect(f'file:{gap_db}?mode=ro', uri=True) as db:
    for row in db.execute('SELECT provider, started_ns, ended_ns, reason FROM gaps WHERE provider LIKE ? ORDER BY id DESC LIMIT 12', ('%perp',)):
        provider, start, end, reason = row
        iso = lambda n: datetime.fromtimestamp(n / 1e9, timezone.utc).isoformat() if n else 'open'
        print('gap', provider, iso(start), iso(end), reason)

archive_db = root / 'crypto_state/vibetrading.sqlite3'
with sqlite3.connect(f'file:{archive_db}?mode=ro', uri=True) as db:
    for row in db.execute('SELECT lifecycle_state, upload_status, COUNT(*), SUM(byte_size) FROM parquet_manifest GROUP BY 1, 2'):
        print('archive', row)
