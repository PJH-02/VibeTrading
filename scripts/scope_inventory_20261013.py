"""Read-only inventory for the requested collection scope migration."""
import asyncio
import json
import sqlite3
from collections import Counter
from pathlib import Path

import nats


async def main():
    out = {}
    for domain, root in [('market', '/data/state'), ('crypto', '/data/crypto_state')]:
        db = Path(root) / 'vibetrading.sqlite3'
        if not db.exists():
            continue
        conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=20)
        conn.row_factory = sqlite3.Row
        out[domain] = [dict(r) for r in conn.execute("SELECT provider,venue,event_type,lifecycle_state,upload_status,local_present,COUNT(*) files,SUM(rows) rows,SUM(byte_size) bytes FROM parquet_manifest GROUP BY provider,venue,event_type,lifecycle_state,upload_status,local_present")]
        out[domain+'_schema'] = [dict(r) for r in conn.execute('PRAGMA table_info(parquet_manifest)')]
        conn.close()
    client = await nats.connect('nats://nats:4222')
    js = client.jetstream()
    for stream in ['MARKET_DATA', 'CRYPTO_DATA', 'OPERATIONS']:
        try:
            response = await js._api_request(f'$JS.API.STREAM.INFO.{stream}', json.dumps({'subjects_filter': '>'}).encode())
            out[stream] = response.get('state')
            out[stream+'_consumers'] = [c.as_dict() for c in await js.consumers_info(stream)]
        except Exception as exc:
            out[stream] = str(exc)
    await client.close()
    print(json.dumps(out, default=str))


asyncio.run(main())
