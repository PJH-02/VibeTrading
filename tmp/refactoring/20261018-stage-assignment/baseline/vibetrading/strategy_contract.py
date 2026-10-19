from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Protocol

from .hot_path import StrategyDataView
from .models import BaseMarketEvent, Signal


class StrategyInstance(Protocol):
    def on_event(self, event: BaseMarketEvent, data: StrategyDataView) -> list[Signal]: ...


@dataclass(frozen=True)
class StrategyModule:
    module: ModuleType
    strategy_id: str
    subscriptions: list[Any]
    warmup: dict[str, Any]

    def create(self) -> StrategyInstance:
        strategy = self.module.create_strategy()
        if not hasattr(strategy, "on_event"):
            raise TypeError(f"Strategy {self.strategy_id} must implement on_event(event, data)")
        return strategy


def load_strategy(module_name: str) -> StrategyModule:
    if module_name.endswith(".py"):
        module_name = module_name[:-3].replace("/", ".").replace("\\", ".")
    if not module_name.startswith("strategies."):
        module_name = f"strategies.{module_name}"
    module = importlib.import_module(module_name)
    missing = [name for name in ("STRATEGY_ID", "SUBSCRIPTIONS", "WARMUP", "create_strategy") if not hasattr(module, name)]
    if missing:
        raise ValueError(f"{module_name} does not satisfy the new strategy contract. Missing: {', '.join(missing)}")
    subscriptions = getattr(module, "SUBSCRIPTIONS")
    if not isinstance(subscriptions, list):
        raise TypeError(f"{module_name}.SUBSCRIPTIONS must be a list")
    warmup = getattr(module, "WARMUP")
    if not isinstance(warmup, dict):
        raise TypeError(f"{module_name}.WARMUP must be a dict")
    return StrategyModule(
        module=module,
        strategy_id=str(getattr(module, "STRATEGY_ID")),
        subscriptions=subscriptions,
        warmup=warmup,
    )
