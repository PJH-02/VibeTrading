import json,pathlib,sqlite3
root=pathlib.Path('/srv/vibetrading-capacity/incident-20261006/market-historical-audit');root.mkdir(exist_ok=True);root.chmod(0o777)
db=pathlib.Path('/srv/vibetrading-capacity/market-state/vibetrading.sqlite3');wal=db.with_name(db.name+'-wal');assert not wal.exists() or wal.stat().st_size==0
c=sqlite3.connect('file:'+str(db)+'?immutable=1',uri=True);c.row_factory=sqlite3.Row;c.execute('PRAGMA cache_size=-8192')
rows=[dict(r) for r in c.execute("SELECT file_id,provider,event_type,trading_date,path,remote_key,upload_status,local_present,rows,byte_size,file_sha256 FROM parquet_manifest WHERE lifecycle_state='active' AND provider IN ('kis','kiwoom') AND event_type IN ('order_book','trade') AND trading_date<'2026-09-26' ORDER BY trading_date,file_id")];c.close()
(root/'manifest.json').write_text(json.dumps(rows));print(json.dumps({'files':len(rows),'local':sum(r['local_present'] for r in rows),'rows':sum(r['rows'] for r in rows)}))
