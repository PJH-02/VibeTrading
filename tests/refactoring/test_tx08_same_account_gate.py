"""TX08: selected same-account joint acceptance through the existing real owners.

The fixture is two actual strategy files loaded by the canonical file loader and
registered on one physical Paper account/matcher/hot path.  A real spool, local
engine, RuntimeCheckpoint restore and the existing watchdog are reused; there is
no second engine, risk scheduler, mock loader or product edit.
"""
from decimal import Decimal as D

import pytest

from test_runtime_checkpoint_integration import append, codec, producer
from test_strategy_runtime import (T, BTC, book, ledger_at, matcher_for, runtime,
                                   trade)
from test_tx05_file_paper_e2e import (feed_new, live_engine, repeated_frame,
                                      restore_engine, wait_until)
from test_vn13_file_registration import configured, strategy_files
from vibetrading.feed_recovery import EngineCursor, RecoveryFrame
from core.models import OrderDestination, OrderIntent, Side, SignalAction
from vibetrading.runtime_checkpoint import RuntimeCheckpoint
from vibetrading.trading_contracts import ActionKind, ExecutionAction, TradingMode

ALPHA = "tx08_alpha_file"
BETA = "tx08_beta_file"
ALPHA_SID = "tx08_alpha"
BETA_SID = "tx08_beta"
ALPHA_JOB = "tx08-alpha-job"
BETA_JOB = "tx08-beta-job"
CONFIG_VERSION = "tx08-config-1"
STRATEGY_VERSION = "tx08-strategy-1"
RISK = {
    "funding_buffer": "0",
    "account_notional_limit": "5000",
    "strategy_notional_limit": "3000",
    "account_capital_limit": "600",
    "strategy_capital_limit": "300",
    "margin_rate": ".1",
}


def strategy_source(strategy_id, action, price):
    """A real stateful file with explicit synchronous checkpoint hooks."""
    return f'''from decimal import Decimal as D
from vibetrading.models import Signal, SignalAction

STRATEGY_ID = {strategy_id!r}
SUBSCRIPTIONS = [{{"instrument_key": {BTC.key!r}, "event_type": "order_book"}}]
WARMUP = {{"bars": 0}}

class Strategy:
    def __init__(self):
        self.state = {{"count": 0, "event_ids": []}}

    def checkpoint_state(self):
        return {{"count": self.state["count"],
                 "event_ids": list(self.state["event_ids"])}}

    def restore_state(self, state):
        self.state = {{"count": int(state["count"]),
                       "event_ids": list(state["event_ids"])}}

    def on_event(self, event, data):
        self.state["count"] += 1
        self.state["event_ids"].append(event.event_id)
        return [Signal(strategy_id=STRATEGY_ID, instrument=event.instrument,
            action=SignalAction.{action}, event_ts_ns=event.receive_ts_ns,
            reference_price=D({price!r}),
            valid_until_ts_ns=event.receive_ts_ns + 100_000,
            source_event_id=event.event_id)]

def create_strategy():
    return Strategy()
'''


def actions_for(ledger, strategy_id, job_id, side):
    def build(signal, view, config):
        identity = job_id + "-" + signal.signal_id
        intent = OrderIntent(intent_id=identity, job_id=job_id,
            signal_id=signal.signal_id, destination=OrderDestination.REAL,
            instrument=BTC, side=side, created_ts_ns=signal.event_ts_ns,
            client_order_id="client-" + identity,
            quantity=D(config["quantity"]), limit_price=D(config["limit_price"]))
        return [ExecutionAction("action-" + identity, ActionKind.NEW,
            ledger.scope, strategy_id, CONFIG_VERSION, TradingMode.PAPER,
            signal.valid_until_ts_ns, intent=intent, post_only=True)]
    return build


def document_for(ledger, module, job_id, price, strategy_id):
    return configured(ledger, module, job_id, config_version=CONFIG_VERSION,
        strategy_version=STRATEGY_VERSION, risk={ledger.scope.key: RISK},
        config={"quantity": "2", "limit_price": price},
        research_receipt={"research_id": "tx08-synthetic-fixture"})


