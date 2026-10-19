"""Append reviewed progress and inactive v6 sources to the existing release."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'docs/refactoring/runs/20261018-stage-assignment'
candidate=ROOT/'tmp/refactoring/server-candidates-20261019/active-owner-v6'
remote='/opt/vibetrading/releases/refactoring-20261019-p02-schema/progress-v6'
files={}
for path in (candidate/'vibetrading').rglob('*.py'):
    if '__pycache__' not in path.parts:files['candidate/'+path.relative_to(candidate).as_posix()]=path.read_bytes()
for name in ('refactoring_plan.md','refactoring_context.md','refactoring_ai.md','docs/refactoring/execution_board.md'):
    files['review/'+name]=(ROOT/name).read_bytes()
for name in ('tasks.json','gates.json','handoff.md','p02-server-resume-receipt.json',
             'p02-server-health.jsonl','server-active-owner-after-p02.json',
             'server-active-owner-candidate-v6.json',
             'local-facts-wal-windows-v6/result.json','local-facts-wal-linux-v6/linux_result.json'):
    files['review/'+name]=(RUN/name).read_bytes()
for name in ('deploy_p02_schema.py','check_p02_server.py','rebase_after_p02.py','update_execution_state.py'):
    files['scripts/'+name]=(ROOT/'scripts/refactoring'/name).read_bytes()
files['review/REVIEW.txt']=b'Luna: no findings in P02 schema diff or P08 WAL cap diff. Rollback verification finding fixed before deployment. V6 remains inactive. Missing-minute work deferred by user.\n'
manifest={'active':'P02 schema cache only in two storage services','candidate':'Inactive NATS-free v6; no historical-minute hotfix',
    'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}}
files['manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
package=RUN/'progress-v6.tar.gz'
if package.exists():raise RuntimeError('progress package already exists')
with tarfile.open(package,'w:gz') as archive:
    for name,raw in sorted(files.items()):
        item=tarfile.TarInfo(name);item.size=len(raw);item.mode=0o600
        archive.addfile(item,io.BytesIO(raw))
subprocess.run(['ssh','-T','-o','BatchMode=yes','49.50.141.106','mkdir',remote],check=True,timeout=20)
subprocess.run(['scp','-q',str(package),'49.50.141.106:'+remote+'/package.tar.gz'],check=True,timeout=60)
digest=hashlib.sha256(package.read_bytes()).hexdigest()
code=r'''
import hashlib,json,pathlib,tarfile
root=pathlib.Path(REMOTE)
package=root/'package.tar.gz'
assert hashlib.sha256(package.read_bytes()).hexdigest()==DIGEST
with tarfile.open(package,'r:gz') as archive:
    for item in archive.getmembers():
        relative=pathlib.PurePosixPath(item.name)
        if not item.isfile() or relative.is_absolute() or '..' in relative.parts or item.size>5_000_000:raise RuntimeError('unsafe progress member')
        target=root.joinpath(*relative.parts);target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as handle:handle.write(archive.extractfile(item).read())
manifest=json.loads((root/'manifest.json').read_text())
for name,expected in manifest['files'].items():
    if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:raise RuntimeError('progress SHA mismatch')
print(json.dumps({'status':'PUBLISHED_VERIFIED_INACTIVE_V6_AND_CURRENT_PROGRESS','path':str(root),'files':len(manifest['files']),'package_sha256':DIGEST}))
'''.replace('REMOTE',repr(remote)).replace('DIGEST',repr(digest))
result=subprocess.run(['ssh','-T','-o','BatchMode=yes','49.50.141.106','python3','-'],
    input=code,text=True,capture_output=True,timeout=60)
if result.returncode:raise RuntimeError('server progress publication failed; output suppressed')
data=json.loads(result.stdout)
(RUN/'server-progress-v6-receipt.json').write_text(json.dumps(data,indent=2)+'\n')
print(json.dumps(data))
