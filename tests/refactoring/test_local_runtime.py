"""NATS-free wiring and explicit capacity contracts, synthetic traffic only."""
import asyncio
from dataclasses import asdict
import hashlib
import json
import os
import sys

import pytest

from market.hot_path import HotPathStore
from runtime.local_config import LocalRuntimePlan
from market.local_engine import EngineBudgets, LocalMarketEngine
from feeds.local_pipeline import DeliveryBudgets, PipelineBudgets, SpoolingMarketPublisher
from runtime.local_runtime import replay_startup
from runtime.local_transport import LocalTransportClient, LocalTransportServer, TransportBudgets
from storage.market_spool import MarketSpool, SpoolReader
from core.models import AssetClass, Bar1mEvent, InstrumentId, TradeEvent, Venue


def document(tmp_path):
    evidence=tmp_path/'measurement.json'
    evidence.write_text(json.dumps({'scope':'production_encoded_ingress','sample_seconds':1801,
        'peak_records_per_second':1.0,'peak_encoded_bytes_per_second':2000.0,
        'identity':{'producer':'kiwoom','provider':'kiwoom','symbols':'069500'}}))
    return {'schema_version':1,'endpoint':str(tmp_path/'market'),'allowed_uid':0,
        'disk_reserve_bytes':1024,'transport':asdict(TransportBudgets(8192,1024,32,262144,5,128,262144,1,5)),
        'engine':asdict(EngineBudgets(1,8,8192,32,262144,5_000_000_000,32,1,20_000_000_000,.01)),
        'producers':[{'producer':'kiwoom','provider':'kiwoom','incarnation':'run','symbols':'069500',
            'spool_root':str(tmp_path/'spool'),
            'spool':dict(max_disk_bytes=16*1024*1024,max_records=10000,max_frame_bytes=8192,max_header_bytes=512,
                segment_max_bytes=65536,segment_max_records=128,segment_max_age_seconds=60),
            'pipeline':asdict(PipelineBudgets(32,262144,4096,16,1)),
            'capacity':dict(outage_seconds=1801,peak_records_per_second=1.0,peak_encoded_bytes_per_second=2000.0,
                headroom=1.0,evidence_file=str(evidence),evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest())}],
        'etf_enabled':False,'observation':None,
        'storage':dict(batch_records=32,batch_bytes=262144,max_payload_bytes=8192,
            max_writer_buffer_bytes=1048576,max_fact_rows=10000,max_fact_db_bytes=1048576,poll_interval_ms=10),
        'events':dict(max_records=100,max_bytes=262144,max_frame_bytes=8192,max_consumers=4,poll_seconds=.01,
            query_timeout_seconds=1,max_nak_seconds=1,max_checkpoint_bytes=131072),
        'snapshot':dict(max_bytes=262144,max_instruments=8,book_depth=10,interval_seconds=.01,max_age_seconds=1)}


@pytest.mark.parametrize('fault',['short_window','records','bytes','frame_overhead','duplicate_producer',
    'retired_provider','path','sessions','nan_rate','bool_rate','headroom','unknown_field'])
def test_unsafe_or_unmeasured_runtime_contracts_are_rejected(tmp_path,fault):
    value=document(tmp_path)
    item=value['producers'][0]
    if fault=='short_window':item['capacity']['outage_seconds']=1800
    elif fault=='records':item['spool']['max_records']=1800
    elif fault=='bytes':item['spool']['max_disk_bytes']=20000
    elif fault=='frame_overhead':item['pipeline']['max_frame_bytes']=8192
    elif fault=='duplicate_producer':value['producers'].append(dict(item))
    elif fault=='retired_provider':item['provider']='bybit-perp'
    elif fault=='path':item['spool_root']='relative/spool'
    elif fault=='sessions':value['transport']['max_sessions']=0
    elif fault=='nan_rate':item['capacity']['peak_records_per_second']=float('nan')
    elif fault=='bool_rate':item['capacity']['peak_records_per_second']=True
    elif fault=='headroom':item['capacity']['headroom']=.9
    else:value['nats_url']='nats://unused'
    with pytest.raises((ValueError,TypeError)):
        LocalRuntimePlan.from_document(value)


