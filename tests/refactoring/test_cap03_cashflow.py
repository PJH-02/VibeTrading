from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
from execution.execution_ledger import AccountScope
from execution.execution_ledger import canonical
from execution.trading_contracts import ProductKind, ProductSpec
from portfolio.portfolio_ledger import PortfolioLedger


D = Decimal


def perp_spec():
    return ProductSpec(
        instrument=InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_PERP,
                                raw_symbol="BTCUSDT", std_symbol="BTCUSDT"),
        kind=ProductKind.LINEAR_PERPETUAL, multiplier=D("10"), tick_size=D("1"),
        lot_size=D("1"), min_notional=D("0"), quote_currency="USDT",
        settlement_currency="USDT", collateral_currency="USDT", position_mode="net",
        margin_mode="isolated", fee_provenance="explicit local fixture",
        funding_provenance="explicit local fixture", margin_model_provenance="paper assumption",
        maker_fee_rate=D("0.001"), taker_fee_rate=D("0.002"))


def make_ledger(root):
    ledger = PortfolioLedger(root / "paper" / "book.sqlite",
        AccountScope("binance", "acct", "trading", "paper"), owner_root=root, mode="paper",
        max_orders=20, max_fills=40, max_events=40, max_document_bytes=100_000,
        max_db_bytes=4_000_000, max_wal_bytes=8_000_000)
    ledger.seed_balance("USDT", D("10000"), {"local": True})
    return ledger


def admit(ledger, rules, ident, strategy, side, quantity, price):
    intent = OrderIntent(intent_id=ident, job_id="job", signal_id="signal",
        destination=OrderDestination.REAL, instrument=rules.instrument, side=side,
        quantity=D(quantity), limit_price=D(price), client_order_id="client-" + ident,
        created_ts_ns=1, metadata={})
    return ledger.admit_order(intent, strategy, {"local": True}, 10_000, rules,
        funding_buffer=D("0"), account_notional_limit=D("100000"),
        strategy_notional_limit=D("100000"), account_capital_limit=D("100000"),
        strategy_capital_limit=D("100000"), margin_rate=D("0.1"), now_ns=1)


def test_allocated_funding_requires_version_and_duplicate_replays_after_advance(tmp_path):
    rules = perp_spec()
    with make_ledger(tmp_path) as book:
        admit(book, rules, "alpha-order", "alpha", Side.BUY, "1", "100")
        admit(book, rules, "beta-order", "beta", Side.SELL, "1", "100")
        base = book.account_version()
        with pytest.raises(ValueError, match="account_version"):
            book.apply_funding("fund-1", rules.instrument.key, "-5", "USDT", {"period": "p1"},
                               allocations={"alpha": "-10", "beta": "5"})
        assert book.apply_funding("fund-1", rules.instrument.key, "-5", "USDT", {"period": "p1"},
                                  allocations={"alpha": "-10", "beta": "5"}, account_version=base)
        assert book.account_version() == base + 1
        assert not book.apply_funding("fund-1", rules.instrument.key, "-5", "USDT", {"period": "p1"},
                                      allocations={"alpha": "-10", "beta": "5"}, account_version=base)
        assert book.report()["balances"]["USDT"] == "9995"
        with pytest.raises(ValueError, match="collision"):
            book.apply_funding("fund-1", rules.instrument.key, "-5", "USDT", {"period": "changed"},
                               allocations={"alpha": "-10", "beta": "5"}, account_version=base)


def test_valuation_is_exact_and_separates_physical_adjustment_from_strategy(tmp_path):
    rules = perp_spec()
    with make_ledger(tmp_path) as book:
        admit(book, rules, "long", "alpha", Side.BUY, "2", "100")
        book.apply_fill("f-long", "long", "2", "100", fee="2", fee_currency="USDT")
        admit(book, rules, "short", "beta", Side.SELL, "1", "110")
        book.apply_fill("f-short", "short", "1", "110", fee="2.2", fee_currency="USDT")
        book.apply_funding("fund", rules.instrument.key, "-5", "USDT", {"period": "p"},
                           allocations={"alpha": "-10", "beta": "5"},
                           account_version=book.account_version())
        with localcontext() as context:
            context.prec = 3
            result = book.valuation({rules.instrument.key: "115"},
                maintenance_margin_rates={rules.instrument.key: "0.05"},
                account_version=book.account_version(), asof_ns=10, now_ns=10, max_age_ns=1)
        assert result["cash"] == D("10090.8")
        assert result["equity"] == D("10240.8")
        assert result["unrealized"] == D("150")
        assert result["initial_margin"] == D("310")
        assert result["maintenance_margin"] == D("172.5")
        assert result["physical_netting_adjustment"] == D("100")
        assert "@physical_netting_adjustment" not in result["strategies"]
        assert result["strategies"]["alpha"]["funding"] == D("-10")
        stale = book.valuation({rules.instrument.key: "115"},
            maintenance_margin_rates={rules.instrument.key: "0.05"},
            account_version=book.account_version() - 1, asof_ns=10, now_ns=10, max_age_ns=1)
        assert stale["quality"] == "UNAVAILABLE" and stale["equity"] is None


