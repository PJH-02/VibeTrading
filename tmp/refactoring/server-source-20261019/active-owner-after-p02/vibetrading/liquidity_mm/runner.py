from __future__ import annotations

import asyncio
import hashlib
import json
import math
import shutil
import signal
import time
import uuid
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

import aiohttp

from . import MODEL_VERSION
from .config import Config
from .domain import ACTIVE, TERMINAL, Inventory, Order, worst_inventory
from .exchange import PREFIX, TestnetClient
from .journal import Journal, RiskLedger, RunLock, encode, write_json
from .market import DataFault, Market
from .strategy import Decision, Strategy

D = Decimal
KST = timezone(timedelta(hours=9))


class Runner:
    def __init__(self, config: Config, client: TestnetClient, instrument, details: dict,
                 mode: str, duration: float, state_dir: Path, risk_path: Path | None = None) -> None:
        self.config, self.client, self.instrument = config, client, instrument
        self.mode, self.duration, self.state_dir = mode, duration, state_dir
        self.run_id = datetime.now(KST).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.journal = Journal(state_dir / (self.run_id + ".sqlite3"), self.run_id)
        self.risk_ledger = RiskLedger(risk_path or state_dir / "risk.sqlite3")
        self.market = Market(instrument)
        self.strategy = Strategy(config, self.market, details["maker"], details["taker"])
        self.inventory = Inventory()
        self.orders: dict[str, Order] = {}
        self.native_ids: set[str] = set()
        self.wallet = details["wallet"]
        self.next_funding_ms = int(details["ticker"].get("nextFundingTime") or 0)
        self.mark_ticks = instrument.ticks(details["ticker"]["markPrice"])
        self.mark_received = 0.0
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self.tasks: list[asyncio.Task] = []
        self.sockets = []
        self.started = self.end_at = 0.0
        self.last_evaluation = self.last_reconcile = self.last_status = 0.0
        self.last_wallet = self.last_clock = 0.0
        self.last_private = 0.0
        self.cooldown_until = 0.0
        self.soft_count = 0
        self.soft_since: float | None = None
        self.stop_requested = False
        self.halt_reason: str | None = None
        self.reason_counts: Counter = Counter()
        self.order_counts: Counter = Counter()
        self.fill_counts: Counter = Counter()
        self.equity_start = self.equity_high = self.wallet["totalEquity"]
        self.daily_start = self.equity_start
        self.risk_day = ""
        self.risk_equity = None
        self.state = "BOOT"
        self.last_error: str | None = None
        self.last_decision: Decision | None = None
        self.max_queue = 0
        self.last_hypothesis_ended = False
        self.executions_after_ms = client.now_ms() - 10000
        self.exit_failures = 0
        self.journal.db.execute("INSERT INTO runs VALUES(?,?,?,?,?)", (self.run_id, int(time.time() * 1000), mode, encode(asdict(config)), None))
        self.journal.event("metadata", time.monotonic(), {"instrument": asdict(instrument),
            "maker_fee": details["maker"], "taker_fee": details["taker"], "equity": self.equity_start,
            "available": self.wallet["totalAvailableBalance"], "next_funding_ms": self.next_funding_ms,
            "clock_offset_ms": client.server_clock_offset_ms, "model_version": MODEL_VERSION,
            "policy_sha256": config.policy_digest()})
        self.journal.commit()

    async def reader(self, ws, kind: str) -> None:
        try:
            async for raw in ws:
                now, wall = time.monotonic(), int(time.time() * 1000)
                msg = json.loads(raw)
                if self.queue.full():
                    self.halt_reason = "event_queue_overflow"
                    self.stop_requested = True
                    return
                self.queue.put_nowait((kind, now, wall, msg))
                self.max_queue = max(self.max_queue, self.queue.qsize())
            self.halt_reason = f"{kind}_disconnected"
            self.stop_requested = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = type(exc).__name__
            self.halt_reason = f"{kind}_disconnected"
            self.stop_requested = True

    async def heartbeat(self, ws) -> None:
        try:
            while True:
                await asyncio.sleep(20)
                await ws.send('{"op":"ping"}')
        except asyncio.CancelledError:
            raise
        except Exception:
            self.halt_reason = "websocket_heartbeat_failed"
            self.stop_requested = True

    def own_depth(self, side: str | None = None) -> dict:
        result = Counter()
        for order in self.orders.values():
            if order.status in {"New", "PartiallyFilled", "PENDING_CANCEL"} and order.leaves:
                key = order.price if side else (order.side, order.price)
                if side is None or side == order.side:
                    result[key] += order.leaves
        return dict(result)

    def process(self, kind: str, now: float, wall: int, msg: dict) -> None:
        self.journal.event(kind, now, msg, wall)
        if msg.get("success") is False:
            raise DataFault(f"{kind}_subscription_rejected")
        if kind == "private":
            self.last_private = now
        topic = msg.get("topic", "")
        if topic.startswith("orderbook."):
            # A fresh snapshot mid-session invalidates old flow/supply forecasts.
            if msg.get("type") == "snapshot" and self.market.ready:
                self.market.episode = None
                self.market.recoveries["Buy"].clear()
                self.market.recoveries["Sell"].clear()
                if any(o.role == "ENTRY" and o.leaves for o in self.orders.values()):
                    self.last_hypothesis_ended = True
            self.market.book(msg, now)
        elif topic.startswith("publicTrade."):
            for row in msg["data"]:
                self.market.trade(row, now)
        elif topic.startswith("tickers."):
            row = msg["data"]
            if row.get("markPrice"):
                self.mark_ticks = self.instrument.ticks(row["markPrice"])
                self.mark_received = now
            elif self.mark_ticks:
                # Ticker deltas omit unchanged fields. The cached mark remains
                # part of the freshly received incremental ticker state.
                self.mark_received = now
            if row.get("nextFundingTime"):
                self.next_funding_ms = int(row["nextFundingTime"])
        elif topic == "order":
            for row in msg["data"]:
                self.order_event(row, now)
        elif topic == "execution":
            for row in msg["data"]:
                self.execution_event(row, now)
        # Position notifications can arrive before execution notifications. REST
        # reconciliation after processing queued executions is authoritative.

    def order_event(self, row: dict, now: float) -> None:
        if row.get("symbol") != self.config.symbol:
            return
        client_id = row.get("orderLinkId", "")
        order = self.orders.get(client_id)
        if order is None:
            if row.get("stopOrderType") in {"StopLoss", "PartialStopLoss"}:
                self.native_ids.add(row["orderId"])
                return
            if row.get("orderStatus") not in TERMINAL:
                raise DataFault("unowned_symbol_order")
            return
        order.exchange_id = row.get("orderId", order.exchange_id)
        reported = self.instrument.lots(row.get("cumExecQty", "0"))
        order.reported_filled = max(order.reported_filled, reported)
        status = row["orderStatus"]
        if order.status not in TERMINAL or status in TERMINAL:
            order.status = status
        if status in {"New", "PartiallyFilled"} and order.live_at is None:
            created_ms = int(row.get("createdTime") or self.client.now_ms())
            order.live_at = min(now, now - max(0, self.client.now_ms() - created_ms) / 1000)
        self.journal.order(order)

    def execution_event(self, row: dict, now: float) -> None:
        if row.get("symbol") != self.config.symbol or row.get("execType") != "Trade":
            return
        order = self.orders.get(row.get("orderLinkId", ""))
        native = row.get("orderId") in self.native_ids or row.get("stopOrderType") in {"StopLoss", "PartialStopLoss"}
        if native:
            self.native_ids.add(row["orderId"])
        if not order and not native:
            raise DataFault("unowned_or_unmatched_execution")
        qty, price = self.instrument.lots(row["execQty"]), self.instrument.ticks(row["execPrice"])
        if not self.journal.execution(row, now):
            return
        if order:
            order.filled += qty
            order.reported_filled = max(order.reported_filled, order.filled)
            if order.filled > order.qty:
                raise DataFault("overfilled_order")
            if order.filled == order.qty:
                order.status = "Filled"
            elif order.status not in TERMINAL:
                order.status = "PartiallyFilled"
            self.journal.order(order)
        exchange_age = max(0, (self.client.now_ms() - int(row["execTime"])) / 1000)
        fill_time = now - exchange_age
        was_flat = self.inventory.net == 0
        self.inventory.fill(row["execId"], row["side"], qty, price, D(row.get("execFee", "0")),
                            fill_time, deadline=order.deadline if order else self.inventory.deadline or now,
                            target=order.target if order else self.inventory.target,
                            stop=order.stop if order else self.inventory.stop)
        maker = row.get("isMaker") is True
        self.fill_counts[(order.role if order else "NATIVE_STOP") + ("_MAKER" if maker else "_TAKER")] += 1
        self.executions_after_ms = max(self.executions_after_ms, int(row["execTime"]) - 60000)
        if order and order.role in {"ENTRY", "EXIT"} and not maker:
            raise DataFault("post_only_contract_violation")
        if not was_flat and self.inventory.net == 0:
            self.cooldown_until = max(self.cooldown_until, now + self.config.cooldown_seconds)
            self.market.episode = None
            self.soft_count = 0
            self.soft_since = None
        # Every fill triggers revaluation on the next tick without waiting 5s.
        self.last_evaluation = 0

    def fresh(self, now: float) -> str | None:
        c, m = self.config, self.market
        if not m.ready:
            return "book_not_ready"
        if now - m.book_received > c.max_book_age_seconds:
            return "stale_orderbook"
        source_age = (self.client.now_ms() - m.book_source_ms) / 1000
        if source_age > c.max_source_age_seconds or source_age < -self.client.clock_rtt_ms / 1000 - 1:
            return "source_clock_or_latency"
        if now - self.last_private > c.max_private_age_seconds:
            return "stale_private_connection"
        if now - self.mark_received > c.max_source_age_seconds:
            return "stale_mark_price"
        if self.queue.qsize() > 200:
            return "event_processing_backlog"
        return None

    def risk(self, now: float) -> str | None:
        c, inv, inst = self.config, self.inventory, self.instrument
        equity = self.wallet["totalEquity"]
        self.equity_high = max(self.equity_high, equity)
        day = datetime.now(KST).date().isoformat()
        if day != self.risk_day or self.risk_equity != equity:
            self.risk_day, self.risk_equity = day, equity
            self.daily_start, watermark, persisted_halt = self.risk_ledger.observe(day, equity)
            self.equity_high = max(self.equity_high, watermark)
            if persisted_halt:
                return "persisted_account_risk_halt"
        pnl = inv.realized_ticks_lots * inst.tick * inst.lot - inv.fees
        if inv.net:
            close_price = self.market.bid if inv.net > 0 else self.market.ask
            pnl += (D(close_price) - inv.average) * inv.net * inst.tick * inst.lot
            pnl -= abs(inst.qty(inv.net)) * inst.price(close_price) * max(D(0), self.strategy.taker_fee)
        risk_reason = None
        if equity <= self.daily_start * (1 - c.daily_loss_fraction):
            risk_reason = "daily_equity_loss_limit"
        elif equity <= self.equity_high * (1 - c.drawdown_fraction):
            risk_reason = "equity_drawdown_limit"
        elif pnl <= -c.max_testnet_loss_usdt:
            risk_reason = "testnet_absolute_loss_limit"
        elif max(self.wallet["accountIMRate"], self.wallet["accountMMRate"]) >= c.max_margin_rate:
            risk_reason = "margin_rate_limit"
        if risk_reason:
            self.risk_ledger.halt(day, risk_reason)
            return risk_reason
        worst_long, worst_short = worst_inventory(inv, list(self.orders.values()))
        if max(abs(worst_long), abs(worst_short)) > inst.lots(c.unit_qty) * c.max_units:
            return "inventory_reservation_limit"
        if inv.net:
            # Strong opposite aggression plus depleted defensive liquidity is an
            # event-level hard invalidation, separate from 5-second soft checks.
            direction = "Buy" if inv.net > 0 else "Sell"
            opposing = "Sell" if inv.net > 0 else "Buy"
            current_bin = self.market.bins.get(int(now // 5))
            if current_bin:
                opposite_qty = current_bin.sell if inv.net > 0 else current_bin.buy
                total = current_bin.buy + current_bin.sell
                band = self.market.bids if inv.net > 0 else self.market.asks
                best = self.market.bid if inv.net > 0 else self.market.ask
                remaining_depth = sum(q for p, q in band.items() if abs(p - best) <= 2)
                if (opposite_qty / max(1, total) >= .65
                    and opposite_qty >= max(1, self.market.background(opposing) * 5)
                    and remaining_depth * float(c.depth_haircut) < opposite_qty):
                    return "opposing_flow_and_defense_collapse"
            if inv.oldest_fill is not None and now >= inv.oldest_fill + c.max_holding_seconds:
                return "hard_max_holding"
            if inv.deadline is not None and now >= inv.deadline:
                return "normal_exit_deadline"
            if inv.stop and ((inv.net > 0 and min(self.market.bid, self.mark_ticks) <= inv.stop)
                             or (inv.net < 0 and max(self.market.ask, self.mark_ticks) >= inv.stop)):
                return "hard_price_stop"
        return None

    async def submit(self, decision: Decision | None = None, *, role: str = "ENTRY", qty: int = 0,
                     price: int = 0, ioc: bool = False) -> Order | None:
        now = time.monotonic()
        if role == "ENTRY":
            if self.mode != "testnet" or self.stop_requested or (self.end_at and now >= self.end_at) or self.fresh(now):
                return None
            assert decision and decision.approved
            # Revalidate the full policy with CURRENT state before serialization.
            current = self.strategy.decide(now, self.inventory, list(self.orders.values()),
                                           self.wallet["totalEquity"], self.wallet["totalAvailableBalance"],
                                           self.next_funding_ms, self.client.now_ms())
            if not current.approved or (current.direction, current.entry, current.qty, current.target) != (
                decision.direction, decision.entry, decision.qty, decision.target
            ):
                return None
            side, qty, price = decision.direction, decision.qty, decision.entry
            deadline, target, stop = decision.deadline, decision.target, decision.stop
            if self.inventory.net:
                stop = max(stop, self.inventory.stop) if side == "Buy" else min(stop, self.inventory.stop)
        else:
            side = "Sell" if self.inventory.net > 0 else "Buy"
            reserved = sum(o.leaves for o in self.orders.values() if o.role != "ENTRY")
            qty = min(qty, max(0, abs(self.inventory.net) - reserved))
            if qty <= 0:
                return None
            deadline, target, stop = self.inventory.deadline or now, self.inventory.target, self.inventory.stop
        if not self.instrument.valid_order(price, qty, reduction=role != "ENTRY"):
            if role != "ENTRY":
                self.halt_reason = "invalid_reduction_quantity"
            return None
        order = Order(PREFIX + uuid.uuid4().hex[:28], side, role, price, qty, now, deadline, target, stop)
        self.orders[order.client_id] = order
        self.journal.order(order)
        self.order_counts[role] += 1
        try:
            result = await self.client.create(self.config.symbol, self.instrument, order, ioc=ioc)
            order.exchange_id = result["result"]["orderId"]
        except Exception as exc:
            # Even explicit REST rejection is reconciled: timeout/duplicate ID
            # must never create a second intent or release its risk reservation.
            order.status = "UNKNOWN"
            self.last_error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            self.halt_reason = "order_submission_unknown"
            self.stop_requested = True
        self.journal.order(order)
        return order

    async def cancel(self, order: Order) -> None:
        if order.status in TERMINAL or order.status == "PENDING_CANCEL":
            return
        order.status, order.cancel_at = "PENDING_CANCEL", time.monotonic()
        self.journal.order(order)
        try:
            await self.client.cancel(self.config.symbol, order)
            # Cancellation ACK is not final and does not release the reservation.
        except Exception as exc:
            self.last_error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            order.status = "UNKNOWN"
            self.journal.order(order)

    async def cancel_entries(self) -> None:
        for order in list(self.orders.values()):
            if order.role == "ENTRY" and order.leaves:
                await self.cancel(order)

    async def drain_queue(self) -> None:
        while not self.queue.empty():
            kind, now, wall, msg = self.queue.get_nowait()
            self.process(kind, now, wall, msg)

    async def reconcile(self, *, wallet: bool = False) -> dict:
        rows = await self.client.open_orders(self.config.symbol)
        fills = await self.client.rows("/v5/execution/list", {"category": "linear", "symbol": self.config.symbol,
                                       "startTime": max(self.executions_after_ms, self.client.now_ms() - 7 * 86400000 + 1000), "limit": 100})
        await self.drain_queue()
        # Oldest actual executions first; REST repeated IDs are harmless.
        for row in sorted(fills, key=lambda r: (int(r.get("execTime", "0")), str(r.get("execId", "")))):
            if row.get("orderLinkId") in self.orders or row.get("orderId") in self.native_ids or row.get("stopOrderType") in {"StopLoss", "PartialStopLoss"}:
                self.execution_event(row, time.monotonic())
        open_ids = set()
        foreign = []
        for row in rows:
            client_id = row.get("orderLinkId", "")
            if client_id in self.orders:
                open_ids.add(client_id)
                self.order_event(row, time.monotonic())
            elif row.get("stopOrderType") in {"StopLoss", "PartialStopLoss"}:
                self.native_ids.add(row["orderId"])
            else:
                foreign.append(row["orderId"])
        for order in list(self.orders.values()):
            if order.status not in TERMINAL and order.client_id not in open_ids:
                data = await self.client.request("GET", "/v5/order/realtime", {"category": "linear", "symbol": self.config.symbol,
                                                                                 "orderLinkId": order.client_id, "openOnly": 1})
                history = data["result"].get("list", [])
                if not history:
                    history = await self.client.rows("/v5/order/history", {"category": "linear", "symbol": self.config.symbol,
                                                                          "orderLinkId": order.client_id, "limit": 1})
                if history:
                    self.order_event(history[0], time.monotonic())
                else:
                    order.status = "UNKNOWN"
                    self.journal.order(order)
        await self.drain_queue()
        positions = await self.client.positions(self.config.symbol)
        await self.drain_queue()
        net = sum(self.instrument.lots(p.get("size") or "0") * (1 if p.get("side") == "Buy" else -1) for p in positions)
        if wallet:
            self.wallet = await self.client.wallet()
            self.last_wallet = time.monotonic()
            other_positions = await self.client.positions()
            other_orders = await self.client.open_orders()
            if any(p.get("symbol") != self.config.symbol and D(p.get("size") or "0") for p in other_positions):
                raise DataFault("other_symbol_inventory_during_run")
            if any(o.get("symbol") != self.config.symbol for o in other_orders):
                raise DataFault("other_symbol_orders_during_run")
        self.last_reconcile = time.monotonic()
        result = {"exchange_net_lots": net, "ledger_net_lots": self.inventory.net,
                  "open_orders": len(rows), "foreign_orders": len(foreign),
                  "unknown_orders": sum(o.status == "UNKNOWN" or o.reported_filled > o.filled for o in self.orders.values()),
                  "owned_open_orders": len(open_ids)}
        self.journal.event("reconciliation", self.last_reconcile, result)
        if foreign:
            raise DataFault("unowned_order_during_run")
        if any(int(p.get("positionIdx", 0)) != 0 for p in positions):
            raise DataFault("position_mode_changed")
        if net != self.inventory.net:
            raise DataFault("exchange_inventory_mismatch")
        # Inspect liquidation price independently from the strategy stop.
        for p in positions:
            if D(p.get("size") or "0") and p.get("liqPrice"):
                distance = abs(D(p["markPrice"]) - D(p["liqPrice"])) / D(p["markPrice"])
                if distance <= self.config.stop_bps * 3 / 10000:
                    raise DataFault("liquidation_margin_headroom")
        return result

    async def maintain_exit(self, now: float, valid: bool) -> None:
        inv = self.inventory
        side = "Sell" if inv.net > 0 else "Buy"
        active = [o for o in self.orders.values() if o.role != "ENTRY" and o.leaves]
        reserved = sum(o.leaves for o in active)
        if reserved > abs(inv.net):
            for order in active:
                await self.cancel(order)
            return
        target = inv.target
        crossed = self.market.bid >= target if inv.net > 0 else self.market.ask <= target
        if crossed and not active:
            # An already executable original target is not pushed away again.
            await self.submit(role="REDUCE", qty=abs(inv.net), price=target, ioc=True)
            return
        if not valid:
            # Soft drain can improve the execution price, never raise profit target.
            target = min(target, self.market.ask) if inv.net > 0 else max(target, self.market.bid)
        target = max(target, self.market.ask) if inv.net > 0 else min(target, self.market.bid)
        if active and any(o.price != target for o in active) and self.soft_since is not None:
            for order in active:
                await self.cancel(order)
            return
        qty = abs(inv.net) - reserved
        if qty:
            await self.submit(role="EXIT", qty=qty, price=target)

    async def tick(self, now: float) -> None:
        self.market.advance(now, self.own_depth())
        if (self.state_dir / "STOP").exists():
            self.stop_requested = True
            return
        if now - self.last_clock >= 300:
            await self.client.sync_clock(samples=1)
            self.last_clock = time.monotonic()
        if now - self.started >= 20:
            fault = self.fresh(now)
            if fault:
                if fault in {"stale_private_connection", "event_processing_backlog", "source_clock_or_latency"} or self.inventory.net or any(o.leaves for o in self.orders.values()):
                    self.halt_reason = fault
                    self.stop_requested = True
                else:
                    self.state = "WATCH"
                    if now - self.last_evaluation >= self.config.evaluation_seconds:
                        self.reason_counts[fault] += 1
                        self.last_evaluation = now
                    if self.market.episode:
                        self.market.episode = None  # censor quiet/stale observation
                return
        if self.mode == "testnet":
            for order in list(self.orders.values()):
                if order.role == "ENTRY" and order.leaves:
                    active_since = order.live_at if order.live_at is not None else order.created + self.config.entry_latency_seconds
                    if now >= min(active_since + self.config.entry_ttl_seconds, order.deadline):
                        await self.cancel(order)
            if self.last_hypothesis_ended:
                await self.cancel_entries()
                self.last_hypothesis_ended = False
            if now - self.last_reconcile >= 5:
                result = await self.reconcile(wallet=now - self.last_wallet >= 10)
                if result["unknown_orders"]:
                    raise DataFault("unreconciled_order_or_fill")
            risk = self.risk(now)
            if risk:
                if risk in {"hard_max_holding", "normal_exit_deadline", "hard_price_stop", "opposing_flow_and_defense_collapse"}:
                    await self.reduce_cycle(risk)
                else:
                    self.halt_reason = risk
                    self.stop_requested = True
                return
        if now - self.last_evaluation < self.config.evaluation_seconds:
            return
        self.last_evaluation = now
        previous = self.market.episode
        self.market.evaluate_episode(now)
        if previous and self.market.episode is None:
            await self.cancel_entries()
        for order in list(self.orders.values()):
            if order.role == "ENTRY" and order.leaves and order.status not in TERMINAL:
                remaining = max(0, order.deadline - now)
                target, _, _ = self.strategy.projection(order.side, order.leaves + abs(self.inventory.net), now,
                    self.config.private_latency_seconds + self.config.exit_latency_seconds,
                    remaining, stress=True, own_depth=self.own_depth("Sell" if order.side == "Buy" else "Buy"))
                covered = target and (target >= order.target if order.side == "Buy" else target <= order.target)
                if not self.market.episode or self.market.episode.direction != order.side or not covered or not self.strategy.defense(order.side, order.price, now):
                    await self.cancel(order)
        if now - self.started < self.config.warmup_seconds:
            self.state = "WATCH"
            self.reason_counts["warmup"] += 1
            return
        if now < self.cooldown_until:
            self.state = "COOLDOWN"
            self.reason_counts["cooldown"] += 1
            return
        if self.fresh(now):
            self.reason_counts[self.fresh(now)] += 1
            return
        if self.inventory.net:
            own_side = "Sell" if self.inventory.net > 0 else "Buy"
            valid = self.strategy.exit_valid(now, self.inventory, self.own_depth(own_side))
            self.soft_count = 0 if valid else self.soft_count + 1
            if self.soft_count >= 2 and self.soft_since is None:
                self.soft_since = now
            if self.soft_since is not None:
                self.state = "DRAIN"
                await self.cancel_entries()
                if now - self.soft_since >= self.config.soft_grace_seconds:
                    await self.reduce_cycle("soft_invalidation_grace_expired")
                    return
            else:
                self.state = "INVENTORY_LONG" if self.inventory.net > 0 else "INVENTORY_SHORT"
            if self.mode == "testnet":
                await self.maintain_exit(now, valid)
            if not valid or self.soft_since is not None:
                return
        decision = self.strategy.decide(now, self.inventory, list(self.orders.values()),
                                         self.wallet["totalEquity"], self.wallet["totalAvailableBalance"],
                                         self.next_funding_ms, self.client.now_ms())
        self.last_decision = decision
        self.reason_counts[decision.reason] += 1
        self.journal.decision(now, decision)
        if decision.approved:
            self.state = "ARMED_LONG" if decision.direction == "Buy" else "ARMED_SHORT"
            if self.mode == "testnet":
                order = await self.submit(decision)
                if order:
                    self.state = "QUOTING_LONG" if order.side == "Buy" else "QUOTING_SHORT"
        elif not self.inventory.net:
            self.state = "WATCH"
            if decision.reason != "entry_already_reserved":
                await self.cancel_entries()

    def status(self) -> dict:
        inst, inv = self.instrument, self.inventory
        worst_long, worst_short = worst_inventory(inv, list(self.orders.values()))
        realized = inv.realized_ticks_lots * inst.tick * inst.lot - inv.fees
        return {"run_id": self.run_id, "pid": __import__("os").getpid(), "model_version": MODEL_VERSION,
                "environment": "bybit-testnet", "mode": self.mode, "symbol": self.config.symbol,
                "state": self.state, "running": self.state not in {"STOPPED", "HALT"},
                "updated_at": datetime.now(KST).isoformat(),
                "elapsed_seconds": round(time.monotonic() - self.started, 2), "requested_seconds": self.duration,
                "evidence_mode": self.config.evidence_mode, "inventory_qty": str(inst.qty(inv.net)),
                "worst_long_qty": str(inst.qty(worst_long)), "worst_short_qty": str(inst.qty(worst_short)),
                "realized_pnl_after_execution_fees_usdt": str(realized), "execution_fees_usdt": str(inv.fees),
                "equity_change_usdt": str(self.wallet["totalEquity"] - self.equity_start),
                "orders": dict(self.order_counts), "fills": dict(self.fill_counts),
                "decision_reasons": dict(self.reason_counts), "max_queue": self.max_queue,
                "halt_reason": self.halt_reason, "last_error": self.last_error,
                "journal": str(self.journal.path.resolve()),
                "last_decision": asdict(self.last_decision) if self.last_decision else None}

    async def cleanup(self) -> dict:
        self.state = "REDUCE"
        verification = {"verified_flat": False}
        if self.mode != "testnet":
            try:
                positions = await self.client.positions()
                orders = await self.client.open_orders()
                self.wallet = await self.client.wallet()
                flat = not any(D(p.get("size") or "0") for p in positions) and not orders
                return {"verified_flat": flat, "orders_submitted": 0, "open_orders": len(orders)}
            except Exception as exc:
                return {"verified_flat": False, "verification_error": type(exc).__name__}
        cleanup_end = time.monotonic() + 60
        for attempt in range(3):
            if time.monotonic() >= cleanup_end:
                break
            try:
                for order in list(self.orders.values()):
                    if order.leaves or order.status not in TERMINAL:
                        await self.cancel(order)
                for _ in range(5):
                    await asyncio.sleep(.3)
                    verification = await self.reconcile(wallet=True)
                    if not verification["owned_open_orders"] and not verification["unknown_orders"]:
                        break
                if verification["owned_open_orders"] or verification["unknown_orders"]:
                    self.last_error = "Cannot replace orders before cancellations/fills are reconciled"
                    continue
                if not self.inventory.net:
                    if verification["open_orders"] == 0:
                        return {**verification, "verified_flat": True}
                    self.last_error = "Residual native protection orders remain"
                    continue
                # Fresh REST book also permits risk reduction after public WS loss.
                book = (await self.client.request("GET", "/v5/market/orderbook", {"category": "linear", "symbol": self.config.symbol, "limit": 50}, public=True))["result"]
                side_buy = self.inventory.net < 0
                best = self.instrument.ticks(book["a"][0][0] if side_buy else book["b"][0][0])
                buffer = max(1, int((D(best) * self.config.liquidation_slippage_bps / 10000).to_integral_value(rounding=ROUND_CEILING)))
                price = best + buffer if side_buy else best - buffer
                order = await self.submit(role="REDUCE", qty=abs(self.inventory.net), price=price, ioc=True)
                if order is None or order.status == "UNKNOWN":
                    break
                await asyncio.sleep(.5)
                await self.reconcile(wallet=True)
            except Exception as exc:
                self.last_error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
                self.exit_failures += 1
        try:
            verification = await self.reconcile(wallet=True)
            flat = (verification["exchange_net_lots"] == 0 and verification["ledger_net_lots"] == 0
                    and verification["open_orders"] == 0 and verification["unknown_orders"] == 0)
            return {**verification, "verified_flat": flat}
        except Exception as exc:
            self.last_error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            return {**verification, "verified_flat": False, "verification_error": self.last_error}

    async def bounded_cleanup(self) -> dict:
        try:
            return await asyncio.wait_for(self.cleanup(), timeout=60)
        except asyncio.TimeoutError:
            return {"verified_flat": False, "verification_error": "cleanup_60_second_timeout"}
        except Exception as exc:
            return {"verified_flat": False, "verification_error": type(exc).__name__}

    async def reduce_cycle(self, reason: str) -> None:
        result = await self.bounded_cleanup()
        self.journal.event("risk_reduction", time.monotonic(), {"reason": reason, **result})
        if not result.get("verified_flat"):
            self.halt_reason = "risk_reduction_incomplete: " + reason
            self.stop_requested = True
            return
        self.cooldown_until = time.monotonic() + self.config.cooldown_seconds
        self.market.episode = None
        self.soft_count, self.soft_since = 0, None
        self.state = "COOLDOWN"

    async def run(self) -> dict:
        self.started = time.monotonic()
        self.end_at = self.started + self.duration
        self.last_private = self.last_clock = self.last_wallet = self.started
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, lambda: setattr(self, "stop_requested", True))
            except (NotImplementedError, RuntimeError):
                pass
        try:
            private = await self.client.connect_private()
            public = await self.client.connect_public(self.config)
            self.sockets = [private, public]
            for ws, kind in ((private, "private"), (public, "public")):
                self.tasks.extend([asyncio.create_task(self.reader(ws, kind)), asyncio.create_task(self.heartbeat(ws))])
            self.state = "WATCH"
            while time.monotonic() < self.end_at and not self.stop_requested:
                try:
                    kind, now, wall, msg = await asyncio.wait_for(self.queue.get(), timeout=.25)
                    self.process(kind, now, wall, msg)
                    await self.drain_queue()
                except asyncio.TimeoutError:
                    pass
                await self.tick(time.monotonic())
                if time.monotonic() - self.last_status >= 1:
                    write_json(self.state_dir / "status.json", self.status())
                    self.journal.commit()
                    self.last_status = time.monotonic()
                    bytes_used = sum(p.stat().st_size for p in self.journal.path.parent.glob(self.journal.path.name + "*"))
                    if bytes_used > 512 * 1024**2 or shutil.disk_usage(self.state_dir).free < 512 * 1024**2:
                        raise DataFault("journal_storage_budget")
        except asyncio.CancelledError:
            self.halt_reason = self.halt_reason or "process_interrupted"
        except Exception as exc:
            self.halt_reason = self.halt_reason or (str(exc) if isinstance(exc, DataFault) else "runtime_error")
            self.last_error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        finally:
            self.stop_requested = True
            self.market.censor()
            verification = await self.bounded_cleanup()
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            for ws in self.sockets:
                await ws.close()
            if not verification.get("verified_flat"):
                self.halt_reason = self.halt_reason or "cleanup_exposure_unverified"
            self.state = "STOPPED" if verification.get("verified_flat") and self.halt_reason is None else "HALT"
            summary = {**self.status(), "cleanup": verification}
            self.journal.db.execute("UPDATE runs SET summary=? WHERE run_id=?", (encode(summary), self.run_id))
            write_json(self.state_dir / (self.run_id + "-report.json"), summary)
            write_json(self.state_dir / "status.json", summary)
            self.journal.close()
            self.risk_ledger.close()
        return summary


async def execute(config: Config, mode: str, duration: float, project_root: Path) -> dict:
    state_dir = Path(config.state_dir)
    if not state_dir.is_absolute():
        state_dir = project_root / state_dir
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
        client = TestnetClient(session, trading=mode == "testnet")
        identity = (await client.request("GET", "/v5/user/query-api"))["result"].get("userID")
        if not identity:
            raise RuntimeError("Cannot establish the testnet account identity for the exclusive lock")
        # UID-based lock also blocks a second API key belonging to this account.
        # Only its digest is persisted; neither the key nor UID is written.
        account_hash = hashlib.sha256(("bybit-testnet:" + str(identity)).encode()).hexdigest()[:16]
        # Fixed account-wide location even when user changes profile state_dir.
        lock_path = project_root / "runtime_data" / "liquidity_mm" / (account_hash + ".lock")
        with RunLock(lock_path):
            preflight, instrument, details = await client.preflight(config)
            write_json(state_dir / "preflight.json", preflight)
            if mode == "testnet" and not preflight["ready"]:
                raise RuntimeError("Testnet preflight blocked: " + ", ".join(preflight["reasons"]))
            if (state_dir / "STOP").exists():
                # A new explicitly requested run resets a previous local stop only
                # after REST proves the account has no leftover exposure.
                if not preflight["ready"]:
                    raise RuntimeError("Previous STOP remains pending; reconcile the account first")
                (state_dir / "STOP").unlink()
            risk_path = lock_path.with_suffix(".risk.sqlite3")
            risk_check = RiskLedger(risk_path)
            _, _, halted = risk_check.observe(datetime.now(KST).date().isoformat(), details["wallet"]["totalEquity"])
            risk_check.close()
            if mode == "testnet" and halted:
                raise RuntimeError("Persisted account risk halt requires operator review")
            runner = Runner(config, client, instrument, details, mode, duration, state_dir, risk_path)
            return await runner.run()
