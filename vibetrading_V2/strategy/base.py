"""Base strategy contract for V2 runners."""

from __future__ import annotations

from vibetrading_V2.core.ports import Clock, DataSource, ExecutionPort, Logger
from vibetrading_V2.core.types import Bar, Fill, OrderIntent


class Strategy:
    """Minimal deterministic strategy interface."""

    def attach_ports(
        self,
        data_source: DataSource,
        execution: ExecutionPort,
        clock: Clock,
        logger: Logger,
    ) -> None:
        self.data_source = data_source
        self.execution = execution
        self.clock = clock
        self.logger = logger

    def on_bar(self, bar: Bar) -> list[OrderIntent]:
        raise NotImplementedError

    def on_fill(self, fill: Fill) -> None:
        return None

    def finalize(self) -> None:
        return None
