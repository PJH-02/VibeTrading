from __future__ import annotations
import argparse
import asyncio
from pathlib import Path
import sys
import signal

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from vibetrading.fv_mm.runner import Runner

if __name__=="__main__":
    p=argparse.ArgumentParser(description="S1-S4 simultaneously on fixed Bybit and GRVT TESTNET endpoints")
    p.add_argument("--models",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--config",type=Path,default=ROOT/"config/fv_mm_testnets.json")
    p.add_argument("--duration",type=int,default=1800)
    p.add_argument("--trade-testnet",action="store_true",help="Submit explicitly authorized TESTNET orders")
    p.add_argument("--legacy-fv",action="store_true",help="Historical FV reproduction only")
    p.add_argument("--strategy",choices=("S1","S2","S3","S4"),help="Omit to operate all four")
    p.add_argument("--venue",choices=("all","bybit","grvt"),default="all")
    args=p.parse_args()
    if args.duration<=0 or args.duration>3600: p.error("duration must be 1..3600 seconds")
    async def main():
        runner=Runner(args)
        # Signals request orderly maker/IOC cleanup, never cancel the event loop.
        for s in (signal.SIGINT,signal.SIGTERM):
            signal.signal(s,lambda *_: runner.requested_stop.set())
        await runner.run()
    asyncio.run(main())
