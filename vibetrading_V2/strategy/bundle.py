"""Strategy plugin bundle schema."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Optional, Sequence

from vibetrading_V2.strategy.base import Strategy

Timeframe = Literal["1m", "5m", "15m", "1h", "1d"]


@dataclass(frozen=True)
class StrategyMeta:
    name: str
    universe: Sequence[str]
    timeframe: Timeframe
    required_fields: Sequence[str]
    session: Optional[str] = None


@dataclass(frozen=True)
class CostOverride:
    commission_bps: Optional[float] = None
    slippage_bps: Optional[float] = None
    min_fee: Optional[float] = None


@dataclass(frozen=True)
class RiskOverride:
    max_leverage: Optional[float] = None
    max_position_notional: Optional[float] = None
    max_drawdown: Optional[float] = None
    kill_switch_dd: Optional[float] = None


@dataclass(frozen=True)
class SizingOverride:
    target_vol: Optional[float] = None
    max_gross_exposure: Optional[float] = None
    per_trade_risk: Optional[float] = None


@dataclass(frozen=True)
class PolicyOverrides:
    cost: Optional[CostOverride] = None
    risk: Optional[RiskOverride] = None
    sizing: Optional[SizingOverride] = None


@dataclass(frozen=True)
class StrategyBundle:
    meta: StrategyMeta
    build: Callable[[], Strategy]
    overrides: Optional[PolicyOverrides] = None

