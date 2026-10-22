from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

from . import MODEL_VERSION
from .config import Config
from .domain import Inventory, Order, worst_inventory
from .market import Market

D = Decimal


@dataclass
class Decision:
    approved: bool
    reason: str
    direction: str = ""
    entry: int = 0
    target: int = 0
    stop: int = 0
    qty: int = 0
    deadline: float = 0
    structural: int = 0
    reachable: int = 0
    predicted_volume: float = 0
    service_needed: float = 0
    edge_ticks: int = 0
    evidence: str = ""


class Evidence:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.cells: dict[str, dict] = {}
        self.data: dict = {}
        if config.evidence_file:
            data = json.loads(Path(config.evidence_file).read_text(encoding="utf-8"))
            if data.get("model_version") != MODEL_VERSION or data.get("symbol") != config.symbol:
                raise ValueError("Evidence model/symbol does not match the running policy")
            if data.get("horizon_seconds") != config.horizon_seconds or data.get("out_of_sample") is not True:
                raise ValueError("Evidence requires the matching frozen horizon and out-of-sample results")
            if data.get("policy_sha256") != config.policy_digest():
                raise ValueError("Evidence does not match the frozen strategy configuration")
            self.cells = data["cells"]
            self.data = data

    def gate(self, direction: str, offset: int, age_bucket: int) -> tuple[bool, str]:
        if self.config.evidence_mode == "testnet_pilot":
            return True, "EXPERIMENTAL_TESTNET_UNVALIDATED"
        row = self.cells.get(f"{direction}:{offset}:{age_bucket}")
        if not row:
            return False, "missing_conditional_fill_evidence"
        valid = (row.get("episodes", 0) >= 100 and row.get("net_pnl_lower_95", -1) > 0
                 and row.get("normal_exit_lower_95", 0) >= max(.70, row.get("break_even_probability", 1) + .05)
                 and row.get("block_bootstrap") is True)
        return valid, "calibrated" if valid else "conditional_economics_gate"


