"""Public USDT perpetual feeds. No account keys or paid market data are used."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from decimal import Decimal
from typing import Any

import aiohttp
import websockets

from core.models import AssetClass, BookUpdateType, DataGapEvent, FundingRateEvent, InstrumentId, OpenInterestEvent, OrderBookEvent, OrderBookLevel, Side, TradeEvent, Venue

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def _instrument(venue: Venue, symbol: str) -> InstrumentId:
    if symbol not in SYMBOLS:
        raise ValueError(f"crypto perpetual is outside explicit allowlist: {symbol}")
    raw = symbol
    currency = "USDT"
    if venue == Venue.GRVT:
        raw = symbol.removesuffix("USDT") + "_USDT_Perp"
    elif venue == Venue.ARCUS:
        raw = symbol.removesuffix("USDT") + "-USD"
        currency = "USD"
    return InstrumentId(venue=venue, asset_class=AssetClass.CRYPTO_PERP,
                        raw_symbol=raw, std_symbol=symbol, currency=currency, security_kind="perpetual")


def _levels(items: list[Any]) -> list[OrderBookLevel]:
    return [OrderBookLevel(price=Decimal(str(x[0])), quantity=Decimal(str(x[1]))) for x in items]


def _ms(value: Any) -> int | None:
    try:
        return int(value) * 1_000_000 if value is not None else None
    except (TypeError, ValueError):
        return None


class CryptoPerpCollector:
    """One exchange per process; raw book frames remain durable and ordered."""

    def __init__(self, venue: Venue, symbols: tuple[str, ...] = SYMBOLS) -> None:
        if venue not in {Venue.BINANCE, Venue.BITGET, Venue.BYBIT, Venue.GRVT, Venue.ARCUS}:
            raise ValueError("unsupported crypto perpetual venue")
        self.venue = venue
        self.symbols = tuple(dict.fromkeys(symbols))
        for symbol in self.symbols:
            _instrument(venue, symbol)
        self.name = f"{venue.value}-perp"
        self.connection_id = uuid.uuid4().hex
        self.sequence = 0
        self.last_book: dict[str, int] = {}
        self.book_generation: dict[str, int] = {}
        self.binance_ready: set[str] = set()
        self._first_delta_after_snapshot: set[str] = set()
        self._ticker_last_ns: dict[tuple[str, str], int] = {}

    def _common(self, symbol: str, source_ms: Any, native_id: str | None = None) -> dict[str, Any]:
        self.sequence += 1
        return dict(instrument=_instrument(self.venue, symbol), venue=self.venue,
                    provider=self.name, source_ts_ns=_ms(source_ms),
                    receive_ts_ns=time.time_ns(), receive_monotonic_ns=time.monotonic_ns(),
                    connection_id=self.connection_id, receive_sequence=self.sequence,
                    native_event_id=native_id, session="CRYPTO_24X7")

    async def _gap(self, bus: Any, symbol: str, reason: str) -> None:
        self.last_book.pop(symbol, None)
        self._first_delta_after_snapshot.discard(symbol)
        self.book_generation[symbol] = self.book_generation.get(symbol, 0) + 1
        await bus.publish_market_event(DataGapEvent(**self._common(symbol, None), reason=reason,
                                                   resync_status="book_invalid_reconnect"))

    async def _unsynced_book(self, bus: Any, symbol: str, source_ms: Any, bids: list[Any], asks: list[Any],
                             seq: int, previous: int | None, first: int | None, channel: str,
                             raw_payload: str | None = None) -> None:
        await bus.publish_market_event(OrderBookEvent(**self._common(symbol,source_ms),
            update_type=BookUpdateType.DELTA,bids=_levels(bids),asks=_levels(asks),
            depth=max(len(bids),len(asks)),channel=channel,first_update_id=first,
            final_update_id=seq,previous_update_id=previous,sync_generation=0,fast=False,
            raw_payload=raw_payload))

    async def _book(self, bus: Any, symbol: str, source_ms: Any, bids: list[Any], asks: list[Any],
                    kind: BookUpdateType, seq: int, previous: int | None = None,
                    first: int | None = None, channel: str = "",
                    raw_payload: str | None = None) -> None:
        last = self.last_book.get(symbol)
        if kind == BookUpdateType.DELTA and last is None:
            await self._unsynced_book(bus,symbol,source_ms,bids,asks,seq,previous,first,channel,raw_payload)
            await self._gap(bus, symbol, "delta before snapshot")
            raise RuntimeError(f"{self.name} {symbol} book has no snapshot")
        first_delta = symbol in self._first_delta_after_snapshot
        bridge = channel == "books" and first_delta and previous is not None and last is not None and previous <= last <= seq
        if kind == BookUpdateType.DELTA and previous is not None and previous != last and not bridge:
            await self._unsynced_book(bus,symbol,source_ms,bids,asks,seq,previous,first,channel,raw_payload)
            await self._gap(bus, symbol, f"book sequence gap previous={previous} last={last}")
            raise RuntimeError(f"{self.name} {symbol} book sequence gap")
        if kind == BookUpdateType.DELTA and seq <= (last or 0):
            await self._unsynced_book(bus,symbol,source_ms,bids,asks,seq,previous,first,channel,raw_payload)
            await self._gap(bus, symbol, f"book sequence regressed current={seq} last={last}")
            raise RuntimeError(f"{self.name} {symbol} book sequence regression")
        if kind == BookUpdateType.SNAPSHOT:
            self.book_generation[symbol] = self.book_generation.get(symbol, 0) + 1
            self._first_delta_after_snapshot.add(symbol)
        event = OrderBookEvent(**self._common(symbol, source_ms), update_type=kind,
                               bids=_levels(bids), asks=_levels(asks), depth=max(len(bids), len(asks)),
                               is_full_depth=channel.endswith("books"), channel=channel,
                               first_update_id=first, final_update_id=seq, previous_update_id=previous,
                               sync_generation=self.book_generation[symbol],raw_payload=raw_payload)
        await bus.publish_market_event(event)
        self.last_book[symbol] = seq
        if kind == BookUpdateType.DELTA:
            self._first_delta_after_snapshot.discard(symbol)

    async def _ticker(self, bus: Any, symbol: str, row: dict[str, Any], source_ms: Any,
                      oi_key: str, funding_key: str, oi_unit: str) -> None:
        now=time.monotonic_ns()
        if row.get(funding_key) not in (None, "") and now-self._ticker_last_ns.get((symbol,"funding"),0)>=1_000_000_000:
            await bus.publish_market_event(FundingRateEvent(**self._common(symbol, source_ms),
                source_timestamp_kind="websocket_ticker_1s_sample",
                funding_rate=Decimal(str(row[funding_key])),
                funding_interval_hours=int(row["fundingIntervalHour"]) if row.get("fundingIntervalHour") else None))
            self._ticker_last_ns[(symbol,"funding")]=now
        if row.get(oi_key) not in (None, "") and now-self._ticker_last_ns.get((symbol,"oi"),0)>=1_000_000_000:
            await bus.publish_market_event(OpenInterestEvent(**self._common(symbol, source_ms),
                source_timestamp_kind="websocket_ticker_1s_sample",
                open_interest=Decimal(str(row[oi_key])), open_interest_unit=oi_unit,
                open_interest_value=Decimal(str(row["openInterestValue"])) if row.get("openInterestValue") else None,
                value_currency="USDT" if row.get("openInterestValue") else None,
                observation_method="websocket_ticker_1s_sample"))
            self._ticker_last_ns[(symbol,"oi")]=now

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

    async def _binance_snapshot(self, session: aiohttp.ClientSession, bus: Any, symbol: str) -> int:
        async with session.get("https://fapi.binance.com/fapi/v1/depth", params={"symbol": symbol, "limit": 1000}) as response:
            response.raise_for_status()
            row = await response.json()
        seq = int(row["lastUpdateId"])
        await self._book(bus, symbol, None, row.get("bids", []), row.get("asks", []),
                         BookUpdateType.SNAPSHOT, seq, channel="depth1000_rest_snapshot",
                         raw_payload=json.dumps(row,separators=(",",":")))
        return seq

    async def _binance(self, bus: Any, duration_sec: float | None) -> None:
        public_streams = "/".join(f"{s.lower()}@depth@100ms" for s in self.symbols)
        market_streams = "/".join(f"{s.lower()}@{topic}" for s in self.symbols
                                  for topic in ("aggTrade", "markPrice@1s"))
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with websockets.connect("wss://fstream.binance.com/public/stream?streams=" + public_streams,
                                          ping_interval=20, ping_timeout=20, max_queue=8, max_size=2 * 1024 * 1024) as public_ws:
              async with websockets.connect("wss://fstream.binance.com/market/stream?streams=" + market_streams,
                                            ping_interval=20, ping_timeout=20, max_queue=8, max_size=2 * 1024 * 1024) as market_ws:
                deadline = time.monotonic() + duration_sec if duration_sec else None
                # Buffer WS depth frames while obtaining REST snapshots. The first
                # eligible delta must bridge snapshot lastUpdateId.
                snapshot_ids = await asyncio.gather(*(self._binance_snapshot(session,bus,s) for s in self.symbols))
                snapshots = dict(zip(self.symbols,snapshot_ids))
                oi_task = asyncio.create_task(self._binance_oi(session, bus, deadline))
                market_task = asyncio.create_task(self._binance_market(market_ws, bus, deadline))
                try:
                    while deadline is None or time.monotonic() < deadline:
                        if market_task.done():
                            await market_task
                        if oi_task.done():
                            await oi_task
                        raw = await asyncio.wait_for(public_ws.recv(), timeout=2)
                        msg = json.loads(raw).get("data", {})
                        await self._binance_message(bus, msg, snapshots, raw)
                finally:
                    oi_task.cancel(); market_task.cancel()
                    await asyncio.gather(oi_task, market_task, return_exceptions=True)

    async def _binance_market(self, ws: Any, bus: Any, deadline: float | None) -> None:
        while deadline is None or time.monotonic() < deadline:
            raw=await ws.recv()
            msg=json.loads(raw).get("data",{})
            symbol=str(msg.get("s") or "")
            if symbol not in self.symbols:
                continue
            if msg.get("e") == "aggTrade":
                await bus.publish_market_event(TradeEvent(**self._common(symbol,msg.get("T"),str(msg.get("a"))),
                    price=Decimal(str(msg["p"])),quantity=Decimal(str(msg["q"])),
                    aggressor_side=Side.SELL if msg.get("m") else Side.BUY,
                    trade_id=str(msg.get("a")),source_timestamp_kind="aggregate_trade"))
            elif msg.get("e") == "markPriceUpdate":
                await self._ticker(bus,symbol,{"fundingRate":msg.get("r")},msg.get("E"),
                                   "missing","fundingRate","base_asset")

    async def _binance_oi(self, session: aiohttp.ClientSession, bus: Any, deadline: float | None) -> None:
        while deadline is None or time.monotonic() < deadline:
            for symbol in self.symbols:
                async with session.get("https://fapi.binance.com/fapi/v1/openInterest", params={"symbol": symbol}) as response:
                    response.raise_for_status()
                    row = await response.json()
                await bus.publish_market_event(OpenInterestEvent(**self._common(symbol, row.get("time")),
                    open_interest=Decimal(str(row["openInterest"])), open_interest_unit="base_asset",
                    observation_method="rest_poll"))
            await asyncio.sleep(5)

    async def _binance_message(self, bus: Any, msg: dict[str, Any], snapshots: dict[str, int],
                               raw_payload: str | None = None) -> None:
        symbol = str(msg.get("s") or "")
        if symbol not in self.symbols or msg.get("e") != "depthUpdate":
            return
        first, last = int(msg["U"]), int(msg["u"])
        snapshot = snapshots[symbol]
        bridging = symbol not in self.binance_ready
        # Binance's initial bridge is inclusive: U <= snapshot <= u.
        # A boundary frame with u == snapshot proves the bridge even though
        # its quantities are already represented by the REST snapshot.
        if last < snapshot or (not bridging and last <= self.last_book[symbol]):
            await self._unsynced_book(bus,symbol,msg.get("E"),msg.get("b",[]),msg.get("a",[]),
                                       last,int(msg["pu"]) if msg.get("pu") is not None else None,
                                       first,"depth@100ms_pre_snapshot" if bridging else "depth@100ms_stale",raw_payload)
            return
        if bridging:
            if not first <= snapshot <= last:
                await self._unsynced_book(bus,symbol,msg.get("E"),msg.get("b",[]),msg.get("a",[]),
                                           last,int(msg["pu"]) if msg.get("pu") is not None else None,
                                           first,"depth@100ms_unbridged",raw_payload)
                await self._gap(bus, symbol, "initial depth delta does not bridge snapshot")
                raise RuntimeError(f"Binance {symbol} snapshot bridge failure")
            self.binance_ready.add(symbol)
        previous = int(msg["pu"]) if msg.get("pu") is not None else None
        if not bridging and previous is not None and previous != self.last_book[symbol]:
            await self._unsynced_book(bus,symbol,msg.get("E"),msg.get("b",[]),msg.get("a",[]),
                                       last,previous,first,"depth@100ms_sequence_gap",raw_payload)
            await self._gap(bus, symbol, "depth pu gap")
            raise RuntimeError(f"Binance {symbol} depth gap")
        if last == snapshot:
            # Keep the original boundary frame without applying the same
            # sequence twice or invalidating the synchronized snapshot.
            await self._unsynced_book(bus,symbol,msg.get("E"),msg.get("b",[]),msg.get("a",[]),
                                       last,previous,first,"depth@100ms_snapshot_boundary",raw_payload)
            return
        await self._book(bus, symbol, msg.get("E"), msg.get("b", []), msg.get("a", []),
                         BookUpdateType.DELTA, last, previous=None, first=first, channel="depth@100ms",
                         raw_payload=raw_payload)

    async def run(self, bus: Any, duration_sec: float | None = None) -> None:
        if self.venue == Venue.BYBIT:
            await self._bybit(bus, duration_sec)
        elif self.venue == Venue.BITGET:
            await self._bitget(bus, duration_sec)
        else:
            await self._binance(bus, duration_sec)
