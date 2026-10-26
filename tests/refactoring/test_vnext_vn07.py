"""Owner-barrier checkpoint mechanics, never a production readiness proof."""
import asyncio
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from vibetrading.feed_recovery import EngineCursor, RecoveryFrame
from vibetrading.hot_path import HotPathStore
from vibetrading.local_engine import EngineBudgets, EngineError, LocalMarketEngine
from vibetrading.local_transport import AppliedReceipt
from vibetrading.market_checkpoint import CheckpointCodec
from core.models import AssetClass, InstrumentId, OrderBookEvent, OrderBookLevel, TradeEvent, Venue


NOW = 1_792_281_600_000_000_000
MONOTONIC = 100_000_000_000
CONFIG = hashlib.sha256(b'vn07-owner-fixture-config').hexdigest()


def cursor(sequence=0, producer='p', incarnation='run'):
    return EngineCursor(producer, incarnation, sequence)


def frame(sequence=1, producer='p', **changes):
    values = dict(event_id=f'{producer}-{sequence}', provider='kiwoom', venue=Venue.KIWOOM,
        instrument=InstrumentId(venue=Venue.KIWOOM, asset_class=AssetClass.ETF,
            raw_symbol='069500', std_symbol='069500'), source_ts_ns=None,
        wallclock_ts_ns=NOW, receive_ts_ns=NOW, price=Decimal('123.000000000000000001'),
        quantity=Decimal('2.000000000000000001'), sequence=str(sequence))
    event = TradeEvent(**(values | changes))
    return RecoveryFrame(cursor(sequence, producer), event.model_dump_json().encode())


def budgets(**changes):
    return replace(EngineBudgets(4,8,8192,8,65536,10_000_000_000,8,4,20_000_000_000,.01), **changes)


def build(*, fresh=True, providers=None, epoch=None, **updates):
    providers = providers or {'p':'kiwoom'}
    epoch = epoch if epoch is not None else [NOW]
    options = dict(producer_providers=providers, budgets=budgets(), on_invalidate=lambda *args:None,
        hot_path=HotPathStore(max_trades=3,max_books=3,max_index_quotes=3),
        clock=lambda:MONOTONIC, snapshot_clock=lambda:epoch[0], trading_filter=lambda event:False,
        fresh_starts={name:'run' for name in providers} if fresh else None, verified_fresh_start=fresh)
    return LocalMarketEngine(**(options | updates))


def codec(engine, **updates):
    options = dict(max_bytes=100_000,max_event_bytes=8192,max_producers=4,
                   config_sha256=CONFIG,capacity=engine.hot_path.checkpoint_capacity())
    return CheckpointCodec(**(options | updates))


def capture(engine, serializer=None):
    return engine.capture_checkpoint(serializer or codec(engine),
        capture_epoch_ns=NOW,capture_monotonic_ns=MONOTONIC)


def source_checkpoint(*, providers=None):
    source = build(providers=providers)
    for producer in source._feeds:source.on_frame(frame(producer=producer))
    serializer = codec(source)
    payload = capture(source,serializer)
    receipts = {name:source.query_cursor(name) for name in source._feeds}
    highs = {name:proof.cursor for name,proof in receipts.items()}
    return source, serializer, payload, receipts, highs


def restore(target, source, serializer, payload, receipts, highs):
    return target.restore_virgin(serializer,payload,highs,verified_cursors=receipts,
        expected_engine_incarnation=source.hot_path.engine_incarnation,
        restore_epoch_ns=NOW,restore_monotonic_ns=MONOTONIC)


def rewrite(payload, change):
    envelope = json.loads(payload)
    change(envelope['body'])
    canonical = lambda value:json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    envelope['sha256'] = hashlib.sha256(canonical(envelope['body'])).hexdigest()
    return canonical(envelope)


@pytest.fixture(autouse=True)
def fixed_dedup_clock(monkeypatch):
    monkeypatch.setattr("core.dedup.monotonic",lambda:MONOTONIC/1_000_000_000)


