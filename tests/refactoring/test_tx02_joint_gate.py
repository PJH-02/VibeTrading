"""TX02 joint gate: existing paper books plus the preserved ETF journal exercise.

All expectations below are calculated by hand from the synthetic fixtures. The
product ledger, matcher, ownership fence and ETF journal are the current real
implementations; only test-local constructors mirror the existing test helpers.
"""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from vibetrading.etf_journal import EtfJournal
from vibetrading.execution_ledger import AccountScope
from core.models import AssetClass, BookUpdateType, InstrumentId, OrderBookEvent, OrderBookLevel, OrderDestination, OrderIntent, Side, TradeEvent, Venue
from vibetrading.paper_execution import PaperExecution
from vibetrading.portfolio_ledger import PortfolioLedger
from vibetrading.trading_contracts import ProductKind, ProductSpec, TradingMode


T = 1_800_000_000_000_000_000
SCOPE = AccountScope("bybit", "paper-a", "perpetual", "paper")
INSTRUMENT = InstrumentId(venue=Venue.BYBIT, asset_class=AssetClass.CRYPTO_PERP,
                          raw_symbol="BTCUSDT", std_symbol="BTCUSDT")


def open_ledger(root, name="account.sqlite", scope=SCOPE):
    return PortfolioLedger(root / "paper" / name, scope,
        owner_root=root, mode=TradingMode.PAPER, max_orders=100, max_fills=100,
        max_events=1000, max_document_bytes=1_000_000, max_db_bytes=16_000_000,
        max_wal_bytes=16_000_000)


def engine(ledger):
    return PaperExecution(ledger, entry_latency_ns=10, cancel_latency_ns=5,
                          max_open_orders=8, max_depth=8, max_receipts=100,
                          max_state_bytes=500_000, stale_after_ns=1000)


def rules():
    return ProductSpec(INSTRUMENT, ProductKind.LINEAR_PERPETUAL, D(10), D(1), D(1), D(1),
        "USDT", "USDT", "USDT", "net", "isolated", "synthetic fee",
        "explicit funding receipts", "synthetic isolated margin", D(".001"), D(".002"))


def intent(identity, price, quantity, side=Side.BUY):
    return OrderIntent(intent_id=identity, job_id="job", signal_id="signal-" + identity,
        destination=OrderDestination.REAL, instrument=INSTRUMENT, side=side,
        created_ts_ns=T, client_order_id="client-" + identity,
        quantity=D(quantity), limit_price=D(price))


def submit(matcher, strategy, identity, price, quantity, now=T, *, side=Side.BUY,
           account_notional_limit=D(100000)):
    return matcher.submit(intent(identity, price, quantity, side), strategy,
        {"decision": identity}, T + 1000, rules(), now, reference_price=D(100),
        margin_rate=D(".1"), funding_buffer=D(0),
        account_notional_limit=account_notional_limit,
        strategy_notional_limit=D(100000), account_capital_limit=D(100000),
        strategy_capital_limit=D(100000))


def book(now=T, *, bid=99, ask=101, bid_qty=5, ask_qty=5, sequence=1):
    return OrderBookEvent(instrument=INSTRUMENT, venue=Venue.BYBIT, provider="synthetic",
        event_id="book-" + str(now), receive_ts_ns=now, wallclock_ts_ns=now,
        source_ts_ns=now, sequence=sequence, sync_generation=1, depth=1,
        bids=[OrderBookLevel(price=D(bid), quantity=D(bid_qty))],
        asks=[OrderBookLevel(price=D(ask), quantity=D(ask_qty))])


def trade(now, *, quantity, price, side):
    return TradeEvent(instrument=INSTRUMENT, venue=Venue.BYBIT, provider="synthetic",
        event_id="trade-" + str(now), receive_ts_ns=now, wallclock_ts_ns=now,
        source_ts_ns=now, price=D(price), quantity=D(quantity), aggressor_side=side)


def reserved(ledger, identity):
    return D(ledger.get(identity)["reservation"])


