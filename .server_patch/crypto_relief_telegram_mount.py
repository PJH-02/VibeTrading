"""Make Telegram read the moved crypto health directory; preserve its config."""
import ast
import copy
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT=Path('/opt/vibetrading/releases/crypto-relief-final-20261002')
NAME='vibetrading-telegram-1'
BACKUP='vibetrading-telegram-before-state-mount-20261002'
SOURCE='/srv/vibetrading-crypto-state'
DEST='/data/crypto_state'
# Reuse only these pure definitions; do not execute the storage cutover body.
tree=ast.parse((ROOT/'crypto_relief_market_cache.py').read_text())
definitions=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='DockerConnection'
             or isinstance(n,ast.FunctionDef) and n.name=='api']
assert len(definitions)==2
exec(compile(ast.Module(body=definitions,type_ignores=[]),'<local-docker-client>','exec'),globals())
old=api('GET','/containers/'+NAME+'/json')
assert old['State']['Running']
assert old['Config']['Cmd'] in (['python','main.py','telegram'], ['python','main.py','run','--role','telegram'])
already=next((m for m in old['Mounts'] if m['Destination']==DEST),None)
if already:
    assert already['Source']==SOURCE and not already['RW']
    print(json.dumps({'already_applied':True,'state_mount':SOURCE}),flush=True)
    sys.exit(0)
assert not any('/'+BACKUP in c['Names'] for c in api('GET','/containers/json?all=true'))
private=ROOT/'telegram-private-before.json'
private.write_text(json.dumps(old))
private.chmod(0o600)
payload=copy.deepcopy(old['Config'])
payload['Image']=old['Image']
payload['HostConfig']=copy.deepcopy(old['HostConfig'])
payload['HostConfig'].pop('ConsoleSize',None)
payload['HostConfig']['Binds']=list(payload['HostConfig'].get('Binds') or [])+[SOURCE+':'+DEST+':ro']
payload['NetworkingConfig']={'EndpointsConfig':{name:{k:copy.deepcopy(n[k]) for k in ('Aliases','DriverOpts') if n.get(k) is not None}
    for name,n in old['NetworkSettings']['Networks'].items()}}
created=renamed=False
check='import json,time;from pathlib import Path;p=Path("/data/crypto_state/health/crypto-storage.json");h=json.loads(p.read_text());age=(time.time_ns()-h["ts_ns"])/1e9;assert age<120 and h.get("ready") is True;print(json.dumps({"ready":h["ready"],"age_seconds":round(age,2)}))'
try:
    api('POST','/containers/'+NAME+'/stop?t=30')
    api('POST','/containers/'+NAME+'/update',{'RestartPolicy':{'Name':'no','MaximumRetryCount':0}})
    api('POST','/containers/'+NAME+'/rename?name='+BACKUP)
    renamed=True
    api('POST','/containers/create?name='+NAME,payload)
    created=True
    api('POST','/containers/'+NAME+'/start')
    new=api('GET','/containers/'+NAME+'/json')
    original={(m['Destination'],m['Source'],m['RW']) for m in old['Mounts']}
    expected=original|{(DEST,SOURCE,False)}
    assert {(m['Destination'],m['Source'],m['RW']) for m in new['Mounts']}==expected
    assert new['Image']==old['Image'] and new['Config']['Env']==old['Config']['Env']
    assert new['Config']['Cmd']==old['Config']['Cmd']
    assert new['HostConfig']['RestartPolicy']==old['HostConfig']['RestartPolicy']
    fresh=json.loads(subprocess.check_output(['docker','exec',NAME,'python','-c',check],text=True))
except BaseException:
    if created:
        api('POST','/containers/'+NAME+'/stop?t=30')
        api('DELETE','/containers/'+NAME+'?force=false')
    if renamed:api('POST','/containers/'+BACKUP+'/rename?name='+NAME)
    api('POST','/containers/'+NAME+'/update',{'RestartPolicy':old['HostConfig']['RestartPolicy']})
    api('POST','/containers/'+NAME+'/start')
    raise
result={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'same_image':True,'same_env':True,
    'same_command':True,'previous_mounts_preserved':True,'new_state_mount_readonly':True,
    'crypto_storage_health':fresh,'rollback_restart_disabled':True}
(ROOT/'telegram-state-mount.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
