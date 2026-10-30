"""VEN-01 local registry contract tests; fixtures remain synthetic/offline."""
from __future__ import annotations

import asyncio
import inspect
import math
import pytest

from execution.execution_ledger import AccountScope
from execution.venues import dummy_crypto, dummy_stock
from execution.venues.registry import (
    AdapterProtocolError,
    Capabilities,
    ForeignOrderError,
    MANDATORY_REMOVAL_FACTS,
    RemovalBlocked,
    RouteError,
    RouteSpec,
    RouteState,
    RouteStateError,
    UnsupportedOperation,
    VenueRegistry,
    existing_adapter_contract,
)
from runtime.local_config import LocalRuntimePlan, parse_execution_aliases


ZERO_FACTS = {key: 0 for key in MANDATORY_REMOVAL_FACTS}


def _registry(**limits):
    settings = {"max_routes": 8, "max_aliases": 16, "max_fact_bytes": 2048}
    settings.update(limits)
    return VenueRegistry(**settings)


def _routes():
    crypto_scope = dummy_crypto.DEFAULT_CRYPTO_SCOPE
    stock_scope = dummy_stock.DEFAULT_STOCK_SCOPE
    crypto = RouteSpec("crypto", "dummy-crypto", "linear", "linear_perpetual", "sandbox", crypto_scope)
    stock = RouteSpec("stock", "dummy-stock", "cash", "cash", "sandbox", stock_scope)
    return crypto, stock, crypto_scope, stock_scope


def _register(registry, route, adapter, capabilities, operations, refs=None):
    return registry.register(route, adapter, operations, capabilities,
                             refs or (lambda: dict(ZERO_FACTS)))


def _fixtures(*, crypto_before=None, stock_before=None, max_aliases=16):
    registry = _registry(max_aliases=max_aliases)
    crypto, stock, cs, ss = _routes()
    ca = dummy_crypto.CryptoDummyAdapter(cs, before_permit=crypto_before)
    sa = dummy_stock.StockDummyAdapter(ss, before_permit=stock_before)
    _register(registry, crypto, ca, dummy_crypto.CRYPTO_CAPABILITIES, dummy_crypto.OPERATIONS)
    _register(registry, stock, sa, dummy_stock.STOCK_CAPABILITIES, dummy_stock.OPERATIONS)
    return registry, crypto, stock, ca, sa, cs, ss


def _order(identity, symbol, *, product, tif="GTC", **extra):
    scope = (dummy_stock.DEFAULT_STOCK_SCOPE if product == "cash"
             else dummy_crypto.DEFAULT_CRYPTO_SCOPE)
    return {
        "client_order_id": identity, "symbol": symbol, "side": "buy",
        "quantity": "2", "limit_price": "12.5", "product": product,
        "time_in_force": tif, "action": "NEW", "strategy_id": "strategy",
        "account_scope": scope, "environment": scope.environment,
        "venue": scope.venue, **extra,
    }


def _permit():
    used = []
    def consume():
        used.append(True)
    consume.used = used
    return consume


def _alias(strategy, alias, route, scope, symbol, *, required_feeds=()):
    return {
        "route_id": route.route_id, "symbol": symbol,
        "market_segment": route.market_segment, "product": route.product,
        "account_scope": {
            "venue": scope.venue, "account": scope.account,
            "product": scope.product, "environment": scope.environment,
        },
        "environment": route.environment, "required_feeds": list(required_feeds),
    }


def _alias_doc(strategy, alias, route, scope, symbol, *, required_feeds=()):
    return {strategy: {alias: _alias(strategy, alias, route, scope, symbol,
                                    required_feeds=required_feeds)}}


def test_registers_two_explicit_synthetic_adapters_and_exposes_factual_owners():
    registry, crypto, stock, ca, sa, _, _ = _fixtures()
    assert registry.binding(crypto.route_id).adapter is ca
    assert registry.binding(stock.route_id).adapter is sa
    assert registry.binding(crypto.route_id).canonical_owner.endswith("CryptoDummyAdapter")
    assert registry.binding(stock.route_id).canonical_owner.endswith("StockDummyAdapter")
    assert registry.binding(crypto.route_id).reference_provider() == ZERO_FACTS
    assert registry.binding(stock.route_id).reference_provider() == ZERO_FACTS
    assert all(callable(getattr(registry.binding(crypto.route_id).adapter,
                                method_name, None))
               for method_name in dummy_crypto.OPERATIONS.values())


