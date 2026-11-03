"""Public Arcus perp feed retaining native identifiers and book frames."""
from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from decimal import Decimal
from typing import Any

import aiohttp
import websockets

from core.models import (BookUpdateType, OrderBookEvent, OrderBookLevel,
                         Side, TradeEvent, Venue)

from .perp_common import PerpCollectorBase, SYMBOLS, _instrument


_MAX_BOOK_LEVELS = 4096
_MAX_NATIVE_LEVELS = 1000
_MIN_RECONNECT_SECONDS = 1.0
_MAX_RECONNECT_SECONDS = 30.0


class ArcusBookResync(RuntimeError):
    """The source cursor is no longer safe; close this WS epoch and resnapshot."""


class ArcusNetworkRetry(RuntimeError):
    """An expected idle/close failure that can be retried within the run bound."""


class _ArcusAdmissionError(RuntimeError):
    """A source callback failed; never classify it as a market reconnect."""


class _GuardedBus:
    def __init__(self, bus):
        self._bus = bus

    async def publish_market_event(self, event):
        try:
            await self._bus.publish_market_event(event)
        except Exception as error:
            raise _ArcusAdmissionError("Arcus event admission failed") from error


class ArcusPerpCollector(PerpCollectorBase):
    def __init__(self, symbols: tuple[str, ...] = SYMBOLS) -> None:
        super().__init__(Venue.ARCUS, symbols)
        self.native_symbols = {_instrument(Venue.ARCUS, s).raw_symbol: s for s in self.symbols}
        self._native_books: dict[str, tuple[dict[Decimal, Decimal], dict[Decimal, Decimal]]] = {}
        self._retry_count = 0

    def _common(self, symbol: str, source: Any, native_id: str | None = None) -> dict[str, Any]:
        result = super()._common(symbol, None, native_id)
        result['source_ts_ns'] = int(source) * 1000 if source is not None else None
        return result

    async def run(self, bus: Any, duration_sec: float | None = None) -> None:
        if duration_sec is not None and (isinstance(duration_sec, bool)
                or not isinstance(duration_sec, (int, float)) or not math.isfinite(duration_sec)
                or duration_sec < 0):
            raise ValueError("duration_sec must be finite and nonnegative")
        deadline = None if duration_sec is None else time.monotonic() + duration_sec
        guarded = _GuardedBus(bus)
        delay = _MIN_RECONNECT_SECONDS
        self._retry_count = 0
        while deadline is None or time.monotonic() < deadline:
            self.connection_id = uuid.uuid4().hex
            try:
                await self._arcus(guarded, deadline=deadline)
                await self._gap_disconnected_books(guarded)
                return
            except asyncio.CancelledError:
                await self._gap_disconnected_books(guarded)
                raise
            except ArcusBookResync:
                # The parser already emitted the invalid raw frame and a gap for
                # its instrument. Fence the other active books from this socket.
                await self._gap_disconnected_books(guarded)
            except (ArcusNetworkRetry, aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError,
                    asyncio.TimeoutError, ConnectionError, OSError,
                    websockets.exceptions.ConnectionClosed):
                await self._gap_disconnected_books(guarded)
            self._retry_count = min(self._retry_count + 1, 31)
            remaining = None if deadline is None else deadline-time.monotonic()
            if remaining is not None and remaining <= 0:
                return
            await asyncio.sleep(delay if remaining is None else min(delay, remaining))
            delay = min(delay * 2, _MAX_RECONNECT_SECONDS)

    async def _gap_disconnected_books(self, bus: Any) -> None:
        active = tuple(self._native_books)
        for symbol in active:
            await self._gap(bus, symbol, "Arcus websocket disconnected; fresh snapshot required")
        self._native_books.clear()
        self.last_book.clear()
        self._first_delta_after_snapshot.clear()

    async def _arcus(self, bus: Any, *, deadline: float | None) -> None:
        remaining = None if deadline is None else max(0, deadline-time.monotonic())
        if remaining is not None and remaining <= 0:
            return
        request_timeout = 15 if remaining is None else max(.001, min(15, remaining))
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=request_timeout)) as session:
            async with session.get('https://api.arcus.xyz/v1/markets') as response:
                response.raise_for_status()
                rows = (await response.json())['markets']
        listed = {r['marketDisplayName'] for r in rows if r.get('status') == 'ONLINE'}
        missing = set(self.native_symbols) - listed
        if missing:
            raise RuntimeError(f'Arcus requested markets are unavailable: {sorted(missing)}')
        remaining = None if deadline is None else max(0, deadline-time.monotonic())
        if remaining is not None and remaining <= 0:
            return
        async with websockets.connect('wss://api.arcus.xyz/v1/ws', ping_interval=20,
                ping_timeout=20, max_queue=8, max_size=2*1024*1024,
                open_timeout=15 if remaining is None else max(.001, min(15, remaining))) as ws:
            for native in self.native_symbols:
                await ws.send(json.dumps({'type':'subscribe','channel':'l2OrderbookUpdates','id':native,'nLevels':100}))
                await ws.send(json.dumps({'type':'subscribe','channel':'trades','id':native,'snapshot':False}))
            await ws.send(json.dumps({'type':'subscribe','channel':'markets'}))
            await self._arcus_receive(ws, bus, deadline)

    async def _arcus_receive(self, ws, bus, deadline):
        while deadline is None or time.monotonic() < deadline:
            timeout = 45 if deadline is None else min(45, max(.001, deadline-time.monotonic()))
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout)
            except asyncio.TimeoutError as error:
                if deadline is not None and time.monotonic() >= deadline:
                    return
                raise ArcusNetworkRetry("Arcus WebSocket receive timed out") from error
            await self._arcus_message(bus, json.loads(raw), raw)

    async def _arcus_message(self, bus: Any, msg: dict[str, Any], raw: str) -> None:
        if msg.get('type') == 'error' or msg.get('error'):
            raise RuntimeError('Arcus public subscription rejected')
        channel = msg.get('channel')
        data = msg.get('contents')
        if channel == 'markets' and isinstance(data, dict):
            for row in (data.get('markets') or {}).values():
                symbol = self.native_symbols.get(row.get('marketDisplayName'))
                if symbol:
                    await self._ticker(bus,symbol,row,None,'openInterest','fundingRate','base_asset')
            return
        symbol = self.native_symbols.get(msg.get('id'))
        if symbol is None or data is None:
            return
        if channel == 'l2OrderbookUpdates' and isinstance(data,dict):
            await self._arcus_book(bus, symbol, msg, data, raw)
        elif channel == 'trades' and isinstance(data,list):
            for row in data:
                if row.get('tradeId') is None:
                    raise RuntimeError('Arcus trade is missing native tradeId')
                await bus.publish_market_event(TradeEvent(**self._common(symbol,row.get('timestamp'),str(row['tradeId'])),
                    price=Decimal(str(row['price'])),quantity=Decimal(str(row['size'])),
                    aggressor_side=Side.UNKNOWN,trade_id=str(row['tradeId'])))

    @staticmethod
    def _parse_book_levels(rows):
        if not isinstance(rows, list) or len(rows) > _MAX_BOOK_LEVELS:
            return None
        parsed = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                return None
            try:
                price, size = Decimal(str(row[0])), Decimal(str(row[1]))
            except Exception:
                return None
            if not price.is_finite() or not size.is_finite() or price <= 0 or size < 0:
                return None
            parsed.append((price, size))
        return parsed

    def _bounded_sides(self, bids, asks):
        # Retain only the best 1000 levels, matching HotPathStore's finite book.
        if len(bids) > _MAX_NATIVE_LEVELS:
            for price in sorted(bids, reverse=True)[_MAX_NATIVE_LEVELS:]:
                del bids[price]
        if len(asks) > _MAX_NATIVE_LEVELS:
            for price in sorted(asks)[_MAX_NATIVE_LEVELS:]:
                del asks[price]

    async def _publish_invalid_book(self, bus, symbol, source, kind, seq, rows_bids, rows_asks,
                                    channel, raw, reason):
        parsed_bids = self._parse_book_levels(rows_bids)
        parsed_asks = self._parse_book_levels(rows_asks)
        # Keep safe native levels when representable; raw_payload always retains
        # the exact input frame. Malformed values are never normalized as quotes.
        bids = parsed_bids if parsed_bids is not None else []
        asks = parsed_asks if parsed_asks is not None else []
        generation = self.book_generation.get(symbol, 0)
        await bus.publish_market_event(OrderBookEvent(**self._common(symbol, source),
            update_type=kind,
            bids=[OrderBookLevel(price=p, quantity=q) for p, q in bids],
            asks=[OrderBookLevel(price=p, quantity=q) for p, q in asks],
            depth=max(len(bids), len(asks)), channel=channel,
            final_update_id=seq, sync_generation=generation, fast=False, raw_payload=raw))
        self._native_books.pop(symbol, None)
        await self._gap(bus, symbol, reason)
        raise ArcusBookResync(reason)

    async def _arcus_book(self, bus, symbol, msg, data, raw):
        is_snapshot = msg.get('type') == 'subscribed'
        kind = BookUpdateType.SNAPSHOT if is_snapshot else BookUpdateType.DELTA
        source = data.get('timestamp')
        seq_value = data.get('lastSequenceId')
        seq_valid = type(seq_value) is int and 0 <= seq_value <= 2**63-1
        seq = seq_value if seq_valid else None
        bids_update = self._parse_book_levels(data.get('bids', []))
        asks_update = self._parse_book_levels(data.get('asks', []))
        if not seq_valid or bids_update is None or asks_update is None:
            await self._publish_invalid_book(bus, symbol, source, kind, seq,
                data.get('bids', []), data.get('asks', []), 'l2OrderbookUpdates', raw,
                'Arcus native book payload is malformed')

        last = self.last_book.get(symbol)
        if not is_snapshot and last is not None and seq <= last:
            # Preserve the old stale/duplicate raw-only behavior without moving state.
            generation = self.book_generation.get(symbol, 0)
            await bus.publish_market_event(OrderBookEvent(**self._common(symbol, source),
                update_type=kind,
                bids=[OrderBookLevel(price=p, quantity=q) for p, q in bids_update],
                asks=[OrderBookLevel(price=p, quantity=q) for p, q in asks_update],
                depth=max(len(bids_update), len(asks_update)), channel='l2OrderbookUpdates',
                final_update_id=seq, sync_generation=generation, fast=False, raw_payload=raw))
            return

        if (not is_snapshot and last is not None and symbol not in self._first_delta_after_snapshot
                and seq != last + 1):
            await self._publish_invalid_book(bus, symbol, source, kind, seq,
                data.get('bids', []), data.get('asks', []), 'l2OrderbookUpdates', raw,
                f'Arcus midstream sequence gap last={last} current={seq}')

        if is_snapshot:
            bids, asks = {}, {}
        else:
            current = self._native_books.get(symbol)
            if last is None or current is None:
                await self._publish_invalid_book(bus, symbol, source, kind, seq,
                    data.get('bids', []), data.get('asks', []), 'l2OrderbookUpdates', raw,
                    'Arcus delta arrived without a valid snapshot')
            bids, asks = dict(current[0]), dict(current[1])
        for side, updates in ((bids, bids_update), (asks, asks_update)):
            for price, size in updates:
                if size == 0:
                    side.pop(price, None)
                else:
                    side[price] = size
        self._bounded_sides(bids, asks)
        if not bids or not asks or max(bids) >= min(asks):
            await self._publish_invalid_book(bus, symbol, source, kind, seq,
                data.get('bids', []), data.get('asks', []), 'l2OrderbookUpdates', raw,
                'Arcus native book has empty or crossed top of book')

        await self._book(bus, symbol, source, data.get('bids', []), data.get('asks', []),
            kind, seq, channel='l2OrderbookUpdates', raw_payload=raw)
        self._native_books[symbol] = (bids, asks)
