"""Bounded read-only pilot watcher; prints compact progress every 45 seconds."""
import argparse
import json
from pathlib import Path
import time

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);a=p.parse_args();deadline=time.monotonic()+2100
    while time.monotonic()<deadline:
        done=a.directory/'complete.json';status=a.directory/'status.json'
        if status.exists():
            r=json.loads(status.read_text());counts=r.get('counts',{})
            compact={'phase':r['phase'],'elapsed_seconds':r['elapsed_seconds'],'paper_open_orders':r.get('paper_open_orders'),'inventory_nonzero':{k:v for k,v in r.get('inventory',{}).items() if float(v)!=0},'scenarios':{s:{kind:sum(n for k,n in values.items() if k.endswith(':'+kind)) for kind in ('intents','active','fills','post_only_rejected')} for s,values in counts.items()},'external_order_submissions':r.get('external_order_submissions')}
            if done.exists():compact.update(complete=r['complete'],requested_duration_met=r['requested_duration_met'])
            print(json.dumps(compact),flush=True)
        if done.exists():break
        time.sleep(45)