def test_crypto_stock_submit_query_cancel_and_fill_use_distinct_fixture_state():
    async def run():
        registry, crypto, stock, ca, sa, _, _ = _fixtures()
        for route, adapter, product, symbol, identity in (
                (crypto, ca, "linear_perpetual", "SAME", "c-1"),
                (stock, sa, "cash", "SAME", "s-1")):
            permit = _permit()
            await registry.invoke(route.route_id, "submit",
                                  _order(identity, symbol, product=product), permit=permit)
            assert permit.used == [True]
            found = await registry.invoke(route.route_id, "query_order", symbol, identity)
            assert found["client_order_id"] == identity
            fill = adapter.fill(symbol, identity, quantity="0.5", price="12", fill_id="f-" + identity)
            assert fill is True
            assert len((await registry.invoke(route.route_id, "get_fills", symbol))["rows"]) == 1
            # Cancellation remains tied to this fixture's actual order ID.
            scope = dummy_stock.DEFAULT_STOCK_SCOPE if product == "cash" else dummy_crypto.DEFAULT_CRYPTO_SCOPE
            await registry.invoke(route.route_id, "cancel", symbol, identity,
                                  strategy_id="strategy", account_scope=scope,
                                  permit=_permit())
            assert (await registry.invoke(route.route_id, "query_order", symbol, identity))["status"] == "CANCELED"
        assert ca.mutation_count == sa.mutation_count == 2
    asyncio.run(run())


def test_toss_is_data_only_and_cannot_be_registered():
    scope = AccountScope("toss", "local", "cash", "offline")
    with pytest.raises(ValueError):
        RouteSpec("toss", "toss", "cash", "cash", "offline", scope)


def test_aliases_require_exact_route_and_same_symbol_spot_perp_stays_explicit():
    registry, crypto, stock, _, _, cs, ss = _fixtures()
    parse_execution_aliases(_alias_doc("strategy", "perp", crypto, cs, "BTC"), registry)
    parse_execution_aliases(_alias_doc("strategy", "cash", stock, ss, "BTC"), registry)
    assert registry.alias("strategy", "perp").product == "linear_perpetual"
    assert registry.alias("strategy", "cash").product == "cash"
    bad = _alias_doc("other", "wrong", crypto, cs, "BTC")
    bad["other"]["wrong"]["product"] = "cash"
    with pytest.raises((ValueError, RouteError)):
        parse_execution_aliases(bad, registry)


def test_alias_rejects_ambiguous_duplicate_strategy_symbol_and_incompatible_physical_account():
    registry, crypto, stock, _, _, cs, ss = _fixtures()
    parse_execution_aliases(_alias_doc("s", "one", crypto, cs, "BTC"), registry)
    # A second route with the same exact product/symbol but another route ID is ambiguous.
    second_scope = AccountScope("dummy-crypto", "other-account", "linear_perpetual", "sandbox")
    second = RouteSpec("crypto2", "dummy-crypto", "linear", "linear_perpetual", "sandbox", second_scope)
    other = dummy_crypto.CryptoDummyAdapter(second_scope)
    _register(registry, second, other, dummy_crypto.CRYPTO_CAPABILITIES, dummy_crypto.OPERATIONS)
    with pytest.raises((ValueError, RouteError)):
        parse_execution_aliases(_alias_doc("s", "two", second, second_scope, "btc"), registry)
    # Same physical account and symbol cannot be declared as cash and linear.
    from execution.venues.registry import AliasBinding
    cash_scope = AccountScope(cs.venue, cs.account, "cash", cs.environment)
    cash_route = RouteSpec("cash-same-account", cs.venue, "cash", "cash", cs.environment, cash_scope)
    cash_adapter = dummy_stock.StockDummyAdapter(cash_scope)
    _register(registry, cash_route, cash_adapter, dummy_stock.STOCK_CAPABILITIES,
              dummy_stock.OPERATIONS)
    item = AliasBinding("s", "cash-btc", cash_route.route_id, "BTC", "cash", "cash",
                        "sandbox", cash_scope)
    with pytest.raises((ValueError, RouteError)):
        registry.register_aliases((item,))


