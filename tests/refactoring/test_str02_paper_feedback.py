"""STR-02 PAPER action/feedback acceptance through the actual matcher."""
from dataclasses import replace
from decimal import Decimal as D
import importlib
import json
from types import ModuleType, SimpleNamespace
import pytest

from core.models import (AssetClass, InstrumentId, OrderDestination, OrderIntent, Side,
    Signal, SignalAction, TradeEvent, Venue)
from execution.execution_ledger import AccountScope
from execution.paper_execution import PaperExecution
from execution.trading_contracts import (ActionKind, ExecutionAction, ProductKind, ProductSpec,
    StrategyActionPlan, TradingMode)
from market.hot_path import HotPathStore
from runtime.strategy_runtime import PaperStrategyRuntime, StrategyRegistration, StrategyRisk
from strategies.contract import StrategyModule
from portfolio.portfolio_ledger import PortfolioLedger

from test_strategy_runtime import (T, BTC, ledger_at, matcher_for, book, trade, budget,
    spec, deliver, registration)

ETH = InstrumentId(venue=Venue.BYBIT, asset_class=AssetClass.CRYPTO_PERP,
                  raw_symbol="ETHUSDT", std_symbol="ETHUSDT", currency="USDT")


def source_runtime(ledger, matcher, now, *, fok=False):
    instrument = BTC
    product = replace(spec(instrument), lot_size=D("0.1"))
    host = PaperStrategyRuntime(hot_path=HotPathStore(),
        accounts={ledger.scope.key: matcher}, products={instrument.key: product},
        known_instruments={instrument.key: instrument}, reference_feeds={}, budgets=budget(),
        settings={"fixture": "explicit local STR-02"}, mode=TradingMode.PAPER,
        phase="paper", now_ns=lambda: now[0])
    source = importlib.import_module("strategies.crypto.example_order_policy")
    module = StrategyModule(source, source.STRATEGY_ID,
        [{"instrument_key": instrument.key, "event_type": "order_book"}], {"bars": 0})
    config = {"account_key": ledger.scope.key, "quantity": "1", "maker_offset": "0",
              "fok_probe": fok, "fallback_price": "99"}
    registration = StrategyRegistration(source.STRATEGY_ID, module, "fixture-v1", "cfg-v1",
        config, {"research_id": "local-fixture"}, True, (instrument.key,), 0,
        (ledger.scope.key,), {ledger.scope.key: StrategyRisk(D(0), D(100000), D(100000),
        D(100000), D(100000), D("0.1"))}, lambda *_: (_ for _ in ()).throw(
            AssertionError("Action plans must bypass action_builder")))
    host.register(registration)
    return host


def trade_qty(now_ns, quantity, *, identifier, side=Side.SELL, price="99"):
    return TradeEvent(instrument=BTC, venue=BTC.venue, provider="scripted",
        event_id=identifier, receive_ts_ns=now_ns, wallclock_ts_ns=now_ns,
        source_ts_ns=now_ns, price=D(price), quantity=D(quantity), aggressor_side=side)