def test_capacity_evidence_is_bound_and_remaining_budget_not_total_is_checked(tmp_path):
    from types import SimpleNamespace
    plan=LocalRuntimePlan.from_document(document(tmp_path))
    item=plan.producers[0]
    identity={'producer':'kiwoom','provider':'kiwoom','symbols':'069500'}
    item.capacity.verify_evidence(identity)
    with pytest.raises(ValueError,match='match'):
        item.capacity.verify_evidence(identity|{'symbols':'other'})
    item.check_remaining(SimpleNamespace(uncertain=False,last_sequence=0,disk_bytes=0))
    with pytest.raises(RuntimeError,match='remaining'):
        item.check_remaining(SimpleNamespace(uncertain=False,last_sequence=9000,disk_bytes=0))
    item.capacity.evidence_file.write_text('{}')
    with pytest.raises(ValueError,match='changed'):
        item.capacity.verify_evidence(identity)


def delivery_document(tmp_path):
    doc=document(tmp_path)
    doc['producers'][0]['delivery']=dict(max_batch_records=8,max_batch_bytes=32768,
        poll_seconds=.01,reconnect_backoff_cap_seconds=.1,io_timeout_seconds=1)
    return doc


def test_durable_delivery_is_explicit_and_bounded_by_producer_capacity(tmp_path):
    plan=LocalRuntimePlan.from_document(delivery_document(tmp_path))
    assert isinstance(plan.producers[0].delivery,DeliveryBudgets)
    assert LocalRuntimePlan.from_document(document(tmp_path)).producers[0].delivery is None
    for field,value in (('max_batch_records',33),('max_batch_bytes',4095),('io_timeout_seconds',0)):
        doc=delivery_document(tmp_path)
        doc['producers'][0]['delivery'][field]=value
        with pytest.raises(ValueError):LocalRuntimePlan.from_document(doc)
    doc=document(tmp_path)
    doc['producers'][0]['automatic_trading_resume']=True
    with pytest.raises(ValueError,match='producer config'):
        LocalRuntimePlan.from_document(doc)


@pytest.mark.asyncio
async def test_collector_delivery_outage_keeps_durable_records_beyond_ram_capacity(tmp_path,monkeypatch):
    import runtime.local_runtime as runtime
    from types import SimpleNamespace
    from storage.market_spool import read_spool_records
    plan=LocalRuntimePlan.from_document(delivery_document(tmp_path))
    attempts=[];publishers=[]
    class Offline:
        def __init__(self,*args,**kwargs):pass
        async def connect(self):
            attempts.append(True)
            raise OSError('isolated engine is unavailable')
        async def close(self):pass
    class Collector:
        async def run(self,publisher,*,duration_sec):
            assert duration_sec is None
            publishers.append(publisher)
            for batch in range(5):
                for offset in range(8):await publisher.publish_market_event(event(batch*8+offset+1))
                async def durable():
                    while publisher.stats['pending']:await asyncio.sleep(.001)
                await asyncio.wait_for(durable(),timeout=10)
            assert publisher.stats['accepted']==publisher.stats['durable']==40
            assert publisher.stats['pending']==publisher.stats['queued_bytes']==0
            assert publisher.stats['last_applied_sequence']==0
            assert not publisher.stats['invalid']
    monkeypatch.setattr(runtime,'LocalTransportClient',Offline)
    await runtime.run_collector(plan,plan.producers[0],SimpleNamespace(env={}),collector=Collector())
    assert attempts
    item=plan.producers[0]
    rows=read_spool_records(item.spool_root,producer_id=item.producer,incarnation=item.incarnation,
                           **item.limits.reader_kwargs)
    assert [row.cursor.sequence for row in rows]==list(range(1,41))
    assert publishers[0].stats['failed']==0


def test_existing_observation_log_must_have_remaining_window_and_is_retained(tmp_path):
    from runtime.local_config import ObservationPlan
    from runtime.local_runtime import create_spool
    entry=document(tmp_path)['producers'][0]
    entry['spool']['max_records']=1802
    entry.pop('provider');entry.pop('symbols')
    item=ObservationPlan.parse(entry)
    spool=MarketSpool(item.spool_root,item.producer,item.incarnation,**asdict(item.limits))
    spool.append(b'old-observation-1');spool.append(b'old-observation-2');spool.close()
    before={path.name:path.read_bytes() for path in item.spool_root.glob('*.sealed')}
    with pytest.raises(RuntimeError,match='remaining observation'):
        create_spool(item)
    assert {path.name:path.read_bytes() for path in item.spool_root.glob('*.sealed')}==before


