"""Read-only current-cut available-balance facts in actual Owner feedback."""
from decimal import Decimal

import pytest

from core.models import AssetClass, OrderDestination, OrderIntent, Side, Venue
from execution.execution_ledger import AccountScope
from execution.perp_execution import BinancePerpAdapter, PerpScope
from execution.trading_contracts import ActionKind, ExecutionAction, StrategyActionPlan, TradingMode
from execution.venues.registry import AliasBinding, Capabilities, RouteSpec, VenueRegistry
from test_exec01_native import (
    NOW, ZERO_FACTS, ScriptedPerpTransport, _perp_native_facts,
    action, evidence, intent, make_owner, open_gates, readiness,
)


def feedback_fixture(root, *, post_submit_mode=None, include_local_cut=True):
    native_id = "qa01-feedback-binance-perp"
    strategy = "alpha"
    scope = AccountScope("binance", "qa01-feedback-account", "linear_perpetual", "sandbox")
    route = RouteSpec("qa01-feedback-route", "binance", "usdm_perp",
                      "linear_perpetual", "sandbox", scope, native_id)
    transport = ScriptedPerpTransport()
    adapter = BinancePerpAdapter(
        PerpScope("binance", "linear_perpetual", "sandbox", scope.account, "USDT"),
        transport, api_key="fixture-only-key", api_secret="fixture-only-secret",
    )
    adapter._metadata["BTCUSDT"] = {
        "lot_size": "0.001", "tick_size": "0.1", "min_quantity": "0.001",
        "min_notional": "5", "multiplier": "1",
    }
    owner = make_owner(root, scope, native_id)
    registry = VenueRegistry(max_routes=2, max_aliases=4, max_fact_bytes=4096)
    capabilities = Capabilities(
        {"linear_perpetual"}, {"sandbox"}, {"GTC", "IOC", "FOK", "POST_ONLY"},
        True, True, False,
    )
    state = {"post_submit_mode": post_submit_mode, "calls": 0, "race_action": None,
             "raced": False, "provided_cut": 0, "include_local_cut": include_local_cut}
    def facts_provider(symbol):
        assert symbol == "BTCUSDT"
        state["calls"] += 1
        local_cut = state["provided_cut"]
        facts = _perp_native_facts(adapter, native_id)
        if state["include_local_cut"]:
            facts["account_facts"]["local_ledger_version"] = local_cut
        mode = state["post_submit_mode"] if state["calls"] >= 3 else None
        if mode == "wrong_scope":
            facts["account_facts"]["normalized"]["scope_key"] = "other-scope"
        elif mode == "wrong_currency":
            facts["account_facts"]["normalized"]["currency"] = "EUR"
        elif mode == "stale":
            facts["account_facts"]["observed_ns"] = NOW - 1_000_000_001
        elif mode == "wrong_cut":
            facts["account_facts"]["local_ledger_version"] = local_cut - 1
        elif mode == "exception":
            raise RuntimeError("synthetic read fixture unavailable")
        elif mode == "async":
            async def unsupported_async_read():
                return facts
            return unsupported_async_read()
        elif mode == "race" and not state["raced"]:
            state["raced"] = True
            owner.apply_fill(
                state["race_action"], fill_id="qa01-feedback-race-fill",
                quantity="0.01", price="100", fee="0.01", order_id="101",
            )
        return facts

    registry.bind_existing_adapter(
        route, adapter, reference_provider=lambda: dict(ZERO_FACTS),
        capabilities=capabilities, native_facts_provider=facts_provider,
    )
    registry.register_aliases((AliasBinding(
        strategy, "qa01-btc-perp", route.route_id, "BTCUSDT", "usdm_perp",
        "linear_perpetual", "sandbox", scope,
    ),))
    order = intent(
        "qa01-feedback-order", venue=Venue.BINANCE, asset=AssetClass.CRYPTO_PERP,
        symbol="BTCUSDT", account=scope.account, environment=scope.environment,
        quantity="0.1", price="100",
    )
    order = order.model_copy(update={
        "metadata": {**order.metadata, "reserve_unit_price": "100"},
    })
    new = action("qa01-feedback-new", scope, order, mode=TradingMode.SANDBOX)
    state["race_action"] = new
    open_gates(owner, strategy)
    return {
        "owner": owner, "registry": registry, "adapter": adapter,
        "transport": transport, "scope": scope, "strategy": strategy,
        "route": route,
        "order": order, "new": new, "facts_state": state,
    }


