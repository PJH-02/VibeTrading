"""Guarded source-copy assertions: state application and ETF source only."""
import asyncio
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from vibetrading.feed_recovery import EngineCursor, FeedMode, RecoveryFrame
from vibetrading.hot_path import HotPathStore
from vibetrading.local_engine import EngineBudgets, EngineError, LocalMarketEngine
from vibetrading.local_transport import AppliedReceipt
from core.models import AssetClass, DataGapEvent, InstrumentId, TradeEvent, Venue


PRODUCER = "synthetic-kiwoom"
PROVIDER = "kiwoom"
INCARNATION = "synthetic-boot"


def cursor(seq=0, *, producer=PRODUCER, incarnation=INCARNATION):
    return EngineCursor(producer, incarnation, seq)


def event(seq=1, **changes):
    base = TradeEvent(instrument=InstrumentId(venue=Venue.KIWOOM, asset_class=AssetClass.ETF,
        raw_symbol="069500", std_symbol="069500"), venue=Venue.KIWOOM, provider=PROVIDER,
        source_ts_ns=1_800_000_000_000_000_000 + seq, receive_ts_ns=1_800_000_000_000_000_000 + seq,
        wallclock_ts_ns=1_800_000_000_000_000_000 + seq, price=Decimal("123.000000000000000001"),
        quantity=Decimal("2.000000000000000001"), event_id="event-" + str(seq), sequence=seq)
    return base.model_copy(update=changes)


def frame(seq=1, *, changes=None, **cursor_changes):
    value = event(seq, **(changes or {}))
    return RecoveryFrame(cursor(seq, **cursor_changes), value.model_dump_json().encode())


def limits(**changes):
    return replace(EngineBudgets(4, 8, 8192, 8, 65536, 10_000_000_000, 8, 4,
                                20_000_000_000, 0.01), **changes)


def build(*, fresh=True, budgets=None, hot_path=None, callbacks=(), producer_providers=None, **changes):
    invalid, now = [], [1_000_000_000]
    providers = producer_providers or {PRODUCER: PROVIDER}
    engine = LocalMarketEngine(producer_providers=providers, budgets=budgets or limits(),
        on_invalidate=lambda producer, reason: invalid.append((producer, reason)),
        hot_path=hot_path if hot_path is not None else (None if callbacks else HotPathStore()),
        state_callbacks=callbacks, clock=lambda: now[0],
        fresh_starts={name: INCARNATION for name in providers} if fresh else None,
        verified_fresh_start=fresh, **changes)
    return engine, invalid, now


@pytest.mark.asyncio
async def test_post_apply_hook_receives_frozen_epoch_snapshot_and_phase():
    received = []
    hot = HotPathStore()
    engine = LocalMarketEngine(producer_providers={PRODUCER: PROVIDER}, budgets=limits(),
        on_invalidate=lambda *args: None, hot_path=hot,
        post_apply_callbacks=(lambda result, snapshot: received.append((result, snapshot)),),
        snapshot_clock=lambda: 123456789, fresh_starts={PRODUCER: INCARNATION},
        verified_fresh_start=True)
    try:
        result = engine.on_frame(frame(1))
        assert len(received) == 1
        applied, snapshot = received[0]
        assert applied.event.event_id == "event-1"
        assert (applied.engine_incarnation, applied.apply_seq) == snapshot.cursor
        assert snapshot.now_ns == 123456789 and snapshot.phase == "LIVE"
        assert result.cursor == cursor(1)
        with pytest.raises((AttributeError, TypeError)):
            snapshot.phase = "REPLAY"
    finally:
        await engine.close()


def test_post_apply_hook_requires_hot_path_and_separate_epoch_clock():
    with pytest.raises(ValueError, match="Post-apply callbacks"):
        LocalMarketEngine(producer_providers={PRODUCER: PROVIDER}, budgets=limits(),
            on_invalidate=lambda *args: None, state_callbacks=(lambda *args: None,),
            post_apply_callbacks=(lambda *args: None,), snapshot_clock=lambda: 1)
    with pytest.raises(ValueError, match="Post-apply callbacks"):
        LocalMarketEngine(producer_providers={PRODUCER: PROVIDER}, budgets=limits(),
            on_invalidate=lambda *args: None, hot_path=HotPathStore(),
            post_apply_callbacks=(lambda *args: None,))


