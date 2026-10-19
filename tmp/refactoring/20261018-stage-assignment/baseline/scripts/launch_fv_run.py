"""Launch the operator-requested bounded run as a separate server process."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--models",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--duration",type=int,default=1800)
    p.add_argument("--trade-testnet",action="store_true")
    args=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    output=args.output.resolve()
    if output.exists():p.error("Output directory already exists")
    output.mkdir(parents=True)
    command=[sys.executable,str(root/"scripts/run_fv_testnets.py"),"--models",str(args.models.resolve()),"--output",str(output),"--duration",str(args.duration)]
    if args.trade_testnet:command.append("--trade-testnet")
    with (output/"process.log").open("wb") as log:
        proc=subprocess.Popen(command,cwd=root,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
    (output/"pid.json").write_text(json.dumps({"pid":proc.pid,"command":command}),encoding="utf-8")
    print("Started bounded experiment PID",proc.pid,"output",output)
