"""Independent retained-spool oracles for market-only checkpoint startup."""
import hashlib
from dataclasses import replace
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from vibetrading.feed_recovery import EngineCursor, RecoveryFrame
from vibetrading.hot_path import HotPathStore
from vibetrading.local_engine import EngineBudgets, LocalMarketEngine
from vibetrading.local_pipeline import PipelineBudgets
from vibetrading.local_runtime import replay_checkpoint_startup, replay_startup
from vibetrading.market_checkpoint import CheckpointCodec, _canonical
from vibetrading.market_apply_order import ApplyOrder
from vibetrading.market_spool import MarketSpool, SpoolReader
from vibetrading.models import AssetClass, BaseMarketEvent, InstrumentId, OrderBookEvent, TradeEvent, Venue


LIMITS = dict(max_disk_bytes=1_048_576, max_records=128, max_frame_bytes=4096,
    max_header_bytes=256, segment_max_bytes=65536, segment_max_records=64,
    segment_max_age_seconds=60)


def producer(root, name='a'):
    return SimpleNamespace(producer=name, provider='binance-perp', incarnation='run',
        spool_root=root/name, limits=SimpleNamespace(reader_kwargs={k: LIMITS[k] for k in
            ('max_disk_bytes','max_records','max_frame_bytes','max_header_bytes')}),
        pipeline=PipelineBudgets(16,65536,4096,16,1))


def event(name, sequence, price=None):
    symbol='BTCUSDT' if name=='a' else 'ETHUSDT'
    instrument=InstrumentId(venue=Venue.BINANCE,asset_class=AssetClass.CRYPTO_PERP,
        raw_symbol=symbol,std_symbol=symbol)
    now=time.time_ns()
    return TradeEvent(event_id=f'{name}-{sequence}',instrument=instrument,venue=Venue.BINANCE,
        provider='binance-perp',source_ts_ns=now,receive_ts_ns=now,wallclock_ts_ns=now,
        price=Decimal(price or str(100+sequence)),quantity=Decimal('2'),trade_id=str(sequence))


def append(item, rows):
    spool=MarketSpool(item.spool_root,item.producer,item.incarnation,**LIMITS)
    try:
        frames=[]
        for row in rows:
            payload=row.model_dump_json().encode()
            receipt=spool.append(payload)
            frames.append(RecoveryFrame(EngineCursor(item.producer,item.incarnation,
                receipt.cursor.sequence),payload))
        spool.flush()
        return frames
    finally:
        spool.close()


def engine(items, *, fresh=False):
    return LocalMarketEngine(producer_providers={p.producer:p.provider for p in items},
        budgets=EngineBudgets(4,16,4096,16,65536,60_000_000_000,32,2,60_000_000_000,.01),
        on_invalidate=lambda *args:None, hot_path=HotPathStore(max_trades=16,max_books=16,max_index_quotes=16),
        trading_filter=lambda event:False,
        fresh_starts={p.producer:p.incarnation for p in items} if fresh else None,
        verified_fresh_start=fresh)


def capture(items, *, order_spool=None):
    source=engine(items,fresh=True)
    for item in items:
        source.on_frame(append(item,[event(item.producer,1)])[0])
    codec=CheckpointCodec(max_bytes=262144,max_event_bytes=8192,max_producers=4,
        config_sha256=hashlib.sha256(b'fixed-market-only-config').hexdigest(),
        capacity=source.hot_path.checkpoint_capacity())
    return source,codec,source.capture_checkpoint(codec,order_spool=order_spool)


@pytest.mark.asyncio
async def test_retained_c_verified_then_single_suffix_applies_once_without_delivery(tmp_path):
    items=[producer(tmp_path)]
    source,codec,payload=capture(items)
    append(items[0],[event('a',2),event('a',3)])
    target=engine(items)
    try:
        handoffs=await replay_checkpoint_startup(target,items,codec=codec,payload=payload,
            expected_engine_incarnation=source.hot_path.engine_incarnation)
        assert [(c.sequence,h.sequence) for c,h in handoffs]==[(1,3)]
        assert target.hot_path.apply_seq==3 and target.stats['replayed']==2
        assert target.stats['delivered']==target.stats['queued_records']==0
        assert target.stats['feeds']['a']['mode']=='WARMING'
        assert target.complete_checkpoint_recovery(lambda owner,snapshot,receipts:
            snapshot.phase=='REPLAY' and receipts[0].cursor.sequence==3) is True
        assert target.request_resume(EngineCursor('a','run',3)) is True
    finally:
        await target.close();await source.close()


def read_order(spool, *, max_bytes=65536):
    reader=SpoolReader(spool.root,spool.producer_id,spool.incarnation,
        **{k:LIMITS[k] for k in ('max_disk_bytes','max_records','max_frame_bytes','max_header_bytes')})
    return ApplyOrder.read(reader,max_bytes=max_bytes)