def order_plan(fixture):
    return StrategyActionPlan(
        "qa01-feedback-plan", fixture["strategy"], "cfg-1", NOW,
        (fixture["new"],),
    )


def run_readiness(fixture):
    return readiness(fixture["owner"], fixture["scope"], fixture["order"].instrument.key)


def no_order_event_mutation(*_args):
    return None


@pytest.mark.asyncio
async def test_actual_binance_owner_feedback_exposes_current_cut_available_only(tmp_path):
    fixture = feedback_fixture(tmp_path)
    owner, callback_rows = fixture["owner"], []
    callback = lambda event, context: callback_rows.append((event, context))
    try:
        sent = await owner.dispatch_plan(
            order_plan(fixture), registry=fixture["registry"], readiness=run_readiness(fixture),
            evidence=evidence(owner, fixture["strategy"], "sandbox"), on_event=callback,
        )
        assert sent["results"][0]["status"] == "SUBMITTED"
        event, context = callback_rows.pop(0)
        execution = context.execution
        assert event.status == "ACCEPTED"
        assert execution["native_available_balances"] == {"USDT": "1000000"}
        assert execution["native_available_quality"] == "VALID"
        assert execution["native_available_observed_ns"] == NOW
        assert execution["native_available_local_ledger_version"] == 0
        assert execution["native_physical_position"] is None
        assert execution["native_physical_position_quality"] == "UNAVAILABLE"
        assert context.data is None
        with pytest.raises(TypeError):
            execution["native_available_balances"]["USDT"] = "0"
        with pytest.raises(TypeError):
            execution["native_available_balances"]["BTC"] = "1"
        assert not ({"raw", "native_identity", "api_key", "api_secret", "actor"} & set(execution))
        assert len(fixture["transport"].calls) == 1

        assert owner.apply_fill(
            fixture["new"], fill_id="qa01-feedback-partial", quantity="0.04",
            price="100", fee="0.04", order_id="101",
        )
        await owner.deliver_feedback(fixture["strategy"], callback)
        partial_event, partial_context = callback_rows.pop(0)
        assert partial_event.status == "PARTIAL_FILL"
        assert partial_context.execution["account_version"] == 1
        assert partial_context.execution["native_available_quality"] == "UNAVAILABLE"
        assert "native_available_balances" not in partial_context.execution
        assert partial_context.execution["native_physical_position"] is None

        # A fresh exact local-cut provider snapshot may restore only its balance
        # fact on the next distinct feedback. It does not claim physical position.
        assert owner.apply_fill(
            fixture["new"], fill_id="qa01-feedback-refreshed-fill", quantity="0.01",
            price="100", fee="0.01", order_id="101",
        )
        fixture["facts_state"]["provided_cut"] = owner.ledger.account_version()
        await owner.deliver_feedback(fixture["strategy"], callback)
        refreshed_event, refreshed_context = callback_rows.pop(0)
        assert refreshed_event.status == "PARTIAL_FILL"
        assert refreshed_context.execution["native_available_balances"] == {"USDT": "1000000"}
        assert refreshed_context.execution["native_available_local_ledger_version"] == 2
        assert refreshed_context.execution["native_physical_position_quality"] == "UNAVAILABLE"
        assert len(fixture["transport"].calls) == 1
    finally:
        await owner.close()


