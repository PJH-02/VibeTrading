from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

D = Decimal
ACTIVE = {"PENDING_NEW", "New", "PartiallyFilled", "PENDING_CANCEL", "UNKNOWN"}
TERMINAL = {"Filled", "Cancelled", "Rejected", "Deactivated", "PartiallyFilledCanceled"}


@dataclass(frozen=True)
class Instrument:
    symbol: str
    tick: Decimal
    lot: Decimal
    min_qty: Decimal
    min_notional: Decimal
    max_qty: Decimal
    multiplier: Decimal = D(1)

    @classmethod
    def from_api(cls, row: dict) -> "Instrument":
        if row.get("contractType") != "LinearPerpetual" or row.get("settleCoin") != "USDT" or row.get("status") != "Trading":
            raise ValueError("Instrument must be a trading USDT linear perpetual")
        q = row["lotSizeFilter"]
        inst = cls(row["symbol"], D(row["priceFilter"]["tickSize"]), D(q["qtyStep"]),
                   D(q["minOrderQty"]), D(q.get("minNotionalValue", "0")),
                   min(D(q["maxOrderQty"]), D(q["maxMktOrderQty"])))
        if inst.tick <= 0 or inst.lot <= 0 or inst.min_qty <= 0:
            raise ValueError("Invalid instrument units")
        return inst

    def ticks(self, price: str | Decimal, *, ceil: bool = False) -> int:
        value = D(price) / self.tick
        return int(value.to_integral_value(rounding=ROUND_CEILING if ceil else ROUND_FLOOR))

    def lots(self, qty: str | Decimal) -> int:
        return int((D(qty) / self.lot).to_integral_value(rounding=ROUND_FLOOR))

    def price(self, ticks: int) -> Decimal:
        return self.tick * ticks

    def qty(self, lots: int) -> Decimal:
        return self.lot * lots

    def valid_order(self, ticks: int, lots: int, *, reduction: bool = False) -> bool:
        qty = self.qty(lots)
        return ticks > 0 and qty > 0 and qty <= self.max_qty and (
            reduction or (qty >= self.min_qty and qty * self.price(ticks) >= self.min_notional)
        )


@dataclass
class Order:
    client_id: str
    side: str
    role: str
    price: int
    qty: int
    created: float
    deadline: float
    target: int = 0
    stop: int = 0
    status: str = "PENDING_NEW"
    filled: int = 0
    reported_filled: int = 0
    exchange_id: str = ""
    live_at: float | None = None
    cancel_at: float | None = None

    @property
    def leaves(self) -> int:
        # A terminal order notification can precede its execution notification.
        # Keep this discrepancy reserved until fills are reconciled.
        if self.status in TERMINAL:
            return max(0, self.reported_filled - self.filled)
        return max(0, self.qty - self.filled)


@dataclass
class Inventory:
    net: int = 0
    average: Decimal = D(0)  # integer ticks represented exactly
    oldest_fill: float | None = None
    deadline: float | None = None
    target: int = 0
    stop: int = 0
    realized_ticks_lots: Decimal = D(0)
    fees: Decimal = D(0)
    fill_ids: set[str] = field(default_factory=set)

    def fill(self, exec_id: str, side: str, qty: int, price: int, fee: Decimal,
             now: float, *, deadline: float, target: int, stop: int) -> bool:
        if exec_id in self.fill_ids:
            return False
        if qty <= 0:
            raise ValueError("Fill quantity must be positive")
        self.fill_ids.add(exec_id)
        change = qty if side == "Buy" else -qty
        previous = self.net
        if previous == 0 or previous * change > 0:
            self.average = (self.average * abs(previous) + D(price) * qty) / (abs(previous) + qty)
        else:
            closing = min(abs(previous), qty)
            self.realized_ticks_lots += (D(price) - self.average) * closing * (1 if previous > 0 else -1)
            if qty > abs(previous):
                self.average = D(price)
        self.net += change
        self.fees += fee
        if previous == 0 and self.net:
            self.oldest_fill, self.deadline = now, deadline
            self.target, self.stop = target, stop
        elif self.net and previous * self.net > 0:
            self.deadline = min(self.deadline or deadline, deadline)
            if target:
                self.target = min(self.target, target) if self.net > 0 else max(self.target, target)
            if stop:
                self.stop = max(self.stop, stop) if self.net > 0 else min(self.stop, stop)
        elif self.net == 0:
            self.average = D(0)
            self.oldest_fill = self.deadline = None
            self.target = self.stop = 0
        else:
            raise RuntimeError("Unexpected inventory reversal")
        return True


def worst_inventory(inventory: Inventory, orders: list[Order]) -> tuple[int, int]:
    buys = sum(o.leaves for o in orders if o.role == "ENTRY" and o.side == "Buy")
    sells = sum(o.leaves for o in orders if o.role == "ENTRY" and o.side == "Sell")
    return inventory.net + buys, inventory.net - sells
