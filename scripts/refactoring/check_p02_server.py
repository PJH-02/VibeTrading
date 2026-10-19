"""Read bounded storage readiness/progress; no application imports or secrets."""
import datetime
import json
from pathlib import Path
import subprocess

REMOTE=r'''
import hashlib,json,pathlib,subprocess
out={}
for name,component,root in [('vibetrading-crypto-storage-1','crypto-storage','crypto_state'),('vibetrading-storage-1','storage','state')]:
    d=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
    code="""import json,pathlib,sqlite3,time
root=pathlib.Path('/data/ROOT')
value=json.loads((root/'health/COMPONENT.json').read_text())
result={k:value.get(k) for k in ('state','ready','ts_ns','messages','acked','flushes','event_index')}
db=sqlite3.connect((root/'vibetrading.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.2)
db.execute('PRAGMA query_only=ON')
deadline=time.monotonic()+2;db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
try:result['run_high']=db.execute("SELECT seq FROM sqlite_sequence WHERE name='event_index_runs'").fetchone()[0]
except (sqlite3.Error,TypeError):result['run_high']=None
db.close();print(json.dumps(result))
""".replace('ROOT',root).replace('COMPONENT',component)
    worker=subprocess.run(['docker','exec',name,'python','-c',code],capture_output=True,timeout=8)
    source=subprocess.check_output(['docker','exec',name,'python','-c',
        "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('/app/vibetrading/parquet_store.py').read_bytes()).hexdigest())"]).decode().strip()
    out[name]={'id':d['Id'],'image_id':d['Image'],'health':d['State'].get('Health',{}).get('Status'),
        'status':d['State']['Status'],'restarts':d['RestartCount'],'oom_killed':d['State']['OOMKilled'],
        'source_sha256':source,'worker':json.loads(worker.stdout) if worker.returncode==0 else {'read_failed':True}}
release=pathlib.Path('/opt/vibetrading/releases/refactoring-20261019-p02-schema')
started=json.loads((release/'apply-started.json').read_text())
alive=set(subprocess.check_output(['docker','ps','-q','--no-trunc']).decode().split())
print(json.dumps({'containers':out,'other_containers_unchanged':set(started['untouched_container_ids'])<=alive}))
'''
result=subprocess.run(['ssh','-T','-o','BatchMode=yes','49.50.141.106','python3','-'],
    input=REMOTE,text=True,capture_output=True,timeout=25)
if result.returncode:raise RuntimeError('readiness collection failed; output suppressed')
data={'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),**json.loads(result.stdout)}
path=Path('docs/refactoring/runs/20261018-stage-assignment/p02-server-health.jsonl')
with path.open('a',encoding='utf-8') as handle:handle.write(json.dumps(data)+'\n')
print(json.dumps(data))
