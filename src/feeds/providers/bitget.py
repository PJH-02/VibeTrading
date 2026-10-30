"""Public Bitget USDT perpetual feed. No account keys are used."""
from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal
from typing import Any

import websockets

from core.models import BookUpdateType, Side, TradeEvent, Venue

from .perp_common import PerpCollectorBase, SYMBOLS


class BitgetPerpCollector(PerpCollectorBase):
    def __init__(self, symbols: tuple[str, ...] = SYMBOLS) -> None:
        super().__init__(Venue.BITGET, symbols)

    async def _bitget(self, bus: Any, duration_sec: float | None) -> None:
        args = [dict(instType="usdt-futures", topic=topic, symbol=symbol)
                for symbol in self.symbols for topic in ("books", "publicTrade", "ticker")]
        async with websockets.connect("wss://ws.bitget.com/v3/ws/public", ping_interval=20, ping_timeout=20,
                                      max_queue=8, max_size=2 * 1024 * 1024) as ws:
            await ws.send(json.dumps({"op": "subscribe", "args": args}))
            deadline = time.monotonic() + duration_sec if duration_sec else None
            async def heartbeat() -> None:
                while True:
                    await asyncio.sleep(30)
                    await ws.send("ping")
            ping_task=asyncio.create_task(heartbeat())
            try:
                while deadline is None or time.monotonic() < deadline:
                    if ping_task.done(): await ping_task
                    raw = await ws.recv()
                    if raw == "pong":
                        continue
                    msg = json.loads(raw)
                    if msg.get("event") == "error":
                        raise RuntimeError(f"Bitget subscription rejected: {msg.get('code')} {msg.get('msg')}")
                    arg = msg.get("arg") or {}
                    symbol = str(arg.get("symbol") or "")
                    if symbol not in self.symbols:
                        continue
                    for row in msg.get("data") or []:
                        topic = arg.get("topic")
                        if topic == "books":
                            kind = BookUpdateType.SNAPSHOT if msg.get("action") == "snapshot" else BookUpdateType.DELTA
                            previous = int(row["pseq"]) if row.get("pseq") is not None else None
                            await self._book(bus, symbol, row.get("ts") or msg.get("ts"), row.get("b", []), row.get("a", []),
                                             kind, int(row["seq"]), previous=previous, channel="books", raw_payload=raw)
                        elif topic == "publicTrade":
                            await bus.publish_market_event(TradeEvent(**self._common(symbol, row.get("T"), str(row.get("i"))),
                                price=Decimal(str(row["p"])), quantity=Decimal(str(row["v"])),
                                aggressor_side=Side.BUY if str(row.get("S")).lower() == "buy" else Side.SELL,
                                trade_id=str(row.get("i"))))
                        elif topic == "ticker":
                            await self._ticker(bus, symbol, row, row.get("ts") or msg.get("ts"),
                                               "openInterest", "fundingRate", "provider_native_unspecified")
            finally:
                ping_task.cancel()
                await asyncio.gather(ping_task,return_exceptions=True)

    async def run(self, bus: Any, duration_sec: float | None = None) -> None:
        await self._bitget(bus, duration_sec)
