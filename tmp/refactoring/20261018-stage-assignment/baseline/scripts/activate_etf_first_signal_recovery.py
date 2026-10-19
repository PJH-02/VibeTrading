"""Activate the audited recovery images without touching unrelated services."""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

stage = Path('/srv/vibetrading-data/releases/etf-recovery-07a74502d725')
build = json.loads((stage/'build.json').read_text())
assert build['tests']['exit_code'] == 0 and build['preview']['exit_code'] == 0
preview = json.loads((stage/'preview-plan-r2.json').read_text())
assert preview['fills'][0]['strategy'] == 'KD'
assert preview['fills'][0]['event_ns'] == 1792024200000000000
container = json.loads(subprocess.check_output(['docker','inspect','vibetrading-etf-mock-1']))[0]
assert not container['State']['Running']
env = stage/'recovery.env'
env.write_text('\n'.join(container['Config']['Env'])+'\n'); os.chmod(env,0o600)
config = Path('/root/vibetrading/config/universe.yaml')
old = yaml.safe_load(config.read_text()); new = yaml.safe_load((stage/'source/config/universe.yaml').read_text()) if (stage/'source/config/universe.yaml').exists() else yaml.safe_load((stage/'universe.yaml').read_text())
old.setdefault('dynamic',{})['kis_cash_index'] = new['dynamic']['kis_cash_index']
shutil.copy2(config,stage/'universe.before.yaml')
config.write_text(yaml.safe_dump(old,allow_unicode=True,sort_keys=False))
source_before = stage/'source-before'; source_before.mkdir(exist_ok=True)
for relative in json.loads((stage/'source.json').read_text())['changed_files']:
    target = Path('/root/vibetrading/vibetrading')/relative
    if target.exists():
        backup = source_before/relative; backup.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(target,backup)
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(stage/'source/vibetrading'/relative,target)
canonical = Path('/opt/vibetrading/releases/telegram-recovery-20261014/runtime.yml')
shutil.copy2(canonical,stage/'runtime.before.json'); os.chmod(stage/'runtime.before.json',0o600)
runtime = json.loads(canonical.read_text())
for role,item in build['images'].items():
    service = runtime['services'][role]
    service['image'] = item['tag']
    service['environment'].update(VIBETRADING_BUILD_REVISION=build['revision'],VIBETRADING_IMAGE_ID=item['image'])
    service['environment'].pop('VIBETRADING_BUILD_CONFIG_HASH',None)
    service['environment']['VIBETRADING_BUILD_CONFIG_HASH'] = hashlib.sha256(json.dumps(service,sort_keys=True).encode()).hexdigest()
canonical.write_text(json.dumps(runtime,indent=2)); os.chmod(canonical,0o600)
shutil.copy2(canonical,'/opt/vibetrading/releases/capacity-20261006/market.yml')
base = ['docker','compose','--project-name','vibetrading','-f',str(canonical)]
subprocess.check_call(base+['config','--quiet'])
for role in ['collectors','etf-csv-sync','telegram']:
    subprocess.check_call(base+['up','-d','--no-deps','--no-build','--timeout','10',role])
print(json.dumps({'activated': ['collectors','etf-csv-sync','telegram'],'trading_still_paused':True,'revision':build['revision']}))
