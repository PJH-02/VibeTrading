"""Independent VN07 acceptance probes; no provider/account access."""
from dataclasses import replace
from decimal import Decimal
import hashlib
from types import SimpleNamespace

import pytest

from test_checkpoint_startup import producer, append
from test_strategy_runtime import (T, BTC, ledger_at, matcher_for, runtime, registration, book)
from market.hot_path import HotPathStore
from market.local_engine import LocalMarketEngine, EngineBudgets
from storage.market_checkpoint import CheckpointCodec
from vibetrading.runtime_checkpoint import RuntimeCheckpoint
from vibetrading.etf_trading import EtfTradingLoop, EtfDispatchBlocked


def fixture(root, *, fresh, now=None, corrupt_restore=None, no_callback=False, two_accounts=False):
    now = now or [T]
    ledger = ledger_at(root)
    if fresh:
        ledger.seed_balance("USDT", Decimal(100000), {"source": "synthetic"})
    matcher = matcher_for(ledger)
    additional = []
    if two_accounts:
        from test_strategy_restart_barrier import account_ledger, multi_runtime, multi_registration
        additional = [account_ledger(root, "second-paper")]
        if fresh:
            additional[0].seed_balance("USDT", Decimal(200000), {"source": "synthetic"})
        host = multi_runtime([ledger, *additional], now)
        matcher = host.accounts[ledger.scope.key]
    else:
        host = runtime(ledger, matcher, now)
    observed = []
    reg = (multi_registration([ledger, *additional], "one", observed) if two_accounts
           else registration(ledger, "one", observed))
    original = reg.module.module.create_strategy

    class Counter:
        def __init__(self):
            self.count = 0
            self.wrapped = original()

        def on_event(self, event, view):
            self.count += 1
            return self.wrapped.on_event(event, view)

        def checkpoint_state(self):
            return {"count": self.count}

        def restore_state(self, state):
            self.count = state["count"]
            if corrupt_restore:
                corrupt_restore(ledger)

    reg.module.module.create_strategy = Counter
    host.register(reg)
    item = producer(root / "spools")
    item.provider = "scripted"
    engine = LocalMarketEngine(producer_providers={item.producer: item.provider},
        budgets=EngineBudgets(4,16,4096,16,65536,60_000_000_000,32,2,60_000_000_000,.01),
        on_invalidate=lambda *args: None, hot_path=host.hot_path,
        post_apply_callbacks=(host.process_applied,) if fresh and not no_callback else (),
        snapshot_clock=lambda: now[0], trading_filter=lambda event: False,
        fresh_starts={item.producer: item.incarnation} if fresh else None,
        verified_fresh_start=fresh)
    codec = CheckpointCodec(max_bytes=2_000_000,max_event_bytes=8192,max_producers=4,
        config_sha256=hashlib.sha256(b"vn07-independent-config").hexdigest(),
        capacity=host.hot_path.checkpoint_capacity())
    manager = RuntimeCheckpoint(root / "runtime.json", codec, max_bytes=4_000_000,
        max_journal_rows=10000, max_journal_bytes=8_000_000)
    return SimpleNamespace(ledger=ledger, matcher=matcher, host=host, engine=engine,
                           item=item, manager=manager, now=now, observed=observed, additional=additional)


def input_book(node, sequence):
    node.now[0] = T + sequence - 1
    event = book(node.now[0], sequence=sequence)
    frame = append(node.item, [event])[0]
    node.engine.on_frame(frame)


async def close(node):
    await node.engine.close()
    node.ledger.close()
    for ledger in node.additional:
        ledger.close()


@pytest.mark.asyncio
async def test_exact_cut_restores_real_plugin_and_waits_for_fresh_matching_input(tmp_path):
    source = fixture(tmp_path, fresh=True)
    input_book(source, 1)
    before = source.matcher.report()
    await source.manager.save(source.engine, paper_runtime=source.host)
    await close(source)
    target = fixture(tmp_path, fresh=False)
    try:
        await target.manager.restore(target.engine, [target.item], paper_runtime=target.host,
                                     readiness=lambda *args: True)
        assert target.manager.state == "READY"
        assert target.host.hot_path is target.engine.hot_path
        assert target.host._jobs["one"].instance.checkpoint_state() == {"count": 1}
        assert target.matcher.report() == before
        target.host.advance(T+100)
        assert target.matcher.report() == before, "heartbeat matched cached checkpoint depth"
        input_book(target, 2)
        assert target.host._jobs["one"].instance.checkpoint_state() == {"count": 2}
        assert len(target.ledger.report()["orders"]) == 2
    finally:
        await close(target)


