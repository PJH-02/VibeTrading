"""Lead-owned public assembly and adversarial tests, offline only."""
import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import pytest
from runtime.selected_execution import parse_strategy_selections, NativeSelectionBinding
from test_cap06_selection import selection


def test_nested_config_is_detached_and_immutable():
    row=selection(); row['registration']['config']['nested']={'a':['original']}
    parsed=parse_strategy_selections([row])[0]
    row['registration']['config']['nested']['a'][0]='mutated'
    assert parsed.registration['config']['nested']['a']==('original',)
    with pytest.raises(TypeError):parsed.registration['config']['nested']['a']='replace'


@pytest.mark.parametrize('value',[float('nan'),float('inf'),object(),{'not-json'}])
def test_noncanonical_config_rejected(value):
    row=selection();row['registration']['config']['bad']=value
    with pytest.raises((ValueError,TypeError)):parse_strategy_selections([row])


@pytest.mark.asyncio
@pytest.mark.parametrize("delayed_peer",[False,True])
async def test_public_run_engine_consumes_native_selection_and_keeps_borrowed_owner(tmp_path,monkeypatch,delayed_peer):
    import runtime.local_runtime as runtime
    import cap06_source_fixture as source_module
    from test_cap06_routing import prepare_scripted_reconcile
    from cap05_source_fixture import build_native_group,make_market_event_and_view,CONFIG_VERSION,NOW,STRATEGY_ID
    from test_local_runtime import document
    from runtime.local_config import LocalRuntimePlan
    from runtime.real_runner import RealExecutionRunner,RealExecutionRunnerConfig
    from execution.trading_contracts import StrategyActionPlan
    from feeds.feed_recovery import EngineCursor,RecoveryFrame
    from market.local_engine import LocalMarketEngine
    fixture=build_native_group(tmp_path/'native');owner=fixture['binance_owner'];key=owner.ledger.scope.key
    source=source_module.create_strategy()
    action=replace(fixture['plan'].actions[0],group_id=None)
    source_module.PLAN=StrategyActionPlan('lead-public',STRATEGY_ID,CONFIG_VERSION,NOW,(action,))
    event,_=make_market_event_and_view();event=event.model_copy(update={'provider':'kiwoom'})
    row=selection();row.update(venue=owner.ledger.scope.venue,account=owner.ledger.scope.account,product=owner.ledger.scope.product)
    row['registration'].update(accounts=[key],required_feeds=[event.instrument.key],config_version=CONFIG_VERSION)
    row['registration']['risk']={key:next(iter(row['registration']['risk'].values()))}
    row['registration']['config']['config_version']=CONFIG_VERSION
    doc=document(tmp_path);doc['strategy_selections']=[row]
    if delayed_peer:
        doc['engine']['max_state_callbacks']=2
        from copy import deepcopy
        peer_scope=fixture['bybit_owner'].ledger.scope
        peer_row=deepcopy(row)
        peer_row.update(venue=peer_scope.venue,account=peer_scope.account,product=peer_scope.product)
        peer_row['registration'].update(job_id='cap06-peer',accounts=[peer_scope.key])
        peer_row['registration']['risk']={peer_scope.key:next(iter(row['registration']['risk'].values()))}
        doc['strategy_selections'].append(peer_row)
    plan=LocalRuntimePlan.from_document(doc)
    runner=RealExecutionRunner(RealExecutionRunnerConfig('lead-public',owner.ledger.scope.account,'fixture',Decimal('1000000'),Decimal('1000000')),store=object(),bus=object(),settings=object(),owner=owner)
    facts={'accounts':{key:{'scope':{'venue':owner.ledger.scope.venue,'account':owner.ledger.scope.account,'product':owner.ledger.scope.product,'environment':'sandbox'},'mode':'sandbox'}}}
    binding=NativeSelectionBinding(runner,owner,source,fixture['registry'],fixture['readiness'][key],lambda *_:{'config_version':CONFIG_VERSION,**fixture['evidence'][key]},facts,'binance-perp')
    prepare_scripted_reconcile(fixture)
    bindings={'cap06-job':binding}
    peer_started=asyncio.Event()
    if delayed_peer:
        peer_owner=fixture['bybit_owner']
        peer_runner=RealExecutionRunner(RealExecutionRunnerConfig('lead-delayed-peer',peer_scope.account,'fixture',Decimal('1000000'),Decimal('1000000')),store=object(),bus=object(),settings=object(),owner=peer_owner)
        peer_source=source_module.create_strategy()
        peer_facts={'accounts':{peer_scope.key:{'scope':{'venue':peer_scope.venue,'account':peer_scope.account,'product':peer_scope.product,'environment':'sandbox'},'mode':'sandbox'}}}
        bindings['cap06-peer']=NativeSelectionBinding(peer_runner,peer_owner,peer_source,fixture['registry'],fixture['readiness'][peer_scope.key],lambda *_:{'config_version':CONFIG_VERSION,**fixture['evidence'][peer_scope.key]},peer_facts,'bybit-perp')
        async def delayed_read(*args,**kwargs):
            peer_started.set()
            await asyncio.Event().wait()
        fixture['bybit_transport'].request=delayed_read
    captured=[]
    def engine_factory(**kwargs):
        engine=LocalMarketEngine(**kwargs);captured.append(engine);return engine
    class Server:
        def __init__(self,*args,**kwargs):pass
        async def start(self):pass
        async def close(self):pass
    monkeypatch.setattr(runtime,'LocalMarketEngine',engine_factory)
    monkeypatch.setattr(runtime,'LocalTransportServer',Server)
    monkeypatch.setattr(runtime.time,'time_ns',lambda:NOW)
    task=asyncio.create_task(runtime.run_engine(plan,SimpleNamespace(state_dir=tmp_path),native_selection_bindings=bindings))
    try:
        async def started():
            while not captured or captured[0]._watchdog is None:
                if task.done():await task
                await asyncio.sleep(.001)
        await asyncio.wait_for(started(),3)
        engine=captured[0]
        assert len(engine.post_apply_callbacks)==(2 if delayed_peer else 1)
        assert engine.initial_resume(EngineCursor('kiwoom','run',0))
        engine.on_frame(RecoveryFrame(EngineCursor('kiwoom','run',1),event.model_dump_json().encode()))
        dispatcher=engine.post_apply_callbacks[0].__self__
        await asyncio.wait_for(dispatcher.queue.join(),3)
        assert engine.hot_path.apply_seq==1
        assert source.calls==1
        assert not dispatcher.errors,dispatcher.errors
        assert dispatcher.results[0]['results'][0]['status']=='SUBMITTED'
        assert len(fixture['binance_transport'].calls)==1
        assert fixture['bybit_transport'].calls==[]
        if delayed_peer:
            await asyncio.wait_for(peer_started.wait(),1)
            assert peer_source.calls==0
            peer_dispatcher=engine.post_apply_callbacks[1].__self__
            assert peer_dispatcher._promotion_receipt is None
            assert not peer_dispatcher.task.done()
    finally:
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        if captured:
            for callback in captured[0].post_apply_callbacks:
                dispatcher=callback.__self__
                assert dispatcher.task.done()
                assert dispatcher.blocked
                assert dispatcher._promotion_receipt is None
                assert not dispatcher._promotion_guard()
        # The assembly borrows owners; stopping its task must not release the caller's ledger.
        assert owner.ledger.account_version()>=0
        await owner.close();await fixture['bybit_owner'].close()


