"""Local scripted paper executions; no collectors, credentials, or venue clients."""
from decimal import Decimal as D

import pytest

from vibetrading.execution_ledger import AccountScope
from core.models import AssetClass, BookUpdateType, InstrumentId, OrderBookEvent, OrderBookLevel, OrderDestination, OrderIntent, Side, TradeEvent, Venue
from vibetrading.paper_execution import PaperExecution
from vibetrading.portfolio_ledger import PortfolioLedger
from vibetrading.trading_contracts import ProductKind, ProductSpec, TradingMode


T = 1_800_000_000_000_000_000
INSTRUMENT = InstrumentId(venue=Venue.BYBIT, asset_class=AssetClass.CRYPTO_PERP,
                          raw_symbol="BTCUSDT", std_symbol="BTCUSDT")


def open_ledger(root):
    return PortfolioLedger(root / "paper" / "account.sqlite",
        AccountScope("bybit", "paper-a", "perpetual", "paper"), owner_root=root,
        mode=TradingMode.PAPER, max_orders=100, max_fills=100, max_events=1000,
        max_document_bytes=1_000_000, max_db_bytes=16_000_000, max_wal_bytes=16_000_000)


def engine(ledger, **changes):
    options = dict(entry_latency_ns=10, cancel_latency_ns=5, max_open_orders=8,
                   max_depth=8, max_receipts=100, max_state_bytes=500_000, stale_after_ns=1000)
    return PaperExecution(ledger, **{**options, **changes})


def rules():
    return ProductSpec(INSTRUMENT, ProductKind.LINEAR_PERPETUAL, D(10), D(1), D(1), D(1),
        "USDT", "USDT", "USDT", "net", "isolated", "synthetic fee", "explicit funding receipts",
        "synthetic isolated margin", D(".001"), D(".002"))


def intent(identity="one", price=99, quantity=1, side=Side.BUY):
    return OrderIntent(intent_id=identity, job_id="job", signal_id="signal-"+identity,
        destination=OrderDestination.REAL, instrument=INSTRUMENT, side=side,
        created_ts_ns=T, client_order_id="client-"+identity, quantity=D(quantity), limit_price=D(price))


def submit(matcher, identity="one", price=99, quantity=1, now=T, **changes):
    return matcher.submit(intent(identity, price, quantity), "strategy", {"decision": identity},
        T+1000, rules(), now, reference_price=D(100), margin_rate=D(".1"), funding_buffer=D(0),
        account_notional_limit=D(100000), strategy_notional_limit=D(100000),
        account_capital_limit=D(100000), strategy_capital_limit=D(100000), **changes)


def book(now=T, *, bid=99, ask=101, bid_qty=5, ask_qty=2, sequence=1, identity=None):
    return OrderBookEvent(instrument=INSTRUMENT, venue=Venue.BYBIT, provider="synthetic",
        event_id=identity or "book-"+str(now), receive_ts_ns=now, wallclock_ts_ns=now,
        source_ts_ns=now, sequence=sequence, sync_generation=1, depth=1,
        bids=[OrderBookLevel(price=D(bid), quantity=D(bid_qty))],
        asks=[OrderBookLevel(price=D(ask), quantity=D(ask_qty))])


def trade(now=T+11, *, quantity=6, price=99, identity=None):
    return TradeEvent(instrument=INSTRUMENT, venue=Venue.BYBIT, provider="synthetic",
        event_id=identity or "trade-"+str(now), receive_ts_ns=now, wallclock_ts_ns=now,
        source_ts_ns=now, price=D(price), quantity=D(quantity), aggressor_side=Side.SELL)


@pytest.fixture
def local(tmp_path):
    ledger = open_ledger(tmp_path)
    ledger.seed_balance("USDT", D(100000), {"fixture": "paper only"})
    matcher = engine(ledger)
    matcher.on_book(book())
    yield ledger, matcher
    ledger.close()


def test_arrival_uses_past_book_and_book_touch_never_fills(local):
    ledger, matcher = local
    submit(matcher)
    matcher.on_book(book(T+20, bid=97, ask=98, sequence=2))
    assert "one" in matcher.report()["open_orders"]
    assert matcher.report()["fills"] == 0
    assert ledger.get("one")["filled"] == "0"


def test_maker_entire_queue_and_shared_trade_volume_and_multiplier_fee(local):
    ledger, matcher = local
    submit(matcher, "one", quantity=2)
    submit(matcher, "two", quantity=2)
    matcher.advance(T+10)
    matcher.on_trade(trade(quantity=6))
    assert D(ledger.get("one")["filled"]) == 1
    assert D(ledger.get("two")["filled"]) == 0
    assert matcher.report()["fills"] == 1
    assert ledger.position("strategy", INSTRUMENT.key) == 1
    assert D(ledger.report()["balances"]["USDT"]) == D("99999.01")


def test_taker_uses_observed_depth_limit_and_never_reuses_same_book(local):
    ledger, matcher = local
    submit(matcher, "one", price=101, quantity=4, post_only=False, time_in_force="IOC")
    matcher.advance(T+10)
    assert D(ledger.get("one")["filled"]) == 2
    assert ledger.get("one")["state"] == "CANCELED"
    submit(matcher, "two", price=101, now=T+10, post_only=False, time_in_force="IOC")
    matcher.advance(T+20)
    assert D(ledger.get("two")["filled"]) == 0
    assert D(ledger.report()["balances"]["USDT"]) == D("99995.96")


