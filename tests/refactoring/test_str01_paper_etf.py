"""STR-01 real Paper acceptance: configured file plugins through the existing host."""
from decimal import Decimal as D
from pathlib import Path
import socket

import pandas as pd
import pytest

from core.models import (AssetClass, Bar1mEvent, IndexQuoteEvent, InstrumentId,
                         OrderBookEvent, OrderBookLevel, Side, TradeEvent, Venue)
from execution.execution_ledger import AccountScope
from feeds.providers.kiwoom import KiwoomCollector, KiwoomSubscription
from feeds.providers.kis import kis_row_to_event
from execution.paper_execution import PaperExecution
from execution.trading_contracts import ProductKind, ProductSpec, TradingMode
from market.hot_path import HotPathStore
from portfolio.portfolio_ledger import PortfolioLedger
from runtime.strategy_runtime import (PaperStrategyRuntime, RuntimeBudgets,
                                      StrategyRisk)
from strategies import load_strategy
from strategies.kr._etf_plugin import _feed_instrument
from str01_native_history_fixture import DEFAULT_DAY, build_native_history

SYMBOLS = ("069500", "122630", "229200", "251340")
DAY = DEFAULT_DAY
ACCOUNT = {"venue": "kis", "account": "str01-paper", "product": "cash", "environment": "paper"}
SCOPE = AccountScope(**ACCOUNT)


def kiwoom(symbol):
    return KiwoomCollector.__new__(KiwoomCollector).instrument(KiwoomSubscription(symbol, "KRX"))


def kis(symbol):
    return InstrumentId(venue=Venue.KIS, asset_class=AssetClass.ETF, raw_symbol=symbol,
                        std_symbol=symbol, currency="KRW", exchange="KRX")


INDEX = InstrumentId(venue=Venue.KIS, asset_class=AssetClass.INDEX, raw_symbol="2001",
                     std_symbol="KOSPI200", currency="KRW", exchange="KRX")


def feed_key(symbol):
    return kiwoom(symbol).key


def product_key(symbol):
    return kis(symbol).key


def spec(symbol):
    instrument = kis(symbol)
    return ProductSpec(instrument, ProductKind.CASH, D(1), D(1), D(1), D(1), "KRW",
                       "KRW", "KRW", "net", "cash", "synthetic", "none", "cash",
                       D("0"), D("0"))


def budgets(**changes):
    values = dict(max_jobs=4, max_accounts=2, max_required_feeds=8,
                  max_signals_per_callback=2, max_actions_per_callback=2,
                  max_pending_actions=4, max_seen_actions=100,
                  max_config_bytes=64_000, max_signal_bytes=16_384,
                  max_action_bytes=32_000, max_callback_ns=1_000_000_000,
                  max_feed_age_ns=86_400_000_000_000)
    return RuntimeBudgets(**{**values, **changes})


def make_host(tmp_path, *, capital=D(1_000_000)):
    ledger = PortfolioLedger(tmp_path / "paper" / "account.sqlite", SCOPE,
                             owner_root=tmp_path, mode=TradingMode.PAPER,
                             max_orders=100, max_fills=100, max_events=1000,
                             max_document_bytes=1_000_000, max_db_bytes=16_000_000,
                             max_wal_bytes=16_000_000)
    ledger.seed_balance("KRW", capital, {"source": "str01-fixture"})
    matcher = PaperExecution(ledger, entry_latency_ns=10, cancel_latency_ns=5,
                             max_open_orders=16, max_depth=8, max_receipts=1000,
                             max_state_bytes=5_000_000,
                             stale_after_ns=86_400_000_000_000,
                             allow_taker_gtc=True, allow_market_orders=True)
    known = {feed_key(symbol): kiwoom(symbol) for symbol in SYMBOLS}
    known[INDEX.key] = INDEX
    now = [0]
    host = PaperStrategyRuntime(
        hot_path=HotPathStore(), accounts={SCOPE.key: matcher},
        products={product_key(symbol): spec(symbol) for symbol in SYMBOLS},
        known_instruments=known,
        reference_feeds={product_key(symbol): feed_key(symbol) for symbol in SYMBOLS},
        budgets=budgets(), settings={"fixture": "str01"}, mode=TradingMode.PAPER,
        phase="paper", now_ns=lambda: now[0], callback_clock_ns=lambda: 0)
    return host, matcher, ledger, now


def config(facts, strategy, symbols=SYMBOLS):
    cfg = {"day": facts["day"], "history_root": str(facts["root"]), "mode": "paper",
           "job_id": f"str01-{strategy.lower()}", "strategy": strategy,
           "config_version": "str01-v1", "account": dict(ACCOUNT),
           "account_scope": SCOPE.key, "symbols": list(symbols),
           "execution_venue": "kis", "reference_venue": "kiwoom",
           "history_manifest_sha256": facts["manifest_sha256"]}
    if strategy == "C3":
        cfg["index_key"] = INDEX.key
    return cfg


