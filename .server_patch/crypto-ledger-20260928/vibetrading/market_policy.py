from __future__ import annotations

import time
from typing import Any

from .models import AssetClass, EventType, Venue
from .storage_guard import StorageGuard
from .universe_config import configured_binance_securities, configured_hyperliquid_securities


class CollectionPaused(RuntimeError):
    """Raised when storage or JetStream backpressure must stop collectors."""


class _CollectionPressureLatch:
    """Process-wide pause state shared by every provider reconnect attempt.

    A collector creates a fresh policy bus after a WebSocket reconnect.  The
    emergency stop must therefore not live on an individual policy instance:
    otherwise a full NATS stream can be immediately refilled by the next
    reconnect.  The runtime runs all collector tasks in one process, so this
    small process-wide latch is sufficient and has no persistence/locking IO
    in the market-data hot path.
    """

    paused = False
    resume_eligible_since: float | None = None


class _CryptoPressureLatch:
    paused = False
    resume_eligible_since: float | None = None


class MarketDataPolicyBus:
    """Enforce universe and pressure policy while preserving raw book frames."""

    def __init__(self, inner: Any, **_: Any) -> None:
        self.inner = inner
        self._allowed_hyperliquid = set(configured_hyperliquid_securities())
        self._allowed_binance = set(configured_binance_securities())
        self._guard = StorageGuard()
        self._next_pressure_check = 0.0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    @staticmethod
    def pause_crypto_collection() -> None:
        _CryptoPressureLatch.paused=True
        _CryptoPressureLatch.resume_eligible_since=None

    async def publish_market_event(self, event: Any) -> None:
        await self._check_pressure(event.instrument.asset_class == AssetClass.CRYPTO_PERP)
        if self._allowed(event):
            await self.inner.publish_market_event(event)

    def _allowed(self, event: Any) -> bool:
        if event.event_type == EventType.DATA_GAP:
            return True
        if event.instrument.asset_class == AssetClass.CRYPTO_PERP:
            return (event.instrument.venue in {Venue.BINANCE, Venue.BITGET, Venue.BYBIT}
                    and event.instrument.std_symbol in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
                    and event.event_type in {EventType.TRADE, EventType.ORDER_BOOK, EventType.FUNDING_RATE, EventType.OPEN_INTEREST})
        expanded = {EventType.ORDER_BOOK, EventType.BAR_1M, EventType.FUNDING_RATE, EventType.OPEN_INTEREST}
        if event.instrument.venue == Venue.HYPERLIQUID:
            return event.instrument.raw_symbol.upper() in self._allowed_hyperliquid and event.instrument.asset_class == AssetClass.FUTURE and event.event_type in expanded
        if event.instrument.venue == Venue.BINANCE:
            return event.instrument.raw_symbol.upper() in self._allowed_binance and event.instrument.asset_class == AssetClass.FUTURE and event.event_type in expanded
        if event.event_type == EventType.BAR_1M:
            return (event.instrument.venue == Venue.TOSS
                    and event.provider == "toss"
                    and event.instrument.asset_class == AssetClass.EQUITY
                    and event.instrument.std_symbol in {"TOSS:US:SPY", "TOSS:US:QQQ"})
        if event.event_type not in {EventType.TRADE, EventType.ORDER_BOOK, EventType.PARTICIPANT_FLOW, EventType.DATA_GAP}:
            return False
        if event.event_type == EventType.PARTICIPANT_FLOW:
            return (event.instrument.asset_class == AssetClass.FUTURE
                    and (event.instrument.venue == Venue.KIS
                         or (event.instrument.venue == Venue.KRX
                             and event.provider == "ls-kospi200-foreign"
                             and event.instrument.std_symbol == "KOSPI200_FUTURES")))
        return event.instrument.asset_class not in {AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERP}

    async def flush_due(self) -> int:
        return 0

    async def flush_all(self) -> int:
        return 0

    async def _check_pressure(self, crypto: bool = False) -> None:
        now = time.monotonic()
        latch = _CryptoPressureLatch if crypto else _CollectionPressureLatch
        if now < self._next_pressure_check:
            if latch.paused:
                raise CollectionPaused("collection paused by storage or JetStream backpressure")
            return
        self._next_pressure_check = now + 10.0
        decision = self._guard.evaluate()
        stream_ratio = await self._market_stream_ratio(True) if crypto else await self._market_stream_ratio()
        should_pause = decision.used_ratio >= 0.80 or decision.free_bytes <= 4 * 1024**3 or stream_ratio >= 0.80
        resume_ratio = self._guard.settings.storage_guard.resume_used_ratio if crypto else 0.50
        can_resume = decision.used_ratio < resume_ratio and decision.free_bytes >= 6 * 1024**3 and stream_ratio < 0.50
        if should_pause:
            latch.paused = True
            latch.resume_eligible_since = None
        elif latch.paused and can_resume:
            if latch.resume_eligible_since is None:
                latch.resume_eligible_since = now
            elif now - latch.resume_eligible_since >= self._guard.settings.storage_guard.resume_stable_seconds:
                latch.paused = False
                latch.resume_eligible_since = None
        else:
            latch.resume_eligible_since = None
        if latch.paused:
            raise CollectionPaused("collection paused by storage or JetStream backpressure")

    async def _market_stream_ratio(self, crypto: bool = False) -> float:
        probe = getattr(self.inner, "crypto_stream_usage_ratio" if crypto else "market_stream_usage_ratio", None)
        if probe is None:
            return 0.0
        try:
            return float(await probe())
        except Exception:
            return 1.0
