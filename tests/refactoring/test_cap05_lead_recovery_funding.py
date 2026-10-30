"""Lead-only money and recovery adverse cases, kept outside worker write scope."""
from dataclasses import replace
from decimal import Decimal
import pytest
from execution.trading_contracts import StrategyActionPlan
from cap05_source_fixture import build_native_group,NOW
from test_cap05_native_integration import coordinator_for,close_fixture

async def execute(g,f,p=None):
    return await g.execute(p or f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])

def receipt(f,group,amount,identity='native-funding-id'):
    return {'scope_key':f['binance_scope'].key,'native_account_id':f['binance_owner'].ledger.native_account_id,'source':'native_funding_receipt','receipt_id':identity,'period_ns':NOW,'currency':'USDT','amount':amount,'version':1,
        'raw':{'id':identity,'native_currency':'USDT','amount':amount},'allocations':{group:amount}}

@pytest.mark.asyncio
async def test_source_native_quantities_larger_than_one_remain_supported(tmp_path):
    f=build_native_group(tmp_path,binance_qty='2',bybit_qty='3');g=coordinator_for(f)
    try:
        r=await execute(g,f)
        assert r['state']=='ACKNOWLEDGED',r
        assert Decimal(f['binance_owner'].ledger.get(f['plan'].actions[0].intent.intent_id)['reservation'])==Decimal('200.4')
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_negative_native_funding_is_valid_debit_not_unallocatable_credit(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        await execute(g,f)
        gid=f['plan'].actions[0].group_id
        r=g.record_group_funding(gid,[receipt(f,gid,'-0.50')])
        assert r['funding']['status']!='UNKNOWN'
        assert Decimal(r['funding']['attributed_by_currency']['USDT'])==Decimal('-0.50')
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_same_native_funding_id_cannot_be_double_allocated_across_groups(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        await execute(g,f); p=f['plan'];gid=p.actions[0].group_id
        acts=[]
        for a in p.actions:
            i=replace(a.intent,intent_id=a.intent.intent_id+'-two',client_order_id=a.intent.client_order_id+'-two') if hasattr(a.intent,'__dataclass_fields__') else a.intent.model_copy(update={'intent_id':a.intent.intent_id+'-two','client_order_id':a.intent.client_order_id+'-two'})
            acts.append(replace(a,action_id=a.action_id+'-two',intent=i,group_id='group-two'))
        q=replace(p,plan_id=p.plan_id+'-two',actions=tuple(acts))
        # Distinct actual native client IDs and known groups are prerequisites.
        second=await execute(g,f,q)
        assert second['state']=='ACKNOWLEDGED',second
        g.record_group_funding(gid,[receipt(f,gid,'1')])
        with pytest.raises((ValueError,RuntimeError)):
            g.record_group_funding('group-two',[receipt(f,'group-two','1')])
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_complete_native_open_order_history_is_reconciled_known_working_risk(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        await execute(g,f)
        f['binance_transport'].native_orders={'c-binance':{'status':'NEW','cumulative':'0','quantity':'0.1','native_id':7001}}
        f['bybit_transport'].native_orders={'c-bybit':{'status':'New','cumulative':'0','quantity':'0.2','native_id':'bybit-7001','terminal':False}}
        f['binance_transport'].native_fills=[];f['bybit_transport'].native_fills=[]
        r=await g.reconcile(f['plan'].actions[0].group_id,registry=f['registry'],start_ns=NOW,end_ns=NOW+10**9)
        assert r['reconciled'] is True,r
        assert all(leg['state']=='SUBMITTED' for leg in r['legs'])
        assert f['binance_owner'].ledger.reserved()==Decimal('10.02')
        assert f['bybit_owner'].ledger.reserved()==Decimal('20.04')
    finally:await close_fixture(f)
