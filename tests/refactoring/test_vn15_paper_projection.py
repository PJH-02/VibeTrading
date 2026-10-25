"""VN15: cold paper projection over explicit local DBs; no venue or owner writes."""
import asyncio
from dataclasses import asdict
from decimal import Decimal as D
import json
import os
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from vibetrading.dashboard import bridge
from vibetrading.dashboard.projection import paper_projection, paper_projection_paths
from vibetrading.execution_ledger import AccountScope
from vibetrading.hot_path import HotPathStore
from vibetrading.local_snapshot import (SnapshotBudgets, atomic_write_snapshot,
                                        capture_market_snapshot)
from vibetrading.models import (AssetClass, InstrumentId, OrderBookEvent, OrderBookLevel,
                                OrderDestination, OrderIntent, Side, TradeEvent, Venue)
from vibetrading.paper_execution import PaperExecution
from vibetrading.portfolio_ledger import PortfolioLedger
from vibetrading.trading_contracts import ProductKind, ProductSpec


T = 1_800_000_000_000_000_000
INSTRUMENT = InstrumentId(venue=Venue.BYBIT, asset_class=AssetClass.CRYPTO_PERP,
                          raw_symbol="BTCUSDT", std_symbol="BTCUSDT", currency="USDT")


def open_ledger(root):
    return PortfolioLedger(root / "paper" / "account.sqlite",
        AccountScope("bybit", "paper-a", "perpetual", "paper"), owner_root=root,
        mode="paper", max_orders=100, max_fills=100, max_events=1000,
        max_document_bytes=1_000_000, max_db_bytes=16_000_000, max_wal_bytes=16_000_000)


def rules():
    return ProductSpec(INSTRUMENT, ProductKind.LINEAR_PERPETUAL, D(10), D(1), D(1), D(1),
        "USDT", "USDT", "USDT", "net", "isolated", "synthetic fee", "explicit funding receipts",
        "synthetic isolated margin", D(".001"), D(".002"))


def intent(identity="one", price=99, quantity=1, side=Side.BUY, metadata=None):
    return OrderIntent(intent_id=identity, job_id="job", signal_id="signal-" + identity,
        destination=OrderDestination.REAL, instrument=INSTRUMENT, side=side,
        created_ts_ns=T, client_order_id="client-" + identity, quantity=D(quantity),
        limit_price=D(price), metadata=metadata or {})


def engine(ledger, **changes):
    options = dict(entry_latency_ns=10, cancel_latency_ns=5, max_open_orders=8,
                   max_depth=8, max_receipts=100, max_state_bytes=500_000, stale_after_ns=1000)
    return PaperExecution(ledger, **{**options, **changes})


def submit(matcher, identity="one", price=99, quantity=1, now=T, strategy="strategy", **changes):
    return matcher.submit(intent(identity, price, quantity), strategy, {"decision": identity},
        T + 1000, rules(), now, reference_price=D(100), margin_rate=D(".1"), funding_buffer=D(0),
        account_notional_limit=D(100000), strategy_notional_limit=D(100000),
        account_capital_limit=D(100000), strategy_capital_limit=D(100000), **changes)


def book(now=T, *, bid=99, ask=101, bid_qty=5, ask_qty=2, sequence=1, identity=None):
    return OrderBookEvent(instrument=INSTRUMENT, venue=Venue.BYBIT, provider="synthetic",
        event_id=identity or "book-" + str(now), receive_ts_ns=now, wallclock_ts_ns=now,
        source_ts_ns=now, sequence=sequence, sync_generation=1, depth=1,
        bids=[OrderBookLevel(price=D(bid), quantity=D(bid_qty))],
        asks=[OrderBookLevel(price=D(ask), quantity=D(ask_qty))])


def trade(now=T + 11, *, quantity=6, price=99, identity=None):
    return TradeEvent(instrument=INSTRUMENT, venue=Venue.BYBIT, provider="synthetic",
        event_id=identity or "trade-" + str(now), receive_ts_ns=now, wallclock_ts_ns=now,
        source_ts_ns=now, price=D(price), quantity=D(quantity), aggressor_side=Side.SELL)


