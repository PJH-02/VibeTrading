from __future__ import annotations

import asyncio
import logging
from collections import deque

from .bus import EventBus, NatsJetStreamBus
from .hot_path import HotPathStore, StrategyDataView
from .models import AssetClass, EventType, event_from_dict
from .parquet_store import ParquetHistoryReader
from .settings import get_settings
from .strategy_contract import load_strategy
from .dedup import BoundedDedup
from .dashboard.telemetry import heartbeat

logger = logging.getLogger(__name__)


class LiveStrategyRunner:
    def __init__(self, job_id: str, strategy_module: str, bus: EventBus | None = None) -> None:
        self.job_id = job_id
        self.settings = get_settings()
        self.strategy_module = load_strategy(strategy_module)
        self.strategy = self.strategy_module.create()
        self.hot_path = HotPathStore()
        self.history = ParquetHistoryReader(self.settings.parquet.data_root)
        self.view = StrategyDataView(self.hot_path, self.history)
        self.bus = bus or NatsJetStreamBus(self.settings)
        self._seen_signals = BoundedDedup(max_entries=100_000)

    async def run(self, duration_sec: float | None = None) -> list[str]:
        await self.bus.connect()
        deadline = asyncio.get_running_loop().time() + duration_sec if duration_sec else None
        # Keep only a bounded diagnostic tail; the long-lived supervisor does
        # not need to retain every signal ever emitted.
        produced: deque[str] = deque(maxlen=1000)
        messages = self.bus.subscribe_live("live.crypto.>")
        monitor = asyncio.create_task(heartbeat(self.bus, job_id=self.job_id,
            name=getattr(self.strategy_module, "STRATEGY_ID", self.strategy_module.__name__),
            account="signal-only", section="crypto", mode="paper", currency="USDT"))
        try:
            while True:
                if deadline and asyncio.get_running_loop().time() >= deadline:
                    break
                timeout = min(1.0, max(0.05, deadline - asyncio.get_running_loop().time())) if deadline else 1.0
                try:
                    message = await asyncio.wait_for(anext(messages), timeout=timeout)
                except asyncio.TimeoutError:
                    continue
                except StopAsyncIteration:
                    break
                try:
                    event = event_from_dict(message.data)
                    if not self.hot_path.apply(event):
                        await message.ack()
                        continue
                    if event.instrument.asset_class == AssetClass.CRYPTO_PERP and self.hot_path.latest_book(event.instrument.key) is None:
                        await message.ack()
                        continue
                    signals = self.strategy.on_event(event, self.view)
                    for signal in signals:
                        if not self._seen_signals.add_if_new(signal.signal_id):
                            continue
                        await self.bus.publish_signal(signal)
                        produced.append(signal.signal_id)
                        logger.info("signal %s %s %s", signal.strategy_id, signal.action.value, signal.instrument.key)
                    await message.ack()
                except Exception:
                    logger.exception("strategy failed to process message")
                    await message.nak(delay=5)
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            await self.bus.close()
        return list(produced)
