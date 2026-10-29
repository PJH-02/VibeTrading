"""Independent exact-value checks for the read-only dashboard PnL mapper."""
from decimal import Decimal
import json
import sqlite3
from pathlib import Path
import pytest

from operations.dashboard.pnl import (native_ledger_pnl_projection, paper_pnl_projection,
                                      projection_id, public_scope, etf_journal_pnl_projection)


@pytest.fixture(autouse=True)
def isolate_runtime_latency_registry():
    from operations.dashboard import latency
    latency._fresh_process_state()
    yield
    latency._fresh_process_state()


def paper_document(*, gap=False):
    scope = '["kis","shared-account","cash","paper"]'
    return {
        "accounts": [{"scope": scope, "gap_count": int(gap),
                      "balances": [{"currency": "KRW", "amount": "500000"},
                                   {"currency": "USDT", "amount": "1000"}]}],
        "pnl": [
            {"strategy": "alpha", "account": scope, "currency": "KRW", "mode": "paper",
             "realized_pnl": "1000", "fees": "100", "funding": "25",
             "recovery": "UNVERIFIED"},
            {"strategy": "beta", "account": scope, "currency": "USDT", "mode": "paper",
             "realized_pnl": "10.50", "fees": "0.25", "funding": "-0.10",
             "recovery": "UNVERIFIED"},
        ],
        "positions": [
            {"strategy": "alpha", "account": scope, "quantity": "0", "instrument": "kis:equity:005930",
             "currency": "KRW", "settlement_currency": "KRW", "quote_currency": "KRW", "multiplier": "1"},
            {"strategy": "beta", "account": scope, "quantity": "2", "instrument": "bybit:crypto_perp:BTCUSDT",
             "currency": "USDT", "settlement_currency": "USDT", "quote_currency": "USDT", "multiplier": "1", "average": "100"},
        ],
        "orders": [
            {"strategy": "alpha", "account": scope, "status": "FILLED", "filled": "2"},
            {"strategy": "beta", "account": scope, "status": "CANCEL_PENDING", "filled": "1.5"},
        ],
        "fills": [
            {"strategy": "alpha", "account": scope, "currency": "KRW"},
            {"strategy": "beta", "account": scope, "currency": "USDT"},
            {"strategy": "beta", "account": scope, "currency": "USDT"},
        ],
    }


def test_two_strategies_shared_account_exact_net_and_unknown_mark():
    # Independent hand calculation: alpha 1000 + 0 - 100 + 25 = 925 KRW.
    # Beta has an open position: 10.50 - 0.25 - 0.10 = 10.15 USDT before
    # unrealized PnL; because no versioned mark is supplied, net must be null.
    rows = paper_pnl_projection(paper_document(), generated_ms=1_800_000_000_000)
    alpha = next(row for row in rows if row["strategy_id"] == "alpha")
    beta = next(row for row in rows if row["strategy_id"] == "beta")
    assert Decimal(alpha["net_pnl"]) == Decimal("925")
    assert alpha["realized_pnl"] == "1000"
    assert alpha["fees"] == "100" and alpha["funding"] == "25"
    assert alpha["position_count"] == 0 and alpha["trades"] == 1
    assert Decimal(beta["realized_pnl"]) - Decimal(beta["fees"]) + Decimal(beta["funding"]) == Decimal("10.15")
    assert beta["net_pnl"] is None
    assert beta["unrealized_pnl"] is None and beta["mark_provenance"] == "MISSING_TRUSTED_MARK"
    assert beta["working_order_count"] == 1 and beta["trades"] == 2
    assert all(row["account_scope"] == public_scope('["kis","shared-account","cash","paper"]') for row in rows)
    assert "shared-account" not in str(rows)


def test_unallocated_account_gap_suppresses_strategy_net_and_emits_bucket():
    rows = paper_pnl_projection(paper_document(gap=True))
    strategies = [row for row in rows if row["strategy_id"] != "UNALLOCATED"]
    assert len(strategies) == 2
    assert all(row["attribution_status"] == "INCOMPLETE" and row["net_pnl"] is None for row in strategies)
    buckets = [row for row in rows if row["strategy_id"] == "UNALLOCATED"]
    assert {row["currency"] for row in buckets} == {"KRW", "USDT"}
    assert all(row["attribution_status"] == "UNALLOCATED" and row["net_pnl"] is None for row in buckets)


