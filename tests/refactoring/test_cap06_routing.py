"""Selected config -> bounded callback queue -> existing native owner path."""
import asyncio
from dataclasses import replace
from decimal import Decimal
import sys
from types import MappingProxyType, SimpleNamespace

import pytest

import cap06_source_fixture as source_module
sys.modules["strategies.cap06_source_fixture"] = source_module

from execution.trading_contracts import StrategyActionPlan
from market.hot_path import ApplyResult
from runtime.real_runner import RealExecutionRunner, RealExecutionRunnerConfig
from runtime.selected_execution import (NativeSelectionBinding, NativeSelectionDispatcher,
                                        activate_paper_selections, parse_strategy_selections)
from cap05_source_fixture import CONFIG_VERSION, NOW, STRATEGY_ID, build_native_group, make_market_event_and_view
from test_cap06_selection import selection


def prepare_scripted_reconcile(fixture, route_id="binance-perp"):
    from execution.perp_execution import HttpResponse
    adapter = fixture["registry"].binding(route_id).adapter
    transport = adapter.transport
    original_get = transport._get
    def reconcile_get(url, params):
        if adapter.scope.venue == "bybit":
            if "/v5/account/wallet-balance" in url:
                # Synthetic response uses documented Unified isolated coin fields:
                # https://bybit-exchange.github.io/docs/v5/account/wallet-balance
                return HttpResponse(200, {"retCode": 0, "result": {"list": [{
                    "accountType": "UNIFIED", "totalEquity": "1000000",
                    "totalAvailableBalance": "1000000", "totalInitialMargin": "0",
                    "totalMaintenanceMargin": "0", "coin": [{"coin": "USDT",
                        "equity": "1000000", "walletBalance": "1000000",
                        "totalPositionIM": "0", "totalOrderIM": "0",
                        "locked": "0", "bonus": "0"}]}]}})
            if "/v5/position/list" in url or "/v5/order/realtime" in url or "/v5/execution/list" in url:
                return HttpResponse(200, {"retCode": 0, "result": {"list": [], "nextPageCursor": ""}})
        if "/fapi/v3/account" in url:
            body = {"assets": [{"asset": "USDT", "marginBalance": "1000000",
                "availableBalance": "1000000", "initialMargin": "0", "maintMargin": "0"}]}
            return HttpResponse(200, body)
        if "/fapi/v3/positionRisk" in url or "/fapi/v1/openOrders" in url:
            return HttpResponse(200, [])
        return original_get(url, params)
    transport._get = reconcile_get
    account_binding = fixture["registry"].binding(route_id)
    original_facts = account_binding.native_facts_provider
    raw_account = ({"accountType": "UNIFIED", "totalEquity": "1000000",
        "totalAvailableBalance": "1000000", "totalInitialMargin": "0",
        "totalMaintenanceMargin": "0", "coin": [{"coin": "USDT",
            "equity": "1000000", "walletBalance": "1000000",
            "totalPositionIM": "0", "totalOrderIM": "0", "locked": "0", "bonus": "0"}]}
        if adapter.scope.venue == "bybit" else {"assets": [{"asset": "USDT",
            "marginBalance": "1000000", "availableBalance": "1000000",
            "initialMargin": "0", "maintMargin": "0"}]})
    def exact_facts(symbol):
        value = original_facts(symbol)
        value["account_facts"]["normalized"]["raw"] = raw_account
        route_scope_key = fixture["registry"].route(route_id).account_scope.key
        owners = [fixture[key] for key in ("binance_owner", "bybit_owner") if key in fixture]
        owner = next((item for item in owners if item.ledger.scope.key == route_scope_key), None)
        if owner is not None:
            value["account_facts"]["observed_ns"] = owner.clock()
        value["max_age_ns"] = 20_000_000_000
        return value
    object.__setattr__(account_binding, "native_facts_provider", exact_facts)


@pytest.mark.asyncio
async def test_selected_testnet_callback_is_queued_then_dispatched_by_existing_runner(tmp_path):
    fixture = build_native_group(tmp_path)
    owner = fixture["binance_owner"]
    action = replace(fixture["plan"].actions[0], group_id=None)
    source_module.PLAN = StrategyActionPlan("cap06-selected", STRATEGY_ID,
        CONFIG_VERSION, NOW, (action,))
    source = source_module.create_strategy()
    key = fixture["binance_scope"].key
    source_facts = {"accounts": {key: {"scope": {
        "venue": "binance", "account": "acct-binance", "product": "linear_perpetual",
        "environment": "sandbox"}, "mode": "sandbox"}}}
    runner = RealExecutionRunner(RealExecutionRunnerConfig(
        "cap06-job", owner.ledger.scope.account, "fixture", Decimal("1000000"),
        Decimal("1000000")), store=object(), bus=object(), settings=object(), owner=owner,
        group_coordinator=None)
    event, data = make_market_event_and_view()
    configured = selection()
    configured["account"] = owner.ledger.scope.account
    configured["venue"] = owner.ledger.scope.venue
    configured["product"] = owner.ledger.scope.product
    configured["registration"]["accounts"] = [key]
    configured["registration"]["risk"] = {key: next(iter(configured["registration"]["risk"].values()))}
    configured["registration"]["module"] = "strategies.cap06_source_fixture"
    configured["registration"]["config"]["config_version"] = CONFIG_VERSION
    configured["registration"]["config_version"] = CONFIG_VERSION
    configured["registration"]["required_feeds"] = [event.instrument.key]
    route = parse_strategy_selections([configured])[0]
    binding = NativeSelectionBinding(runner, owner, source, fixture["registry"],
        readiness=fixture["readiness"][key],
        evidence_provider=lambda result, snapshot: {
            "config_version": CONFIG_VERSION, **fixture["evidence"][key]},
        registration_facts=source_facts, route_id="binance-perp")
    dispatcher = NativeSelectionDispatcher(route, binding)
    prepare_scripted_reconcile(fixture)
    await dispatcher.prepare_promotion()
    dispatcher.task = asyncio.create_task(dispatcher.run())
    snapshot = replace(data.hot_path, phase="LIVE",
        quality=MappingProxyType({event.instrument.key: "VALID"}))
    result = ApplyResult(event, True, False, "", snapshot.engine_incarnation, snapshot.apply_seq)
    try:
        dispatcher.enqueue_applied(result, snapshot)
        await asyncio.wait_for(dispatcher.queue.join(), timeout=3)
        assert source.calls == 1
        assert not dispatcher.errors, dispatcher.errors
        assert dispatcher.results[0]["results"][0]["status"] == "SUBMITTED"
        assert len(fixture["binance_transport"].calls) == 1
        assert fixture["bybit_transport"].calls == []
    finally:
        dispatcher.task.cancel()
        await asyncio.gather(dispatcher.task, return_exceptions=True)
        await fixture["binance_owner"].close()
        await fixture["bybit_owner"].close()


