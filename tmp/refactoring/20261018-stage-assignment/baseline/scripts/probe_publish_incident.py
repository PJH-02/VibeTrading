"""Read-only NATS pressure, collector freshness and mock-order counts."""
import asyncio
import json
import sqlite3
import time
from pathlib import Path
import nats
import aiohttp

async def main():
    client=await nats.connect('nats://nats:4222')
    js=client.jetstream()
    streams={}
    for name in ['MARKET_DATA','CRYPTO_DATA','OPERATIONS']:
        info=await js.stream_info(name)
        streams[name]={'state':info.state.as_dict(),'config':{k:info.config.as_dict().get(k) for k in ['max_bytes','max_msgs','max_age','subjects','retention','discard']}}
        streams[name]['consumers']=[]
        for c in await js.consumers_info(name):
            streams[name]['consumers'].append({'name':c.name,'pending':c.num_pending,'ack_pending':c.num_ack_pending,
                'redelivered':c.num_redelivered,'delivered':c.delivered.as_dict(),'ack_floor':c.ack_floor.as_dict()})
    await client.close()
    health={}
    for provider in ['kis','kiwoom','toss','binance-perp','arcus-perp','grvt-perp']:
        path=Path('/data/state/health')/('collectors-'+provider+'.json')
        data=json.loads(path.read_text())
        health[provider]={k:data.get(k) for k in ['state','phase','reason','event_age_seconds','events','published_events','symbol_last_seen_ns','blocking_reasons','primary_reason']}
        health[provider]['heartbeat_age_seconds']=(time.time_ns()-data.get('ts_ns',0))/1e9
    async with aiohttp.ClientSession() as session:
        async with session.get('http://nats:8222/varz') as r:
            data=await r.json()
            server={k:data.get(k) for k in ['cpu','mem','slow_consumers','in_msgs','out_msgs','in_bytes','out_bytes','connections']}
    db=sqlite3.connect('file:/data/state/etf-mock/journal.sqlite3?mode=ro',uri=True)
    orders=[dict(zip(['strategy','status','count'],r)) for r in db.execute('SELECT strategy,status,count(*) FROM orders GROUP BY strategy,status')]
    db.close()
    print(json.dumps({'streams':streams,'health':health,'nats':server,'mock_orders':orders}))

asyncio.run(main())
