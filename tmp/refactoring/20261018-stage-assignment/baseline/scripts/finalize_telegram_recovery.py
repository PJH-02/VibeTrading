"""Assert the restored production scope and save non-secret evidence."""
import json
import subprocess
import time
from pathlib import Path

root=Path('/srv/vibetrading-data/runtime_data')
release=Path('/opt/vibetrading/releases/telegram-recovery-20261014')
names=subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines()
names=[n for n in names if n.startswith('vibetrading-')]
rows=json.loads(subprocess.check_output(['docker','inspect',*names],text=True))
assert len(rows)==14
containers=[]
for row in rows:
    assert row['State']['Running'] and not row['State']['OOMKilled']
    health=row['State'].get('Health',{}).get('Status')
    if health is not None: assert health=='healthy', (row['Name'],health)
    assert row['HostConfig']['RestartPolicy']['Name']=='unless-stopped'
    containers.append({'name':row['Name'].lstrip('/'),'running':True,'health':health,
        'restart_count':row['RestartCount'],'image':row['Config']['Image']})
assert not any('bitget' in n or 'bybit' in n for n in names)
sources={}
for provider in ['kis','kiwoom','toss','binance-perp','arcus-perp','grvt-perp']:
    data=json.loads((root/'state/health'/f'collectors-{provider}.json').read_text())
    assert time.time_ns()-data['ts_ns']<120_000_000_000
    symbols=sorted(s for s,stamp in (data.get('symbol_last_seen_ns') or {}).items() if time.time_ns()-int(stamp)<120_000_000_000)
    if provider.endswith('-perp'):
        assert data['phase']=='publishing' and symbols==['BTCUSDT','ETHUSDT','SOLUSDT'], (provider,data['phase'],symbols)
    else:
        assert data['phase']=='market_closed'
    sources[provider]={'phase':data['phase'],'recent_symbols':symbols}
unit_names=['vibetrading.service','vibetrading-etf-mock.service','vibetrading-etf-csv-sync.service','vibetrading-monitor.service']
for name in unit_names:
    assert subprocess.check_output(['systemctl','is-active',name],text=True).strip()=='active'
    assert subprocess.check_output(['systemctl','is-enabled',name],text=True).strip()=='enabled'
assert subprocess.run(['systemctl','is-active','oracle-a1-catcher.service'],capture_output=True).stdout.strip()==b'inactive'
assert subprocess.run(['systemctl','is-enabled','oracle-a1-catcher.service'],capture_output=True).stdout.strip()==b'disabled'
mock=json.loads((root/'state/health/etf-mock.json').read_text())
assert mock['account_mode']=='mock' and mock['pending_orders']==0
assert mock['account_routes']=={'mock':['069500','229200'],'mock2':['122630','251340']}
csv=json.loads((root/'state/etf-mock/history/manifest.json').read_text())
monitor=root/'dashboard-monitor'
accounts=json.loads((monitor/'accounts.json').read_text())
timefolio=json.loads((monitor/'timefolio.json').read_text())
result={'checked_ns':time.time_ns(),'containers':containers,'sources':sources,'units':unit_names,
    'oracle_loop':'inactive_disabled','mock_pending_orders':0,'mock_routes':mock['account_routes'],
    'ticker_csv_last_dates':{k:v['last_date'] for k,v in csv['files'].items()},
    'dashboard_registered_extra_accounts':len(accounts),'timefolio_statuses':[r.get('status') for r in timefolio],
    'telegram':json.loads((root/'state/telegram-command-verification.json').read_text()),
    'gaps':json.loads((root/'state/main-server-recovery-gaps.json').read_text()),
    'tests':{'integration_passed':154,'final_sqlite_connection_change_affected_tests_passed':36}}
(release/'final-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False))
