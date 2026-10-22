"""Account-local paper matching. No broker, credentials, or network capability.

The matcher stages each bounded state transition and commits it with the portfolio
changes. Only a successful commit makes the staged matching state visible.
Receipt order is causal; late inputs are rejected rather than replayed into a
position. Capacity exhaustion stops admission/input instead of discarding dedup.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR, localcontext
import json

from .models import BookUpdateType, OrderBookEvent, OrderIntent, Side, TradeEvent, stable_hash
from .trading_contracts import ProductSpec, TradingMode

D = Decimal


class PaperExecution:
    def __init__(self, ledger, *, entry_latency_ns: int, cancel_latency_ns: int,
                 max_open_orders: int, max_depth: int, max_receipts: int,
                 max_state_bytes: int, stale_after_ns: int):
        if ledger.mode != TradingMode.PAPER:
            raise ValueError("Paper matching requires a paper portfolio ledger")
        values = dict(entry_latency_ns=entry_latency_ns, cancel_latency_ns=cancel_latency_ns,
                      max_open_orders=max_open_orders, max_depth=max_depth,
                      max_receipts=max_receipts, max_state_bytes=max_state_bytes,
                      stale_after_ns=stale_after_ns)
        for name, value in values.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < (0 if "latency" in name else 1):
                raise ValueError(name + " requires an explicit bounded integer")
        self.ledger, self.config = ledger, values
        self._state = ledger.paper_state() or {
            "version": 2, "config": values, "clock": 0, "orders": {},
            "books": {}, "receipts": {}, "fills": 0, "blocked": {}, "gap_generations": {},
        }
        if self._state["version"] != 2 or self._state["config"] != values:
            raise ValueError("Paper matcher state/configuration differs from the durable run")
        self._check_bound(self._state)
        self._aligned(self._state)

    def _aligned(self, state):
        rows = {row["intent_id"]: row for row in self.ledger.active_orders()}
        if set(rows) != set(state["orders"]):
            raise ValueError("Paper matcher and durable active orders disagree; explicit recovery required")
        with localcontext() as context:
            context.prec = 128
            for identity, order in state["orders"].items():
                row = rows[identity]
                if (D(row["quantity"])-D(row["filled"]) != D(order["leaves"]) or
                        row["strategy"] != order["strategy"] or row["instrument"] != order["instrument"] or
                        row["side"] != order["side"] or json.loads(row["rules"]) != order["rules"]):
                    raise ValueError("Paper matcher and durable order facts disagree")

    def _check_bound(self, state):
        if len(state["orders"]) > self.config["max_open_orders"]:
            raise ValueError("Paper open-order capacity exceeded")
        if len(state["receipts"]) > self.config["max_receipts"]:
            raise ValueError("Paper receipt capacity exceeded")
        if len(json.dumps(state, separators=(",", ":")).encode()) > self.config["max_state_bytes"]:
            raise ValueError("Paper matching state byte capacity exceeded")

    def _transition(self, operation):
        if self.ledger.transaction_active:
            raise RuntimeError("Paper transitions cannot join an outer transaction")
        staged = deepcopy(self._state)
        with localcontext() as context:
            context.prec = 128
            with self.ledger.transaction():
                self._aligned(staged)
                result = operation(staged)
                self._check_bound(staged)
                self.ledger.save_paper_state(staged)
        self._state = staged
        return result

    @staticmethod
    def _time(now):
        if isinstance(now, bool) or not isinstance(now, int) or now < 0:
            raise ValueError("Receipt time must be a nonnegative integer nanosecond timestamp")
        return now

    def _clock(self, state, now):
        self._time(now)
        if now < state["clock"]:
            raise ValueError("Paper receipt clock regression")

    @staticmethod
    def _price(value):
        return format(D(value).normalize(), "f")

    def _terminal(self, state, order, kind, now, reason):
        getattr(self.ledger, kind)(order["intent_id"], confirmed=True,
            evidence={"mode": "paper", "receipt_ns": now, "reason": reason})
        state["orders"].pop(order["intent_id"])

    def submit(self, intent: OrderIntent, strategy: str, evidence: dict, expiry_ns: int,
               rules: ProductSpec, now_ns: int, *, reference_price=None,
               margin_rate=None, funding_buffer, account_notional_limit,
               strategy_notional_limit, account_capital_limit, strategy_capital_limit,
               post_only: bool = True, reduce_only: bool = False,
               time_in_force: str = "GTC", group_id: str | None = None):
        rules.supported()
        if time_in_force not in {"GTC", "IOC"}:
            raise ValueError("Paper execution supports GTC/IOC; FOK requires a separate policy")
        if not post_only and time_in_force != "IOC":
            raise ValueError("Paper taker admission requires explicit IOC protection")
        if post_only and time_in_force == "IOC":
            raise ValueError("Post-only IOC is contradictory")
        if intent.limit_price is None or intent.quantity is None:
            raise ValueError("Paper orders require explicit protective price and quantity")
        if intent.instrument != rules.instrument:
            raise ValueError("Paper rules must match the complete intent instrument")
        enriched = intent.model_copy(update={"metadata": {
            **intent.metadata, "reduce_only": reduce_only, "post_only": post_only,
            "time_in_force": time_in_force, "group_id": group_id,
        }})

        def operation(state):
            if intent.intent_id in state["orders"]:
                # The ledger verifies duplicate payload identity as well.
                created, row = self.ledger.admit_order(enriched, strategy, evidence, expiry_ns, rules,
                    reference_price=reference_price, margin_rate=margin_rate,
                    funding_buffer=funding_buffer, account_notional_limit=account_notional_limit,
                    strategy_notional_limit=strategy_notional_limit, account_capital_limit=account_capital_limit,
                    strategy_capital_limit=strategy_capital_limit, now_ns=now_ns)
                return created, row
            self._clock(state, now_ns)
            self._advance(state, now_ns)
            if intent.instrument.key in state["blocked"]:
                raise ValueError("Paper instrument is blocked until a valid full book")
            if len(state["orders"]) >= self.config["max_open_orders"]:
                raise ValueError("Paper open-order capacity exceeded")
            created, row = self.ledger.admit_order(enriched, strategy, evidence, expiry_ns, rules,
                reference_price=reference_price, margin_rate=margin_rate,
                funding_buffer=funding_buffer, account_notional_limit=account_notional_limit,
                strategy_notional_limit=strategy_notional_limit, account_capital_limit=account_capital_limit,
                strategy_capital_limit=strategy_capital_limit, now_ns=now_ns)
            if created:
                state["orders"][intent.intent_id] = {
                    "intent_id": intent.intent_id, "strategy": strategy, "instrument": intent.instrument.key,
                    "side": intent.side.value, "price": self._price(intent.limit_price), "leaves": str(intent.quantity),
                    "rules": rules.to_document(), "born_ns": now_ns,
                    "arrival_ns": now_ns + self.config["entry_latency_ns"], "expiry_ns": expiry_ns,
                    "cancel_ns": None, "active": False, "queue": None,
                    "source_floor": None, "fill_serial": 0, "reduce_only": reduce_only,
                    "post_only": post_only, "time_in_force": time_in_force, "group_id": group_id,
                }
                self._advance(state, now_ns)
            return created, self.ledger.get(intent.intent_id)
        return self._transition(operation)

    def cancel(self, intent_id: str, strategy: str, now_ns: int):
        def operation(state):
            self._clock(state, now_ns)
            self._advance(state, now_ns)
            order = state["orders"].get(intent_id)
            if order is None:
                return False
            if order["strategy"] != strategy:
                raise ValueError("Paper cancel belongs to a different strategy")
            if order["cancel_ns"] is None:
                self.ledger.request_cancel(intent_id, strategy)
                order["cancel_ns"] = max(order["arrival_ns"], now_ns + self.config["cancel_latency_ns"])
            self._advance(state, now_ns)
            return True
        return self._transition(operation)

    def advance(self, now_ns: int):
        return self._transition(lambda state: self._advance(state, now_ns))

    def _advance(self, state, now, *, inclusive=True, cancel_inclusive=True):
        self._clock(state, now)
        # Scan only admitted active orders, never terminal ledger history.
        due = []
        for order in state["orders"].values():
            if not order["active"]:
                due.append((order["arrival_ns"], 0, order["intent_id"], "arrive"))
            due.append((order["expiry_ns"], 1, order["intent_id"], "expire"))
            if order["cancel_ns"] is not None:
                due.append((order["cancel_ns"], 2, order["intent_id"], "cancel"))
        for when, _, identifier, kind in sorted(due):
            if when > now or (when == now and not inclusive):
                continue
            if when == now and kind == "cancel" and not cancel_inclusive:
                continue
            order = state["orders"].get(identifier)
            if order is None:
                continue
            if kind == "arrive":
                self._arrive(state, order, when)
            else:
                self._terminal(state, order, kind, when, "expiry" if kind == "expire" else "cancel_effective")
        state["clock"] = now

    def _book_valid(self, state, key, now):
        book = state["books"].get(key)
        if book is None or now < book["received_ns"] or now-book["received_ns"] > self.config["stale_after_ns"]:
            return None
        if not book["bids"] or not book["asks"] or max(map(D, book["bids"])) >= min(map(D, book["asks"])):
            return None
        return book

    def _queue(self, state, order):
        book = state["books"].get(order["instrument"])
        if not book or not book["bids"] or not book["asks"]:
            return None
        price = D(order["price"])
        if max(map(D, book["bids"])) < price < min(map(D, book["asks"])):
            return "0"
        levels = book["bids"] if order["side"] == "buy" else book["asks"]
        if not min(map(D, levels)) <= price <= max(map(D, levels)):
            return None
        own = sum((D(other["leaves"]) for other in state["orders"].values()
                   if other is not order and other["active"] and
                   (other["instrument"], other["side"], other["price"]) ==
                   (order["instrument"], order["side"], order["price"])), D(0))
        return str(D(levels.get(self._price(price), "0")) + own)

    def _arrive(self, state, order, now):
        book = self._book_valid(state, order["instrument"], now)
        if book is None:
            self._terminal(state, order, "reject", now, "stale_arrival")
            return
        order["source_floor"] = book["source_ns"]
        if order["time_in_force"] == "IOC":
            self._sweep(state, order, book, now)
            if order["intent_id"] in state["orders"]:
                self._terminal(state, order, "cancel", now, "ioc_remainder")
            return
        price = D(order["price"])
        crosses = price >= min(map(D, book["asks"])) if order["side"] == "buy" else price <= max(map(D, book["bids"]))
        if crosses:
            self._terminal(state, order, "reject", now, "post_only_cross")
        else:
            order["active"], order["queue"] = True, self._queue(state, order)

    def _fill(self, state, order, quantity, price, now, maker, token):
        rules = ProductSpec.from_document(order["rules"])
        quantity = min(D(order["leaves"]), quantity)
        if order["reduce_only"]:
            position = self.ledger.position(order["strategy"], order["instrument"])
            quantity = min(quantity, max(D(0), position if order["side"] == "sell" else -position))
        quantity = (quantity / rules.lot_size).to_integral_value(rounding=ROUND_FLOOR) * rules.lot_size
        if quantity <= 0:
            return D(0)
        with localcontext() as context:
            context.prec = 128
            fee = quantity * price * rules.multiplier * (rules.maker_fee_rate if maker else rules.taker_fee_rate)
        order["fill_serial"] += 1
        identifier = stable_hash(["paper", order["intent_id"], order["fill_serial"], token])
        if not self.ledger.apply_fill(identifier, order["intent_id"], quantity, price,
                fee=fee, fee_currency=rules.settlement_currency, order_id=order["intent_id"],
                position_side="net", group_id=order["group_id"]):
            raise RuntimeError("Paper fill dedup disagrees with durable matching state")
        order["leaves"] = str(D(order["leaves"]) - quantity)
        state["fills"] += 1
        if D(order["leaves"]) == 0:
            state["orders"].pop(order["intent_id"])
        return quantity

    def _sweep(self, state, order, book, now):
        levels = book["asks"] if order["side"] == "buy" else book["bids"]
        for price in sorted(map(D, levels), reverse=order["side"] == "sell"):
            outside = price > D(order["price"]) if order["side"] == "buy" else price < D(order["price"])
            if outside:
                break
            token = order["side"] + ":" + str(price)
            available = max(D(0), D(levels[str(price)]) - D(book["used"].get(token, "0")))
            used = self._fill(state, order, available, price, now, False, [book["event_id"], token])
            book["used"][token] = str(D(book["used"].get(token, "0")) + used)
            if not D(order["leaves"]):
                break

    def _receipt(self, state, event):
        now = self._time(event.receive_ts_ns)
        token = event.event_id
        fingerprint = stable_hash([event.model_dump(mode="json")])
        if token in state["receipts"]:
            if state["receipts"][token] != fingerprint:
                raise ValueError("Paper input identity has conflicting contents")
            return None
        self._clock(state, now)
        if event.ingest_mode != "realtime":
            raise ValueError("Paper matching cannot consume historical backfill")
        if event.instrument.venue.value != self.ledger.scope.venue:
            raise ValueError("Paper market event belongs to a different account venue")
        if len(state["receipts"]) >= self.config["max_receipts"]:
            raise ValueError("Paper receipt capacity exceeded; dedup history cannot be silently discarded")
        state["receipts"][token] = fingerprint
        return now

    def on_book(self, event: OrderBookEvent):
        def operation(state):
            now = self._receipt(state, event)
            if now is None:
                return False
            self._advance(state, now, inclusive=False)
            key = event.instrument.key
            previous = state["books"].get(key)
            if event.update_type == BookUpdateType.DELTA:
                raise ValueError("Paper matcher requires a reconstructed full book; raw delta is unsupported")
            book = {"bids": {}, "asks": {}}
            generation = [event.sync_generation, event.connection_id, event.collector_run_id]
            if key in state["gap_generations"]:
                old_generation = state["gap_generations"][key]
                if not any(value is not None for value in generation) or generation == old_generation:
                    raise ValueError("Paper resync requires an explicit new snapshot generation")
            if previous and previous["generation"] != generation:
                raise ValueError("Paper snapshot generation changed without an explicit gap")
            for side in ("bids", "asks"):
                rows = getattr(event, side)
                if len(rows) > self.config["max_depth"]:
                    raise ValueError("Paper observed-depth capacity exceeded")
                for row in rows:
                    if not row.price.is_finite() or row.price <= 0 or not row.quantity.is_finite() or row.quantity < 0:
                        raise ValueError("Invalid paper book level")
                    price = self._price(row.price)
                    if row.quantity:
                        book[side][price] = str(row.quantity)
                    else:
                        book[side].pop(price, None)
                if len(book[side]) > self.config["max_depth"]:
                    raise ValueError("Paper observed-depth capacity exceeded")
            sequence = event.final_update_id if event.final_update_id is not None else event.sequence
            advanced = False
            if previous:
                old_sequence = previous["sequence"]
                if isinstance(sequence, int) and isinstance(old_sequence, int):
                    if sequence < old_sequence:
                        raise ValueError("Paper native book sequence regression")
                    advanced = sequence > old_sequence
                # Opaque/absent native sequence has no invented ordering. Its
                # displayed volume cannot replenish consumption until a fenced
                # generation change following an explicit gap.
            used = previous["used"] if previous and not advanced else {}
            book.update(received_ns=now, source_ns=event.source_ts_ns, event_id=event.event_id,
                        sequence=sequence, generation=generation, used=used)
            state["books"][key] = book
            if self._book_valid(state, key, now) is None:
                raise ValueError("Paper book is empty or crossed")
            state["blocked"].pop(key, None)
            state["gap_generations"].pop(key, None)
            for order in state["orders"].values():
                if order["instrument"] == key and order["active"] and order["queue"] is None:
                    order["queue"] = self._queue(state, order)
            self._advance(state, now)
            return True
        return self._transition(operation)

    def on_gap(self, instrument_key: str, now_ns: int, reason: str):
        """Invalidate queue evidence and retain reservations until cancel is effective."""
        if not isinstance(reason, str) or not reason or len(reason.encode()) > 1024:
            raise ValueError("Paper gap requires a bounded reason")
        def operation(state):
            self._clock(state, now_ns)
            self._advance(state, now_ns, inclusive=False)
            previous = state["books"].pop(instrument_key, None)
            if previous:
                state["gap_generations"][instrument_key] = previous["generation"]
            elif instrument_key not in state["gap_generations"]:
                state["gap_generations"][instrument_key] = None
            state["blocked"][instrument_key] = reason
            for order in state["orders"].values():
                if order["instrument"] != instrument_key:
                    continue
                order["queue"] = None
                if order["cancel_ns"] is None:
                    self.ledger.request_cancel(order["intent_id"], order["strategy"])
                    order["cancel_ns"] = max(order["arrival_ns"], now_ns+self.config["cancel_latency_ns"])
            self._advance(state, now_ns)
        return self._transition(operation)

    def on_trade(self, event: TradeEvent):
        def operation(state):
            now = self._receipt(state, event)
            if now is None:
                return False
            if not event.quantity.is_finite() or event.quantity <= 0 or not event.price.is_finite() or event.price <= 0:
                raise ValueError("Invalid paper trade")
            self._advance(state, now, cancel_inclusive=False)
            book = self._book_valid(state, event.instrument.key, now)
            if book and event.aggressor_side != Side.UNKNOWN:
                maker_side = "sell" if event.aggressor_side == Side.BUY else "buy"
                candidates = [order for order in state["orders"].values() if order["active"] and
                              order["instrument"] == event.instrument.key and order["side"] == maker_side]
                candidates.sort(key=lambda order: (D(order["price"]) if maker_side == "sell" else -D(order["price"]),
                                                   order["arrival_ns"], order["intent_id"]))
                remaining = event.quantity
                for order in candidates:
                    if event.source_ts_ns and order["source_floor"] and event.source_ts_ns < order["source_floor"]:
                        continue
                    price = D(order["price"])
                    reaches = event.price >= price if maker_side == "sell" else event.price <= price
                    if not reaches or order["queue"] is None:
                        continue
                    if event.price != price:
                        order["queue"], available = "0", event.quantity
                    else:
                        ahead = min(D(order["queue"]), event.quantity)
                        order["queue"] = str(D(order["queue"]) - ahead)
                        available = event.quantity - ahead
                    remaining -= self._fill(state, order, min(available, remaining), price, now, True, event.event_id)
            self._advance(state, now)
            return True
        return self._transition(operation)

    def report(self):
        return {"mode": "paper", "external_mutations": 0, "clock_ns": self._state["clock"],
                "open_orders": deepcopy(self._state["orders"]), "fills": self._state["fills"],
                "receipt_count": len(self._state["receipts"]), "limits": dict(self.config),
                "blocked": deepcopy(self._state["blocked"])}
