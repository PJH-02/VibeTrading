import asyncio, json, hashlib
from pathlib import Path

import pytest

from core.models import EventType, BookUpdateType, OrderBookEvent
from feeds.feed_recovery import EngineCursor, RecoveryFrame
from feeds.providers.arcus import ArcusPerpCollector
import feeds.providers.arcus as arcus
from market.hot_path import HotPathStore
from market.local_engine import EngineBudgets, LocalMarketEngine, EngineError
from storage.market_spool import MarketSpool, SpoolReader, SpoolCursor

PRODUCER, INC = 'arcus-perp', 'repro-epoch'

def book(symbol, seq, *, snapshot=True, bids=None, asks=None):
    native=symbol.removesuffix('USDT')+'-USD'
    return {'type':'subscribed' if snapshot else 'channel_data','channel':'l2OrderbookUpdates','id':native,
        'contents':{'timestamp':1800000000000000,'lastSequenceId':seq,'globalSequenceId':seq+500000000,
        'bids':[['2253.09','1']] if bids is None else bids,
        'asks':[['2253.14','1']] if asks is None else asks}}

def wire(msg): return json.dumps(msg,separators=(',',':'))

class Bus:
    def __init__(self): self.events=[]
    async def publish_market_event(self,event): self.events.append(event)

class Response:
    async def __aenter__(self): return self
    async def __aexit__(self,*a): pass
    def raise_for_status(self): pass
    async def json(self): return {'markets':[{'marketDisplayName':s+'-USD','status':'ONLINE'} for s in ('BTC','ETH','SOL')]}
class Session:
    def __init__(self,**kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self,*a): pass
    def get(self,url): return Response()
class Socket:
    def __init__(self,messages): self.messages=list(messages)
    async def __aenter__(self): return self
    async def __aexit__(self,*a): pass
    async def send(self,msg): pass
    async def recv(self):
        if self.messages: return self.messages.pop(0)
        await asyncio.Event().wait()
class Connector:
    def __init__(self,messages): self.s=Socket(messages)
    async def __aenter__(self): return await self.s.__aenter__()
    async def __aexit__(self,*a): return await self.s.__aexit__(*a)


from dataclasses import replace
import os
from market.local_engine import _observation_noop
from feeds.local_pipeline import SpoolingMarketPublisher, PipelineBudgets, DeliveryBudgets
from runtime.local_transport import LocalTransportServer, LocalTransportClient, TransportBudgets

LIMITS=dict(max_disk_bytes=1_000_000,max_records=100,max_frame_bytes=8192,max_header_bytes=512,
    segment_max_bytes=65536,segment_max_records=32,segment_max_age_seconds=60)
READ={k:v for k,v in LIMITS.items() if not k.startswith('segment_')}
EMPTY=hashlib.sha256(b'').hexdigest()

def cursor(seq): return EngineCursor(PRODUCER,INC,seq)

def engine(observer=True, **changes):
    args=dict(producer_providers={PRODUCER:'arcus-perp'},
        budgets=EngineBudgets(2,16,8192,32,262144,5_000_000_000,32,1,20_000_000_000,.01),
        on_invalidate=_observation_noop,hot_path=HotPathStore(),observation_only=observer)
    args.update(changes)
    return LocalMarketEngine(**args)

async def native_events(monkeypatch):
    first=[wire(book(s,100)) for s in ('BTCUSDT','ETHUSDT','SOLUSDT')]
    first.append(wire(book('ETHUSDT',101,snapshot=False,
        asks=[['2253.22','103.9146142'],['2252.86','0.4793693']])))
    second=[wire(book(s,200)) for s in ('BTCUSDT','ETHUSDT','SOLUSDT')]
    calls=iter((first,second))
    monkeypatch.setattr(arcus.aiohttp,'ClientSession',Session)
    monkeypatch.setattr(arcus.websockets,'connect',lambda *a,**kw:Connector(next(calls)))
    async def no_retry_delay(_): return None
    original_sleep=arcus.asyncio.sleep
    monkeypatch.setattr(arcus.asyncio,'sleep',no_retry_delay)
    bus=Bus()
    try:
        await ArcusPerpCollector(('BTCUSDT','ETHUSDT','SOLUSDT')).run(bus,duration_sec=.02)
    finally:
        monkeypatch.setattr(arcus.asyncio,'sleep',original_sleep)
    # Duration expiry emits trailing gaps. Keep the genuine native prefix through recovery snapshots.
    assert [e.event_type for e in bus.events[4:7]]==[EventType.DATA_GAP]*3
    assert all(e.event_type==EventType.ORDER_BOOK for e in bus.events[7:10])
    return bus.events[:10]

