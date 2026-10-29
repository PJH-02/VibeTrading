"""Independent lifecycle accounting, capacity and native receipt crash tests."""
from dataclasses import replace
from decimal import Decimal
import pytest
from cap05_source_fixture import build_native_group
from test_cap05_native_integration import coordinator_for,close_fixture
from test_cap05_lead_boundaries import followup,execute
from test_cap05_lead_recovery_funding import receipt

@pytest.mark.asyncio
async def test_followup_leg_is_current_group_member_and_counts_in_next_group_cap(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    policy=dict(f['plan'].group_policy);policy['notional_caps']={f['binance_scope'].key:'1000',f['bybit_scope'].key:'35'}
    f['plan']=replace(f['plan'],group_policy=policy)
    try:
        await execute(g,f)
        first=followup(f,'first-follow')
        await g.execute_followup(first,registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        r=g.report(f['plan'].actions[0].group_id)
        assert any(x['intent_id']=='intent-first-follow' for x in r['legs']), 'followup disappeared from durable group membership/report/recovery'
        assert Decimal(r['pending_reservation_by_currency']['scope:'+f['bybit_scope'].key])==f['bybit_owner'].ledger.reserved()==Decimal('30.06')
        before=len(f['bybit_transport'].calls)
        try:await g.execute_followup(followup(f,'second-follow'),registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        except (ValueError,RuntimeError):pass
        assert len(f['bybit_transport'].calls)==before, 'second followup excluded first followup from cumulative cap'
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_same_followup_plan_replay_does_not_consume_another_budget_or_resend(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        await execute(g,f);p=followup(f,'replay-follow');gid=f['plan'].actions[0].group_id
        kwargs=dict(registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        await g.execute_followup(p,**kwargs)
        count=g._followup_used(gid);calls=len(f['bybit_transport'].calls)
        r=await g.execute_followup(p,**kwargs)
        assert r['duplicate'] is True
        assert g._followup_used(gid)==count==1
        assert len(f['bybit_transport'].calls)==calls
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_failed_followup_cap_releases_definitely_unsent_admission_and_reserve(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    policy=dict(f['plan'].group_policy);policy['notional_caps']={f['binance_scope'].key:'1000',f['bybit_scope'].key:'25'}
    f['plan']=replace(f['plan'],group_policy=policy)
    try:
        await execute(g,f);p=followup(f,'rejected-follow');o=f['bybit_owner'];reserve=o.ledger.reserved()
        try:await g.execute_followup(p,registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        except (ValueError,RuntimeError):pass
        assert len(f['bybit_transport'].calls)==1
        assert o._pending==0 and o.ledger.reserved()==reserve
        assert not o._prepared_inventory
        assert any(x['kind']=='followup-fault' for x in g.ledger.group_facts(f['plan'].actions[0].group_id))
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_used_prepared_inventory_does_not_grow_on_valid_duplicate_dispatch(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        await execute(g,f);o=f['binance_owner'];a=f['plan'].actions[0]
        for _ in range(12):
            token=await o.prepare_group_leg(a,group_id=a.group_id,registry=f['registry'],readiness=f['readiness'][a.scope.key],evidence=f['evidence'][a.scope.key])
            result=await o.dispatch_prepared(token)
            assert result['duplicate']
        assert o._pending==0
        assert not o._prepared_inventory, 'one-use token history retained unbounded prepared objects'
        assert len(f['binance_transport'].calls)==1
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_funding_receipt_version_change_cannot_credit_same_native_payment_twice(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        await execute(g,f);gid=f['plan'].actions[0].group_id;r=receipt(f,gid,'1')
        g.record_group_funding(gid,[r])
        changed={**r,'version':2}
        with pytest.raises((ValueError,RuntimeError)):g.record_group_funding(gid,[changed])
        assert Decimal(g.report(gid)['funding']['attributed_by_currency']['USDT'])==1
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_nonanchor_native_funding_survives_crash_after_owning_account_commit(tmp_path):
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        await execute(g,f);gid=f['plan'].actions[0].group_id;r=receipt(f,gid,'0.25')
        native=f['bybit_owner'].ledger
        r.update(scope_key=f['bybit_scope'].key,native_account_id=native.native_account_id)
        original=native.record_group_fact
        def commit_then_cut(*a,**kw):
            result=original(*a,**kw)
            if len(a)>=3 and a[2]=='funding-receipt':raise OSError('cut after native child funding fact committed')
            return result
        native.record_group_fact=commit_then_cut
        try:g.record_group_funding(gid,[r])
        except OSError:pass
        finally:native.record_group_fact=original
        assert any(x['kind']=='funding-receipt' for x in native.group_facts(gid))
        recovered=coordinator_for(f)
        assert Decimal(recovered.report(gid)['funding']['attributed_by_currency']['USDT'])==Decimal('0.25'), 'native child receipt must not depend on a cross-ledger derived mirror'
        recovered.record_group_funding(gid,[r])
        assert Decimal(recovered.report(gid)['funding']['attributed_by_currency']['USDT'])==Decimal('0.25')
    finally:await close_fixture(f)
