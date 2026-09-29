from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
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


logger = logging.getLogger(__name__)


def load_hyperliquid_tokenized_symbols(config_dir: Path) -> list[str]:
    """Compatibility shim: external TOML is no longer a subscription source."""
    del config_dir
    return []


@dataclass(frozen=True)
class HyperliquidAsset:
    name: str
    asset_class: str
    index: int | None = None


class HyperliquidCatalog:
    info_url = "https://api.hyperliquid.xyz/info"

    async def assets(self) -> list[HyperliquidAsset]:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            perps = await self._post(session, {"type": "meta"})
            spots = await self._post(session, {"type": "spotMeta"})
        out: list[HyperliquidAsset] = []
        for index, item in enumerate(perps.get("universe", [])):
            out.append(HyperliquidAsset(name=str(item.get("name") or ""), asset_class="crypto_perp", index=index))
        for index, item in enumerate(spots.get("universe", [])):
            name = str(item.get("name") or item.get("tokens") or "")
            out.append(HyperliquidAsset(name=name, asset_class="crypto_spot", index=index))
        return [asset for asset in out if asset.name]

    @staticmethod
    async def _post(session: aiohttp.ClientSession, payload: dict[str, Any]) -> dict[str, Any]:
        async with session.post(HyperliquidCatalog.info_url, json=payload) as response:
            response.raise_for_status()
            return await response.json()


class HyperliquidCollector:
    name = "hyperliquid"

    def __init__(self, symbols: list[str] | None = None, tokenized_symbols: list[str] | None = None) -> None:
        self.symbols = symbols or []
        self.tokenized_symbols = {symbol.upper() for symbol in (tokenized_symbols or [])}

    async def run(self, bus: EventBus, duration_sec: float | None = None) -> None:
        symbols = self.symbols or await self._default_symbols()
        if not symbols:
            raise RuntimeError("Hyperliquid universe is empty")
        deadline = asyncio.get_running_loop().time() + duration_sec if duration_sec else None
        url = "wss://api.hyperliquid.xyz/ws"
        async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=512) as ws:
            for symbol in symbols:
                await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": symbol}}))
                await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": symbol}}))
            logger.info("Hyperliquid collector subscribed to %d symbols", len(symbols))
            while True:
                if deadline and asyncio.get_running_loop().time() >= deadline:
                    break
                timeout = max(0.1, deadline - asyncio.get_running_loop().time()) if deadline else None
                try:
                    payload = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                except asyncio.TimeoutError:
                    break
                except websockets.exceptions.ConnectionClosed as exc:
                    raise RuntimeError(f"Hyperliquid websocket closed code={exc.code} reason={exc.reason or 'unspecified'}") from exc
                for event in self._parse_message(payload):
                    await bus.publish_market_event(event)

    async def raw_ws_messages(self, coin: str):
        url = "wss://api.hyperliquid.xyz/ws"
        subscribe = {"method": "subscribe", "subscription": {"type": "trades", "coin": coin}}
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps(subscribe))
            async for message in ws:
                yield json.loads(message)

    async def _default_symbols(self) -> list[str]:
        return sorted(self.tokenized_symbols)

    def _parse_message(self, payload: dict[str, Any]) -> list[TradeEvent | OrderBookEvent]:
        channel = payload.get("channel")
        data = payload.get("data")
        if channel == "trades" and isinstance(data, list):
            return [self._parse_trade(row) for row in data]
        if channel == "l2Book" and isinstance(data, dict):
            return [self._parse_l2_book(data)]
        return []

    def _instrument(self, coin: str) -> InstrumentId:
        asset_class = self._asset_class(coin)
        return InstrumentId(
            venue=Venue.HYPERLIQUID,
            asset_class=asset_class,
            raw_symbol=coin,
            std_symbol=f"HYPE:{coin}",
            currency="USDC",
            exchange="HYPERLIQUID",
        )

    def _asset_class(self, coin: str) -> AssetClass:
        if coin.upper() in self.tokenized_symbols:
            return AssetClass.TOKENIZED_EQUITY
        raise ValueError(f"Hyperliquid symbol is not in the explicit security allowlist: {coin}")

    def _parse_trade(self, row: dict[str, Any]) -> TradeEvent:
        coin = str(row.get("coin") or "")
        side_raw = str(row.get("side") or "").upper()
        return TradeEvent(
            instrument=self._instrument(coin),
            venue=Venue.HYPERLIQUID,
            provider=self.name,
            source_ts_ns=int(row.get("time") or 0) * 1_000_000 or utc_now_ns(),
            receive_ts_ns=utc_now_ns(),
            session="24x7",
            sequence=row.get("tid"),
            price=Decimal(str(row.get("px", "0"))),
            quantity=Decimal(str(row.get("sz", "0"))),
            aggressor_side=Side.BUY if side_raw == "B" else Side.SELL if side_raw == "A" else Side.UNKNOWN,
            trade_id=str(row.get("tid") or row.get("hash") or ""),
        )

    def _parse_l2_book(self, data: dict[str, Any]) -> OrderBookEvent:
        coin = str(data.get("coin") or "")
        levels = data.get("levels") or [[], []]
        bids_raw = levels[0] if len(levels) > 0 else []
        asks_raw = levels[1] if len(levels) > 1 else []
        bids = [OrderBookLevel(price=Decimal(str(item["px"])), quantity=Decimal(str(item["sz"]))) for item in bids_raw[:10]]
        asks = [OrderBookLevel(price=Decimal(str(item["px"])), quantity=Decimal(str(item["sz"]))) for item in asks_raw[:10]]
        source_ts_ns = int(data.get("time") or 0) * 1_000_000 or utc_now_ns()
        return OrderBookEvent(
            instrument=self._instrument(coin),
            venue=Venue.HYPERLIQUID,
            provider=self.name,
            source_ts_ns=source_ts_ns,
            receive_ts_ns=utc_now_ns(),
            session="24x7",
            sequence=data.get("block_height") or data.get("time"),
            update_type=BookUpdateType.SNAPSHOT,
            depth=max(len(bids), len(asks)),
            bids=bids,
            asks=asks,
            is_full_depth=len(bids) >= 10 and len(asks) >= 10,
        )
