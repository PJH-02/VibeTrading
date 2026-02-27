"""Deterministic simulated execution port for backtests."""

from __future__ import annotations

from typing import Callable

from vibetrading_V2.core.ports import Clock, ExecutionPort
from vibetrading_V2.core.types import Fill, OrderIntent


class SimulatedExecution(ExecutionPort):
    """Simple deterministic fill model with injectable price lookup."""

    def __init__(self, clock: Clock, price_for_order: Callable[[OrderIntent], float] | None = None) -> None:
        self.clock = clock
        self.price_for_order = price_for_order or (lambda _order: 0.0)

    def execute(self, order: OrderIntent) -> Fill:
        return Fill(
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=float(self.price_for_order(order)),
            timestamp=self.clock.now(),
        )