def manual_db(path, *, mode="paper", orders=0, include_tables=True):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE portfolio_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        if include_tables:
            db.execute("CREATE TABLE portfolio_balances(currency TEXT PRIMARY KEY,amount TEXT NOT NULL)")
            db.execute("CREATE TABLE portfolio_orders(intent_id TEXT PRIMARY KEY,strategy TEXT NOT NULL,"
                       "payload TEXT NOT NULL,rules TEXT NOT NULL,state TEXT NOT NULL,quantity TEXT NOT NULL,"
                       "filled TEXT NOT NULL,reservation TEXT NOT NULL,reserve_currency TEXT NOT NULL,"
                       "instrument TEXT NOT NULL,side TEXT NOT NULL)")
            db.execute("CREATE TABLE portfolio_positions(strategy TEXT NOT NULL,instrument TEXT NOT NULL,"
                       "quantity TEXT NOT NULL,average TEXT NOT NULL,margin TEXT NOT NULL,"
                       "PRIMARY KEY(strategy,instrument))")
            db.execute("CREATE TABLE portfolio_strategy_cash(strategy TEXT NOT NULL,currency TEXT NOT NULL,"
                       "amount TEXT NOT NULL,fees TEXT NOT NULL,funding TEXT NOT NULL,realized TEXT NOT NULL,"
                       "PRIMARY KEY(strategy,currency))")
            db.execute("CREATE TABLE portfolio_gaps(instrument TEXT PRIMARY KEY,document TEXT NOT NULL)")
        db.executemany("INSERT INTO portfolio_metadata VALUES(?,?)", [
            ("scope", json.dumps(["bybit", "acct", "perpetual", "paper"])),
            ("mode", mode), ("account_version", "0"), ("path", str(path))])
        for index in range(orders):
            db.execute("INSERT INTO portfolio_orders VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("manual-" + str(index), "strategy", "{}", "{}", "OPEN", "1", "0",
                 "1", "USDT", INSTRUMENT.key, "buy"))


def test_paths_require_explicit_distinct_absolute_configuration(tmp_path):
    assert paper_projection_paths({}) == ()
    one = tmp_path / "one.sqlite3"
    assert paper_projection_paths({"PAPER_PROJECTION_DATABASES": json.dumps([str(one)])}) == (one,)
    bad = [
        "", "{", json.dumps([]), json.dumps([str(one), str(one)]),
        json.dumps(["relative.sqlite3"]), json.dumps([str(tmp_path / "*.sqlite3")]),
        json.dumps([str(tmp_path / ("db-" + str(index))) for index in range(17)]),
        json.dumps({"path": str(one)}), json.dumps([1]),
    ]
    for value in bad:
        with pytest.raises(ValueError):
            paper_projection_paths({"PAPER_PROJECTION_DATABASES": value})
    with pytest.raises(ValueError):
        paper_projection_paths({"PAPER_PROJECTION_DATABASES": [str(one)]})


def test_projection_requires_explicit_limit_and_empty_path_bound(tmp_path):
    for limit in (0, 1001, True, "10", None):
        with pytest.raises(ValueError):
            paper_projection((), limit=limit)
    with pytest.raises(ValueError):
        paper_projection([tmp_path / "x"] * 17, limit=1)
    with pytest.raises(ValueError):
        paper_projection((path for path in ()), limit=1)
    empty = paper_projection((), limit=1)
    assert empty["mode"] == "paper" and empty["evidence"] == "PAPER_SIMULATION"
    assert empty["freshness"] == "UNVERIFIED" and empty["fact_asof_ns"] is None
    assert type(empty["read_at_ns"]) is int
    assert all(empty[section] == [] for section in
               ("accounts", "orders", "strategies", "positions", "pnl", "errors"))


