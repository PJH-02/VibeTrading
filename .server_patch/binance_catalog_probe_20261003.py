import json
from pathlib import Path
import sqlite3
root=Path('/opt/vibetrading/releases/binance-bridge-fix-20261003')
cut=json.loads((root/'deployment.json').read_text())['completed_ns']
db=sqlite3.connect('file:/srv/vibetrading-crypto-state/vibetrading.sqlite3?mode=ro',uri=True,timeout=3)
db.execute('PRAGMA query_only=ON')
print(json.dumps({'cutoff':cut,'recent_groups':db.execute('SELECT provider,event_type,COUNT(*),SUM(rows),SUM(local_present),MAX(created_ts_ns) FROM parquet_manifest WHERE created_ts_ns>=? GROUP BY 1,2',(cut,)).fetchall(),
 'latest':db.execute('SELECT path,provider,event_type,local_present,created_ts_ns FROM parquet_manifest ORDER BY created_ts_ns DESC LIMIT 16').fetchall()}))
db.close()
