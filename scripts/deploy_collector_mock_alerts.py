"""Upgrade the existing Telegram service without exposing its credentials."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

release=Path(sys.argv[1] if len(sys.argv)>1 else '/opt/vibetrading/releases/collector-alerts-20261014')
release.mkdir(exist_ok=True); os.chmod(release,0o700)
core=Path('/opt/vibetrading/releases/scope-20261013')
build=json.loads((core/'build.json').read_text())
profile=Path('/root/vibetrading/deploy/crypto-profile.yml')
if not (release/'crypto-profile.before.yml').exists(): shutil.copy2(profile,release/'crypto-profile.before.yml')
content=profile.read_text()
section=re.search(r'^  telegram:\n.*?(?=^  \S|\Z)',content,re.M|re.S)
assert section
updated=re.sub(r'^    image:.*$', '    image: '+build['image'],section.group(0),flags=re.M)
if not re.search(r'^    image:',updated,re.M):
    updated=updated.replace('  telegram:\n','  telegram:\n    image: '+build['image']+'\n',1)
profile.write_text(content[:section.start()]+updated+content[section.end():])
row=json.loads(subprocess.check_output(['docker','inspect','vibetrading-telegram-1'],text=True))[0]
hashes={}
for name in ('health.py','telegram.py'):
    source=core/'vibetrading'/name
    target=Path('/root/vibetrading/vibetrading')/name
    if not (release/(name+'.before')).exists(): shutil.copy2(target,release/(name+'.before'))
    shutil.copy2(source,target)
    hashes[name]=hashlib.sha256(target.read_bytes()).hexdigest()
env=dict(e.split('=',1) for e in row['Config']['Env'] if '=' in e)
env.update({'CRYPTO_COLLECTOR_PROVIDERS':'binance-perp,arcus-perp,grvt-perp',
    'VIBETRADING_BUILD_REVISION':build['revision'],'VIBETRADING_IMAGE_ID':build['image']})
env.pop('VIBETRADING_BUILD_CONFIG_HASH',None)
service={'image':build['image'],'command':row['Config']['Cmd'],'environment':env,
    'restart':row['HostConfig']['RestartPolicy']['Name'],
    'volumes':[{'type':'bind','source':m['Source'],'target':m['Destination'],'read_only':not m['RW']} for m in row['Mounts']]}
for original,target in [('WorkingDir','working_dir'),('User','user'),('Entrypoint','entrypoint')]:
    if row['Config'].get(original): service[target]=row['Config'][original]
if row['HostConfig']['Memory']: service['mem_limit']=row['HostConfig']['Memory']
if row['HostConfig']['NanoCpus']: service['cpus']=row['HostConfig']['NanoCpus']/1e9
config={'services':{'telegram':service},'networks':{'default':{'external':True,'name':'vibetrading_default'}}}
compose=release/'telegram-private.yml'
compose.write_text(json.dumps(config,indent=2)); os.chmod(compose,0o600)
subprocess.check_call(['docker','compose','--project-name','vibetrading','-f',str(compose),'up','-d','--no-deps','--no-build','telegram'])
after=json.loads(subprocess.check_output(['docker','inspect','vibetrading-telegram-1'],text=True))[0]
receipt={'image':build['image'],'revision':build['revision'],'sources':hashes,'running':after['State']['Running']}
assert receipt['running']
(release/'receipt.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt))
