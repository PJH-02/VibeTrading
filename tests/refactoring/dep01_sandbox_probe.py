"""Lead independent LOCAL/PAPER/FAKE integration; real bind mounts, no network."""
from pathlib import Path
import asyncio,copy,hashlib,importlib,json,os,sqlite3,subprocess,sys,time,uuid
from decimal import Decimal as D
import resource
os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:2])
resource.setrlimit(resource.RLIMIT_AS,(4080218931,4080218931))
import pytest
from runtime.runtime_paths import linux_mounts
from scripts.refactoring import deployment_session as dep
from scripts.refactoring.deployment_checks import account_facts,digest,sha_file
from dep01_session_fixture import make_paper_proof,make_host_proofs
from test_vn17_role_assembly import real_assembly
from test_vn13_file_registration import strategy_files
from test_strategy_runtime import book,trade,T
from test_execution_owner import intent
from operations.telegram import TelegramController
from runtime.settings import RuntimeSettings,NatsSettings,ParquetSettings,ArchiveSettings,StorageGuardSettings
from execution.execution_ledger import AccountScope,ExecutionLedger
from runtime.runtime_control import RuntimeControl
from core.models import Side

import argparse
parser=argparse.ArgumentParser(description=__doc__)
for option in ('release-root','data-root','stage-root','manifest','wheel'):
 parser.add_argument('--'+option,required=True,type=Path)
parser.add_argument('--tamper-stage',action='store_true')
args=parser.parse_args()
ROOT=args.release_root;DATA=args.data_root;STAGE=args.stage_root;MANIFEST=args.manifest;WHEEL=args.wheel
for path in (ROOT,DATA,STAGE):
 assert path.is_absolute() and path.is_dir() and not path.is_symlink()
assert not list(STAGE.iterdir()),'probe requires a new empty private stage mount'
M=json.loads(MANIFEST.read_text());MS=M['manifest_sha256'];SS=M['source_sha256']
EVIDENCE=DATA/'evidence';EVIDENCE.mkdir(mode=0o700)
def put(path,obj):
 path.write_text(json.dumps(obj,sort_keys=True,indent=2)+'\n');return {'path':str(path),'sha256':sha_file(path)}
def save(name,obj):return put(EVIDENCE/(name+'.json'),obj)

def setup_mounts():
 entries=linux_mounts(Path('/proc/self/mountinfo').read_text())
 rows=[{'path':p,**{k:v for k,v in entries[p].items() if k in ('device','source','filesystem')}} for p in (str(DATA),str(STAGE))]
 contract={'schema_version':1,'mounts':rows,'path_bindings':{k:str(DATA) for k in ('data_root','state_dir','parquet_root')}}
 path=DATA/'mounts.json';put(path,contract);return path

async def paper_run():
 root=DATA/'paper';root.mkdir(mode=0o700);mp=pytest.MonkeyPatch();gen=strategy_files.__wrapped__(root,mp);write=next(gen)
 try:
  async with real_assembly(root,mp,write) as env:
   await env.publish(book(T,instrument=env.instrument).model_copy(update={'provider':'kiwoom'}))
   await env.publish(trade(T+11,side=Side.SELL,price=99,instrument=env.instrument).model_copy(update={'provider':'kiwoom','quantity':D(6)}))
   assert env.ledger.report()['orders'][0]['state']=='FILLED'
   assert env.host.report()['strategies'][env.sid]['callbacks']==1
   capture=env
 finally:
  try:next(gen)
  except StopIteration:pass
  mp.undo()
 return capture

class Response:
 status=200
 async def __aenter__(self):return self
 async def __aexit__(self,*args):pass
 def raise_for_status(self):pass
 async def json(self):return {'ok':True,'result':{'message_id':1}}
class ClosedFakeTransport:
 def __init__(self):self.calls=0;self.closed=False
 def get(self,*args,**kwargs):assert not self.closed;self.calls+=1;return Response()
 async def close(self):self.closed=True