def test_query_replay_and_commit_require_exact_digest_and_owner_readiness():
    engine, _, _ = build()
    try:
        proof = engine.query_cursor(PRODUCER)
        assert proof.cursor == cursor(0) and proof.digest
        engine.begin_recovery(proof.cursor, cursor(1), verified_checkpoint=True)
        applied = engine.apply_replay(frame(1))
        assert applied.cursor == cursor(1)
        assert not engine.commit_recovery(cursor(1), "0" * 64, ready=True)
        assert not engine.commit_recovery(cursor(1), applied.digest, ready=False)
        assert engine.stats["feeds"][PRODUCER]["mode"] == "WARMING"
        assert engine.commit_recovery(cursor(1), applied.digest, ready=True)
        assert engine.request_resume(cursor(1))
    finally:
        asyncio.run(engine.close())


@pytest.mark.asyncio
async def test_isolated_source_retains_healthy_interleaved_delivery_after_other_feed_gap():
    other = "synthetic-second"
    engine, invalid, _ = build(producer_providers={PRODUCER: PROVIDER, other: PROVIDER})
    source = engine.event_source(required_producers=())
    try:
        first = frame()
        second_instrument = event().instrument.model_copy(update={"raw_symbol": "229200", "std_symbol": "229200"})
        engine.on_frame(first)
        engine.on_frame(frame(producer=other, changes={"event_id": "other-1", "instrument": second_instrument}))
        engine.on_gap(cursor(1, producer=other), "disconnected")
        assert engine.stats["discarded"] == 1
        assert engine.stats["queued_records"] == 1
        assert engine.stats["queued_bytes"] == len(first.payload)
        assert engine.stats["feeds"][PRODUCER]["mode"] == "LIVE"
        assert [name for name, _ in invalid] == [other]
        assert (await anext(source))["event_id"] == "event-1"
        engine.on_frame(frame(2))
        assert (await anext(source))["event_id"] == "event-2"
        assert engine.stats["queued_bytes"] == 0
    finally:
        await source.aclose()
        await engine.close()


def test_partial_state_failure_poison_cannot_be_cleared_by_boolean_restore_claim():
    engine, _, _ = build(post_apply_callbacks=(lambda result, snapshot: (_ for _ in ()).throw(RuntimeError("hook failed")),),
                         snapshot_clock=lambda: 1_800_000_000_000_000_000)
    try:
        with pytest.raises(RuntimeError,match="hook failed"):
            engine.on_frame(frame(1))
        assert engine.stats["feeds"][PRODUCER]["state_poisoned"]
        with pytest.raises(EngineError,match="recovery_cursor_unavailable"):
            engine.begin_delivery_recovery(cursor(1))
        with pytest.raises(EngineError,match="same_engine_state_restore_unsupported"):
            engine.authorize_state_restore(cursor(0),verified=True)
        with pytest.raises(EngineError,match="recovery_cursor_unavailable"):
            engine.query_cursor(PRODUCER)
        with pytest.raises(EngineError,match="checkpoint_high_water_unverified"):
            engine.begin_recovery(cursor(0),cursor(1),verified_checkpoint=True)
        assert engine.stats["feeds"][PRODUCER]["state_poisoned"]
    finally:
        asyncio.run(engine.close())


@pytest.mark.asyncio
async def test_explicit_source_dependencies_and_unknown_authority_failure_remain_fail_closed():
    other = "synthetic-second"
    engine, _, _ = build(producer_providers={PRODUCER: PROVIDER, other: PROVIDER})
    source = engine.event_source(required_producers=(PRODUCER,))
    try:
        engine.on_gap(cursor(producer=other), "unrelated")
        engine.on_frame(frame())
        assert (await anext(source))["event_id"] == "event-1"
        engine.on_frame(frame(2))
        engine.invalidate("unknown-authority", "state_identity_unproven")
        assert engine.stats["queued_records"] == engine.stats["queued_bytes"] == 0
        with pytest.raises(EngineError, match="source_invalid"):
            await anext(source)
    finally:
        await source.aclose()
        await engine.close()