@pytest.mark.asyncio
async def test_expired_native_receipt_renews_in_async_drain_before_next_source_call(tmp_path):
    fixture = build_native_group(tmp_path)
    owner = fixture["binance_owner"]
    event, view = make_market_event_and_view()
    source_module.PLAN = []
    source = source_module.create_strategy()
    key = owner.ledger.scope.key
    source_facts = {"accounts": {key: {"scope": {"venue": "binance",
        "account": "acct-binance", "product": "linear_perpetual",
        "environment": "sandbox"}, "mode": "sandbox"}}}
    runner = RealExecutionRunner(RealExecutionRunnerConfig("cap06-renew",
        owner.ledger.scope.account, "fixture", Decimal("1000000"), Decimal("1000000")),
        store=object(), bus=object(), settings=object(), owner=owner)
    configured = selection()
    configured.update(venue=owner.ledger.scope.venue, account=owner.ledger.scope.account,
        product=owner.ledger.scope.product)
    configured["registration"].update(module="strategies.cap06_source_fixture",
        accounts=[key], config_version=CONFIG_VERSION, required_feeds=[event.instrument.key])
    configured["registration"]["risk"] = {key: next(iter(configured["registration"]["risk"].values()))}
    configured["registration"]["config"]["config_version"] = CONFIG_VERSION
    binding = NativeSelectionBinding(runner, owner, source, fixture["registry"],
        readiness=fixture["readiness"][key],
        evidence_provider=lambda *_: {"config_version": CONFIG_VERSION,
            **fixture["evidence"][key]}, registration_facts=source_facts,
        route_id="binance-perp")
    dispatcher = NativeSelectionDispatcher(parse_strategy_selections([configured])[0], binding)
    current = [NOW]
    owner.clock = lambda: current[0]
    prepare_scripted_reconcile(fixture)
    try:
        await dispatcher.prepare_promotion()
        initial_expiry = dispatcher._promotion_receipt.expires_ns
        dispatcher.task = asyncio.create_task(dispatcher.run())
        snapshot = replace(view.hot_path, phase="LIVE",
            quality=MappingProxyType({event.instrument.key: "VALID"}))
        dispatcher.enqueue_applied(ApplyResult(event, True, False, "",
            snapshot.engine_incarnation, snapshot.apply_seq), snapshot)
        await asyncio.wait_for(dispatcher.queue.join(), timeout=3)
        assert source.calls == 1
        current[0] = NOW + 6_000_000_000
        next_event = event.model_copy(update={"event_id": "cap06-renewed-event",
            "source_ts_ns": current[0], "receive_ts_ns": current[0]})
        next_snapshot = replace(snapshot, apply_seq=snapshot.apply_seq + 1,
            now_ns=current[0], trades=MappingProxyType({event.instrument.key: next_event}))
        dispatcher.enqueue_applied(ApplyResult(next_event, True, False, "",
            next_snapshot.engine_incarnation, next_snapshot.apply_seq), next_snapshot)
        await asyncio.wait_for(dispatcher.queue.join(), timeout=3)
        assert source.calls == 2
        assert dispatcher._promotion_receipt.observed_ns >= current[0]
        assert dispatcher._promotion_receipt.expires_ns > initial_expiry
        assert not dispatcher.errors, dispatcher.errors
        action = replace(fixture["plan"].actions[0], group_id=None,
            expiry_ns=current[0] + 10_000_000_000)
        _, row = owner.ledger.authorize(action.intent, evidence={"fixture": "version-cut"},
            expiry_ns=current[0] + 10_000_000_000,
            epochs=owner.control.snapshot(STRATEGY_ID)["epochs"], reserve_unit=Decimal("100"),
            route_id="binance-perp", strategy_id=STRATEGY_ID,
            config_version=CONFIG_VERSION)
        owner.ledger.mark_handoff(row["intent_id"])
        owner.ledger.bind_native_order(row["intent_id"], "native-renewal-order")
        owner.ledger.outcome(row["intent_id"], "SUBMITTED", {"order_id": "native-renewal-order"})
        prior_version = owner.ledger.account_version()
        owner.apply_fill(action, fill_id="native-renewal-fill", quantity="0.01",
            price="100", fee="0.01", order_id="native-renewal-order")
        assert owner.ledger.account_version() == prior_version + 1
        assert not dispatcher._promotion_guard(), "an account fill must invalidate the exact receipt cut"
    finally:
        if dispatcher.task:
            dispatcher.task.cancel()
            await asyncio.gather(dispatcher.task, return_exceptions=True)
        await owner.close()
        await fixture["bybit_owner"].close()


@pytest.mark.asyncio
async def test_promotion_uses_post_read_clock_for_fresh_native_facts(tmp_path):
    fixture = build_native_group(tmp_path)
    owner = fixture["binance_owner"]
    event, _ = make_market_event_and_view()
    source_module.PLAN = []
    key = owner.ledger.scope.key
    runner = RealExecutionRunner(RealExecutionRunnerConfig("cap06-advancing-clock",
        owner.ledger.scope.account, "fixture", Decimal("1000000"), Decimal("1000000")),
        store=object(), bus=object(), settings=object(), owner=owner)
    configured = selection()
    configured.update(venue=owner.ledger.scope.venue, account=owner.ledger.scope.account,
        product=owner.ledger.scope.product)
    configured["registration"].update(module="strategies.cap06_source_fixture",
        accounts=[key], config_version=CONFIG_VERSION, required_feeds=[event.instrument.key])
    configured["registration"]["risk"] = {key: next(iter(configured["registration"]["risk"].values()))}
    configured["registration"]["config"]["config_version"] = CONFIG_VERSION
    source_facts = {"accounts": {key: {"scope": {"venue": "binance",
        "account": "acct-binance", "product": "linear_perpetual",
        "environment": "sandbox"}, "mode": "sandbox"}}}
    dispatcher = NativeSelectionDispatcher(parse_strategy_selections([configured])[0],
        NativeSelectionBinding(runner, owner, source_module.create_strategy(), fixture["registry"],
            readiness=fixture["readiness"][key],
            evidence_provider=lambda *_: {"config_version": CONFIG_VERSION,
                **fixture["evidence"][key]}, registration_facts=source_facts,
            route_id="binance-perp"))
    current = [NOW]
    owner.clock = lambda: current[0]
    prepare_scripted_reconcile(fixture)
    adapter = fixture["registry"].binding("binance-perp").adapter
    original_get = adapter.transport._get
    def advancing_get(url, params):
        result = original_get(url, params)
        current[0] += 100_000_000
        return result
    adapter.transport._get = advancing_get
    try:
        await dispatcher.prepare_promotion()
        assert current[0] > NOW
        assert dispatcher._promotion_receipt.observed_ns <= current[0]
        assert dispatcher._promotion_guard()
    finally:
        await owner.close()
        await fixture["bybit_owner"].close()


