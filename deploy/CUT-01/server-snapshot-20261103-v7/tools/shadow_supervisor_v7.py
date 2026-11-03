"""Bounded credential-free public observation and engine-only recovery trial."""
from pathlib import Path
import os,json,subprocess,time,datetime,traceback
R=Path('/srv/vibetrading-crypto-state/cut01-20261102');E=R/'evidence';DATA=R/'data-v7';CONFIG=R/'config-v7';IMAGE='sha256:0876cf2f41bcd75e953a5bff41bc49d01ffb5897681bbc295c62ffd3b5ad37ab';REVISION='111fa0a9ec4e71b311ff1c3e38f790f8611e82ae';TRIAL='cut01-v7-'+REVISION[:12]
created=[];active={};start=time.monotonic();state='STARTING';reason=None;events=[];bad_since={};restarted=False;recovery_deadline=None;live_started=None;live_started_utc=None
old={x['Id']:x['Name']for x in json.loads(subprocess.check_output(['docker','inspect',*subprocess.check_output(['docker','ps','-q'],text=True).split()],text=True))};assert len(old)==14 and all(n.startswith('/vibetrading-')for n in old.values())
def docker(args,timeout=40):
 p=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=timeout)
 if p.returncode:raise RuntimeError('DOCKER_OPERATION_FAILED:'+args[0])
 return p.stdout

