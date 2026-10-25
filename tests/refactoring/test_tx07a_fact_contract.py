"""TX07A: reuse existing identity/fill facts; do not invent a second lifecycle.

These tests only exercise the already-shipped offline owner, paper portfolio
ledger, and ETF journal.  They pin the current field names/authority facts that
TX08A can consume; they do not add a projection API or a universal state enum.
"""
from decimal import Decimal
import json

import pytest

from vibetrading.etf_journal import EtfJournal
from vibetrading.execution_ledger import AccountScope, ExecutionLedger
from vibetrading.execution_owner import ExecutionBudgets, ExecutionOwner, ExecutionReadiness
from vibetrading.models import (
    AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue,
)
from vibetrading.portfolio_ledger import PortfolioLedger
from vibetrading.runtime_control import RuntimeControl
from vibetrading.trading_contracts import ProductKind, ProductSpec

D = Decimal


# ---------------------------------------------------------------------------
# Existing offline execution owner helpers (same contract as test_execution_owner)
# ---------------------------------------------------------------------------

class FakeAdapter:
    offline_only = True
    execution_handoff_protocol = "offline-permit-v1"

    def __init__(self):
        self.preparing = None
        self.before_permit = None
        self.after_permit = None
        self.connects = 0
        self.mutations = []

    async def connect(self):
        self.connects += 1
        if self.preparing:
            await self.preparing()

    async def submit_order_with_permit(self, order, permit):
        if self.before_permit:
            await self.before_permit()
        permit()
        self.mutations.append(order.client_order_id)
        if self.after_permit:
            await self.after_permit()
        return {"status": "ACCEPTED", "order_id": "fake-" + order.client_order_id}


def execution_intent(identity="one", quantity="2", **updates):
    base = OrderIntent(
        intent_id=identity, job_id="strategy", signal_id="signal-" + identity,
        destination=OrderDestination.REAL, client_order_id="client-" + identity,
        instrument=InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_SPOT,
                                raw_symbol="BTCUSDT", std_symbol="BTCUSDT"),
        side=Side.BUY, quantity=Decimal(quantity), limit_price=Decimal("10"),
        metadata={"account_id": "synthetic-account", "environment": "offline"})
    return base.model_copy(update=updates)


def execution_evidence(**changes):
    return {"contract_version": "offline-test-1", "config_version": "fixture-1",
            "engine_incarnation": "synthetic-engine", "apply_seq": 1,
            "event_ids": ["synthetic-event"], "input_versions": {"fixture-feed": 1},
            "expected_epochs": {"global": 1, "account:synthetic-account": 1,
                                "strategy:strategy": 1},
            "quality": "LIVE", "mode": "offline", **changes}


def build_owner(tmp_path, *, adapter=None, resume=True, clock=None):
    now = clock if clock is not None else [1_000_000_000]
    scope = AccountScope("binance", "synthetic-account", "spot", "offline")
    ledger = ExecutionLedger(tmp_path / "orders.sqlite3", scope,
                             max_intents=16, max_evidence_bytes=16384, max_fills=32,
                             authority_root=tmp_path / "authority")
    control = RuntimeControl(ledger)
    transport = adapter if adapter is not None else FakeAdapter()
    budgets = ExecutionBudgets(Decimal("100"), Decimal("1000"), 4,
                               10_000_000_000, 1.0, 1.0)
    owner = ExecutionOwner(ledger, control, transport, budgets, clock=lambda: now[0])
    reconciled_version = ledger.account_version()
    ready = lambda: ExecutionReadiness(
        scope.key, True, True, True, now[0] + 100_000_000,
        Decimal("0"), Decimal("1000"), account_version=reconciled_version,
        allowed_instruments=(execution_intent().instrument.key,),
        market_price_caps=((execution_intent().instrument.key, Decimal("10")),))
    if resume:
        resume_owner(owner, ready)
    return owner, transport, ready, now


def resume_owner(owner, ready):
    for scope in ("global", "account", "strategy"):
        owner.resume("strategy",
                     expected_epochs=owner.control.snapshot("strategy")["epochs"],
                     readiness=ready, scope=scope)


