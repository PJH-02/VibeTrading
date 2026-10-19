"""Upgrade only the ETF loop, importing audited counterfactual records first."""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

release=Path('/opt/vibetrading/releases/dual-mock-20261014')
release.mkdir(exist_ok=True); os.chmod(release,0o700)
core=Path('/opt/vibetrading/releases/scope-20261013')
build=json.loads((core/'build.json').read_text())
for name in ['build.json','manifest.json']: shutil.copy2(core/name,release/name)
root=Path('/srv/vibetrading-data/runtime_data/state/etf-mock')
with sqlite3.connect('file:'+str(root/'journal.sqlite3')+'?mode=ro',uri=True) as db:
    assert db.execute("SELECT COUNT(*) FROM orders WHERE status IN ('submitting','unknown','pending')").fetchone()[0]==0
    with sqlite3.connect(release/'journal-before.sqlite3') as backup: db.backup(backup)
validation=json.loads((root/'mock2-validation.json').read_text())
assert validation.get('complete') and all(r['status']=='filled' for r in validation['orders'])
compose=core/'etf-mock.yml'; shutil.copy2(compose,release/'etf-mock.before.yml')
config=json.loads(compose.read_text()); service=config['services']['etf-mock']
previous=service['image']; service['image']=build['image']
service['environment'].update({'VIBETRADING_BUILD_REVISION':build['revision'],'VIBETRADING_IMAGE_ID':build['image'],
    'NATS_URL':'nats://nats:4222'})
service['environment'].pop('VIBETRADING_BUILD_CONFIG_HASH',None)
service['environment']['VIBETRADING_BUILD_CONFIG_HASH']=hashlib.sha256(json.dumps(service,sort_keys=True).encode()).hexdigest()
compose.write_text(json.dumps(config,indent=2))
subprocess.check_call(['docker','compose','--project-name','vibetrading','-f',str(compose),'config','--quiet'])
subprocess.check_call(['docker','stop','-t','15','vibetrading-etf-mock-1'])
try:
    result=subprocess.check_output(['docker','run','--rm','--network=none','--memory','256m','--cpus','.5',
        '-e','VIBETRADING_DATA_ROOT=/data','-v','/srv/vibetrading-data/runtime_data:/data',
        '-v','/tmp/import_permission_reconstruction.py:/app/import_reconstruction.py:ro',build['image'],
        'python','/app/import_reconstruction.py'],text=True)
except BaseException:
    shutil.copy2(release/'etf-mock.before.yml',compose)
    subprocess.run(['docker','start','vibetrading-etf-mock-1'])
    raise
subprocess.check_call(['docker','compose','--project-name','vibetrading','-f',str(compose),'up','-d','--no-deps','--no-build','etf-mock'])
receipt={'activated_ns':time.time_ns(),'image':build['image'],'revision':build['revision'],
    'previous_image':previous,'historical_import':json.loads(result),'validation':validation}
(release/'activation.json').write_text(json.dumps(receipt,indent=2)); print(json.dumps(receipt))
