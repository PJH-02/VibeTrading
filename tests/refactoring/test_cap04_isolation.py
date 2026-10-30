"""CAP-04 hard process isolation through the existing PaperStrategyRuntime."""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import importlib.util
import os
from pathlib import Path
import site
import time
from types import ModuleType

import pytest

from core.models import (
    AssetClass, InstrumentId, OrderBookEvent, OrderBookLevel, OrderIntent,
    OrderDestination, Side, Signal, SignalAction, TradeEvent, Venue,
)
from execution.execution_ledger import AccountScope
from execution.paper_execution import PaperExecution
from execution.trading_contracts import (
    ActionKind, ExecutionAction, ProductKind, ProductSpec, TradingMode,
)
from market.hot_path import HotPathStore
from portfolio.portfolio_ledger import PortfolioLedger
from runtime.strategy_process import IsolationBudgets, StrategyIsolation
from runtime.strategy_runtime import PaperStrategyRuntime, RuntimeBudgets, StrategyRegistration, StrategyRisk
from strategies.contract import StrategyModule
from strategies.contract import load_strategy_manifest

T = 1_800_000_000_000_000_000
BTC = InstrumentId(venue=Venue.BYBIT, asset_class=AssetClass.CRYPTO_PERP,
                   raw_symbol="BTCUSDT", std_symbol="BTCUSDT", currency="USDT")


def ledger_at(root):
    return PortfolioLedger(root / "paper" / "account.sqlite", AccountScope(
        BTC.venue.value, "synthetic-paper", "perpetual", "paper"),
        owner_root=root, mode=TradingMode.PAPER, max_orders=100, max_fills=100,
        max_events=1000, max_document_bytes=1_000_000, max_db_bytes=16_000_000,
        max_wal_bytes=16_000_000)


def matcher_for(ledger):
    return PaperExecution(ledger, entry_latency_ns=10, cancel_latency_ns=5,
        max_open_orders=16, max_depth=8, max_receipts=100, max_state_bytes=500_000,
        stale_after_ns=1_000_000_000)


def spec():
    return ProductSpec(BTC, ProductKind.LINEAR_PERPETUAL, D(10), D(1), D(1), D(1),
        BTC.currency, BTC.currency, BTC.currency, "net", "isolated",
        "synthetic fee", "explicit simulated funding only", "synthetic margin",
        D(".001"), D(".002"))


def budget():
    return RuntimeBudgets(max_jobs=4, max_accounts=2, max_required_feeds=8,
        max_signals_per_callback=2, max_actions_per_callback=2, max_pending_actions=4,
        max_seen_actions=100, max_config_bytes=32_000, max_signal_bytes=8192,
        max_action_bytes=16_000, max_callback_ns=1_000_000_000,
        max_feed_age_ns=1_000_000_000)


def runtime(ledger, matcher, now, *, isolation=None, known_instruments=None):
    return PaperStrategyRuntime(hot_path=HotPathStore(),
        accounts={ledger.scope.key: matcher}, products={BTC.key: spec()},
        known_instruments=known_instruments or {BTC.key: BTC}, reference_feeds={},
        budgets=budget(), settings={"fixture": "cap04"}, mode=TradingMode.PAPER,
        phase="paper", now_ns=lambda: now[0], callback_clock_ns=lambda: 0,
        isolation=isolation)


