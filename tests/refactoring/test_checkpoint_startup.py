"""Independent retained-spool oracles for market-only checkpoint startup."""
import hashlib
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from vibetrading.feed_recovery import EngineCursor, RecoveryFrame
from vibetrading.hot_path import HotPathStore
from vibetrading.local_engine import EngineBudgets, LocalMarketEngine
from vibetrading.local_pipeline import PipelineBudgets
from vibetrading.local_runtime import replay_checkpoint_startup, replay_startup
from vibetrading.market_checkpoint import CheckpointCodec
from vibetrading.market_spool import MarketSpool
from vibetrading.models import AssetClass, InstrumentId, TradeEvent, Venue


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


def capture(items):
    source=engine(items,fresh=True)
    for item in items:
        source.on_frame(append(item,[event(item.producer,1)])[0])
    codec=CheckpointCodec(max_bytes=262144,max_event_bytes=8192,max_producers=4,
        config_sha256=hashlib.sha256(b'fixed-market-only-config').hexdigest(),
        capacity=source.hot_path.checkpoint_capacity())
    return source,codec,source.capture_checkpoint(codec)


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