def test_cold_thread_read_of_real_ledger_is_bounded_exact_and_untouched(tmp_path):
    root = tmp_path / "owner"
    ledger = open_ledger(root)
    try:
        ledger.seed_balance("USDT", D("100000"), {"fixture": "paper only"})
        matcher = engine(ledger)
        matcher.on_book(book())
        submit(matcher, "alpha-partial", quantity=2, price=99)
        matcher.advance(T + 10)
        matcher.on_trade(trade(quantity=6))
        assert D(ledger.get("alpha-partial")["filled"]) == 1

        ledger.admit_order(intent("beta-short", price=110, quantity=1, side=Side.SELL), "beta",
            {"decision": "beta"}, T + 100, rules(), now_ns=T + 10, reference_price=D(109),
            margin_rate=D(".1"), funding_buffer=D(0), account_notional_limit=D(100000),
            strategy_notional_limit=D(100000), account_capital_limit=D(100000),
            strategy_capital_limit=D(100000))
        ledger.apply_fill("beta-fill", "beta-short", 1, 110, fee=D("0.2"))
        ledger.apply_funding("fund-alpha", INSTRUMENT.key, D("-0.03"), "USDT", {"period": "one"},
                             strategy="strategy")
        ledger.admit_order(intent("gamma-secret", price=98, quantity=1,
            metadata={"api_key": "must-not-leak", "secret": "must-not-leak"}), "gamma",
            {"credential": "must-not-leak"}, T + 100, rules(), now_ns=T + 10,
            reference_price=D(98), margin_rate=D(".1"), funding_buffer=D(0),
            account_notional_limit=D(100000), strategy_notional_limit=D(100000),
            account_capital_limit=D(100000), strategy_capital_limit=D(100000))
        ledger.cancel("alpha-partial")
        assert ledger.get("alpha-partial")["state"] == "UNKNOWN"

        before_version = ledger.account_version()
        before_orders = ledger.report()["orders"]
        before_cash = {(row["strategy"], row["currency"]): row
                       for row in ledger.report()["strategy_cash"]}
        before_dump = list(ledger._db.iterdump())
        result = {}

        def read():
            result["value"] = paper_projection((ledger.path,), limit=100)

        reader = threading.Thread(target=read)
        reader.start()
        reader.join(10)
        assert not reader.is_alive() and "value" in result
        projected = result["value"]
        assert projected["errors"] == []
        assert projected["mode"] == "paper" and projected["evidence"] == "PAPER_SIMULATION"
        assert projected["freshness"] == "UNVERIFIED" and projected["fact_asof_ns"] is None

        assert ledger.account_version() == before_version
        assert ledger.report()["orders"] == before_orders
        assert list(ledger._db.iterdump()) == before_dump

        account = projected["accounts"][0]
        assert account["scope"] == ledger.scope.key and account["account_version"] == before_version
        assert account["mode"] == "paper" and account["recovery"] == "UNRECONCILED"
        assert {row["currency"]: row["amount"] for row in account["balances"]}["USDT"] == \
            ledger.report()["balances"]["USDT"]

        orders = {row["intent_id"]: row for row in projected["orders"]}
        assert orders["alpha-partial"]["status"] == "UNKNOWN"
        assert orders["alpha-partial"]["filled"] == "1" and orders["alpha-partial"]["quantity"] == "2"
        assert orders["alpha-partial"]["recovery"] == "UNRECONCILED"
        assert orders["alpha-partial"]["strategy"] == "strategy"
        assert orders["alpha-partial"]["side"] == "buy"
        assert orders["alpha-partial"]["mode"] == "paper"
        assert orders["alpha-partial"]["evidence"] == "PAPER_SIMULATION"
        assert orders["beta-short"]["strategy"] == "beta"
        assert orders["beta-short"]["recovery"] == "UNVERIFIED"
        for field in ("payload", "rules", "credential"):
            assert field not in orders["alpha-partial"]
        assert "must-not-leak" not in json.dumps(projected)

        strategy_names = {row["strategy"] for row in projected["strategies"]}
        assert {"strategy", "beta", "gamma"} <= strategy_names
        assert all(row["running"] is None and row["status"] == "UNVERIFIED"
                   for row in projected["strategies"])
        assert not any(name.startswith("@") for name in strategy_names)

        positions = {(row["category"], row["strategy"]): row for row in projected["positions"]}
        assert positions[("virtual", "strategy")]["quantity"] == "1"
        assert positions[("virtual", "beta")]["quantity"] == "-1"
        assert positions[("physical", "@physical")]["quantity"] == "0"

        pnl = {(row["strategy"], row["currency"]): row for row in projected["pnl"]}
        assert ("@physical_netting_adjustment", "USDT") in pnl
        for key, row in pnl.items():
            raw = before_cash[key]
            assert row["realized_pnl"] == str(D(raw["realized"]))
            assert row["fees"] == str(D(raw["fees"]))
            assert row["funding"] == str(D(raw["funding"]))
            assert row["unrealized_pnl"] is None and row["mark"] is None and row["mark_asof"] is None
            assert row["unrealized_pnl_status"] == "UNAVAILABLE"
        assert pnl[("strategy", "USDT")]["funding"] == "-0.03"

        os.utime(ledger.path, ns=(time.time_ns(), time.time_ns()))
        touched = paper_projection((ledger.path,), limit=100)
        assert touched["freshness"] == "UNVERIFIED" and touched["fact_asof_ns"] is None
        assert all(row["mark"] is None for row in touched["pnl"])
    finally:
        ledger.close()

    reopened = open_ledger(root)
    try:
        after_restart = paper_projection((reopened.path,), limit=100)
        assert after_restart["freshness"] == "UNVERIFIED"
        assert after_restart["fact_asof_ns"] is None
        assert {row["intent_id"]: row for row in after_restart["orders"]}["alpha-partial"]["status"] == "UNKNOWN"
    finally:
        reopened.close()


