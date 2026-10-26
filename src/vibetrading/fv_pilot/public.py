from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from decimal import Decimal as D, ROUND_FLOOR
import json
import time

import aiohttp
import websockets

from vibetrading.fv_mm.types import Quote
from vibetrading.mm_state.clock import require_source_fresh

ASSETS=("BTC","ETH","SOL","XRP")
VENUES=("arcus","bybit","bitget","grvt")


class Book:
    def __init__(self):
        self.bids,self.asks,self.sequence={},{},None

    def update(self,bids,asks,seq,snapshot=False,previous=None,contiguous=False):
        if snapshot:self.bids.clear();self.asks.clear()
        elif self.sequence is None or seq<=self.sequence:return None
        elif contiguous and (previous!=self.sequence or seq!=self.sequence+1):raise RuntimeError("book sequence gap")
        for target,rows in ((self.bids,bids),(self.asks,asks)):
            for price,size in rows:
                price,size=D(str(price)),D(str(size))
                if not price.is_finite() or not size.is_finite() or price<=0 or size<0:
                    raise RuntimeError("Invalid public book level")
                if size==0:target.pop(price,None)
                else:target[price]=size
            if len(target)>4096:
                raise RuntimeError("Book depth capacity exceeded; a bounded snapshot feed is required")
        self.sequence=seq
        if not self.bids or not self.asks:return None
        b,a=max(self.bids),min(self.asks)
        return b,a,self.bids[b],self.asks[a]


@dataclass(frozen=True)
class Instrument:
    symbol: str
    currency: str
    tick: D
    lot: D
    minimum: D
    min_notional: D
    maker_fee: D
    taker_fee: D

    def floor(self, quantity):
        return (quantity/self.lot).to_integral_value(rounding=ROUND_FLOOR)*self.lot

    def valid(self, quantity, price, reduce=False):
        return quantity>0 and quantity>=self.minimum and (reduce or quantity*price>=self.min_notional)


async def metadata():
    """Only unauthenticated public instrument/config endpoints; no trading client."""
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver()),timeout=aiohttp.ClientTimeout(total=20)) as s:
        async def get(url):
            async with s.get(url) as r:r.raise_for_status();return await r.json()
        async def post(url):
            async with s.post(url,json={}) as r:r.raise_for_status();return await r.json()
        arcus,fees,bybit,bitget,grvt=await asyncio.gather(
            get("https://api.arcus.xyz/v1/markets"),get("https://api.arcus.xyz/v1/feetiers"),
            get("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000"),
            get("https://api.bitget.com/api/v3/market/instruments?category=USDT-FUTURES"),
            post("https://market-data.grvt.io/full/v1/all_instruments"))
    if bybit.get("retCode")!=0 or bitget.get("code")!="00000":raise RuntimeError("Public instrument API rejected")
    tier=next(t for t in fees["tiers"] if t["level"]==0)
    result={v:{} for v in VENUES}
    for asset in ASSETS:
        a=next(r for r in arcus["markets"] if r["marketDisplayName"]==asset+"-USD" and r["status"]=="ONLINE")
        result["arcus"][asset]=Instrument(a["marketDisplayName"],"USD",D(a["tickSize"]),D(a["stepSize"]),D(a["minOrderSize"]),D(a["minOrderNotional"]),D(tier["maker_fee_ppm"])/10**6,D(tier["taker_fee_ppm"])/10**6)
        b=next(r for r in bybit["result"]["list"] if r["symbol"]==asset+"USDT" and r["status"]=="Trading")
        result["bybit"][asset]=Instrument(b["symbol"],"USDT",D(b["priceFilter"]["tickSize"]),D(b["lotSizeFilter"]["qtyStep"]),D(b["lotSizeFilter"]["minOrderQty"]),D(b["lotSizeFilter"]["minNotionalValue"]),D(".0002"),D(".00055"))
        c=next(r for r in bitget["data"] if r["symbol"]==asset+"USDT" and r["status"]=="online")
        result["bitget"][asset]=Instrument(c["symbol"],"USDT",D(c["priceMultiplier"]),D(c["quantityMultiplier"]),D(c["minOrderQty"]),D(c["minOrderAmount"]),D(c["makerFeeRate"]),D(c["takerFeeRate"]))
        g=next(r for r in grvt["result"] if r["instrument"]==asset+"_USDT_Perp")
        result["grvt"][asset]=Instrument(g["instrument"],"USDT",D(g["tick_size"]),D(g["min_size"]),D(g["min_size"]),D(g.get("min_notional",0)),D(0),D(".00045"))
    return result,{"arcus":arcus,"arcus_fee_tiers":fees,"bybit":bybit,"bitget":bitget,"grvt":grvt,"fee_note":"Arcus/Bitget public base rates; Bybit VIP0 0.02%/0.055%; GRVT conservative 0%/maximum 0.045%, no rebates assumed"}