def test_capture_binds_actual_store_cursor_sources_and_instrument_ownership():
    source, serializer, payload, receipts, _ = source_checkpoint(providers={'p':'kiwoom','q':'kiwoom'})
    inspected = source.inspect_checkpoint(serializer,payload,restore_epoch_ns=NOW,restore_monotonic_ns=MONOTONIC)
    assert inspected.store.apply_seq == source.hot_path.apply_seq == 2
    assert inspected.store.engine_incarnation == source.hot_path.engine_incarnation
    assert {item.cursor.producer:AppliedReceipt(item.cursor,item.digest) for item in inspected.producers} == receipts
    body = json.loads(payload)['body']
    assert body['producer_providers'] == {'p':'kiwoom','q':'kiwoom'}
    assert body['instruments'] == {'p':['kiwoom:etf:069500'],'q':['kiwoom:etf:069500']}
    assert capture(source,serializer) == payload


@pytest.mark.parametrize('where', ['filter','postapply','invalidation'])
def test_capture_rejects_filter_postapply_and_invalidation_reentrancy(where):
    attempts = []
    engine = build()
    serializer = codec(engine)
    def callback(*args):
        with pytest.raises(EngineError,match='operation_in_progress'):
            capture(engine,serializer)
        attempts.append(where)
    if where == 'filter':
        engine.trading_filter = lambda event:(callback() or False)
        engine.on_frame(frame())
    elif where == 'postapply':
        engine.post_apply_callbacks = (callback,)
        engine.on_frame(frame())
    else:
        engine.on_invalidate = callback
        engine.invalidate('p','test')
    assert attempts == [where]


def test_nested_input_is_rejected_without_advancing_another_cursor():
    engine = build()
    def callback(result,snapshot):
        with pytest.raises(EngineError,match='operation_in_progress'):
            engine.on_frame(frame(2))
    engine.post_apply_callbacks = (callback,)
    engine.on_frame(frame())
    assert engine.query_cursor('p').cursor == cursor(1)
    assert engine.hot_path.apply_seq == 1


@pytest.mark.parametrize('throws', [False,True])
def test_filter_direct_application_cannot_be_blessed_by_the_normal_commit(throws):
    engine = build()
    def filter_event(event):
        engine.hot_path.apply_versioned(event.model_copy(update={'event_id':'outside-filter'}))
        if throws:raise RuntimeError('filter failed after outside apply')
        return False
    engine.trading_filter = filter_event
    with pytest.raises(RuntimeError):engine.on_frame(frame(1))
    assert engine.hot_path.apply_seq == 1
    assert 'outside-filter' in engine.hot_path._fingerprints
    assert 'p-1' not in engine.hot_path._fingerprints
    with pytest.raises(EngineError):engine.query_cursor('p')
    with pytest.raises(EngineError):capture(engine)


def test_postapply_failure_and_missing_digest_cannot_be_checkpointed():
    engine = build()
    engine.post_apply_callbacks = (lambda *args:(_ for _ in ()).throw(RuntimeError('failed')),)
    with pytest.raises(RuntimeError):engine.on_frame(frame())
    assert engine.hot_path.apply_seq == 1 and engine._feeds['p'].applied == cursor(0)
    with pytest.raises(EngineError):capture(engine)
    healthy, serializer, _, _, _ = source_checkpoint()
    healthy._feeds['p'].digests.clear()
    with pytest.raises(EngineError,match='digest_unavailable'):capture(healthy,serializer)


def test_rewind_cannot_replay_changed_content_and_certify_two_events_at_one_cursor():
    engine = build()
    engine.on_frame(frame(1))
    original = capture(engine)
    with pytest.raises(EngineError,match='not_owner_committed'):
        engine.begin_recovery(cursor(0),cursor(1),verified_checkpoint=True)
    assert capture(engine) == original
    with pytest.raises(EngineError,match='cursor_content_conflict'):
        engine.apply_replay(frame(1,event_id='different-event-at-same-cursor'))
    assert engine.hot_path.apply_seq == 1
    assert 'different-event-at-same-cursor' not in engine.hot_path._fingerprints
    with pytest.raises(EngineError):capture(engine)


def test_current_committed_cursor_can_recover_forward_without_rewind():
    engine = build()
    engine.on_frame(frame(1))
    engine.begin_recovery(cursor(1),cursor(2),verified_checkpoint=True)
    engine.apply_replay(frame(2))
    assert engine.hot_path.apply_seq == 2
    inspected = engine.inspect_checkpoint(codec(engine),capture(engine),
        restore_epoch_ns=NOW,restore_monotonic_ns=MONOTONIC)
    assert inspected.producers[0].cursor == cursor(2)


