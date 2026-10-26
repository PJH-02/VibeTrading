from __future__ import annotations

import asyncio
import logging
import json
import signal
import os
import time
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bus import BufferedMarketEventBus, MarketEventPacket, NatsJetStreamBus, prepare_market_event, publish_prepared_market_event
from .compactor import ParquetCompactor
from .archive import ArchiveMaintenance
from .storage_worker import StorageWorker
from .strategy_runner import SharedStrategyRunner
from .session_alerts import session_state
from .market_policy import MarketDataPolicyBus
from .storage_guard import StorageGuard
from .maintenance_gate import maintenance_lock
from .health import touch as health_touch
from .gap_ledger import CollectionGapLedger
from .settings import get_settings
from .models import AssetClass, DataGapEvent, EventType, InstrumentId, Venue
from .providers.crypto_perp import SYMBOLS as CRYPTO_SYMBOLS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeJob:
    provider: str
    symbols: str = ""


_TRANSIENT_COLLECTOR_ERRORS = (
    "code=1006",
    "no close frame received",
    "ping timeout",
    "nats: timeout",
    "timed out",
    "timeout",
    "connection reset",
    "temporarily unavailable",
    "server disconnected",
    "502",
    "503",
    "504",
    "429",
)


def _collector_error_details(exc: BaseException) -> tuple[str, str, bool]:
    message = str(exc).strip() or repr(exc)
    cause = exc.__cause__
    if cause is not None:
        cause_text = f"{type(cause).__name__}: {str(cause).strip() or repr(cause)}"
    else:
        cause_text = ""
    reason = f"{type(exc).__name__}: {message}"
    lowered = f"{reason} {cause_text}".lower()
    transient = any(marker in lowered for marker in _TRANSIENT_COLLECTOR_ERRORS)
    # Normal gateway rotation (1000/1001) needs a new connection and snapshot,
    # but isn't an authentication/parser failure. Other close codes retain
    # their classification, including policy and protocol rejections.
    from websockets.exceptions import ConnectionClosedOK
    if isinstance(exc, ConnectionClosedOK):
        transient = True
    # aiohttp's ClientConnectionResetError renders as "Cannot write to
    # closing transport" and its class name has no spaces. String matching
    # "connection reset" misses it and incorrectly pages on the first EOF.
    import aiohttp
    if isinstance(exc,(ConnectionError,aiohttp.ClientConnectionError)) and not isinstance(
            exc,(aiohttp.ClientConnectorCertificateError,aiohttp.ClientSSLError)):
        transient = True
    return reason[:800], cause_text[:800], transient