@pytest.mark.asyncio
async def test_readiness_side_effect_cannot_commit_stale_journal_proof(tmp_path):
    source = fixture(tmp_path, fresh=True)
    input_book(source, 1)
    await source.manager.save(source.engine, paper_runtime=source.host)
    await close(source)
    target = fixture(tmp_path, fresh=False)
    invoked = []
    def bad_readiness(*args):
        invoked.append("readiness")
        target.ledger.claim_strategy_event("one", "config-1", "after-proof", {"probe": "changed"})
        return True
    try:
        with pytest.raises((ValueError, RuntimeError)):
            await target.manager.restore(target.engine, [target.item], paper_runtime=target.host,
                                         readiness=bad_readiness)
        assert invoked == ["readiness"], "restore failed before the intended readiness fault"
        assert target.manager.state == "QUARANTINED"
        before = target.matcher.report()
        target.host.advance(T+100)
        assert target.matcher.report() == before
        assert not target.observed
    finally:
        await close(target)


@pytest.mark.asyncio
async def test_restore_hook_side_effect_quarantines_before_first_callback(tmp_path):
    source = fixture(tmp_path, fresh=True)
    input_book(source, 1)
    await source.manager.save(source.engine, paper_runtime=source.host)
    await close(source)
    invoked = []
    def corrupt(ledger):
        invoked.append("hook")
        ledger.claim_strategy_event("one", "config-1", "during-restore", {"probe": "changed"})
    target = fixture(tmp_path, fresh=False, corrupt_restore=corrupt)
    try:
        with pytest.raises((ValueError, RuntimeError)):
            await target.manager.restore(target.engine, [target.item], paper_runtime=target.host,
                                         readiness=lambda *args: True)
        assert invoked == ["hook"], "restore failed before the intended hook fault"
        assert target.manager.state == "QUARANTINED"
        assert not target.observed
        assert len(target.ledger.report()["orders"]) == 1
    finally:
        await close(target)


@pytest.mark.asyncio
async def test_unattached_consumer_cannot_capture_plausible_shared_store(tmp_path):
    source = fixture(tmp_path, fresh=True, no_callback=True)
    try:
        input_book(source, 1)
        with pytest.raises((ValueError, RuntimeError)):
            await source.manager.save(source.engine, paper_runtime=source.host)
        assert not source.observed
    finally:
        await close(source)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["RECOVERING", "QUARANTINED"])
async def test_etf_checkpoint_gate_precedes_unguarded_order_and_observation(state):
    calls = []
    etf = EtfTradingLoop.__new__(EtfTradingLoop)
    etf._checkpoint_recovery_state = state
    etf.execution_guards_enabled = lambda: False
    def touched(*args, **kwargs):
        calls.append("side-effect")
        raise AssertionError("recovery gate must precede dispatch side effects")
    etf._check_ownership = touched
    etf.journal = SimpleNamespace(ambiguous=touched, dispatch_state=touched)
    etf.bus = SimpleNamespace(publish_order_intent=touched)
    broker = SimpleNamespace(submit_order=touched)
    with pytest.raises(EtfDispatchBlocked):
        await etf._submit_staged(broker, SimpleNamespace(intent_id="never-submit", metadata={}))
    assert calls == []


@pytest.mark.asyncio
async def test_corrupt_newest_bundle_fences_before_any_restore_side_effect(tmp_path):
    source = fixture(tmp_path, fresh=True)
    input_book(source, 1)
    await source.manager.save(source.engine, paper_runtime=source.host)
    await close(source)
    (tmp_path / "runtime.json").write_bytes(b'{"torn":')
    target = fixture(tmp_path, fresh=False)
    try:
        with pytest.raises((ValueError, RuntimeError)):
            await target.manager.restore(target.engine, [target.item], paper_runtime=target.host,
                                         readiness=lambda *args: True)
        assert target.manager.state == "QUARANTINED", "corrupt input escaped the group recovery fence"
        assert not target.observed
    finally:
        await close(target)