@pytest.mark.asyncio
async def test_exact_state_application_one_etf_input_and_idempotent_cursor_duplicate():
    engine, invalid, _ = build()
    source = engine.event_source()
    try:
        value = frame()
        assert engine.request_resume(cursor())
        assert engine.on_frame(value) == AppliedReceipt(value.cursor, value.digest)
        assert engine.on_frame(value) == AppliedReceipt(value.cursor, value.digest)
        assert engine.hot_path.apply_seq == 1
        delivered = await anext(source)
        assert delivered == json.loads(value.payload)
        assert delivered["price"] == "123.000000000000000001"
        delivered["instrument"]["raw_symbol"] = "caller-mutated"
        assert engine.hot_path.snapshot().latest_trade(event().instrument.key).instrument.raw_symbol == "069500"
        assert engine.stats["queued_records"] == 0 and engine.stats["queued_bytes"] == 0
        assert engine.stats["duplicates"] == 1 and not invalid
    finally:
        await source.aclose()
        await engine.close()


def test_explicit_producer_allowlist_state_authority_and_fresh_proof_are_required():
    kwargs = dict(budgets=limits(), on_invalidate=lambda *args: None, hot_path=HotPathStore())
    with pytest.raises(ValueError, match="allowlist"):
        LocalMarketEngine(producer_providers={}, **kwargs)
    with pytest.raises(ValueError, match="state authority"):
        LocalMarketEngine(producer_providers={PRODUCER: PROVIDER}, budgets=limits(),
                          on_invalidate=lambda *args: None)
    with pytest.raises(ValueError, match="state authority"):
        LocalMarketEngine(producer_providers={PRODUCER: PROVIDER}, state_callbacks=(lambda *args: None,), **kwargs)
    with pytest.raises(ValueError, match="virgin"):
        LocalMarketEngine(producer_providers={PRODUCER: PROVIDER}, fresh_starts={PRODUCER: INCARNATION}, **kwargs)
    engine, _, _ = build(fresh=False)
    assert not engine.request_resume(cursor())
    with pytest.raises(EngineError, match="explicit_recovery"):
        engine.on_frame(frame())


@pytest.mark.parametrize("fault", ["provider", "venue", "producer", "incarnation", "sequence", "conflict", "json", "missing", "duplicate_key", "schema"])
def test_bad_identity_cursor_or_payload_immediately_invalidates_and_flushes(fault):
    engine, invalid, _ = build()
    engine.on_frame(frame())
    if fault == "provider":
        candidate = frame(2, changes={"provider": "unregistered"})
    elif fault == "venue":
        candidate = frame(2, changes={"venue": Venue.KIS})
    elif fault == "producer":
        candidate = frame(2, producer="not-registered")
    elif fault == "incarnation":
        candidate = frame(2, incarnation="other-boot")
    elif fault == "sequence":
        candidate = frame(3)
    elif fault == "conflict":
        candidate = frame(1, changes={"price": Decimal("999")})
    elif fault == "json":
        candidate = RecoveryFrame(cursor(2), b"partial json {")
    elif fault == "missing":
        row = event(2).model_dump(mode="json")
        row.pop("event_id")
        candidate = RecoveryFrame(cursor(2), json.dumps(row).encode())
    elif fault == "duplicate_key":
        payload = frame(2).payload
        candidate = RecoveryFrame(cursor(2), payload[:-1] + b',"provider":"kiwoom"}')
    else:
        candidate = frame(2, changes={"schema_version": 999})
    with pytest.raises((EngineError, ValueError)):
        engine.on_frame(candidate)
    assert invalid and engine.stats["feeds"][PRODUCER]["mode"] == "INVALID"
    assert engine.stats["queued_records"] == engine.stats["queued_bytes"] == 0
    assert not engine.request_resume(cursor(1))