def frames(root, events):
    spool=MarketSpool(root,PRODUCER,INC,**LIMITS)
    try:
        for event in events: spool.append(event.model_dump_json().encode())
        spool.flush()
    finally: spool.close()
    reader=SpoolReader(root,PRODUCER,INC,**READ)
    return [RecoveryFrame(cursor(row.cursor.sequence),row.payload)
        for row in reader.read_batch(max_records=100,max_bytes=262144)]

def replay(local, rows):
    local.begin_recovery(cursor(0),cursor(len(rows)),verified_checkpoint=True)
    for row in rows: local.apply_replay(row)
    return local.query_cursor(PRODUCER)

@pytest.mark.asyncio
async def test_real_arcus_spool_default_guard_and_observation_exact_recovery(tmp_path,monkeypatch):
    events=await native_events(monkeypatch)
    rows=frames(tmp_path/'spool',events)
    ordinary, observed=engine(False),engine()
    try:
        ordinary.begin_recovery(cursor(0),cursor(0),verified_checkpoint=True)
        ordinary.commit_recovery(cursor(0),EMPTY,ready=True)
        for row in rows[:5]: ordinary.on_frame(row)
        assert ordinary.query_cursor(PRODUCER).cursor==cursor(5)
        ordinary.begin_delivery_recovery(cursor(10))
        with pytest.raises(EngineError,match='gap_inside_replay_range'):
            ordinary.apply_replay(rows[5])
        receipt=replay(observed,rows[:7])
        state=observed.hot_path.snapshot()
        keys={e.instrument.key for e in events[:3]}
        assert all(state.quality[k]=='INVALID' and state.latest_book(k) is None for k in keys)
        assert observed.commit_recovery(cursor(7),receipt.digest,ready=True)
        # A fresh BTC snapshot restores BTC alone; unrelated invalid books stay hidden.
        observed.on_frame(rows[7])
        state=observed.hot_path.snapshot()
        btc=events[7].instrument.key
        assert state.quality[btc]=='VALID'
        assert all(state.latest_book(k) is None for k in keys-{btc})
        for row in rows[8:]: observed.on_frame(row)
        before=observed.hot_path.apply_seq
        assert observed.on_frame(rows[-1]).digest==rows[-1].digest
        assert observed.hot_path.apply_seq==before==10
        assert observed.query_cursor(PRODUCER).cursor==cursor(10)
        assert observed.query_cursor(PRODUCER).digest==hashlib.sha256(rows[-1].payload).hexdigest()
        assert all(observed.hot_path.snapshot().quality[k]=='VALID' for k in keys)
        assert observed.stats['queued_records']==observed.stats['queued_bytes']==0
    finally:
        await ordinary.close(); await observed.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('live_prefix', [0,5])
