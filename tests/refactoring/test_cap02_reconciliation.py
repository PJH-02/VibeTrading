from decimal import Decimal
import json

import pytest

from execution.execution_ledger import AccountScope
from execution.reconciliation import ReconciliationSession
from execution.trading_contracts import ProductKind, ProductSpec
from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
from portfolio.portfolio_ledger import PortfolioLedger

SOURCE_SCOPE = json.dumps({"venue": "binance", "product": "linear_perpetual",
                           "environment": "sandbox", "account_alias": "acct",
                           "settlement_currency": "USDT", "subaccount": ""},
                          sort_keys=True, separators=(",", ":"))


def make_ledger(tmp_path):
    currency, venue, asset_class = "USDT", Venue.BINANCE, AssetClass.CRYPTO_PERP
    product_kind = ProductKind.LINEAR_PERPETUAL
    book = PortfolioLedger(tmp_path / "paper" / "book.sqlite",
                           AccountScope("binance", "acct", "trading", "paper"),
                           owner_root=tmp_path, mode="paper", max_orders=20,
                           max_fills=30, max_events=40, max_document_bytes=100_000,
                           max_db_bytes=4_000_000, max_wal_bytes=8_000_000)
    book.seed_balance(currency, Decimal("10000"), {"fixture": "paper"})
    product = ProductSpec(
        instrument=InstrumentId(venue=venue, asset_class=asset_class,
                                raw_symbol="BTCUSDT", std_symbol="BTCUSDT"),
        kind=product_kind, multiplier=Decimal("1"), tick_size=Decimal("0.1"),
        lot_size=Decimal("0.1"), min_notional=Decimal("1"), quote_currency=currency,
        settlement_currency=currency, collateral_currency=currency, position_mode="net",
        margin_mode="isolated", fee_provenance="fixture", funding_provenance="fixture",
        margin_model_provenance="paper fixture", maker_fee_rate=Decimal("0"),
        taker_fee_rate=Decimal("0"))
    intent = OrderIntent(intent_id="intent-1", job_id="job", signal_id="signal",
                         destination=OrderDestination.REAL, instrument=product.instrument,
                         side=Side.BUY, quantity=Decimal("5"), limit_price=Decimal("100"),
                         client_order_id="client-1", created_ts_ns=1, metadata={})
    book.admit_order(intent, "alpha", {"fixture": "admitted"}, 1000, product,
                     funding_buffer=0, account_notional_limit=10000,
                     strategy_notional_limit=10000, account_capital_limit=10000,
                     strategy_capital_limit=10000, now_ns=1, margin_rate=Decimal("0.1"))
    return book, product.instrument.raw_symbol, product.instrument.key


def test_start_order_and_fill_are_factual_deduped_and_restart_blocked(tmp_path):
    book, symbol, instrument_key = make_ledger(tmp_path)
    now = [1_000_000_000]
    session = ReconciliationSession(book, SOURCE_SCOPE, max_orders=8, max_events=30,
                                    max_document_bytes=50_000, max_staleness_ns=10_000,
                                    clock_ns=lambda: now[0])
    with pytest.raises(RuntimeError):
        session.require_ready()
    session.bind_order("intent-1", "native-1", symbol=symbol)
    with pytest.raises(ValueError):
        session.ingest({"kind": "fill", "scope_key": "foreign", "event_id": "f1"})
    assert book.readiness()["gap_count"] > 0

    async def auth():
        return {"success": True, "scope_key": SOURCE_SCOPE}

    async def subscribe():
        return True

    async def run():
        generation = await session.start(auth, subscribe)
        fill = {"kind": "fill", "scope_key": SOURCE_SCOPE, "event_id": "fill-1",
                "order_id": "native-1", "symbol": symbol, "quantity": "2", "price": "100",
                "fee": None, "fee_currency": None}
        assert session.ingest(fill, generation)
        assert not session.ingest(fill, generation)
        assert book.position("alpha", instrument_key) == Decimal("2")
        with pytest.raises(ValueError):
            session.ingest({**fill, "price": "101"}, generation)
        await session.stop()

    import asyncio
    asyncio.run(run())
    frozen = session.snapshot()
    frozen["orders"].clear()
    assert session.snapshot()["orders"]
    session2 = ReconciliationSession(book, SOURCE_SCOPE, max_orders=8, max_events=30,
                                     max_document_bytes=50_000, max_staleness_ns=10_000,
                                     clock_ns=lambda: now[0])
    assert not session2.snapshot()["ready"]
    assert session2.snapshot()["orders"]["intent-1"]["status"] == "UNKNOWN"
    book.close()


def test_public_mutation_cannot_publish_inside_caller_transaction(tmp_path):
    book, symbol, instrument_key = make_ledger(tmp_path)
    session = ReconciliationSession(book, SOURCE_SCOPE, max_orders=8, max_events=10,
                                    max_document_bytes=50_000, max_staleness_ns=1000)
    with book.transaction():
        with pytest.raises(RuntimeError, match="caller-owned"):
            session.bind_order("intent-1", "native-1", symbol=symbol)
    assert not session.events()
    assert book.load_reconciliation_state()["revision"] == session._revision
    book.close()


