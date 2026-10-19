import asyncio, collections, json, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from vibetrading.models import Venue
from vibetrading.providers.extended_perp import ExtendedPerpCollector

class Sink:
    def __init__(self): self.counts=collections.Counter(); self.samples={}
    async def publish_market_event(self,event):
        key=event.instrument.std_symbol+':'+event.event_type.value
        self.counts[key]+=1
        self.samples.setdefault(key,event.model_dump(mode='json'))

async def main():
    async def check(venue):
        bus=Sink(); c=ExtendedPerpCollector(venue)
        original=c._arcus_message if venue==Venue.ARCUS else c._grvt_message
        frames=[]
        async def receive(bus,msg,raw):
            if len(frames)<18: frames.append(msg)
            await original(bus,msg,raw)
        if venue==Venue.ARCUS: c._arcus_message=receive
        else: c._grvt_message=receive
        error=None
        try: await c.run(bus,duration_sec=25)
        except Exception as exc: error=type(exc).__name__+': '+str(exc)
        result={'venue':venue.value,'counts':dict(bus.counts),'samples':bus.samples,'frames':frames,'error':error}
        pathlib.Path('docs/evidence/'+venue.value+'-live-smoke-20261003.json').write_text(json.dumps(result,indent=2),encoding='utf8')
        print(json.dumps({'venue':venue.value,'counts':dict(bus.counts),'error':error}),flush=True)
    await asyncio.gather(check(Venue.ARCUS),check(Venue.GRVT))

asyncio.run(main())
