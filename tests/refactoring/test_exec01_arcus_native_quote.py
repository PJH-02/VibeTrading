"""Credential-free Arcus USD-quote / USDG-settlement proof tests."""
from copy import deepcopy
from decimal import Decimal, localcontext

import pytest

from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
from execution.execution_ledger import AccountScope, ExecutionLedger
from execution.execution_owner import ExecutionBudgets, ExecutionOwner
from execution.execution_owner import GateBlocked
from execution.perp_execution import ArcusPerpAdapter, CapabilityBlocked, PerpScope
from execution.trading_contracts import validate_native_account_facts
from execution.trading_contracts import ActionKind, ExecutionAction, StrategyActionPlan, TradingMode
from execution.venues.registry import AliasBinding, Capabilities, RouteSpec, VenueRegistry
from runtime.runtime_control import RuntimeControl
from test_exec01_native import NOW, evidence, open_gates, readiness
from test_perp_execution import ADDRESS, NOW_NS, ScriptedTransport, arcus_adapter, arcus_market, arcus_signer


SCOPE = PerpScope("arcus", "linear_perpetual", "sandbox", "local-alias", "USDG", "2")
IDENTITY = {"scope_key": SCOPE.key, "native_account_id": ADDRESS,
            "authenticated_session_id": "independent-session", "source": "authenticated_native_session",
            "permissions": ("account_read", "trade")}


def account(*, positions=None, **changes):
    row = {"address": ADDRESS, "accountIndex": 2, "sequenceNumber": 7,
           "equity": "1000", "freeCollateral": "800", "marginState": "healthy",
           "netDepositsByAsset": [{"spotAssetId": 0, "spotAssetDisplayName": "USDG", "netAmount": "1000"}],
           "pendingDeposits": "0", "pendingWithdrawals": "0",
           "positions": positions or {}}
    row.update(changes)
    return row


def market(**changes):
    row = arcus_market(initialMarginFraction="0.1", maintenanceMarginFraction="0.04",
                       offHoursInitialMarginFraction="0.2")
    row.update(changes)
    return row


def envelope(a, m=None, l=None):
    m = [m or market()]
    l = l or [{"marketId": 1, "marketDisplayName": "BTC-USD", "leverage": 5,
               "isolated": False, "marginMode": "CROSS"}]
    normalized = {"scope_key": SCOPE.key, "venue": "arcus", "product": "linear_perpetual",
                  "environment": "sandbox", "equity": a["equity"], "available": a["freeCollateral"],
                  "currency": "USD", "settlement_currency": "USDG", "initial_margin": "200",
                  "maintenance_margin": "0", "account_sequence": a["sequenceNumber"], "raw": a}
    return {"normalized": normalized, "observed_ns": NOW_NS - 1, "account_version": "arcus:7",
            "local_ledger_version": 0,
            "reconciliation_state": "RECONCILED", "unknown": False, "position_mode": "net",
            "margin_mode": "cross", "native_identity": IDENTITY,
            "arcus_quote_proof": {"account": a, "markets": m, "leverages": l}}


def product(**changes):
    row = {"scope_key": SCOPE.key, "venue": "arcus", "product": "linear_perpetual",
           "environment": "sandbox", "symbol": "BTC-USD", "kind": "linear_perpetual",
           "multiplier": "1", "tick_size": "0.1", "lot_size": "0.001", "min_quantity": "0.001",
           "min_notional": "10", "base_currency": "BTC", "quote_currency": "USD",
           "settlement_currency": "USDG", "collateral_currency": "USDG", "position_mode": "net",
           "margin_mode": "cross", "raw": market()}
    row.update(changes)
    return row


def validate(facts, prod=None):
    return validate_native_account_facts(facts, {"BTC-USD": prod or product()}, SCOPE,
        account_version="arcus:7", expected_native_account_id=ADDRESS,
        now_ns=NOW_NS, max_age_ns=100)