def test_trusted_mark_completes_strategy_unrealized_and_net_exactly():
    doc = paper_document()
    scope = doc["accounts"][0]["scope"]
    rows = paper_pnl_projection(doc, generated_ms=10000, max_mark_age_ms=5000, marks=[{
        "key": "bybit:crypto_perp:BTCUSDT", "account_scope": scope, "currency": "USDT",
        "price": "110", "last_event_ms": 9500, "provider": "bybit-public-feed",
    }])
    beta = next(row for row in rows if row["strategy_id"] == "beta")
    # 2 × (110 mark - 100 average) = 20 unrealized; plus 10.50 - 0.25 - 0.10.
    assert Decimal(beta["unrealized_pnl"]) == Decimal("20")
    assert Decimal(beta["net_pnl"]) == Decimal("30.15")
    assert beta["status"] == "AVAILABLE"
    assert beta["mark_asof_ms"] == 9500 and beta["mark_provenance"] == "LOCAL_VALIDATED_MARKET_SNAPSHOT"

    stale = paper_pnl_projection(doc, generated_ms=20000, max_mark_age_ms=5000, marks=[{
        "key": "bybit:crypto_perp:BTCUSDT", "account_scope": scope, "currency": "USDT",
        "price": "110", "last_event_ms": 9500, "provider": "bybit-public-feed",
    }])
    beta_stale = next(row for row in stale if row["strategy_id"] == "beta")
    assert beta_stale["unrealized_pnl"] is None and beta_stale["net_pnl"] is None


def test_unallocated_cash_fee_or_funding_suppresses_affected_strategy_net():
    doc = paper_document()
    scope = doc["accounts"][0]["scope"]
    doc["pnl"].append({"strategy": "@unallocated_funding", "account": scope,
                       "currency": "KRW", "mode": "paper", "realized_pnl": "0",
                       "fees": "3", "funding": "-5", "recovery": "UNVERIFIED", "amount": "-8"})
    rows = paper_pnl_projection(doc)
    alpha = next(row for row in rows if row["strategy_id"] == "alpha")
    unallocated = next(row for row in rows if row["strategy_id"] == "UNALLOCATED" and row["currency"] == "KRW")
    assert alpha["net_pnl"] is None and alpha["attribution_status"] == "INCOMPLETE"
    assert Decimal(unallocated["fees"]) == Decimal("3")
    assert Decimal(unallocated["funding"]) == Decimal("-5")
    assert Decimal(unallocated["unallocated_amount"]) == Decimal("-8")


def test_gap_bucket_merges_known_unallocated_cash_amount_and_fees():
    doc = paper_document(gap=True)
    scope = doc["accounts"][0]["scope"]
    doc["pnl"].append({"strategy": "@unallocated_funding", "account": scope,
                       "currency": "KRW", "mode": "paper", "realized_pnl": "0",
                       "fees": "1.125", "funding": "0", "amount": "-1.125"})
    rows = paper_pnl_projection(doc)
    bucket = next(row for row in rows if row["strategy_id"] == "UNALLOCATED" and row["currency"] == "KRW")
    assert bucket["fees"] == "1.125" and bucket["unallocated_amount"] == "-1.125"
    assert bucket["attribution_status"] == "UNALLOCATED" and bucket["net_pnl"] is None


def test_projection_keys_split_currency_scope_and_mode_without_exposing_scope():
    scope = public_scope("account-secret-123")
    assert len(scope) == 24
    assert scope == public_scope("account-secret-123")
    assert projection_id("alpha", scope, "paper", "KRW") != projection_id("alpha", scope, "live", "KRW")
    assert projection_id("alpha", scope, "paper", "KRW") != projection_id("alpha", scope, "paper", "USDT")
    assert projection_id("alpha", scope, "paper", "KRW") != projection_id("beta", scope, "paper", "KRW")