def registration(facts, strategy, required_feeds, symbols=SYMBOLS):
    module = load_strategy(f"strategies.kr.{strategy.lower()}")
    risk = {SCOPE.key: {"funding_buffer": "0", "account_notional_limit": "100000000",
                        "strategy_notional_limit": "100000000",
                        "account_capital_limit": "100000000",
                        "strategy_capital_limit": "100000000", "margin_rate": None}}
    return {"job_id": f"str01-{strategy.lower()}", "module": f"strategies.kr.{strategy.lower()}",
            "strategy_version": "str01-v1", "config_version": "str01-v1",
            "config": config(facts, strategy, symbols),
            "research_receipt": {"ticket": "STR-01"}, "paper_permission": True,
            "required_feeds": list(required_feeds), "warmup_bars": 0,
            "accounts": [SCOPE.key], "risk": risk}, module.module.action_builder


def ns(clock):
    return int(pd.Timestamp(f"{DAY} {clock}", tz="Asia/Seoul").value)


def deliver(host, now, event):
    now[0] = event.receive_ts_ns
    return host.process_event(event)


def book(symbol, clock="09:00", bid=None, ask=None, *, bid_qty=100000, ask_qty=100000):
    stamp = ns(clock)
    bid = D("100") if bid is None and symbol == "069500" else D(bid or "100")
    ask = D("101") if ask is None and symbol == "069500" else D(ask or "101")
    return OrderBookEvent(instrument=kiwoom(symbol), venue=Venue.KIWOOM, provider="kiwoom",
                          event_id=f"book:{symbol}:{stamp}", receive_ts_ns=stamp,
                          source_ts_ns=stamp, sequence=stamp, final_update_id=stamp,
                          sync_generation=1, depth=1,
                          bids=[OrderBookLevel(price=bid, quantity=D(bid_qty))],
                          asks=[OrderBookLevel(price=ask, quantity=D(ask_qty))])


def index_quote(clock, open_):
    stamp = ns(clock)
    utc = pd.Timestamp(f"{DAY} {clock}", tz="Asia/Seoul").tz_convert("UTC")
    row = {"bstp_cls_code": "2001", "bsop_hour": clock.replace(":", "") + "00",
           "prpr_nmix": str(open_), "oprc_nmix": str(open_), "nmix_hgpr": str(open_),
           "nmix_lwpr": str(open_), "recv_date": utc.strftime("%Y%m%d"),
           "recv_time": utc.strftime("%H%M%S")}
    return kis_row_to_event("H0UPCNT0", row, receive_ts_ns=stamp)


def bar(symbol, clock, open_, close, *, high=None, low=None, volume=1000):
    stamp = ns(clock)
    return Bar1mEvent(instrument=kiwoom(symbol), venue=Venue.KIWOOM, provider="kiwoom",
                      event_id=f"bar:{symbol}:{stamp}", receive_ts_ns=stamp,
                      source_ts_ns=stamp, trading_date=DAY,
                      open_ts_ns=stamp - 60_000_000_000, close_ts_ns=stamp,
                      open=D(str(open_)), high=D(str(high or max(open_, close))),
                      low=D(str(low or min(open_, close))), close=D(str(close)),
                      volume=D(str(volume)))


def trade(symbol, clock, price, quantity=200000):
    stamp = ns(clock)
    return TradeEvent(instrument=kiwoom(symbol), venue=Venue.KIWOOM, provider="kiwoom",
                      event_id=f"trade:{symbol}:{stamp}", receive_ts_ns=stamp,
                      source_ts_ns=stamp, trading_date=DAY, price=D(str(price)),
                      quantity=D(str(quantity)), aggressor_side=Side.SELL)


