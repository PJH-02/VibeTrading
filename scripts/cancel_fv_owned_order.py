"""Operator diagnostic for an explicitly identified experiment-owned testnet order."""
import argparse
import asyncio
import sys
from pathlib import Path
import aiohttp
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from vibetrading.fv_mm.venues import Grvt

async def main(args):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
        c=Grvt(session,trading=True);await c.login()
        try:
            await c.rpc("cancel_order",{"client_order_id":args.client_id,"time_to_live_ms":"5000"},mutation=True)
            print("TESTNET cancel request acknowledged")
        except RuntimeError as exc:print(str(exc))
        state=await c.order(args.asset,args.client_id)
        print("Terminal:",state["terminal"] if state else None,"Status:",state["status"] if state else None)

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("client_id");p.add_argument("--asset",choices=("BTC","ETH","SOL","XRP"),required=True)
    asyncio.run(main(p.parse_args()))
