"""Activate verified collector/storage changes and persist mock ETF service."""
import copy
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

release = Path('/opt/vibetrading/releases/scope-20261013')
build = json.loads((release/'build.json').read_text())
image = build['image']
def run(*args):
    return subprocess.check_output(args,text=True).strip()
changes = []
for name in ('runtime.yaml','universe.yaml','crypto_runtime.yaml','etf_strategy_provenance.json'):
    target = Path('/root/vibetrading/config')/name
    backup = release/(name+'.before')
    if target.exists() and not backup.exists():
        shutil.copy2(target,backup)
    shutil.copy2(release/'config'/name,target)

market = Path('/opt/vibetrading/releases/capacity-20261006/market.yml')
storage = Path('/opt/vibetrading/releases/capacity-maintenance-final-20261006/market-storage.yml')
for path,names in ((market,['collectors']), (storage,['storage','maintenance'])):
    backup = release/(path.name+'.before')
    if not backup.exists():
        shutil.copy2(path,backup)
    config = json.loads(path.read_text())
    for name in names:
        service = config['services'][name]
        changes.append({'service':name,'old_image':service['image'],'image':image})
        service['image'] = image
        service['environment']['VIBETRADING_BUILD_REVISION'] = build['revision']
        service['environment']['VIBETRADING_IMAGE_ID'] = image
        if name=='collectors':
            service['environment']['KIS_USE_MOCK'] = 'false'
        service['environment'].pop('VIBETRADING_BUILD_CONFIG_HASH',None)
        config_hash = hashlib.sha256(json.dumps(service,sort_keys=True).encode()).hexdigest()
        service['environment']['VIBETRADING_BUILD_CONFIG_HASH'] = config_hash
    path.write_text(json.dumps(config,indent=2))
    compose = ['docker','compose','--project-name','vibetrading','-f',str(path)]
    run(*compose,'config','--quiet')
    # Storage learns ETF NAV before collectors begin publishing it.
    if path == storage:
        run(*compose,'up','-d','--no-deps','--no-build',*names)

collector = json.loads(market.read_text())['services']['collectors']
# Existing stream and durable IDs are preserved; only add NAV subject coverage.
cmd = ['docker','run','--rm','--network','vibetrading_default','--memory','256m',
    '--env-file','/etc/vibetrading/app.env']
for key,value in collector['environment'].items():
    cmd.extend(['-e',f'{key}={value}'])
cmd.extend(['-v','/srv/vibetrading-data/runtime_data:/data',image,'python','main.py','nats-init'])
run(*cmd)
run('docker','compose','--project-name','vibetrading','-f',str(market),'up','-d','--no-deps','--no-build','collectors')

# Save the dedicated service now; start it only after its account/API preflight.
etf = copy.deepcopy(collector)
etf['env_file'] = ['/etc/vibetrading/app.env','/root/vibetrading/.env']
etf['command'] = ['python','-m','vibetrading.etf_trading','run']
etf['mem_limit'] = '512m'
etf['healthcheck'] = {'test':['CMD','python','main.py','health-check','--component','etf-mock','--max-age-seconds','120'],
    'interval':'30s','timeout':'10s','retries':4}
etf['environment'].update({'KIS_USE_MOCK':'true','ARROW_NUM_THREADS':'1','OMP_NUM_THREADS':'1'})
csv_sync = copy.deepcopy(etf)
csv_sync['command'] = ['python','-m','vibetrading.etf_csv_sync','run']
csv_sync['environment']['KIS_USE_MOCK'] = 'false'
csv_sync['healthcheck']['test'] = ['CMD','python','main.py','health-check','--component','etf-csv-sync','--max-age-seconds','180']
for service in (etf,csv_sync):
    service['environment'].pop('VIBETRADING_BUILD_CONFIG_HASH',None)
    service['environment']['VIBETRADING_BUILD_CONFIG_HASH'] = hashlib.sha256(json.dumps(service,sort_keys=True).encode()).hexdigest()
etf_config = {'services':{'etf-mock':etf,'etf-csv-sync':csv_sync},'networks':{'default':{'external':True,'name':'vibetrading_default'}}}
(release/'etf-mock.yml').write_text(json.dumps(etf_config,indent=2))
receipt = {'activated_ns':time.time_ns(),'image':image,'revision':build['revision'],'changes':changes,'etf_service':'prepared'}
(release/'activation.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt),flush=True)
