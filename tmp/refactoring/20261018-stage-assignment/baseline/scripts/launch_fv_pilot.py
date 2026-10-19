"""Detach a bounded public-only collector or pilot on the experiment server."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("mode",choices=("collect","pilot"));p.add_argument("--output",type=Path,required=True);p.add_argument("--duration",type=int,default=1800);p.add_argument("--models",type=Path);p.add_argument("--latency",type=Path)
    args=p.parse_args();root=Path(__file__).resolve().parents[1];output=args.output.resolve()
    if args.mode=="pilot" and (not args.models or not args.latency or not args.models.is_file() or not args.latency.is_file()):p.error("Pilot requires existing models and latency files")
    if output.exists():p.error("Output already exists")
    output.mkdir(parents=True)
    script="collect_fv_mainnet.py" if args.mode=="collect" else "run_fv_pilot.py"
    command=[sys.executable,str(root/"scripts"/script),"--output",str(output),"--duration",str(args.duration)]
    if args.mode=="pilot":
        if not args.models or not args.latency:p.error("Pilot requires --models and --latency")
        command.extend(["--models",str(args.models.resolve()),"--latency",str(args.latency.resolve())])
    with (output/"process.log").open("wb") as log:
        proc=subprocess.Popen(command,cwd=root,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    (output/"pid.json").write_text(json.dumps({"pid":proc.pid,"command":command}),encoding="utf-8")
    print(json.dumps({"pid":proc.pid,"output":str(output),"mode":args.mode}))
