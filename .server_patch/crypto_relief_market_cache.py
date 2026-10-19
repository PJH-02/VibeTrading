"""Increase only the existing Market storage cache; retain its exact image/config.

The stopped rollback container has restart disabled to prevent two DB writers.
Private Docker settings stay root-only on the host and are never printed.
"""
import copy
import http.client
import json
from pathlib import Path
import socket
import sys
import time
import urllib.parse

ROOT = Path('/opt/vibetrading/releases/crypto-relief-final-20261002')
NAME = 'vibetrading-storage-1'
BACKUP = 'vibetrading-storage-before-cache-20261002'
HEALTH = Path('/srv/vibetrading-data/runtime_data/state/health/storage.json')
DB = HEALTH.parents[1] / 'vibetrading.sqlite3'

class DockerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(120)
        self.sock.connect('/var/run/docker.sock')

def api(method, path, value=None, accepted=(200,201,204,304)):
    c = DockerConnection('localhost')
    try:
        c.request(method, path, body=json.dumps(value) if value is not None else None,
                  headers={'Content-Type':'application/json'})
        r = c.getresponse()
        data = r.read()
        if r.status not in accepted:
            # Docker errors may include config values; do not print them.
            raise RuntimeError(f'Docker operation failed: {method} {path}: {r.status}')
        return json.loads(data) if data else None
    finally:
        c.close()

old = api('GET', '/containers/'+NAME+'/json')
assert old['State']['Running']
assert old['Image'] == 'sha256:c8689b8f46e9e79cf8c43c4460f5465dc724095b0c5223d2554eedb682f011f2'
assert old['Config']['Cmd'] == ['python','main.py','run','--role','storage']
assert old['HostConfig']['Memory'] == 1024**3
if 'SQLITE_CACHE_KIB=131072' in old['Config']['Env']:
    print(json.dumps({'already_applied':True,'cache_kib':131072}),flush=True)
    sys.exit(0)
# Detect the retained rollback container before stopping the active writer.
existing = api('GET','/containers/json?all=true')
assert not any('/'+BACKUP in c['Names'] for c in existing), 'Rollback container already exists'
assert all(m['Destination'] != '/data/crypto_state' for m in old['Mounts'])
assert DB.stat().st_size > 4*1024**3
assert int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                if line.startswith('MemAvailable:'))) > 512*1024
inode = DB.stat().st_ino
secret = ROOT/'market-storage-private-before.json'
secret.write_text(json.dumps(old))
secret.chmod(0o600)
payload = copy.deepcopy(old['Config'])
payload['Image'] = old['Image']
payload['Env'] = [v for v in payload['Env'] if not v.startswith('SQLITE_CACHE_KIB=')]+['SQLITE_CACHE_KIB=131072']
payload['HostConfig'] = copy.deepcopy(old['HostConfig'])
payload['HostConfig'].pop('ConsoleSize',None)
networks = {}
for name, n in old['NetworkSettings']['Networks'].items():
    networks[name] = {k:copy.deepcopy(n[k]) for k in ('Aliases','DriverOpts') if n.get(k) is not None}
payload['NetworkingConfig'] = {'EndpointsConfig':networks}
created = False
renamed = False
started_ns = time.time_ns()
try:
    api('POST', '/containers/'+NAME+'/stop?t=90')
    api('POST', '/containers/'+NAME+'/update', {'RestartPolicy':{'Name':'no','MaximumRetryCount':0}})
    api('POST', '/containers/'+NAME+'/rename?name='+BACKUP)
    renamed = True
    api('POST', '/containers/create?name='+NAME, payload)
    created = True
    api('POST', '/containers/'+NAME+'/start')
    for _ in range(180):
        if HEALTH.exists():
            h = json.loads(HEALTH.read_text())
            if h.get('ts_ns',0)>started_ns and h.get('sqlite_writer_pragmas',{}).get('cache_size') == -131072:
                break
        time.sleep(1)
    else:
        raise RuntimeError('New Market cache setting never became healthy')
    new = api('GET', '/containers/'+NAME+'/json')
    assert new['Image'] == old['Image']
    assert new['Config']['Cmd'] == old['Config']['Cmd']
    assert {(m['Destination'],m['Source'],m['RW']) for m in new['Mounts']} == {(m['Destination'],m['Source'],m['RW']) for m in old['Mounts']}
    assert new['HostConfig']['RestartPolicy'] == old['HostConfig']['RestartPolicy']
    assert DB.stat().st_ino == inode
except BaseException:
    if created:
        api('POST', '/containers/'+NAME+'/stop?t=90')
        api('DELETE', '/containers/'+NAME+'?force=false')
    if renamed:
        api('POST', '/containers/'+BACKUP+'/rename?name='+NAME)
    api('POST', '/containers/'+NAME+'/update', {'RestartPolicy':old['HostConfig']['RestartPolicy']})
    api('POST', '/containers/'+NAME+'/start')
    raise
result = {'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'only_setting_changed':'SQLITE_CACHE_KIB',
          'cache_kib':131072,'same_image':True,'same_mounts':True,'same_db_inode':True,
          'rollback_container':BACKUP,'rollback_restart_disabled':True,
          'restart_policy':new['HostConfig']['RestartPolicy']}
(ROOT/'market-cache.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
