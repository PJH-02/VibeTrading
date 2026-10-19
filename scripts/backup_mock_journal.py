"""Read-only SQLite snapshot before the intraday mock service upgrade."""
import json
import sqlite3
from pathlib import Path

source=Path('/srv/vibetrading-data/runtime_data/state/etf-mock/journal.sqlite3')
target=Path('/opt/vibetrading/releases/collector-alerts-20261014/journal-before.sqlite3')
target.parent.mkdir(exist_ok=True)
with sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True) as db:
    pending=db.execute("SELECT COUNT(*) FROM orders WHERE status IN ('pending','unknown','submitting')").fetchone()[0]
    assert pending==0,'Reconcile pending orders before the intraday deployment'
    with sqlite3.connect(target) as backup: db.backup(backup)
print(json.dumps({'journal_backup':str(target),'pending_orders':pending}))