@pytest.mark.asyncio
async def test_etf_disabled_runtime_does_not_build_an_unconsumed_trading_queue(tmp_path,monkeypatch):
    import runtime.local_runtime as runtime
    from feeds.feed_recovery import EngineCursor, RecoveryFrame
    plan=LocalRuntimePlan.from_document(document(tmp_path))
    captured=[]
    def factory(**kwargs):
        instance=LocalMarketEngine(**kwargs);captured.append(instance);return instance
    class Server:
        def __init__(self,*args,**kwargs):pass
        async def start(self):pass
        async def close(self):pass
    monkeypatch.setattr(runtime,'LocalMarketEngine',factory)
    monkeypatch.setattr(runtime,'LocalTransportServer',Server)
    from types import SimpleNamespace
    task=asyncio.create_task(runtime.run_engine(plan,SimpleNamespace(state_dir=tmp_path)))
    try:
        for _ in range(100):
            if captured and captured[0]._watchdog is not None:break
            await asyncio.sleep(.001)
        instance=captured[0]
        assert instance._watchdog is not None
        assert instance.initial_resume(EngineCursor('kiwoom','run',0))
        for number in range(1,40):
            instance.on_frame(RecoveryFrame(EngineCursor('kiwoom','run',number),event(number).model_dump_json().encode()))
        assert instance.hot_path.apply_seq==39 and instance.stats['queued_records']==0
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task


def event(number):
    return TradeEvent(instrument=InstrumentId(venue=Venue.KIWOOM,asset_class=AssetClass.ETF,
        raw_symbol='069500',std_symbol='069500'),venue=Venue.KIWOOM,provider='kiwoom',
        price='123.000000000000000001',quantity='1',event_id='synthetic-'+str(number),
        source_ts_ns=1_800_000_000_000_000_000+number,receive_ts_ns=1_800_000_000_000_000_000+number)


def test_etf_invalidation_uses_live_provider_dependency_and_shared_authority(tmp_path):
    from types import SimpleNamespace
    from runtime.local_runtime import etf_feed_dependencies
    from runtime.etf_trading import SYMBOLS
    plan=SimpleNamespace(producers=(
        SimpleNamespace(producer='kr',provider='kiwoom',symbols='A069500,122630'),
        SimpleNamespace(producer='crypto',provider='binance-perp',symbols='BTCUSDT'),
        SimpleNamespace(producer='history',provider='toss',symbols='069500'),
        SimpleNamespace(producer='wildcard',provider='kiwoom',symbols='@etf'),
    ))
    assert etf_feed_dependencies(plan,'kr')==('069500','122630')
    assert etf_feed_dependencies(plan,'crypto')==()
    assert etf_feed_dependencies(plan,'history')==()
    for name in ('wildcard','observations','shutdown','unknown'):
        assert set(etf_feed_dependencies(plan,name))==SYMBOLS


@pytest.mark.asyncio
async def test_etf_wiring_does_not_promote_unrelated_collector_to_global_dependency(tmp_path,monkeypatch):
    import runtime.local_runtime as runtime
    from types import SimpleNamespace
    doc=document(tmp_path)
    doc['etf_enabled']=True
    doc['observation']={k:v for k,v in doc['producers'][0].items() if k not in {'provider','symbols'}}
    doc['observation'].update(producer='observations',spool_root=str(tmp_path/'observations'))
    doc['producers'].append(dict(doc['producers'][0],producer='crypto',provider='binance-perp',
        symbols='BTCUSDT',spool_root=str(tmp_path/'crypto')))
    doc['engine']['max_producers']=2
    doc['transport']['max_sessions']=2
    plan=LocalRuntimePlan.from_document(doc)
    engines=[]
    def factory(**kwargs):
        engine=LocalMarketEngine(**kwargs)
        original=engine.event_source
        def source(**params):
            assert params=={'required_producers':()}
            return original(**params)
        engine.event_source=source
        engines.append(engine)
        return engine
    class Server:
        def __init__(self,*args,**kwargs):pass
        async def start(self):pass
        async def close(self):pass
    invalidated=[]
    class Etf:
        def __init__(self,**kwargs):
            self.journal=SimpleNamespace(db=SimpleNamespace(close=lambda:None))
            self.source=kwargs['event_source']
        async def initialize(self):pass
        def _invalidate_market_feed(self,symbol):invalidated.append(symbol)
        async def run(self,**kwargs):
            engines[0].on_invalidate('crypto','disconnected')
            assert invalidated==[]
            engines[0].on_invalidate('kiwoom','disconnected')
            assert invalidated==['069500']
            await self.source.aclose()
    monkeypatch.setattr(runtime,'LocalMarketEngine',factory)
    monkeypatch.setattr(runtime,'LocalTransportServer',Server)
    await runtime.run_engine(plan,SimpleNamespace(env={},state_dir=tmp_path),etf_factory=Etf)