@pytest.mark.parametrize("bound", ["records", "bytes", "frame", "instruments"])
def test_every_owned_queue_and_frame_bound_fails_closed(bound):
    initial = frame()
    if bound == "records":
        budget = limits(max_records=1)
    elif bound == "bytes":
        budget = limits(max_frame_bytes=len(initial.payload) + 10, max_bytes=len(initial.payload) + 10)
    elif bound == "frame":
        budget = limits(max_frame_bytes=len(initial.payload) - 1)
    else:
        budget = limits(max_instruments=1)
    engine, invalid, _ = build(budgets=budget)
    if bound != "frame":
        engine.on_frame(initial)
    candidate = frame(2) if bound != "frame" else initial
    if bound == "instruments":
        other = event().instrument.model_copy(update={"raw_symbol": "229200", "std_symbol": "229200"})
        candidate = frame(2, changes={"instrument": other})
    with pytest.raises(EngineError, match="budget"):
        engine.on_frame(candidate)
    assert invalid and engine.stats["queued_bytes"] == 0


def test_bounded_digest_history_never_invents_proof_for_evicted_cursor():
    engine, invalid, _ = build(budgets=limits(max_digests_per_feed=2))
    for seq in (1, 2, 3):
        engine.on_frame(frame(seq))
    assert engine.stats["feeds"][PRODUCER]["retained_digests"] == 2
    with pytest.raises(EngineError, match="unverifiable"):
        engine.on_frame(frame(1))
    assert invalid


def test_replay_is_state_only_and_matching_verified_c_h_handoff_is_required():
    phases = []
    engine, _, _ = build(fresh=False, callbacks=(lambda event, phase: phases.append((event.event_id, phase)),))
    checkpoint, high = cursor(), cursor(2)
    engine.begin_recovery(checkpoint, high, verified_checkpoint=True)
    assert not engine.request_resume(checkpoint)
    engine.apply_replay(frame(1))
    engine.apply_replay(frame(2))
    assert phases == [("event-1", FeedMode.REPLAY), ("event-2", FeedMode.REPLAY)]
    assert engine.stats["queued_records"] == engine.stats["queued_bytes"] == 0
    assert engine.stats["feeds"][PRODUCER]["mode"] == "WARMING"
    engine.handoff(checkpoint, high, verified=True, ready=True, lease_ns=limits().feed_lease_ns)
    assert engine.request_resume(high)
    engine.on_frame(frame(3))
    assert phases[-1] == ("event-3", FeedMode.LIVE)
    assert engine.stats["queued_records"] == 1


@pytest.mark.parametrize("failure", ["unverified_checkpoint", "missing_replay", "wrong_h", "not_ready", "early_live"])
def test_recovery_cannot_automatically_authorize_live(failure):
    engine, invalid, _ = build(fresh=False)
    c, h = cursor(), cursor(1)
    if failure == "unverified_checkpoint":
        with pytest.raises(EngineError):
            engine.begin_recovery(c, h, verified_checkpoint=False)
    else:
        engine.begin_recovery(c, h, verified_checkpoint=True)
        if failure != "missing_replay" and failure != "early_live":
            engine.apply_replay(frame(1))
        with pytest.raises(EngineError):
            if failure == "early_live":
                engine.on_frame(frame(1))
            else:
                engine.handoff(c, cursor(2) if failure == "wrong_h" else h,
                    verified=True, ready=failure != "not_ready", lease_ns=limits().feed_lease_ns)
    assert invalid and engine.stats["feeds"][PRODUCER]["mode"] == "INVALID"
    assert engine.stats["queued_records"] == 0


def test_backfill_and_transport_gap_never_enter_or_leave_old_trading_queue():
    phases = []
    engine, invalid, _ = build(callbacks=(lambda event, phase: phases.append(phase),))
    engine.on_frame(frame(1, changes={"ingest_mode": "backfill"}))
    assert phases == [FeedMode.REPLAY] and engine.stats["queued_records"] == 0
    engine.on_frame(frame(2))
    assert engine.stats["queued_records"] == 1
    engine.on_gap(cursor(2), "disconnect")
    assert invalid and engine.stats["queued_records"] == engine.stats["queued_bytes"] == 0
    assert not engine.request_resume(cursor(2))