@pytest.mark.asyncio
async def test_global_order_restores_shared_ring_in_actual_apply_order(tmp_path):
    items=[producer(tmp_path,'a'),producer(tmp_path,'b')]
    journal=MarketSpool(tmp_path/'order','apply-order','epoch',**LIMITS)
    source,codec,payload=capture(items,order_spool=journal)
    target=engine(items)
    shared=event('a',1).instrument
    frames=[]
    try:
        for name,seq,price in [('b',2,'202'),('a',2,'102'),('b',3,'203'),('a',3,'103')]:
            row=event(name,seq,price).model_copy(update={'instrument':shared})
            frame=append(items[0 if name=='a' else 1],[row])[0]
            frames.append(frame)
            source.on_frame(frame)
        source.on_frame(frames[-1])  # Transport retransmission adds no order entry.
        high=journal.flush()
        proof=read_order(journal)
        assert high.sequence==5 and proof.journal_high==high
        assert [r.cursor.producer for r in proof.receipts]==['b','a','b','a']
        with pytest.raises(TypeError,match='durable journal'):
            replace(proof,receipts=(proof.receipts[1],proof.receipts[0],proof.receipts[2],proof.receipts[3]))
        handoffs=await replay_checkpoint_startup(target,items,codec=codec,payload=payload,
            expected_engine_incarnation=source.hot_path.engine_incarnation,apply_order=proof)
        assert [(c.sequence,h.sequence) for c,h in handoffs]==[(1,3),(1,3)]
        assert [t.price for t in target.hot_path.recent_trades(shared.key)]==list(map(Decimal,['103','203','102','202','101']))
        assert target.hot_path.apply_seq==6 and target.stats['replayed']==4
        assert target.stats['queued_records']==target.stats['delivered']==0
        assert all(f['mode']=='WARMING' for f in target.stats['feeds'].values())
        assert target.complete_checkpoint_recovery(lambda *args:False) is False
        assert target.complete_checkpoint_recovery(lambda *args:True) is True
    finally:
        journal.close();await target.close();await source.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['anchor','missing','extra','journal_ahead','duplicate','incarnation','digest','bare','unflushed','budget'])
async def test_bad_global_order_never_grants_readiness(tmp_path,fault):
    items=[producer(tmp_path,'a'),producer(tmp_path,'b')]
    journal=MarketSpool(tmp_path/'order','apply-order','epoch',**LIMITS)
    source,codec,payload=capture(items,order_spool=journal)
    target=engine(items)
    try:
        for item in items:source.on_frame(append(item,[event(item.producer,2)])[0])
        if fault=='unflushed':
            with pytest.raises(ValueError,match='uncertain|durable anchor'):read_order(journal)
            return
        journal.flush()
        if fault=='budget':
            with pytest.raises(ValueError,match='byte budget'):read_order(journal,max_bytes=10)
            return
        proof=read_order(journal)
        refs=proof.receipts
        anchor=proof.checkpoint_sha256
        if fault=='anchor':anchor='0'*64
        if fault=='missing':refs=refs[:-1]
        if fault=='extra':append(items[0],[event('a',3)])  # Durable producer H never applied by old engine.
        if fault=='duplicate':refs=(refs[0],refs[0])
        if fault=='incarnation':refs=(replace(refs[0],cursor=EngineCursor('a','wrong',2)),refs[1])
        if fault=='digest':refs=(replace(refs[0],digest='0'*64),refs[1])
        if fault=='journal_ahead':refs=refs+(replace(refs[0],cursor=EngineCursor('a','run',3)),)
        if fault in {'anchor','missing','journal_ahead','duplicate','incarnation','digest'}:
            damaged=MarketSpool(tmp_path/'damaged-order','apply-order','damaged',**LIMITS)
            try:
                damaged.append(_canonical(['vibetrading-apply-order',1,anchor],4096))
                for r in refs:
                    c=r.cursor
                    damaged.append(_canonical([c.producer,c.incarnation,c.sequence,r.digest],4096))
                damaged.flush()
                proof=read_order(damaged)
            finally:damaged.close()
        if fault=='bare':proof=True
        with pytest.raises((RuntimeError,ValueError)):
            await replay_checkpoint_startup(target,items,codec=codec,payload=payload,
                expected_engine_incarnation=source.hot_path.engine_incarnation,apply_order=proof)
        assert all(f['mode']!='LIVE' for f in target.stats['feeds'].values())
        if fault!='digest':assert target.hot_path.apply_seq==0
        else:assert target.stats['closed'] is True
    finally:
        journal.close();await target.close();await source.close()


