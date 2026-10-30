"""Selected native promotion headroom stays bounded and fail-closed."""
import asyncio
from dataclasses import replace
from decimal import Decimal
import sys
from types import MappingProxyType

import pytest

import cap06_source_fixture as source_module
sys.modules["strategies.cap06_source_fixture"] = source_module

from execution.execution_groups import CrossVenueExecutionGroups
from market.hot_path import ApplyResult
from runtime.real_runner import RealExecutionRunner, RealExecutionRunnerConfig
from runtime.selected_execution import (NativeSelectionBinding, NativeSelectionDispatcher,
    parse_strategy_selections)
from cap05_source_fixture import CONFIG_VERSION, NOW, STRATEGY_ID, build_native_group, make_market_event_and_view
from test_cap06_routing import prepare_scripted_reconcile
from test_cap06_selection import selection


def _group_dispatcher(tmp_path,current):
    fixture=build_native_group(tmp_path)
    primary=fixture["binance_owner"]
    key_a,key_b=fixture["binance_scope"].key,fixture["bybit_scope"].key
    event,view=make_market_event_and_view()
    prior_plan=source_module.PLAN
    source_module.PLAN=[]
    source=source_module.create_strategy()
    for owner in (fixture["binance_owner"],fixture["bybit_owner"]):
        owner.clock=lambda current=current:current[0]
    facts={"accounts":{
        key_a:{"scope":{"venue":"binance","account":"acct-binance",
            "product":"linear_perpetual","environment":"sandbox"},"mode":"sandbox"},
        key_b:{"scope":{"venue":"bybit","account":"acct-bybit",
            "product":"linear_perpetual","environment":"sandbox"},"mode":"sandbox"}}}
    coordinator=CrossVenueExecutionGroups(primary.ledger,
        {key_a:fixture["binance_owner"],key_b:fixture["bybit_owner"]},
        max_groups=8,max_facts=128,max_bytes=200_000,max_followup_actions=2,clock=lambda:NOW)
    runner=RealExecutionRunner(RealExecutionRunnerConfig("perf01-native-freshness",
        primary.ledger.scope.account,"fixture",Decimal("1000000"),Decimal("1000000")),
        store=object(),bus=object(),settings=object(),owner=primary,group_coordinator=coordinator)
    configured=selection()
    configured.update(venue="binance",account="acct-binance",product="linear_perpetual")
    configured["registration"].update(module="strategies.cap06_source_fixture",
        accounts=[key_a,key_b],config_version=CONFIG_VERSION,required_feeds=[event.instrument.key])
    risk=next(iter(configured["registration"]["risk"].values()))
    configured["registration"]["risk"]={key_a:risk,key_b:risk}
    configured["registration"]["config"]["config_version"]=CONFIG_VERSION
    binding=NativeSelectionBinding(runner,primary,source,fixture["registry"],
        readiness=fixture["readiness"],
        evidence_provider={key:(lambda _result,_snapshot,key=key:
            {"config_version":CONFIG_VERSION,**fixture["evidence"][key]}) for key in (key_a,key_b)},
        registration_facts=facts,route_id="binance-perp")
    dispatcher=NativeSelectionDispatcher(parse_strategy_selections([configured])[0],binding)
    prepare_scripted_reconcile(fixture,"binance-perp")
    prepare_scripted_reconcile(fixture,"bybit-perp")
    return fixture,dispatcher,source,event,view,prior_plan


def _enqueue(dispatcher,event,view,*,event_id=None,now_ns=NOW):
    event=event.model_copy(update={"event_id":event_id or event.event_id,
        "source_ts_ns":now_ns,"receive_ts_ns":now_ns,"wallclock_ts_ns":now_ns})
    snapshot=replace(view.hot_path,phase="LIVE",now_ns=now_ns,
        quality=MappingProxyType({event.instrument.key:"VALID"}),
        trades=MappingProxyType({event.instrument.key:event}))
    dispatcher.enqueue_applied(ApplyResult(event,True,False,"",
        snapshot.engine_incarnation,snapshot.apply_seq),snapshot)


async def _close(fixture,dispatcher,prior_plan):
    if dispatcher.task:
        dispatcher.task.cancel()
        await asyncio.gather(dispatcher.task,return_exceptions=True)
    source_module.PLAN=prior_plan
    await fixture["binance_owner"].close()
    await fixture["bybit_owner"].close()