def test_data_gap_applies_invalid_state_and_synchronously_closes_trading_gate():
    engine, invalid, _ = build()
    engine.on_frame(frame(1))
    row = event(2)
    gap = DataGapEvent(instrument=row.instrument, provider=row.provider, venue=row.venue,
        event_id="gap-2", receive_ts_ns=row.receive_ts_ns, wallclock_ts_ns=row.wallclock_ts_ns,
        reason="synthetic missing data")
    value = RecoveryFrame(cursor(2), gap.model_dump_json().encode())
    assert engine.on_frame(value) == AppliedReceipt(value.cursor, value.digest)
    assert invalid and engine.stats["queued_records"] == 0
    assert engine.hot_path.snapshot().quality[row.instrument.key] == "INVALID"


def test_queue_age_and_feed_lease_expire_without_a_new_market_message():
    engine, invalid, now = build()
    engine.on_frame(frame(1))
    now[0] += limits().max_age_ns + 1
    engine.check_health()
    assert invalid[-1][1] == "trading_queue_oldest_age_exceeded"
    assert engine.stats["queued_records"] == 0
    other, failures, clock = build()
    clock[0] += limits().feed_lease_ns + 1
    other.check_health()
    assert failures[-1][1] == "feed_health_lease_expired"
    assert not other.request_resume(cursor())


@pytest.mark.asyncio
async def test_single_async_consumer_wait_is_woken_by_immediate_gap():
    engine, _, _ = build()
    source, second = engine.event_source(), engine.event_source()
    pending = asyncio.create_task(anext(source))
    await asyncio.sleep(0)
    try:
        with pytest.raises(EngineError, match="already_has_consumer"):
            await anext(second)
        engine.on_gap(cursor(), "partial_frame")
        with pytest.raises(EngineError, match="source_invalid"):
            await pending
    finally:
        await source.aclose()
        await second.aclose()
        await engine.close()


def test_callback_authority_is_applied_once_and_cannot_enqueue_after_its_own_invalidation():
    calls = []
    owner = [None]
    def callback(event, phase):
        calls.append((event.event_id, phase))
        owner[0].invalidate(PRODUCER, "state detected a gap")
    engine, invalid, _ = build(callbacks=(callback,))
    owner[0] = engine
    with pytest.raises(EngineError, match="callback_invalidated"):
        engine.on_frame(frame())
    assert calls == [("event-1", FeedMode.LIVE)]
    assert invalid and engine.stats["queued_records"] == 0


@pytest.mark.asyncio
async def test_one_health_task_expires_quiet_feed_and_stops_on_close():
    engine, invalid, now = build()
    await engine.start()
    now[0] += limits().feed_lease_ns + 1
    await asyncio.sleep(0.03)
    assert invalid and not engine.request_resume(cursor())
    await engine.close()
    assert engine._watchdog.done()


def arm_startup(engine, names=(PRODUCER,)):
    for name in names:
        initial = cursor(producer=name)
        engine.begin_recovery(initial, initial, verified_checkpoint=True)
    for name in names:
        initial = cursor(producer=name)
        engine.arm_initial_resume(initial, initial)


def test_initial_resume_starts_lease_at_late_first_connection_not_replay_time():
    engine, invalid, now = build(fresh=False, trading_filter=lambda event: False)
    arm_startup(engine)
    now[0] += limits().feed_lease_ns * 100
    engine.check_health()
    assert engine.stats["feeds"][PRODUCER]["mode"] == "WARMING"
    assert not engine.request_resume(cursor())
    assert engine.initial_resume(cursor())
    assert engine.request_resume(cursor())
    engine.on_frame(frame())
    assert engine.stats["applied"] == 1 and engine.stats["queued_records"] == 0
    # Repeated initial requests do not extend the first connection's lease.
    lease_started = now[0]
    now[0] += limits().feed_lease_ns - 1
    assert engine.initial_resume(cursor(1))
    now[0] = lease_started + limits().feed_lease_ns + 1
    assert not engine.initial_resume(cursor(1))
    assert invalid[-1][1] == "feed_health_lease_expired"


