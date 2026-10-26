"""TX05: real configured strategy file through the existing continuous Paper path.

This is intentionally an integration gate over existing owners only: the real
canonical file loader/register_configured, LocalMarketEngine/RecoveryFrame,
PaperStrategyRuntime, PaperExecution, PortfolioLedger, MarketSpool and
RuntimeCheckpoint.  The strategy files are real files in an owned temporary
canonical package path; no ModuleType/sys.modules fake or mock loader is used.
"""
import asyncio
from decimal import Decimal as D
import importlib
import json

import pytest

from test_strategy_runtime import (T, BTC, book, budget, deliver, ledger_at,
                                   matcher_for, runtime, trade)
from test_vn13_file_registration import configured, strategy_files
from test_runtime_checkpoint_integration import append, codec, producer
from feeds.feed_recovery import EngineCursor, RecoveryFrame
from market.local_engine import EngineBudgets, LocalMarketEngine
from core.models import OrderDestination, OrderIntent, Side, SignalAction
from vibetrading.runtime_checkpoint import RuntimeCheckpoint
from execution.trading_contracts import ActionKind, ExecutionAction, TradingMode

SID = "tx05_e2e_strategy"
BAD_SID = "tx05_bad_file"
GOOD_SID = "tx05_good_file"
JOB_ID = "tx05-e2e-job"
CONFIG = {"quantity": "2", "limit_price": "99",
          "exit_quantity": "1", "exit_price": "101"}


def strategy_source(strategy_id=SID):
    """A real file-backed stateful strategy with an explicit checkpoint hook."""
    return f'''from decimal import Decimal as D
from vibetrading.models import Signal, SignalAction

STRATEGY_ID = {strategy_id!r}
SUBSCRIPTIONS = [{{"instrument_key": {BTC.key!r}, "event_type": "order_book"}}]
WARMUP = {{"bars": 0}}

class Strategy:
    def __init__(self):
        self.state = {{"count": 0, "event_ids": [], "actions": []}}

    def checkpoint_state(self):
        return {{"count": self.state["count"],
                 "event_ids": list(self.state["event_ids"]),
                 "actions": list(self.state["actions"])}}

    def restore_state(self, state):
        self.state = {{"count": int(state["count"]),
                       "event_ids": list(state["event_ids"]),
                       "actions": list(state["actions"])}}

    def on_event(self, event, data):
        self.state["count"] += 1
        action = (SignalAction.ENTER_LONG if self.state["count"] == 1
                  else SignalAction.EXIT_LONG)
        self.state["event_ids"].append(event.event_id)
        self.state["actions"].append(action.value)
        return [Signal(strategy_id=STRATEGY_ID, instrument=event.instrument,
            action=action, event_ts_ns=event.receive_ts_ns,
            reference_price=D("100"),
            valid_until_ts_ns=event.receive_ts_ns + 100_000,
            source_event_id=event.event_id,
            metadata={{"state_count": self.state["count"],
                       "action": action.value}})]

def create_strategy():
    return Strategy()
'''


def fault_source(strategy_id, failure):
    if failure == "raise":
        body = 'raise RuntimeError("configured file callback fault")'
    else:
        body = ('return [Signal(strategy_id=STRATEGY_ID, instrument=event.instrument,\n'
                '            action=SignalAction.ENTER_LONG, event_ts_ns=event.receive_ts_ns,\n'
                '            reference_price=D("100"),\n'
                '            valid_until_ts_ns=event.receive_ts_ns + 100_000,\n'
                '            source_event_id=event.event_id)]')
    return f'''from decimal import Decimal as D
from vibetrading.models import Signal, SignalAction

STRATEGY_ID = {strategy_id!r}
SUBSCRIPTIONS = [{{"instrument_key": {BTC.key!r}, "event_type": "order_book"}}]
WARMUP = {{"bars": 0}}

class Strategy:
    def on_event(self, event, data):
        {body}

def create_strategy():
    return Strategy()
'''


