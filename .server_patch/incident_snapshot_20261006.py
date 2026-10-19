"""Read-only incident capture. Never exports credentials or changes services."""
import collections, datetime, json, pathlib, sqlite3, subprocess, time, urllib.request,os
OUT=pathlib.Path('/srv/vibetrading-capacity/incident-20261006')
OUT.mkdir(exist_ok=True)
def run(*args):
    p=subprocess.run(args,text=True,capture_output=True,timeout=30)
    return p.stdout if p.returncode==0 else {'returncode':p.returncode,'error':p.stderr[-1500:]}
def db(path):
    # SQLite read-only opens can create WAL/SHM. Create those as the database
    # owner so a root audit cannot make a later application writer read-only.
    previous=os.geteuid()
    try:
        os.seteuid(path.stat().st_uid)
        conn=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=5)
        conn.row_factory=sqlite3.Row;conn.execute('PRAGMA cache_size=-2048')
        conn.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchall()
        return conn
    finally:os.seteuid(previous)
result={'ts_ns':time.time_ns(),'date':run('date','-Is'),'uptime':run('uptime'),'disks':run('df','-h'),
    'memory':run('free','-m'),'pressure':{k:pathlib.Path('/proc/pressure/'+k).read_text() for k in ('cpu','io','memory')},'health':{},'gaps':{},'storage_errors':{},'catalogue':{}}
for root in (pathlib.Path('/srv/vibetrading-data/runtime_data/state'),pathlib.Path('/srv/vibetrading-crypto-state')):
    for p in (root/'health').glob('*.json'):
        if p.stem.startswith('collectors') or p.stem in ('crypto-storage','storage','maintenance','crypto-maintenance'):
            result['health'][str(p)]=json.loads(p.read_text())
    p=root/'collection_gaps.sqlite3'
    if p.exists():
        conn=db(p); result['gaps'][str(p)]=[dict(r) for r in conn.execute('SELECT * FROM gaps ORDER BY started_ns')];conn.close()
    p=root/'storage_errors.jsonl'
    if p.exists():
        with p.open() as f: result['storage_errors'][str(p)]=list(collections.deque(f,maxlen=30))
    p=root/'vibetrading.sqlite3'
    if p.exists():
        conn=db(p)
        result['catalogue'][str(p)]=[dict(r) for r in conn.execute("SELECT provider,event_type,trading_date,count(*) files,sum(rows) rows,sum(byte_size) bytes,min(min_ts_ns) first_ns,max(max_ts_ns) last_ns,sum(local_present) local_files FROM parquet_manifest WHERE lifecycle_state='active' GROUP BY provider,event_type,trading_date")]
        conn.close()
for ep in ('/varz','/jsz?streams=true&consumers=true&config=true'):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8222'+ep,timeout=10) as response:result[ep]=json.load(response)
    except Exception as e: result[ep]={'error':type(e).__name__}
ids=run('docker','ps','-aq')
containers=json.loads(run('docker','inspect',*ids.split()))
result['containers']=[]
for c in containers:
    if not c['State']['Running']: continue
    env=dict(x.split('=',1) for x in c['Config']['Env'] if '=' in x)
    safe={k:v for k,v in env.items() if k.startswith(('CRYPTO_','NATS_','STORAGE_','SQLITE_','PARQUET_','COLLECTOR_','ARCHIVE_','MARKET_EVENT_','VIBETRADING_BUILD_','VIBETRADING_IMAGE')) and not any(t in k for t in ('KEY','SECRET','TOKEN','AUTH','PASSWORD','ADDRESS'))}
    record={'name':c['Name'],'image':c['Image'],'started':c['State']['StartedAt'],'restart_count':c['RestartCount'],'health':(c['State'].get('Health') or {}).get('Status'),'env':safe,'memory_limit':c['HostConfig']['Memory']}
    pid=c['State']['Pid']; cg=pathlib.Path('/proc/'+str(pid)+'/cgroup').read_text().strip().split('::')[-1]
    for f in ('memory.events','memory.current','cpu.stat'):
        p=pathlib.Path('/sys/fs/cgroup'+cg+'/'+f)
        if p.exists():record[f]=p.read_text()
    result['containers'].append(record)
result['stats']=run('docker','stats','--no-stream','--format','{{.Name}} {{.CPUPerc}} {{.MemUsage}}')
result['unit']=run('systemctl','cat','vibetrading.service')
path=OUT/('snapshot-'+str(result['ts_ns'])+'.json'); path.write_text(json.dumps(result,indent=2))
print(json.dumps({'path':str(path),'health':{pathlib.Path(k).stem:{x:v.get(x) for x in ('phase','reason','ready','pending','messages','acked','ts_ns')} for k,v in result['health'].items()},'stats':result['stats'],'open_gaps':[{'provider':r['provider'],'start':r['started_ns'],'reason':r['reason']} for rows in result['gaps'].values() for r in rows if r['ended_ns'] is None]},indent=2))
