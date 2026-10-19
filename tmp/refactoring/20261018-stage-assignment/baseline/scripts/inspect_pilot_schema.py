import json
from pathlib import Path
from vibetrading.dashboard.projection import readonly_db

for path in Path("/audits").glob("bybit_*.sqlite3"):
    with readonly_db(path, immutable=True) as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        print(json.dumps({"file": path.name, "tables": {table: [row[1] for row in db.execute('PRAGMA table_info("' + table.replace('"', '""') + '")')] for table in tables}}, ensure_ascii=False))
