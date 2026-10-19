from __future__ import annotations

import asyncio
import logging
import signal
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bus import BufferedMarketEventBus, NatsJetStreamBus, dumps
from .compactor import ParquetCompactor
from .archive import ArchiveMaintenance
from .storage_worker import StorageWorker
from .strategy_runner import LiveStrategyRunner
from .session_alerts import session_state
from .market_policy import MarketDataPolicyBus
from .health import touch as health_touch
from .gap_ledger import CollectionGapLedger
from .settings import get_settings
from .models import AssetClass, DataGapEvent, InstrumentId, Venue
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
    return reason[:800], cause_text[:800], transient


class _CollectorReadinessBus:
    """Report readiness only after the first event reaches JetStream."""

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

    async def publish_market_event(self, event: Any) -> None:
        await self.inner.publish_market_event(event)
        self.last_published_monotonic = time.monotonic()
        self.last_event_ts_ns = time.time_ns()
        if event.instrument.asset_class == AssetClass.CRYPTO_PERP:
            self.symbol_last_seen_ns[event.instrument.std_symbol] = self.last_event_ts_ns
        self.published_count += 1
        self.published_bytes += len(dumps(event))
        if time.monotonic() - self._last_health >= 30:
            health_touch(self.inner.settings,f"collectors-{self.provider}",detail={"provider":self.provider,"phase":"publishing","last_event_ts_ns":self.last_event_ts_ns,"published_count":self.published_count,"published_bytes":self.published_bytes,"symbol_last_seen_ns":self.symbol_last_seen_ns,"event_type":event.event_type.value,"wallclock_ts_ns":event.wallclock_ts_ns})
            self._last_health = time.monotonic()
        if not self.ready:
            self.ready = True
            await self.on_ready()

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
    consecutive_failures = 0
    outage_started: float | None = None

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

    def provider_open() -> bool:
        if job.provider in {"binance", "hyperliquid", "binance-perp", "bitget-perp", "bybit-perp"}:
            return True
        if job.provider == "toss":
            return session_state("KR_EQUITY_ETF")[0] or session_state("US_EQUITY_ETF")[0]
        if job.provider == "kis" and "overseas" in job.symbols.lower():
            return session_state("US_FUTURES")[0]
        return session_state("KR_DERIVATIVES")[0] or session_state("KR_EQUITY_ETF")[0]

    while True:
        health_touch(bus.settings,f"collectors-{job.provider}",detail={"provider":job.provider,"phase":"connecting"})
        if job.provider not in {"binance", "hyperliquid", "binance-perp", "bitget-perp", "bybit-perp"} and not provider_open():
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
            buffered_bus = BufferedMarketEventBus(readiness_bus)
            policy_bus = MarketDataPolicyBus(buffered_bus)
            try:
                async def watch_provider() -> None:
                    while True:
                        await asyncio.sleep(20.0)
                        age = time.monotonic() - readiness_bus.last_published_monotonic
                        health_touch(bus.settings,f"collectors-{job.provider}",detail={"provider":job.provider,"phase":"publishing" if readiness_bus.ready else "connecting","last_event_ts_ns":readiness_bus.last_event_ts_ns,"published_count":readiness_bus.published_count,"published_bytes":readiness_bus.published_bytes,"symbol_last_seen_ns":readiness_bus.symbol_last_seen_ns,"event_age_seconds":round(age,1)})
                        stale_after=60 if job.provider.endswith("-perp") else 180
                        if job.provider in {"binance", "hyperliquid", "binance-perp", "bitget-perp", "bybit-perp"} and age > stale_after:
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
                await buffered_bus.close()
            backoff = 2.0
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:
            # Backpressure is an intentional, recoverable collection stop.
            # Do not turn it into a Telegram incident or exponential retry
            # storm; the process-wide policy latch will reopen collection only
            # after the NATS/disk recovery criteria have held long enough.
            if caused_by_collection_pause(exc):
                await asyncio.to_thread(gap_ledger.start, job.provider, job.symbols, "storage_or_nats_backpressure")
                await publish_crypto_disconnect("storage_or_nats_backpressure")
                health_touch(
                    bus.settings,
                    f"collectors-{job.provider}",
                    detail={"provider": job.provider, "phase": "paused", "reason": "storage_or_nats_backpressure"},
                )
                try:
                    if bus._js is not None and announced_running:
                        await bus.publish_system(
                            "collector_stop",
                            {"provider": job.provider, "symbols": job.symbols, "reason": "storage_or_nats_backpressure"},
                        )
                        announced_running = False
                except Exception:
                    logger.warning("failed to publish collector backpressure state provider=%s", job.provider, exc_info=True)
                # Keep attempting at a bounded cadence so the shared latch can
                # observe the 60-second recovery window without a long retry
                # delay or reconnect storm.
                backoff = 10.0
                await asyncio.sleep(backoff)
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
            await asyncio.to_thread(gap_ledger.start, job.provider, job.symbols, reason)
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
        if not provider_open() and job.provider not in {"binance", "hyperliquid", "binance-perp", "bitget-perp", "bybit-perp"}:
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
        except Exception:
            logger.exception("storage task failed; retrying")
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
    while True:
        archive_result: dict[str, Any] = {}
        try:
            archive_result=await _with_maintenance_heartbeat(
                settings,
                "archive",
                asyncio.to_thread(ArchiveMaintenance().run_once),
            )
            await _with_maintenance_heartbeat(settings,"compaction",_compactor_loop_once())
            health_touch(settings,"maintenance",detail={"stage":"idle","archive":archive_result})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("maintenance task failed")
        # Drain a backlog promptly; verified files must not occupy the
        # shared disk through a fixed five-minute wait.
        batch_size=max(1,int(settings.env.get("ARCHIVE_UPLOAD_BATCH","32")))
        busy=bool(archive_result.get("reclaim_batch_full") or archive_result.get("upload_candidates",0)>=batch_size)
        for _ in range(1 if busy else 10):
            await asyncio.sleep(30.0)
            health_touch(settings,"maintenance",detail={"stage":"idle","archive":archive_result})


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
    for _ in range(jobs):
        result = await asyncio.to_thread(run)
        logger.info("compactor result=%s", result)
        if not result.get("ran"):
            break


async def run_runtime(config_path: str | None = None, role: str = "all") -> None:
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
    if role in {"all", "collectors", "crypto-collectors"}:
        selected = [job for job in configured_collectors if (job.provider.endswith("-perp")) == (role == "crypto-collectors")]
        if role == "all": selected = configured_collectors
        async def collectors_heartbeat() -> None:
            while True:
                health_touch(get_settings(),"crypto-collectors" if role == "crypto-collectors" else "collectors",detail={"providers":[job.provider for job in selected]})
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
    for job_id, module in (configured_strategies if role in {"all", "crypto-strategies"} else []):
        if job_id and module:
            async def strategy_loop(j: str = job_id, m: str = module) -> None:
                while True:
                    try:
                        await LiveStrategyRunner(j, m).run()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("strategy task failed job=%s; retrying", j)
                    await asyncio.sleep(2.0)
            tasks.append(asyncio.create_task(strategy_loop(), name=f"strategy-{job_id}"))
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