def test_actual_source_maker_partial_late_fill_cancel_and_confirmed_reprice(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher = PaperExecution(ledger, entry_latency_ns=10, cancel_latency_ns=5,
            max_open_orders=16, max_depth=8, max_receipts=100, max_state_bytes=500_000,
            stale_after_ns=1_000_000_000)
        now = [T]
        host = source_runtime(ledger, matcher, now)
        delivered = deliver(host, now, book(T))
        assert len(delivered) == 2, host.report()["strategies"]
        orders = ledger.report()["orders"]
        assert len(orders) == 2
        intents = [json.loads(row["payload"]) for row in orders]
        assert {item["side"] for item in intents} == {"buy", "sell"}
        assert all(item["metadata"]["post_only"] is True
                   and item["metadata"]["time_in_force"] == "GTC" for item in intents)
        buy = next(row for row in orders if row["side"] == "buy")
        sell = next(row for row in orders if row["side"] == "sell")
        target = buy["intent_id"]
        ask_target = sell["intent_id"]

        # Establish queue position and then produce two distinct partial fills;
        # the second arrives while the source-requested cancel is pending.
        deliver(host, now, book(T + 10, sequence=2))
        deliver(host, now, trade_qty(T + 11, "5.5", identifier="partial-a"))
        first = ledger.get(target)
        assert D(first["filled"]) == D("0.5")
        assert first["state"] == "OPEN"
        assert ledger.get(ask_target)["state"] == "CANCEL_PENDING"
        deliver(host, now, trade_qty(T + 12, "5.2", identifier="partial-b",
                                     side=Side.BUY, price="101"))
        after_late_fill = ledger.get(ask_target)
        assert D(after_late_fill["filled"]) == D("0.2")
        # Duplicate native input is factual dedup and cannot create a second fill.
        late = trade_qty(T + 12, "5.2", identifier="partial-b",
                         side=Side.BUY, price="101")
        deliver(host, now, late)
        assert D(ledger.get(ask_target)["filled"]) == D("0.2")
        deliver(host, now, book(T + 17, sequence=3))

        feedback = ledger.order_feedback(host._jobs["example_crypto_order_policy"].registration.module.strategy_id)
        assert any(event.status == "CANCEL_PENDING" for _, event in feedback)
        assert sum(event.status == "PARTIAL_FILL" for _, event in feedback) == 2
        replacements = [row for row in ledger.report()["orders"]
                        if json.loads(row["payload"])["metadata"].get("replacement_of") == ask_target]
        assert len(replacements) == 1
        assert D(replacements[0]["quantity"]) == D("0.8")
        trace = host._jobs["example_crypto_order_policy"].instance.trace
        source_fills = [row for row in trace if row["status"] == "PARTIAL_FILL"]
        assert len(source_fills) == 2
        assert all(row["fill_id"] and row["fill_quantity"] and row["fill_price"]
                   and row["fee"] is not None
                   and any("BTCUSDT" in key for positions in row["virtual_positions"].values()
                           for key in positions)
                   and "USDT" in row["available"] for row in source_fills), [
                           {"status": row["status"], "positions": row["virtual_positions"]}
                       for row in source_fills]
        assert any(row["status"] == "CANCEL_PENDING" for row in trace)
        assert matcher.report()["fills"] >= 2
    finally:
        ledger.close()


def test_actual_source_fok_reject_then_explicit_ioc_choice(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher = matcher_for(ledger)
        now = [T]
        host = source_runtime(ledger, matcher, now, fok=True)
        deliver(host, now, book(T))
        sid = "example_crypto_order_policy"
        feedback = ledger.order_feedback(sid)
        rejection = next(event for _, event in feedback
                         if event.metadata.get("provenance") == "LOCAL_NOT_SENT")
        assert rejection.status == "REJECTED"
        assert len(ledger.report()["orders"]) == 1
        admitted = ledger.report()["orders"][0]
        payload = json.loads(admitted["payload"])
        assert payload["metadata"]["time_in_force"] == "IOC"
        assert payload["metadata"]["post_only"] is False
        deliver(host, now, book(T + 10, sequence=2))
        feedback = ledger.order_feedback(sid)
        assert any(event.status == "FILLED" for _, event in feedback)
        assert D(ledger.report()["orders"][0]["filled"]) == D("1")
        assert host._jobs[sid].state == "PAPER"
        trace = host._jobs[sid].instance.trace
        assert trace[0]["status"] == "REJECTED"
        assert trace[0]["quality"] == "VALID"
        assert any(row["status"] == "FILLED" and row["fill_id"] for row in trace)
    finally:
        ledger.close()


def test_checkpoint_restores_source_feedback_cursor_before_confirmed_reprice(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher = PaperExecution(ledger, entry_latency_ns=10, cancel_latency_ns=5,
            max_open_orders=16, max_depth=8, max_receipts=100, max_state_bytes=500_000,
            stale_after_ns=1_000_000_000)
        now = [T]
        host = source_runtime(ledger, matcher, now)
        deliver(host, now, book(T))
        orders = ledger.report()["orders"]
        ask_target = next(row["intent_id"] for row in orders if row["side"] == "sell")
        deliver(host, now, book(T + 10, sequence=2))
        deliver(host, now, trade_qty(T + 11, "5.5", identifier="restart-partial"))
        assert ledger.get(ask_target)["state"] == "CANCEL_PENDING"

        host._checkpoint_capture_guard(SimpleNamespace(hot_path=host.hot_path))
        saved = host.capture_runtime_state(max_bytes=1_000_000)
        sid = "example_crypto_order_policy"
        saved_cursor = saved["jobs"][0]["feedback_cursors"][ledger.scope.key]
        assert saved_cursor > 0

        restored = source_runtime(ledger, matcher, now)
        restored.hot_path = host.hot_path
        restored.begin_checkpoint_recovery()
        proof = restored.restore_runtime_state(saved, max_bytes=1_000_000)
        assert restored.publish_checkpoint_restore(host.hot_path, proof)
        assert restored._jobs[sid].feedback_cursors[ledger.scope.key] == saved_cursor

        # The original source instance is gone. The restored source receives
        # only the new terminal fact and chooses a bounded remaining quantity.
        deliver(restored, now, book(T + 17, sequence=3))
        replacements = [row for row in ledger.report()["orders"]
                        if json.loads(row["payload"])["metadata"].get("replacement_of") == ask_target]
        assert len(replacements) == 1
        assert D(replacements[0]["quantity"]) == D("1")
        assert restored._jobs[sid].feedback_cursors[ledger.scope.key] > saved_cursor
        feedback = ledger.order_feedback(sid)
        assert sum(event.status == "CANCEL_PENDING" for _, event in feedback) == 1
        assert sum(event.status == "CANCELLED" for _, event in feedback) == 1
    finally:
        ledger.close()


def test_replace_plan_rejects_wrong_job_future_time_and_unbounded_intent_ids(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher = matcher_for(ledger)
        now = [T]
        host = source_runtime(ledger, matcher, now)
        deliver(host, now, book(T))
        sid = "example_crypto_order_policy"
        target = next(row["intent_id"] for row in ledger.report()["orders"]
                      if row["side"] == "sell")
        matcher.cancel(target, sid, T + 1)
        matcher.advance(T + 10)
        assert ledger.get(target)["state"] == "CANCELED"
        job = host._jobs[sid]

        def replacement(*, job_id=sid, created=T, client_id="replacement-client"):
            intent = OrderIntent(intent_id="replacement-intent", job_id=job_id,
                signal_id="replacement-source", destination=OrderDestination.REAL,
                instrument=BTC, side=Side.SELL, created_ts_ns=created,
                client_order_id=client_id, quantity=D("1"), limit_price=D("101"))
            action = ExecutionAction(action_id="replacement-action", action=ActionKind.REPLACE,
                scope=ledger.scope, strategy_id=sid, config_version="cfg-v1",
                mode=TradingMode.PAPER, expiry_ns=T + 1000, intent=intent,
                target_order_id=target, post_only=True, time_in_force="GTC")
            return StrategyActionPlan("replacement-plan", sid, "cfg-v1", T, (action,)), action

        before_orders = len(ledger.report()["orders"])
        before_claims = ledger._db.execute(
            "SELECT COUNT(*) FROM portfolio_receipts WHERE kind='strategy_event'").fetchone()[0]
        invalid = (
            replacement(job_id="wrong-job"),
            replacement(created=T + 1),
            replacement(client_id="x" * 513),
        )
        for plan, action in invalid:
            with pytest.raises(ValueError):
                host._validate_plan_action(job, action, plan, T, host.view)
        assert len(ledger.report()["orders"]) == before_orders
        assert ledger._db.execute(
            "SELECT COUNT(*) FROM portfolio_receipts WHERE kind='strategy_event'").fetchone()[0] == before_claims
    finally:
        ledger.close()


def test_account_unknown_order_blocks_other_strategy_risk_but_not_other_account(tmp_path):
    first = ledger_at(tmp_path / "primary")
    other = PortfolioLedger(tmp_path / "paper" / "other.sqlite",
        AccountScope("bybit", "other-paper", "perpetual", "paper"), owner_root=tmp_path,
        mode=TradingMode.PAPER, max_orders=100, max_fills=100, max_events=1000,
        max_document_bytes=1_000_000, max_db_bytes=16_000_000, max_wal_bytes=16_000_000)
    try:
        for ledger in (first, other):
            ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher_a, matcher_b, now = matcher_for(first), matcher_for(other), [T]
        products = {BTC.key: spec(BTC), ETH.key: spec(ETH)}
        known = {BTC.key: BTC, ETH.key: ETH}
        host = PaperStrategyRuntime(hot_path=HotPathStore(),
            accounts={first.scope.key: matcher_a, other.scope.key: matcher_b},
            products=products, known_instruments=known, reference_feeds={}, budgets=budget(),
            settings={"fixture": "account-scoped UNKNOWN fence"}, mode=TradingMode.PAPER,
            phase="paper", now_ns=lambda: now[0])

        observed = []
        host.register(registration(first, "beta", observed, instrument=BTC))

        def opted_module(sid):
            source = ModuleType("opted_" + sid)
            source.STRATEGY_ID = sid
            source.ACTION_PROTOCOL_VERSION = 1
            source.SUBSCRIPTIONS = [{"instrument_key": ETH.key, "event_type": "order_book"}]
            source.WARMUP = {"bars": 0}
            class Instance:
                def on_event(self, event, data): return []
                def on_order_event(self, event, context): return []
            source.create_strategy = Instance
            return StrategyModule(source, sid, source.SUBSCRIPTIONS, source.WARMUP)

        def add_opted(sid, ledger):
            config = {"account_key": ledger.scope.key}
            risk = StrategyRisk(D(0), D(100000), D(100000), D(100000), D(100000), D("0.1"))
            host.register(StrategyRegistration(sid, opted_module(sid), "v1", "cfg-v1",
                config, {"research_id": "local"}, True, (ETH.key,), 0,
                (ledger.scope.key,), {ledger.scope.key: risk}, lambda *_: []))

        add_opted("alpha", first)
        add_opted("gamma", other)
        deliver(host, now, book(T, instrument=BTC))
        deliver(host, now, book(T + 1, instrument=ETH, sequence=2))
        beta_order = first.report()["orders"][0]["intent_id"]
        first._outcome(beta_order, "UNKNOWN", False, None)
        # Keep this unit test focused on the explicit account fence rather than
        # the separate ledger readiness fence, which also fails closed.
        host._ready = lambda _job, _snapshot: True
        view = host.view

        def new_action(sid, ledger, *, action_id=None, intent_id=None, signal_id=None):
            intent = OrderIntent(intent_id=intent_id or sid + ":intent", job_id=sid,
                signal_id=signal_id or sid + ":signal", destination=OrderDestination.REAL,
                instrument=ETH, side=Side.BUY, created_ts_ns=T + 1,
                client_order_id=sid + ":client", quantity=D("1"), limit_price=D("99"))
            return ExecutionAction(action_id=action_id or sid + ":action", action=ActionKind.NEW,
                scope=ledger.scope, strategy_id=sid, config_version="cfg-v1",
                mode=TradingMode.PAPER, expiry_ns=T + 1000, intent=intent,
                post_only=True, time_in_force="GTC")

        alpha_action = new_action("alpha", first)
        alpha_plan = StrategyActionPlan("alpha-plan", "alpha", "cfg-v1", T + 1, (alpha_action,))
        alpha_job = host._jobs["alpha"]
        with pytest.raises(ValueError, match="unresolved account order"):
            host._validate_plan_action(alpha_job, alpha_action, alpha_plan, T + 1, view)

        # Opted-in callbacks that use the backwards-compatible Signal union
        # receive the same account-wide block.
        signal = Signal(strategy_id="alpha", instrument=ETH, action=SignalAction.ENTER_LONG,
            event_ts_ns=T + 1, reference_price=D(100), valid_until_ts_ns=T + 500,
            source_event_id="alpha-source", signal_id="alpha-signal")
        alpha_signal_action = new_action("alpha", first, action_id="alpha:signal-action",
            intent_id="alpha:signal-intent", signal_id=signal.signal_id)
        with pytest.raises(ValueError, match="unresolved account order"):
            host._validate(alpha_job, alpha_signal_action, signal, T + 1, view)

        gamma_action = new_action("gamma", other)
        gamma_plan = StrategyActionPlan("gamma-plan", "gamma", "cfg-v1", T + 1, (gamma_action,))
        assert host._validate_plan_action(host._jobs["gamma"], gamma_action,
                                          gamma_plan, T + 1, view)
    finally:
        first.close()
        other.close()