@pytest.mark.asyncio
async def test_completed_fill_stream_is_not_readded_while_funding_pages(tmp_path):
    fixture = build_native_group(tmp_path)
    owner = fixture["binance_owner"]
    action = replace(fixture["plan"].actions[0], group_id=None,
        expiry_ns=NOW + 10_000_000_000)
    _, row = owner.ledger.authorize(action.intent, evidence={"fixture": "mixed-page-completion"},
        expiry_ns=NOW + 10_000_000_000, epochs=owner.control.snapshot(STRATEGY_ID)["epochs"],
        reserve_unit=Decimal("100"), route_id="binance-perp", strategy_id=STRATEGY_ID,
        config_version=CONFIG_VERSION)
    owner.ledger.mark_handoff(row["intent_id"])
    owner.ledger.bind_native_order(row["intent_id"], "7001")
    owner.ledger.outcome(row["intent_id"], "SUBMITTED", {"order_id": "7001"})
    event, _ = make_market_event_and_view()
    key = owner.ledger.scope.key
    runner = RealExecutionRunner(RealExecutionRunnerConfig("cap06-mixed-pages",
        owner.ledger.scope.account, "fixture", Decimal("1000000"), Decimal("1000000")),
        store=object(), bus=object(), settings=object(), owner=owner)
    configured = selection()
    configured.update(venue=owner.ledger.scope.venue, account=owner.ledger.scope.account,
        product=owner.ledger.scope.product)
    configured["registration"].update(module="strategies.cap06_source_fixture",
        accounts=[key], config_version=CONFIG_VERSION, required_feeds=[event.instrument.key])
    configured["registration"]["risk"] = {key: next(iter(configured["registration"]["risk"].values()))}
    configured["registration"]["config"]["config_version"] = CONFIG_VERSION
    source_facts = {"accounts": {key: {"scope": {"venue": "binance",
        "account": "acct-binance", "product": "linear_perpetual",
        "environment": "sandbox"}, "mode": "sandbox"}}}
    dispatcher = NativeSelectionDispatcher(parse_strategy_selections([configured])[0],
        NativeSelectionBinding(runner, owner, source_module.create_strategy(), fixture["registry"],
            readiness=fixture["readiness"][key],
            evidence_provider=lambda *_: {"config_version": CONFIG_VERSION,
                **fixture["evidence"][key]}, registration_facts=source_facts,
            route_id="binance-perp"))
    prepare_scripted_reconcile(fixture)
    adapter = fixture["registry"].binding("binance-perp").adapter
    adapter.max_pages = 4
    original_get = adapter.transport._get
    fill = {"symbol": "BTCUSDT", "id": 1, "orderId": 7001,
        "side": "BUY", "qty": "0.01", "price": "100", "commission": "0.01",
        "commissionAsset": "USDT", "time": 1000, "maker": False}
    funding = [{"symbol": "BTCUSDT", "income": "0", "asset": "USDT",
        "time": 1000, "tranId": index} for index in range(1000)]
    def paginated_get(url, params):
        if "/fapi/v3/positionRisk" in url:
            return __import__("execution.perp_execution", fromlist=["HttpResponse"]).HttpResponse(
                200, [{"symbol": "BTCUSDT", "positionAmt": "0.01", "entryPrice": "100",
                    "positionSide": "BOTH", "marginType": "cross"}])
        if "/fapi/v1/openOrders" in url:
            return __import__("execution.perp_execution", fromlist=["HttpResponse"]).HttpResponse(
                200, [{"symbol": "BTCUSDT", "orderId": 7001,
                    "clientOrderId": action.intent.client_order_id, "status": "PARTIALLY_FILLED",
                    "origQty": "0.1", "executedQty": "0.01"}])
        if "/fapi/v1/userTrades" in url:
            return __import__("execution.perp_execution", fromlist=["HttpResponse"]).HttpResponse(200, [fill])
        if "/fapi/v1/income" in url:
            return __import__("execution.perp_execution", fromlist=["HttpResponse"]).HttpResponse(
                200, funding if params.get("page") == 1 else [])
        return original_get(url, params)
    adapter.transport._get = paginated_get
    try:
        await dispatcher.prepare_promotion()
        assert owner.ledger.get(row["intent_id"])["filled"] == "0.01"
        assert owner.ledger.position(action.intent.instrument.key) == Decimal("0.01")
        assert dispatcher._promotion_guard()
    finally:
        await owner.close()
        await fixture["bybit_owner"].close()


@pytest.mark.asyncio
async def test_selected_group_uses_existing_coordinator_and_exact_per_owner_receipts(tmp_path):
    from test_cap05_groups import coordinator_for
    fixture = build_native_group(tmp_path)
    primary = fixture["binance_owner"]
    key_a, key_b = fixture["binance_scope"].key, fixture["bybit_scope"].key
    event, view = make_market_event_and_view()
    source_module.PLAN = fixture["plan"]
    source = source_module.create_strategy()
    facts = {"accounts": {
        key_a: {"scope": {"venue": "binance", "account": "acct-binance",
            "product": "linear_perpetual", "environment": "sandbox"}, "mode": "sandbox"},
        key_b: {"scope": {"venue": "bybit", "account": "acct-bybit",
            "product": "linear_perpetual", "environment": "sandbox"}, "mode": "sandbox"}}}
    runner = RealExecutionRunner(RealExecutionRunnerConfig("cap06-group",
        primary.ledger.scope.account, "fixture", Decimal("1000000"), Decimal("1000000")),
        store=object(), bus=object(), settings=object(), owner=primary,
        group_coordinator=coordinator_for(fixture))
    configured = selection()
    configured.update(venue="binance", account="acct-binance", product="linear_perpetual")
    configured["registration"].update(module="strategies.cap06_source_fixture",
        accounts=[key_a, key_b], config_version=CONFIG_VERSION,
        required_feeds=[event.instrument.key])
    risk = next(iter(configured["registration"]["risk"].values()))
    configured["registration"]["risk"] = {key_a: risk, key_b: risk}
    configured["registration"]["config"]["config_version"] = CONFIG_VERSION
    selected = parse_strategy_selections([configured])[0]
    binding = NativeSelectionBinding(runner, primary, source, fixture["registry"],
        readiness=fixture["readiness"],
        evidence_provider={key: lambda _result, _snapshot, key=key:
            {"config_version": CONFIG_VERSION, **fixture["evidence"][key]}
            for key in (key_a, key_b)}, registration_facts=facts, route_id="binance-perp")
    dispatcher = NativeSelectionDispatcher(selected, binding)
    prepare_scripted_reconcile(fixture, "binance-perp")
    prepare_scripted_reconcile(fixture, "bybit-perp")
    try:
        await dispatcher.prepare_promotion()
        assert set(dispatcher._scope_dispatchers) == {key_a, key_b}
        dispatcher.task = asyncio.create_task(dispatcher.run())
        snapshot = replace(view.hot_path, phase="LIVE",
            quality=MappingProxyType({event.instrument.key: "VALID"}))
        dispatcher.enqueue_applied(ApplyResult(event, True, False, "",
            snapshot.engine_incarnation, snapshot.apply_seq), snapshot)
        await asyncio.wait_for(dispatcher.queue.join(), timeout=3)
        assert not dispatcher.errors, dispatcher.errors
        assert dispatcher.results[0]["state"] == "ACKNOWLEDGED", str(dispatcher.results[0])[:1600]
        assert len(fixture["binance_transport"].calls) == 1
        assert len(fixture["bybit_transport"].calls) == 1
    finally:
        if dispatcher.task:
            dispatcher.task.cancel()
            await asyncio.gather(dispatcher.task, return_exceptions=True)
        await primary.close()
        await fixture["bybit_owner"].close()


