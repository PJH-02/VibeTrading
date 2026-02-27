"""Sample V2 strategy plugin contract.

This file is the intended user-editable strategy surface in V2 mode.
"""

from __future__ import annotations

from typing import Optional

from vibetrading_V2.core.ports import Clock, DataSource, ExecutionPort, Logger
from vibetrading_V2.core.types import Bar, OrderIntent
from vibetrading_V2.strategy.base import Strategy
from vibetrading_V2.strategy.bundle import (
    PolicyOverrides,
    RiskOverride,
    StrategyBundle,
    StrategyMeta,
)


class MyStrategyA(Strategy):
    def __init__(self) -> None:
        self._last_close: Optional[float] = None
        self.logger: Optional[Logger] = None

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
        orders: list[OrderIntent] = []
        if self._last_close is not None:
            side = "buy" if bar.close >= self._last_close else "sell"
            orders.append(OrderIntent(symbol=bar.symbol, side=side, quantity=1.0))
        self._last_close = bar.close
        return orders


def get_bundle() -> StrategyBundle:
    return StrategyBundle(
        meta=StrategyMeta(
            name="my_strategy_a",
            universe=["BTC-USD"],
            timeframe="1m",
            required_fields=["open", "high", "low", "close", "volume"],
            session="24x7",
        ),
        build=MyStrategyA,
        overrides=PolicyOverrides(
            risk=RiskOverride(max_leverage=1.5),
        ),
    )
