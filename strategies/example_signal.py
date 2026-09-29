from __future__ import annotations

from vibetrading.hot_path import StrategyDataView
from vibetrading.models import BaseMarketEvent, Signal


STRATEGY_ID = "example_signal"
# One-minute bars are intentionally not generated at runtime.  Strategies opt
# into explicit trade/order-book subscriptions and derive bars offline.
SUBSCRIPTIONS: list[dict[str, str]] = []
WARMUP: dict[str, int] = {}


class ExampleSignalStrategy:
    def on_event(self, event: BaseMarketEvent, data: StrategyDataView) -> list[Signal]:
        del event, data
        return []


def create_strategy() -> ExampleSignalStrategy:
    return ExampleSignalStrategy()
