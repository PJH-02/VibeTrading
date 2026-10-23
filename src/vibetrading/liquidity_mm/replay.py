from __future__ import annotations

import heapq
import json
import sqlite3
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

from .config import Config
from .domain import Instrument, Inventory, Order
from .market import DataFault, Market
from .strategy import Strategy


@dataclass
class QueueOrder:
    order: Order
    active_at: float
    ahead: int = 0
    activated: bool = False
    cancel_effective: float | None = None


class FifoSimulator:
    """No touch fills, no cancellation credit, no reuse of one trade's volume."""

    def __init__(self) -> None:
        self.orders: list[QueueOrder] = []

    def add(self, order: Order, now: float, latency: float) -> None:
        self.orders.append(QueueOrder(order, now + latency))

    def cancel(self, order: Order, now: float, latency: float) -> None:
        row = next(r for r in self.orders if r.order is order)
        if row.cancel_effective is None:
            row.cancel_effective = now + latency
            order.status = "PENDING_CANCEL"

    def advance(self, now: float, market: Market) -> None:
        for row in self.orders:
            o = row.order
            if o.status in {"Filled", "Cancelled", "Rejected"}:
                continue
            if row.cancel_effective is not None and now >= row.cancel_effective:
                o.status = "Cancelled"
                continue
            if not row.activated and now >= row.active_at and market.ready:
                if (o.side == "Buy" and o.price >= market.ask) or (o.side == "Sell" and o.price <= market.bid):
                    o.status = "Rejected"
                    continue
                row.activated = True
                row.ahead = (market.bids if o.side == "Buy" else market.asks).get(o.price, 0)
                # Visible same-price queue can include previous synthetic orders
                # only once. Those orders are filled first by time priority below.
                o.live_at = row.active_at
                if o.status != "PENDING_CANCEL":
                    o.status = "New"

    def trade(self, aggressor: str, price: int, qty: int, now: float) -> list[tuple[Order, int]]:
        if aggressor not in {"Buy", "Sell"}:
            return []
        own_side = "Sell" if aggressor == "Buy" else "Buy"
        candidates = [r for r in self.orders if r.activated and r.order.side == own_side
                      and r.order.price == price and r.order.status not in {"Filled", "Cancelled", "Rejected"}
                      and (r.cancel_effective is None or now < r.cancel_effective)]
        candidates.sort(key=lambda r: r.active_at)
        # Each order's ahead observation is a barrier in one shared trade path.
        # Reduce all barriers by the same public flow, then allocate leftover once.
        barriers = {id(r): r.ahead for r in candidates}
        for r in candidates:
            r.ahead = max(0, r.ahead - qty)
        fills, used = [], 0
        for row in candidates:
            available = max(0, qty - max(used, barriers[id(row)]))
            fill = min(row.order.qty - row.order.filled, available)
            if fill:
                used = max(used, barriers[id(row)]) + fill
                row.order.filled += fill
                row.order.reported_filled = row.order.filled
                if row.order.filled == row.order.qty:
                    row.order.status = "Filled"
                elif row.order.status != "PENDING_CANCEL":
                    row.order.status = "PartiallyFilled"
                fills.append((row.order, fill))
        return fills