class _CollectorReadinessBus:
    """Report readiness only after the first event reaches JetStream."""

    _market_packet_protocol = 1

    def __init__(self, inner: NatsJetStreamBus, on_ready: Any, provider: str) -> None:
        self.inner = inner
        self.on_ready = on_ready
        self.ready = False
        self.provider = provider
        self._last_health = 0.0
        self.last_published_monotonic = time.monotonic()
        self.last_event_ts_ns: int | None = None
        self.published_count = 0
        self.published_bytes = 0
        self.symbol_last_seen_ns: dict[str, int] = {}
        self.first_published_monotonic: float | None = None
        self._recovery_ratio: float | None = None

    async def publish_market_event(self, event: Any) -> None:
        await self.publish_market_packet(prepare_market_event(event))

    _packet_public_method = publish_market_event

    async def publish_market_packet(self, packet: MarketEventPacket) -> None:
        await publish_prepared_market_event(self.inner, packet)
        self.record_durable_success(packet)

    def record_durable_success(self, packet: MarketEventPacket) -> None:
        event = packet.snapshot
        self.last_published_monotonic = time.monotonic()
        if self.first_published_monotonic is None:self.first_published_monotonic=self.last_published_monotonic
        self.last_event_ts_ns = time.time_ns()
        if event.event_type != EventType.DATA_GAP and time.time_ns()-int(event.source_ts_ns or event.receive_ts_ns) <= 120_000_000_000:
            symbol = event.instrument.std_symbol if event.instrument.asset_class==AssetClass.CRYPTO_PERP else event.instrument.raw_symbol
            self.symbol_last_seen_ns[symbol] = self.last_event_ts_ns
        self.published_count += 1
        self.published_bytes += packet.byte_length
        if time.monotonic() - self._last_health >= 30:
            health_touch(self.inner.settings,f"collectors-{self.provider}",detail={"provider":self.provider,"phase":"publishing","last_event_ts_ns":self.last_event_ts_ns,"published_count":self.published_count,"published_bytes":self.published_bytes,"symbol_last_seen_ns":self.symbol_last_seen_ns,"event_type":event.event_type.value,"wallclock_ts_ns":event.wallclock_ts_ns})
            self._last_health = time.monotonic()

    async def confirm_recovered(self)->bool:
        if self.ready:return True
        if self.first_published_monotonic is None:return False
        now=time.monotonic();stable_seconds=max(10,float(self.inner.settings.env.get("COLLECTOR_RECOVERY_STABLE_SECONDS","60")))
        if now-self.first_published_monotonic<stable_seconds or now-self.last_published_monotonic>30:return False
        crypto = self.provider.endswith("-perp")
        stream = "CRYPTO_DATA" if crypto else getattr(self.inner,"market_stream_name","MARKET_DATA")
        probe = await self.inner.stream_probe(stream) if hasattr(self.inner, "stream_probe") else None
        if probe is not None:
            if probe.status != "fresh" or probe.config_mismatch:
                return False
            ratio = float(probe.ratio or 0.0)
        else:
            try:
                ratio = await self.inner.crypto_stream_usage_ratio() if crypto else await self.inner.market_stream_usage_ratio()
            except Exception:
                return False
        if self._recovery_ratio is None:
            self._recovery_ratio=ratio
        backlog_healthy=ratio<0.50 or ratio<=max(0.0,self._recovery_ratio-0.02)
        if not backlog_healthy:return False
        self.ready=True;await self.on_ready();return True

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def _jobs_from_env() -> list[RuntimeJob]:
    raw = os.getenv("VIBETRADING_COLLECTORS", "")
    jobs: list[RuntimeJob] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        provider, _, symbols = value.partition(":")
        jobs.append(RuntimeJob(provider=provider.lower(), symbols=symbols))
    return jobs


def _load_config(path: Path | None) -> tuple[list[RuntimeJob], list[tuple[str, str]]]:
    if path is not None and path.exists():
        try:
            import yaml
            payload: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            collectors = [RuntimeJob(str(item.get("provider", "")).lower(), str(item.get("symbols", "")))
                          for item in payload.get("collectors", []) if isinstance(item, dict) and item.get("provider")]
            strategies = [(str(item.get("job_id")), str(item.get("strategy_module")))
                          for item in payload.get("strategies", [])
                          if isinstance(item, dict) and item.get("job_id") and item.get("strategy_module")]
            return collectors, strategies
        except ImportError as exc:
            raise RuntimeError("PyYAML is required for runtime config") from exc
    strategies: list[tuple[str, str]] = []
    for spec in os.getenv("VIBETRADING_STRATEGY_JOBS", "").split(","):
        job_id, sep, module = spec.partition(":")
        if sep and job_id and module:
            strategies.append((job_id, module))
    return _jobs_from_env(), strategies


