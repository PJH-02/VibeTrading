import asyncio
from datetime import datetime, timedelta, timezone

from vibetrading.bus import InMemoryEventBus
from vibetrading.market_policy import MarketDataPolicyBus
from vibetrading.providers.toss import TossCollector, parse_toss_symbols


KST = timezone(timedelta(hours=9))


def test_spy_qqq_subscribe_on_same_toss_websocket() -> None:
    collector = TossCollector(parse_toss_symbols("US:SPY,US:QQQ"), publish_rest_snapshot=False)
    declarations = collector._ws_declarations()
    assert {item["type"]: item["codes"] for item in declarations if "type" in item} == {
        "trade:us": ["QQQ", "SPY"],
        "orderbook:us": ["QQQ", "SPY"],
    }


def test_toss_candle_is_published_only_after_close_and_deduplicates() -> None:
    collector = TossCollector(parse_toss_symbols("US:QQQ"), publish_rest_snapshot=False)
    row = {
        "timestamp": "2026-09-22T11:59:00+09:00",
        "openPrice": "600.1", "highPrice": "600.5", "lowPrice": "600.0",
        "closePrice": "600.4", "volume": "123",
    }
    close_ns = int(datetime(2026, 9, 22, 12, 0, tzinfo=KST).timestamp() * 1_000_000_000)
    assert collector.candle_event_from_payload("QQQ", row, receive_ts_ns=close_ns + 1_000_000_000) is None
    event = collector.candle_event_from_payload("QQQ", row, receive_ts_ns=close_ns + 3_000_000_000)
    assert event is not None
    assert event.instrument.std_symbol == "TOSS:US:QQQ"
    assert event.source_ts_ns == close_ns
    assert (event.receive_ts_ns - event.source_ts_ns) // 1_000_000 == 3000
    assert event.close_ts_ns - event.open_ts_ns == int(timedelta(minutes=1).total_seconds() * 1_000_000_000)
    assert event.event_id == collector.candle_event_from_payload(
        "QQQ", row, receive_ts_ns=close_ns + 30_000_000_000
    ).event_id
    assert MarketDataPolicyBus(object())._allowed(event)
    bus = InMemoryEventBus()
    asyncio.run(bus.publish_market_event(event))
    assert len(bus.messages) == 1
    assert bus.messages[0].subject.startswith("md.bar_1m.toss.")
    assert collector.candle_event_from_payload(
        "QQQ", row, receive_ts_ns=close_ns + 2 * 24 * 60 * 60 * 1_000_000_000
    ) is None
