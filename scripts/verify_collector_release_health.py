"""Host-side evidence with no credentials, order submissions or log secrets."""
import json
import subprocess
import time
from pathlib import Path

core=Path('/opt/vibetrading/releases/scope-20261013')
build=json.loads((core/'build.json').read_text())
names=['collectors','storage','maintenance','telegram','etf-mock','etf-csv-sync',
    'crypto-collector-binance','crypto-collector-arcus','crypto-collector-grvt',
    'crypto-collector-bitget','crypto-collector-bybit']
containers={}
for name in names:
    full='vibetrading-'+name+'-1'
    row=json.loads(subprocess.check_output(['docker','inspect',full],text=True))[0]
    containers[name]={'running':row['State']['Running'],'status':row['State']['Status'],
        'health':row['State'].get('Health',{}).get('Status'),'started':row['State']['StartedAt'],
        'restart':row['HostConfig']['RestartPolicy']['Name']}
    if name in names[:6]:
        assert row['Config']['Image']==build['image'],name
        assert row['State']['Running'],name
    if name in names[-2:]:
        assert not row['State']['Running'] and row['HostConfig']['RestartPolicy']['Name']=='no'
row=json.loads(subprocess.check_output(['docker','inspect','vibetrading-collectors-1'],text=True))[0]
log=subprocess.run(['docker','logs','--since',row['State']['StartedAt'],'vibetrading-collectors-1'],capture_output=True,text=True)
text=log.stdout+log.stderr
errors={term:text.count(term) for term in ['nats_publish_timeout','NatsPublishError','collector task failed','NameError']}
assert not any(errors.values()),errors
health={}
root=Path('/srv/vibetrading-data/runtime_data/state')
for provider in ['kis','kiwoom','toss','binance-perp','arcus-perp','grvt-perp']:
    data=json.loads((root/'health'/('collectors-'+provider+'.json')).read_text())
    fresh=sorted(s for s,ts in data.get('symbol_last_seen_ns',{}).items() if 0<=(time.time_ns()-ts)/1e9<=120)
    health[provider]={'phase':data.get('phase'),'heartbeat_age':round((time.time_ns()-data['ts_ns'])/1e9,2),'symbols':fresh}
    assert fresh and data.get('state')=='running',provider
assert set(['069500','122630','114800','252670','229200','233740','251340']).issubset(health['kiwoom']['symbols'])
caches={p.name:round(time.time()-p.stat().st_mtime,2) for p in (root/'universe_cache').glob('*.json')}
receipt={'checked_ns':time.time_ns(),'revision':build['revision'],'containers':containers,'collector_log_errors':errors,
    'collector_health':health,'universe_cache_age_seconds':caches}
Path('/opt/vibetrading/releases/collector-alerts-20261014/verification.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt))