@pytest.mark.asyncio
async def test_recovered_runtime_can_save_its_next_verified_cut(tmp_path):
    source = fixture(tmp_path, fresh=True)
    input_book(source, 1)
    await source.manager.save(source.engine, paper_runtime=source.host)
    await close(source)
    target = fixture(tmp_path, fresh=False)
    try:
        await target.manager.restore(target.engine, [target.item], paper_runtime=target.host,
                                     readiness=lambda *args: True)
        input_book(target, 2)
        await target.manager.save(target.engine, paper_runtime=target.host)
    finally:
        await close(target)
    again = fixture(tmp_path, fresh=False, now=[T+1])
    try:
        await again.manager.restore(again.engine, [again.item], paper_runtime=again.host,
                                    readiness=lambda *args: True)
        assert again.host._jobs["one"].instance.checkpoint_state() == {"count": 2}
        assert len(again.ledger.report()["orders"]) == 2
    finally:
        await close(again)


@pytest.mark.asyncio
async def test_product_economics_change_with_same_instrument_key_is_rejected(tmp_path):
    from types import MappingProxyType
    source = fixture(tmp_path, fresh=True)
    input_book(source, 1)
    await source.manager.save(source.engine, paper_runtime=source.host)
    await close(source)
    target = fixture(tmp_path, fresh=False)
    changed = replace(target.host.products[BTC.key], maker_fee_rate=Decimal(".005"))
    target.host.products = MappingProxyType({BTC.key: changed})
    try:
        with pytest.raises((ValueError, RuntimeError), match="(?i)inventory|identity|settings|config|product"):
            await target.manager.restore(target.engine, [target.item], paper_runtime=target.host,
                                         readiness=lambda *args: True)
        assert target.manager.state == "QUARANTINED"
    finally:
        await close(target)


@pytest.mark.asyncio
async def test_readiness_cannot_mutate_restored_plugin_behind_cached_proof(tmp_path):
    source = fixture(tmp_path, fresh=True)
    input_book(source, 1)
    await source.manager.save(source.engine, paper_runtime=source.host)
    await close(source)
    target = fixture(tmp_path, fresh=False)
    invoked = []
    def mutate_plugin(*args):
        invoked.append("readiness")
        target.host._jobs["one"].instance.count = 999
        return True
    try:
        with pytest.raises((ValueError, RuntimeError)):
            await target.manager.restore(target.engine, [target.item], paper_runtime=target.host,
                                         readiness=mutate_plugin)
        assert invoked == ["readiness"], "restore failed before the intended plugin fault"
        assert target.manager.state == "QUARANTINED"
        assert not target.observed
    finally:
        await close(target)


def legacy_etf(root, initialization_calls):
    import asyncio
    from portfolio.etf_journal import EtfJournal
    etf = EtfTradingLoop.__new__(EtfTradingLoop)
    etf.journal = EtfJournal(root / "legacy-etf.sqlite")
    etf.journal.initialize(Decimal(3000), "hashed-mock-identity")
    etf.journal.db.execute("INSERT OR IGNORE INTO metadata VALUES(?,?)",
                           ("mock2_account_identity", "hashed-mock2-identity"))
    etf._day = "2026-10-23"
    etf.phase = "waiting_session"
    etf._runner_task = None
    etf._closing = False
    etf._lock = asyncio.Lock()
    etf._checkpoint_recovery_state = "IDLE"
    etf._checkpoint_initialized = True
    etf._check_ownership = lambda: None
    etf._feed_invalid, etf._feed_snapshots = set(), set()
    etf._feed_history_ready = False
    etf._feed_gap_generation = 0
    etf.quotes, etf.today, etf.features = {}, {}, {}
    async def initialize_without_accounts():
        initialization_calls.append(etf._checkpoint_recovery_state)
        assert etf._checkpoint_recovery_state == "RECOVERING"
        etf._day = "2026-10-23"
        # The existing initialize contract prepares this phase, never directly enables alpha.
        etf.phase = "waiting_session"
    etf.initialize = initialize_without_accounts
    return etf


@pytest.mark.asyncio
async def test_real_legacy_etf_journal_and_paper_share_recovery_boundary(tmp_path):
    calls = []
    source = fixture(tmp_path, fresh=True)
    etf_source = legacy_etf(tmp_path, calls)
    input_book(source, 1)
    await source.manager.save(source.engine, paper_runtime=source.host, etf=etf_source)
    balances = {sid: etf_source.journal.book(sid)["cash"] for sid in ("C3", "KP", "KD")}
    etf_source.journal.db.close()
    await close(source)
    target = fixture(tmp_path, fresh=False)
    etf_target = legacy_etf(tmp_path, calls)
    etf_target._day = ""  # Fresh EtfTradingLoop constructor, before initialize.
    try:
        await target.manager.restore(target.engine, [target.item], paper_runtime=target.host,
                                     etf=etf_target, readiness=lambda *args: True)
        assert target.manager.state == "READY"
        assert etf_target._checkpoint_recovery_state == "READY"
        assert calls == ["RECOVERING"]
        assert {sid: etf_target.journal.book(sid)["cash"] for sid in balances} == balances
        assert target.host._jobs["one"].instance.checkpoint_state() == {"count": 1}
        assert not etf_target.quotes
    finally:
        etf_target.journal.db.close()
        await close(target)


