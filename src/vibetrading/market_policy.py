from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from .models import AssetClass, EventType, Venue
from .bus import allowed_supplement
from .storage_guard import StorageGuard
from .universe_config import configured_binance_securities, configured_hyperliquid_securities


class CollectionPaused(RuntimeError):
    """A classified, intentional collection pause."""

    def __init__(self, reason: str, *, primary_reason: str, blocking_reasons: tuple[str, ...]) -> None:
        self.reason = reason
        self.primary_reason = primary_reason
        self.blocking_reasons = blocking_reasons
        super().__init__(f"collection paused: {reason}")


def crypto_storage_blocker(settings: Any) -> str | None:
    if str(getattr(settings, 'env', {}).get('CRYPTO_REQUIRE_STORAGE_READY', 'false')).lower() != 'true':
        return None
    path = Path(settings.data_root) / 'crypto_state' / 'health' / 'crypto-storage.json'
    try:
        health = json.loads(path.read_text(encoding='utf-8'))
        age = (time.time_ns() - int(health.get('ts_ns', 0))) / 1_000_000_000
    except (OSError, ValueError, TypeError):
        return 'storage_health_unavailable'
    if age > 120 or age < -5:
        return 'storage_heartbeat_stale'
    if health.get('state') == 'initializing':
        return 'storage_initializing'
    if health.get('state') != 'running' or health.get('ready') is not True:
        return 'storage_not_ready'
    return None


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
    primary_reason: str | None = None
    blocking_reasons: tuple[str, ...] = ()
    paused_since: float | None = None


