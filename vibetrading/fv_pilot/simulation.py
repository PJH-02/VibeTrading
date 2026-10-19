"""Deterministic receipt-time paper engine. No exchange order client exists here."""
from dataclasses import dataclass
from decimal import Decimal as D
from types import SimpleNamespace
import heapq
import math

from vibetrading.fv_mm.types import Intent
from vibetrading.mm_state.analytics import Markouts


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
    ioc: bool = False
    cancel_at: float | None = None
    active: bool = False
    done: bool = False
    queue: D | None = None
    source_floor: int = 0
    status: str = "IN_FLIGHT"
    sent_ts: float | None = None
    ack_ts: float | None = None
    active_ts: float | None = None
    cancel_requested_ts: float | None = None
    first_fill_ts: float | None = None
    signal_snapshot: dict | None = None
    inventory_snapshot: D = D(0)
    reason_cancel: str | None = None


class PaperMatching:
    # Same-time semantics: quote observation, arrival, trade, effective cancel.
    # A same-time cancellation cannot retroactively prevent a known trade.
    PRIORITY = {"BOOK":0,"ARRIVE":1,"TRADE":2,"CANCEL":3,"GAP":0}

    def __init__(self, instruments, latencies, journal=None, scenario="measured",factor=1,
                 fill_callback=None, *, queue_model="conservative",position_callback=None,
                 emergency_taker=False):
        self.instruments,self.latencies = instruments,latencies
        self.journal,self.scenario,self.factor = journal,scenario,factor
        self.fill_callback = fill_callback or (lambda *args:None)
        self.position_callback = position_callback
        self.emergency_taker = emergency_taker
        if queue_model not in ("conservative","proportional_cancel"):
            raise ValueError("Unknown queue assumption")
        self.queue_model = queue_model
        for venue,values in latencies.items():
            for key in ("entry_seconds","cancel_seconds"):
                x = values[key]
                if not math.isfinite(x) or not 0<=x<=30:
                    raise ValueError("Invalid venue-specific latency")
            if values.get("proxy_venue") not in (None,venue) or values.get("source_venue") not in (None,venue):
                raise ValueError("Cross-venue latency substitution is forbidden")
        self.orders,self.pending,self.books,self.quotes = {},{},{},{}
        self.sequence,self.serial,self.clock = 0,0,-float("inf")
        self.events,self.counts,self.taker_used,self.traded = [],{},{},{}
        self.markouts = Markouts(journal)
        if journal:
            journal.db.executescript("""
            CREATE TABLE IF NOT EXISTS pilot_orders(id TEXT PRIMARY KEY,scenario TEXT,venue TEXT,strategy TEXT,asset TEXT,side TEXT,role TEXT,qty TEXT,price TEXT,born REAL,arrival REAL,live REAL,cancel_request REAL,cancel_effective REAL,terminal REAL,status TEXT,leaves TEXT,queue TEXT,ioc INTEGER);
            CREATE TABLE IF NOT EXISTS pilot_fills(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id TEXT,scenario TEXT,venue TEXT,strategy TEXT,asset TEXT,t REAL,source_ns INTEGER,side TEXT,qty TEXT,price TEXT,fee TEXT,maker INTEGER,basis TEXT);
            CREATE TABLE IF NOT EXISTS pilot_states(t REAL,wall_ns INTEGER,scenario TEXT,venue TEXT,strategy TEXT,asset TEXT,inventory TEXT,cash TEXT,fees TEXT,mid TEXT,pnl_ex_funding TEXT);
            CREATE TABLE IF NOT EXISTS pilot_lifecycle(id TEXT,t REAL,status TEXT,reason TEXT,queue_model TEXT);
            """)

    def count(self,name,venue,strategy):
        key = venue+":"+strategy+":"+name
        self.counts[key] = self.counts.get(key,0)+1

    def schedule(self,t,kind,payload):
        if t<self.clock:
            raise ValueError("Out-of-order receipt event")
        self.serial += 1
        heapq.heappush(self.events,(t,self.PRIORITY[kind],self.serial,kind,payload))

    def submit_intent(self,venue,strategy,asset,intent,now,ioc=False,signal_snapshot=None):
        if ioc and not self.emergency_taker:
            raise ValueError("Emergency taker is disabled")
        if intent.side not in ("Buy","Sell") or intent.quantity<=0 or intent.price<=0:
            raise ValueError("Invalid order intent")
        i = self.instruments[venue][asset]
        if intent.price%i.tick or intent.quantity%i.lot:
            raise ValueError("Order price/size off exchange grid")
        self.sequence += 1
        identifier = f"paper-{self.scenario}-{self.sequence}"
        arrival = now+self.factor*self.latencies[venue]["entry_seconds"]
        order = PaperOrder(identifier,venue,strategy,asset,intent,now,arrival,intent.quantity,ioc,
                           sent_ts=now,signal_snapshot=signal_snapshot)
        if self.position_callback:order.inventory_snapshot=self.position_callback(order)
        self.orders[identifier] = self.pending[identifier] = order
        self.count("intents",venue,strategy)
        if journal := self.journal:
            journal.db.execute("INSERT INTO pilot_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (identifier,self.scenario,venue,strategy,asset,intent.side,intent.role,str(intent.quantity),str(intent.price),now,arrival,None,None,None,None,order.status,str(order.leaves),None,int(ioc)))
        self.schedule(arrival,"ARRIVE",order)
        return order

    def cancel_intent(self,order,now,reason="POLICY_CANCEL"):
        if order.done or order.cancel_at is not None:
            return
        order.cancel_requested_ts,order.reason_cancel = now,reason
        order.cancel_at = max(order.arrival,now+self.factor*self.latencies[order.venue]["cancel_seconds"])
        order.status = "CANCEL_PENDING"
        self.count("cancel_intents",order.venue,order.strategy)
        if self.journal:
            self.journal.db.execute("UPDATE pilot_orders SET cancel_request=?,cancel_effective=? WHERE id=?",(now,order.cancel_at,order.identifier))
        self.schedule(order.cancel_at,"CANCEL",order)

    def persist(self,order,now,reason=""):
        if order.done:
            self.pending.pop(order.identifier,None)
            order.active = False
        if self.journal:
            self.journal.db.execute("UPDATE pilot_orders SET status=?,leaves=?,queue=?,terminal=? WHERE id=?",
                (order.status,str(order.leaves),str(order.queue) if order.queue is not None else None,now if order.done else None,order.identifier))
            self.journal.db.execute("INSERT INTO pilot_lifecycle VALUES(?,?,?,?,?)",(order.identifier,now,order.status,reason,self.queue_model))
        if len(self.orders)>5000:
            for key,o in list(self.orders.items()):
                if o.done:
                    self.orders.pop(key)
                    if len(self.orders)<=4000:
                        break

    def queue_at(self,order):
        key = order.venue,order.asset
        book,q = self.books.get(key),self.quotes.get(key)
        if not book or not q:
            return None
        price = order.intent.price
        levels = book.bids if order.intent.side=="Buy" else book.asks
        if order.intent.side=="Buy" and q.bid<price<q.ask or order.intent.side=="Sell" and q.bid<price<q.ask:
            return D(0)  # Newly improved maker price; no public queue at this level.
        if levels and min(levels)<=price<=max(levels):
            own = sum((o.leaves for o in self.pending.values() if o is not order and o.active
                       and (o.venue,o.asset,o.intent.side,o.intent.price)==(order.venue,order.asset,order.intent.side,price)),D(0))
            return levels.get(price,D(0))+own
        return None  # Outside observed depth cannot be called an empty queue.

    def on_book(self,venue,asset,quote,book,reset=False):
        frozen = SimpleNamespace(bids=dict(book.bids),asks=dict(book.asks),sequence=book.sequence)
        self.schedule(quote.received,"BOOK",(venue,asset,quote,frozen,reset))
        self.advance(quote.received)

    def on_trade(self,venue,asset,side,quantity,price,now,source):
        self.schedule(now,"TRADE",(venue,asset,side,quantity,price,source))
        self.advance(now)

    def on_gap(self,venue,now):
        self.schedule(now,"GAP",venue)
        self.advance(now)

    def advance(self,now):
        if now<self.clock:
            raise ValueError("Clock regression")
        while self.events and self.events[0][0]<=now:
            t,priority,serial,kind,payload = heapq.heappop(self.events)
            self.clock = t
            if kind=="BOOK":
                self._book(*payload)
            elif kind=="TRADE":
                self._trade(*payload,now=t)
            elif kind=="GAP":
                self.markouts.gap(payload)
                for key in list(self.quotes):
                    if key[0]==payload:
                        self.quotes.pop(key);self.books.pop(key,None)
                for order in list(self.pending.values()):
                    if order.venue==payload:
                        order.queue=None
                        self.cancel_intent(order,t,"FEED_GAP")
            elif kind=="ARRIVE" and not payload.done:
                self._arrive(payload,t)
            elif kind=="CANCEL" and not payload.done:
                payload.done,payload.status = True,"CANCELLED"
                self.persist(payload,t,payload.reason_cancel)
        self.clock = now

    def _book(self,venue,asset,q,book,reset):
        key = venue,asset
        previous = self.books.get(key)
        self.quotes[key],self.books[key] = q,book
        for o in list(self.pending.values()):
            if (o.venue,o.asset)!=key or not o.active or o.done:
                continue
            observed = self.queue_at(o)
            if reset:
                # A reconnect loses queue evidence. Cancel rather than infer old rank.
                o.queue = None
                self.cancel_intent(o,q.received,"SNAPSHOT_QUEUE_UNKNOWN")
            elif o.queue is None:
                o.queue = observed
            elif self.queue_model=="proportional_cancel" and previous:
                old = previous.bids if o.intent.side=="Buy" else previous.asks
                new = book.bids if o.intent.side=="Buy" else book.asks
                p = o.intent.price
                if p in old and p in new and old[p]>0:
                    executed = self.traded.get((key,o.intent.side,p),D(0))
                    decrease = max(D(0),old[p]-new[p]-executed)
                    credit = decrease*min(D(1),o.queue/old[p])
                    o.queue = max(D(0),o.queue-credit)
        self.traded = {k:v for k,v in self.traded.items() if k[0]!=key}
        self.markouts.observe(venue,asset,q)

    def _arrive(self,o,now):
        q = self.quotes.get((o.venue,o.asset))
        if not q or not q.valid() or now-q.received>3:
            o.done,o.status = True,"REJECTED_STALE"
            self.count("stale_arrival",o.venue,o.strategy);self.persist(o,now);return
        o.source_floor,o.ack_ts = q.source_ns,now
        if o.ioc:
            self.sweep(o,now)
            o.done,o.status = True,"FILLED" if o.leaves==0 else "IOC_PARTIAL" if o.leaves<o.intent.quantity else "IOC_NO_FILL"
            self.persist(o,now);return
        if o.intent.price>=q.ask if o.intent.side=="Buy" else o.intent.price<=q.bid:
            o.done,o.status = True,"POST_ONLY_REJECTED"
            self.count("post_only_rejected",o.venue,o.strategy);self.persist(o,now);return
        o.active,o.active_ts = True,now
        o.status = "CANCEL_PENDING" if o.cancel_at is not None else "ACTIVE"
        o.queue = self.queue_at(o)
        self.count("active",o.venue,o.strategy)
        if self.journal:
            self.journal.db.execute("UPDATE pilot_orders SET live=? WHERE id=?",(now,o.identifier))
        self.persist(o,now,"ARRIVAL")

    def fill(self,o,quantity,price,now,source,maker,basis):
        if o.intent.reduce_only:
            inventory = self.position_callback(o) if self.position_callback else D(0)
            allowed = inventory if o.intent.side=="Sell" else -inventory
            quantity = min(quantity,max(D(0),allowed))
        quantity = min(o.leaves,self.instruments[o.venue][o.asset].floor(quantity))
        if quantity<=0:
            return D(0)
        i = self.instruments[o.venue][o.asset]
        fee = quantity*price*(i.maker_fee if maker else i.taker_fee)
        o.leaves -= quantity
        if o.first_fill_ts is None:
            o.first_fill_ts = now
        self.count("fills",o.venue,o.strategy)
        if self.journal:
            self.journal.db.execute("INSERT INTO pilot_fills VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (o.identifier,self.scenario,o.venue,o.strategy,o.asset,now,source,o.intent.side,str(quantity),str(price),str(fee),int(maker),basis))
        self.markouts.add(o,quantity,price,fee,now)
        self.fill_callback(o,quantity,price,fee,now)
        o.done = o.leaves==0
        o.status = "FILLED" if o.done else "CANCEL_PENDING" if o.cancel_at is not None else "PARTIAL"
        self.persist(o,now,basis)
        return quantity

    def sweep(self,o,now):
        key = o.venue,o.asset
        book,q = self.books[key],self.quotes[key]
        levels = book.asks if o.intent.side=="Buy" else book.bids
        for p,size in sorted(levels.items(),reverse=o.intent.side=="Sell"):
            if o.intent.side=="Buy" and p>o.intent.price or o.intent.side=="Sell" and p<o.intent.price:
                break
            token = key,book.sequence,o.intent.side,p
            available = max(D(0),size-self.taker_used.get(token,D(0)))
            used = self.fill(o,min(available,o.leaves),p,now,q.source_ns,False,"emergency_arrival_sweep")
            self.taker_used[token] = self.taker_used.get(token,D(0))+used
            if not o.leaves:
                break
        if len(self.taker_used)>10000:
            self.taker_used = {k:v for k,v in self.taker_used.items() if k[0]!=key or k[1]==book.sequence}

    def _trade(self,venue,asset,side,quantity,price,source,now):
        if side not in ("Buy","Sell") or quantity<=0:
            return
        key = venue,asset
        q = self.quotes.get(key)
        if q is None or not q.valid() or now-q.received>3:
            return
        maker_side = "Sell" if side=="Buy" else "Buy"
        token = key,maker_side,price
        self.traded[token] = self.traded.get(token,D(0))+quantity
        candidates = [o for o in self.pending.values() if o.active and not o.done and
            (o.venue,o.asset,o.intent.side)==(venue,asset,maker_side)]
        candidates.sort(key=lambda o:(o.intent.price if side=="Buy" else -o.intent.price,o.arrival,o.identifier))
        available_volume = quantity
        for o in candidates:
            if source and o.source_floor and source<o.source_floor:
                self.count("older_source_trade_skipped",venue,o.strategy);continue
            reaches = price<=o.intent.price if o.intent.side=="Buy" else price>=o.intent.price
            if not reaches or o.queue is None:
                continue
            if price!=o.intent.price:
                o.queue,available,basis = D(0),quantity,"strict_trade_through_observed_queue"
            else:
                ahead = min(o.queue,quantity)
                o.queue -= ahead
                available,basis = quantity-ahead,"same_price_volume_after_displayed_queue"
            used = self.fill(o,min(available,available_volume),o.intent.price,now,source,True,basis)
            available_volume -= used
            self.persist(o,now)
