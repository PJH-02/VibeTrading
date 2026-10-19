"""Bounded 16-policy public-mainnet pilot. Does not load .env or trading clients."""
import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vibetrading.fv_pilot.runner import PilotRunner

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--models",type=Path,required=True);p.add_argument("--latency",type=Path,required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--duration",type=int,default=1800)
    p.add_argument("--legacy-fv",action="store_true",help="Historical reproduction only")
    p.add_argument("--emergency-taker",action="store_true",help="Explicit optional risk liquidation; default maker-only")
    args=p.parse_args()
    if not 1<=args.duration<=3600:p.error("Duration must be 1..3600 seconds")
    asyncio.run(PilotRunner(args).run())
