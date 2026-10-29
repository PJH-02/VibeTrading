"""Final bounded lifecycle checks owned by the CAP-05 core repair."""
from dataclasses import replace

import pytest

from core.models import Venue, Side
from execution.execution_groups import CrossVenueExecutionGroups
from execution.trading_contracts import ActionKind, StrategyActionPlan
from cap05_source_fixture import (build_native_group, make_intent, make_action,
                                  rebuild_native_registry)
from test_cap05_native_integration import coordinator_for, close_fixture
from test_cap05_core_adverse import reopen_owners


@pytest.mark.asyncio
async def test_late_literal_source_followup_runs_on_direct_feedback_drain(tmp_path):
    f = build_native_group(tmp_path)
    g = coordinator_for(f)
    seen = []
    def source(event, context):
        seen.append(event.status)
        if event.status != "PARTIAL_FILL":
            return None
        scope = f["bybit_scope"]
        intent = make_intent("luna-late", venue=Venue.BYBIT, account=scope.account,
                             side=Side.BUY, quantity="0.1", raw_symbol="BTCUSDT.P")
        action = replace(make_action("luna-late", scope, intent),
                         group_id=f["plan"].actions[0].group_id)
        return StrategyActionPlan("plan-luna-late", f["plan"].strategy_id,
                                  f["plan"].config_version, f["plan"].event_ts_ns,
                                  (action,), group_followup=True)
    try:
        await g.execute(f["plan"], registry=f["registry"], readiness=f["readiness"],
                        evidence=f["evidence"], on_event=source)
        action = f["plan"].actions[0]
        owner = f["binance_owner"]
        assert owner.apply_fill(action, fill_id="luna-late-fill", quantity="0.04",
                                price="100", fee="0.008")
        await owner.deliver_feedback(action.strategy_id,
                                     owner._source_dispatch[action.strategy_id]["callback"])
        facts = g.ledger.group_facts(action.group_id)
        failures = [fact["document"] for fact in facts if fact["kind"] == "followup-fault"]
        assert not failures, failures
        assert "PARTIAL_FILL" in seen
        assert len(f["bybit_transport"].calls) == 2
        assert f["bybit_owner"].ledger.get("intent-luna-late") is not None
    finally:
        await close_fixture(f)


@pytest.mark.asyncio
async def test_cold_registry_restores_owned_query_from_native_account_ledger(tmp_path):
    f = build_native_group(tmp_path)
    try:
        await coordinator_for(f).execute(f["plan"], registry=f["registry"],
                                         readiness=f["readiness"], evidence=f["evidence"])
        f["binance_transport"].native_orders["c-binance"] = {
            "status": "NEW", "cumulative": "0", "quantity": "0.1",
            "native_id": 7001, "terminal": False}
        await f["binance_owner"].close()
        await f["bybit_owner"].close()
        fresh = reopen_owners(f, tmp_path)
        f["binance_owner"], f["bybit_owner"] = fresh["binance"], fresh["bybit"]
        registry = rebuild_native_registry(f)
        original = f["plan"].actions[0]
        cancel = replace(original, action=ActionKind.CANCEL, target_order_id="c-binance")
        facts = await fresh["binance"].query_order(cancel, registry=registry)
        assert facts["status"] == "NEW"
        assert registry.route_snapshot("binance-perp")["owned_orders"]
    finally:
        await close_fixture(f)


@pytest.mark.asyncio
async def test_raw_empty_list_followup_is_noop_without_fault(tmp_path):
    f = build_native_group(tmp_path)
    g = coordinator_for(f)
    calls = []
    def source(event, context):
        calls.append((event.event_id, context.execution["execution_group"]["group_id"]))
        return []
    try:
        result = await g.execute(f["plan"], registry=f["registry"],
                                 readiness=f["readiness"], evidence=f["evidence"],
                                 on_event=source)
        assert result["state"] == "ACKNOWLEDGED"
        assert len(calls) == 2
        assert not any(fact["kind"] == "followup-fault"
                       for fact in g.ledger.group_facts(f["plan"].actions[0].group_id))
    finally:
        await close_fixture(f)
