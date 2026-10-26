"""TX04B: existing continuous Paper input via the engine watchdog only.

All market/account fixtures are synthetic and reuse the existing real local
engine, Paper runtime, matcher, and portfolio ledger helpers.
"""
import asyncio
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from test_local_runtime import document, event as runtime_event
from test_paper_execution import submit as paper_submit
from test_strategy_runtime import (T, BTC, book, budget, deliver, ledger_at, matcher_for,
                                   registration, runtime, spec, trade)
from feeds.feed_recovery import EngineCursor, RecoveryFrame
from market.hot_path import HotPathStore
from vibetrading.local_config import LocalRuntimePlan
from market.local_engine import EngineBudgets, EngineError, LocalMarketEngine
from core.models import OrderDestination, OrderIntent, Side
from vibetrading.strategy_runtime import PaperStrategyRuntime
from execution.trading_contracts import ActionKind, ExecutionAction, TradingMode


def engine_budgets():
    return EngineBudgets(1, 8, 8192, 16, 65536, 10_000_000_000, 8, 2,
                         20_000_000_000, .01)


def stale_runtime(tmp_path, *, age=5):
    ledger = ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
    matcher, now = matcher_for(ledger), [T]
    host = runtime(ledger, matcher, now, budgets=budget(max_feed_age_ns=age))
    return ledger, matcher, host, now


@pytest.mark.asyncio
async def test_engine_watchdog_tick_callback_validates_epoch_and_closes_cleanly():
    ticks, epoch = [], [123]
    engine = LocalMarketEngine(producer_providers={"synthetic": "scripted"},
        budgets=engine_budgets(), on_invalidate=lambda *args: None,
        hot_path=HotPathStore(), snapshot_clock=lambda: epoch[0],
        on_tick=ticks.append, clock=lambda: 100)
    await engine.start()
    for _ in range(100):
        if ticks:
            break
        await asyncio.sleep(.001)
    assert ticks == [123]
    await engine.close()
    assert engine._watchdog.done()
    observed = list(ticks)
    await asyncio.sleep(.03)
    assert ticks == observed

    async def async_tick(now_ns):
        return now_ns

    class NotCallable:
        pass

    for callback, error in ((async_tick, TypeError), (NotCallable(), TypeError)):
        with pytest.raises(error, match="synchronous"):
            LocalMarketEngine(producer_providers={"synthetic": "scripted"},
                budgets=engine_budgets(), on_invalidate=lambda *args: None,
                hot_path=HotPathStore(), on_tick=callback)

    invalid, clock = [], [0]
    broken = LocalMarketEngine(producer_providers={"synthetic": "scripted"},
        budgets=engine_budgets(), on_invalidate=lambda *args: None,
        hot_path=HotPathStore(), snapshot_clock=lambda: clock[0],
        on_tick=invalid.append, clock=lambda: 100)
    await broken.start()
    result = await asyncio.wait_for(
        asyncio.gather(broken._watchdog, return_exceptions=True), timeout=3)
    assert isinstance(result[0], EngineError)
    assert not invalid
    await broken.close()


