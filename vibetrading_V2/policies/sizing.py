"""Sizing policy defaults and merge rules."""

from __future__ import annotations

from dataclasses import dataclass, replace

from vibetrading_V2.strategy.bundle import SizingOverride


@dataclass(frozen=True)
class SizingPolicy:
    target_vol: float = 0.15
    max_gross_exposure: float = 1.0
    per_trade_risk: float = 0.01


def default_sizing_policy() -> SizingPolicy:
    return SizingPolicy()


def merge_sizing_override(
    defaults: SizingPolicy, override: SizingOverride | None
) -> SizingPolicy:
    if override is None:
        return defaults
    return replace(
        defaults,
        target_vol=defaults.target_vol if override.target_vol is None else override.target_vol,
        max_gross_exposure=(
            defaults.max_gross_exposure
            if override.max_gross_exposure is None
            else override.max_gross_exposure
        ),
        per_trade_risk=(
            defaults.per_trade_risk
            if override.per_trade_risk is None
            else override.per_trade_risk
        ),
    )

