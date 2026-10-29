"""CAP-05 native two-venue execution through existing concrete adapters."""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.models import Side
from execution.execution_groups import CrossVenueExecutionGroups
from execution.trading_contracts import GroupSubmitMode
from runtime.real_runner import RealExecutionRunner, RealExecutionRunnerConfig
from runtime.runtime_control import GateBlocked

from cap05_source_fixture import (
    CONFIG_VERSION, Cap05Source, SendBarrier, build_native_group,
    make_market_event_and_view,
)


def coordinator_for(fixture):
    return CrossVenueExecutionGroups(
        fixture["binance_owner"].ledger,
        {fixture["binance_scope"].key: fixture["binance_owner"],
         fixture["bybit_scope"].key: fixture["bybit_owner"]},
        max_groups=8, max_facts=128, max_bytes=200_000,
        max_followup_actions=2, clock=lambda: 1_000_000_000)


async def close_fixture(fixture):
    await fixture["binance_owner"].close()
    await fixture["bybit_owner"].close()


@pytest.mark.asyncio
async def test_concurrent_barrier_is_before_both_acks_and_first_duplicate_does_not_resend(tmp_path):
    barrier = SendBarrier()
    fixture = build_native_group(tmp_path, barrier=barrier)
    source = Cap05Source(fixture["plan"])
    coordinator = coordinator_for(fixture)
    try:
        result = await coordinator.execute(
            fixture["plan"], registry=fixture["registry"],
            readiness=fixture["readiness"], evidence=fixture["evidence"],
            on_event=source.on_order_event)
        assert result["state"] == "ACKNOWLEDGED", [
            (leg["leg_id"], leg["document"].get("error_detail")) for leg in result["legs"]]
        assert barrier.count == 2
        assert [call[0] for call in fixture["binance_transport"].calls] == ["POST"]
        assert [call[0] for call in fixture["bybit_transport"].calls] == ["POST"]
        assert len(source.order_events) == 2
        facts = coordinator.ledger.group_facts()
        assert {fact["kind"] for fact in facts} >= {"plan", "leg-definition", "reservation"}
        assert result["funding"]["status"] == "UNKNOWN"
        assert result["cross_ledger_atomic_commit"] is False
        assert result["distributed_atomic_fill"] is False

        again = await coordinator.execute(
            fixture["plan"], registry=fixture["registry"],
            readiness=fixture["readiness"], evidence=fixture["evidence"],
            on_event=source.on_order_event)
        assert again["duplicate"] is True
        assert again["state"] == "ACKNOWLEDGED"
        assert len(source.order_events) == 2
        assert len(fixture["binance_transport"].calls) == 1
        assert len(fixture["bybit_transport"].calls) == 1
    finally:
        await close_fixture(fixture)


@pytest.mark.asyncio
async def test_sequential_sends_exact_order_and_predecessor_unknown_blocks_not_sent(tmp_path):
    sent = []
    fixture = build_native_group(tmp_path, mode=GroupSubmitMode.SEQUENTIAL,
                                 order_log=sent, fail_binance=True)
    source = Cap05Source(fixture["plan"])
    coordinator = coordinator_for(fixture)
    try:
        result = await coordinator.execute(
            fixture["plan"], registry=fixture["registry"],
            readiness=fixture["readiness"], evidence=fixture["evidence"],
            on_event=source.on_order_event)
        assert sent == ["binance"]
        assert result["state"] == "UNKNOWN"
        legs = {leg["leg_id"]: leg["state"] for leg in result["legs"]}
        assert legs["action-binance"] == "UNKNOWN"
        assert legs["action-bybit"] == "NOT_SENT"
        assert fixture["bybit_transport"].calls == []
    finally:
        await close_fixture(fixture)


