"""Read-only metadata, wallet and native private-feed readiness check."""
import asyncio
import json
import sys
from pathlib import Path
import aiohttp

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from vibetrading.fv_mm.venues import Bybit,Grvt
from vibetrading.fv_mm.journal import Journal
from vibetrading.fv_mm.private import bybit_private,grvt_private

async def main():
    journal=Journal(ROOT/"runtime_data/fv_connection_check")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
        result={}; tasks=[]
        try:
            for cls,fn in ((Bybit,bybit_private),(Grvt,grvt_private)):
                client=cls(session); await client.connect()
                ready=asyncio.Event()
                tasks.append(asyncio.create_task(fn(client,lambda *x:None,lambda *x:None,ready,journal)))
                await asyncio.wait_for(ready.wait(),30)
                result[client.name]={"authenticated":True,"private_ready":True,"wallet":await client.account(),"positions":await client.positions(),"open_orders":len(await client.open_orders()),"instruments":{a:{"tick":i.tick,"lot":i.lot,"min":i.minimum,"min_notional":i.min_notional,"fee_budget":client.fees[a]} for a,i in client.instruments.items()}}
            journal.save("readiness.json",result)
            print(json.dumps(result,default=str,indent=2))
        finally:
            for t in tasks:t.cancel()
            await asyncio.gather(*tasks,return_exceptions=True); journal.close()

if __name__=="__main__":asyncio.run(main())
