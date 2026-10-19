import hashlib,json,pathlib,subprocess,time,urllib.request,sqlite3,os
out=pathlib.Path('/srv/vibetrading-capacity/incident-20261006')
report=pathlib.Path('/srv/vibetrading-data/runtime_data/reports/market-coverage/20261006')
def run(*args):return subprocess.check_output(args,text=True).strip()
result={'ts_ns':time.time_ns(),'date':run('date','-Is'),'health':{},'reports':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in report.iterdir() if p.is_file()},'mounts':{},'unit_active':run('systemctl','is-active','vibetrading.service'),'disks':run('df','-h','/','/srv/vibetrading-data','/srv/vibetrading-capacity')}
for directory in ('/srv/vibetrading-crypto-state/health','/srv/vibetrading-data/runtime_data/state/health'):
    for p in pathlib.Path(directory).glob('*.json'):
        if p.stem in ('storage','crypto-storage','maintenance','collectors-hyperliquid','collectors-kis','collectors-kiwoom') or p.stem.endswith('-perp'):
            value=json.loads(p.read_text());result['health'][str(p)]=value
for suffix in ('state','market_data','exports'):
    path='/srv/vibetrading-data/runtime_data/'+suffix
    result['mounts'][path]=run('findmnt','-n','-o','SOURCE,TARGET','--target',path)
names=['vibetrading-'+s+'-1' for s in ('crypto-storage','crypto-maintenance','storage','maintenance','collectors','crypto-collector-binance','crypto-collector-bybit','crypto-collector-bitget','crypto-collector-arcus','crypto-collector-grvt')]
containers=json.loads(run('docker','inspect',*names))
result['containers']=[{'name':c['Name'],'image':c['Image'],'status':c['State']['Status'],'health':c['State'].get('Health',{}).get('Status'),'restart_count':c['RestartCount'],'oom_killed':c['State']['OOMKilled']} for c in containers]
assert all(c['status']=='running' and c['health']=='healthy' and not c['oom_killed'] and c['restart_count']==0 for c in result['containers'])
assert result['unit_active']=='active'
for stem in ('binance-perp','bybit-perp','bitget-perp','arcus-perp','grvt-perp'):
    h=next(v for k,v in result['health'].items() if k.endswith('/collectors-'+stem+'.json'))
    assert h['phase']=='publishing' and (time.time_ns()-h['ts_ns'])/1e9<90
for stem in ('storage','crypto-storage'):
    h=next(v for k,v in result['health'].items() if k.endswith('/'+stem+'.json'))
    assert h['ready'] and (time.time_ns()-h['ts_ns'])/1e9<90
path=pathlib.Path('/srv/vibetrading-data/runtime_data/state/collection_gaps.sqlite3');previous=os.geteuid()
try:
    os.seteuid(path.stat().st_uid)
    conn=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=5);conn.row_factory=sqlite3.Row
    result['original_gaps']=[dict(r) for r in conn.execute('SELECT * FROM gaps WHERE id IN (456,460) ORDER BY id')];conn.close()
finally:os.seteuid(previous)
assert len(result['original_gaps'])==2 and all(r['ended_ns'] is None for r in result['original_gaps'])
with urllib.request.urlopen('http://127.0.0.1:8222/jsz?streams=true&consumers=true&config=true',timeout=10) as f:result['nats']=json.load(f)
(out/'completion-verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'date':result['date'],'assertions':'passed','disks':result['disks'],'reports':result['reports'],'mounts':result['mounts'],'health':{pathlib.Path(k).stem:{f:v.get(f) for f in ('phase','reason','ready','messages','acked','pending')} for k,v in result['health'].items() if not k.endswith('/maintenance.json')}},indent=2),flush=True)