async def health_run():
 root=DATA/'telegram';root.mkdir(mode=0o700)
 settings=RuntimeSettings(env={'TELEGRAM_BOT_TOKEN':'fake','TELEGRAM_USER_ID':'123'},project_root=ROOT,data_root=root,state_dir=root/'state',log_dir=root/'logs',external_config_dir=root/'unused',nats=NatsSettings(),parquet=ParquetSettings(data_root=root/'parquet'),archive=ArchiveSettings(),storage_guard=StorageGuardSettings(),providers={},max_strategy_jobs=1,live_order_enabled=False,timefolio_live_submit_enabled=False,telegram_user_id='123')
 bot=TelegramController(settings);transport=ClosedFakeTransport()
 await bot._send_direct(transport,'123','DEP-01 LOCAL_FAKE cold transport only')
 await transport.close();assert transport.calls==1 and transport.closed
 origins={}
 for package in ('core','feeds','market','storage','strategies','execution','portfolio','runtime','operations','timefolio'):
  module=importlib.import_module(package);origins[package]=module.__file__
  assert Path(module.__file__).is_relative_to(ROOT/'src'),(package,module.__file__)
 return {'transport_calls':transport.calls,'transport_closed':transport.closed,'origins':origins}

scope=AccountScope('binance','FAKE_DEP','spot','offline');authority=DATA/'authority'
def ledger_open():return ExecutionLedger(DATA/'orders.sqlite3',scope,max_intents=32,max_evidence_bytes=16384,max_fills=32,authority_root=authority)
def fill(ledger,identity):
 order=intent(identity,quantity='1',metadata={'account_id':scope.account,'environment':'offline'})
 ledger.authorize(order,evidence={'classification':'LOCAL_FAKE'},expiry_ns=time.time_ns()+10000000000,epochs={},reserve_unit=D(10),strategy_id='DEP')
 assert ledger.apply_fill_once(identity,'fake-fill-'+identity,quantity=D(1),price=D(10),fee=D(0))