def healthy_source(strategy_id=GOOD_SID):
    return f'''from decimal import Decimal as D
from vibetrading.models import Signal, SignalAction

STRATEGY_ID = {strategy_id!r}
SUBSCRIPTIONS = [{{"instrument_key": {BTC.key!r}, "event_type": "order_book"}}]
WARMUP = {{"bars": 0}}

class Strategy:
    def on_event(self, event, data):
        return [Signal(strategy_id=STRATEGY_ID, instrument=event.instrument,
            action=SignalAction.ENTER_LONG, event_ts_ns=event.receive_ts_ns,
            reference_price=D("100"),
            valid_until_ts_ns=event.receive_ts_ns + 100_000,
            source_event_id=event.event_id)]

def create_strategy():
    return Strategy()
'''


def loaded(name):
    return importlib.import_module("strategies." + name)


def actions_for(ledger, *, job_id=JOB_ID):
    """Build from the signal's action/state metadata only, never a counter."""
    def build(signal, view, config):
        if signal.action is SignalAction.ENTER_LONG:
            side, quantity = Side.BUY, D(config["quantity"])
            price, reduce_only, expected = D(config["limit_price"]), False, 1
        elif signal.action is SignalAction.EXIT_LONG:
            side, quantity = Side.SELL, D(config["exit_quantity"])
            price, reduce_only, expected = D(config["exit_price"]), True, 2
        else:
            raise ValueError("unexpected strategy action")
        if signal.metadata.get("state_count") != expected:
            raise ValueError("signal state metadata differs from its action")
        identity = job_id + "-" + signal.signal_id
        intent = OrderIntent(intent_id=identity, job_id=job_id,
            signal_id=signal.signal_id, destination=OrderDestination.REAL,
            instrument=BTC, side=side, created_ts_ns=signal.event_ts_ns,
            client_order_id="client-" + identity, quantity=quantity,
            limit_price=price,
            metadata={"signal_action": signal.action.value,
                      "state_count": expected})
        return [ExecutionAction("action-" + identity, ActionKind.NEW,
            ledger.scope, SID, "config-1", TradingMode.PAPER,
            signal.valid_until_ts_ns, intent=intent, post_only=True,
            reduce_only=reduce_only)]
    return build


def entry_actions_for(ledger, strategy_id, job_id):
    def build(signal, view, config):
        identity = job_id + "-" + signal.signal_id
        intent = OrderIntent(intent_id=identity, job_id=job_id,
            signal_id=signal.signal_id, destination=OrderDestination.REAL,
            instrument=BTC, side=Side.BUY, created_ts_ns=signal.event_ts_ns,
            client_order_id="client-" + identity,
            quantity=D(config["quantity"]), limit_price=D(config["limit_price"]))
        return [ExecutionAction("action-" + identity, ActionKind.NEW,
            ledger.scope, strategy_id, "config-1", TradingMode.PAPER,
            signal.valid_until_ts_ns, intent=intent, post_only=True)]
    return build


def engine_budgets():
    return EngineBudgets(4, 16, 4096, 16, 65536, 60_000_000_000, 32, 2,
                         60_000_000_000, .01)


def live_engine(item, host, now, monotonic):
    return LocalMarketEngine(producer_providers={item.producer: item.provider},
        budgets=engine_budgets(), on_invalidate=lambda *args: None,
        hot_path=host.hot_path, post_apply_callbacks=(host.process_applied,),
        on_tick=host.advance, snapshot_clock=lambda: now[0],
        clock=lambda: monotonic[0],
        fresh_starts={item.producer: item.incarnation},
        verified_fresh_start=True)


def restore_engine(item, host, now, monotonic):
    # A restore target must start callback-free and with a virgin hot path.
    return LocalMarketEngine(producer_providers={item.producer: item.provider},
        budgets=engine_budgets(), on_invalidate=lambda *args: None,
        hot_path=host.hot_path, post_apply_callbacks=(),
        on_tick=host.advance, snapshot_clock=lambda: now[0],
        clock=lambda: monotonic[0])


def feed_new(engine, item, sequence, event, now, monotonic):
    now[0] = event.receive_ts_ns
    monotonic[0] += 1
    payload = event.model_dump_json().encode()
    append(item, payload)
    return engine.on_frame(RecoveryFrame(
        EngineCursor(item.producer, item.incarnation, sequence), payload))


def repeated_frame(engine, item, sequence, event):
    return engine.on_frame(RecoveryFrame(
        EngineCursor(item.producer, item.incarnation, sequence),
        event.model_dump_json().encode()))