def test_c3_paper_retries_native_index_open_and_enters(tmp_path, monkeypatch):
    facts = build_native_history(tmp_path / "history")
    host, matcher, ledger, now = make_host(tmp_path)
    try:
        document, builder = registration(
            facts, "C3", (feed_key("069500"), feed_key("122630"), INDEX.key))
        host.register_configured(document, action_builder=builder)

        def forbidden(*args, **kwargs):
            raise AssertionError("ETF callback attempted disk or network I/O after startup")

        input_events = [book("069500", "09:00"), book("122630", "09:00", "200", "201"),
                        bar("069500", "09:01", 100, 100),
                        book("069500", "09:03", "100", "101"),
                        book("122630", "09:03", "200", "201"),
                        index_quote("09:03", facts["last_index_close"] * 1.001)]
        with monkeypatch.context() as cache_guard:
            cache_guard.setattr(Path, "stat", forbidden)
            for symbol in ("069500", "122630"):
                _feed_instrument(config(facts, "C3"), symbol)
        with monkeypatch.context() as io_guard:
            for name in ("open", "read_bytes", "read_text"):
                io_guard.setattr(Path, name, forbidden)
            io_guard.setattr(socket, "create_connection", forbidden)
            for event in input_events:
                deliver(host, now, event)
        rows = ledger.report()["orders"]
        assert len(rows) == 1, (host.report()["strategies"], host.report()["account_faults"],
                                host.hot_path.snapshot(now[0], "PAPER").quality)
        payload = __import__("json").loads(rows[0]["payload"])
        plan = payload["metadata"]["plan"]
        assert plan["kind"] == "entry" and plan["symbol"] == "122630"
        assert plan["execution"] == "limit"
        assert plan["limit_price"] == "210" and plan["post_only"] is False
        assert host.report()["strategies"]["C3"]["state"] == "PAPER"
    finally:
        ledger.close()


def test_c3_completed_no_entry_does_not_redecide_on_revised_index_open(tmp_path):
    facts = build_native_history(tmp_path / "history")
    host, matcher, ledger, now = make_host(tmp_path)
    try:
        document, builder = registration(
            facts, "C3", (feed_key("069500"), feed_key("122630"), INDEX.key))
        host.register_configured(document, action_builder=builder)
        deliver(host, now, book("069500", "09:00", "100", "101"))
        deliver(host, now, book("122630", "09:00", "200", "201"))
        deliver(host, now, bar("069500", "09:01", 100, 100))
        deliver(host, now, book("069500", "09:03", "100", "101"))
        deliver(host, now, book("122630", "09:03", "200", "201"))
        # ETF opens rich against this native index open, so the completed call is NO_ENTRY.
        deliver(host, now, index_quote("09:03", facts["last_index_close"] * .95))
        assert ledger.report()["orders"] == []
        deliver(host, now, index_quote("09:04", facts["last_index_close"] * 1.001))
        assert ledger.report()["orders"] == []
    finally:
        ledger.close()


def deliver_minutes(host, now, symbol, *, start_open=100.0, drift=.008, count=30):
    price = start_open
    for minute in range(1, count + 1):
        close = price * (1 + drift)
        deliver(host, now, bar(symbol, f"09:{minute:02d}", price, close))
        price = close
    return price


def test_kp_paper_entry_fills_and_session_exit_is_source_owned(tmp_path):
    facts = build_native_history(tmp_path / "history")
    host, matcher, ledger, now = make_host(tmp_path)
    try:
        document, builder = registration(
            facts, "KP", (feed_key("069500"), feed_key("122630")))
        host.register_configured(document, action_builder=builder)
        deliver(host, now, book("069500", "09:00", "100", "101"))
        deliver(host, now, book("122630", "09:00", "200", "201"))
        deliver_minutes(host, now, "069500")
        rows = ledger.report()["orders"]
        assert rows and all(__import__("json").loads(row["payload"])["metadata"]["plan"]["kind"] == "entry"
                            for row in rows)
        for row in list(rows):
            symbol = __import__("json").loads(row["payload"])["metadata"]["plan"]["symbol"]
            price = "100" if symbol == "069500" else "200"
            deliver(host, now, trade(symbol, "09:30:30", price))
        assert ledger.report()["virtual_positions"]["KP"]
        deliver(host, now, book("069500", "15:19:54", "100", "101"))
        deliver(host, now, book("122630", "15:19:54", "200", "201"))
        deliver(host, now, bar("069500", "15:19:55", 100, 100))
        plans = [__import__("json").loads(row["payload"])["metadata"]["plan"]
                 for row in ledger.report()["orders"]]
        assert any(plan["kind"] == "exit" for plan in plans), (plans, host.report()["strategies"],
            [(row["state"], row["side"], row["quantity"], row["filled"]) for row in ledger.report()["orders"]])
    finally:
        ledger.close()


def test_kd_paper_minute_stop_precedes_thirty_minute_gate(tmp_path):
    facts = build_native_history(tmp_path / "history")
    host, matcher, ledger, now = make_host(tmp_path)
    try:
        document, builder = registration(
            facts, "KD", (feed_key("229200"), feed_key("251340")))
        host.register_configured(document, action_builder=builder)
        deliver(host, now, book("229200", "09:00", "150", "151"))
        deliver(host, now, book("251340", "09:00", "50", "51"))
        deliver_minutes(host, now, "229200", start_open=150.0)
        entry = ledger.report()["orders"]
        assert entry
        deliver(host, now, trade("229200", "09:30:30", "150"))
        assert ledger.report()["virtual_positions"]["KD"]
        deliver(host, now, bar("229200", "09:31", 150, 1))
        plans = [__import__("json").loads(row["payload"])["metadata"]["plan"]
                 for row in ledger.report()["orders"]]
        assert any(plan["kind"] == "exit" for plan in plans)
    finally:
        ledger.close()


