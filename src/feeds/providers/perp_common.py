"""Shared perpetual-feed bookkeeping without venue-specific parsing."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from decimal import Decimal
from typing import Any

from core.models import (
    AssetClass,
    BookUpdateType,
    DataGapEvent,
    FundingRateEvent,
    InstrumentId,
    OpenInterestEvent,
    OrderBookEvent,
    OrderBookLevel,
    Venue,
)

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


class PerpCollectorBase:
    """One exchange per process; raw book frames remain durable and ordered."""

    def __init__(self, venue: Venue, symbols: tuple[str, ...] = SYMBOLS) -> None:
        self.venue = venue
        self.symbols = tuple(dict.fromkeys(symbols))
        for symbol in self.symbols:
            _instrument(venue, symbol)
        self.name = f"{venue.value}-perp"
        self.connection_id = uuid.uuid4().hex
        self.sequence = 0
        self.last_book: dict[str, int] = {}
        self.book_generation: dict[str, int] = {}
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

    async def _receive(self, ws: Any, bus: Any, duration: float | None, handler: Any) -> None:
        """Read raw frames and dispatch them to the owning venue handler."""
        deadline = time.monotonic()+duration if duration is not None else None
        while deadline is None or time.monotonic()<deadline:
            timeout = min(45, max(.001, deadline-time.monotonic())) if deadline is not None else 45
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout)
            except asyncio.TimeoutError:
                if deadline is not None and time.monotonic()>=deadline:
                    return
                raise RuntimeError(f'{self.name} WebSocket received no frames for 45 seconds')
            await handler(bus, json.loads(raw), raw)

    async def run(self, bus: Any, duration_sec: float | None = None) -> None:
        raise NotImplementedError
