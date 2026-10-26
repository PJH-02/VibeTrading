"""Scripted local PAPER lifecycle and dispatch; no services or alpha strategy."""
from dataclasses import replace
from decimal import Decimal as D
import socket
from types import ModuleType

import pytest

from execution.execution_ledger import AccountScope
from feeds.feed_recovery import EngineCursor, RecoveryFrame
from market.hot_path import HotPathStore
from market.local_engine import EngineBudgets, LocalMarketEngine
from core.models import AssetClass, Bar1mEvent, InstrumentId, OrderBookEvent, OrderBookLevel, OrderDestination, OrderIntent, Side, Signal, SignalAction, TradeEvent, Venue
from execution.paper_execution import PaperExecution
from portfolio.portfolio_ledger import PortfolioLedger
from vibetrading.strategy_contract import StrategyModule
from runtime.strategy_runtime import PaperStrategyRuntime, RuntimeBudgets, StrategyRegistration, StrategyRisk
from execution.trading_contracts import ActionKind, ExecutionAction, ProductKind, ProductSpec, TradingMode

T = 1_800_000_000_000_000_000
BTC = InstrumentId(venue=Venue.BYBIT, asset_class=AssetClass.CRYPTO_PERP,
                   raw_symbol="BTCUSDT", std_symbol="BTCUSDT", currency="USDT")


def ledger_at(root, instrument=BTC):
    return PortfolioLedger(root / "paper" / "account.sqlite", AccountScope(instrument.venue.value,
        "synthetic-paper", "perpetual" if instrument.asset_class == AssetClass.CRYPTO_PERP else "cash", "paper"),
        owner_root=root, mode=TradingMode.PAPER, max_orders=100, max_fills=100, max_events=1000,
        max_document_bytes=1_000_000, max_db_bytes=16_000_000, max_wal_bytes=16_000_000)


def matcher_for(ledger):
    return PaperExecution(ledger, entry_latency_ns=10, cancel_latency_ns=5,
        max_open_orders=16, max_depth=8, max_receipts=100, max_state_bytes=500_000,
        stale_after_ns=1_000_000_000)


def spec(instrument=BTC):
    cash = instrument.asset_class != AssetClass.CRYPTO_PERP
    return ProductSpec(instrument, ProductKind.CASH if cash else ProductKind.LINEAR_PERPETUAL,
        D(1) if cash else D(10), D(1), D(1), D(1), instrument.currency, instrument.currency,
        instrument.currency, "net", "cash" if cash else "isolated", "synthetic fee",
        "explicit simulated funding only", "synthetic margin", D(".001"), D(".002"))


def budget(**changes):
    values = dict(max_jobs=4, max_accounts=2, max_required_feeds=8, max_signals_per_callback=2,
        max_actions_per_callback=2, max_pending_actions=4, max_seen_actions=100,
        max_config_bytes=32_000, max_signal_bytes=8192, max_action_bytes=16_000,
        max_callback_ns=1_000_000_000, max_feed_age_ns=1_000_000_000)
    return RuntimeBudgets(**{**values, **changes})


def runtime(ledger, matcher, now, *, instrument=BTC, reference=None, phase="paper", budgets=None,
            callback_clock_ns=lambda: 0):
    feed = reference or instrument
    return PaperStrategyRuntime(hot_path=HotPathStore(), accounts={ledger.scope.key: matcher},
        products={instrument.key: spec(instrument)}, known_instruments={feed.key: feed},
        reference_feeds={instrument.key: feed.key} if reference else {}, budgets=budgets or budget(),
        settings={"fixture": "explicit local inputs"}, mode=TradingMode.PAPER, phase=phase,
        now_ns=lambda: now[0], callback_clock_ns=callback_clock_ns)