class _CryptoPressureLatch:
    paused = False
    resume_eligible_since: float | None = None
    primary_reason: str | None = None
    blocking_reasons: tuple[str, ...] = ()
    paused_since: float | None = None


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
    def pause_crypto_collection(reason: str = "nats_publish_timeout") -> None:
        MarketDataPolicyBus._pause(_CryptoPressureLatch, reason, (reason,))

    @staticmethod
    def pause_market_collection(reason: str) -> None:
        MarketDataPolicyBus._pause(_CollectionPressureLatch, reason, (reason,))

    @staticmethod
    def _pause(latch: Any, reason: str, blocking: tuple[str, ...]) -> None:
        now = time.monotonic()
        if not latch.paused:
            latch.primary_reason = reason
            latch.paused_since = now
        latch.paused = True
        latch.blocking_reasons = blocking
        latch.resume_eligible_since = None

    @staticmethod
    def pressure_state(crypto: bool = False) -> dict[str, Any]:
        latch = _CryptoPressureLatch if crypto else _CollectionPressureLatch
        return {"paused": latch.paused, "primary_reason": latch.primary_reason,
                "blocking_reasons": list(latch.blocking_reasons), "paused_since": latch.paused_since}

    async def publish_market_event(self, event: Any) -> None:
        await self._check_pressure(event.instrument.asset_class == AssetClass.CRYPTO_PERP)
        if self._allowed(event):
            await self.inner.publish_market_event(event)

    def _allowed(self, event: Any) -> bool:
        # Retired feeds must not return through stale runtime configuration.
        if event.provider in {"bitget-perp", "bybit-perp", "hyperliquid", "binance"}:
            return False
        if event.instrument.venue == Venue.KIS and event.instrument.asset_class == AssetClass.OPTION:
            return False
        if event.event_type == EventType.DATA_GAP:
            return True
        if allowed_supplement(event):
            return True
        if event.instrument.asset_class == AssetClass.CRYPTO_PERP:
            return (event.instrument.venue in {Venue.BINANCE, Venue.GRVT, Venue.ARCUS}
                    and event.instrument.std_symbol in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
                    and event.event_type in {EventType.TRADE, EventType.ORDER_BOOK, EventType.FUNDING_RATE, EventType.OPEN_INTEREST})
        expanded = {EventType.ORDER_BOOK, EventType.BAR_1M, EventType.FUNDING_RATE, EventType.OPEN_INTEREST}
        if event.instrument.venue == Venue.HYPERLIQUID:
            return event.instrument.raw_symbol.upper() in self._allowed_hyperliquid and event.instrument.asset_class == AssetClass.FUTURE and event.event_type in expanded
        if event.instrument.venue == Venue.BINANCE:
            return event.instrument.raw_symbol.upper() in self._allowed_binance and event.instrument.asset_class == AssetClass.FUTURE and event.event_type in expanded
        if event.event_type == EventType.BAR_1M:
            if event.instrument.venue == Venue.KIWOOM:
                return event.instrument.asset_class == AssetClass.ETF
            return (event.instrument.venue == Venue.TOSS
                    and event.provider == "toss"
                    and event.instrument.asset_class == AssetClass.EQUITY
                    and event.instrument.std_symbol in {"TOSS:US:SPY", "TOSS:US:QQQ"})
        if event.event_type == EventType.ETF_NAV:
            return event.instrument.venue == Venue.KIWOOM and event.instrument.asset_class == AssetClass.ETF
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
        if now < self._next_pressure_check and not latch.paused:
            return
        self._next_pressure_check = now + (5.0 if latch.paused else 10.0)
        decision = self._guard.evaluate()
        probe = await self._stream_probe(crypto)
        blocking: list[str] = []
        if getattr(decision, "probe_error", None) is not None:
            blocking.append("disk_probe_unavailable")
        elif decision.free_bytes <= decision.reserve_bytes:
            blocking.append("disk_reserve")
        state_free=getattr(decision,'state_free_bytes',None)
        state_reserve=getattr(decision,'state_reserve_bytes',0)
        state_margin=getattr(decision,'state_resume_margin_bytes',0)
        if state_free is not None and state_free<=state_reserve:
            blocking.append('state_disk_reserve')
        state_can_resume=(state_free is None or state_free>=state_reserve+state_margin)
        nats_free=getattr(decision,'nats_free_bytes',None)
        nats_reserve=getattr(decision,'nats_reserve_bytes',0)
        nats_margin=getattr(decision,'nats_resume_margin_bytes',0)
        if nats_free is not None and nats_free<=nats_reserve:
            blocking.append('nats_disk_reserve')
        nats_can_resume=(nats_free is None or nats_free>=nats_reserve+nats_margin)
        if crypto:
            storage_reason=await asyncio.to_thread(crypto_storage_blocker,self._guard.settings)
            if storage_reason is not None:
                blocking.append(storage_reason)
        if probe["status"] == "unavailable":
            # A single STREAM.INFO timeout is a telemetry blip. Pause only
            # after repeated failures, and keep an existing pause latched until
            # a fresh probe confirms the stream is safe again.
            if int(probe.get("consecutive_failures", 0)) >= 3:
                blocking.append("nats_control_plane_unavailable")
        elif probe.get("config_mismatch"):
            blocking.append("configuration_mismatch")
        elif probe.get("ratio") is not None and probe["ratio"] >= 0.80:
            blocking.append("nats_stream_capacity")

        if blocking:
            reason = blocking[0]
            self._pause(latch, reason, tuple(blocking))
        elif latch.paused:
            raw_stream_ratio = probe.get("ratio")
            enough_disk = (getattr(decision, "probe_error", None) is None
                           and decision.free_bytes >= decision.reserve_bytes + decision.resume_margin_bytes
                           and state_can_resume and nats_can_resume)
            can_resume = (enough_disk and raw_stream_ratio is not None
                          and float(raw_stream_ratio) < 0.50 and probe["status"] == "fresh")
            if can_resume:
                if latch.resume_eligible_since is None:
                    latch.resume_eligible_since = now
                elif now - latch.resume_eligible_since >= self._guard.settings.storage_guard.resume_stable_seconds:
                    latch.paused = False
                    latch.resume_eligible_since = None
                    latch.primary_reason = None
                    latch.blocking_reasons = ()
                    latch.paused_since = None
            else:
                latch.resume_eligible_since = None
        elif probe["status"] == "cached":
            # A short-lived control-plane failure with safe estimated headroom
            # degrades telemetry, but does not pause an otherwise healthy feed.
            cache_age = float(probe.get("cache_age_seconds", 0.0))
            buffered = getattr(self.inner, "stats", {})
            queued_bytes = int(buffered.get("queued_bytes", 0)) if isinstance(buffered, dict) else 0
            bus = self.inner
            while hasattr(bus, "inner"):
                bus = bus.inner
            rate = float(getattr(bus, "recent_publish_bytes_per_second", 0.0) or 0.0)
            floor = int(self._guard.settings.env.get("COLLECTOR_INGRESS_FLOOR_BYTES_PER_SECOND", str(1024 * 1024)))
            rate = max(rate * 2.0, float(floor))
            estimated = int(probe.get("bytes") or 0) + int(rate * cache_age) + queued_bytes
            max_bytes = int(probe.get("max_bytes") or 0)
            if max_bytes > 0 and estimated >= max_bytes * 0.80:
                self._pause(latch, "nats_stream_capacity", ("nats_stream_capacity_estimated",))
        else:
            latch.resume_eligible_since = None
        if latch.paused:
            if not blocking:
                if probe["status"] != "fresh":
                    blocking.append("nats_probe_recovery_pending")
                else:
                    stream_ratio = probe.get("ratio")
                    if stream_ratio is None or float(stream_ratio) >= 0.50:
                        blocking.append("nats_resume_hysteresis")
                    if not (getattr(decision, "probe_error", None) is None
                            and decision.free_bytes >= decision.reserve_bytes + decision.resume_margin_bytes
                            and state_can_resume):
                        blocking.append("disk_resume_hysteresis")
                    if latch.resume_eligible_since is None:
                        blocking.append("recovery_stability_window")
            latch.blocking_reasons = tuple(blocking)
            primary = latch.primary_reason or "storage_or_nats_backpressure"
            raise CollectionPaused(primary, primary_reason=primary,
                                   blocking_reasons=latch.blocking_reasons or (primary,))

    async def _stream_probe(self, crypto: bool = False) -> dict[str, Any]:
        stream = "CRYPTO_DATA" if crypto else self._guard.settings.nats.stream_name
        bus = self.inner
        while hasattr(bus, "inner"):
            bus = bus.inner
        probe = getattr(bus, "stream_probe", None)
        if probe is not None:
            try:
                result = await probe(stream)
                return {"status": result.status, "ratio": result.ratio, "bytes": result.bytes,
                        "max_bytes": result.max_bytes, "generation": result.generation,
                        "cache_age_seconds": max(0.0, time.monotonic() - result.observed_at),
                        "config_mismatch": result.config_mismatch, "error": result.error,
                        "consecutive_failures": result.consecutive_failures}
            except Exception as exc:
                return {"status": "unavailable", "ratio": None, "error": type(exc).__name__}
        # Compatibility with in-memory buses and older test adapters.
        legacy = getattr(bus, "crypto_stream_usage_ratio" if crypto else "market_stream_usage_ratio", None)
        if legacy is None:
            return {"status": "unavailable", "ratio": None, "error": "probe_missing"}
        try:
            ratio = float(await legacy())
            return {"status": "fresh", "ratio": ratio, "bytes": 0, "max_bytes": 1,
                    "generation": None, "cache_age_seconds": 0.0, "config_mismatch": False,
                    "consecutive_failures": 0}
        except Exception as exc:
            return {"status": "unavailable", "ratio": None, "error": type(exc).__name__,
                    "consecutive_failures": int(getattr(bus, "_stream_probe_failures", {}).get(stream, 3))}