def test_ledger_reconciliation_cas_joins_transaction(tmp_path):
    book, _, _ = make_ledger(tmp_path)
    with book.transaction():
        rev = book.load_reconciliation_state()["revision"]
        assert book.save_reconciliation_state({"x": 1}, rev) == rev + 1
        with pytest.raises(ValueError, match="revision conflict"):
            book.save_reconciliation_state({"x": 2}, rev)
    assert book.load_reconciliation_state()["state"] == {"x": 1}
    with pytest.raises(RuntimeError):
        with book.transaction():
            book.save_reconciliation_state({"x": 3}, rev + 1)
            raise RuntimeError("rollback")
    assert book.load_reconciliation_state()["state"] == {"x": 1}
    book.close()


def test_lost_ack_order_id_is_learned_once_from_exact_client_order(tmp_path):
    book, symbol, _ = make_ledger(tmp_path)
    session = ReconciliationSession(book, SOURCE_SCOPE, max_orders=8, max_events=20,
                                    max_document_bytes=50_000, max_staleness_ns=1000)
    session.bind_order("intent-1", None, symbol=symbol)
    fact = {"kind": "order", "scope_key": SOURCE_SCOPE, "order_id": "native-recovered",
            "client_order_id": "client-1", "symbol": symbol, "status": "OPEN",
            "terminal_proven": False, "cumulative_quantity": "0"}
    session.ingest(fact)
    assert session.snapshot()["orders"]["intent-1"]["native_order_id"] == "native-recovered"
    with pytest.raises(ValueError):
        session.ingest({**fact, "order_id": "different-native"})
    book.close()


def test_invalid_oversized_fact_closes_started_generation(tmp_path):
    book, symbol, _ = make_ledger(tmp_path)
    session = ReconciliationSession(book, SOURCE_SCOPE, max_orders=8, max_events=20,
                                    max_document_bytes=1024, max_staleness_ns=1000)
    session.bind_order("intent-1", "native-1", symbol=symbol)
    with pytest.raises(ValueError):
        session.ingest({"kind": "order", "scope_key": SOURCE_SCOPE, "blob": "x" * 2000})
    assert session._ready is False
    assert book.readiness()["gap_count"] > 0
    book.close()


def test_complete_history_and_fresh_snapshot_can_open_projection_gate(tmp_path):
    book, symbol, instrument_key = make_ledger(tmp_path)
    now = 1_000_000
    session = ReconciliationSession(book, SOURCE_SCOPE, max_orders=8, max_events=30,
                                    max_document_bytes=50_000, max_staleness_ns=1000,
                                    clock_ns=lambda: now)
    session.bind_order("intent-1", "native-1", symbol=symbol)
    fill = {"kind": "fill", "scope_key": SOURCE_SCOPE, "event_id": "fill-1",
            "order_id": "native-1", "symbol": symbol, "quantity": "5", "price": "100",
            "fee": "0.1", "fee_currency": "USDT"}
    terminal = {"kind": "order", "scope_key": SOURCE_SCOPE, "order_id": "native-1",
                "symbol": symbol, "status": "FILLED", "terminal_proven": True,
                "cumulative_quantity": "5"}

    class Source:
        async def history(self, start, end):
            return {"scope_key": SOURCE_SCOPE, "fills": [fill], "funding": [],
                    "complete": True, "start_ns": start, "end_ns": end}

        async def account_snapshot(self):
            return {"scope_key": SOURCE_SCOPE, "account": {},
                    "positions": {instrument_key: "5"}, "orders": [terminal],
                    "complete": True, "observed_ns": now, "identity_verified": True}

    async def run():
        await session.start(lambda: _resolved({"success": True, "scope_key": SOURCE_SCOPE}),
                            lambda: _resolved(True))
        view = await session.reconcile(Source(), start_ns=0, end_ns=now)
        assert view["ready"] is True
        session.require_ready()

    import asyncio
    asyncio.run(run())
    book.close()


async def _resolved(value):
    return value


def test_account_scope_canonical_unicode_and_environment_validation(tmp_path):
    book, _, _ = make_ledger(tmp_path)
    book.scope = AccountScope("binance", "계정", "spot", "paper")
    key = json.dumps(["binance", "계정", "spot", "sandbox"],
                     separators=(",", ":"), ensure_ascii=False)
    ReconciliationSession(book, key, max_orders=8, max_events=20,
                          max_document_bytes=50_000, max_staleness_ns=1000)
    invalid = json.dumps(["binance", "계정", "spot", "garbage"],
                         separators=(",", ":"), ensure_ascii=False)
    with pytest.raises(ValueError, match="environment"):
        ReconciliationSession(book, invalid, max_orders=8, max_events=20,
                              max_document_bytes=50_000, max_staleness_ns=1000)
    with book.transaction():
        with pytest.raises(RuntimeError, match="caller-owned"):
            ReconciliationSession(book, key, max_orders=8, max_events=20,
                                  max_document_bytes=50_000, max_staleness_ns=1000)
    book.close()


