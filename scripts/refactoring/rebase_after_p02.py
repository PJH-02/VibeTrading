"""Advance the server-owned baseline after the verified P02 deployment."""
import hashlib
import json
from pathlib import Path
import shutil

from build_server_candidate import build

ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'docs/refactoring/runs/20261018-stage-assignment'
BASE=ROOT/'tmp/refactoring/server-source-20261019'
before=BASE/'active-owner-base';after=BASE/'active-owner-after-p02'
health=json.loads((RUN/'p02-server-health.jsonl').read_text().splitlines()[-1])
applied=json.loads((RUN/'p02-server-resume-receipt.json').read_text())
post=ROOT/'tmp/refactoring/20261018-stage-assignment/p02-schema-v1/vibetrading/parquet_store.py'
digest=hashlib.sha256(post.read_bytes()).hexdigest()
if not health['other_containers_unchanged']:raise RuntimeError('unrelated server state changed')
for item in health['containers'].values():
    if item['health']!='healthy' or item['image_id']!=applied['image_id'] or item['source_sha256']!=digest:
        raise RuntimeError('deployed P02 source is unverified')
if after.exists():raise RuntimeError('do not overwrite a source baseline')
shutil.copytree(before,after)
shutil.copy2(post,after/'vibetrading/parquet_store.py')
report={'basis':'Original active-owner export + one verified deployed P02 file',
    'source':str(after),'changed_owner':'vibetrading/parquet_store.py -> storage/crypto-storage',
    'source_sha256':digest,'health_evidence':str(RUN/'p02-server-health.jsonl'),
    'other_modules':'Unchanged running owner container identities verified at deployment and final health collection'}
(RUN/'server-active-owner-after-p02.json').write_text(json.dumps(report,indent=2)+'\n')
candidate=ROOT/'tmp/refactoring/server-candidates-20261019/active-owner-v6'
local=ROOT/'tmp/refactoring/20261018-stage-assignment/local-runtime-v6'
result=build(after,local,candidate)
if result['blocked_files']:raise RuntimeError('new server candidate merge blocked')
differences=[];count=0
for path in (local/'vibetrading').rglob('*.py'):
    relative=path.relative_to(local);count+=1
    if path.read_bytes()!=(candidate/relative).read_bytes():differences.append(relative.as_posix())
result['local_v6_product_equivalence']={'files':count,'differences':differences}
(RUN/'server-active-owner-candidate-v6.json').write_text(json.dumps(result,indent=2)+'\n')
if differences:raise RuntimeError('candidate differs from tested v6; inspect explicit merges')
print(json.dumps({'candidate':str(candidate),'changes':len(result['changes']),
    'conflicts':0,'v6_product_files_identical':count,'status':'INACTIVE_REVIEW_CANDIDATE'}))
