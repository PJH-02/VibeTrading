"""Lead verifies CAP04 raw callback passes coordinator bounds before effects."""
from dataclasses import replace
from types import SimpleNamespace
from decimal import Decimal
import pytest
from core.models import Venue,Side
from execution.trading_contracts import StrategyActionPlan,GroupSubmitMode
from runtime.real_runner import RealExecutionRunner,RealExecutionRunnerConfig
from runtime.strategy_process import _result_document
from cap05_source_fixture import build_native_group,make_intent,make_action,make_market_event_and_view,CONFIG_VERSION,NOW,STRATEGY_ID
from test_cap05_native_integration import coordinator_for,close_fixture

@pytest.mark.asyncio
async def test_isolated_callback_cannot_dispatch_ungrouped_new_before_coordinator_validation(tmp_path):
    f=build_native_group(tmp_path,mode=GroupSubmitMode.SEQUENTIAL);g=coordinator_for(f)
    original=f['binance_transport'].request
    async def request(method,url,**kw):
        if method=='POST':kw['json_body']=kw.get('params')
        return await original(method,url,**kw)
    f['binance_transport'].request=request
    i=make_intent('invalid-follow',venue=Venue.BINANCE,account=f['binance_scope'].account,side=Side.BUY,quantity='0.1')
    a=replace(make_action('invalid-follow',f['binance_scope'],i),group_id=None)
    invalid=StrategyActionPlan('invalid-normal-plan',STRATEGY_ID,CONFIG_VERSION,NOW,(a,))
    class Worker:
        strategy_id=STRATEGY_ID
        expected={'strategy_id':STRATEGY_ID}
        alive=True;inflight=False;queued=0
        budgets=SimpleNamespace(callback_timeout_ns=1_000_000_000)
        feedback_count=0
        def submit_event(self,**kw):self.inflight=True;return {'kind':'market'}
        def submit_feedback(self,**kw):self.inflight=True;self.feedback_count+=1;return {'kind':'order'}
        async def await_response(self,req,**kw):
            self.inflight=False
            return _result_document(f['plan'] if req['kind']=='market' else (invalid if self.feedback_count==1 else []))
    worker=Worker()
    class Ready(dict):
        def __call__(self):return self[f['binance_scope'].key]()
    ready=Ready(f['readiness'])
    runner=RealExecutionRunner(RealExecutionRunnerConfig(job_id='cap05-job',account_id=f['binance_scope'].account,destination_name='fixture',max_order_notional=Decimal('1000000'),max_position_notional=Decimal('1000000')),store=object(),bus=object(),settings=object(),owner=f['binance_owner'],group_coordinator=g)
    event,data=make_market_event_and_view()
    try:
        await runner.dispatch_strategy_market_event(None,event,data,registry=f['registry'],readiness=ready,evidence={**f['evidence'][f['binance_scope'].key],**f['evidence']},isolated_worker=worker)
        assert worker.feedback_count>=1
        assert f['binance_owner'].ledger.get(i.intent_id) is None, 'raw isolated source plan executed before group ownership/budget validation'
        assert len(f['binance_transport'].calls)==1
        facts=g.ledger.group_facts(f['plan'].actions[0].group_id)
        assert any('fault' in fact['kind'] for fact in facts), 'rejected callback must be visible as a durable group fault'
    finally:await close_fixture(f)