def test_source_failures_are_fixed_unavailable_without_echoing_payload(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"this is not a sqlite database")
    nonpaper = tmp_path / "nonpaper.sqlite3"
    manual_db(nonpaper, mode="live")
    ill_schema = tmp_path / "schema.sqlite3"
    manual_db(ill_schema, include_tables=False)
    overflow = tmp_path / "overflow.sqlite3"
    manual_db(overflow, orders=2)

    expectations = [(missing, "missing"), (nonpaper, "nonpaper"), (ill_schema, "schema")]
    for path, reason in expectations:
        result = paper_projection((path,), limit=10)
        assert result["accounts"] == [] and result["orders"] == [] and result["positions"] == []
        assert result["errors"] == [{"source": str(path), "type": "UNAVAILABLE", "reason": reason}]
    corrupt_result = paper_projection((corrupt,), limit=10)
    assert corrupt_result["errors"][0]["type"] == "UNAVAILABLE"
    assert corrupt_result["errors"][0]["reason"] in {"corrupt_or_schema", "unavailable"}
    overflow_result = paper_projection((overflow,), limit=1)
    assert overflow_result["orders"] == [] and overflow_result["strategies"] == []
    assert overflow_result["errors"] == [{"source": str(overflow), "type": "UNAVAILABLE",
                                          "reason": "row_limit"}]

    serialized = json.dumps([corrupt_result, overflow_result]).lower()
    assert "no such table" not in serialized
    assert "not a database" not in serialized
    assert "must-not-leak" not in serialized


def _snapshot_budgets():
    return SnapshotBudgets(65536, 16, 2, .01, 1.0)


def _write_market_snapshot(tmp_path):
    now = time.time_ns()
    instrument = InstrumentId(venue=Venue.KIWOOM, asset_class=AssetClass.ETF,
                              raw_symbol="069500", std_symbol="069500", currency="KRW")
    common = dict(instrument=instrument, venue=Venue.KIWOOM, provider="kiwoom",
                  receive_ts_ns=now, wallclock_ts_ns=now)
    hot = HotPathStore()
    hot.apply(OrderBookEvent(**common, bids=[OrderBookLevel(price="100", quantity="2")],
        asks=[OrderBookLevel(price="101", quantity="3")], depth=1))
    owner = SimpleNamespace(hot_path=hot, stats={"closed": False, "feeds": {
        "fixture": {"provider": "kiwoom", "mode": "LIVE", "reason": "verified",
                    "generation": 1, "applied_sequence": 1}}})
    budgets = _snapshot_budgets()
    document = capture_market_snapshot(owner, budgets, now_ns=now)
    path = tmp_path / "market.json"
    atomic_write_snapshot(path, json.dumps(document).encode())
    return path, budgets


