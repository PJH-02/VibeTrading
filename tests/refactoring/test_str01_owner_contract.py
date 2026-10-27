"""Opt-in Paper owner contracts for protective ETF execution semantics."""
from decimal import Decimal as D

import pytest

from execution.paper_execution import PaperExecution
from test_paper_execution import T, book, engine, intent, open_ledger, rules, submit


def market_intent(identity="market", quantity=4, reserve="110"):
    base = intent(identity, price=99, quantity=quantity)
    return base.model_copy(update={
        "limit_price": None,
        "metadata": {"reserve_unit_price": str(reserve)},
    })


def test_default_owner_rejects_taker_gtc_and_true_market(tmp_path):
    ledger = open_ledger(tmp_path)
    ledger.seed_balance("USDT", D(100000), {"fixture": "paper only"})
    matcher = engine(ledger)
    try:
        matcher.on_book(book())
        assert dict(matcher.capabilities) == {"allow_taker_gtc": False, "allow_market_orders": False}
        with pytest.raises(ValueError, match="IOC protection"):
            submit(matcher, post_only=False, time_in_force="GTC")
        with pytest.raises(ValueError, match="market orders are disabled"):
            matcher.submit(market_intent(), "strategy", {"decision": "market"}, T+1000,
                rules(), T, reference_price=D(100), margin_rate=D(".1"), funding_buffer=D(0),
                account_notional_limit=D(100000), strategy_notional_limit=D(100000),
                account_capital_limit=D(100000), strategy_capital_limit=D(100000),
                post_only=False, time_in_force="IOC")
    finally:
        ledger.close()


def test_taker_gtc_sweeps_once_then_uses_only_native_replenishment(tmp_path):
    ledger = open_ledger(tmp_path)
    ledger.seed_balance("USDT", D(100000), {"fixture": "paper only"})
    matcher = engine(ledger, allow_taker_gtc=True)
    try:
        matcher.on_book(book())
        submit(matcher, "gtc", price=101, quantity=4, post_only=False, time_in_force="GTC")
        matcher.advance(T+10)
        assert D(ledger.get("gtc")["filled"]) == 2
        assert matcher.report()["open_orders"]["gtc"]["leaves"] == "2"

        # A repeated sequence cannot replenish used volume, even if the
        # displayed quantity grows and the receipt has a new identity.
        matcher.on_book(book(T+20, ask_qty=5, sequence=1, identity="repeat-sequence"))
        assert D(ledger.get("gtc")["filled"]) == 2

        # A newer sequence without source-time evidence cannot advance a
        # source-time fenced order, and must not crash or invent fills.
        unknown_source = book(T+25, ask_qty=2, sequence=2, identity="unknown-source")
        matcher.on_book(unknown_source.model_copy(update={"source_ts_ns": None}))
        assert D(ledger.get("gtc")["filled"]) == 2

        matcher.on_book(book(T+30, ask_qty=2, sequence=3, identity="next-sequence"))
        assert D(ledger.get("gtc")["filled"]) == 4
        assert matcher.report()["open_orders"] == {}
        assert ledger.get("gtc")["state"] == "FILLED"
    finally:
        ledger.close()


def test_true_market_keeps_null_limit_reserves_at_explicit_cap_and_restarts(tmp_path):
    ledger = open_ledger(tmp_path)
    ledger.seed_balance("USDT", D(100000), {"fixture": "paper only"})
    matcher = engine(ledger, allow_market_orders=True)
    try:
        matcher.on_book(book())
        order = market_intent("market", quantity=4, reserve="110")
        matcher.submit(order, "strategy", {"decision": "market"}, T+1000, rules(), T,
            reference_price=D(999), margin_rate=D(".1"), funding_buffer=D(0),
            account_notional_limit=D(100000), strategy_notional_limit=D(100000),
            account_capital_limit=D(100000), strategy_capital_limit=D(100000),
            post_only=False, time_in_force="IOC")
        row = ledger.get("market")
        assert row is not None
        assert __import__("json").loads(row["payload"])["limit_price"] is None
        assert __import__("json").loads(row["payload"])["metadata"]["reserve_unit_price"] == "110"

        # Restart while the market intent is durable but before its arrival.
        ledger.close()
        ledger = open_ledger(tmp_path)
        matcher = engine(ledger, allow_market_orders=True)
        matcher.advance(T+10)
        assert D(ledger.get("market")["filled"]) == 2
        assert ledger.get("market")["state"] == "CANCELED"
        assert D(ledger.get("market")["reservation"]) == 0
        assert ledger.position("strategy", order.instrument.key) == 2
    finally:
        ledger.close()