async def wait_until(predicate, *, timeout=3):
    async def poll():
        while not predicate():
            await asyncio.sleep(.001)
    await asyncio.wait_for(poll(), timeout=timeout)


def order_by_side(ledger, side):
    rows = [row for row in ledger.report()["orders"] if row["side"] == side]
    assert len(rows) == 1
    return rows[0]


def economic_view(ledger, matcher, sid):
    """All economic fields and deterministic IDs.

    The only omitted order columns are the evidence-derived fingerprint and the
    raw evidence cursor's process engine incarnation, which is root-local rather
    than an economic fact.  Every other evidence field (including registration/input versions) is retained.
    """
    report = ledger.report()
    orders = []
    for row in sorted(report["orders"], key=lambda item: item["intent_id"]):
        item = {key: value for key, value in dict(row).items()
                if key not in {"fingerprint", "evidence"}}
        evidence = json.loads(row["evidence"])
        item["evidence_identity"] = {
            **evidence,
            "cursor": {"apply_seq": evidence["cursor"][1]},
        }
        orders.append(item)
    return {
        "balances": report["balances"],
        "reserved": report["reserved"],
        "simulated_margin": report["simulated_margin"],
        "simulated_capital": report["simulated_capital"],
        "physical_positions": report["physical_positions"],
        "virtual_positions": report["virtual_positions"],
        "strategy_cash": sorted(report["strategy_cash"],
                                key=lambda row: (row["strategy"], row["currency"])),
        "account_version": report["account_version"],
        "orders": orders,
        "gaps": report["gaps"],
        "readiness": report["readiness"],
        "matcher": {"fills": matcher.report()["fills"],
                    "receipt_count": matcher.report()["receipt_count"],
                    "open_orders": matcher.report()["open_orders"],
                    "clock_ns": matcher.report()["clock_ns"]},
        "strategy_id": sid,
    }


