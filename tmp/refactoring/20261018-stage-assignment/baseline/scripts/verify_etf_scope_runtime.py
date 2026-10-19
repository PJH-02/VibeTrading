"""Save runtime evidence without exporting credentials or complete environments."""
import json
import sqlite3
import subprocess
import time
from pathlib import Path

release = Path('/opt/vibetrading/releases/scope-20261013')
build = json.loads((release/'build.json').read_text())
names = ['collectors','storage','maintenance','etf-mock','etf-csv-sync','crypto-collector-binance',
         'crypto-collector-arcus','crypto-collector-grvt','crypto-collector-bitget','crypto-collector-bybit']
containers = {}
for name in names:
    row = json.loads(subprocess.check_output(['docker','inspect','vibetrading-'+name+'-1'],text=True))[0]
    state = row['State']
    containers[name] = {'status':state['Status'],'health':state.get('Health',{}).get('Status'),
        'image':row['Config']['Image'],'restart':row['HostConfig']['RestartPolicy']['Name']}
    if name in {'crypto-collector-bitget','crypto-collector-bybit'}:
        assert state['Status']=='exited' and containers[name]['restart']=='no'
    else:
        assert state['Running'] and containers[name]['health']=='healthy', name
    if name in {'collectors','storage','maintenance','etf-mock','etf-csv-sync'}:
        assert containers[name]['image']==build['image'], name
    if name=='etf-mock':
        env = dict(e.split('=',1) for e in row['Config']['Env'] if '=' in e)
        assert env['KIS_USE_MOCK']=='true'
        assert all(env.get(k) for k in ['KIS_MOCK_APP_KEY','KIS_MOCK_APP_SECRET','KIS_MOCK_ACCOUNT_NUMBER'])
        assert env['KIS_MOCK_ACCOUNT_PRODUCT_CODE']=='01'
        containers[name]['account_mode']='mock'
        containers[name]['command']=row['Config']['Cmd']

health = json.loads(Path('/srv/vibetrading-data/runtime_data/state/health/etf-mock.json').read_text())
assert time.time_ns()-health['ts_ns'] < 120_000_000_000
assert health['account_mode']=='mock' and health['build']['build_revision']==build['revision']
assert set(health['vehicle_bars'])=={'069500','122630','229200','251340'}
csv_manifest = json.loads(Path('/srv/vibetrading-data/runtime_data/state/etf-mock/history/manifest.json').read_text())
assert set(csv_manifest['files'])=={'069500_daily','069500_minute','122630_daily','122630_minute',
    '229200_daily','229200_minute','251340_daily','251340_minute','KOSPI200_daily','SP500_daily'}
assert csv_manifest['files']['SP500_daily']['last_date']=='2026-10-12'
csv_health = json.loads(Path('/srv/vibetrading-data/runtime_data/state/health/etf-csv-sync.json').read_text())
assert time.time_ns()-csv_health['ts_ns'] < 180_000_000_000
assert csv_health['state']=='running' and csv_health['last_sync']['us_completed_session']=='2026-10-12'
assert subprocess.check_output(['systemctl','is-enabled','vibetrading-etf-csv-sync.service'],text=True).strip()=='enabled'
db = sqlite3.connect('file:/srv/vibetrading-data/runtime_data/state/etf-mock/journal.sqlite3?mode=ro',uri=True)
orders = db.execute('SELECT count(*) FROM orders').fetchone()[0]
assert orders==0, 'Unexpected actual broker submissions during closed-session verification'
db.close()
enabled = subprocess.check_output(['systemctl','is-enabled','vibetrading-etf-mock.service'],text=True).strip()
active = subprocess.check_output(['systemctl','is-active','vibetrading-etf-mock.service'],text=True).strip()
assert enabled=='enabled' and active=='active'
scope = json.loads((release/'final-scope.json').read_text())
assert not any(scope['local_files'][p] for p in ['binance','hyperliquid','bitget-perp','bybit-perp'])
assert not scope['legacy_exports']
for stream in ['MARKET_DATA','CRYPTO_DATA','OPERATIONS']:
    assert not scope[stream]['option_subjects_removed'] and not scope[stream]['retired_crypto_subjects']
result = {'verified_ns':time.time_ns(),'revision':build['revision'],'containers':containers,
    'service':{'enabled':enabled,'active':active},'mock_health':health,'actual_order_count':orders,
    'csv_file_count':len(csv_manifest['files']),'csv_health':csv_health,
    'crypto_subject_count':len(scope['CRYPTO_DATA']['subjects'])}
(release/'runtime-verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