def native_fixture(tmp_path):
    import cap06_source_fixture as source_module
    from cap05_source_fixture import build_native_group,make_market_event_and_view,CONFIG_VERSION,NOW,STRATEGY_ID
    from runtime.real_runner import RealExecutionRunner,RealExecutionRunnerConfig
    from runtime.selected_execution import NativeSelectionDispatcher
    from execution.trading_contracts import StrategyActionPlan
    from test_cap06_routing import prepare_scripted_reconcile
    f=build_native_group(tmp_path);owner=f['binance_owner'];scope=owner.ledger.scope
    event,data=make_market_event_and_view();key=scope.key
    action=replace(f['plan'].actions[0],group_id=None)
    source_module.PLAN=StrategyActionPlan('lead-adverse',STRATEGY_ID,CONFIG_VERSION,NOW,(action,))
    row=selection();row.update(venue=scope.venue,account=scope.account,product=scope.product)
    row['registration'].update(accounts=[key],required_feeds=[event.instrument.key],config_version=CONFIG_VERSION)
    row['registration']['risk']={key:next(iter(row['registration']['risk'].values()))}
    runner=RealExecutionRunner(RealExecutionRunnerConfig('lead-adverse',scope.account,'fixture',Decimal('1000000'),Decimal('1000000')),store=object(),bus=object(),settings=object(),owner=owner)
    facts={'accounts':{key:{'scope':{'venue':scope.venue,'account':scope.account,'product':scope.product,'environment':scope.environment},'mode':scope.environment}}}
    d=NativeSelectionDispatcher(parse_strategy_selections([row])[0],NativeSelectionBinding(runner,owner,source_module.create_strategy(),f['registry'],f['readiness'][key],lambda *_:{'config_version':CONFIG_VERSION,**f['evidence'][key]},facts,'binance-perp'))
    prepare_scripted_reconcile(f)
    return f,d,action