async def run_economic_branch(root, module_name, *, restore):
    now, monotonic = [T], [0]
    item = producer(root)
    ledger = ledger_at(root)
    engine = None
    checkpoint = None
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher = matcher_for(ledger)
        host = runtime(ledger, matcher, now)
        host.register_configured(
            configured(ledger, module_name, JOB_ID, config=CONFIG),
            action_builder=actions_for(ledger))
        engine = live_engine(item, host, now, monotonic)

        first_book = book()
        feed_new(engine, item, 1, first_book, now, monotonic)
        assert host.hot_path.apply_seq == 1
        assert host.report()["strategies"][SID]["callbacks"] == 1
        assert host.report()["strategies"][SID]["actions"] == 1
        buy_row = order_by_side(ledger, "buy")
        buy_id = buy_row["intent_id"]
        buy_payload = json.loads(buy_row["payload"])
        assert D(buy_payload["quantity"]) == D(2)
        assert D(buy_payload["limit_price"]) == D("99")
        assert buy_payload["metadata"]["reduce_only"] is False
        assert buy_payload["metadata"]["signal_action"] == "enter_long"
        assert buy_payload["metadata"]["state_count"] == 1
        assert buy_payload["created_ts_ns"] == T
        assert json.loads(buy_row["evidence"])["source_event_id"] == first_book.event_id
        assert D(buy_row["reservation"]) == D("201.96")
        assert D(ledger.report()["reserved"]["USDT"]) == D("201.96")
        assert D(ledger.report()["balances"]["USDT"]) == D("100000")

        sell_trade = trade(T + 11, side=Side.SELL, price=99).model_copy(
            update={"quantity": D(6)})
        feed_new(engine, item, 2, sell_trade, now, monotonic)
        assert host.hot_path.apply_seq == 2
        assert T + 11 == sell_trade.receive_ts_ns == sell_trade.source_ts_ns
        assert matcher.report()["fills"] == 1
        buy_row = ledger.get(buy_id)
        assert buy_row["state"] == "OPEN"
        assert D(buy_row["filled"]) == D(1)
        assert D(buy_row["reservation"]) == D("100.98")
        assert D(matcher.report()["open_orders"][buy_id]["leaves"]) == D(1)
        partial = ledger.report()
        assert D(partial["balances"]["USDT"]) == D("99999.01")
        assert D(partial["reserved"]["USDT"]) == D("100.98")
        assert D(partial["physical_positions"][BTC.key]) == D(1)
        assert D(partial["virtual_positions"][SID][BTC.key]) == D(1)
        assert host.report()["strategies"][SID]["callbacks"] == 1
        assert host._jobs[SID].instance.state["count"] == 1
        assert engine.stats["feeds"][item.producer]["applied_sequence"] == 2

        # Save the real coupled checkpoint at exactly spooled cut 2, before
        # appending any suffix record or publishing another callback.
        checkpoint = RuntimeCheckpoint(root / "runtime-checkpoint.json",
            codec(engine), max_bytes=262144, max_journal_rows=10000,
            max_journal_bytes=4_000_000)
        await checkpoint.save(engine, paper_runtime=host)
        assert checkpoint.state == "IDLE"
        if restore:
            await engine.close()
            engine = None
            ledger.close()
            ledger = None

            ledger = ledger_at(root)
            matcher = matcher_for(ledger)
            host = runtime(ledger, matcher, now)
            host.register_configured(
                configured(ledger, module_name, JOB_ID, config=CONFIG),
                action_builder=actions_for(ledger))
            cold = host.report()["strategies"][SID]
            assert (cold["state"], cold["reason"]) == (
                "BLOCKED", "strategy_state_restore_unverified")
            assert cold["callbacks"] == 0 and cold["actions"] == 0
            assert ledger.get(buy_id)["state"] == "UNKNOWN"
            assert D(ledger.get(buy_id)["reservation"]) == D("100.98")
            assert host.hot_path.apply_seq == 0
            engine = restore_engine(item, host, now, monotonic)
            assert engine.post_apply_callbacks == ()
            seen = {}

            def readiness(market_engine, snapshot, receipts):
                seen.update({
                    "phase": snapshot.phase,
                    "cursor": snapshot.cursor,
                    "callbacks_bound": market_engine.post_apply_callbacks,
                    "recovery_state": host.recovery_state,
                    "callbacks": host.report()["strategies"][SID]["callbacks"],
                    "count": host._jobs[SID].instance.state["count"],
                    "receipts": tuple(receipt.cursor for receipt in receipts),
                })
                return (snapshot.phase == "REPLAY"
                        and snapshot.cursor == (market_engine.hot_path.engine_incarnation,
                                                market_engine.hot_path.apply_seq))

            await checkpoint.restore(engine, (item,), paper_runtime=host,
                                     readiness=readiness)
            assert checkpoint.state == "READY"
            assert host.recovery_state == "READY"
            assert engine.post_apply_callbacks == (host.process_applied,)
            assert seen["phase"] == "REPLAY"
            assert seen["callbacks_bound"] == ()
            assert seen["recovery_state"] == "RECOVERING"
            assert seen["callbacks"] == 1
            assert seen["count"] == 1
            assert host.report()["strategies"][SID]["callbacks"] == 1
            assert host._jobs[SID].instance.state["count"] == 1
            assert engine.stats["feeds"][item.producer]["applied_sequence"] == 2

        second_book = book(T + 20, sequence=2)
        feed_new(engine, item, 3, second_book, now, monotonic)
        assert host.hot_path.apply_seq == 3
        assert host.report()["strategies"][SID]["callbacks"] == 2
        assert host.report()["strategies"][SID]["actions"] == 2
        assert host._jobs[SID].instance.state["count"] == 2
        sell_row = order_by_side(ledger, "sell")
        sell_id = sell_row["intent_id"]
        sell_payload = json.loads(sell_row["payload"])
        assert D(sell_payload["quantity"]) == D(1)
        assert D(sell_payload["limit_price"]) == D("101")
        assert sell_payload["metadata"]["reduce_only"] is True
        assert sell_payload["metadata"]["signal_action"] == "exit_long"
        assert sell_payload["metadata"]["state_count"] == 2
        assert sell_payload["created_ts_ns"] == T + 20
        assert json.loads(sell_row["evidence"])["source_event_id"] == second_book.event_id
        assert D(ledger.get(buy_id)["filled"]) == D(1)
        assert D(ledger.get(buy_id)["reservation"]) == D("100.98")
        assert D(matcher.report()["open_orders"][buy_id]["leaves"]) == D(1)

        buy_trade = trade(T + 31, side=Side.BUY, price=101).model_copy(
            update={"quantity": D(6)})
        feed_new(engine, item, 4, buy_trade, now, monotonic)
        assert host.hot_path.apply_seq == 4
        assert T + 31 == buy_trade.receive_ts_ns == buy_trade.source_ts_ns
        assert matcher.report()["fills"] == 2
        assert len(ledger.report()["orders"]) == 2
        assert D(ledger.get(buy_id)["filled"]) == D(1)
        assert D(ledger.get(buy_id)["reservation"]) == D("100.98")
        assert ledger.get(sell_id)["state"] == "FILLED"
        assert D(ledger.get(sell_id)["filled"]) == D(1)
        assert D(ledger.get(sell_id)["reservation"]) == 0
        closed = ledger.report()
        assert D(closed["balances"]["USDT"]) == D("100018")
        assert D(closed["reserved"]["USDT"]) == D("100.98")
        assert D(closed["simulated_margin"]) == 0
        assert D(closed["physical_positions"][BTC.key]) == 0
        assert D(closed["virtual_positions"][SID][BTC.key]) == 0
        cash = next(row for row in closed["strategy_cash"]
                    if row["strategy"] == SID)
        assert D(cash["realized"]) == D(20)
        assert D(cash["fees"]) == D(2)
        assert D(cash["amount"]) == D("18")
        assert matcher.report()["receipt_count"] == 4
        assert host.report()["strategies"][SID]["callbacks"] == 2
        assert host.report()["strategies"][SID]["actions"] == 2
        assert host._jobs[SID].instance.state["count"] == 2

        # The exact same transport frame is an engine duplicate before the hot
        # path/Paper runtime, so no fill, callback, order, cash or receipt moves.
        before_ledger, before_matcher = ledger.report(), matcher.report()
        before_host = host.report()
        repeated_frame(engine, item, 4, buy_trade)
        assert engine.stats["duplicates"] == 1
        assert host.hot_path.apply_seq == 4
        assert ledger.report() == before_ledger
        assert matcher.report() == before_matcher
        assert host.report() == before_host
        assert matcher.report()["fills"] == 2
        assert host.report()["strategies"][SID]["callbacks"] == 2
        assert len(ledger.report()["orders"]) == 2

        # Operator pause requests the unfilled original BUY cancel; only the
        # existing watchdog tick at the injected epoch makes it effective.
        host.pause(SID, T + 32)
        assert ledger.get(buy_id)["state"] == "CANCEL_PENDING"
        assert D(ledger.get(buy_id)["reservation"]) == D("100.98")
        assert ledger.get(sell_id)["state"] == "FILLED"
        applied_before_tick = engine.stats["applied"]
        now[0] = T + 37
        monotonic[0] += 1
        await engine.start()
        await wait_until(lambda: ledger.get(buy_id)["state"] == "CANCELED")
        assert engine.stats["applied"] == applied_before_tick
        assert host.hot_path.apply_seq == 4
        assert engine.stats["feeds"][item.producer]["applied_sequence"] == 4
        assert matcher.report()["clock_ns"] == T + 37

        final = ledger.report()
        assert ledger.get(buy_id)["state"] == "CANCELED"
        assert D(ledger.get(buy_id)["filled"]) == D(1)
        assert D(ledger.get(buy_id)["reservation"]) == 0
        assert ledger.get(sell_id)["state"] == "FILLED"
        assert D(ledger.get(sell_id)["filled"]) == D(1)
        assert D(final["balances"]["USDT"]) == D("100018")
        assert D(final["reserved"]["USDT"]) == 0
        assert D(final["simulated_margin"]) == 0
        assert D(final["physical_positions"][BTC.key]) == 0
        assert D(final["virtual_positions"][SID][BTC.key]) == 0
        assert host.report()["strategies"][SID]["state"] == "PAUSED"
        assert host.report()["strategies"][SID]["callbacks"] == 2
        module = host._jobs[SID].registration.module.module
        return {
            "economic": economic_view(ledger, matcher, SID),
            "plugin_state": dict(host._jobs[SID].instance.state),
            "module_file": module.__file__,
            "buy_id": buy_id,
            "sell_id": sell_id,
            "apply_seq": host.hot_path.apply_seq,
            "duplicates": engine.stats["duplicates"],
        }
    finally:
        if engine is not None:
            await engine.close()
        if ledger is not None:
            ledger.close()