def test_c3_paper_cancels_once_after_five_seconds_and_replaces_confirmed_remainder(tmp_path):
    facts = build_native_history(tmp_path / "history")
    host, matcher, ledger, now = make_host(tmp_path)
    try:
        document, builder = registration(
            facts, "C3", (feed_key("069500"), feed_key("122630"), INDEX.key))
        host.register_configured(document, action_builder=builder)
        deliver(host, now, book("069500", "09:00", "100", "101"))
        deliver(host, now, book("122630", "09:00", "200", "201", ask_qty=1))
        deliver(host, now, bar("069500", "09:01", 100, 100))
        deliver(host, now, book("069500", "09:03", "100", "101"))
        deliver(host, now, book("122630", "09:03", "200", "201", ask_qty=1))
        deliver(host, now, index_quote("09:03", facts["last_index_close"] * 1.001))
        original = ledger.report()["orders"]
        assert len(original) == 1
        deliver(host, now, bar("069500", "09:03:09", 100, 100))
        states = [row["state"] for row in ledger.report()["orders"]]
        assert states == ["CANCEL_PENDING"]
        host.advance(now[0] + 1_000_000)
        deliver(host, now, bar("069500", "09:03:10", 100, 100))
        rows = ledger.report()["orders"]
        plans = [__import__("json").loads(row["payload"])["metadata"]["plan"] for row in rows]
        assert any(plan.get("execution") == "market_remainder" for plan in plans)
        market_order = next(row for row in rows if __import__("json").loads(row["payload"])
                            ["metadata"]["plan"].get("execution") == "market_remainder")
        market_payload = __import__("json").loads(market_order["payload"])
        assert market_payload["limit_price"] is None
        assert market_payload["metadata"]["reserve_unit_price"] == "210"
        deliver(host, now, book("122630", "09:03:10", "200", "201", ask_qty=10000))
        deliver(host, now, book("069500", "09:03:10", "100", "101"))
        deliver(host, now, trade("122630", "09:03:11", "201"))
        final = {row["state"] for row in ledger.report()["orders"]}
        assert "CANCELED" in final and "FILLED" in final
        assert ledger.report()["virtual_positions"]["C3"]
    finally:
        ledger.close()


@pytest.mark.parametrize("strategy", ["C3", "KP", "KD"])
def test_native_underfunded_entry_skips_without_fault_or_false_entry(tmp_path, strategy):
    facts = build_native_history(tmp_path / "history")
    host, matcher, ledger, now = make_host(tmp_path, capital=D(1))
    try:
        symbols = ("229200", "251340") if strategy == "KD" else ("069500", "122630")
        required = tuple(feed_key(s) for s in symbols) + ((INDEX.key,) if strategy == "C3" else ())
        document, builder = registration(facts, strategy, required)
        host.register_configured(document, action_builder=builder)
        for symbol in symbols:
            deliver(host, now, book(symbol, "09:00"))
        if strategy == "C3":
            deliver(host, now, bar("069500", "09:01", 100, 100))
            for symbol in symbols:
                deliver(host, now, book(symbol, "09:03"))
            deliver(host, now, index_quote("09:03", facts["last_index_close"] * 1.001))
        else:
            deliver_minutes(host, now, symbols[0], start_open=150.0 if strategy == "KD" else 100.0)
        assert ledger.report()["orders"] == []
        assert host.report()["strategies"][strategy]["state"] == "PAPER"
        assert host._jobs[strategy].instance.checkpoint_state()["entries"] == 0
    finally:
        ledger.close()


def test_c3_untraded_first_minute_does_not_claim_native_day_open(tmp_path):
    facts = build_native_history(tmp_path / "history")
    host, matcher, ledger, now = make_host(tmp_path)
    try:
        document, builder = registration(facts, "C3", (feed_key("069500"), feed_key("122630"), INDEX.key))
        host.register_configured(document, action_builder=builder)
        deliver(host, now, book("069500", "09:00"))
        deliver(host, now, book("122630", "09:00", "200", "201"))
        deliver(host, now, bar("069500", "09:01", 100, 100, volume=0))
        deliver(host, now, book("069500", "09:03"))
        deliver(host, now, book("122630", "09:03", "200", "201"))
        deliver(host, now, index_quote("09:03", facts["last_index_close"] * 1.001))
        assert ledger.report()["orders"] == []
        state = host._jobs["C3"].instance.checkpoint_state()
        assert state["first_open"] is None and state["decision_complete"] is False
    finally:
        ledger.close()
