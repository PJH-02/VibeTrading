from decimal import Decimal as D
import json

import pytest

from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
from execution.execution_ledger import AccountScope
from execution.paper_execution import PaperExecution
from portfolio.portfolio_ledger import PortfolioLedger
from execution.trading_contracts import ProductKind, ProductSpec, TradingMode


T = 1_800_000_000_000_000_000
INSTRUMENT = InstrumentId(venue=Venue.BYBIT, asset_class=AssetClass.CRYPTO_PERP,
                          raw_symbol="BTCUSDT", std_symbol="BTCUSDT")


def open_ledger(root):
    return PortfolioLedger(root / "paper" / "account.sqlite",
        AccountScope("bybit", "paper-a", "perpetual", "paper"), owner_root=root,
        mode=TradingMode.PAPER, max_orders=30, max_fills=100, max_events=200,
        max_document_bytes=500_000, max_db_bytes=8_000_000, max_wal_bytes=8_000_000)


def make_engine(ledger):
    return PaperExecution(ledger, entry_latency_ns=10, cancel_latency_ns=5,
        max_open_orders=8, max_depth=8, max_receipts=100, max_state_bytes=500_000,
        stale_after_ns=1000)


def rules():
    return ProductSpec(INSTRUMENT, ProductKind.LINEAR_PERPETUAL, D(10), D(1), D(1), D(1),
        "USDT", "USDT", "USDT", "net", "isolated", "synthetic fee",
        "explicit funding receipts", "synthetic isolated margin", D(".001"), D(".002"))


def submit(matcher, identity="open", *, quantity=2, now=T):
    intent = OrderIntent(intent_id=identity, job_id="job", signal_id="signal-"+identity,
        destination=OrderDestination.REAL, instrument=INSTRUMENT, side=Side.BUY,
        created_ts_ns=T, client_order_id="client-"+identity, quantity=D(quantity), limit_price=D(99))
    return matcher.submit(intent, "strategy", {"decision": identity}, T+1000, rules(), now,
        reference_price=D(100), margin_rate=D(".1"), funding_buffer=D(0),
        account_notional_limit=D(100000), strategy_notional_limit=D(100000),
        account_capital_limit=D(100000), strategy_capital_limit=D(100000))


def start(root):
    ledger = open_ledger(root)
    if not ledger.report()["balances"]:
        ledger.seed_balance("USDT", D(100000), {"fixture": "paper only"})
    return ledger


def test_startup_converted_open_recovers_after_two_restarts(tmp_path):
    ledger = start(tmp_path)
    matcher = make_engine(ledger)
    submit(matcher)
    assert ledger.get("open")["state"] == "OPEN"
    ledger.close()

    for _ in range(2):
        ledger = open_ledger(tmp_path)
        assert ledger.get("open")["state"] == "UNKNOWN"
        assert ledger._db.execute("SELECT 1 FROM portfolio_metadata WHERE key='paper_startup_recovery'").fetchone()
        ledger.close()

    ledger = open_ledger(tmp_path)
    try:
        matcher = make_engine(ledger)
        matcher.advance(T + 1)
        assert ledger.get("open")["state"] == "UNKNOWN"
        submit(matcher, "next", now=T + 2)
        assert ledger.get("open")["state"] == "OPEN"
        assert ledger.get("next")["state"] == "OPEN"
        marker = ledger._db.execute(
            "SELECT value FROM portfolio_metadata WHERE key='paper_startup_recovery'").fetchone()
        assert json.loads(marker[0]) == {"open": "OPEN", "next": "OPEN"}
        assert D(ledger.get("open")["reservation"]) > 0
    finally:
        ledger.close()