def test_cash_asset_inventory_is_not_valued_with_perpetual_formula(tmp_path):
    cash = ProductSpec(
        instrument=InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_SPOT,
                                raw_symbol="BTCUSDT", std_symbol="BTCUSDT"),
        kind=ProductKind.CASH, multiplier=D("1"), tick_size=D("1"), lot_size=D("1"),
        min_notional=D("0"), quote_currency="USDT", settlement_currency="USDT",
        collateral_currency="USDT", position_mode="net", margin_mode="cash",
        fee_provenance="explicit local fixture", funding_provenance="explicit local fixture",
        margin_model_provenance="cash", maker_fee_rate=D("0"), taker_fee_rate=D("0"))
    with make_ledger(tmp_path) as book:
        admit(book, cash, "buy", "alpha", Side.BUY, "1", "100")
        book.apply_fill("cash-fill", "buy", "1", "100", fee="0", fee_currency="USDT")
        result = book.valuation({cash.instrument.key: "110"},
            maintenance_margin_rates={cash.instrument.key: "0"},
            account_version=book.account_version(), asof_ns=1, now_ns=1, max_age_ns=1)
        assert result["quality"] == "UNAVAILABLE"
        assert result["equity"] is None


def test_pending_only_strategy_receives_reservation_attribution(tmp_path):
    rules = perp_spec()
    with make_ledger(tmp_path) as book:
        admit(book, rules, "pending", "pending-only", Side.BUY, "1", "100")
        result = book.valuation({}, maintenance_margin_rates={},
            account_version=book.account_version(), asof_ns=1, now_ns=1, max_age_ns=1)
        strategy = result["strategies"]["pending-only"]
        assert strategy["positions"] == {}
        assert strategy["unrealized"] == 0
        assert strategy["reserved"] == D("102")
        assert result["reserved"] == D("102")


def test_pending_cash_product_is_unavailable(tmp_path):
    cash = ProductSpec(
        instrument=InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_SPOT,
                                raw_symbol="BTCUSDT", std_symbol="BTCUSDT"),
        kind=ProductKind.CASH, multiplier=D("1"), tick_size=D("1"), lot_size=D("1"),
        min_notional=D("0"), quote_currency="USDT", settlement_currency="USDT",
        collateral_currency="USDT", position_mode="net", margin_mode="cash",
        fee_provenance="explicit local fixture", funding_provenance="explicit local fixture",
        margin_model_provenance="cash", maker_fee_rate=D("0"), taker_fee_rate=D("0"))
    with make_ledger(tmp_path) as book:
        admit(book, cash, "pending-cash", "cash-strategy", Side.BUY, "1", "100")
        result = book.valuation({}, maintenance_margin_rates={},
            account_version=book.account_version(), asof_ns=1, now_ns=1, max_age_ns=1)
        assert result["quality"] == "UNAVAILABLE"
        assert result["equity"] is None


def test_pending_product_currency_mismatch_is_unavailable(tmp_path):
    rules = perp_spec()
    with make_ledger(tmp_path) as book:
        admit(book, rules, "pending", "alpha", Side.BUY, "1", "100")
        mismatched = replace(rules, quote_currency="USDC", settlement_currency="USDC",
                             collateral_currency="USDC")
        book._db.execute("UPDATE portfolio_orders SET rules=? WHERE intent_id=?",
                         (canonical(mismatched.to_document()), "pending"))
        result = book.valuation({}, maintenance_margin_rates={},
            account_version=book.account_version(), asof_ns=1, now_ns=1, max_age_ns=1)
        assert result["quality"] == "UNAVAILABLE"
        assert result["equity"] is None


def test_unknown_order_blocks_new_risk_but_keeps_exact_replay_idempotent(tmp_path):
    rules = perp_spec()
    with make_ledger(tmp_path) as book:
        first, _ = admit(book, rules, "pending", "alpha", Side.BUY, "1", "100")
        assert first
        book.cancel("pending", confirmed=False)
        assert book.get("pending")["state"] == "UNKNOWN"
        replay, _ = admit(book, rules, "pending", "alpha", Side.BUY, "1", "100")
        assert not replay
        with pytest.raises(RuntimeError, match="Unknown order outcome"):
            admit(book, rules, "new-risk", "beta", Side.SELL, "1", "100")
        assert book.get("new-risk") is None


@pytest.mark.parametrize("bad_value", [1.25, True])
def test_funding_allocations_reject_inexact_numeric_inputs_without_mutation(tmp_path, bad_value):
    rules = perp_spec()
    with make_ledger(tmp_path) as book:
        admit(book, rules, "alpha", "alpha", Side.BUY, "1", "100")
        before = book.report()
        with pytest.raises(ValueError, match="exact decimal"):
            book.apply_funding("bad-fund", rules.instrument.key, "1", "USDT", {"period": "p"},
                               allocations={"alpha": bad_value}, account_version=book.account_version())
        assert book.report() == before


@pytest.mark.parametrize("bad_mark,bad_rate", [(1.25, "0.05"), ("100", True)])
def test_valuation_rejects_inexact_mark_or_rate_without_mutation(tmp_path, bad_mark, bad_rate):
    rules = perp_spec()
    with make_ledger(tmp_path) as book:
        admit(book, rules, "alpha", "alpha", Side.BUY, "1", "100")
        book.apply_fill("fill", "alpha", "1", "100", fee="0", fee_currency="USDT")
        before = book.report()
        with pytest.raises(ValueError, match="exact decimal"):
            book.valuation({rules.instrument.key: bad_mark},
                maintenance_margin_rates={rules.instrument.key: bad_rate},
                account_version=book.account_version(), asof_ns=1, now_ns=1, max_age_ns=1)
        assert book.report() == before