@pytest.mark.asyncio
async def test_real_file_economic_uninterrupted_restored_parity(tmp_path, strategy_files):
    live_root, restored_root = tmp_path / "live", tmp_path / "restored"
    live_root.mkdir()
    restored_root.mkdir()
    name = strategy_files("tx05_e2e_real_file", strategy_source())
    module_name = "strategies/" + name + ".py"
    expected_path = str(tmp_path / (name + ".py"))

    live = await run_economic_branch(live_root, module_name, restore=False)
    restored = await run_economic_branch(restored_root, module_name, restore=True)

    # Real file-backed module, not a ModuleType/sys.modules fake or mock loader.
    assert live["module_file"] == restored["module_file"] == expected_path
    assert loaded(name).__file__ == expected_path
    assert live["economic"] == restored["economic"]
    assert live["plugin_state"] == restored["plugin_state"] == {
        "count": 2,
        "event_ids": ["book-" + str(T), "book-" + str(T + 20)],
        "actions": ["enter_long", "exit_long"],
    }
    assert live["buy_id"] == restored["buy_id"]
    assert live["sell_id"] == restored["sell_id"]
    assert live["apply_seq"] == restored["apply_seq"] == 4
    assert live["duplicates"] == restored["duplicates"] == 1

    view = live["economic"]
    assert {row["side"]: row["state"] for row in view["orders"]} == {
        "buy": "CANCELED", "sell": "FILLED"}
    assert view["matcher"]["fills"] == 2
    assert view["matcher"]["receipt_count"] == 4
    assert view["matcher"]["open_orders"] == {}


