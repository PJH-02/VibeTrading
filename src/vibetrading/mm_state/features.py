"""Receipt-time microstructure, bounded 100 ms buckets; L2 changes are net proxies."""
from collections import deque
from dataclasses import dataclass
import math

from vibetrading.fv_mm.types import Signal
from decimal import Decimal as D


@dataclass(frozen=True)
class Features:
    ready: bool = False
    ofi: float = 0
    obi: float = 0
    l2_obi: float | None = None
    trade_imbalance: float = 0
    buy: float = 0
    sell: float = 0
    unknown: float = 0
    bid_refill: float = 0
    ask_refill: float = 0
    bid_depletion: float = 0
    ask_depletion: float = 0
    liquidity_seconds: float | None = None
    rv_bps: float = 0
    spread_bps: float = 0
    bid_gap_ticks: float | None = None
    ask_gap_ticks: float | None = None
    book_rate: float = 0
    trade_rate: float = 0
    reason: str = "WARMUP"


class Microstructure:
    # ofi, buy, sell, unknown, bid-add, ask-add, bid-remove, ask-remove,
    # stock*time, depth*time, squared mid return, books, trades, live time
    def __init__(self, window=5., tick=.01, stale=3.):
        if not .1 <= window <= 60 or tick <= 0:
            raise ValueError("Window must be 0.1..60s; positive tick required")
        self.window, self.tick, self.stale = float(window), float(tick), stale
        self.buckets = deque()
        self.previous = None
        self.previous_levels = None
        self.started = None
        self.cursor = None
        self.last_event = None
        self.next_gaps = (None, None)
        self.l2_obi = None

    def reset(self):
        self.buckets.clear()
        self.previous = self.previous_levels = None
        self.cursor = self.started = self.last_event = None
        self.l2_obi = None
        self.next_gaps = (None, None)

    def _bucket(self, t):
        index = math.floor(t * 10 + 1e-7)
        if not self.buckets or self.buckets[-1][0] != index:
            self.buckets.append((index, [0.] * 14))
        cutoff = index - math.ceil(self.window * 10)
        while self.buckets and self.buckets[0][0] < cutoff:
            self.buckets.popleft()
        return self.buckets[-1][1]

    def advance(self, now):
        if self.cursor is not None and now < self.cursor:
            self.reset()
            return False
        if self.previous is not None and now - self.previous.received > self.stale:
            self.reset()
            return False
        if self.cursor is None:
            self.cursor = now
        q = self.previous
        while self.cursor < now:
            end = min(now, (math.floor(self.cursor * 10 + 1e-7) + 1) / 10)
            if end <= self.cursor:
                end = min(now, self.cursor + .1)
            if q:
                acc = self._bucket(self.cursor)
                dt = end - self.cursor
                acc[8] += float(q.bid*q.bid_qty + q.ask*q.ask_qty) * dt
                acc[9] += float(q.depth) * dt
                acc[13] += dt
            self.cursor = end
        self._bucket(now)
        return True

    def book(self, q, reset=False, book=None):
        if reset or not q.valid():
            self.reset()
        if not q.valid():
            return
        self.advance(q.received)
        old = self.previous
        acc = self._bucket(q.received)
        if old:
            u = (q.bid_qty if q.bid >= old.bid else D(0)) - (old.bid_qty if q.bid <= old.bid else D(0))
            v = (q.ask_qty if q.ask <= old.ask else D(0)) - (old.ask_qty if q.ask >= old.ask else D(0))
            acc[0] += float(u-v)
            acc[10] += (float(q.mid/old.mid)-1)**2 * 1e8
        if book is not None:
            bids = dict(sorted(book.bids.items(), reverse=True)[:10])
            asks = dict(sorted(book.asks.items())[:10])
            if bids and asks:
                total_b, total_a = float(sum(bids.values())), float(sum(asks.values()))
                self.l2_obi = (total_b-total_a)/(total_b+total_a) if total_b+total_a else None
                b, a = list(bids), list(asks)
                self.next_gaps = ((float(b[0]-b[1])/self.tick if len(b)>1 else None),
                                  (float(a[1]-a[0])/self.tick if len(a)>1 else None))
            levels = (bids, asks)
        else:
            levels = ({q.bid:q.bid_qty}, {q.ask:q.ask_qty})
            self.l2_obi = None
            self.next_gaps = (None, None)
        if self.previous_levels:
            for side in (0, 1):
                before, after = self.previous_levels[side], levels[side]
                for p in before.keys() & after.keys():
                    change = float(after[p]-before[p])
                    acc[4+side] += max(0., change)
                    acc[6+side] += max(0., -change)
        acc[11] += 1
        self.previous, self.previous_levels = q, levels
        self.last_event = q.received
        if self.started is None:
            self.started = q.received

    def trade(self, side, quantity, now, price=None):
        if not quantity.is_finite() or quantity <= 0 or price is not None and (not price.is_finite() or price<=0) or self.previous is None or not self.advance(now):
            return
        q = self.previous
        acc = self._bucket(now)
        acc[1 if side=="Buy" else 2 if side=="Sell" else 3] += float(quantity)
        # Preserve actual traded notional separately from the state stock integral.
        acc[12] += float(quantity * (price if price is not None else q.mid))

    def snapshot(self, now):
        if not self.advance(now) or not self.previous:
            return Features(reason="STALE_OR_INVALID_BOOK")
        q = self.previous
        acc = [0.] * 14
        for index, values in self.buckets:
            # Include only complete buckets inside the causal window. The earliest
            # boundary bucket is omitted, so no pre-window flow is attributed.
            if index/10 < now-self.window-1e-7:
                continue
            for i, value in enumerate(values):
                acc[i] += value
        duration = acc[13]
        ready = self.started is not None and now-self.started >= self.window and duration >= self.window-.11
        depth = acc[9]/duration if duration else float(q.depth)
        total = acc[1]+acc[2]
        return Features(ready, math.asinh(acc[0]/max(depth, 1e-12)),
            float((q.bid_qty-q.ask_qty)/(q.bid_qty+q.ask_qty)), self.l2_obi,
            (acc[1]-acc[2])/total if total else 0., acc[1], acc[2], acc[3],
            acc[4], acc[5], acc[6], acc[7], acc[8]/acc[12] if acc[12]>0 else None,
            math.sqrt(acc[10]), float(q.spread/q.mid)*1e4, *self.next_gaps,
            acc[11]/max(duration, .1), total/max(duration, .1), "READY" if ready else "WARMUP")


