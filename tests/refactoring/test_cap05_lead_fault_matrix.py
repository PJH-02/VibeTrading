"""Independent exact native outcomes through real two-venue adapter mappings."""
from decimal import Decimal
import pytest
from cap05_source_fixture import build_native_group
from test_cap05_native_integration import coordinator_for,close_fixture

@pytest.mark.asyncio
@pytest.mark.parametrize('fail_both',[False,True])
async def test_concurrent_unknown_does_not_suppress_sibling_and_duplicate_never_resends(tmp_path,fail_both):
    f=build_native_group(tmp_path,fail_binance=True,fail_bybit=fail_both);g=coordinator_for(f)
    try:
        kw=dict(registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        r=await g.execute(f['plan'],**kw)
        assert r['state'] in {'UNKNOWN','BLOCKED','RECONCILING'}
        assert len(f['binance_transport'].calls)==len(f['bybit_transport'].calls)==1
        assert f['binance_owner'].ledger.get(f['plan'].actions[0].intent.intent_id)['state']=='UNKNOWN'
        assert f['bybit_owner'].ledger.get(f['plan'].actions[1].intent.intent_id)['state']==('UNKNOWN' if fail_both else 'SUBMITTED')
        before=(f['binance_owner'].ledger.reserved(),f['bybit_owner'].ledger.reserved())
        assert before==(Decimal('10.02'),Decimal('20.04'))
        duplicate=await g.execute(f['plan'],**kw)
        assert duplicate['duplicate']
        assert len(f['binance_transport'].calls)==len(f['bybit_transport'].calls)==1
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_unequal_partial_native_fill_fee_receipts_remain_exact_once_and_current(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        await g.execute(f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        a,b=f['plan'].actions
        x=f['registry'].binding('binance-perp').adapter._fill({'symbol':'BTCUSDT','id':9101,'orderId':7001,'side':'BUY','qty':'0.04','price':'100','commission':'0.008','commissionAsset':'USDT','time':1000,'maker':False})
        y=f['registry'].binding('bybit-perp').adapter._fill({'symbol':'BTCUSDT.P','execId':'9102','orderId':'bybit-7001','orderLinkId':'c-bybit','side':'Sell','execQty':'0.15','execPrice':'100','execFee':'0.03','execTime':'1000','isMaker':False})
        for owner,act,native in [(f['binance_owner'],a,x),(f['bybit_owner'],b,y)]:
            args=dict(fill_id=native['event_id'],quantity=native['quantity'],price=native['price'],fee=native['fee'])
            assert owner.apply_fill(act,**args)
            assert owner.apply_fill(act,**args) is False
        r=g.report(a.group_id)
        current={leg['scope_key']:leg['current'] for leg in r['legs']}
        assert Decimal(current[a.scope.key]['filled'])==Decimal('0.04')
        assert Decimal(current[b.scope.key]['filled'])==Decimal('0.15')
        assert Decimal(current[a.scope.key]['fees'])==Decimal('0.008')
        assert Decimal(current[b.scope.key]['fees'])==Decimal('0.03')
        assert f['binance_owner'].ledger.reserved()==Decimal('6.012')
        assert f['bybit_owner'].ledger.reserved()==Decimal('5.010')
        assert r['economic_neutrality']=='NOT_ASSESSED'
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_native_explicit_b_rejection_and_a_fullfill_are_separate_exact_facts(tmp_path):
    from execution.perp_execution import HttpResponse
    f=build_native_group(tmp_path);g=coordinator_for(f)
    request=f['bybit_transport'].request
    async def reject(method,url,**kw):
        response=await request(method,url,**kw)
        if method=='POST' and url.endswith('/order/create'):
            return HttpResponse(200,{'retCode':110007,'retMsg':'Insufficient available balance','result':{}})
        return response
    f['bybit_transport'].request=reject
    try:
        await g.execute(f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        a,b=f['plan'].actions
        native=f['registry'].binding('binance-perp').adapter._fill({'symbol':'BTCUSDT','id':9201,'orderId':7001,'side':'BUY','qty':'0.1','price':'100','commission':'0.02','commissionAsset':'USDT','time':1000,'maker':False})
        assert f['binance_owner'].apply_fill(a,fill_id=native['event_id'],quantity=native['quantity'],price=native['price'],fee=native['fee'])
        r=g.report(a.group_id);cur={x['scope_key']:x['current'] for x in r['legs']}
        assert cur[a.scope.key]['state']=='FILLED'
        assert cur[b.scope.key]['state']=='REJECTED', 'authoritative venue rejection is not ACK loss or ambiguous transport'
        assert Decimal(cur[a.scope.key]['filled'])==Decimal('0.1')
        assert Decimal(cur[b.scope.key]['filled'])==0
        assert Decimal(cur[a.scope.key]['fees'])==Decimal('0.02')
        assert Decimal(cur[b.scope.key]['reservation'])==0
        assert len(f['binance_transport'].calls)==len(f['bybit_transport'].calls)==1
    finally:await close_fixture(f)
