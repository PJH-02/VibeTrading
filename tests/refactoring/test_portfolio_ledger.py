"""Local simulation contracts: no root settings, venue clients, or external account calls."""
from dataclasses import replace
from decimal import Decimal
import sqlite3

import pytest

from execution.execution_ledger import AccountScope
from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
from portfolio.portfolio_ledger import PortfolioLedger
from execution.trading_contracts import ActionKind, ExecutionAction, ProductKind, ProductSpec, TradingMode


D = Decimal


def spec(kind=ProductKind.CASH):
    return ProductSpec(
        instrument=InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_SPOT if kind == ProductKind.CASH else AssetClass.CRYPTO_PERP,
                                raw_symbol="BTCUSDT", std_symbol="BTCUSDT"),
        kind=kind, multiplier=D("1"), tick_size=D("0.1"), lot_size=D("0.1"), min_notional=D("1"),
        quote_currency="USDT", settlement_currency="USDT", collateral_currency="USDT",
        position_mode="net", margin_mode="cash" if kind == ProductKind.CASH else "isolated",
        fee_provenance="explicit test fee", funding_provenance="explicit test funding",
        margin_model_provenance="explicit simulated assumption", maker_fee_rate=D("-0.001"), taker_fee_rate=D("0.002"))


def order(rules=None, identity="one", side=Side.BUY, quantity="1", price="100", metadata=None):
    return OrderIntent(intent_id=identity, job_id="job", signal_id="signal", destination=OrderDestination.REAL,
                       instrument=(rules or spec()).instrument, side=side, quantity=D(quantity), limit_price=D(price),
                       client_order_id="client-" + identity, created_ts_ns=1, metadata=metadata or {})


def ledger(tmp_path, **kwargs):
    options = dict(owner_root=tmp_path, mode="paper", max_orders=30, max_fills=30, max_events=30,
                   max_document_bytes=100_000, max_db_bytes=4_000_000, max_wal_bytes=8_000_000)
    options.update(kwargs)
    return PortfolioLedger(tmp_path / "paper" / "portfolio.sqlite", AccountScope("binance", "acct", "trading", "paper"), **options)


def admit(book, intent=None, *, rules=None, strategy="alpha", **kwargs):
    options = dict(funding_buffer=D("0"), account_notional_limit=D("10000"), strategy_notional_limit=D("5000"),
                   account_capital_limit=D("10000"), strategy_capital_limit=D("5000"), now_ns=1)
    options.update(kwargs)
    rules = rules or spec()
    return book.admit_order(intent or order(rules), strategy, {"source": "local test"}, 1000, rules, **options)


def seeded(tmp_path, **kwargs):
    book = ledger(tmp_path, **kwargs)
    book.seed_balance("USDT", D("1000"), {"simulation": True})
    return book


def test_product_wire_roundtrip_and_explicit_capabilities():
    rules = spec()
    assert ProductSpec.from_document(rules.to_document()) == rules
    for altered in (replace(rules, kind=ProductKind.INVERSE_PERPETUAL), replace(rules, position_mode="hedge"),
                    replace(rules, collateral_currency="BTC")):
        with pytest.raises(ValueError):
            altered.supported()
    assert rules.maker_fee_rate < 0
    with pytest.raises(ValueError):
        replace(rules, taker_fee_rate=D("NaN"))


def test_toss_and_mode_and_path_rejected_before_registration(tmp_path):
    with pytest.raises(ValueError, match="TOSS"):
        PortfolioLedger(tmp_path / "paper" / "x", AccountScope("toss", "acct", "cash", "paper"),
                        owner_root=tmp_path, mode="paper", max_orders=1, max_fills=1, max_events=1,
                        max_document_bytes=1000, max_db_bytes=1000000, max_wal_bytes=1000000)
    assert not (tmp_path / ".portfolio-owners").exists()
    with pytest.raises(ValueError, match="environment"):
        ledger(tmp_path, mode="live")
    with pytest.raises(ValueError, match="protected"):
        PortfolioLedger(tmp_path / "unsafe", AccountScope("binance", "acct", "trading", "paper"),
                        owner_root=tmp_path, mode="paper", max_orders=1, max_fills=1, max_events=1,
                        max_document_bytes=1000, max_db_bytes=1000000, max_wal_bytes=1000000)


def test_action_preserves_legacy_wire_and_cancel_namespace_contract():
    intent = order()
    before = intent.model_dump(mode="json")
    action = ExecutionAction("action", ActionKind.NEW, AccountScope("binance", "acct", "cash", "paper"),
                             "alpha", "config-v1", TradingMode.PAPER, 1000, intent)
    assert action.intent.model_dump(mode="json") == before
    with pytest.raises(ValueError, match="target_order_id"):
        replace(action, action=ActionKind.CANCEL)