class StateStrategy:
    """Common compatibility surface; state signals never invent an FV estimate."""
    def __init__(self, calibration=None, window_seconds=5., tick=.01, **kwargs):
        self.calibration = calibration or {}
        self.engine = Microstructure(window_seconds, tick)
        self.coefficient = None  # Historical callers may set it; state mode ignores it.
        self.last_state = "NEUTRAL"

    def on_book(self, quote, reset=False, book=None):
        if not getattr(self,"external_features",False):self.engine.book(quote, reset, book)

    def on_trade(self, side, quantity, received, price=None):
        if not getattr(self,"external_features",False):self.engine.trade(side, quantity, received, price)

    def make_signal(self, sid, f, score, state, reason, **extra):
        from dataclasses import asdict
        thresholds=extra.get("risk_thresholds",{})
        magnitude=("FLAT" if f.rv_bps==0 else "ACTIVE" if f.rv_bps>float(thresholds.get("rv_high",4)) else "SMALL")
        meta = {"features":asdict(f),"magnitude_state":magnitude,"magnitude_basis":"observed_window_rv_not_a_future_price_forecast", **extra}
        return Signal(sid, D(str(score)), None, None, self.engine.window, reason,
                      direction=1 if score>0 else -1 if score<0 else 0,
                      strength=D(str(min(1.,abs(score)))), state=state,
                      ready=f.ready, metadata=meta)

    def order_intents(self, signal, quote, **risk):
        from .policy import state_quote_intents
        return state_quote_intents(signal, quote, **risk)


def direction_score(value, calibration, key, fallback):
    threshold = max(float(calibration.get(key, fallback)), 1e-8)
    return max(-1., min(1., value/threshold)) if abs(value)>=threshold*.4 else 0.


def legacy_instance(module, coefficient, mode, kwargs):
    if mode == "legacy_fv" or (mode is None and coefficient is not None):
        import importlib
        return importlib.import_module("strategies.legacy.fair_value."+module).Strategy(coefficient=coefficient, **kwargs)
    if mode not in (None, "state"):
        raise ValueError("Unknown strategy mode")