@pytest.mark.asyncio
async def test_injected_paper_runtime_shares_state_authority_and_receives_replay_phase(tmp_path,monkeypatch):
    import runtime.local_runtime as runtime
    from types import SimpleNamespace
    from feeds.feed_recovery import EngineCursor, RecoveryFrame
    plan=LocalRuntimePlan.from_document(document(tmp_path))
    item=plan.producers[0]
    spool=MarketSpool(item.spool_root,item.producer,item.incarnation,**asdict(item.limits))
    spool.append(event(1).model_dump_json().encode());spool.close()
    received=[];fenced=[];engines=[];started=asyncio.Event()
    key=event(1).instrument.key
    class Paper:
        hot_path=HotPathStore()
        known={key:event(1).instrument}
        def process_applied(self,result,snapshot):
            received.append((result.apply_seq,snapshot.apply_seq,snapshot.phase))
        def invalidate_inputs(self,keys,reason,now_ns):
            fenced.append(keys)
    paper=Paper()
    def factory(**kwargs):
        engine=LocalMarketEngine(**kwargs);engines.append(engine);return engine
    class Server:
        def __init__(self,*args,**kwargs):pass
        async def start(self):
            engine=engines[0]
            assert engine.initial_resume(EngineCursor('kiwoom','run',1))
            engine.on_frame(RecoveryFrame(EngineCursor('kiwoom','run',2),event(2).model_dump_json().encode()))
            started.set()
        async def close(self):pass
    monkeypatch.setattr(runtime,'LocalMarketEngine',factory)
    monkeypatch.setattr(runtime,'LocalTransportServer',Server)
    task=asyncio.create_task(runtime.run_engine(plan,SimpleNamespace(state_dir=tmp_path),paper_runtime=paper,
        paper_feed_dependencies={'kiwoom':(key,)}))
    try:
        await asyncio.wait_for(started.wait(),timeout=3)
        assert engines[0].hot_path is paper.hot_path
        assert received==[(1,1,'REPLAY'),(2,2,'LIVE')]
        assert paper.hot_path.apply_seq==2
        engines[0].invalidate('kiwoom','disconnected')
        assert fenced==[(key,),(key,)]  # Startup recovery fence, then the explicit live gap.
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task


@pytest.mark.asyncio
async def test_startup_replay_restores_state_without_emitting_old_etf_orders(tmp_path):
    item=LocalRuntimePlan.from_document(document(tmp_path)).producers[0]
    spool=MarketSpool(item.spool_root,item.producer,item.incarnation,**asdict(item.limits))
    for number in range(1,4):spool.append(event(number).model_dump_json().encode())
    spool.close()
    engine=LocalMarketEngine(producer_providers={'kiwoom':'kiwoom'},
        budgets=EngineBudgets(**document(tmp_path)['engine']),on_invalidate=lambda *args:None,hot_path=HotPathStore())
    try:
        await replay_startup(engine,(item,))
        assert engine.stats['replayed']==3 and engine.stats['queued_records']==0
        from feeds.feed_recovery import EngineCursor
        assert engine.request_resume(EngineCursor('kiwoom','run',3))
        assert not engine.request_resume(EngineCursor('kiwoom','run',0))
    finally:await engine.close()


