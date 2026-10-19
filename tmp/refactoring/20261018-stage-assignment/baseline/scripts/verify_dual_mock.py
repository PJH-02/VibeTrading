"""Host verification: active routes, durable assumptions, delivery and smoke fills."""
import json
import sqlite3
import subprocess
import time
from decimal import Decimal
from pathlib import Path

release=Path('/opt/vibetrading/releases/dual-mock-20261014')
build=json.loads((release/'build.json').read_text()); manifest=json.loads((release/'manifest.json').read_text())
root=Path('/srv/vibetrading-data/runtime_data/state/etf-mock')
health=json.loads((root.parent/'health/etf-mock.json').read_text())
assert health['phase']=='ready' and health['pending_orders']==0,health.get('phase')
assert health['account_routes']=={'mock':['069500','229200'],'mock2':['122630','251340']}
services={}
telegram_release=Path('/opt/vibetrading/releases/dual-mock-telegram-retry-20261014')
telegram=json.loads((telegram_release/'receipt.json').read_text()) if (telegram_release/'receipt.json').exists() else build
for name in ['etf-mock','telegram']:
    full='vibetrading-'+name+'-1'; row=json.loads(subprocess.check_output(['docker','inspect',full],text=True))[0]
    expected=telegram['image'] if name=='telegram' else build['image']
    assert row['State']['Running'] and row['Config']['Image']==expected
    services[name]={'running':True,'image':row['Config']['Image'],'restarts':row['RestartCount'],
        'health':row['State'].get('Health',{}).get('Status')}
    log=subprocess.run(['docker','logs','--since',row['State']['StartedAt'],full],text=True,capture_output=True)
    assert 'Traceback' not in log.stdout+log.stderr
db=sqlite3.connect('file:'+str(root/'journal.sqlite3')+'?mode=ro',uri=True); db.row_factory=sqlite3.Row
assumed=[dict(row) for row in db.execute('SELECT * FROM assumed_fills ORDER BY event_ns')]
net=fees=Decimal(0)
for row in assumed:
    fees+=Decimal(row['fee']); net-=(1 if row['side']=='buy' else -1)*row['quantity']*Decimal(row['price'])+Decimal(row['fee'])
notifications=[dict(row) for row in db.execute('SELECT kind,COUNT(*) AS total,SUM(sent_ns IS NOT NULL) AS sent FROM notifications GROUP BY kind')]
assert next(row for row in notifications if row['kind']=='mock_assumed')['sent']==4
assert len(assumed)==4
assert db.execute('SELECT SUM(quantity) FROM positions').fetchone()[0] in (None,0)
validation=json.loads((root/'mock2-validation.json').read_text())
assert validation['complete'] and all(row['filled']==1 for row in validation['orders'])
receipt={'checked_ns':time.time_ns(),'revision':build['revision'],'tests':build['tests'],'services':services,
    'telegram_revision':telegram['revision'],
    'health':{key:health.get(key) for key in ['phase','account_routes','pending_orders','assumed_positions','vehicle_bars']},
    'notifications':notifications,'assumed':{'net_pnl':str(net),'fees':str(fees),'fills':assumed},'validation':validation}
(release/'verification.json').write_text(json.dumps(receipt,indent=2)); print(json.dumps(receipt))
