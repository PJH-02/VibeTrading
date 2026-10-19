"""Read-only catalogue inventory; never reads credentials."""
import json, sqlite3
from pathlib import Path
db=Path('/srv/vibetrading-crypto-state/vibetrading.sqlite3')
c=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True); c.row_factory=sqlite3.Row
print(json.dumps([dict(r) for r in c.execute("SELECT provider,venue,event_type,COUNT(*) files,SUM(rows) rows,SUM(byte_size) bytes,MIN(min_ts_ns) start_ns,MAX(max_ts_ns) end_ns,SUM(local_present) local_files FROM parquet_manifest WHERE lifecycle_state='active' GROUP BY provider,venue,event_type")]))
out=Path('/srv/vibetrading-capacity/fv-research-20261004'); out.mkdir(exist_ok=True)
rows=[dict(r) for r in c.execute("SELECT * FROM parquet_manifest WHERE lifecycle_state='active' AND event_type IN ('order_book','trade','data_gap') ORDER BY min_ts_ns,file_id")]
(out/'catalogue.json').write_text(json.dumps(rows),encoding='utf-8')
print(json.dumps({'catalogue_rows':len(rows),'output':str(out)}))
