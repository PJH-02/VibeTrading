"""Shared runtime loop for V2 backtest/paper/live runners."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from vibetrading_V2.core.ports import Clock, DataSource, ExecutionPort, Logger
from vibetrading_V2.core.types import Fill, OrderIntent
from vibetrading_V2.policies.merge import PolicySet, default_policy_set, merge_policy_overrides
from vibetrading_V2.strategy.registry import load_strategy_bundle


@dataclass(frozen=True)
class RunnerPorts:
    data_source: DataSource
    execution: ExecutionPort
    clock: Clock
    logger: Logger


@dataclass(frozen=True)
class RunnerResult:
    mode: str
    strategy_name: str
    bars_processed: int
    orders_submitted: int
    fills: tuple[Fill, ...]
    policies: PolicySet


def _inject_ports(strategy: object, ports: RunnerPorts) -> None:
    if hasattr(strategy, "attach_ports") and callable(strategy.attach_ports):
        strategy.attach_ports(ports.data_source, ports.execution, ports.clock, ports.logger)
        return
    if hasattr(strategy, "set_ports") and callable(strategy.set_ports):
        strategy.set_ports(ports.data_source, ports.execution, ports.clock, ports.logger)
        return

    setattr(strategy, "data_source", ports.data_source)
    setattr(strategy, "execution", ports.execution)
    setattr(strategy, "clock", ports.clock)
    setattr(strategy, "logger", ports.logger)


def _iter_orders(strategy: object, bar: object) -> Iterable[OrderIntent]:
    orders = strategy.on_bar(bar)
    if orders is None:
        return []
    return orders


def run_mode(
    mode: str,
    strategy: str,
    *,
    ports: RunnerPorts,
    strategies_dir: str = "strategies",
) -> RunnerResult:
    bundle = load_strategy_bundle(strategy, strategies_dir=strategies_dir)
    policies = merge_policy_overrides(default_policy_set(), bundle.overrides)

    strategy_instance = bundle.build()
    _inject_ports(strategy_instance, ports)

    fills: list[Fill] = []
    bars_processed = 0
    orders_submitted = 0

    for symbol in bundle.meta.universe:
        bars: Sequence[object] = ports.data_source.get_bars(symbol)
        for bar in bars:
            bars_processed += 1
            for order in _iter_orders(strategy_instance, bar):
                orders_submitted += 1
                fill = ports.execution.execute(order)
                fills.append(fill)
                if hasattr(strategy_instance, "on_fill"):
                    strategy_instance.on_fill(fill)

    if hasattr(strategy_instance, "finalize"):
        strategy_instance.finalize()

    ports.logger.info(
        f"[{mode}] strategy={bundle.meta.name} bars={bars_processed} orders={orders_submitted}"
    )
    return RunnerResult(
        mode=mode,
        strategy_name=bundle.meta.name,
        bars_processed=bars_processed,
        orders_submitted=orders_submitted,
        fills=tuple(fills),
        policies=policies,
    )
