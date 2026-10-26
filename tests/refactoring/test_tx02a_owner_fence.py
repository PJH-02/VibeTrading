"""TX02A: Paper reuses the physical account fence without changing economics."""
from dataclasses import replace
from decimal import Decimal
import hashlib
import sqlite3
import subprocess
import sys
import threading

import pytest

from vibetrading.execution_ledger import AccountScope, ExecutionLedger
from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
from vibetrading.portfolio_ledger import PortfolioLedger
from vibetrading.process_lease import ProcessLease
from vibetrading.trading_contracts import ProductKind, ProductSpec, TradingMode


D = Decimal
SCOPE = AccountScope("binance", "acct", "trading", "paper")


def portfolio(root, name="portfolio.sqlite", scope=SCOPE, mode=TradingMode.PAPER):
    return PortfolioLedger(
        root / mode.value / name, scope, owner_root=root, mode=mode,
        max_orders=30, max_fills=30, max_events=30,
        max_document_bytes=100_000, max_db_bytes=4_000_000, max_wal_bytes=8_000_000)


def execution(root, name="execution.sqlite", scope=SCOPE):
    return ExecutionLedger(
        root / name, scope, authority_root=root, max_intents=8,
        max_evidence_bytes=4096, max_fills=8)


def scope_lease_path(root, scope):
    return root / ".portfolio-owners" / (hashlib.sha256(scope.key.encode()).hexdigest() + ".lock")


def spec(kind=ProductKind.CASH):
    return ProductSpec(
        instrument=InstrumentId(
            venue=Venue.BINANCE,
            asset_class=AssetClass.CRYPTO_SPOT if kind == ProductKind.CASH else AssetClass.CRYPTO_PERP,
            raw_symbol="BTCUSDT", std_symbol="BTCUSDT"),
        kind=kind, multiplier=D("1"), tick_size=D("0.1"), lot_size=D("0.1"), min_notional=D("1"),
        quote_currency="USDT", settlement_currency="USDT", collateral_currency="USDT",
        position_mode="net", margin_mode="cash" if kind == ProductKind.CASH else "isolated",
        fee_provenance="explicit test fee", funding_provenance="explicit test funding",
        margin_model_provenance="explicit simulated assumption",
        maker_fee_rate=D("-0.001"), taker_fee_rate=D("0.002"))


def order(rules=None, identity="one", price="100"):
    rules = rules or spec()
    return OrderIntent(intent_id=identity, job_id="job", signal_id="signal",
                       destination=OrderDestination.REAL, instrument=rules.instrument,
                       side=Side.BUY, quantity=D("1"), limit_price=D(price),
                       client_order_id="client-" + identity, created_ts_ns=1, metadata={})


def admit(book, intent=None, *, rules=None):
    rules = rules or spec()
    return book.admit_order(
        intent or order(rules), "alpha", {"source": "local test"}, 1000, rules,
        funding_buffer=D("0"), account_notional_limit=D("10000"),
        strategy_notional_limit=D("5000"), account_capital_limit=D("10000"),
        strategy_capital_limit=D("5000"), now_ns=1)


def test_physical_paper_authority_ignores_product_and_db_labels(tmp_path):
    first = portfolio(tmp_path, "one.sqlite")
    other_scope = replace(SCOPE, product="cash")
    try:
        with pytest.raises(RuntimeError, match="another process"):
            portfolio(tmp_path, "two.sqlite", other_scope)
        assert not (tmp_path / "paper" / "two.sqlite").exists()
        assert not scope_lease_path(tmp_path, other_scope).with_suffix(".binding").exists()
    finally:
        first.close()
    portfolio(tmp_path, "two.sqlite", other_scope).close()