def test_kiwoom_cash_scope_accepts_krx_equity_binding():
    from types import SimpleNamespace
    from execution.reconciliation import ReconciliationSession

    spec = ProductSpec(
        instrument=InstrumentId(venue=Venue.KRX, asset_class=AssetClass.EQUITY,
                                raw_symbol="005930", std_symbol="005930"),
        kind=ProductKind.CASH, multiplier=Decimal("1"), tick_size=Decimal("1"),
        lot_size=Decimal("1"), min_notional=Decimal("0"), quote_currency="KRW",
        settlement_currency="KRW", collateral_currency="KRW", position_mode="net",
        margin_mode="cash", fee_provenance="fixture", funding_provenance="fixture",
        margin_model_provenance="fixture", maker_fee_rate=Decimal("0"),
        taker_fee_rate=Decimal("0"))
    stub = object.__new__(ReconciliationSession)
    stub.ledger = SimpleNamespace(
        scope=AccountScope("kiwoom", "acct", "cash", "paper"),
        transaction_active=False,
        get=lambda intent_id: {"rules": json.dumps(spec.to_document()),
                               "client_order_id": "client-1", "instrument": spec.instrument.key})
    stub.source_scope_key = json.dumps(["kiwoom", "acct", "cash", "mock"], separators=(",", ":"))
    stub.source_family, stub.source_product = "account", "cash"
    stub._orders, stub.max_orders = {}, 2
    stub._persist = lambda *args, **kwargs: None
    bound = stub.bind_order("intent-1", "kiwoom-native-1", symbol="005930")
    assert bound["instrument_key"] == spec.instrument.key


def test_private_rest_fill_overlap_allows_optional_client_id_but_checks_provided_id(tmp_path):
    book, symbol, instrument_key = make_ledger(tmp_path)
    session = ReconciliationSession(book, SOURCE_SCOPE, max_orders=8, max_events=20,
                                    max_document_bytes=50_000, max_staleness_ns=1000)
    session.bind_order("intent-1", "native-1", symbol=symbol)
    rest = {"kind": "fill", "scope_key": SOURCE_SCOPE, "event_id": "shared-fill",
            "order_id": "native-1", "symbol": symbol, "quantity": "1", "price": "100",
            "fee": "0.1", "fee_currency": "USDT"}
    assert session.ingest(rest)
    assert not session.ingest({**rest, "client_order_id": "client-1"})
    with pytest.raises(ValueError):
        session.ingest({**rest, "client_order_id": "other-client"})
    assert book.position("alpha", instrument_key) == Decimal("1")
    book.close()


# Independent lead-reviewed fault and recovery regressions (CAP-02).
"""Independent adverse contracts for CAP-02's private reconciliation API.

This file is intentionally kept in the private run directory until the parent
has reviewed the candidate implementation. Fixtures are local PAPER ledger
admissions with the same explicit product and risk budgets as the existing
PortfolioLedger contract tests.
"""
import asyncio
from decimal import Decimal as D

import pytest

from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
from execution.execution_ledger import AccountScope
from execution.perp_execution import PerpScope
from execution.reconciliation import ReconciliationSession
from execution.trading_contracts import ProductKind, ProductSpec
from portfolio.portfolio_ledger import PortfolioLedger


SCOPE = PerpScope("binance", "linear_perpetual", "sandbox", "acct", "USDT").key
NATIVE_SYMBOL = "BTCUSDT"


def seeded(tmp_path, *, quantity="5", identity="intent-1", account="acct"):
    root = tmp_path / account
    product = ProductSpec(
        instrument=InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_PERP,
                                raw_symbol="BTCUSDT", std_symbol="BTCUSDT"),
        kind=ProductKind.LINEAR_PERPETUAL, multiplier=D("1"), tick_size=D("0.1"),
        lot_size=D("0.1"), min_notional=D("1"), quote_currency="USDT",
        settlement_currency="USDT", collateral_currency="USDT", position_mode="net",
        margin_mode="isolated", fee_provenance="explicit paper test fee",
        funding_provenance="explicit paper test funding",
        margin_model_provenance="explicit simulated assumption",
        maker_fee_rate=D("-0.001"), taker_fee_rate=D("0.002"))
    book = PortfolioLedger(root / "paper" / "portfolio.sqlite",
                           AccountScope("binance", account, "trading", "paper"),
                           owner_root=root, mode="paper", max_orders=20,
                           max_fills=50, max_events=60, max_document_bytes=100_000,
                           max_db_bytes=4_000_000, max_wal_bytes=8_000_000)
    book.seed_balance("USDT", D("10000"), {"simulation": "local-paper-fixture"})
    intent = OrderIntent(intent_id=identity, job_id="job", signal_id="signal",
                         destination=OrderDestination.REAL, instrument=product.instrument,
                         side=Side.BUY, quantity=D(quantity), limit_price=D("100"),
                         client_order_id="client-" + identity, created_ts_ns=1, metadata={})
    book.admit_order(intent, "alpha", {"source": "local-paper-fixture"}, 1000, product,
                     funding_buffer=D("0"), account_notional_limit=D("10000"),
                     strategy_notional_limit=D("5000"), account_capital_limit=D("10000"),
                     strategy_capital_limit=D("5000"), now_ns=1, margin_rate=D("0.1"))
    return book, product.instrument.key