def test_injected_pre_engine_state_has_no_checkpoint_origin_authority():
    hot = HotPathStore(max_trades=3,max_books=3,max_index_quotes=3)
    hot.apply_versioned(json.loads(frame(1).payload))
    engine = build(fresh=False,hot_path=hot)
    engine.begin_recovery(cursor(0),cursor(0),verified_checkpoint=True)
    engine.handoff(cursor(0),cursor(0),verified=True,ready=True,lease_ns=budgets().feed_lease_ns)
    engine.on_frame(frame(1,event_id='new-engine-event'))
    assert engine.hot_path.apply_seq == 2
    with pytest.raises(EngineError,match='origin_unverified'):capture(engine)


def test_outside_store_application_cannot_be_hidden_by_next_owner_commit():
    engine = build()
    engine.hot_path.apply_versioned(json.loads(frame(1,event_id='outside-owner').payload))
    with pytest.raises(EngineError,match='outside_owner'):engine.on_frame(frame(1))
    assert engine.hot_path.apply_seq == 1
    assert 'p-1' not in engine.hot_path._fingerprints
    with pytest.raises(EngineError):engine.query_cursor('p')
    with pytest.raises(EngineError):capture(engine)


@pytest.mark.asyncio
async def test_capture_rejects_queued_and_currently_yielded_decisions():
    engine = build(trading_filter=lambda event:True)
    engine.on_frame(frame())
    with pytest.raises(EngineError,match='quiescent'):capture(engine)
    stream = engine.event_source()
    event = await anext(stream)
    assert event['event_id'] == 'p-1' and not engine._queue
    with pytest.raises(EngineError,match='quiescent'):capture(engine)
    await stream.aclose()
    assert capture(engine)
    await engine.close()


def test_capture_requires_owner_thread_and_detects_external_store_application():
    engine, serializer, _, _, _ = source_checkpoint()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(capture,engine,serializer)
        with pytest.raises(EngineError,match='owner_thread'):future.result()
    engine.hot_path.apply_versioned(json.loads(frame(2).payload))
    with pytest.raises(EngineError,match='outside_owner'):capture(engine,serializer)
    with pytest.raises(EngineError):engine.query_cursor('p')


def test_restore_into_new_engine_retains_warming_and_exact_global_state():
    source, serializer, payload, receipts, highs = source_checkpoint()
    target = build(fresh=False)
    original_virgin_store = target.hot_path
    assert restore(target,source,serializer,payload,receipts,highs) == ((cursor(1),cursor(1)),)
    assert target.hot_path is not source.hot_path and target.hot_path is not original_virgin_store
    assert original_virgin_store.apply_seq == 0
    assert target.hot_path.apply_seq == source.hot_path.apply_seq == 1
    assert target.query_cursor('p') == receipts['p']
    assert target.stats['feeds']['p']['mode'] == 'WARMING'
    assert target._feeds['p'].last_live_ns is None and target._feeds['p'].lease_ns == 0
    assert not target.request_resume(cursor(1)) and not target.initial_resume(cursor(1))
    assert not target._queue


@pytest.mark.parametrize('damage', ['provider','budget','ownership','digest','incarnation','multi_suffix'])
def test_restore_rejects_mismatched_proof_without_mutating_virgin_target(damage):
    source, serializer, payload, receipts, highs = source_checkpoint(providers={'p':'kiwoom','q':'kiwoom'})
    target = build(fresh=False,providers={'p':'kiwoom','q':'kiwoom'})
    before_store = target.hot_path
    before_stats = target.stats
    if damage == 'provider':payload = rewrite(payload,lambda body:body['producer_providers'].update(p='other'))
    elif damage == 'budget':payload = rewrite(payload,lambda body:body['engine_budgets'].update(max_records=99))
    elif damage == 'ownership':payload = rewrite(payload,lambda body:body['instruments'].update(p=[],q=[]))
    elif damage == 'digest':receipts['p'] = AppliedReceipt(cursor(1),'0'*64)
    elif damage == 'incarnation':highs['p'] = cursor(1,incarnation='other')
    elif damage == 'multi_suffix':highs = {name:cursor(2,name) for name in highs}
    with pytest.raises(EngineError):restore(target,source,serializer,payload,receipts,highs)
    assert target.hot_path is before_store and target.hot_path.apply_seq == 0
    assert target.stats == before_stats and not target._checkpoint_restored