async def _collector_loop(job: RuntimeJob, bus: NatsJetStreamBus) -> None:
    gap_ledger = CollectionGapLedger(bus.settings.state_dir / "collection_gaps.sqlite3")
    backoff = 2.0
    announced_running = False
    cancelled = False
    last_error_alert: tuple[str, float] | None = None
    last_buffer_stats: dict[str,int] = {}
    consecutive_failures = 0
    outage_started: float | None = None
    pressure_paused = False
    pressure_probe_backoff = 5.0

    async def publish_crypto_disconnect(reason: str) -> None:
        if not job.provider.endswith("-perp") or bus._js is None:
            return
        venue=Venue(job.provider.split("-",1)[0])
        for symbol in CRYPTO_SYMBOLS:
            try:
                await bus.publish_market_event(DataGapEvent(
                    instrument=InstrumentId(venue=venue,asset_class=AssetClass.CRYPTO_PERP,
                                            raw_symbol=symbol,std_symbol=symbol,currency="USDT"),
                    venue=venue,provider=job.provider,session="CRYPTO_24X7",
                    reason=reason,resync_status="collector_reconnecting"))
            except Exception:
                logger.warning("failed to publish crypto disconnect gap provider=%s symbol=%s",job.provider,symbol)

    def caused_by_collection_pause(exc: BaseException) -> bool:
        """Providers may wrap a task failure while preserving its cause."""
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            from .market_policy import CollectionPaused
            if isinstance(current, CollectionPaused):
                return True
            next_exc = current.__cause__ or current.__context__
            current = next_exc if isinstance(next_exc, BaseException) else None
        return False

    def find_collection_pause(exc: BaseException) -> Any | None:
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            from .market_policy import CollectionPaused
            if isinstance(current, CollectionPaused):
                return current
            next_exc = current.__cause__ or current.__context__
            current = next_exc if isinstance(next_exc, BaseException) else None
        return None

    def find_nats_publish_error(exc: BaseException) -> Any | None:
        from .bus import NatsPublishError
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, NatsPublishError):
                return current
            next_exc = current.__cause__ or current.__context__
            current = next_exc if isinstance(next_exc, BaseException) else None
        return None

    def provider_open() -> bool:
        if job.provider == "ls-kospi200-foreign":
            from .providers.ls_market import krx_futures_session
            return krx_futures_session() is not None
        if job.provider == "ls-overseas-delayed":
            return session_state("US_FUTURES")[0]
        if job.provider in {"binance", "hyperliquid", "binance-perp", "bitget-perp", "bybit-perp", "arcus-perp", "grvt-perp"}:
            return True
        if job.provider == "toss":
            return session_state("KR_EQUITY_ETF")[0] or session_state("US_EQUITY_ETF")[0]
        if job.provider == "kiwoom":
            return session_state("KR_EQUITY_ETF")[0]
        if job.provider == "kis" and "overseas" in job.symbols.lower():
            return session_state("US_FUTURES")[0]
        return session_state("KR_DERIVATIVES")[0] or session_state("KR_EQUITY_ETF")[0]

    while True:
        if pressure_paused:
            state = MarketDataPolicyBus.pressure_state(job.provider.endswith("-perp"))
            health_touch(bus.settings, f"collectors-{job.provider}", detail={
                "provider": job.provider, "phase": "paused",
                "reason": state.get("primary_reason") or "nats_control_plane_unavailable",
                "primary_reason": state.get("primary_reason"),
                "blocking_reasons": state.get("blocking_reasons", []),
                "last_event_ts_ns": last_buffer_stats.get("last_event_ts_ns"),
            })
            await asyncio.sleep(pressure_probe_backoff)
            try:
                if bus._js is None:
                    await bus.connect()
                pressure_bus = MarketDataPolicyBus(bus)
                await pressure_bus._check_pressure(job.provider.endswith("-perp"))
            except Exception as exc:
                pause = find_collection_pause(exc)
                if pause is not None:
                    pressure_probe_backoff = 5.0
                else:
                    pressure_probe_backoff = min(30.0, pressure_probe_backoff * 2.0)
                    logger.warning("pressure probe unavailable provider=%s error=%s", job.provider, type(exc).__name__)
                continue
            pressure_paused = False
            pressure_probe_backoff = 5.0
            logger.info("collection pressure recovered provider=%s", job.provider)
        health_touch(bus.settings,f"collectors-{job.provider}",detail={"provider":job.provider,"phase":"connecting"})
        if job.provider not in {"binance", "hyperliquid", "binance-perp", "bitget-perp", "bybit-perp", "arcus-perp", "grvt-perp"} and not provider_open():
            health_touch(bus.settings,f"collectors-{job.provider}",detail={"provider":job.provider,"phase":"market_closed"})
            if announced_running and bus._js is not None:
                try:
                    await bus.publish_system(
                        "collector_stop",
                        {"provider": job.provider, "symbols": job.symbols, "reason": "market_closed"},
                    )
                except Exception:
                    logger.warning("failed to publish market-close state provider=%s", job.provider, exc_info=True)
                announced_running = False
            # Do not authenticate or open vendor WebSockets outside their
            # configured sessions. This removes predictable close/401 noise
            # and needless retries while markets are shut.
            await asyncio.sleep(30.0)
            continue
        attempt_started = time.monotonic()
        try:
            if bus._js is None:
                await bus.connect()
            from .cli import _collector_for_args
            collector = _collector_for_args(type("Args", (), {"provider": job.provider, "symbols": job.symbols})())

            async def mark_ready() -> None:
                nonlocal announced_running, consecutive_failures, outage_started, backoff

                await asyncio.to_thread(gap_ledger.end, job.provider)
                if not announced_running:
                    await bus.publish_system("collector_start", {"provider": job.provider, "symbols": job.symbols})
                    announced_running = True
                consecutive_failures = 0
                outage_started = None
                backoff = 2.0

            readiness_bus = _CollectorReadinessBus(bus, mark_ready, job.provider)
            if bus.settings.env.get("COLLECTOR_SPLIT_FANOUT", "false").lower() == "true":
                from .market_fanout import SplitMarketEventBus
                def invalidate_feed(reason: str) -> None:
                    if job.provider.endswith("-perp"):
                        MarketDataPolicyBus.pause_crypto_collection("fanout_" + reason)
                    else:
                        MarketDataPolicyBus.pause_market_collection("fanout_" + reason)
                buffered_bus = SplitMarketEventBus(bus,
                    on_durable=readiness_bus.record_durable_success, on_invalid=invalidate_feed,
                    max_items=int(bus.settings.env.get("COLLECTOR_PUBLISH_BUFFER_ITEMS", "128")),
                    max_bytes=int(bus.settings.env.get("COLLECTOR_PUBLISH_BUFFER_BYTES", str(64*1024*1024))),
                    live_max_items=int(bus.settings.env.get("COLLECTOR_LIVE_BUFFER_ITEMS", "256")),
                    live_max_bytes=int(bus.settings.env.get("COLLECTOR_LIVE_BUFFER_BYTES", str(8*1024*1024))))
            else:
                buffered_bus = BufferedMarketEventBus(readiness_bus,
                    max_bytes=int(bus.settings.env.get("COLLECTOR_PUBLISH_BUFFER_BYTES",str(64*1024*1024))))
            policy_bus = MarketDataPolicyBus(buffered_bus)
            try:
                async def watch_provider() -> None:
                    while True:
                        await asyncio.sleep(20.0)
                        buffered_bus._raise_error()
                        age = time.monotonic() - readiness_bus.last_published_monotonic
                        await readiness_bus.confirm_recovered()
                        crypto = job.provider.endswith("-perp")
                        stream_probe = await bus.stream_probe("CRYPTO_DATA" if crypto else bus.market_stream_name)
                        pressure = StorageGuard(bus.settings).evaluate().as_dict()
                        health_touch(bus.settings,f"collectors-{job.provider}",detail={
                            "provider":job.provider,
                            "phase":"publishing" if readiness_bus.ready else "recovering" if readiness_bus.first_published_monotonic else "connecting",
                            "last_event_ts_ns":readiness_bus.last_event_ts_ns,
                            "published_count":readiness_bus.published_count,
                            "published_bytes":readiness_bus.published_bytes,
                            "symbol_last_seen_ns":readiness_bus.symbol_last_seen_ns,
                            "event_age_seconds":round(age,1),
                            "stream_probe":stream_probe.as_dict(),
                            "disk":pressure,
                            "publisher":buffered_bus.stats,
                        })
                        stale_after=60 if job.provider.endswith("-perp") else 180
                        if age > stale_after and provider_open():
                            if job.provider.endswith("-perp"):
                                gap_start=readiness_bus.last_event_ts_ns or time.time_ns()-int(age*1_000_000_000)
                                await asyncio.to_thread(gap_ledger.start,job.provider,job.symbols,"market_event_stalled",gap_start)
                            raise RuntimeError(f"{job.provider} published no market event for {int(age)} seconds")

                collector_task = asyncio.create_task(collector.run(policy_bus),name=f"{job.provider}-run")
                watcher_task = asyncio.create_task(watch_provider(),name=f"{job.provider}-watchdog")
                try:
                    done, _ = await asyncio.wait({collector_task, watcher_task},return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        await task
                finally:
                    collector_task.cancel(); watcher_task.cancel()
                    await asyncio.gather(collector_task, watcher_task,return_exceptions=True)
            finally:
                try:
                    await buffered_bus.close(timeout_seconds=5)
                finally:
                    last_buffer_stats=buffered_bus.stats
            backoff = 2.0
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:
            # Backpressure is an intentional, recoverable collection stop.
            # Do not turn it into a Telegram incident or exponential retry
            # storm; the process-wide policy latch will reopen collection only
            # after the NATS/disk recovery criteria have held long enough.
            pause = find_collection_pause(exc)
            publish_error = find_nats_publish_error(exc)
            intentional_pause = pause is not None
            nats_backpressure = publish_error is not None
            if nats_backpressure:
                if job.provider.endswith("-perp"):
                    MarketDataPolicyBus.pause_crypto_collection(publish_error.reason)
                else:
                    MarketDataPolicyBus.pause_market_collection(publish_error.reason)
                from .market_policy import CollectionPaused
                pause = CollectionPaused(publish_error.reason, primary_reason=publish_error.reason,
                                         blocking_reasons=(publish_error.reason,))
            if intentional_pause or nats_backpressure:
                pressure_paused = True
                primary_reason = (pause.primary_reason if pause is not None else "nats_publish_error")
                blocking_reasons = list(pause.blocking_reasons if pause is not None else (primary_reason,))
                await asyncio.to_thread(gap_ledger.start, job.provider, job.symbols, primary_reason,
                                        None,last_buffer_stats,last_buffer_stats.get("pending",0),last_buffer_stats.get("queued_bytes",0))
                await publish_crypto_disconnect(primary_reason)
                health_touch(
                    bus.settings,
                    f"collectors-{job.provider}",
                    detail={"provider": job.provider, "phase": "paused", "reason": primary_reason,
                            "primary_reason": primary_reason, "blocking_reasons": blocking_reasons},
                )
                try:
                    if bus._js is not None and announced_running:
                        await bus.publish_system(
                            "collector_stop",
                            {"provider": job.provider, "symbols": job.symbols, "reason": primary_reason},
                        )
                        announced_running = False
                except Exception:
                    logger.warning("failed to publish collector backpressure state provider=%s", job.provider, exc_info=True)
                # The outer-loop pressure gate waits on NATS/disk only. It
                # does not reconstruct the exchange adapter until recovered.
                continue
            now = time.monotonic()
            uptime = now - attempt_started
            # A connection that carried data for a meaningful period is a new
            # outage, not another failure in the previous streak.
            if uptime >= 60.0:
                consecutive_failures = 0
                outage_started = None
                backoff = 2.0
            consecutive_failures += 1
            outage_started = outage_started or now
            reason, cause, transient = _collector_error_details(exc)
            await asyncio.to_thread(gap_ledger.start, job.provider, job.symbols, reason,None,last_buffer_stats,
                                    last_buffer_stats.get("pending",0),last_buffer_stats.get("queued_bytes",0))
            await publish_crypto_disconnect(reason)
            health_touch(bus.settings,f"collectors-{job.provider}",state="error",detail={"provider":job.provider,"phase":"retrying","reason":reason})
            outage_seconds = max(0, int(now - outage_started))
            if transient and consecutive_failures < 5 and outage_seconds < 90:
                logger.warning(
                    "collector transient disconnect provider=%s failures=%d outage_seconds=%d reason=%s; reconnecting",
                    job.provider,
                    consecutive_failures,
                    outage_seconds,
                    reason,
                )
            else:
                logger.exception(
                    "collector task failed provider=%s transient=%s failures=%d outage_seconds=%d; retrying",
                    job.provider,
                    transient,
                    consecutive_failures,
                    outage_seconds,
                )
            try:
                # Short-lived 1006/ping/NATS disconnects are handled by the
                # retry loop and are not operational incidents.  Escalate only
                # when repeated or sustained; deterministic auth/parser/TR
                # failures remain immediate.
                sustained = consecutive_failures >= 5 or outage_seconds >= 90
                crypto_alert = job.provider in {"bitget-perp", "bybit-perp"}
                should_alert = provider_open() and (crypto_alert or not transient or sustained) and (
                    last_error_alert is None
                    or now - last_error_alert[1] >= (300 if crypto_alert else 900)
                    or (not crypto_alert and last_error_alert[0] != reason)
                )
                if bus._js is not None and should_alert:  # best-effort observability
                    await bus.publish_system(
                        "collector_error",
                        {
                            "provider": job.provider,
                            "symbols": job.symbols,
                            "error": reason,
                            "cause": cause,
                            "transient": transient,
                            "consecutive_failures": consecutive_failures,
                            "outage_seconds": outage_seconds,
                        },
                    )
                    last_error_alert = (reason, now)
                    # The next successfully published event emits a recovery
                    # COLLECTOR START notification.
                    announced_running = False
                elif not provider_open() and announced_running:
                    await bus.publish_system("collector_stop", {"provider": job.provider, "symbols": job.symbols, "reason": "market_closed"})
                    announced_running = False
            except Exception:
                logger.exception("failed to publish collector error")
        finally:
            try:
                if bus._js is not None and cancelled and announced_running:
                    await bus.publish_system("collector_stop", {"provider": job.provider, "symbols": job.symbols})
                    announced_running = False
            except Exception:
                pass
            if bus._js is not None:
                try:
                    await bus.close()
                except Exception:
                    logger.warning("failed to close collector NATS connection provider=%s",job.provider,exc_info=True)
        if not provider_open() and job.provider not in {"binance", "hyperliquid", "binance-perp", "bitget-perp", "bybit-perp", "arcus-perp", "grvt-perp"}:
            # Do not hammer domestic/US sessions while their market is closed.
            backoff = 300.0
        await asyncio.sleep(backoff)
        backoff = min(300.0, backoff * 2.0)


async def _storage_loop(domain: str = "market") -> None:
    backoff = 2.0
    while True:
        # A failed run may retain delivery handles without corresponding
        # Arrow buffers. Recreate both the worker and its bus on every retry.
        worker = StorageWorker(bus=NatsJetStreamBus(),domain=domain)
        try:
            await worker.run()
            backoff = 2.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("storage task failed; retrying")
            # Keep the cause across retries: the next initialization heartbeat
            # replaces the error state and Docker restart counts stay at zero.
            try:
                import traceback
                record={'ts_ns':time.time_ns(),'domain':domain,'error_type':type(exc).__name__,
                        'reason':str(exc)[:2000],'traceback':traceback.format_exc()[-12000:]}
                with (worker.settings.state_dir/'storage_errors.jsonl').open('a',encoding='utf-8') as f:
                    f.write(json.dumps(record)+'\n')
            except OSError:
                logger.warning('could not persist storage failure',exc_info=True)
        finally:
            try:
                await worker.bus.close()
            except Exception:
                logger.warning("failed to close storage NATS connection",exc_info=True)
            worker.writer.store.close()
        await asyncio.sleep(backoff)
        backoff = min(300.0, backoff * 2.0)


async def _compactor_loop() -> None:
    def compact_once_sync() -> dict[str, Any]:
        # Create/close the SQLite connection inside the worker thread; SQLite
        # connections are thread-affine by default.
        compactor = ParquetCompactor()
        try:
            return compactor.compact_once()
        finally:
            compactor.store.close()

    while True:
        try:
            # Compaction performs blocking Arrow reads/writes and fsyncs; run
            # it off the event loop so market WebSocket ping/pong stays timely.
            result = await asyncio.to_thread(compact_once_sync)
            logger.info("compactor result=%s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("compactor task failed")
        await asyncio.sleep(300.0)


async def _maintenance_loop() -> None:
    settings=get_settings()
    health_touch(settings,"maintenance",detail={"stage":"starting"})
    def archive_once() -> dict[str, Any]:
        maintenance = ArchiveMaintenance(settings)
        try:
            return maintenance.run_once()
        finally:
            maintenance.close()

    def resource_recovered() -> bool:
        # This probe reads only health JSON and /proc pressure. It does not
        # open SQLite, scan files or acquire the heavy maintenance lock.
        maintenance = ArchiveMaintenance(settings)
        try:
            probe=getattr(maintenance,'_resource_maintenance_allowed',None)
            return bool(probe and probe()[0])
        except Exception:
            logger.warning('maintenance recovery probe unavailable',exc_info=True)
            return False
        finally:
            maintenance.close()

    while True:
        archive_result: dict[str, Any] = {}
        try:
            archive_result=await _with_maintenance_heartbeat(
                settings,
                "archive",
                asyncio.to_thread(archive_once),
            )
            await _with_maintenance_heartbeat(settings,"compaction",_compactor_loop_once())
            health_touch(settings,"maintenance",detail={"stage":"idle","archive":archive_result})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("maintenance task failed")
        # Drain a backlog promptly; a fixed five-minute sleep leaves verified
        # local files occupying the shared disk long after upload succeeds.
        batch_size=max(1,int(settings.env.get("ARCHIVE_UPLOAD_BATCH","32")))
        prune_limit=max(1,int(settings.env.get("ARCHIVE_DELIVERY_HISTORY_PRUNE_LIMIT","5000")))
        recovery=archive_result.get('recovery_blocks',{})
        retirement=archive_result.get('event_run_retirement',{})
        retire_limit=max(1,int(settings.env.get('CRYPTO_EVENT_RUN_RETIRE_BATCH','4')))
        # A prune-only buffer gate does not invalidate completed uploads or
        # recovery scans. Retain the I/O cooldown for actual resource pressure.
        prune_only_deferred=(archive_result.get('reason') in {'storage_pending_above_prune_limit','storage_io_above_prune_limit'}
                             and archive_result.get('deferred_stage') in {'delivery_prune','crypto_event_run_retirement'})
        archive_progress_allowed=not archive_result.get('deferred',False) or prune_only_deferred
        busy=archive_progress_allowed and bool(archive_result.get("reclaim_batch_full") or archive_result.get("upload_candidates",0)>=batch_size
                  or archive_result.get("pruned_deliveries",0)>=prune_limit
                  or retirement.get('retired_runs',0)>=retire_limit
                  or ((recovery.get('uploaded',0)>0 or recovery.get('scan_progressed',False))
                      and recovery.get('candidates',0)>=recovery.get('batch_limit',1)))
        # Idle is still liveness, so refresh health throughout the wait.
        idle_seconds=max(30.0,float(settings.env.get("ARCHIVE_IDLE_SECONDS","300")))
        # Prune-only deferrals retry archive normally. Resource deferrals use
        # cheap recovery probes below instead of sleeping through recovery.
        backlog_idle=max(1.0,min(30.0,float(settings.env.get('ARCHIVE_BACKLOG_IDLE_SECONDS','30'))))
        wait_seconds=backlog_idle if busy else (max(300.0,idle_seconds) if
            (archive_result.get("deferred") and not prune_only_deferred) or archive_result.get("ran") is False else idle_seconds)
        wait_seconds=_maintenance_lock_retry_delay(settings,archive_result,wait_seconds)
        resource_deferred=(archive_result.get('reason') in
                           {'storage_or_io_busy','storage_health_unavailable','io_pressure_unavailable'})
        quiet_checks=0
        while wait_seconds>0:
            interval=min(30.0,wait_seconds)
            await asyncio.sleep(interval)
            wait_seconds-=interval
            health_touch(settings,"maintenance",detail={"stage":"idle","archive":archive_result})
            if resource_deferred:
                recovered=await asyncio.to_thread(resource_recovered)
                quiet_checks=quiet_checks+1 if recovered else 0
                if quiet_checks>=2:
                    break


def _maintenance_lock_retry_delay(settings: Any, result: dict[str, Any], default: float) -> float:
    if result.get('reason') != 'heavy_maintenance_busy':
        return default
    # A competing database owns the lock temporarily. Resource-pressure
    # deferrals retain their existing cooldown; jitter avoids synchronized
    # Market/Crypto retries permanently favoring the same worker.
    seconds=max(1.0,min(30.0,float(settings.env.get('ARCHIVE_LOCK_RETRY_SECONDS','10'))))
    return max(1.0,min(30.0,seconds*random.uniform(0.7,1.3)))


async def _with_maintenance_heartbeat(settings: Any, stage: str, operation: Any) -> Any:
    """Keep health tied to real long-running maintenance progress."""
    task=asyncio.ensure_future(operation)
    while not task.done():
        health_touch(settings,"maintenance",detail={"stage":stage})
        try:
            return await asyncio.wait_for(asyncio.shield(task),timeout=30.0)
        except asyncio.TimeoutError:
            continue
    return await task


async def _compactor_loop_once() -> None:
    def run() -> dict[str, Any]:
        compact = ParquetCompactor()
        try:
            return compact.compact_once()
        finally:
            compact.store.close()
    jobs=max(0,min(24,int(get_settings().env.get("COMPACTOR_JOBS_PER_CYCLE","1"))))
    if jobs == 0:
        return
    settings=get_settings()
    with maintenance_lock(settings.data_root) as lock:
        if not lock["acquired"]:
            logger.info("compactor deferred reason=%s",lock["reason"])
            return
        for _ in range(jobs):
            # This probe is SQLite-free; recheck between compaction jobs too.
            allowed,detail=ArchiveMaintenance(settings)._heavy_maintenance_allowed()
            if not allowed:
                logger.info("compactor deferred reason=%s",detail.get("reason"))
                return
            result = await asyncio.to_thread(run)
            logger.info("compactor result=%s", result)
            if not result.get("ran"):
                break


async def run_runtime(config_path: str | None = None, role: str = "all", provider_filter:str|None=None) -> None:
    """Run the long-lived Docker runtime; individual tasks restart independently."""
    if role in {"crypto-collectors","crypto-storage","crypto-maintenance","crypto-strategies"}:
        settings=get_settings()
        archive=settings.archive
        archive_configured=(archive.backend.strip().lower() == "ncloud_storage"
                            and bool(archive.endpoint and archive.bucket))
        # Only maintenance receives the object-store credentials. Collectors
        # and storage require its explicit deployment flag instead of secrets.
        archive_available=archive.enabled if role == "crypto-maintenance" else settings.env.get("ARCHIVE_WORKER_ENABLED", "").lower() == "true"
        if not archive_configured or not archive_available:
            raise RuntimeError("crypto profile requires a configured Ncloud archive worker before ingestion")
    configured_collectors, configured_strategies = _load_config(Path(config_path) if config_path else None)
    if len(configured_strategies) > 5:
        raise RuntimeError("MAX_STRATEGY_JOBS is 5; reduce configured strategies")
    job_ids = [job_id for job_id, _ in configured_strategies]
    if len(job_ids) != len(set(job_ids)):
        raise RuntimeError("strategy job IDs must be unique")
    if role == "crypto-strategies" and not configured_strategies:
        raise RuntimeError("crypto-strategies requires at least one configured strategy")
    tasks: list[asyncio.Task[Any]] = []
    if role in {"all", "collectors", "ls-collectors", "crypto-collectors"}:
        if role == "crypto-collectors":
            selected = [job for job in configured_collectors if job.provider.endswith("-perp")]
        elif role == "ls-collectors":
            selected = [job for job in configured_collectors if job.provider.startswith("ls-")]
        elif role == "collectors":
            selected = [job for job in configured_collectors if not job.provider.endswith("-perp") and not job.provider.startswith("ls-")]
        else:
            selected = configured_collectors
        if provider_filter:
            selected=[job for job in selected if job.provider==provider_filter]
            if len(selected)!=1:raise RuntimeError(f"collector provider filter did not match exactly one job: {provider_filter}")
        async def collectors_heartbeat() -> None:
            while True:
                component = (f"crypto-collectors-{provider_filter}" if role=="crypto-collectors" and provider_filter
                             else "crypto-collectors" if role == "crypto-collectors"
                             else "ls-collectors" if role == "ls-collectors" else "collectors")
                health_touch(get_settings(),component,detail={"providers":[job.provider for job in selected]})
                await asyncio.sleep(20.0)
        tasks.append(asyncio.create_task(collectors_heartbeat(),name="collectors-heartbeat"))
        for job in selected:
            tasks.append(asyncio.create_task(_collector_loop(job, NatsJetStreamBus()), name=f"collector-{job.provider}"))
            await asyncio.sleep(0.5)
    if role in {"all", "storage"}:
        tasks.append(asyncio.create_task(_storage_loop(), name="storage"))
    if role in {"all", "crypto-storage"}:
        tasks.append(asyncio.create_task(_storage_loop("crypto"), name="crypto-storage"))
    if role in {"all", "maintenance", "crypto-maintenance"}:
        tasks.append(asyncio.create_task(_maintenance_loop(), name="maintenance"))
    if configured_strategies and role in {"all", "crypto-strategies"}:
        # The candidate engine is shadow-only until execution evidence and
        # adapter handoff contracts have separately passed their live gates.
        async def strategy_loop() -> None:
            while True:
                try:
                    await SharedStrategyRunner(configured_strategies, mode="shadow").run()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("shared strategy task failed; retrying")
                await asyncio.sleep(2.0)
        tasks.append(asyncio.create_task(strategy_loop(), name="shared-strategies"))
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    installed_signals: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
            installed_signals.append(sig)
        except (NotImplementedError, RuntimeError):
            pass
    runtime_task = asyncio.gather(*tasks)
    stop_task = asyncio.create_task(stop_event.wait(), name="runtime-stop-signal")
    try:
        done, _ = await asyncio.wait({runtime_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if runtime_task in done:
            await runtime_task
    finally:
        stop_task.cancel()
        runtime_task.cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(runtime_task, stop_task, *tasks, return_exceptions=True)
        for sig in installed_signals:
            loop.remove_signal_handler(sig)
