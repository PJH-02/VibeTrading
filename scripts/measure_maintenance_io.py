"""Short read-only host measurement; no SQLite scans or secret/environment output."""
from __future__ import annotations
import argparse
import datetime
import json
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request


def snapshot():
    names=['nats','storage','crypto-storage','maintenance','crypto-maintenance',
           'crypto-collector-binance','crypto-collector-bitget','crypto-collector-bybit']
    containers=json.loads(subprocess.check_output(['docker','inspect']+['vibetrading-'+n+'-1' for n in names]))
    io={};states={}
    for c in containers:
        name=c['Name'].removeprefix('/vibetrading-').removesuffix('-1')
        pid=c['State']['Pid']
        states[name]={'pid':pid,'status':c['State']['Status'],'health':c['State'].get('Health',{}).get('Status'),'restarts':c['RestartCount'],'image_id':c['Image']}
        if pid:
            rel=next(line.split(':',2)[2] for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if line.startswith('0:'))
            for line in (Path('/sys/fs/cgroup')/rel.lstrip('/')/'io.stat').read_text().splitlines():
                if line.startswith('253:16 '):io[name]={k:int(v) for k,v in (item.split('=') for item in line.split()[1:])}
    root=Path('/srv/vibetrading-data/runtime_data');health={}
    for d in ('state','crypto_state'):
        for p in (root/d/'health').glob('*.json'):
            if p.stem not in ['storage','crypto-storage','maintenance','collectors','collectors-binance-perp','collectors-bitget-perp','collectors-bybit-perp']:continue
            payload=json.loads(p.read_text())
            health[d+'/'+p.stem]={k:v for k,v in payload.items() if k not in ('build','config')}
    try:
        with urllib.request.urlopen('http://127.0.0.1:8222/jsz?streams=true&consumers=true',timeout=5) as f:data=json.load(f)
        streams=[{'name':s['name'],'state':s.get('state'), 'consumers':s.get('consumer_detail')} for account in data.get('account_details',[]) for s in account.get('stream_detail',[])]
    except Exception as exc:streams={'error':type(exc).__name__}
    disk=next(line.split() for line in Path('/proc/diskstats').read_text().splitlines() if line.split()[2]=='vdb')
    return {'time':datetime.datetime.now().isoformat(),'monotonic':time.monotonic(),'io':io,'states':states,'health':health,'streams':streams,
            'diskstats':[int(v) for v in disk[3:]],'io_pressure':Path('/proc/pressure/io').read_text(),
            'disk_free_bytes':shutil.disk_usage(root).free}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=int,default=10);parser.add_argument('--output');args=parser.parse_args()
    a=snapshot();time.sleep(max(1,min(30,args.seconds)));b=snapshot();dt=b['monotonic']-a['monotonic']
    b['interval_seconds']=round(dt,2)
    b['io_per_second']={name:{k:round((values[k]-a['io'][name].get(k,0))/dt,2) for k in values} for name,values in b['io'].items() if name in a['io'] and b['states'][name]['pid']==a['states'][name]['pid']}
    x,y=a['diskstats'],b['diskstats'];reads=y[0]-x[0];writes=y[4]-x[4]
    b['disk_rates']={'read_iops':round(reads/dt,2),'write_iops':round(writes/dt,2),'util_percent':round((y[9]-x[9])/dt/10,2),'read_await_ms':round((y[3]-x[3])/max(reads,1),2),'write_await_ms':round((y[7]-x[7])/max(writes,1),2)}
    payload=json.dumps(b,indent=2)
    if args.output:Path(args.output).write_text(payload,encoding='utf-8')
    print(payload)


if __name__=='__main__':main()