def test_wrong_expected_engine_identity_and_reused_targets_are_rejected():
    source, serializer, payload, receipts, highs = source_checkpoint()
    target = build(fresh=False)
    with pytest.raises(EngineError,match='engine_incarnation'):
        target.restore_virgin(serializer,payload,highs,verified_cursors=receipts,
            expected_engine_incarnation='unrelated-engine',restore_epoch_ns=NOW,restore_monotonic_ns=MONOTONIC)
    restore(target,source,serializer,payload,receipts,highs)
    with pytest.raises(EngineError,match='virgin'):restore(target,source,serializer,payload,receipts,highs)
    with pytest.raises(EngineError):restore(source,source,serializer,payload,receipts,highs)


def test_single_producer_suffix_is_state_only_and_explicit_readiness_is_required():
    source, serializer, payload, receipts, highs = source_checkpoint(providers={'p':'kiwoom','q':'kiwoom'})
    highs['p'] = cursor(2)
    target = build(fresh=False,providers={'p':'kiwoom','q':'kiwoom'},
                   trading_filter=lambda event:(_ for _ in ()).throw(AssertionError('no replay decisions')))
    restore(target,source,serializer,payload,receipts,highs)
    with pytest.raises(EngineError,match='not_complete'):target.complete_checkpoint_recovery(lambda *args:True)
    target.apply_replay(frame(2))
    assert not target._queue
    assert target.hot_path.apply_seq == source.hot_path.apply_seq+1
    observed = []
    def readiness(engine,snapshot,proofs):
        observed.append((engine is target,snapshot.phase,snapshot.apply_seq,proofs))
        return True
    assert target.complete_checkpoint_recovery(readiness)
    assert observed[0][:3] == (True,'REPLAY',3)
    assert [proof.cursor for proof in observed[0][3]] == [cursor(2),cursor(1,'q')]
    assert all(feed.mode.value == 'LIVE' and feed.initial_proof is None for feed in target._feeds.values())
    assert target.request_resume(cursor(2)) and not target.initial_resume(cursor(2))
    assert capture(target,serializer)  # Sticky virgin-proof fence does not block a later valid capture.


def test_callback_bearing_restore_is_unsupported_without_running_callbacks():
    source, serializer, payload, receipts, highs = source_checkpoint()
    invoked = []
    target = build(fresh=False,post_apply_callbacks=(lambda *args:invoked.append(args),))
    with pytest.raises(EngineError,match='callback_state_restore_unsupported'):
        restore(target,source,serializer,payload,receipts,highs)
    assert not invoked and target.hot_path.apply_seq == 0


def test_later_feed_recovery_preserves_healthy_peer_cursor_and_lease():
    source, serializer, payload, receipts, highs = source_checkpoint(providers={'p':'kiwoom','q':'kiwoom'})
    clock = [MONOTONIC]
    target = build(fresh=False,providers={'p':'kiwoom','q':'kiwoom'},clock=lambda:clock[0])
    restore(target,source,serializer,payload,receipts,highs)
    assert target.complete_checkpoint_recovery(lambda *args:True)
    clock[0] += 1_000_000_000
    target.on_frame(frame(2,'q'))
    peer_lease = (target._feeds['q'].last_live_ns,target._feeds['q'].lease_ns)
    target.invalidate('p','offline')
    assert target.begin_delivery_recovery(cursor(2)).cursor == cursor(1)
    target.apply_replay(frame(2))
    clock[0] += 1_000_000_000
    observed = []
    assert target.complete_checkpoint_recovery(lambda engine,snapshot,proofs:(observed.append(proofs) or True))
    assert [proof.cursor for proof in observed[0]] == [cursor(2),cursor(2,'q')]
    assert (target._feeds['q'].last_live_ns,target._feeds['q'].lease_ns) == peer_lease
    assert target._feeds['p'].last_live_ns == clock[0]
    assert all(feed.mode.value == 'LIVE' for feed in target._feeds.values())


