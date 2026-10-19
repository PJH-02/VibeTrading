"""Inspect retained rollback inputs without exposing credentials."""
import json
import sqlite3
from pathlib import Path

for file in sorted(Path('/srv/vibetrading-capacity/incident-20261006').glob('snapshot*.json')):
    data=json.loads(file.read_text())
    print(file.name, list(data)[:15])
    for key in ['containers','docker']:
        rows=data.get(key)
        if isinstance(rows,list) and rows:
            print(key, list(rows[0]) if isinstance(rows[0],dict) else type(rows[0]).__name__)
print('ENV_NAMES', [line.split('=',1)[0] for line in Path('/etc/vibetrading/app.env').read_text().splitlines() if '=' in line and not line.startswith('#')])
root=Path('/srv/vibetrading-data/runtime_data/state/etf-mock')
with sqlite3.connect((root/'journal.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
    print('DAILY', [json.loads(row[0]) for row in db.execute("SELECT payload FROM notifications WHERE kind='mock_daily'")])
    print('ORDER_STATUS',list(db.execute('SELECT symbol,status,filled,day FROM orders')))
    print('EXPECTED_ACCOUNT_IDENTITIES',list(db.execute("SELECT key,value FROM metadata WHERE key IN ('account_identity','mock2_account_identity')")))
