"""Risk policy defaults and merge rules."""

from __future__ import annotations

from dataclasses import dataclass, replace

from vibetrading_V2.strategy.bundle import RiskOverride


@dataclass(frozen=True)
class RiskPolicy:
    max_leverage: float = 1.0
    max_position_notional: float = 100_000.0
    max_drawdown: float = 0.20
    kill_switch_dd: float = 0.30


def default_risk_policy() -> RiskPolicy:
    return RiskPolicy()


def merge_risk_override(defaults: RiskPolicy, override: RiskOverride | None) -> RiskPolicy:
    if override is None:
        return defaults
    return replace(
        defaults,
        max_leverage=(
            defaults.max_leverage
            if override.max_leverage is None
            else override.max_leverage
        ),
        max_position_notional=(
            defaults.max_position_notional
            if override.max_position_notional is None
            else override.max_position_notional
        ),
        max_drawdown=(
            defaults.max_drawdown
            if override.max_drawdown is None
            else override.max_drawdown
        ),
        kill_switch_dd=(
            defaults.kill_switch_dd
            if override.kill_switch_dd is None
            else override.kill_switch_dd
        ),
    )

