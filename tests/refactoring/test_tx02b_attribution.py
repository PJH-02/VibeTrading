"""TX02B reserved strategy attribution over the actual paper portfolio ledger."""
from decimal import Decimal

import pytest

from vibetrading.execution_ledger import AccountScope
from vibetrading.models import (AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue)
from vibetrading.portfolio_ledger import PortfolioLedger
from vibetrading.trading_contracts import ProductKind, ProductSpec

D = Decimal
RESERVED_STRATEGIES = ("@physical", "@physical_netting_adjustment", "@unallocated_funding",
                       "@registration", "@action:user")


def spec(kind=ProductKind.CASH):
    return ProductSpec(
        instrument=InstrumentId(venue=Venue.BINANCE,
                                asset_class=AssetClass.CRYPTO_SPOT if kind == ProductKind.CASH else AssetClass.CRYPTO_PERP,
                                raw_symbol="BTCUSDT", std_symbol="BTCUSDT"),
        kind=kind, multiplier=D("1"), tick_size=D("0.1"), lot_size=D("0.1"), min_notional=D("1"),
        quote_currency="USDT", settlement_currency="USDT", collateral_currency="USDT",
        position_mode="net", margin_mode="cash" if kind == ProductKind.CASH else "isolated",
        fee_provenance="explicit test fee", funding_provenance="explicit test funding",
        margin_model_provenance="explicit simulated assumption",
        maker_fee_rate=D("-0.001"), taker_fee_rate=D("0.002"))


def order(rules=None, identity="one", side=Side.BUY, quantity="1", price="100", metadata=None):
    return OrderIntent(intent_id=identity, job_id="job", signal_id="signal", destination=OrderDestination.REAL,
                       instrument=(rules or spec()).instrument, side=side, quantity=D(quantity),
                       limit_price=D(price), client_order_id="client-" + identity,
                       created_ts_ns=1, metadata=metadata or {})


def ledger(tmp_path, **kwargs):
    options = dict(owner_root=tmp_path, mode="paper", max_orders=30, max_fills=30, max_events=30,
                   max_document_bytes=100_000, max_db_bytes=4_000_000, max_wal_bytes=8_000_000)
    options.update(kwargs)
    return PortfolioLedger(tmp_path / "paper" / "portfolio.sqlite",
                           AccountScope("binance", "acct", "trading", "paper"), **options)


def admit(book, intent=None, *, rules=None, strategy="alpha", **kwargs):
    options = dict(funding_buffer=D("0"), account_notional_limit=D("10000"),
                   strategy_notional_limit=D("5000"), account_capital_limit=D("10000"),
                   strategy_capital_limit=D("5000"), now_ns=1)
    options.update(kwargs)
    rules = rules or spec()
    return book.admit_order(intent or order(rules), strategy, {"source": "local test"}, 1000, rules, **options)


def seeded(tmp_path, **kwargs):
    book = ledger(tmp_path, **kwargs)
    book.seed_balance("USDT", D("1000"), {"simulation": True})
    return book


@pytest.mark.parametrize("strategy", RESERVED_STRATEGIES)
def test_reserved_strategy_identity_cannot_write_order_claim_reservation_or_cash(tmp_path, strategy):
    with seeded(tmp_path) as book:
        before = book.report()
        with pytest.raises(ValueError, match="Reserved strategy namespace"):
            admit(book, strategy=strategy)
        with pytest.raises(ValueError, match="Reserved strategy namespace"):
            book.claim_strategy_event(strategy, "config1", "market-event", {"market": "receipt"})
        after = book.report()
        assert after["orders"] == []
        assert after["strategy_cash"] == []
        assert after["reserved"] == before["reserved"]
        assert after["balances"] == before["balances"]
        assert after["account_version"] == before["account_version"]
        assert not book.has_strategy_dispatch(strategy)
        # The same ledger still admits and claims an ordinary user strategy.
        created, row = admit(book)
        assert created and D(row["reservation"]) == D("100.200")
        assert book.claim_strategy_event("alpha", "config1", "market-event", {"market": "receipt"})
        assert book.has_strategy_dispatch("alpha")


def test_normal_strategy_partial_fills_and_restart_attribution(tmp_path):
    registration = {"strategy_version": "1", "config": {"risk": "bounded"}}
    with seeded(tmp_path) as book:
        assert book.claim_strategy_event("alpha", "config1", "@registration", registration)
        assert not book.claim_strategy_event("alpha", "config1", "@registration", registration)
        assert book.claim_strategy_event("alpha", "config1", "@action:new", {"market": "receipt"})
        admit(book)
        assert book.apply_fill("part-1", "one", D("0.4"), D("99"), fee=D("-0.02"))
        row = book.get("one")
        assert row["state"] == "OPEN"
        assert D(row["filled"]) == D("0.4")
        assert D(row["reservation"]) == D("60.12")
        cash = next(r for r in book.report()["strategy_cash"] if r["strategy"] == "alpha")
        assert D(cash["amount"]) == D("-39.58")
        assert D(cash["fees"]) == D("-0.02")
        assert D(book.report()["balances"]["USDT"]) == D("960.42")
    with ledger(tmp_path) as reopened:
        assert not reopened.claim_strategy_event("alpha", "config1", "@action:new", {"market": "receipt"})
        row = reopened.get("one")
        assert D(row["filled"]) == D("0.4")
        assert D(row["reservation"]) == D("60.12")
        cash = next(r for r in reopened.report()["strategy_cash"] if r["strategy"] == "alpha")
        assert D(cash["amount"]) == D("-39.58")
        assert D(cash["fees"]) == D("-0.02")
        assert reopened.apply_fill("part-2", "one", D("0.6"), D("100"))
        row = reopened.get("one")
        assert row["state"] == "FILLED"
        assert D(row["filled"]) == D("1")
        assert D(row["reservation"]) == D("0")
        cash = next(r for r in reopened.report()["strategy_cash"] if r["strategy"] == "alpha")
        assert D(cash["amount"]) == D("-99.58")
        assert D(cash["fees"]) == D("-0.02")
        assert D(reopened.report()["balances"]["USDT"]) == D("900.42")