def write_plugin(tmp_path, *, sleep=0.0, feedback=False):
    path = tmp_path / "cap04_plugin.py"
    protocol = "ACTION_PROTOCOL_VERSION = 1\n" if feedback else ""
    extra = (
        "    def on_order_event(self, event, context):\n"
        "        self.feedback_seen += 1\n"
        "        return []\n"
        "    def checkpoint_state(self):\n"
        "        return {'feedback_seen': self.feedback_seen}\n"
        "    def restore_state(self, state):\n"
        "        self.feedback_seen = int(state['feedback_seen'])\n"
        if feedback else
        "    def checkpoint_state(self):\n"
        "        return {'seen': self.seen}\n"
        "    def restore_state(self, state):\n"
        "        self.seen = int(state['seen'])\n"
    )
    path.write_text(
        "import time\n"
        "from decimal import Decimal\n"
        "from core.models import Signal, SignalAction\n"
        "STRATEGY_ID = 'cap04_iso'\n"
        f"{protocol}"
        f"SUBSCRIPTIONS = [{{'instrument_key': {BTC.key!r}, 'event_type': 'order_book'}}]\n"
        "WARMUP = {'bars': 0}\n"
        "class Strategy:\n"
        "    def __init__(self):\n"
        "        self.seen = 0\n"
        "        self.feedback_seen = 0\n"
        "    def configure(self, config):\n"
        "        self.config = dict(config)\n"
        "    def validate_registration(self, facts):\n"
        "        assert facts['strategy_id'] == STRATEGY_ID\n"
        "    def on_event(self, event, data):\n"
        f"        time.sleep({sleep!r})\n"
        "        self.seen += 1\n"
        "        return [Signal(strategy_id=STRATEGY_ID, instrument=event.instrument,\n"
        "            action=SignalAction.ENTER_LONG, event_ts_ns=event.receive_ts_ns,\n"
        "            reference_price=Decimal(100), valid_until_ts_ns=event.receive_ts_ns+100000,\n"
        "            source_event_id=event.event_id, signal_id='sig-'+str(self.seen))]\n"
        f"{extra}"
        "def create_strategy():\n"
        "    return Strategy()\n",
        encoding="utf-8",
    )
    return path


def load_plugin(path):
    module_name = "cap04_plugin_" + str(abs(hash(str(path))))
    spec_obj = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec_obj)
    spec_obj.loader.exec_module(module)
    return StrategyModule(module, module.STRATEGY_ID, module.SUBSCRIPTIONS, module.WARMUP)


def isolated_manifest(path):
    return load_strategy_manifest(path.stem, (str(path.parent),))


def registration(ledger, module):
    sid = module.strategy_id

    def actions(signal, view, config):
        identity = sid + "-" + signal.signal_id
        intent = OrderIntent(intent_id=identity, job_id=sid, signal_id=signal.signal_id,
            destination=OrderDestination.REAL, instrument=BTC, side=Side.BUY,
            created_ts_ns=signal.event_ts_ns, client_order_id="client-" + identity,
            quantity=D("1"), limit_price=D(99))
        return [ExecutionAction("action-" + identity, ActionKind.NEW, ledger.scope, sid,
            "config-1", TradingMode.PAPER, signal.valid_until_ts_ns, intent=intent,
            post_only=True)]

    return StrategyRegistration(sid, module, "strategy-1", "config-1",
        {"quantity": "1"}, {"research_id": "cap04-fixture"}, True, (BTC.key,), 0,
        (ledger.scope.key,),
        {ledger.scope.key: StrategyRisk(D(0), D(100000), D(100000), D(100000),
                                      D(100000), D(".1"))}, actions)


def book(now=T):
    return OrderBookEvent(instrument=BTC, venue=BTC.venue, provider="scripted",
        event_id="book", receive_ts_ns=now, wallclock_ts_ns=now, source_ts_ns=now,
        sequence=1, final_update_id=1, sync_generation=1, depth=1,
        bids=[OrderBookLevel(price=D(99), quantity=D(5))],
        asks=[OrderBookLevel(price=D(101), quantity=D(5))])


def trade(now, side, price):
    return TradeEvent(instrument=BTC, venue=BTC.venue, provider="scripted",
        event_id="trade-" + str(now), receive_ts_ns=now, wallclock_ts_ns=now,
        source_ts_ns=now, price=D(price), quantity=D(1), aggressor_side=side)


