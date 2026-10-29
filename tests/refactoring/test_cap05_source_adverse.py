"""Runtime-only CAP-05 source callback and quality-boundary regressions."""
from dataclasses import replace
from types import ModuleType, SimpleNamespace

import pytest

from core.models import Venue
from execution.execution_ledger import AccountScope
from execution.execution_owner import GateBlocked
from execution.trading_contracts import (
    ActionKind, ExecutionAction, StrategyActionPlan, StrategyOrderContext,
    StrategyOrderEvent, TradingMode,
)
from runtime.real_runner import RealExecutionRunner, RealExecutionRunnerConfig
from test_cap05_native_integration import close_fixture
from cap05_source_fixture import (
    CONFIG_VERSION, NOW, STRATEGY_ID, build_native_group,
)


def _runner(owner, *, coordinator=None):
    return RealExecutionRunner(
        RealExecutionRunnerConfig("cap05-source-test", owner.ledger.scope.account,
            "offline", None, None),
        owner=owner, store=object(), bus=object(), settings=object(),
        group_coordinator=coordinator)


def _event(scope):
    from decimal import Decimal
    return StrategyOrderEvent("source-feedback", STRATEGY_ID, scope,
        "owned-order", "UNKNOWN", None, Decimal("1"), Decimal("0"), Decimal("1"),
        metadata={"config_version": CONFIG_VERSION})


def _context(quality):
    return StrategyOrderContext(None, {"order_state": "UNKNOWN"}, NOW,
        ("cap05-engine", 1), quality)


class _Coordinator:
    def __init__(self):
        self.initial = []
        self.followups = []

    async def execute(self, plan, **kwargs):
        self.initial.append(plan)
        return {"status": "COORDINATED"}

    async def execute_followup(self, plan, **kwargs):
        self.followups.append(plan)
        return {"status": "FOLLOWUP"}


@pytest.mark.asyncio
async def test_public_module_callback_keeps_explicit_instance_abi(tmp_path):
    f = build_native_group(tmp_path)
    owner = f["binance_owner"]
    coordinator = _Coordinator()
    runner = _runner(owner, coordinator=coordinator)
    action = replace(f["plan"].actions[0], group_id=None)
    plan = StrategyActionPlan("module-instance-plan", STRATEGY_ID,
        CONFIG_VERSION, NOW, (action,))
    module = ModuleType("cap05_module_source")
    module.STRATEGY_ID = STRATEGY_ID
    module.ACTION_PROTOCOL_VERSION = 1
    called = []
    instance = object()

    def callback(event, context, supplied_instance):
        called.append(supplied_instance)
        return plan

    module.on_order_event = callback
    # The stub verifies normal dispatch remains an owner operation and is not
    # accidentally redirected through the group coordinator.
    dispatched = []

    async def dispatch_plan(value, **kwargs):
        dispatched.append(value)
        return {"status": "OWNER"}

    owner.dispatch_plan = dispatch_plan
    try:
        result = await runner.dispatch_strategy_order_event(
            module, _event(f["binance_scope"]), _context("VALID"),
            registry=None, evidence={"config_version": CONFIG_VERSION},
            instance=instance, source_trust="trusted_cooperative")
        assert result == {"status": "OWNER"}
        assert called == [instance]
        assert dispatched == [plan]
        assert coordinator.initial == []
    finally:
        await close_fixture(f)


@pytest.mark.asyncio
@pytest.mark.parametrize("followup", [False, True])
async def test_unproven_context_blocks_group_new_before_coordinator(tmp_path, followup):
    f = build_native_group(tmp_path)
    coordinator = _Coordinator()
    runner = _runner(f["binance_owner"], coordinator=coordinator)
    action = f["plan"].actions[0]
    if followup:
        plan = StrategyActionPlan("group-new-followup", STRATEGY_ID,
            CONFIG_VERSION, NOW, (action,), group_followup=True)
    else:
        plan = f["plan"]
    source = SimpleNamespace(STRATEGY_ID=STRATEGY_ID, ACTION_PROTOCOL_VERSION=1)
    source.on_order_event = lambda event, context: plan
    try:
        with pytest.raises(GateBlocked, match="unproven order context"):
            await runner.dispatch_strategy_order_event(
                source, _event(f["binance_scope"]), _context("UNKNOWN"),
                registry=f["registry"], evidence={"config_version": CONFIG_VERSION},
                source_trust="trusted_cooperative")
        assert coordinator.initial == coordinator.followups == []
        assert f["binance_transport"].calls == f["bybit_transport"].calls == []
        assert f["binance_owner"].ledger.get(action.intent.intent_id) is None
    finally:
        await close_fixture(f)


@pytest.mark.asyncio
async def test_unproven_context_allows_only_cancel_followup_to_coordinator(tmp_path):
    f = build_native_group(tmp_path)
    coordinator = _Coordinator()
    runner = _runner(f["binance_owner"], coordinator=coordinator)
    cancel = ExecutionAction("owned-cancel", ActionKind.CANCEL,
        f["binance_scope"], STRATEGY_ID, CONFIG_VERSION, TradingMode.SANDBOX,
        NOW + 1_000_000_000, target_order_id="owned-native-order",
        group_id=f["plan"].actions[0].group_id)
    plan = StrategyActionPlan("cancel-followup", STRATEGY_ID,
        CONFIG_VERSION, NOW, (cancel,), group_followup=True)
    source = SimpleNamespace(STRATEGY_ID=STRATEGY_ID, ACTION_PROTOCOL_VERSION=1)
    source.on_order_event = lambda event, context: plan
    try:
        result = await runner.dispatch_strategy_order_event(
            source, _event(f["binance_scope"]), _context("UNKNOWN"),
            registry=f["registry"], evidence={"config_version": CONFIG_VERSION},
            source_trust="trusted_cooperative")
        assert result == {"status": "FOLLOWUP"}
        assert coordinator.followups == [plan]
        assert coordinator.initial == []
        assert f["binance_transport"].calls == f["bybit_transport"].calls == []
    finally:
        await close_fixture(f)