def test_true_unknown_and_explicit_unconfirmed_startup_order_never_recover(tmp_path):
    ledger = start(tmp_path)
    matcher = make_engine(ledger)
    submit(matcher)
    ledger.close()
    ledger = open_ledger(tmp_path)
    assert ledger.get("open")["state"] == "UNKNOWN"
    ledger.cancel("open", confirmed=False)
    assert not ledger._db.execute("SELECT 1 FROM portfolio_metadata WHERE key='paper_startup_recovery'").fetchone()
    ledger.close()

    ledger = open_ledger(tmp_path)
    try:
        matcher = make_engine(ledger)
        matcher.advance(T + 1)
        assert ledger.get("open")["state"] == "UNKNOWN"
        with pytest.raises(RuntimeError, match="Unknown order outcome"):
            submit(matcher, "new-risk", now=T + 2)
    finally:
        ledger.close()


def test_true_unknown_before_restart_has_no_recovery_marker(tmp_path):
    ledger = start(tmp_path)
    matcher = make_engine(ledger)
    submit(matcher)
    ledger.cancel("open", confirmed=False)
    ledger.close()
    ledger = open_ledger(tmp_path)
    try:
        assert ledger.get("open")["state"] == "UNKNOWN"
        assert not ledger._db.execute("SELECT 1 FROM portfolio_metadata WHERE key='paper_startup_recovery'").fetchone()
        matcher = make_engine(ledger)
        matcher.advance(T + 1)
        assert ledger.get("open")["state"] == "UNKNOWN"
    finally:
        ledger.close()


def test_legacy_open_checkpoint_recovers_from_preconversion_memory_only(tmp_path):
    ledger = start(tmp_path)
    matcher = make_engine(ledger)
    submit(matcher)
    # Model a checkpoint written before paper_startup_recovery existed.
    ledger._db.execute("DELETE FROM portfolio_metadata WHERE key='paper_startup_recovery'")
    ledger.close()

    ledger = open_ledger(tmp_path)
    try:
        assert ledger.get("open")["state"] == "UNKNOWN"
        assert not ledger._db.execute(
            "SELECT 1 FROM portfolio_metadata WHERE key='paper_startup_recovery'").fetchone()
        matcher = make_engine(ledger)
        matcher.advance(T + 1)
        assert ledger.get("open")["state"] == "UNKNOWN"
        submit(matcher, "next", now=T + 2)
        assert ledger.get("open")["state"] == "OPEN"
        # The first successful transition upgrades recovery evidence durably.
        marker = ledger._db.execute(
            "SELECT value FROM portfolio_metadata WHERE key='paper_startup_recovery'").fetchone()
        assert json.loads(marker[0]) == {"open": "OPEN", "next": "OPEN"}
    finally:
        ledger.close()


def test_legacy_recovery_proof_expires_after_another_restart(tmp_path):
    ledger = start(tmp_path)
    matcher = make_engine(ledger)
    submit(matcher)
    ledger._db.execute("DELETE FROM portfolio_metadata WHERE key='paper_startup_recovery'")
    ledger.close()

    # First open captures bounded process-local proof, but does not start a matcher.
    ledger = open_ledger(tmp_path)
    assert ledger.get("open")["state"] == "UNKNOWN"
    ledger.close()
    ledger = open_ledger(tmp_path)
    try:
        matcher = make_engine(ledger)
        matcher.advance(T + 1)
        assert ledger.get("open")["state"] == "UNKNOWN"
        with pytest.raises(RuntimeError, match="Unknown order outcome"):
            submit(matcher, "new-risk", now=T + 2)
    finally:
        ledger.close()


def test_mismatched_saved_checkpoint_keeps_unknown_and_marker_atomically(tmp_path):
    ledger = start(tmp_path)
    matcher = make_engine(ledger)
    submit(matcher)
    ledger.close()
    ledger = open_ledger(tmp_path)
    matcher = make_engine(ledger)
    saved = ledger.paper_state()
    changed = dict(saved)
    changed["clock"] += 1
    ledger.save_paper_state(changed)
    with pytest.raises(ValueError, match="checkpoint differs"):
        submit(matcher, "next", now=T + 1)
    assert ledger.get("open")["state"] == "UNKNOWN"
    assert ledger._db.execute("SELECT 1 FROM portfolio_metadata WHERE key='paper_startup_recovery'").fetchone()
    assert ledger.paper_state() == changed
    ledger.close()
