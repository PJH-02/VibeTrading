"""Read-only capacity sample; no benchmark or production configuration change."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

observer=Path('/opt/vibetrading/releases/crypto-relief-final-20261002/crypto_relief_observe.py')
if not observer.exists():observer=Path('/tmp/crypto_relief_observe.py')
namespace={'__name__':'capacity_observer'}
exec(compile(observer.read_text().split('label = sys.argv[1]')[0],str(observer),'exec'),namespace)
snapshot=namespace['snapshot']
samples=[]
for i in range(3):
    if i:time.sleep(30)
    s=snapshot()
    s['proc_cpu']=list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:]))
    s['loadavg']=Path('/proc/loadavg').read_text().strip()
    s['net']={parts[0].strip(':'):list(map(int,parts[1:])) for line in Path('/proc/net/dev').read_text().splitlines()[2:] if (parts:=line.replace(':',': ').split())}
    for c in s['containers'].values():
        if c['pid']:
            rel=next(line.split(':',2)[2] for line in Path('/proc/%s/cgroup'%c['pid']).read_text().splitlines() if line.startswith('0:'))
            group=Path('/sys/fs/cgroup')/rel.lstrip('/')
            c['cpu_stat']={k:int(v) for k,v in (line.split() for line in (group/'cpu.stat').read_text().splitlines())}
            c['memory_limit']=(group/'memory.max').read_text().strip()
    samples.append(s)
    print(json.dumps({'sample':i+1,'utc':s['utc'],'root_free':s['root_disk_free'],'data_free':s['data_disk_free'],'loadavg':s['loadavg']}),flush=True)

db=sqlite3.connect('file:/srv/vibetrading-crypto-state/vibetrading.sqlite3?mode=ro',uri=True,timeout=3)
db.execute('PRAGMA query_only=ON')
now=time.time_ns();groups={}
for hours in (1,6,24):
    groups[str(hours)]=db.execute('SELECT provider,event_type,COUNT(*),SUM(rows),SUM(byte_size),MIN(created_ts_ns),MAX(created_ts_ns) FROM parquet_manifest WHERE created_ts_ns>=? GROUP BY 1,2',(now-int(hours*3600e9),)).fetchall()
recent=db.execute('SELECT provider,event_type,COUNT(*),SUM(rows),SUM(byte_size),MIN(created_ts_ns),MAX(created_ts_ns) FROM parquet_manifest WHERE created_ts_ns>=? GROUP BY 1,2',(now-int(600e9),)).fetchall()
db.close()
system={'cpu_count':os.cpu_count(),'meminfo':Path('/proc/meminfo').read_text(),
    'lsblk':json.loads(subprocess.check_output(['lsblk','-J','-b','-o','NAME,SIZE,TYPE,ROTA,FSTYPE,MOUNTPOINTS,MODEL'])),
    'mounts':subprocess.check_output(['findmnt','-rn','-o','SOURCE,TARGET,FSTYPE,OPTIONS'],text=True),
    'df':subprocess.check_output(['df','-B1','/','/srv/vibetrading-data'],text=True),
    'devices':{dev:{p:(Path('/sys/block')/dev/'queue'/p).read_text().strip() for p in ('rotational','scheduler','nr_requests','logical_block_size')} for dev in ('vda','vdb')},
    'uname':subprocess.check_output(['uname','-r'],text=True).strip()}
result={'samples':samples,'manifest_hours':groups,'manifest_last_10min':recent,'system':system}
path=Path('/opt/vibetrading/releases/binance-bridge-fix-20261003/capacity-20261003.json')
path.write_text(json.dumps(result,indent=2))
print(json.dumps({'output':str(path),'manifest_last_10min':recent,'manifest_hours':groups,'cpu_count':system['cpu_count'],'devices':system['devices']}),flush=True)