@pytest.mark.parametrize("state_change", ["disable", "quarantine"])
def test_route_lifecycle_blocks_new_risk_but_keeps_owned_query_cancel_and_other_route(state_change):
    async def run():
        registry, crypto, stock, ca, sa, _, _ = _fixtures()
        await registry.invoke("crypto", "submit", _order("owned", "BTC", product="linear_perpetual"), permit=_permit())
        getattr(registry, state_change)("crypto")
        with pytest.raises(RouteStateError):
            await registry.invoke("crypto", "submit", _order("blocked", "BTC", product="linear_perpetual"), permit=_permit())
        assert (await registry.invoke("crypto", "query_order", "BTC", "owned"))["status"] == "OPEN"
        await registry.invoke("crypto", "cancel", "BTC", "owned", strategy_id="strategy",
                              account_scope=dummy_crypto.DEFAULT_CRYPTO_SCOPE, permit=_permit())
        await registry.invoke("stock", "submit", _order("still-works", "ABC", product="cash"), permit=_permit())
        assert ca.mutation_count == 2 and sa.mutation_count == 1
    asyncio.run(run())


def test_old_owned_handle_cannot_be_cancelled_through_foreign_scope():
    async def run():
        registry, _, _, adapter, _, scope, _ = _fixtures()
        await registry.invoke("crypto", "submit", _order("owned", "BTC", product="linear_perpetual", strategy_id="owner-a"), permit=_permit())
        foreign = AccountScope(scope.venue, "foreign-account", scope.product, scope.environment)
        with pytest.raises((ValueError, ForeignOrderError, RouteError)):
            await registry.invoke("crypto", "cancel", "BTC", "owned", account_scope=foreign, strategy_id="owner-a", permit=_permit())
        assert adapter.mutation_count == 1
    asyncio.run(run())


def test_disable_during_adapter_await_invalidates_preexisting_risk_permit():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def pause():
            entered.set()
            await release.wait()
        registry, _, _, adapter, _, _, _ = _fixtures(crypto_before=pause)
        task = asyncio.create_task(registry.invoke(
            "crypto", "submit", _order("stale", "BTC", product="linear_perpetual"), permit=_permit()))
        await entered.wait()
        registry.disable("crypto")
        release.set()
        with pytest.raises((RouteError, AdapterProtocolError)):
            await task
        assert adapter.mutation_count == 0
    asyncio.run(run())


def test_owner_permit_source_cannot_be_reused_across_mutations():
    async def run():
        registry, _, _, adapter, _, _, _ = _fixtures()
        source = _permit()
        await registry.invoke("crypto", "submit", _order("one-use", "BTC", product="linear_perpetual"), permit=source)
        with pytest.raises((RouteError, TypeError)):
            await registry.invoke("crypto", "submit", _order("second-use", "BTC", product="linear_perpetual"), permit=source)
        assert adapter.mutation_count == 1
    asyncio.run(run())


@pytest.mark.parametrize("state", [RouteState.DISABLED, RouteState.QUARANTINED])
def test_late_fill_is_accepted_once_after_disable(state):
    async def run():
        registry, _, _, adapter, _, _, _ = _fixtures()
        await registry.invoke("crypto", "submit", _order("late", "BTC", product="linear_perpetual"), permit=_permit())
        if state is RouteState.DISABLED:
            registry.disable("crypto")
        else:
            registry.quarantine("crypto")
        first = adapter.fill("BTC", "late", quantity="1", price="10", fill_id="late-fill")
        duplicate = adapter.fill("BTC", "late", quantity="1", price="10", fill_id="late-fill")
        assert first is not None
        assert duplicate is False
        assert len((await registry.invoke("crypto", "get_fills", "BTC"))["rows"]) == 1
    asyncio.run(run())


@pytest.mark.parametrize("route_id,order", [
    ("stock", _order("bad-fok", "ABC", product="cash", tif="FOK")),
    ("stock", _order("bad-post", "ABC", product="cash", post_only=True)),
    ("stock", _order("bad-reduce", "ABC", product="cash", reduce_only=True)),
    ("stock", _order("bad-funding", "ABC", product="cash", funding=True)),
    ("crypto", _order("bad-fok", "BTC", product="linear_perpetual", tif="FOK")),
])
def test_unsupported_order_features_rejected_before_adapter_effect(route_id, order):
    async def run():
        registry, _, _, ca, sa, _, _ = _fixtures()
        with pytest.raises((ValueError, RouteError)):
            await registry.invoke(route_id, "submit", order, permit=_permit())
        assert ca.mutation_count == sa.mutation_count == 0
    asyncio.run(run())


