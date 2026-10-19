import json, pathlib, subprocess, time, urllib.request, sqlite3

def run(*args): return subprocess.check_output(args,text=True).strip()
def disks():
    out={}
    for line in pathlib.Path('/proc/diskstats').read_text().splitlines():
        p=line.split()
        if p[2] in ('vda','vdb','vdc'):
            out[p[2]]={'read_ios':int(p[3]),'read_sectors':int(p[5]),'read_ms':int(p[6]),
                'write_ios':int(p[7]),'write_sectors':int(p[9]),'write_ms':int(p[10]),'busy_ms':int(p[12])}
    return out
def snapshot():
    result={'time_ns':time.time_ns(),'diskstats':disks(),'health':{}}
    for root in ('/srv/vibetrading-data/runtime_data/state/health','/srv/vibetrading-crypto-state/health'):
        for p in pathlib.Path(root).glob('*.json'):
            if p.stem in ('collectors','collectors-hyperliquid','collectors-binance','crypto-storage','maintenance') or p.stem.startswith('collectors-') and p.stem.endswith('-perp'):
                try: result['health'][p.stem]=json.loads(p.read_text())
                except (OSError,ValueError): pass
    for endpoint in ('/varz','/jsz?streams=true&consumers=true&config=true'):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8222'+endpoint,timeout=5) as response:
                result[endpoint]=json.load(response)
        except Exception as e: result[endpoint]=type(e).__name__
    result['memory']=run('free','-m')
    result['pressure']={k:pathlib.Path('/proc/pressure/'+k).read_text() for k in ('cpu','io','memory')}
    result['containers']=[]
    ids=run('docker','ps','-q').split()
    for c in json.loads(run('docker','inspect',*ids)):
        pid=c['State']['Pid']; cgroup=pathlib.Path('/proc/'+str(pid)+'/cgroup').read_text().strip().split('::')[-1]
        memory_event=pathlib.Path('/sys/fs/cgroup'+cgroup+'/memory.events')
        result['containers'].append({'name':c['Name'],'image':c['Image'],'restart_count':c['RestartCount'],
            'state':{k:c['State'].get(k) for k in ('Status','OOMKilled','Health')},
            'memory_events':memory_event.read_text() if memory_event.exists() else None})
    return result

first=snapshot(); time.sleep(40); second=snapshot()
duration=(second['time_ns']-first['time_ns'])/1e9
derived={}
for name,after in second['diskstats'].items():
    delta={k:v-first['diskstats'][name][k] for k,v in after.items()}
    ios=delta['read_ios']+delta['write_ios']
    derived[name]={'iops':round(ios/duration,1),'busy_percent':round(delta['busy_ms']/duration/10,1),
        'read_mib_per_second':round(delta['read_sectors']*512/duration/2**20,3),
        'write_mib_per_second':round(delta['write_sectors']*512/duration/2**20,3),
        'average_io_ms':round((delta['read_ms']+delta['write_ms'])/max(1,ios),2)}
rates={}
for name,h in second['health'].items():
    before=first['health'].get(name,{})
    if h.get('published_count') is not None and before.get('published_count') is not None:
        elapsed=(h['ts_ns']-before['ts_ns'])/1e9
        if elapsed>0:
            rates[name]={'events_per_second':round((h['published_count']-before['published_count'])/elapsed,2),
                'bytes_per_second':round((h['published_bytes']-before['published_bytes'])/elapsed)}
output={'first':first,'second':second,'disk_rates':derived,'publish_rates':rates}
path=pathlib.Path('/opt/vibetrading/releases/exchange-expansion-20261003/observation-'+str(second['time_ns'])+'.json')
path.write_text(json.dumps(output,indent=2))
print(json.dumps({'path':str(path),'disk_rates':derived,'publish_rates':rates,
    'health':{k:{f:v.get(f) for f in ('phase','ready','pending','messages','acked','published_count','reason')} for k,v in second['health'].items()},
    'memory':second['memory'],'containers':[{k:c[k] for k in ('name','restart_count','memory_events')} for c in second['containers']]},indent=2))
