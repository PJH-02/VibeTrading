from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from statistics import median
from decimal import Decimal

from .domain import Instrument


class DataFault(RuntimeError):
    pass


@dataclass
class Bin:
    buy: int = 0
    sell: int = 0
    unknown: int = 0
    trades: int = 0
    supply_ask: float = 0
    supply_bid: float = 0
    consumed_ask: int = 0
    consumed_bid: int = 0


@dataclass
class Episode:
    direction: str
    start: float
    weak_count: int = 0
    observations: int = 0


class Survival:
    """Direction/age-specific Beta(1,1) discrete hazards, no hindsight updates."""

    def __init__(self) -> None:
        self.risk = {s: [0] * 5 for s in ("Buy", "Sell")}
        self.ends = {s: [0] * 5 for s in ("Buy", "Sell")}
        self.frozen = False

    @staticmethod
    def bucket(age: float) -> int:
        return sum(age >= edge for edge in (30, 60, 120, 300))

    def observe(self, direction: str, age: float, ended: bool) -> None:
        if self.frozen:
            return
        k = self.bucket(age)
        self.risk[direction][k] += 1
        self.ends[direction][k] += int(ended)

    def probability(self, direction: str, age: float, future: float) -> float:
        result, elapsed = 1.0, 0.0
        while elapsed < future:
            step = min(5, future - elapsed)
            k = self.bucket(age + elapsed)
            hazard = (1 + self.ends[direction][k]) / (2 + self.risk[direction][k])
            result *= (1 - hazard) ** (step / 5)
            elapsed += step
        return result