@pytest.mark.parametrize("field,value", [
    ("product", "linear_perpetual"), ("environment", "live"), ("venue", "toss"),
    ("quantity", "NaN"), ("limit_price", "Infinity"),
])
def test_route_tuple_and_numeric_guards_reject_before_effect(field, value):
    async def run():
        registry, _, _, ca, sa, _, _ = _fixtures()
        values = {"product": "linear_perpetual"}
        values[field] = value
        order = _order("bad-" + field, "ABC", **values)
        with pytest.raises((ValueError, RouteError)):
            await registry.invoke("stock", "submit", order, permit=_permit())
        assert ca.mutation_count == sa.mutation_count == 0
    asyncio.run(run())


@pytest.mark.parametrize("fact", MANDATORY_REMOVAL_FACTS)
def test_removal_requires_each_reference_count_to_be_zero(fact):
    registry = _registry()
    route = RouteSpec("r", "dummy-stock", "cash", "cash", "sandbox", dummy_stock.DEFAULT_STOCK_SCOPE)
    adapter = dummy_stock.StockDummyAdapter()
    refs = lambda: dict(ZERO_FACTS, **{fact: 1})
    _register(registry, route, adapter, dummy_stock.STOCK_CAPABILITIES, dummy_stock.OPERATIONS, refs)
    with pytest.raises(RemovalBlocked):
        registry.remove("r")
    assert registry.state("r") is RouteState.ENABLED


@pytest.mark.parametrize("facts", [None, {"open_orders": 0},
                                    dict(ZERO_FACTS, unknown_orders=True),
                                    dict(ZERO_FACTS, open_orders=math.inf),
                                    dict(ZERO_FACTS, extra="x" * 4096)])
def test_removal_rejects_missing_malformed_or_oversized_owner_facts(facts):
    registry = _registry(max_fact_bytes=256)
    route = RouteSpec("r", "dummy-stock", "cash", "cash", "sandbox", dummy_stock.DEFAULT_STOCK_SCOPE)
    adapter = dummy_stock.StockDummyAdapter()
    provider = (lambda: facts) if facts is not None else (lambda: None)
    _register(registry, route, adapter, dummy_stock.STOCK_CAPABILITIES, dummy_stock.OPERATIONS, provider)
    with pytest.raises(RemovalBlocked):
        registry.remove("r")
    assert registry.state("r") is RouteState.ENABLED


def test_removal_blocked_by_alias_then_succeeds_only_after_unbind_and_zero_facts():
    registry, crypto, _, _, _, scope, _ = _fixtures()
    parse_execution_aliases(_alias_doc("s", "btc", crypto, scope, "BTC"), registry)
    with pytest.raises(RemovalBlocked):
        registry.remove("crypto")
    registry.unbind_alias("s", "btc")
    assert registry.remove("crypto") is True
    assert registry.state("crypto") is RouteState.REMOVED


def test_route_alias_limits_and_duplicate_canonical_tuple_fail_without_eviction():
    registry, crypto, _, _, _, scope, _ = _fixtures(max_aliases=1)
    parse_execution_aliases(_alias_doc("s", "one", crypto, scope, "BTC"), registry)
    with pytest.raises((ValueError, RouteError)):
        parse_execution_aliases(_alias_doc("s", "two", crypto, scope, "ETH"), registry)
    assert registry.alias("s", "one").symbol == "BTC"
    duplicate = RouteSpec("other", crypto.venue, crypto.market_segment, crypto.product,
                          crypto.environment, crypto.account_scope)
    with pytest.raises(ValueError):
        _register(registry, duplicate, dummy_crypto.CryptoDummyAdapter(scope),
                  dummy_crypto.CRYPTO_CAPABILITIES, dummy_crypto.OPERATIONS)
    assert registry.route("crypto") == crypto


