"""Run close-gated CSV synchronization and preserve a deployment receipt."""
import json
import subprocess
from pathlib import Path

release = Path('/opt/vibetrading/releases/scope-20261013')
build = json.loads((release/'build.json').read_text())
result = subprocess.run(['docker','run','--rm','--network','vibetrading_default','--memory','512m','--cpus','0.5',
    '--env-file','/etc/vibetrading/app.env','--env-file','/root/vibetrading/.env',
    '-e','VIBETRADING_DATA_ROOT=/data','-e','ARROW_NUM_THREADS=1','-e','OMP_NUM_THREADS=1',
    '-v','/srv/vibetrading-data/runtime_data:/data',build['image'],
    'python','-m','vibetrading.etf_csv_sync','sync'],text=True,capture_output=True)
receipt = {'revision':build['revision'],'exit_code':result.returncode,'output':result.stdout,'diagnostics':result.stderr}
(release/'csv-sync-check.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt))
assert result.returncode==0
