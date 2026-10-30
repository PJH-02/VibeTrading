"""CAP-05 core adverse tests: native recovery, budgets and funding attribution.

These exercises use only the existing concrete Binance/Bybit adapters with the
scripted credential-free transport from ``cap05_source_fixture``. Nothing here
touches a network, credentials, or any live order mutation.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.models import Side
from execution.execution_groups import CrossVenueExecutionGroups
from execution.execution_ledger import ExecutionLedger
from execution.execution_owner import ExecutionBudgets, ExecutionOwner
from execution.trading_contracts import (
    ActionKind, ExecutionAction, GroupSubmitMode, StrategyActionPlan, TradingMode,
)
from runtime.runtime_control import GateBlocked, RuntimeControl

from cap05_source_fixture import (
    CONFIG_VERSION, NOW, STRATEGY_ID, build_native_group, group_policy, make_intent,
)


def related_plan(fixture, *, group_id, action_prefix):
    """One independent two-venue group of the same source strategy."""
    scopes = (fixture["binance_scope"], fixture["bybit_scope"])
    binance_intent = make_intent(action_prefix, venue="binance",
                                 account=scopes[0].account, side=Side.BUY,
                                 quantity="0.1")
    bybit_intent = make_intent(action_prefix + "b", venue="bybit",
                               account=scopes[1].account, side=Side.SELL,
                               quantity="0.2", std_symbol="BTCUSDT.P",
                               raw_symbol="BTCUSDT.P")
    actions = (
        ExecutionAction(
            action_id="action-" + action_prefix, action=ActionKind.NEW,
            scope=scopes[0], strategy_id=STRATEGY_ID, config_version=CONFIG_VERSION,
            mode=TradingMode.SANDBOX, expiry_ns=NOW + 1_000_000_000,
            intent=binance_intent, time_in_force="GTC", group_id=group_id),
        ExecutionAction(
            action_id="action-" + action_prefix + "b", action=ActionKind.NEW,
            scope=scopes[1], strategy_id=STRATEGY_ID, config_version=CONFIG_VERSION,
            mode=TradingMode.SANDBOX, expiry_ns=NOW + 1_000_000_000,
            intent=bybit_intent, time_in_force="GTC", group_id=group_id),
    )
    return StrategyActionPlan(
        "plan-" + action_prefix, STRATEGY_ID, CONFIG_VERSION, NOW, actions,
        group_submit_mode=GroupSubmitMode.CONCURRENT, group_policy=group_policy(scopes))


def cancel_followup(plan, order_ids, *, prefix, group_id):
    scopes = {action.scope.venue: action.scope for action in plan.actions}
    actions = tuple(
        ExecutionAction(
            action_id="cancel-" + prefix + "-" + venue, action=ActionKind.CANCEL,
            scope=scope, strategy_id=plan.strategy_id,
            config_version=plan.config_version, mode=TradingMode.SANDBOX,
            expiry_ns=NOW + 1_000_000_000, target_order_id=identity,
            group_id=group_id)
        for venue, order_id in order_ids.items()
        for scope, identity in ((scopes[venue], order_id),))
    return StrategyActionPlan(
        "plan-cancel-" + prefix, plan.strategy_id, plan.config_version, NOW, actions,
        group_followup=True)


def new_followup(base, *, prefix, scale="1"):
    """One literal NEW follow-up against one existing child of the base leg."""
    intent = base.intent.model_copy(update={
        "intent_id": "intent-f-" + prefix, "client_order_id": "c-f-" + prefix,
        "quantity": Decimal(str(base.intent.quantity)) * Decimal(scale)})
    action = ExecutionAction(
        action_id="action-f-" + prefix, action=ActionKind.NEW,
        scope=base.scope, strategy_id=base.strategy_id,
        config_version=base.config_version, mode=TradingMode.SANDBOX,
        expiry_ns=NOW + 1_000_000_000, intent=intent, time_in_force="GTC",
        group_id=base.group_id)
    return StrategyActionPlan(
        "plan-f-" + prefix, base.strategy_id, base.config_version, NOW,
        (action,), group_followup=True)


def reopen_owners(fixture, tmp_path):
    """True close/reopen of both SQLite ledgers with fresh native owners."""
    fresh = {}
    for name in ("binance", "bybit"):
        scope = fixture[name + "_scope"]
        ledger = ExecutionLedger(
            tmp_path / name / "orders.sqlite3", scope, max_intents=64,
            max_evidence_bytes=65536, max_fills=64,
            authority_root=tmp_path / name / "authority",
            native_account_id=fixture[name + "_native"])
        control = RuntimeControl(ledger)
        budgets = ExecutionBudgets(Decimal("10000000"), Decimal("100000000"), 8,
                                   10_000_000_000, 2.0, 2.0)
        fresh[name] = ExecutionOwner(ledger, control, object(), budgets, clock=lambda: NOW)
    return fresh


@pytest.mark.asyncio
async def test_true_reopen_reconciles_transport_unknown_and_duplicate_never_resends(tmp_path):
    fixture = build_native_group(tmp_path, fail_binance=True, fail_bybit=True)
    plan = related_plan(fixture, group_id="cap05-unknown", action_prefix="unknown")
    coordinator = coordinator = CrossVenueExecutionGroups(
        fixture["binance_owner"].ledger,
        {fixture["binance_scope"].key: fixture["binance_owner"],
         fixture["bybit_scope"].key: fixture["bybit_owner"]},
        max_groups=8, max_facts=256, max_bytes=200_000,
        max_followup_actions=4, clock=lambda: NOW)
    try:
        result = await coordinator.execute(
            plan, registry=fixture["registry"], readiness=fixture["readiness"],
            evidence=fixture["evidence"])
        assert result["state"] == "UNKNOWN", result
        assert [c[0] for c in fixture["binance_transport"].calls] == ["POST"]
        assert [c[0] for c in fixture["bybit_transport"].calls] == ["POST"]
    finally:
        await fixture["binance_owner"].close()
        await fixture["bybit_owner"].close()

    fresh = reopen_owners(fixture, tmp_path)
    fixture["binance_owner"], fixture["bybit_owner"] = fresh["binance"], fresh["bybit"]
    coordinator = CrossVenueExecutionGroups(
        fresh["binance"].ledger,
        {fixture["binance_scope"].key: fresh["binance"],
         fixture["bybit_scope"].key: fresh["bybit"]},
        max_groups=8, max_facts=256, max_bytes=200_000,
        max_followup_actions=4, clock=lambda: NOW)
    stale_readiness = {fixture["binance_scope"].key: lambda: (_ for _ in ()).throw(
        GateBlocked("readiness is not restored after a crash")),
        fixture["bybit_scope"].key: lambda: (_ for _ in ()).throw(
            GateBlocked("readiness is not restored after a crash"))}
    duplicate = await coordinator.execute(
        plan, registry=fixture["registry"], readiness=stale_readiness,
        evidence=fixture["evidence"])
    assert duplicate["duplicate"] is True
    assert duplicate["state"] == "UNKNOWN"
    assert [c[0] for c in fixture["binance_transport"].calls] == ["POST"]  # never resent
    assert [c[0] for c in fixture["bybit_transport"].calls] == ["POST"]

    fixture["binance_transport"].native_orders = {
        "c-unknown": {"status": "New", "cumulative": "0", "quantity": "0.1",
                      "native_id": 7001, "terminal": False}}
    fixture["bybit_transport"].native_orders = {
        "c-unknownb": {"status": "New", "cumulative": "0", "quantity": "0.2",
                       "native_id": "bybit-7001", "terminal": False}}
    summary = await coordinator.reconcile(
        "cap05-unknown", registry=fixture["registry"], start_ns=NOW,
        end_ns=NOW + 10**9)
    def compact_docs(summary):
        keys = ("leg_id", "state", "resolved", "unresolved", "query_fault", "reason",
                "cancel_confirm_fault", "fill_fault", "fill_gap", "status", "terminal",
                "native_cumulative", "local_cumulative", "history_incomplete")
        return [{k: v for k, v in leg.items() if k in keys} for leg in summary["legs"]]
    summary_str = " | ".join(
        f"{leg['leg_id']}:state={leg.get('state')},res={leg.get('resolved')},"
        f"qf={leg.get('query_fault')},rs={leg.get('reason')},ccf={leg.get('cancel_confirm_fault')},"
        f"ff={leg.get('fill_fault')},gap={leg.get('fill_gap')},st={leg.get('status')},"
        f"term={leg.get('terminal')},hc={leg.get('history_incomplete')},"
        f"ncum={leg.get('native_cumulative')},lcum={leg.get('local_cumulative')}"
        for leg in summary["legs"])
    assert summary["state"] == "RECONCILED", summary_str
    assert summary["report"]["state"] == "RECONCILED"
    for leg in summary["report"]["legs"]:
        assert leg["current"]["state"] == "SUBMITTED"
        assert leg["current"]["reservation"] != "0"  # known-open orders stay reserved
    assert [c[0] for c in fixture["binance_transport"].calls] == ["POST"]  # no resend
    assert [c[0] for c in fixture["bybit_transport"].calls] == ["POST"]
    await fresh["binance"].close()
    await fresh["bybit"].close()


@pytest.mark.asyncio
async def test_cancel_ack_then_exact_late_fill_recovers_group(tmp_path):
    fixture = build_native_group(tmp_path)
    plan = related_plan(fixture, group_id="cap05-cancel", action_prefix="cancel")
    coordinator = CrossVenueExecutionGroups(
        fixture["binance_owner"].ledger,
        {fixture["binance_scope"].key: fixture["binance_owner"],
         fixture["bybit_scope"].key: fixture["bybit_owner"]},
        max_groups=8, max_facts=256, max_bytes=200_000,
        max_followup_actions=4, clock=lambda: NOW)
    try:
        result = await coordinator.execute(
            plan, registry=fixture["registry"], readiness=fixture["readiness"],
            evidence=fixture["evidence"])
        assert result["state"] == "ACKNOWLEDGED", result
        cancel_plan = cancel_followup(
            plan, {"binance": "7001", "bybit": "bybit-7001"},
            prefix="latefill", group_id="cap05-cancel")
        outcome = await coordinator.execute_followup(
            cancel_plan, registry=fixture["registry"], readiness=fixture["readiness"],
            evidence=fixture["evidence"])
        assert {row["status"] for row in outcome["followup_results"].values()} == {
            "CANCEL_PENDING"}
        fixture["binance_transport"].native_orders = {
            "c-cancel": {"status": "CANCELED", "cumulative": "0.1", "quantity": "0.1",
                         "native_id": 7001, "terminal": True}}
        fixture["binance_transport"].native_fills = [{
            "id": 1001, "orderId": "7001", "symbol": "BTCUSDT", "side": "buy",
            "qty": "0.1", "price": "100", "commission": "0.02",
            "commissionAsset": "USDT", "time": 1000, "maker": False}]
        fixture["bybit_transport"].native_orders = {
            "c-cancelb": {"status": "Cancelled", "cumulative": "0.2", "quantity": "0.2",
                          "native_id": "bybit-7001", "terminal": True}}
        fixture["bybit_transport"].native_fills = [{
            "execId": "e-1", "orderId": "bybit-7001", "orderLinkId": "c-cancelb",
            "symbol": "BTCUSDT.P", "side": "Sell", "execQty": "0.2", "execPrice": "100",
            "execFee": "0.04", "execTime": 1000, "isMaker": False}]
        summary = await coordinator.reconcile(
            "cap05-cancel", registry=fixture["registry"],
            start_ns=NOW, end_ns=NOW + 10**9)
        def compact_docs(summary):
            keys = {"leg_id", "state", "resolved", "unresolved", "query_fault", "reason",
                    "cancel_confirm_fault", "fill_fault", "fill_gap", "status", "terminal",
                    "native_cumulative", "local_cumulative", "history_incomplete"}
            return [{k: v for k, v in leg.items() if k in keys} for leg in summary["legs"]]
        docs = compact_docs(summary)
        assert summary["reconciled"] is True, docs
        assert summary["legs"][0]["fees"] == "0.02"
        assert summary["legs"][1]["fees"] == "0.04"
        again = await coordinator.reconcile(
            "cap05-cancel", registry=fixture["registry"],
            start_ns=NOW, end_ns=NOW + 10**9)
        again_str = " | ".join(
            f"{leg['leg_id']}:state={leg.get('state')},res={leg.get('resolved')},"
            f"qf={leg.get('query_fault')},rs={leg.get('reason')},ccf={leg.get('cancel_confirm_fault')},"
            f"ff={leg.get('fill_fault')},gap={leg.get('fill_gap')},st={leg.get('status')},"
            f"term={leg.get('terminal')},hc={leg.get('history_incomplete')},"
            f"ncum={leg.get('native_cumulative')},lcum={leg.get('local_cumulative')}"
            for leg in again["legs"])
        assert again["reconciled"] is True, again_str
        assert again["legs"][1]["fees"] == "0.04"  # exact fill dedup, never double
        report = again["report"]
        assert all(Decimal(leg["current"]["reservation"]) == 0 for leg in report["legs"]), [
            (leg["scope_key"], leg["current"]["state"], leg["current"]["filled"],
             leg["current"]["reservation"], leg["current"]["cancel_state"])
            for leg in report["legs"]]
    finally:
        await fixture["binance_owner"].close()
        await fixture["bybit_owner"].close()


@pytest.mark.asyncio
async def test_native_gap_keeps_group_reconciling_and_fence_new(tmp_path):
    fixture = build_native_group(tmp_path, fail_binance=True, fail_bybit=True)
    plan = related_plan(fixture, group_id="cap05-gap", action_prefix="gap")
    coordinator = CrossVenueExecutionGroups(
        fixture["binance_owner"].ledger,
        {fixture["binance_scope"].key: fixture["binance_owner"],
         fixture["bybit_scope"].key: fixture["bybit_owner"]},
        max_groups=8, max_facts=256, max_bytes=200_000,
        max_followup_actions=4, clock=lambda: NOW)
    try:
        result = await coordinator.execute(
            plan, registry=fixture["registry"], readiness=fixture["readiness"],
            evidence=fixture["evidence"])
        assert result["state"] == "UNKNOWN"
        summary = await coordinator.reconcile(
            "cap05-gap", registry=fixture["registry"],
            start_ns=NOW, end_ns=NOW + 10**9)
        assert summary["state"] == "RECONCILING"
        assert all(doc["resolved"] is False for doc in summary["legs"])
        binance_leg = next(a for a in plan.actions if a.scope.venue == "binance")
        fenced = new_followup(binance_leg, prefix="gap-new")
        with pytest.raises(GateBlocked, match="fenced"):
            await coordinator.execute_followup(
                fenced, registry=fixture["registry"], readiness=fixture["readiness"],
                evidence=fixture["evidence"])
        assert [c[0] for c in fixture["binance_transport"].calls] == ["POST"]
        # Owned cancel/reconcile stays available while the group is unreconciled.
        cancel_plan = cancel_followup(
            plan, {"binance": "c-gap"}, prefix="gap-cancel", group_id="cap05-gap")
        outcome = await coordinator.execute_followup(
            cancel_plan, registry=fixture["registry"], readiness=fixture["readiness"],
            evidence=fixture["evidence"])
        assert outcome["followup_results"]["cancel-gap-cancel-binance"][
            "acknowledged"] is True
    finally:
        await fixture["binance_owner"].close()
        await fixture["bybit_owner"].close()


def coordinator_of(fixture, clock=None):
    return CrossVenueExecutionGroups(
        fixture["binance_owner"].ledger,
        {fixture["binance_scope"].key: fixture["binance_owner"],
         fixture["bybit_scope"].key: fixture["bybit_owner"]},
        max_groups=8, max_facts=256, max_bytes=200_000,
        max_followup_actions=4, clock=clock or (lambda: NOW))


@pytest.mark.asyncio
async def test_cumulative_group_caps_count_existing_legs_for_new_candidate(tmp_path):
    fixture = build_native_group(tmp_path)
    plan = related_plan(fixture, group_id="cap05-caps", action_prefix="caps")
    scopes = (fixture["binance_scope"], fixture["bybit_scope"])
    policy = {**dict(plan.group_policy),
              "notional_caps": {scopes[0].key: "1000000", scopes[1].key: "30"},
              "cost_caps": {scopes[0].key: "1000", scopes[1].key: "1000"}}
    actions = plan.actions
    tight_plan = StrategyActionPlan(
        "plan-caps-tight", STRATEGY_ID, CONFIG_VERSION, NOW, actions,
        group_submit_mode=GroupSubmitMode.CONCURRENT, group_policy=policy)
    coordinator = coordinator_of(fixture)
    try:
        result = await coordinator.execute(
            tight_plan, registry=fixture["registry"], readiness=fixture["readiness"],
            evidence=fixture["evidence"])
        assert result["state"] == "ACKNOWLEDGED", result
        bybit_leg = next(a for a in actions if a.scope.venue == "bybit")
        # Existing bybit pending price-notional is 0.2*100 = 20; the biggest
        # candidate is 0.2 more, so the cumulative 30+ exceeds the same cap.
        rejected = new_followup(bybit_leg, prefix="caps-reject", scale="2")
        with pytest.raises(GateBlocked, match="Cumulative"):
            await coordinator.execute_followup(
                rejected, registry=fixture["registry"], readiness=fixture["readiness"],
                evidence=fixture["evidence"])
        assert [c[0] for c in fixture["bybit_transport"].calls] == ["POST"]
        cheapest = new_followup(bybit_leg, prefix="caps-pass", scale="0.5")
        with pytest.raises(GateBlocked, match="fault"):
            await coordinator.execute_followup(
                cheapest, registry=fixture["registry"], readiness=fixture["readiness"],
                evidence=fixture["evidence"])
        assert [c[0] for c in fixture["bybit_transport"].calls] == ["POST"]
    finally:
        await fixture["binance_owner"].close()
        await fixture["bybit_owner"].close()


@pytest.mark.asyncio
async def test_funding_receipts_dedup_shared_allocation_and_conservation(tmp_path):
    fixture = build_native_group(tmp_path)
    first = related_plan(fixture, group_id="cap05-fund-a", action_prefix="fa")
    second = related_plan(fixture, group_id="cap05-fund-b", action_prefix="fb")
    coordinator = coordinator_of(fixture)
    try:
        for plan in (first, second):
            result = await coordinator.execute(
                plan, registry=fixture["registry"], readiness=fixture["readiness"],
                evidence=fixture["evidence"])
            assert result["state"] == "ACKNOWLEDGED", result
        assert coordinator.report("cap05-fund-a")["funding"]["status"] == "UNKNOWN"
        receipt = {
            "receipt_id": "fund-2026-05", "period_ns": NOW, "currency": "USDT",
            "amount": "10", "version": 3, "raw": {"fixture": "native funding row"},
                "scope_key": fixture["binance_scope"].key,
                "native_account_id": fixture["binance_owner"].ledger.native_account_id,
                "source": "native_funding_receipt",
            "allocations": {"cap05-fund-a": "6", "cap05-fund-b": "3"},
        }
        known = coordinator.record_group_funding("cap05-fund-a", [receipt])
        assert known["funding_receipts"]["recorded"] == ["fund-2026-05"]
        assert known["funding"]["status"] == "ATTRIBUTED"
        assert known["funding"]["attributed_by_currency"] == {"USDT": str(Decimal("6"))}
        assert known["funding"]["unallocated_by_currency"] == {"USDT": str(Decimal("1"))}
        other = coordinator.report("cap05-fund-b")
        assert other["funding"]["attributed_by_currency"] == {"USDT": str(Decimal("3"))}
        replay = coordinator.record_group_funding("cap05-fund-a", [receipt])
        assert replay["funding_receipts"]["replayed"] == ["fund-2026-05"]
        assert replay["funding"]["attributed_by_currency"] == {"USDT": str(Decimal("6"))}
        changed_receipt = receipt | {"amount": "12"}
        with pytest.raises(ValueError, match="collision|Immutable"):
            coordinator.record_group_funding("cap05-fund-a", [changed_receipt])
        over = receipt | {"receipt_id": "fund-2026-06",
                          "allocations": {"cap05-fund-a": "11"}}
        with pytest.raises(GateBlocked, match="exceeds"):
            coordinator.record_group_funding("cap05-fund-a", [over])
        report = coordinator.report("cap05-fund-a")
        assert Decimal(report["funding"]["attributed_by_currency"]["USDT"]) == Decimal("6")
    finally:
        await fixture["binance_owner"].close()
        await fixture["bybit_owner"].close()


@pytest.mark.asyncio
async def test_deadline_blocks_new_but_owned_cancel_stays_available(tmp_path):
    fixture = build_native_group(tmp_path)
    plan = related_plan(fixture, group_id="cap05-deadline", action_prefix="dl")
    coordinator = coordinator_of(fixture)
    aged = coordinator_of(fixture, clock=lambda: NOW + 2_000_000_000)
    try:
        await coordinator.execute(
            plan, registry=fixture["registry"], readiness=fixture["readiness"],
            evidence=fixture["evidence"])
        binance_leg = next(a for a in plan.actions if a.scope.venue == "binance")
        fresh_new = new_followup(binance_leg, prefix="deadline-new")
        with pytest.raises(GateBlocked, match="deadline"):
            await aged.execute_followup(
                fresh_new, registry=fixture["registry"], readiness=fixture["readiness"],
                evidence=fixture["evidence"])
        assert [c[0] for c in fixture["binance_transport"].calls] == ["POST"]
        cancel_plan = cancel_followup(
            plan, {"binance": "7001"}, prefix="dl-cancel",
            group_id="cap05-deadline")
        outcome = await aged.execute_followup(
            cancel_plan, registry=fixture["registry"], readiness=fixture["readiness"],
            evidence=fixture["evidence"])
        assert outcome["followup_results"]["cancel-dl-cancel-binance"][
            "status"] == "CANCEL_PENDING"
    finally:
        await fixture["binance_owner"].close()
        await fixture["bybit_owner"].close()
