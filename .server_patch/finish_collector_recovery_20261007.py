import hashlib,json,pathlib,subprocess,time,urllib.request
R=pathlib.Path('/opt/vibetrading/releases/collector-recovery-20261007');out=pathlib.Path('/srv/vibetrading-capacity/incident-20261007')
def run(*args):return subprocess.check_output(args,text=True).strip()
names=['vibetrading-'+s+'-1' for s in ('collectors','crypto-collector-binance','crypto-collector-bybit','crypto-collector-bitget','crypto-collector-arcus','crypto-collector-grvt','maintenance','storage','crypto-storage','nats')]
m=json.loads((R/'build.json').read_text());changed={r['service'] for r in json.loads((R/'activation.json').read_text())['changes']}
result={'ts_ns':time.time_ns(),'date':run('date','-Is'),'containers':[],'logs':{}}
for c in json.loads(run('docker','inspect',*names)):
    name=c['Name'];pid=c['State']['Pid'];cg=pathlib.Path('/proc/'+str(pid)+'/cgroup').read_text().strip().split('::')[-1];base=pathlib.Path('/sys/fs/cgroup'+cg)
    memory=dict(line.split() for line in (base/'memory.stat').read_text().splitlines())
    r={'name':name,'image':c['Image'],'health':c['State'].get('Health',{}).get('Status'),'restarts':c['RestartCount'],'started':c['State']['StartedAt'],'oom_killed':c['State']['OOMKilled'],'memory.events':(base/'memory.events').read_text(),'memory_mib':{k:round(int(memory[k])/2**20,1) for k in ('anon','file','slab')}}
    assert r['health']=='healthy' and not r['oom_killed'] and r['restarts']==0
    if name.removeprefix('/vibetrading-').removesuffix('-1') in changed:assert r['image']==m['image']
    result['containers'].append(r)
    p=subprocess.run(['docker','logs','--since',c['State']['StartedAt'],'--tail','30',name],text=True,capture_output=True,timeout=10)
    # Log lines only from new services; skip older incidental histories.
    if name.removeprefix('/vibetrading-').removesuffix('-1') in changed:
        result['logs'][name]=p.stdout+p.stderr
        assert 'ERROR' not in result['logs'][name]
result['kernel_oom_since_deployment']=run('journalctl','-k','--since','2026-10-07 03:09:14','--no-pager')
assert 'oom-kill:' not in result['kernel_oom_since_deployment']
with urllib.request.urlopen('http://127.0.0.1:8222/jsz?streams=true&consumers=true&config=true',timeout=10) as f:result['nats']=json.load(f)
result['memory']=run('free','-m');result['disks']=run('df','-h','/','/srv/vibetrading-data','/srv/vibetrading-capacity');result['unit_active']=run('systemctl','is-active','vibetrading.service')
(out/'completion.json').write_text(json.dumps(result,indent=2));print(json.dumps({'date':result['date'],'assertions':'passed','memory':result['memory'],'container_memory':{c['name']:c['memory_mib'] for c in result['containers']}}),flush=True)