def test_canonical_mutation_owner_normalizes_supported_segment_synonyms():
    scope = AccountScope("binance", "fixture", "linear_perpetual", "sandbox")
    first = RouteSpec("binance-linear", "binance", "linear", "linear_perpetual", "sandbox", scope)
    synonym = RouteSpec("binance-usdm", "binance", "usdm_perp", "linear_perpetual", "sandbox", scope)
    class Adapter:
        def __init__(self): self.scope = scope
        async def submit(self, order, permit): permit()
    registry = VenueRegistry(max_routes=3, max_aliases=2, max_fact_bytes=512)
    caps = Capabilities({"linear_perpetual"}, {"sandbox"}, {"GTC"}, False, True, False)
    registry.register(first, Adapter(), {"submit": "submit"}, caps, lambda: dict(ZERO_FACTS))
    with pytest.raises(ValueError, match="Duplicate canonical"):
        registry.register(synonym, Adapter(), {"submit": "submit"}, caps, lambda: dict(ZERO_FACTS))


def test_native_testnet_flag_must_match_route_environment():
    scope = AccountScope("binance", "fixture", "linear_perpetual", "sandbox")
    route = RouteSpec("binance-sandbox", "binance", "usdm_perp", "linear_perpetual", "sandbox", scope)
    class Adapter:
        use_testnet = False
        def __init__(self): self.scope = scope
        async def submit(self, order, permit): permit()
    registry = VenueRegistry(max_routes=1, max_aliases=1, max_fact_bytes=512)
    caps = Capabilities({"linear_perpetual"}, {"sandbox"}, {"GTC"}, False, True, False)
    with pytest.raises(ValueError, match="testnet state"):
        registry.register(route, Adapter(), {"submit": "submit"}, caps, lambda: dict(ZERO_FACTS))


def test_existing_cash_adapter_name_and_class_cannot_be_relabelled_as_another_venue_or_product():
    from execution.brokers import KiwoomBrokerAdapter

    registry = VenueRegistry(max_routes=2, max_aliases=1, max_fact_bytes=512)
    adapter = KiwoomBrokerAdapter.__new__(KiwoomBrokerAdapter)
    adapter.use_mock = True
    other_scope = AccountScope("bybit", "fixture", "cash", "mock")
    other_route = RouteSpec("wrong-venue", "bybit", "cash", "cash", "mock", other_scope)
    caps = Capabilities({"cash"}, {"mock"}, {"GTC"}, False, False, False)
    with pytest.raises(ValueError, match="venue name"):
        registry.register(other_route, adapter, {"account_state": "account_state"},
                          caps, lambda: dict(ZERO_FACTS))

    perp_scope = AccountScope("kiwoom", "fixture", "linear_perpetual", "sandbox")
    perp_route = RouteSpec("wrong-product", "kiwoom", "linear", "linear_perpetual",
                           "sandbox", perp_scope)
    adapter.use_mock = False
    perp_caps = Capabilities({"linear_perpetual"}, {"sandbox"}, {"GTC"}, False, False, False)
    with pytest.raises(ValueError, match="cash adapter"):
        registry.register(perp_route, adapter, {"account_state": "account_state"},
                          perp_caps, lambda: dict(ZERO_FACTS))


def test_submit_requires_explicit_strategy_attribution_before_dispatch():
    async def run():
        registry, _, _, adapter, _, _, _ = _fixtures()
        unowned = _order("no-strategy", "BTC", product="linear_perpetual")
        unowned.pop("strategy_id")
        with pytest.raises(ValueError, match="strategy_id"):
            await registry.invoke("crypto", "submit", unowned, permit=_permit())
        assert adapter.mutation_count == 0
    asyncio.run(run())


@pytest.mark.parametrize("adapter_cls,scope,product", [
    (dummy_crypto.CryptoDummyAdapter, dummy_crypto.DEFAULT_CRYPTO_SCOPE, "linear_perpetual"),
    (dummy_stock.StockDummyAdapter, dummy_stock.DEFAULT_STOCK_SCOPE, "cash"),
])
def test_fixture_capacity_rejects_before_order_fill_or_event_side_effects(adapter_cls, scope, product):
    async def run():
        adapter = adapter_cls(scope, max_orders=1, max_fills=1, max_events=2)
        await adapter.submit_with_permit(_order("one", "X", product=product), _permit())
        replacement = _order("replacement", "X", product=product, action="REPLACE")
        with pytest.raises(ValueError, match="NEW only"):
            await adapter.submit_with_permit(replacement, _permit())
        assert adapter.mutation_count == 1
        with pytest.raises(ValueError):
            await adapter.submit_with_permit(_order("two", "Y", product=product), _permit())
        assert adapter.mutation_count == 1
        assert adapter.fill("X", "one", quantity="1", price="2", fill_id="fill-one") is True
        before_long_id = (adapter.mutation_count, adapter.event_count, len(adapter.orders))
        with pytest.raises(ValueError, match="200 UTF-8 bytes"):
            adapter.fill("X", "one", quantity="1", price="2", fill_id="f" * 201)
        assert (adapter.mutation_count, adapter.event_count, len(adapter.orders)) == before_long_id
        before = (adapter.mutation_count, adapter.event_count, len(adapter.orders))
        with pytest.raises(ValueError):
            adapter.fill("X", "one", quantity="1", price="2", fill_id="fill-two")
        assert (adapter.mutation_count, adapter.event_count, len(adapter.orders)) == before
        assert adapter.fill("X", "one", quantity="9", price="99", fill_id="fill-one") is False
    asyncio.run(run())


