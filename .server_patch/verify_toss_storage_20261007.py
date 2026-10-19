import hashlib,json,os,pathlib,sqlite3,subprocess,time
out=pathlib.Path('/srv/vibetrading-capacity/incident-20261007');root=pathlib.Path('/srv/vibetrading-data/runtime_data');path=root/'state/vibetrading.sqlite3';uid=os.geteuid()
try:
    os.seteuid(path.stat().st_uid);conn=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=5);conn.row_factory=sqlite3.Row;conn.execute('PRAGMA cache_size=-2048')
    rows=[dict(r) for r in conn.execute("SELECT file_id,path,event_type,rows,created_ts_ns,file_sha256,byte_size,local_present FROM parquet_manifest WHERE provider='toss' AND lifecycle_state='active' AND created_ts_ns>=? ORDER BY created_ts_ns DESC LIMIT 16",(1791310060000000000,))];conn.close()
finally:os.seteuid(uid)
assert rows
for r in rows:
    p=root/pathlib.Path(r['path']).relative_to('/data')
    if r['local_present'] and p.is_file():
        with p.open('rb') as f:
            sha=hashlib.file_digest(f,'sha256').hexdigest()
        r['verified_local_sha256']=sha==r['file_sha256'];assert r['verified_local_sha256']
code='import json,pyarrow.parquet as pq;paths='+repr([r['path'] for r in rows if r.get('verified_local_sha256')])+';print(json.dumps({p:{"rows":pq.ParquetFile(p).metadata.num_rows,"symbols":sorted(set(r["instrument_std_symbol"] for b in pq.ParquetFile(p).iter_batches(batch_size=128,columns=["instrument_std_symbol"]) for r in b.to_pylist()))} for p in paths}))'
verified=json.loads(subprocess.check_output(['docker','exec','vibetrading-storage-1','python','-c',code],text=True))
for r in rows:
    if r['path'] in verified:assert r['rows']==verified[r['path']]['rows']
result={'ts_ns':time.time_ns(),'files':rows,'parquet':verified,'event_types':sorted({r['event_type'] for r in rows})}
(out/'toss-storage-verification.json').write_text(json.dumps(result,indent=2));print(json.dumps({'files':len(rows),'event_types':result['event_types'],'symbols':sorted({s for v in verified.values() for s in v['symbols']})}))