def test_native_restart_fill_reuses_exact_owner_receipt(tmp_path):
    fixture = build_native_group(tmp_path)
    owner = fixture["binance_owner"]
    action = fixture["plan"].actions[0]
    intent = action.intent
    try:
        _, row = owner.ledger.authorize(intent, evidence={"fixture": "matched-restart"},
            expiry_ns=NOW + 10_000_000_000, epochs={}, reserve_unit=Decimal("100"),
            route_id="binance-perp", strategy_id=STRATEGY_ID,
            config_version=CONFIG_VERSION)
        owner.ledger.mark_handoff(row["intent_id"])
        owner.ledger.bind_native_order(row["intent_id"], "native-order-1")
        owner.ledger.outcome(row["intent_id"], "SUBMITTED", {"order_id": "native-order-1"})
        current = owner.ledger.get(row["intent_id"])
        owned = {"native-order-1": current, intent.client_order_id: current}
        dispatcher = object.__new__(NativeSelectionDispatcher)
        dispatcher.binding = SimpleNamespace(owner=owner)
        native_fill = {"event_id": "BTCUSDT:fill-1", "order_id": "native-order-1",
            "symbol": "BTCUSDT", "quantity": "0.04", "price": "100", "fee": "0.01",
            "exchange_ns": NOW}
        dispatcher._apply_reconciled_fills([native_fill], "BTCUSDT", owned)
        applied_row = owner.ledger.get(row["intent_id"])
        assert applied_row["filled"] == "0.04"
        version = owner.ledger.account_version()
        dispatcher._apply_reconciled_fills([native_fill], "BTCUSDT", owned)
        assert owner.ledger.get(row["intent_id"])["filled"] == "0.04"
        assert owner.ledger.account_version() == version
    finally:
        import asyncio
        asyncio.run(owner.close())
        asyncio.run(fixture["bybit_owner"].close())


@pytest.mark.asyncio
async def test_native_promotion_keeps_unknown_intent_blocked(tmp_path):
    fixture = build_native_group(tmp_path)
    owner = fixture["binance_owner"]
    action = replace(fixture["plan"].actions[0], group_id=None)
    key = owner.ledger.scope.key
    runner = RealExecutionRunner(RealExecutionRunnerConfig("cap06-unknown",
        owner.ledger.scope.account, "fixture", Decimal("1000000"), Decimal("1000000")),
        store=object(), bus=object(), settings=object(), owner=owner)
    event, _ = make_market_event_and_view()
    configured = selection()
    configured.update(venue=owner.ledger.scope.venue, account=owner.ledger.scope.account,
        product=owner.ledger.scope.product)
    configured["registration"].update(module="strategies.cap06_source_fixture",
        accounts=[key], config_version=CONFIG_VERSION, required_feeds=[event.instrument.key])
    configured["registration"]["risk"] = {key: next(iter(configured["registration"]["risk"].values()))}
    configured["registration"]["config"]["config_version"] = CONFIG_VERSION
    selected = parse_strategy_selections([configured])[0]
    source_module.PLAN = StrategyActionPlan("cap06-unknown-plan", STRATEGY_ID,
        CONFIG_VERSION, NOW, (action,))
    binding = NativeSelectionBinding(runner, owner, source_module.create_strategy(),
        fixture["registry"], fixture["readiness"][key],
        lambda *_: {"config_version": CONFIG_VERSION, **fixture["evidence"][key]},
        {"accounts": {key: {"scope": {"venue": owner.ledger.scope.venue,
            "account": owner.ledger.scope.account, "product": owner.ledger.scope.product,
            "environment": "sandbox"}, "mode": "sandbox"}}}, route_id="binance-perp")
    dispatcher = NativeSelectionDispatcher(selected, binding)
    try:
        _, row = owner.ledger.authorize(action.intent, evidence={"fixture": "unknown"},
            expiry_ns=NOW + 10_000_000_000, epochs={}, reserve_unit=Decimal("100"),
            route_id="binance-perp", strategy_id=STRATEGY_ID,
            config_version=CONFIG_VERSION)
        owner.ledger.mark_handoff(row["intent_id"])
        owner.ledger.outcome(row["intent_id"], "UNKNOWN", {"fixture": True})
        prepare_scripted_reconcile(fixture)
        with pytest.raises(RuntimeError, match="UNKNOWN"):
            await dispatcher.prepare_promotion()
    finally:
        await owner.close()
        await fixture["bybit_owner"].close()


