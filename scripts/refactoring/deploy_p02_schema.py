"""Small server-first release: one proven source file, two storage services.

Prepare immutable candidate/docs, then stage/build, apply, or roll back only
the image selection. Never restore a database or touch order processes.
"""
from __future__ import annotations
import argparse
import datetime
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'docs/refactoring/runs/20261018-stage-assignment'
SOURCE=ROOT/'tmp/refactoring/20261018-stage-assignment/local-runtime-v5'
P02=ROOT/'tmp/refactoring/20261018-stage-assignment/p02-schema-v1/vibetrading/parquet_store.py'
HOST='49.50.141.106'
RELEASE='/opt/vibetrading/releases/refactoring-20261019-p02-schema'

REMOTE=r'''
import ast,datetime,hashlib,itertools,json,os,pathlib,re,shutil,subprocess,sys,tarfile,time
request=json.loads(sys.stdin.read())
release=pathlib.Path(request['release']); mode=request['mode']
assert str(release)=='/opt/vibetrading/releases/refactoring-20261019-p02-schema'
def sha(raw):return hashlib.sha256(raw).hexdigest()
def identity(value):return sha(json.dumps(value,sort_keys=True,separators=(',',':')).encode())
def run(args,timeout=45):
    result=subprocess.run(args,capture_output=True,timeout=timeout)
    if result.returncode:raise RuntimeError('command failed: '+args[0]+' '+args[1]+'; output suppressed')
    return result.stdout
def atomic(path,raw,mode=0o600):
    temp=path.with_name(path.name+'.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    with os.fdopen(fd,'wb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())
    os.replace(temp,path)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
def record(name,data):
    atomic(release/name,(json.dumps(data,sort_keys=True,indent=2)+'\n').encode())
def inspect(name):return json.loads(run(['docker','inspect',name]))[0]
def config():return json.loads(run(compose+['config','--format','json']))
def process(d):return {k:d['Config'].get(k) for k in ('Cmd','Entrypoint','Env','WorkingDir','User','Healthcheck','StopSignal')}
def same_configuration(name,d,expected):
    if d['HostConfig']!=expected['host_config']:return False
    if len(d['Mounts'])>5 or not any(identity(list(items))==expected['mounts'] for items in itertools.permutations(d['Mounts'])):return False
    if identity(process(d))==expected['process']:return True
    # Compose may reorder Env on recreation. Compare values to the original
    # pin and every other process field to the unchanged resolved service/base.
    pin=pins['containers'][name]
    if identity(sorted(d['Config'].get('Env') or []))!=pin['env_identity_sha256']:return False
    original=json.loads(run(['docker','compose','--project-directory',str(compose_root),'-p','vibetrading',
        '-f',str(release/'runtime.before.yml'),'-f',str(dashboard),'config','--format','json']))
    if identity(original)!=pins['resolved_compose_sha256']:return False
    service=original['services'][pin['service']]
    base=json.loads(run(['docker','image','inspect',pin['image_id']]))[0]['Config']
    for key,field in [('Cmd','command'),('Entrypoint','entrypoint'),('WorkingDir','working_dir'),('User','user'),('StopSignal','stop_signal')]:
        if d['Config'].get(key)!=service.get(field,base.get(key)):return False
    health=dict(base.get('Healthcheck') or {})
    for field,value in service.get('healthcheck',{}).items():
        key={'test':'Test','interval':'Interval','timeout':'Timeout','retries':'Retries','start_period':'StartPeriod','start_interval':'StartInterval'}.get(field)
        if key is None:return False
        if field not in {'test','retries'} and isinstance(value,str):
            parts=re.findall(r'(\d+(?:\.\d+)?)(ns|us|ms|s|m|h)',value)
            if ''.join(a+b for a,b in parts)!=value:return False
            value=round(sum(float(a)*{'ns':1,'us':1000,'ms':1000000,'s':1000000000,'m':60000000000,'h':3600000000000}[b] for a,b in parts))
        health[key]=value
    return d['Config'].get('Healthcheck')==health
def verify_ready(name,image_id,expected):
    for attempt in range(180):
        d=inspect(name);health=d['State'].get('Health',{}).get('Status')
        if d['Image']==image_id and d['State']['Status']=='running' and health=='healthy':break
        if d['State']['Status'] in {'dead','exited'}:raise RuntimeError('storage failed health')
        time.sleep(1)
    else:raise RuntimeError('storage health timeout')
    if not same_configuration(name,d,expected):raise RuntimeError('storage configuration changed')
if mode=='stage':
    package=release/'package.tar.gz'
    if sha(package.read_bytes())!=request['package_sha256']:raise RuntimeError('package SHA mismatch')
    if (release/'manifest.json').exists():raise RuntimeError('release already staged')
    with tarfile.open(package,'r:gz') as archive:
        for item in archive.getmembers():
            path=pathlib.PurePosixPath(item.name)
            if not item.isfile() or path.is_absolute() or '..' in path.parts:raise RuntimeError('unsafe release member')
            if item.size>5_000_000:raise RuntimeError('oversize release member')
            target=release.joinpath(*path.parts)
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as handle:handle.write(archive.extractfile(item).read())
    manifest=json.loads((release/'manifest.json').read_text())
    for name,digest in manifest['files'].items():
        if sha((release/name).read_bytes())!=digest:raise RuntimeError('staged source SHA mismatch')
    for path in (release/'candidate').rglob('*.py'):ast.parse(path.read_bytes())
    parent=manifest['parent_image_id'];tag='vibetrading:refactor-p02-base-'+parent.split(':')[1][:12]
    run(['docker','image','inspect',parent])
    run(['docker','tag',parent,tag])
    atomic(release/'Dockerfile',('FROM '+tag+'\nCOPY overlay/parquet_store.py /app/vibetrading/parquet_store.py\n').encode())
    image='vibetrading:refactor-p02-schema-'+manifest['postimage_sha256'][:12]
    run(['docker','build','--network','none','--pull=false','-t',image,str(release)],timeout=120)
    image_id=json.loads(run(['docker','image','inspect',image]))[0]['Id']
    # No application imports or network: check exact image bytes and syntax.
    checked=json.loads(run(['docker','run','--rm','--network','none','--read-only',
        '--entrypoint','python',image,'-c',
        "import ast,hashlib,json,pathlib; p=pathlib.Path('/app/vibetrading/parquet_store.py'); b=p.read_bytes(); ast.parse(b); print(json.dumps({'sha256':hashlib.sha256(b).hexdigest()}))"]))
    if checked['sha256']!=manifest['postimage_sha256']:raise RuntimeError('built image source mismatch')
    record('staged.json',{'status':'STAGED_AND_IMAGE_BUILT_NOT_ACTIVE','image':image,'image_id':image_id,
        'source_sha256':checked['sha256'],'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    print(json.dumps({'status':'STAGED_AND_IMAGE_BUILT_NOT_ACTIVE','release':str(release),'image':image,'image_id':image_id}))
    sys.exit(0)
manifest=json.loads((release/'manifest.json').read_text()); staged=json.loads((release/'staged.json').read_text())
pins=manifest['preflight']; compose_root=pathlib.Path(pins['compose_root'])
runtime=compose_root/'runtime.yml'; dashboard=compose_root/'dashboard.yml'
compose=['docker','compose','--project-directory',str(compose_root),'-p','vibetrading',
         '-f',str(runtime),'-f',str(dashboard)]
names=tuple(pins['containers']);services=tuple(pins['containers'][name]['service'] for name in names)
assert set(services)=={'storage','crypto-storage'}
if mode=='resume':
    started=json.loads((release/'apply-started.json').read_text())
    state=started['before_containers']
    if sha(runtime.read_bytes())!=started['runtime_after_sha256']:raise RuntimeError('runtime changed after partial apply')
    original=json.loads(run(['docker','compose','--project-directory',str(compose_root),'-p','vibetrading',
        '-f',str(release/'runtime.before.yml'),'-f',str(dashboard),'config','--format','json']))
    if identity(original)!=pins['resolved_compose_sha256']:raise RuntimeError('original configuration changed')
    expected=json.loads(json.dumps(original))
    for service in services:expected['services'][service]['image']=staged['image']
    if config()!=expected:raise RuntimeError('partial apply configuration differs')
    active=set(run(['docker','ps','-q','--no-trunc']).decode().split())
    if not set(started['untouched_container_ids'])<=active:raise RuntimeError('unrelated container changed')
    for name in names:
        d=inspect(name)
        if d['Image'] not in {pins['containers'][name]['image_id'],staged['image_id']}:
            raise RuntimeError('unexpected partial-apply image')
        if not same_configuration(name,d,state[name]):raise RuntimeError('partial-apply process configuration changed')
    for service in services:
        name=next(name for name,pin in pins['containers'].items() if pin['service']==service)
        if inspect(name)['Image']!=staged['image_id']:
            run(compose+['up','-d','--no-deps','--no-build','--pull','never','--timeout','30',service],timeout=75)
        verify_ready(name,staged['image_id'],state[name])
    output={'status':'P02_SCHEMA_ACTIVE_TWO_STORAGE_SERVICES','release':str(release),'image_id':staged['image_id'],
        'containers':{name:{'id':inspect(name)['Id'],'healthy':True} for name in names},
        'other_containers_unchanged':True,'database_migration':False,'missing_bar_hotfix_included':False,
        'resumed_after':'Existing crypto index validation exceeded initial readiness timeout; no repeated restart',
        'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    record('applied.json',output);print(json.dumps(output))
elif mode=='apply':
    if (release/'apply-started.json').exists():raise RuntimeError('apply already attempted; inspect receipt or use rollback')
    for name,digest in pins['compose_files'].items():
        if sha(pathlib.Path(name).read_bytes())!=digest:raise RuntimeError('compose preimage changed')
    before=config()
    if identity(before)!=pins['resolved_compose_sha256']:raise RuntimeError('resolved compose changed')
    all_before=run(['docker','ps','-q','--no-trunc']).decode().split()
    state={}
    for name,pin in pins['containers'].items():
        d=inspect(name)
        if d['Id']!=pin['container_id'] or d['Image']!=pin['image_id'] or d['State']['Status']!='running':
            raise RuntimeError('container preimage changed')
        if identity({'Config':d['Config'],'HostConfig':d['HostConfig'],'Mounts':d['Mounts']})!=pin['runtime_config_sha256']:
            raise RuntimeError('container configuration changed')
        actual=run(['docker','exec',name,'python','-c',
            "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('/app/vibetrading/parquet_store.py').read_bytes()).hexdigest())"]).decode().strip()
        if actual!=manifest['preimage_sha256']:raise RuntimeError('running source preimage mismatch')
        state[name]={'id':d['Id'],'image_id':d['Image'],'process':identity(process(d)),
                     'mounts':identity(d['Mounts']),'host_config':d['HostConfig']}
    raw=runtime.read_bytes();text=raw.decode()
    if text.lstrip().startswith('{'):
        document=json.loads(text)
        for service in services:document['services'][service]['image']=staged['image']
        text=json.dumps(document,indent=2,ensure_ascii=False)+'\n'
    else:
        for service in services:
            pattern=r'(?ms)(^  '+re.escape(service)+r':\s*\n)(.*?)(?=^  [A-Za-z0-9_-]+:\s*\n|\Z)'
            match=re.search(pattern,text)
            if match is None:raise RuntimeError('service section not uniquely found')
            body,count=re.subn(r'(?m)^(    image:\s*)[^\r\n]+',lambda m:m[1]+staged['image'],match[2])
            if count!=1:raise RuntimeError('service image line not unique')
            text=text[:match.start(2)]+body+text[match.end(2):]
    atomic(release/'runtime.before.yml',raw)
    atomic(release/'runtime.after.yml',text.encode())
    # Validate proposed config before editing the actual runtime file.
    preview=json.loads(run(['docker','compose','--project-directory',str(compose_root),'-p','vibetrading',
        '-f',str(release/'runtime.after.yml'),'-f',str(dashboard),'config','--format','json']))
    expected=json.loads(json.dumps(before))
    for service in services:expected['services'][service]['image']=staged['image']
    if preview!=expected:raise RuntimeError('proposed change alters more than two images')
    # Source-only schema cache has no data/schema migration, DB restore or collector/order restart.
    record('apply-started.json',{'status':'APPLY_STARTED','before_containers':state,
        'untouched_container_ids':[item for item in all_before if item not in {s['id'] for s in state.values()}],
        'runtime_after_sha256':sha(text.encode()),'resolved_before_sha256':identity(before),
        'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    atomic(runtime,text.encode(),runtime.stat().st_mode&0o777)
    if config()!=expected:raise RuntimeError('actual compose unexpected; rollback required')
    for service in services:
        run(compose+['up','-d','--no-deps','--no-build','--pull','never','--timeout','30',service],timeout=75)
        name=next(name for name,pin in pins['containers'].items() if pin['service']==service)
        verify_ready(name,staged['image_id'],state[name])
    all_after=set(run(['docker','ps','-q','--no-trunc']).decode().split())
    started=json.loads((release/'apply-started.json').read_text())
    if not set(started['untouched_container_ids'])<=all_after:raise RuntimeError('unrelated container changed')
    output={'status':'P02_SCHEMA_ACTIVE_TWO_STORAGE_SERVICES','release':str(release),'image_id':staged['image_id'],
        'containers':{name:{'id':inspect(name)['Id'],'healthy':True} for name in names},
        'other_containers_unchanged':True,'database_migration':False,'missing_bar_hotfix_included':False,
        'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    record('applied.json',output);print(json.dumps(output))
elif mode=='rollback':
    started=json.loads((release/'apply-started.json').read_text())
    current=sha(runtime.read_bytes());before=(release/'runtime.before.yml').read_bytes()
    if current not in {sha(before),started['runtime_after_sha256']}:raise RuntimeError('runtime changed externally; cannot overwrite')
    original_config=json.loads(run(['docker','compose','--project-directory',str(compose_root),'-p','vibetrading',
        '-f',str(release/'runtime.before.yml'),'-f',str(dashboard),'config','--format','json']))
    if identity(original_config)!=pins['resolved_compose_sha256']:raise RuntimeError('original resolved configuration changed')
    current_config=config()
    for service in services:
        if current_config['services'][service]['image'] not in {original_config['services'][service]['image'],staged['image']}:
            raise RuntimeError('unexpected compose image; cannot rollback')
        current_config['services'][service]['image']=original_config['services'][service]['image']
    if current_config!=original_config:raise RuntimeError('non-image compose change; cannot rollback')
    for name in names:
        d=inspect(name);expected=started['before_containers'][name]
        if d['Image'] not in {pins['containers'][name]['image_id'],staged['image_id']}:
            raise RuntimeError('container changed externally; cannot rollback')
        if not same_configuration(name,d,expected):raise RuntimeError('container configuration changed externally')
    atomic(runtime,before,runtime.stat().st_mode&0o777)
    for service in services:
        run(compose+['up','-d','--no-deps','--no-build','--pull','never','--timeout','30',service],timeout=75)
        name=next(name for name,pin in pins['containers'].items() if pin['service']==service)
        verify_ready(name,pins['containers'][name]['image_id'],started['before_containers'][name])
    output={'status':'IMAGE_SELECTION_ROLLED_BACK_NO_DATABASE_RESTORE','release':str(release)}
    record('rolled-back.json',output);print(json.dumps(output))
else:raise ValueError('unsupported mode')
'''

