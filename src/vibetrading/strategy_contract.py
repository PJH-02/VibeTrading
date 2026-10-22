from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Protocol

from .hot_path import StrategyDataView
from .models import BaseMarketEvent, EventType, InstrumentId, Signal, Venue


class StrategyInstance(Protocol):
    def on_event(self, event: BaseMarketEvent, data: StrategyDataView) -> list[Signal]: ...


@dataclass(frozen=True)
class CompiledSubscription:
    instrument_key: str | None = None
    venue: Venue | None = None
    symbol: str | None = None
    event_type: EventType | None = None

    def matches_instrument(self, instrument: InstrumentId) -> bool:
        return ((self.instrument_key is None or self.instrument_key == instrument.key)
                and (self.venue is None or self.venue == instrument.venue)
                and (self.symbol is None or self.symbol == instrument.std_symbol))

    def matches(self, event: BaseMarketEvent) -> bool:
        return self.matches_instrument(event.instrument) and (
            self.event_type is None or self.event_type == event.event_type)


def compile_subscriptions(values: list[Any]) -> tuple[CompiledSubscription, ...]:
    out = []
    allowed = {"instrument_key", "venue", "symbol", "event_type"}
    for value in values:
        if isinstance(value, str) and value:
            out.append(CompiledSubscription(instrument_key=value))
        elif isinstance(value, dict) and value and set(value) <= allowed:
            key, symbol = value.get("instrument_key"), value.get("symbol")
            if key is not None and (not isinstance(key, str) or not key):
                raise ValueError("instrument_key must be a nonempty exact key")
            if symbol is not None and (not isinstance(symbol, str) or not symbol.strip()):
                raise ValueError("symbol must be nonempty")
            out.append(CompiledSubscription(
                key, Venue(value["venue"]) if "venue" in value else None,
                symbol.strip().upper() if symbol is not None else None,
                EventType(value["event_type"]) if "event_type" in value else None))
        else:
            raise ValueError(f"Unsupported strategy subscription: {value!r}")
    return tuple(out)


def compile_warmup(values: dict[str, Any]) -> int:
    if set(values) - {"bars"}:
        raise ValueError("Only explicit closed-bar count warmup is supported")
    bars = values.get("bars", 0)
    if isinstance(bars, bool) or not isinstance(bars, int) or bars < 0:
        raise ValueError("Warmup bars must be a nonnegative integer")
    return bars


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