async def send(owner, ready, now, order=None, **changes):
    return await owner.dispatch(
        order or execution_intent(), expiry_ns=now[0] + 1_000_000_000,
        evidence=execution_evidence(), reserve_unit=Decimal("10"),
        readiness=ready, **changes)


# ---------------------------------------------------------------------------
# Existing paper portfolio ledger helpers (same contract as test_portfolio_ledger)
# ---------------------------------------------------------------------------

def paper_spec(kind=ProductKind.CASH):
    return ProductSpec(
        instrument=InstrumentId(
            venue=Venue.BINANCE,
            asset_class=AssetClass.CRYPTO_SPOT if kind == ProductKind.CASH else AssetClass.CRYPTO_PERP,
            raw_symbol="BTCUSDT", std_symbol="BTCUSDT"),
        kind=kind, multiplier=D("1"), tick_size=D("0.1"), lot_size=D("0.1"),
        min_notional=D("1"), quote_currency="USDT", settlement_currency="USDT",
        collateral_currency="USDT", position_mode="net",
        margin_mode="cash" if kind == ProductKind.CASH else "isolated",
        fee_provenance="explicit test fee", funding_provenance="explicit test funding",
        margin_model_provenance="explicit simulated assumption",
        maker_fee_rate=D("-0.001"), taker_fee_rate=D("0.002"))


def paper_order(rules=None, identity="one", side=Side.BUY, quantity="1",
                price="100", metadata=None):
    return OrderIntent(
        intent_id=identity, job_id="job", signal_id="signal",
        destination=OrderDestination.REAL,
        instrument=(rules or paper_spec()).instrument, side=side,
        quantity=D(quantity), limit_price=D(price),
        client_order_id="client-" + identity, created_ts_ns=1,
        metadata=metadata or {})


def paper_ledger(root):
    return PortfolioLedger(
        root / "paper" / "portfolio.sqlite",
        AccountScope("binance", "acct", "trading", "paper"),
        owner_root=root, mode="paper", max_orders=30, max_fills=30,
        max_events=30, max_document_bytes=100_000,
        max_db_bytes=4_000_000, max_wal_bytes=8_000_000)


def seeded_paper(root):
    book = paper_ledger(root)
    book.seed_balance("USDT", D("1000"), {"simulation": True})
    return book


def admit_paper(book, intent=None, *, rules=None, strategy="alpha", **kwargs):
    options = dict(funding_buffer=D("0"), account_notional_limit=D("10000"),
                   strategy_notional_limit=D("5000"), account_capital_limit=D("10000"),
                   strategy_capital_limit=D("5000"), now_ns=1)
    options.update(kwargs)
    rules = rules or paper_spec()
    return book.admit_order(
        intent or paper_order(rules), strategy, {"source": "local test"},
        1000, rules, **options)


# ---------------------------------------------------------------------------
# Existing ETF journal helpers (same contract as test_vnext_vn12)
# ---------------------------------------------------------------------------

def make_etf_journal(tmp_path):
    journal = EtfJournal(tmp_path / "journal.sqlite3")
    journal.initialize(D(6000), "offline-account")
    value = OrderIntent(
        intent_id="one", job_id="C3", signal_id="signal",
        client_order_id="client-one", destination=OrderDestination.REAL,
        instrument=InstrumentId(venue=Venue.KIS, asset_class=AssetClass.ETF,
                                raw_symbol="069500", std_symbol="069500"),
        side=Side.BUY, quantity=D(10), limit_price=D(100))
    journal.stage(value, "20261021", 1, None)
    return journal


def etf_facts(journal):
    row = dict(journal.db.execute("SELECT * FROM orders").fetchone())
    book = journal.book("C3")
    notices = tuple(
        (r["id"], r["kind"]) for r in journal.db.execute(
            "SELECT id,kind FROM notifications ORDER BY id"))
    return row, book, notices


@pytest.fixture
def journal(tmp_path):
    item = make_etf_journal(tmp_path)
    yield item
    item.db.close()


