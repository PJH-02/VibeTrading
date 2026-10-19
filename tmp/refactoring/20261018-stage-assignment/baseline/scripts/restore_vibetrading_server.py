"""Recover the mistakenly decommissioned main server, reusing retained state."""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT=Path('/root/vibetrading')
RELEASE=Path('/opt/vibetrading/releases/telegram-recovery-20261014')
DATA=Path('/srv/vibetrading-data/runtime_data')

def run(*args):
    return subprocess.check_output(args,text=True).strip()

def prepare():
    ROOT.mkdir(exist_ok=True)
    RELEASE.mkdir(parents=True,exist_ok=True)
    os.chmod(RELEASE,0o700)
    with tarfile.open('/tmp/telegram-recovery-20261014.tar.gz') as archive:
        assert all(m.isfile() and (ROOT/m.name).resolve().is_relative_to(ROOT) for m in archive.getmembers())
        archive.extractall(ROOT,filter='data')
    manifest=json.loads((ROOT/'manifest.json').read_text())
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==digest for p,digest in manifest['files'].items())
    run('docker','build','--pull=false','--network=none','-t','vibetrading:'+manifest['revision'],str(ROOT))
    image=run('docker','image','inspect','--format','{{.Id}}','vibetrading:'+manifest['revision'])
    (RELEASE/'build.json').write_text(json.dumps({'image':image,'revision':manifest['revision']}))
    print(json.dumps({'image':image,'revision':manifest['revision'],'source_verified':len(manifest['files'])}),flush=True)