async def _run_local_bridge(tmp_path, monkeypatch, paper_databases):
    market_path, budgets = _write_market_snapshot(tmp_path)
    env = {"VIBETRADING_RUNTIME": "local", "LOCAL_MARKET_SNAPSHOT_FILE": str(market_path),
        "LOCAL_SNAPSHOT_BUDGETS": json.dumps(asdict(budgets)),
        "DASHBOARD_SITE_URL": "https://fixture.invalid", "DASHBOARD_INGEST_TOKEN": "fixture",
        "DASHBOARD_SITE_BYPASS_TOKEN": "fixture",
        "DASHBOARD_DATA_ROOT": str(tmp_path / "data"),
        "DASHBOARD_MONITOR_ROOT": str(tmp_path / "monitor")}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PAPER_PROJECTION_DATABASES", raising=False)
    if paper_databases is not None:
        monkeypatch.setenv("PAPER_PROJECTION_DATABASES",
                           json.dumps([str(path) for path in paper_databases]))

    async def forbidden(*args, **kwargs):
        raise AssertionError("local bridge must not use NATS, public feeds, or venue accounts")

    monkeypatch.setattr("nats.connect", forbidden)
    monkeypatch.setattr(bridge, "observe_public", forbidden)
    monkeypatch.setattr(bridge, "bybit_account", forbidden)
    monkeypatch.setattr(bridge, "MarketProjection",
                        lambda: (_ for _ in ()).throw(AssertionError("no duplicate market state")))
    monkeypatch.setattr(bridge, "public_key", lambda path: {"kty": "fixture"})
    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda *args: None)
    stop, sent = asyncio.Event(), []

    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def json(self): return {"accounts": []}

    class Session:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def post(self, url, *, json, **kwargs):
            sent.append(json)
            stop.set()
            return Response()

    monkeypatch.setattr(bridge.aiohttp, "ClientSession", Session)
    await bridge.run(stop=stop)
    return sent


@pytest.mark.asyncio
async def test_local_bridge_opt_in_appends_paper_facts_without_missing_telemetry(tmp_path, monkeypatch):
    root = tmp_path / "owner"
    ledger = open_ledger(root)
    try:
        ledger.seed_balance("USDT", D("100000"), {"fixture": "paper only"})
        matcher = engine(ledger)
        matcher.on_book(book())
        submit(matcher, "paper-order")
        matcher.advance(T + 10)
        matcher.on_trade(trade(quantity=6))
        sent = await _run_local_bridge(tmp_path, monkeypatch, [ledger.path])
    finally:
        ledger.close()

    assert len(sent) == 1
    snapshot = sent[0]
    assert not any(error["type"] == "StrategyTelemetryNotConfigured" for error in snapshot["errors"])
    assert any(order["intent_id"] == "paper-order" and order["mode"] == "paper" for order in snapshot["orders"])
    paper_strategies = [row for row in snapshot["strategies"] if row["strategy"] == "strategy"]
    assert paper_strategies and all(row["recovery"] != "READY" for row in paper_strategies)
    assert snapshot["markets"] and snapshot["nats_connected"] is False


@pytest.mark.asyncio
async def test_local_bridge_without_config_keeps_explicit_missing_telemetry(tmp_path, monkeypatch):
    sent = await _run_local_bridge(tmp_path, monkeypatch, None)
    assert len(sent) == 1
    assert any(error["type"] == "StrategyTelemetryNotConfigured" for error in sent[0]["errors"])
    assert not any(order.get("mode") == "paper" for order in sent[0]["orders"])
