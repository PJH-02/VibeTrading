import asyncio,json,pathlib,time
import nats
from nats.js.errors import NotFoundError
async def main():
 nc=await nats.connect('nats://vibetrading-nats-1:4222');js=nc.jetstream();info=await js.stream_info('OPERATIONS');sem=asyncio.Semaphore(4);out=[]
 async def one(seq):
  async with sem:
   try:m=await js.get_msg('OPERATIONS',seq=seq)
   except NotFoundError:return
   if not m.subject.startswith('system.'):return
   d=json.loads(m.data)
   if d.get('provider') in ('kis','kiwoom'):
    out.append({'sequence':seq,'subject':m.subject,**{k:d.get(k) for k in ('kind','ts_ns','provider','reason','transient') if k in d}})
 for start in range(info.state.first_seq,info.state.last_seq+1,128):await asyncio.gather(*(one(seq) for seq in range(start,min(start+128,info.state.last_seq+1))))
 result={'captured_ts_ns':time.time_ns(),'stream_created':str(info.created),'first_seq':info.state.first_seq,'last_seq':info.state.last_seq,'events':sorted(out,key=lambda x:x['sequence'])};pathlib.Path('/audit/operations.json').write_text(json.dumps(result,indent=2));await nc.close();print(json.dumps({'events':len(out),'first_seq':info.state.first_seq,'last_seq':info.state.last_seq}))
asyncio.run(main())