# ---------------------------------------------------------------------------
# 1. Offline ExecutionOwner: immutable intent/fill facts, no venue proof claim
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("filled,expected_state,expected_reservation,expected_position", [
    ("1", "SUBMITTED", D("10"), D("1")),
    ("2", "FILLED", D("0"), D("2")),
])
async def test_offline_owner_fill_before_ack_is_one_immutable_fact(
        tmp_path, filled, expected_state, expected_reservation, expected_position):
    owner, adapter, ready, now = build_owner(tmp_path)

    async def fill_before_ack():
        assert owner.ledger.apply_fill_once(
            "one", "provider-fill-1", quantity=filled, price="10", fee="0.1")

    adapter.after_permit = fill_before_ack
    result = await send(owner, ready, now)

    row = owner.ledger.get("one")
    assert result["status"] == expected_state
    assert row["state"] == expected_state
    assert D(row["filled"]) == D(filled)
    assert D(row["notional"]) == D(filled) * D("10")
    assert D(row["fees"]) == D("0.1")
    assert D(row["reservation"]) == expected_reservation
    assert owner.ledger.position(execution_intent().instrument.key) == expected_position
    assert owner.ledger.account_version() == 1

    # The fake adapter response is persisted, but it is not venue proof.
    assert json.loads(row["response"]) == {
        "status": "ACCEPTED", "order_id": "fake-client-one"}
    assert adapter.offline_only is True
    assert adapter.execution_handoff_protocol == "offline-permit-v1"

    version, snapshot = owner.ledger.account_version(), dict(row)
    assert not owner.ledger.apply_fill_once(
        "one", "provider-fill-1", quantity=filled, price="10", fee="0.1")
    assert owner.ledger.get("one") == snapshot
    assert owner.ledger.account_version() == version
    assert owner.ledger.position(execution_intent().instrument.key) == expected_position

    with pytest.raises(ValueError, match="conflicts"):
        owner.ledger.apply_fill_once(
            "one", "provider-fill-1", quantity="1", price="10", fee="0")
    assert owner.ledger.get("one") == snapshot
    assert owner.ledger.account_version() == version
    assert owner.ledger.position(execution_intent().instrument.key) == expected_position

    # A late/duplicate ACK after the fill fact cannot resurrect reservation.
    owner.ledger.outcome("one", "SUBMITTED",
                         {"order_id": "fake-client-one", "late": True})
    late = owner.ledger.get("one")
    assert late["state"] == expected_state
    assert D(late["reservation"]) == expected_reservation
    assert D(late["filled"]) == D(filled)
    assert D(late["notional"]) == D(filled) * D("10")
    assert D(late["fees"]) == D("0.1")
    assert owner.ledger.account_version() == version
    await owner.close()


@pytest.mark.asyncio
async def test_execution_fact_names_map_to_immutable_intent_and_account_scope(tmp_path):
    owner, adapter, ready, now = build_owner(tmp_path)
    await send(owner, ready, now)

    row = owner.ledger.get("one")
    stored = OrderIntent.model_validate_json(row["payload"])
    source = execution_intent()
    assert row["intent_id"] == source.intent_id == "one"
    assert row["client_order_id"] == source.client_order_id == "client-one"
    assert stored.client_order_id == source.client_order_id
    assert stored.instrument == source.instrument
    assert stored.instrument.key == source.instrument.key
    assert stored.metadata["account_id"] == owner.ledger.scope.account == "synthetic-account"
    assert stored.metadata["environment"] == owner.ledger.scope.environment == "offline"
    assert stored.job_id == "strategy"
    assert json.loads(row["epochs"]) == execution_evidence()["expected_epochs"]

    scope = AccountScope("binance", "synthetic-account", "spot", "offline")
    assert owner.ledger.scope == scope
    assert owner.ledger.scope.key == scope.key
    assert owner.supports_offline_handoff() is True

    # Same intent id with a different immutable payload is fenced before send.
    with pytest.raises(ValueError, match="conflicts"):
        await send(owner, ready, now, execution_intent(quantity="3"))
    assert len(adapter.mutations) == 1
    await owner.close()


