import datetime,json,os,pathlib,sqlite3,subprocess,time,urllib.request
out=pathlib.Path('/srv/vibetrading-capacity/incident-20261007');rows=[]
names=['vibetrading-'+s+'-1' for s in ('collectors','crypto-collector-binance','crypto-collector-bybit','crypto-collector-bitget','crypto-collector-arcus','crypto-collector-grvt','maintenance','storage','crypto-storage')]
def run(*args):return subprocess.check_output(args,text=True).strip()
for i in range(13):
    row={'ts_ns':time.time_ns(),'health':{},'containers':[]}
    for directory in ('/srv/vibetrading-data/runtime_data/state/health','/srv/vibetrading-crypto-state/health'):
        for p in pathlib.Path(directory).glob('*.json'):
            if p.stem in ('storage','crypto-storage','maintenance') or p.stem.startswith('collectors-'):
                row['health'][str(p)]=json.loads(p.read_text())
    for c in json.loads(run('docker','inspect',*names)):
        r={'name':c['Name'],'image':c['Image'],'health':c['State'].get('Health',{}).get('Status'),'restarts':c['RestartCount'],'started':c['State']['StartedAt'],'oom_killed':c['State']['OOMKilled']}
        pid=c['State']['Pid'];cg=pathlib.Path('/proc/'+str(pid)+'/cgroup').read_text().strip().split('::')[-1]
        for field in ('memory.current','memory.events','cpu.stat'):
            r[field]=pathlib.Path('/sys/fs/cgroup'+cg+'/'+field).read_text()
        row['containers'].append(r)
    with urllib.request.urlopen('http://127.0.0.1:8222/jsz?streams=true&consumers=true&config=true',timeout=10) as f:row['nats']=json.load(f)
    row['pressure']={k:pathlib.Path('/proc/pressure/'+k).read_text() for k in ('cpu','io','memory')}
    rows.append(row);(out/'validation.json').write_text(json.dumps(rows,indent=2))
    print(json.dumps({'sample':i,'phase':{pathlib.Path(k).stem:v.get('phase') for k,v in row['health'].items() if k.endswith('/collectors-toss.json') or k.endswith('-perp.json')},'memory_mib':{c['name']:round(int(c['memory.current'])/2**20,1) for c in row['containers']},'restarts':{c['name']:c['restarts'] for c in row['containers']}}),flush=True)
    if i<12:time.sleep(30)
path=pathlib.Path('/srv/vibetrading-data/runtime_data/state/collection_gaps.sqlite3');uid=os.geteuid()
try:
    os.seteuid(path.stat().st_uid);conn=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=5);conn.row_factory=sqlite3.Row
    gaps=[dict(r) for r in conn.execute('SELECT * FROM gaps WHERE started_ns>=? ORDER BY started_ns',(rows[0]['ts_ns'],))]
    toss=[dict(r) for r in conn.execute("SELECT * FROM gaps WHERE provider='toss' ORDER BY started_ns DESC LIMIT 2")];conn.close()
finally:os.seteuid(uid)
summary={'completed_ts_ns':time.time_ns(),'duration_seconds':round((rows[-1]['ts_ns']-rows[0]['ts_ns'])/1e9,2),'new_gaps':gaps,'toss_gaps':toss,'samples':len(rows)}
(out/'validation-summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
