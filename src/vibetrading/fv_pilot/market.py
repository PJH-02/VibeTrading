import importlib
import time

from vibetrading.fv_mm.market import Markets,ROUTES
from .public import VENUES


class PilotMarkets(Markets):
    def __init__(self,journal,strategy_mode="state"):
        super().__init__(journal,strategy_mode)
        self.dirty={v:False for v in VENUES}
        self.models={(v,a):importlib.import_module("vibetrading.strategies.research.state."+module).Strategy(mode=strategy_mode) for v in VENUES for _,(module,a) in ROUTES.items()}
        journal.db.executescript("""
        CREATE TABLE IF NOT EXISTS public_frames(t REAL,wall_ns INTEGER,venue TEXT,asset TEXT,kind TEXT,source_ns INTEGER,raw TEXT);
        CREATE TABLE IF NOT EXISTS public_trades(t REAL,wall_ns INTEGER,venue TEXT,asset TEXT,source_ns INTEGER,side TEXT,qty TEXT,price TEXT,trade_id TEXT);
        CREATE INDEX IF NOT EXISTS public_trades_idx ON public_trades(venue,asset,t);
        """)
        self.book_counts={};self.trade_counts={};self.unknown_counts={}

    def external(self,venue,asset,q):
        # Simulated orders do not exist in the actual book. Do not subtract them.
        return q

    def book(self,venue,asset,q,reset,book,raw):
        self.on_book(venue,asset,q,reset,book)
        self.book_counts[venue,asset]=self.book_counts.get((venue,asset),0)+1
        self.journal.db.execute("INSERT INTO public_frames VALUES(?,?,?,?,?,?,?)",(q.received,time.time_ns(),venue,asset,"book",q.source_ns,raw))

    def trade(self,venue,asset,side,qty,price,identifier,received,source,raw):
        key=venue,asset
        quote=self.quotes.get(key)
        lag=2 if self.strategy_mode=="state" else 10
        historical=quote is None or (quote.source_ns>0 and source<quote.source_ns-lag*10**9)
        if not historical:self.on_trade(venue,asset,side,qty,received,source,price=price)
        else:self.journal.event("historical_trade_skipped",venue,asset,source_ns=source)
        self.trade_counts[key]=self.trade_counts.get(key,0)+1
        if side=="Unknown":self.unknown_counts[key]=self.unknown_counts.get(key,0)+1
        self.journal.db.execute("INSERT INTO public_trades VALUES(?,?,?,?,?,?,?,?,?)",(received,time.time_ns(),venue,asset,source,side,str(qty),str(price),identifier))
        if venue=="arcus":self.journal.db.execute("INSERT INTO public_frames VALUES(?,?,?,?,?,?,?)",(received,time.time_ns(),venue,asset,"trade",source,raw))
