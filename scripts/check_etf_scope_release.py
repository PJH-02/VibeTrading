"""Read-only broker/API preflight using server-owned credentials and new image."""
import json
import subprocess
from pathlib import Path

root = Path('/opt/vibetrading/releases/scope-20261013')
build = json.loads((root/'build.json').read_text())
cmd = ['docker','run','--rm','--name','vibetrading-etf-check-20261013',
    '--network','vibetrading_default','--memory','512m','--cpus','0.5',
    '--env-file','/etc/vibetrading/app.env','--env-file','/root/vibetrading/.env',
    '-e','NATS_URL=nats://nats:4222','-e','VIBETRADING_DATA_ROOT=/data','-e','VIBETRADING_LOG_DIR=/tmp/logs',
    '-e','ARROW_NUM_THREADS=1','-e','OMP_NUM_THREADS=1',
    '-v','/srv/vibetrading-data/runtime_data:/data',build['image'],
    'python','-m','vibetrading.etf_trading','check','--warmup']
result = subprocess.run(cmd,text=True,capture_output=True)
receipt = {'exit_code':result.returncode,'output':result.stdout,'diagnostics':result.stderr}
(root/'preflight.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt),flush=True)