def module(sid, observed, *, instrument=BTC, warmup=0, fault=False):
    source = ModuleType("scripted_"+sid)
    class Dummy:
        def on_event(self, event, data):
            observed.append((sid, data.hot_path))
            if fault:
                raise RuntimeError("scripted callback fault")
            return [Signal(strategy_id=sid, instrument=event.instrument,
                action=SignalAction.ENTER_LONG, event_ts_ns=event.receive_ts_ns,
                reference_price=D(100), valid_until_ts_ns=event.receive_ts_ns+100_000,
                source_event_id=event.event_id)]
    source.create_strategy = Dummy
    return StrategyModule(source, sid, [{"instrument_key": instrument.key, "event_type": "order_book"}],
                          {"bars": warmup})


def registration(ledger, sid, observed, *, instrument=BTC, reference=None, side=Side.BUY,
                 warmup=0, fault=False, builder=None, permission=True):
    feed = reference or instrument
    def actions(signal, view, config):
        identity = sid+"-"+signal.signal_id
        intent = OrderIntent(intent_id=identity, job_id=sid, signal_id=signal.signal_id,
            destination=OrderDestination.REAL, instrument=instrument, side=side,
            created_ts_ns=signal.event_ts_ns, client_order_id="client-"+identity,
            quantity=D(config["quantity"]), limit_price=D(99 if side == Side.BUY else 101))
        return [ExecutionAction("action-"+identity, ActionKind.NEW, ledger.scope, sid, "config-1",
            TradingMode.PAPER, signal.valid_until_ts_ns, intent=intent, post_only=True)]
    return StrategyRegistration(sid, module(sid, observed, instrument=feed, warmup=warmup, fault=fault),
        "strategy-1", "config-1", {"quantity": "1"}, {"research_id": "synthetic-fixture"},
        permission, (feed.key,), warmup, (ledger.scope.key,),
        {ledger.scope.key: StrategyRisk(D(0), D(100000), D(100000), D(100000), D(100000),
                                      None if instrument.asset_class != AssetClass.CRYPTO_PERP else D(".1"))},
        builder or actions)


def book(now=T, *, instrument=BTC, sequence=1):
    return OrderBookEvent(instrument=instrument, venue=instrument.venue, provider="scripted",
        event_id="book-"+str(now), receive_ts_ns=now, wallclock_ts_ns=now, source_ts_ns=now,
        sequence=sequence, final_update_id=sequence, sync_generation=1, depth=1,
        bids=[OrderBookLevel(price=D(99), quantity=D(5))],
        asks=[OrderBookLevel(price=D(101), quantity=D(5))])


def trade(now, *, side, price, instrument=BTC):
    return TradeEvent(instrument=instrument, venue=instrument.venue, provider="scripted",
        event_id="trade-"+str(now), receive_ts_ns=now, wallclock_ts_ns=now, source_ts_ns=now,
        price=D(price), quantity=D(1), aggressor_side=side)


def deliver(host, now, event):
    now[0] = event.receive_ts_ns
    return host.process_event(event)