@pytest.mark.asyncio
async def test_engine_consumes_order_before_state_and_cannot_restart_pending_suffix(tmp_path):
    items=[producer(tmp_path,'a'),producer(tmp_path,'b')]
    journal=MarketSpool(tmp_path/'order','apply-order','epoch',**LIMITS)
    source,codec,payload=capture(items,order_spool=journal)
    receipts={p.producer:source.query_cursor(p.producer) for p in items}
    frames=[append(p,[event(p.producer,2)])[0] for p in reversed(items)]
    target=engine(items)
    try:
        for frame in frames:source.on_frame(frame)
        journal.flush()
        highs={f.cursor.producer:f.cursor for f in frames}
        target.restore_virgin(codec,payload,highs,verified_cursors=receipts,
            expected_engine_incarnation=source.hot_path.engine_incarnation,apply_order=read_order(journal))
        with pytest.raises(RuntimeError,match='not_consumed'):
            target.begin_recovery(receipts['a'].cursor,highs['a'],verified_checkpoint=True)
        with pytest.raises(RuntimeError,match='replay_not_complete'):
            target.complete_checkpoint_recovery(lambda *args:True)
        with pytest.raises(RuntimeError,match='order_cursor_or_digest'):
            target.apply_replay(frames[1])
        assert target.hot_path.apply_seq==2 and target.stats['replayed']==0
    finally:
        journal.close();await target.close();await source.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['write','encoding'])
async def test_order_write_failure_closes_all_feeds_and_cannot_certify_state(tmp_path,monkeypatch,fault):
    items=[producer(tmp_path,'a'),producer(tmp_path,'b')]
    journal=MarketSpool(tmp_path/'order','apply-order','epoch',**LIMITS)
    source,codec,_=capture(items,order_spool=journal)
    frames=[append(p,[event(p.producer,2)])[0] for p in items]
    def fail(_):raise OSError('injected order write failure')
    if fault=='write':monkeypatch.setattr(journal,'append',fail)
    else:monkeypatch.setattr('vibetrading.local_engine.order_record',lambda frame:object())
    try:
        with pytest.raises((OSError,ValueError)):source.on_frame(frames[0])
        assert all(f['mode']=='INVALID' for f in source.stats['feeds'].values())
        with pytest.raises(RuntimeError):source.on_frame(frames[1])
        with pytest.raises(RuntimeError):source.capture_checkpoint(codec)
        with pytest.raises(RuntimeError):source.complete_checkpoint_recovery(lambda *args:True)
        assert source.hot_path.apply_seq==3
    finally:
        journal.close();await source.close()


@pytest.mark.asyncio
async def test_order_binding_is_single_use_and_event_duplicates_still_cover_cursors(tmp_path):
    item=producer(tmp_path)
    journal=MarketSpool(tmp_path/'order','apply-order','epoch',**LIMITS)
    source,codec,_=capture([item],order_spool=journal)
    target=engine([item])
    try:
        same=event('a',2)
        base=event('a',4)
        raw=OrderBookEvent(**{k:getattr(base,k) for k in BaseMarketEvent.model_fields if k!='event_type'},
            depth=0,fast=False)
        frames=append(item,[same,same,raw])
        for frame in frames:source.on_frame(frame)
        journal.flush()
        proof=read_order(journal)
        assert [r.cursor.sequence for r in proof.receipts]==[2,3,4]
        assert source.hot_path.apply_seq==3 and source.hot_path.latest_book(raw.instrument.key) is None
        with pytest.raises(TypeError,match='durable journal'):
            replace(proof,receipts=tuple(reversed(proof.receipts)))
        with pytest.raises(TypeError,match='durable journal'):
            ApplyOrder(proof.checkpoint_sha256,proof.journal_high,list(proof.receipts))
        with pytest.raises(RuntimeError,match='already_bound'):source.capture_checkpoint(codec)
        # A different owner cannot attach this used spool to its own checkpoint.
        zero=EngineCursor('a','run',0)
        target.begin_recovery(zero,zero,verified_checkpoint=True)
        target.handoff(zero,zero,verified=True,ready=True,lease_ns=target.budgets.feed_lease_ns)
        other=CheckpointCodec(max_bytes=262144,max_event_bytes=8192,max_producers=4,
            config_sha256=codec.config_sha256,capacity=target.hot_path.checkpoint_capacity())
        with pytest.raises(RuntimeError,match='empty_spool'):target.capture_checkpoint(other,order_spool=journal)
    finally:
        journal.close();await target.close();await source.close()