class Market:
    def __init__(self, instrument: Instrument) -> None:
        self.instrument = instrument
        self.bids: dict[int, int] = {}
        self.asks: dict[int, int] = {}
        self.update_id = self.sequence = 0
        self.ready = False
        self.book_received = self.trade_received = 0.0
        self.book_source_ms = 0
        self.bins: dict[int, Bin] = defaultdict(Bin)
        self.trade_ids: dict[str, float] = {}
        self.rates = {s: {h: 0.0 for h in (10, 30, 120)} for s in ("Buy", "Sell")}
        self.rate_time: float | None = None
        self.rate_volumes = {"Buy": 0, "Sell": 0}
        self.episode: Episode | None = None
        self.survival = Survival()
        self.neutral = {s: deque(maxlen=360) for s in ("Buy", "Sell")}
        self.last_evaluation: int | None = None
        self.align_time: float | None = None
        self.align_source_ms = 0
        self.previous_bids: dict[int, int] = {}
        self.previous_asks: dict[int, int] = {}
        self.previous_own_depth: dict[tuple[str, int], int] = {}
        self.consumption = {"Buy": defaultdict(int), "Sell": defaultdict(int)}
        self.recoveries = {s: deque() for s in ("Buy", "Sell")}
        self.shocks: list[tuple[str, float, int, int]] = []
        self.late_trades = 0
        self.observed_trades = 0

    @property
    def bid(self) -> int:
        return max(self.bids, default=0)

    @property
    def ask(self) -> int:
        return min(self.asks, default=0)

    def book(self, msg: dict, now: float) -> bool:
        row = msg["data"]
        update, seq = int(row["u"]), int(row["seq"])
        snapshot = msg["type"] == "snapshot" or update == 1
        if not snapshot:
            if not self.ready:
                raise DataFault("delta_before_snapshot")
            if update == self.update_id and seq == self.sequence:
                return False
            # Bybit cross sequence is NOT contiguous for one symbol/depth.
            if update <= self.update_id or seq <= self.sequence:
                self.ready = False
                raise DataFault("orderbook_sequence_regression")
        bids = {} if snapshot else self.bids.copy()
        asks = {} if snapshot else self.asks.copy()
        for dest, key in ((bids, "b"), (asks, "a")):
            for p, q in row.get(key, []):
                ticks, lots = self.instrument.ticks(p), self.instrument.lots(q)
                if self.instrument.price(ticks) != Decimal(p) or self.instrument.qty(lots) != Decimal(q):
                    raise DataFault("off_grid_book")
                if ticks <= 0 or lots < 0:
                    raise DataFault("invalid_book_value")
                if lots == 0:
                    dest.pop(ticks, None)
                else:
                    dest[ticks] = lots
        if not bids or not asks or max(bids) >= min(asks):
            self.ready = False
            raise DataFault("empty_or_crossed_book")
        self.bids, self.asks = bids, asks
        self.update_id, self.sequence = update, seq
        self.book_received, self.book_source_ms = now, int(msg.get("cts", msg["ts"]))
        self.ready = True
        if snapshot:
            self.previous_bids, self.previous_asks = bids.copy(), asks.copy()
            self.align_time, self.align_source_ms = now, self.book_source_ms
            self.consumption = {"Buy": defaultdict(int), "Sell": defaultdict(int)}
        return True

    def trade(self, row: dict, now: float) -> bool:
        trade_id = str(row["i"])
        if trade_id in self.trade_ids:
            return False
        self.trade_ids[trade_id] = now
        side, qty, price = row.get("S", "UNKNOWN"), self.instrument.lots(row["v"]), self.instrument.ticks(row["p"])
        if qty <= 0:
            raise DataFault("invalid_trade_quantity")
        b = self.bins[int(now // 5)]
        if side == "Buy":
            b.buy += qty
        elif side == "Sell":
            b.sell += qty
        else:
            b.unknown += qty
        b.trades += 1
        self.observed_trades += 1
        self.trade_received = now
        if side in self.rate_volumes:
            self.rate_volumes[side] += qty
            # Delayed events enter the CURRENT information set, never old bins.
            if int(row["T"]) < self.align_source_ms:
                self.late_trades += 1
            else:
                self.consumption[side][price] += qty
        return True

    def advance(self, now: float, own_depth: dict[tuple[str, int], int] | None = None) -> None:
        if self.rate_time is None:
            self.rate_time = now
        dt = now - self.rate_time
        if dt >= 1:
            for side in self.rates:
                value = self.rate_volumes[side] / dt
                for half in self.rates[side]:
                    weight = math.exp(-math.log(2) * dt / half)
                    self.rates[side][half] = weight * self.rates[side][half] + (1 - weight) * value
                self.rate_volumes[side] = 0
            self.rate_time = now
        if self.ready and self.align_time is not None and now - self.align_time >= 1:
            self._supply(now, own_depth or {})
        if len(self.bins) > 390:
            self.bins = defaultdict(Bin, {k: v for k, v in self.bins.items() if k >= int(now // 5) - 365})
        if len(self.trade_ids) > 2000:
            self.trade_ids = {k: t for k, t in self.trade_ids.items() if t >= now - 120}

    def _supply(self, now: float, own_depth: dict[tuple[str, int], int]) -> None:
        b = self.bins[int(now // 5)]
        for side, old, current in (("Buy", self.previous_asks, self.asks), ("Sell", self.previous_bids, self.bids)):
            best = min(old) if side == "Buy" else max(old)
            outer = max(current) if side == "Buy" else min(current)
            sign = 1 if side == "Buy" else -1
            front = {p for p in set(old) | set(current) if 0 <= sign * (p - best) <= 2}
            positive, consumed = 0.0, 0
            for p in front:
                # An outgoing top-N boundary is censored, never a cancellation.
                if (side == "Buy" and p > outer) or (side == "Sell" and p < outer):
                    continue
                own_side = "Sell" if side == "Buy" else "Buy"
                old_q = max(0, old.get(p, 0) - self.previous_own_depth.get((own_side, p), 0))
                new_q = max(0, current.get(p, 0) - own_depth.get((own_side, p), 0))
                m = self.consumption[side].get(p, 0)
                positive += max(0, new_q - old_q + m)
                consumed += m
            depth_old, depth_new = sum(old.get(p, 0) for p in front), sum(current.get(p, 0) for p in front)
            if consumed > 0 and depth_old > 0:
                # A recovery needs actual opposing consumption, not a quiet bid.
                if depth_new >= depth_old * .9 and positive >= consumed * .7:
                    self.recoveries[side].append(now)
                elif depth_new < depth_old * .9:
                    self.shocks.append((side, now, best, depth_old))
            if side == "Buy":
                b.supply_ask += positive
                b.consumed_ask += consumed
            else:
                b.supply_bid += positive
                b.consumed_bid += consumed
        kept = []
        for side, started, price, depth in self.shocks:
            current = self.asks if side == "Buy" else self.bids
            sign = 1 if side == "Buy" else -1
            recovered = sum(q for p, q in current.items() if 0 <= sign * (p - price) <= 2) >= depth * .9
            if recovered and now - started <= 10:
                self.recoveries[side].append(now)
            elif now - started < 10:
                kept.append((side, started, price, depth))
        self.shocks = kept[-50:]
        for rows in self.recoveries.values():
            while rows and rows[0] < now - 30:
                rows.popleft()
        self.previous_bids, self.previous_asks = self.bids.copy(), self.asks.copy()
        self.previous_own_depth = own_depth.copy()
        self.align_time, self.align_source_ms = now, self.book_source_ms
        self.consumption = {"Buy": defaultdict(int), "Sell": defaultdict(int)}

    def recent(self, now: float, seconds: int) -> list[Bin]:
        end = int(now // 5)
        return [self.bins.get(k, Bin()) for k in range(end - seconds // 5, end)]

    def evaluate_episode(self, now: float) -> str | None:
        end = int(now // 5)
        if self.last_evaluation == end:
            return self.episode.direction if self.episode else None
        self.last_evaluation = end
        bins = self.recent(now, 30)
        buy, sell = sum(b.buy for b in bins), sum(b.sell for b in bins)
        total = buy + sell
        fast = self.recent(now, 10)
        fast_buy, fast_sell = sum(b.buy for b in fast), sum(b.sell for b in fast)
        if self.episode:
            direction = self.episode.direction
            fraction = (fast_buy if direction == "Buy" else fast_sell) / max(1, fast_buy + fast_sell)
            self.episode.weak_count = self.episode.weak_count + 1 if fraction < .55 else 0
            last = bins[-1]
            opposite = last.sell if direction == "Buy" else last.buy
            background = (self.background("Buy") + self.background("Sell")) * 5
            ended = self.episode.weak_count >= 2 or (
                opposite / max(1, last.buy + last.sell) >= .65 and opposite >= max(1, background)
            )
            self.survival.observe(direction, max(0, now - self.episode.start - 5), ended)
            self.episode.observations += 1
            if ended:
                self.episode = None
                return None  # cannot flip in the same evaluation
        else:
            for direction in ("Buy", "Sell"):
                participation = (buy if direction == "Buy" else sell) / max(1, total)
                persistent = sum((b.buy > b.sell) if direction == "Buy" else (b.sell > b.buy) for b in bins)
                fast_fraction = (fast_buy if direction == "Buy" else fast_sell) / max(1, fast_buy + fast_sell)
                if (participation >= .65 and persistent >= 4 and sum(b.trades > 0 for b in bins) >= 4
                        and sum(b.trades for b in bins) >= 20 and fast_fraction >= .55):
                    self.episode = Episode(direction, now)
                    break
            if self.episode is None:
                self.neutral["Buy"].append(bins[-1].buy / 5)
                self.neutral["Sell"].append(bins[-1].sell / 5)
        return self.episode.direction if self.episode else None

    def background(self, side: str) -> float:
        return median(self.neutral[side]) if len(self.neutral[side]) >= 12 else self.rates[side][120]

    def rate(self, side: str, future: float, now: float) -> float:
        own = self.episode and self.episode.direction == side
        values = [self.rates[side][h] for h in (10, 30)]
        current = min(values) if own else max(values)
        background = self.background(side)
        if not self.episode:
            return background
        survive = self.survival.probability(self.episode.direction, now - self.episode.start, future)
        return max(0, background + (current - background) * survive)

    def volume(self, side: str, start: float, end: float, now: float) -> float:
        value, u = 0.0, max(0, start)
        while u < end:
            dt = min(1, end - u)
            value += self.rate(side, u + dt / 2, now) * dt
            u += dt
        return value

    def supply_rate(self, side: str, now: float) -> float:
        values = [(b.supply_ask if side == "Buy" else b.supply_bid) / 5 for b in self.recent(now, 30)]
        values.sort()
        return values[min(len(values) - 1, int(len(values) * .75))]

    def depleted(self, direction: str, now: float) -> bool:
        count = 0
        for b in self.recent(now, 15):
            m = b.consumed_ask if direction == "Buy" else b.consumed_bid
            supply = b.supply_ask if direction == "Buy" else b.supply_bid
            count += int(m > 0 and supply / (m + 1) < .7)
        return count >= 2

    def unknown_fraction(self, now: float) -> float:
        bins = self.recent(now, 30)
        return sum(b.unknown for b in bins) / max(1, sum(b.buy + b.sell + b.unknown for b in bins))

    def censor(self) -> None:
        self.episode = None  # connectivity failure is not a natural end
        self.ready = False
