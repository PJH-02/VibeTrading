"""Verify the live collector, shared services and fresh committed records."""
import datetime
import json
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

ROOT=Path('/opt/vibetrading/releases/binance-bridge-fix-20261003')
BASE=Path('/opt/vibetrading/releases/crypto-relief-final-20261002')
DATA=Path('/srv/vibetrading-data/runtime_data')
STATE=Path('/srv/vibetrading-crypto-state')
release=json.loads((ROOT/'.deploy-release.json').read_text())
deployment=json.loads((ROOT/'deployment.json').read_text())
names=['crypto-collector-binance','crypto-collector-bybit','crypto-collector-bitget','crypto-storage','crypto-maintenance','nats','storage','maintenance','collectors','telegram']
containers=json.loads(subprocess.check_output(['docker','inspect',*['vibetrading-'+n+'-1' for n in names]]))
states={c['Name']:{'image':c['Image'],'status':c['State']['Status'],'health':c['State'].get('Health',{}).get('Status'),
    'restarts':c['RestartCount'],'oom_killed':c['State']['OOMKilled']} for c in containers}
binance=next(c for c in containers if c['Name']=='/vibetrading-crypto-collector-binance-1')
assert binance['Image']==release['image'] and binance['State']['Running']
assert binance['RestartCount']==0 and not binance['State']['OOMKilled']
for c in containers:
    if c['Name'] in deployment['other_container_ids']:assert c['Id']==deployment['other_container_ids'][c['Name']]
health=json.loads((DATA/'state/health/collectors-binance-perp.json').read_text())
assert health['build']['image_id']==release['image'] and health['phase']=='publishing'
assert time.time_ns()-health['ts_ns']<60*10**9
assert all(time.time_ns()-health['symbol_last_seen_ns'][s]<60*10**9 for s in ('BTCUSDT','ETHUSDT','SOLUSDT'))
publisher_failures=health.get('publisher',{}).get('failed')
assert publisher_failures is None or publisher_failures==0
logs=subprocess.check_output(['docker','logs','vibetrading-crypto-collector-binance-1'],stderr=subprocess.STDOUT,text=True)
log_counts={'errors':sum(' ERROR ' in l for l in logs.splitlines()),'tracebacks':logs.count('Traceback'),
            'warnings':sum(' WARNING ' in l for l in logs.splitlines())}
assert not any(log_counts.values()),log_counts
with urllib.request.urlopen('http://127.0.0.1:8222/jsz?streams=true&consumers=true&config=true',timeout=5) as f:nats=json.load(f)
stream=next(s for a in nats['account_details'] for s in a['stream_detail'] if s['name']=='CRYPTO_DATA')
assert stream['state']['bytes']<stream['config']['max_bytes']*0.5
assert shutil.disk_usage('/').free>1280*1024**2
assert shutil.disk_usage(DATA).free>int(4.5*1024**3)
compose=json.loads((BASE/'deploy/crypto-r2.yml').read_text())
assert compose['services']['crypto-collector-binance']['image']==release['image']
for name in ('crypto-storage','crypto-maintenance','crypto-collector-bybit','crypto-collector-bitget'):
    assert compose['services'][name]['image'].startswith('${VIBETRADING_CRYPTO_IMAGE')
unit=Path('/etc/systemd/system/vibetrading.service').read_text()
assert str(BASE/'deploy/crypto-r2.yml') in unit and '--remove-orphans' not in unit
coverage_code=Path('/tmp/binance-fix-20261003/binance_remote_coverage_20261003.py').read_text().replace('CUTOFF',str(deployment['completed_ns']))
covered=subprocess.run(['docker','exec','-i','vibetrading-crypto-maintenance-1','python','-'],input=coverage_code,
                       stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
assert covered.returncode==0,covered.stderr[-1000:]+covered.stdout
coverage=json.loads(covered.stdout)
storage=json.loads((STATE/'health/crypto-storage.json').read_text())
assert storage['ready']
result={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'minutes_since_deployment':round((time.time_ns()-deployment['completed_ns'])/60e9,2),
    'health':health,'storage_health':storage,'containers':states,'collector_logs':log_counts,
    'root_free_bytes':shutil.disk_usage('/').free,'data_free_bytes':shutil.disk_usage(DATA).free,
    'nats':{'bytes':stream['state']['bytes'],'ratio':stream['state']['bytes']/stream['config']['max_bytes'],
            'undelivered':stream['consumer_detail'][0]['num_pending']},'coverage':coverage,
    'publisher_failed_sample':publisher_failures,'boot_persisted':True,'other_containers_unchanged':True}
(ROOT/'verified.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k not in ('health','storage_health','containers')}),flush=True)