@pytest.mark.asyncio
async def test_group_refreshes_near_expiry_before_durable_claim_crosses_old_expiry(tmp_path):
    current=[NOW]
    fixture,dispatcher,source,event,view,prior_plan=_group_dispatcher(tmp_path,current)
    try:
        await dispatcher.prepare_promotion()
        assert len(dispatcher._scope_dispatchers)==2
        old_expiries={key:item._promotion_receipt.expires_ns
            for key,item in dispatcher._scope_dispatchers.items()}
        old_expiry=min(old_expiries.values())
        current[0]=old_expiry-500_000_000
        primary=fixture["binance_owner"]
        original_claim=primary.ledger.claim_source_event
        claims=[]
        def advance_after_durable_claim(*args,**kwargs):
            result=original_claim(*args,**kwargs)
            claims.append(result)
            current[0]+=1_000_000_000
            return result
        primary.ledger.claim_source_event=advance_after_durable_claim
        callback_cuts=[]
        original_callback=source.on_event
        def record_callback(event_value,data):
            callback_cuts.append(tuple(
                item._promotion_receipt.ledger_version==item.binding.owner.ledger.account_version()
                and item._promotion_guard_one()
                for item in dispatcher._scope_dispatchers.values()))
            return original_callback(event_value,data)
        source.on_event=record_callback
        dispatcher.task=asyncio.create_task(dispatcher.run())
        _enqueue(dispatcher,event,view,event_id="native-headroom-cross-old-expiry",now_ns=current[0])
        await asyncio.wait_for(dispatcher.queue.join(),timeout=3)
        assert claims==[True]
        assert current[0]>old_expiry
        assert source.calls==1 and callback_cuts==[(True,True)]
        assert not dispatcher.errors,dispatcher.errors
        assert all(item._promotion_receipt.observed_ns>=old_expiry-500_000_000
            and item._promotion_receipt.expires_ns>current[0]
            for item in dispatcher._scope_dispatchers.values())
        assert not fixture["binance_transport"].calls and not fixture["bybit_transport"].calls
    finally:
        await _close(fixture,dispatcher,prior_plan)


@pytest.mark.asyncio
async def test_claim_past_refreshed_hard_ttl_still_blocks_source_and_actions(tmp_path):
    current=[NOW]
    fixture,dispatcher,source,event,view,prior_plan=_group_dispatcher(tmp_path,current)
    try:
        await dispatcher.prepare_promotion()
        initial_reads={key:len(fixture[key+"_transport"].read_calls)
            for key in ("binance","bybit")}
        old_expiry=min(item._promotion_receipt.expires_ns
            for item in dispatcher._scope_dispatchers.values())
        current[0]=old_expiry-500_000_000
        primary=fixture["binance_owner"]
        original_claim=primary.ledger.claim_source_event
        refreshed_expiries=[]
        def overrun_after_claim(*args,**kwargs):
            refreshed_expiries.extend(item._promotion_receipt.expires_ns
                for item in dispatcher._scope_dispatchers.values())
            result=original_claim(*args,**kwargs)
            current[0]+=5_100_000_000
            return result
        primary.ledger.claim_source_event=overrun_after_claim
        dispatcher.task=asyncio.create_task(dispatcher.run())
        _enqueue(dispatcher,event,view,event_id="native-headroom-hard-ttl",now_ns=current[0])
        await asyncio.wait_for(dispatcher.queue.join(),timeout=3)
        assert refreshed_expiries and min(refreshed_expiries)>old_expiry
        assert source.calls==0
        assert dispatcher.errors and "fresh exact promotion proof" in dispatcher.errors[0]
        assert not fixture["binance_transport"].calls and not fixture["bybit_transport"].calls
        assert len(fixture["binance_transport"].read_calls)>initial_reads["binance"]
        assert len(fixture["bybit_transport"].read_calls)>initial_reads["bybit"]
    finally:
        await _close(fixture,dispatcher,prior_plan)


@pytest.mark.asyncio
async def test_refresh_headroom_is_quarter_of_short_proof_window_and_skips_fresh_proofs(tmp_path):
    current=[NOW]
    fixture,dispatcher,_source,_event,_view,prior_plan=_group_dispatcher(tmp_path,current)
    try:
        await dispatcher.prepare_promotion()
        root=dispatcher
        original=root._promotion_receipt
        assert root._promotion_refresh_due_one() is False
        root._promotion_receipt=replace(original,observed_ns=NOW,expires_ns=NOW+400_000_000)
        assert root._promotion_guard_one()
        assert root._promotion_refresh_due_one() is False
        current[0]=NOW+299_999_999
        assert root._promotion_refresh_due_one() is False
        current[0]=NOW+300_000_000
        assert root._promotion_guard_one()
        assert root._promotion_refresh_due_one() is True
        root._promotion_receipt=original
        current[0]=NOW
        assert root._promotion_refresh_due_one() is False
    finally:
        await _close(fixture,dispatcher,prior_plan)
