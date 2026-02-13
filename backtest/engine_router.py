"""Backtest engine routing by team."""

from typing import Type

from backtest.engine import BacktestEngine
from backtest.engines.arbitrage_engine import ArbitrageBacktestEngine
from backtest.engines.portfolio_engine import PortfolioBacktestEngine
from shared.models import TeamType


def resolve_backtest_engine(team: TeamType) -> Type[BacktestEngine]:
    """Resolve engine class for the requested team."""
    mapping: dict[TeamType, Type[BacktestEngine]] = {
        TeamType.TRADING: BacktestEngine,
        TeamType.PORTFOLIO: PortfolioBacktestEngine,
        TeamType.ARBITRAGE: ArbitrageBacktestEngine,
    }
    return mapping[team]
