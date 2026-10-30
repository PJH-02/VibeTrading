"""Native fee unit identity and group/leg idempotence guards."""
from dataclasses import replace
from decimal import Decimal
import pytest
from cap05_source_fixture import build_native_group,NOW
from test_cap05_native_integration import coordinator_for,close_fixture

@pytest.mark.asyncio
async def test_same_native_child_cannot_be_claimed_as_a_new_execution_group(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        kw=dict(registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        await g.execute(f['plan'],**kw)
        p=replace(f['plan'],plan_id='foreign-reuse',actions=tuple(replace(a,group_id='different-group') for a in f['plan'].actions))
        with pytest.raises((ValueError,RuntimeError)):await g.execute(p,**kw)
        for a in f['plan'].actions:assert tuple(g.ledger.group_ids_for_intent(a.intent.intent_id,a.scope.key))==('cap05-group',)
        assert len(f['binance_transport'].calls)==len(f['bybit_transport'].calls)==1
    finally:await close_fixture(f)

@pytest.mark.asyncio
@pytest.mark.parametrize('bad_fact',[{'commissionAsset':'BNB'},{'symbol':'ETHUSDT'},{'side':'SELL'}],ids=['fee_unit','foreign_symbol','opposite_side'])
async def test_inconsistent_native_fill_facts_are_not_applied_to_owned_quote_cost(tmp_path,bad_fact):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        await g.execute(f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        f['binance_transport'].native_orders={'c-binance':{'status':'FILLED','cumulative':'0.1','quantity':'0.1','native_id':7001,'terminal':True}}
        f['bybit_transport'].native_orders={'c-bybit':{'status':'New','cumulative':'0','quantity':'0.2','native_id':'bybit-7001','terminal':False}}
        f['binance_transport'].native_fills=[{'symbol':'BTCUSDT','id':9301,'orderId':7001,'side':'BUY','qty':'0.1','price':'100','commission':'0.01','commissionAsset':'USDT','time':1000,'maker':False}]
        f['binance_transport'].native_fills[0].update(bad_fact)
        r=await g.reconcile('cap05-group',registry=f['registry'],start_ns=NOW,end_ns=NOW+10**9)
        assert r['reconciled'] is False, 'no conversion proof authorizes treating BNB fee as USDT cost'
        assert r['state'] in {'UNKNOWN','RECONCILING','BLOCKED'}
        a=f['plan'].actions[0];row=f['binance_owner'].ledger.get(a.intent.intent_id)
        assert Decimal(row['fees'])==0
        assert f['binance_owner'].ledger.reserved()>0
    finally:await close_fixture(f)
