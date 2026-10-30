"""Explicit local fixture for source-owned maker quotes and order feedback.

All prices, quantities, account binding, and optional FOK probe behavior come
from the caller's local config. This module is not auto-discovered.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from core.models import OrderDestination, OrderIntent, Side
from execution.trading_contracts import (AccountScope, ActionKind, ExecutionAction,
    StrategyActionPlan, StrategyOrderContext, StrategyOrderEvent, TradingMode)

STRATEGY_ID = "example_crypto_order_policy"
ACTION_PROTOCOL_VERSION = 1
SUBSCRIPTIONS = [{"venue": "binance", "symbol": "BTCUSDT", "event_type": "order_book"}]
WARMUP = {"bars": 0}


class ExampleCryptoOrderPolicy:
    def __init__(self):
        self.config = {}
        self.scope = None
        self.instrument = None
        self.sequence = 0
        self.pending: dict[str, dict] = {}
        self.fok_done = False
        self.ioc_done = False
        self.trace: list[dict] = []

    def configure(self, config):
        self.config = dict(config)
        return None

    def validate_registration(self, facts):
        account_key = self.config.get("account_key")
        selected = facts["accounts"].get(account_key)
        if selected is None:
            raise ValueError("Example requires one explicit configured account_key")
        raw = selected["scope"]
        self.scope = AccountScope(raw["venue"], raw["account"], raw["product"], raw["environment"])
        if selected["mode"] != "paper" or raw["environment"] != "paper":
            raise ValueError("Example crypto policy is PAPER-only")
        self.account_key = account_key
        self.job_id = facts["job_id"]
        self.config_version = facts["config_version"]
        return None

    def _plan(self, event_ts_ns, actions, label):
        self.sequence += 1
        return StrategyActionPlan(f"{STRATEGY_ID}:{label}:{self.sequence}", STRATEGY_ID,
            self.config_version, event_ts_ns, tuple(actions))

    def _quote(self, side, price, quantity, now_ns, label, *, tif="GTC", post_only=True,
               target=None, action_kind=ActionKind.NEW):
        self.sequence += 1
        sequence = self.sequence
        intent = OrderIntent(
            intent_id=f"{STRATEGY_ID}:intent:{sequence}", job_id=self.job_id,
            signal_id=f"{STRATEGY_ID}:source:{label}:{sequence}",
            destination=OrderDestination.REAL, instrument=self.instrument, side=side,
            created_ts_ns=now_ns, client_order_id=f"{STRATEGY_ID}:client:{sequence}",
            quantity=Decimal(str(quantity)), limit_price=Decimal(str(price)),
            metadata={"source_policy": STRATEGY_ID, "quote_label": label})
        action = ExecutionAction(
            action_id=f"{STRATEGY_ID}:action:{sequence}", action=action_kind,
            scope=self.scope, strategy_id=STRATEGY_ID,
            config_version=self.config_version, mode=TradingMode.PAPER,
            expiry_ns=now_ns + int(self.config.get("action_ttl_ns", 60_000_000_000)),
            intent=intent, target_order_id=target, post_only=post_only,
            reduce_only=False, time_in_force=tif)
        self.pending[intent.intent_id] = {"side": side.value, "remaining": str(quantity), "tif": tif}
        return action

    def _maker_pair(self, data, now_ns, label):
        book = data.latest_book(self.instrument.key) if data is not None else None
        if book is None or not book.bids or not book.asks:
            return []
        offset = Decimal(str(self.config.get("maker_offset", "0")))
        qty = Decimal(str(self.config["quantity"]))
        bid = max(level.price for level in book.bids) - offset
        ask = min(level.price for level in book.asks) + offset
        if bid <= 0 or ask <= bid:
            return []
        working = {row.get("side") for row in self.pending.values()
                   if row.get("status") in {None, "ACCEPTED", "PARTIAL_FILL", "CANCEL_PENDING", "UNKNOWN", "cancel_requested"}}
        actions = []
        if "buy" not in working:
            actions.append(self._quote(Side.BUY, bid, qty, now_ns, label + ":bid"))
        if "sell" not in working:
            actions.append(self._quote(Side.SELL, ask, qty, now_ns, label + ":ask"))
        return actions

    def on_event(self, event, data):
        if self.instrument is None:
            self.instrument = event.instrument
        if event.instrument != self.instrument or not data.ready:
            return []
        event_time = event.source_ts_ns or event.receive_ts_ns
        if not self.config.get("fok_probe", False):
            return self._plan(event_time, self._maker_pair(data, event_time, "maker"), "maker")
        if not self.fok_done:
            self.fok_done = True
            probe = self._quote(Side.BUY, event.bids[0].price if event.bids else Decimal("1"),
                self.config["quantity"], event_time, "fok-probe", tif="FOK", post_only=False)
            return self._plan(event_time, [probe], "fok")
        return []

    def on_order_event(self, event: StrategyOrderEvent, context: StrategyOrderContext):
        if len(self.trace) >= 256:
            raise RuntimeError("Fixture source feedback trace reached its explicit state bound")
        def plain(value):
            if isinstance(value, Mapping):
                return {key: plain(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [plain(item) for item in value]
            return value
        own_positions = plain(context.execution.get("virtual_positions", {}))
        self.trace.append({"event_id": event.event_id, "status": event.status,
            "quantity": str(event.quantity), "filled": str(event.filled_quantity),
            "remaining": str(event.remaining_quantity), "fill_id": event.fill_id,
            "fill_quantity": str(event.fill_quantity) if event.fill_quantity is not None else None,
            "fill_price": str(event.fill_price) if event.fill_price is not None else None,
            "fee": str(event.fee) if event.fee is not None else None,
            "virtual_positions": own_positions,
            "available": plain(context.execution.get("available", {})),
            "working_orders": len(context.execution.get("active_orders", ())),
            "quality": context.quality})
        intent_id = event.metadata.get("intent_id")
        if not intent_id:
            return self._plan(context.now_ns, [], "no-intent")
        if event.metadata.get("provenance") == "LOCAL_NOT_SENT" and event.status == "REJECTED":
            if intent_id in self.pending:
                self.pending[intent_id]["status"] = "REJECTED"
            if not self.ioc_done and self.fok_done:
                self.ioc_done = True
                self.instrument = self.instrument or self._instrument_from_context(context)
                side = Side.BUY
                price = self._best_price(context, Side.BUY, executable=True)
                action = self._quote(side, price, event.remaining_quantity, context.now_ns,
                                     "explicit-ioc", tif="IOC", post_only=False)
                return self._plan(context.now_ns, [action], "ioc-after-fok")
            return self._plan(context.now_ns, [], "rejection-observed")

        current = self.pending.setdefault(intent_id, {"side": "sell", "tif": "GTC"})
        current.update({"remaining": str(event.remaining_quantity), "status": event.status})
        if event.status == "ACCEPTED":
            # Recover the side from this strategy's own immutable working-order
            # facts after restart; no other strategy order is visible here.
            for row in context.execution.get("active_orders", ()):
                if row.get("intent_id") == intent_id:
                    self.pending[intent_id]["side"] = row["side"]
                    break
            return self._plan(context.now_ns, [], "accepted")
        if event.status == "PARTIAL_FILL":
            self.pending[intent_id]["status"] = "cancel_requested"
            side = Side(self.pending[intent_id].get("side", "buy"))
            if side == Side.BUY:
                ask = next((row for row in context.execution.get("active_orders", ())
                            if row.get("side") == "sell" and row.get("instrument") == self.instrument.key), None)
                if ask is not None:
                    ask_id = ask["intent_id"]
                    if ask_id in self.pending:
                        self.pending[ask_id]["status"] = "cancel_requested"
                    action = self._cancel_action(event, context, ask_id)
                else:
                    action = None
            else:
                action = self._cancel_action(event, context, intent_id)
            return self._plan(context.now_ns, [action] if action else [], "partial-cancel")
        if event.status in {"CANCEL_PENDING", "UNKNOWN"}:
            return self._plan(context.now_ns, [], "await-terminal")
        if event.status == "CANCELLED" and event.remaining_quantity > 0:
            row = self.pending.get(intent_id, {})
            if row.get("tif") == "IOC":
                return self._plan(context.now_ns, [], "ioc-terminal")
            row["status"] = "REPLACED"
            side = Side(row.get("side", "buy"))
            price = self._best_price(context, side)
            action = self._quote(side, price, event.remaining_quantity, context.now_ns,
                                 "confirmed-reprice", target=intent_id,
                                 action_kind=ActionKind.REPLACE)
            return self._plan(context.now_ns, [action], "confirmed-reprice")
        return self._plan(context.now_ns, [], "fact-observed")

    def _cancel_action(self, event, context, intent_id):
        scope = event.account_scope
        self.sequence += 1
        return ExecutionAction(action_id=f"{STRATEGY_ID}:cancel:{self.sequence}",
            action=ActionKind.CANCEL, scope=scope, strategy_id=STRATEGY_ID,
            config_version=self.config_version, mode=TradingMode.PAPER,
            expiry_ns=context.now_ns + int(self.config.get("action_ttl_ns", 60_000_000_000)),
            target_order_id=intent_id, post_only=False, reduce_only=False, time_in_force="GTC")

    def _best_price(self, context, side, *, executable=False):
        if context.data is not None and self.instrument is not None:
            book = context.data.latest_book(self.instrument.key)
            if book is not None and book.bids and book.asks:
                if side == Side.BUY:
                    return min(level.price for level in book.asks) if executable else max(level.price for level in book.bids)
                return max(level.price for level in book.bids) if executable else min(level.price for level in book.asks)
        return Decimal(str(self.config.get("fallback_price", "1")))

    def checkpoint_state(self):
        return {"sequence": self.sequence, "pending": self.pending,
                "fok_done": self.fok_done, "ioc_done": self.ioc_done,
                "trace": self.trace,
                "instrument": self.instrument.model_dump(mode="json") if self.instrument else None}

    def restore_state(self, state):
        from core.models import InstrumentId
        self.sequence = state["sequence"]
        self.pending = dict(state["pending"])
        self.fok_done, self.ioc_done = state["fok_done"], state["ioc_done"]
        self.trace = list(state.get("trace", []))
        self.instrument = InstrumentId.model_validate(state["instrument"]) if state["instrument"] else None


def create_strategy() -> ExampleCryptoOrderPolicy:
    return ExampleCryptoOrderPolicy()
