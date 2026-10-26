from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .bus import EventBus, NatsJetStreamBus
from .dashboard.telemetry import heartbeat
from core.dedup import BoundedDedup
from market.hot_path import DecisionSnapshot, HotPathStore, StrategyDataView
from core.models import AssetClass, BaseMarketEvent, EventType, InstrumentId, Signal, event_from_dict
from storage.parquet_store import ParquetHistoryReader
from .settings import get_settings
from strategies import CompiledSubscription, StrategyModule, compile_subscriptions, compile_warmup, load_strategy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShadowDecision:
    job_id: str
    signal_json: bytes
    cursor: tuple[str, int]
    input_versions: tuple[tuple[str, int], ...]
    quality: tuple[tuple[str, str], ...]

    @property
    def signal(self) -> Signal:
        return Signal.model_validate_json(self.signal_json)


@dataclass
class _StrategyJob:
    job_id: str
    module: StrategyModule
    strategy: Any
    subscriptions: tuple[CompiledSubscription, ...]
    warmup_bars: int
    seen: BoundedDedup = field(default_factory=lambda: BoundedDedup(max_entries=100_000))
    produced: deque[str] = field(default_factory=lambda: deque(maxlen=1000))
    state: str = "WARMING"
    error: str | None = None


class SharedStrategyRunner:
    """One writer/subscription for replay or shadow; no external signal/order capability."""

    def __init__(self, jobs: list[tuple[str, str | StrategyModule]], bus: EventBus | None = None,
                 *, history_reader: Any = None, hot_path: HotPathStore | None = None,
                 mode: str = "shadow", settings: Any = None,
                 known_instruments: Iterable[InstrumentId] = (),
                 now_ns: Callable[[], int] = time.time_ns, tick_seconds: float = 1.0,
                 max_jobs: int = 5) -> None:
        if mode not in {"shadow", "replay"}:
            raise ValueError("Shared market engine supports replay/shadow only; live gate is not implemented")
        if not 0 < len(jobs) <= max_jobs or len({job for job, _ in jobs}) != len(jobs):
            raise ValueError("Strategy jobs must be nonempty, unique and within the declared limit")
        if tick_seconds <= 0:
            raise ValueError("tick_seconds must be positive")
        self.settings = settings or get_settings()
        self.mode, self.now_ns, self.tick_seconds = mode, now_ns, tick_seconds
        self.hot_path = hot_path or HotPathStore()
        self.bus = bus or NatsJetStreamBus(self.settings)
        self.history = history_reader
        self._owns_history = False
        self._known = {instrument.key: instrument for instrument in known_instruments}
        if len(self._known) > self.hot_path.history.max_feeds:
            raise ValueError("Known instrument count exceeds market-state feed bound")
        self._jobs: list[_StrategyJob] = []
        for job_id, definition in jobs:
            module = load_strategy(definition) if isinstance(definition, str) else definition
            count = compile_warmup(module.warmup)
            if count > self.hot_path.history.max_rows:
                raise ValueError("Strategy warmup exceeds prepared-history row bound")
            self._jobs.append(_StrategyJob(job_id, module, module.create(),
                                          compile_subscriptions(module.subscriptions), count))
        self.decisions: deque[ShadowDecision] = deque(maxlen=1000)
        self.view = StrategyDataView(self.hot_path.snapshot(self.now_ns(), self.mode.upper()), ready=False)
        self._prepared = False
        self._running = False

    def prepare(self) -> None:
        if self._prepared:
            return
        requests: dict[str, int] = {}
        for job in self._jobs:
            if job.warmup_bars:
                for key, instrument in self._known.items():
                    if any(sub.matches_instrument(instrument) for sub in job.subscriptions):
                        requests[key] = max(requests.get(key, 0), job.warmup_bars)
        if requests and self.history is None:
            self.history = ParquetHistoryReader(self.settings.parquet.data_root)
            self._owns_history = True
        for key, limit in requests.items():
            rows = self.history.history(instrument_key=key, event_type=EventType.BAR_1M.value, limit=limit)
            self.hot_path.load_history(key, rows)
        self._prepared = True

    def _ready(self, job: _StrategyJob, snapshot: DecisionSnapshot) -> bool:
        dependencies: set[str] = set()
        for sub in job.subscriptions:
            matching = {key for key, instrument in self._known.items() if sub.matches_instrument(instrument)}
            if not matching:
                return False
            dependencies.update(matching)
        for key in dependencies:
            if snapshot.quality.get(key, "WARMING") != "VALID":
                return False
            if (self._known[key].asset_class == AssetClass.CRYPTO_PERP
                    and snapshot.latest_book(key) is None):
                return False
            if job.warmup_bars and (snapshot.history_quality.get(key) != "VALID"
                                  or len(snapshot.closed_bars(key, job.warmup_bars)) < job.warmup_bars):
                return False
        return bool(job.subscriptions)

    def process_event(self, event: BaseMarketEvent | dict) -> tuple[ShadowDecision, ...]:
        if not self._prepared:
            self.prepare()
        if isinstance(event, dict):
            event = event_from_dict(event)
        if event.instrument.key not in self._known and len(self._known) >= self.hot_path.history.max_feeds:
            raise ValueError("Market-state feed bound exceeded")
        self._known[event.instrument.key] = event.instrument
        result = self.hot_path.apply_versioned(event)
        snapshot = self.hot_path.snapshot(self.now_ns(), self.mode.upper())
        self.view = StrategyDataView(snapshot, ready=False, phase=self.mode.upper())
        if result.duplicate or not result.applied:
            if not result.duplicate and result.reason != "raw_only":
                for job in self._jobs:
                    if any(sub.matches_instrument(event.instrument) for sub in job.subscriptions):
                        job.state = "INVALID"
            return ()
        generated = []
        for job in self._jobs:
            if job.error is not None or not any(sub.matches(result.event) for sub in job.subscriptions):
                continue
            ready = self._ready(job, snapshot)
            job.state = "SHADOW" if ready and self.mode == "shadow" else "REPLAY" if self.mode == "replay" else "WARMING"
            view = StrategyDataView(snapshot, ready=ready, phase=job.state)
            if snapshot.quality.get(event.instrument.key) not in {"VALID", "WARMING", "HISTORY_INCOMPLETE"}:
                job.state = "INVALID"
                continue
            try:
                signals = job.strategy.on_event(result.event, view)
                if not isinstance(signals, list) or any(not isinstance(signal, Signal) for signal in signals):
                    raise TypeError("on_event must return list[Signal]")
                packed = [(signal, signal.model_dump_json().encode("utf-8")) for signal in signals]
            except Exception as exc:
                job.error = type(exc).__name__
                job.state = "INVALID"
                logger.exception("strategy callback faulted job=%s", job.job_id)
                continue
            if not ready or self.mode != "shadow":
                continue
            for signal, signal_json in packed:
                if not job.seen.add_if_new(signal.signal_id):
                    continue
                decision = ShadowDecision(job.job_id, signal_json,
                                          snapshot.cursor, tuple(sorted(snapshot.versions.items())),
                                          tuple(sorted(snapshot.quality.items())))
                job.produced.append(signal.signal_id)
                self.decisions.append(decision)
                generated.append(decision)
        return tuple(generated)

    async def run(self, duration_sec: float | None = None) -> dict[str, list[str]]:
        if self._running:
            raise RuntimeError("Shared engine already has a consumer")
        if duration_sec is not None and duration_sec < 0:
            raise ValueError("duration_sec must be nonnegative")
        self._running = True
        messages = None
        pending: asyncio.Future | None = None
        monitors: list[asyncio.Task] = []
        try:
            self.prepare()
            await self.bus.connect()
            messages = self.bus.subscribe_live("live.crypto.>")
            for job in self._jobs:
                monitors.append(asyncio.create_task(heartbeat(
                    self.bus, job_id=job.job_id, name=job.module.strategy_id,
                    account="signal-only", section="crypto", mode=self.mode, currency="USDT")))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + duration_sec if duration_sec is not None else None
            while deadline is None or loop.time() < deadline:
                if pending is None:
                    pending = asyncio.ensure_future(anext(messages))
                timeout = self.tick_seconds if deadline is None else min(self.tick_seconds, max(0, deadline - loop.time()))
                done, _ = await asyncio.wait({pending}, timeout=timeout)
                if not done:
                    self.hot_path.expire(self.now_ns())
                    continue
                completed, pending = pending, None
                try:
                    message = completed.result()
                except StopAsyncIteration:
                    break
                try:
                    self.process_event(message.data)
                    await message.ack()
                except Exception:
                    logger.exception("shared engine rejected market input")
                    await message.nak(delay=5)
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            for monitor in monitors:
                monitor.cancel()
            await asyncio.gather(*monitors, return_exceptions=True)
            try:
                if messages is not None and hasattr(messages, "aclose"):
                    await messages.aclose()
            finally:
                try:
                    await self.bus.close()
                finally:
                    self._running = False
                    if self._owns_history:
                        self.history.store.close()
        return {job.job_id: list(job.produced) for job in self._jobs}


class LiveStrategyRunner:
    """Single-job API adapter; generated signals are shadow diagnostics only.

    Expected migration difference: production signal subjects are no longer
    published. A separately reviewed live execution gate is required for that.
    """

    def __init__(self, job_id: str, strategy_module: str, bus: EventBus | None = None) -> None:
        self.job_id = job_id
        self.engine = SharedStrategyRunner([(job_id, strategy_module)], bus)
        self.settings, self.bus = self.engine.settings, self.engine.bus
        self.hot_path = self.engine.hot_path
        self.strategy_module = self.engine._jobs[0].module
        self.strategy = self.engine._jobs[0].strategy

    @property
    def view(self) -> StrategyDataView:
        return self.engine.view

    async def run(self, duration_sec: float | None = None) -> list[str]:
        return (await self.engine.run(duration_sec))[self.job_id]