class PublicFeeds:
    """Separate mainnet adapter. It has no credentials, signing or order API."""
    def __init__(self,on_book,on_trade,on_gap,on_reference):
        self.on_book,self.on_trade,self.on_gap,self.on_reference=on_book,on_trade,on_gap,on_reference
        self.closed=False
        self.books={};self.last_quotes={};self.ids=set();self.id_queue=deque()

    def book(self,venue,asset,bids,asks,seq,snapshot,source,raw,previous=None,contiguous=False,bridge=False):
        require_source_fresh(int(source))
        key=venue,asset
        book=self.books.setdefault(key,Book())
        reset_history=book.sequence is None or (snapshot and seq<=book.sequence)
        if bridge and not snapshot and book.sequence is not None:
            if seq<=book.sequence:return
            first=getattr(book,"first_delta",True)
            if not ((previous<=book.sequence<seq) if first else previous==book.sequence):
                raise RuntimeError("book sequence gap")
            book.first_delta=False
        values=book.update(bids,asks,seq,snapshot,previous,contiguous)
        if snapshot:book.first_delta=True
        if values:
            q=Quote(*values,time.monotonic(),int(source),seq)
            self.last_quotes[key]=q
            self.on_book(venue,asset,q,reset_history,book,raw)

    def trade(self,venue,asset,side,qty,price,identifier,source,raw):
        key=venue,asset,str(identifier)
        if key in self.ids:return
        self.ids.add(key);self.id_queue.append(key)
        if len(self.id_queue)>100000:self.ids.remove(self.id_queue.popleft())
        side=side.title() if isinstance(side,str) else "Unknown"
        if side not in ("Buy","Sell"):side="Unknown"
        # Arcus currently omits native aggressor direction. Keep it unknown;
        # never turn a guessed side into S3 input or assumed maker execution.
        self.on_trade(venue,asset,side,D(str(qty)),D(str(price)),str(identifier),time.monotonic(),int(source),raw)

    async def reconnect(self,venue,method):
        while not self.closed:
            try:
                await method()
                raise RuntimeError("socket closed")
            except asyncio.CancelledError:raise
            except Exception as e:
                for key in list(self.books):
                    if key[0]==venue:self.books.pop(key);self.last_quotes.pop(key,None)
                self.on_gap(venue,type(e).__name__+":"+str(e)[:180])
                await asyncio.sleep(2)

    async def bybit(self):
        async with websockets.connect("wss://stream.bybit.com/v5/public/linear",ping_interval=None,max_queue=8) as ws:
            await ws.send(json.dumps({"op":"subscribe","args":[x for a in ASSETS for x in (f"orderbook.50.{a}USDT",f"publicTrade.{a}USDT")]}))
            async def heartbeat():
                while True:await asyncio.sleep(15);await ws.send('{"op":"ping"}')
            ping=asyncio.create_task(heartbeat())
            try:
                async for raw in ws:
                    msg=json.loads(raw);topic=msg.get("topic","");r=msg.get("data")
                    if msg.get("success") is False:raise RuntimeError("Bybit subscription rejected")
                    if topic.startswith("orderbook."):
                        self.book("bybit",r["s"].removesuffix("USDT"),r.get("b",[]),r.get("a",[]),int(r["u"]),msg.get("type")=="snapshot" or r["u"]==1,int(msg.get("cts",r.get("cts",msg["ts"])))*10**6,raw)
                    elif topic.startswith("publicTrade."):
                        for t in r:self.trade("bybit",t["s"].removesuffix("USDT"),t.get("S"),t["v"],t["p"],t["i"],int(t["T"])*10**6,raw)
            finally:ping.cancel();await asyncio.gather(ping,return_exceptions=True)

    async def bitget(self):
        async with websockets.connect("wss://ws.bitget.com/v3/ws/public",ping_interval=20,max_queue=8) as ws:
            await ws.send(json.dumps({"op":"subscribe","args":[{"instType":"usdt-futures","topic":x,"symbol":a+"USDT"} for a in ASSETS for x in ("books50","publicTrade")]}))
            async def heartbeat():
                while True:await asyncio.sleep(25);await ws.send("ping")
            ping=asyncio.create_task(heartbeat())
            try:
                async for raw in ws:
                    if raw=="pong":continue
                    msg=json.loads(raw)
                    if msg.get("event")=="error":raise RuntimeError("Bitget subscription rejected")
                    arg=msg.get("arg",{});asset=arg.get("symbol","").removesuffix("USDT")
                    if asset not in ASSETS:continue
                    for r in msg.get("data",[]):
                        if arg.get("topic")=="books50":
                            snap=msg.get("action")=="snapshot" or int(r.get("pseq",-1))==0
                            self.book("bitget",asset,r["b"],r["a"],int(r["seq"]),snap,int(r["ts"])*10**6,raw,int(r.get("pseq",0)),bridge=True)
                        elif arg.get("topic")=="publicTrade":
                            self.trade("bitget",asset,r.get("S"),r["v"],r["p"],r["i"],int(r["T"])*10**6,raw)
            finally:ping.cancel();await asyncio.gather(ping,return_exceptions=True)

    async def grvt(self):
        baselines={}
        async with websockets.connect("wss://market-data.grvt.io/ws/full",ping_interval=15,max_queue=8) as ws:
            for n,(stream,suffix) in enumerate((("v1.book.d","@100"),("v1.trade","@50")),1):
                await ws.send(json.dumps({"jsonrpc":"2.0","method":"subscribe","params":{"stream":stream,"selectors":[a+"_USDT_Perp"+suffix for a in ASSETS]},"id":n}))
            async for raw in ws:
                msg=json.loads(raw)
                if msg.get("error"):raise RuntimeError("GRVT subscription rejected")
                result=msg.get("result")
                if isinstance(result,dict) and result.get("stream")=="v1.book.d":
                    for selector,first in zip(result.get("subs",[]),result.get("first_sequence_number",[])):baselines[selector.split("_")[0]]=int(first)-1
                r=msg.get("feed")
                if not isinstance(r,dict):continue
                asset=(r.get("instrument") or msg["selector"]).split("_")[0]
                if asset not in ASSETS:continue
                if msg.get("stream")=="v1.book.d":
                    wire=int(msg["sequence_number"]);snap=wire==0;seq=baselines.get(asset,0) if snap else wire
                    previous=int(msg.get("prev_sequence_number",self.books.get(("grvt",asset),Book()).sequence or 0))
                    self.book("grvt",asset,[[x["price"],x["size"]] for x in r.get("bids",[])],[[x["price"],x["size"]] for x in r.get("asks",[])],seq,snap,r["event_time"],raw,previous,True)
                elif msg.get("stream")=="v1.trade":
                    self.trade("grvt",asset,"Buy" if r.get("is_taker_buyer") is True else "Sell" if r.get("is_taker_buyer") is False else "Unknown",r["size"],r["price"],r["trade_id"],r["event_time"],raw)

    async def arcus(self):
        first=set()
        async with websockets.connect("wss://api.arcus.xyz/v1/ws",ping_interval=20,max_queue=8) as ws:
            for a in ASSETS:
                await ws.send(json.dumps({"type":"subscribe","channel":"l2OrderbookUpdates","id":a+"-USD","nLevels":100}))
                await ws.send(json.dumps({"type":"subscribe","channel":"trades","id":a+"-USD","snapshot":False}))
            async for raw in ws:
                msg=json.loads(raw)
                if msg.get("type")=="error" or msg.get("error"):raise RuntimeError("Arcus subscription rejected")
                asset=msg.get("id","").removesuffix("-USD");r=msg.get("contents")
                if asset not in ASSETS:continue
                if msg.get("channel")=="l2OrderbookUpdates" and isinstance(r,dict):
                    seq=int(r["lastSequenceId"]);snap=msg.get("type")=="subscribed"
                    b=self.books.get(("arcus",asset));last=b.sequence if b else None
                    if not snap and last is not None:
                        if seq<=last:continue
                        if asset not in first and seq!=last+1:raise RuntimeError("Arcus book sequence gap")
                        first.discard(asset)
                    if snap:first.add(asset)
                    self.book("arcus",asset,r.get("bids",[]),r.get("asks",[]),seq,snap,int(r.get("timestamp") or 0)*1000,raw)
                elif msg.get("channel")=="trades" and isinstance(r,list):
                    for t in r:self.trade("arcus",asset,t.get("side","Unknown"),t["size"],t["price"],t["tradeId"],int(t["timestamp"])*1000,raw)

    async def binance(self):
        streams="/".join(a.lower()+"usdt@bookTicker" for a in ASSETS)
        async with websockets.connect("wss://fstream.binance.com/stream?streams="+streams,ping_interval=15,max_queue=8) as ws:
            async for raw in ws:
                r=json.loads(raw).get("data",{})
                if r.get("e")=="bookTicker":
                    q=Quote(D(r["b"]),D(r["a"]),D(r["B"]),D(r["A"]),time.monotonic(),int(r.get("E",r["T"]))*10**6,int(r["u"]))
                    require_source_fresh(q.source_ns)
                    if q.valid():self.on_reference(r["s"].removesuffix("USDT"),q)

    def tasks(self):
        return [asyncio.create_task(self.reconnect(v,getattr(self,v)),name=v+"-public-mainnet") for v in (*VENUES,"binance")]
