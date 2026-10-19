"""Public-only simultaneous selector diagnostic; never loads .env/orders."""
import asyncio,json,time
from pathlib import Path
import websockets
async def probe(suffix):
 start=time.time_ns();end=time.monotonic()+120
 result={'selector_suffix':suffix,'start_ns':start,'ack':None,'errors':[],'historical':0,'current':0,'trade_ids':[]}
 async with websockets.connect('wss://market-data.grvt.io/ws/full',max_queue=8,ping_interval=20,ping_timeout=20) as ws:
  await ws.send(json.dumps({'jsonrpc':'2.0','method':'subscribe','params':{'stream':'v1.trade','selectors':[a+'_USDT_Perp'+suffix for a in ('BTC','ETH','SOL')]},'id':1}))
  while time.monotonic()<end:
   try:m=json.loads(await asyncio.wait_for(ws.recv(),timeout=min(10,max(.001,end-time.monotonic()))))
   except asyncio.TimeoutError:continue
   if m.get('error'):result['errors'].append(m['error'])
   if m.get('result'):result['ack']=m['result']
   d=m.get('feed')
   if d:
    if int(d.get('event_time',0))<start:result['historical']+=1
    else:result['current']+=1;result['trade_ids'].append(str(d.get('trade_id')))
 result['end_ns']=time.time_ns();return result
async def main():
 r=await asyncio.gather(probe(''),probe('@50'))
 out=Path('/srv/vibetrading-capacity/fv-research-20261004/grvt_selector_probe.json')
 out.write_text(json.dumps(r,indent=2));print(json.dumps(r))
asyncio.run(main())
