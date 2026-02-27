"""Pure port definitions for V2 adapters."""

from __future__ import annotations

from typing import Protocol, Sequence

from .types import Bar, Fill, OrderIntent


class DataSource(Protocol):
    def get_bars(self, symbol: str) -> Sequence[Bar]:
        """Return read-only bars for a symbol."""


class ExecutionPort(Protocol):
    def execute(self, order: OrderIntent) -> Fill:
        """Execute an order intent and return a fill."""


class Clock(Protocol):
    def now(self) -> object:
        """Return current time value."""


class Logger(Protocol):
    def info(self, message: str) -> None:
        """Emit informational logging."""

