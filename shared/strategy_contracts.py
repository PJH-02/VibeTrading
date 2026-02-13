"""Team-scoped strategy contracts for refactored backtesting."""

from __future__ import annotations

from decimal import Decimal
from typing import List, Protocol

from pydantic import BaseModel, ConfigDict, Field

from shared.models import Candle, Signal, StrategyContext


class TeamPortfolioWeights(BaseModel):
    """Portfolio strategy output for multi-asset allocations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    weights: dict[str, Decimal] = Field(default_factory=dict)
    rebalance: bool = True


class TeamArbitrageLeg(BaseModel):
    """One leg in an arbitrage trade."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    side: str  # buy/sell
    weight: Decimal = Decimal("1")


class TeamArbitrageSignal(BaseModel):
    """Arbitrage strategy output for spread-based executions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spread_id: str
    legs: List[TeamArbitrageLeg]
    confidence: Decimal = Decimal("1")


class TradingStrategyContract(Protocol):
    """Single-instrument signal strategy contract."""

    name: str

    def initialize(self) -> None: ...

    def reset(self) -> None: ...

    def on_candle(self, candle: Candle, context: StrategyContext): ...


class PortfolioStrategyContract(Protocol):
    """Portfolio optimization strategy contract."""

    name: str

    def initialize(self) -> None: ...

    def reset(self) -> None: ...

    def on_candle(self, candle: Candle, context: StrategyContext) -> TeamPortfolioWeights: ...


class ArbitrageStrategyContract(Protocol):
    """Arbitrage strategy contract."""

    name: str

    def initialize(self) -> None: ...

    def reset(self) -> None: ...

    def on_candle(self, candle: Candle, context: StrategyContext) -> TeamArbitrageSignal: ...


class TradingStrategyResult(BaseModel):
    """Typed wrapper for classic trading signals."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signals: List[Signal] = Field(default_factory=list)
