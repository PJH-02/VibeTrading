import collections,json,os,pathlib,re,sqlite3,subprocess,time,urllib.request
out=pathlib.Path('/srv/vibetrading-capacity/incident-20261007');out.mkdir(exist_ok=True)
def run(*args):
    p=subprocess.run(args,text=True,capture_output=True,timeout=45);return p.stdout+p.stderr
def sanitize(s):
    s=re.sub(r'(?i)(bearer\s+)[^\s"\']+',r'\1[REDACTED]',s)
    s=re.sub(r'(?i)((?:api[_-]?key|secret|password|access_token|token)["\']?\s*[:=]\s*["\']?)[^\s,"\'}]+',r'\1[REDACTED]',s)
    return s
names=['vibetrading-'+s+'-1' for s in ('collectors','crypto-collector-binance','crypto-collector-bybit','crypto-collector-bitget','crypto-collector-arcus','crypto-collector-grvt','crypto-storage','crypto-maintenance','storage','maintenance','nats')]
result={'ts_ns':time.time_ns(),'date':run('date','-Is'),'uptime':run('uptime'),'memory':run('free','-m'),'disks':run('df','-h','/','/srv/vibetrading-data','/srv/vibetrading-capacity'),'health':{},'gaps':{},'containers':[],'logs':{},'pressure':{k:pathlib.Path('/proc/pressure/'+k).read_text() for k in ('cpu','io','memory')}}
for name in names:
    log=sanitize(run('docker','logs','--since','2026-10-05T15:00:00Z','--tail','25000','--timestamps',name))
    (out/(name+'.log')).write_text(log)
    lines=log.splitlines();picked=[i for i,l in enumerate(lines) if any(w in l.lower() for w in ('error','failed','disconnect','capacity','pause','defer','timeout','memory','killed'))]
    excerpts=[]
    for i in picked[-20:]:excerpts.extend(lines[max(0,i-1):min(len(lines),i+7)])
    result['logs'][name]={'lines':len(lines),'matching':len(picked),'tail_excerpts':excerpts[-140:]}
for directory in ('/srv/vibetrading-data/runtime_data/state','/srv/vibetrading-crypto-state'):
    root=pathlib.Path(directory)
    for p in (root/'health').glob('*.json'):
        if p.stem.startswith('collectors') or p.stem in ('storage','crypto-storage','maintenance'):result['health'][str(p)]=json.loads(p.read_text())
    path=root/'collection_gaps.sqlite3';uid=os.geteuid()
    if not path.exists():continue
    try:
        os.seteuid(path.stat().st_uid);conn=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=5);conn.row_factory=sqlite3.Row
        cutoff=1791126000000000000
        result['gaps'][str(path)]=[dict(r) for r in conn.execute('SELECT * FROM gaps WHERE started_ns>=? OR ended_ns IS NULL ORDER BY started_ns',(cutoff,))];conn.close()
    finally:os.seteuid(uid)
for c in json.loads(run('docker','inspect',*names)):
    pid=c['State']['Pid'];env=dict(x.split('=',1) for x in c['Config']['Env'] if '=' in x)
    safe={k:v for k,v in env.items() if k.startswith(('VIBETRADING_BUILD_','VIBETRADING_IMAGE','NATS_','CRYPTO_','ARCHIVE_','SQLITE_','STORAGE_','COLLECTOR_','TOSS_')) and not any(t in k for t in ('KEY','SECRET','TOKEN','PASSWORD','AUTH','ACCOUNT'))}
    r={'name':c['Name'],'image':c['Image'],'state':{k:c['State'][k] for k in ('Status','StartedAt','FinishedAt','OOMKilled','Pid')},'restarts':c['RestartCount'],'health':c['State'].get('Health'),'env':safe,'mounts':[{k:m[k] for k in ('Source','Destination','RW')} for m in c['Mounts']]}
    cg=pathlib.Path('/proc/'+str(pid)+'/cgroup').read_text().strip().split('::')[-1]
    for f in ('memory.events','memory.current','cpu.stat'):
        p=pathlib.Path('/sys/fs/cgroup'+cg+'/'+f)
        if p.exists():r[f]=p.read_text()
    result['containers'].append(r)
for ep in ('/varz','/jsz?streams=true&consumers=true&config=true'):
    with urllib.request.urlopen('http://127.0.0.1:8222'+ep,timeout=10) as f:result[ep]=json.load(f)
result['kernel']=sanitize(run('journalctl','-k','--since','2026-10-06 00:00:00','--no-pager','-n','120'))
result['stats']=run('docker','stats','--no-stream','--format','{{.Name}} {{.CPUPerc}} {{.MemUsage}}')
(out/'diagnosis.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'path':str(out),'date':result['date'],'stats':result['stats'],'log_matches':{k:v['matching'] for k,v in result['logs'].items()},'gaps':result['gaps']},indent=2))
