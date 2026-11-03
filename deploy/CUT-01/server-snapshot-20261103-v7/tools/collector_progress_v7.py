"""Public-only candidate observer through accepted run_collector API, no economic scope."""
from pathlib import Path
import argparse,asyncio,json,os,time
from types import SimpleNamespace
from runtime.local_config import LocalRuntimePlan
from runtime.local_runtime import run_collector,validate_runtime
from runtime.settings import RuntimeSettings
from runtime.cli import _collector_for_args
from runtime.data_only_runtime import _public_settings_from_env
p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--producer',required=True);a=p.parse_args()
plan=LocalRuntimePlan.load(a.config);assert not plan.etf_enabled and not plan.strategy_selections and plan.observation is None
settings=_public_settings_from_env();assert not settings.live_order_enabled and not settings.timefolio_live_submit_enabled
validate_runtime(plan,settings);settings.ensure_dirs();matches=[x for x in plan.producers if x.producer==a.producer];assert len(matches)==1;item=matches[0];assert item.provider in ('binance-perp','arcus-perp','grvt-perp')and item.symbols=='@crypto'
class Observer:
 async def run(self,bus,duration_sec=None):
  collector=_collector_for_args(SimpleNamespace(provider=item.provider,symbols=item.symbols));path=settings.data_root/'telemetry'/('progress-'+item.producer+'.json');high_count=high_bytes=0
  def capture(state,error_type=None):
   nonlocal high_count,high_bytes
   stats=bus.stats;high_count=max(high_count,stats['pending']);high_bytes=max(high_bytes,stats['queued_bytes']);body={'observed_ns':time.time_ns(),'state':state,'error_type':error_type,'producer':item.producer,'stats':stats,'sampled_peak_pending':high_count,'sampled_peak_queued_bytes':high_bytes,'sample_interval_seconds':1,'source_revision':os.environ.get('VIBETRADING_BUILD_REVISION'),'economic_scope':'DATA_ONLY','capture_before_publisher_close':True};tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(body,sort_keys=True)+'\n');os.replace(tmp,path)
  async def periodic():
   while True:capture('RUNNING');await asyncio.sleep(1)
  # Cold-start barrier only, before any external feed is opened. Fresh root D=0
  # avoids the publisher advancing H before the first RESUME. Never pauses an
  # already active collector on engine loss; normal independent durability stays.
  assert bus.stats['last_durable_sequence']==0,'FRESH_COLLECTOR_REQUIRES_FRESH_INCARNATION_AND_ROOT'
  deadline=time.monotonic()+30
  while bus.stats['delivery_state']!='LIVE':
   if bus.stats['invalid'] or time.monotonic()>deadline:raise RuntimeError('INITIAL_EMPTY_HANDOFF_NOT_READY')
   await asyncio.sleep(.01)
  task=asyncio.create_task(periodic())
  try:await collector.run(bus,duration_sec=duration_sec)
  except BaseException as e:capture('STOPPING_OR_FAULTED',type(e).__name__);raise
  finally:task.cancel();await asyncio.gather(task,return_exceptions=True)
asyncio.run(run_collector(plan,item,settings,collector=Observer()))