def test_readiness_refresh_rejects_book_that_becomes_stale_during_callback():
    source = build(providers={'p':'binance-perp'})
    native = OrderBookEvent(event_id='native', provider='binance-perp', venue=Venue.BINANCE,
        instrument=InstrumentId(venue=Venue.BINANCE,asset_class=AssetClass.CRYPTO_PERP,
            raw_symbol='BTCUSDT',std_symbol='BTCUSDT'), source_ts_ns=None,receive_ts_ns=NOW,
        wallclock_ts_ns=NOW,depth=1,sync_generation=1,final_update_id=10,
        bids=[OrderBookLevel(price=Decimal(100),quantity=Decimal(2))],
        asks=[OrderBookLevel(price=Decimal(101),quantity=Decimal(3))])
    source.on_frame(RecoveryFrame(cursor(1),native.model_dump_json().encode()))
    serializer = codec(source)
    payload = capture(source,serializer)
    epoch = [NOW]
    target = build(fresh=False,providers={'p':'binance-perp'},epoch=epoch)
    restore(target,source,serializer,payload,{'p':source.query_cursor('p')},{'p':cursor(1)})
    def readiness(*args):
        epoch[0] += 1_000_000_001
        return True
    with pytest.raises(EngineError,match='quality_changed'):target.complete_checkpoint_recovery(readiness)
    assert target.stats['feeds']['p']['mode'] == 'WARMING'
    assert target._feeds['p'].lease_ns == 0
    assert target.hot_path.snapshot(epoch[0]).quality[native.instrument.key] == 'STALE'
    # Ordinary observation expiry changes clock_version, not the owner apply
    # cursor; it must not poison the next valid source event.
    source.hot_path.snapshot(NOW+1_000_000_001)
    source.on_frame(frame(2,provider='binance-perp',venue=Venue.BINANCE,instrument=native.instrument))
    assert source.hot_path.apply_seq == 2 and source.query_cursor('p').cursor == cursor(2)


def test_readiness_outside_apply_then_throw_poisoned_before_any_proof_can_escape():
    source, serializer, payload, receipts, highs = source_checkpoint()
    target = build(fresh=False)
    restore(target,source,serializer,payload,receipts,highs)
    def readiness(engine,*args):
        engine.hot_path.apply_versioned(json.loads(frame(2).payload))
        raise RuntimeError('validator failed after outside apply')
    with pytest.raises(EngineError,match='outside_owner'):target.complete_checkpoint_recovery(readiness)
    assert not target._checkpoint_active
    with pytest.raises(EngineError):target.query_cursor('p')
    assert target._feeds['p'].mode.value == 'INVALID' and target._feeds['p'].lease_ns == 0


def test_final_clock_callback_cannot_insert_state_before_live_promotion():
    source, serializer, payload, receipts, highs = source_checkpoint()
    calls = []
    def clock():
        calls.append(True)
        if len(calls) == 3:target.hot_path.apply_versioned(json.loads(frame(2).payload))
        return MONOTONIC
    target = build(fresh=False,clock=clock)
    restore(target,source,serializer,payload,receipts,highs)
    with pytest.raises(EngineError,match='outside_owner'):target.complete_checkpoint_recovery(lambda *args:True)
    assert len(calls) == 3
    assert target._feeds['p'].mode.value == 'INVALID' and target._feeds['p'].lease_ns == 0


@pytest.mark.parametrize('outcome', ['false','throw','awaitable','reentrant','slow','clock_regression'])
def test_failed_or_expired_readiness_never_promotes_any_feed(outcome):
    source, serializer, payload, receipts, highs = source_checkpoint(providers={'p':'kiwoom','q':'kiwoom'})
    epoch = [NOW]
    target = build(fresh=False,providers={'p':'kiwoom','q':'kiwoom'},epoch=epoch)
    restore(target,source,serializer,payload,receipts,highs)
    async def awaitable():return True
    def readiness(engine,snapshot,proofs):
        if outcome == 'false':return False
        if outcome == 'throw':raise RuntimeError('readiness failed')
        if outcome == 'awaitable':return awaitable()
        if outcome == 'reentrant':return engine.on_frame(frame(2))
        if outcome == 'slow':epoch[0] += target.budgets.max_age_ns+1
        if outcome == 'clock_regression':epoch[0] -= 1
        return True
    if outcome == 'false':assert target.complete_checkpoint_recovery(readiness) is False
    else:
        with pytest.raises((EngineError,RuntimeError)):target.complete_checkpoint_recovery(readiness)
    assert all(feed.mode.value == 'WARMING' and feed.lease_ns == 0 for feed in target._feeds.values())
    assert not target.request_resume(cursor(1))
    with pytest.raises(EngineError):target.handoff(cursor(1),cursor(1),verified=True,ready=True,lease_ns=1)
    with pytest.raises(EngineError):target.commit_recovery(cursor(1),receipts['p'].digest,ready=True)
    with pytest.raises(EngineError):target.arm_initial_resume(cursor(1),cursor(1))