def session(book, now, *, max_events=80, operation_timeout_seconds=10.0):
    return ReconciliationSession(book, SCOPE, max_orders=8, max_events=max_events,
                                 max_document_bytes=50_000, max_staleness_ns=100,
                                 clock_ns=lambda: now[0],
                                 operation_timeout_seconds=operation_timeout_seconds)


def start(s):
    async def auth():
        return {"success": True, "scope_key": SCOPE}

    async def subscribe():
        return True

    return asyncio.run(s.start(auth, subscribe))


def fill(symbol, event_id, native_id="native-1", *, qty="2", fee="0.1", price="100"):
    return {"kind": "fill", "scope_key": SCOPE, "event_id": event_id,
            "order_id": native_id, "symbol": symbol, "quantity": qty,
            "price": price, "fee": fee, "fee_currency": "USDT"}


def test_private_and_history_overlap_is_once_and_same_id_conflict_fails_closed(tmp_path):
    book, symbol = seeded(tmp_path)
    now = [1_000]
    s = session(book, now)
    s.bind_order("intent-1", "native-1", symbol=NATIVE_SYMBOL)
    generation = start(s)
    fact = fill(NATIVE_SYMBOL, "native-exec-1")
    assert s.ingest(fact, generation) is True
    assert s.ingest(fact, generation) is False
    assert book.position("alpha", symbol) == D("2")
    with pytest.raises((ValueError, RuntimeError), match="collision|conflict|Immutable"):
        s.ingest(fill(NATIVE_SYMBOL, "native-exec-1", qty="1", price="101"), generation)
    assert book.position("alpha", symbol) == D("2")
    assert not s.snapshot()["ready"]
    book.close()


def test_cancel_ack_partial_fill_late_fill_restart_deduplicates(tmp_path):
    book, symbol = seeded(tmp_path)
    now = [1_000]
    s = session(book, now)
    s.bind_order("intent-1", "native-1", symbol=NATIVE_SYMBOL)
    generation = start(s)
    assert s.ingest(fill(NATIVE_SYMBOL, "exec-1", qty="2"), generation)
    pending = {"kind": "order", "scope_key": SCOPE, "order_id": "native-1",
               "symbol": NATIVE_SYMBOL, "status": "CANCEL_PENDING", "terminal_proven": False,
               "cumulative_quantity": "2"}
    assert s.ingest(pending, generation)
    assert D(book.get("intent-1")["reservation"]) > 0
    assert s.ingest(fill(NATIVE_SYMBOL, "exec-2", qty="1"), generation)
    terminal = {**pending, "status": "CANCELED", "terminal_proven": True,
                "cumulative_quantity": "3"}
    assert s.ingest(terminal, generation)
    assert book.get("intent-1")["state"] == "CANCELED"
    assert book.get("intent-1")["filled"] == "3"
    book.close()

    reopened = PortfolioLedger(tmp_path / "acct" / "paper" / "portfolio.sqlite",
                               AccountScope("binance", "acct", "trading", "paper"),
                               owner_root=tmp_path / "acct", mode="paper", max_orders=20,
                               max_fills=50, max_events=60, max_document_bytes=100_000,
                               max_db_bytes=4_000_000, max_wal_bytes=8_000_000)
    s2 = session(reopened, now)
    assert not s2.snapshot()["ready"]
    generation2 = start(s2)
    assert s2.ingest(fill(NATIVE_SYMBOL, "exec-2", qty="1"), generation2) is False
    assert reopened.get("intent-1")["filled"] == "3"
    reopened.close()


def test_unknown_fee_and_stale_final_gate_never_become_ready(tmp_path):
    book, symbol = seeded(tmp_path)
    now = [1_000]
    s = session(book, now)
    s.bind_order("intent-1", "native-1", symbol=NATIVE_SYMBOL)
    start(s)
    fact = {**fill(NATIVE_SYMBOL, "unknown-fee", qty="5"), "fee": None, "fee_currency": None}
    terminal = {"kind": "order", "scope_key": SCOPE, "order_id": "native-1",
                "symbol": NATIVE_SYMBOL, "status": "FILLED", "terminal_proven": True,
                "cumulative_quantity": "5"}

    class Source:
        async def history(self, start_ns, end_ns):
            return {"scope_key": SCOPE, "fills": [fact], "funding": [], "complete": True,
                    "start_ns": start_ns, "end_ns": end_ns}

        async def account_snapshot(self):
            return {"scope_key": SCOPE, "account": {}, "positions": {symbol: "5"},
                    "orders": [terminal], "complete": True, "observed_ns": now[0],
                    "identity_verified": True}

    with pytest.raises((ValueError, RuntimeError), match="fee|unknown|gap|ready"):
        asyncio.run(s.reconcile(Source(), start_ns=0, end_ns=now[0]))
    assert not s.snapshot()["ready"]
    with pytest.raises(RuntimeError):
        s.require_ready()
    now[0] += 101
    assert not s.snapshot()["ready"]
    book.close()