def test_reservation_duplicate_evidence_collision_and_multistrategy_limit(tmp_path):
    with seeded(tmp_path) as book:
        created, row = admit(book)
        assert created and D(row["reservation"]) == D("100.200")
        assert not admit(book)[0]
        with pytest.raises(ValueError, match="collision"):
            book.admit_order(order(), "alpha", {"source": "changed"}, 1000, spec(),
                             funding_buffer=0, account_notional_limit=10000, strategy_notional_limit=5000,
                             account_capital_limit=10000, strategy_capital_limit=5000, now_ns=1)
        with pytest.raises(ValueError, match="capital"):
            admit(book, order(identity="two", quantity="9"), strategy="beta")
        assert book.get("two") is None
        with pytest.raises(ValueError, match="notional"):
            admit(book, order(identity="three"), strategy="beta", account_notional_limit=150)


def test_fill_exactly_once_signed_fee_and_physical_virtual_cash_atomicity(tmp_path):
    with seeded(tmp_path) as book:
        admit(book)
        assert book.apply_fill("fill", "one", D("0.4"), D("99"), fee=D("-0.02"), fee_currency="USDT")
        assert not book.apply_fill("fill", "one", D("0.4"), D("99"), fee=D("-0.02"), fee_currency="USDT")
        with pytest.raises(ValueError, match="collision"):
            book.apply_fill("fill", "one", D("0.5"), D("99"), fee=D("-0.02"))
        report = book.report()
        assert D(report["balances"]["USDT"]) == D("960.42")
        assert report["physical_positions"] == report["virtual_positions"]["alpha"]
        assert D(book.get("one")["reservation"]) == D("60.12")
        assert D(report["strategy_cash"][0]["fees"]) == D("-0.02")


def test_outer_matcher_transaction_and_inner_failure_rollback(tmp_path):
    with seeded(tmp_path) as book:
        admit(book)
        with pytest.raises(RuntimeError, match="fault"):
            with book.transaction():
                book.apply_fill("fill", "one", 1, 100)
                book.save_paper_state({"orders": ["one"]})
                raise RuntimeError("fault before commit")
        assert book.paper_state() is None and book.position("alpha", spec().instrument.key) == 0
        with book.transaction():
            with pytest.raises(ValueError, match="exceeds"):
                book.apply_fill("fill", "one", 2, 100)
            book.save_paper_state({"committed": True})
        # A caught nested failure must not retain the rejected receipt.
        assert book.apply_fill("fill", "one", 1, 100)


def test_cancel_ack_retains_reserve_late_fill_and_restart(tmp_path):
    with seeded(tmp_path) as book:
        admit(book)
        with pytest.raises(ValueError, match="namespace"):
            book.request_cancel("one", "beta")
        book.request_cancel("one", "alpha")
        book.cancel("one")
        assert D(book.get("one")["reservation"]) > 0
        book.save_paper_state({"orders": ["one"]})
    with ledger(tmp_path) as reopened:
        assert reopened.get("one")["state"] == "UNKNOWN"
        assert reopened.paper_state() == {"orders": ["one"]}
        reopened.cancel("one", confirmed=True, evidence={"terminal": "paper cancel effective"})
        assert D(reopened.get("one")["reservation"]) == 0
        assert reopened.apply_fill("late", "one", D("0.5"), 100)
        assert reopened.get("one")["state"] == "CANCELED"
        assert D(reopened.get("one")["filled"]) == D("0.5")


def test_cash_short_sell_and_reduce_only_claim_fail_closed(tmp_path):
    with seeded(tmp_path) as book:
        with pytest.raises(ValueError, match="holdings"):
            admit(book, order(side=Side.SELL))
        with pytest.raises(ValueError, match="Reduce-only"):
            admit(book, order(metadata={"reduce_only": True}))
        admit(book)
        book.apply_fill("buy", "one", 1, 100)
        admit(book, order(identity="sell", side=Side.SELL, metadata={"reduce_only": True}))
        book.apply_fill("sell-fill", "sell", 1, 100)
        assert book.position("alpha", spec().instrument.key) == 0


