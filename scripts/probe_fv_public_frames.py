"""Read-only native frame shape probe; no account access."""
import asyncio
import json
from pathlib import Path
import sys
import time
import websockets


async def grvt():
    counts={};samples=[];deadline=time.monotonic()+30
    async with websockets.connect('wss://market-data.grvt.io/ws/full',ping_interval=15) as ws:
        await ws.send(json.dumps({'jsonrpc':'2.0','method':'subscribe','params':{'stream':'v1.trade','selectors':[a+'_USDT_Perp@50' for a in ('BTC','ETH','SOL','XRP')]},'id':1}))
        while time.monotonic()<deadline:
            try:raw=await asyncio.wait_for(ws.recv(),max(.01,deadline-time.monotonic()))
            except asyncio.TimeoutError:break
            r=json.loads(raw);f=r.get('feed')
            key=str(r.get('stream'))+':'+type(f).__name__;counts[key]=counts.get(key,0)+1
            if len(samples)<3:samples.append(r)
            if isinstance(f,list):samples=[r];break
    return {'counts':counts,'samples':samples}


async def bitget():
    result=[]
    async with websockets.connect('wss://ws.bitget.com/v3/ws/public',ping_interval=15) as ws:
        await ws.send(json.dumps({'op':'subscribe','args':[{'instType':'usdt-futures','topic':'books50','symbol':'BTCUSDT'}]}))
        while len(result)<5:
            r=json.loads(await asyncio.wait_for(ws.recv(),15));d=r.get('data')
            if d:result.append({'action':r.get('action'),'keys':list(d[0]),'seq':d[0].get('seq'),'pseq':d[0].get('pseq')})
    return result


async def main():
    g,b=await asyncio.gather(grvt(),bitget());print(json.dumps({'grvt':g,'bitget':b},indent=2),flush=True)

if __name__=='__main__':asyncio.run(main())
