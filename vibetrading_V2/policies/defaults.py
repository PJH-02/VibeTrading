"""Default policy values used when strategies do not override them."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CostPolicy:
    commission_bps: float = 1.0
    slippage_bps: float = 1.0
    min_fee: float = 0.0


@dataclass(frozen=True)
class RiskPolicy:
    max_leverage: float = 1.0
    max_position_notional: float = 100_000.0
    max_drawdown: float = 0.2
    kill_switch_dd: float = 0.3


@dataclass(frozen=True)
class SizingPolicy:
    target_vol: float = 0.1
    max_gross_exposure: float = 1.0
    per_trade_risk: float = 0.01


@dataclass(frozen=True)
class DefaultPolicies:
    cost: CostPolicy = field(default_factory=CostPolicy)
    risk: RiskPolicy = field(default_factory=RiskPolicy)
    sizing: SizingPolicy = field(default_factory=SizingPolicy)