def test_snapshot_generation_race_and_caller_outer_transaction_publish_nothing(tmp_path):
    book, symbol = seeded(tmp_path)
    now = [1_000]
    s = session(book, now)
    s.bind_order("intent-1", "native-1", symbol=NATIVE_SYMBOL)
    start(s)
    before_events = s.events()
    before_fill = book.get("intent-1")["filled"]
    with book.transaction():
        with pytest.raises(RuntimeError, match="caller-owned"):
            s.ingest(fill(NATIVE_SYMBOL, "outer-tx-fill"), s.generation)
        with pytest.raises(RuntimeError, match="caller-owned"):
            s.mark_gap("outer transaction must not leak")
    assert s.events() == before_events
    assert book.get("intent-1")["filled"] == before_fill

    class PausedSource:
        def __init__(self):
            self.resume = asyncio.Event()

        async def history(self, start_ns, end_ns):
            return {"scope_key": SCOPE, "fills": [], "funding": [], "complete": True,
                    "start_ns": start_ns, "end_ns": end_ns}

        async def account_snapshot(self):
            await self.resume.wait()
            return {"scope_key": SCOPE, "account": {}, "positions": {symbol: "0"},
                    "orders": [], "complete": True, "observed_ns": now[0],
                    "identity_verified": True}

    async def race():
        source = PausedSource()
        task = asyncio.create_task(s.reconcile(source, start_ns=0, end_ns=now[0]))
        await asyncio.sleep(0)
        await s.stop()
        source.resume.set()
        with pytest.raises((ValueError, RuntimeError), match="generation|stale|stop"):
            await task

    asyncio.run(race())
    assert not s.snapshot()["ready"]
    book.close()


def test_ack_scope_symbol_identity_and_outer_transaction_reads_are_fenced(tmp_path):
    book, instrument_key = seeded(tmp_path)
    now = [1_000]
    s = session(book, now)
    assert s.scope_key == book.scope.key and s.source_scope_key == SCOPE
    s.bind_order("intent-1", None, symbol=NATIVE_SYMBOL)
    with pytest.raises(ValueError):
        s.bind_order("intent-1", None, symbol=instrument_key)

    async def rejected_auth():
        return {"success": True, "scope_key": "wrong canonical scope"}
    with pytest.raises(ValueError):
        asyncio.run(s.start(rejected_auth, lambda: asyncio.sleep(0, result=True)))
    assert not s.snapshot()["ready"]

    generation = start(s)
    with pytest.raises(ValueError, match="scope"):
        s.ingest({**fill(NATIVE_SYMBOL, "foreign"), "scope_key": "[]"}, generation)
    assert not s.snapshot()["ready"]
    with book.transaction():
        with pytest.raises(RuntimeError, match="caller-owned"):
            s.snapshot()
        with pytest.raises(RuntimeError, match="caller-owned"):
            s.events()
    book.close()


def test_lost_ack_native_id_binding_requires_exact_admitted_client_and_symbol(tmp_path):
    book, _ = seeded(tmp_path)
    now = [1_000]
    s = session(book, now)
    s.bind_order("intent-1", None, symbol=NATIVE_SYMBOL)
    generation = start(s)
    no_id = {"kind": "fill", "scope_key": SCOPE, "event_id": "lost-ack-fill",
             "order_id": "venue-77", "symbol": NATIVE_SYMBOL, "quantity": "1",
             "price": "100", "fee": "0", "fee_currency": "USDT"}
    with pytest.raises(ValueError, match="Unknown native|client ID"):
        s.ingest(no_id, generation)
    repaired = {**no_id, "client_order_id": "client-intent-1"}
    assert s.ingest(repaired, generation)
    assert s.snapshot()["orders"]["intent-1"]["native_order_id"] == "venue-77"
    book.close()


def test_complete_history_snapshot_can_open_paper_gate_then_external_funding_closes_it(tmp_path):
    book, instrument_key = seeded(tmp_path)
    now = [1_000]
    s = session(book, now)
    s.bind_order("intent-1", "native-1", symbol=NATIVE_SYMBOL)
    start(s)
    terminal = {"kind": "order", "scope_key": SCOPE, "order_id": "native-1",
                "symbol": NATIVE_SYMBOL, "status": "CANCELED", "terminal_proven": True,
                "cumulative_quantity": "0"}

    class Source:
        async def history(self, start_ns, end_ns):
            return {"scope_key": SCOPE, "fills": [], "funding": [], "complete": True,
                    "start_ns": start_ns, "end_ns": end_ns}
        async def account_snapshot(self):
            return {"scope_key": SCOPE, "account": {"equity": "10000"},
                    "positions": {instrument_key: "0"}, "orders": [terminal],
                    "complete": True, "observed_ns": now[0], "identity_verified": True}

    view = asyncio.run(s.reconcile(Source(), start_ns=0, end_ns=now[0]))
    assert view["ready"]
    s.require_ready()
    # A directly applied external financial fact advances the ledger account version.
    book.apply_funding("outside-session-funding", instrument_key, D("1"), "USDT",
                       {"source": "test"})
    assert not s.snapshot()["ready"]
    with pytest.raises(RuntimeError):
        s.require_ready()
    book.close()


