"""Lead-owned adversarial group admission checks; credential-free real adapter seam."""
from dataclasses import replace
import pytest
from core.models import AssetClass,Venue
from execution.execution_owner import GateBlocked
from execution.trading_contracts import StrategyActionPlan,TradingMode
from test_exec01_native import register_perp,intent,action,evidence,readiness,NOW

@pytest.mark.asyncio
@pytest.mark.parametrize('entrypoint',['action','plan'])
async def test_grouped_new_cannot_bypass_two_owner_preparation(tmp_path,entrypoint):
    owner,registry,adapter,transport,scope=register_perp(tmp_path,native_proof=True)
    try:
        order=intent('lead-group-bypass',venue=Venue.BINANCE,asset=AssetClass.CRYPTO_PERP,
            symbol='BTCUSDT',account=scope.account,environment=scope.environment,
            quantity='0.1',price='100')
        act=replace(action('lead-group-action',scope,order,mode=TradingMode.SANDBOX),group_id='unprepared-group')
        kwargs=dict(registry=registry,readiness=readiness(owner,scope,order.instrument.key),
            evidence=evidence(owner,'alpha','sandbox'))
        with pytest.raises((GateBlocked,ValueError)):
            if entrypoint=='action':await owner.dispatch_action(act,**kwargs)
            else:await owner.dispatch_plan(StrategyActionPlan('lead-group-plan','alpha','cfg-1',NOW,(act,)),**kwargs)
        assert transport.calls==[]
        assert owner.ledger.get(order.intent_id) is None
    finally:await owner.close()

def test_group_policy_rejects_float_caps_instead_of_decimal_coercion():
    from execution.execution_ledger import AccountScope
    from execution.trading_contracts import ExecutionAction,ActionKind
    from decimal import Decimal
    def leg(venue,account,identity):
        sc=AccountScope(venue.value,account,'linear_perpetual','sandbox')
        order=intent(identity,venue=venue,asset=AssetClass.CRYPTO_PERP,symbol='BTCUSDT',
            account=account,environment='sandbox',quantity='0.1',price='100')
        return replace(action(identity+'-action',sc,order,mode=TradingMode.SANDBOX),group_id='float-caps')
    acts=(leg(Venue.BINANCE,'a','float-a'),leg(Venue.BYBIT,'b','float-b'))
    policy={'base_asset':'BTC','deadline_ns':NOW+1_000_000_000,'max_followup_actions':2,
        'notional_caps':{a.scope.key:1000.1 for a in acts},'cost_caps':{a.scope.key:'10' for a in acts}}
    with pytest.raises((ValueError,TypeError)):
        StrategyActionPlan('float-caps','alpha','cfg-1',NOW,acts,
            group_submit_mode='CONCURRENT',group_policy=policy)

@pytest.mark.asyncio
async def test_group_reservation_report_tracks_child_fill_instead_of_initial_snapshot(tmp_path):
    from cap05_source_fixture import build_native_group
    from test_cap05_native_integration import coordinator_for,close_fixture
    from decimal import Decimal
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        await g.execute(f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        a=f['plan'].actions[0]
        f['binance_owner'].apply_fill(a,fill_id='lead-partial',quantity='0.05',price='100',fee='0.01')
        r=g.report(a.group_id)
        expected=f['binance_owner'].ledger.reserved()+f['bybit_owner'].ledger.reserved()
        assert sum((Decimal(v) for v in r['pending_reservation_by_currency'].values()),Decimal(0))==expected
    finally:await close_fixture(f)

@pytest.mark.asyncio
async def test_group_unknown_child_cannot_keep_acknowledged_report(tmp_path):
    from cap05_source_fixture import build_native_group
    from test_cap05_native_integration import coordinator_for,close_fixture
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        await g.execute(f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
        a=f['plan'].actions[0]
        f['binance_owner'].ledger.outcome(a.intent.intent_id,'UNKNOWN',{'provenance':'native_uncertainty'})
        r=g.report(a.group_id)
        assert r['state'] in {'UNKNOWN','BLOCKED','RECONCILING'}
    finally:await close_fixture(f)

def test_single_owned_cancel_followup_is_representable_without_new_initial_pair():
    from cap05_source_fixture import NOW,STRATEGY_ID,CONFIG_VERSION
    from execution.execution_ledger import AccountScope
    from execution.trading_contracts import ExecutionAction,ActionKind
    sc=AccountScope('binance','cap05-binance','linear_perpetual','sandbox')
    cancel=ExecutionAction('source-cancel',ActionKind.CANCEL,sc,STRATEGY_ID,CONFIG_VERSION,
        TradingMode.SANDBOX,NOW+1_000_000_000,target_order_id='source-owned-leg',group_id='existing-group')
    p=StrategyActionPlan('source-cancel-plan',STRATEGY_ID,CONFIG_VERSION,NOW,(cancel,),group_followup=True)
    assert p.actions==(cancel,)
