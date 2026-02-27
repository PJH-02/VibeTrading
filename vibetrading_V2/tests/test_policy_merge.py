from vibetrading_V2.policies.merge import default_policy_set, merge_policy_overrides
from vibetrading_V2.strategy.bundle import CostOverride, PolicyOverrides, RiskOverride, SizingOverride


def test_merge_none_overrides_keeps_defaults() -> None:
    defaults = default_policy_set()
    merged = merge_policy_overrides(defaults, None)
    assert merged == defaults


def test_merge_partial_overrides_updates_only_specified_fields() -> None:
    defaults = default_policy_set()
    overrides = PolicyOverrides(
        cost=CostOverride(commission_bps=0.9),
        risk=RiskOverride(max_leverage=2.0),
        sizing=SizingOverride(per_trade_risk=0.02),
    )
    merged = merge_policy_overrides(defaults, overrides)

    assert merged.cost.commission_bps == 0.9
    assert merged.cost.slippage_bps == defaults.cost.slippage_bps
    assert merged.risk.max_leverage == 2.0
    assert merged.risk.max_drawdown == defaults.risk.max_drawdown
    assert merged.sizing.per_trade_risk == 0.02
    assert merged.sizing.max_gross_exposure == defaults.sizing.max_gross_exposure