def test_order_native_and_client_id_cross_binding_conflict_closes_gate(tmp_path):
    book, _ = seeded(tmp_path)
    now = [1_000]
    s = session(book, now)
    s.bind_order("intent-1", "native-1", symbol=NATIVE_SYMBOL)
    generation = start(s)
    wrong = {"kind": "order", "scope_key": SCOPE, "order_id": "native-1",
             "client_order_id": "someone-elses-order", "symbol": NATIVE_SYMBOL,
             "status": "OPEN", "terminal_proven": False, "cumulative_quantity": "0"}
    with pytest.raises(ValueError):
        s.ingest(wrong, generation)
    assert not s.snapshot()["ready"]
    book.close()


def test_outer_commit_failure_does_not_publish_or_apply_financial_fact(tmp_path):
    book, instrument_key = seeded(tmp_path)
    now = [1_000]
    s = session(book, now)
    s.bind_order("intent-1", "native-1", symbol=NATIVE_SYMBOL)
    generation = start(s)
    before = (book.get("intent-1"), book.position("alpha", instrument_key),
              book.account_version(), s.events(), s.snapshot()["version"])
    real_db = book._db

    class CommitFault:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.fail_once = True
        def execute(self, statement, *args):
            if statement.strip().upper() == "COMMIT" and self.fail_once:
                self.fail_once = False
                raise RuntimeError("injected outer COMMIT failure")
            return self.wrapped.execute(statement, *args)
        def __getattr__(self, name):
            return getattr(self.wrapped, name)

    book._db = CommitFault(real_db)
    with pytest.raises(RuntimeError, match="COMMIT"):
        s.ingest(fill(NATIVE_SYMBOL, "commit-cut-fill", qty="1"), generation)
    book._db = real_db
    after = (book.get("intent-1"), book.position("alpha", instrument_key),
             book.account_version(), s.events(), s.snapshot()["version"])
    assert after[0]["filled"] == before[0]["filled"] == "0"
    assert after[1] == before[1]
    assert after[2] == before[2]
    assert after[3] == before[3]
    assert after[4] == before[4]
    assert not s.snapshot()["ready"]
    # The rollback leaves the same native event available to a later recovery attempt.
    assert s.ingest(fill(NATIVE_SYMBOL, "commit-cut-fill", qty="1"), generation)
    assert book.get("intent-1")["filled"] == "1"
    book.close()


def _fully_ready_session(tmp_path, *, operation_timeout_seconds=10.0):
    book, instrument_key = seeded(tmp_path)
    now = [1_000]
    s = session(book, now, operation_timeout_seconds=operation_timeout_seconds)
    s.bind_order("intent-1", "native-1", symbol=NATIVE_SYMBOL)
    start(s)
    terminal = {"kind": "order", "scope_key": SCOPE, "order_id": "native-1",
                "symbol": NATIVE_SYMBOL, "status": "CANCELED", "terminal_proven": True,
                "cumulative_quantity": "0"}

    class CompleteSource:
        async def history(self, start_ns, end_ns):
            return {"scope_key": SCOPE, "fills": [], "funding": [], "complete": True,
                    "start_ns": start_ns, "end_ns": end_ns}
        async def account_snapshot(self):
            return {"scope_key": SCOPE, "account": {"equity": "10000"},
                    "positions": {instrument_key: "0"}, "orders": [terminal],
                    "complete": True, "observed_ns": now[0], "identity_verified": True}

    asyncio.run(s.reconcile(CompleteSource(), start_ns=0, end_ns=now[0]))
    assert s.snapshot()["ready"]
    return book, s, now, instrument_key


def test_malformed_fact_after_ready_closes_gate_without_financial_mutation(tmp_path):
    book, s, now, instrument_key = _fully_ready_session(tmp_path)
    before = (book.get("intent-1"), book.position("alpha", instrument_key),
              book.account_version())
    malformed = {"kind": "fill", "scope_key": SCOPE, "event_id": "bad-after-ready",
                 "order_id": "native-1", "symbol": NATIVE_SYMBOL,
                 "quantity": "NaN", "price": "100", "fee": "0", "fee_currency": "USDT"}
    with pytest.raises(ValueError):
        s.ingest(malformed, s.generation)
    after = (book.get("intent-1"), book.position("alpha", instrument_key),
             book.account_version())
    assert after == before
    assert not s.snapshot()["ready"]
    with pytest.raises(RuntimeError):
        s.require_ready()
    book.close()


