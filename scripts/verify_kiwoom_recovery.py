"""Verify the running transport/Telegram fix without submitting orders."""
import json
import sqlite3
import subprocess
import time
from pathlib import Path

release=Path('/opt/vibetrading/releases/kiwoom-recovery-20261014')
build=json.loads((release/'build.json').read_text())
containers={}; errors={}
for name in ['collectors','telegram','etf-mock','etf-csv-sync']:
    full='vibetrading-'+name+'-1'
    data=json.loads(subprocess.check_output(['docker','inspect',full],text=True))[0]
    containers[name]={'image':data['Config']['Image'],'running':data['State']['Running'],
        'health':data['State'].get('Health',{}).get('Status'),'started':data['State']['StartedAt'],'restarts':data['RestartCount']}
    assert data['State']['Running']
    if name in ['collectors','telegram']: assert data['Config']['Image']==build['image']
    log=subprocess.run(['docker','logs','--since',data['State']['StartedAt'],full],text=True,capture_output=True)
    text=log.stdout+log.stderr
    errors[name]={term:text.count(term) for term in ['nats_publish_timeout','collector task failed','Traceback',
        'collector task failed provider=kiwoom','ClientConnectionResetError','status=429']}
root=Path('/srv/vibetrading-data/runtime_data/state')
health={}
for provider in ['kis','kiwoom','toss','binance-perp','arcus-perp','grvt-perp']:
    data=json.loads((root/'health'/('collectors-'+provider+'.json')).read_text())
    fresh=sorted(s for s,ts in data.get('symbol_last_seen_ns',{}).items() if 0<=(time.time_ns()-ts)/1e9<=120)
    health[provider]={'phase':data.get('phase'),'heartbeat_age_seconds':round((time.time_ns()-data['ts_ns'])/1e9,2),'symbols':fresh}
    assert data.get('state')=='running' and fresh,provider
assert set(['069500','122630','114800','252670','229200','233740','251340']).issubset(health['kiwoom']['symbols'])
db=sqlite3.connect('file:'+str(root/'etf-mock/journal.sqlite3')+'?mode=ro',uri=True)
db.row_factory=sqlite3.Row
orders=[dict(row) for row in db.execute('SELECT strategy,symbol,quantity,price,status,filled FROM orders')]
db.close()
# Compare active code, including the Telegram file bind mount, to the build.
code_check="import hashlib,json; from pathlib import Path; print(json.dumps({n:hashlib.sha256(Path('/app/'+n).read_bytes()).hexdigest() for n in ['vibetrading/telegram.py','vibetrading/runtime.py','vibetrading/providers/kiwoom.py']}))"
sources={}
manifest=json.loads((release/'manifest.json').read_text())
for name in ['collectors','telegram']:
    sources[name]=json.loads(subprocess.check_output(['docker','exec','vibetrading-'+name+'-1','python','-c',code_check],text=True))
    assert all(manifest['files'][n]==h for n,h in sources[name].items())
receipt={'checked_ns':time.time_ns(),'revision':build['revision'],'tests':build['tests'],
    'containers':containers,'errors':errors,'health':health,'orders':orders,'active_source_hashes':sources}
assert not any(errors[n][term] for n in ['collectors','telegram'] for term in
    ['nats_publish_timeout','collector task failed','ClientConnectionResetError']),errors
(release/'verification.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt))
