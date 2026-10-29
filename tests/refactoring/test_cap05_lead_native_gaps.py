"""Actual native private gap and non-advancing REST page fencing."""
import pytest
from dataclasses import replace
from execution.perp_execution import HttpResponse
from cap05_source_fixture import build_native_group,NOW
from test_cap05_native_integration import coordinator_for,close_fixture
from test_cap05_lead_boundaries import followup

@pytest.mark.asyncio
async def test_concrete_private_gap_readiness_prevents_both_group_sends(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    gap=f['registry'].binding('bybit-perp').adapter.normalize_gap('private websocket disconnected')
    assert gap['requires_reconciliation'] is True and gap['ready'] is False
    old=f['readiness'][f['bybit_scope'].key]
    f['readiness'][f['bybit_scope'].key]=lambda:replace(old(),risk_verified=gap['ready'])
    try:
        r=await g.execute(f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        assert r['mutations_attempted']==0
        assert not f['binance_transport'].calls and not f['bybit_transport'].calls
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_real_bybit_incomplete_page_cannot_clear_known_open_group_risk_fence(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f);reads=[]
    try:
        await g.execute(f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        f['binance_transport'].native_orders={'c-binance':{'status':'NEW','cumulative':'0','quantity':'0.1','native_id':7001}}
        f['bybit_transport'].native_orders={'c-bybit':{'status':'New','cumulative':'0','quantity':'0.2','native_id':'bybit-7001','terminal':False}}
        request=f['bybit_transport'].request
        async def stuck(method,url,**kw):
            if method=='GET' and 'execution/list' in url:
                reads.append(kw.get('params',{}).get('cursor'))
                return HttpResponse(200,{'retCode':0,'result':{'list':[],'nextPageCursor':'same-native-page'}})
            return await request(method,url,**kw)
        f['bybit_transport'].request=stuck
        r=await g.reconcile('cap05-group',registry=f['registry'],start_ns=NOW,end_ns=NOW+10**9)
        assert r['reconciled'] is False and r['state']=='RECONCILING'
        assert len(reads)==2, 'actual adapter cursor loop must fail within bounded native pages'
        before=len(f['binance_transport'].calls)
        try:await g.execute_followup(followup(f,'after-native-page-gap'),registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        except (ValueError,RuntimeError):pass
        assert len(f['binance_transport'].calls)==before
        assert len(f['bybit_transport'].calls)==1
        assert f['binance_owner'].ledger.reserved()>0 and f['bybit_owner'].ledger.reserved()>0
    finally:await close_fixture(f)
