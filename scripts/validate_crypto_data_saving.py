"""
Validate crypto candle saving behavior.

Checks that:
1) Open candles are not persisted
2) Closed candles are persisted to QuestDB line protocol writer
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from shared.models import Candle, Market
from services.data_feed.crypto_feed import CryptoDataFeed
import services.data_feed.crypto_feed as crypto_feed_module


class FakeQuestDB:
    """Minimal QuestDB stub for persistence verification."""

    def __init__(self) -> None:
        self.writes: list[dict] = []

    def write_line(self, table: str, tags: dict, fields: dict, timestamp_ns: int) -> None:
        self.writes.append(
            {
                "table": table,
                "tags": tags,
                "fields": fields,
                "timestamp_ns": timestamp_ns,
            }
        )


async def main() -> int:
    fake_db = FakeQuestDB()
    original_get_questdb = crypto_feed_module.get_questdb
    crypto_feed_module.get_questdb = lambda: fake_db

    try:
        feed = CryptoDataFeed(exchange="binance")

        open_candle = Candle(
            market=Market.CRYPTO,
            symbol="BTCUSDT",
            timestamp=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
            open=Decimal("100000"),
            high=Decimal("101000"),
            low=Decimal("99000"),
            close=Decimal("100500"),
            volume=Decimal("12.34"),
            quote_volume=Decimal("1234567.89"),
            trades=321,
            interval="1m",
            is_closed=False,
        )
        closed_candle = open_candle.model_copy(update={"is_closed": True})

        await feed._persist_candle(open_candle)
        if fake_db.writes:
            print("FAIL: open candle was persisted")
            return 1

        await feed._persist_candle(closed_candle)
        if len(fake_db.writes) != 1:
            print("FAIL: closed candle was not persisted exactly once")
            return 1

        saved = fake_db.writes[0]
        if saved["table"] != "candles":
            print("FAIL: unexpected table name")
            return 1
        if saved["tags"].get("symbol") != "BTCUSDT":
            print("FAIL: symbol tag mismatch")
            return 1
        if saved["tags"].get("market") != "crypto":
            print("FAIL: market tag mismatch")
            return 1

        print("PASS: crypto candle saving validated")
        return 0
    finally:
        crypto_feed_module.get_questdb = original_get_questdb


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