@pytest.mark.asyncio
async def test_second_owner_preflight_failure_means_both_mutations_zero(tmp_path):
    fixture = build_native_group(tmp_path)
    coordinator = coordinator_for(fixture)
    scope = fixture["bybit_scope"]
    fixture["readiness"][scope.key] = lambda: (_ for _ in ()).throw(
        GateBlocked("B native readiness is stale"))
    try:
        result = await coordinator.execute(
            fixture["plan"], registry=fixture["registry"],
            readiness=fixture["readiness"], evidence=fixture["evidence"])
        assert result["state"] == "PREFLIGHT_FAILED"
        assert result["mutations_attempted"] == 0
        assert fixture["binance_transport"].calls == []
        assert fixture["bybit_transport"].calls == []
        legs = {leg["leg_id"]: leg["state"] for leg in result["legs"]}
        assert legs["action-binance"] == "RESERVED_NOT_SENT"
        assert legs["action-bybit"] == "PREFLIGHT_FAILED"
        # Staged reservations are visible, not converted to success.
        assert result["pending_reservation_by_currency"]
    finally:
        await close_fixture(fixture)


@pytest.mark.asyncio
async def test_reverse_unequal_quantities_remain_source_chosen(tmp_path):
    fixture = build_native_group(
        tmp_path, binance_side=Side.SELL, bybit_side=Side.BUY,
        binance_qty="0.3", bybit_qty="0.07")
    coordinator = coordinator_for(fixture)
    try:
        result = await coordinator.execute(
            fixture["plan"], registry=fixture["registry"],
            readiness=fixture["readiness"], evidence=fixture["evidence"])
        assert result["state"] == "ACKNOWLEDGED", result
        definitions = [fact["document"] for fact in coordinator.ledger.group_facts()
                       if fact["kind"] == "leg-definition"]
        by_id = {row["action_id"]: row for row in definitions}
        assert by_id["action-binance"]["quantity"] == "0.3"
        assert by_id["action-binance"]["side"] == "sell"
        assert by_id["action-bybit"]["quantity"] == "0.07"
        assert by_id["action-bybit"]["side"] == "buy"
    finally:
        await close_fixture(fixture)


@pytest.mark.asyncio
async def test_real_runner_market_callback_routes_group_to_injected_coordinator(tmp_path):
    fixture = build_native_group(tmp_path)
    coordinator = coordinator_for(fixture)
    source = Cap05Source(fixture["plan"])
    runner = RealExecutionRunner(
        RealExecutionRunnerConfig(
            job_id="cap05-job", account_id=fixture["binance_scope"].account,
            destination_name="fixture", max_order_notional=Decimal("1000000"),
            max_position_notional=Decimal("1000000")),
        store=object(), bus=object(), settings=object(),
        owner=fixture["binance_owner"], group_coordinator=coordinator)
    event, data = make_market_event_and_view()
    evidence = {"config_version": CONFIG_VERSION, **fixture["evidence"]}
    try:
        result = await runner.dispatch_strategy_market_event(
            source, event, data, registry=fixture["registry"], evidence=evidence,
            readiness=fixture["readiness"], source_trust="trusted_cooperative")
        assert result["state"] == "ACKNOWLEDGED", result
        assert source.market_calls == 1
        assert len(source.order_events) == 2
    finally:
        await close_fixture(fixture)


@pytest.mark.asyncio
async def test_runner_without_coordinator_rejects_grouped_plan_before_send(tmp_path):
    fixture = build_native_group(tmp_path)
    source = Cap05Source(fixture["plan"])
    runner = RealExecutionRunner(
        RealExecutionRunnerConfig(
            job_id="cap05-job", account_id=fixture["binance_scope"].account,
            destination_name="fixture", max_order_notional=Decimal("1000000"),
            max_position_notional=Decimal("1000000")),
        store=object(), bus=object(), settings=object(),
        owner=fixture["binance_owner"])
    event, data = make_market_event_and_view()
    evidence = {"config_version": CONFIG_VERSION, **fixture["evidence"]}
    try:
        with pytest.raises(GateBlocked, match="coordinator"):
            await runner.dispatch_strategy_market_event(
                source, event, data, registry=fixture["registry"], evidence=evidence,
                readiness=fixture["readiness"], source_trust="trusted_cooperative")
        assert fixture["binance_transport"].calls == []
        assert fixture["bybit_transport"].calls == []
    finally:
        await close_fixture(fixture)