@pytest.mark.asyncio
async def test_partial_initial_readiness_retains_early_etf_inputs_until_all_resume():
    other = "synthetic-second"
    names = (PRODUCER, other)
    engine, invalid, _ = build(fresh=False, producer_providers={name: PROVIDER for name in names})
    arm_startup(engine, names)
    source = engine.event_source()
    assert engine.initial_resume(cursor())
    engine.on_frame(frame())
    engine.on_frame(frame(2))
    assert engine.stats["queued_records"] == 2
    pending = asyncio.create_task(anext(source))
    await asyncio.sleep(0)
    try:
        assert not pending.done()
        assert engine.initial_resume(cursor(producer=other))
        first = await pending
        second = await anext(source)
        assert [first["event_id"], second["event_id"]] == ["event-1", "event-2"]
        assert engine.stats["queued_records"] == 0 and not invalid[-1][1].startswith("transport_gap")
    finally:
        await source.aclose()
        await engine.close()


@pytest.mark.parametrize("fault", ["full", "age", "gap"])
def test_startup_queue_fault_prevents_later_initial_resume_and_rearming(fault):
    other = "synthetic-second"
    names = (PRODUCER, other)
    engine, invalid, now = build(fresh=False, budgets=limits(max_records=1),
                               producer_providers={name: PROVIDER for name in names})
    arm_startup(engine, names)
    assert engine.initial_resume(cursor())
    engine.on_frame(frame())
    if fault == "full":
        with pytest.raises(EngineError, match="queue_budget"):
            engine.on_frame(frame(2))
    elif fault == "age":
        now[0] += limits().max_age_ns + 1
        engine.check_health()
    else:
        engine.on_gap(cursor(1), "producer_disconnected")
    assert invalid and engine.stats["queued_records"] == engine.stats["queued_bytes"] == 0
    assert not engine.initial_resume(cursor(producer=other))
    # Even a newly attested checkpoint cannot repurpose the initial-start path
    # after a real runtime fault; normal explicit recovery is a separate API.
    engine.begin_recovery(cursor(1), cursor(1), verified_checkpoint=True)
    with pytest.raises(EngineError, match="initial_resume_proof_invalid"):
        engine.arm_initial_resume(cursor(1), cursor(1))


def test_initial_proof_requires_exact_completed_range_and_is_never_generic_reset():
    engine, _, _ = build(fresh=False)
    engine.begin_recovery(cursor(), cursor(1), verified_checkpoint=True)
    with pytest.raises(EngineError, match="initial_resume_proof_invalid"):
        engine.arm_initial_resume(cursor(), cursor(1))
    completed, _, _ = build(fresh=False)
    completed.begin_recovery(cursor(), cursor(1), verified_checkpoint=True)
    completed.apply_replay(frame())
    completed.arm_initial_resume(cursor(), cursor(1))
    assert not completed.initial_resume(cursor())
    assert not completed.initial_resume(cursor(1, incarnation="wrong-boot"))
    assert completed.initial_resume(cursor(1))
    with pytest.raises(EngineError, match="initial_resume_proof_invalid"):
        completed.arm_initial_resume(cursor(), cursor(1))


@pytest.mark.asyncio
async def test_all_initial_proofs_must_be_armed_before_any_resume_grant():
    other = "synthetic-second"
    names = (PRODUCER, other)
    engine, _, _ = build(fresh=False, producer_providers={name: PROVIDER for name in names})
    for name in names:
        c = cursor(producer=name)
        engine.begin_recovery(c, c, verified_checkpoint=True)
    engine.arm_initial_resume(cursor(), cursor())
    assert not engine.initial_resume(cursor())
    c = cursor(producer=other)
    engine.arm_initial_resume(c, c)
    assert engine.initial_resume(cursor()) and engine.initial_resume(c)
    await engine.close()