@pytest.mark.asyncio
async def test_empty_native_position_inventory_cannot_promote_nonzero_local_position(tmp_path):
    from cap05_source_fixture import NOW
    f,d,action=native_fixture(tmp_path);owner=f['binance_owner']
    try:
        _,row=owner.ledger.authorize(action.intent,evidence={'source':'lead-position'},expiry_ns=NOW+1000000000,epochs={},reserve_unit=Decimal('100'),route_id='binance-perp',strategy_id=action.strategy_id,config_version=action.config_version)
        owner.ledger.mark_handoff(row['intent_id']);owner.ledger.bind_native_order(row['intent_id'],'native-position')
        owner.ledger.outcome(row['intent_id'],'SUBMITTED',{'order_id':'native-position'})
        owner.apply_fill(action,fill_id='lead-position-fill',quantity=action.intent.quantity,price=Decimal('100'),fee=Decimal('0'),order_id='native-position',fact_quality='VALID')
        assert owner.ledger.position(action.intent.instrument.key)!=0
        with pytest.raises(RuntimeError,match='position'):
            await d.prepare_promotion()
        assert f['binance_transport'].calls==[]
    finally:
        await owner.close();await f['bybit_owner'].close()


@pytest.mark.asyncio
async def test_control_epoch_change_during_native_read_cannot_mint_receipt(tmp_path):
    f,d,action=native_fixture(tmp_path);owner=f['binance_owner'];transport=f['binance_transport'];original=transport.request
    async def raced(method,url,**kwargs):
        response=await original(method,url,**kwargs)
        if '/fapi/v3/account' in url:owner.control.halt(action.strategy_id,scope='strategy')
        return response
    transport.request=raced
    try:
        with pytest.raises(RuntimeError,match='epochs changed'):
            await d.prepare_promotion()
        assert d._promotion_receipt is None
        assert f['binance_transport'].calls==[]
    finally:
        await owner.close();await f['bybit_owner'].close()


