"""Add reviewed operational entrypoints/docs to an already tested isolated source."""
import hashlib,json,subprocess,tarfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/evidence/mm_state_revision_20261007'
record=json.loads((OUT/'server_final/server_verification.json').read_text(encoding='utf-8'))
remote=record['remote_source']
files=[Path('scripts')/x for x in ('collect_fv_mainnet.py','collect_fv_public.py','run_fv_pilot.py','run_fv_testnets.py','replay_mm_state_revision.py')]
files += [Path(x) for x in ('config/fv_mm_testnets.json','config/mm_state_latency_scenarios.json','requirements-mm-state-research.txt','requirements-analysis.txt','requirements-fv-mm.txt','requirements-fv-pilot.txt')]
files += sorted(Path('docs/mm_state').glob('*.md'))
manifest={p.as_posix():hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files}
archive=OUT/'server_final/operational_entrypoints.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
    for p in files:tar.add(ROOT/p,arcname=p.as_posix())
ssh=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=12','root@49.50.141.106']
subprocess.run(['scp','-o','BatchMode=yes',str(archive),'root@49.50.141.106:'+remote+'/operations.tar.gz'],check=True,capture_output=True,timeout=45)
code=r'''
import hashlib,json,pathlib,subprocess,tarfile
root=pathlib.Path(%r).resolve();manifest=%r
with tarfile.open(root/'operations.tar.gz') as tar:
    for member in tar.getmembers():
        p=(root/member.name).resolve()
        if member.name not in manifest or not p.is_relative_to(root) or member.issym() or member.islnk():raise ValueError('Invalid operational archive')
        data=tar.extractfile(member).read()
        if hashlib.sha256(data).hexdigest()!=manifest[member.name]:raise ValueError('Archive integrity mismatch')
        if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest()!=manifest[member.name]:raise ValueError('Existing file differs; preserve tested source')
        p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
image=%r
base=['docker','run','--rm','--network','none','--cpus','.25','--memory','256m','--pids-limit','128','-e','PYTHONDONTWRITEBYTECODE=1','-v',str(root)+':/review:ro','-w','/review','--entrypoint','python',image]
results={}
for script in ('collect_fv_mainnet.py','collect_fv_public.py','run_fv_pilot.py','run_fv_testnets.py','replay_mm_state_revision.py'):
    r=subprocess.run(base+['scripts/'+script,'--help'],capture_output=True,text=True,timeout=45)
    results[script]={'exit':r.returncode,'stderr':r.stderr[-500:]}
print(json.dumps(results))
'''%(remote,manifest,record['image'])
r=subprocess.run(ssh+['python3 -'],input=code,text=True,capture_output=True,timeout=240)
if r.returncode:raise RuntimeError('Review bundle completion failed: '+r.stderr[-400:])
results=json.loads(r.stdout)
receipt={'remote_source':remote,'tested_base_sha256':record['source_sha256'],'added_files':manifest,'cli_import_smoke':results,
         'network':'none','credentials_mounted':False,'production_services_modified':False,'orders_submitted':0}
(OUT/'server_final/operational_bundle.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
if any(x['exit'] for x in results.values()):raise RuntimeError('Operational CLI import smoke failed')
print(json.dumps({'staged_files':len(files),'cli_imports':len(results),'remote':remote,'orders_submitted':0}))
