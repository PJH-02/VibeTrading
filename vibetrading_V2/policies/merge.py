"""Compose default policies and apply partial strategy overrides."""

from __future__ import annotations

from dataclasses import dataclass

from vibetrading_V2.policies.cost import CostPolicy, default_cost_policy, merge_cost_override
from vibetrading_V2.policies.risk import RiskPolicy, default_risk_policy, merge_risk_override
from vibetrading_V2.policies.sizing import (
    SizingPolicy,
    default_sizing_policy,
    merge_sizing_override,
)
from vibetrading_V2.strategy.bundle import PolicyOverrides


@dataclass(frozen=True)
class PolicySet:
    cost: CostPolicy
    risk: RiskPolicy
    sizing: SizingPolicy


def default_policy_set() -> PolicySet:
    return PolicySet(
        cost=default_cost_policy(),
        risk=default_risk_policy(),
        sizing=default_sizing_policy(),
    )


def merge_policy_overrides(defaults: PolicySet, overrides: PolicyOverrides | None) -> PolicySet:
    if overrides is None:
        return defaults
    return PolicySet(
        cost=merge_cost_override(defaults.cost, overrides.cost),
        risk=merge_risk_override(defaults.risk, overrides.risk),
        sizing=merge_sizing_override(defaults.sizing, overrides.sizing),
    )

