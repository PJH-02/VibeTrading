"""Credential-free CAP-05 source/native fixtures.

This module is a test fixture only.  It uses the existing Binance and Bybit
perpetual adapter classes with scripted transports and explicit synthetic
native account facts.  It never reads provider settings or touches a network.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import MappingProxyType

from core.models import (
    AssetClass, EventType, InstrumentId, OrderDestination, OrderIntent, Side,
    TradeEvent, Venue,
)
from execution.execution_ledger import AccountScope, ExecutionLedger
from execution.execution_owner import ExecutionBudgets, ExecutionOwner, ExecutionReadiness
from execution.perp_execution import (
    BinancePerpAdapter, BybitPerpAdapter, HttpResponse, PerpScope,
)
from execution.trading_contracts import (
    ActionKind, ExecutionAction, GroupSubmitMode, StrategyActionPlan, TradingMode,
)
from execution.venues.registry import (
    AliasBinding, Capabilities, MANDATORY_REMOVAL_FACTS, RouteSpec, VenueRegistry,
)
from market.hot_path import DecisionSnapshot, StrategyDataView
from runtime.runtime_control import RuntimeControl

NOW = 1_000_000_000
STRATEGY_ID = "cap05-alpha"
CONFIG_VERSION = "cfg-cap05"
ZERO_FACTS = {key: 0 for key in MANDATORY_REMOVAL_FACTS}


class SendBarrier:
    def __init__(self):
        self.count = 0
        self.event = asyncio.Event()

    async def wait(self):
        self.count += 1
        if self.count >= 2:
            self.event.set()
        await asyncio.wait_for(self.event.wait(), timeout=2)
        return True


class ScriptedPerpTransport:
    mutation_permit_protocol = 1

    def __init__(self, venue, *, barrier=None, order_log=None, fail_after_permit=False,
                 native_orders=None, native_fills=None):
        self.venue = venue
        self.calls = []
        self.read_calls = []
        self.barrier = barrier
        self.order_log = order_log
        self.fail_after_permit = fail_after_permit
        # Native orders: client_order_id -> {"status","cumulative","native_id"};
        # native fills: exact per-fill rows in each adapter's native response
        # schema. Reads are scripted; nothing here touches a network.
        self.native_orders = native_orders or {}
        self.native_fills = native_fills or []
        self._serial = 0
        self.post_payloads = []

    def _get(self, url, params):
        params = params or {}
        client = (params.get("origClientOrderId") or params.get("orderLinkId")
                  or params.get("client_order_id"))
        self.read_calls.append((url, client))
        row = self.native_orders.get(client) if client else None
        if "fapi/v1/order" in url:
            if row is None:
                return HttpResponse(200, {"code": -2013, "msg": "Order does not exist"})
            return HttpResponse(200, {
                "symbol": params.get("symbol", "BTCUSDT"),
                "orderId": row.get("native_id", 7001), "clientOrderId": client,
                "status": row.get("status", "NEW"),
                "origQty": row.get("quantity", "0.1"),
                "executedQty": row.get("cumulative", "0"),
            })
        if "userTrades" in url:
            rows = [r for r in self.native_fills
                    if r.get("client_order_id") == client or "orderId" in r]
            if params.get("fromId") is not None:
                rows = []
            return HttpResponse(200, rows)
        if "fapi/v1/income" in url:
            return HttpResponse(200, [])
        if "order/realtime" in url or "order/history" in url:
            if row is None:
                return HttpResponse(200, {"retCode": 0, "result": {"list": [], "nextPageCursor": ""}})
            normalized = dict(row)
            if "order/realtime" in url and row.get("terminal"):
                normalized = {"list": [], "nextPageCursor": ""}
            else:
                normalized = {"list": [{
                    "symbol": "BTCUSDT.P", "orderId": row.get("native_id", "bybit-7001"),
                    "orderLinkId": client, "orderStatus": row.get("status", "New"),
                    "cumExecQty": row.get("cumulative", "0"), "executedQty": row.get("cumulative", "0"),
                    "queuePosition": 0, "size": row.get("quantity", "0.1"),
                    "cumExecValue": "0", "avgPrice": "100", "side": "Buy",
                    "lastPriceOnCreated": "100", "createType": "CreateByUser",
                    "reduceOnly": True, "timeInForce": "GTC", "orderType": "Limit",
                }], "nextPageCursor": ""}
            return HttpResponse(200, {"retCode": 0, "result": normalized})
        if "execution/list" in url:
            rows = [r for r in self.native_fills
                    if (not client or r.get("orderLinkId") == client)
                    and (not params.get("symbol") or r.get("symbol") == params.get("symbol"))]
            if params.get("cursor"):
                rows = []
            return HttpResponse(200, {"retCode": 0, "result": {"list": rows, "nextPageCursor": ""}})
        if "transaction-log" in url:
            return HttpResponse(200, {"retCode": 0, "result": {"list": [], "nextPageCursor": ""}})
        return HttpResponse(200, {"retCode": 0, "result": {"list": []}})

    async def request(self, method, url, *, params=None, json_body=None, data=None,
                      headers=None, before_send=None):
        if before_send is not None:
            before_send()
        if method == "DELETE":
            return HttpResponse(200, {"retCode": 0, "result": {"orderLinkId":
                params.get("origClientOrderId", "cap05") if params else "cap05"}})
        if method != "POST":
            return self._get(url, params)
        payload = json_body
        if payload is None and isinstance(data, str):
            payload = json.loads(data)
        if payload is None:
            payload = dict(params or {})
        self._serial += 1
        self.calls.append(("POST", url))
        self.post_payloads.append(dict(payload))
        if self.order_log is not None:
            self.order_log.append(self.venue)
            if self.venue == "bybit" and "binance" not in self.order_log:
                raise AssertionError("SEQUENTIAL bybit send preceded binance ACK")
        if self.barrier is not None:
            await self.barrier.wait()
        if self.fail_after_permit:
            raise RuntimeError("scripted ambiguous transport failure")
        client = payload.get("newClientOrderId") or payload.get("orderLinkId") or "cap05"
        if self.venue == "binance":
            return HttpResponse(200, {
                "symbol": payload.get("symbol", "BTCUSDT"), "orderId": 7000 + self._serial,
                "clientOrderId": client, "status": "NEW", "origQty": payload.get("quantity", "0.1"),
                "executedQty": "0",
            })
        return HttpResponse(200, {"retCode": 0, "result": {
            "symbol": payload.get("symbol", "BTCUSDT"),
            "orderId": "bybit-" + str(7000 + self._serial),
            "orderLinkId": client,
        }})


class CombinedRegistry:
    """Read-only test composition of two existing VenueRegistry instances."""

    def __init__(self, registries):
        self.registries = tuple(registries)

    def aliases_for_strategy(self, strategy_id):
        return tuple(alias for registry in self.registries
                     for alias in registry.aliases_for_strategy(strategy_id))

    def _registry(self, route_id):
        for registry in self.registries:
            try:
                registry.route(route_id)
            except Exception:
                continue
            return registry
        raise KeyError(route_id)

    def route(self, route_id):
        return self._registry(route_id).route(route_id)

    def binding(self, route_id):
        return self._registry(route_id).binding(route_id)

    def invoke(self, route_id, operation, *args, **kwargs):
        return self._registry(route_id).invoke(route_id, operation, *args, **kwargs)

    def route_snapshot(self, route_id):
        return self._registry(route_id).route_snapshot(route_id)

    def restore_owned(self, route_id, rows):
        return self._registry(route_id).restore_owned(route_id, rows)


def rebuild_native_registry(fixture):
    """Cold registration with fresh existing adapters and scripted transports."""
    specs = (
        ("binance", "binance-perp", fixture["binance_scope"], fixture["binance_native"],
         BinancePerpAdapter, "BTCUSDT", fixture["binance_transport"].native_orders,
         fixture["binance_transport"].native_fills),
        ("bybit", "bybit-perp", fixture["bybit_scope"], fixture["bybit_native"],
         BybitPerpAdapter, "BTCUSDT.P", fixture["bybit_transport"].native_orders,
         fixture["bybit_transport"].native_fills),
    )
    registries = []
    for venue, route_id, scope, native_id, adapter_type, symbol, orders, fills in specs:
        transport = ScriptedPerpTransport(venue, native_orders=orders, native_fills=fills)
        adapter = adapter_type(
            PerpScope(venue, "linear_perpetual", "sandbox", scope.account, "USDT"),
            transport, api_key="synthetic-key", api_secret="synthetic-secret",
            clock_ns=lambda: NOW)
        adapter._metadata[symbol] = {
            "lot_size": "0.001", "tick_size": "0.1", "min_quantity": "0.001",
            "min_notional": "5", "multiplier": "1",
        }
        route = RouteSpec(route_id, venue, "usdm_perp" if venue == "binance" else "linear",
                          "linear_perpetual", "sandbox", scope, native_id)
        registry = VenueRegistry(max_routes=2, max_aliases=2, max_fact_bytes=4096)
        capabilities = Capabilities({"linear_perpetual"}, {"sandbox"},
                                    {"GTC", "IOC", "FOK", "POST_ONLY"}, True, True, False)
        registry.bind_existing_adapter(
            route, adapter, reference_provider=lambda: dict(ZERO_FACTS),
            capabilities=capabilities,
            native_facts_provider=lambda request_symbol, a=adapter, n=native_id:
                _perp_native_facts(a, n, symbol=request_symbol))
        registry.register_aliases((AliasBinding(
            STRATEGY_ID, venue + "-btc", route_id, symbol,
            "usdm_perp" if venue == "binance" else "linear",
            "linear_perpetual", "sandbox", scope),))
        registries.append(registry)
    return CombinedRegistry(registries)


def _perp_native_facts(adapter, native_id, symbol="BTCUSDT"):
    scope = adapter.scope
    product = dict(adapter._metadata[symbol])
    product.update(scope_key=scope.key, venue=scope.venue, product=scope.product,
                   environment=scope.environment, symbol=symbol, kind="linear_perpetual",
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
        "native_identity": {"native_account_id": native_id,
            "authenticated_session_id": "fixture-session", "scope_key": scope.key,
            "source": "authenticated_native_session",
            "permissions": ("account_read", "trade")}},
        "product_specs": {symbol: product}, "taker_fee_rate": "0.002",
        "max_age_ns": 1_000_000_000}


def make_owner(tmp_path, scope, native_account_id):
    ledger = ExecutionLedger(tmp_path / "orders.sqlite3", scope, max_intents=64,
                             max_evidence_bytes=65536, max_fills=64,
                             authority_root=tmp_path / "authority",
                             native_account_id=native_account_id)
    control = RuntimeControl(ledger)
    budgets = ExecutionBudgets(Decimal("10000000"), Decimal("100000000"), 8,
                               10_000_000_000, 2.0, 2.0)
    return ExecutionOwner(ledger, control, object(), budgets, clock=lambda: NOW)


def open_gates(owner):
    if owner.ledger.unresolved():
        return
    for gate in ("global", "account", "strategy"):
        owner.control.resume_if_ready(
            STRATEGY_ID, expected_epochs=owner.control.snapshot(STRATEGY_ID)["epochs"],
            readiness_verified=True, scope=gate)


def readiness(owner, intent):
    return lambda: ExecutionReadiness(
        owner.ledger.scope.key, True, True, True,
        NOW + 100_000_000, Decimal("0"), Decimal("1000000"),
        account_version=owner.ledger.account_version(),
        allowed_instruments=(intent.instrument.key,),
        market_price_caps=((intent.instrument.key, Decimal("100000")),))


def evidence(owner, environment="sandbox"):
    return {
        "contract_version": "cap05-1", "config_version": CONFIG_VERSION,
        "engine_incarnation": "cap05-fixture-engine", "apply_seq": 1,
        "event_ids": ["cap05-event-1"], "input_versions": {"feed": 1},
        "expected_epochs": owner.control.snapshot(STRATEGY_ID)["epochs"],
        "quality": "LIVE", "mode": environment,
    }


def make_intent(leg_id, *, venue, account, side, quantity, std_symbol=None,
                raw_symbol="BTCUSDT"):
    return OrderIntent(
        intent_id="intent-" + leg_id, job_id="cap05-job", signal_id="signal-" + leg_id,
        destination=OrderDestination.REAL, client_order_id="c-" + leg_id,
        instrument=InstrumentId(venue=venue, asset_class=AssetClass.CRYPTO_PERP,
                                raw_symbol=raw_symbol,
                                std_symbol=std_symbol or raw_symbol),
        side=side, quantity=Decimal(quantity), limit_price=Decimal("100"),
        metadata={"account_id": account, "environment": "sandbox",
                  "strategy_id": STRATEGY_ID})


def make_action(leg_id, scope, intent, mode=TradingMode.SANDBOX):
    return ExecutionAction(
        action_id="action-" + leg_id, action=ActionKind.NEW, scope=scope,
        strategy_id=STRATEGY_ID, config_version=CONFIG_VERSION, mode=mode,
        expiry_ns=NOW + 1_000_000_000, intent=intent, time_in_force="GTC",
        group_id="cap05-group")


def group_policy(scopes):
    return {
        "base_asset": "BTC", "deadline_ns": NOW + 900_000_000,
        "max_followup_actions": 2, "max_legs": 2,
        "notional_caps": {scope.key: "1000000" for scope in scopes},
        "cost_caps": {scope.key: "1000" for scope in scopes},
    }


def make_group_plan(scopes, *, mode=GroupSubmitMode.CONCURRENT,
                    binance_side=Side.BUY, bybit_side=Side.SELL,
                    binance_qty="0.1", bybit_qty="0.2", bybit_std="BTCUSDT.P"):
    binance_intent = make_intent("binance", venue=Venue.BINANCE,
                                 account=scopes[0].account, side=binance_side,
                                 quantity=binance_qty)
    bybit_intent = make_intent("bybit", venue=Venue.BYBIT,
                               account=scopes[1].account, side=bybit_side,
                               quantity=bybit_qty, std_symbol=bybit_std,
                               raw_symbol=bybit_std)
    actions = (make_action("binance", scopes[0], binance_intent),
               make_action("bybit", scopes[1], bybit_intent))
    return StrategyActionPlan(
        "plan-cap05", STRATEGY_ID, CONFIG_VERSION, NOW, actions,
        group_submit_mode=mode, group_policy=group_policy(scopes))


def build_native_group(tmp_path, *, mode=GroupSubmitMode.CONCURRENT,
                       barrier=None, order_log=None, fail_binance=False,
                       fail_bybit=False, binance_side=Side.BUY, bybit_side=Side.SELL,
                       binance_qty="0.1", bybit_qty="0.2",
                       binance_orders=None, bybit_orders=None,
                       binance_fills=None, bybit_fills=None):
    binance_scope = AccountScope("binance", "acct-binance", "linear_perpetual", "sandbox")
    bybit_scope = AccountScope("bybit", "acct-bybit", "linear_perpetual", "sandbox")
    binance_native_id = "native-binance-cap05"
    bybit_native_id = "native-bybit-cap05"

    binance_transport = ScriptedPerpTransport(
        "binance", barrier=barrier, order_log=order_log, fail_after_permit=fail_binance,
        native_orders=binance_orders, native_fills=binance_fills)
    bybit_transport = ScriptedPerpTransport(
        "bybit", barrier=barrier, order_log=order_log, fail_after_permit=fail_bybit,
        native_orders=bybit_orders, native_fills=bybit_fills)
    binance_adapter = BinancePerpAdapter(
        PerpScope("binance", "linear_perpetual", "sandbox", "acct-binance", "USDT"),
        binance_transport, api_key="synthetic-key", api_secret="synthetic-secret",
        clock_ns=lambda: NOW)
    bybit_adapter = BybitPerpAdapter(
        PerpScope("bybit", "linear_perpetual", "sandbox", "acct-bybit", "USDT"),
        bybit_transport, api_key="synthetic-key", api_secret="synthetic-secret",
        clock_ns=lambda: NOW)
    binance_adapter._metadata["BTCUSDT"] = {
        "lot_size": "0.001", "tick_size": "0.1", "min_quantity": "0.001",
        "min_notional": "5", "multiplier": "1",
    }
    bybit_adapter._metadata["BTCUSDT.P"] = {
        "lot_size": "0.001", "tick_size": "0.1", "min_quantity": "0.001",
        "min_notional": "5", "multiplier": "1",
    }

    def registry_for(route_id, venue, scope, native_id, adapter, alias_symbol):
        route = RouteSpec(route_id, venue, "usdm_perp" if venue == "binance" else "linear",
                          "linear_perpetual", "sandbox", scope, native_id)
        registry = VenueRegistry(max_routes=2, max_aliases=2, max_fact_bytes=4096)
        caps = Capabilities({"linear_perpetual"}, {"sandbox"},
                            {"GTC", "IOC", "FOK", "POST_ONLY"}, True, True, False)
        registry.bind_existing_adapter(
            route, adapter, reference_provider=lambda: dict(ZERO_FACTS),
            capabilities=caps,
            native_facts_provider=lambda symbol: _perp_native_facts(
                adapter, native_id, symbol=symbol))
        registry.register_aliases((AliasBinding(
            STRATEGY_ID, venue + "-btc", route_id, alias_symbol, "usdm_perp" if venue == "binance" else "linear",
            "linear_perpetual", "sandbox", scope),))
        return registry

    binance_registry = registry_for("binance-perp", "binance", binance_scope,
                                    binance_native_id, binance_adapter, "BTCUSDT")
    bybit_registry = registry_for("bybit-perp", "bybit", bybit_scope,
                                  bybit_native_id, bybit_adapter, "BTCUSDT.P")
    combined = CombinedRegistry((binance_registry, bybit_registry))
    binance_owner = make_owner(tmp_path / "binance", binance_scope, binance_native_id)
    bybit_owner = make_owner(tmp_path / "bybit", bybit_scope, bybit_native_id)
    open_gates(binance_owner)
    open_gates(bybit_owner)
    plan = make_group_plan((binance_scope, bybit_scope), mode=mode,
                           binance_side=binance_side, bybit_side=bybit_side,
                           binance_qty=binance_qty, bybit_qty=bybit_qty,
                           bybit_std="BTCUSDT.P")
    readiness_map = {
        binance_scope.key: readiness(binance_owner, plan.actions[0].intent),
        bybit_scope.key: readiness(bybit_owner, plan.actions[1].intent),
    }
    evidence_map = {
        binance_scope.key: evidence(binance_owner),
        bybit_scope.key: evidence(bybit_owner),
    }
    return {
        "plan": plan, "registry": combined,
        "binance_owner": binance_owner, "bybit_owner": bybit_owner,
        "binance_transport": binance_transport, "bybit_transport": bybit_transport,
        "binance_scope": binance_scope, "bybit_scope": bybit_scope,
        "binance_native": binance_native_id, "bybit_native": bybit_native_id,
        "readiness": readiness_map, "evidence": evidence_map,
    }


def make_market_event_and_view():
    instrument = InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_PERP,
                              raw_symbol="BTCUSDT", std_symbol="BTCUSDT")
    trade = TradeEvent(event_id="cap05-market-1", instrument=instrument,
                       venue=Venue.BINANCE, provider="cap05-fixture",
                       price=Decimal("100"), quantity=Decimal("1"),
                       source_ts_ns=NOW, receive_ts_ns=NOW)
    snapshot = DecisionSnapshot(
        "cap05-engine", 1, NOW, "SHADOW", 0,
        MappingProxyType({}), MappingProxyType({}), MappingProxyType({}),
        MappingProxyType({instrument.key: trade}), MappingProxyType({}),
        MappingProxyType({}), MappingProxyType({}), MappingProxyType({}))
    return trade, StrategyDataView(snapshot, ready=True, phase="SHADOW")


class Cap05Source:
    STRATEGY_ID = STRATEGY_ID
    ACTION_PROTOCOL_VERSION = 1
    config_version = CONFIG_VERSION

    def __init__(self, plan):
        self.plan = plan
        self.market_calls = 0
        self.order_events = []

    def on_event(self, event, data):
        self.market_calls += 1
        return self.plan

    def on_order_event(self, event, context):
        self.order_events.append((event.event_id, dict(event.metadata)))
        return None