@pytest.mark.asyncio
async def test_real_driver_requires_explicit_readiness_before_setup(tmp_path):
    from vibetrading.local_runtime import run_engine
    source = fixture(tmp_path, fresh=True)
    try:
        with pytest.raises(ValueError, match="(?i)readiness"):
            await run_engine(SimpleNamespace(etf_enabled=False), SimpleNamespace(),
                             paper_runtime=source.host, runtime_checkpoint=source.manager)
    finally:
        await close(source)


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt", [False, True])
async def test_real_driver_starts_transport_and_etf_only_after_group_ready(tmp_path, monkeypatch, corrupt):
    from dataclasses import asdict
    import vibetrading.local_runtime as driver
    import vibetrading.local_observation as observations
    import market.local_snapshot as snapshots
    source = fixture(tmp_path, fresh=True)
    etf_source = legacy_etf(tmp_path, [])
    input_book(source, 1)
    engine_budgets = asdict(source.engine.budgets)
    await source.manager.save(source.engine, paper_runtime=source.host, etf=etf_source)
    etf_source.journal.db.close()
    await close(source)
    target = fixture(tmp_path, fresh=False)
    if corrupt:
        (tmp_path / "runtime.json").write_bytes(b'{"torn":')
    target.item.delivery = None
    target.item.symbols = "BTCUSDT"
    plan = SimpleNamespace(etf_enabled=True, producers=[target.item], engine=engine_budgets,
        endpoint=tmp_path / "local.sock", transport={}, allowed_uid=0,
        observation=SimpleNamespace(producer="observations", incarnation="run", pipeline=target.item.pipeline),
        snapshot=dict(max_bytes=262144,max_instruments=8,book_depth=10,interval_seconds=.01,max_age_seconds=1))
    events, etfs = [], []
    class Observation:
        def __init__(self, **kwargs): pass
        async def connect(self): events.append("observation")
        async def close(self): pass
    class Server:
        def __init__(self, *args, **kwargs): pass
        async def start(self):
            assert target.manager.state == "READY"
            assert target.host.recovery_state == "READY"
            assert etfs[0]._checkpoint_recovery_state == "READY"
            events.append("transport")
        async def close(self): pass
    class Snapshot:
        def __init__(self, *args, **kwargs): pass
        async def start(self):
            assert "transport" in events
        async def close(self): pass
    def etf_factory(**kwargs):
        etf = legacy_etf(tmp_path, [])
        etf._day = ""  # Real fresh-constructor lifecycle.
        async def run(**options):
            assert options == {"initialized": True}
            assert target.manager.state == etf._checkpoint_recovery_state == "READY"
            assert target.host.hot_path.apply_seq == 1
            events.append("etf-timer")
        async def cleanup():
            await kwargs["event_source"].aclose()
            etf.journal.db.close()
        etf.run, etf.close = run, cleanup
        etfs.append(etf)
        return etf
    monkeypatch.setattr(observations, "LocalObservationBus", Observation)
    monkeypatch.setattr(driver, "LocalTransportServer", Server)
    monkeypatch.setattr(snapshots, "LocalSnapshotWriter", Snapshot)
    monkeypatch.setattr(driver.time, "time_ns", lambda: T)
    try:
        if corrupt:
            with pytest.raises((ValueError, RuntimeError)):
                await driver.run_engine(plan, SimpleNamespace(state_dir=tmp_path, env={}),
                    paper_runtime=target.host, runtime_checkpoint=target.manager,
                    etf_factory=etf_factory, recovery_readiness=lambda *args: True)
            assert "transport" not in events and "etf-timer" not in events
            assert target.manager.state == "QUARANTINED"
        else:
            await driver.run_engine(plan, SimpleNamespace(state_dir=tmp_path, env={}),
                paper_runtime=target.host, runtime_checkpoint=target.manager,
                etf_factory=etf_factory, recovery_readiness=lambda *args: True)
            assert events[-2:] == ["transport", "etf-timer"]
    finally:
        await close(target)