async def test_real_uds_publisher_replays_consecutive_gaps_to_exact_live_high(tmp_path,monkeypatch,live_prefix):
    events=await native_events(monkeypatch)
    root=tmp_path/'spool'; rows=frames(root,events)
    local,cold=engine(),engine()
    replay(cold,rows)
    local.begin_recovery(cursor(0),cursor(0),verified_checkpoint=True)
    if live_prefix:
        assert local.commit_recovery(cursor(0),EMPTY,ready=True)
        for row in rows[:live_prefix]: local.on_frame(row)
        assert local.query_cursor(PRODUCER).cursor == cursor(5)
    # Publisher's fixed H forces exact recovery from zero or the retained first live gap.
    limits=TransportBudgets(8192,1024,32,262144,2,128,262144,1,2)
    endpoint=tmp_path/'uds'/'engine.sock'
    server=LocalTransportServer(endpoint,budgets=limits,allowed_uid=os.geteuid(),
        on_frame=local.on_frame,on_gap=local.on_gap,on_resume=local.request_resume,
        on_replay=local.apply_replay,on_recovery_query=local.begin_delivery_recovery,
        on_recovery_commit=lambda h,d:local.commit_recovery(h,d,ready=True))
    await server.start()
    publisher=SpoolingMarketPublisher(producer=PRODUCER,incarnation=INC,
        spool_factory=lambda:MarketSpool(root,PRODUCER,INC,**LIMITS),transport=None,
        budgets=PipelineBudgets(32,262144,8192,16,1),env={},on_invalid=lambda _:None,
        resume_existing=True,delivery_budgets=DeliveryBudgets(16,131072,.001,.01,1),
        reader_factory=lambda c:SpoolReader(root,PRODUCER,INC,
            after=SpoolCursor(c.producer,c.incarnation,c.sequence),**READ),
        transport_factory=lambda:LocalTransportClient(endpoint,producer=PRODUCER,
            incarnation=INC,budgets=limits,allowed_uid=os.geteuid(),on_gap=lambda *_:None))
    try:
        await publisher.connect()
        async def caught_up():
            while publisher.stats['delivery_state']!='LIVE':
                await asyncio.sleep(.001)
        try:
            await asyncio.wait_for(caught_up(),3)
        except TimeoutError:
            pytest.fail(f'publisher={publisher.stats}; engine={local.stats}')
        assert local.query_cursor(PRODUCER).cursor==cursor(10)
        assert local.query_cursor(PRODUCER).digest==rows[-1].digest
        assert publisher.stats['last_applied_sequence']==10
        actual,expected=local.hot_path.snapshot(),cold.hot_path.snapshot()
        assert actual.quality==expected.quality and actual.books==expected.books
        assert local.stats['queued_records']==local.stats['queued_bytes']==0
    finally:
        await publisher.close(); await server.close(); await local.close(); await cold.close()

@pytest.mark.parametrize('mutation',['state_callbacks','post_apply_callbacks','on_tick','trading_filter'])
@pytest.mark.parametrize('entry',['replay','live','begin','handoff','arm','initial','commit','resume'])
def test_mutated_observer_fails_before_state_cursor_or_callback(mutation,entry):
    local=engine(); calls=[]
    local.begin_recovery(cursor(0),cursor(0),verified_checkpoint=True)
    local.commit_recovery(cursor(0),EMPTY,ready=True)
    callback=lambda *_:calls.append(True)
    setattr(local,mutation,(callback,) if mutation.endswith('callbacks') else callback)
    operations={
        'replay':lambda:local.apply_replay(RecoveryFrame(cursor(1),b'{}')),
        'live':lambda:local.on_frame(RecoveryFrame(cursor(1),b'{}')),
        'begin':lambda:local.begin_delivery_recovery(cursor(1)),
        'handoff':lambda:local.handoff(cursor(0),cursor(0),verified=True,ready=True,lease_ns=1),
        'arm':lambda:local.arm_initial_resume(cursor(0),cursor(0)),
        'initial':lambda:local.initial_resume(cursor(0)),
        'commit':lambda:local.commit_recovery(cursor(0),EMPTY,ready=True),
        'resume':lambda:local.request_resume(cursor(0))}
    try:
        with pytest.raises(EngineError,match='observation_only_invariant_changed'):operations[entry]()
        assert calls==[] and local.hot_path.apply_seq==0
        assert local.stats['feeds'][PRODUCER]['applied_sequence']==0
    finally:asyncio.run(local.close())

@pytest.mark.parametrize('change',[dict(observation_only=1),dict(trading_filter=lambda _:False),
    dict(post_apply_callbacks=(lambda *_:None,)),dict(state_callbacks=(lambda *_:None,)),
    dict(on_tick=lambda *_:None),dict(hot_path=None),dict(producer_providers={PRODUCER:'kiwoom'})])
def test_observer_constructor_rejects_economic_injection(change):
    with pytest.raises((TypeError,ValueError)):engine(**change)