@pytest.mark.parametrize(
    ("mode", "expected_quality"),
    [("wrong_scope", "UNAVAILABLE"), ("wrong_currency", "UNAVAILABLE"),
     ("stale", "UNAVAILABLE"), ("wrong_cut", "UNAVAILABLE"),
     ("exception", "UNAVAILABLE"), ("async", "UNAVAILABLE")],
)
@pytest.mark.asyncio
async def test_feedback_fact_provider_failures_are_read_only_and_unavailable(
    tmp_path, mode, expected_quality,
):
    fixture = feedback_fixture(tmp_path, post_submit_mode=mode)
    owner, callback_rows = fixture["owner"], []
    callback = lambda event, context: callback_rows.append((event, context))
    try:
        result = await owner.dispatch_plan(
            order_plan(fixture), registry=fixture["registry"], readiness=run_readiness(fixture),
            evidence=evidence(owner, fixture["strategy"], "sandbox"), on_event=callback,
        )
        assert result["results"][0]["status"] == "SUBMITTED"
        event, context = callback_rows.pop(0)
        assert event.status == "ACCEPTED"
        assert context.execution["native_available_quality"] == expected_quality
        assert "native_available_balances" not in context.execution
        assert context.execution["native_physical_position"] is None
        assert len(fixture["transport"].calls) == 1
        assert fixture["transport"].calls[0][0] == "POST"
        assert len(fixture["transport"].calls) == 1
        assert owner.ledger.get(fixture["order"].intent_id)["state"] == "SUBMITTED"
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_feedback_rejects_local_ledger_cut_changed_inside_read_provider(tmp_path):
    fixture = feedback_fixture(tmp_path, post_submit_mode="race")
    owner, callback_rows = fixture["owner"], []
    callback = lambda event, context: callback_rows.append((event, context))
    try:
        await owner.dispatch_plan(
            order_plan(fixture), registry=fixture["registry"], readiness=run_readiness(fixture),
            evidence=evidence(owner, fixture["strategy"], "sandbox"), on_event=callback,
        )
        event, context = callback_rows.pop(0)
        assert event.status == "ACCEPTED"
        assert fixture["facts_state"]["raced"] is True
        assert context.execution["native_available_quality"] == "UNAVAILABLE"
        assert "native_available_balances" not in context.execution
        assert context.execution["account_version"] == 1
        assert context.execution["filled_quantity"] == "0.01"
        assert context.execution["remaining_quantity"] == "0.09"
        assert context.execution["account_journal_position"] == "0.01"
        assert len(fixture["transport"].calls) == 1
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_feedback_rejects_registered_route_rebound_to_another_native_account(tmp_path):
    fixture = feedback_fixture(tmp_path)
    owner, callback_rows = fixture["owner"], []
    callback = lambda event, context: callback_rows.append((event, context))
    try:
        await owner.dispatch_plan(
            order_plan(fixture), registry=fixture["registry"], readiness=run_readiness(fixture),
            evidence=evidence(owner, fixture["strategy"], "sandbox"), on_event=callback,
        )
        event, context = callback_rows.pop(0)
        assert event.status == "ACCEPTED"
        assert context.execution["native_available_balances"] == {"USDT": "1000000"}

        route = fixture["route"]
        foreign_route = RouteSpec(
            route.route_id, route.venue, route.market_segment, route.product,
            route.environment, route.account_scope, "other-native-account",
        )
        provider_calls = []
        foreign_registry = VenueRegistry(max_routes=2, max_aliases=2, max_fact_bytes=4096)
        foreign_registry.bind_existing_adapter(
            foreign_route, fixture["adapter"],
            reference_provider=lambda: dict(ZERO_FACTS),
            capabilities=Capabilities(
                {"linear_perpetual"}, {"sandbox"}, {"GTC", "IOC", "FOK", "POST_ONLY"},
                True, True, False,
            ),
            native_facts_provider=lambda symbol: provider_calls.append(symbol),
        )
        owner._source_dispatch[fixture["strategy"]]["registry"] = foreign_registry
        assert owner.apply_fill(
            fixture["new"], fill_id="qa01-feedback-rebound-fill", quantity="0.01",
            price="100", fee="0.01", order_id="101",
        )
        await owner.deliver_feedback(fixture["strategy"], callback)
        _, rebound_context = callback_rows.pop(0)
        assert rebound_context.execution["native_available_quality"] == "UNAVAILABLE"
        assert "native_available_balances" not in rebound_context.execution
        assert provider_calls == []
        assert len(fixture["transport"].calls) == 1
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_legacy_native_facts_without_local_cut_still_submit_but_feedback_is_unavailable(tmp_path):
    fixture = feedback_fixture(tmp_path, include_local_cut=False)
    owner, callback_rows = fixture["owner"], []
    callback = lambda event, context: callback_rows.append((event, context))
    try:
        result = await owner.dispatch_plan(
            order_plan(fixture), registry=fixture["registry"], readiness=run_readiness(fixture),
            evidence=evidence(owner, fixture["strategy"], "sandbox"), on_event=callback,
        )
        assert result["results"][0]["status"] == "SUBMITTED"
        _, context = callback_rows.pop(0)
        assert context.execution["native_available_quality"] == "UNAVAILABLE"
        assert "native_available_balances" not in context.execution
        assert len(fixture["transport"].calls) == 1
    finally:
        await owner.close()