@pytest.mark.asyncio
async def test_wall_clock_regression_is_rejected_instead_of_clamped_to_saved_time(tmp_path):
    source = fixture(tmp_path, fresh=True)
    input_book(source, 1)
    await source.manager.save(source.engine, paper_runtime=source.host)
    await close(source)
    target = fixture(tmp_path, fresh=False)
    target.now[0] = T - 1
    try:
        with pytest.raises((ValueError, RuntimeError)) as caught:
            await target.manager.restore(target.engine, [target.item], paper_runtime=target.host,
                                         readiness=lambda *args: True)
        causes, error = [], caught.value
        while error is not None:
            causes.append(str(error).lower())
            error = error.__cause__
        assert any("clock" in reason and "regress" in reason for reason in causes)
        assert target.manager.state == "QUARANTINED"
        assert not target.observed
    finally:
        await close(target)


@pytest.mark.asyncio
async def test_unguarded_etf_rechecks_checkpoint_gate_after_observation_await():
    calls = []
    etf = EtfTradingLoop.__new__(EtfTradingLoop)
    etf._checkpoint_recovery_state = "READY"
    etf.execution_guards_enabled = lambda: False
    etf._check_ownership = lambda: None
    async def observation(_intent):
        calls.append("observation")
        etf._checkpoint_recovery_state = "RECOVERING"
    async def submit(_intent):
        calls.append("submit")
        return {"unwanted": "new-intent"}
    etf.bus = SimpleNamespace(publish_order_intent=observation)
    etf.journal = SimpleNamespace(ambiguous=lambda *args: calls.append("ambiguous"))
    broker = SimpleNamespace(submit_order=submit)
    etf.brokers = {"mock": broker}
    with pytest.raises(EtfDispatchBlocked):
        await etf._submit_staged(broker, SimpleNamespace(intent_id="never-send", metadata={"account_scope": "mock"}))
    assert "submit" not in calls


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
async def test_checkpoint_output_cannot_replace_its_existing_economic_journal(tmp_path, suffix):
    source = fixture(tmp_path, fresh=True)
    input_book(source, 1)
    collision = RuntimeCheckpoint(source.ledger.path.with_name(source.ledger.path.name + suffix), source.manager.codec,
        max_bytes=4_000_000, max_journal_rows=10000, max_journal_bytes=8_000_000)
    try:
        with pytest.raises((ValueError, RuntimeError), match="(?i)journal|path|output|checkpoint"):
            await collision.save(source.engine, paper_runtime=source.host)
        assert source.ledger.path.read_bytes()[:16] == b"SQLite format 3" + bytes([0])
        assert source.ledger.report()["balances"] == {"USDT": "100000"}
    finally:
        await close(source)


@pytest.mark.asyncio
@pytest.mark.parametrize("second_journal_ahead", [False, True])
async def test_two_real_account_journals_restore_together_or_fence_together(tmp_path, second_journal_ahead):
    source = fixture(tmp_path, fresh=True, two_accounts=True)
    input_book(source, 1)
    await source.manager.save(source.engine, paper_runtime=source.host)
    await close(source)
    target = fixture(tmp_path, fresh=False, two_accounts=True)
    try:
        if second_journal_ahead:
            target.additional[0].claim_strategy_event(
                "one", "config-1", "unproven-second-account", {"probe": "changed"})
        journals = [target.ledger, *target.additional]
        reports = [ledger.report() for ledger in journals]
        if second_journal_ahead:
            with pytest.raises((ValueError, RuntimeError)):
                await target.manager.restore(target.engine, [target.item], paper_runtime=target.host,
                                             readiness=lambda *args: True)
            assert target.manager.state == "QUARANTINED"
            from market.local_engine import EngineError
            with pytest.raises(EngineError, match="engine_closed"):
                input_book(target, 2)
            target.host.advance(T+100)
            assert not target.observed
            assert [ledger.report() for ledger in journals] == reports
            assert target.host._recovery_state == "QUARANTINED"
        else:
            await target.manager.restore(target.engine, [target.item], paper_runtime=target.host,
                                         readiness=lambda *args: True)
            assert target.manager.state == "READY"
            assert [ledger.report() for ledger in journals] == reports
            assert target.host._jobs["one"].instance.checkpoint_state() == {"count": 1}
            input_book(target, 2)
            assert target.host._jobs["one"].instance.checkpoint_state() == {"count": 2}
            assert len(target.ledger.report()["orders"]) == 2
            assert target.additional[0].has_strategy_dispatch("one")
    finally:
        await close(target)
