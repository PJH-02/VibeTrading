"""Temporary sampled timing taps. Does not change normalized event payloads."""
import contextvars
import json
import os
import time
from collections import OrderedDict

if os.environ.get('LATENCY_AUDIT_ID'):
    import websockets
    from core.models import BaseMarketEvent
    from vibetrading.bus import NatsJetStreamBus

    AUDIT = os.environ['LATENCY_AUDIT_ID']
    DEADLINE = time.monotonic()+600
    frames = OrderedDict()
    traces = OrderedDict()
    current = contextvars.ContextVar('latency_frame', default=None)
    original_connect = websockets.connect
    original_loads = json.loads
    original_init = BaseMarketEvent.__init__
    original_publish = NatsJetStreamBus.publish_market_event
    count = 0

    class TimedSocket:
        def __init__(self, socket): self.socket = socket
        def __getattr__(self, name): return getattr(self.socket, name)
        async def recv(self, *args, **kwargs):
            raw = await self.socket.recv(*args, **kwargs)
            if time.monotonic()<DEADLINE:
                frames[id(raw)] = (raw,time.time_ns(),time.monotonic_ns())
                if len(frames)>128: frames.popitem(last=False)
            return raw

    class TimedConnect:
        def __init__(self, *args, **kwargs): self.connection = original_connect(*args, **kwargs)
        async def __aenter__(self): return TimedSocket(await self.connection.__aenter__())
        async def __aexit__(self, *args): return await self.connection.__aexit__(*args)
        def __await__(self):
            async def connect(): return TimedSocket(await self.connection)
            return connect().__await__()

    def loads(raw, *args, **kwargs):
        frame = frames.pop(id(raw),None)
        current.set((frame[1],frame[2]) if frame and frame[0] is raw else None)
        return original_loads(raw,*args,**kwargs)

    def event_init(self, *args, **kwargs):
        global count
        original_init(self,*args,**kwargs)
        frame = current.get()
        if not frame or time.monotonic()>=DEADLINE or self.event_type.value not in {'order_book','trade'}:
            return
        count += 1
        if self.provider!='grvt-perp' and count%5: return
        key = self.event_id+':'+str(self.receive_monotonic_ns)
        traces[key] = {'event_id':self.event_id,'receive_monotonic_ns':self.receive_monotonic_ns,
            'wire_receive_wall_ns':frame[0],'wire_receive_mono_ns':frame[1],
            'normalized_mono_ns':time.monotonic_ns(),'provider':self.provider,
            'source_ts_ns':self.source_ts_ns,'symbol':self.instrument.std_symbol,
            'event_type':self.event_type.value,'snapshot':getattr(self,'update_type',None)=='snapshot'}
        if len(traces)>20000: traces.popitem(last=False)

    async def publish(self,event):
        key=event.event_id+':'+str(event.receive_monotonic_ns)
        trace=traces.pop(key,None)
        if trace: trace['publish_enter_mono_ns']=time.monotonic_ns()
        await original_publish(self,event)
        if trace and self._nc is not None:
            trace['publish_done_mono_ns']=time.monotonic_ns()
            try:
                await self._nc.publish('audit_latency.'+AUDIT+'.trace',json.dumps(trace,separators=(',',':')).encode())
            except Exception:
                pass

    websockets.connect=TimedConnect
    json.loads=loads
    BaseMarketEvent.__init__=event_init
    NatsJetStreamBus.publish_market_event=publish
