"""Lead independent group-boundary checks, actual scripted Binance/Bybit adapters."""
from dataclasses import replace
from decimal import Decimal
import pytest
from execution.trading_contracts import StrategyActionPlan,ExecutionAction,ActionKind,GroupSubmitMode
from cap05_source_fixture import build_native_group,NOW,make_intent,make_action
from test_cap05_native_integration import coordinator_for,close_fixture
from core.models import Venue,Side

async def execute(g,f,plan=None):
    return await g.execute(plan or f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])

def followup(f,suffix='extra'):
    sc=f['bybit_scope']; p=f['plan']
    i=make_intent(suffix,venue=Venue.BYBIT,account=sc.account,side=Side.BUY,quantity='0.1',raw_symbol='BTCUSDT.P')
    a=replace(make_action(suffix,sc,i),group_id=p.actions[0].group_id)
    return StrategyActionPlan('plan-'+suffix,p.strategy_id,p.config_version,NOW,(a,),group_followup=True)

@pytest.mark.asyncio
async def test_exact_native_notional_cap_does_not_double_multiply_fee(tmp_path):
    f=build_native_group(tmp_path); g=coordinator_for(f)
    try:
        policy=dict(f['plan'].group_policy)
        policy['notional_caps']={f['binance_scope'].key:'10',f['bybit_scope'].key:'20'}
        policy['cost_caps']={f['binance_scope'].key:'0.02',f['bybit_scope'].key:'0.04'}
        p=replace(f['plan'],group_policy=policy)
        r=await execute(g,f,p)
        assert r['state']=='ACKNOWLEDGED',r
        assert len(f['binance_transport'].calls)==len(f['bybit_transport'].calls)==1
    finally: await close_fixture(f)