def test_joint_paper_owner_books_cancel_restart_and_physical_fence(tmp_path):
    ledger = open_ledger(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"fixture": "joint paper gate"})
        matcher = engine(ledger)
        matcher.on_book(book())

        submit(matcher, "alpha", "alpha-buy", 99, 2)
        submit(matcher, "beta", "beta-sell", 101, 2, side=Side.SELL)

        # Linear reserves: 2 * (99*10*.1 + 99*10*.002) and
        # 2 * (101*10*.1 + 101*10*.002).
        assert reserved(ledger, "alpha-buy") == D("201.96")
        assert reserved(ledger, "beta-sell") == D("206.04")
        assert D(ledger.report()["reserved"]["USDT"]) == D("408")
        initial_reservations = D("408")

        matcher.advance(T + 10)
        assert matcher.report()["open_orders"]["alpha-buy"]["active"]
        assert matcher.report()["open_orders"]["beta-sell"]["active"]

        assert matcher.on_trade(trade(T + 11, quantity=6, price=99, side=Side.SELL))
        assert D(ledger.get("alpha-buy")["filled"]) == 1
        assert D(ledger.get("beta-sell")["filled"]) == 0
        assert matcher.on_trade(trade(T + 12, quantity=6, price=101, side=Side.BUY))
        assert D(ledger.get("alpha-buy")["filled"]) == 1
        assert D(ledger.get("beta-sell")["filled"]) == 1

        # One maker fill on each side: physical nets flat, the 20.00 physical
        # realized gain is reduced by .99 and 1.01 of maker fees.
        report = ledger.report()
        assert ledger.position("@physical", INSTRUMENT.key) == 0
        assert ledger.position("alpha", INSTRUMENT.key) == 1
        assert ledger.position("beta", INSTRUMENT.key) == -1
        assert D(report["balances"]["USDT"]) == D("100018")
        assert D(report["reserved"]["USDT"]) == D("204")
        assert reserved(ledger, "alpha-buy") == D("100.98")
        assert reserved(ledger, "beta-sell") == D("103.02")
        assert D(report["simulated_margin"]) == D("200")
        by_strategy = {row["strategy"]: row for row in report["strategy_cash"]}
        # The fees column records the positive fee charged; the strategy amount
        # is physical/virtual PnL net of that fee.
        assert D(by_strategy["alpha"]["fees"]) == D("0.99")
        assert D(by_strategy["alpha"]["amount"]) == D("-0.99")
        assert D(by_strategy["beta"]["fees"]) == D("1.01")
        assert D(by_strategy["beta"]["amount"]) == D("-1.01")
        assert D(by_strategy["@physical_netting_adjustment"]["amount"]) == D("20")

        # Virtual long/short plus both pending leaves remain gross risk even
        # though the physical account is flat. Current gross = 4000, so a new
        # 990 notional order cannot pass an explicit 4000 account limit.
        before = matcher.report()
        with pytest.raises(ValueError, match="notional"):
            submit(matcher, "gamma", "gamma-probe", 99, 1, T + 12,
                   account_notional_limit=D(4000))
        assert matcher.report() == before
        assert ledger.get("gamma-probe") is None

        # The active physical book owns this account/product path. Neither a
        # different product label on a new DB nor a same-scope DB alias may
        # create a competitor owner or leave a competitor database behind.
        for name, alias in (("same-scope.sqlite", SCOPE),
                            ("product-alias.sqlite", replace(SCOPE, product="cash"))):
            with pytest.raises(RuntimeError, match="another process"):
                open_ledger(tmp_path, name, alias)
            assert not (tmp_path / "paper" / name).exists()

        # Wrong-strategy cancellation rolls back without touching alpha.
        with pytest.raises(ValueError, match="different strategy"):
            matcher.cancel("alpha-buy", "beta", T + 13)
        assert ledger.get("alpha-buy")["state"] == "OPEN"
        assert reserved(ledger, "alpha-buy") == D("100.98")

        # Alpha cancel is effective at T+18; until then its reservation stays.
        assert matcher.cancel("alpha-buy", "alpha", T + 13)
        assert ledger.get("alpha-buy")["state"] == "CANCEL_PENDING"
        assert reserved(ledger, "alpha-buy") == D("100.98")
        assert reserved(ledger, "beta-sell") == D("103.02")
        matcher.advance(T + 18)
        assert ledger.get("alpha-buy")["state"] == "CANCELED"
        assert reserved(ledger, "alpha-buy") == 0
        assert reserved(ledger, "beta-sell") == D("103.02")
        assert D(ledger.report()["reserved"]["USDT"]) == D("103.02")
        assert set(matcher.report()["open_orders"]) == {"beta-sell"}

        # A repeated cancel cannot release the same reservation twice.
        assert matcher.cancel("alpha-buy", "alpha", T + 18) is False
        assert reserved(ledger, "alpha-buy") == 0
        assert reserved(ledger, "beta-sell") == D("103.02")
        assert initial_reservations - D(ledger.report()["reserved"]["USDT"]) == D("304.98")

        beta_before = ledger.get("beta-sell")
        beta_leaves = D(matcher.report()["open_orders"]["beta-sell"]["leaves"])
        cash_before = D(ledger.report()["balances"]["USDT"])
        ledger.close()

        ledger = open_ledger(tmp_path)
        matcher = engine(ledger)
        beta = ledger.get("beta-sell")
        assert beta["state"] == "UNKNOWN"
        assert beta["strategy"] == "beta"
        assert beta["instrument"] == INSTRUMENT.key
        assert beta["side"] == "sell"
        assert D(beta["filled"]) == D(beta_before["filled"]) == 1
        assert D(beta["reservation"]) == D("103.02")
        assert D(matcher.report()["open_orders"]["beta-sell"]["leaves"]) == beta_leaves == 1
        assert matcher.report()["open_orders"]["beta-sell"]["strategy"] == "beta"
        assert ledger.position("alpha", INSTRUMENT.key) == 1
        assert ledger.position("beta", INSTRUMENT.key) == -1

        # Receipt dedup survives restart: replaying both original trades cannot
        # reapply a fill or a fee.
        fills_before = matcher.report()["fills"]
        assert matcher.on_trade(trade(T + 11, quantity=6, price=99, side=Side.SELL)) is False
        assert matcher.on_trade(trade(T + 12, quantity=6, price=101, side=Side.BUY)) is False
        assert matcher.report()["fills"] == fills_before
        assert D(ledger.report()["balances"]["USDT"]) == cash_before
        assert reserved(ledger, "beta-sell") == D("103.02")

        # A fresh one-lot BUY trade completes the remaining beta leave once.
        assert matcher.on_trade(trade(T + 20, quantity=1, price=101, side=Side.BUY))
        assert D(ledger.get("beta-sell")["filled"]) == 2
        assert ledger.get("beta-sell")["state"] == "FILLED"
        report = ledger.report()
        assert ledger.position("@physical", INSTRUMENT.key) == -1
        assert ledger.position("alpha", INSTRUMENT.key) == 1
        assert ledger.position("beta", INSTRUMENT.key) == -2
        assert D(report["balances"]["USDT"]) == D("100016.99")
        assert D(report["reserved"]["USDT"]) == 0
        assert matcher.report()["open_orders"] == {}
        assert all(D(row["reservation"]) == 0 for row in report["orders"])
        by_strategy = {row["strategy"]: row for row in report["strategy_cash"]}
        assert D(by_strategy["alpha"]["fees"]) == D("0.99")
        assert D(by_strategy["alpha"]["amount"]) == D("-0.99")
        assert D(by_strategy["beta"]["fees"]) == D("2.02")
        assert D(by_strategy["beta"]["amount"]) == D("-2.02")
        assert D(by_strategy["@physical_netting_adjustment"]["amount"]) == D("20")
        # Every unit of the original 408 reservation was released exactly once.
        assert initial_reservations - D(report["reserved"]["USDT"]) == initial_reservations
    finally:
        ledger.close()