@pytest.mark.asyncio
async def test_isolated_raw_feedback_returns_codec_plan_without_owner_effects(tmp_path):
    from runtime.strategy_process import plan_document

    f = build_native_group(tmp_path)
    owner = f["binance_owner"]
    runner = _runner(owner)
    plan = f["plan"]
    snapshot = SimpleNamespace(phase="SHADOW")

    class Worker:
        strategy_id = STRATEGY_ID
        expected = {"strategy_id": STRATEGY_ID}
        alive = True
        inflight = False
        queued = 0
        budgets = SimpleNamespace(callback_timeout_ns=1_000_000)

        def submit_event(self, **kwargs):
            return object()

        def submit_feedback(self, **kwargs):
            return object()

        async def await_response(self, request, *, timeout_ns):
            self.inflight = False
            return {"type": "plan", "plan": plan_document(plan)}

    worker = Worker()
    event = _event(f["binance_scope"])
    context = StrategyOrderContext(None, {"order_state": "UNKNOWN"}, NOW,
        ("cap05-engine", 1), "VALID")
    try:
        with pytest.raises(GateBlocked, match="unproven order context"):
            await runner._invoke_order_source_raw(
                None, event, _context("UNKNOWN"), evidence={"config_version": CONFIG_VERSION},
                isolated_worker=worker, isolated_snapshot=snapshot)
        assert owner.ledger.get(plan.actions[0].intent.intent_id) is None
        assert owner.ledger.get(plan.actions[1].intent.intent_id) is None
        assert not f["binance_transport"].calls and not f["bybit_transport"].calls
        raw = await runner._invoke_order_source_raw(
            None, event, context, evidence={"config_version": CONFIG_VERSION},
            isolated_worker=worker, isolated_snapshot=snapshot)
        assert isinstance(raw, StrategyActionPlan)
        assert raw.plan_id == plan.plan_id
        assert owner.ledger.get(plan.actions[0].intent.intent_id) is None
        assert owner.ledger.get(plan.actions[1].intent.intent_id) is None
        assert not f["binance_transport"].calls and not f["bybit_transport"].calls
    finally:
        await close_fixture(f)


@pytest.mark.asyncio
async def test_concurrent_native_acks_serialize_one_isolated_worker(tmp_path):
    import asyncio
    from runtime.strategy_process import order_event_document, plan_document
    from test_cap05_native_integration import coordinator_for
    from cap05_source_fixture import make_market_event_and_view

    f = build_native_group(tmp_path)
    coordinator = coordinator_for(f)
    event, view = make_market_event_and_view()
    evidence = dict(f["evidence"], config_version=CONFIG_VERSION)

    class Worker:
        strategy_id = STRATEGY_ID
        expected = {"strategy_id": STRATEGY_ID}
        alive = True
        inflight = False
        queued = 0
        budgets = SimpleNamespace(callback_timeout_ns=1_000_000_000)

        def __init__(self):
            self.feedback_event_ids = []
            self.feedback_submit_count = 0

        def _submit(self, kind, payload):
            if self.inflight or self.queued:
                raise AssertionError("CAP04 worker received overlapping callbacks")
            self.inflight, self.queued = True, 1
            return {"kind": kind, "payload": payload}

        def submit_event(self, **kwargs):
            return self._submit("event", kwargs)

        def submit_feedback(self, *, event, context, cursor):
            self.feedback_submit_count += 1
            return self._submit("feedback", {"event": order_event_document(event), "context": context,
                                               "cursor": cursor})

        async def await_response(self, request, *, timeout_ns):
            assert timeout_ns == self.budgets.callback_timeout_ns
            if request["kind"] == "feedback":
                await asyncio.sleep(0.01)
                self.feedback_event_ids.append(request["payload"]["event"]["event_id"])
                response = {"type": "signals", "signals": []}
            else:
                response = {"type": "plan", "plan": plan_document(f["plan"])}
            self.inflight, self.queued = False, 0
            return response

    worker = Worker()
    runner = RealExecutionRunner(
        RealExecutionRunnerConfig("cap05-isolated-group", f["binance_scope"].account,
            "offline", None, None),
        owner=f["binance_owner"], store=object(), bus=object(), settings=object(),
        readiness=f["readiness"], group_coordinator=coordinator)
    try:
        result = await runner.dispatch_strategy_market_event(
            None, event, view, registry=f["registry"], evidence=evidence,
            isolated_worker=worker)
        assert result["state"] == "ACKNOWLEDGED", repr(result)
        assert worker.feedback_submit_count == 2, repr({
            "result": result,
            "faults": [fact for fact in coordinator.ledger.group_facts("cap05-group")
                       if fact["kind"] == "followup-fault"],
        })
        assert len(worker.feedback_event_ids) == 2
        assert len(set(worker.feedback_event_ids)) == 2
        assert result["followup_faults"] == []
        assert not worker.inflight and worker.queued == 0
        assert len(f["binance_transport"].calls) == 1
        assert len(f["bybit_transport"].calls) == 1
        assert all(call[0] == "POST" for call in
                   f["binance_transport"].calls + f["bybit_transport"].calls)
    finally:
        await close_fixture(f)