def test_same_canonical_plugin_runs_paper_without_constructing_native_clients(tmp_path,monkeypatch):
    import cap06_source_fixture as source_module
    import test_cap06_routing  # registers the identical native fixture module under its canonical name
    from test_strategy_runtime import T,budget,book,ledger_at,matcher_for,spec
    from cap05_source_fixture import CONFIG_VERSION,STRATEGY_ID,make_market_event_and_view,make_intent,make_action
    from core.models import Side,Venue
    from market.hot_path import HotPathStore
    from runtime.strategy_runtime import PaperStrategyRuntime
    from runtime.selected_execution import activate_paper_selections
    from execution.trading_contracts import TradingMode,StrategyActionPlan
    from execution.execution_owner import ExecutionOwner
    from execution.perp_execution import BinancePerpAdapter,BybitPerpAdapter
    def forbidden(*args,**kwargs):raise AssertionError('PAPER attempted a native constructor')
    for cls in (ExecutionOwner,BinancePerpAdapter,BybitPerpAdapter):monkeypatch.setattr(cls,'__init__',forbidden)
    trade,_=make_market_event_and_view();instrument=trade.instrument.model_copy(update={'currency':'USDT'});trade=trade.model_copy(update={'instrument':instrument})
    trade=trade.model_copy(update={'source_ts_ns':T,'receive_ts_ns':T})
    ledger=ledger_at(tmp_path,instrument);ledger.seed_balance('USDT',Decimal('100000'),{'source':'lead-paper'})
    host=PaperStrategyRuntime(hot_path=HotPathStore(),accounts={ledger.scope.key:matcher_for(ledger)},products={instrument.key:spec(instrument)},known_instruments={instrument.key:instrument},reference_feeds={},budgets=budget(),settings={},mode=TradingMode.PAPER,phase='paper',now_ns=lambda:T)
    intent=make_intent('lead-paper',venue=Venue.BINANCE,account=ledger.scope.account,side=Side.BUY,quantity='1').model_copy(update={'instrument':instrument,'job_id':'cap06-job','created_ts_ns':T})
    action=replace(make_action('lead-paper',ledger.scope,intent,TradingMode.PAPER),group_id=None,post_only=True,expiry_ns=T+1000000000)
    source_module.PLAN=StrategyActionPlan('lead-same-plugin',STRATEGY_ID,CONFIG_VERSION,T,(action,))
    row=selection('PAPER','paper');row.update(venue=ledger.scope.venue,account=ledger.scope.account,product=ledger.scope.product)
    row['registration'].update(accounts=[ledger.scope.key],required_feeds=[instrument.key],paper_permission=True,research_receipt={'research_id':'lead-scripted'})
    row['registration']['risk']={ledger.scope.key:{'funding_buffer':'0','account_notional_limit':'100000','strategy_notional_limit':'100000','account_capital_limit':'100000','strategy_capital_limit':'100000','margin_rate':'0.1'}}
    try:
        activate_paper_selections(SimpleNamespace(strategy_selections=parse_strategy_selections([row])),host,{'cap06-job':lambda *_:[]})
        host.process_event(book(instrument=instrument));host.process_event(trade)
        assert host._jobs[STRATEGY_ID].registration.config_version==CONFIG_VERSION
        assert ledger.active_orders(), (host._jobs[STRATEGY_ID].state,host._jobs[STRATEGY_ID].reason,host._jobs[STRATEGY_ID].callbacks,host._jobs[STRATEGY_ID].actions,ledger.get(action.intent.intent_id))
    finally:ledger.close()


def test_finite_json_config_numbers_are_preserved():
    row=selection();row["registration"]["config"]["threshold"]=1.25
    assert parse_strategy_selections([row])[0].registration["config"]["threshold"]==1.25