def test_arcus_quote_proof_keeps_usd_valuation_separate_from_usdg_and_recomputes():
    pos = {"1": {"address": ADDRESS, "accountIndex": 2, "marketId": 1,
        "marketDisplayName": "BTC-USD", "size": "2", "positionValueNotional": "20000",
        "markPx": "10000", "averageEntryPrice": "9000", "marginMode": "CROSS", "side": "LONG"}}
    a = account(positions=pos, freeCollateral="200")
    f = envelope(a)
    f["normalized"].update(equity="1000", available="200", initial_margin="800", maintenance_margin="800")
    result = validate(f)
    assert result["quality"] == "VALID", result["reason"]
    facts = result["facts"]
    assert facts["currency"] == facts["valuation_currency"] == "USD"
    assert facts["settlement_currency"] == facts["collateral_currency"] == "USDG"
    assert facts["reported"]["initial_margin"] == Decimal("800")  # exact native equity - free collateral
    assert facts["reported"]["maintenance_margin"] == Decimal("800")  # 20000 x .04
    assert facts["initial_margin_basis"] == "DOCUMENTED_NATIVE_EQUITY_MINUS_FREE_COLLATERAL"
    assert facts["maintenance_margin_basis"] == "DOCUMENTED_DERIVED_NATIVE_REQUIREMENT"


@pytest.mark.parametrize("bad", [
    {"markPx": "0"}, {"markPx": "10000.01"}, {"size": "2.0", "positionValueNotional": "1"},
    {"marginMode": "UNKNOWN"}, {"address": "0x" + "22" * 20},
])
def test_hostile_native_position_data_fails_closed(bad):
    row = {"address": ADDRESS, "accountIndex": 2, "marketId": 1, "marketDisplayName": "BTC-USD",
           "size": "2", "positionValueNotional": "20000", "markPx": "10000",
           "averageEntryPrice": "9000", "marginMode": "CROSS", "side": "LONG"}
    row.update(bad)
    facts = envelope(account(positions={"1": row}))
    assert validate(facts)["quality"] == "UNAVAILABLE"


def test_arcus_unproven_legacy_flags_wrong_currency_and_forgery_fail_closed():
    f = envelope(account())
    assert validate({k: v for k, v in f.items() if k != "arcus_quote_proof"})["quality"] == "UNAVAILABLE"
    f = envelope(account())
    f["normalized"]["available"] = "801"
    assert validate(f)["quality"] == "UNAVAILABLE"
    assert validate(envelope(account()), product(quote_currency="USDG"))["quality"] == "UNAVAILABLE"


@pytest.mark.parametrize("change", [
    {"loanBalance": "0", "unsettledInterest": "0", "borrowHeadroom": "0"},
    {"netDepositsByAsset": [{"spotAssetId": 0, "netAmount": "100"},
                             {"spotAssetId": 4, "netAmount": "0"}]},
    {"positions": None},
])
def test_lending_multiaset_or_incomplete_position_facts_are_unavailable(change):
    a = account()
    a.update(change)
    assert validate(envelope(a))["quality"] == "UNAVAILABLE"


def test_arcus_account_sequence_and_observation_version_are_bound():
    f = envelope(account())
    f["account_version"] = "arcus:6"
    assert "version" in validate(f)["reason"]
    f = envelope(account())
    f["observed_ns"] = NOW_NS + 1
    assert "future" in validate(f)["reason"]


def test_spot_positions_without_other_lending_fields_are_not_quote_only():
    assert validate(envelope(account(spotPositions={"3": {"size": "1", "value": "1000000"}})))["quality"] == "UNAVAILABLE"


@pytest.mark.parametrize("change", [
    {"status": "OFFLINE"}, {"maintenanceMarginFraction": None},
    {"initialMarginFraction": "1.2"}, {"quoteAsset": "USDC"},
])
def test_flat_market_native_parameters_are_validated_before_new_risk(change):
    m = market(**change)
    assert validate(envelope(account(), m=m), product(raw=m))["quality"] == "UNAVAILABLE"


@pytest.mark.parametrize("change", [
    {"isolated": True}, {"marketDisplayName": "ETH-USD"}, {"leverage": "0"},
])
def test_flat_native_leverage_identity_mode_and_value_are_exact(change):
    l = {"marketId": 1, "marketDisplayName": "BTC-USD", "leverage": 5,
         "isolated": False, "marginMode": "CROSS"}
    l.update(change)
    assert validate(envelope(account(), l=[l]))["quality"] == "UNAVAILABLE"


@pytest.mark.parametrize("change", [
    {"multiplier": "0.1"}, {"tick_size": "0.00001"}, {"base_currency": "ETH"},
])
def test_product_fields_cannot_disagree_with_preserved_native_raw(change):
    assert validate(envelope(account()), product(**change))["quality"] == "UNAVAILABLE"