def test_market_reserve_tampering_or_capability_mismatch_rejects_restart(tmp_path):
    ledger = open_ledger(tmp_path)
    ledger.seed_balance("USDT", D(100000), {"fixture": "paper only"})
    matcher = engine(ledger, allow_market_orders=True)
    matcher.on_book(book())
    order = market_intent("market", quantity=1, reserve="110")
    matcher.submit(order, "strategy", {"decision": "market"}, T+1000, rules(), T,
        margin_rate=D(".1"), funding_buffer=D(0), account_notional_limit=D(100000),
        strategy_notional_limit=D(100000), account_capital_limit=D(100000),
        strategy_capital_limit=D(100000), post_only=False, time_in_force="IOC")
    try:
        with pytest.raises(ValueError, match="configuration"):
            engine(ledger)
        state = ledger.paper_state()
        state["orders"]["market"]["price"] = "120"
        ledger.save_paper_state(state)
        with pytest.raises(ValueError, match="differs from the admitted order"):
            engine(ledger, allow_market_orders=True)
    finally:
        ledger.close()


def test_market_sweep_is_capped_by_reserve_not_midpoint_or_available_depth(tmp_path):
    ledger = open_ledger(tmp_path)
    ledger.seed_balance("USDT", D(100000), {"fixture": "paper only"})
    matcher = engine(ledger, allow_market_orders=True)
    try:
        matcher.on_book(book())
        order = market_intent("capped", quantity=3, reserve="100")
        matcher.submit(order, "strategy", {"decision": "market"}, T+1000, rules(), T,
            reference_price=D(999), margin_rate=D(".1"), funding_buffer=D(0),
            account_notional_limit=D(100000), strategy_notional_limit=D(100000),
            account_capital_limit=D(100000), strategy_capital_limit=D(100000),
            post_only=False, time_in_force="IOC")
        matcher.advance(T+10)
        assert D(ledger.get("capped")["filled"]) == 0
        assert ledger.get("capped")["state"] == "CANCELED"
        assert ledger.position("strategy", order.instrument.key) == 0
    finally:
        ledger.close()


def test_capabilities_are_read_only_and_strict(tmp_path):
    ledger = open_ledger(tmp_path)
    try:
        with pytest.raises(ValueError, match="explicit booleans"):
            PaperExecution(ledger, entry_latency_ns=10, cancel_latency_ns=5,
                max_open_orders=8, max_depth=8, max_receipts=100,
                max_state_bytes=500000, stale_after_ns=1000, allow_market_orders=1)
        matcher = engine(ledger)
        with pytest.raises(TypeError):
            matcher.capabilities["allow_market_orders"] = True
    finally:
        ledger.close()


@pytest.mark.parametrize("reserve", [100, True, D("100")])
def test_non_string_market_reserve_rejects_without_admission_or_state_change(tmp_path, reserve):
    ledger = open_ledger(tmp_path)
    ledger.seed_balance("USDT", D(100000), {"fixture": "paper only"})
    matcher = engine(ledger, allow_market_orders=True)
    try:
        matcher.on_book(book())
        before_report = matcher.report()
        before_state = ledger.paper_state()
        malformed = intent("bad-reserve", price=99, quantity=1).model_copy(update={
            "limit_price": None, "metadata": {"reserve_unit_price": reserve},
        })
        with pytest.raises(ValueError, match="decimal string"):
            matcher.submit(malformed, "strategy", {"decision": "market"}, T+1000, rules(), T,
                margin_rate=D(".1"), funding_buffer=D(0), account_notional_limit=D(100000),
                strategy_notional_limit=D(100000), account_capital_limit=D(100000),
                strategy_capital_limit=D(100000), post_only=False, time_in_force="IOC")
        assert matcher.report() == before_report
        assert ledger.paper_state() == before_state
        assert ledger.get("bad-reserve") is None
    finally:
        ledger.close()