def sha(raw):return hashlib.sha256(raw).hexdigest()
def prepare():
    preflight=json.loads((RUN/'p02-server-preflight.json').read_text())
    parents={pin['image_id'] for pin in preflight['containers'].values()}
    if len(parents)!=1:raise RuntimeError('role-specific parent images require separate review')
    result=json.loads((RUN/'p02-schema-v1/result.json').read_text(encoding='utf-8'))
    if result['exit_code']!=0 or result['tested_source_hashes']['vibetrading/parquet_store.py']!=sha(P02.read_bytes()):
        raise RuntimeError('P02 source is not the tested source')
    files={'overlay/parquet_store.py':P02.read_bytes()}
    for path in SOURCE.rglob('*.py'):
        relative=path.relative_to(SOURCE)
        if relative.parts[0]=='vibetrading' and '__pycache__' not in relative.parts:
            files['candidate/'+relative.as_posix()]=path.read_bytes()
    for name in ('refactoring_plan.md','refactoring_context.md','refactoring_ai.md',
                 'docs/refactoring/execution_board.md',
                 'docs/refactoring/runs/20261018-stage-assignment/tasks.json',
                 'docs/refactoring/runs/20261018-stage-assignment/gates.json',
                 'docs/refactoring/runs/20261018-stage-assignment/server-candidate-source-equivalence.json'):
        files['review/'+name]=(ROOT/name).read_bytes()
    status=('NATS-free refactor: frozen v5 Windows1078/Linux1087 PASS; production cutover remains open.\n'
            'Active deployment scope: P02 schema cache only in storage/crypto-storage.\n'
            'P03-P08: offline implementation, retained as inactive candidate. Next: P09/P10 operational budgets and shadow.\n'
            'Historical minute repair paused at user request; excluded from release.\n')
    files['review/refactoring_status.txt']=status.encode()
    manifest={'schema_version':1,'parent_image_id':next(iter(parents)),'preflight':preflight,
        'preimage_sha256':sha((ROOT/'tmp/refactoring/server-source-20261019/active-owner-base/vibetrading/parquet_store.py').read_bytes()),
        'postimage_sha256':sha(P02.read_bytes()),'files':{name:sha(raw) for name,raw in files.items()},
        'scope':'P02 schema cache only; all other refactor code inactive; no historical minute hotfix',
        'review':'Luna independent minimal diff: no findings',
        'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    files['manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    output=RUN/'p02-server-package.tar.gz'
    if output.exists():raise RuntimeError('package already exists')
    with tarfile.open(output,'w:gz') as archive:
        for name,raw in sorted(files.items()):
            item=tarfile.TarInfo(name);item.size=len(raw);item.mode=0o600
            archive.addfile(item,io.BytesIO(raw))
    (RUN/'p02-server-package-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'package':str(output),'sha256':sha(output.read_bytes()),'files':len(files)}))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=('prepare','stage','apply','resume','rollback'))
    args=parser.parse_args()
    if args.mode=='prepare':return prepare()
    package=RUN/'p02-server-package.tar.gz'
    if args.mode=='stage':
        subprocess.run(['ssh','-T','-o','BatchMode=yes',HOST,'mkdir','-p',RELEASE],check=True,timeout=20)
        subprocess.run(['scp','-q',str(package),HOST+':'+RELEASE+'/package.tar.gz'],check=True,timeout=60)
    request={'release':RELEASE,'mode':args.mode,'package_sha256':sha(package.read_bytes())}
    # Script and request are serialized into one Python argument; credentials are never interpolated.
    wrapper='import sys;sys.stdin=__import__("io").StringIO('+repr(json.dumps(request))+');exec('+repr(REMOTE)+')'
    result=subprocess.run(['ssh','-T','-o','BatchMode=yes',HOST,'python3','-'],input=wrapper,
                          text=True,encoding='utf-8',capture_output=True,timeout=480)
    path=RUN/('p02-server-'+args.mode+'-receipt.json')
    if result.returncode:
        # Tracebacks contain no process/environment output; save diagnostics locally.
        (RUN/('p02-server-'+args.mode+'-error.txt')).write_text(result.stderr,encoding='utf-8')
        raise RuntimeError('Server '+args.mode+' failed; inspect local error receipt; no blind retry')
    data=json.loads(result.stdout);path.write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps(data))

if __name__=='__main__':main()