def replay_journal(path: Path, config: Config, latency: float) -> dict:
    if latency not in (5, 10, 20):
        raise ValueError("Use the predeclared 5/10/20-second stresses")
    db = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        metadata = db.execute("SELECT body FROM events WHERE kind='metadata' ORDER BY id LIMIT 1").fetchone()
        if not metadata:
            raise ValueError("Journal has no instrument/fee metadata")
        meta = json.loads(metadata[0])
        inst = Instrument(**{k: Decimal(v) if k != "symbol" else v for k, v in meta["instrument"].items()})
        if inst.symbol != config.symbol:
            raise ValueError("Replay profile symbol differs from the recorded market")
        rows = db.execute("SELECT received_mono,received_wall_ms,body FROM events WHERE kind='public' ORDER BY id")
        market = Market(inst)
        replay_config = replace(config, entry_latency_seconds=latency, cancel_latency_seconds=latency, exit_latency_seconds=latency)
        strategy = Strategy(replay_config, market, Decimal(meta["maker_fee"]), Decimal(meta["taker_fee"]))
        inventory, sim = Inventory(), FifoSimulator()
        pending = []
        counter, quotes, fills, exits, decision_count, reasons = 0, 0, 0, 0, 0, {}
        started = last_decision = last_time = None
        cooldown = 0.0
        normal_exits, reduction_qty = 0, 0

        def deliver(until: float) -> None:
            nonlocal fills, exits, normal_exits, cooldown
            while pending and pending[0][0] <= until:
                receive, _, o, qty, price, actual = heapq.heappop(pending)
                fee = inst.qty(qty) * inst.price(price) * strategy.maker_fee
                inventory.fill(f"sim-{receive}-{o.client_id}-{actual}", o.side, qty, price, fee, actual,
                               deadline=o.deadline, target=o.target, stop=o.stop)
                if o.role == "ENTRY":
                    fills += 1
                    side = "Sell" if inventory.net > 0 else "Buy"
                    reserved = sum(r.order.leaves for r in sim.orders if r.order.role == "EXIT")
                    exit_qty = abs(inventory.net) - reserved
                    if exit_qty > 0:
                        exit_order = Order(f"exit-{len(sim.orders)}", side, "EXIT", inventory.target, exit_qty,
                                           receive, inventory.deadline, inventory.target, inventory.stop)
                        sim.add(exit_order, receive, latency)
                else:
                    exits += 1
                    if inventory.net == 0:
                        normal_exits += 1
                        cooldown = receive + config.cooldown_seconds
                        market.episode = None

        for now, wall, body in rows:
            if last_time is not None and now < last_time:
                raise ValueError("Recorded receive-time order regressed")
            last_time = now
            if started is None:
                started = now
            deliver(now)
            sim.advance(now, market)
            msg = json.loads(body)
            topic = msg.get("topic", "")
            try:
                if topic.startswith("orderbook."):
                    market.book(msg, now)
                elif topic.startswith("publicTrade."):
                    for trade in msg["data"]:
                        if market.trade(trade, now):
                            price, qty = inst.ticks(trade["p"]), inst.lots(trade["v"])
                            for order, executed in sim.trade(trade.get("S", "UNKNOWN"), price, qty, now):
                                counter += 1
                                heapq.heappush(pending, (now + config.private_latency_seconds, counter, order, executed, price, now))
            except DataFault as exc:
                raise ValueError("Replay data fault: " + str(exc)) from None
            market.advance(now)
            for row in sim.orders:
                order = row.order
                if order.role == "ENTRY" and order.leaves and now >= min(order.deadline, row.active_at + config.entry_ttl_seconds):
                    sim.cancel(order, now, latency)
            if inventory.net and (now >= (inventory.deadline or now) or now >= (inventory.oldest_fill or now) + config.max_holding_seconds):
                # Conservative executable book valuation for terminal/reduction;
                # this simplified replay does not certify a live liquidation fill.
                break
            if now - started < config.warmup_seconds or now < cooldown:
                continue
            if last_decision is not None and now - last_decision < config.evaluation_seconds:
                continue
            last_decision = now
            market.evaluate_episode(now)
            orders = [r.order for r in sim.orders]
            decision = strategy.decide(now, inventory, orders, Decimal(meta["equity"]), Decimal(meta["available"]),
                                       int(meta["next_funding_ms"]), wall + int(meta["clock_offset_ms"]))
            decision_count += 1
            reasons[decision.reason] = reasons.get(decision.reason, 0) + 1
            if decision.approved and not inventory.net:
                quotes += 1
                o = Order(f"entry-{quotes}", decision.direction, "ENTRY", decision.entry, decision.qty, now,
                          decision.deadline, decision.target, decision.stop)
                sim.add(o, now, latency)
            elif decision.reason not in {"entry_already_reserved", "eligible"}:
                for row in sim.orders:
                    if row.order.role == "ENTRY" and row.order.leaves:
                        sim.cancel(row.order, now, latency)
        # Include executed but not-yet-delivered fills at recording end.
        deliver(float("inf"))
        pnl = inventory.realized_ticks_lots * inst.tick * inst.lot - inventory.fees
        if inventory.net:
            side_book = market.bids if inventory.net > 0 else market.asks
            remaining, cash = abs(inventory.net), Decimal(0)
            for price, size in sorted(side_book.items(), reverse=inventory.net > 0):
                taken = min(remaining, int(size * float(config.depth_haircut)))
                cash += inst.price(price) * inst.qty(taken)
                remaining -= taken
                if not remaining:
                    break
            if remaining:
                raise ValueError("Terminal liquidation exceeds observed stressed depth")
            reduction_qty = abs(inventory.net)
            avg = cash / inst.qty(reduction_qty)
            adverse = config.liquidation_slippage_bps / Decimal(10000)
            avg *= 1 - adverse if inventory.net > 0 else 1 + adverse
            pnl += (avg - inventory.average * inst.tick) * inst.qty(inventory.net)
            pnl -= cash * max(Decimal(0), strategy.taker_fee)
        return {"source": str(path.resolve()), "environment": "bybit-testnet-replay", "latency_seconds": latency,
                "decision_count": decision_count, "submitted_quotes": quotes, "entry_fill_events": fills,
                "exit_fill_events": exits, "normal_full_exits": normal_exits,
                "terminal_reduction_lots": reduction_qty, "net_pnl_usdt": str(pnl), "reasons": reasons,
                "queue_assumption": "FIFO, no cancellation credit, trade price exact, receive order",
                "statistically_validated": False,
                "limitations": ["receive-time market path; exact exchange interleaving unavailable",
                                "terminal reduction is a stressed executable-depth valuation",
                                "diagnostic replay is not an out-of-sample calibration certificate"]}
    finally:
        db.close()
