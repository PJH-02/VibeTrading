"""Portfolio team backtest engine.

Current implementation reuses the core event-driven engine while exposing
portfolio-specific naming and hooks for future basket-level execution logic.
"""

from dataclasses import dataclass

from backtest.engine import BacktestConfig, BacktestEngine, BacktestResult


@dataclass
class PortfolioBacktestConfig(BacktestConfig):
    """Portfolio-specific backtest configuration."""

    rebalance_frequency: str = "1d"


class PortfolioBacktestEngine(BacktestEngine):
    """Portfolio backtest engine entrypoint."""

    pass


__all__ = ["PortfolioBacktestConfig", "PortfolioBacktestEngine", "BacktestResult"]
