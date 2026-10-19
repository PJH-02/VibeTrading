import json,pathlib,sqlite3
root=pathlib.Path('/srv/vibetrading-capacity/incident-20261006/market-audit');root.mkdir(exist_ok=True);root.chmod(0o777)
c=sqlite3.connect('file:/srv/vibetrading-data/runtime_data/state/vibetrading.sqlite3?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('PRAGMA cache_size=-8192')
rows=[dict(r) for r in c.execute("SELECT file_id,provider,event_type,trading_date,path,remote_key,upload_status,local_present,rows,byte_size,file_sha256 FROM parquet_manifest WHERE lifecycle_state='active' AND provider IN ('kis','kiwoom') AND trading_date>='2026-09-26' ORDER BY trading_date,file_id")];c.close()
(root/'manifest.json').write_text(json.dumps(rows));print(json.dumps({'files':len(rows),'local':sum(r['local_present'] for r in rows),'rows':sum(r['rows'] for r in rows),'bytes':sum(r['byte_size'] for r in rows)}))
