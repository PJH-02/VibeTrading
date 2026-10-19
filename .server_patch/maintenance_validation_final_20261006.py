import json,pathlib,time,urllib.request,os,sqlite3
root=pathlib.Path('/srv/vibetrading-capacity/incident-20261006')
rows=[]
for i in range(13):
    row={'ts_ns':time.time_ns(),'health':{}}
    for directory in ('/srv/vibetrading-crypto-state/health','/srv/vibetrading-data/runtime_data/state/health'):
        for p in pathlib.Path(directory).glob('*.json'):
            if p.stem in ('maintenance','storage','crypto-storage') or p.stem.endswith('-perp') or p.stem=='collectors-hyperliquid':
                row['health'][str(p)]=json.loads(p.read_text())
    with urllib.request.urlopen('http://127.0.0.1:8222/jsz?streams=true&consumers=true&config=true',timeout=10) as f:row['nats']=json.load(f)
    rows.append(row)
    (root/'maintenance-validation-final.json').write_text(json.dumps(rows,indent=2))
    print(json.dumps({'sample':i,'maintenance':{k:v for k,v in row['health'].items() if k.endswith('/maintenance.json')}}),flush=True)
    if i<12:time.sleep(5)
path=pathlib.Path('/srv/vibetrading-crypto-state/vibetrading.sqlite3')
previous=os.geteuid()
try:
    os.seteuid(path.stat().st_uid)
    conn=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=5)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA cache_size=-2048')
    result={'ts_ns':time.time_ns(),'metadata':[dict(r) for r in conn.execute("SELECT * FROM runtime_metadata WHERE key LIKE '%prune%' OR key LIKE '%archive_cursor%' OR key LIKE '%retir%' ORDER BY key")],
            'latest_pruned_blocks':[dict(r) for r in conn.execute('SELECT block_id,prune_cursor,pruned_ts_ns FROM crypto_delivery_recovery WHERE pruned_ts_ns IS NOT NULL ORDER BY block_id DESC LIMIT 3')],
            'recent_uploads':[dict(r) for r in conn.execute("SELECT file_id,verified_ts_ns,rows,byte_size FROM parquet_manifest WHERE verified_ts_ns IS NOT NULL ORDER BY verified_ts_ns DESC LIMIT 3")]}
    conn.close()
finally:os.seteuid(previous)
(root/'maintenance-progress-final.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2),flush=True)
