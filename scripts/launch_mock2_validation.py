"""Supply server credentials privately to an isolated mock-only validation."""
import json
import subprocess
import sys
image='sha256:9199039698699ae77add8cdf1d21e68eb950521dd6ebd0835ef0a0fc3a7c642f'
command=['docker','run','--rm','--name','vibetrading-mock2-validation','--network','vibetrading_default',
    '--memory','256m','--cpus','.5','--env-file','/etc/vibetrading/app.env','--env-file','/root/vibetrading/.env',
    '-e','VIBETRADING_DATA_ROOT=/data','-e','VIBETRADING_STATE_DIR=/data/state','-e','NATS_URL=nats://nats:4222',
    '-v','/srv/vibetrading-data/runtime_data:/data']
for source,target in [('kis_client.py','vibetrading/providers/kis_client.py'),('kis_execution.py','vibetrading/kis_execution.py'),
    ('validate_kis_mock2_orders.py','validate_kis_mock2_orders.py')]:
    command+=['-v','/tmp/'+source+':/app/'+target+':ro']
command+=[image,'python','/app/validate_kis_mock2_orders.py']
result=subprocess.run(command,text=True,capture_output=True)
print(result.stdout)
if result.returncode:
    print(result.stderr[-2000:]); sys.exit(result.returncode)
