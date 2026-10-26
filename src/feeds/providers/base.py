from __future__ import annotations

from typing import Protocol

from runtime.bus import EventBus


class MarketDataCollector(Protocol):
    name: str

    async def run(self, bus: EventBus, duration_sec: float | None = None) -> None: ...
