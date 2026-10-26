from __future__ import annotations
import importlib
import time
import json
from decimal import Decimal as D
from .types import Quote

ROUTES={"S1":("s1_net_liquidity","BTC"),"S2":("s2_external_anchor","ETH"),"S3":("s3_aggressive_flow","SOL"),"S4":("s4_obi","XRP")}


class Markets:
    def __init__(self,journal,strategy_mode="state"):
        self.journal=journal
        self.strategy_mode=strategy_mode
        journal.db.execute("CREATE TABLE IF NOT EXISTS state_samples(venue TEXT,strategy TEXT,asset TEXT,t REAL,wall_ns INTEGER,state TEXT,direction INTEGER,strength REAL,ready INTEGER,mid REAL,ref_mid REAL,metadata TEXT)")
        self.quotes,self.references,self.models={},{},{}
        self.dirty={v:False for v in ("bybit","grvt")}
        self.own={}
        self.last_raw={}
        self.phase="training"
        self.quote_observers=[];self.gap_observers=[]
        for venue in self.dirty:
            for sid,(module,asset) in ROUTES.items():
                self.models[venue,asset]=importlib.import_module("strategies.research.state."+module).Strategy(mode=strategy_mode)

    def external(self,venue,asset,q):
        # Confirmed resting size only; pending/unknown never fabricated as depth.
        b,a=q.bid_qty,q.ask_qty
        for intent,leaves,live in self.own.get((venue,asset),[]):
            if live:
                if intent.side=="Buy" and intent.price==q.bid:
                    b=max(D(0),b-leaves)
                elif intent.side=="Sell" and intent.price==q.ask:
                    a=max(D(0),a-leaves)
        return Quote(q.bid,q.ask,b,a,q.received,q.source_ns,q.sequence)

    def on_book(self,venue,asset,quote,reset,book=None):
        key=venue,asset
        self.quotes[key]=quote
        for observe in self.quote_observers:observe(venue,asset,quote)
        external=self.external(venue,asset,quote)
        if key in self.models:
            if self.strategy_mode=="state":self.models[key].on_book(external,reset=reset,book=book)
            else:self.models[key].on_book(external,reset=reset)
            self.dirty[venue]=True
        value=(quote.bid,quote.ask,quote.bid_qty,quote.ask_qty)
        if value!=self.last_raw.get(key) or reset:
            self.journal.db.execute("INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?,?,?)",(venue,asset,quote.received,time.time_ns(),quote.source_ns,*map(str,value),quote.sequence,int(reset)))
            self.last_raw[key]=value

    def on_trade(self,venue,asset,side,qty,received,source,price=None):
        key=venue,asset
        quote=self.quotes.get(key)
        if self.strategy_mode=="state" and (quote is None or quote.source_ns>0 and source>0 and source<quote.source_ns-2*10**9):
            self.journal.event("historical_trade_skipped",venue,asset,source_ns=source)
            return
        if key in self.models:
            if self.strategy_mode=="state":self.models[key].on_trade(side,qty,received,price=price)
            else:self.models[key].on_trade(side,qty,received)
            self.dirty[venue]=True
        self.journal.db.execute("INSERT INTO trades VALUES(?,?,?,?,?,?)",(venue,asset,received,source,side,str(qty)))

    def on_gap(self,venue,reason):
        for observe in self.gap_observers:observe(venue)
        self.journal.event("feed_gap",venue,reason=reason)
        if venue=="binance":
            self.references.clear()
        else:
            for key in list(self.quotes):
                if key[0]==venue:
                    q=self.quotes.pop(key)
                    if key in self.models:
                        self.models[key].on_book(q,reset=True)
            self.dirty[venue]=True

    def on_reference(self,asset,quote):
        self.references[asset]=quote
        for venue in self.dirty:
            if (venue,asset) in self.models:
                self.dirty[venue]=True
        self.on_book("binance",asset,quote,False)

    def signal(self,venue,asset,now):
        raw=self.quotes.get((venue,asset))
        if raw is None or not raw.valid() or not 0<=now-raw.received<=3:
            return None,None
        q=self.external(venue,asset,raw)
        if not q.valid():
            return None,None
        return q,self.models[venue,asset].signal(q,now,self.references.get(asset))

    def sample(self):
        now=time.monotonic()
        for venue in self.dirty:
            for sid,(_,asset) in ROUTES.items():
                q,s=self.signal(venue,asset,now)
                if q is None:
                    continue
                ref=self.references.get(asset)
                refmid=float(ref.mid) if ref and now-ref.received<=3 else None
                self.journal.db.execute("INSERT INTO samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(venue,sid,asset,now,time.time_ns(),self.phase,float(q.mid),float(q.spread),float(q.depth),float(s.x),float(s.fv) if s.fv else None,refmid,s.reason,s.window_seconds,q.source_ns,q.sequence))
                if self.strategy_mode=="state":
                    self.journal.db.execute("INSERT INTO state_samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(venue,sid,asset,now,time.time_ns(),s.state,s.direction,float(s.strength),int(s.ready),float(q.mid),refmid,json.dumps(s.metadata,separators=(",",":"))))