# ---------------------------------------------------------------------------
# 2. Paper PortfolioLedger: existing fill receipts/order states only
# ---------------------------------------------------------------------------

def test_cash_full_fill_and_terminal_cancel_facts_never_revive(tmp_path):
    with seeded_paper(tmp_path / "full") as book:
        created, admitted = admit_paper(book)
        assert created
        assert D(admitted["reservation"]) == D("100.2")

        assert book.apply_fill("fill-complete", "one", D("1"), D("100"),
                               fee=D("0.1"), fee_currency="USDT")
        row = book.get("one")
        assert row["state"] == "FILLED"
        assert D(row["filled"]) == D("1")
        assert D(row["reservation"]) == D("0")
        report = book.report()
        assert D(report["balances"]["USDT"]) == D("899.9")
        assert D(report["physical_positions"][paper_spec().instrument.key]) == D("1")
        assert D(report["virtual_positions"]["alpha"][paper_spec().instrument.key]) == D("1")
        assert D(report["reserved"]["USDT"]) == D("0")

        book.cancel("one")
        assert book.get("one")["state"] == "FILLED"
        assert D(book.get("one")["reservation"]) == D("0")
        book.cancel("one", confirmed=True,
                    evidence={"terminal": "paper cancel effective"})
        assert book.get("one")["state"] == "FILLED"
        assert D(book.get("one")["reservation"]) == D("0")
        assert book.report() == report


def test_cash_partial_cancel_unknown_fill_and_snapshot_guards(tmp_path):
    with seeded_paper(tmp_path / "partial") as book:
        created, admitted = admit_paper(book)
        assert created
        assert D(admitted["reservation"]) == D("100.2")

        assert book.apply_fill("part-1", "one", D("0.4"), D("100"),
                               fee=D("0.04"), fee_currency="USDT")
        row = book.get("one")
        assert row["state"] == "OPEN"
        assert D(row["filled"]) == D("0.4")
        assert D(row["reservation"]) == D("60.12")
        assert D(book.report()["balances"]["USDT"]) == D("959.96")
        assert book.position("alpha", paper_spec().instrument.key) == D("0.4")

        requested = book.request_cancel("one", "alpha")
        assert requested["state"] == "CANCEL_PENDING"
        book.cancel("one")
        row = book.get("one")
        assert row["state"] == "UNKNOWN"
        assert D(row["filled"]) == D("0.4")
        assert D(row["reservation"]) == D("60.12")
        assert D(book.report()["balances"]["USDT"]) == D("959.96")

        assert book.apply_fill("part-2", "one", D("0.6"), D("100"),
                               fee=D("0.06"), fee_currency="USDT")
        row = book.get("one")
        assert row["state"] == "FILLED"
        assert D(row["filled"]) == D("1")
        assert D(row["reservation"]) == D("0")
        terminal = book.report()
        assert D(terminal["balances"]["USDT"]) == D("899.9")
        assert D(terminal["physical_positions"][paper_spec().instrument.key]) == D("1")
        assert D(terminal["virtual_positions"]["alpha"][paper_spec().instrument.key]) == D("1")
        version = book.account_version()
        assert version == 2

        # Exact duplicate fill receipts are inert; no second accounting change.
        assert not book.apply_fill("part-1", "one", D("0.4"), D("100"),
                                   fee=D("0.04"), fee_currency="USDT")
        assert not book.apply_fill("part-2", "one", D("0.6"), D("100"),
                                   fee=D("0.06"), fee_currency="USDT")
        assert book.report() == terminal
        assert book.account_version() == version

        # Only the exact current account version can be reconciled.
        instrument = paper_spec().instrument.key
        with pytest.raises(ValueError, match="Stale"):
            book.reconcile_positions(
                "snap-stale", {instrument: D("1")},
                {"scope": "stale paper fixture"}, account_version=version - 1)
        assert book.report() == terminal
        assert book.reconcile_positions(
            "snap-current", {instrument: D("1")},
            {"scope": "current paper fixture"}, account_version=version)
        assert book.report() == terminal
        assert book.report()["gaps"] == {}

        # Unknown intent and conflicting receipt identity roll back completely.
        with pytest.raises(ValueError, match="unknown intent"):
            book.apply_fill("ghost", "missing", D("1"), D("100"),
                            fee=D("0"), fee_currency="USDT")
        assert book.report() == terminal
        with pytest.raises(ValueError, match="collision"):
            book.apply_fill("part-2", "one", D("0.1"), D("100"),
                            fee=D("0"), fee_currency="USDT")
        assert book.report() == terminal
        assert book.account_version() == version


