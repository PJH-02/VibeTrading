"""Correlated production pipeline traces and explicitly scoped GRVT testnet orders."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import time
import uuid
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

import aiohttp
import nats
import websockets

from execution.extended_brokers import GrvtBrokerAdapter, OrderOutcomeUnknown
from core.models import InstrumentId, OrderDestination, OrderIntent, Side, Venue
from feeds.providers.extended_perp import ExtendedPerpCollector
from vibetrading.settings import get_settings

D=Decimal
ASSETS=('BTCUSDT','ETHUSDT','SOLUSDT')

def order_state(response):
    """GRVT WS wraps its REST-shaped body in another result envelope."""
    while isinstance(response,dict):
        if 'state' in response: return response['state'] or {}
        nested=response.get('order') or response.get('result')
        if not isinstance(nested,dict): break
        response=nested
    return {}

class OrderSocket:
    def __init__(self,socket,audit): self.socket,self.audit=socket,audit
    def __getattr__(self,name): return getattr(self.socket,name)
    async def send(self,raw):
        msg=json.loads(raw); record=self.audit.pending
        if record and msg.get('method')=='v1/create_order':
            record['request_id']=msg['id']; record['send_mono_ns']=time.monotonic_ns()
        await self.socket.send(raw)
        if record and msg.get('method')=='v1/create_order': record['send_done_mono_ns']=time.monotonic_ns()
    async def recv(self):
        raw=await self.socket.recv(); now=time.monotonic_ns(); msg=json.loads(raw); record=self.audit.pending
        if record and msg.get('id')==record.get('request_id'):
            record['ack_wire_receive_mono_ns']=now
            if msg.get('error'): record['reject_code']=msg['error'].get('code')
        return raw

def distribution(values):
    values=sorted(values)
    if not values: return {'n':0}
    def quantile(q):
        point=(len(values)-1)*q; low=int(point); high=min(low+1,len(values)-1)
        return round(values[low]+(values[high]-values[low])*(point-low),4)
    return {'n':len(values),'p50_ms':quantile(.5),'p95_ms':quantile(.95),'p99_ms':quantile(.99),
        'min_ms':round(values[0],4),'max_ms':round(values[-1],4)}

class Audit:
    def __init__(self,run_id,root):
        self.run_id,self.root=run_id,root
        root.mkdir(parents=True,exist_ok=True)
        self.rows={}; self.orders=[]; self.quotes={}; self.book={s:({'bids':{},'asks':{}}) for s in ASSETS}
        self.pending=None; self.private_ready=asyncio.Event(); self.public_ready=asyncio.Event()
        self.armed=None; self.trigger=asyncio.Queue(maxsize=1); self.errors=[]
        self.started_ns=time.time_ns(); self.snapshots=0; self.observed=defaultdict(int)

    def save(self):
        path=self.root/'orders.json'; temporary=path.with_suffix('.tmp')
        with temporary.open('w') as handle:
            json.dump(self.orders,handle,indent=2); handle.flush(); os.fsync(handle.fileno())
        temporary.replace(path)

    async def trace(self,msg):
        row=json.loads(msg.data); key=row['event_id']+':'+str(row['receive_monotonic_ns'])
        self.rows.setdefault(key,{}).update(row)

    async def live(self,msg):
        live_ns=time.monotonic_ns(); live_wall=time.time_ns()
        event=json.loads(msg.data)
        if event.get('event_type') not in {'order_book','trade'}: return
        venue=event.get('venue'); symbol=event['instrument']['std_symbol']
        if venue not in {'binance','arcus','grvt'} or symbol not in ASSETS: return
        self.observed[venue]+=1
        # Deterministic sampling is correlated after the timing trace arrives.
        key=event['event_id']+':'+str(event.get('receive_monotonic_ns'))
        if len(self.rows)>120000: return
        start=time.monotonic_ns()
        intent=OrderIntent(intent_id=uuid.uuid4().hex,client_order_id=uuid.uuid4().hex,
            job_id='e2e-latency-probe',signal_id=event['event_id'],destination=OrderDestination.REAL,
            instrument=InstrumentId.model_validate(event['instrument']),side=Side.BUY,quantity=D('.001'),
            metadata={'measurement_only':True,'testnet':True})
        ready=time.monotonic_ns()
        self.rows.setdefault(key,{}).update({'live_mono_ns':live_ns,'live_wall_ns':live_wall,
            'decision_start_mono_ns':start,'intent_ready_mono_ns':ready,'venue':venue,'symbol':symbol,
            'event_type':event['event_type'],'source_ts_ns':event.get('source_ts_ns'),
            'model_receive_mono_ns':event.get('receive_monotonic_ns')})
        if venue=='grvt' and self.armed and self.armed['symbol']==symbol and self.trigger.empty():
            source=event.get('source_ts_ns')
            if source and -100_000_000<live_wall-int(source)<1_000_000_000:
                arm=self.armed; self.armed=None
                self.trigger.put_nowait((key,event,live_ns,live_wall,arm))

    async def publish_market_event(self,event):
        if event.event_type.value!='order_book' or getattr(event,'fast',True) is False: return
        symbol=event.instrument.std_symbol; book=self.book[symbol]
        if event.update_type.value=='snapshot': book['bids'].clear(); book['asks'].clear()
        for side in ['bids','asks']:
            for level in getattr(event,side):
                if level.quantity: book[side][level.price]=level.quantity
                else: book[side].pop(level.price,None)
        if book['bids'] and book['asks']:
            self.quotes[symbol]=(max(book['bids']),min(book['asks']),time.monotonic_ns())
            if len(self.quotes)==3: self.public_ready.set()
        for order in self.orders:
            if order['symbol']!=symbol or order.get('mode')!='maker' or order.get('public_book_mono_ns') or order.get('cancel_ack_mono_ns') or order.get('terminal_status'): continue
            price=D(order['price']); amount=book['bids'].get(price,D(0))
            if amount==D(order['baseline_size'])+D(order['quantity']) and time.monotonic_ns()>order['rpc_start_mono_ns']:
                order['public_book_mono_ns']=time.monotonic_ns()
                order['public_book_source_ns']=event.source_ts_ns

    async def testnet_public(self):
        collector=ExtendedPerpCollector(Venue.GRVT)
        async with websockets.connect('wss://market-data.testnet.grvt.io/ws/full',ping_interval=15,max_queue=16) as ws:
            await ws.send(json.dumps({'jsonrpc':'2.0','method':'subscribe','params':{'stream':'v1.book.d',
                'selectors':[s.removesuffix('USDT')+'_USDT_Perp@100' for s in ASSETS]},'id':1}))
            await collector._receive(ws,self,None)

    async def private(self,broker):
        async with websockets.connect(broker.ws_url,additional_headers=broker._headers,ping_interval=15,max_queue=128) as ws:
            for request_id,stream in enumerate(['v1.order','v1.fill'],1):
                await ws.send(json.dumps({'jsonrpc':'2.0','method':'subscribe','params':{'stream':stream,
                    'selectors':[str(broker.sub_account)+'-'+s.removesuffix('USDT')+'_USDT_Perp' for s in ASSETS]},'id':request_id}))
            acks=set()
            while True:
                raw=await ws.recv(); mono=time.monotonic_ns(); wall=time.time_ns(); msg=json.loads(raw)
                if msg.get('error'): raise RuntimeError('GRVT private subscription error')
                if msg.get('id') in {1,2} and msg.get('result'):
                    acks.add(msg['id'])
                    if len(acks)==2: self.private_ready.set()
                feed=msg.get('feed')
                if not isinstance(feed,dict): continue
                cid=str((feed.get('metadata') or {}).get('client_order_id') or feed.get('client_order_id') or '')
                order=next((r for r in reversed(self.orders) if r['native_client_id']==cid),None)
                if not order: continue
                if msg.get('stream')=='v1.order':
                    state=feed.get('state') or feed.get('order_state') or {}
                    status=state.get('status','UNKNOWN')
                    order.setdefault('private_states',[]).append({'status':status,'receive_mono_ns':mono,
                        'receive_wall_ns':wall,'exchange_update_ns':state.get('update_time'),
                        'traded_size':state.get('traded_size'),'reject_reason':state.get('reject_reason')})
                    if status in {'OPEN','FILLED'} and 'private_reflected_mono_ns' not in order:
                        order['private_reflected_mono_ns']=mono
                    if status in {'FILLED','CANCELLED','REJECTED'}:
                        order['terminal_mono_ns']=mono; order['terminal_status']=status
                elif msg.get('stream')=='v1.fill':
                    order.setdefault('fills',[]).append({k:feed.get(k) for k in ['size','price','fee','event_time','trade_id']})
                    order.setdefault('first_fill_mono_ns',mono)
                    order['last_fill_mono_ns']=mono

    async def wait_for(self,predicate,seconds):
        end=time.monotonic()+seconds
        while not predicate():
            if time.monotonic()>end: return False
            await asyncio.sleep(.01)
        return True

    async def execute(self,broker,symbol,mode,side='buy',quantity=None):
        native=symbol.removesuffix('USDT')+'_USDT_Perp'; meta=broker.instruments[native]
        if not await self.wait_for(lambda:symbol in self.quotes and time.monotonic_ns()-self.quotes[symbol][2]<10_000_000_000,15):
            raise RuntimeError('Fresh testnet quote unavailable')
        self.armed={'symbol':symbol,'mode':mode,'side':side}
        try: key,event,live,wall,arm=await asyncio.wait_for(self.trigger.get(),10)
        finally: self.armed=None
        begin=time.monotonic_ns()
        if not await self.wait_for(lambda:time.monotonic_ns()-self.quotes[symbol][2]<10_000_000_000,15):
            raise RuntimeError('Testnet quote became stale')
        bid,ask,quote_ns=self.quotes[symbol]
        tick=D(meta['tick_size']); lot=D(meta['min_size'])
        if mode=='maker':
            price=bid-3*tick
            while price in self.book[symbol]['bids']: price-=tick
        else:
            # Cross a bounded 20 bps range so quote movement during the RPC
            # does not turn the execution-latency probe into a passive order.
            price=((ask*D('1.002') if side=='buy' else bid*D('.998'))/tick).to_integral_value(
                rounding=ROUND_CEILING if side=='buy' else ROUND_FLOOR)*tick
        qty=quantity or (max(lot,D(str(meta.get('min_notional') or 0))/price)/lot).to_integral_value(rounding=ROUND_CEILING)*lot
        if not D(0)<qty*price<D(600): raise RuntimeError('Testnet order exceeds probe notional cap')
        identity='latency-'+self.run_id+'-'+uuid.uuid4().hex
        intent=OrderIntent(intent_id=identity,client_order_id=identity,job_id='e2e-latency-grvt-testnet',
            signal_id=event['event_id'],destination=OrderDestination.REAL,instrument=InstrumentId.model_validate(event['instrument']),
            side=Side.BUY if side=='buy' else Side.SELL,quantity=qty,limit_price=price,
            metadata={'testnet':True,'post_only':mode=='maker','reduce_only':side=='sell',
                'time_in_force':'GOOD_TILL_TIME' if mode=='maker' else 'IMMEDIATE_OR_CANCEL'})
        record={'event_key':key,'symbol':symbol,'mode':mode,'side':side,'quantity':str(qty),'price':str(price),
            'client_id':identity,'native_client_id':broker.native_client_id(identity),'source_ts_ns':event.get('source_ts_ns'),
            'source_event_type':event['event_type'],'live_mono_ns':live,'live_wall_ns':wall,
            'intent_start_mono_ns':begin,'intent_ready_mono_ns':time.monotonic_ns(),
            'quote_age_ms':(begin-quote_ns)/1e6,'baseline_size':str(self.book[symbol]['bids'].get(price,0)),
            'rpc_start_mono_ns':time.monotonic_ns(),'outcome':'prepared'}
        self.orders.append(record); self.save()
        record['sign_start_mono_ns']=time.monotonic_ns()
        payload=broker.prepare_order(intent)
        payload['params']['order'].update(builder='0x0000000000000000000000000000000000000000',builder_fee='0')
        record['sign_done_mono_ns']=time.monotonic_ns()
        record['rpc_start_mono_ns']=time.monotonic_ns()
        record['outcome']='submitting'; self.save()
        try:
            self.pending=record
            response=await broker._rpc(payload,headers=broker._headers,mutation=True)
            record['ack_mono_ns']=time.monotonic_ns(); record['outcome']='acknowledged'
            state=order_state(response)
            record['ack_state']=state.get('status')
            if state: record['ack_traded_size']=state.get('traded_size')
        except Exception as exc:
            record['outcome']='unknown' if isinstance(exc,OrderOutcomeUnknown) else 'rejected'
            record['error']=type(exc).__name__; self.save(); raise
        finally:
            self.pending=None
            self.save()
        await self.wait_for(lambda:record.get('private_reflected_mono_ns') or record.get('terminal_status'),5)
        if mode=='maker':
            await self.wait_for(lambda:record.get('public_book_mono_ns') or record.get('terminal_status'),1.5)
            await self.cancel(broker,record)
        else:
            await self.wait_for(lambda:record.get('terminal_status'),3)
        # Reconcile via order endpoint even if private confirmation was lost.
        status=await broker.order_status(identity)
        state=order_state(status)
        record['reconciled_status']=state.get('status')
        filled=D(str((state.get('traded_size') or ['0'])[0]))
        record['filled_quantity']=str(filled); self.save()
        return filled

    async def cancel(self,broker,record):
        if record.get('terminal_status') in {'FILLED','CANCELLED','REJECTED'}: return
        response=await broker._rpc({'jsonrpc':'2.0','method':'v1/cancel_order','params':{
            'sub_account_id':str(broker.sub_account),'client_order_id':record['native_client_id'],'time_to_live_ms':'5000'}},headers=broker._headers,mutation=True)
        record['cancel_ack_mono_ns']=time.monotonic_ns()
        await self.wait_for(lambda:record.get('terminal_status'),5)

    def summary(self):
        grouped=defaultdict(lambda:defaultdict(list)); matched=[]
        for key,row in self.rows.items():
            if 'wire_receive_mono_ns' not in row or 'intent_ready_mono_ns' not in row: continue
            if row.get('snapshot'): self.snapshots+=1; continue
            metrics={'receive_to_normalized_ms':(row['normalized_mono_ns']-row['wire_receive_mono_ns'])/1e6,
                'normalized_to_live_ms':(row['live_mono_ns']-row['normalized_mono_ns'])/1e6,
                'durable_publish_ms':(row['publish_done_mono_ns']-row['publish_enter_mono_ns'])/1e6,
                'live_to_intent_ms':(row['intent_ready_mono_ns']-row['live_mono_ns'])/1e6,
                'receive_to_intent_ms':(row['intent_ready_mono_ns']-row['wire_receive_mono_ns'])/1e6}
            if row.get('source_ts_ns'):
                metrics['source_to_receive_unadjusted_ms']=(row['wire_receive_wall_ns']-int(row['source_ts_ns']))/1e6
                metrics['source_to_intent_unadjusted_ms']=metrics['source_to_receive_unadjusted_ms']+metrics['receive_to_intent_ms']
            row['metrics']=metrics; matched.append(row)
            for group in [row['provider'],row['provider']+'/'+row['symbol']+'/'+row['event_type']]:
                for name,value in metrics.items(): grouped[group][name].append(value)
        orders=defaultdict(lambda:defaultdict(list))
        for row in self.orders:
            trace=self.rows.get(row['event_key'],{})
            if trace.get('wire_receive_mono_ns'):
                row['wire_receive_mono_ns']=trace['wire_receive_mono_ns']; row['wire_receive_wall_ns']=trace['wire_receive_wall_ns']
            values={}
            for name,a,b in [('intent_creation_ms','intent_start_mono_ns','intent_ready_mono_ns'),
                ('signing_ms','sign_start_mono_ns','sign_done_mono_ns'),('intent_to_submit_ms','intent_ready_mono_ns','send_mono_ns'),
                ('submit_to_ack_ms','send_mono_ns','ack_wire_receive_mono_ns'),('submit_to_private_reflected_ms','send_mono_ns','private_reflected_mono_ns'),
                ('submit_to_public_book_ms','send_mono_ns','public_book_mono_ns'),('submit_to_fill_ms','send_mono_ns','first_fill_mono_ns'),
                ('rpc_dispatch_to_send_ms','rpc_start_mono_ns','send_mono_ns'),
                ('receive_to_private_reflected_ms','wire_receive_mono_ns','private_reflected_mono_ns'),
                ('receive_to_fill_ms','wire_receive_mono_ns','first_fill_mono_ns'),
                ('live_to_private_reflected_ms','live_mono_ns','private_reflected_mono_ns')]:
                if row.get(a) and row.get(b): values[name]=(row[b]-row[a])/1e6
            if row.get('wire_receive_mono_ns') and row.get('private_reflected_mono_ns'):
                values['source_to_private_reflected_unadjusted_ms']=(row['wire_receive_wall_ns']-int(row['source_ts_ns']))/1e6+values['receive_to_private_reflected_ms']
            row['metrics']=values
            for group in [row['mode'],row['symbol']+'/'+row['mode']]:
                for name,value in values.items(): orders[group][name].append(value)
        (self.root/'pipeline-events.json').write_text(json.dumps(matched,separators=(',',':')))
        self.save()
        return {'started_ns':self.started_ns,'finished_ns':time.time_ns(),'pipeline':{k:{m:distribution(v) for m,v in g.items()} for k,g in grouped.items()},
            'grvt_testnet':{k:{m:distribution(v) for m,v in g.items()} for k,g in orders.items()},
            'observed_live':dict(self.observed),'matched_sample_events':len(matched),'snapshots_excluded':self.snapshots,
            'orders':self.orders,'errors':self.errors,
            'method':'production raw WS receive -> existing parser -> production durable NATS -> live feed -> probe intent; GRVT production trigger -> testnet quote/sign/RPC -> private order/fill; source clock unadjusted'}

async def main(args):
    settings=get_settings(); root=Path(args.output); audit=Audit(args.run_id,root)
    settings=replace(settings,live_order_enabled=True,env=dict(settings.env,GRVT_USE_TESTNET='true',GRVT_ORDER_SUBMIT_ENABLED='true'))
    broker=GrvtBrokerAdapter(settings)
    assert broker.use_testnet and broker.chain_id==326 and '.testnet.grvt.io' in broker.rest_url
    nc=await nats.connect(settings.nats.url)
    subscriptions=[await nc.subscribe('live.crypto.>',cb=audit.live,pending_msgs_limit=16384,pending_bytes_limit=64*1024*1024),
        await nc.subscribe('audit_latency.'+args.run_id+'.trace',cb=audit.trace,pending_msgs_limit=16384)]
    tasks=[]; baseline=[]; final={}; start=None
    try:
        await broker.connect(); broker._assert_submit_enabled()
        async def rest(endpoint):
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.post(broker.rest_url+'/full/v1/'+endpoint,json={'sub_account_id':str(broker.sub_account)},headers=broker._headers) as response:
                    body=await response.json()
                    if response.status!=200 or 'result' not in body: raise RuntimeError('GRVT reconciliation query failed')
                    return body['result']
        baseline=await rest('positions'); initial_orders=await rest('open_orders')
        if any(D(r.get('size','0')) for r in baseline) or initial_orders:
            raise RuntimeError('Pre-existing GRVT positions/orders: do not mutate this account')
        await broker.account_state()  # Warm the authenticated order RPC socket.
        broker._ws=OrderSocket(broker._ws,audit)
        tasks=[asyncio.create_task(audit.private(broker)),asyncio.create_task(audit.testnet_public())]
        await asyncio.wait_for(audit.private_ready.wait(),20); await asyncio.wait_for(audit.public_ready.wait(),20)
        start=time.monotonic()
        for _ in range(args.maker_repeats):
            for symbol in ASSETS:
                filled=await audit.execute(broker,symbol,'maker')
                if filled: await audit.execute(broker,symbol,'ioc','sell',filled)
        for _ in range(args.ioc_repeats):
            for symbol in ASSETS:
                filled=await audit.execute(broker,symbol,'ioc')
                if filled:
                    closed=await audit.execute(broker,symbol,'ioc','sell',filled)
                    if closed<filled: raise RuntimeError('IOC close left residual; stop new entries and reconcile')
        await asyncio.sleep(max(0,args.seconds-(time.monotonic()-start)))
    except Exception as exc:
        audit.errors.append({'type':type(exc).__name__,'message':str(exc) if isinstance(exc,(RuntimeError,OrderOutcomeUnknown)) else type(exc).__name__})
    finally:
        try:
            if broker._headers:
                for order in audit.orders:
                    if order.get('terminal_status') in {'FILLED','CANCELLED','REJECTED'}:
                        continue
                    if order.get('outcome') in {'submitting','acknowledged','unknown'}:
                        state=await broker.order_status(order['client_id'])
                        current=order_state(state)
                        if current.get('status') in {'OPEN','PENDING'}: await audit.cancel(broker,order)
                positions=await rest('positions')
                for row in positions:
                    size=D(row.get('size','0'))
                    if not size: continue
                    symbol=row['instrument'].split('_')[0]+'USDT'
                    if baseline or symbol not in ASSETS: raise RuntimeError('Unexpected position: manual reconciliation required')
                    identity='cleanup-'+uuid.uuid4().hex
                    from feeds.providers.crypto_perp import _instrument
                    intent=OrderIntent(intent_id=identity,client_order_id=identity,job_id='latency-cleanup',signal_id=identity,
                        destination=OrderDestination.REAL,instrument=_instrument(Venue.GRVT,symbol),side=Side.SELL if size>0 else Side.BUY,
                        quantity=abs(size),metadata={'reduce_only':True,'time_in_force':'IMMEDIATE_OR_CANCEL','testnet':True})
                    payload=broker.prepare_order(intent)
                    payload['params']['order'].update(builder='0x0000000000000000000000000000000000000000',builder_fee='0')
                    await broker._rpc(payload,headers=broker._headers,mutation=True)
                    await asyncio.sleep(.5)
                remaining=await rest('positions'); open_orders=await rest('open_orders')
                final={'nonzero_positions':[{k:r.get(k) for k in ['instrument','size']} for r in remaining if D(r.get('size','0'))],
                    'open_orders_count':len(open_orders)}
        except Exception as exc:
            audit.errors.append({'cleanup_error':type(exc).__name__,'message':str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__})
        if start is not None:
            await asyncio.sleep(max(0,args.seconds-(time.monotonic()-start)))
        for task in tasks:
            if task.done() and not task.cancelled() and task.exception(): audit.errors.append({'stream_error':type(task.exception()).__name__})
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        for sub in subscriptions: await sub.unsubscribe()
        await nc.close(); await broker.close()
        result=audit.summary(); result['final_account']=final
        (root/'result.json').write_text(json.dumps(result,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k not in {'orders','pipeline'}},separators=(',',':')),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--run-id',required=True); parser.add_argument('--output',default='/audit')
    parser.add_argument('--seconds',type=int,default=120); parser.add_argument('--maker-repeats',type=int,default=5); parser.add_argument('--ioc-repeats',type=int,default=3)
    asyncio.run(main(parser.parse_args()))
