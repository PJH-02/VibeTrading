"""Replace Binance alone; persist the override in the boot-owned Compose file."""
import json
from pathlib import Path
import subprocess
import time

BASE=Path('/opt/vibetrading/releases/crypto-relief-final-20261002')
ROOT=Path('/opt/vibetrading/releases/binance-bridge-fix-20261003')
DATA=Path('/srv/vibetrading-data/runtime_data')
COMPOSE=BASE/'deploy/crypto-r2.yml'
manifest=json.loads((ROOT/'.deploy-release.json').read_text())
assert manifest['test_passed'] is True
before=COMPOSE.read_text()
config=json.loads(before)
service=config['services']['crypto-collector-binance']
assert service['image'].startswith('${VIBETRADING_CRYPTO_IMAGE') or service['image']==manifest['base_image']
other_names=['nats','storage','maintenance','collectors','telegram','crypto-storage',
             'crypto-maintenance','crypto-collector-bitget','crypto-collector-bybit']
inspected=json.loads(subprocess.check_output(['docker','inspect',*['vibetrading-'+n+'-1' for n in other_names]]))
others={c['Name']:c['Id'] for c in inspected}
protected=json.loads((Path('/opt/vibetrading/releases/crypto-relief-20261002')/'before.json').read_text())
service['image']=manifest['image']
service['environment'].update(VIBETRADING_BUILD_REVISION=manifest['revision'],
    VIBETRADING_BUILD_CONFIG_HASH=manifest['config_hash'],VIBETRADING_IMAGE_ID=manifest['image'])
(ROOT/'crypto-r2-before.json').write_text(before)
started=time.time_ns()
cmd=['docker','compose','--project-name','vibetrading','--env-file','/etc/vibetrading/compose.env',
     '--env-file',str(BASE/'deploy/release.env'),'-f',str(COMPOSE),'up','-d','--no-deps','--no-build','crypto-collector-binance']
COMPOSE.write_text(json.dumps(config,indent=2)+'\n')
try:
    subprocess.run(cmd,check=True)
    for _ in range(180):
        h=json.loads((DATA/'state/health/collectors-binance-perp.json').read_text())
        if (h.get('ts_ns',0)>started and h.get('build',{}).get('image_id')==manifest['image']
            and h.get('phase')=='publishing' and h.get('published_count',0)>1000
            and all(time.time_ns()-h.get('symbol_last_seen_ns',{}).get(s,0)<60*10**9 for s in ('BTCUSDT','ETHUSDT','SOLUSDT'))):
            break
        time.sleep(1)
    else:raise RuntimeError('Patched Binance collector did not become ready')
    now=json.loads(subprocess.check_output(['docker','inspect',*others.keys()]))
    assert {c['Name']:c['Id'] for c in now}==others,'Other containers recreated'
    for name,expected in protected['protected_exports'].items():
        s=Path(name).stat()
        assert {'inode':s.st_ino,'size':s.st_size,'mtime_ns':s.st_mtime_ns}==expected
    assert (DATA/'state/vibetrading.sqlite3').stat().st_ino==protected['market_db_inode']
    deployed=json.loads(subprocess.check_output(['docker','inspect','vibetrading-crypto-collector-binance-1']))[0]
    assert deployed['Image']==manifest['image']
    assert deployed['HostConfig']['RestartPolicy']['Name']=='unless-stopped'
except BaseException:
    COMPOSE.write_text(before)
    subprocess.run(cmd,check=True)
    raise
base=json.loads((BASE/'.deploy-release.json').read_text())
base.setdefault('service_image_overrides',{})['crypto-collector-binance']={
    'image':manifest['image'],'release':str(ROOT),'revision':manifest['revision']}
(BASE/'.deploy-release.json').write_text(json.dumps(base,indent=2))
result={'started_ns':started,'completed_ns':time.time_ns(),'image':manifest['image'],'revision':manifest['revision'],
    'binance_publishing':True,'other_containers_unchanged':True,'protected_exports_preserved':True,
    'market_db_inode_preserved':True,'boot_compose_persisted':str(COMPOSE),
    'binance_health':h,'other_container_ids':others}
(ROOT/'deployment.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k not in ('binance_health','other_container_ids')}),flush=True)