def test_auth_subscribe_history_snapshot_timeouts_are_bounded_and_non_economic(tmp_path):
    async def too_slow():
        await asyncio.sleep(0.05)
        return True

    # Authentication timeout.
    book, _ = seeded(tmp_path / "auth")
    now = [1_000]
    s = session(book, now, operation_timeout_seconds=0.01)
    before = (book.get("intent-1"), {k: v for k, v in book.report().items() if k != "gaps"}, book.account_version())
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(s.start(too_slow, lambda: asyncio.sleep(0, result=True)))
    assert (book.get("intent-1"), {k: v for k, v in book.report().items() if k != "gaps"}, book.account_version()) == before
    assert not s.snapshot()["ready"]
    book.close()

    # Subscription timeout, after successful authentication.
    book, _ = seeded(tmp_path / "subscribe")
    now = [1_000]
    s = session(book, now, operation_timeout_seconds=0.01)
    before = (book.get("intent-1"), {k: v for k, v in book.report().items() if k != "gaps"}, book.account_version())
    async def auth_ok(): return {"success": True, "scope_key": SCOPE}
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(s.start(auth_ok, too_slow))
    assert (book.get("intent-1"), {k: v for k, v in book.report().items() if k != "gaps"}, book.account_version()) == before
    assert not s.snapshot()["ready"]
    book.close()

    # History timeout: no snapshot call is permitted after history fails.
    book, _ = seeded(tmp_path / "history")
    now = [1_000]
    s = session(book, now, operation_timeout_seconds=0.01)
    s.bind_order("intent-1", "native-1", symbol=NATIVE_SYMBOL)
    start(s)
    before = (book.get("intent-1"), {k: v for k, v in book.report().items() if k != "gaps"}, book.account_version())
    class SlowHistory:
        async def history(self, start_ns, end_ns):
            await too_slow()
        async def account_snapshot(self):
            pytest.fail("snapshot must not run after history timeout")
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(s.reconcile(SlowHistory(), start_ns=0, end_ns=now[0]))
    assert (book.get("intent-1"), {k: v for k, v in book.report().items() if k != "gaps"}, book.account_version()) == before
    assert not s.snapshot()["ready"]
    book.close()

    # Snapshot timeout after a complete empty history.
    book, _ = seeded(tmp_path / "snapshot")
    now = [1_000]
    s = session(book, now, operation_timeout_seconds=0.01)
    s.bind_order("intent-1", "native-1", symbol=NATIVE_SYMBOL)
    start(s)
    before = (book.get("intent-1"), {k: v for k, v in book.report().items() if k != "gaps"}, book.account_version())
    class SlowSnapshot:
        async def history(self, start_ns, end_ns):
            return {"scope_key": SCOPE, "fills": [], "funding": [], "complete": True,
                    "start_ns": start_ns, "end_ns": end_ns}
        async def account_snapshot(self):
            await too_slow()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(s.reconcile(SlowSnapshot(), start_ns=0, end_ns=now[0]))
    assert (book.get("intent-1"), {k: v for k, v in book.report().items() if k != "gaps"}, book.account_version()) == before
    assert not s.snapshot()["ready"]
    book.close()


def test_complete_snapshot_omitting_bound_order_cannot_open_gate(tmp_path):
    book, instrument_key = seeded(tmp_path)
    now = [1_000]
    s = session(book, now)
    s.bind_order("intent-1", "native-1", symbol=NATIVE_SYMBOL)
    start(s)

    class OmitsOrder:
        async def history(self, start_ns, end_ns):
            return {"scope_key": SCOPE, "fills": [], "funding": [], "complete": True,
                    "start_ns": start_ns, "end_ns": end_ns}
        async def account_snapshot(self):
            return {"scope_key": SCOPE, "account": {}, "positions": {instrument_key: "0"},
                    "orders": [], "complete": True, "observed_ns": now[0],
                    "identity_verified": True}

    with pytest.raises(ValueError, match="cover|bound|order"):
        asyncio.run(s.reconcile(OmitsOrder(), start_ns=0, end_ns=now[0]))
    assert not s.snapshot()["ready"]
    with pytest.raises(RuntimeError):
        s.require_ready()
    book.close()


def test_late_fill_after_terminal_cancel_reconciles_latest_quantity_without_releasing_again(tmp_path):
    book,key=seeded(tmp_path);now=[1000];s=session(book,now)
    s.bind_order('intent-1','native-1',symbol=NATIVE_SYMBOL);g=start(s)
    s.ingest(fill(NATIVE_SYMBOL,'early',qty='2'),g)
    terminal={'kind':'order','scope_key':SCOPE,'order_id':'native-1','symbol':NATIVE_SYMBOL,'status':'CANCELED','terminal_proven':True,'cumulative_quantity':'2','proof':'first native query'}
    s.ingest(terminal,g)
    assert book.get('intent-1')['reservation']=='0'
    s.ingest(fill(NATIVE_SYMBOL,'late-after-terminal',qty='1'),g)
    latest={**terminal,'cumulative_quantity':'3','proof':'later complete native query'}
    assert s.ingest(latest,g)
    assert book.get('intent-1')['filled']=='3'
    assert s.snapshot()['orders']['intent-1']['cumulative_quantity']=='3'
    assert book.get('intent-1')['state']=='CANCELED'
    assert book.get('intent-1')['reservation']=='0'
    book.close()


