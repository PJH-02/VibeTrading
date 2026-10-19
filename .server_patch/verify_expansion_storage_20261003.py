import json, pathlib, sqlite3, subprocess, time
ROOT=pathlib.Path('/opt/vibetrading/releases/exchange-expansion-20261003')
def run(*args): return subprocess.check_output(args,text=True).strip()
conn=sqlite3.connect('file:/srv/vibetrading-crypto-state/vibetrading.sqlite3?mode=ro',uri=True,timeout=15)
conn.row_factory=sqlite3.Row
rows=[dict(r) for r in conn.execute("SELECT provider,venue,event_type,count(*) files,sum(rows) rows,sum(byte_size) bytes FROM parquet_manifest WHERE lifecycle_state='active' AND local_present=1 GROUP BY provider,venue,event_type")]
files=[dict(r) for r in conn.execute("SELECT path,provider,venue,event_type,rows FROM parquet_manifest WHERE lifecycle_state='active' AND local_present=1 AND venue IN ('arcus','grvt') ORDER BY created_ts_ns DESC LIMIT 300")]
selected={}
for row in files: selected.setdefault((row['venue'],row['event_type']),row)
code='''import json,pyarrow.parquet as pq,pathlib
files=INPUT
output=[]
for item in files:
    p=item['path']; table=pq.ParquetFile(p).read()
    symbols=set();quotes=set();raw=set()
    for r in table.to_pylist():
        ins=r.get('instrument')
        if isinstance(ins,str): ins=json.loads(ins)
        ins=ins or {}
        symbols.add(str(ins.get('std_symbol',r.get('instrument_std_symbol'))))
        quotes.add(str(ins.get('currency',r.get('instrument_currency'))))
        raw.add(str(ins.get('raw_symbol',r.get('instrument_raw_symbol'))))
    output.append({**item,'actual_rows':table.num_rows,'columns':table.column_names,'symbols':sorted(symbols),'quotes':sorted(quotes),'native_symbols':sorted(raw)})
print(json.dumps(output))'''.replace('INPUT',repr(list(selected.values())))
parquet=json.loads(run('docker','exec','vibetrading-crypto-storage-1','python','-c',code))
credentials={}
def env(path):
    values={}
    for line in pathlib.Path(path).read_text().splitlines():
        if line.startswith('#') or '=' not in line: continue
        k,v=line.split('=',1); values[k]=v.strip().strip('"').strip("'")
    return values
a,b=env('/etc/vibetrading/app.env'),env('/root/vibetrading/.env')
for k in sorted(set(a)|set(b)):
    if k.startswith('GRVT'):
        credentials[k]={'runtime_present':bool(a.get(k)),'source_present':bool(b.get(k)),'same':a.get(k)==b.get(k)}
result={'time_ns':time.time_ns(),'manifest':rows,'parquet_read':parquet,'credential_presence':credentials,
    'disk_usage':run('df','-h','/','/srv/vibetrading-data','/srv/vibetrading-capacity'),
    'mounts':{p:run('findmnt','--target',p,'-o','TARGET,SOURCE,OPTIONS') for p in ('/srv/vibetrading-data','/srv/vibetrading-capacity','/srv/vibetrading-crypto-state','/srv/vibetrading-data/runtime_data/crypto_market_data')},
    'unit_verify':subprocess.run(['systemd-analyze','verify','/etc/systemd/system/vibetrading.service'],capture_output=True,text=True).stderr,
    'service_status':run('systemctl','is-active','vibetrading.service','qemu-guest-agent.service')}
(ROOT/'storage-verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps({**result,'parquet_read':[{k:v for k,v in r.items() if k not in ('columns','path')} for r in parquet]},indent=2))