def test_missing_currency_and_invalid_curve_bound_fail_closed():
    doc = paper_document()
    doc["pnl"].append({"strategy": "missing", "account": doc["accounts"][0]["scope"],
                       "currency": None, "mode": "paper", "realized_pnl": "1",
                       "fees": "0", "funding": "0"})
    assert {row["strategy_id"] for row in paper_pnl_projection(doc)} == {"alpha", "beta"}
    try:
        paper_pnl_projection(doc, curve_limit=1441)
    except ValueError as exc:
        assert "1..1440" in str(exc)
    else:
        raise AssertionError("unbounded history limit accepted")


def test_actual_kis_etf_journal_receipts_project_positive_marked_net(tmp_path):
    # Build only local MOCK receipts through the existing ETF journal API.
    # Independent arithmetic: buys 2@1000 and 2@1100, sell 2@1150 => gross
    # realized 200, remaining 2 at average 1050; mark 1200 => unrealized 300.
    # Configured mock fee model: 2 + 2.2 + 2.3 = 6.5; net = 493.5 KRW.
    from portfolio.etf_journal import EtfJournal

    path = tmp_path / "kis-etf-mock.sqlite3"
    journal = EtfJournal(path)
    journal.enable_order_feedback("C3")
    journal.bind_order_feedback_scope("mock", "acct-secret")
    journal.db.execute("INSERT INTO books(strategy,cash) VALUES('C3','100000')")
    for index, (side, quantity, cumulative_filled, cumulative_notional, status) in enumerate((
        ("buy", 2, 2, "2000", "filled"),
        ("buy", 2, 2, "2200", "filled"),
        ("sell", 2, 2, "2300", "filled"),
    )):
        intent_id = f"fixture-{index}"
        journal.db.execute("""INSERT INTO orders(
            intent_id,strategy,symbol,side,quantity,price,reserve,status,order_number,
            organization,day,filled,notional,created_ns,payload,cancel_requested,account_scope)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            intent_id, "C3", "005930", side, quantity, str(cumulative_notional), "0",
            status, f"order-{index}", "org", "20261030", cumulative_filled,
            cumulative_notional, index + 1, "{}", 0, "mock"))
        row = journal.db.execute("SELECT * FROM orders WHERE intent_id=?", (intent_id,)).fetchone()
        with journal.transaction():
            journal._record_order_feedback_tx(row, status="FILLED", prior_filled=0,
                prior_notional=Decimal(0), observation_ns=index + 10)
    before = path.read_bytes()
    rows = etf_journal_pnl_projection([{
        "path": str(path), "scope": ["kis", "acct-secret", "cash", "mock"],
        "currency": "KRW", "fee_rate": "0.001", "source": "explicit mock fixture",
        "instruments": {"005930": "kiwoom:etf:005930"},
    }], generated_ms=1000, marks=[{
        "key": "kiwoom:etf:005930", "account_scope": '["kis","acct-secret","cash","mock"]',
        "currency": "KRW", "price": "1200", "last_event_ms": 900,
        "provider": "fixture-market-snapshot",
    }])
    assert path.read_bytes() == before
    row = next(item for item in rows if item["strategy_id"] == "C3")
    assert row["trades"] == 3 and row["position_count"] == 1
    assert Decimal(row["realized_pnl"]) == Decimal("200")
    assert Decimal(row["unrealized_pnl"]) == Decimal("300")
    assert Decimal(row["fees"]) == Decimal("6.5")
    assert Decimal(row["net_pnl"]) == Decimal("493.5")
    assert row["currency"] == "KRW" and row["evidence"] == "ETF_JOURNAL"
    assert "acct-secret" not in str(rows)
    journal.db.close()


def test_actual_kis_mock2_etf_receipts_cover_c3_kp_kd(tmp_path):
    from portfolio.etf_journal import EtfJournal

    path = tmp_path / "kis-etf-mock2.sqlite3"
    journal = EtfJournal(path)
    journal.bind_order_feedback_scope("mock2", "acct-mock2")
    for index, strategy in enumerate(("C3", "KP", "KD")):
        journal.enable_order_feedback(strategy)
        journal.db.execute("INSERT INTO books(strategy,cash) VALUES(?,?)", (strategy, "100000"))
        intent_id = f"mock2-{strategy}"
        journal.db.execute("""INSERT INTO orders(
            intent_id,strategy,symbol,side,quantity,price,reserve,status,order_number,
            organization,day,filled,notional,created_ns,payload,cancel_requested,account_scope)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            intent_id, strategy, "005930", "buy", 1, "1000", "0", "filled",
            f"mock2-order-{index}", "org", "20261030", 1, "1000", index + 1,
            "{}", 0, "mock2"))
        row = journal.db.execute("SELECT * FROM orders WHERE intent_id=?", (intent_id,)).fetchone()
        with journal.transaction():
            journal._record_order_feedback_tx(row, status="FILLED", prior_filled=0,
                prior_notional=Decimal(0), observation_ns=index + 20)
    rows = etf_journal_pnl_projection([{
        "path": str(path), "scope": ["kis", "acct-mock2", "cash", "mock2"],
        "currency": "KRW", "fee_rate": "0.001", "source": "explicit mock2 model",
        "instruments": {"005930": "kiwoom:etf:005930"},
    }], generated_ms=1000, marks=[{
        "key": "kiwoom:etf:005930", "account_scope": '["kis","acct-mock2","cash","mock2"]',
        "currency": "KRW", "price": "1100", "last_event_ms": 900,
        "provider": "fixture-market-snapshot",
    }])
    assert {row["strategy_id"] for row in rows} == {"C3", "KP", "KD"}
    assert all(row["mode"] == "mock2" and row["status"] == "AVAILABLE" for row in rows)
    assert all(Decimal(row["net_pnl"]) == Decimal("99") for row in rows)
    journal.db.close()