class Strategy:
    def __init__(self, config: Config, market: Market, maker_fee: Decimal, taker_fee: Decimal) -> None:
        self.config, self.market = config, market
        self.maker_fee, self.taker_fee = maker_fee, taker_fee
        self.evidence = Evidence(config)
        if config.evidence_mode == "calibrated":
            data = self.evidence.data
            if maker_fee > D(data.get("maker_fee_rate", "-1")) or taker_fee > D(data.get("taker_fee_rate", "-1")):
                raise ValueError("Current fees exceed the frozen calibration fees")
            survival = data.get("survival", {})
            for side in ("Buy", "Sell"):
                risk, ends = survival.get("risk", {}).get(side), survival.get("ends", {}).get(side)
                if (not isinstance(risk, list) or not isinstance(ends, list) or len(risk) != 5 or len(ends) != 5
                    or any(not isinstance(n, int) or n < 0 for n in risk + ends)
                    or any(d > n for n, d in zip(risk, ends))):
                    raise ValueError("Calibrated mode needs a frozen direction/age survival model")
                market.survival.risk[side] = risk.copy()
                market.survival.ends[side] = ends.copy()
            market.survival.frozen = True

    def projection(self, direction: str, qty: int, now: float, start: float, end: float,
                   *, stress: bool, own_depth: dict[int, int] | None = None) -> tuple[int, float, float]:
        """Sequential front exposure; positive cancellations never help profit."""
        m, c = self.market, self.config
        levels = sorted(m.asks.items()) if direction == "Buy" else sorted(m.bids.items(), reverse=True)
        supply = m.supply_rate(direction, now) * float(c.supply_stress_multiplier if stress else D(1))
        multiplier = float(c.flow_stress_multiplier) if stress else 1.0
        volume = m.volume(direction, start, end, now) * multiplier
        reserve = volume * float(c.reserve_fraction)
        # Consume only AFTER activation. Supply accrues only while each level is
        # attacked, plus one conservative stream of new better-price liquidity.
        time_at, spent, target, need = start, 0.0, 0, 0.0
        own_depth = own_depth or {}
        for price, displayed in levels:
            depth = max(0, displayed - own_depth.get(price, 0))
            remaining, cursor, consumed = float(depth), time_at, 0.0
            while cursor < end and remaining > 0:
                dt = min(1, end - cursor)
                flow = m.rate(direction, cursor + dt / 2, now) * multiplier
                net_rate = flow - 2 * supply
                if net_rate <= 0:
                    return target, volume, need
                step = min(dt, remaining / net_rate)
                remaining -= net_rate * step
                consumed += flow * step
                cursor += step
            if remaining > 1e-7:
                break
            # The own exit is behind the visible queue and uncertainty reserve.
            available = m.volume(direction, cursor, end, now) * multiplier - 2 * supply * (end - cursor)
            if available < qty + reserve:
                break
            spent += consumed
            target, need, time_at = price, spent + qty + reserve, cursor
        return target, volume, need

    def defense(self, direction: str, entry: int, now: float) -> bool:
        m, c = self.market, self.config
        opposing = "Sell" if direction == "Buy" else "Buy"
        levels = m.bids if direction == "Buy" else m.asks
        sign = 1 if direction == "Buy" else -1
        depth = sum(q for p, q in levels.items() if 0 <= sign * (entry - p) <= 2)
        stressed = depth * float(c.depth_haircut)
        window = c.cancel_latency_seconds + c.private_latency_seconds
        demand = m.volume(opposing, 0, window, now) / float(c.flow_stress_multiplier)
        return stressed > demand and len(m.recoveries[opposing]) >= 2

    def decide(self, now: float, inventory: Inventory, orders: list[Order], equity: Decimal,
               available: Decimal, next_funding_ms: int, exchange_now_ms: int) -> Decision:
        m, c, inst = self.market, self.config, self.market.instrument
        def reject(reason: str) -> Decision:
            return Decision(False, reason, evidence=c.evidence_mode)
        if not m.ready or m.unknown_fraction(now) > .1:
            return reject("market_data_quality")
        if m.observed_trades >= 20 and m.late_trades / m.observed_trades > .1:
            return reject("supply_alignment_quality")
        if not m.episode:
            return reject("no_persistent_flow_episode")
        direction = m.episode.direction
        if inventory.net and (inventory.net > 0) != (direction == "Buy"):
            return reject("inventory_direction_conflict")
        if not m.depleted(direction, now):
            return reject("front_supply_not_depleted")
        if available < c.min_available_usdt:
            return reject("insufficient_margin_headroom")
        if next_funding_ms <= 0 or abs(next_funding_ms - exchange_now_ms) <= (c.max_holding_seconds + c.funding_guard_seconds) * 1000:
            return reject("funding_window")
        worst_long, worst_short = worst_inventory(inventory, orders)
        unit = inst.lots(c.unit_qty)
        room = unit * c.max_units - (worst_long if direction == "Buy" else -worst_short)
        if any(o.role == "ENTRY" and o.leaves for o in orders):
            return reject("entry_already_reserved")
        if room <= 0:
            return reject("inventory_limit")
        base = m.bid if direction == "Buy" else m.ask
        sign = 1 if direction == "Buy" else -1
        age_bucket = m.survival.bucket(now - m.episode.start)
        candidates: list[Decision] = []
        last_reason = "no_executable_candidate"
        for offset in (0, 1):
            entry = base - sign * offset
            if not self.defense(direction, entry, now):
                last_reason = "entry_defense_or_recovery"
                continue
            allowed, evidence = self.evidence.gate(direction, offset, age_bucket)
            if not allowed:
                last_reason = evidence
                continue
            price = inst.price(entry)
            stress_loss = (c.stop_bps + c.liquidation_slippage_bps + c.latency_buffer_bps) / D(10000) + max(D(0), self.maker_fee) + max(D(0), self.taker_fee)
            supportive_notional = inst.qty(sum(b.buy if direction == "Buy" else b.sell for b in m.recent(now, 60))) * price
            liquidation_book = m.bids if direction == "Buy" else m.asks
            stop_distance = max(2, int((D(entry) * c.stop_bps / 10000).to_integral_value(rounding=ROUND_CEILING)))
            liquidation_depth = sum(q for p, q in liquidation_book.items() if abs(p - entry) <= stop_distance)
            notional = min(c.unit_notional_usdt, c.max_notional_usdt / c.max_units,
                           equity * D("0.5") / c.max_units, equity * D("0.0005") / stress_loss,
                           supportive_notional * D("0.01"), inst.qty(liquidation_depth) * price * D("0.05"))
            qty = min(unit, room, inst.lots(notional / price))
            worst_abs = max(abs(worst_long), abs(worst_short))
            qty = min(qty, inst.lots(c.max_notional_usdt / price) - worst_abs,
                      inst.lots(equity * D("0.5") / price) - worst_abs)
            if qty <= 0 or not inst.valid_order(entry, qty):
                last_reason = "quantity_or_participation_limit"
                continue
            # Pre-entry stress assumes a late fill at TTL, then private/new latency.
            start = c.entry_latency_seconds + c.entry_ttl_seconds + c.private_latency_seconds + c.exit_latency_seconds
            structural, _, _ = self.projection(direction, 0, now, c.entry_latency_seconds, c.horizon_seconds, stress=False)
            reach, _, _ = self.projection(direction, 0, now, start, c.horizon_seconds, stress=True)
            target, volume, need = self.projection(direction, qty + abs(inventory.net), now, start, c.horizon_seconds, stress=True)
            if not target or not reach:
                last_reason = "insufficient_delayed_exit_service"
                continue
            edge = sign * (target - entry)
            fees = D(entry) * max(D(0), self.maker_fee) + D(target) * max(D(0), self.maker_fee)
            buffer = D(entry) * (c.latency_buffer_bps + c.model_buffer_bps) / D(10000)
            if D(edge) < fees + buffer + 1:
                last_reason = "all_cost_edge"
                continue
            candidates.append(Decision(True, "eligible", direction, entry, target,
                                       entry - sign * stop_distance, qty, now + c.horizon_seconds,
                                       structural, reach, volume, need, edge, evidence))
        return max(candidates, key=lambda d: d.edge_ticks * d.qty) if candidates else reject(last_reason)

    def exit_valid(self, now: float, inventory: Inventory, own_depth: dict[int, int] | None = None) -> bool:
        if not inventory.net or not self.market.episode or inventory.deadline is None:
            return False
        direction = "Buy" if inventory.net > 0 else "Sell"
        if self.market.episode.direction != direction:
            return False
        target, _, _ = self.projection(direction, abs(inventory.net), now,
                                       self.config.exit_latency_seconds, max(0, inventory.deadline - now),
                                       stress=True, own_depth=own_depth)
        return bool(target and (target >= inventory.target if direction == "Buy" else target <= inventory.target)
                    and self.defense(direction, self.market.bid if direction == "Buy" else self.market.ask, now))
