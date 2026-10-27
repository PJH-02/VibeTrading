"""Public Bybit USDT perpetual feed. No account keys are used."""
from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

import websockets

from core.models import BookUpdateType, Side, TradeEvent, Venue

from .perp_common import PerpCollectorBase, SYMBOLS


class BybitPerpCollector(PerpCollectorBase):
    def __init__(self, symbols: tuple[str, ...] = SYMBOLS) -> None:
        super().__init__(Venue.BYBIT, symbols)

    async def _bybit(self, bus: Any, duration_sec: float | None) -> None:
        url = "wss://stream.bybit.com/v5/public/linear"
        topics = [f"{topic}.{symbol}" for symbol in self.symbols for topic in ("orderbook.50", "publicTrade", "tickers")]
        async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=8, max_size=2 * 1024 * 1024) as ws:
            await ws.send(json.dumps({"op": "subscribe", "args": topics}))
            deadline = time.monotonic() + duration_sec if duration_sec else None
            while deadline is None or time.monotonic() < deadline:
                raw = await ws.recv()
                msg = json.loads(raw)
                topic = str(msg.get("topic") or "")
                if not topic:
                    if msg.get("success") is False:
                        raise RuntimeError(f"Bybit subscription rejected: {msg.get('ret_msg')}")
                    continue
                symbol = topic.rsplit(".", 1)[-1]
                if symbol not in self.symbols:
                    continue
                data = msg.get("data") or {}
                if topic.startswith("orderbook."):
                    seq = int(data["u"])
                    kind = BookUpdateType.SNAPSHOT if msg.get("type") == "snapshot" or seq == 1 else BookUpdateType.DELTA
                    # Bybit update IDs increase; a new snapshot reinitializes the book.
                    await self._book(bus, symbol, data.get("cts") or msg.get("ts"), data.get("b", []), data.get("a", []), kind, seq, channel="orderbook.50", raw_payload=raw)
                elif topic.startswith("publicTrade."):
                    for trade in data:
                        await bus.publish_market_event(TradeEvent(**self._common(symbol, trade.get("T"), str(trade.get("i"))),
                            price=Decimal(str(trade["p"])), quantity=Decimal(str(trade["v"])),
                            aggressor_side=Side.BUY if trade.get("S") == "Buy" else Side.SELL,
                            trade_id=str(trade.get("i"))))
                elif topic.startswith("tickers."):
                    await self._ticker(bus, symbol, data, msg.get("ts"), "openInterest", "fundingRate", "base_asset")

    async def run(self, bus: Any, duration_sec: float | None = None) -> None:
        await self._bybit(bus, duration_sec)
