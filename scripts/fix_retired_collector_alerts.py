"""Deploy active-scope alert checks without restarting any market collector."""
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

release=Path('/opt/vibetrading/releases/retired-alerts-20261014')
release.mkdir(exist_ok=True)
os.chmod(release,0o700)
names=['vibetrading-telegram-1','vibetrading-crypto-collector-bitget-1','vibetrading-crypto-collector-bybit-1']
before={name:json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0] for name in names}
for name in names[1:]:
    assert not before[name]['State']['Running']
    assert before[name]['HostConfig']['RestartPolicy']['Name']=='no'
hashes={}
for name in ['health.py','telegram.py']:
    destination=Path('/root/vibetrading/vibetrading')/name
    backup=release/(name+'.before')
    if not backup.exists(): shutil.copy2(destination,backup)
    source=Path('/tmp')/name
    compile(source.read_text(),name,'exec')
    shutil.copyfile(source,destination)
    hashes[name]=hashlib.sha256(destination.read_bytes()).hexdigest()

profile=Path('/root/vibetrading/deploy/crypto-profile.yml')
backup=release/'crypto-profile.yml.before'
if not backup.exists(): shutil.copy2(profile,backup)
text=profile.read_text()
match=re.search(r'^  telegram:\n(?P<body>.*?)(?=^  \S|\Z)',text,re.M|re.S)
assert match
section=match.group(0)
value='binance-perp,arcus-perp,grvt-perp'
if 'CRYPTO_COLLECTOR_PROVIDERS:' in section:
    section=re.sub(r'CRYPTO_COLLECTOR_PROVIDERS:.*','CRYPTO_COLLECTOR_PROVIDERS: '+value,section)
elif '    environment:\n' in section:
    section=section.replace('    environment:\n','    environment:\n      CRYPTO_COLLECTOR_PROVIDERS: '+value+'\n')
else:
    section=section.replace('  telegram:\n','  telegram:\n    environment:\n      CRYPTO_COLLECTOR_PROVIDERS: '+value+'\n')
profile.write_text(text[:match.start()]+section+text[match.end():])

# Preserve the running bot's image, credentials, mounts and resource limits.
# The private Compose file is mode 0600; credentials never enter tool output.
row=before[names[0]]
env=dict(e.split('=',1) for e in row['Config']['Env'] if '=' in e)
env['CRYPTO_COLLECTOR_PROVIDERS']=value
service={'image':row['Config']['Image'],'command':row['Config']['Cmd'],
    'environment':env,'restart':row['HostConfig']['RestartPolicy']['Name'],
    'volumes':[{'type':'bind','source':m['Source'],'target':m['Destination'],'read_only':not m['RW']} for m in row['Mounts']]}
for original,target in [('WorkingDir','working_dir'),('User','user'),('Entrypoint','entrypoint')]:
    if row['Config'].get(original): service[target]=row['Config'][original]
if row['HostConfig']['Memory']: service['mem_limit']=row['HostConfig']['Memory']
if row['HostConfig']['NanoCpus']: service['cpus']=row['HostConfig']['NanoCpus']/1e9
config={'services':{'telegram':service},'networks':{'default':{'external':True,'name':'vibetrading_default'}}}
compose=release/'telegram-private.yml'
compose.write_text(json.dumps(config,indent=2))
os.chmod(compose,0o600)
subprocess.check_call(['docker','compose','--project-name','vibetrading','-f',str(compose),'up','-d','--no-deps','--no-build','telegram'])
after={name:json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0] for name in names}
receipt={'source_sha256':hashes,'active_providers':value.split(','),'telegram_recreated':after[names[0]]['State']['Running'],
    'retired':{name:{'status':after[name]['State']['Status'],'restart':after[name]['HostConfig']['RestartPolicy']['Name'],
        'finished_at':after[name]['State']['FinishedAt']} for name in names[1:]}}
assert all(r['status']=='exited' and r['restart']=='no' for r in receipt['retired'].values())
(release/'receipt.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt))
