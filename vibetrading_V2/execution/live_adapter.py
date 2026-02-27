"""Live-trading execution adapter scaffold."""

from __future__ import annotations

from typing import Callable

from vibetrading_V2.core.ports import Clock, ExecutionPort
from vibetrading_V2.core.types import Fill, OrderIntent


class LiveExecutionAdapter(ExecutionPort):
    """Minimal live adapter placeholder with injectable broker submit function."""

    def __init__(
        self,
        clock: Clock,
        submit_order: Callable[[OrderIntent], float] | None = None,
    ) -> None:
        self.clock = clock
        self.submit_order = submit_order or (lambda _order: 0.0)

    def execute(self, order: OrderIntent) -> Fill:
        return Fill(
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=float(self.submit_order(order)),
            timestamp=self.clock.now(),
        )
