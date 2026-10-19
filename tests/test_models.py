from decimal import Decimal

from vibetrading.models import AssetClass, InstrumentId, Side, TradeEvent, Venue


def test_trade_event_id_is_stable_for_duplicate_delivery() -> None:
    instrument = InstrumentId(
        venue=Venue.BINANCE,
        asset_class=AssetClass.CRYPTO_SPOT,
        raw_symbol="BTCUSDT",
        std_symbol="BTCUSDT",
        currency="USDT",
    )
    first = TradeEvent(
        instrument=instrument,
        venue=Venue.BINANCE,
        provider="binance",
        source_ts_ns=1,
        receive_ts_ns=2,
        sequence=123,
        price=Decimal("100"),
        quantity=Decimal("0.1"),
        aggressor_side=Side.BUY,
        trade_id="abc",
    )
    second = TradeEvent(
        instrument=instrument,
        venue=Venue.BINANCE,
        provider="binance",
        source_ts_ns=1,
        receive_ts_ns=999,
        sequence=123,
        price=Decimal("100"),
        quantity=Decimal("0.1"),
        aggressor_side=Side.BUY,
        trade_id="abc",
    )
    assert first.event_id == second.event_id
    assert first.trading_date == "1970-01-01"