@pytest.mark.parametrize("adapter_cls,scope,product", [
    (dummy_crypto.CryptoDummyAdapter, dummy_crypto.DEFAULT_CRYPTO_SCOPE, "linear_perpetual"),
    (dummy_stock.StockDummyAdapter, dummy_stock.DEFAULT_STOCK_SCOPE, "cash"),
])
def test_synthetic_fixtures_never_allow_live_even_if_scope_says_live(adapter_cls, scope, product):
    async def run():
        live_scope = AccountScope(scope.venue, scope.account, scope.product, "live")
        adapter = adapter_cls(live_scope)
        with pytest.raises(ValueError, match="LIVE"):
            await adapter.submit_with_permit(_order("live", "X", product=product), _permit())
        assert adapter.mutation_count == 0 and adapter.orders == {}
    asyncio.run(run())


def test_execution_only_alias_is_usable_without_feed_and_missing_feed_is_local():
    registry, crypto, stock, _, _, cs, ss = _fixtures()
    parse_execution_aliases({
        "s": {
            "needs-feed": _alias("s", "needs-feed", crypto, cs, "BTC", required_feeds=("btc-feed",)),
            "execution-only": _alias("s", "execution-only", stock, ss, "ABC"),
        },
    }, registry)
    statuses = {item["alias"]: item for item in registry.alias_status(())}
    assert statuses["needs-feed"]["enabled"] is False
    assert statuses["needs-feed"]["missing_feeds"] == ("btc-feed",)
    assert statuses["execution-only"]["enabled"] is True
    assert registry.alias_status(("btc-feed",))[0]["enabled"] is True


def test_alias_status_tracks_route_lifecycle_and_removed_routes_reject_aliases():
    registry, _, stock, _, _, _, scope = _fixtures()
    parse_execution_aliases(_alias_doc("s", "cash", stock, scope, "ABC"), registry)
    registry.disable("stock")
    enabled = {row["route_id"]: row["enabled"] for row in registry.alias_status()}
    assert enabled["stock"] is False
    registry.unbind_alias("s", "cash")
    assert registry.remove("stock") is True
    from execution.venues.registry import AliasBinding
    item = AliasBinding("s", "cash", "stock", "ABC", "cash", "cash", "sandbox", scope)
    with pytest.raises(RouteStateError, match="REMOVED"):
        registry.register_aliases((item,))


def test_actual_adapter_maps_preserve_honest_gaps_and_native_route_spellings():
    # Import concrete classes only for this explicit binding proof; constructors
    # with clients, credentials, or external accounts are never invoked.
    from execution.brokers import KiwoomBrokerAdapter
    from execution.kis_execution import KisCashBroker
    from execution.perp_execution import BinancePerpAdapter

    assert RouteSpec("kis-mock", "kis", "krx_cash", "cash", "mock",
                     AccountScope("kis", "fixture", "cash", "mock"))
    assert RouteSpec("kis-mock2", "kis", "krx_cash", "cash", "mock2",
                     AccountScope("kis", "fixture2", "cash", "mock2"))
    assert RouteSpec("kiwoom-mock", "kiwoom", "krx_cash", "cash", "mock",
                     AccountScope("kiwoom", "fixture", "cash", "mock"))
    assert RouteSpec("binance-usdm", "binance", "usdm_perp", "linear_perpetual", "sandbox",
                     AccountScope("binance", "fixture", "linear_perpetual", "sandbox"))

    for cls, fragment in ((KiwoomBrokerAdapter, "KiwoomBrokerAdapter"),
                          (KisCashBroker, "KisCashBroker"),
                          (BinancePerpAdapter, "BinancePerpAdapter")):
        owner, operations, gaps = existing_adapter_contract(cls.__new__(cls))
        assert owner.endswith(fragment)
        assert operations
        if cls in (KiwoomBrokerAdapter, KisCashBroker):
            assert gaps
        if cls is KiwoomBrokerAdapter:
            assert any("NOT_IMPLEMENTED" in gap or "unsupported" in gap for gap in gaps)
        if cls is BinancePerpAdapter:
            assert "submit" in operations and "cancel" in operations


