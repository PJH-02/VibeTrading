"""Deploy the tested transport fix to collectors only; preserve live trading."""
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

release=Path('/opt/vibetrading/releases/kiwoom-recovery-20261014')
release.mkdir(exist_ok=True)
core=Path('/opt/vibetrading/releases/scope-20261013')
build=json.loads((core/'build.json').read_text())
shutil.copy2(core/'build.json',release/'build.json')
shutil.copy2(core/'manifest.json',release/'manifest.json')
market=Path('/opt/vibetrading/releases/capacity-20261006/market.yml')
if not (release/'market.before.yml').exists(): shutil.copy2(market,release/'market.before.yml')
config=json.loads(market.read_text())
service=config['services']['collectors']
previous=service['image']
service['image']=build['image']
service['environment'].update({'VIBETRADING_BUILD_REVISION':build['revision'],'VIBETRADING_IMAGE_ID':build['image']})
service['environment'].pop('VIBETRADING_BUILD_CONFIG_HASH',None)
service['environment']['VIBETRADING_BUILD_CONFIG_HASH']=hashlib.sha256(json.dumps(service,sort_keys=True).encode()).hexdigest()
market.write_text(json.dumps(config,indent=2))
subprocess.check_call(['docker','compose','--project-name','vibetrading','-f',str(market),'up','-d','--no-deps','--no-build','collectors'])
receipt={'activated_ns':time.time_ns(),'previous_image':previous,'image':build['image'],'revision':build['revision']}
(release/'collector-activation.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt))
