"""Bounded local evidence sampler: read-only SSH OS/NATS counters, no app calls."""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REMOTE = r'''
import datetime,json,os,pathlib,time,urllib.request
def read(path):
    try: return pathlib.Path(path).read_text()[:65536]
    except OSError as error: return {'error':type(error).__name__}
out={'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'boot_id':read('/proc/sys/kernel/random/boot_id'),'uptime':read('/proc/uptime'),'cpu_count':os.cpu_count(),'meminfo':read('/proc/meminfo'),'cpu_stat':read('/proc/stat'),'diskstats':read('/proc/diskstats'),'pressure':{kind:read('/proc/pressure/'+kind) for kind in ('cpu','memory','io')}}
vm=read('/proc/vmstat');out['swap_counters']={line.split()[0]:int(line.split()[1]) for line in vm.splitlines() if line.startswith(('pswpin ','pswpout '))} if isinstance(vm,str) else vm
out['filesystems']={}
for name in ('/','/srv/vibetrading-data','/srv/vibetrading-capacity'):
    try:
        stat=os.statvfs(name);out['filesystems'][name]={'device':os.stat(name).st_dev,'total_bytes':stat.f_blocks*stat.f_frsize,'available_bytes':stat.f_bavail*stat.f_frsize}
    except OSError as error:out['filesystems'][name]={'error':type(error).__name__}
for key,endpoint in (('nats','varz'),('jetstream','jsz?accounts=true&streams=true&config=true')):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8222/'+endpoint,timeout=3) as response: data=json.load(response)
        if key=='nats':out[key]={k:data.get(k) for k in ('server_id','start','now','mem','cpu','in_msgs','out_msgs','in_bytes','out_bytes','slow_consumers')}
        else:out[key]=[{'name':stream.get('name'),'created':stream.get('created'),'state':stream.get('state'),'max_bytes':stream.get('config',{}).get('max_bytes')} for account in data.get('account_details',[]) for stream in account.get('stream_detail',[])]
    except Exception as error:out[key]={'error':type(error).__name__}
print(json.dumps(out,separators=(',',':')))
'''


def collect(host: str):
    result = subprocess.run(["ssh","-T","-o","BatchMode=yes","-o","ConnectTimeout=8",
        "-o","ServerAliveInterval=5","-o","ServerAliveCountMax=1",host,"python3","-"],
        input=REMOTE,text=True,encoding="utf-8",capture_output=True,timeout=25)
    if result.returncode:
        raise RuntimeError("read-only SSH sample failed exit="+str(result.returncode))
    return json.loads(result.stdout)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--host",required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--hours",type=float,default=72)
    parser.add_argument("--interval-seconds",type=int,default=60)
    parser.add_argument("--once",action="store_true")
    args=parser.parse_args()
    if not 0 < args.hours <= 168 or not 30 <= args.interval_seconds <= 3600:
        raise ValueError("measurement bounds exceeded")
    root=Path(__file__).resolve().parents[2]
    output=args.output.resolve()
    if not output.is_relative_to(root/"docs/refactoring/runs"):
        raise ValueError("measurement evidence must stay inside the run directory")
    output.mkdir(parents=True,exist_ok=True)
    lock=(output/"sampler.lock").open("a+b")
    try:
        if os.name=="nt":
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        started=datetime.datetime.now(datetime.timezone.utc)
        state={'pid':os.getpid(),'started_utc':started.isoformat(),'duration_hours':args.hours,
            'interval_seconds':args.interval_seconds,'state':'RUNNING','samples':0,'errors':0,
            'scope':'OS/NATS counters only. NATS ingress bytes are not provider raw bytes; filesystem used deltas are not daily Parquet production; no app entrypoint/DB/order/message/service action.'}
        deadline=time.monotonic()+args.hours*3600
        with (output/"samples.jsonl").open("a",encoding="utf-8") as handle:
            while time.monotonic()<deadline and not (output/"STOP").exists():
                tick=time.monotonic()
                try:
                    sample=collect(args.host);state['samples']+=1
                except Exception as error:
                    sample={'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'error':type(error).__name__};state['errors']+=1
                handle.write(json.dumps(sample,ensure_ascii=False,separators=(',',':'))+'\n')
                handle.flush();os.fsync(handle.fileno())
                state['last_sample_utc']=sample['observed_utc']
                temporary=output/'state.tmp';temporary.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temporary.replace(output/'state.json')
                if args.once:
                    break
                remaining=max(0,min(deadline-time.monotonic(),args.interval_seconds-(time.monotonic()-tick)))
                time.sleep(remaining)
        state['state']='COMPLETE' if time.monotonic()>=deadline else 'ONE_SAMPLE_COMPLETE' if args.once else 'STOPPED'
        state['completed_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        (output/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    finally:
        lock.close()


if __name__=='__main__':
    main()