def test_portfolio_and_execution_ledger_share_paper_physical_fence(tmp_path):
    book = portfolio(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="another process"):
            execution(tmp_path, scope=replace(SCOPE, product="spot"))
    finally:
        book.close()

    journal = execution(tmp_path, scope=replace(SCOPE, product="spot"))
    try:
        with pytest.raises(RuntimeError, match="another process"):
            portfolio(tmp_path, "other.sqlite", replace(SCOPE, product="cash"))
    finally:
        journal.close()
    portfolio(tmp_path, "other.sqlite", replace(SCOPE, product="cash")).close()


def test_distinct_accounts_and_environments_do_not_share_a_global_lock(tmp_path):
    paper = portfolio(tmp_path, "paper.sqlite")
    sandbox_scope = replace(SCOPE, environment="sandbox")
    sandbox = portfolio(tmp_path, "sandbox.sqlite", sandbox_scope, TradingMode.SANDBOX)
    other = portfolio(tmp_path, "other.sqlite", replace(SCOPE, account="other-acct"))
    offline = execution(tmp_path, "offline.sqlite",
                        replace(SCOPE, environment="offline", product="spot"))
    try:
        assert paper._authority is not None
        assert sandbox._authority is None
        assert paper.paper_state() is None
    finally:
        for item in (offline, other, sandbox, paper):
            item.close()


@pytest.mark.parametrize("scope", [
    AccountScope("kraken", "acct", "trading", "paper"),
    AccountScope("binance", " acct", "trading", "paper"),
])
def test_paper_authority_rejects_aliased_physical_identity(tmp_path, scope):
    with pytest.raises(ValueError):
        portfolio(tmp_path, scope=scope)
    assert not (tmp_path / ".portfolio-owners").exists()


def test_scope_lease_failure_releases_new_physical_authority(tmp_path):
    held = ProcessLease(scope_lease_path(tmp_path, SCOPE))
    held.acquire()
    try:
        with pytest.raises(RuntimeError, match="another process"):
            portfolio(tmp_path)
    finally:
        held.close()
    assert not (tmp_path / "paper" / "portfolio.sqlite").exists()
    portfolio(tmp_path).close()


def test_db_connect_failure_releases_authority_and_allows_retry(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect",
                      lambda *a, **k: (_ for _ in ()).throw(OSError("disk unavailable")))
        with pytest.raises(OSError, match="disk unavailable"):
            portfolio(tmp_path)
    assert not (tmp_path / "paper" / "portfolio.sqlite").exists()
    portfolio(tmp_path).close()


def test_binding_prevents_relocating_bound_scope_after_close(tmp_path):
    portfolio(tmp_path, "one.sqlite").close()
    with pytest.raises(ValueError, match="bound"):
        portfolio(tmp_path, "two.sqlite")
    portfolio(tmp_path, "one.sqlite").close()


def test_restart_reads_latest_balances_orders_and_unknown_state(tmp_path):
    book = portfolio(tmp_path)
    book.seed_balance("USDT", D("1000"), {"simulation": True})
    created, row = admit(book)
    assert created and row["state"] == "OPEN"
    book.close()

    reopened = portfolio(tmp_path)
    try:
        assert D(reopened.report()["balances"]["USDT"]) == D("1000")
        assert reopened.get("one")["state"] == "UNKNOWN"
        assert D(reopened.get("one")["quantity"]) == D("1")
    finally:
        reopened.close()


@pytest.mark.parametrize("attribute", ["_lease", "_authority"])
def test_lost_lease_blocks_checked_api_and_cleanup_survives(tmp_path, attribute):
    book = portfolio(tmp_path)
    lease = getattr(book, attribute)
    moved = lease.path.with_name(lease.path.name + ".moved")
    lease.path.rename(moved)
    with pytest.raises(RuntimeError, match="identity was lost"):
        book.paper_state()
    moved.rename(lease.path)
    with pytest.raises(RuntimeError, match="identity was lost"):
        book.paper_state()
    with pytest.raises(RuntimeError, match="identity was lost"):
        with book.transaction():
            pass
    book.close()
    portfolio(tmp_path).close()