def test_restart_retains_partial_fill_cancel_time_and_duplicate_receipt(tmp_path):
    ledger = open_ledger(tmp_path)
    ledger.seed_balance("USDT", D(100000), {"fixture": "paper only"})
    matcher = engine(ledger)
    matcher.on_book(book())
    submit(matcher, quantity=2)
    matcher.cancel("one", "strategy", T+11)
    evidence = trade(T+15, quantity=6)
    matcher.on_trade(evidence)
    assert D(ledger.get("one")["filled"]) == 1
    ledger.close()
    ledger = open_ledger(tmp_path)
    try:
        matcher = engine(ledger)
        assert matcher.on_trade(evidence) is False
        assert D(ledger.get("one")["filled"]) == 1
        matcher.advance(T+16)
        assert ledger.get("one")["state"] == "CANCELED"
        assert matcher.report()["open_orders"] == {}
        assert ledger.position("strategy", INSTRUMENT.key) == 1
    finally:
        ledger.close()


def test_commit_failure_rolls_back_fill_portfolio_receipt_and_clock(local, monkeypatch):
    ledger, matcher = local
    submit(matcher)
    matcher.advance(T+10)
    before = matcher.report()
    database = ledger._db
    class FailingCommit:
        def __getattr__(self, name):
            return getattr(database, name)
        def execute(self, sql, *args):
            if sql == "COMMIT":
                raise OSError("injected durable commit failure")
            return database.execute(sql, *args)
    monkeypatch.setattr(ledger, "_db", FailingCommit())
    evidence = trade()
    with pytest.raises(OSError, match="injected"):
        matcher.on_trade(evidence)
    assert matcher.report() == before
    assert D(ledger.get("one")["filled"]) == 0
    assert ledger.position("strategy", INSTRUMENT.key) == 0
    monkeypatch.setattr(ledger, "_db", database)
    assert matcher.on_trade(evidence)
    assert D(ledger.get("one")["filled"]) == 1


def test_outer_transaction_cannot_publish_matching_state_before_commit(local):
    ledger, matcher = local
    submit(matcher)
    matcher.advance(T+10)
    previous = matcher.report()
    with pytest.raises(RuntimeError, match="outer transaction"):
        with ledger.transaction():
            matcher.on_trade(trade())
    assert matcher.report() == previous
    assert D(ledger.get("one")["filled"]) == 0
    assert ledger.position("strategy", INSTRUMENT.key) == 0
    matcher.on_trade(trade())
    assert D(ledger.get("one")["filled"]) == 1


def test_gap_blocks_new_orders_and_preserves_reservation_until_effective_cancel(local):
    ledger, matcher = local
    submit(matcher)
    matcher.advance(T+10)
    reservation = D(ledger.get("one")["reservation"])
    matcher.on_gap(INSTRUMENT.key, T+11, "scripted sequence gap")
    assert D(ledger.get("one")["reservation"]) == reservation
    with pytest.raises(ValueError, match="blocked"):
        submit(matcher, "two", now=T+12)
    matcher.on_trade(trade(T+12, quantity=100))
    assert D(ledger.get("one")["filled"]) == 0
    matcher.advance(T+16)
    assert D(ledger.get("one")["reservation"]) == 0
    with pytest.raises(ValueError, match="new snapshot generation"):
        matcher.on_book(book(T+17, sequence=2))
    matcher.on_book(book(T+17, sequence=2).model_copy(update={"sync_generation": 2}))
    submit(matcher, "two", now=T+17)


def test_capacity_and_raw_delta_rejections_leave_prior_state_unchanged(local):
    ledger, matcher = local
    previous = matcher.report()
    with pytest.raises(ValueError, match="raw delta"):
        matcher.on_book(book(T+1).model_copy(update={"update_type": BookUpdateType.DELTA}))
    assert matcher.report() == previous
    with pytest.raises(ValueError, match="configuration"):
        engine(ledger, max_receipts=1)


def test_receipt_budget_does_not_discard_durable_dedup(tmp_path):
    ledger = open_ledger(tmp_path)
    try:
        matcher = engine(ledger, max_receipts=1)
        observed = book()
        matcher.on_book(observed)
        with pytest.raises(ValueError, match="receipt capacity"):
            matcher.on_trade(trade())
        assert matcher.on_book(observed) is False
        assert matcher.report()["receipt_count"] == 1
    finally:
        ledger.close()


def test_older_snapshot_cannot_replenish_consumed_depth(local):
    ledger, matcher = local
    matcher.on_book(book(T+1, sequence=2))
    submit(matcher, "one", price=101, quantity=2, now=T+1, post_only=False, time_in_force="IOC")
    matcher.advance(T+11)
    previous = matcher.report()
    with pytest.raises(ValueError, match="sequence regression"):
        matcher.on_book(book(T+12, sequence=1))
    assert matcher.report() == previous
    submit(matcher, "two", price=101, now=T+12, post_only=False, time_in_force="IOC")
    matcher.advance(T+22)
    assert D(ledger.get("two")["filled"]) == 0


@pytest.mark.parametrize("sequence", [None, "opaque-a"])
def test_unordered_snapshot_does_not_invent_depth_replenishment(tmp_path, sequence):
    ledger = open_ledger(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"fixture": "paper only"})
        matcher = engine(ledger)
        matcher.on_book(book(sequence=sequence))
        submit(matcher, "one", price=101, quantity=2, post_only=False, time_in_force="IOC")
        matcher.advance(T+10)
        matcher.on_book(book(T+11, sequence=None if sequence is None else "opaque-b"))
        submit(matcher, "two", price=101, now=T+11, post_only=False, time_in_force="IOC")
        matcher.advance(T+21)
        assert D(ledger.get("two")["filled"]) == 0
    finally:
        ledger.close()