def test_quote_only_account_without_optional_margin_state_does_not_invent_health():
    a = account()
    a.pop("marginState")
    result = validate(envelope(a))
    assert result["quality"] == "VALID"
    assert result["facts"]["native_margin_state"] is None


@pytest.mark.parametrize("bad_value", ["9" * 39, "1e-39", "1e+100"])
def test_arcus_decimal_envelope_is_enforced_before_native_arithmetic(bad_value):
    a = account(equity=bad_value)
    facts = envelope(a)
    facts["normalized"]["equity"] = bad_value
    assert validate(facts)["quality"] == "UNAVAILABLE"


def test_low_decimal_context_does_not_change_native_fraction_arithmetic():
    pos = {"1": {"address": ADDRESS, "accountIndex": 2, "marketId": 1, "marketDisplayName": "BTC-USD",
                  "size": "3", "positionValueNotional": "333.3333333333333333333333333333",
                  "markPx": "111.1111111111111111111111111111", "averageEntryPrice": "100", "marginMode": "CROSS", "side": "LONG"}}
    a = account(positions=pos, equity="1000", freeCollateral="900")
    f = envelope(a)
    f["normalized"].update(equity="1000", available="900", initial_margin="100",
                           maintenance_margin="13.333333333333333333333333333332")
    with localcontext() as ctx:
        ctx.prec = 3
        result = validate(f)
    assert result["quality"] == "VALID", result["reason"]


def test_adapter_gathers_actual_double_cut_and_retains_legacy_wire():
    a = account()
    responses = [a, {"markets": [market()]}, {"leverages": [{"marketId": 1, "marketDisplayName": "BTC-USD",
        "leverage": 5, "isolated": False, "marginMode": "CROSS"}]}, deepcopy(a), deepcopy(a)]
    transport = ScriptedTransport(*responses)
    adapter, _ = arcus_adapter(transport)
    identity = dict(IDENTITY, scope_key=adapter.scope.key)
    result = __import__("asyncio").run(adapter.native_risk_snapshot("BTC-USD", native_identity=identity,
        local_ledger_version=0, max_age_ns=10_000_000_000))
    assert len(transport.calls) == 4 and all(call["method"] == "GET" for call in transport.calls)
    assert result["account_facts"]["normalized"]["currency"] == "USD"
    assert result["account_facts"]["normalized"]["settlement_currency"] == "USDG"
    assert result["account_facts"]["arcus_quote_proof"]["decoded"]["quote_currency"] == "USD"
    legacy = __import__("asyncio").run(adapter.account_state()) if transport.responses else None
    # Legacy method still has its original two currency labels / unavailable totals.
    assert legacy is not None and legacy["currency"] == "USD" and legacy["settlement_currency"] == "USDG"
    assert legacy["initial_margin"] is None and legacy["maintenance_margin"] is None


@pytest.mark.asyncio
async def test_adapter_rejects_changed_account_cut_between_reads():
    first = account()
    changed = account(sequenceNumber=8)
    transport = ScriptedTransport(first, {"markets": [market()]},
        {"leverages": [{"marketId": 1, "marketDisplayName": "BTC-USD", "leverage": 5,
                        "isolated": False, "marginMode": "CROSS"}]}, changed)
    adapter, _ = arcus_adapter(transport)
    identity = dict(IDENTITY, scope_key=adapter.scope.key)
    with pytest.raises(CapabilityBlocked, match="economic cut changed"):
        await adapter.native_risk_snapshot("BTC-USD", native_identity=identity,
            local_ledger_version=0, max_age_ns=10_000_000_000)


@pytest.mark.asyncio
async def test_adapter_bounds_oldest_snapshot_observation_span():
    a = account()
    transport = ScriptedTransport(a, {"markets": [market()]},
        {"leverages": [{"marketId": 1, "marketDisplayName": "BTC-USD", "leverage": 5,
                        "isolated": False, "marginMode": "CROSS"}]}, deepcopy(a))
    times = iter([NOW_NS, NOW_NS + 20])
    signer, _ = arcus_signer()
    scope = PerpScope("arcus", "linear_perpetual", "sandbox", "synthetic-account", "USDG", "2")
    adapter = ArcusPerpAdapter(scope, transport, account_address=ADDRESS, signer=signer,
                               clock_ns=lambda: next(times))
    identity = dict(IDENTITY, scope_key=adapter.scope.key)
    with pytest.raises(CapabilityBlocked, match="read span"):
        await adapter.native_risk_snapshot("BTC-USD", native_identity=identity,
            local_ledger_version=0, max_age_ns=10)