def test_kis_etf_order_with_missing_fill_receipt_is_incomplete(tmp_path):
    from portfolio.etf_journal import EtfJournal

    path = tmp_path / "kis-etf-missing-fill.sqlite3"
    journal = EtfJournal(path)
    journal.enable_order_feedback("C3")
    journal.bind_order_feedback_scope("mock", "acct-gap")
    journal.db.execute("INSERT INTO books(strategy,cash) VALUES('C3','100000')")
    journal.db.execute("""INSERT INTO orders(
        intent_id,strategy,symbol,side,quantity,price,reserve,status,order_number,
        organization,day,filled,notional,created_ns,payload,cancel_requested,account_scope)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        "missing-fill", "C3", "005930", "buy", 2, "1000", "0", "filled",
        "missing-fill-order", "org", "20261030", 2, "2000", 1, "{}", 0, "mock"))
    row = journal.db.execute("SELECT * FROM orders WHERE intent_id='missing-fill'").fetchone()
    with journal.transaction():
        journal._record_order_feedback_tx(row, status="FILLED", prior_filled=1,
            prior_notional=Decimal("1000"), observation_ns=10)
    rows = etf_journal_pnl_projection([{
        "path": str(path), "scope": ["kis", "acct-gap", "cash", "mock"],
        "currency": "KRW", "fee_rate": "0.001", "source": "explicit mock model",
        "instruments": {"005930": "kiwoom:etf:005930"},
    }], generated_ms=1000, marks=[{
        "key": "kiwoom:etf:005930", "account_scope": '["kis","acct-gap","cash","mock"]',
        "currency": "KRW", "price": "1100", "last_event_ms": 900,
        "provider": "fixture-market-snapshot",
    }])
    row = next(item for item in rows if item["strategy_id"] == "C3")
    assert row["trades"] == 1 and row["net_pnl"] is None
    assert row["status"] == "INCOMPLETE" and row["attribution_status"] == "INCOMPLETE"
    journal.db.close()


def test_frozen_latency_module_emits_nonempty_allowlisted_lifecycle_and_windows():
    import time
    from operations.dashboard.latency import observe, observe_strategy, snapshot

    now = time.monotonic_ns()
    assert observe("dashboard_projection", started_ns=now - 2_000_000,
                   ended_ns=now, source_id="fixture", strategy_id="alpha",
                   account_scope='["bybit","acct","perpetual","testnet"]',
                   mode="testnet", trace_id="trace-fixture", queue_depth=2,
                   oldest_queue_age_ns=4_000_000, source_age_ns=3_000_000)
    assert observe_strategy("alpha", '["bybit","acct","perpetual","testnet"]',
                            "testnet", "RUNNING", currency="USDT")
    emitted = snapshot()
    assert emitted["schema_version"] == 1 and emitted["kind"] == "runtime_latency"
    assert emitted["metrics"] and emitted["strategies"]
    metric = next(item for item in emitted["metrics"] if item["stage"] == "dashboard_projection")
    assert metric["mode"] == "sandbox" and metric["windows"]["1m"]["n"] >= 1
    assert {"seen", "sampled", "sample_out", "last_observation_age_ms", "stale"} <= set(metric)
    assert {"p50_ms", "p95_ms", "p99_ms", "n", "coverage_ms"} <= set(metric["windows"]["1m"])
    assert {"process_cpu_seconds", "rss_bytes", "export_errors", "export_write_errors"} <= set(emitted["resources"])
    lifecycle = next(item for item in emitted["strategies"] if item["strategy_id"] == "alpha")
    assert lifecycle["mode"] == "sandbox" and lifecycle["currency"] == "USDT"


def native_fixture(path, *, scope, currency, venue, asset_class):
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE owner_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE execution_intents(intent_id TEXT PRIMARY KEY,strategy_id TEXT,state TEXT,
                filled TEXT,fees TEXT,payload TEXT,reserve_currency TEXT,updated_ns INTEGER,
                contract_multiplier TEXT);
            CREATE TABLE execution_fills(fill_id TEXT PRIMARY KEY,intent_id TEXT,fingerprint TEXT);
            CREATE TABLE execution_group_facts(seq INTEGER PRIMARY KEY AUTOINCREMENT,group_id TEXT,
                fact_id TEXT,kind TEXT,document TEXT,sha256 TEXT,created_ns INTEGER,
                UNIQUE(group_id,fact_id));
        """)
        db.execute("INSERT INTO owner_metadata VALUES('scope',?)", (scope,))
        db.execute("INSERT INTO execution_intents VALUES(?,?,?,?,?,?,?,?,?)", (
            "intent-a", "alpha", "FILLED", "2", "0.2",
            json.dumps({"instrument": {"venue": venue, "asset_class": asset_class,
                                         "currency": currency, "std_symbol": "BTCUSDT"}, "side": "buy"}),
            "USDT", 2, "1"))
        db.execute("INSERT INTO execution_fills VALUES(?,?,?)", ("fill-hash-a", "intent-a", "receipt-hash-a"))
        db.execute("UPDATE execution_fills SET fingerprint=? WHERE fill_id='fill-hash-a'",
                   (json.dumps(["intent-a", "2", "100", "0.2"], separators=(",", ":")),))
        db.execute("INSERT INTO execution_intents VALUES(?,?,?,?,?,?,?,?,?)", (
            "intent-b", "alpha", "CANCEL_PENDING", "2", "0.22",
            json.dumps({"instrument": {"venue": venue, "asset_class": asset_class,
                                         "currency": currency, "std_symbol": "BTCUSDT"}, "side": "sell"}),
            "USDT", 1, "1"))
        db.execute("INSERT INTO execution_fills VALUES(?,?,?)", ("fill-hash-b", "intent-b", "receipt-hash-b"))
        db.execute("UPDATE execution_fills SET fingerprint=? WHERE fill_id='fill-hash-b'",
                   (json.dumps(["intent-b", "2", "110", "0.22"], separators=(",", ":")),))