def test_linear_simulated_margin_realized_pnl_and_funding_receipt(tmp_path):
    rules = spec(ProductKind.LINEAR_PERPETUAL)
    with seeded(tmp_path) as book:
        with pytest.raises(ValueError, match="margin rate"):
            admit(book, order(rules), rules=rules)
        admit(book, order(rules), rules=rules, margin_rate=D("0.1"))
        assert D(book.get("one")["reservation"]) == D("10.2")
        book.apply_fill("buy", "one", 1, 100, fee=D("0.2"))
        assert D(book.report()["balances"]["USDT"]) == D("999.8")
        assert book.apply_funding("fund", rules.instrument.key, D("-1"), "USDT", {"period": "one"}, strategy="alpha")
        assert not book.apply_funding("fund", rules.instrument.key, D("-1"), "USDT", {"period": "one"}, strategy="alpha")
        admit(book, order(rules, identity="close", side=Side.SELL, price="110"), rules=rules, margin_rate=D("0.1"))
        book.apply_fill("close-fill", "close", 1, 110)
        assert D(book.report()["balances"]["USDT"]) == D("1008.8")
        assert book.position("alpha", rules.instrument.key) == 0
        assert book.readiness()["margin_model"] == "paper simulation assumption"


def test_unknown_external_position_blocks_and_matching_snapshot_resolves(tmp_path):
    with seeded(tmp_path) as book:
        instrument = spec().instrument.key
        book.reconcile_positions("snapshot-1", {instrument: 2}, {"scope": "paper fixture"}, account_version=book.account_version())
        assert not book.readiness()["ready"]
        with pytest.raises(RuntimeError, match="gap"):
            admit(book)
        assert book.position("alpha", instrument) == 0
        book.reconcile_positions("snapshot-2", {instrument: 0}, {"scope": "paper corrected fixture"}, account_version=book.account_version())
        assert book.readiness()["ready"]
        admit(book)


def test_bounds_and_path_alias_scope_lease(tmp_path):
    with seeded(tmp_path, max_orders=1, max_fills=1) as book:
        admit(book)
        with pytest.raises(RuntimeError, match="row budget"):
            admit(book, order(identity="second"))
        assert book.get("second") is None
        book.apply_fill("first", "one", D("0.5"), 100)
        with pytest.raises(RuntimeError, match="row budget"):
            book.apply_fill("second", "one", D("0.5"), 100)
        assert D(book.get("one")["filled"]) == D("0.5")
        with pytest.raises(RuntimeError, match="another process"):
            ledger(tmp_path)
    with pytest.raises(ValueError, match="bound"):
        PortfolioLedger(tmp_path / "paper" / "other.sqlite", AccountScope("binance", "acct", "trading", "paper"),
                        owner_root=tmp_path, mode="paper", max_orders=30, max_fills=30, max_events=30,
                        max_document_bytes=100000, max_db_bytes=4000000, max_wal_bytes=8000000)


def test_document_budget_rolls_back_receipt_and_ledger(tmp_path):
    with seeded(tmp_path, max_document_bytes=10000) as book:
        admit(book)
        with pytest.raises(ValueError, match="byte budget"):
            with book.transaction():
                book.apply_fill("fill", "one", 1, 100)
                book.save_paper_state({"oversized": "x" * 10001})
        assert D(book.get("one")["filled"]) == 0
        assert book.position("alpha", spec().instrument.key) == 0


def test_opposing_strategy_virtual_exposure_keeps_gross_risk_and_physical_pnl(tmp_path):
    rules = spec(ProductKind.LINEAR_PERPETUAL)
    with seeded(tmp_path) as book:
        admit(book, order(rules), rules=rules, margin_rate=D("0.1"))
        book.apply_fill("long", "one", 1, 100)
        admit(book, order(rules, identity="short", side=Side.SELL, price="110"), rules=rules,
              strategy="beta", margin_rate=D("0.1"))
        book.apply_fill("short", "short", 1, 110)
        assert book.position("@physical", rules.instrument.key) == 0
        assert book.position("alpha", rules.instrument.key) == 1
        assert book.position("beta", rules.instrument.key) == -1
        assert D(book.report()["balances"]["USDT"]) == 1010
        adjustment = next(r for r in book.report()["strategy_cash"] if r["strategy"] == "@physical_netting_adjustment")
        assert D(adjustment["amount"]) == 10
        with pytest.raises(ValueError, match="notional"):
            admit(book, order(rules, identity="third"), rules=rules, margin_rate=D("0.1"), account_notional_limit=300)