def test_two_strategies_one_account_one_snapshot_opposite_virtual_fills_and_no_network(tmp_path, monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append("external call")
        raise AssertionError("PAPER must not make an external connection")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        host = runtime(ledger, matcher, now)
        host.register(registration(ledger, "long", observed))
        host.register(registration(ledger, "short", observed, side=Side.SELL))
        event = book()
        assert len(deliver(host, now, event)) == 2
        assert len(observed) == 2 and observed[0][1] is observed[1][1]
        assert host.hot_path.apply_seq == 1
        assert deliver(host, now, event) == ()
        assert len(observed) == 2 and len(ledger.report()["orders"]) == 2
        deliver(host, now, trade(T+20, side=Side.SELL, price=98))
        deliver(host, now, trade(T+21, side=Side.BUY, price=102))
        report = ledger.report()
        assert D(report["physical_positions"][BTC.key]) == 0
        assert D(report["virtual_positions"]["long"][BTC.key]) == 1
        assert D(report["virtual_positions"]["short"][BTC.key]) == -1
        duplicate = trade(T+21, side=Side.BUY, price=102)
        deliver(host, now, duplicate)
        assert matcher.report()["fills"] == 2
        assert not calls and host.report()["external_mutations"] == 0
        assert host.report()["production_gate"] == "BLOCKED"
    finally:
        ledger.close()


def test_cold_warmup_and_replay_emit_zero_paper_orders(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        host = runtime(ledger, matcher, now)
        host.register(registration(ledger, "warm", observed, warmup=1))
        deliver(host, now, book())
        assert not observed and not ledger.report()["orders"]
        host.prepare_history(BTC.key, [Bar1mEvent(instrument=BTC, venue=Venue.BYBIT, provider="scripted",
            receive_ts_ns=T-1, wallclock_ts_ns=T-1, source_ts_ns=T-1,
            open_ts_ns=T-60_000_000_001, close_ts_ns=T-1,
            open=D(100), high=D(101), low=D(99), close=D(100), volume=D(1))])
        deliver(host, now, book(T+1, sequence=2))
        assert len(ledger.report()["orders"]) == 1
        host.pause("warm", T+2)
        host.advance(T+20)
        observed.clear()
        replay = runtime(ledger, matcher, now, phase="replay")
        replay.register(registration(ledger, "replay", observed))
        before = matcher.report()
        deliver(replay, now, book(T+21, sequence=3))
        assert len(observed) == 1
        assert matcher.report() == before
        assert len(ledger.report()["orders"]) == 1
    finally:
        ledger.close()


def test_restart_claim_suppresses_callback_and_replay_restores_open_matching_state(tmp_path):
    now, observed = [T], []
    ledger = ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
    matcher = matcher_for(ledger)
    host = runtime(ledger, matcher, now)
    host.register(registration(ledger, "one", observed))
    original = book()
    deliver(host, now, original)
    matching_before = matcher.report()
    ledger.close()
    ledger = ledger_at(tmp_path)
    try:
        matcher = matcher_for(ledger)
        host = runtime(ledger, matcher, now)
        host.register(registration(ledger, "one", observed))
        deliver(host, now, original)
        assert len(observed) == 1 and len(ledger.report()["orders"]) == 1
        assert matcher.report() == matching_before
        replay = runtime(ledger, matcher, now, phase="replay")
        replay.register(registration(ledger, "one", observed))
        deliver(replay, now, original)
        deliver(replay, now, trade(T+20, side=Side.SELL, price=98))
        assert matcher.report() == matching_before
        assert replay.report()["restart_evidence"]["continuous_input_cursor"] == "MISSING_EVIDENCE"
    finally:
        ledger.close()


def test_callback_fault_isolated_and_cooperative_timeout_reports_production_block(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        host = runtime(ledger, matcher, now)
        host.register(registration(ledger, "bad", observed, fault=True))
        host.register(registration(ledger, "good", observed))
        assert len(deliver(host, now, book())) == 1
        assert host.report()["strategies"]["bad"]["state"] == "FAULTED"
        assert host.report()["strategies"]["good"]["state"] == "PAPER"
        host.halt("scripted stop", T+1)
        host.advance(T+20)
        ticks = iter([0, 1000])
        slow = runtime(ledger, matcher, now, budgets=budget(max_callback_ns=10), callback_clock_ns=lambda: next(ticks))
        slow.register(registration(ledger, "slow", observed))
        deliver(slow, now, book(T+21, sequence=2))
        report = slow.report()
        assert report["strategies"]["slow"]["reason"] == "TimeoutError"
        assert report["callback_isolation"] == "cooperative_return_time_only"
        assert report["production_gate"] == "BLOCKED"
    finally:
        ledger.close()


def test_kis_cash_uses_explicit_same_security_kiwoom_reference_without_changing_strategy_view(tmp_path):
    execution = InstrumentId(venue=Venue.KIS, asset_class=AssetClass.ETF,
        raw_symbol="069500", std_symbol="069500", currency="KRW")
    reference = execution.model_copy(update={"venue": Venue.KIWOOM})
    ledger = ledger_at(tmp_path, execution)
    try:
        ledger.seed_balance("KRW", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        host = runtime(ledger, matcher, now, instrument=execution, reference=reference)
        host.register(registration(ledger, "etf", observed, instrument=execution, reference=reference))
        assert len(deliver(host, now, book(instrument=reference))) == 1
        deliver(host, now, trade(T+20, side=Side.SELL, price=98, instrument=reference))
        assert ledger.position("etf", execution.key) == 1
        snapshot = observed[0][1]
        assert reference.key in snapshot.books and execution.key not in snapshot.books
        assert snapshot.books[reference.key].instrument.venue == Venue.KIWOOM
        assert host.report()["reference_feeds"] == {execution.key: reference.key}
        bad_reference = reference.model_copy(update={"currency": "USD"})
        with pytest.raises(ValueError, match="same explicit Korean security"):
            runtime(ledger, matcher, now, instrument=execution, reference=bad_reference)
    finally:
        ledger.close()


def test_process_applied_does_not_apply_twice_and_permission_blocks_dispatch(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        host = runtime(ledger, matcher, now)
        host.register(registration(ledger, "allowed", observed))
        host.register(registration(ledger, "denied", observed, permission=False))
        applied = host.hot_path.apply_versioned(book())
        snapshot = host.hot_path.snapshot(T, "PAPER")
        assert len(host.process_applied(applied, snapshot)) == 1
        assert host.hot_path.apply_seq == 1
        assert [sid for sid, _ in observed] == ["allowed"]
        assert host.report()["strategies"]["denied"]["state"] == "BLOCKED"
    finally:
        ledger.close()


def test_shared_engine_replay_phase_and_backfill_never_match_claim_or_build_actions(tmp_path, monkeypatch):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed, built = matcher_for(ledger), [T], [], []
        live = runtime(ledger, matcher, now)
        live.register(registration(ledger, "one", observed))
        deliver(live, now, book())
        before = matcher.report()
        host = runtime(ledger, matcher, now)
        host.register(registration(ledger, "one", observed, builder=lambda *args: built.append(args) or []))
        def forbidden_claim(*args, **kwargs):
            raise AssertionError("Replay must not claim a durable strategy event")
        monkeypatch.setattr(ledger, "claim_strategy_event", forbidden_claim)
        for phase, event in (("REPLAY", book(T+20, sequence=2)),
                             ("LIVE", book(T+21, sequence=3).model_copy(update={"ingest_mode": "backfill"}))):
            now[0] = event.receive_ts_ns
            applied = host.hot_path.apply_versioned(event)
            snapshot = host.hot_path.snapshot(now[0], phase)
            assert host.process_applied(applied, snapshot) == ()
            host.advance(T+30)
            assert matcher.report() == before
        assert len(observed) == 3 and not built
        assert host.report()["input_phase"] == "REPLAY"
        assert host.report()["restart_evidence"]["full_restart_gate"] == "BLOCKED"
    finally:
        ledger.close()


@pytest.mark.parametrize("phase", ["INVALID", "WARMING", "UNSPECIFIED"])
def test_shared_engine_nonlive_phase_cannot_advance_or_dispatch(tmp_path, phase):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        host = runtime(ledger, matcher, now)
        host.register(registration(ledger, "one", observed))
        before = matcher.report()
        applied = host.hot_path.apply_versioned(book())
        snapshot = host.hot_path.snapshot(T, phase)
        if phase == "UNSPECIFIED":
            with pytest.raises(ValueError, match="phase"):
                host.process_applied(applied, snapshot)
        else:
            assert host.process_applied(applied, snapshot) == ()
        host.advance(T+20)
        assert matcher.report() == before and not observed and not ledger.active_orders()
    finally:
        ledger.close()


def test_replay_callback_fault_does_not_cancel_existing_paper_orders(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        live = runtime(ledger, matcher, now)
        live.register(registration(ledger, "one", observed))
        deliver(live, now, book())
        before = matcher.report()
        replay = runtime(ledger, matcher, now)
        replay.register(registration(ledger, "one", observed, fault=True))
        applied = replay.hot_path.apply_versioned(book(T+20, sequence=2))
        assert replay.process_applied(applied, replay.hot_path.snapshot(T+20, "REPLAY")) == ()
        assert replay.report()["strategies"]["one"]["state"] == "FAULTED"
        assert matcher.report() == before
        assert ledger.get(next(iter(before["open_orders"])))["state"] == "OPEN"
    finally:
        ledger.close()


def test_shared_snapshot_clock_must_still_match_current_market_authority(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now)
        applied = host.hot_path.apply_versioned(book())
        snapshot = host.hot_path.snapshot(T, "LIVE")
        with pytest.raises(ValueError, match="cursor"):
            host.process_applied(applied, replace(snapshot, clock_version=snapshot.clock_version+1))
        before = matcher.report()
        host.advance(T+20)
        assert matcher.report() == before
    finally:
        ledger.close()


def test_input_invalidation_is_dependency_scoped_and_needs_a_fresh_book(tmp_path):
    other = BTC.model_copy(update={"raw_symbol": "ETHUSDT", "std_symbol": "ETHUSDT"})
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        host = PaperStrategyRuntime(hot_path=HotPathStore(), accounts={ledger.scope.key: matcher},
            products={BTC.key: spec(), other.key: spec(other)}, known_instruments={BTC.key: BTC, other.key: other},
            reference_feeds={}, budgets=budget(), settings={"fixture": "explicit local inputs"},
            mode=TradingMode.PAPER, phase="paper", now_ns=lambda: now[0])
        host.register(registration(ledger, "btc", observed))
        host.register(registration(ledger, "eth", observed, instrument=other))
        deliver(host, now, book())
        deliver(host, now, book(T+1, instrument=other))
        host.invalidate_inputs((BTC.key,), "producer_disconnected", T+2)
        report = host.report()
        assert report["strategies"]["btc"]["state"] == "WARMING"
        assert report["strategies"]["eth"]["state"] == "PAPER"
        assert set(matcher.report()["blocked"]) == {BTC.key}
        before = matcher.report()
        host.advance(T+3)
        assert matcher.report() == before
        # An unrelated fresh input remains useful; it cannot clear BTC's fence.
        deliver(host, now, book(T+20, instrument=other, sequence=2))
        assert host.report()["strategies"]["eth"]["callbacks"] == 2
        assert BTC.key in host.report()["invalid_inputs"]
        deliver(host, now, trade(T+21, side=Side.SELL, price=98))
        assert BTC.key in host.report()["invalid_inputs"]
        assert host.report()["strategies"]["btc"]["callbacks"] == 1
        # Explicitly new native generation is the matcher's independent resync proof.
        recovered = book(T+22, sequence=3).model_copy(update={"sync_generation": 2})
        deliver(host, now, recovered)
        assert BTC.key not in host.report()["invalid_inputs"]
        assert host.report()["strategies"]["btc"]["callbacks"] == 2
        assert host.report()["strategies"]["btc"]["reason"] is None
    finally:
        ledger.close()


def test_fixed_replay_input_invalidation_and_startup_heartbeat_do_not_mutate_matching(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now)
        before = matcher.report()
        host.advance(T+20)
        assert matcher.report() == before
        replay = runtime(ledger, matcher, now, phase="replay")
        replay.invalidate_inputs((BTC.key,), "replay_source_invalid", T+21)
        replay.advance(T+22)
        assert matcher.report() == before
        assert replay.report()["production_gate"] == "BLOCKED"
    finally:
        ledger.close()


def test_recovery_phase_invalidation_defers_matching_until_live_input(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        live = runtime(ledger, matcher, now)
        live.register(registration(ledger, "one", observed))
        deliver(live, now, book())
        before = matcher.report()
        host = runtime(ledger, matcher, now)
        host.register(registration(ledger, "one", observed))
        applied = host.hot_path.apply_versioned(book(T+20, sequence=2))
        host.process_applied(applied, host.hot_path.snapshot(T+20, "REPLAY"))
        host.invalidate_inputs((BTC.key,), "recovery_input_fault", T+21)
        host.advance(T+30)
        assert matcher.report() == before
        assert host.report()["deferred_matching_gaps"] == (BTC.key,)
        # Returning live fences from the last committed clock, before it can
        # fill arrivals using cached depth across the missing recovery span.
        restored = book(T+40, sequence=3).model_copy(update={"sync_generation": 2})
        now[0] = T+40
        applied = host.hot_path.apply_versioned(restored)
        host.process_applied(applied, host.hot_path.snapshot(now[0], "LIVE"))
        assert not host.report()["deferred_matching_gaps"]
        assert matcher.report()["fills"] == before["fills"]
        old = next(iter(before["open_orders"]))
        # Arrival sorts before a same-time cancel; its fenced book is absent,
        # so this not-yet-arrived order is rejected without a simulated fill.
        assert ledger.get(old)["state"] == "REJECTED"
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_real_shared_engine_applies_once_replay_has_no_orders_and_fault_is_dependency_scoped(tmp_path):
    other = BTC.model_copy(update={"raw_symbol": "ETHUSDT", "std_symbol": "ETHUSDT"})
    ledger = ledger_at(tmp_path)
    engine = None
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        host = PaperStrategyRuntime(hot_path=HotPathStore(), accounts={ledger.scope.key: matcher},
            products={BTC.key: spec(), other.key: spec(other)}, known_instruments={BTC.key: BTC, other.key: other},
            reference_feeds={}, budgets=budget(), settings={"fixture": "shared engine"},
            mode=TradingMode.PAPER, phase="paper", now_ns=lambda: now[0])
        host.register(registration(ledger, "btc", observed))
        host.register(registration(ledger, "eth", observed, instrument=other))
        def invalidated(producer, reason):
            keys = engine.feed_instruments(producer) or tuple(host.known)
            host.invalidate_inputs(keys, reason, now[0])
        engine = LocalMarketEngine(producer_providers={"btc-source": "scripted", "eth-source": "scripted"},
            budgets=EngineBudgets(2, 4, 16_384, 16, 262_144, 10_000_000_000, 32, 2, 20_000_000_000, .01),
            on_invalidate=invalidated, hot_path=host.hot_path, trading_filter=lambda event: False,
            post_apply_callbacks=(host.process_applied,), snapshot_clock=lambda: now[0], clock=lambda: 100)
        btc0, btc1 = EngineCursor("btc-source", "boot", 0), EngineCursor("btc-source", "boot", 1)
        eth0 = EngineCursor("eth-source", "boot", 0)
        engine.begin_recovery(btc0, btc1, verified_checkpoint=True)
        engine.apply_replay(RecoveryFrame(btc1, book().model_dump_json().encode()))
        assert host.hot_path.apply_seq == 1 and not ledger.active_orders()
        assert matcher.report()["clock_ns"] == 0
        engine.handoff(btc0, btc1, verified=True, ready=True, lease_ns=20_000_000_000)
        engine.begin_recovery(eth0, eth0, verified_checkpoint=True)
        engine.handoff(eth0, eth0, verified=True, ready=True, lease_ns=20_000_000_000)
        now[0] = T+20
        engine.on_frame(RecoveryFrame(EngineCursor("btc-source", "boot", 2), book(T+20, sequence=2).model_dump_json().encode()))
        now[0] = T+21
        engine.on_frame(RecoveryFrame(EngineCursor("eth-source", "boot", 1), book(T+21, instrument=other).model_dump_json().encode()))
        assert host.hot_path.apply_seq == 3 and len(ledger.active_orders()) == 2
        assert [sid for sid, _ in observed] == ["btc", "eth"]
        now[0] = T+22
        engine.on_gap(EngineCursor("btc-source", "boot", 2), "disconnected")
        assert host.report()["strategies"]["btc"]["state"] == "WARMING"
        assert host.report()["strategies"]["eth"]["state"] == "PAPER"
        now[0] = T+30
        engine.on_frame(RecoveryFrame(EngineCursor("eth-source", "boot", 2), book(T+30, instrument=other, sequence=2).model_dump_json().encode()))
        assert host.hot_path.apply_seq == 4
        assert host.report()["strategies"]["eth"]["callbacks"] == 2
        assert host.report()["strategies"]["btc"]["callbacks"] == 1
        assert host.report()["production_gate"] == "BLOCKED"
        assert host.report()["restart_evidence"]["continuous_input_cursor"] == "MISSING_EVIDENCE"
    finally:
        if engine is not None:
            await engine.close()
        ledger.close()
