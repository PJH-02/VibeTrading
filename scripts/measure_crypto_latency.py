"""Read-only live ingestion timing. Does not publish intents or submit orders."""
from __future__ import annotations

import asyncio
from collections import defaultdict
import json
import time
import uuid
from decimal import Decimal
from pathlib import Path

import aiohttp
import nats
import websockets

from execution.bybit_execution import BybitCredentials, BybitOrderClient
from core.models import InstrumentId, OrderDestination, OrderIntent, Side
from vibetrading.settings import get_settings


def distribution(values):
    values = sorted(values)
    if not values:
        return {'samples': 0}
    def at(p):
        x = (len(values)-1)*p
        lo = int(x)
        hi = min(lo+1, len(values)-1)
        return round(values[lo]+(values[hi]-values[lo])*(x-lo), 3)
    return {'samples': len(values), 'p50': at(.5), 'p95': at(.95),
            'p99': at(.99), 'min': round(values[0],3), 'max': round(values[-1],3)}


async def main():
    settings = get_settings()
    rows = []
    async def received(msg):
        mono = time.monotonic_ns()
        wall = time.time_ns()
        event = json.loads(msg.data)
        if event.get('event_type') not in {'order_book', 'trade'}:
            return
        instrument = event['instrument']
        if len(rows) >= 100000:
            return
        decision = time.monotonic_ns()
        intent = OrderIntent(intent_id=uuid.uuid4().hex, job_id='latency-read-only',
            signal_id=event['event_id'], destination=OrderDestination.REAL,
            instrument=InstrumentId.model_validate(instrument), side=Side.BUY,
            client_order_id='measure-only', quantity=Decimal('0.1'),
            metadata={'testnet':True,'measurement_only':True})
        ready = time.monotonic_ns()
        assert intent.metadata['measurement_only']
        source = event.get('source_ts_ns')
        recv = event.get('receive_ts_ns')
        recv_mono = event.get('receive_monotonic_ns')
        rows.append({'venue':event['venue'], 'symbol':instrument['std_symbol'],
            'type':event['event_type'], 'event_id':event['event_id'],
            'collector_receive_wall_ns':recv, 'collector_receive_mono_ns':recv_mono,
            'source_ts_ns':source, 'live_receive_mono_ns':mono, 'live_receive_wall_ns':wall,
            'intent_ready_mono_ns':ready,
            'source_to_collector_unadjusted_ms':(recv-source)/1e6 if recv and source else None,
            'collector_to_live_ms':(mono-recv_mono)/1e6 if recv_mono else None,
            'live_to_decision_ms':(decision-mono)/1e6,
            'intent_construction_ms':(ready-decision)/1e6,
            'collector_to_intent_ms':(ready-recv_mono)/1e6 if recv_mono else None})
    nc = await nats.connect(settings.nats.url)
    subscription = await nc.subscribe('live.crypto.>', cb=received,
        pending_msgs_limit=8192,pending_bytes_limit=32*1024*1024)
    await nc.flush()
    public_rows = []
    clock = {}
    async def public():
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            # Public time queries require no account key. The dummy credential is never used privately.
            client = BybitOrderClient(BybitCredentials('unused','unused',True),session)
            await client.sync_clock(7)
            clock.update(offset_ms=client.server_clock_offset_ms,
                         min_rtt_ms=client.clock_rtt_ms,
                         uncertainty_at_least_ms=client.clock_rtt_ms/2)
            async with websockets.connect('wss://stream-testnet.bybit.com/v5/public/linear',
                                          max_queue=32) as ws:
                await ws.send(json.dumps({'op':'subscribe','args':['orderbook.1.SOLUSDT']}))
                end = time.monotonic()+20
                while time.monotonic()<end:
                    try:
                        raw=await asyncio.wait_for(ws.recv(),timeout=min(5,max(.1,end-time.monotonic())))
                    except asyncio.TimeoutError:
                        continue
                    wall=time.time_ns()
                    msg=json.loads(raw)
                    if msg.get('topic')!='orderbook.1.SOLUSDT':
                        continue
                    public_rows.append({'cts':msg['cts'],'ts':msg['ts'],
                        'receive_wall_ns':wall,'u':msg['data']['u'],
                        'engine_to_system_ms':msg['ts']-msg['cts'],
                        'system_to_receive_est_ms':wall/1e6+client.server_clock_offset_ms-msg['ts']})
    started=time.time_ns()
    try:
        tasks = await asyncio.gather(public(), asyncio.sleep(30),return_exceptions=True)
    finally:
        await subscription.unsubscribe()
        await nc.close()
    metrics=['source_to_collector_unadjusted_ms','collector_to_live_ms',
             'live_to_decision_ms','intent_construction_ms','collector_to_intent_ms']
    groups=defaultdict(list)
    for row in rows:
        groups[f"{row['venue']}/{row['symbol']}/{row['type']}"].append(row)
    summary={key:{metric:distribution([r[metric] for r in group if r[metric] is not None])
                  for metric in metrics} for key,group in groups.items()}
    result={'started_ns':started,'finished_ns':time.time_ns(),
        'scope':'read-only production collector + durable NATS acceptance + core live feed + synthetic OrderIntent model construction; no real strategy or orders',
        'clock':clock,'groups':summary,
        'public_errors':[str(x) for x in tasks if isinstance(x,BaseException)],
        'testnet_public':{metric:distribution([r[metric] for r in public_rows[1:]])
             for metric in ['engine_to_system_ms','system_to_receive_est_ms']},
        'testnet_first_snapshot':public_rows[0] if public_rows else None,
        'production_events':rows,'testnet_public_events':public_rows}
    path=Path('/audit/ingestion-latency.json')
    path.write_text(json.dumps(result,ensure_ascii=False))
    print(json.dumps({k:v for k,v in result.items() if k not in {'production_events','testnet_public_events'}},ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
