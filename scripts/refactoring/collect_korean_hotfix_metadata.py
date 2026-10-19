"""Read-only deployment identity collector. Never emits environment values."""
import json
from pathlib import Path
import subprocess

REMOTE = r'''
import datetime,hashlib,json,pathlib,subprocess
ROOT=pathlib.Path('/opt/vibetrading/releases/telegram-recovery-20261014')
NAMES=('vibetrading-etf-mock-1','vibetrading-etf-csv-sync-1')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def identity(value):return sha(json.dumps(value,sort_keys=True,separators=(',',':')).encode())
def run(args):
    p=subprocess.run(args,capture_output=True,timeout=25)
    if p.returncode:raise RuntimeError('metadata command failed; output suppressed')
    return p.stdout
out={'schema_version':1,'status':'READ_ONLY_IDENTITY_PINS_NOT_AUTHORIZATION',
     'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
     'compose_root':str(ROOT),'compose_files':{},'containers':{},'compose_services':[]}
for name in ('runtime.yml','dashboard.yml'):
    p=ROOT/name
    if p.stat().st_size>1048576:raise ValueError('compose file bound')
    out['compose_files'][str(p)]=sha(p.read_bytes())
config=json.loads(run(['docker','compose','--project-directory',str(ROOT),'-p','vibetrading',
                      '-f',str(ROOT/'runtime.yml'),'-f',str(ROOT/'dashboard.yml'),'config','--format','json']))
out['resolved_compose_sha256']=identity(config)
out['compose_services']=sorted(config.get('services',{}))
for name in NAMES:
    d=json.loads(run(['docker','inspect',name]))[0]
    labels=d['Config'].get('Labels') or {}
    config_identity={k:d['Config'].get(k) for k in ('Cmd','Entrypoint','Env','WorkingDir','User','Healthcheck','StopSignal')}
    out['containers'][name]={'container_id':d['Id'],'image_id':d['Image'],'image_tag':d['Config']['Image'],
        'state':d['State']['Status'],'restart_count':d['RestartCount'],
        'service':labels.get('com.docker.compose.service'),'project':labels.get('com.docker.compose.project'),
        'compose_config_files':labels.get('com.docker.compose.project.config_files'),
        'runtime_config_sha256':identity({'Config':d['Config'],'HostConfig':d['HostConfig'],'Mounts':d['Mounts']}),
        'process_config_sha256':identity(config_identity),
        'env_identity_sha256':identity(sorted(d['Config'].get('Env') or [])),
        'host_config_sha256':identity(d['HostConfig']),
        'mounts_sha256':identity(d['Mounts']),
        'compose_service_sha256':identity(config['services'].get(labels.get('com.docker.compose.service')))}
print(json.dumps(out,sort_keys=True))
'''


def main():
    result = subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8',
        '49.50.141.106','python3','-'], input=REMOTE, text=True, encoding='utf-8',
        capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError('Read-only deployment metadata failed; remote output suppressed')
    data = json.loads(result.stdout)
    path = Path('docs/evidence/refactoring_20261019/korean_hotfix_deploy_identity.json')
    path.write_text(json.dumps(data,indent=2),encoding='utf-8')
    print(json.dumps({'path':str(path),'status':data['status'],
                      'containers':list(data['containers']),'environment_values_emitted':False}))


if __name__ == '__main__':
    main()