def test_existing_binance_adapter_uses_registry_permit_with_scripted_transport():
    from execution.perp_execution import BinancePerpAdapter, HttpResponse, PerpScope

    class ScriptedTransport:
        mutation_permit_protocol = 1
        def __init__(self): self.calls = []
        async def request(self, method, url, *, params=None, json_body=None, data=None,
                          headers=None, before_send=None):
            if before_send is not None:
                before_send()
            self.calls.append((method, url))
            if method == "POST":
                body = {"symbol": "BTCUSDT", "orderId": 101, "clientOrderId": "native-1",
                        "status": "NEW", "executedQty": "0"}
            else:
                body = {"symbol": "BTCUSDT", "orderId": 101, "clientOrderId": "native-1",
                        "status": "CANCELED"}
            return HttpResponse(200, body)

    async def run():
        perp_scope = PerpScope("binance", "linear_perpetual", "sandbox", "fixture", "USDT")
        account_scope = AccountScope("binance", "fixture", "linear_perpetual", "sandbox")
        route = RouteSpec("native-binance", "binance", "usdm_perp", "linear_perpetual",
                          "sandbox", account_scope)
        transport = ScriptedTransport()
        adapter = BinancePerpAdapter(perp_scope, transport, api_key="synthetic-key",
                                     api_secret="synthetic-secret")
        adapter._metadata["BTCUSDT"] = {
            "lot_size": "0.001", "tick_size": "0.1", "min_quantity": "0.001",
            "min_notional": "5",
        }

        async def cancel_native(request, permit):
            return await adapter.cancel_with_permit(request["symbol"],
                request["target_order_id"], permit)

        registry = VenueRegistry(max_routes=2, max_aliases=2, max_fact_bytes=512)
        caps = Capabilities({"linear_perpetual"}, {"sandbox"}, {"GTC", "IOC"},
                            False, True, False)
        registry.register(route, adapter,
            {"submit": "submit_with_permit", "cancel": cancel_native}, caps,
            lambda: dict(ZERO_FACTS))
        request = {
            "client_order_id": "native-1", "symbol": "BTCUSDT", "side": "buy",
            "quantity": "0.1", "limit_price": "100", "product": "linear_perpetual",
            "venue": "binance", "environment": "sandbox", "time_in_force": "GTC",
            "reduce_only": False, "post_only": False, "action": "NEW",
            "strategy_id": "strategy", "account_scope": account_scope,
            "scope_key": perp_scope.key,
        }
        submitted = await registry.invoke("native-binance", "submit", request, permit=_permit())
        assert submitted["client_order_id"] == "native-1"
        await registry.invoke("native-binance", "cancel", {
            "target_order_id": "native-1", "symbol": "BTCUSDT",
            "strategy_id": "strategy", "account_scope": account_scope,
        }, permit=_permit())
        assert [method for method, _ in transport.calls] == ["POST", "DELETE"]

    asyncio.run(run())


def test_local_runtime_plan_type_and_legacy_entrypoint_remain_available():
    assert inspect.isclass(LocalRuntimePlan)
    assert callable(LocalRuntimePlan.load)
    assert callable(LocalRuntimePlan.from_document)
    assert callable(parse_execution_aliases)


def test_timefolio_is_not_implicitly_registered_or_aliased():
    registry, _, _, _, _, _, _ = _fixtures()
    assert registry.snapshot()["routes"] == (
        ("crypto", "ENABLED", 1, 0), ("stock", "ENABLED", 1, 0))
    assert registry.alias_status() == ()
    assert not any("timefolio" in binding.canonical_owner.casefold()
                   for binding in (registry.binding("crypto"), registry.binding("stock")))

