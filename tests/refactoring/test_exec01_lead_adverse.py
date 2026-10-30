from dataclasses import replace
from decimal import Decimal as D

import pytest

from core.models import Side
from execution.trading_contracts import StrategyOrderEvent
from runtime.runtime_control import GateBlocked
from test_exec01_native import (NOW, action, cancel_action, evidence, make_owner,
    open_gates, readiness, register_perp, intent)


@pytest.mark.asyncio
async def test_legacy_readiness_booleans_without_authenticated_native_facts_block(tmp_path):
    owner, registry, adapter, transport, scope = register_perp(tmp_path)
    try:
        order = intent("unproved", venue=scope.venue, asset=__import__("core.models", fromlist=["AssetClass"]).AssetClass.CRYPTO_PERP,
                       symbol="BTCUSDT", account=scope.account, environment=scope.environment)
        act = action("act-unproved", scope, order, mode=__import__("execution.trading_contracts", fromlist=["TradingMode"]).TradingMode.SANDBOX)
        with pytest.raises((GateBlocked, ValueError)):
            await owner.dispatch_action(act, registry=registry,
                readiness=readiness(owner, scope, order.instrument.key),
                evidence=evidence(owner, "alpha", "sandbox"))
        assert transport.calls == []
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_live_adapter_is_blocked_before_any_transport_mutation(tmp_path):
    from core.models import AssetClass, Venue
    from execution.execution_ledger import AccountScope
    from execution.perp_execution import BinancePerpAdapter, PerpScope
    from execution.trading_contracts import TradingMode
    from execution.venues.registry import AliasBinding, Capabilities, RouteSpec, VenueRegistry
    from test_exec01_native import ScriptedPerpTransport, ZERO_FACTS
    scope = AccountScope("binance", "live-alias", "linear_perpetual", "live")
    native_id = "physical-live"
    route = RouteSpec("live-route", "binance", "usdm_perp", "linear_perpetual", "live", scope, native_id)
    transport = ScriptedPerpTransport()
    adapter = BinancePerpAdapter(PerpScope("binance", "linear_perpetual", "live", scope.account, "USDT"),
        transport, api_key="fixture", api_secret="fixture")
    adapter._metadata["BTCUSDT"] = {"lot_size":".001", "tick_size":".1", "min_quantity":".001",
                                    "min_notional":"5", "multiplier":"1"}
    registry = VenueRegistry(max_routes=2,max_aliases=2,max_fact_bytes=2048)
    registry.bind_existing_adapter(route,adapter,reference_provider=lambda:dict(ZERO_FACTS),
        capabilities=Capabilities({"linear_perpetual"},{"live"},{"GTC"},True,True,False))
    registry.register_aliases((AliasBinding("alpha","btc",route.route_id,"BTCUSDT","usdm_perp",
        "linear_perpetual","live",scope),))
    owner=make_owner(tmp_path,scope,native_id);open_gates(owner,"alpha")
    try:
        order=intent("live",venue=Venue.BINANCE,asset=AssetClass.CRYPTO_PERP,symbol="BTCUSDT",
                     account=scope.account,environment="live")
        with pytest.raises(GateBlocked,match="LIVE"):
            await owner.dispatch_action(action("live-action",scope,order,mode=TradingMode.LIVE),
                registry=registry,readiness=readiness(owner,scope,order.instrument.key),
                evidence=evidence(owner,"alpha","live"))
        assert transport.calls == []
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_multiplier_is_durable_reservation_factor(tmp_path):
    owner,registry,adapter,transport,scope=register_perp(tmp_path,native_proof=True)
    adapter._metadata["BTCUSDT"]["multiplier"]="10"
    # Provider projects the pinned native instrument row, including multiplier.
    from test_exec01_native import _perp_native_facts
    binding=registry.binding("binance-perp")
    registry._records["binance-perp"].binding=native_binding=replace(binding,
        native_facts_provider=lambda symbol:_perp_native_facts(adapter,"native-binance-perp"))
    try:
        order=intent("multiplied",venue=scope.venue,asset=__import__("core.models",fromlist=["AssetClass"]).AssetClass.CRYPTO_PERP,
                     symbol="BTCUSDT",account=scope.account,environment=scope.environment,quantity="2",price="100")
        act=action("multiply-action",scope,order,mode=__import__("execution.trading_contracts",fromlist=["TradingMode"]).TradingMode.SANDBOX)
        await owner.dispatch_action(act,registry=registry,readiness=readiness(owner,scope,order.instrument.key),
            evidence=evidence(owner,"alpha","sandbox"))
        assert owner.ledger.reserved() >= D("2000")
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_foreign_strategy_cannot_confirm_or_cancel_durable_order(tmp_path):
    from test_exec01_native import intent
    owner,registry,adapter,transport,scope=register_perp(tmp_path,native_proof=True)
    try:
        order=intent("owned",venue=scope.venue,asset=__import__("core.models",fromlist=["AssetClass"]).AssetClass.CRYPTO_PERP,
                     symbol="BTCUSDT",account=scope.account,environment=scope.environment)
        act=action("owned-action",scope,order,mode=__import__("execution.trading_contracts",fromlist=["TradingMode"]).TradingMode.SANDBOX)
        await owner.dispatch_action(act,registry=registry,readiness=readiness(owner,scope,order.instrument.key),
            evidence=evidence(owner,"alpha","sandbox"))
        forged=replace(cancel_action("forged",scope,"101",mode=act.mode),strategy_id="beta")
        before=owner.ledger.get("owned")
        from execution.venues.registry import ForeignOrderError
        with pytest.raises((GateBlocked,ValueError,ForeignOrderError)):
            owner.confirm_cancel(forged)
        assert owner.ledger.get("owned")==before
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_fill_attribution_cumulative_feedback_and_atomic_outbox(tmp_path,monkeypatch):
    owner,registry,adapter,transport,scope=register_perp(tmp_path,native_proof=True)
    try:
        from core.models import AssetClass
        from execution.trading_contracts import TradingMode
        order=intent("partial",venue=scope.venue,asset=AssetClass.CRYPTO_PERP,symbol="BTCUSDT",
                     account=scope.account,environment=scope.environment,quantity="5")
        act=action("partial-action",scope,order,mode=TradingMode.SANDBOX)
        await owner.dispatch_action(act,registry=registry,readiness=readiness(owner,scope,order.instrument.key),
            evidence=evidence(owner,"alpha","sandbox"))
        forged=replace(act,strategy_id="beta")
        before=owner.ledger.get("partial")
        with pytest.raises(GateBlocked):
            owner.apply_fill(forged,fill_id="fill-forged",quantity="1",price="100",fee=".1")
        assert owner.ledger.get("partial")==before
        owner.apply_fill(act,fill_id="fill-1",quantity="2",price="100",fee=".1")
        owner.apply_fill(act,fill_id="fill-2",quantity="1",price="100",fee=".1")
        last=owner.ledger.feedback("alpha")[-1]["event"]
        assert D(last["filled_quantity"])==3 and D(last["remaining_quantity"])==2
        pre=owner.ledger.get("partial")
        position=owner.ledger.position(order.instrument.key)
        def fail(*args,**kwargs): raise RuntimeError("outbox capacity failure")
        monkeypatch.setattr(owner.ledger,"record_feedback",fail)
        with pytest.raises(RuntimeError,match="outbox"):
            owner.apply_fill(act,fill_id="fill-3",quantity="1",price="100",fee=".1")
        assert owner.ledger.get("partial")==pre
        assert owner.ledger.position(order.instrument.key)==position
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_source_receives_typed_event_and_context_once(tmp_path):
    from core.models import AssetClass
    from execution.trading_contracts import TradingMode
    owner,registry,adapter,transport,scope=register_perp(tmp_path,native_proof=True)
    received=[]
    class Source:
        def on_order_event(self,event,context):
            assert isinstance(event,StrategyOrderEvent)
            received.append((event,context))
            return None
    try:
        order=intent("feedback",venue=scope.venue,asset=AssetClass.CRYPTO_PERP,symbol="BTCUSDT",
                     account=scope.account,environment=scope.environment)
        act=action("feedback-action",scope,order,mode=TradingMode.SANDBOX)
        await owner.dispatch_action(act,registry=registry,readiness=readiness(owner,scope,order.instrument.key),
            evidence=evidence(owner,"alpha","sandbox"),on_event=Source().on_order_event)
        assert len(received)==1
        assert received[0][1].execution["broker_fee_amount_received"] is False
    finally:
        await owner.close()