def register_pair(host, ledger, alpha_module, beta_module):
    host.register_configured(
        document_for(ledger, alpha_module, ALPHA_JOB, "99", ALPHA_SID),
        action_builder=actions_for(ledger, ALPHA_SID, ALPHA_JOB, Side.BUY))
    host.register_configured(
        document_for(ledger, beta_module, BETA_JOB, "101", BETA_SID),
        action_builder=actions_for(ledger, BETA_SID, BETA_JOB, Side.SELL))


def strategy_cash(report, strategy):
    return next(row for row in report["strategy_cash"] if row["strategy"] == strategy)


def order_for(report, strategy):
    rows = [row for row in report["orders"] if row["strategy"] == strategy]
    assert len(rows) == 1
    return rows[0]


def spooled_frame(receipt, payload):
    """Translate the actual spool append receipt into the engine transport frame."""
    cursor = receipt.cursor
    frame = RecoveryFrame(EngineCursor(cursor.producer, cursor.incarnation,
                                       cursor.sequence), payload)
    assert (frame.cursor.producer, frame.cursor.incarnation, frame.cursor.sequence) == (
        cursor.producer, cursor.incarnation, cursor.sequence)
    return frame


@pytest.mark.asyncio
async def test_selected_same_account_files_restore_cancel_and_finish_once(
        tmp_path, strategy_files):
    alpha_name = strategy_files(ALPHA, strategy_source(ALPHA_SID, "ENTER_LONG", "100"))
    beta_name = strategy_files(BETA, strategy_source(BETA_SID, "ENTER_SHORT", "101"))
    alpha_path, beta_path = tmp_path / (alpha_name + ".py"), tmp_path / (beta_name + ".py")
    assert alpha_path.is_file() and beta_path.is_file() and alpha_path != beta_path
    assert alpha_name != beta_name
    alpha_module, beta_module = alpha_name, beta_name

    now, monotonic = [T], [0]
    item = producer(tmp_path)
    ledger = ledger_at(tmp_path)
    engine = None
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "tx08 synthetic"})
        matcher = matcher_for(ledger)
        host = runtime(ledger, matcher, now)
        register_pair(host, ledger, alpha_module, beta_module)
        alpha_file = host._jobs[ALPHA_SID].registration.module.module.__file__
        beta_file = host._jobs[BETA_SID].registration.module.module.__file__
        assert alpha_file == str(alpha_path) and beta_file == str(beta_path)
        assert alpha_file != beta_file

        engine = live_engine(item, host, now, monotonic)
        first_book = book()
        feed_new(engine, item, 1, first_book, now, monotonic)
        assert host.hot_path.apply_seq == 1
        assert host.report()["strategies"][ALPHA_SID]["callbacks"] == 1
        assert host.report()["strategies"][BETA_SID]["callbacks"] == 1
        alpha_row = order_for(ledger.report(), ALPHA_SID)
        beta_row = order_for(ledger.report(), BETA_SID)
        alpha_id, beta_id = alpha_row["intent_id"], beta_row["intent_id"]
        assert (alpha_row["side"], beta_row["side"]) == ("buy", "sell")
        assert D(alpha_row["reservation"]) == D("201.96")
        assert D(beta_row["reservation"]) == D("206.04")
        assert D(ledger.report()["reserved"]["USDT"]) == D("408")
        # Opposite pending exposure is gross, not netted away.
        assert ledger.position("@physical", BTC.key) == 0
        assert ledger.position(ALPHA_SID, BTC.key) == 0
        assert ledger.position(BETA_SID, BTC.key) == 0

        sell_trade = trade(T + 11, side=Side.SELL, price=99).model_copy(
            update={"quantity": D(6)})
        feed_new(engine, item, 2, sell_trade, now, monotonic)
        buy_trade = trade(T + 12, side=Side.BUY, price=101).model_copy(
            update={"quantity": D(6)})
        feed_new(engine, item, 3, buy_trade, now, monotonic)
        assert host.hot_path.apply_seq == 3
        assert matcher.report()["fills"] == 2
        assert D(ledger.get(alpha_id)["filled"]) == D(1)
        assert D(ledger.get(beta_id)["filled"]) == D(1)
        filled = ledger.report()
        assert D(filled["balances"]["USDT"]) == D("100018")
        assert ledger.position("@physical", BTC.key) == 0
        assert ledger.position(ALPHA_SID, BTC.key) == 1
        assert ledger.position(BETA_SID, BTC.key) == -1
        assert D(filled["reserved"]["USDT"]) == D("204")
        assert D(filled["simulated_margin"]) == D("200")
        assert D(filled["reserved"]["USDT"]) == D("100.98") + D("103.02")
        assert host.report()["strategies"][ALPHA_SID]["callbacks"] == 1
        assert host.report()["strategies"][BETA_SID]["callbacks"] == 1
        assert engine.stats["feeds"][item.producer]["applied_sequence"] == 3

        # Save the real coupled checkpoint at exactly spooled cut 3, before any
        # idle/cancel state change and before the suffix trade is appended.
        checkpoint = RuntimeCheckpoint(tmp_path / "runtime-checkpoint.json",
            codec(engine), max_bytes=262144, max_journal_rows=10000,
            max_journal_bytes=4_000_000)
        await checkpoint.save(engine, paper_runtime=host)
        assert checkpoint.state == "IDLE"

        await engine.close()
        engine = None
        ledger.close()
        ledger = None

        ledger = ledger_at(tmp_path)
        matcher = matcher_for(ledger)
        host = runtime(ledger, matcher, now)
        register_pair(host, ledger, alpha_module, beta_module)
        for sid in (ALPHA_SID, BETA_SID):
            cold = host.report()["strategies"][sid]
            assert (cold["state"], cold["reason"]) == (
                "BLOCKED", "strategy_state_restore_unverified")
            assert cold["callbacks"] == 0 and cold["actions"] == 0
        assert ledger.get(alpha_id)["state"] == "UNKNOWN"
        assert ledger.get(beta_id)["state"] == "UNKNOWN"
        assert D(ledger.get(alpha_id)["reservation"]) == D("100.98")
        assert D(ledger.get(beta_id)["reservation"]) == D("103.02")
        assert host.hot_path.apply_seq == 0
        assert ledger.position(ALPHA_SID, BTC.key) == 1
        assert ledger.position(BETA_SID, BTC.key) == -1

        engine = restore_engine(item, host, now, monotonic)
        assert engine.post_apply_callbacks == ()
        seen = {}

        def readiness(market_engine, snapshot, receipts):
            seen.update({
                "phase": snapshot.phase,
                "cursor": snapshot.cursor,
                "callbacks_bound": market_engine.post_apply_callbacks,
                "recovery_state": host.recovery_state,
                "callbacks": {sid: host.report()["strategies"][sid]["callbacks"]
                              for sid in (ALPHA_SID, BETA_SID)},
                "counts": {sid: host._jobs[sid].instance.state["count"]
                           for sid in (ALPHA_SID, BETA_SID)},
                "receipts": tuple(receipt.cursor for receipt in receipts),
            })
            return (snapshot.phase == "REPLAY"
                    and snapshot.cursor == (market_engine.hot_path.engine_incarnation,
                                            market_engine.hot_path.apply_seq)
                    and seen["callbacks"] == {ALPHA_SID: 1, BETA_SID: 1}
                    and seen["counts"] == {ALPHA_SID: 1, BETA_SID: 1}
                    and seen["callbacks_bound"] == ())

        await checkpoint.restore(engine, (item,), paper_runtime=host,
                                 readiness=readiness)
        assert checkpoint.state == "READY" and host.recovery_state == "READY"
        assert engine.post_apply_callbacks == (host.process_applied,)
        assert seen["phase"] == "REPLAY"
        assert seen["callbacks_bound"] == ()
        assert seen["recovery_state"] == "RECOVERING"
        assert seen["callbacks"] == {ALPHA_SID: 1, BETA_SID: 1}
        assert seen["counts"] == {ALPHA_SID: 1, BETA_SID: 1}
        assert host._jobs[ALPHA_SID].instance.state["count"] == 1
        assert host._jobs[BETA_SID].instance.state["count"] == 1
        assert host.report()["strategies"][ALPHA_SID]["callbacks"] == 1
        assert host.report()["strategies"][BETA_SID]["callbacks"] == 1
        assert engine.stats["feeds"][item.producer]["applied_sequence"] == 3
        assert D(ledger.get(alpha_id)["reservation"]) == D("100.98")
        assert D(ledger.get(beta_id)["reservation"]) == D("103.02")

        # A wrong-strategy cancel rolls back completely at the real ledger/matcher.
        before_ledger, before_matcher = ledger.report(), matcher.report()
        with pytest.raises(ValueError, match="different strategy"):
            matcher.cancel(alpha_id, BETA_SID, T + 13)
        assert ledger.report() == before_ledger
        assert matcher.report() == before_matcher

        # A legitimate transport suffix re-presents the already-seen buy_trade
        # native payload as real spooled sequence 4.  The native event identity,
        # timestamps and generation are unchanged; only the transport cursor
        # advances, which reattaches the actual engine boundary after restore.
        suffix_payload = buy_trade.model_dump_json().encode()
        receipt = append(item, suffix_payload)
        assert (receipt.cursor.producer, receipt.cursor.incarnation,
                receipt.cursor.sequence) == (item.producer, item.incarnation, 4)
        frame = spooled_frame(receipt, suffix_payload)
        monotonic[0] += 1
        now[0] = buy_trade.receive_ts_ns
        engine.on_frame(frame)
        assert engine.stats["feeds"][item.producer]["applied_sequence"] == 4
        assert host.hot_path.apply_seq == 3
        assert host.report()["input_phase"] in {"LIVE", "PAPER"}
        # The duplicate native identity changes no durable or matcher fact.
        assert ledger.report() == before_ledger
        assert matcher.report() == before_matcher
        assert host.report()["strategies"][ALPHA_SID]["callbacks"] == 1
        assert host.report()["strategies"][BETA_SID]["callbacks"] == 1
        assert host.report()["strategies"][ALPHA_SID]["actions"] == 1
        assert host.report()["strategies"][BETA_SID]["actions"] == 1
        assert len(ledger.report()["orders"]) == 2
        assert D(ledger.get(alpha_id)["filled"]) == D(1)
        assert D(ledger.get(beta_id)["filled"]) == D(1)

        host.pause(ALPHA_SID, T + 13)
        assert host.report()["strategies"][ALPHA_SID]["state"] == "PAUSED"
        assert ledger.get(alpha_id)["state"] == "CANCEL_PENDING"
        assert D(ledger.get(alpha_id)["reservation"]) == D("100.98")
        assert ledger.get(beta_id)["state"] == "UNKNOWN"
        assert D(matcher.report()["open_orders"][beta_id]["leaves"]) == D(1)
        assert D(ledger.get(beta_id)["reservation"]) == D("103.02")
        assert ledger.position(ALPHA_SID, BTC.key) == 1
        assert ledger.position(BETA_SID, BTC.key) == -1

        # The existing engine watchdog invokes host.advance at the injected epoch;
        # no native market event is needed to make the cancel effective.
        now[0] = T + 18
        await engine.start()
        await wait_until(lambda: ledger.get(alpha_id)["state"] == "CANCELED")
        assert D(ledger.get(alpha_id)["reservation"]) == 0
        assert ledger.get(beta_id)["state"] == "UNKNOWN"
        assert D(matcher.report()["open_orders"][beta_id]["leaves"]) == D(1)
        assert D(ledger.get(beta_id)["reservation"]) == D("103.02")
        assert D(ledger.report()["reserved"]["USDT"]) == D("103.02")
        assert ledger.position(ALPHA_SID, BTC.key) == 1
        assert ledger.position(BETA_SID, BTC.key) == -1
        assert matcher.cancel(alpha_id, ALPHA_SID, T + 18) is False
        assert D(ledger.get(alpha_id)["reservation"]) == 0

        # Cursor 5: the remaining beta leave fills exactly once.
        final_trade = trade(T + 20, side=Side.BUY, price=101).model_copy(
            update={"quantity": D(1)})
        feed_new(engine, item, 5, final_trade, now, monotonic)
        final = ledger.report()
        assert engine.stats["feeds"][item.producer]["applied_sequence"] == 5
        # Transport suffix 4 carried no new native event, so this first fresh
        # native event after restore is apply_seq 4.
        assert host.hot_path.apply_seq == 4
        assert matcher.report()["fills"] == 3
        assert len(final["orders"]) == 2
        assert ledger.get(alpha_id)["state"] == "CANCELED"
        assert ledger.get(beta_id)["state"] == "FILLED"
        assert D(ledger.get(beta_id)["filled"]) == D(2)
        assert D(final["balances"]["USDT"]) == D("100016.99")
        assert D(final["physical_positions"][BTC.key]) == D(-1)
        assert D(final["virtual_positions"][ALPHA_SID][BTC.key]) == 1
        assert D(final["virtual_positions"][BETA_SID][BTC.key]) == D(-2)
        assert D(final["reserved"]["USDT"]) == 0
        assert all(D(row["reservation"]) == 0 for row in final["orders"])
        assert D(final["simulated_margin"]) == D("301")
        assert D("408") - D(final["reserved"]["USDT"]) == D("408")
        assert D(strategy_cash(final, ALPHA_SID)["fees"]) == D("0.99")
        assert D(strategy_cash(final, BETA_SID)["fees"]) == D("2.02")
        assert D(strategy_cash(final, "@physical_netting_adjustment")["amount"]) == D("20")
        assert host.report()["strategies"][ALPHA_SID]["callbacks"] == 1
        assert host.report()["strategies"][BETA_SID]["callbacks"] == 1

        # The exact same transport frame is an engine duplicate and moves nothing.
        before_ledger, before_matcher = ledger.report(), matcher.report()
        before_host = host.report()
        repeated_frame(engine, item, 5, final_trade)
        assert engine.stats["duplicates"] == 1
        assert host.hot_path.apply_seq == 4
        assert ledger.report() == before_ledger
        assert matcher.report() == before_matcher
        assert host.report() == before_host
        assert matcher.report()["fills"] == 3

        # A stale full native book cannot create a new risky order or be treated
        # as known-fresh evidence; no new native generation is forged.
        stale = book(T + 1, sequence=2)
        stale_payload = stale.model_dump_json().encode()
        stale_receipt = append(item, stale_payload)
        assert (stale_receipt.cursor.producer, stale_receipt.cursor.incarnation,
                stale_receipt.cursor.sequence) == (item.producer, item.incarnation, 6)
        monotonic[0] += 1
        now[0] = T + 2_000_000_000
        engine.on_frame(spooled_frame(stale_receipt, stale_payload))
        assert engine.stats["feeds"][item.producer]["applied_sequence"] == 6
        assert host.hot_path.apply_seq == 5
        stale_report = ledger.report()
        assert all(row["state"] in {"CANCELED", "FILLED"} for row in stale_report["orders"])
        assert len(stale_report["orders"]) == 2
        assert matcher.report()["fills"] == 3
        assert D(stale_report["balances"]["USDT"]) == D("100016.99")
        assert D(stale_report["reserved"]["USDT"]) == 0
        assert D(stale_report["physical_positions"][BTC.key]) == D(-1)
        assert D(stale_report["virtual_positions"][ALPHA_SID][BTC.key]) == 1
        assert D(stale_report["virtual_positions"][BETA_SID][BTC.key]) == D(-2)
        assert host.report()["strategies"][ALPHA_SID]["callbacks"] == 1
        assert host.report()["strategies"][BETA_SID]["callbacks"] == 1
        assert BTC.key in host.report()["invalid_inputs"]
        assert [row["strategy"] for row in stale_report["orders"]].count(ALPHA_SID) == 1
        assert [row["strategy"] for row in stale_report["orders"]].count(BETA_SID) == 1
    finally:
        if engine is not None:
            await engine.close()
        if ledger is not None:
            ledger.close()
