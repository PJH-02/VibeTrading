"""Paper-trading execution adapter scaffold."""

from __future__ import annotations

from typing import Callable

from vibetrading_V2.core.ports import Clock, ExecutionPort
from vibetrading_V2.core.types import Fill, OrderIntent


class PaperExecutionAdapter(ExecutionPort):
    """Minimal paper adapter that emulates broker acknowledgements."""

    def __init__(self, clock: Clock, mark_price: Callable[[str], float] | None = None) -> None:
        self.clock = clock
        self.mark_price = mark_price or (lambda _symbol: 0.0)

    def execute(self, order: OrderIntent) -> Fill:
        return Fill(
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=float(self.mark_price(order.symbol)),
            timestamp=self.clock.now(),
        )