@pytest.mark.asyncio
async def test_run_engine_wires_paper_advance_only_when_paper_selected(tmp_path, monkeypatch):
    import vibetrading.local_runtime as runtime_module

    plan = LocalRuntimePlan.from_document(document(tmp_path))
    instrument = runtime_event(1).instrument
    captured, started = [], asyncio.Event()

    class Paper:
        def __init__(self):
            self.hot_path = HotPathStore()
            self.known = {instrument.key: instrument}
            self.advanced = []

        def process_applied(self, result, snapshot):
            pass

        def invalidate_inputs(self, keys, reason, now_ns):
            pass

        def advance(self, now_ns):
            self.advanced.append(now_ns)

    def factory(**kwargs):
        captured.append(kwargs)
        return LocalMarketEngine(**kwargs)

    class Server:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self):
            started.set()

        async def close(self):
            pass

    monkeypatch.setattr(runtime_module, "LocalMarketEngine", factory)
    monkeypatch.setattr(runtime_module, "LocalTransportServer", Server)
    paper = Paper()
    task = asyncio.create_task(runtime_module.run_engine(plan,
        SimpleNamespace(state_dir=tmp_path), paper_runtime=paper))
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        assert captured[-1]["on_tick"] == paper.advance
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    captured.clear()
    started.clear()
    task = asyncio.create_task(runtime_module.run_engine(plan,
        SimpleNamespace(state_dir=tmp_path)))
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        assert captured[-1]["on_tick"] is None
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_idle_advance_expires_orders_without_any_native_event(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        host = runtime(ledger, matcher, now)
        host.register(registration(ledger, "idle", observed))
        deliver(host, now, book())
        identity = next(iter(matcher.report()["open_orders"]))
        assert D(ledger.get(identity)["reservation"]) > 0
        host.advance(T + 100_001)
        assert matcher.report()["open_orders"] == {}
        assert ledger.get(identity)["state"] == "EXPIRED"
        assert D(ledger.get(identity)["reservation"]) == 0
    finally:
        ledger.close()


def test_runtime_freshness_fences_pending_ioc_before_native_stale_budget(tmp_path):
    ledger, matcher, host, now = stale_runtime(tmp_path)
    try:
        host.process_event(book())
        created, _ = paper_submit(matcher, "one", price=101, quantity=4, now=T,
                                  post_only=False, time_in_force="IOC")
        assert created and "one" in matcher.report()["open_orders"]
        reservation = D(ledger.get("one")["reservation"])
        assert reservation > 0
        # The native matcher would still accept this book, so only the runtime
        # freshness check can prevent a cached-depth taker fill at T+11.
        assert matcher.report()["limits"]["stale_after_ns"] > 11
        now[0] = T + 11
        host.advance(T + 11)
        assert matcher.report()["fills"] == 0
        assert matcher.report()["open_orders"] == {}
        assert ledger.get("one")["state"] == "REJECTED"
        assert D(ledger.get("one")["reservation"]) == 0
        assert ledger.position("strategy", BTC.key) == 0
        assert D(ledger.report()["balances"]["USDT"]) == D(100000)
        assert BTC.key in host.report()["invalid_inputs"]
    finally:
        ledger.close()


def test_late_stale_event_fences_before_any_cached_depth_fill(tmp_path):
    ledger, matcher, host, now = stale_runtime(tmp_path)
    try:
        host.process_event(book())
        created, _ = paper_submit(matcher, "one", price=101, quantity=4, now=T,
                                  post_only=False, time_in_force="IOC")
        assert created and "one" in matcher.report()["open_orders"]
        now[0] = T + 11
        late = book(T, sequence=2).model_copy(update={"event_id": "late-stale-book"})
        host.process_event(late)
        assert matcher.report()["fills"] == 0
        assert matcher.report()["open_orders"] == {}
        assert ledger.get("one")["state"] == "REJECTED"
        assert D(ledger.get("one")["reservation"]) == 0
        assert ledger.position("strategy", BTC.key) == 0
        assert BTC.key in host.report()["invalid_inputs"]
    finally:
        ledger.close()


def test_fresh_trade_cannot_consume_stale_reference_book_for_pending_ioc(tmp_path):
    ledger, matcher, host, now = stale_runtime(tmp_path)
    try:
        observed = []
        def ioc_actions(signal, view, config):
            identity = signal.signal_id
            intent = OrderIntent(intent_id=identity, job_id="ioc",
                signal_id=signal.signal_id, destination=OrderDestination.REAL,
                instrument=BTC, side=Side.BUY, created_ts_ns=signal.event_ts_ns,
                client_order_id="client-"+identity, quantity=D(1), limit_price=D(101))
            return [ExecutionAction("action-"+identity, ActionKind.NEW, ledger.scope,
                "ioc", "config-1", TradingMode.PAPER, signal.valid_until_ts_ns,
                intent=intent, post_only=False, time_in_force="IOC")]
        host.register(registration(ledger, "ioc", observed, builder=ioc_actions))
        deliver(host, now, book())
        assert len(observed) == 1
        identity = next(iter(matcher.report()["open_orders"]))
        assert matcher.report()["open_orders"][identity]["arrival_ns"] == T + 10
        assert D(ledger.get(identity)["reservation"]) > 0

        # The trade itself is fresh, but the only reference book is from T.
        # It must not advance the pending IOC arrival into cached depth.
        now[0] = T + 11
        host.process_event(trade(T + 11, side=Side.SELL, price=100))
        assert matcher.report()["fills"] == 0
        assert matcher.report()["open_orders"] == {}
        assert ledger.get(identity)["state"] == "REJECTED"
        assert D(ledger.get(identity)["reservation"]) == 0
        assert ledger.position("strategy", BTC.key) == 0
        assert D(ledger.report()["balances"]["USDT"]) == D(100000)
        assert BTC.key in host.report()["invalid_inputs"]
    finally:
        ledger.close()


def test_stale_feed_reopens_only_on_fresh_full_book_not_a_trade(tmp_path):
    ledger, matcher, host, now = stale_runtime(tmp_path)
    try:
        observed = []
        host.register(registration(ledger, "dep", observed))
        deliver(host, now, book())
        assert len(observed) == 1 and len(ledger.active_orders()) == 1
        now[0] = T + 11
        host.advance(T + 11)
        assert BTC.key in host.report()["invalid_inputs"]
        assert host.report()["strategies"]["dep"]["state"] == "WARMING"

        # A fresh trade is not a full projected book and cannot reopen the feed.
        host.process_event(trade(T + 11, side=Side.SELL, price=98))
        assert BTC.key in host.report()["invalid_inputs"]
        assert len(observed) == 1
        assert matcher.report()["fills"] == 0

        fresh = book(T + 12, sequence=2).model_copy(update={"sync_generation": 2})
        now[0] = T + 12
        host.process_event(fresh)
        assert BTC.key not in host.report()["invalid_inputs"]
        assert host.report()["strategies"]["dep"]["state"] == "PAPER"
        assert len(observed) == 2
        assert BTC.key not in matcher.report()["blocked"]
    finally:
        ledger.close()


def test_unaffected_second_feed_stays_independent_of_a_stale_feed(tmp_path):
    other = BTC.model_copy(update={"raw_symbol": "ETHUSDT", "std_symbol": "ETHUSDT"})
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = PaperStrategyRuntime(hot_path=HotPathStore(),
            accounts={ledger.scope.key: matcher},
            products={BTC.key: spec(), other.key: spec(other)},
            known_instruments={BTC.key: BTC, other.key: other}, reference_feeds={},
            budgets=budget(max_feed_age_ns=5), settings={"fixture": "two feeds"},
            mode=TradingMode.PAPER, phase="paper", now_ns=lambda: now[0])
        host.process_event(book())
        now[0] = T + 10
        host.process_event(book(T + 10, instrument=other))
        host.advance(T + 11)
        assert BTC.key in host.report()["invalid_inputs"]
        assert other.key not in host.report()["invalid_inputs"]
        assert BTC.key in matcher.report()["blocked"]
        assert other.key not in matcher.report()["blocked"]
    finally:
        ledger.close()


def test_replay_recovery_and_permission_fences_never_add_risk(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        replay = runtime(ledger, matcher, now, phase="replay",
                         budgets=budget(max_feed_age_ns=5))
        before = matcher.report()
        replay.process_event(book())
        replay.advance(T + 1)
        assert matcher.report() == before

        host = runtime(ledger, matcher, now, budgets=budget(max_feed_age_ns=5))
        host.begin_checkpoint_recovery()
        before = matcher.report()
        host.advance(T + 1)
        assert matcher.report() == before
        host.quarantine_checkpoint_recovery("fixture fault")
        host.advance(T + 2)
        assert matcher.report() == before

        denied_ledger = ledger_at(tmp_path / "denied")
        try:
            denied_ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
            denied_matcher = matcher_for(denied_ledger)
            denied_now, observed = [T], []
            denied = runtime(denied_ledger, denied_matcher, denied_now)
            denied.register(registration(denied_ledger, "denied", observed,
                                         permission=False))
            deliver(denied, denied_now, book())
            denied.advance(T + 20)
            assert not denied_ledger.report()["orders"]
            assert not observed
            assert denied.report()["strategies"]["denied"]["state"] == "BLOCKED"
        finally:
            denied_ledger.close()
    finally:
        ledger.close()


def test_fresh_other_instrument_book_does_not_fill_btc_stale_depth(tmp_path):
    other = BTC.model_copy(update={"raw_symbol": "ETHUSDT", "std_symbol": "ETHUSDT"})
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = PaperStrategyRuntime(hot_path=HotPathStore(),
            accounts={ledger.scope.key: matcher},
            products={BTC.key: spec(), other.key: spec(other)},
            known_instruments={BTC.key: BTC, other.key: other}, reference_feeds={},
            budgets=budget(max_feed_age_ns=5), settings={"fixture": "two feeds"},
            mode=TradingMode.PAPER, phase="paper", now_ns=lambda: now[0])
        host.process_event(book())
        created, _ = paper_submit(matcher, "btc", price=101, quantity=1, now=T,
                                  post_only=False, time_in_force="IOC")
        assert created and len(matcher.report()["open_orders"]) == 1
        now[0] = T + 11
        host.process_event(book(T + 11, instrument=other))
        assert matcher.report()["fills"] == 0
        assert matcher.report()["open_orders"] == {}
        assert ledger.get("btc")["state"] == "REJECTED"
        assert D(ledger.get("btc")["reservation"]) == 0
        assert ledger.position("strategy", BTC.key) == 0
        assert D(ledger.report()["balances"]["USDT"]) == D(100000)
        assert BTC.key in host.report()["invalid_inputs"]
        assert other.key not in host.report()["invalid_inputs"]
        assert BTC.key in matcher.report()["blocked"]
        assert other.key not in matcher.report()["blocked"]
    finally:
        ledger.close()


def test_fresh_same_feed_trade_after_other_book_does_not_fill_btc(tmp_path):
    other = BTC.model_copy(update={"raw_symbol": "ETHUSDT", "std_symbol": "ETHUSDT"})
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = PaperStrategyRuntime(hot_path=HotPathStore(),
            accounts={ledger.scope.key: matcher},
            products={BTC.key: spec(), other.key: spec(other)},
            known_instruments={BTC.key: BTC, other.key: other}, reference_feeds={},
            budgets=budget(max_feed_age_ns=5), settings={"fixture": "two feeds"},
            mode=TradingMode.PAPER, phase="paper", now_ns=lambda: now[0])
        host.process_event(book())
        created, _ = paper_submit(matcher, "btc", price=101, quantity=1, now=T,
                                  post_only=False, time_in_force="IOC")
        assert created and len(matcher.report()["open_orders"]) == 1
        now[0] = T + 7
        host.process_event(book(T + 7, instrument=other))
        assert other.key not in host.report()["invalid_inputs"]
        assert other.key not in matcher.report()["blocked"]
        now[0] = T + 11
        host.process_event(trade(T + 11, side=Side.SELL, price=100, instrument=other))
        assert matcher.report()["fills"] == 0
        assert matcher.report()["open_orders"] == {}
        assert ledger.position("strategy", BTC.key) == 0
        assert D(ledger.report()["balances"]["USDT"]) == D(100000)
        assert BTC.key in host.report()["invalid_inputs"]
        assert other.key not in host.report()["invalid_inputs"]
        assert BTC.key in matcher.report()["blocked"]
        assert other.key not in matcher.report()["blocked"]
    finally:
        ledger.close()


def test_new_btc_book_never_fills_old_depth_and_only_explicit_generation_reopens(tmp_path):
    for generation, reopened in ((2, True), (1, False)):
        ledger = ledger_at(tmp_path / ("generation-" + str(generation)))
        try:
            ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
            matcher, now = matcher_for(ledger), [T]
            host = PaperStrategyRuntime(hot_path=HotPathStore(),
                accounts={ledger.scope.key: matcher}, products={BTC.key: spec()},
                known_instruments={BTC.key: BTC}, reference_feeds={},
                budgets=budget(max_feed_age_ns=5), settings={"fixture": "btc"},
                mode=TradingMode.PAPER, phase="paper", now_ns=lambda: now[0])
            host.process_event(book())
            created, _ = paper_submit(matcher, "btc", price=101, quantity=1, now=T,
                                      post_only=False, time_in_force="IOC")
            assert created and len(matcher.report()["open_orders"]) == 1
            now[0] = T + 11
            incoming = book(T + 11, sequence=2)
            if generation == 2:
                incoming = incoming.model_copy(update={"sync_generation": 2})
            host.process_event(incoming)
            assert matcher.report()["fills"] == 0
            assert matcher.report()["open_orders"] == {}
            assert ledger.get("btc")["state"] == "REJECTED"
            assert D(ledger.get("btc")["reservation"]) == 0
            assert ledger.position("strategy", BTC.key) == 0
            assert D(ledger.report()["balances"]["USDT"]) == D(100000)
            if reopened:
                assert BTC.key not in host.report()["invalid_inputs"]
                assert BTC.key not in matcher.report()["blocked"]
                assert host.report()["account_faults"] == {}
            else:
                assert BTC.key in host.report()["invalid_inputs"]
                assert BTC.key in matcher.report()["blocked"]
        finally:
            ledger.close()