def test_terminal_and_snapshot_facts_are_monotonic(tmp_path):
    with seeded(tmp_path) as book:
        admit(book)
        book.cancel("one", confirmed=True, evidence={"effective": 2})
        book.cancel("one")
        assert book.get("one")["state"] == "CANCELED"
        with pytest.raises(ValueError, match="Contradictory"):
            book.reject("one", confirmed=True, evidence={"effective": 3})
        book.apply_fill("late", "one", D("0.5"), 100)
        with pytest.raises(ValueError, match="Stale"):
            book.reconcile_positions("stale", {spec().instrument.key: 0}, {"snapshot": "old"}, account_version=0)
        assert book.readiness()["gap_count"] == 0


def test_db_and_wal_budgets_fail_closed(tmp_path):
    with pytest.raises((RuntimeError, sqlite3.DatabaseError)):
        ledger(tmp_path / "db-small", max_db_bytes=4096)
    with pytest.raises(RuntimeError, match="WAL"):
        ledger(tmp_path / "wal-small", max_wal_bytes=4096)


@pytest.mark.parametrize("kind,asset_class", [
    (ProductKind.CASH, AssetClass.CRYPTO_PERP),
    (ProductKind.CASH, AssetClass.FUTURE),
    (ProductKind.CASH, AssetClass.TOKENIZED_EQUITY),
    (ProductKind.LINEAR_PERPETUAL, AssetClass.CRYPTO_SPOT),
    (ProductKind.LINEAR_PERPETUAL, AssetClass.EQUITY),
    (ProductKind.INVERSE_PERPETUAL, AssetClass.CRYPTO_SPOT),
])
def test_product_kind_cannot_relabel_instrument_asset_class(kind, asset_class):
    rules = spec(kind)
    instrument = rules.instrument.model_copy(update={"asset_class": asset_class})
    with pytest.raises(ValueError, match="contradicts"):
        replace(rules, instrument=instrument).supported()


def test_pinned_margin_rate_cannot_reprice_existing_inventory_or_reset_on_restart(tmp_path):
    rules = spec(ProductKind.LINEAR_PERPETUAL)
    with seeded(tmp_path) as book:
        admit(book, order(rules), rules=rules, margin_rate=D("1"))
        book.apply_fill("first", "one", 1, 100)
        assert D(book.report()["simulated_margin"]) == 100
        with pytest.raises(ValueError, match="margin rate is pinned"):
            admit(book, order(rules, identity="cheaper"), rules=rules, margin_rate=D("0.01"))
        assert book.get("cheaper") is None
        assert D(book.report()["simulated_margin"]) == 100
    with ledger(tmp_path) as reopened:
        with pytest.raises(ValueError, match="margin rate is pinned"):
            admit(reopened, order(rules, identity="restart-cheaper"), rules=rules, margin_rate=D("0.01"))
        admit(reopened, order(rules, identity="close", side=Side.SELL), rules=rules, margin_rate=D("1"))
        reopened.apply_fill("close", "close", 1, 100)
        assert D(reopened.report()["simulated_margin"]) == 0
        # A flat balance is not authorization to rewrite the model's economics.
        with pytest.raises(ValueError, match="margin rate is pinned"):
            admit(reopened, order(rules, identity="flat-cheaper"), rules=rules, margin_rate=D("0.01"))


def test_strategy_event_claim_commits_dedup_and_registration_collision(tmp_path):
    registration = {"strategy_version": "1", "config": {"risk": "bounded"}}
    with seeded(tmp_path) as book:
        assert book.claim_strategy_event("alpha", "config1", "@registration", registration)
        assert not book.claim_strategy_event("alpha", "config1", "@registration", registration)
        assert book.claim_strategy_event("alpha", "config1", "market-event", {"market": "receipt"})
        assert not book.transaction_active
        with book.transaction():
            assert book.transaction_active
            with pytest.raises(RuntimeError, match="standalone"):
                book.claim_strategy_event("alpha", "config1", "nested-event", {"market": "receipt"})
    with ledger(tmp_path) as reopened:
        assert not reopened.claim_strategy_event("alpha", "config1", "market-event", {"market": "receipt"})
        with pytest.raises(ValueError, match="collision"):
            reopened.claim_strategy_event("alpha", "config1", "@registration", {"strategy_version": "2"})


def test_strategy_event_claim_obeys_shared_event_row_budget(tmp_path):
    with seeded(tmp_path, max_events=2) as book:
        assert book.claim_strategy_event("alpha", "config1", "one", {"receipt": 1})
        with pytest.raises(RuntimeError, match="row budget"):
            book.claim_strategy_event("alpha", "config1", "two", {"receipt": 2})
        assert not book.claim_strategy_event("alpha", "config1", "one", {"receipt": 1})
