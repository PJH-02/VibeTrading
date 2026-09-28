from __future__ import annotations

import asyncio
import json
import logging
import os
from decimal import Decimal
from typing import Any

import aiohttp
import websockets

from vibetrading.bus import EventBus
from vibetrading.models import (
    AssetClass,
    BookUpdateType,
    InstrumentId,
    OrderBookEvent,
    OrderBookLevel,
    Side,
    TradeEvent,
    Venue,
    utc_now_ns,
)
from vibetrading.providers.coingecko import CoinGeckoClient

logger = logging.getLogger(__name__)


class BinanceCatalog:
    rest_url = "https://api.binance.com"

    async def exchange_symbols(self) -> set[str]:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.rest_url}/api/v3/exchangeInfo") as response:
                response.raise_for_status()
                payload = await response.json()
        symbols: set[str] = set()
        for item in payload.get("symbols", []):
            if item.get("status") == "TRADING":
                symbols.add(str(item.get("symbol") or "").upper())
        return symbols

    async def top_usdt_pairs(self, limit: int = 20) -> list[str]:
        coins = await CoinGeckoClient().top_market_cap_symbols(limit=limit * 2)
        available = await self.exchange_symbols()
        pairs: list[str] = []
        for coin in coins:
            pair = f"{coin.symbol.upper()}USDT"
            if pair in available and pair not in pairs:
                pairs.append(pair)
            if len(pairs) >= limit:
                break
        return pairs


class BinanceCollector:
    name = "binance"

    def __init__(self, symbols: list[str] | None = None) -> None:
        self.symbols = [symbol.upper() for symbol in (symbols or [])]

    async def resolve_symbols(self) -> list[str]:
        if self.symbols:
            return self.symbols
        limit = max(1, int(os.getenv("BINANCE_TOP_N", "5")))
        return await BinanceCatalog().top_usdt_pairs(limit=limit)

    async def run(self, bus: EventBus, duration_sec: float | None = None) -> None:
        symbols = await self.resolve_symbols()
        if not symbols:
            raise RuntimeError("Binance universe is empty")
        streams = []
        for symbol in symbols:
            lower = symbol.lower()
            streams.append(f"{lower}@trade")
            streams.append(f"{lower}@depth10@100ms")
        url = "wss://stream.binance.com:9443/stream?streams=" + "/".join(streams)
        deadline = asyncio.get_running_loop().time() + duration_sec if duration_sec else None
        async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=512) as ws:
            logger.info("Binance collector subscribed to %d symbols", len(symbols))
            while True:
                if deadline and asyncio.get_running_loop().time() >= deadline:
                    break
                timeout = max(0.1, deadline - asyncio.get_running_loop().time()) if deadline else None
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    break
                event = self._parse_message(raw)
                if event is None:
                    continue
                await bus.publish_market_event(event)

    def _parse_message(self, raw: str | bytes) -> TradeEvent | OrderBookEvent | None:
        payload = json.loads(raw)
        data: dict[str, Any] = payload.get("data") or payload
        stream = str(payload.get("stream") or "")
        event_type = data.get("e")
        if event_type == "trade":
            return self._parse_trade(data)
        if "lastUpdateId" in data and "bids" in data and "asks" in data:
            symbol = str(data.get("s") or stream.split("@", 1)[0]).upper()
            return self._parse_depth(data, symbol=symbol)
        return None

    @staticmethod
    def _instrument(symbol: str) -> InstrumentId:
        symbol = symbol.upper()
        return InstrumentId(
            venue=Venue.BINANCE,
            asset_class=AssetClass.CRYPTO_SPOT,
            raw_symbol=symbol,
            std_symbol=symbol,
            currency="USDT",
            exchange="BINANCE",
        )

    def _parse_trade(self, data: dict[str, Any]) -> TradeEvent:
        symbol = str(data["s"]).upper()
        source_ts_ns = int(data.get("T") or data.get("E") or 0) * 1_000_000
        buyer_is_maker = bool(data.get("m"))
        return TradeEvent(
            instrument=self._instrument(symbol),
            venue=Venue.BINANCE,
            provider=self.name,
            source_ts_ns=source_ts_ns or utc_now_ns(),
            receive_ts_ns=utc_now_ns(),
            session="24x7",
            sequence=int(data.get("t") or 0),
            price=Decimal(str(data.get("p", "0"))),
            quantity=Decimal(str(data.get("q", "0"))),
            aggressor_side=Side.SELL if buyer_is_maker else Side.BUY,
            trade_id=str(data.get("t") or ""),
        )

    def _parse_depth(self, data: dict[str, Any], *, symbol: str | None = None) -> OrderBookEvent:
        symbol = str(symbol or data["s"]).upper()
        source_ts_ns = int(data.get("E") or 0) * 1_000_000
        bids = [OrderBookLevel(price=Decimal(str(price)), quantity=Decimal(str(qty))) for price, qty in data.get("bids", [])[:10]]
        asks = [OrderBookLevel(price=Decimal(str(price)), quantity=Decimal(str(qty))) for price, qty in data.get("asks", [])[:10]]
        return OrderBookEvent(
            instrument=self._instrument(symbol),
            venue=Venue.BINANCE,
            provider=self.name,
            source_ts_ns=source_ts_ns or utc_now_ns(),
            receive_ts_ns=utc_now_ns(),
            session="24x7",
            sequence=int(data.get("lastUpdateId") or 0),
            update_type=BookUpdateType.SNAPSHOT,
            depth=max(len(bids), len(asks)),
            bids=bids,
            asks=asks,
            is_full_depth=len(bids) >= 10 and len(asks) >= 10,
        )
