"""Native MM lifecycle through concrete adapters and durable group owners.

All exchange facts are supplied by the scripted transport in cap05_source_fixture;
the adapters, owners, runner, router, and SQLite ledgers are the production classes.
"""
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from core.models import Side
from execution.execution_groups import CrossVenueExecutionGroups
from execution.trading_contracts import ActionKind, ExecutionAction, StrategyActionPlan
from runtime.real_runner import RealExecutionRunner, RealExecutionRunnerConfig
from cap05_source_fixture import (
    CONFIG_VERSION, STRATEGY_ID, build_native_group, make_intent,
    make_market_event_and_view, rebuild_native_registry,
)


def _coordinator(f):
    return CrossVenueExecutionGroups(
        f["binance_owner"].ledger,
        {f["binance_scope"].key: f["binance_owner"],
         f["bybit_scope"].key: f["bybit_owner"]},
        max_groups=8, max_facts=128, max_bytes=200_000,
        max_followup_actions=2, clock=lambda: 1_000_000_000)


class _NativeMMSource:
    STRATEGY_ID = STRATEGY_ID
    ACTION_PROTOCOL_VERSION = 1
    config_version = CONFIG_VERSION

    def __init__(self, plan, binance_scope):
        self.plan = plan
        self.binance_scope = binance_scope
        self.events = []
        self.cancel_plan = None
        self.reprice_plan = None
        self.latest_fill_price = None

    def on_event(self, event, data):
        return self.plan

    def on_order_event(self, event, context):
        execution = dict(context.execution)
        self.events.append((event.status, event.order_id, event.fill_id, execution))
        if event.status == "PARTIAL_FILL":
            if event.fill_price is not None:
                self.latest_fill_price = Decimal(event.fill_price)
            if self.cancel_plan is None and event.account_scope.key == self.binance_scope.key:
                original = self.plan.actions[0]
                action = ExecutionAction(
                    action_id="native-mm-cancel", action=ActionKind.CANCEL,
                    scope=original.scope, strategy_id=original.strategy_id,
                    config_version=original.config_version, mode=original.mode,
                    expiry_ns=1_800_000_000, target_order_id=event.order_id,
                    group_id=original.group_id)
                self.cancel_plan = StrategyActionPlan(
                    "native-mm-cancel-plan", STRATEGY_ID, CONFIG_VERSION,
                    1_000_000_000, (action,), group_followup=True)
                return self.cancel_plan
        if event.status == "CANCELLED" and event.account_scope.key == self.binance_scope.key:
            if self.reprice_plan is not None:
                return None
            original = self.plan.actions[0]
            intent = make_intent(
                "native-mm-residual", venue=original.intent.instrument.venue,
                account=original.scope.account, side=Side.BUY,
                quantity=str(execution["remaining_quantity"]), raw_symbol="BTCUSDT")
            intent = intent.model_copy(update={
                "job_id": original.intent.job_id,
                "limit_price": self.latest_fill_price - Decimal("0.2"),
                "metadata": {**original.intent.metadata,
                             "execution_venue_id": "binance"}})
            action = ExecutionAction(
                action_id="native-mm-residual-new", action=ActionKind.NEW,
                scope=original.scope, strategy_id=original.strategy_id,
                config_version=original.config_version, mode=original.mode,
                expiry_ns=1_800_000_000, intent=intent, post_only=True,
                time_in_force="GTC", group_id=original.group_id)
            self.reprice_plan = StrategyActionPlan(
                "native-mm-residual-plan", STRATEGY_ID, CONFIG_VERSION,
                1_000_000_000, (action,), group_followup=True)
            return self.reprice_plan
        return None


