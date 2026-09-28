from __future__ import annotations

import time
from typing import Any

from .models import AssetClass, EventType, Venue
from .storage_guard import StorageGuard
from .universe_config import configured_binance_securities, configured_hyperliquid_securities


class CollectionPaused(RuntimeError):
    """Raised when storage or JetStream backpressure must stop collectors."""


class MarketDataPolicyBus:
    """Enforce universe and pressure policy while preserving raw book frames."""

    def __init__(self, inner: Any, **_: Any) -> None:
        self.inner = inner
        self._allowed_hyperliquid = set(configured_hyperliquid_securities())
        self._allowed_binance = set(configured_binance_securities())
        self._guard = StorageGuard()
        self._collection_paused = False
        self._next_pressure_check = 0.0
        self._resume_eligible_since: float | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def publish_market_event(self, event: Any) -> None:
        await self._check_pressure()
        if self._allowed(event):
            await self.inner.publish_market_event(event)

    def _allowed(self, event: Any) -> bool:
        if event.event_type == EventType.DATA_GAP:
            return True
        expanded = {EventType.ORDER_BOOK, EventType.BAR_1M, EventType.FUNDING_RATE, EventType.OPEN_INTEREST}
        if event.instrument.venue == Venue.HYPERLIQUID:
            return event.instrument.raw_symbol.upper() in self._allowed_hyperliquid and event.instrument.asset_class == AssetClass.FUTURE and event.event_type in expanded
        if event.instrument.venue == Venue.BINANCE:
            return event.instrument.raw_symbol.upper() in self._allowed_binance and event.instrument.asset_class == AssetClass.FUTURE and event.event_type in expanded
        if event.event_type not in {EventType.TRADE, EventType.ORDER_BOOK, EventType.DATA_GAP}:
            return False
        return event.instrument.asset_class not in {AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERP}

    async def flush_due(self) -> int:
        return 0

    async def flush_all(self) -> int:
        return 0

    async def _check_pressure(self) -> None:
        now = time.monotonic()
        if now < self._next_pressure_check:
            if self._collection_paused:
                raise CollectionPaused("collection paused by storage or JetStream backpressure")
            return
        self._next_pressure_check = now + 10.0
        decision = self._guard.evaluate()
        stream_ratio = await self._market_stream_ratio()
        should_pause = decision.used_ratio >= 0.80 or decision.free_bytes <= 4 * 1024**3 or stream_ratio >= 0.80
        can_resume = decision.used_ratio < 0.50 and decision.free_bytes >= 6 * 1024**3 and stream_ratio < 0.50
        if should_pause:
            self._collection_paused = True
            self._resume_eligible_since = None
        elif self._collection_paused and can_resume:
            if self._resume_eligible_since is None:
                self._resume_eligible_since = now
            elif now - self._resume_eligible_since >= self._guard.settings.storage_guard.resume_stable_seconds:
                self._collection_paused = False
                self._resume_eligible_since = None
        else:
            self._resume_eligible_since = None
        if self._collection_paused:
            raise CollectionPaused("collection paused by storage or JetStream backpressure")

    async def _market_stream_ratio(self) -> float:
        probe = getattr(self.inner, "market_stream_usage_ratio", None)
        if probe is None:
            return 0.0
        try:
            return float(await probe())
        except Exception:
            return 1.0
