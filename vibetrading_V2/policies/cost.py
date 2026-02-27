"""Cost policy defaults and merge rules."""

from __future__ import annotations

from dataclasses import dataclass, replace

from vibetrading_V2.strategy.bundle import CostOverride


@dataclass(frozen=True)
class CostPolicy:
    commission_bps: float = 5.0
    slippage_bps: float = 1.0
    min_fee: float = 0.0


def default_cost_policy() -> CostPolicy:
    return CostPolicy()


def merge_cost_override(defaults: CostPolicy, override: CostOverride | None) -> CostPolicy:
    if override is None:
        return defaults
    return replace(
        defaults,
        commission_bps=(
            defaults.commission_bps
            if override.commission_bps is None
            else override.commission_bps
        ),
        slippage_bps=(
            defaults.slippage_bps
            if override.slippage_bps is None
            else override.slippage_bps
        ),
        min_fee=defaults.min_fee if override.min_fee is None else override.min_fee,
    )

