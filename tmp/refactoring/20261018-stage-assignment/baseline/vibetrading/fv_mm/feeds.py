"""Shared sockets; signal updates happen on each received market event."""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from decimal import Decimal as D

import websockets

from .types import Quote
from .venues import ASSETS
from vibetrading.mm_state.clock import require_source_fresh


class Book:
    def __init__(self):
        self.bids, self.asks, self.sequence = {}, {}, None

    def update(self, bids, asks, seq, snapshot=False, previous=None, contiguous=False):
        if snapshot:
            self.bids.clear(); self.asks.clear()
        elif self.sequence is None:
            return None
        elif seq <= self.sequence:
            return None
        elif contiguous and (previous != self.sequence or seq != self.sequence+1):
            raise RuntimeError("book sequence gap")
        for target, rows in ((self.bids,bids),(self.asks,asks)):
            for price, size in rows:
                price,size=D(price),D(size)
                if not price.is_finite() or not size.is_finite() or price<=0 or size<0:
                    raise RuntimeError("Invalid public book level")
                if size==0:
                    target.pop(price,None)
                else:
                    target[price]=size
            if len(target)>4096:
                raise RuntimeError("Book depth capacity exceeded; a bounded snapshot feed is required")
        self.sequence=seq
        if not self.bids or not self.asks:
            return None
        b,a=max(self.bids),min(self.asks)
        return b,a,self.bids[b],self.asks[a]


class Feeds:
    def __init__(self, on_book, on_trade, on_gap, on_reference):
        self.on_book,self.on_trade,self.on_gap,self.on_reference=on_book,on_trade,on_gap,on_reference
        self.closed=False
        self.trade_ids=set()
        self.id_queue=deque()

    def emit_book(self,venue,asset,q,reset,book):
        require_source_fresh(q.source_ns)
        self.on_book(venue,asset,q,reset,book)

    def trade(self, venue, asset, side, size, identifier, source,price=None):
        key=(venue,asset,identifier)
        if key in self.trade_ids:
            return
        self.trade_ids.add(key); self.id_queue.append(key)
        if len(self.id_queue)>50000:
            self.trade_ids.remove(self.id_queue.popleft())
        self.on_trade(venue,asset,side,D(size),time.monotonic(),int(source),price=D(price) if price is not None else None)

    async def reconnect(self, venue, method):
        while not self.closed:
            try:
                await method()
                raise RuntimeError("socket closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.on_gap(venue,type(exc).__name__)
                await asyncio.sleep(2)

    async def bybit(self):
        books={a:Book() for a in ASSETS}
        async with websockets.connect("wss://stream-testnet.bybit.com/v5/public/linear",ping_interval=None,max_queue=8) as ws:
            await ws.send(json.dumps({"op":"subscribe","args":[topic for a in ASSETS for topic in (f"orderbook.50.{a}USDT",f"publicTrade.{a}USDT")]}))
            async def heartbeat():
                while True:
                    await asyncio.sleep(15); await ws.send('{"op":"ping"}')
            ping=asyncio.create_task(heartbeat())
            try:
                async for raw in ws:
                    msg=json.loads(raw); topic=msg.get("topic",""); data=msg.get("data")
                    if msg.get("op")=="subscribe" and msg.get("success") is False:
                        raise RuntimeError("Bybit public subscription rejected")
                    if topic.startswith("orderbook."):
                        asset=data["s"].removesuffix("USDT")
                        snap=msg.get("type")=="snapshot" or data["u"]==1
                        values=books[asset].update(data.get("b",[]),data.get("a",[]),int(data["u"]),snap)
                        if values:
                            self.emit_book("bybit",asset,Quote(*values,time.monotonic(),int(data.get("cts",msg.get("cts",msg["ts"])))*10**6,int(data["u"])),snap,books[asset])
                    elif topic.startswith("publicTrade."):
                        for r in data:
                            self.trade("bybit",r["s"].removesuffix("USDT"),r["S"],r["v"],str(r["i"]),int(r["T"])*10**6,r["p"])
            finally:
                ping.cancel(); await asyncio.gather(ping,return_exceptions=True)

    async def grvt(self):
        books,baselines={a:Book() for a in ASSETS},{}
        async with websockets.connect("wss://market-data.testnet.grvt.io/ws/full",ping_interval=15,max_queue=8) as ws:
            for n,(stream,suffix) in enumerate((("v1.book.d","@100"),("v1.trade","@50")),1):
                await ws.send(json.dumps({"jsonrpc":"2.0","method":"subscribe","params":{"stream":stream,"selectors":[a+"_USDT_Perp"+suffix for a in ASSETS]},"id":n}))
            async for raw in ws:
                msg=json.loads(raw)
                if msg.get("error"):
                    raise RuntimeError("GRVT public subscription rejected")
                result=msg.get("result")
                if isinstance(result,dict) and result.get("stream")=="v1.book.d":
                    for selector,first in zip(result.get("subs",[]),result.get("first_sequence_number",[])):
                        baselines[selector.split("_")[0]]=int(first)-1
                data=msg.get("feed")
                if not isinstance(data,dict):
                    continue
                asset=(data.get("instrument") or msg["selector"]).split("_")[0]
                if asset not in books:
                    continue
                if msg.get("stream")=="v1.book.d":
                    wire=int(msg["sequence_number"]); snap=wire==0
                    seq=baselines.get(asset,0) if snap else wire
                    previous=int(msg.get("prev_sequence_number",books[asset].sequence or 0))
                    values=books[asset].update([[r["price"],r["size"]] for r in data.get("bids",[])],[[r["price"],r["size"]] for r in data.get("asks",[])],seq,snap,previous,True)
                    if values:
                        self.emit_book("grvt",asset,Quote(*values,time.monotonic(),int(data["event_time"]),seq),snap,books[asset])
                elif msg.get("stream")=="v1.trade":
                    side="Buy" if data.get("is_taker_buyer") is True else "Sell" if data.get("is_taker_buyer") is False else "Unknown"
                    self.trade("grvt",asset,side,data["size"],str(data["trade_id"]),data["event_time"],data["price"])

    async def binance(self):
        streams="/".join(a.lower()+"usdt@bookTicker" for a in ASSETS)
        async with websockets.connect("wss://fstream.binance.com/stream?streams="+streams,ping_interval=15,max_queue=8) as ws:
            async for raw in ws:
                msg=json.loads(raw); r=msg.get("data",msg)
                if r.get("e")=="bookTicker":
                    q=Quote(D(r["b"]),D(r["a"]),D(r["B"]),D(r["A"]),time.monotonic(),int(r.get("E",r["T"]))*10**6,int(r["u"]))
                    require_source_fresh(q.source_ns)
                    if q.valid():
                        self.on_reference(r["s"].removesuffix("USDT"),q)

    def tasks(self):
        return [asyncio.create_task(self.reconnect(v,f),name=v+"-public") for v,f in (("bybit",self.bybit),("grvt",self.grvt),("binance",self.binance))]