def configure():
    build=json.loads((RELEASE/'build.json').read_text()); image=build['image']
    # Preserve verified disk/cache/archive tuning from the last retained audit.
    snapshot=json.loads(sorted(Path('/srv/vibetrading-capacity/incident-20261006').glob('snapshot*.json'))[-1].read_text())
    previous={row['name'].lstrip('/').removeprefix('vibetrading-').removesuffix('-1'):row['env'] for row in snapshot['containers']}
    services={}
    base={'image':image,'env_file':['/etc/vibetrading/app.env'],
        'environment':{'VIBETRADING_DATA_ROOT':'/data','NATS_URL':'nats://nats:4222',
            'VIBETRADING_LOG_DIR':'/tmp/logs','CRYPTO_COLLECTOR_PROVIDERS':'binance-perp,arcus-perp,grvt-perp',
            'ARROW_NUM_THREADS':'1','OMP_NUM_THREADS':'1','NATS_FILE_STORE_MAX_BYTES':'2415919104'},
        'volumes':[str(DATA)+':/data','/srv/vibetrading-crypto-state:/data/crypto_state',str(ROOT/'config')+':/app/config:ro'],
        'restart':'unless-stopped','mem_limit':'512m','stop_grace_period':'90s',
        'logging':{'driver':'local','options':{'max-size':'10m','max-file':'3'}}}
    def add(name,command,component=None,limit='512m'):
        service=copy.deepcopy(base); service['command']=command; service['mem_limit']=limit
        env=service['environment']; env.update(previous.get(name,{}))
        for key in ['VIBETRADING_BUILD_CONFIG_HASH','VIBETRADING_BUILD_REVISION','VIBETRADING_IMAGE_ID']:
            env.pop(key,None)
        env.update({'VIBETRADING_BUILD_REVISION':build['revision'],'VIBETRADING_IMAGE_ID':image,
            'CRYPTO_COLLECTOR_PROVIDERS':'binance-perp,arcus-perp,grvt-perp','NATS_FILE_STORE_MAX_BYTES':'2415919104',
            'CRYPTO_NATS_MAX_BYTES':'134217728'})
        if name.startswith('crypto-'):
            env['DELIVERY_LEDGER_FORMAT']='compact'
        if component:
            service['healthcheck']={'test':['CMD','python','main.py','health-check','--component',component,'--max-age-seconds','180' if component=='etf-csv-sync' else '120'],
                'interval':'30s','timeout':'10s','retries':4}
        services[name]=service
        return service
    add('collectors',['python','main.py','run','--config','/app/config/runtime.yaml','--role','collectors'],'collectors','768m')['environment']['KIS_USE_MOCK']='false'
    add('storage',['python','main.py','run','--role','storage'],'storage','1024m')
    add('maintenance',['ionice','-c2','-n7','nice','-n','10','python','main.py','run','--role','maintenance'],'maintenance','512m')
    add('telegram',['python','main.py','telegram'],limit='256m')
    for provider in ['binance','arcus','grvt']:
        add('crypto-collector-'+provider,['python','main.py','run','--config','/app/config/crypto_runtime.yaml','--role','crypto-collectors','--provider',provider+'-perp'],
            'collectors-'+provider+'-perp','256m')
    for name,limit in [('crypto-storage','1280m'),('crypto-maintenance','512m')]:
        service=add(name,['python','main.py','run','--role',name],'crypto-storage' if name=='crypto-storage' else None,limit)
        service['environment'].update({'VIBETRADING_STATE_DIR':'/data/crypto_state','PARQUET_DATA_ROOT':'/data/crypto_market_data',
            'STORAGE_DATA_DISK_ROOT':'/data/crypto_market_data','STORAGE_STATE_DISK_ROOT':'/data/crypto_state','ARCHIVE_PREFIX':'vibetrading/crypto'})
    for name,role in [('etf-mock','vibetrading.etf_trading'),('etf-csv-sync','vibetrading.etf_csv_sync')]:
        service=add(name,['python','-m',role,'run'],name,'512m')
        service['env_file'].append(str(ROOT/'.env'))
        service['environment']['KIS_USE_MOCK']='true' if name=='etf-mock' else 'false'
    for name in ['maintenance','crypto-maintenance']:
        services[name]['environment'].update({'ARCHIVE_WORKER_ENABLED':'true','ARCHIVE_ACCESS_KEY_FILE':'/run/secrets/archive_access_key','ARCHIVE_SECRET_KEY_FILE':'/run/secrets/archive_secret_key'})
        services[name]['volumes'] += ['/etc/vibetrading/secrets/archive_access_key:/run/secrets/archive_access_key:ro','/etc/vibetrading/secrets/archive_secret_key:/run/secrets/archive_secret_key:ro']
    services['nats']={'image':'nats:2.10-alpine','command':['-c','/etc/nats/nats.conf'],
        'volumes':['/srv/vibetrading-data/nats:/data',str(ROOT/'deploy/nats.conf')+':/etc/nats/nats.conf:ro'],
        'ports':['127.0.0.1:8222:8222'],'mem_limit':'512m','restart':'unless-stopped',
        'logging':base['logging'],'healthcheck':{'test':['CMD','wget','-q','--spider','http://localhost:8222/healthz'],
        'interval':'10s','timeout':'5s','retries':6}}
    for service in services.values():
        env=service.get('environment')
        if env is not None:
            env['VIBETRADING_BUILD_CONFIG_HASH']=hashlib.sha256(json.dumps(service,sort_keys=True).encode()).hexdigest()
    config={'services':services}
    target=RELEASE/'runtime.yml'; target.write_text(json.dumps(config,indent=2))
    run('docker','compose','--project-name','vibetrading','-f',str(target),'config','--quiet')
    # The existing disk guard resumes collectors through this historical path.
    compat=Path('/opt/vibetrading/releases/capacity-20261006'); compat.mkdir(parents=True,exist_ok=True)
    shutil.copy2(target,compat/'market.yml')
    unit=Path('/etc/systemd/system/vibetrading.service')
    shutil.copy2(unit,RELEASE/'vibetrading.service.before')
    unit.write_text(f'''[Unit]
Description=VibeTrading restored collectors, storage and Telegram
Requires=docker.service
After=docker.service network-online.target
RequiresMountsFor=/srv/vibetrading-data /srv/vibetrading-capacity /srv/vibetrading-crypto-state
ConditionPathIsMountPoint=/srv/vibetrading-data

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/root/vibetrading
TimeoutStartSec=300
TimeoutStopSec=300
ExecStart=/usr/bin/docker compose --project-name vibetrading -f {target} up -d --no-deps --no-build nats storage maintenance telegram collectors crypto-storage crypto-maintenance crypto-collector-binance crypto-collector-arcus crypto-collector-grvt
ExecStop=/usr/bin/docker compose --project-name vibetrading -f {target} stop

[Install]
WantedBy=multi-user.target
''')
    for name in ['etf-mock','etf-csv-sync']:
        path=Path('/etc/systemd/system/vibetrading-'+name+'.service')
        if path.exists(): shutil.copy2(path,RELEASE/(name+'.service.before'))
        path.write_text(f'''[Unit]
Description=VibeTrading {name}
Requires=docker.service vibetrading.service
After=docker.service vibetrading.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/root/vibetrading
TimeoutStartSec=180
TimeoutStopSec=90
ExecStart=/usr/bin/docker compose --project-name vibetrading -f {target} up -d --no-deps --no-build {name}
ExecStop=/usr/bin/docker compose --project-name vibetrading -f {target} stop {name}

[Install]
WantedBy=multi-user.target
''')
    run('systemctl','daemon-reload')
    print(json.dumps({'configured_services':list(services),'retired_excluded':['bitget','bybit'],'runtime':str(target)}),flush=True)

if __name__=='__main__':
    {'prepare':prepare,'configure':configure}[sys.argv[1]]()