def test_internal_netting_and_unallocated_funding_categories_survive_restart(tmp_path):
    rules = spec(ProductKind.LINEAR_PERPETUAL)
    with seeded(tmp_path) as book:
        admit(book, order(rules), rules=rules, margin_rate=D("0.1"))
        book.apply_fill("long", "one", 1, 100)
        admit(book, order(rules, identity="short", side=Side.SELL, price="110"), rules=rules,
              strategy="beta", margin_rate=D("0.1"))
        book.apply_fill("short-fill", "short", 1, 110)
        assert book.apply_funding("fund", rules.instrument.key, D("-1.25"), "USDT", {"period": "one"})
        by_strategy = {r["strategy"]: r for r in book.report()["strategy_cash"]}
        assert D(by_strategy["@physical_netting_adjustment"]["amount"]) == D("10")
        assert D(by_strategy["@physical_netting_adjustment"]["realized"]) == D("10")
        assert D(by_strategy["@unallocated_funding"]["amount"]) == D("-1.25")
        assert D(by_strategy["@unallocated_funding"]["funding"]) == D("-1.25")
        assert D(book.report()["balances"]["USDT"]) == D("1008.75")
    with ledger(tmp_path) as reopened:
        by_strategy = {r["strategy"]: r for r in reopened.report()["strategy_cash"]}
        assert D(by_strategy["@physical_netting_adjustment"]["amount"]) == D("10")
        assert D(by_strategy["@unallocated_funding"]["amount"]) == D("-1.25")
        # Internal credit writers stay available; only user strategy identities are fenced.
        assert reopened.apply_funding("fund-2", rules.instrument.key, D("-0.25"), "USDT", {"period": "two"})
        by_strategy = {r["strategy"]: r for r in reopened.report()["strategy_cash"]}
        assert D(by_strategy["@unallocated_funding"]["amount"]) == D("-1.50")
        for identity, internal in (("spoof-netting", "@physical_netting_adjustment"),
                                   ("spoof-funding", "@unallocated_funding")):
            with pytest.raises(ValueError, match="Reserved strategy namespace"):
                admit(reopened, order(rules, identity=identity), rules=rules,
                      strategy=internal, margin_rate=D("0.1"))
            with pytest.raises(ValueError, match="Reserved strategy namespace"):
                reopened.claim_strategy_event(internal, "config1", "market-event", {"market": "receipt"})
            assert reopened.get(identity) is None
        assert not reopened.has_strategy_dispatch("@physical_netting_adjustment")
        assert not reopened.has_strategy_dispatch("@unallocated_funding")


def test_opposite_strategies_gross_pending_risk_cancel_unknown_restart(tmp_path):
    rules = spec(ProductKind.LINEAR_PERPETUAL)
    with seeded(tmp_path) as book:
        admit(book, order(rules), rules=rules, margin_rate=D("0.1"))
        book.apply_fill("long", "one", 1, 100)
        admit(book, order(rules, identity="short", side=Side.SELL, price="110"), rules=rules,
              strategy="beta", margin_rate=D("0.1"))
        book.apply_fill("short-fill", "short", 1, 110)
        assert book.position("@physical", rules.instrument.key) == 0
        assert book.position("alpha", rules.instrument.key) == 1
        assert book.position("beta", rules.instrument.key) == -1
        assert D(book.report()["balances"]["USDT"]) == D("1010")
        adjustment = next(r for r in book.report()["strategy_cash"]
                          if r["strategy"] == "@physical_netting_adjustment")
        assert D(adjustment["amount"]) == D("10")
        with pytest.raises(ValueError, match="notional"):
            admit(book, order(rules, identity="third"), rules=rules, margin_rate=D("0.1"),
                  account_notional_limit=300)
        admit(book, order(rules, identity="pending"), rules=rules, strategy="gamma",
              margin_rate=D("0.1"), account_notional_limit=1000)
        book.request_cancel("pending", "gamma")
        book.cancel("pending")
        assert D(book.get("pending")["reservation"]) > 0
    with ledger(tmp_path) as reopened:
        assert reopened.get("pending")["state"] == "UNKNOWN"
        assert D(reopened.get("pending")["reservation"]) > 0
        reopened.cancel("pending", confirmed=True, evidence={"terminal": "paper cancel effective"})
        assert D(reopened.get("pending")["reservation"]) == 0
        assert reopened.position("@physical", rules.instrument.key) == 0
        assert reopened.position("alpha", rules.instrument.key) == 1
        assert reopened.position("beta", rules.instrument.key) == -1
