"""Backtest runner for V2 strategy bundles."""

from __future__ import annotations

from vibetrading_V2.runner.runtime import RunnerPorts, RunnerResult, run_mode


def run_backtest(
    strategy: str,
    *,
    ports: RunnerPorts,
    strategies_dir: str = "strategies",
) -> RunnerResult:
    return run_mode("backtest", strategy, ports=ports, strategies_dir=strategies_dir)