@pytest.mark.asyncio
async def test_selected_spot_promotes_after_exact_order_coverage_and_empty_inventory(tmp_path):
    from test_exec01_native import register_spot
    from execution.venues.registry import AliasBinding
    owner, registry, adapter, transport, scope = register_spot(tmp_path)
    current = [NOW]
    owner.clock = lambda: current[0]
    original_request = transport.request
    async def scripted_read(method, url, **kwargs):
        if "/api/v3/account?" in url:
            from execution.perp_execution import HttpResponse
            transport.calls.append((method, url))
            current[0] += 100_000_000
            return HttpResponse(200, {"canTrade": True, "balances": [], "accountType": "SPOT"})
        if "/api/v3/openOrders?" in url:
            from execution.perp_execution import HttpResponse
            transport.calls.append((method, url))
            current[0] += 100_000_000
            return HttpResponse(200, [])
        if "/api/v3/myTrades?" in url:
            from execution.perp_execution import HttpResponse
            transport.calls.append((method, url))
            current[0] += 100_000_000
            return HttpResponse(200, [])
        return await original_request(method, url, **kwargs)
    transport.request = scripted_read
    route_binding = registry.binding("binance-spot")
    original_facts = route_binding.native_facts_provider
    account_raw = {"canTrade": True, "balances": [], "accountType": "SPOT"}
    def exact_spot_facts(symbol):
        value = original_facts(symbol)
        value["account_facts"]["normalized"]["raw"] = account_raw
        value["account_facts"]["physical_positions"] = {}
        value["account_facts"]["observed_ns"] = owner.clock()
        value["max_age_ns"] = 20_000_000_000
        return value
    object.__setattr__(route_binding, "native_facts_provider", exact_spot_facts)
    registry.register_aliases((AliasBinding(STRATEGY_ID, "cap06-spot", "binance-spot",
        "BTCUSDT", "spot", "spot", "sandbox", scope),))
    runner = RealExecutionRunner(RealExecutionRunnerConfig("cap06-spot", scope.account,
        "fixture", Decimal("100000"), Decimal("100000")), store=object(), bus=object(),
        settings=SimpleNamespace(), owner=owner)
    event, _ = make_market_event_and_view()
    configured = selection("TESTNET", "sandbox")
    configured.update(venue=scope.venue, account=scope.account, product=scope.product)
    configured["registration"].update(module="strategies.cap06_source_fixture",
        config_version=CONFIG_VERSION, accounts=[scope.key], required_feeds=[event.instrument.key])
    configured["registration"]["risk"] = {scope.key: next(iter(configured["registration"]["risk"].values()))}
    configured["registration"]["config"]["config_version"] = CONFIG_VERSION
    selected = parse_strategy_selections([configured])[0]
    facts = {"accounts": {scope.key: {"scope": {"venue": scope.venue,
        "account": scope.account, "product": scope.product, "environment": "sandbox"},
        "mode": "sandbox"}}}
    binding = NativeSelectionBinding(runner, owner, source_module.create_strategy(), registry,
        readiness=lambda: {}, evidence_provider=lambda *_: {}, registration_facts=facts,
        route_id="binance-spot")
    dispatcher = NativeSelectionDispatcher(selected, binding)
    try:
        await dispatcher.prepare_promotion()
        assert current[0] > NOW
        assert dispatcher._promotion_guard()
        assert any("/api/v3/account?" in url for _, url in transport.calls)
        assert any("/api/v3/openOrders?" in url for _, url in transport.calls)
        assert any("/api/v3/myTrades?" in url for _, url in transport.calls)
        assert not any(method == "POST" for method, _ in transport.calls)
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_selected_spot_matches_recent_fills_to_exact_durable_order_query(tmp_path):
    from test_exec01_native import register_spot, intent as make_spot_intent
    from execution.trading_contracts import TradingMode
    from core.models import AssetClass, Venue
    from execution.perp_execution import HttpResponse
    from execution.venues.registry import AliasBinding
    owner, registry, adapter, transport, scope = register_spot(tmp_path)
    order = make_spot_intent("cap06-spot-fill", venue=Venue.BINANCE,
        asset=AssetClass.CRYPTO_SPOT, symbol="BTCUSDT", account=scope.account,
        environment="sandbox", quantity="0.1")
    _, row = owner.ledger.authorize(order, evidence={"fixture": "exact-spot-order"},
        expiry_ns=NOW + 10_000_000_000, epochs={}, reserve_unit=Decimal("10"),
        route_id="binance-spot", strategy_id=STRATEGY_ID, config_version=CONFIG_VERSION)
    owner.ledger.mark_handoff(row["intent_id"])
    owner.ledger.bind_native_order(row["intent_id"], "222")
    owner.ledger.outcome(row["intent_id"], "SUBMITTED", {"order_id": "222"})
    original_request = transport.request
    account_raw = {"canTrade": True, "balances": [{"asset": "BTC", "free": "0.1",
        "locked": "0"}, {"asset": "USDT", "free": "1000", "locked": "0"}],
        "accountType": "SPOT"}
    async def scripted_read(method, url, **kwargs):
        transport.calls.append((method, url))
        if "/api/v3/account?" in url:
            return HttpResponse(200, account_raw)
        if "/api/v3/openOrders?" in url:
            return HttpResponse(200, [])
        if "/api/v3/myTrades?" in url:
            return HttpResponse(200, [{"symbol": "BTCUSDT", "id": 1, "orderId": 222,
                "qty": "0.1", "price": "100", "quoteQty": "10", "commission": "0.01",
                "commissionAsset": "USDT", "time": 1000, "isBuyer": True}])
        if "/api/v3/order?" in url:
            return HttpResponse(200, {"symbol": "BTCUSDT", "orderId": 222,
                "clientOrderId": order.client_order_id, "status": "FILLED",
                "executedQty": "0.1", "origQty": "0.1"})
        return await original_request(method, url, **kwargs)
    transport.request = scripted_read
    binding_route = registry.binding("binance-spot")
    original_facts = binding_route.native_facts_provider
    def exact_spot_facts(symbol):
        value = original_facts(symbol)
        value["account_facts"]["normalized"]["raw"] = account_raw
        value["account_facts"]["physical_positions"] = {symbol: "0.1"}
        return value
    object.__setattr__(binding_route, "native_facts_provider", exact_spot_facts)
    registry.register_aliases((AliasBinding(STRATEGY_ID, "cap06-spot-fill", "binance-spot",
        "BTCUSDT", "spot", "spot", "sandbox", scope),))
    runner = RealExecutionRunner(RealExecutionRunnerConfig("cap06-spot-fill", scope.account,
        "fixture", Decimal("100000"), Decimal("100000")), store=object(), bus=object(),
        settings=SimpleNamespace(), owner=owner)
    event, _ = make_market_event_and_view()
    configured = selection("TESTNET", "sandbox")
    configured.update(venue=scope.venue, account=scope.account, product=scope.product)
    configured["registration"].update(module="strategies.cap06_source_fixture",
        config_version=CONFIG_VERSION, accounts=[scope.key], required_feeds=[event.instrument.key])
    configured["registration"]["risk"] = {scope.key: next(iter(configured["registration"]["risk"].values()))}
    configured["registration"]["config"]["config_version"] = CONFIG_VERSION
    source_facts = {"accounts": {scope.key: {"scope": {"venue": scope.venue,
        "account": scope.account, "product": scope.product, "environment": "sandbox"},
        "mode": "sandbox"}}}
    dispatcher = NativeSelectionDispatcher(parse_strategy_selections([configured])[0],
        NativeSelectionBinding(runner, owner, source_module.create_strategy(), registry,
            readiness=lambda: {}, evidence_provider=lambda *_: {},
            registration_facts=source_facts, route_id="binance-spot"))
    try:
        await dispatcher.prepare_promotion()
        assert owner.ledger.get(order.intent_id)["filled"] == "0.1"
        assert owner.ledger.position(order.instrument.key) == Decimal("0.1")
        assert dispatcher._promotion_guard()
        assert any("/api/v3/order?" in url for _, url in transport.calls)
        assert not any(method == "POST" for method, _ in transport.calls)
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_selected_kiwoom_cash_reconciles_complete_fee_free_daily_pages(tmp_path):
    from datetime import datetime, timedelta
    from execution.brokers import KiwoomBrokerAdapter
    from execution.execution_ledger import AccountScope
    from execution.perp_execution import HttpResponse
    from execution.trading_contracts import LiveExecutionAuthorization
    from execution.venues.registry import AliasBinding, Capabilities, RouteSpec, VenueRegistry
    from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
    from cap05_source_fixture import make_owner, open_gates, NOW
    import time
    cash_now = time.time_ns()

    native_id, symbol, route_id = "1234567890", "005930", "cap06-kiwoom-live"
    scope = AccountScope("kiwoom", "acct-kiwoom-live", "cash", "live")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    expiry = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d%H%M%S")
    (state_dir / "kiwoom_token_real.json").write_text(
        '{"token":"scripted-only","expires_dt":"' + expiry + '"}', encoding="utf-8")
    settings = SimpleNamespace(providers={"kiwoom": SimpleNamespace(values={
        "KIWOOM_APP_KEY": "scripted-only", "KIWOOM_APP_SECRET": "scripted-only",
        "KIWOOM_USE_MOCK": "0"})}, state_dir=state_dir, live_order_enabled=True,
        env={"LIVE_ORDER_ARMED_" + scope.account.upper(): "1"})
    context = {"venue": "kiwoom", "product": "cash", "environment": "live",
        "account_alias": "scripted-account", "account_identity_verified": True,
        "account_id": scope.account, "strategy_id": STRATEGY_ID,
        "native_account_id": native_id}

    class CashReadTransport:
        mutation_permit_protocol = 1
        def __init__(self): self.calls = []
        async def request(self, method, url, *, params=None, json_body=None, data=None,
                          headers=None, before_send=None):
            api = headers["api-id"]
            self.calls.append(api)
            if before_send is not None: before_send()
            if api == "ka00001": body = {"acctNo": native_id, "return_code": 0}
            elif api == "kt00018": body = {"acnt_evlt_remn_indv_tot": [{
                "stk_cd": "A" + symbol, "rmnd_qty": "10", "trde_able_qty": "10"}],
                "total_estimated_assets": "1000000", "return_code": 0}
            elif api == "ka10075": body = {"oso": [], "return_code": 0}
            elif api == "kt00009": body = {"acnt_ord_cntr_prst_array": [{
                "ord_no": "7654321", "stk_cd": "A" + symbol,
                "io_tp_nm": "현금매수", "mdfy_cncl_tp": "0", "cntr_no": "0000001",
                "cntr_qty": "10", "cntr_uv": "70000", "cntr_tm": "100000"}],
                "return_code": 0}
            else: raise AssertionError("unexpected scripted native operation " + api)
            return HttpResponse(200, body, {"cont-yn": "N", "next-key": ""})

    transport = CashReadTransport()
    adapter = KiwoomBrokerAdapter(settings, transport=transport, account_context=context)
    route = RouteSpec(route_id, "kiwoom", "krx_cash", "cash", "live", scope, native_id)
    registry = VenueRegistry(max_routes=1, max_aliases=1, max_fact_bytes=8192)
    caps = Capabilities({"cash"}, {"live"}, {"GTC"}, False, False, False)
    account_snapshot = {"provider": "kiwoom", "mock": False,
        "account_identity": {"native_account_id": native_id, "alias": "scripted-account",
        "identity_proven": True},
        "balances": {"total_estimated_assets": "1000000", "return_code": 0},
        "positions": [{"stk_cd": "A" + symbol, "rmnd_qty": "10", "trde_able_qty": "10"}],
        "complete": True}
    product = {"scope_key": scope.key, "venue": scope.venue, "product": scope.product,
        "environment": scope.environment, "symbol": symbol, "kind": "cash",
        "quote_currency": "KRW", "settlement_currency": "KRW",
        "collateral_currency": "KRW", "raw": {"scripted": True}}
    native_facts = {"account_facts": {"normalized": {"scope_key": scope.key,
        "venue": scope.venue, "product": scope.product, "environment": scope.environment,
        "raw": account_snapshot, "currency": "KRW", "equity": "1000000",
        "available": "1000000"}, "observed_ns": cash_now, "account_version": cash_now,
        "reconciliation_state": "RECONCILED", "unknown": False,
        "native_identity": {"native_account_id": native_id, "scope_key": scope.key,
            "source": "authenticated_native_session", "permissions": ("account_read", "trade")},
        "physical_positions": {symbol: "10"}}, "product_specs": {symbol: product},
        "taker_fee_rate": "0.001", "max_age_ns": 5_000_000_000}
    registry.bind_existing_adapter(route, adapter, reference_provider=lambda: {},
        capabilities=caps, native_facts_provider=lambda requested: native_facts)
    registry.register_aliases((AliasBinding(STRATEGY_ID, "kiwoom-cash", route_id,
        symbol, "krx_cash", "cash", "live", scope),))
    owner = make_owner(tmp_path / "owner", scope, native_id)
    owner.clock = lambda: cash_now
    open_gates(owner)
    intent = OrderIntent(intent_id="cap06-kiwoom-order", job_id="cap06-kiwoom",
        signal_id="cap06-kiwoom-signal", destination=OrderDestination.REAL,
        client_order_id="cap06-kiwoom-client",
        instrument=InstrumentId(venue=Venue.KIWOOM, asset_class=AssetClass.EQUITY,
            raw_symbol=symbol, std_symbol=symbol, currency="KRW"),
        side=Side.BUY, quantity=Decimal("10"), limit_price=Decimal("70000"),
        metadata={"account_id": scope.account, "environment": "live", "strategy_id": STRATEGY_ID})
    _, durable = owner.ledger.authorize(intent, evidence={"fixture": "cash-restart"},
        expiry_ns=cash_now + 10_000_000_000,
        epochs=owner.control.snapshot(STRATEGY_ID)["epochs"], reserve_unit=Decimal("70000"),
        route_id=route_id, strategy_id=STRATEGY_ID, config_version=CONFIG_VERSION)
    owner.ledger.mark_handoff(durable["intent_id"])
    owner.ledger.bind_native_order(durable["intent_id"], "7654321")
    owner.ledger.outcome(durable["intent_id"], "SUBMITTED", {"order_id": "7654321"})
    owner.live_authorization = LiveExecutionAuthorization(STRATEGY_ID, CONFIG_VERSION,
        scope.key, native_id, "scripted-human-approval", cash_now - 1, cash_now + 10_000_000_000)
    runner = RealExecutionRunner(RealExecutionRunnerConfig("cap06-kiwoom", scope.account,
        "fixture", Decimal("1000000"), Decimal("1000000")), store=object(), bus=object(),
        settings=settings, owner=owner)
    event, _ = make_market_event_and_view()
    configured = selection("LIVE", "live")
    configured.update(venue=scope.venue, account=scope.account, product=scope.product)
    configured["registration"].update(module="strategies.cap06_source_fixture",
        config_version=CONFIG_VERSION, accounts=[scope.key], required_feeds=[event.instrument.key])
    configured["registration"]["risk"] = {scope.key: next(iter(configured["registration"]["risk"].values()))}
    configured["registration"]["config"]["config_version"] = CONFIG_VERSION
    selected = parse_strategy_selections([configured])[0]
    registration_facts = {"accounts": {scope.key: {"scope": {"venue": scope.venue,
        "account": scope.account, "product": scope.product, "environment": "live"}, "mode": "live"}}}
    binding = NativeSelectionBinding(runner, owner, source_module.create_strategy(), registry,
        readiness=lambda: {}, evidence_provider=lambda *_: {},
        registration_facts=registration_facts, route_id=route_id)
    dispatcher = NativeSelectionDispatcher(selected, binding)
    try:
        await dispatcher.prepare_promotion()
        assert dispatcher._promotion_guard()
        assert transport.calls == ["ka00001", "kt00018", "ka10075", "kt00009"]
        assert owner.ledger.get(durable["intent_id"])["filled"] == "10"
        assert Decimal(owner.ledger.get(durable["intent_id"])["fees"]) == Decimal("700")
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_apply_callback_only_enqueues_and_overflow_fences_selected_owner(tmp_path):
    fixture = build_native_group(tmp_path)
    try:
        owner = fixture["binance_owner"]
        source_module.PLAN = fixture["plan"]
        source = source_module.create_strategy()
        key = fixture["binance_scope"].key
        runner = RealExecutionRunner(RealExecutionRunnerConfig("job", owner.ledger.scope.account,
            "fixture", Decimal("1000000"), Decimal("1000000")), store=object(), bus=object(),
            settings=object(), owner=owner)
        configured = selection()
        configured.update(venue=owner.ledger.scope.venue, account=owner.ledger.scope.account,
            product=owner.ledger.scope.product)
        configured["registration"].update(module="strategies.cap06_source_fixture",
            accounts=[key], config_version=CONFIG_VERSION)
        configured["registration"]["risk"] = {key: next(iter(configured["registration"]["risk"].values()))}
        configured["registration"]["config"]["config_version"] = CONFIG_VERSION
        event, view = make_market_event_and_view()
        configured["registration"]["required_feeds"] = [event.instrument.key]
        configured["queue"]["max_items"] = 1
        route = parse_strategy_selections([configured])[0]
        binding = NativeSelectionBinding(runner, owner, source, fixture["registry"],
            fixture["readiness"][key], lambda *_: {"config_version": CONFIG_VERSION,
                **fixture["evidence"][key]}, {"accounts": {key: {"scope": {
                    "venue": owner.ledger.scope.venue, "account": owner.ledger.scope.account,
                    "product": owner.ledger.scope.product, "environment": "sandbox"}, "mode": "sandbox"}}},
            route_id="binance-perp")
        dispatcher = NativeSelectionDispatcher(route, binding)
        prepare_scripted_reconcile(fixture)
        await dispatcher.prepare_promotion()
        snapshot = replace(view.hot_path, phase="LIVE",
            quality=MappingProxyType({event.instrument.key: "VALID"}))
        applied = ApplyResult(event, True, False, "", snapshot.engine_incarnation, snapshot.apply_seq)
        dispatcher.enqueue_applied(applied, snapshot)
        dispatcher.enqueue_applied(applied, snapshot)
        assert dispatcher.blocked is True
        assert owner.control.snapshot(STRATEGY_ID)["closed"]["strategy:" + STRATEGY_ID] is True
        assert fixture["binance_transport"].calls == []
    finally:
        await fixture["binance_owner"].close()
        await fixture["bybit_owner"].close()


