"""Bounded fill-conditioned future observations; gaps/tails are missing labels."""
from collections import deque, OrderedDict
import heapq
from decimal import Decimal as D


class Markouts:
    def __init__(self, journal=None, horizons=(.1,.25,.5,1,2,5,10,30), tolerance=.5, observer=None):
        self.journal,self.horizons,self.tolerance = journal,horizons,tolerance
        self.pending = OrderedDict()
        self.heaps,self.serial,self.max_pending = {},0,20000
        self.observer = observer
        self.rows = deque(maxlen=10000)
        self.generations = {}
        if journal:
            journal.db.execute("CREATE TABLE IF NOT EXISTS maker_markouts(order_id TEXT,venue TEXT,asset TEXT,fill_t REAL,horizon REAL,observed_t REAL,qty TEXT,fill_price TEXT,mid TEXT,markout_bps REAL,fee_bps REAL,status TEXT)")

    def add(self, order, qty, price, fee, now):
        if order.ioc:
            return
        key = order.venue,order.asset
        generation = self.generations.get(key,0)
        heap = self.heaps.setdefault(key,[])
        for horizon in self.horizons:
            if len(self.pending)==self.max_pending:
                _,row=self.pending.popitem(last=False)
                self._record(row,None,None,"CAPACITY_CENSORED")
            self.serial+=1
            self.pending[self.serial]=(order.identifier,key,order.intent.side,qty,price,fee,now,horizon,generation)
            heapq.heappush(heap,(now+horizon,self.serial))
        # Censored rows can leave lazy heap entries; compact with a fixed bound.
        if len(heap)>2*self.max_pending:
            heap[:]=[(due,n) for due,n in heap if n in self.pending]
            heapq.heapify(heap)

    def gap(self, venue):
        for key in set(self.heaps) | set(self.generations):
            if key[0]==venue:
                self.generations[key] = self.generations.get(key,0)+1

    def observe(self, venue, asset, quote):
        key = venue,asset
        heap=self.heaps.get(key,[])
        while heap and heap[0][0]<=quote.received:
            _,n=heapq.heappop(heap)
            row=self.pending.pop(n,None)
            if row is None:continue
            valid = row[8]==self.generations.get(key,0) and quote.valid() and quote.received-row[6]-row[7]<=self.tolerance
            self._record(row,quote.mid if valid else None,quote.received,"OBSERVED" if valid else "GAP_OR_LATE")

    def finish(self):
        while self.pending:
            _,row=self.pending.popitem(last=False)
            self._record(row,None,None,"TAIL_CENSORED")
        self.heaps.clear()

    def _record(self, row, mid, observed, status):
        identifier,key,side,qty,price,fee,t,h,generation = row
        mo = float((mid/price-1 if side=="Buy" else price/mid-1)*10000) if mid else None
        fee_bp = float(fee/(qty*price)*10000)
        values = (identifier,*key,t,h,observed,str(qty),str(price),str(mid) if mid else None,mo,fee_bp,status)
        self.rows.append(values)
        if self.observer:self.observer(values)
        if self.journal:
            self.journal.db.execute("INSERT INTO maker_markouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",values)