def test_preserved_etf_journal_books_cancel_and_restart(tmp_path):
    path = tmp_path / "etf" / "journal.sqlite3"
    journal = EtfJournal(path)
    try:
        journal.initialize(D(6000), "offline-account")
        for identity, strategy in (("c3-order", "C3"), ("kp-order", "KP")):
            value = OrderIntent(intent_id=identity, job_id=strategy, signal_id="signal-" + identity,
                client_order_id="client-" + identity, destination=OrderDestination.REAL,
                instrument=InstrumentId(venue=Venue.KIS, asset_class=AssetClass.ETF,
                                        raw_symbol="069500", std_symbol="069500"),
                side=Side.BUY, quantity=D(10), limit_price=D(100),
                metadata={"account_scope": "mock"})
            assert journal.stage(value, "20261021", 1, None)

        assert journal.book("C3")["cash"] == D(2000)
        assert journal.book("KP")["cash"] == D(2000)
        assert journal.book("KD")["cash"] == D(2000)
        assert journal.book("C3")["reserved"] == D(1005)
        assert journal.book("KP")["reserved"] == D(1005)

        # Book cash = 2000 - fill notional - 0.015% conservative fee.
        journal.apply_status("c3-order",
            {"tot_ccld_qty": "4", "tot_ccld_amt": "396", "rmn_qty": "6"})
        c3 = journal.book("C3")
        assert c3["cash"] == D("1603.9406")
        assert c3["positions"] == {"069500": 4}
        assert c3["reserved"] == D(603)
        assert journal.book("KP")["cash"] == D(2000)
        assert journal.book("KP")["reserved"] == D(1005)

        journal.apply_status("kp-order",
            {"tot_ccld_qty": "3", "tot_ccld_amt": "300", "rmn_qty": "7"})
        kp = journal.book("KP")
        assert kp["cash"] == D("1699.955")
        assert kp["positions"] == {"069500": 3}
        assert kp["reserved"] == D("703.5")
        assert journal.book("C3")["cash"] == D("1603.9406")
        assert journal.book("C3")["reserved"] == D(603)

        # The confirmed C3 cancel releases exactly its own remaining reserve;
        # the independent KP reservation is untouched.
        journal.apply_status("c3-order",
            {"tot_ccld_qty": "4", "tot_ccld_amt": "396", "rmn_qty": "6", "cncl_yn": "Y"})
        c3 = journal.book("C3")
        assert c3["cash"] == D("1603.9406")
        assert c3["positions"] == {"069500": 4}
        assert c3["reserved"] == 0
        assert journal.book("KP")["cash"] == D("1699.955")
        assert journal.book("KP")["reserved"] == D("703.5")

        before_books = {name: journal.book(name) for name in ("C3", "KP", "KD")}
        before_notifications = [dict(row) for row in journal.db.execute(
            "SELECT * FROM notifications ORDER BY id")]
        before_orders = [dict(row) for row in journal.db.execute(
            "SELECT * FROM orders ORDER BY intent_id")]
        assert [row["id"] for row in before_notifications] == [
            "c3-order:filled:4", "kp-order:filled:3"]
        journal.db.close()

        journal = EtfJournal(path)
        # Same-account restart must not refresh initialized thirds once orders
        # exist, and duplicate cumulative status facts must be inert.
        journal.initialize(D(6000), "offline-account")
        journal.apply_status("c3-order",
            {"tot_ccld_qty": "4", "tot_ccld_amt": "396", "rmn_qty": "6", "cncl_yn": "Y"})
        journal.apply_status("kp-order",
            {"tot_ccld_qty": "3", "tot_ccld_amt": "300", "rmn_qty": "7"})

        assert {name: journal.book(name) for name in ("C3", "KP", "KD")} == before_books
        assert [dict(row) for row in journal.db.execute(
            "SELECT * FROM notifications ORDER BY id")] == before_notifications
        assert [dict(row) for row in journal.db.execute(
            "SELECT * FROM orders ORDER BY intent_id")] == before_orders
    finally:
        journal.db.close()
