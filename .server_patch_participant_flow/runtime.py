from __future__ import annotations

import asyncio
import logging
import signal
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bus import BufferedMarketEventBus, NatsJetStreamBus
from .compactor import ParquetCompactor
from .archive import ArchiveMaintenance
from .storage_worker import StorageWorker
from .strategy_runner import LiveStrategyRunner
from .session_alerts import session_state
from .market_policy import MarketDataPolicyBus
from .health import touch as health_touch
from .settings import get_settings

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

    async def publish_market_event(self, event: Any) -> None:
        await self.inner.publish_market_event(event)
        if time.monotonic() - self._last_health >= 30:
            health_touch(self.inner.settings,"collectors",detail={"provider":self.provider,"event_type":event.event_type.value,"wallclock_ts_ns":event.wallclock_ts_ns})
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
    backoff = 2.0
    announced_running = False
    cancelled = False
    last_error_alert: tuple[str, float] | None = None
    consecutive_failures = 0
    outage_started: float | None = None

    def provider_open() -> bool:
        if job.provider in {"binance", "hyperliquid"}:
            return True
        if job.provider == "toss":
            return session_state("KR_EQUITY_ETF")[0] or session_state("US_EQUITY_ETF")[0]
        if job.provider == "kis" and "overseas" in job.symbols.lower():
            return session_state("US_FUTURES")[0]
        return session_state("KR_DERIVATIVES")[0] or session_state("KR_EQUITY_ETF")[0]

    while True:
        health_touch(bus.settings,"collectors",state="running",detail={"provider":job.provider,"phase":"connecting"})
        if job.provider not in {"binance", "hyperliquid"} and not provider_open():
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
            from .cli import _collector_for_args
            collector = _collector_for_args(type("Args", (), {"provider": job.provider, "symbols": job.symbols})())

            async def mark_ready() -> None:
                nonlocal announced_running, consecutive_failures, outage_started, backoff
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
                await collector.run(policy_bus)
            finally:
                await buffered_bus.close()
            backoff = 2.0
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:
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
                should_alert = provider_open() and (not transient or sustained) and (
                    last_error_alert is None
                    or last_error_alert[0] != reason
                    or now - last_error_alert[1] >= 900
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
        if not provider_open() and job.provider not in {"binance", "hyperliquid"}:
            # Do not hammer domestic/US sessions while their market is closed.
            backoff = 300.0
        await asyncio.sleep(backoff)
        backoff = min(300.0, backoff * 2.0)


async def _storage_loop(bus: NatsJetStreamBus) -> None:
    backoff = 2.0
    worker = StorageWorker(bus=bus)
    while True:
        try:
            await worker.run()
            backoff = 2.0
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("storage task failed; retrying")
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
        # Idle is an intentional wait, but it is still liveness.  Keep the
        # status endpoint fresh rather than looking failed after its shorter
        # 120-second reporting window.
        for _ in range(10):
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
    result = await asyncio.to_thread(run)
    logger.info("compactor result=%s", result)


async def run_runtime(config_path: str | None = None, role: str = "all") -> None:
    """Run the long-lived Docker runtime; individual tasks restart independently."""
    configured_collectors, configured_strategies = _load_config(Path(config_path) if config_path else None)
    if len(configured_strategies) > 5:
        raise RuntimeError("MAX_STRATEGY_JOBS is 5; reduce configured strategies")
    job_ids = [job_id for job_id, _ in configured_strategies]
    if len(job_ids) != len(set(job_ids)):
        raise RuntimeError("strategy job IDs must be unique")
    # All collectors publish through one reconnecting NATS connection.  The
    # previous one-connection-per-collector design caused a DNS/connect storm
    # at container startup and multiplied ping/reconnect overhead.
    if configured_strategies:
        raise RuntimeError("live strategies cannot consume the storage-only MARKET_DATA WorkQueue; leave strategies disabled in this deployment")
    collector_bus = NatsJetStreamBus()
    tasks: list[asyncio.Task[Any]] = []
    if role in {"all", "collectors"}:
        await collector_bus.connect()
        for job in configured_collectors:
            tasks.append(asyncio.create_task(_collector_loop(job, collector_bus), name=f"collector-{job.provider}"))
    if role in {"all", "storage"}:
        tasks.append(asyncio.create_task(_storage_loop(NatsJetStreamBus()), name="storage"))
    if role in {"all", "maintenance"}:
        tasks.append(asyncio.create_task(_maintenance_loop(), name="maintenance"))
    for job_id, module in ([] if role != "all" else configured_strategies):
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
        if role in {"all", "collectors"}:
            await collector_bus.close()
