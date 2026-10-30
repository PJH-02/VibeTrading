"""EXEC-01 native route proofs using existing canonical adapters only.

Every adapter below is an existing product class invoked through the one
common ExecutionOwner/registry seam.  Transports are scripted and credential
free; no live account or network is touched.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
from execution.execution_ledger import AccountScope, ExecutionLedger
from execution.execution_owner import ExecutionBudgets, ExecutionOwner, ExecutionReadiness
from execution.perp_execution import BinancePerpAdapter, HttpResponse, PerpScope
from execution.trading_contracts import (
    ActionKind, ExecutionAction, StrategyActionPlan, TradingMode,
)
from execution.venues.registry import (
    AliasBinding, Capabilities, MANDATORY_REMOVAL_FACTS, RouteSpec, VenueRegistry,
)
from runtime.runtime_control import RuntimeControl

ZERO_FACTS = {key: 0 for key in MANDATORY_REMOVAL_FACTS}
NOW = 1_000_000_000


def make_owner(tmp_path, scope, native_account_id=None):
    ledger = ExecutionLedger(tmp_path / "orders.sqlite3", scope, max_intents=32,
                             max_evidence_bytes=32768, max_fills=64,
                             authority_root=tmp_path / "authority",
                             native_account_id=native_account_id)
    control = RuntimeControl(ledger)
    budgets = ExecutionBudgets(Decimal("10000000"), Decimal("100000000"), 8,
                               10_000_000_000, 1.0, 1.0)
    owner = ExecutionOwner(ledger, control, object(), budgets, clock=lambda: NOW)
    return owner


def readiness(owner, scope, instrument_key):
    return lambda: ExecutionReadiness(
        scope.key, True, True, True, NOW + 100_000_000,
        Decimal("0"), Decimal("1000000"),
        account_version=owner.ledger.account_version(),
        allowed_instruments=(instrument_key,),
        market_price_caps=((instrument_key, Decimal("100000")),))


def open_gates(owner, strategy):
    for gate in ("global", "account", "strategy"):
        owner.control.resume_if_ready(
            strategy, expected_epochs=owner.control.snapshot(strategy)["epochs"],
            readiness_verified=True, scope=gate)


def evidence(owner, strategy, mode):
    return {
        "contract_version": "exec01-1", "config_version": "cfg-1",
        "engine_incarnation": "synthetic-engine", "apply_seq": 1,
        "event_ids": ["event-1"], "input_versions": {"feed": 1},
        "expected_epochs": owner.control.snapshot(strategy)["epochs"],
        "quality": "LIVE", "mode": mode,
    }


def intent(identity, *, venue, asset, symbol, account, environment,
           quantity="0.1", price="100"):
    return OrderIntent(
        intent_id=identity, job_id="alpha", signal_id="signal-" + identity,
        destination=OrderDestination.REAL, client_order_id="c-" + identity,
        instrument=InstrumentId(venue=venue, asset_class=asset,
                                raw_symbol=symbol, std_symbol=symbol),
        side=Side.BUY, quantity=Decimal(quantity),
        limit_price=None if price is None else Decimal(price),
        metadata={"account_id": account, "environment": environment})


def action(identity, scope, intent_value, *, mode, config="cfg-1"):
    return ExecutionAction(
        action_id=identity, action=ActionKind.NEW, scope=scope, strategy_id="alpha",
        config_version=config, mode=mode, expiry_ns=NOW + 1_000_000_000,
        intent=intent_value, time_in_force="GTC")


def cancel_action(identity, scope, target, *, mode):
    return ExecutionAction(
        action_id=identity, action=ActionKind.CANCEL, scope=scope, strategy_id="alpha",
        config_version="cfg-1", mode=mode, expiry_ns=NOW + 1_000_000_000,
        target_order_id=target)


class ScriptedPerpTransport:
    mutation_permit_protocol = 1

    def __init__(self):
        self.calls = []

    async def request(self, method, url, *, params=None, json_body=None, data=None,
                      headers=None, before_send=None):
        if before_send is not None:
            before_send()
        self.calls.append((method, url))
        if method == "POST":
            return HttpResponse(200, {"symbol": "BTCUSDT", "orderId": 101,
                                      "clientOrderId": "c-perp", "status": "NEW",
                                      "origQty": "0.1", "executedQty": "0"})
        return HttpResponse(200, {"symbol": "BTCUSDT", "orderId": 101,
                                  "clientOrderId": "c-perp", "status": "CANCELED",
                                  "origQty": "0.1", "executedQty": "0"})


def _perp_native_facts(adapter, native_id):
    scope = adapter.scope
    product = dict(adapter._metadata["BTCUSDT"])
    product.update(scope_key=scope.key, venue=scope.venue, product=scope.product,
                   environment=scope.environment, symbol="BTCUSDT", kind="linear_perpetual",
                   base_currency="BTC", quote_currency="USDT",
                   settlement_currency="USDT", collateral_currency="USDT",
                   raw={"fixture": "native instrument metadata"},
                   position_mode="net", margin_mode="isolated")
    return {"account_facts": {
        "normalized": {"scope_key": scope.key, "venue": scope.venue, "product": scope.product,
            "environment": scope.environment, "raw": {"fixture": "authenticated synthetic session"},
            "currency": scope.settlement_currency, "settlement_currency": scope.settlement_currency,
            "equity": "1000000", "available": "1000000", "initial_margin": "0",
            "maintenance_margin": "0"},
        "observed_ns": NOW, "account_version": "fixture-v1",
        "reconciliation_state": "RECONCILED", "unknown": False,
        "position_mode": "net", "margin_mode": "isolated",
        "native_identity": {"native_account_id": native_id, "authenticated_session_id": "fixture-session",
            "scope_key": scope.key, "source": "authenticated_native_session",
            "permissions": ("account_read", "trade")}},
        "product_specs": {"BTCUSDT": product}, "taker_fee_rate": "0.002",
        "max_age_ns": 1_000_000_000}


def _cash_native_facts(scope, symbol, native_id, currency):
    return {"account_facts": {
        "normalized": {"scope_key": scope.key, "venue": scope.venue, "product": scope.product,
            "environment": scope.environment, "raw": {"fixture": "authenticated synthetic cash session"},
            "currency": currency, "equity": "1000000", "available": "1000000"},
        "observed_ns": NOW, "account_version": "fixture-v1",
        "reconciliation_state": "RECONCILED", "unknown": False,
        "native_identity": {"native_account_id": native_id, "authenticated_session_id": "fixture-session",
            "scope_key": scope.key, "source": "authenticated_native_session",
            "permissions": ("account_read", "trade")}},
        "product_specs": {symbol: {"scope_key": scope.key, "venue": scope.venue,
            "product": scope.product, "environment": scope.environment, "symbol": symbol,
            "kind": "cash", "quote_currency": currency, "settlement_currency": currency,
            "collateral_currency": currency, "raw": {"fixture": "native symbol row"}}},
        "taker_fee_rate": "0.001", "max_age_ns": 1_000_000_000}


def register_perp(tmp_path, *, native_proof=False):
    native_id = "native-binance-perp"
    scope = AccountScope("binance", "acct-perp", "linear_perpetual", "sandbox")
    route = RouteSpec("binance-perp", "binance", "usdm_perp", "linear_perpetual",
                      "sandbox", scope, native_id)
    transport = ScriptedPerpTransport()
    adapter = BinancePerpAdapter(
        PerpScope("binance", "linear_perpetual", "sandbox", "acct-perp", "USDT"),
        transport, api_key="synthetic-key", api_secret="synthetic-secret")
    adapter._metadata["BTCUSDT"] = {
        "lot_size": "0.001", "tick_size": "0.1", "min_quantity": "0.001",
        "min_notional": "5", "multiplier": "1",
    }
    registry = VenueRegistry(max_routes=4, max_aliases=8, max_fact_bytes=2048)
    caps = Capabilities({"linear_perpetual"}, {"sandbox"},
                        {"GTC", "IOC", "FOK", "POST_ONLY"}, True, True, False)
    registry.bind_existing_adapter(route, adapter,
                                   reference_provider=lambda: dict(ZERO_FACTS),
                                   capabilities=caps,
                                   native_facts_provider=(lambda symbol: _perp_native_facts(adapter, native_id))
                                   if native_proof else None)
    registry.register_aliases((AliasBinding(
        "alpha", "btc", route.route_id, "BTCUSDT", "usdm_perp",
        "linear_perpetual", "sandbox", scope),))
    owner = make_owner(tmp_path, scope, native_id)
    open_gates(owner, "alpha")
    return owner, registry, adapter, transport, scope


@pytest.mark.asyncio
async def test_existing_binance_perp_adapter_runs_through_common_owner(tmp_path):
    owner, registry, adapter, transport, scope = register_perp(tmp_path, native_proof=True)
    order = intent("perp", venue=Venue.BINANCE, asset=AssetClass.CRYPTO_PERP,
                   symbol="BTCUSDT", account="acct-perp", environment="sandbox")
    plan = StrategyActionPlan("plan-perp", "alpha", "cfg-1", NOW,
                              (action("a-perp", scope, order, mode=TradingMode.SANDBOX),))
    result = await owner.dispatch_plan(
        plan, registry=registry, readiness=readiness(owner, scope, order.instrument.key),
        evidence=evidence(owner, "alpha", "sandbox"))
    assert result["results"][0]["status"] == "SUBMITTED"
    assert [method for method, _ in transport.calls] == ["POST"]
    assert owner.ledger.get("perp")["route_id"] == "binance-perp"
    assert owner.ledger.get("perp")["strategy_id"] == "alpha"
    assert "c-perp" in registry.route_snapshot("binance-perp")["owned_orders"]
    await owner.close()


@pytest.mark.asyncio
async def test_existing_perp_cancel_ack_is_not_terminal_and_late_fill_is_once(tmp_path):
    owner, registry, adapter, transport, scope = register_perp(tmp_path, native_proof=True)
    order = intent("perp", venue=Venue.BINANCE, asset=AssetClass.CRYPTO_PERP,
                   symbol="BTCUSDT", account="acct-perp", environment="sandbox")
    plan = StrategyActionPlan("plan-perp", "alpha", "cfg-1", NOW,
                              (action("a-perp", scope, order, mode=TradingMode.SANDBOX),))
    await owner.dispatch_plan(plan, registry=registry,
                              readiness=readiness(owner, scope, order.instrument.key),
                              evidence=evidence(owner, "alpha", "sandbox"))
    cancel = cancel_action("c-perp", scope, "c-perp", mode=TradingMode.SANDBOX)
    ack = await owner.cancel_action(cancel, registry=registry,
                                    readiness=readiness(owner, scope, order.instrument.key))
    assert ack["status"] == "CANCEL_PENDING" and ack["terminal"] is False
    assert owner.ledger.get("perp")["state"] == "CANCEL_PENDING"
    assert (await owner.reconcile_order(cancel, registry=registry))["status"] == "CANCELED"
    assert owner.ledger.get("perp")["state"] == "CANCELLED"
    assert owner.apply_fill(cancel, fill_id="native-fill-1", quantity="0.04",
                            price="100", fee="0.1", order_id="101") is True
    assert owner.apply_fill(cancel, fill_id="native-fill-1", quantity="0.04",
                            price="100", fee="0.1", order_id="101") is False
    assert owner.ledger.position(order.instrument.key) == Decimal("0.04")
    assert owner.ledger.get("perp")["state"] == "CANCELLED"
    assert [row["event"]["status"] for row in owner.ledger.feedback("alpha")] == [
        "ACCEPTED", "CANCEL_PENDING", "CANCELLED", "PARTIAL_FILL"]
    await owner.close()


class ScriptedSpotTransport:
    mutation_permit_protocol = 1

    def __init__(self):
        self.calls = []

    async def request(self, method, url, *, params=None, json_body=None, data=None,
                      headers=None, before_send=None):
        if before_send is not None:
            before_send()
        self.calls.append((method, url))
        if "/exchangeInfo" in url:
            return HttpResponse(200, {"symbols": [{
                "symbol": "BTCUSDT", "status": "TRADING", "isSpotTradingAllowed": True,
                "orderTypes": ["LIMIT", "LIMIT_MAKER", "MARKET"],
                "permissions": ["SPOT"],
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001",
                     "maxQty": "1000"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.1", "minPrice": "0.1",
                     "maxPrice": "1000000"},
                    {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
                ]}]})
        if method == "DELETE":
            return HttpResponse(200, {"symbol": "BTCUSDT", "orderId": 222,
                                      "clientOrderId": "c-spot"})
        return HttpResponse(200, {"symbol": "BTCUSDT", "orderId": 222,
                                  "clientOrderId": "c-spot", "status": "NEW",
                                  "executedQty": "0"})


def register_spot(tmp_path):
    from execution.brokers import BinanceBrokerAdapter

    scope = AccountScope("binance", "acct-spot", "spot", "sandbox")
    native_id = "native-binance-spot"
    route = RouteSpec("binance-spot", "binance", "spot", "spot", "sandbox", scope, native_id)
    settings = SimpleNamespace(env={
        "BINANCE_USE_TESTNET": "1", "BINANCE_TESTNET_API_KEY": "synthetic-key",
        "BINANCE_TESTNET_API_SECRET": "synthetic-secret"}, state_dir=tmp_path)
    context = {"venue": "binance", "product": "spot", "environment": "sandbox",
               "account_alias": "spot-alias", "account_identity_verified": True,
               "account_id": "acct-spot", "strategy_id": "alpha"}
    transport = ScriptedSpotTransport()
    adapter = BinanceBrokerAdapter(settings, transport=transport, account_context=context)
    registry = VenueRegistry(max_routes=4, max_aliases=8, max_fact_bytes=2048)
    caps = Capabilities({"spot"}, {"sandbox"}, {"GTC", "IOC", "FOK"}, False, False, False)
    registry.bind_existing_adapter(route, adapter,
                                   reference_provider=lambda: dict(ZERO_FACTS),
                                   capabilities=caps,
                                   native_facts_provider=lambda symbol: _cash_native_facts(
                                       scope, symbol, native_id, "USDT"))
    registry.register_aliases((AliasBinding(
        "alpha", "spot", route.route_id, "BTCUSDT", "spot", "spot",
        "sandbox", scope),))
    owner = make_owner(tmp_path, scope, native_id)
    open_gates(owner, "alpha")
    return owner, registry, adapter, transport, scope


@pytest.mark.asyncio
async def test_spot_and_perp_same_symbol_keep_separate_physical_routes(tmp_path):
    spot_owner, spot_registry, spot_adapter, spot_transport, spot_scope = register_spot(
        tmp_path / "spot")
    perp_owner, perp_registry, perp_adapter, perp_transport, perp_scope = register_perp(
        tmp_path / "perp", native_proof=True)
    spot_order = intent("spot", venue=Venue.BINANCE, asset=AssetClass.CRYPTO_SPOT,
                        symbol="BTCUSDT", account="acct-spot", environment="sandbox",
                        quantity="0.1")
    perp_order = intent("perp", venue=Venue.BINANCE, asset=AssetClass.CRYPTO_PERP,
                        symbol="BTCUSDT", account="acct-perp", environment="sandbox")
    spot_result = await spot_owner.dispatch_plan(
        StrategyActionPlan("p1", "alpha", "cfg-1", NOW,
                           (action("a-spot", spot_scope, spot_order,
                                   mode=TradingMode.SANDBOX),)),
        registry=spot_registry,
        readiness=readiness(spot_owner, spot_scope, spot_order.instrument.key),
        evidence=evidence(spot_owner, "alpha", "sandbox"))
    assert spot_result["results"][0]["status"] == "SUBMITTED", spot_result
    await perp_owner.dispatch_plan(
        StrategyActionPlan("p2", "alpha", "cfg-1", NOW,
                           (action("a-perp", perp_scope, perp_order,
                                   mode=TradingMode.SANDBOX),)),
        registry=perp_registry,
        readiness=readiness(perp_owner, perp_scope, perp_order.instrument.key),
        evidence=evidence(perp_owner, "alpha", "sandbox"))
    assert [m for m, _ in spot_transport.calls] == ["GET", "POST"]
    assert [m for m, _ in perp_transport.calls] == ["POST"]
    assert spot_owner.ledger.scope.product == "spot"
    assert perp_owner.ledger.scope.product == "linear_perpetual"
    await spot_owner.close()
    await perp_owner.close()


class ScriptedKiwoomTransport:
    mutation_permit_protocol = 1

    def __init__(self):
        self.calls = []

    async def request(self, method, url, *, params=None, json_body=None, data=None,
                      headers=None, before_send=None):
        if before_send is not None:
            before_send()
        api_id = (headers or {}).get("api-id")
        self.calls.append(api_id)
        if api_id == "kt10000":
            return HttpResponse(200, {"ord_no": "1234567", "return_code": 0})
        if api_id == "kt10003":
            return HttpResponse(200, {"ord_no": "7654321",
                                      "base_orig_ord_no": json_body["orig_ord_no"],
                                      "return_code": 0})
        if api_id == "kt00007":
            return HttpResponse(200, {"acnt_ord_cntr_prps_dtl": [{
                "ord_no": "1234567", "ori_ord": "", "stk_cd": "005930",
                "io_tp_nm": "현금매수", "cntr_qty": "4", "cntr_uv": "70000",
            }], "return_code": 0}, {"cont-yn": "N", "next-key": ""})
        return HttpResponse(200, {"return_code": 0})


def register_kiwoom(tmp_path):
    from execution.brokers import KiwoomBrokerAdapter

    state_dir = Path(tmp_path)
    expires = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d%H%M%S")
    (state_dir / "kiwoom_token_mock.json").write_text(
        '{"token":"synthetic-token","expires_dt":"' + expires + '"}', encoding="utf-8")
    settings = SimpleNamespace(providers={"kiwoom": SimpleNamespace(values={
        "KIWOOM_APP_KEY": "synthetic-key", "KIWOOM_APP_SECRET": "synthetic-secret",
        "KIWOOM_USE_MOCK": "1"})}, state_dir=state_dir)
    context = {"venue": "kiwoom", "product": "cash", "environment": "mock",
               "account_alias": "kw-alias", "account_identity_verified": True,
               "account_id": "acct-kiwoom", "strategy_id": "alpha",
               "native_account_id": "1234567890"}
    scope = AccountScope("kiwoom", "acct-kiwoom", "cash", "mock")
    native_id = "1234567890"
    route = RouteSpec("kiwoom-mock", "kiwoom", "krx_cash", "cash", "mock", scope, native_id)
    transport = ScriptedKiwoomTransport()
    adapter = KiwoomBrokerAdapter(settings, transport=transport, account_context=context)
    registry = VenueRegistry(max_routes=4, max_aliases=8, max_fact_bytes=2048)
    caps = Capabilities({"cash"}, {"mock"}, {"GTC"}, False, False, False)
    registry.bind_existing_adapter(route, adapter,
                                   reference_provider=lambda: dict(ZERO_FACTS),
                                   capabilities=caps,
                                   native_facts_provider=lambda symbol: _cash_native_facts(
                                       scope, symbol, native_id, "KRW"))
    registry.register_aliases((AliasBinding(
        "alpha", "krx", route.route_id, "005930", "krx_cash", "cash", "mock", scope),))
    owner = make_owner(tmp_path, scope, native_id)
    open_gates(owner, "alpha")
    return owner, registry, adapter, transport, scope


@pytest.mark.asyncio
async def test_actual_kiwoom_rest_cancel_query_and_late_fill_once(tmp_path):
    owner, registry, adapter, transport, scope = register_kiwoom(tmp_path)
    order = intent("kw", venue=Venue.KIWOOM, asset=AssetClass.EQUITY, symbol="005930",
                   account="acct-kiwoom", environment="mock", quantity="10",
                   price="70000")
    plan = StrategyActionPlan("plan-kw", "alpha", "cfg-1", NOW,
                              (action("a-kw", scope, order, mode=TradingMode.KIS_MOCK),))
    result = await owner.dispatch_plan(
        plan, registry=registry, readiness=readiness(owner, scope, order.instrument.key),
        evidence=evidence(owner, "alpha", "mock"))
    assert result["results"][0]["status"] == "SUBMITTED", result
    assert result["results"][0]["order_id"] == "1234567"
    cancel = cancel_action("c-kw", scope, "1234567", mode=TradingMode.KIS_MOCK)
    ack = await owner.cancel_action(cancel, registry=registry,
                                    readiness=readiness(owner, scope, order.instrument.key))
    assert ack["status"] == "CANCEL_PENDING"
    assert owner.ledger.get("kw")["cancel_state"] == "CANCEL_PENDING"
    assert transport.calls == ["kt10000", "kt10003"]
    # The original source query remains usable after the cancel ACK.
    rows = await registry.invoke("kiwoom-mock", "query_order", "005930", "20261028",
                                 "1234567")
    assert rows["status"] == "FACTS" and rows["identity_proven"] is True
    with pytest.raises(Exception):
        owner.confirm_cancel(cancel)
    assert owner.ledger.get("kw")["state"] == "CANCEL_PENDING"
    assert owner.apply_fill(cancel, fill_id="kw-exec-1", quantity="4", price="70000", fee="0.1",
                            order_id="1234567") is True
    assert owner.apply_fill(cancel, fill_id="kw-exec-1", quantity="4", price="70000", fee="0.1",
                            order_id="1234567") is False
    assert owner.ledger.get("kw")["state"] == "CANCEL_PENDING"
    assert owner.ledger.position(order.instrument.key) == Decimal("4")
    assert owner.ledger.get("kw")["cancel_state"] == "CANCEL_PENDING"
    await owner.close()

    # Reopen the same durable owner ledger after the factual late fill. Its
    # persisted fill identity still rejects a replay after process restart.
    restarted = make_owner(tmp_path, scope, "1234567890")
    try:
        assert restarted.ledger.get("kw")["filled"] == "4"
        assert restarted.ledger.position(order.instrument.key) == Decimal("4")
        assert restarted.apply_fill(cancel, fill_id="kw-exec-1", quantity="4",
                                    price="70000", fee="0.1", order_id="1234567") is False
        assert restarted.ledger.get("kw")["filled"] == "4"
    finally:
        await restarted.close()


def test_new_dummy_module_registers_without_core_source_change(tmp_path):
    """A new NH-like module supplies only an explicit protocol declaration."""
    from execution.process_lease import ProcessLease
    from execution.venues import registry as registry_module

    class DummyNhAdapter:
        execution_candidate_protocol = "mapping-v1"
        execution_cancel_protocol = "symbol-client-id"
        name = "dummy-nh"

        def __init__(self, scope):
            self.scope = scope
            self.orders = {}
            self.mutations = []

        async def submit_with_permit(self, order, permit):
            permit()
            self.orders[order["client_order_id"]] = dict(order)
            self.mutations.append(("NEW", order["client_order_id"]))
            return {"client_order_id": order["client_order_id"],
                    "order_id": "nh-" + order["client_order_id"], "status": "NEW"}

        async def cancel_with_permit(self, symbol, client_order_id, permit):
            permit()
            self.mutations.append(("CANCEL", client_order_id))
            return {"client_order_id": client_order_id, "acknowledged": True}

        async def order_status(self, symbol, client_order_id):
            return dict(self.orders[client_order_id], status="FILLED")

        def normalize_private_event(self, frame):
            return []

    ProcessLease.register_extension_venue("dummy-nh")
    scope = AccountScope("dummy-nh", "nh-account", "cash", "sandbox")
    route = RouteSpec("nh-cash", "dummy-nh", "cash", "cash", "sandbox", scope)
    registry = VenueRegistry(max_routes=2, max_aliases=2, max_fact_bytes=512)
    adapter = DummyNhAdapter(scope)
    caps = Capabilities({"cash"}, {"sandbox"}, {"GTC"}, False, False, False)
    registry.register(route, adapter, {"submit": "submit_with_permit",
                                       "cancel": "cancel_with_permit",
                                       "query_order": "order_status"},
                      caps, lambda: dict(ZERO_FACTS))
    registry.register_aliases((AliasBinding(
        "alpha", "nh", route.route_id, "NHX", "cash", "cash", "sandbox", scope),))
    assert registry.binding("nh-cash").canonical_owner.endswith("DummyNhAdapter")
    # Core source was not extended with a venue branch; the declared protocol
    # is the only integration surface.
    assert "dummy-nh" not in registry_module.EXISTING_ADAPTER_MAPS