def isolation(tmp_path, *, require_bubblewrap=False, **budget_changes):
    values = dict(callback_timeout_ns=2_000_000_000)
    values.update(budget_changes)
    return StrategyIsolation(budgets=IsolationBudgets(**values),
        allowed_source_roots=(str(tmp_path),),
        readonly_paths=(site.getusersitepackages(),) if require_bubblewrap else (),
        require_bubblewrap=require_bubblewrap)


def spin(host, expected, *, rounds=300):
    generated = []
    for _ in range(rounds):
        generated.extend(host.poll_isolated())
        if len(generated) >= expected:
            return generated
        time.sleep(0.005)
    return generated


def test_hard_isolated_callback_returns_typed_action_and_keeps_parent_authority(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now, isolation=isolation(tmp_path))
        module = isolated_manifest(write_plugin(tmp_path))
        host.register(registration(ledger, module))
        job = host._jobs[module.strategy_id]
        assert job.instance is None
        assert job.worker.pid not in (None, os.getpid())
        assert host.report()["callback_isolation"] == "process_isolation_trusted_only"
        assert host.report()["production_gate"] == "BLOCKED"
        now[0] = T
        generated = list(host.process_event(book()))
        generated.extend(spin(host, 1))
        assert generated
        assert len(ledger.report()["orders"]) == 1
        assert matcher.report()["fills"] == 0
        assert host.hot_path.apply_seq == 1
        # Duplicate input cannot produce a second durable action.
        host.process_event(book())
        spin(host, 2, rounds=4)
        assert len(ledger.report()["orders"]) == 1
        host.close()
    finally:
        ledger.close()


def test_deadlined_child_is_fenced_and_owned_orders_are_cancelled(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now,
                       isolation=isolation(tmp_path, callback_timeout_ns=50_000_000))
        module = isolated_manifest(write_plugin(tmp_path, sleep=0.5))
        host.register(registration(ledger, module))
        host.process_event(book())
        now[0] = T + 60_000_000
        # The child deadline is intentionally independent of `now` (the
        # market clock); advance actual local time before invoking watchdog.
        for _ in range(300):
            time.sleep(0.01)
            host.watchdog()
            if host.report()["strategies"][module.strategy_id]["state"] == "FAULTED":
                break
        report = host.report()
        state = report["strategies"][module.strategy_id]
        assert state["state"] == "FAULTED"
        assert state["reason"] == "StrategyTimeout"
        assert host._jobs[module.strategy_id].worker.alive is False
        host.close()
    finally:
        ledger.close()


def test_confined_strategy_runtime_delivers_action_from_bubblewrapped_child(tmp_path):
    import shutil
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap is not installed; confined mode fails closed")
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now,
                       isolation=isolation(tmp_path, require_bubblewrap=True))
        module = isolated_manifest(write_plugin(tmp_path))
        host.register(registration(ledger, module))
        job = host._jobs[module.strategy_id]
        assert host.report()["callback_isolation"] == "process_hard_isolation"
        assert job.worker.pid != os.getpid()
        host.process_event(book())
        assert spin(host, 1)
        assert len(ledger.report()["orders"]) == 1
    finally:
        host.close()
        ledger.close()


def test_feedback_and_checkpoint_state_stay_in_the_same_child_instance(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now, isolation=isolation(tmp_path))
        module = isolated_manifest(write_plugin(tmp_path, feedback=True))
        host.register(registration(ledger, module))
        host.process_event(book())
        assert spin(host, 1)
        assert len(ledger.report()["orders"]) == 1
        now[0] = T + 20
        host.process_event(trade(T + 20, Side.SELL, 98))
        # Feedback is delivered as a child callback, then checkpointed from
        # that exact same child instance.
        for _ in range(300):
            host.poll_isolated()
            if host._jobs[module.strategy_id].callbacks >= 2:
                break
            time.sleep(0.005)
        job = host._jobs[module.strategy_id]
        assert job.callbacks >= 2
        asyncio.run(host.prepare_checkpoint_capture())
        assert job.isolated_checkpoint_state["feedback_seen"] >= 1
        assert job.isolated_checkpoint_sha256
        host.close()
    finally:
        ledger.close()
