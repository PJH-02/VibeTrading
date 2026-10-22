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
from vibetrading.collection_timing import CollectionContext, MINUTE_NS, next_utc_minute_ns, receipt_clock
from vibetrading.models import (
    Bar1mEvent, BookUpdateType, FundingRateEvent, InstrumentId,
    OpenInterestEvent, OrderBookEvent, OrderBookLevel, Venue, stable_hash,
)
from vibetrading.universe_config import HyperliquidSecurity

logger = logging.getLogger(__name__)


def load_hyperliquid_tokenized_symbols(config_dir: Path) -> list[str]:
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
        result = [HyperliquidAsset(str(item.get("name") or ""), "crypto_perp", index) for index, item in enumerate(perps.get("universe", []))]
        result += [HyperliquidAsset(str(item.get("name") or item.get("tokens") or ""), "crypto_spot", index) for index, item in enumerate(spots.get("universe", []))]
        return [asset for asset in result if asset.name]

    @staticmethod
    async def _post(session: aiohttp.ClientSession, payload: dict[str, Any]) -> Any:
        async with session.post(HyperliquidCatalog.info_url, json=payload) as response:
            response.raise_for_status()
            return await response.json()


class HyperliquidCollector:
    name = "hyperliquid"
    ws_url = "wss://api.hyperliquid.xyz/ws"
    candle_poll_seconds = 30.0
    funding_poll_seconds = 300.0

    def __init__(self, securities: dict[str, HyperliquidSecurity], *, enable_rest: bool = True) -> None:
        self.securities = {symbol.upper(): security for symbol, security in securities.items()}
        self._published: set[str] = set()
        self.enable_rest = enable_rest

    async def run(self, bus: EventBus, duration_sec: float | None = None) -> None:
        if not self.securities:
            raise RuntimeError("Hyperliquid universe is empty")
        await self._validate_symbols()
        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20, max_queue=8, max_size=2 * 1024 * 1024) as ws:
            provider_symbols = sorted(security.provider_symbol for security in self.securities.values())
            for symbol in provider_symbols:
                await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": symbol, "fast": True}}))
                await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "activeAssetCtx", "coin": symbol}}))
            # The same readiness->next UTC minute cutoff used by Binance.
            context = CollectionContext(self.name, next_utc_minute_ns())
            connection_id = context.new_connection_id()
            logger.info("Hyperliquid ready; exact UTC collection boundary=%d symbols=%s", context.collection_start_ts_ns, provider_symbols)
            tasks = [asyncio.create_task(self._websocket_loop(ws, bus, context, connection_id))]
            if self.enable_rest:
                tasks.append(asyncio.create_task(self._rest_loop(bus, context)))
            try:
                done, _ = await asyncio.wait(tasks, timeout=duration_sec, return_when=asyncio.FIRST_COMPLETED)
                if duration_sec is None and not done:
                    return
                errors = [task.exception() for task in done if not task.cancelled() and task.exception() is not None]
                if errors:
                    raise RuntimeError("Hyperliquid task failure: " + " | ".join(f"{type(error).__name__}: {error}" for error in errors)) from errors[0]
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _websocket_loop(self, ws: Any, bus: EventBus, context: CollectionContext, connection_id: str) -> None:
        receive_sequence = 0
        async for raw in ws:
            wallclock, monotonic = receipt_clock(); receive_sequence += 1
            if not context.accepts(wallclock):
                continue
            payload = json.loads(raw)
            channel = str(payload.get("channel") or "")
            data = payload.get("data") or {}
            if channel == "l2Book" and isinstance(data, dict):
                event = self._parse_book(data, context, wallclock, monotonic, connection_id, receive_sequence)
                if event is not None:
                    await bus.publish_market_event(event)
            elif channel == "activeAssetCtx" and isinstance(data, dict):
                event = self._parse_open_interest(data, context, wallclock, monotonic, connection_id, receive_sequence)
                if event is not None:
                    await bus.publish_market_event(event)

    async def _rest_loop(self, bus: EventBus, context: CollectionContext) -> None:
        timeout = aiohttp.ClientTimeout(total=20)
        next_candle = next_funding = 0.0
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                now = asyncio.get_running_loop().time()
                if now >= next_candle:
                    await self._rest_cycle(self._collect_candles, session, bus, context)
                    next_candle = asyncio.get_running_loop().time() + self.candle_poll_seconds
                if now >= next_funding:
                    await self._rest_cycle(self._collect_funding, session, bus, context)
                    next_funding = asyncio.get_running_loop().time() + self.funding_poll_seconds
                await asyncio.sleep(max(0.05, min(next_candle, next_funding) - asyncio.get_running_loop().time()))

    async def _rest_cycle(self, collect: Any, session: Any, bus: Any, context: CollectionContext) -> None:
        """A transient REST outage must not tear down a healthy market socket.

        The next bounded polling window overlaps completed bars/funding and
        deduplication prevents repeats. Policy and publish failures propagate.
        """
        try:
            await collect(session, bus, context)
        except aiohttp.ClientResponseError as exc:
            if exc.status != 429 and exc.status < 500:
                raise
            logger.warning("Hyperliquid REST temporarily unavailable operation=%s status=%s; retry next poll", collect.__name__, exc.status)
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
            logger.warning("Hyperliquid REST temporarily unavailable operation=%s; retry next poll", collect.__name__)

    async def _post_clock(self, session: aiohttp.ClientSession, payload: dict[str, Any]) -> tuple[Any, int, int]:
        async with session.post(HyperliquidCatalog.info_url, json=payload) as response:
            response.raise_for_status(); data = await response.json(); wallclock, monotonic = receipt_clock()
        return data, wallclock, monotonic

    async def _collect_candles(self, session: aiohttp.ClientSession, bus: EventBus, context: CollectionContext) -> None:
        end_ms = __import__("time").time_ns() // 1_000_000
        for symbol in sorted(security.provider_symbol for security in self.securities.values()):
            rows, wallclock, monotonic = await self._post_clock(session, {"type": "candleSnapshot", "req": {"coin": symbol, "interval": "1m", "startTime": end_ms - 10 * 60_000, "endTime": end_ms}})
            for index, row in enumerate(rows if isinstance(rows, list) else []):
                event = self._parse_candle(row, context, wallclock, monotonic, index)
                if event is not None:
                    await self._publish_unique(bus, event)

    async def _collect_funding(self, session: aiohttp.ClientSession, bus: EventBus, context: CollectionContext) -> None:
        end_ms = __import__("time").time_ns() // 1_000_000
        for symbol in sorted(security.provider_symbol for security in self.securities.values()):
            rows, wallclock, monotonic = await self._post_clock(session, {"type": "fundingHistory", "coin": symbol, "startTime": end_ms - 2 * 3_600_000, "endTime": end_ms})
            for index, row in enumerate(rows if isinstance(rows, list) else []):
                event = self._parse_funding(row, context, wallclock, monotonic, index)
                if event is not None:
                    await self._publish_unique(bus, event)

    async def _validate_symbols(self) -> None:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            payload = await HyperliquidCatalog._post(session, {"type": "allPerpMetas"})
        listed = {str(asset.get("name") or "").upper() for meta in payload if isinstance(payload, list) and isinstance(meta, dict) for asset in meta.get("universe", []) if isinstance(asset, dict)}
        missing = sorted(set(self.securities) - listed)
        if missing:
            raise RuntimeError(f"Hyperliquid configured HIP-3 symbols are not listed: {missing}")

    def _parse_book(self, data: dict[str, Any], context: CollectionContext, wallclock: int, monotonic: int, connection_id: str, receive_sequence: int) -> OrderBookEvent | None:
        symbol = str(data.get("coin") or "").upper()
        if symbol not in self.securities:
            return None
        levels = data.get("levels") or [[], []]
        bids = self._levels(levels[0] if len(levels) > 0 else [])
        asks = self._levels(levels[1] if len(levels) > 1 else [])
        source_ms = int(data.get("time") or 0)
        return OrderBookEvent(
            instrument=self._instrument(symbol), venue=Venue.HYPERLIQUID, provider=self.name,
            source_ts_ns=source_ms * 1_000_000 or None, source_timestamp_kind="provider_book_snapshot",
            wallclock_ts_ns=wallclock, receive_ts_ns=wallclock, receive_monotonic_ns=monotonic,
            collector_run_id=context.collector_run_id, connection_id=connection_id,
            receive_sequence=receive_sequence, native_event_id=f"l2:{symbol}:{source_ms}:{stable_hash([data.get('levels')])}",
            collection_start_ts_ns=context.collection_start_ts_ns, session="24x7", sequence=source_ms or receive_sequence,
            update_type=BookUpdateType.SNAPSHOT, depth=max(len(bids), len(asks)), bids=bids, asks=asks,
            is_full_depth=False, channel="l2Book.fast", provider_depth=5, fast=True, sync_generation=0,
        )

    def _parse_open_interest(self, data: dict[str, Any], context: CollectionContext, wallclock: int, monotonic: int, connection_id: str, receive_sequence: int) -> OpenInterestEvent | None:
        symbol = str(data.get("coin") or "").upper(); ctx = data.get("ctx") or {}
        if symbol not in self.securities or ctx.get("openInterest") is None:
            return None
        return OpenInterestEvent(
            instrument=self._instrument(symbol), venue=Venue.HYPERLIQUID, provider=self.name,
            source_ts_ns=None, source_timestamp_kind="not_provided",
            wallclock_ts_ns=wallclock, receive_ts_ns=wallclock, receive_monotonic_ns=monotonic,
            collector_run_id=context.collector_run_id, connection_id=connection_id,
            receive_sequence=receive_sequence, native_event_id=f"oi:{symbol}:{connection_id}:{receive_sequence}",
            collection_start_ts_ns=context.collection_start_ts_ns, session="24x7", sequence=receive_sequence,
            open_interest=Decimal(str(ctx.get("openInterest"))), open_interest_unit="contract",
            mark_price=Decimal(str(ctx["markPx"])) if ctx.get("markPx") is not None else None,
            observation_method="activeAssetCtx",
        )

    def _parse_candle(self, row: dict[str, Any], context: CollectionContext, wallclock: int, monotonic: int, index: int) -> Bar1mEvent | None:
        symbol = str(row.get("s") or "").upper(); open_ms = int(row.get("t") or 0); close_ms = int(row.get("T") or 0)
        if symbol not in self.securities or str(row.get("i") or "") != "1m" or open_ms * 1_000_000 < context.collection_start_ts_ns or (open_ms * 1_000_000) % MINUTE_NS or close_ms * 1_000_000 >= wallclock:
            return None
        return Bar1mEvent(
            instrument=self._instrument(symbol), venue=Venue.HYPERLIQUID, provider=self.name,
            source_ts_ns=close_ms * 1_000_000, source_timestamp_kind="provider_candle_close",
            wallclock_ts_ns=wallclock, receive_ts_ns=wallclock, receive_monotonic_ns=monotonic,
            collector_run_id=context.collector_run_id, connection_id=f"{context.collector_run_id}-rest",
            receive_sequence=wallclock, record_index=index, native_event_id=f"bar1m:{symbol}:{open_ms}",
            collection_start_ts_ns=context.collection_start_ts_ns, ingest_mode="backfill", session="24x7", sequence=open_ms,
            open_ts_ns=open_ms * 1_000_000, close_ts_ns=close_ms * 1_000_000,
            open=Decimal(str(row.get("o", "0"))), high=Decimal(str(row.get("h", "0"))),
            low=Decimal(str(row.get("l", "0"))), close=Decimal(str(row.get("c", "0"))),
            volume=Decimal(str(row.get("v", "0"))), trade_count=int(row["n"]) if row.get("n") is not None else None,
        )

    def _parse_funding(self, row: dict[str, Any], context: CollectionContext, wallclock: int, monotonic: int, index: int) -> FundingRateEvent | None:
        symbol = str(row.get("coin") or "").upper(); timestamp_ms = int(row.get("time") or 0)
        if symbol not in self.securities or timestamp_ms * 1_000_000 < context.collection_start_ts_ns or timestamp_ms * 1_000_000 > wallclock:
            return None
        return FundingRateEvent(
            instrument=self._instrument(symbol), venue=Venue.HYPERLIQUID, provider=self.name,
            source_ts_ns=timestamp_ms * 1_000_000, source_timestamp_kind="provider_funding",
            wallclock_ts_ns=wallclock, receive_ts_ns=wallclock, receive_monotonic_ns=monotonic,
            collector_run_id=context.collector_run_id, connection_id=f"{context.collector_run_id}-rest",
            receive_sequence=wallclock, record_index=index, native_event_id=f"funding:{symbol}:{timestamp_ms}",
            collection_start_ts_ns=context.collection_start_ts_ns, ingest_mode="backfill", session="24x7", sequence=timestamp_ms,
            funding_rate=Decimal(str(row.get("fundingRate", "0"))),
            premium=Decimal(str(row["premium"])) if row.get("premium") is not None else None,
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
            venue=Venue.HYPERLIQUID, asset_class=security.asset_class, raw_symbol=security.provider_symbol,
            std_symbol=security.symbol, underlying=security.underlying, security_kind=security.security_kind,
            currency=security.currency, exchange=security.exchange,
        )

    @staticmethod
    def _levels(rows: list[Any]) -> list[OrderBookLevel]:
        return [OrderBookLevel(price=Decimal(str(row.get("px", "0"))), quantity=Decimal(str(row.get("sz", "0"))), order_count=int(row["n"]) if row.get("n") is not None else None) for row in rows if isinstance(row, dict)]
