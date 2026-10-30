"""Causal receipt-time paper matching; no method can submit an exchange order."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D

from vibetrading.fv_mm.types import Intent


@dataclass
class PaperOrder:
    identifier: str
    venue: str
    strategy: str
    asset: str
    intent: Intent
    born: float
    arrival: float
    leaves: D
    ioc: bool=False
    cancel_at: float | None=None
    active: bool=False
    done: bool=False
    queue: D | None=None
    source_floor: int=0
    status: str="PENDING"


class PaperMatching:
    def __init__(self,instruments,latencies,journal=None,scenario="measured",factor=1,fill_callback=None):
        self.instruments,self.latencies=instruments,latencies
        self.journal,self.scenario,self.factor=journal,scenario,factor
        self.fill_callback=fill_callback or (lambda *args:None)
        self.orders={};self.pending={};self.books={};self.quotes={};self.sequence=0
        self.counts={};self.taker_used={}
        if journal:
            journal.db.executescript("""
            CREATE TABLE IF NOT EXISTS pilot_orders(id TEXT PRIMARY KEY,scenario TEXT,venue TEXT,strategy TEXT,asset TEXT,side TEXT,role TEXT,qty TEXT,price TEXT,born REAL,arrival REAL,live REAL,cancel_request REAL,cancel_effective REAL,terminal REAL,status TEXT,leaves TEXT,queue TEXT,ioc INTEGER);
            CREATE TABLE IF NOT EXISTS pilot_fills(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id TEXT,scenario TEXT,venue TEXT,strategy TEXT,asset TEXT,t REAL,source_ns INTEGER,side TEXT,qty TEXT,price TEXT,fee TEXT,maker INTEGER,basis TEXT);
            CREATE TABLE IF NOT EXISTS pilot_states(t REAL,wall_ns INTEGER,scenario TEXT,venue TEXT,strategy TEXT,asset TEXT,inventory TEXT,cash TEXT,fees TEXT,mid TEXT,pnl_ex_funding TEXT);
            """)

    def count(self,name,venue,strategy):
        key=venue+":"+strategy+":"+name
        self.counts[key]=self.counts.get(key,0)+1

    def submit_intent(self,venue,strategy,asset,intent,now,ioc=False):
        self.sequence+=1
        identifier=f"paper-{self.scenario}-{self.sequence}"
        arrival=now+self.factor*self.latencies[venue]["entry_seconds"]
        order=PaperOrder(identifier,venue,strategy,asset,intent,now,arrival,intent.quantity,ioc)
        self.orders[identifier]=order
        self.pending[identifier]=order
        self.count("intents",venue,strategy)
        if self.journal:
            self.journal.db.execute("INSERT INTO pilot_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(identifier,self.scenario,venue,strategy,asset,intent.side,intent.role,str(intent.quantity),str(intent.price),now,arrival,None,None,None,None,order.status,str(order.leaves),None,int(ioc)))
        return order

    def cancel_intent(self,order,now):
        if order.done or order.cancel_at is not None:return
        order.cancel_at=max(order.arrival,now+self.factor*self.latencies[order.venue]["cancel_seconds"])
        self.count("cancel_intents",order.venue,order.strategy)
        if self.journal:self.journal.db.execute("UPDATE pilot_orders SET cancel_request=?,cancel_effective=? WHERE id=?",(now,order.cancel_at,order.identifier))

    def persist(self,order,now):
        if order.done:self.pending.pop(order.identifier,None)
        if self.journal:
            self.journal.db.execute("UPDATE pilot_orders SET status=?,leaves=?,queue=?,terminal=? WHERE id=?",(order.status,str(order.leaves),str(order.queue) if order.queue is not None else None,now if order.done else None,order.identifier))

    def queue_at(self,order):
        book=self.books.get((order.venue,order.asset))
        if not book:return None
        levels=book.bids if order.intent.side=="Buy" else book.asks
        if not levels:return None
        price=order.intent.price
        if min(levels)<=price<=max(levels):return levels.get(price,D(0))
        return None  # Outside observed depth is not an empty queue.

    def on_book(self,venue,asset,quote,book,reset=False):
        self.quotes[venue,asset]=quote;self.books[venue,asset]=book
        for order in list(self.pending.values()):
            if order.venue==venue and order.asset==asset and order.active and not order.done:
                if reset:
                    observed=self.queue_at(order)
                    order.queue=max(order.queue,observed) if order.queue is not None and observed is not None else observed
                elif order.queue is None:order.queue=self.queue_at(order)
        self.advance(quote.received)

    def on_gap(self,venue,now):
        for key in list(self.quotes):
            if key[0]==venue:self.quotes.pop(key);self.books.pop(key,None)
        for order in list(self.pending.values()):
            if order.venue==venue and not order.done:self.cancel_intent(order,now)

    def advance(self,now):
        for order in list(self.pending.values()):
            if order.done:continue
            if order.cancel_at is not None and now>=order.cancel_at:
                order.done=True;order.status="CANCELLED";self.persist(order,now);continue
            if order.active or now<order.arrival:continue
            q=self.quotes.get((order.venue,order.asset))
            if q is None or not q.valid() or now-q.received>3:
                order.done=True;order.status="REJECTED_STALE";self.count("stale_arrival",order.venue,order.strategy);self.persist(order,now);continue
            order.source_floor=q.source_ns
            if order.ioc:
                self.sweep(order,now)
                order.done=True;order.status="FILLED" if order.leaves==0 else "IOC_PARTIAL" if order.leaves<order.intent.quantity else "IOC_NO_FILL"
                self.persist(order,now);continue
            crosses=order.intent.price>=q.ask if order.intent.side=="Buy" else order.intent.price<=q.bid
            if crosses:
                order.done=True;order.status="POST_ONLY_REJECTED";self.count("post_only_rejected",order.venue,order.strategy);self.persist(order,now);continue
            order.active=True;order.status="OPEN";order.queue=self.queue_at(order)
            self.count("active",order.venue,order.strategy)
            if self.journal:self.journal.db.execute("UPDATE pilot_orders SET live=? WHERE id=?",(now,order.identifier))
            self.persist(order,now)

    def fill(self,order,quantity,price,now,source,maker,basis):
        quantity=min(order.leaves,self.instruments[order.venue][order.asset].floor(quantity))
        if quantity<=0:return
        i=self.instruments[order.venue][order.asset]
        fee=quantity*price*(i.maker_fee if maker else i.taker_fee)
        order.leaves-=quantity
        self.count("fills",order.venue,order.strategy)
        if self.journal:self.journal.db.execute("INSERT INTO pilot_fills VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)",(order.identifier,self.scenario,order.venue,order.strategy,order.asset,now,source,order.intent.side,str(quantity),str(price),str(fee),int(maker),basis))
        self.fill_callback(order,quantity,price,fee,now)
        if order.leaves==0:order.done=True;order.status="FILLED"
        else:order.status="PARTIAL"
        self.persist(order,now)

    def sweep(self,order,now):
        key=order.venue,order.asset;book=self.books[key];q=self.quotes[key]
        levels=book.asks if order.intent.side=="Buy" else book.bids
        for price,size in sorted(levels.items(),reverse=order.intent.side=="Sell"):
            if (order.intent.side=="Buy" and price>order.intent.price) or (order.intent.side=="Sell" and price<order.intent.price):break
            consumed=(key,book.sequence,order.intent.side,price)
            available=max(D(0),size-self.taker_used.get(consumed,D(0)))
            used=min(available,order.leaves)
            before=order.leaves
            self.fill(order,used,price,now,q.source_ns,False,"arrival_observed_L2_sweep")
            self.taker_used[consumed]=self.taker_used.get(consumed,D(0))+before-order.leaves
            if order.leaves==0:break
        # Bound memory while retaining consumption in the current book version.
        if len(self.taker_used)>10000:self.taker_used={k:v for k,v in self.taker_used.items() if k[0]!=key or k[1]==book.sequence}

    def on_trade(self,venue,asset,side,quantity,price,now,source):
        self.advance(now)
        if side not in ("Buy","Sell"):return
        for order in list(self.pending.values()):
            if order.done or not order.active or (order.venue,order.asset)!=(venue,asset):continue
            q=self.quotes.get((venue,asset))
            if q is None or now-q.received>3:continue
            if source and order.source_floor and source<order.source_floor:
                self.count("older_source_trade_skipped",venue,order.strategy);continue
            opposite=side==("Sell" if order.intent.side=="Buy" else "Buy")
            reaches=price<=order.intent.price if order.intent.side=="Buy" else price>=order.intent.price
            if not opposite or not reaches or order.queue is None:continue
            if price!=order.intent.price:
                # Strictly trading through a known price is price-priority evidence.
                # Still cap the hypothetical fill by observed aggressor volume.
                order.queue=D(0);available=quantity;basis="strict_trade_through_observed_queue"
            else:
                ahead=min(order.queue,quantity);order.queue-=ahead;available=quantity-ahead;basis="same_price_volume_after_displayed_queue"
            self.fill(order,available,order.intent.price,now,source,True,basis)
            self.persist(order,now)