@pytest.mark.asyncio
@pytest.mark.parametrize('fence',['sibling_unknown','deadline','aggregate_cap'])
async def test_group_fence_blocks_new_on_healthy_child(tmp_path,fence):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        p=f['plan']
        if fence=='aggregate_cap':
            policy=dict(p.group_policy); policy['notional_caps']={f['binance_scope'].key:'1000',f['bybit_scope'].key:'25'}
            p=replace(p,group_policy=policy);f['plan']=p
        await execute(g,f)
        if fence=='sibling_unknown':f['binance_owner'].ledger.outcome(p.actions[0].intent.intent_id,'UNKNOWN',{'native':'gap'})
        if fence=='deadline':g.clock=lambda:NOW+950_000_000
        old=len(f['bybit_transport'].calls)
        try:await g.execute_followup(followup(f),registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        except (ValueError,RuntimeError): pass
        assert len(f['bybit_transport'].calls)==old, 'forbidden group NEW reached healthy child transport'
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_cancel_cannot_steal_same_strategy_order_from_another_group(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    original=f['binance_transport'].request
    async def request(method,url,**kw):
        if method=='POST':kw['json_body']=kw.get('params')
        return await original(method,url,**kw)
    f['binance_transport'].request=request
    try:
        await execute(g,f)
        sc=f['binance_scope']; old=f['plan'];i=make_intent('unrelated',venue=Venue.BINANCE,account=sc.account,side=Side.BUY,quantity='0.1')
        a=replace(make_action('unrelated',sc,i),group_id=None)
        owner=f['binance_owner'];result=await owner.dispatch_action(a,registry=f['registry'],readiness=f['readiness'][sc.key],evidence=f['evidence'][sc.key])
        assert result['status']=='SUBMITTED'
        c=ExecutionAction('steal-cancel',ActionKind.CANCEL,sc,old.strategy_id,old.config_version,a.mode,a.expiry_ns,target_order_id=result['order_id'],group_id=old.actions[0].group_id)
        p=StrategyActionPlan('steal-plan',old.strategy_id,old.config_version,NOW,(c,),group_followup=True)
        try:await g.execute_followup(p,registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        except (ValueError,RuntimeError):pass
        assert owner.ledger.get(i.intent_id)['state']=='SUBMITTED', 'foreign-to-group cancel acquired a durable mutation claim'
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_sequential_preflight_failure_does_not_leak_owner_admission(tmp_path):
    f=build_native_group(tmp_path,mode=GroupSubmitMode.SEQUENTIAL);g=coordinator_for(f)
    try:
        r=dict(f['readiness']);r[f['bybit_scope'].key]=lambda:None
        await g.execute(f['plan'],registry=f['registry'],readiness=r,evidence=f['evidence'])
        assert not f['binance_transport'].calls and not f['bybit_transport'].calls
        assert f['binance_owner']._pending==0, 'definitely-not-sent prepared handoff leaks bounded admission'
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_peer_readiness_drift_at_actual_first_permit_blocks_both_native_sends(tmp_path):
    import asyncio
    f=build_native_group(tmp_path);g=coordinator_for(f)
    fresh=f['readiness'][f['bybit_scope'].key];bad=[False]
    f['readiness'][f['bybit_scope'].key]=lambda:replace(fresh(),feed_ready=not bad[0])
    original=f['binance_transport'].request
    async def request(method,url,**kw):
        if method=='POST':
            bad[0]=True
            await asyncio.sleep(0)
        return await original(method,url,**kw)
    f['binance_transport'].request=request
    try:
        await execute(g,f)
        assert not f['binance_transport'].calls and not f['bybit_transport'].calls, 'peer validity must be rechecked after awaited transport before first native mutation'
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_mutating_one_use_token_cannot_resend_or_release_known_submitted_reserve(tmp_path):
    f=build_native_group(tmp_path);owner=f['binance_owner'];a=f['plan'].actions[0]
    try:
        kw=dict(registry=f['registry'],readiness=f['readiness'][a.scope.key],evidence=f['evidence'][a.scope.key])
        token=await owner.prepare_group_leg(a,group_id=a.group_id,**kw)
        result=await owner.dispatch_prepared(token)
        assert result['status']=='SUBMITTED'
        before=owner.ledger.get(a.intent.intent_id);calls=len(f['binance_transport'].calls)
        try:
            token.used=False
            await owner.dispatch_prepared(token)
        except (ValueError,RuntimeError,AttributeError):pass
        after=owner.ledger.get(a.intent.intent_id)
        assert len(f['binance_transport'].calls)==calls
        assert after['state']==before['state']=='SUBMITTED'
        assert Decimal(after['reservation'])==Decimal(before['reservation'])>0
        assert owner._pending==0
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_late_fill_source_literal_followup_is_dispatched_after_execute_returns(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    seen=[]
    def source(event,context):
        seen.append(event.status)
        return followup(f,'late-hedge') if event.status=='PARTIAL_FILL' else None
    try:
        await g.execute(f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'],on_event=source)
        a=f['plan'].actions[0];owner=f['binance_owner']
        owner.apply_fill(a,fill_id='late-after-execute',quantity='0.04',price='100',fee='0.008')
        await owner.deliver_feedback(a.strategy_id,owner._source_dispatch[a.strategy_id]['callback'])
        assert 'PARTIAL_FILL' in seen
        assert len(f['bybit_transport'].calls)==2, 'durably counted literal source hedge remained stranded in an old execute-local callback queue'
        assert f['bybit_owner'].ledger.get('intent-late-hedge') is not None
    finally:await close_fixture(f)

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['history_gap','callback_fault'])
async def test_durable_group_fault_fences_new_even_when_child_orders_are_known_open(tmp_path,fault):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        await execute(g,f);gid=f['plan'].actions[0].group_id
        if fault=='history_gap':
            g._save_state(gid,'RECONCILING',{'reason':'native private history gap','history_complete':False})
        else:
            g._record(gid,'lead-callback-fault','followup-fault',{'reason':'invalid source action rejected'})
        old=len(f['bybit_transport'].calls)
        try:await g.execute_followup(followup(f,'after-'+fault),registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        except (ValueError,RuntimeError):pass
        assert len(f['bybit_transport'].calls)==old, 'durable group uncertainty/fault must fence NEW independent of known child ACKs'
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_cold_known_orders_require_native_reconciliation_before_group_followup_new(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    await execute(g,f)
    await close_fixture(f)
    fresh=build_native_group(tmp_path);restarted=coordinator_for(fresh)
    try:
        old=len(fresh['bybit_transport'].calls)
        try:await restarted.execute_followup(followup(fresh,'cold-new'),registry=fresh['registry'],readiness=fresh['readiness'],evidence=fresh['evidence'])
        except (ValueError,RuntimeError):pass
        assert len(fresh['bybit_transport'].calls)==old, 'persisted ACK is historical after process restart; group must query/reconcile before new risk'
    finally:await close_fixture(fresh)

@pytest.mark.asyncio
async def test_late_original_group_followup_cannot_borrow_new_group_policy_caps(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    p=f['plan'];policy=dict(p.group_policy);policy['notional_caps']={f['binance_scope'].key:'1000',f['bybit_scope'].key:'25'}
    f['plan']=replace(p,group_policy=policy)
    def source(event,context):return followup(f,'cross-group-late') if event.status=='PARTIAL_FILL' else None
    try:
        await g.execute(f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'],on_event=source)
        a=f['plan'].actions[0]
        f['binance_owner'].apply_fill(a,fill_id='old-queued-fill',quantity='0.04',price='100',fee='0.008')
        acts=[]
        for act in p.actions:
            order=act.intent.model_copy(update={'intent_id':act.intent.intent_id+'-next','client_order_id':act.intent.client_order_id+'-next'})
            acts.append(replace(act,action_id=act.action_id+'-next',intent=order,group_id='next-group'))
        q=replace(p,plan_id='next-group-plan',actions=tuple(acts))
        await g.execute(q,registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'],on_event=source)
        payloads=f['bybit_transport'].post_payloads
        assert not any(x.get('orderLinkId')=='c-cross-group-late' for x in payloads), 'late group1 callback was dispatched under group2 wider caps'
    finally:await close_fixture(f)
