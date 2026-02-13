"""Backtesting package with team-routed engines."""

from backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from backtest.engine_router import resolve_backtest_engine

__all__ = ["BacktestConfig", "BacktestEngine", "BacktestResult", "resolve_backtest_engine"]
