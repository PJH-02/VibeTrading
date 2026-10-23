from __future__ import annotations

import asyncio
import json
import logging
import time
from decimal import Decimal
from typing import Any

import aiohttp
import websockets

from vibetrading.bus import EventBus
from vibetrading.collection_timing import CollectionContext, MINUTE_NS, next_utc_minute_ns, receipt_clock
from vibetrading.models import (
    Bar1mEvent, BookUpdateType, DataGapEvent, FundingRateEvent, InstrumentId,
    OpenInterestEvent, OrderBookEvent, OrderBookLevel, Venue,
)
from vibetrading.universe_config import BinanceSecurity

logger = logging.getLogger(__name__)


class BinanceCatalog:
    rest_url = "https://fapi.binance.com"

    async def exchange_symbols(self) -> set[str]:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.rest_url}/fapi/v1/exchangeInfo") as response:
                response.raise_for_status()
                payload = await response.json()
        return {
            str(item.get("symbol") or "").upper()
            for item in payload.get("symbols", [])
            if item.get("status") == "TRADING" and str(item.get("contractType") or "").endswith("PERPETUAL")
        }


class BinanceCollector:
    """Explicit Binance TradFi perpetual collector; ordinary crypto is impossible."""

    name = "binance"
    rest_url = "https://fapi.binance.com"
    public_ws = "wss://fstream.binance.com/public/stream?streams="
    market_ws = "wss://fstream.binance.com/market/stream?streams="
    oi_poll_seconds = 5.0
    kline_backfill_seconds = 30.0
    funding_poll_seconds = 300.0

    def __init__(self, securities: dict[str, BinanceSecurity]) -> None:
        self.securities = {key.upper(): value for key, value in securities.items()}
        self._published: set[str] = set()
        self._book_last_update: dict[str, int] = {}
        self._book_generation: dict[str, int] = {}

    async def resolve_symbols(self) -> list[str]:
        return sorted(self.securities)

    async def run(self, bus: EventBus, duration_sec: float | None = None) -> None:
        if not self.securities:
            raise RuntimeError("Binance explicit TradFi universe is empty")
        listed = await BinanceCatalog().exchange_symbols()
        missing = sorted(set(self.securities) - listed)
        if missing:
            raise RuntimeError(f"Binance configured perpetuals are not trading: {missing}")

        symbols = sorted(self.securities)
        depth_streams = "/".join(f"{symbol.lower()}@depth@100ms" for symbol in symbols)
        kline_streams = "/".join(f"{symbol.lower()}@kline_1m" for symbol in symbols)
        async with websockets.connect(self.public_ws + depth_streams, ping_interval=20, ping_timeout=20, max_queue=2048) as depth_ws:
            async with websockets.connect(self.market_ws + kline_streams, ping_interval=20, ping_timeout=20, max_queue=512) as market_ws:
                # Both sockets and the REST catalogue are ready before the one
                # shared cutoff is chosen. No provider persists an event before it.
                context = CollectionContext(self.name, next_utc_minute_ns())
                depth_connection = context.new_connection_id()
                market_connection = context.new_connection_id()
                logger.info("Binance ready; exact UTC collection boundary=%d symbols=%s", context.collection_start_ts_ns, symbols)
                tasks = [
                    asyncio.create_task(self._depth_loop(depth_ws, bus, context, depth_connection)),
                    asyncio.create_task(self._market_loop(market_ws, bus, context, market_connection)),
                    asyncio.create_task(self._rest_loop(bus, context)),
                ]
                try:
                    done, _ = await asyncio.wait(tasks, timeout=duration_sec, return_when=asyncio.FIRST_EXCEPTION)
                    if duration_sec is None and not done:
                        return
                    errors = [task.exception() for task in done if not task.cancelled() and task.exception() is not None]
                    if errors:
                        raise RuntimeError("Binance task failure: " + " | ".join(f"{type(error).__name__}: {error}" for error in errors)) from errors[0]
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

    async def _depth_loop(self, ws: Any, bus: EventBus, context: CollectionContext, connection_id: str) -> None:
        receive_sequence = 0
        async for raw in ws:
            wallclock, monotonic = receipt_clock(); receive_sequence += 1
            if not context.accepts(wallclock):
                continue
            payload = json.loads(raw)
            data = payload.get("data") or payload
            if data.get("e") != "depthUpdate":
                continue
            symbol = str(data.get("s") or "").upper()
            if symbol not in self.securities:
                continue
            final_update = int(data.get("u") or 0)
            previous_update = int(data.get("pu") or 0)
            prior = self._book_last_update.get(symbol)
            if prior is not None and previous_update != prior:
                self._book_generation[symbol] = self._book_generation.get(symbol, 0) + 1
                logger.warning("Binance depth sequence gap symbol=%s expected_pu=%d actual_pu=%d", symbol, prior, previous_update)
                await bus.publish_market_event(DataGapEvent(
                    instrument=self._instrument(symbol),venue=Venue.BINANCE,provider=self.name,
                    source_ts_ns=int(data.get("T") or data.get("E") or 0)*1_000_000 or None,
                    source_timestamp_kind="provider_transaction",wallclock_ts_ns=wallclock,receive_ts_ns=wallclock,
                    receive_monotonic_ns=monotonic,collector_run_id=context.collector_run_id,connection_id=connection_id,
                    receive_sequence=receive_sequence,native_event_id=f"gap:{symbol}:{prior}:{previous_update}",
                    collection_start_ts_ns=context.collection_start_ts_ns,session="24x7",sequence=final_update,
                    missing_from_sequence=prior+1,missing_to_sequence=previous_update,reason="binance_depth_previous_update_mismatch",
                    resync_status="new_raw_stream_generation",
                ))
            self._book_last_update[symbol] = final_update
            event = OrderBookEvent(
                instrument=self._instrument(symbol), venue=Venue.BINANCE, provider=self.name,
                source_ts_ns=int(data.get("T") or data.get("E") or 0) * 1_000_000 or None,
                source_timestamp_kind="provider_transaction", wallclock_ts_ns=wallclock,
                receive_ts_ns=wallclock, receive_monotonic_ns=monotonic,
                collector_run_id=context.collector_run_id, connection_id=connection_id,
                receive_sequence=receive_sequence, native_event_id=f"depth:{symbol}:{final_update}",
                collection_start_ts_ns=context.collection_start_ts_ns, session="24x7",
                sequence=final_update, update_type=BookUpdateType.DELTA,
                bids=self._levels(data.get("b", [])), asks=self._levels(data.get("a", [])),
                depth=max(len(data.get("b", [])), len(data.get("a", []))), is_full_depth=False,
                channel="depth@100ms", provider_depth=None,
                first_update_id=int(data.get("U") or 0), final_update_id=final_update,
                previous_update_id=previous_update, sync_generation=self._book_generation.get(symbol, 0),
            )
            await bus.publish_market_event(event)

    async def _market_loop(self, ws: Any, bus: EventBus, context: CollectionContext, connection_id: str) -> None:
        receive_sequence = 0
        async for raw in ws:
            wallclock, monotonic = receipt_clock(); receive_sequence += 1
            if not context.accepts(wallclock):
                continue
            payload = json.loads(raw); data = payload.get("data") or payload
            if data.get("e") != "kline":
                continue
            candle = data.get("k") or {}
            if not candle.get("x"):
                continue
            event = self._parse_kline(candle, context, wallclock, monotonic, connection_id, receive_sequence, "realtime")
            if event is not None:
                await self._publish_unique(bus, event)

    async def _rest_loop(self, bus: EventBus, context: CollectionContext) -> None:
        timeout = aiohttp.ClientTimeout(total=20)
        next_oi = next_kline = next_funding = 0.0
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                now = asyncio.get_running_loop().time()
                if now >= next_oi:
                    await self._collect_oi(session, bus, context); next_oi = now + self.oi_poll_seconds
                if now >= next_kline:
                    await self._collect_klines(session, bus, context); next_kline = now + self.kline_backfill_seconds
                if now >= next_funding:
                    await self._collect_funding(session, bus, context); next_funding = now + self.funding_poll_seconds
                await asyncio.sleep(max(0.05, min(next_oi, next_kline, next_funding) - asyncio.get_running_loop().time()))

    async def _get(self, session: aiohttp.ClientSession, path: str, params: dict[str, Any]) -> tuple[Any, int, int]:
        async with session.get(self.rest_url + path, params=params) as response:
            response.raise_for_status(); payload = await response.json(); wallclock, monotonic = receipt_clock()
        return payload, wallclock, monotonic

    async def _collect_oi(self, session: aiohttp.ClientSession, bus: EventBus, context: CollectionContext) -> None:
        for index, symbol in enumerate(sorted(self.securities)):
            row, wallclock, monotonic = await self._get(session, "/fapi/v1/openInterest", {"symbol": symbol})
            if not context.accepts(wallclock):
                continue
            timestamp_ms = int(row.get("time") or 0)
            event = OpenInterestEvent(
                instrument=self._instrument(symbol), venue=Venue.BINANCE, provider=self.name,
                source_ts_ns=timestamp_ms * 1_000_000 or None, source_timestamp_kind="provider_observation",
                wallclock_ts_ns=wallclock, receive_ts_ns=wallclock, receive_monotonic_ns=monotonic,
                collector_run_id=context.collector_run_id, connection_id=f"{context.collector_run_id}-rest",
                receive_sequence=wallclock, record_index=index, native_event_id=f"oi:{symbol}:{timestamp_ms or wallclock}",
                collection_start_ts_ns=context.collection_start_ts_ns, session="24x7",
                sequence=timestamp_ms or wallclock, open_interest=Decimal(str(row.get("openInterest", "0"))),
                open_interest_unit="contract", observation_method="rest_5s",
            )
            await bus.publish_market_event(event)

    async def _collect_klines(self, session: aiohttp.ClientSession, bus: EventBus, context: CollectionContext) -> None:
        for symbol in sorted(self.securities):
            rows, wallclock, monotonic = await self._get(session, "/fapi/v1/klines", {"symbol": symbol, "interval": "1m", "limit": 5})
            for index, row in enumerate(rows if isinstance(rows, list) else []):
                candle = {"s": symbol, "t": row[0], "T": row[6], "o": row[1], "h": row[2], "l": row[3], "c": row[4], "v": row[5], "n": row[8], "x": int(row[6]) * 1_000_000 < wallclock}
                event = self._parse_kline(candle, context, wallclock, monotonic, f"{context.collector_run_id}-rest", wallclock + index, "backfill")
                if event is not None:
                    await self._publish_unique(bus, event)

    async def _collect_funding(self, session: aiohttp.ClientSession, bus: EventBus, context: CollectionContext) -> None:
        for symbol in sorted(self.securities):
            rows, wallclock, monotonic = await self._get(session, "/fapi/v1/fundingRate", {"symbol": symbol, "limit": 10})
            for index, row in enumerate(rows if isinstance(rows, list) else []):
                funding_ms = int(row.get("fundingTime") or 0)
                if funding_ms * 1_000_000 < context.collection_start_ts_ns or funding_ms * 1_000_000 > wallclock:
                    continue
                event = FundingRateEvent(
                    instrument=self._instrument(symbol), venue=Venue.BINANCE, provider=self.name,
                    source_ts_ns=funding_ms * 1_000_000, source_timestamp_kind="provider_funding",
                    wallclock_ts_ns=wallclock, receive_ts_ns=wallclock, receive_monotonic_ns=monotonic,
                    collector_run_id=context.collector_run_id, connection_id=f"{context.collector_run_id}-rest",
                    receive_sequence=wallclock, record_index=index, native_event_id=f"funding:{symbol}:{funding_ms}",
                    collection_start_ts_ns=context.collection_start_ts_ns, ingest_mode="backfill", session="24x7",
                    sequence=funding_ms, funding_rate=Decimal(str(row.get("fundingRate", "0"))), funding_interval_hours=8,
                )
                await self._publish_unique(bus, event)

    def _parse_kline(self, row: dict[str, Any], context: CollectionContext, wallclock: int, monotonic: int, connection_id: str, receive_sequence: int, mode: str) -> Bar1mEvent | None:
        symbol = str(row.get("s") or "").upper(); open_ms = int(row.get("t") or 0); close_ms = int(row.get("T") or 0)
        if symbol not in self.securities or not row.get("x") or open_ms * 1_000_000 < context.collection_start_ts_ns or (open_ms * 1_000_000) % MINUTE_NS:
            return None
        return Bar1mEvent(
            instrument=self._instrument(symbol), venue=Venue.BINANCE, provider=self.name,
            source_ts_ns=close_ms * 1_000_000, source_timestamp_kind="provider_candle_close",
            wallclock_ts_ns=wallclock, receive_ts_ns=wallclock, receive_monotonic_ns=monotonic,
            collector_run_id=context.collector_run_id, connection_id=connection_id,
            receive_sequence=receive_sequence, native_event_id=f"bar1m:{symbol}:{open_ms}",
            collection_start_ts_ns=context.collection_start_ts_ns, ingest_mode=mode, session="24x7", sequence=open_ms,
            open_ts_ns=open_ms * 1_000_000, close_ts_ns=close_ms * 1_000_000,
            open=Decimal(str(row.get("o", "0"))), high=Decimal(str(row.get("h", "0"))),
            low=Decimal(str(row.get("l", "0"))), close=Decimal(str(row.get("c", "0"))),
            volume=Decimal(str(row.get("v", "0"))), trade_count=int(row["n"]) if row.get("n") is not None else None,
        )

    async def _publish_unique(self, bus: EventBus, event: Any) -> None:
        if event.event_id in self._published:
            return
        await bus.publish_market_event(event); self._published.add(event.event_id)
        if len(self._published) > 50_000:
            self._published.pop()

    def _instrument(self, symbol: str) -> InstrumentId:
        security = self.securities[symbol.upper()]
        return InstrumentId(
            venue=Venue.BINANCE, asset_class=security.asset_class, raw_symbol=security.provider_symbol,
            std_symbol=security.symbol, underlying=security.underlying, security_kind=security.security_kind,
            currency=security.currency, exchange=security.exchange,
        )

    @staticmethod
    def _levels(rows: list[Any]) -> list[OrderBookLevel]:
        return [OrderBookLevel(price=Decimal(str(row[0])), quantity=Decimal(str(row[1]))) for row in rows]
