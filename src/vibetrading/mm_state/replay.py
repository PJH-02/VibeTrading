"""Streaming native pilot SQLite replay; receipt order, read-only source handles."""
import heapq,json,sqlite3
from pathlib import Path
from decimal import Decimal as D
from collections import deque
from vibetrading.fv_mm.types import Quote
from vibetrading.fv_pilot.public import Book


def sqlite_events(path):
    c=sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True);c.row_factory=sqlite3.Row
    def stream(query,priority,kind):
        for r in c.execute(query):yield r["t"],priority,kind,dict(r)
    try:
        yield from heapq.merge(
            stream("SELECT t,venue,asset,source_ns,raw FROM public_frames WHERE kind='book' ORDER BY rowid",0,"BOOK"),
            stream("SELECT * FROM quotes WHERE venue='binance' ORDER BY rowid",0,"REFERENCE"),
            stream("SELECT * FROM public_trades ORDER BY rowid",1,"TRADE"),
            stream("SELECT * FROM events WHERE kind='feed_gap' ORDER BY rowid",0,"GAP"),key=lambda x:x[:2])
    finally:c.close()


class NativeReplay:
    def __init__(self,path):
        self.path=path;self.books={};self.first=set();self.last={};self.ids=set();self.id_queue=deque()
        self.audit={"events":0,"invalid_books":0,"sequence_gaps":0,"historical_trades":0,"duplicate_trades":0}
        self.connection=sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True)

    def close(self):self.connection.close()

    def decode(self,kind,r):
        self.audit["events"]+=1
        v,a,t=r["venue"],r.get("asset",""),r["t"]
        if kind=="GAP":
            for key in list(self.books):
                if key[0]==v:self.books.pop(key);self.last.pop(key,None)
            return "GAP",v,"",t
        if kind=="REFERENCE":
            q=Quote(*(D(r[k]) for k in ("bid","ask","bid_qty","ask_qty")),t,r["source_ns"],r["seq"])
            return ("REFERENCE",v,a,q) if q.valid() else None
        key=v,a
        if kind=="TRADE":
            identifier=(v,a,r["trade_id"])
            if r["trade_id"] and identifier in self.ids:
                self.audit["duplicate_trades"]+=1;return None
            if r["trade_id"]:
                self.ids.add(identifier);self.id_queue.append(identifier)
                if len(self.id_queue)>50000:self.ids.remove(self.id_queue.popleft())
            q=self.last.get(key)
            if not q or t-q.received>3 or q.source_ns and r["source_ns"]<q.source_ns-2*10**9:
                self.audit["historical_trades"]+=1;return None
            return "TRADE",v,a,(r["side"],D(r["qty"]),D(r["price"]),t,r["source_ns"])
        m=json.loads(r["raw"])
        if v=="bybit":
            d=m["data"];bids,asks,seq,snap=d["b"],d["a"],int(d["u"]),m["type"]=="snapshot" or d["u"]==1;prev=None
        elif v=="bitget":
            d=m["data"][0];bids,asks,seq=d["b"],d["a"],int(d["seq"])
            prev=int(d.get("pseq",0));snap=m.get("action")=="snapshot" or prev==0
        elif v=="arcus":
            d=m["contents"];bids,asks,seq=d["bids"],d["asks"],int(d["lastSequenceId"])
            snap=m["type"]=="subscribed";prev=None
        else:
            d=m["feed"];bids=[[x["price"],x["size"]] for x in d["bids"]];asks=[[x["price"],x["size"]] for x in d["asks"]]
            seq=int(m["sequence_number"]);snap=seq==0;prev=m.get("prev_sequence_number")
            if snap:
                row=self.connection.execute("SELECT seq FROM quotes WHERE venue=? AND asset=? AND t=? LIMIT 1",(v,a,t)).fetchone()
                if row:seq=row[0]
        b=self.books.setdefault(key,Book());reset=b.sequence is None
        if not snap and b.sequence is not None and seq<=b.sequence:return None
        bad=False
        if not snap and b.sequence is not None:
            if v=="grvt":bad=seq!=b.sequence+1
            if v=="bitget":bad=not (prev<=b.sequence<seq if key in self.first else prev==b.sequence)
            if v=="arcus" and key not in self.first:bad=seq!=b.sequence+1
        if bad:
            self.audit["sequence_gaps"]+=1;self.books.pop(key,None);self.last.pop(key,None)
            return "GAP",v,a,t
        values=b.update(bids,asks,seq,snap)
        if snap:self.first.add(key)
        else:self.first.discard(key)
        if values is None:return None
        q=Quote(*values,t,r["source_ns"],seq)
        if not q.valid():
            self.audit["invalid_books"]+=1;self.last.pop(key,None)
            return "GAP",v,a,t
        self.last[key]=q
        return "BOOK",v,a,(q,b,reset)
