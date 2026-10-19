"""Bounded read-only monitor; exits when completion or attention appears."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import time

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("directory",type=Path);p.add_argument("--timeout",type=int,default=2400)
    args=p.parse_args();start=time.monotonic()
    while time.monotonic()-start<args.timeout:
        for name in ("complete.json","attention.json","status.json"):
            path=args.directory/name
            if path.exists():
                result=json.loads(path.read_text())
                result["observed_utc"]=datetime.now(timezone.utc).isoformat(timespec="seconds")
                result["snapshot_type"]=name
                print(json.dumps(result),flush=True)
                if name!="status.json":raise SystemExit(0)
                break
        time.sleep(45)
    raise SystemExit("Monitor time limit reached")
