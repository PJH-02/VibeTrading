"""CAP-05 opt-in group contracts, CAP04 wire fields and bounded rejection paths."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from core.models import Side, Venue
from execution.execution_ledger import AccountScope
from execution.trading_contracts import (
    ActionKind, ExecutionAction, GroupSubmitMode, StrategyActionPlan, TradingMode,
)
from runtime.runtime_control import GateBlocked
from runtime.strategy_process import ProtocolError, plan_document, plan_from_document

from cap05_source_fixture import (
    CONFIG_VERSION, NOW, STRATEGY_ID, Cap05Source, build_native_group,
    group_policy, make_action, make_group_plan, make_intent,
)


def scopes():
    return (AccountScope("binance", "acct-a", "linear_perpetual", "sandbox"),
            AccountScope("bybit", "acct-b", "linear_perpetual", "sandbox"))


def coordinator_for(fixture):
    from execution.execution_groups import CrossVenueExecutionGroups
    return CrossVenueExecutionGroups(
        fixture["binance_owner"].ledger,
        {fixture["binance_scope"].key: fixture["binance_owner"],
         fixture["bybit_scope"].key: fixture["bybit_owner"]},
        max_groups=8, max_facts=128, max_bytes=200_000,
        max_followup_actions=2, clock=lambda: NOW)


def test_cap04_group_plan_roundtrip_preserves_exact_decimal_strings():
    left, right = scopes()
    plan = make_group_plan((left, right))
    document = plan_document(plan)
    assert document["group_submit_mode"] == "CONCURRENT"
    assert document["group_policy"]["notional_caps"][left.key] == "1000000"
    restored = plan_from_document(document)
    assert restored.group_submit_mode is GroupSubmitMode.CONCURRENT
    assert restored.group_policy["notional_caps"][left.key] == Decimal("1000000")
    assert plan_document(restored) == document
    with pytest.raises(TypeError):
        restored.group_policy["base_asset"] = "ETH"


def test_cap04_group_wire_schema_is_strict():
    left, right = scopes()
    plan = make_group_plan((left, right))
    document = plan_document(plan)
    with pytest.raises(ProtocolError):
        plan_from_document({**document, "extra": True})
    with pytest.raises(ProtocolError):
        plan_from_document({**document, "group_policy": {
            **document["group_policy"], "notional_caps": {left.key: 1.5}}})
    broken = plan_document(plan)
    broken["group_policy"] = {**broken["group_policy"], "deadline_ns": NOW}
    with pytest.raises(ProtocolError):
        plan_from_document(broken)


def test_group_contract_rejects_one_leg_duplicate_owner_and_missing_mode():
    left, right = scopes()
    policy = group_policy((left, right))
    first = make_action("a", left, make_intent("a", venue=Venue.BINANCE,
        account=left.account, side=Side.BUY, quantity="0.1"))
    second = make_action("b", right, make_intent("b", venue=Venue.BYBIT,
        account=right.account, side=Side.SELL, quantity="0.2",
        std_symbol="BTCUSDT.P"))
    with pytest.raises(ValueError, match="exactly two"):
        StrategyActionPlan("p", STRATEGY_ID, CONFIG_VERSION, NOW, (first,),
                           group_submit_mode=GroupSubmitMode.SEQUENTIAL,
                           group_policy=policy)
    duplicate = make_action("c", left, make_intent("c", venue=Venue.BINANCE,
        account=left.account, side=Side.BUY, quantity="0.3"))
    with pytest.raises(ValueError, match="distinct canonical"):
        StrategyActionPlan("p", STRATEGY_ID, CONFIG_VERSION, NOW, (first, duplicate),
                           group_submit_mode=GroupSubmitMode.SEQUENTIAL,
                           group_policy=policy)
    with pytest.raises(ValueError, match="group submit mode"):
        StrategyActionPlan("p", STRATEGY_ID, CONFIG_VERSION, NOW, (first, second))
    with pytest.raises(ValueError, match="supplied together"):
        StrategyActionPlan("p", STRATEGY_ID, CONFIG_VERSION, NOW, (first, second),
                           group_submit_mode=GroupSubmitMode.CONCURRENT)


@pytest.mark.asyncio
async def test_plain_owner_dispatch_rejects_grouped_new(tmp_path):
    fixture = build_native_group(tmp_path)
    plan = fixture["plan"]
    try:
        with pytest.raises(GateBlocked):
            await fixture["binance_owner"].dispatch_plan(
                plan, registry=fixture["registry"],
                readiness=fixture["readiness"], evidence=fixture["evidence"])
        with pytest.raises(GateBlocked):
            await fixture["binance_owner"].dispatch_action(
                plan.actions[0], registry=fixture["registry"],
                readiness=fixture["readiness"][fixture["binance_scope"].key],
                evidence=fixture["evidence"][fixture["binance_scope"].key])
        assert fixture["binance_transport"].calls == []
    finally:
        await fixture["binance_owner"].close()
        await fixture["bybit_owner"].close()


@pytest.mark.asyncio
async def test_changed_group_and_callback_error_do_not_resend_or_relabel_ack(tmp_path):
    fixture = build_native_group(tmp_path)
    team = coordinator_for(fixture)

    class RaisingSource(Cap05Source):
        def on_order_event(self, event, context):
            self.order_events.append(event.event_id)
            raise RuntimeError("source callback failure after native ACK")

    source = RaisingSource(fixture["plan"])
    try:
        result = await team.execute(
            fixture["plan"], registry=fixture["registry"],
            readiness=fixture["readiness"], evidence=fixture["evidence"],
            on_event=source.on_order_event)
        assert result["state"] == "ACKNOWLEDGED", result
        assert len(source.order_events) == 2

        changed = make_group_plan(
            (fixture["binance_scope"], fixture["bybit_scope"]),
            binance_qty="0.11", bybit_qty="0.2")
        with pytest.raises(GateBlocked, match="collision"):
            await team.execute(
                changed, registry=fixture["registry"],
                readiness=fixture["readiness"], evidence=fixture["evidence"])
        assert len(fixture["binance_transport"].calls) == 1
        assert len(fixture["bybit_transport"].calls) == 1
    finally:
        await fixture["binance_owner"].close()
        await fixture["bybit_owner"].close()


@pytest.mark.asyncio
async def test_duplicate_across_reconstructed_coordinator_keeps_report_and_no_callback(tmp_path):
    fixture = build_native_group(tmp_path)
    source = Cap05Source(fixture["plan"])
    try:
        first = await coordinator_for(fixture).execute(
            fixture["plan"], registry=fixture["registry"],
            readiness=fixture["readiness"], evidence=fixture["evidence"],
            on_event=source.on_order_event)
        assert first["state"] == "ACKNOWLEDGED"
        restarted = coordinator_for(fixture)
        again = await restarted.execute(
            fixture["plan"], registry=fixture["registry"],
            readiness=fixture["readiness"], evidence=fixture["evidence"],
            on_event=source.on_order_event)
        assert again["duplicate"] is True
        assert len(source.order_events) == 2
        assert restarted.report(fixture["plan"].actions[0].group_id)["legs"]
    finally:
        await fixture["binance_owner"].close()
        await fixture["bybit_owner"].close()