def test_native_execution_receipts_are_read_only_and_do_not_claim_missing_pnl(tmp_path):
    bybit = tmp_path / "bybit.sqlite3"
    arcus = tmp_path / "arcus.sqlite3"
    bybit_scope = ["bybit", "account-secret", "perpetual", "live"]
    native_fixture(bybit, scope=json.dumps(bybit_scope, separators=(",", ":")),
                   currency="USDT", venue="bybit", asset_class="crypto_perp")
    scope_key = json.dumps(bybit_scope, separators=(",", ":"))
    funding_fact = {"receipt_id": "fund-1", "scope_key": scope_key,
                    "native_account_id": "account-secret", "source": "native_funding_receipt",
                    "currency": "USDT", "amount": "-0.1", "allocations": {"group-alpha": "-0.1"},
                    "unallocated": "0"}
    with sqlite3.connect(bybit) as db:
        db.execute("INSERT INTO execution_group_facts(group_id,fact_id,kind,document,sha256,created_ns) VALUES(?,?,?,?,?,?)",
                   ("group-alpha", "plan", "plan", json.dumps({"spec": {"strategy_id": "alpha"}}), "plan-hash", 1))
        # Source and anchor mirror share one immutable receipt identity.
        for group_id in ("group-alpha", "anchor"):
            db.execute("INSERT INTO execution_group_facts(group_id,fact_id,kind,document,sha256,created_ns) VALUES(?,?,?,?,?,?)",
                       (group_id, "funding-receipt-1", "funding-receipt", json.dumps(funding_fact), "fund-hash", 2))
        db.execute("INSERT INTO execution_intents VALUES(?,?,?,?,?,?,?,?,?)", (
            "intent-c", "alpha", "FILLED", "1", "0.1",
            json.dumps({"instrument": {"venue": "bybit", "asset_class": "crypto_perp",
                                         "currency": "USDT", "std_symbol": "BTCUSDT"}, "side": "buy"}),
            "USDT", 3, "1"))
        db.execute("INSERT INTO execution_fills VALUES(?,?,?)",
                   ("fill-hash-c", "intent-c", json.dumps(["intent-c", "1", "100", "0.1"], separators=(",", ":"))))
    # This persisted Arcus payload does not declare currency; BTCUSDT symbol
    # text must not cause the projector to guess USDT.
    native_fixture(arcus, scope=json.dumps(["arcus", "another-secret", "perpetual", "testnet"], separators=(",", ":")),
                   currency=None, venue="arcus", asset_class="crypto_perp")
    before = (bybit.read_bytes(), arcus.read_bytes())

    products = [{"scope": bybit_scope, "instrument": "bybit:crypto_perp:BTCUSDT",
                 "currency": "USDT", "fee_currency": "USDT", "settlement_currency": "USDT",
                 "multiplier": "1", "funding_applicable": True, "funding_complete": True,
                 "source": "explicit native product fixture"}]
    native_scope_key = json.dumps(bybit_scope, separators=(",", ":"))
    rows = native_ledger_pnl_projection([bybit, arcus], product_metadata=products, marks=[{
        "account_scope": native_scope_key, "key": "bybit:crypto_perp:BTCUSDT",
        "currency": "USDT", "price": "110", "last_event_ms": 122,
        "provider": "bybit-public-feed",
    }], generated_ms=123)

    assert (bybit.read_bytes(), arcus.read_bytes()) == before
    assert len(rows) == 2
    row = next(item for item in rows if item["mode"] == "live")
    arcus_row = next(item for item in rows if item["mode"] == "sandbox")
    assert row["strategy_id"] == "alpha" and row["currency"] == "USDT"
    assert arcus_row["currency"] == "UNKNOWN"
    # Independent calculation: buy 2 @ 100, sell 2 @ 110 gives gross 20;
    # a final buy 1 @ 100 leaves 1 @ 100. Trusted mark 110 gives 10 unrealized.
    # Fees .20 + .22 + .10 = .52 and one deduplicated funding receipt of -.10;
    # net = 20 + 10 - .52 - .10 = 29.38.
    assert row["trades"] == 3 and row["working_order_count"] == 1
    assert Decimal(row["realized_pnl"]) == Decimal("20")
    assert Decimal(row["unrealized_pnl"]) == Decimal("10")
    assert Decimal(row["fees"]) == Decimal("0.52") and Decimal(row["funding"]) == Decimal("-0.1")
    assert Decimal(row["net_pnl"]) == Decimal("29.38") and row["position_count"] == 1
    assert row["mark_asof_ms"] == 122 and row["mark_provenance"] == "LOCAL_VALIDATED_MARKET_SNAPSHOT"
    assert row["attribution_status"] == "COMPLETE" and row["status"] == "AVAILABLE"
    assert arcus_row["status"] == "INCOMPLETE" and arcus_row["net_pnl"] is None
    assert "account-secret" not in str(rows) and "another-secret" not in str(rows)