@pytest.mark.parametrize("failure,reason,expected_callbacks", [
    ("raise", "RuntimeError", 0),
    ("slow", "TimeoutError", 1),
])
def test_real_file_callback_fault_and_slow_return_are_isolated(
        tmp_path, strategy_files, failure, reason, expected_callbacks):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        if failure == "slow":
            ticks = iter([0, 1000, 0, 0, 0, 0])
            callback_clock = lambda: next(ticks, 0)
        else:
            callback_clock = lambda: 0
        host = runtime(ledger, matcher, now, budgets=budget(max_callback_ns=10),
                       callback_clock_ns=callback_clock)
        bad = strategy_files("tx05_bad_real_file", fault_source(BAD_SID, failure))
        good = strategy_files("tx05_good_real_file", healthy_source())
        host.register_configured(
            configured(ledger, bad, "tx05-bad-job"),
            action_builder=entry_actions_for(ledger, BAD_SID, "tx05-bad-job"))
        host.register_configured(
            configured(ledger, good, "tx05-good-job"),
            action_builder=entry_actions_for(ledger, GOOD_SID, "tx05-good-job"))

        event = book()
        assert len(deliver(host, now, event)) == 1
        assert host.hot_path.apply_seq == 1
        bad_report = host.report()["strategies"][BAD_SID]
        good_report = host.report()["strategies"][GOOD_SID]
        assert (bad_report["state"], bad_report["reason"]) == ("FAULTED", reason)
        assert bad_report["callbacks"] == expected_callbacks
        assert bad_report["actions"] == 0
        assert (good_report["state"], good_report["callbacks"], good_report["actions"]) == (
            "PAPER", 1, 1)
        orders = ledger.report()["orders"]
        assert len(orders) == 1 and orders[0]["strategy"] == GOOD_SID
        assert all(row["strategy"] != BAD_SID for row in orders)
        assert loaded(bad).__file__ == str(tmp_path / (bad + ".py"))
        assert loaded(good).__file__ == str(tmp_path / (good + ".py"))

        before_ledger, before_matcher = ledger.report(), matcher.report()
        before_good = good_report["callbacks"]
        assert deliver(host, now, event) == ()
        assert host.hot_path.apply_seq == 1
        assert ledger.report() == before_ledger
        assert matcher.report() == before_matcher
        assert host.report()["strategies"][GOOD_SID]["callbacks"] == before_good
        assert host.report()["strategies"][BAD_SID]["actions"] == 0
        report = host.report()
        assert report["callback_isolation"] == "cooperative_return_time_only"
        assert report["production_gate"] == "BLOCKED"
    finally:
        ledger.close()
