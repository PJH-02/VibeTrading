"""Real child-process crash cuts, fresh registry/adapters and durable duplicate gate."""
from pathlib import Path
import json,subprocess,sys
import pytest
from cap05_source_fixture import build_native_group
from test_cap05_native_integration import coordinator_for,close_fixture
from runtime.strategy_process import plan_from_document

CHILD = r'''
import asyncio,os,sys,json
from pathlib import Path
from cap05_source_fixture import build_native_group
from test_cap05_native_integration import coordinator_for
from execution.trading_contracts import GroupSubmitMode
async def run():
 p=Path(sys.argv[1]); phase=sys.argv[2];f=build_native_group(p,mode=GroupSubmitMode.SEQUENTIAL);g=coordinator_for(f)
 if phase=='after_a_prepare':
  async def cut(*a,**kw):os._exit(37)
  f['bybit_owner'].prepare_group_leg=cut
 else:
  original=f['binance_transport'].request
  async def request(method,url,**kw):
   if method=='POST':kw['json_body']=kw.get('params')
   response=await original(method,url,**kw)
   if method=='POST':
    marker=p/'scripted-exchange-created.json'
    with marker.open('w') as fh:
     json.dump({'client':'c-binance','native_order_id':7001,'create_count':1},fh);fh.flush();os.fsync(fh.fileno())
    os._exit(39)
   return response
  f['binance_transport'].request=request
 await g.execute(f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'])
 raise RuntimeError('crash cut was not exercised')
asyncio.run(run())
'''

@pytest.mark.asyncio
@pytest.mark.parametrize('phase,exit_code',[('after_a_prepare',37),('after_first_native_send',39)])
async def test_abrupt_process_exit_then_cold_native_objects_never_resend_same_group(tmp_path,phase,exit_code):
    proc=subprocess.run([sys.executable,'-c',CHILD,str(tmp_path),phase],capture_output=True,text=True,timeout=20)
    assert proc.returncode==exit_code,proc.stdout+proc.stderr
    f=build_native_group(tmp_path);g=coordinator_for(f)
    try:
        fact=next(x['document'] for x in g.ledger.group_facts('cap05-group') if x['fact_id']=='plan')
        plan=plan_from_document(fact['plan'])
        r=await g.execute(plan,registry=f['registry'],readiness=f['readiness'],evidence=fact['evidence'])
        assert r['duplicate'] is True
        assert not f['binance_transport'].calls and not f['bybit_transport'].calls
        assert f['binance_owner'].ledger.get(plan.actions[0].intent.intent_id)['state']=='UNKNOWN'
        assert f['binance_owner'].ledger.reserved()>0
        if phase=='after_first_native_send':
            assert f['bybit_owner'].ledger.reserved()>0
            assert json.loads((tmp_path/'scripted-exchange-created.json').read_text())['create_count']==1
        # Fresh native objects have no in-memory history: absence is never
        # converted into invented rejection/non-submission or new sends.
        recovered=await g.reconcile('cap05-group',registry=f['registry'],start_ns=1_000_000_000,end_ns=2_000_000_000)
        assert recovered['reconciled'] is False
        assert recovered['state'] in {'UNKNOWN','RECONCILING','BLOCKED'}
        assert not f['binance_transport'].calls and not f['bybit_transport'].calls
    finally:await close_fixture(f)
