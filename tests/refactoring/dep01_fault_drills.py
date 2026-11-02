"""Independent failure injection before mutation, all artifacts LOCAL_FAKE."""
from pathlib import Path
import copy,json,os,hashlib,subprocess,sys
from execution.process_lease import ProcessLease
from scripts.refactoring import deployment_session as dep

def run_faults(manifest,manifest_path,root,evidence,wheel,facts,stage):
 outcomes={};anchor=manifest['manifest_sha256']
 def expect(name,fn):
  before=list(stage.iterdir())
  try:fn()
  except (ValueError,RuntimeError,OSError,KeyError) as exc:
   outcomes[name]={'status':'PASS_BLOCKED_BEFORE_MUTATION','exception_type':type(exc).__name__,'stage_unchanged':list(stage.iterdir())==before}
   assert outcomes[name]['stage_unchanged']
  else:raise AssertionError(name+' did not reject')
 def create(f=facts,m=manifest_path,a=anchor):return dep.create_session(m,a,root,evidence,wheel_path=wheel,expected_wheel_sha256=manifest['wheel']['sha256'],host_facts=f)
 def altered(group,change):
  f=copy.deepcopy(facts);r=f['artifacts'][group];body=json.loads(Path(r['path']).read_text());change(body['payload']);p=evidence/('fault-'+group+'-'+str(len(outcomes))+'.json');p.write_text(json.dumps(body));f['artifacts'][group]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()};return f
 def manifest_change(name,change):
  m=copy.deepcopy(manifest);change(m);p=evidence/(name+'.json');p.write_text(json.dumps(m));return p
 expect('PRE-IMAGE-MISMATCH',lambda:create(m=manifest_change('bad-image',lambda m:m['images']['app'].update(id='sha256:'+'0'*64))))
 expect('PRE-STALE-CMD',lambda:create(m=manifest_change('bad-cmd',lambda m:next(iter(m['roles'].values()))['command'].append('vibetrading.removed'))))
 expect('PRE-TIMEFOLIO',lambda:create(m=manifest_change('armed-timefolio',lambda m:next(r for r in m['roles'].values() if r['image_class']=='browser').update(submit=True))))
 expect('PRE-NATS-ONLY',lambda:create(m=manifest_change('nats-primary',lambda m:m['roles'].update({'primary-nats':{'command':['nats-init']}}))))
 def wrong_mount(payload):
  p=Path(json.loads(Path(facts['artifacts']['mounts']['path']).read_text())['payload']['contract_path']);c=json.loads(p.read_text());c['mounts'][0]['device']='999:999';bad=evidence/'wrong-mount.json';bad.write_text(json.dumps(c));payload['contract_path']=str(bad)
 expect('PRE-EMPTY-MOUNT',lambda:create(altered('mounts',wrong_mount)))
 expect('DISK-RESERVE',lambda:create(altered('disk',lambda p:p.update(reserve_bytes=10**30))))
 expect('ACCOUNT-CUT',lambda:create(altered('latest_truth',lambda p:p.update(account_version=p['account_version']+1))))
 expect('KILL-GLOBAL-MISSING',lambda:create(altered('kill_switch',lambda p:p['expected_epochs'].pop('global'))))
 expect('WRONG-BACKUP-HASH',lambda:create(altered('sqlite',lambda p:p.update(backup_sha256='0'*64))))
 expect('SPOOL-PREFIX',lambda:create(altered('spool',lambda p:p.update(expected_records_sha256='0'*64))))
 stale=copy.deepcopy(facts);stale['observed_ns']-=400_000_000_000
 expect('STALE-TRUTH',lambda:create(stale))
 owner=json.loads(Path(facts['artifacts']['owner']['path']).read_text())['payload'];lease=ProcessLease(Path(owner['lease_path']));lease.acquire()
 try:
  expect('OLD-OWNER-ACTIVE',create)
  code="from pathlib import Path;import os,sys;from execution.process_lease import ProcessLease\ntry: ProcessLease(Path(sys.argv[1])).acquire()\nexcept RuntimeError: print(os.getpid());sys.exit(0)\nelse: sys.exit(9)"
  child=subprocess.run([sys.executable,'-c',code,owner['lease_path']],capture_output=True,text=True,timeout=10)
  assert child.returncode==0 and int(child.stdout.strip())!=os.getpid(),'second process acquired account lease'
  outcomes['OLD-OWNER-CROSS-PROCESS']={'status':'PASS_BLOCKED_BEFORE_MUTATION','distinct_process':True,'stage_unchanged':True}
 finally:lease.close()
 # Root-shadow fault uses a separate ignored-file clone and public regression.
 return outcomes
