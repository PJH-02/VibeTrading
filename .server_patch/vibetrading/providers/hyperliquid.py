from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiohttp

from vibetrading.bus import EventBus
from vibetrading.models import (
    Bar1mEvent,
    FundingRateEvent,
    InstrumentId,
    Venue,
    utc_now_ns,
)
from vibetrading.universe_config import HyperliquidSecurity


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
    async def _post(session: aiohttp.ClientSession, payload: dict[str, Any]) -> Any:
        async with session.post(HyperliquidCatalog.info_url, json=payload) as response:
            response.raise_for_status()
            return await response.json()


class HyperliquidCollector:
    name = "hyperliquid"
    candle_poll_seconds = 30.0
    funding_poll_seconds = 300.0

    def __init__(self, securities: dict[str, HyperliquidSecurity]) -> None:
        self.securities = {symbol.upper(): security for symbol, security in securities.items()}
        self._published_event_ids: set[str] = set()

    async def run(self, bus: EventBus, duration_sec: float | None = None) -> None:
        if not self.securities:
            raise RuntimeError("Hyperliquid universe is empty")
        await self._validate_symbols()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + duration_sec if duration_sec else None
        await self._collect(bus, initial=True)
        next_candle = loop.time() + self.candle_poll_seconds
        next_funding = loop.time() + self.funding_poll_seconds
        logger.info(
            "Hyperliquid collector polling %d HIP-3 symbols for completed 1m candles and hourly funding",
            len(self.securities),
        )
        while deadline is None or loop.time() < deadline:
            now = loop.time()
            wake_at = min(next_candle, next_funding, deadline if deadline is not None else float("inf"))
            await asyncio.sleep(max(0.05, wake_at - now))
            now = loop.time()
            if deadline is not None and now >= deadline:
                break
            if now >= next_candle:
                await self._collect_candles(bus, lookback_minutes=10)
                next_candle = now + self.candle_poll_seconds
            if now >= next_funding:
                await self._collect_funding(bus, lookback_hours=2)
                next_funding = now + self.funding_poll_seconds

    async def _collect(self, bus: EventBus, *, initial: bool) -> None:
        await self._collect_candles(bus, lookback_minutes=30 if initial else 10)
        await self._collect_funding(bus, lookback_hours=24 if initial else 2)

    async def _collect_candles(self, bus: EventBus, *, lookback_minutes: int) -> None:
        now_ms = int(time.time() * 1000)
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for symbol in sorted(security.provider_symbol for security in self.securities.values()):
                rows = await HyperliquidCatalog._post(
                    session,
                    {
                        "type": "candleSnapshot",
                        "req": {
                            "coin": symbol,
                            "interval": "1m",
                            "startTime": now_ms - lookback_minutes * 60_000,
                            "endTime": now_ms,
                        },
                    },
                )
                if not isinstance(rows, list):
                    raise RuntimeError(f"Hyperliquid candleSnapshot returned non-list for {symbol}")
                for row in rows:
                    if isinstance(row, dict) and int(row.get("T") or 0) < now_ms:
                        await self._publish_unique(bus, self._parse_candle(row))

    async def _collect_funding(self, bus: EventBus, *, lookback_hours: int) -> None:
        now_ms = int(time.time() * 1000)
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for symbol in sorted(security.provider_symbol for security in self.securities.values()):
                rows = await HyperliquidCatalog._post(
                    session,
                    {
                        "type": "fundingHistory",
                        "coin": symbol,
                        "startTime": now_ms - lookback_hours * 3_600_000,
                        "endTime": now_ms,
                    },
                )
                if not isinstance(rows, list):
                    raise RuntimeError(f"Hyperliquid fundingHistory returned non-list for {symbol}")
                for row in rows:
                    if isinstance(row, dict):
                        await self._publish_unique(bus, self._parse_funding(row))

    async def _validate_symbols(self) -> None:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            payload = await HyperliquidCatalog._post(session, {"type": "allPerpMetas"})
        listed = {
            str(asset.get("name") or "").upper()
            for meta in payload if isinstance(payload, list) and isinstance(meta, dict)
            for asset in meta.get("universe", []) if isinstance(asset, dict)
        }
        missing = sorted(set(self.securities) - listed)
        if missing:
            raise RuntimeError(f"Hyperliquid configured HIP-3 symbols are not listed: {missing}")

    async def _publish_unique(self, bus: EventBus, event: Bar1mEvent | FundingRateEvent) -> None:
        if event.event_id in self._published_event_ids:
            return
        await bus.publish_market_event(event)
        self._published_event_ids.add(event.event_id)
        if len(self._published_event_ids) > 20_000:
            self._published_event_ids.pop()

    def _instrument(self, coin: str) -> InstrumentId:
        security = self.securities.get(coin.upper())
        if security is None:
            raise ValueError(f"Hyperliquid symbol is not in the explicit security allowlist: {coin}")
        return InstrumentId(
            venue=Venue.HYPERLIQUID,
            asset_class=security.asset_class,
            raw_symbol=coin,
            std_symbol=security.symbol,
            underlying=security.underlying,
            security_kind=security.security_kind,
            currency=security.currency,
            exchange=security.exchange,
        )

    def _parse_candle(self, row: dict[str, Any]) -> Bar1mEvent:
        coin = str(row.get("s") or "")
        if str(row.get("i") or "") != "1m":
            raise ValueError(f"Unexpected Hyperliquid candle interval: {row.get('i')!r}")
        open_ms = int(row.get("t") or 0)
        close_ms = int(row.get("T") or 0)
        if open_ms <= 0 or close_ms <= 0:
            raise ValueError(f"Hyperliquid candle is missing timestamps: {row}")
        return Bar1mEvent(
            instrument=self._instrument(coin),
            venue=Venue.HYPERLIQUID,
            provider=self.name,
            source_ts_ns=close_ms * 1_000_000,
            receive_ts_ns=utc_now_ns(),
            session="24x7",
            sequence=open_ms,
            open_ts_ns=open_ms * 1_000_000,
            close_ts_ns=close_ms * 1_000_000,
            open=Decimal(str(row.get("o", "0"))),
            high=Decimal(str(row.get("h", "0"))),
            low=Decimal(str(row.get("l", "0"))),
            close=Decimal(str(row.get("c", "0"))),
            volume=Decimal(str(row.get("v", "0"))),
            trade_count=int(row["n"]) if row.get("n") is not None else None,
        )

    def _parse_funding(self, row: dict[str, Any]) -> FundingRateEvent:
        coin = str(row.get("coin") or "")
        timestamp_ms = int(row.get("time") or 0)
        if timestamp_ms <= 0:
            raise ValueError(f"Hyperliquid funding row is missing time: {row}")
        return FundingRateEvent(
            instrument=self._instrument(coin),
            venue=Venue.HYPERLIQUID,
            provider=self.name,
            source_ts_ns=timestamp_ms * 1_000_000,
            receive_ts_ns=utc_now_ns(),
            session="24x7",
            sequence=timestamp_ms,
            funding_rate=Decimal(str(row.get("fundingRate", "0"))),
            premium=Decimal(str(row["premium"])) if row.get("premium") is not None else None,
        )
