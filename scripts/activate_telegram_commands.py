"""Apply tested command/report changes without restarting live market feeds."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

release=Path('/opt/vibetrading/releases/telegram-recovery-20261014')
build=json.loads((release/'build.json').read_text())
path=release/'runtime.yml'; config=json.loads(path.read_text())
names=sys.argv[1:] or ['telegram','etf-mock']
for name in names:
    service=config['services'][name]; service['image']=build['image']
    env=service['environment']; env.update(VIBETRADING_BUILD_REVISION=build['revision'],VIBETRADING_IMAGE_ID=build['image'])
    env.pop('VIBETRADING_BUILD_CONFIG_HASH',None)
    env['VIBETRADING_BUILD_CONFIG_HASH']=hashlib.sha256(json.dumps(service,sort_keys=True).encode()).hexdigest()
path.write_text(json.dumps(config,indent=2))
Path('/opt/vibetrading/releases/capacity-20261006/market.yml').write_bytes(path.read_bytes())
subprocess.check_call(['docker','compose','--project-name','vibetrading','-f',str(path),'up','-d','--no-deps','--no-build',*names])
