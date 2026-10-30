"""EXEC-01 common owner: PAPER stays local, cancel stays owner-bound."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.models import AssetClass, InstrumentId, OrderBookEvent, OrderDestination, OrderIntent, Side, TradeEvent, Venue
from execution.execution_ledger import AccountScope, ExecutionLedger
from execution.execution_owner import ExecutionBudgets, ExecutionOwner
from execution.paper_execution import PaperExecution
from execution.trading_contracts import ActionKind, ExecutionAction, StrategyActionPlan, TradingMode
from execution.venues.registry import ForeignOrderError
from runtime.runtime_control import GateBlocked, RuntimeControl
from market.hot_path import DecisionSnapshot, StrategyDataView
from test_portfolio_ledger import seeded



class NativeStockSourceFixture:
    """Importable native-capable source fixture for owner/runner integration."""

    ACTION_PROTOCOL_VERSION = 1
    STRATEGY_ID = "exec01_native_stock_fixture"

    def __init__(self, scope, native_venue, instrument):
        self.scope = scope
        self.native_venue = native_venue
        self.instrument = instrument
        self.config_version = "cfg-1"
        self.cancel = None
        self.received = []
        self.market_calls = 0

    def on_event(self, event, data):
        self.market_calls += 1
        event_ns = event.source_ts_ns or event.wallclock_ts_ns
        if self.market_calls > 1:
            return StrategyActionPlan("source-stock-noop:" + event.event_id,
                self.STRATEGY_ID, self.config_version, event_ns, ())
        intent = OrderIntent(
            intent_id="source-stock-intent", job_id=self.STRATEGY_ID,
            signal_id="source-stock-signal", destination=OrderDestination.REAL,
            client_order_id="source-stock-client", instrument=self.instrument,
            side=Side.BUY, quantity=Decimal("1"), limit_price=Decimal("100"),
            metadata={"account_id": self.scope.account, "environment": self.scope.environment,
                      "execution_venue_id": self.native_venue})
        self.intent = intent
        action = ExecutionAction(
            action_id="source-stock-new", action=ActionKind.NEW, scope=self.scope,
            strategy_id=self.STRATEGY_ID, config_version=self.config_version,
            mode=TradingMode.SANDBOX, expiry_ns=event_ns + 1_000_000_000, intent=intent)
        return StrategyActionPlan("source-stock-plan", self.STRATEGY_ID,
                                  self.config_version, event_ns, (action,))

    def on_order_event(self, event, context):
        self.received.append((event, context))
        actions = ()
        if event.status == "ACCEPTED":
            self.cancel = ExecutionAction(
                action_id="source-stock-cancel", action=ActionKind.CANCEL,
                scope=self.scope, strategy_id=self.STRATEGY_ID,
                config_version=self.config_version, mode=TradingMode.SANDBOX,
                expiry_ns=context.now_ns + 1_000_000_000,
                target_order_id=event.metadata["client_order_id"])
            actions = (self.cancel,)
        return StrategyActionPlan("source-stock-feedback:" + event.event_id,
            self.STRATEGY_ID, self.config_version, context.now_ns, actions)

def _scope(environment="sandbox"):
    return AccountScope("binance", "acct", "spot", environment)


def _ledger(tmp_path, scope, *, max_feedback=8):
    return ExecutionLedger(tmp_path / "orders.sqlite3", scope, max_intents=8,
                           max_evidence_bytes=8192, max_fills=8,
                           authority_root=tmp_path / "authority",
                           max_feedback=max_feedback, max_feedback_bytes=8192)


def _owner(ledger):
    return ExecutionOwner(ledger, RuntimeControl(ledger), object(),
                          ExecutionBudgets(Decimal("1000"), Decimal("10000"), 4,
                                           10_000_000_000, 1.0, 1.0))


def _intent(scope):
    return OrderIntent(
        intent_id="one", job_id="alpha", signal_id="signal-one",
        destination=OrderDestination.REAL, client_order_id="client-one",
        instrument=InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_SPOT,
                                raw_symbol="BTCUSDT", std_symbol="BTCUSDT"),
        side=Side.BUY, quantity=Decimal("1"), limit_price=Decimal("10"),
        metadata={"account_id": scope.account, "environment": scope.environment})


def _cancel(scope, strategy, mode=TradingMode.SANDBOX):
    return ExecutionAction(
        action_id="cancel-" + strategy, action=ActionKind.CANCEL, scope=scope,
        strategy_id=strategy, config_version="cfg-1", mode=mode,
        expiry_ns=2_000_000_000, target_order_id="client-one")


def test_paper_matching_is_local_and_strict_owner_never_selects_paper_route(tmp_path):
    with seeded(tmp_path) as book:
        matcher = PaperExecution(
            book, entry_latency_ns=0, cancel_latency_ns=0, max_open_orders=4,
            max_depth=8, max_receipts=8, max_state_bytes=100_000,
            stale_after_ns=1_000)
        report = matcher.report()
        assert report["mode"] == "paper"
        assert report["external_mutations"] == 0

    scope = _scope("paper")
    ledger = _ledger(tmp_path, scope)
    owner = _owner(ledger)
    registry = SimpleNamespace(binding=lambda route_id: SimpleNamespace(
        route=SimpleNamespace(account_scope=scope, environment="paper")))
    with pytest.raises(GateBlocked, match="PAPER"):
        owner._binding(registry, "paper-route",
                       _cancel(scope, "alpha", mode=TradingMode.PAPER))
    ledger.close()


def test_cross_strategy_cancel_is_rejected_before_any_registry_call(tmp_path):
    scope = _scope()
    ledger = _ledger(tmp_path, scope)
    owner = _owner(ledger)
    intent = _intent(scope)
    ledger.authorize(intent, evidence={"source": "test"}, expiry_ns=2_000_000_000,
                     epochs={"global": 1, "account:acct": 1, "strategy:alpha": 1},
                     reserve_unit=Decimal("10"), route_id="binance-spot",
                     strategy_id="alpha", config_version="cfg-1",
                     attempt_id="one:attempt:1", action="NEW")
    with pytest.raises(ForeignOrderError, match="another strategy"):
        owner._resolve_cancel_alias(None, _cancel(scope, "beta"))
    # The owning strategy still resolves the same durable order.
    route_id, row = owner._resolve_cancel_alias(
        SimpleNamespace(route=lambda route: SimpleNamespace(account_scope=scope)),
        _cancel(scope, "alpha"))
    assert route_id == "binance-spot" and row["strategy_id"] == "alpha"
    ledger.close()


def test_unknown_keeps_reservation_and_cannot_be_reopened_as_sent(tmp_path):
    scope = _scope()
    ledger = _ledger(tmp_path, scope)
    owner = _owner(ledger)
    intent = _intent(scope)
    ledger.authorize(intent, evidence={"source": "test"}, expiry_ns=2_000_000_000,
                     epochs={"global": 1, "account:acct": 1, "strategy:alpha": 1},
                     reserve_unit=Decimal("10"), route_id="binance-spot",
                     strategy_id="alpha", config_version="cfg-1",
                     attempt_id="one:attempt:1", action="NEW")
    ledger.mark_handoff(intent.intent_id)
    ledger.outcome(intent.intent_id, "UNKNOWN", {"ambiguous": True})
    row = ledger.get(intent.intent_id)
    assert row["state"] == "UNKNOWN"
    assert Decimal(row["reservation"]) == Decimal("10")
    assert [item["intent_id"] for item in ledger.unresolved()] == [intent.intent_id]
    owner.control.halt("alpha")
    with pytest.raises(GateBlocked, match="closed"):
        owner.control.validate("alpha", {"global": 1, "account:acct": 1,
                                         "strategy:alpha": 1})
    ledger.close()


@pytest.mark.asyncio
async def test_explicit_cross_venue_source_plan_uses_synthetic_stock_route_and_facts(tmp_path):
    from dataclasses import replace
    from core.models import Venue
    from execution.trading_contracts import StrategyActionPlan
    from execution.venues import dummy_stock
    from execution.venues.registry import AliasBinding, RouteSpec, VenueRegistry
    from test_exec01_native import NOW, action, cancel_action, evidence, make_owner, open_gates, readiness, intent
    from test_exec01_native import ZERO_FACTS

    from execution.process_lease import ProcessLease
    ProcessLease.register_extension_venue("dummy-stock")
    scope = AccountScope("dummy-stock", "nh-fixture", "cash", "sandbox")
    native_id = "nh-fixture-physical"
    adapter = dummy_stock.StockDummyAdapter(scope)
    snapshot = await adapter.account_state()
    product = await adapter.instrument_metadata("005930")
    owner = make_owner(tmp_path, scope, native_id)
    route = RouteSpec("nh-fixture", "dummy-stock", "cash", "cash", "sandbox", scope, native_id)
    caps = replace(dummy_stock.STOCK_CAPABILITIES, synthetic_fixture=True)
    registry = VenueRegistry(max_routes=2, max_aliases=4, max_fact_bytes=4096)

    def facts(symbol):
        assert symbol == "005930"
        return {
            "quality": "SYNTHETIC", "fee_model": "configured_taker_rate",
            "taker_fee_rate": "0.001", "max_age_ns": 1_000_000_000,
            "account_facts": {
                "scope_key": scope.key, "native_account_id": native_id,
                "reconciliation_state": "RECONCILED", "unknown": False,
                "observed_ns": NOW, "account_version": owner.ledger.account_version(),
                "currency": snapshot["currency"], "equity": snapshot["equity"],
                "available": snapshot["available"], "raw": dict(snapshot),
            },
            "product_specs": {symbol: {
                **product, "scope_key": scope.key, "venue": route.venue,
                "product": route.product, "environment": route.environment,
            }},
        }

    registry.register(route, adapter, dummy_stock.OPERATIONS, caps,
                      reference_provider=lambda: dict(ZERO_FACTS), native_facts_provider=facts)
    registry.register_aliases((AliasBinding(
        NativeStockSourceFixture.STRATEGY_ID, "kiwoom-listing-to-nh-execution", route.route_id, "005930",
        "cash", "cash", "sandbox", scope),))
    open_gates(owner, NativeStockSourceFixture.STRATEGY_ID)
    source_intent = intent("nh-source-order", venue=Venue.KIWOOM, asset=AssetClass.EQUITY,
        symbol="005930", account=scope.account, environment=scope.environment, quantity="1", price="100")
    source_intent = source_intent.model_copy(update={
        "job_id": "example_crypto_order_policy",
        "metadata": {**source_intent.metadata, "execution_venue_id": route.venue},
    })
    source = NativeStockSourceFixture(scope, route.venue, source_intent.instrument)
    from runtime.real_runner import RealExecutionRunner, RealExecutionRunnerConfig
    runner = RealExecutionRunner(RealExecutionRunnerConfig(
        job_id="source-runner", account_id=scope.account, destination_name="dummy-stock",
        max_order_notional=Decimal("10000"), max_position_notional=Decimal("10000")),
        store=object(), bus=object(), broker=None, owner=owner,
        readiness=readiness(owner, scope, source_intent.instrument.key), settings=object())
    try:
        market_event = TradeEvent(event_id="source-market-1", instrument=source_intent.instrument,
            venue=source_intent.instrument.venue, provider="fixture", source_ts_ns=NOW,
            price=Decimal("100"), quantity=Decimal("1"))
        market_snapshot = DecisionSnapshot("source-run", 1, NOW, "SHADOW", 1, {}, {}, {},
            {source_intent.instrument.key: market_event}, {}, {}, {}, {})
        market_data = StrategyDataView(market_snapshot, ready=True, phase="SHADOW")
        with pytest.raises(GateBlocked, match="trusted_cooperative"):
            await runner.dispatch_strategy_market_event(
                source, market_event, market_data, registry=registry,
                readiness=readiness(owner, scope, source_intent.instrument.key),
                evidence=evidence(owner, source.STRATEGY_ID, "sandbox"))
        assert not hasattr(source, "intent")
        result = await runner.dispatch_strategy_market_event(
            source, market_event, market_data, registry=registry,
            readiness=readiness(owner, scope, source_intent.instrument.key),
            evidence=evidence(owner, source.STRATEGY_ID, "sandbox"),
            source_trust="trusted_cooperative")
        assert result["results"][0]["status"] == "CANCEL_PENDING"
        duplicate = await runner.dispatch_strategy_market_event(
                source, market_event, market_data, registry=registry,
                readiness=readiness(owner, scope, source_intent.instrument.key),
                evidence=evidence(owner, source.STRATEGY_ID, "sandbox"),
                source_trust="trusted_cooperative")
        assert duplicate["status"] == "DUPLICATE_SOURCE_EVENT"
        assert source.market_calls == 1
        changed_replay = market_event.model_copy(update={"price": Decimal("101")})
        changed_snapshot = DecisionSnapshot("source-run", 1, NOW, "SHADOW", 1, {}, {}, {},
            {source_intent.instrument.key: changed_replay}, {}, {}, {}, {})
        changed_data = StrategyDataView(changed_snapshot, ready=True, phase="SHADOW")
        with pytest.raises(GateBlocked, match="claim failed closed"):
            await runner.dispatch_strategy_market_event(
                source, changed_replay, changed_data, registry=registry,
                readiness=readiness(owner, scope, source_intent.instrument.key),
                evidence=evidence(owner, source.STRATEGY_ID, "sandbox"),
                source_trust="trusted_cooperative")
        assert source.market_calls == 1
        restarted = RealExecutionRunner(RealExecutionRunnerConfig(
            job_id="source-runner-restart", account_id=scope.account, destination_name="dummy-stock",
            max_order_notional=Decimal("10000"), max_position_notional=Decimal("10000")),
            store=object(), bus=object(), broker=None, owner=owner,
            readiness=readiness(owner, scope, source_intent.instrument.key), settings=object())
        next_event = TradeEvent(event_id="source-market-2", instrument=source_intent.instrument,
            venue=source_intent.instrument.venue, provider="fixture", source_ts_ns=NOW,
            price=Decimal("100"), quantity=Decimal("1"))
        next_snapshot = DecisionSnapshot("source-run", 2, NOW, "SHADOW", 1, {}, {}, {},
            {source_intent.instrument.key: next_event}, {}, {}, {}, {})
        with pytest.raises(GateBlocked, match="source-state contract"):
            await restarted.dispatch_strategy_market_event(
                source, next_event, StrategyDataView(next_snapshot, ready=True, phase="SHADOW"),
                registry=registry, readiness=readiness(owner, scope, source_intent.instrument.key),
                evidence=evidence(owner, source.STRATEGY_ID, "sandbox"),
                source_trust="trusted_cooperative")
        assert source.market_calls == 1
        next_result = await runner.dispatch_strategy_market_event(
            source, next_event, StrategyDataView(next_snapshot, ready=True, phase="SHADOW"),
            registry=registry, readiness=readiness(owner, scope, source_intent.instrument.key),
            evidence=evidence(owner, source.STRATEGY_ID, "sandbox"),
            source_trust="trusted_cooperative")
        assert next_result["results"] == ()
        assert source.market_calls == 2
        submitted_intent = source.intent
        assert adapter.mutations == [submitted_intent.client_order_id, submitted_intent.client_order_id]
        assert owner.ledger.get(submitted_intent.intent_id)["route_id"] == route.route_id
        assert [event.status for event, _ in source.received] == ["ACCEPTED", "CANCEL_PENDING"]
        assert source.received[0][1].quality == "SYNTHETIC"
        assert owner.ledger.feedback(source.STRATEGY_ID)[0]["event"]["metadata"]["fact_quality"] == "SYNTHETIC"
        terminal = await owner.reconcile_order(source.cancel, registry=registry)
        assert terminal["status"] == "CANCELED"
        assert owner.ledger.get(submitted_intent.intent_id)["state"] == "CANCELLED"
        assert adapter.fill("005930", submitted_intent.client_order_id,
                            quantity="1", price="100", fee="0.1", fill_id="nh-fill-1") is True
        assert owner.apply_fill(source.cancel, fill_id="nh-fill-1", quantity="1", price="100",
                                fee="0.1", order_id="stock-" + submitted_intent.client_order_id,
                                fact_quality="SYNTHETIC") is True
        await owner.deliver_feedback(source.STRATEGY_ID, source.on_order_event)
        assert source.received[-1][0].status == "FILLED"
        assert source.received[-1][1].quality == "SYNTHETIC"
        assert owner.ledger.position(submitted_intent.instrument.key) == Decimal("1")
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_untrusted_source_entrypoints_use_isolated_worker_protocol(tmp_path, monkeypatch):
    import asyncio
    import sys
    from types import ModuleType
    from execution.trading_contracts import StrategyActionPlan, StrategyOrderContext, StrategyOrderEvent
    from runtime.real_runner import RealExecutionRunner, RealExecutionRunnerConfig
    from test_exec01_native import NOW, make_owner

    scope = _scope()
    owner = make_owner(tmp_path, scope, "native-isolated-source-test")
    strategy_id, config_version = "isolated-source", "cfg-isolated"
    plan = StrategyActionPlan("isolated-plan", strategy_id, config_version, NOW, ())

    class Worker:
        strategy_id = "isolated-source"
        expected = {"strategy_id": "isolated-source"}
        alive = True
        inflight = False
        queued = 0
        budgets = SimpleNamespace(callback_timeout_ns=1_000_000)

        def submit_event(self, **kwargs):
            assert kwargs["ready"] is True and kwargs["phase"] == "SHADOW"
            self.inflight, self.queued = True, 1
            return {"request_id": "market-request"}

        def submit_feedback(self, **kwargs):
            assert kwargs["context"]["quality"] == "VALID"
            self.inflight, self.queued = True, 1
            return {"request_id": "feedback-request"}

        async def await_response(self, request, *, timeout_ns):
            assert timeout_ns == self.budgets.callback_timeout_ns
            self.inflight, self.queued = False, 0
            return {"type": "plan"}

    worker = Worker()
    decoder_module = ModuleType("runtime.strategy_process")
    decoder_module.result_from_document = lambda _document: plan
    monkeypatch.setitem(sys.modules, "runtime.strategy_process", decoder_module)
    runner = RealExecutionRunner(RealExecutionRunnerConfig(
        job_id="isolated-runner", account_id=scope.account, destination_name="offline",
        max_order_notional=Decimal("1000"), max_position_notional=Decimal("1000")),
        store=object(), bus=object(), broker=None, owner=owner, settings=object())
    source_instrument = _intent(scope).instrument
    event = TradeEvent(event_id="isolated-market-1", instrument=source_instrument,
        venue=source_instrument.venue, provider="fixture", source_ts_ns=NOW,
        price=Decimal("100"), quantity=Decimal("1"))
    snapshot = DecisionSnapshot("run-1", 1, NOW, "SHADOW", 1, {}, {}, {},
        {source_instrument.key: event}, {}, {}, {}, {})
    data = StrategyDataView(snapshot, ready=True, phase="SHADOW")
    evidence_doc = {"config_version": config_version}
    try:
        result = await runner.dispatch_strategy_market_event(
            None, event, data, registry=None, evidence=evidence_doc,
            isolated_worker=worker)
        assert result["plan_id"] == "isolated-plan"
        assert not worker.inflight and worker.queued == 0

        scope_event = AccountScope("binance", "acct", "spot", "sandbox")
        feedback = StrategyOrderEvent("feedback-event", strategy_id, scope_event,
            "order-1", "ACCEPTED", None, Decimal("1"), Decimal("0"), Decimal("1"),
            metadata={"config_version": config_version})
        context = StrategyOrderContext(None, {"order_state": "SUBMITTED"}, NOW,
                                       ("run-1", 1), "VALID")
        order_result = await runner.dispatch_strategy_order_event(
            None, feedback, context, registry=None, evidence=evidence_doc,
            isolated_worker=worker, isolated_snapshot=snapshot)
        assert order_result["plan_id"] == "isolated-plan"
        assert not worker.inflight and worker.queued == 0

        class SlowWorker(Worker):
            async def await_response(self, request, *, timeout_ns):
                await asyncio.sleep(0.02)
                self.inflight, self.queued = False, 0
                raise TimeoutError("bounded fixture timeout")

        slow = SlowWorker()
        retry_event = event.model_copy(update={"event_id": "isolated-market-2"})
        retry_snapshot = DecisionSnapshot("run-1", 2, NOW + 1, "SHADOW", 1, {}, {}, {},
            {source_instrument.key: retry_event}, {}, {}, {}, {})
        retry_data = StrategyDataView(retry_snapshot, ready=True, phase="SHADOW")
        progressed = []
        async def parent_heartbeat():
            await asyncio.sleep(0.001)
            progressed.append(True)
        with pytest.raises(GateBlocked, match="exceeded its bounded deadline"):
            await asyncio.gather(
                runner.dispatch_strategy_market_event(None, retry_event, retry_data, registry=None,
                    evidence=evidence_doc, isolated_worker=slow),
                parent_heartbeat())
        assert progressed == [True]
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_frozen_cap04_process_runs_through_public_source_entrypoint(tmp_path):
    process_api = pytest.importorskip("runtime.strategy_process")
    import hashlib
    import time
    from pathlib import Path
    from runtime.real_runner import RealExecutionRunner, RealExecutionRunnerConfig

    now_ns = time.time_ns()
    scope = _scope("paper")
    owner = _owner(_ledger(tmp_path, scope))
    strategy_id, config_version = "example_crypto_order_policy", "cfg-process-test"
    facts = {"job_id": strategy_id, "config_version": config_version,
        "accounts": {"test": {"mode": "paper", "scope": {
            "venue": scope.venue, "account": scope.account, "product": scope.product,
            "environment": scope.environment}}}}
    plugin_path = "/work/src/strategies/crypto/example_order_policy.py"
    source_sha = hashlib.sha256(Path(plugin_path).read_bytes()).hexdigest()
    isolation = process_api.StrategyIsolation(allowed_source_roots=("/work/src",))
    worker = process_api.IsolatedStrategyWorker(strategy_id=strategy_id,
        module_name=None, module_path=plugin_path,
        config={"account_key": "test"}, registration_facts=facts,
        expected={"strategy_id": strategy_id, "action_protocol_version": 1,
                  "source_sha256": source_sha},
        isolation=isolation, parent_incarnation="parent-test", now_ns=time.time_ns)
    workers = [worker]
    instrument = _intent(scope).instrument
    event = OrderBookEvent(event_id="process-market-1", instrument=instrument,
        venue=instrument.venue, provider="fixture", source_ts_ns=now_ns,
        depth=1, bids=[], asks=[])
    snapshot = DecisionSnapshot("process-run", 1, now_ns, "SHADOW", 1, {}, {},
        {instrument.key: "VALID"}, {}, {instrument.key: event}, {}, {}, {})
    data = StrategyDataView(snapshot, ready=True, phase="SHADOW")
    runner = RealExecutionRunner(RealExecutionRunnerConfig(
        job_id="process-entrypoint", account_id=scope.account, destination_name="offline",
        max_order_notional=Decimal("1000"), max_position_notional=Decimal("1000")),
        store=object(), bus=object(), broker=None, owner=owner, settings=object())
    try:
        await worker.await_response(worker.start(), timeout_ns=2_000_000_000)
        result = await runner.dispatch_strategy_market_event(None, event, data,
            registry=None, evidence={"config_version": config_version}, isolated_worker=worker)
        assert result["results"] == ()
        duplicate = await runner.dispatch_strategy_market_event(None, event, data,
            registry=None, evidence={"config_version": config_version}, isolated_worker=worker)
        assert duplicate["status"] == "DUPLICATE_SOURCE_EVENT"
        saved = await runner.checkpoint_strategy_source(worker, config_version=config_version)
        assert saved["cut"]["market_event_id"] == event.event_id

        await worker.close_async()
        restarted_worker = process_api.IsolatedStrategyWorker(strategy_id=strategy_id,
            module_name=None, module_path=plugin_path,
            config={"account_key": "test"}, registration_facts=facts,
            expected={"strategy_id": strategy_id, "action_protocol_version": 1,
                      "source_sha256": source_sha},
            isolation=isolation, parent_incarnation="parent-restart", now_ns=time.time_ns)
        workers.append(restarted_worker)
        restarted_runner = RealExecutionRunner(RealExecutionRunnerConfig(
            job_id="process-entrypoint-restart", account_id=scope.account,
            destination_name="offline", max_order_notional=Decimal("1000"),
            max_position_notional=Decimal("1000")),
            store=object(), bus=object(), broker=None, owner=owner, settings=object())
        await restarted_worker.await_response(restarted_worker.start(), timeout_ns=2_000_000_000)
        owner.ledger._db.execute(
            "UPDATE execution_source_checkpoints SET sha256=? WHERE strategy_id=?",
            ("0" * 64, strategy_id))
        with pytest.raises(GateBlocked, match="missing or corrupt"):
            await restarted_runner.restore_strategy_source(
                restarted_worker, config_version=config_version)
        owner.ledger._db.execute(
            "UPDATE execution_source_checkpoints SET sha256=? WHERE strategy_id=?",
            (saved["sha256"], strategy_id))
        next_event = event.model_copy(update={"event_id": "process-market-2"})
        next_snapshot = DecisionSnapshot("process-run", 2, now_ns, "SHADOW", 1, {}, {},
            {instrument.key: "VALID"}, {}, {instrument.key: next_event}, {}, {}, {})
        next_data = StrategyDataView(next_snapshot, ready=True, phase="SHADOW")
        with pytest.raises(GateBlocked, match="source-state contract"):
            await restarted_runner.dispatch_strategy_market_event(None, next_event, next_data,
                registry=None, evidence={"config_version": config_version},
                isolated_worker=restarted_worker)
        restored = await restarted_runner.restore_strategy_source(
            restarted_worker, config_version=config_version)
        assert restored["status"] == "RESTORED"
        resumed = await restarted_runner.dispatch_strategy_market_event(None, next_event, next_data,
            registry=None, evidence={"config_version": config_version},
            isolated_worker=restarted_worker)
        assert resumed["plan_id"].endswith(":maker:2")
        await restarted_runner.checkpoint_strategy_source(
            restarted_worker, config_version=config_version)
        await restarted_worker.close_async()
        owner.ledger.record_feedback(strategy_id, "post-cut-feedback", {
            "event_id": "post-cut-feedback", "status": "ACCEPTED"})
        owner.ledger.claim_feedback(strategy_id, limit=1)
        stale_worker = process_api.IsolatedStrategyWorker(strategy_id=strategy_id,
            module_name=None, module_path=plugin_path,
            config={"account_key": "test"}, registration_facts=facts,
            expected={"strategy_id": strategy_id, "action_protocol_version": 1,
                      "source_sha256": source_sha},
            isolation=isolation, parent_incarnation="parent-stale", now_ns=time.time_ns)
        workers.append(stale_worker)
        stale_runner = RealExecutionRunner(RealExecutionRunnerConfig(
            job_id="process-entrypoint-stale", account_id=scope.account,
            destination_name="offline", max_order_notional=Decimal("1000"),
            max_position_notional=Decimal("1000")),
            store=object(), bus=object(), broker=None, owner=owner, settings=object())
        await stale_worker.await_response(stale_worker.start(), timeout_ns=2_000_000_000)
        with pytest.raises(GateBlocked, match="checkpoint is stale"):
            await stale_runner.restore_strategy_source(stale_worker,
                config_version=config_version)
        await stale_worker.close_async()
    finally:
        for active_worker in workers:
            if active_worker.alive:
                await active_worker.close_async()
        await owner.close()