@pytest.mark.asyncio
@pytest.mark.parametrize(("available", "submitted"), [("1000", True), ("20", False)])
async def test_actual_arcus_adapter_registry_owner_plan_dispatches_with_native_quote_proof(tmp_path, available, submitted):
    a = account(freeCollateral=available)
    responses = [a, {"markets": [market()]},
        {"leverages": [{"marketId": 1, "marketDisplayName": "BTC-USD", "leverage": 5,
                        "isolated": False, "marginMode": "CROSS"}]}, deepcopy(a)]
    if submitted:
        responses.append({"status": 200, "result": {"orderId": "arcus-proof-accepted"}})
        updated = account(sequenceNumber=8, equity="1000", freeCollateral="950", positions={"1": {
            "address": ADDRESS, "accountIndex": 2, "marketId": 1, "marketDisplayName": "BTC-USD",
            "size": "0.001", "positionValueNotional": "50.0001", "markPx": "50000.1",
            "averageEntryPrice": "50000.1", "marginMode": "CROSS", "side": "LONG"}})
        responses.extend([updated, {"markets": [market()]},
            {"leverages": [{"marketId": 1, "marketDisplayName": "BTC-USD", "leverage": 5,
                            "isolated": False, "marginMode": "CROSS"}]}, deepcopy(updated),
            {"status": 200, "result": {"orderId": "arcus-proof-accepted-2"}}])
    transport = ScriptedTransport(*responses)
    adapter, _ = arcus_adapter(transport)
    native_identity = dict(IDENTITY, scope_key=adapter.scope.key)
    snapshot = await adapter.native_risk_snapshot("BTC-USD", native_identity=native_identity,
        local_ledger_version=0, max_age_ns=10_000_000_000)
    # The shared owner fixtures use a fixed deterministic clock.
    snapshot["account_facts"]["observed_ns"] = NOW - 1
    native_id = ADDRESS
    scope = AccountScope("arcus", adapter.scope.account_alias, "linear_perpetual", "sandbox")
    route = RouteSpec("arcus-proof", "arcus", "linear", "linear_perpetual", "sandbox", scope, native_id)
    registry = VenueRegistry(max_routes=2, max_aliases=2, max_fact_bytes=65536)
    current_snapshot = {"value": snapshot}
    registry.bind_existing_adapter(route, adapter,
        reference_provider=lambda: {"open_orders": 0, "unknown_orders": 0, "unresolved_fills": 0,
            "reconciliation_sessions": 0, "active_intents": 0, "active_reservations": 0,
            "active_signers": 0, "rollback_refs": 0, "strategy_aliases": 0},
        capabilities=Capabilities({"linear_perpetual"}, {"sandbox"}, {"GTC"}, True, True, False),
        native_facts_provider=lambda requested_symbol: {**current_snapshot["value"], "taker_fee_rate": "0.001"})
    registry.register_aliases((AliasBinding("alpha", "arcus-perp", route.route_id, "BTC-USD",
        "linear", "linear_perpetual", "sandbox", scope),))
    ledger = ExecutionLedger(tmp_path / "arcus-owner.sqlite3", scope, max_intents=8,
        max_evidence_bytes=65536, max_fills=8, authority_root=tmp_path / "arcus-lease",
        native_account_id=native_id)
    owner = ExecutionOwner(ledger, RuntimeControl(ledger), object(),
        ExecutionBudgets(Decimal("1000000"), Decimal("1000000"), 4, 10_000_000_000, 1.0, 1.0),
        clock=lambda: NOW)
    open_gates(owner, "alpha")
    intent = OrderIntent(intent_id="arcus-proof-intent", job_id="alpha", signal_id="arcus-proof-signal",
        destination=OrderDestination.REAL, client_order_id="arcus-proof-client",
        instrument=InstrumentId(venue=Venue.ARCUS, asset_class=AssetClass.CRYPTO_PERP,
            raw_symbol="BTC-USD", std_symbol="BTC-USD"), side=Side.BUY, quantity=Decimal("0.001"),
        limit_price=Decimal("50000.1"), metadata={"account_id": scope.account, "environment": "sandbox"})
    action = ExecutionAction(action_id="arcus-proof-action", action=ActionKind.NEW, scope=scope,
        strategy_id="alpha", config_version="cfg-1", mode=TradingMode.SANDBOX,
        expiry_ns=NOW + 1_000_000_000, intent=intent, time_in_force="GTC")
    delivered = []
    async def on_event(event, context):
        delivered.append((event, context))

    readiness_fn = readiness(owner, scope, intent.instrument.key)

    plan = StrategyActionPlan("arcus-proof-plan", "alpha", "cfg-1", NOW, (action,))
    if submitted:
        result = await owner.dispatch_plan(plan, registry=registry, readiness=readiness_fn,
            evidence=evidence(owner, "alpha", "sandbox"), on_event=on_event)
    else:
        with pytest.raises(GateBlocked, match="reservation exceeds verified native available balance"):
            await owner.dispatch_plan(plan, registry=registry, readiness=readiness_fn,
                evidence=evidence(owner, "alpha", "sandbox"), on_event=on_event)
        result = None
    if submitted:
        assert result["results"][0]["status"] == "SUBMITTED"
        assert result["results"][0]["order_id"] == "arcus-proof-accepted"
        assert len(delivered) == 1 and delivered[0][0].status == "ACCEPTED"
        assert delivered[0][1].quality == "VALID"
        assert sum(call["method"] == "WS" for call in transport.dispatched) == 1
        assert ledger.get(intent.intent_id)["state"] in {"SUBMITTED", "ACKNOWLEDGED"}
        ledger.apply_fill_once(intent.intent_id, "arcus-fill-1", quantity="0.001", price="50000.1", fee="0")
        assert ledger.account_version() == 1
        stale_intent = intent.model_copy(update={"intent_id": "arcus-proof-intent-stale",
            "signal_id": "arcus-proof-signal-stale", "client_order_id": "arcus-proof-client-stale"})
        stale_action = ExecutionAction(action_id="arcus-proof-action-stale", action=ActionKind.NEW,
            scope=scope, strategy_id="alpha", config_version="cfg-1", mode=TradingMode.SANDBOX,
            expiry_ns=NOW + 2_000_000_000, intent=stale_intent, time_in_force="GTC")
        with pytest.raises(GateBlocked, match="stale against local fill version"):
            await owner.dispatch_plan(StrategyActionPlan("arcus-proof-plan-stale", "alpha", "cfg-1", NOW,
                (stale_action,)), registry=registry, readiness=readiness_fn,
                evidence=evidence(owner, "alpha", "sandbox"))
        assert sum(call["method"] == "WS" for call in transport.dispatched) == 1
        current_snapshot["value"] = await adapter.native_risk_snapshot("BTC-USD",
            native_identity=native_identity, local_ledger_version=ledger.account_version(),
            max_age_ns=10_000_000_000)
        current_snapshot["value"]["account_facts"]["observed_ns"] = NOW - 1
        refreshed_intent = intent.model_copy(update={"intent_id": "arcus-proof-intent-refreshed",
            "signal_id": "arcus-proof-signal-refreshed", "client_order_id": "arcus-proof-client-refreshed"})
        refreshed_action = ExecutionAction(action_id="arcus-proof-action-refreshed", action=ActionKind.NEW,
            scope=scope, strategy_id="alpha", config_version="cfg-1", mode=TradingMode.SANDBOX,
            expiry_ns=NOW + 3_000_000_000, intent=refreshed_intent, time_in_force="GTC")
        refreshed = await owner.dispatch_plan(StrategyActionPlan("arcus-proof-plan-refreshed", "alpha", "cfg-1", NOW,
            (refreshed_action,)), registry=registry, readiness=readiness_fn,
            evidence=evidence(owner, "alpha", "sandbox"))
        assert refreshed["results"][0]["status"] == "SUBMITTED"
        assert refreshed["results"][0]["order_id"] == "arcus-proof-accepted-2"
        assert sum(call["method"] == "WS" for call in transport.dispatched) == 2
    else:
        assert delivered == []
        assert sum(call["method"] == "WS" for call in transport.dispatched) == 0
        assert ledger.get(intent.intent_id) is None
    ledger.close()