@pytest.mark.asyncio
async def test_startup_replay_cannot_relabel_preexisting_state_as_a_virgin_checkpoint(tmp_path):
    store=HotPathStore()
    store.apply_versioned(event(1))
    engine=LocalMarketEngine(producer_providers={'kiwoom':'kiwoom'},
        budgets=EngineBudgets(**document(tmp_path)['engine']),on_invalidate=lambda *args:None,hot_path=store)
    try:
        with pytest.raises(RuntimeError,match='virgin state authority'):
            await replay_startup(engine,())
        assert store.apply_seq==1
        assert engine.stats['replayed']==0
    finally:await engine.close()


@pytest.mark.asyncio
async def test_cross_producer_bar_conflict_cannot_choose_an_economic_winner_by_config_order(tmp_path):
    from types import SimpleNamespace
    from feeds.feed_recovery import EngineCursor
    from runtime.local_runtime import restore_etf_startup
    doc=document(tmp_path)
    second=dict(doc['producers'][0],producer='toss',provider='toss',spool_root=str(tmp_path/'toss'))
    doc['producers'].append(second)
    doc['transport']['max_sessions']=2
    plan=LocalRuntimePlan.from_document(doc)
    restored=[]
    async def restore(data,*,state_only):
        assert state_only
        restored.append(data)
    etf=SimpleNamespace(_day='2026-10-19',event=restore)
    handoffs=[]
    for index,item in enumerate(plan.producers):
        venue=Venue.KIWOOM if index==0 else Venue.TOSS
        bar=Bar1mEvent(instrument=InstrumentId(venue=venue,asset_class=AssetClass.ETF,
            raw_symbol='069500',std_symbol='069500'),venue=venue,provider=item.provider,
            trading_date=etf._day,open_ts_ns=1_800_000_000_000_000_000,
            close_ts_ns=1_800_000_060_000_000_000,open='100',high='101',low='99',
            close=str(100+index),volume='1',ingest_mode='backfill' if index else 'realtime')
        spool=MarketSpool(item.spool_root,item.producer,item.incarnation,**asdict(item.limits))
        spool.append(bar.model_dump_json().encode());spool.close()
        handoffs.append((EngineCursor(item.producer,item.incarnation,0),EngineCursor(item.producer,item.incarnation,1)))
    with pytest.raises(RuntimeError,match='cross-producer apply order'):
        await restore_etf_startup(etf,plan.producers,handoffs)
    assert len(restored)==1