def test_fill_identity_cannot_move_to_another_valid_bound_order(tmp_path):
    from test_portfolio_ledger import spec,order,admit
    # Use same immutable selected product and account; each native order is valid.
    book,key=seeded(tmp_path);now=[1000]
    product=spec(ProductKind.LINEAR_PERPETUAL)
    # Match original product fee provenance and contract through its original rules.
    import json
    product=ProductSpec.from_document(json.loads(book.get('intent-1')['rules']))
    second=order(product,identity='intent-2',quantity='1')
    admit(book,second,rules=product,margin_rate=D('.1'))
    s=session(book,now)
    s.bind_order('intent-1','native-1',symbol=NATIVE_SYMBOL)
    s.bind_order('intent-2','native-2',symbol=NATIVE_SYMBOL);g=start(s)
    s.ingest(fill(NATIVE_SYMBOL,'shared-id',qty='1'),g)
    with pytest.raises(ValueError,match='collision'):
        s.ingest(fill(NATIVE_SYMBOL,'shared-id',native_id='native-2',qty='1'),g)
    assert book.get('intent-1')['filled']=='1'
    assert book.get('intent-2')['filled']=='0'
    assert not s.snapshot()['ready']
    book.close()


def test_external_order_admission_also_invalidates_fresh_baseline_without_fill_version(tmp_path):
    from test_portfolio_ledger import order,admit
    import json
    book,s,now,key=_fully_ready_session(tmp_path)
    version=book.account_version()
    product=ProductSpec.from_document(json.loads(book.get('intent-1')['rules']))
    admit(book,order(product,identity='intent-2',quantity='1'),rules=product,margin_rate=D('.1'))
    assert book.account_version()==version # Existing version tracks fill/funding only.
    assert not s.snapshot()['ready']
    with pytest.raises(RuntimeError):s.require_ready()
    book.close()


def test_cancel_pending_survives_partial_fill_and_delayed_open_fact(tmp_path):
    book,key=seeded(tmp_path);now=[1000];s=session(book,now)
    s.bind_order('intent-1','native-1',symbol=NATIVE_SYMBOL);g=start(s)
    pending={'kind':'order','scope_key':SCOPE,'order_id':'native-1','symbol':NATIVE_SYMBOL,'status':'CANCEL_PENDING','terminal_proven':False,'cumulative_quantity':'0'}
    s.ingest(pending,g)
    s.ingest(fill(NATIVE_SYMBOL,'pending-fill',qty='1'),g)
    assert s.snapshot()['orders']['intent-1']['status']=='CANCEL_PENDING'
    opened={**pending,'status':'OPEN','cumulative_quantity':'1'}
    s.ingest(opened,g)
    assert s.snapshot()['orders']['intent-1']['status']=='CANCEL_PENDING'
    class Source:
        async def history(self,start_ns,end_ns):return {'scope_key':SCOPE,'complete':True,'start_ns':start_ns,'end_ns':end_ns,'fills':[],'funding':[]}
        async def account_snapshot(self):return {'scope_key':SCOPE,'complete':True,'identity_verified':True,'observed_ns':now[0],'account':{},'positions':{key:'1'},'orders':[opened]}
    with pytest.raises(ValueError,match='unresolved'):
        asyncio.run(s.reconcile(Source(),start_ns=0,end_ns=1000))
    assert not s.snapshot()['ready']
    assert D(book.get('intent-1')['reservation'])>0
    book.close()


def test_complete_snapshot_must_explicitly_include_bound_zero_positions(tmp_path):
    book,key=seeded(tmp_path);now=[1000];s=session(book,now)
    s.bind_order('intent-1','native-1',symbol=NATIVE_SYMBOL);start(s)
    order={'kind':'order','scope_key':SCOPE,'order_id':'native-1','symbol':NATIVE_SYMBOL,'status':'OPEN','terminal_proven':False,'cumulative_quantity':'0'}
    class Source:
        async def history(self,start_ns,end_ns):return {'scope_key':SCOPE,'complete':True,'start_ns':start_ns,'end_ns':end_ns,'fills':[],'funding':[]}
        async def account_snapshot(self):return {'scope_key':SCOPE,'complete':True,'identity_verified':True,'observed_ns':now[0],'account':{},'orders':[order]}
    with pytest.raises(ValueError,match='positions'):
        asyncio.run(s.reconcile(Source(),start_ns=0,end_ns=1000))
    assert not s.snapshot()['ready']
    book.close()
