from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

D = Decimal


@dataclass(frozen=True)
class Quote:
    bid: Decimal
    ask: Decimal
    bid_qty: Decimal
    ask_qty: Decimal
    received: float
    source_ns: int
    sequence: int = 0

    @property
    def mid(self):
        return (self.bid + self.ask) / 2

    @property
    def spread(self):
        return self.ask - self.bid

    @property
    def depth(self):
        return (self.bid_qty + self.ask_qty) / 2

    def valid(self):
        return all(x.is_finite() for x in (self.bid,self.ask,self.bid_qty,self.ask_qty)) and self.bid > 0 and self.ask > self.bid and self.bid_qty >= 0 and self.ask_qty >= 0 and self.depth > 0


@dataclass(frozen=True)
class Signal:
    candidate: str
    x: Decimal
    fv: Decimal | None
    coefficient: Decimal | None
    window_seconds: float
    reason: str
    direction: int = 0
    strength: Decimal = D(0)
    state: str = "LEGACY_FV"
    ready: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Intent:
    side: str
    price: Decimal
    quantity: Decimal
    role: str
    reduce_only: bool


class ActivityWindow:
    """Quantity clock with a fractionally weighted boundary event."""

    def __init__(self, max_age: float = 120):
        self.events = deque()
        self.max_age = max_age

    def add(self, now: float, signed: Decimal, activity: Decimal):
        if activity > 0:
            self.events.append((now, signed, activity))
        while self.events and (self.events[0][0] < now - self.max_age or len(self.events) > 20000):
            self.events.popleft()

    def value(self, now: float, required: Decimal):
        total, signed, oldest = D(0), D(0), now
        if required <= 0:
            return None, 0
        for when, value, activity in reversed(self.events):
            if when < now - self.max_age:
                break
            used = min(activity, required - total)
            signed += value * used / activity
            total += used
            oldest = when
            if total >= required:
                return signed, now - oldest
        return None, now - oldest

    def reset(self):
        self.events.clear()


def quote_intents(signal: Signal, quote: Quote, *, cost: Decimal, tick: Decimal, lot: Decimal,
                  unit: Decimal, inventory: Decimal, max_inventory: Decimal,
                  new_exposure: bool = True) -> list[Intent]:
    """One common FV quote equation, directional pilot policy + true reductions."""
    fv = signal.fv if signal.fv is not None else quote.mid
    half = max(quote.spread / 2, cost)
    bid = min(quote.bid, ((fv - half) / tick).to_integral_value(rounding=ROUND_FLOOR) * tick)
    ask = max(quote.ask, ((fv + half) / tick).to_integral_value(rounding=ROUND_CEILING) * tick)
    result = []
    if inventory > 0:
        result.append(Intent("Sell", ask, inventory, "REDUCE", True))
    elif inventory < 0:
        result.append(Intent("Buy", bid, -inventory, "REDUCE", True))
    if new_exposure and signal.fv is not None and abs(fv - quote.mid) > cost:
        # Two-sided OPEN quotes require prior independent fill-quality validation.
        # The initial testnet policy quotes only the predicted profitable side.
        side = "Buy" if fv > quote.mid else "Sell"
        room = max_inventory - inventory if side == "Buy" else max_inventory + inventory
        qty = min(unit, max(D(0), room))
        qty = (qty / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
        if qty > 0:
            result.append(Intent(side, bid if side == "Buy" else ask, qty, "OPEN", False))
    return result
