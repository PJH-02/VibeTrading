"""Native read deadlines and cancellation keep bounded owner handoffs."""
import asyncio
from dataclasses import replace
import pytest
from cap05_source_fixture import build_native_group,NOW
from test_cap05_native_integration import coordinator_for,close_fixture

@pytest.mark.asyncio
async def test_native_fill_history_read_uses_existing_owner_deadline(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        await g.execute(f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        f['binance_transport'].native_orders={'c-binance':{'status':'NEW','cumulative':'0','quantity':'0.1','native_id':7001}}
        f['bybit_transport'].native_orders={'c-bybit':{'status':'New','cumulative':'0','quantity':'0.2','native_id':'bybit-7001','terminal':False}}
        owner=f['binance_owner'];owner.budgets=replace(owner.budgets,prepare_timeout_seconds=0.02)
        original=f['binance_transport'].request
        async def slow(method,url,**kw):
            if method=='GET' and 'userTrades' in url:await asyncio.Event().wait()
            return await original(method,url,**kw)
        f['binance_transport'].request=slow
        try:r=await asyncio.wait_for(g.reconcile('cap05-group',registry=f['registry'],start_ns=NOW,end_ns=NOW+10**9),timeout=0.5)
        except asyncio.TimeoutError:pytest.fail('group history read bypassed the existing owner API deadline')
        assert r['reconciled'] is False
        assert r['state'] in {'UNKNOWN','BLOCKED','RECONCILING'}
        assert owner.ledger.reserved()>0
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_outer_cancel_awaits_both_handoffs_and_reports_actual_unknown(tmp_path):
    class Hold:
        count=0
        def __init__(self):self.ready=asyncio.Event();self.release=asyncio.Event()
        async def wait(self):
            self.count+=1
            if self.count==2:self.ready.set()
            await self.release.wait()
    hold=Hold();f=build_native_group(tmp_path,barrier=hold);g=coordinator_for(f)
    task=asyncio.create_task(g.execute(f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence']))
    try:
        await asyncio.wait_for(hold.ready.wait(),timeout=0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,timeout=0.5)
        assert not any(not t.done() and 'dispatch_prepared' in str(t.get_coro()) for t in asyncio.all_tasks())
        for name in ['binance','bybit']:
            owner=f[name+'_owner']
            assert owner._pending==0 and owner.ledger.reserved()>0
            assert not owner._prepared_inventory
            assert owner.ledger.get(next(a.intent.intent_id for a in f['plan'].actions if a.scope.venue==name))['state']=='UNKNOWN'
        assert g.report('cap05-group')['state'] in {'UNKNOWN','RECONCILING','BLOCKED'}
    finally:
        if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
        await close_fixture(f)

@pytest.mark.asyncio
@pytest.mark.parametrize('drift',['group_deadline','peer_epoch'])
async def test_initial_actual_send_permit_rechecks_group_deadline_and_peer_epoch(tmp_path,drift):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    original=f['binance_transport'].request
    async def delayed(method,url,**kw):
        if method=='POST':
            if drift=='group_deadline':g.clock=lambda:NOW+950_000_000
            else:f['binance_owner'].control.halt(f['plan'].strategy_id)
            await asyncio.sleep(0)
        return await original(method,url,**kw)
    f['binance_transport'].request=delayed
    try:
        await g.execute(f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        assert not f['binance_transport'].calls and not f['bybit_transport'].calls, 'actual native send ignored group deadline or peer control cut after transport await'
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_followup_native_permit_rechecks_parent_deadline_after_transport_await(tmp_path):
    from test_cap05_lead_boundaries import followup
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        await g.execute(f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        original=f['bybit_transport'].request
        async def delayed(method,url,**kw):
            if method=='POST':
                g.clock=lambda:NOW+950_000_000
                await asyncio.sleep(0)
            return await original(method,url,**kw)
        f['bybit_transport'].request=delayed
        try:await g.execute_followup(followup(f,'late-permit'),registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        except (ValueError,RuntimeError):pass
        assert len(f['bybit_transport'].calls)==1
        assert f['bybit_owner']._pending==0
    finally:await close_fixture(f)