def sample():
 out={'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'state':state,'elapsed_seconds':time.monotonic()-start,'observation_started_utc':live_started_utc,'observation_seconds':None if live_started is None else time.monotonic()-live_started,'reason':reason,'source_revision':REVISION,'image_id':IMAGE,'economic_profile':'PUBLIC_OBSERVATION_ONLY_NO_CREDENTIALS','existing_roles':14,'duration_limit_seconds':3600,'containers':[],'engine_restart_events':events}
 if active:
  for x in json.loads(docker(['inspect',*active.values()])):
   assert x['Image']==IMAGE,'ACTIVE_ROLE_IMAGE_IDENTITY_CHANGED'
   resource={};pid=x['State']['Pid']
   if pid:
    try:
     cg=Path('/sys/fs/cgroup')/Path('/proc',str(pid),'cgroup').read_text().split('0::',1)[1].strip().lstrip('/')
     resource={'memory_bytes':int((cg/'memory.current').read_text()),'memory_events':dict(l.split()for l in(cg/'memory.events').read_text().splitlines()),'cpu':dict(l.split()for l in(cg/'cpu.stat').read_text().splitlines())}
    except(OSError,ValueError,IndexError):resource={'unavailable':True}
   out['containers'].append({'name':x['Name'],'image_id':x['Image'],'state':x['State']['Status'],'exit':x['State']['ExitCode'],'oom':x['State']['OOMKilled'],'resource':resource})
 p=DATA/'state/local-market.json'
 if p.exists():
  assert p.stat().st_size<=4194304
  j=json.loads(p.read_text());out['snapshot']={'generated_ns':j['generated_ns'],'age_seconds':(time.time_ns()-j['generated_ns'])/1e9,'engine':j['engine'],'markets':[{'key':x['key'],'provider':x['provider'],'book_valid':x['book_valid'],'gap_reason':x['gap_reason'],'book_exposed':x['book']is not None,'has_price':x['price']is not None,'event_age_seconds':time.time()-x['last_event_ms']/1000}for x in j['markets']]}
 paths=list((DATA/'telemetry').glob('*.json'));assert len(paths)<=32
 for p in paths:
  assert p.stat().st_size<=2097152
  try:out.setdefault('telemetry',{})[p.name]=json.loads(p.read_text())
  except(OSError,ValueError):pass
 v=os.statvfs(R);out['available_bytes']=v.f_bavail*v.f_frsize;out['mem_available_bytes']=int(dict(l.split(':',1)for l in Path('/proc/meminfo').read_text().splitlines())['MemAvailable'].split()[0])*1024
 raw=json.dumps(out,sort_keys=True)+'\n';p=E/'shadow-v7-latest.tmp';p.write_text(raw);os.replace(p,E/'shadow-v7-latest.json')
 with(E/'shadow-v7-samples.jsonl').open('a')as f:f.write(raw)
 return out

def healthy(j):
 s=j.get('snapshot',{});rows=s.get('markets',[])
 return s.get('age_seconds',999)<=15 and len(rows)==9 and s.get('engine',{}).get('healthy')is True and all(x['book_valid']and x['event_age_seconds']<=15 for x in rows)

def launch(role,producer=None,restart=False):
 key=producer or role;name='cut01-shadow-v7-'+key+('-restart'if restart else'')
 collision=subprocess.run(['docker','inspect',name],capture_output=True,text=True,timeout=15)
 if collision.returncode==0:raise RuntimeError('OWN_NAME_ALREADY_EXISTS:'+name)
 args=['run','-d','--pull=never','--name',name,'--label','org.vibetrading.cut-trial='+TRIAL,'--network='+('bridge'if producer else'none'),'--read-only','--user=10001:10001','--cap-drop=ALL','--security-opt=no-new-privileges','--memory='+str((192 if producer else 384 if role=='engine'else 512)*1024**2),'--cpus='+('0.2'if producer else'0.6'if role=='engine'else'0.35'),'--pids-limit=64','--log-driver=json-file','--log-opt=max-size=4m','--log-opt=max-file=2','--tmpfs','/tmp:rw,nosuid,nodev,size=67108864,mode=1777','--workdir=/tmp']
 env={'HOME':'/tmp','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','VIBETRADING_RUNTIME':'local','VIBETRADING_DATA_ROOT':'/data','VIBETRADING_STATE_DIR':'/data/state','VIBETRADING_LOG_DIR':'/tmp/logs','PARQUET_DATA_ROOT':'/data/market_data','VIBETRADING_ENFORCE_MOUNTS':'true','VIBETRADING_STORAGE_MOUNTS_FILE':'/run/config/mounts.json','LIVE_ORDER_ENABLED':'false','TIMEFOLIO_LIVE_SUBMIT_ENABLED':'false','DASHBOARD_TELEMETRY_DIR':'/data/telemetry','DASHBOARD_TELEMETRY_INTERVAL_SECONDS':'2'}
 for k,v in env.items():args+=['--env',k+'='+v]
 args+=['--mount','type=bind,src='+str(DATA)+',dst=/data','--mount','type=bind,src='+str(CONFIG)+',dst=/run/config,readonly']
 if producer:args+=['--mount','type=bind,src='+str(R/'tools')+',dst=/run/cut-tools,readonly','--entrypoint=python',IMAGE,'/run/cut-tools/collector_progress_v7.py','--config','/run/config/local.json','--producer',producer]
 elif role=='engine':args+=['--entrypoint=python',IMAGE,'-m','runtime.data_only_runtime','--config','/run/config/local.json']
 else:args+=['--entrypoint=python',IMAGE,'-m','runtime.local_runtime','storage','--config','/run/config/local.json']
 created.append(name)
 docker(args);active[key]=name

def stop(name):
 assert name.startswith('cut01-shadow-v7-')
 inspection=subprocess.run(['docker','inspect',name],capture_output=True,text=True,timeout=15)
 if inspection.returncode:return {'name':name,'not_created':True}
 before=json.loads(inspection.stdout)[0]
 assert before['Image']==IMAGE and before['Config'].get('Labels',{}).get('org.vibetrading.cut-trial')==TRIAL and any(v.get('Source')==str(DATA)and v.get('Destination')=='/data' for v in before['Mounts']),'REFUSE_STOP_UNOWNED_CONTAINER'
 p=subprocess.run(['docker','stop','--signal=SIGINT','--timeout=45',name],capture_output=True,text=True,timeout=60)
 if p.returncode:raise RuntimeError('OWN_ROLE_STOP_FAILED:'+name)
 x=json.loads(docker(['inspect',name]))[0];logs=subprocess.run(['docker','logs','--tail=400',name],capture_output=True,timeout=15);(E/(name+'.log')).write_bytes(logs.stdout+logs.stderr)
 if x['State']['OOMKilled']or x['State']['ExitCode']not in(0,130):raise RuntimeError('OWN_ROLE_DID_NOT_EXIT_CLEANLY:'+name)
 return {'name':name,'exit':x['State']['ExitCode'],'oom':x['State']['OOMKilled']}

def downtime_durable(before):
 stopped_epoch=time.time_ns();deadline=time.monotonic()+30
 while True:
  rows={}
  for path in(DATA/'telemetry').glob('progress-*.json'):
   d=json.loads(path.read_text())
   if d.get('observed_ns',0)>=stopped_epoch:rows[d['producer']]=d['stats']['last_durable_sequence']
  if set(rows)==set(before)and all(rows[k]>before[k]for k in before):return {'result':'PASS_DURABLE_COLLECTION_WITH_ENGINE_STOPPED','stop_completed_epoch_ns':stopped_epoch,'observed_epoch_ns':time.time_ns(),'durable_before':before,'durable_while_engine_stopped':rows}
  if time.monotonic()>deadline:raise RuntimeError('NO_DURABLE_ADVANCEMENT_WITH_ENGINE_STOPPED')
  time.sleep(1)

def durable_counts(j):return {v['producer']:v['stats']['last_durable_sequence']for v in j.get('telemetry',{}).values()if isinstance(v,dict)and 'producer'in v and 'stats'in v}
try:
 assert json.loads((E/'pipeline-v7-check.json').read_text())['result']=='PASS'
 assert int(dict(l.split(':',1)for l in Path('/proc/meminfo').read_text().splitlines())['MemAvailable'].split()[0])*1024>900*1024**2
 launch('engine');launch('storage');deadline=time.monotonic()+90
 while not(DATA/'run/market.control').exists():
  if time.monotonic()>deadline:raise RuntimeError('ENGINE_SOCKET_START_TIMEOUT')
  time.sleep(1)
 for producer in('binance-perp','arcus-perp','grvt-perp'):launch('collector',producer)
 live_started=time.monotonic();live_started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat();state='OBSERVING'
 while time.monotonic()-live_started<3600:
  j=sample();now=time.monotonic()
  if any(x['state']!='running'or x['oom']for x in j['containers']):raise RuntimeError('SHADOW_ROLE_EXITED_OR_OOM')
  for v in j.get('telemetry',{}).values():
   if isinstance(v,dict)and'stats'in v:
    s=v['stats']
    if s['invalid']or s['pending']>=3584 or s['queued_bytes']>=28*1024**2 or s['oldest_pending_age_ns']>15_000_000_000:raise RuntimeError('INGRESS_HEADROOM_GUARD')
  snap=j.get('snapshot',{})
  if any(not x['book_valid']and x['book_exposed']for x in snap.get('markets',[])):raise RuntimeError('INVALID_BOOK_EXPOSED')
  if recovery_deadline is not None:
   if healthy(j)and snap['engine']['incarnation']!=events[-1]['engine_incarnation_before']and snap['generated_ns']>=events[-1]['started_epoch_ns']:
    after=durable_counts(j);before=events[-1]['durable_before'];assert set(after)==set(before)and all(after[k]>before[k]for k in before),'COLLECTION_DID_NOT_ADVANCE_DURING_ENGINE_RECOVERY'
    events[-1].update(result='PASS_FRESH_ENGINE_WITH_CONTINUING_DURABLE_PRODUCERS',durable_after=after,recovered_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),recovery_seconds=now-events[-1]['started_monotonic']);recovery_deadline=None;state='OBSERVING_AFTER_ENGINE_RECOVERY';bad_since.clear()
   elif now>recovery_deadline:raise RuntimeError('ENGINE_ONLY_RECOVERY_DEADLINE')
  elif now-live_started>90:
   conditions={'snapshot':snap.get('age_seconds',999)<=15,'inventory':len(snap.get('markets',[]))==9,'engine':snap.get('engine',{}).get('healthy')is True}
   conditions.update({x['key']:x['book_valid']and x['event_age_seconds']<=15 for x in snap.get('markets',[])})
   for key,ok in conditions.items():
    if ok:bad_since.pop(key,None)
    elif now-bad_since.setdefault(key,now)>30:raise RuntimeError('SOURCE_FRESHNESS_GUARD:'+key)
  if not restarted and now-live_started>=600 and healthy(j):
   before=durable_counts(j);assert len(before)==3
   event={'result':'STARTED','started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'started_monotonic':time.monotonic(),'started_epoch_ns':time.time_ns(),'engine_incarnation_before':snap['engine']['incarnation'],'durable_before':before,'stopped':stop(active['engine'])};events.append(event)
   event['downtime_durability']=downtime_durable(before)
   launch('engine',restart=True);restarted=True;recovery_deadline=time.monotonic()+420;state='RECOVERY_TEST'
  if j['available_bytes']<20*1024**3:raise RuntimeError('NEW_VOLUME_DISK_GUARD')
  if j['mem_available_bytes']<192*1024**2:raise RuntimeError('HOST_MEMORY_GUARD')
  running=set(docker(['ps','-q','--no-trunc']).split())
  if not set(old)<=running:raise RuntimeError('ORIGINAL_ROLE_CHANGED')
  time.sleep(10)
 assert restarted and recovery_deadline is None and events[-1]['result'].startswith('PASS_'),'ENGINE_RECOVERY_NOT_PROVEN'
 state='BOUNDED_WINDOW_COMPLETED';reason='3600seconds actual public-only observation plus engine-only recovery; not full CUT operational acceptance'
except BaseException as error:
 state='FAIL';reason=str(error)if isinstance(error,(RuntimeError,AssertionError))else type(error).__name__; (E/'shadow-v7-exception.txt').write_text(traceback.format_exc())
finally:
 order=[n for n in created if'perp'in n]+[n for n in created if'engine'in n]+[n for n in created if n.endswith('storage')]
 for name in order:
  try:stop(name)
  except BaseException as error:state='FAIL';reason=(reason or'')+';STOP_ERROR:'+name
 sample()
raise SystemExit(1 if state=='FAIL'else 0)
