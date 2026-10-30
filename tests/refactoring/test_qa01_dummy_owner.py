"""QA-01 real Owner lifecycle for the two explicit synthetic fixture families."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, TradeEvent, Venue
from execution.execution_ledger import AccountScope
from execution.process_lease import ProcessLease
from execution.trading_contracts import ActionKind, ExecutionAction, StrategyActionPlan, TradingMode
from execution.venues.registry import AliasBinding, RouteSpec, VenueRegistry
from market.hot_path import DecisionSnapshot, StrategyDataView
from runtime.runtime_control import GateBlocked
from test_exec01_native import NOW, ZERO_FACTS, action, evidence, intent, make_owner, open_gates, readiness


async def crypto_fixture(root, *, variant="valid", available="1000"):
    from execution.venues import dummy_crypto

    ProcessLease.register_extension_venue("dummy-crypto")
    strategy_id = "qa01-crypto-dummy-owner"
    scope = AccountScope("dummy-crypto", "qa01-synthetic-perp", "linear_perpetual", "sandbox")
    native_id = "qa01-synthetic-perp-native"
    adapter = dummy_crypto.CryptoDummyAdapter(scope)
    account_snapshot = dict(await adapter.account_state())
    product = dict(await adapter.instrument_metadata("BTCUSDT"))
    owner = make_owner(root, scope, native_id)
    route = RouteSpec(
        "qa01-dummy-crypto-perp", "dummy-crypto", "usdm_perp",
        "linear_perpetual", "sandbox", scope, native_id,
    )
    capabilities = replace(
        dummy_crypto.CRYPTO_CAPABILITIES, synthetic_fixture=True,
        reduce_only=False, owner_enforced_reduce_only=False,
    )
    registry = VenueRegistry(max_routes=2, max_aliases=4, max_fact_bytes=4096)

    def native_facts(symbol):
        assert symbol == "BTCUSDT"
        account = {
            "scope_key": scope.key, "native_account_id": native_id,
            "reconciliation_state": "RECONCILED", "unknown": False,
            "observed_ns": NOW, "account_version": owner.ledger.account_version(),
            "currency": "USDT", "equity": account_snapshot["equity"],
            "available": available, "raw": dict(account_snapshot),
            "physical_positions": {},
        }
        spec = {
            **product, "scope_key": scope.key, "venue": route.venue,
            "product": route.product, "environment": route.environment,
            "kind": "linear_perpetual", "multiplier": "1",
        }
        facts = {
            "quality": "SYNTHETIC", "fee_model": "configured_taker_rate",
            "taker_fee_rate": "0.001", "max_age_ns": 1_000_000_000,
            "synthetic_risk_model": "FULL_NOTIONAL_USDT_V1",
            "account_facts": account, "product_specs": {symbol: spec},
        }
        if variant == "missing_model":
            facts.pop("synthetic_risk_model")
        elif variant == "wrong_model":
            facts["synthetic_risk_model"] = "NATIVE_MARGIN_V1"
        elif variant == "wrong_currency":
            account["currency"] = "EUR"
            spec["settlement_currency"] = "EUR"
        elif variant == "wrong_product_currency":
            spec["settlement_currency"] = "EUR"
        elif variant == "wrong_multiplier":
            spec["multiplier"] = "2"
        elif variant == "float_multiplier":
            spec["multiplier"] = 1.0
        elif variant == "bool_multiplier":
            spec["multiplier"] = True
        elif variant == "wrong_kind":
            spec["kind"] = "cash"
        elif variant == "wrong_scope":
            account["scope_key"] = "wrong-scope"
        elif variant == "unreconciled":
            account["reconciliation_state"] = "UNKNOWN"
        elif variant == "stale":
            account["observed_ns"] = NOW - 1_000_000_001
        elif variant == "wrong_version":
            account["account_version"] -= 1
        return facts

    registry.register(
        route, adapter, dummy_crypto.OPERATIONS, capabilities,
        reference_provider=lambda: dict(ZERO_FACTS), native_facts_provider=native_facts,
    )
    registry.register_aliases((AliasBinding(
        strategy_id, "binance-perp-to-dummy-crypto", route.route_id,
        "BTCUSDT", "usdm_perp", "linear_perpetual", "sandbox", scope,
    ),))
    instrument = InstrumentId(
        venue=Venue.BINANCE,
        asset_class=(AssetClass.CRYPTO_SPOT if variant == "wrong_asset_class" else AssetClass.CRYPTO_PERP),
        raw_symbol="BTCUSDT", std_symbol="BTCUSDT", currency="USDT",
        security_kind="perpetual",
    )
    source_intent = OrderIntent(
        intent_id="qa01-crypto-dummy-order", job_id=strategy_id,
        signal_id="qa01-crypto-dummy-signal", destination=OrderDestination.REAL,
        client_order_id="qa01-crypto-dummy-client", instrument=instrument,
        side=Side.BUY, quantity=Decimal("1"), limit_price=Decimal("100"),
        metadata={"account_id": scope.account, "environment": scope.environment,
                  "execution_venue_id": route.venue, "reserve_unit_price": "100"},
    )
    new_action = replace(action(
        "qa01-crypto-dummy-new", scope, source_intent,
        mode=TradingMode.SANDBOX,
    ), strategy_id=strategy_id)
    open_gates(owner, strategy_id)
    return SimpleNamespace(
        strategy_id=strategy_id, scope=scope, native_id=native_id, adapter=adapter,
        owner=owner, route=route, capabilities=capabilities, registry=registry,
        facts=native_facts, instrument=instrument, intent=source_intent, action=new_action,
    )



class CryptoSourceFixture:
    ACTION_PROTOCOL_VERSION = 1
    STRATEGY_ID = "qa01-crypto-dummy-owner"

    def __init__(self, fixture):
        self.fixture = fixture
        self.config_version = "cfg-1"
        self.market_calls = 0
        self.received = []
        self.cancel = None

    def on_event(self, event, _view):
        self.market_calls += 1
        return StrategyActionPlan(
            "qa01-crypto-dummy-plan", self.fixture.strategy_id, "cfg-1",
            event.source_ts_ns, (self.fixture.action,),
        )

    def on_order_event(self, event, context):
        self.received.append((event, context))
        actions = ()
        if event.status == "PARTIAL_FILL" and self.cancel is None:
            self.cancel = ExecutionAction(
                action_id="qa01-crypto-dummy-cancel", action=ActionKind.CANCEL,
                scope=self.fixture.scope, strategy_id=self.fixture.strategy_id,
                config_version="cfg-1", mode=TradingMode.SANDBOX,
                expiry_ns=context.now_ns + 1_000_000_000,
                target_order_id=event.metadata["client_order_id"],
            )
            actions = (self.cancel,)
        return StrategyActionPlan(
            "qa01-crypto-dummy-feedback:" + event.event_id,
            self.fixture.strategy_id, "cfg-1", context.now_ns, actions,
        )


@pytest.mark.asyncio
async def test_crypto_dummy_real_owner_full_notional_partial_cancel_late_fill_feedback(tmp_path):
    from runtime.real_runner import RealExecutionRunner, RealExecutionRunnerConfig

    fixture = await crypto_fixture(tmp_path)
    owner, source = fixture.owner, CryptoSourceFixture(fixture)
    runner = RealExecutionRunner(RealExecutionRunnerConfig(
        job_id="qa01-crypto-runner", account_id=fixture.scope.account,
        destination_name="dummy-crypto", max_order_notional=Decimal("1000"),
        max_position_notional=Decimal("1000")), store=object(), bus=object(),
        broker=None, owner=owner,
        readiness=readiness(owner, fixture.scope, fixture.instrument.key), settings=object())
    event = TradeEvent(
        event_id="qa01-crypto-market-1", instrument=fixture.instrument,
        venue=Venue.BINANCE, provider="qa01-synthetic", source_ts_ns=NOW,
        receive_ts_ns=NOW, wallclock_ts_ns=NOW, price=Decimal("100"),
        quantity=Decimal("1"),
    )
    snapshot = DecisionSnapshot(
        "qa01-crypto-run", 1, NOW, "SHADOW", 1, {}, {}, {},
        {fixture.instrument.key: event}, {}, {}, {}, {},
    )
    try:
        result = await runner.dispatch_strategy_market_event(
            source, event, StrategyDataView(snapshot, ready=True, phase="SHADOW"),
            registry=fixture.registry,
            readiness=readiness(owner, fixture.scope, fixture.instrument.key),
            evidence=evidence(owner, fixture.strategy_id, "sandbox"),
            source_trust="trusted_cooperative",
        )
        assert result["results"][0]["status"] == "SUBMITTED"
        order = owner.ledger.get(fixture.intent.intent_id)
        assert Decimal(order["reservation"]) == Decimal("100.1")
        assert fixture.adapter.mutation_count == 1
        assert source.received[0][0].status == "ACCEPTED"
        assert source.received[0][1].quality == "SYNTHETIC"
        assert owner.ledger.feedback(fixture.strategy_id)[0]["event"]["metadata"]["fact_quality"] == "SYNTHETIC"

        assert fixture.adapter.fill(
            "BTCUSDT", fixture.intent.client_order_id, quantity="0.4",
            price="100", fee="0.04", fill_id="qa01-crypto-partial-1",
        )
        assert owner.apply_fill(
            fixture.action, fill_id="qa01-crypto-partial-1", quantity="0.4",
            price="100", fee="0.04", order_id=fixture.intent.client_order_id,
            fact_quality="SYNTHETIC",
        )
        await owner.deliver_feedback(fixture.strategy_id, source.on_order_event)
        assert source.cancel is not None
        assert owner.ledger.get(fixture.intent.intent_id)["state"] == "CANCEL_PENDING"
        assert any(event.status == "PARTIAL_FILL" and context.quality == "SYNTHETIC"
                   for event, context in source.received)

        query = await owner.reconcile_order(source.cancel, registry=fixture.registry)
        assert query["status"] == "CANCELED" and query["fact_quality"] == "SYNTHETIC"
        assert owner.ledger.get(fixture.intent.intent_id)["state"] == "CANCELLED"
        assert any(event.status == "CANCELLED" and context.quality == "SYNTHETIC"
                   for event, context in source.received)

        assert fixture.adapter.fill(
            "BTCUSDT", fixture.intent.client_order_id, quantity="0.2",
            price="100", fee="0.02", fill_id="qa01-crypto-late-fill-1",
        )
        assert owner.apply_fill(
            fixture.action, fill_id="qa01-crypto-late-fill-1", quantity="0.2",
            price="100", fee="0.02", order_id=fixture.intent.client_order_id,
            fact_quality="SYNTHETIC",
        )
        assert not owner.apply_fill(
            fixture.action, fill_id="qa01-crypto-late-fill-1", quantity="0.2",
            price="100", fee="0.02", order_id=fixture.intent.client_order_id,
            fact_quality="SYNTHETIC",
        )
        await owner.deliver_feedback(fixture.strategy_id, source.on_order_event)
        assert owner.ledger.get(fixture.intent.intent_id)["filled"] == "0.6"
        assert owner.ledger.position(fixture.instrument.key) == Decimal("0.6")
        assert owner.ledger._db.execute(
            "SELECT COUNT(*) FROM execution_fills WHERE intent_id=?", (fixture.intent.intent_id,),
        ).fetchone()[0] == 2
        assert all(context.quality == "SYNTHETIC" for _, context in source.received)
        assert fixture.adapter.mutation_count == 2  # one permitted submit and one query-cancel
        assert fixture.adapter.event_count == 4  # submit, cancel, and two synthetic fills
    finally:
        await owner.close()


@pytest.mark.parametrize(
    ("variant", "available", "reason"),
    [
        ("missing_model", "1000", "Synthetic linear fixtures require FULL_NOTIONAL_USDT_V1"),
        ("wrong_model", "1000", "Synthetic linear fixtures require FULL_NOTIONAL_USDT_V1"),
        ("wrong_currency", "1000", "Synthetic linear fixtures require FULL_NOTIONAL_USDT_V1"),
        ("wrong_asset_class", "1000", "Synthetic linear fixtures require FULL_NOTIONAL_USDT_V1"),
        ("wrong_product_currency", "1000", "Synthetic fixture product facts do not match the selected route/currency"),
        ("wrong_multiplier", "1000", "Synthetic linear fixture multiplier must be the exact decimal one"),
        ("float_multiplier", "1000", "Synthetic linear fixture multiplier must be the exact decimal one"),
        ("bool_multiplier", "1000", "Synthetic linear fixture multiplier must be the exact decimal one"),
        ("wrong_kind", "1000", "Synthetic fixture product facts do not match the selected route/currency"),
        ("wrong_scope", "1000", "Bounded synthetic fixture account facts are missing, stale or unversioned"),
        ("unreconciled", "1000", "Bounded synthetic fixture account facts are missing, stale or unversioned"),
        ("stale", "1000", "Bounded synthetic fixture account facts are missing, stale or unversioned"),
        ("wrong_version", "1000", "Bounded synthetic fixture account facts are missing, stale or unversioned"),
        ("valid", "100.09", "Worst-case reservation exceeds verified native available balance"),
        ("reduce_only", "1000", "unused"),
    ],
)
@pytest.mark.asyncio
async def test_crypto_dummy_owner_full_notional_negative_fact_matrix_has_zero_effects(
    tmp_path, variant, available, reason,
):
    fixture = await crypto_fixture(tmp_path, variant=variant, available=available)
    owner = fixture.owner
    action_to_try = fixture.action
    expected_reason = reason
    if variant == "reduce_only":
        action_to_try = replace(action_to_try, reduce_only=True)
        expected_reason = "Cash reduce-only requires the explicit owner-enforced capability"
    plan = StrategyActionPlan(
        "qa01-crypto-negative-plan", fixture.strategy_id, "cfg-1", NOW,
        (action_to_try,),
    )
    try:
        with pytest.raises(GateBlocked, match=expected_reason):
            await owner.dispatch_plan(
                plan, registry=fixture.registry,
                readiness=readiness(owner, fixture.scope, fixture.instrument.key),
                evidence=evidence(owner, fixture.strategy_id, "sandbox"),
            )
        assert fixture.adapter.mutation_count == 0
        assert fixture.adapter.event_count == 0
        assert owner.ledger.get(fixture.intent.intent_id) is None
        assert owner.ledger.feedback(fixture.strategy_id) == []
    finally:
        await owner.close()