@pytest.mark.asyncio
async def test_scoped_live_authorization_and_native_reconciliation_reach_scripted_owner(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace
    from core.models import Side, Venue
    from execution.execution_ledger import AccountScope
    from execution.perp_execution import BinancePerpAdapter, PerpScope
    from execution.trading_contracts import (LiveExecutionAuthorization, TradingMode)
    from execution.venues.registry import AliasBinding, Capabilities, RouteSpec, VenueRegistry
    from cap05_source_fixture import (NOW, ScriptedPerpTransport, _perp_native_facts, evidence,
        make_action, make_intent, make_owner, open_gates, readiness, ZERO_FACTS)

    scope = AccountScope("binance", "acct-live", "linear_perpetual", "live")
    native_id = "native-binance-live-scripted"
    transport = ScriptedPerpTransport("binance")
    adapter = BinancePerpAdapter(PerpScope("binance", "linear_perpetual", "live",
        scope.account, "USDT"), transport, api_key="scripted-key", api_secret="scripted-secret",
        clock_ns=lambda: NOW)
    adapter.base = "https://fapi.binance.com"
    adapter._metadata["BTCUSDT"] = {"lot_size": "0.001", "tick_size": "0.1",
        "min_quantity": "0.001", "min_notional": "5", "multiplier": "1"}
    route = RouteSpec("live-btc", "binance", "usdm_perp", "linear_perpetual", "live", scope, native_id)
    registry = VenueRegistry(max_routes=1, max_aliases=1, max_fact_bytes=4096)
    caps = Capabilities({"linear_perpetual"}, {"live"}, {"GTC", "IOC", "FOK", "POST_ONLY"},
        True, True, False)
    registry.bind_existing_adapter(route, adapter, reference_provider=lambda: dict(ZERO_FACTS),
        capabilities=caps, native_facts_provider=lambda symbol: _perp_native_facts(adapter, native_id, symbol))
    registry.register_aliases((AliasBinding(STRATEGY_ID, "live-btc", "live-btc", "BTCUSDT",
        "usdm_perp", "linear_perpetual", "live", scope),))
    owner = make_owner(tmp_path / "live-owner", scope, native_id)
    open_gates(owner)
    sandbox_scope = AccountScope("binance", scope.account, "linear_perpetual", "sandbox")
    original = make_action("live", sandbox_scope, make_intent("live", venue=Venue.BINANCE,
        account=scope.account, side=Side.BUY, quantity="0.1"))
    intent = original.intent.model_copy(update={"metadata": {
        **original.intent.metadata, "account_id": scope.account, "environment": "live"}})
    action = replace(original, scope=scope, mode=TradingMode.LIVE, intent=intent, group_id=None)
    plan = StrategyActionPlan("cap06-live", STRATEGY_ID, CONFIG_VERSION, NOW, (action,))
    source_module.PLAN = plan
    source = source_module.create_strategy()
    auth = LiveExecutionAuthorization(STRATEGY_ID, CONFIG_VERSION, scope.key, native_id,
        "scripted-approval", NOW - 1, NOW + 5_000_000_000)
    owner.live_authorization = auth
    settings = SimpleNamespace(live_order_enabled=True,
        env={"LIVE_ORDER_ARMED_" + scope.account.upper(): "1"})
    runner = RealExecutionRunner(RealExecutionRunnerConfig("cap06-live", scope.account, "fixture",
        Decimal("1000000"), Decimal("1000000")), store=object(), bus=object(), settings=settings, owner=owner)
    instrument_key = action.intent.instrument.key
    selection_doc = selection("LIVE", "live")
    selection_doc.update(venue=scope.venue, account=scope.account, product=scope.product)
    registration = selection_doc["registration"]
    registration.update(module="strategies.cap06_source_fixture", config_version=CONFIG_VERSION,
        accounts=[scope.key], required_feeds=[instrument_key])
    registration["risk"] = {scope.key: next(iter(registration["risk"].values()))}
    registration["config"]["config_version"] = CONFIG_VERSION
    selected = parse_strategy_selections([selection_doc])[0]
    facts = {"accounts": {scope.key: {"scope": {"venue": scope.venue,
        "account": scope.account, "product": scope.product, "environment": scope.environment},
        "mode": "live"}}}
    bind = NativeSelectionBinding(runner, owner, source, registry,
        readiness=readiness(owner, action.intent),
        evidence_provider=lambda *_: {"config_version": CONFIG_VERSION, **evidence(owner, "live")},
        registration_facts=facts, route_id="live-btc")
    dispatcher = NativeSelectionDispatcher(selected, bind)
    prepare_scripted_reconcile({"registry": registry}, "live-btc")
    await dispatcher.prepare_promotion()
    dispatcher.task = asyncio.create_task(dispatcher.run())
    event, data = make_market_event_and_view()
    snapshot = replace(data.hot_path, phase="LIVE",
        quality=MappingProxyType({event.instrument.key: "VALID"}))
    applied = ApplyResult(event, True, False, "", snapshot.engine_incarnation, snapshot.apply_seq)
    try:
        dispatcher.enqueue_applied(applied, snapshot)
        await asyncio.wait_for(dispatcher.queue.join(), timeout=3)
        assert not dispatcher.errors, dispatcher.errors
        assert dispatcher.results[0]["results"][0]["status"] == "SUBMITTED"
        assert len(transport.calls) == 1
        # A changed arm/authorization is checked again at the dispatch boundary.
        settings.env["LIVE_ORDER_ARMED_" + scope.account.upper()] = "0"
        assert not dispatcher._promotion_guard()
    finally:
        dispatcher.task.cancel()
        await asyncio.gather(dispatcher.task, return_exceptions=True)
        await owner.close()


def test_selected_paper_strategy_uses_existing_configured_matcher(tmp_path):
    from test_strategy_runtime import T, budget, book, ledger_at, matcher_for, spec
    from market.hot_path import HotPathStore
    from runtime.strategy_runtime import PaperStrategyRuntime
    from execution.trading_contracts import TradingMode
    from core.models import AssetClass, InstrumentId, Venue

    instrument = InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_PERP,
        raw_symbol="BTCUSDT", std_symbol="BTCUSDT", currency="USDT")
    ledger = ledger_at(tmp_path, instrument)
    ledger.seed_balance("USDT", Decimal("100000"), {"source": "cap06-scripted"})
    matcher = matcher_for(ledger)
    host = PaperStrategyRuntime(hot_path=HotPathStore(), accounts={ledger.scope.key: matcher},
        products={instrument.key: spec(instrument)}, known_instruments={instrument.key: instrument}, reference_feeds={},
        budgets=budget(), settings={"fixture": "scripted"}, mode=TradingMode.PAPER,
        phase="paper", now_ns=lambda: T)
    risk = {ledger.scope.key: {"funding_buffer": "0", "account_notional_limit": "100000",
        "strategy_notional_limit": "100000", "account_capital_limit": "100000",
        "strategy_capital_limit": "100000", "margin_rate": "0.1"}}
    registration = {"job_id": "cap06-paper", "module": "strategies.crypto.example_order_policy",
        "strategy_version": "example-v1", "config_version": "cap06-paper-v1",
        "config": {"account_key": ledger.scope.key, "config_version": "cap06-paper-v1",
            "quantity": "1", "maker_offset": "0"},
        "research_receipt": {"research_id": "cap06-scripted"}, "paper_permission": True,
        "required_feeds": [instrument.key], "warmup_bars": 0,
        "accounts": [ledger.scope.key], "risk": risk}
    entry = {"mode": "PAPER", "venue": ledger.scope.venue,
        "account": ledger.scope.account, "product": ledger.scope.product,
        "environment": "paper", "queue": {"max_items": 1, "max_bytes": 65536,
            "max_age_ns": 1_000_000_000, "snapshot_charge_bytes": 32768}, "registration": registration}
    parsed = parse_strategy_selections([entry])
    plan = SimpleNamespace(strategy_selections=parsed)
    # StrategyActionPlan supplies its own typed actions; the legacy Signal
    # builder remains required by the existing host registration contract.
    activate_paper_selections(plan, host, {"cap06-paper": lambda *_: []})
    host.process_event(book(instrument=instrument))
    try:
        assert host._jobs["example_crypto_order_policy"].registration.config_version == "cap06-paper-v1"
        assert ledger.active_orders()
        assert matcher.ledger is ledger
    finally:
        ledger.close()