@pytest.mark.parametrize('fault',['provider','incarnation','range','native'])
def test_observer_retains_frame_and_native_validation(fault):
    # Native collector output is unnecessary here: intentionally malformed boundary inputs.
    from core.models import AssetClass,InstrumentId,TradeEvent,Venue
    from decimal import Decimal
    event=TradeEvent(instrument=InstrumentId(venue=Venue.ARCUS,asset_class=AssetClass.CRYPTO_PERP,
        raw_symbol='BTC-USD',std_symbol='BTCUSDT'),venue=Venue.ARCUS,provider='arcus-perp',
        source_ts_ns=1800000000000000000,receive_ts_ns=1800000000000000000,
        wallclock_ts_ns=1800000000000000000,price=Decimal('1'),quantity=Decimal('1'),event_id='x')
    local=engine()
    try:
        local.begin_recovery(cursor(0),cursor(1),verified_checkpoint=True)
        payload=event.model_dump_json().encode(); target=cursor(1)
        if fault=='provider':payload=event.model_copy(update={'provider':'binance-perp'}).model_dump_json().encode()
        elif fault=='incarnation':target=EngineCursor(PRODUCER,'wrong',1)
        elif fault=='range':target=cursor(2)
        elif fault=='native':payload=b'{"event_type":"order_book","bids":[["NaN","1"]]}'
        with pytest.raises(Exception):local.apply_replay(RecoveryFrame(target,payload))
        assert local.hot_path.apply_seq==0
    finally:asyncio.run(local.close())


@pytest.mark.asyncio
async def test_invalid_arcus_book_does_not_block_other_public_source(tmp_path, monkeypatch):
    events = await native_events(monkeypatch)
    rows = frames(tmp_path/'spool', events[:7])
    other, incarnation = 'independent-binance', 'other-epoch'
    local = engine(producer_providers={PRODUCER:'arcus-perp',other:'binance-perp'})
    try:
        receipt = replay(local, rows)
        assert local.commit_recovery(cursor(7), receipt.digest, ready=True)
        zero = EngineCursor(other, incarnation, 0)
        high = EngineCursor(other, incarnation, 1)
        local.begin_recovery(zero, high, verified_checkpoint=True)
        native = events[0].model_dump(mode='json')
        native.update(provider='binance-perp', venue='binance', event_id='other-native')
        native['instrument'].update(venue='binance')
        # Use the collector's real native snapshot schema, with an independent provider identity.
        payload = json.dumps(native).encode()
        result = local.apply_replay(RecoveryFrame(high, payload))
        assert local.commit_recovery(high, result.digest, ready=True)
        assert local.query_cursor(other).cursor == high
        arcus_keys = {e.instrument.key for e in events[:3]}
        snapshot = local.hot_path.snapshot()
        assert all(snapshot.quality[k]=='INVALID' and snapshot.latest_book(k) is None for k in arcus_keys)
        assert local.stats['queued_records'] == local.stats['delivered'] == 0
    finally:
        await local.close()


@pytest.mark.asyncio
async def test_observer_rejects_corrupt_native_book_and_wrong_high_digest(tmp_path, monkeypatch):
    events = await native_events(monkeypatch)
    rows = frames(tmp_path/'spool', events[:1])
    good, corrupt = engine(), engine()
    try:
        receipt = replay(good, rows)
        assert not good.commit_recovery(cursor(1), '0'*64, ready=True)
        assert good.stats['feeds'][PRODUCER]['mode'] == 'WARMING'
        assert good.commit_recovery(cursor(1), receipt.digest, ready=True)
        corrupt.begin_recovery(cursor(0),cursor(1),verified_checkpoint=True)
        native = events[0].model_dump(mode='json')
        native['asks'][0]['price'] = '1'  # A well-formed native snapshot with a crossed book.
        with pytest.raises(EngineError, match='native_book_invalid'):
            corrupt.apply_replay(RecoveryFrame(cursor(1),json.dumps(native).encode()))
        assert corrupt.stats['feeds'][PRODUCER]['state_poisoned'] is True
        assert corrupt.hot_path.snapshot().latest_book(events[0].instrument.key) is None
        assert corrupt.stats['queued_records'] == good.stats['queued_records'] == 0
    finally:
        await good.close(); await corrupt.close()