@pytest.mark.parametrize("attribute", ["_lease", "_authority"])
def test_closed_lease_blocks_checked_api_and_cleanup_survives(tmp_path, attribute):
    book = portfolio(tmp_path)
    getattr(book, attribute).close()
    with pytest.raises(RuntimeError):
        book.paper_state()
    book.close()
    portfolio(tmp_path).close()


def test_lost_authority_before_outer_commit_rolls_back_pending_data(tmp_path):
    book = portfolio(tmp_path)
    with pytest.raises(RuntimeError, match="identity was lost"):
        with book.transaction():
            book.save_paper_state({"pending": True})
            book._authority.path.unlink()
    book.close()
    reopened = portfolio(tmp_path)
    try:
        assert reopened.paper_state() is None
    finally:
        reopened.close()


def test_lost_authority_before_savepoint_release_rolls_back_nested_data(tmp_path):
    book = portfolio(tmp_path)
    with pytest.raises(RuntimeError, match="identity was lost"):
        with book.transaction():
            with pytest.raises(RuntimeError, match="identity was lost"):
                with book.transaction():
                    book.save_paper_state({"nested": True})
                    book._authority.path.unlink()
            assert book._db.execute(
                "SELECT value FROM portfolio_metadata WHERE key='matcher'").fetchone() is None
    book.close()
    reopened = portfolio(tmp_path)
    try:
        assert reopened.paper_state() is None
    finally:
        reopened.close()


def test_backend_close_failure_retains_both_leases_until_retry(tmp_path):
    book = portfolio(tmp_path)
    database = book._db

    class FailingClose:
        def close(self):
            raise OSError("close failed")

    book._db = FailingClose()
    with pytest.raises(OSError, match="close failed"):
        book.close()
    with pytest.raises(RuntimeError, match="another process"):
        execution(tmp_path, scope=replace(SCOPE, product="spot"))
    probe = ProcessLease(scope_lease_path(tmp_path, SCOPE))
    with pytest.raises(RuntimeError, match="another process"):
        probe.acquire()

    book._db = database
    book.close()
    execution(tmp_path, scope=replace(SCOPE, product="spot")).close()
    probe = ProcessLease(scope_lease_path(tmp_path, SCOPE))
    probe.acquire()
    probe.close()
    portfolio(tmp_path).close()


def test_close_refuses_foreign_thread_before_touching_db_or_lease(tmp_path):
    book = portfolio(tmp_path)
    failures = []

    def foreign_close():
        try:
            book.close()
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=foreign_close)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    assert failures and "owning thread" in str(failures[0])
    assert book.paper_state() is None
    with pytest.raises(RuntimeError, match="another process"):
        portfolio(tmp_path, "blocked.sqlite")
    book.close()
    portfolio(tmp_path).close()


def test_fork_child_cannot_use_or_unlock_parent_portfolio(tmp_path):
    script = """
import os, sys
from pathlib import Path
from vibetrading.execution_ledger import AccountScope
from vibetrading.portfolio_ledger import PortfolioLedger

root = Path(sys.argv[1])
options = dict(owner_root=root, mode='paper', max_orders=30, max_fills=30, max_events=30,
               max_document_bytes=100000, max_db_bytes=4000000, max_wal_bytes=8000000)
scope = AccountScope('binance', 'acct', 'trading', 'paper')
book = PortfolioLedger(root / 'paper' / 'fork.sqlite', scope, **options)
pid = os.fork()
if pid == 0:
    try:
        book.close()
    except RuntimeError:
        pass
    else:
        os._exit(1)
    try:
        book.paper_state()
    except RuntimeError:
        pass
    else:
        os._exit(2)
    os._exit(0)
assert os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) == 0
try:
    PortfolioLedger(root / 'paper' / 'other.sqlite', scope, **options)
except RuntimeError:
    pass
else:
    raise AssertionError('child released the parent physical authority')
book.close()
PortfolioLedger(root / 'paper' / 'fork.sqlite', scope, **options).close()
"""
    subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=True, timeout=15)