@pytest.mark.asyncio
async def test_selected_arcus_promotion_preserves_native_usd_quote_usdg_settlement(tmp_path):
    import test_cap06_routing
    import cap06_source_fixture as source_module
    from copy import deepcopy
    from test_exec01_arcus_native_quote import account,market,IDENTITY
    from test_perp_execution import ScriptedTransport,arcus_adapter,ADDRESS,NOW_NS
    from execution.execution_ledger import AccountScope,ExecutionLedger
    from execution.execution_owner import ExecutionOwner,ExecutionBudgets
    from execution.venues.registry import RouteSpec,VenueRegistry,AliasBinding,Capabilities,MANDATORY_REMOVAL_FACTS
    from runtime.runtime_control import RuntimeControl
    from runtime.real_runner import RealExecutionRunner,RealExecutionRunnerConfig
    from runtime.selected_execution import NativeSelectionDispatcher
    from cap05_source_fixture import STRATEGY_ID
    a=account();m=market();leverages={'leverages':[{'marketId':1,'marketDisplayName':'BTC-USD','leverage':5,'isolated':False,'marginMode':'CROSS'}]}
    transport=ScriptedTransport(a,{'markets':[m]},leverages,deepcopy(a),deepcopy(a),{'positions':{}},{'orders':[]},{'fills':[]},{'fundingPayments':[]})
    adapter,_=arcus_adapter(transport)
    native=await adapter.native_risk_snapshot('BTC-USD',native_identity=dict(IDENTITY,scope_key=adapter.scope.key),local_ledger_version=0,max_age_ns=10000000000)
    scope=AccountScope('arcus',adapter.scope.account_alias,'linear_perpetual','sandbox')
    route=RouteSpec('lead-arcus','arcus','linear','linear_perpetual','sandbox',scope,ADDRESS)
    registry=VenueRegistry(max_routes=1,max_aliases=1,max_fact_bytes=65536)
    registry.bind_existing_adapter(route,adapter,reference_provider=lambda:{key:0 for key in MANDATORY_REMOVAL_FACTS},capabilities=Capabilities({'linear_perpetual'},{'sandbox'},{'GTC'},True,True,False),native_facts_provider=lambda symbol:{**native,'taker_fee_rate':'0.001'})
    registry.register_aliases((AliasBinding(STRATEGY_ID,'lead-arcus-alias',route.route_id,'BTC-USD','linear','linear_perpetual','sandbox',scope),))
    ledger=ExecutionLedger(tmp_path/'arcus.sqlite3',scope,max_intents=8,max_evidence_bytes=65536,max_fills=8,authority_root=tmp_path/'authority',native_account_id=ADDRESS)
    owner=ExecutionOwner(ledger,RuntimeControl(ledger),object(),ExecutionBudgets(Decimal('1000000'),Decimal('1000000'),4,10000000000,1.0,1.0),clock=lambda:NOW_NS)
    runner=RealExecutionRunner(RealExecutionRunnerConfig('lead-arcus',scope.account,'fixture',Decimal('1000000'),Decimal('1000000')),store=object(),bus=object(),settings=object(),owner=owner)
    row=selection();row.update(venue=scope.venue,account=scope.account,product=scope.product)
    row['registration']['accounts']=[scope.key];row['registration']['risk']={scope.key:next(iter(row['registration']['risk'].values()))}
    facts={'accounts':{scope.key:{'scope':{'venue':scope.venue,'account':scope.account,'product':scope.product,'environment':'sandbox'},'mode':'sandbox'}}}
    try:
        d=NativeSelectionDispatcher(parse_strategy_selections([row])[0],NativeSelectionBinding(runner,owner,source_module.create_strategy(),registry,lambda:{},lambda *_:{},facts,route.route_id))
        await d.prepare_promotion()
        assert d._promotion_guard()
        assert native['account_facts']['normalized']['currency']=='USD'
        assert native['account_facts']['normalized']['settlement_currency']=='USDG'
        assert not any(call['method']=='WS' for call in transport.dispatched)
    finally:await owner.close()


@pytest.mark.asyncio
async def test_cash_position_without_durable_owner_allocation_blocks_promotion(tmp_path,monkeypatch):
    from runtime.selected_execution import NativeSelectionDispatcher
    from test_cap06_routing import test_selected_spot_promotes_after_exact_order_coverage_and_empty_inventory
    original=NativeSelectionDispatcher._prepare_cash_promotion
    async def unallocated(self,binding,adapter,symbol):
        provider=binding.native_facts_provider
        def facts(requested_symbol):
            value=provider(requested_symbol)
            value['account_facts']['physical_positions']={symbol:'1'}
            return value
        patched=SimpleNamespace(route=binding.route,capabilities=binding.capabilities,native_facts_provider=facts)
        return await original(self,patched,adapter,symbol)
    monkeypatch.setattr(NativeSelectionDispatcher,'_prepare_cash_promotion',unallocated)
    with pytest.raises(RuntimeError,match='unallocated physical position'):
        await test_selected_spot_promotes_after_exact_order_coverage_and_empty_inventory(tmp_path)
