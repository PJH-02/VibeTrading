"""Arbitrage team backtest engine.

Current implementation reuses the core event-driven engine while exposing
arbitrage-specific entrypoint for leg synchronization enhancements.
"""

from dataclasses import dataclass

from backtest.engine import BacktestConfig, BacktestEngine, BacktestResult


@dataclass
class ArbitrageBacktestConfig(BacktestConfig):
    """Arbitrage-specific backtest configuration."""

    max_leg_slippage_bps: int = 15


class ArbitrageBacktestEngine(BacktestEngine):
    """Arbitrage backtest engine entrypoint."""

    pass


__all__ = ["ArbitrageBacktestConfig", "ArbitrageBacktestEngine", "BacktestResult"]
