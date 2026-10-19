"""Read-only SSH metrics with existing user SSH configuration. No environment dump."""
import argparse,json,subprocess
from pathlib import Path

REMOTE = r'''
import json,os,pathlib,subprocess,shutil,urllib.request,time
def sample():
    memory={}
    for line in pathlib.Path('/proc/meminfo').read_text().splitlines():
        k,v=line.split(':',1)
        if k in ('MemTotal','MemAvailable','SwapTotal','SwapFree'):memory[k]=int(v.split()[0])*1024
    disks={p:dict(zip(('total','used','free'),shutil.disk_usage(p))) for p in ('/','/srv/vibetrading-capacity','/srv/vibetrading-data') if pathlib.Path(p).exists()}
    stats=subprocess.check_output(['docker','stats','--no-stream','--format','{{json .}}'],text=True,timeout=25)
    pressure={k:pathlib.Path('/proc/pressure/'+k).read_text() for k in ('cpu','memory','io')}
    nats={}
    try:
        with urllib.request.urlopen('http://127.0.0.1:8222/jsz?accounts=true&streams=true&consumers=true',timeout=5) as f:j=json.load(f)
        for account in j.get('account_details',[]):
            for stream in account.get('stream_detail',[]):
                nats[stream['name']]={'state':stream['state'],'consumers':[{k:c.get(k) for k in ('name','num_pending','num_ack_pending','num_redelivered')} for c in stream.get('consumer_detail',[])]}
    except Exception as e:nats={'error':type(e).__name__}
    return dict(time_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),cpus=os.cpu_count(),load=os.getloadavg(),memory=memory,disks=disks,pressure=pressure,containers=[json.loads(x) for x in stats.splitlines()],nats=nats)
print(json.dumps(sample()))
'''

def main():
    p=argparse.ArgumentParser();p.add_argument('--host',default='root@49.50.141.106');p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=12',args.host,'python3 -'],input=REMOTE,text=True,capture_output=True,timeout=45)
    if r.returncode:raise RuntimeError('SSH metrics failed: '+r.stderr[-300:])
    result=json.loads(r.stdout);args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('time_utc','cpus','load','memory')}))
if __name__=='__main__':main()