async def main():
 contract=setup_mounts();paper=await paper_run();health=await health_run()
 ledger=ledger_open();control=RuntimeControl(ledger);control.halt('DEP',scope='global');fill(ledger,'first')
 version=ledger.account_version();wal=Path(str(ledger.path)+'-wal');assert wal.is_file() and wal.stat().st_size>0
 snap=dep.snapshot_sqlite(ledger.path,DATA/'wal-backup.sqlite3',expected_scope=scope.key,expected_schema=4,expected_account_version=version)
 snap['path']=str(DATA/'wal-backup.sqlite3');assert snap['source_unchanged'] and snap['facts']['facts']['execution_fills']['count']==1
 save('wal-snapshot',snap);ledger.close()
 now=time.time_ns();sid=str(uuid.uuid5(uuid.NAMESPACE_URL,MS+':'+SS+':'+str(now)))
 item=paper.plan.producers[0];limits={k:getattr(item.limits,k) for k in ('max_disk_bytes','max_records','max_frame_bytes','max_header_bytes')}
 paper_ref=make_paper_proof(DATA/'paper-proof.json',session_id=sid,source_sha256=SS,manifest_sha256=MS,engine=paper.engine,paper_runtime=paper.host,ledger=paper.ledger,strategy_id=paper.sid,spool_root=item.spool_root,producer_id=item.producer,incarnation=item.incarnation,limits=limits,observed_ns=now)
 paper_doc=json.loads((DATA/'paper-proof.runtime.json').read_text())
 lineage={'evidence_class':'LOCAL_FAKE','session_id':sid,'source_sha256':SS,'manifest_sha256':MS,'observed_ns':now}
 health_doc={**lineage,'roles_started':False,'telegram_transport':'FAKE_CLOSED','cold_roles':list(health['origins']),'spool_path':str(item.spool_root),'spool_sha256':paper_doc['spool_sha256'],'actual_cold_origins':health['origins'],'actual_telegram':{k:v for k,v in health.items() if k!='origins'}}
 health_content=put(DATA/'health.runtime.json',health_doc)
 health_ref=put(DATA/'health-proof.json',{**lineage,'group':'health','payload':{'artifact_path':health_content['path'],'artifact_sha256':health_content['sha256']}})
 facts=make_host_proofs(DATA,M,now_ns=now,session_id=sid,source_sha256=SS,manifest_sha256=MS,contract_path=contract,settings_paths={k:str(DATA) for k in ('data_root','state_dir','parquet_root')},reserve_bytes=1024*1024,reserve_inodes=100,execution_ledger=ledger,authority_root=authority,venue=scope.venue,physical_account=scope.account,environment=scope.environment,strategy_id='DEP',account_version=version,spool_root=item.spool_root,producer_id=item.producer,incarnation=item.incarnation,limits=limits,health_artifact=health_ref,paper_artifact=paper_ref,precreated_snapshot=snap)
 pristine=copy.deepcopy(facts);save('host-facts',facts)
 from dep01_fault_drills import run_faults
 faults=run_faults(M,MANIFEST,ROOT,EVIDENCE,WHEEL,facts,STAGE);save('faults-before-mutation',faults)
 session=dep.create_session(MANIFEST,MS,ROOT,EVIDENCE,wheel_path=WHEEL,expected_wheel_sha256=M['wheel']['sha256'],host_facts=facts)
 assert next(g for g in session['gates'] if g['gate_id']=='PRE-CURRENT-HOST')['status']=='EXTERNAL_ONLY'
 assert facts==pristine,'constructor mutated caller host references'
 save('plan',dep.run_mode(session,'plan'));save('preflight',dep.run_mode(session,'preflight'))
 rollback=dep.run_mode(session,'rollback');assert rollback['rollback']=='PLAN_ONLY' and rollback['database_restore']=='PROHIBITED';save('rollback-before-new-fill',rollback)
 (STAGE/'.DEP01-LOCAL-FAKE').write_text(dep.LOCAL_MARKER+'\n')
 installed=dep.run_mode(session,'install-blocked',sandbox_root=STAGE,local_fake_marker=dep.LOCAL_MARKER);save('installed',installed)
 verified=dep.run_mode(installed,'verify');assert verified['verification']=='PASS';save('verified',verified)
 activated=dep.run_mode(installed,'activate');assert activated['activation']=='BLOCKED' and activated['mutation_count']==0;save('activate-blocked',activated)
 try:dep.run_mode(session,'install-blocked',sandbox_root=STAGE,local_fake_marker=dep.LOCAL_MARKER)
 except ValueError:pass
 else:raise AssertionError('second install allowed')
 if args.tamper_stage:
  staged=Path(installed['install']['stage']);target=staged/next(iter(M['source_files']))
  target.write_bytes(target.read_bytes()+b'\n# LOCAL_FAKE tamper fault\n')
  bad=dep.run_mode(installed,'verify');assert bad['verification']=='FAIL' and bad['p0_latched']
  assert dep.run_mode(installed,'rollback')['rollback']=='P0_BLOCKED'
  try:dep.run_mode(installed,'install-blocked',sandbox_root=STAGE,local_fake_marker=dep.LOCAL_MARKER)
  except ValueError:pass
  else:raise AssertionError('P0 stage install allowed')
  result={'status':'PASS','classification':'LOCAL_PAPER_FAKE','source_head':M['source_head'],'manifest_sha256':MS,'stage_tamper_latched':True,'rollback_blocked':True,'activation_blocked':True,'faults_before_mutation':faults}
  save('lead-integration',result);print(json.dumps(result,sort_keys=True));return
 before=account_facts(DATA/'orders.sqlite3',scope.key);ledger=ledger_open();fill(ledger,'late');newversion=ledger.account_version();ledger.close();after=account_facts(DATA/'orders.sqlite3',scope.key)
 assert newversion>version and after['facts']['execution_fills']['count']==2
 try:dep.run_mode(installed,'rollback')
 except ValueError:pass
 else:raise AssertionError('rollback allowed stale account cut')
 assert account_facts(DATA/'orders.sqlite3',scope.key)==after
 blocked=dep.run_mode(installed,'rollback');assert blocked['rollback']=='P0_BLOCKED';save('rollback-P0-blocked',blocked)
 result={'faults_before_mutation':faults,'status':'PASS','classification':'LOCAL_PAPER_FAKE','session_id':sid,'source_head':M['source_head'],'manifest_sha256':MS,'all_operational_gates':verified['operational_evidence'],'real_bind_mounts':True,'cpu_affinity':sorted(os.sched_getaffinity(0)),'address_space_limit_bytes':resource.getrlimit(resource.RLIMIT_AS)[0],'one_install':True,'forbidden_activation':0,'roles_started':False,'live_wal_newest_fill_backup':True,'paper_callbacks':paper_doc['strategy_callbacks'],'paper_fills':paper_doc['paper_fills'],'durable_spool_sequence':paper_doc['durable_cursor'],'telegram_fake_calls':health['transport_calls'],'latest_fill_count_preserved':after['facts']['execution_fills']['count'],'rollback_database_restore':'PROHIBITED'}
 save('lead-integration',result);print(json.dumps(result,sort_keys=True))
if __name__=='__main__':
 asyncio.run(main())