# ---------------------------------------------------------------------------
# 3. ETF journal: cumulative/native broker facts keep their existing names
# ---------------------------------------------------------------------------

def test_etf_cumulative_fill_before_native_ack_maps_existing_order_facts(journal):
    journal.apply_status("one", {
        "tot_ccld_qty": "4", "tot_ccld_amt": "396", "rmn_qty": "6"})
    row, book, notices = etf_facts(journal)
    assert row["status"] == "pending"
    assert row["filled"] == 4
    assert D(row["notional"]) == D("396")
    assert D(row["reserve"]) == D("603")
    assert book["cash"] == D("1603.9406")
    assert book["positions"] == {"069500": 4}
    assert book["reserved"] == D("603")
    assert ("one:filled:4", "mock_fill") in notices

    journal.accepted("one", {"ODNO": "native-one",
                             "KRX_FWDG_ORD_ORGNO": "org"})
    row, book, notices = etf_facts(journal)
    assert row["status"] == "pending"
    assert row["order_number"] == "native-one"
    assert row["organization"] == "org"
    assert row["accepted_ns"] is not None
    assert book["cash"] == D("1603.9406")
    assert book["positions"] == {"069500": 4}
    assert book["reserved"] == D("603")
    assert ("one:accepted", "mock_order") in notices

    journal.apply_status("one", {
        "tot_ccld_qty": "10", "tot_ccld_amt": "990", "rmn_qty": "0"})
    row, book, notices = etf_facts(journal)
    assert row["status"] == "complete"
    assert row["filled"] == 10
    assert D(row["notional"]) == D("990")
    assert D(row["reserve"]) == D("0")
    assert book["cash"] == D("1009.8515")
    assert book["positions"] == {"069500": 10}
    assert book["reserved"] == D("0")
    assert ("one:filled:10", "mock_fill") in notices


def test_etf_duplicate_late_facts_are_inert_and_conflicts_roll_back(journal):
    journal.apply_status("one", {
        "tot_ccld_qty": "4", "tot_ccld_amt": "396", "rmn_qty": "6"})
    journal.accepted("one", {"ODNO": "native-one",
                             "KRX_FWDG_ORD_ORGNO": "org"})
    journal.apply_status("one", {
        "tot_ccld_qty": "10", "tot_ccld_amt": "990", "rmn_qty": "0"})
    terminal = etf_facts(journal)
    assert terminal[0]["status"] == "complete"
    assert terminal[1]["cash"] == D("1009.8515")
    assert terminal[1]["positions"] == {"069500": 10}
    assert terminal[1]["reserved"] == D("0")

    # Duplicate cumulative facts and late/duplicate ACK preserve row, book and
    # notification identities exactly.
    journal.apply_status("one", {
        "tot_ccld_qty": "10", "tot_ccld_amt": "990", "rmn_qty": "0"})
    assert etf_facts(journal) == terminal
    journal.accepted("one", {"ODNO": "native-one",
                             "KRX_FWDG_ORD_ORGNO": "org"})
    assert etf_facts(journal) == terminal

    # Regressing cumulative fill facts fails closed without mutation.
    with pytest.raises(RuntimeError, match="inconsistent"):
        journal.apply_status("one", {
            "tot_ccld_qty": "9", "tot_ccld_amt": "891", "rmn_qty": "1"})
    assert etf_facts(journal) == terminal

    # Conflicting native order identity fails without mutation.
    for ack in ({"ODNO": "different"},
                {"ODNO": "native-one", "KRX_FWDG_ORD_ORGNO": "different"}):
        with pytest.raises(ValueError):
            journal.accepted("one", ack)
        assert etf_facts(journal) == terminal