@pytest.mark.asyncio
async def test_native_mm_two_venue_late_fill_cancel_reprice_is_durable_and_once(tmp_path):
    f = build_native_group(tmp_path)
    transport = f["binance_transport"]
    cancel_wire = []
    original_request = transport.request

    async def observe_request(method, url, **kwargs):
        if method == "DELETE":
            observed = {"method": method, "url": url, "permit_consumed": False}
            cancel_wire.append(observed)
            before_send = kwargs.get("before_send")
            if before_send is not None:
                def observed_permit():
                    before_send()
                    observed["permit_consumed"] = True
                kwargs["before_send"] = observed_permit
        return await original_request(method, url, **kwargs)

    transport.request = observe_request
    original = f["plan"]
    actions = tuple(replace(action, post_only=True, time_in_force="GTC")
                    for action in original.actions)
    plan = replace(original, plan_id="native-mm-initial", actions=actions)
    assert plan.actions[0].intent.side is Side.BUY
    assert plan.actions[1].intent.side is Side.SELL
    source = _NativeMMSource(plan, f["binance_scope"])
    coordinator = _coordinator(f)
    runner = RealExecutionRunner(
        RealExecutionRunnerConfig(
            job_id="cap05-job", account_id=f["binance_scope"].account,
            destination_name="fixture", max_order_notional=Decimal("1000000"),
            max_position_notional=Decimal("1000000")),
        store=object(), bus=object(), settings=object(),
        owner=f["binance_owner"], group_coordinator=coordinator)
    trade, view = make_market_event_and_view()
    evidence = {"config_version": CONFIG_VERSION, **f["evidence"]}
    cancel_action = None
    try:
        initial = await runner.dispatch_strategy_market_event(
            source, trade, view, registry=f["registry"], evidence=evidence,
            readiness=f["readiness"], source_trust="trusted_cooperative")
        assert initial["state"] == "ACKNOWLEDGED"
        assert [row[0] for row in f["binance_transport"].calls] == ["POST"]
        assert [row[0] for row in f["bybit_transport"].calls] == ["POST"]
        assert all(payload["timeInForce"] in {"GTX", "PostOnly"}
                   for payload in f["binance_transport"].post_payloads
                   + f["bybit_transport"].post_payloads)

        bid = plan.actions[0]
        owner = f["binance_owner"]
        assert owner.apply_fill(bid, fill_id="native-mm-fill-1", quantity="0.03",
                                price="100", fee="0.006")
        callback = owner._source_dispatch[STRATEGY_ID]["callback"]
        await owner.deliver_feedback(STRATEGY_ID, callback)
        assert source.cancel_plan is not None
        cancel_action = source.cancel_plan.actions[0]
        assert [row[0] for row in f["binance_transport"].calls].count("POST") == 1
        assert len(cancel_wire) == 1
        assert cancel_wire[0]["url"].endswith("/fapi/v1/order")
        assert cancel_wire[0]["permit_consumed"] is True
        assert owner.ledger.get(bid.intent.intent_id)["state"] == "CANCEL_PENDING"

        # A late native fill is incorporated while the cancellation is pending.
        assert owner.apply_fill(bid, fill_id="native-mm-fill-2", quantity="0.01",
                                price="100.1", fee="0.002")
        await owner.deliver_feedback(STRATEGY_ID, callback)
        assert source.reprice_plan is None
        assert owner.ledger.get(bid.intent.intent_id)["filled"] == "0.04"

        client_id = bid.intent.client_order_id
        f["binance_transport"].native_orders[client_id] = {
            "status": "CANCELED", "cumulative": "0.04", "quantity": "0.1",
            "native_id": 7001,
        }
        facts = await owner.reconcile_order(cancel_action, registry=f["registry"])
        assert facts["status"] == "CANCELED"
        assert source.reprice_plan is not None
        residual = source.reprice_plan.actions[0]
        assert residual.intent.quantity == Decimal("0.06")
        assert residual.intent.limit_price == Decimal("99.9")
        assert residual.post_only is True and residual.time_in_force == "GTC"
        assert residual.intent.job_id == bid.intent.job_id
        assert owner.ledger.strategy_position(STRATEGY_ID, bid.intent.instrument.key) == Decimal("0.04")

        # Re-delivering drained feedback cannot create a second follow-up.
        event_ids_before_drain = {row[0] + ":" + str(row[1]) + ":" + str(row[2])
                                  for row in source.events}
        await owner.deliver_feedback(STRATEGY_ID, callback)
        event_ids_after_drain = {row[0] + ":" + str(row[1]) + ":" + str(row[2])
                                 for row in source.events}
        assert len(event_ids_after_drain) == len(source.events)
        assert event_ids_before_drain.issubset(event_ids_after_drain)
        assert len([row for row in source.events if row[0] == "CANCELLED"]) == 1
        assert source.reprice_plan.actions[0].intent.limit_price == Decimal("99.9")
        assert not owner.apply_fill(bid, fill_id="native-mm-fill-2", quantity="0.01",
                                    price="100.1", fee="0.002")
        assert len(f["binance_transport"].calls) == 2
        assert len(f["bybit_transport"].calls) == 1
        assert len(cancel_wire) == 1
        assert not [fact for fact in coordinator.ledger.group_facts("cap05-group")
                    if fact["kind"] == "followup-fault"]
        assert len([row for row in source.events if row[0] == "CANCELLED"]) == 1
        cancelled = next(row for row in source.events if row[0] == "CANCELLED")[3]
        assert cancelled["filled_quantity"] == "0.04"
        assert cancelled["remaining_quantity"] == "0.06"
        assert cancelled["strategy_position"] == "0.04"
        assert cancelled["account_journal_position"] == "0.04"
        assert cancelled["native_physical_position"] is None
        assert "available" not in cancelled
        residual_row = owner.ledger.get(residual.intent.intent_id)
        assert residual_row is not None and residual_row["state"] == "SUBMITTED"
        persisted_residual = json.loads(residual_row["payload"])
        assert persisted_residual["quantity"] == "0.06"
        assert persisted_residual["limit_price"] == "99.9"
        residual_payload = next(
            payload for payload in f["binance_transport"].post_payloads
            if payload.get("newClientOrderId") == residual.intent.client_order_id)
        assert residual_payload["side"] == "BUY"
        assert residual_payload["quantity"] == "0.06"
        assert residual_payload["price"] == "99.9"

        # Reopen real SQLite owners and rebuild concrete adapters. The same
        # immutable initial group claim must not send either native order again.
        await owner.close()
        await f["bybit_owner"].close()
        from test_cap05_core_adverse import reopen_owners
        fresh = reopen_owners(f, tmp_path)
        f["binance_owner"], f["bybit_owner"] = fresh["binance"], fresh["bybit"]
        new_registry = rebuild_native_registry(f)
        new_coordinator = _coordinator(f)
        duplicate = await new_coordinator.execute(
            plan, registry=new_registry, readiness=f["readiness"], evidence=f["evidence"])
        assert duplicate["duplicate"] is True
        assert not new_registry.binding("binance-perp").adapter.transport.calls
        assert not new_registry.binding("bybit-perp").adapter.transport.calls
    finally:
        await f["binance_owner"].close()
        await f["bybit_owner"].close()