@pytest.mark.asyncio
async def test_order_anchor_failure_poisoned_before_any_unrecorded_continuation(tmp_path):
    item=producer(tmp_path)
    source,codec,_=capture([item])
    journal=MarketSpool(tmp_path/'closed-order','apply-order','epoch',**LIMITS)
    journal.close()
    try:
        with pytest.raises(RuntimeError,match='closed'):source.capture_checkpoint(codec,order_spool=journal)
        with pytest.raises(RuntimeError):source.query_cursor('a')
        with pytest.raises(RuntimeError):source.capture_checkpoint(codec)
        with pytest.raises(RuntimeError):source.on_frame(append(item,[event('a',2)])[0])
        assert source.hot_path.apply_seq==1
        assert source.stats['feeds']['a']['mode']=='INVALID'
    finally:await source.close()


@pytest.mark.asyncio
async def test_two_unchanged_producers_restore_but_two_nonempty_suffixes_are_unordered(tmp_path):
    items=[producer(tmp_path,'a'),producer(tmp_path,'b')]
    source,codec,payload=capture(items)
    unchanged=engine(items)
    rejected=engine(items)
    try:
        handoffs=await replay_checkpoint_startup(unchanged,items,codec=codec,payload=payload,
            expected_engine_incarnation=source.hot_path.engine_incarnation)
        assert all(c==h for c,h in handoffs)
        assert unchanged.hot_path.apply_seq==2
        for item in items:append(item,[event(item.producer,2)])
        with pytest.raises(RuntimeError,match='global apply order'):
            await replay_checkpoint_startup(rejected,items,codec=codec,payload=payload,
                expected_engine_incarnation=source.hot_path.engine_incarnation)
        assert rejected.hot_path.apply_seq==0
        assert all(f['applied_sequence'] is None for f in rejected.stats['feeds'].values())
    finally:
        await unchanged.close();await rejected.close();await source.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['different_c','missing_c','incarnation','inventory'])
async def test_bad_retained_checkpoint_prefix_rejects_before_install(tmp_path,fault):
    item=producer(tmp_path/'original')
    source,codec,payload=capture([item])
    replacement=producer(tmp_path/'replacement')
    append(replacement,[event('a',1,price='999')] if fault=='different_c' else [])
    items=[replacement]
    if fault=='incarnation':replacement.incarnation='different'
    if fault=='inventory':items=[producer(tmp_path/'other','b')]
    target=engine([item])
    try:
        with pytest.raises((RuntimeError,ValueError)):
            await replay_checkpoint_startup(target,items,codec=codec,payload=payload,
                expected_engine_incarnation=source.hot_path.engine_incarnation)
        assert target.hot_path.apply_seq==0
        assert target.stats['feeds']['a']['applied_sequence'] is None
    finally:
        await target.close();await source.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('checkpoint',[False,True])
async def test_normal_append_beyond_frozen_h_is_left_for_live_delivery(tmp_path,monkeypatch,checkpoint):
    import vibetrading.local_runtime as runtime
    item=producer(tmp_path)
    source,codec,payload=capture([item])
    append(item,[event('a',2)])
    original_scan=runtime._scan
    later=[]

    def scan_then_append(selected):
        report=original_scan(selected)
        later.extend(append(selected,[event('a',3)]))
        return report

    monkeypatch.setattr(runtime,'_scan',scan_then_append)
    target=engine([item])
    try:
        if checkpoint:
            handoffs=await replay_checkpoint_startup(target,[item],codec=codec,payload=payload,
                expected_engine_incarnation=source.hot_path.engine_incarnation)
            target.complete_checkpoint_recovery(lambda *args:True)
        else:
            handoffs=await replay_startup(target,[item])
        assert handoffs[0][1].sequence==2
        assert target.hot_path.apply_seq==2 and target.stats['feeds']['a']['applied_sequence']==2
        target.on_frame(later[0])
        assert target.hot_path.apply_seq==3 and target.stats['feeds']['a']['applied_sequence']==3
    finally:
        await target.close();await source.close()


@pytest.mark.asyncio
async def test_segment_replacement_after_c_proof_cannot_open_a_new_suffix_authority(tmp_path,monkeypatch):
    item=producer(tmp_path)
    source,codec,payload=capture([item])
    append(item,[event('a',2)])
    target=engine([item])
    restore=target.restore_virgin

    def restore_then_replace(*args,**kwargs):
        result=restore(*args,**kwargs)
        segment=next(path for path in item.spool_root.iterdir() if path.suffix in {'.active','.sealed'})
        replacement=segment.with_suffix('.replacement')
        replacement.write_bytes(segment.read_bytes())
        replacement.replace(segment)
        return result

    monkeypatch.setattr(target,'restore_virgin',restore_then_replace)
    try:
        with pytest.raises(RuntimeError,match='identity changed'):
            await replay_checkpoint_startup(target,[item],codec=codec,payload=payload,
                expected_engine_incarnation=source.hot_path.engine_incarnation)
        assert target.stats['closed'] is True
        assert target.stats['replayed']==0
    finally:
        await target.close();await source.close()
