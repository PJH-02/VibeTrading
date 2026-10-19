"""Restore the two previously running read-only dashboard workers."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

root=Path('/root/vibetrading')
release=Path('/opt/vibetrading/releases/telegram-recovery-20261014')
build=json.loads((release/'build.json').read_text())
monitor=Path('/srv/vibetrading-data/runtime_data/dashboard-monitor')
monitor.mkdir(exist_ok=True)
os.chown(monitor,10001,10001); os.chmod(monitor,0o700)
dashboard=Path('/opt/vibetrading/dashboard')
dashboard.mkdir(parents=True,exist_ok=True)
if not (dashboard/'monitor').exists(): (dashboard/'monitor').symlink_to(monitor,target_is_directory=True)
config=json.loads((release/'runtime.yml').read_text())
volumes=[str(monitor)+':/monitor']
envfiles=['/root/vibetrading/.env','/etc/vibetrading/dashboard.env']
common={'image':build['image'],'env_file':envfiles,'volumes':volumes,'mem_limit':'192m','cpus':.5,
    'read_only':True,'tmpfs':['/tmp:size=16m'],'restart':'unless-stopped',
    'logging':{'driver':'local','options':{'max-size':'2m','max-file':'2'}}}
bridge=dict(common,command=['python','-m','vibetrading.dashboard.bridge'],
    environment={'NATS_URL':'nats://nats:4222','DASHBOARD_CRYPTO_VENUES':'binance,grvt,arcus',
        'DASHBOARD_DATA_ROOT':'/data','DASHBOARD_MONITOR_ROOT':'/monitor','DASHBOARD_MM_ROOT':'/pilot'},
    volumes=volumes+['/srv/vibetrading-data/runtime_data:/data:ro','/srv/vibetrading-crypto-state:/data/crypto_state:ro',
        '/srv/vibetrading-data/runtime_data/liquidity_mm:/pilot:ro'],
    healthcheck={'test':['CMD','python','-c',"import json,time; s=json.load(open('/monitor/health.json')); assert time.time()*1000-s['updated_ms']<60000"],
        'interval':'30s','timeout':'5s','retries':3})
timefolio=dict(common,image='vibetrading-dashboard-timefolio:recovery-20261014',
    command=['python','-m','vibetrading.dashboard.timefolio_monitor'],mem_limit='384m',shm_size='64m',tmpfs=['/tmp:size=64m'],
    environment={'DASHBOARD_MONITOR_ROOT':'/monitor','PLAYWRIGHT_BROWSERS_PATH':'/opt/dashboard-browsers','VIBETRADING_DATA_ROOT':'/tmp/dashboard'})
if sys.argv[1]=='prepare':
    # This key was on the deleted OS code path. Generate only if no retained key
    # exists, and keep its replacement on the preserved data disk from now on.
    code="""from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import os
p=Path('/monitor/private.pem')
if not p.exists():
 k=rsa.generate_private_key(public_exponent=65537,key_size=3072)
 p.write_bytes(k.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
 os.chmod(p,0o600)
 print('dashboard_key_regenerated')
else: print('dashboard_key_retained')
"""
    subprocess.check_call(['docker','run','--rm','--network=none','-v',str(monitor)+':/monitor',build['image'],'python','-c',code])
    file=release/'dashboard.yml'
    file.write_text(json.dumps({'services':{'bridge':bridge,'timefolio':timefolio},'networks':{'default':{'external':True,'name':'vibetrading_default'}}},indent=2))
    subprocess.check_call(['docker','compose','--project-name','vibetrading-monitor','-f',str(file),'up','-d','--no-deps','--no-build','bridge'])
    (release/'Dockerfile.timefolio').write_text(f'''FROM vibetrading:{build['revision']}
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/dashboard-browsers
RUN python -m playwright install --with-deps chromium --only-shell && rm -rf /var/lib/apt/lists/*
USER vibetrading
''')
    subprocess.check_call(['docker','build','--pull=false','-f',str(release/'Dockerfile.timefolio'),'-t',timefolio['image'],str(release)])
elif sys.argv[1]=='activate':
    subprocess.check_call(['docker','compose','--project-name','vibetrading-monitor','-f',str(release/'dashboard.yml'),'up','-d','--no-deps','--no-build'])
    unit=Path('/etc/systemd/system/vibetrading-monitor.service')
    unit.write_text(f'''[Unit]
Description=VibeTrading dashboard monitors
Requires=docker.service vibetrading.service
After=docker.service vibetrading.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
TimeoutStartSec=180
ExecStart=/usr/bin/docker compose --project-name vibetrading-monitor -f {release/'dashboard.yml'} up -d --no-deps --no-build
ExecStop=/usr/bin/docker compose --project-name vibetrading-monitor -f {release/'dashboard.yml'} stop

[Install]
WantedBy=multi-user.target
''')
    subprocess.check_call(['systemctl','daemon-reload'])
    subprocess.check_call(['systemctl','enable','--now','vibetrading-monitor.service'])