@pytest.mark.asyncio
async def test_failed_etf_initialization_closes_journal_before_run_is_entered(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from runtime.local_runtime import run_engine
    class Server:
        def __init__(self,*args,**kwargs):pass
        async def start(self):raise AssertionError('must initialize ETF before listening')
        async def close(self):pass
    monkeypatch.setattr("runtime.local_runtime.LocalTransportServer",Server)
    doc=document(tmp_path)
    entry={key:value for key,value in doc['producers'][0].items() if key not in {'provider','symbols'}}
    entry=entry|{'producer':'observations','spool_root':str(tmp_path/'observations')}
    doc['observation']=entry;doc['etf_enabled']=True
    plan=LocalRuntimePlan.from_document(doc)
    closed=[]
    class Etf:
        def __init__(self,**kwargs):self.journal=SimpleNamespace(db=SimpleNamespace(close=lambda:closed.append(True)))
        def _invalidate_market_feed(self,symbol):pass
        async def initialize(self):raise RuntimeError('fixture initialize failed')
    with pytest.raises(RuntimeError,match='fixture initialize failed'):
        await run_engine(plan,SimpleNamespace(env={}),etf_factory=Etf)
    assert closed


@pytest.mark.skipif(sys.platform!='linux',reason='production UDS and directory-fsync proof needs Linux')
@pytest.mark.asyncio
async def test_1801_virtual_seconds_with_no_nats_durable_spool_uds_and_local_decisions(tmp_path,monkeypatch):
    calls=[]
    async def unavailable(*args,**kwargs):
        calls.append(True);raise OSError('NATS absent for the entire scenario')
    monkeypatch.setattr('nats.connect',unavailable)
    doc=document(tmp_path)
    plan=LocalRuntimePlan.from_document(doc)
    item=plan.producers[0]
    clock=[1_000_000_000]
    invalid=[]
    engine=LocalMarketEngine(producer_providers={'kiwoom':'kiwoom'},budgets=EngineBudgets(**plan.engine),
        on_invalidate=lambda *args:invalid.append(args),hot_path=HotPathStore(),
        fresh_starts={'kiwoom':'run'},verified_fresh_start=True,clock=lambda:clock[0])
    server=LocalTransportServer(plan.endpoint,budgets=plan.transport,on_frame=engine.on_frame,
        on_gap=engine.on_gap,on_resume=engine.request_resume,allowed_uid=os.getuid())
    await server.start()
    client=LocalTransportClient(plan.endpoint,producer='kiwoom',incarnation='run',budgets=plan.transport,
        on_gap=lambda *args:invalid.append(args),allowed_uid=os.getuid())
    bus=SpoolingMarketPublisher(producer='kiwoom',incarnation='run',
        spool_factory=lambda:MarketSpool(item.spool_root,'kiwoom','run',**asdict(item.limits)),
        transport=client,budgets=item.pipeline,env={},on_invalid=lambda reason:invalid.append(reason))
    source=engine.event_source()
    bodies=[]
    try:
        await bus.connect()
        for first in range(1,1802,10):
            numbers=range(first,min(first+10,1802))
            clock[0]+=len(numbers)*1_000_000_000
            for number in numbers:
                body=event(number).model_dump_json().encode()
                bodies.append(body)
                bus.accept_payload(body)
            for number in numbers:
                delivered=await asyncio.wait_for(anext(source),2)
                assert delivered['event_id']=='synthetic-'+str(number)
                assert delivered['price']=='123.000000000000000001'
        assert engine.hot_path.apply_seq==1801 and not calls and not invalid
        await bus.close()
        assert bus.stats['applied']==bus.stats['durable']==1801
        reader=SpoolReader(item.spool_root,'kiwoom','run',**item.limits.reader_kwargs)
        actual=[]
        while reader.cursor.sequence<1801:
            actual.extend(record.payload for record in reader.read_batch(max_records=32,max_bytes=262144))
        assert actual==bodies
    finally:
        await source.aclose()
        if not bus._closing:
            try:await bus.close()
            except RuntimeError:pass
        await server.close()
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [None, 'early_client_close', 'lease_gap_during_close'])
async def test_collector_orderly_close_requires_complete_durable_and_applied_progress(tmp_path, fault):
    from dataclasses import replace
    from types import SimpleNamespace
    import time
    from runtime.local_runtime import run_collector
    plan = replace(LocalRuntimePlan.from_document(document(tmp_path)), allowed_uid=os.getuid())
    engine = LocalMarketEngine(producer_providers={'kiwoom': 'kiwoom'},
        budgets=EngineBudgets(**plan.engine), on_invalidate=lambda *args: None,
        hot_path=HotPathStore(), fresh_starts={'kiwoom': 'run'}, verified_fresh_start=True)
    server = LocalTransportServer(plan.endpoint, budgets=plan.transport,
        on_frame=engine.on_frame, on_gap=engine.on_gap, on_resume=engine.request_resume,
        allowed_uid=os.getuid())
    await server.start()
    captured = []
    class Collector:
        async def run(self, publisher, *, duration_sec):
            captured.append(publisher)
            now = time.time_ns()
            await publisher.publish_market_event(event(1).model_copy(
                update={'source_ts_ns': now, 'receive_ts_ns': now}))
            async with asyncio.timeout(5):
                while publisher.stats['applied'] != 1:
                    await asyncio.sleep(.001)
            if fault == 'early_client_close':
                await publisher.transport.close()
            elif fault == 'lease_gap_during_close':
                publisher._closing = True
                publisher.transport.on_gap(publisher.transport.cursor, 'control_lease_expired')
    try:
        if fault is None:
            await run_collector(plan, plan.producers[0], SimpleNamespace(env={}), collector=Collector())
        else:
            with pytest.raises(RuntimeError, match='closed with unconfirmed work'):
                await run_collector(plan, plan.producers[0], SimpleNamespace(env={}), collector=Collector())
        stats = captured[0].stats
        assert stats['accepted'] == stats['durable'] == stats['applied'] == 1
        assert stats['invalid'] is (fault is not None)
        item = plan.producers[0]
        reader = SpoolReader(item.spool_root, 'kiwoom', 'run', **item.limits.reader_kwargs)
        assert [row.cursor.sequence for row in reader.read_batch(max_records=1, max_bytes=8192)] == [1]
    finally:
        await server.close()
        await engine.close()
