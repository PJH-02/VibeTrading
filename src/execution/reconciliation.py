"""Bounded factual native/private reconciliation for a PAPER projection.

This module never submits venue actions. Native source identity and PAPER ledger
scope are recorded separately; readiness is only a freshness gate for local
projection consumers.
"""
from __future__ import annotations

import copy
import asyncio
import json
import time
from decimal import Decimal, InvalidOperation


def _identity(value, name):
    if not isinstance(value, str) or not value or len(value.encode()) > 4096:
        raise ValueError(name + " must be a bounded explicit string")
    return value


def _json_value(value, depth=0):
    if depth > 32:
        raise ValueError("Native document nesting exceeds limit")
    if value is None or type(value) in (str, bool, int):
        if type(value) is int and not -(2**63) <= value < 2**63:
            raise ValueError("Native integer exceeds exact envelope")
        if isinstance(value, str) and len(value.encode()) > 4096:
            raise ValueError("Native string exceeds exact envelope")
        return
    if isinstance(value, float):
        raise ValueError("Native facts cannot contain binary floats")
    if isinstance(value, list):
        for item in value:
            _json_value(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Native document keys must be strings")
            _json_value(key, depth + 1)
            _json_value(item, depth + 1)
        return
    raise ValueError("Native document contains a non-JSON value")


def _canonical_scope(source_scope_key, ledger):
    if len(source_scope_key.encode()) > 4096:
        raise ValueError("Source scope exceeds 4096 bytes")
    try:
        body = json.loads(source_scope_key)
    except (TypeError, ValueError):
        raise ValueError("Source scope must use a canonical native scope key") from None
    # AccountScope.key serializes Unicode directly; PerpScope.key uses the
    # standard JSON ASCII escape form. Preserve each native scope's canonical
    # encoder rather than imposing one representation across both contracts.
    if json.dumps(body, sort_keys=isinstance(body, dict), separators=(",", ":"),
                   ensure_ascii=isinstance(body, dict)) != source_scope_key:
        raise ValueError("Source scope key is not canonical")
    if isinstance(body, list) and len(body) == 4 and all(isinstance(v, str) for v in body):
        venue, account, product, environment = body
        if venue != ledger.scope.venue or account != ledger.scope.account or product != ledger.scope.product:
            raise ValueError("Source AccountScope does not match the PAPER projection")
        tag = product.strip().casefold().replace("-", "_")
        if venue == "binance":
            if tag not in {"cash", "spot", "cash_spot", "linear_perpetual", "linearperpetual", "perp", "perpetual"} or environment not in {"sandbox", "live"}:
                raise ValueError("Unsupported Binance AccountScope product or environment")
        elif venue == "kiwoom":
            if tag not in {"cash", "spot", "cash_spot"} or environment not in {"mock", "live"}:
                raise ValueError("Unsupported Kiwoom AccountScope product or environment")
        else:
            raise ValueError("Unsupported native AccountScope venue")
        return "account", product
    if isinstance(body, dict) and set(body) == {
            "venue", "product", "environment", "account_alias", "settlement_currency", "subaccount"}:
        from execution.perp_execution import PerpScope
        if not all(isinstance(value, str) for value in body.values()):
            raise ValueError("Native PerpScope fields must be explicit strings")
        PerpScope(**body)  # Reuse the existing five-venue product/account validation.
        if (body["venue"] != ledger.scope.venue or body["account_alias"] != ledger.scope.account
                or body["product"] != "linear_perpetual"
                or body["environment"] not in {"sandbox", "live"}):
            raise ValueError("Source PerpScope does not match the PAPER projection identity")
        # AccountScope.product is a venue/account namespace, not a product
        # contract. The actual product family and settlement are checked from
        # each immutable admitted ProductSpec when binding orders.
        return "linear_perpetual", body["product"]
    raise ValueError("Unsupported canonical native scope shape")


def _number(value, name, *, positive=False, zero=False):
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(name + " must be an exact decimal value")
    try:
        number = Decimal(value if isinstance(value, str) else str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(name + " must be an exact decimal value") from None
    if not number.is_finite() or (positive and number <= 0) or (not positive and not zero and number == 0):
        raise ValueError(name + " is outside its valid range")
    return number


class ReconciliationSession:
    """One account-bound synchronizer. All mutation is a ledger transaction."""
    def __init__(self, ledger, source_scope_key, *, max_orders, max_events,
                 max_document_bytes, max_staleness_ns, clock_ns=time.time_ns,
                 operation_timeout_seconds=10.0):
        if getattr(ledger.mode, "value", ledger.mode) != "paper":
            raise ValueError("Reconciliation projection is PAPER-only")
        if ledger.transaction_active:
            raise RuntimeError("Reconciliation construction cannot join a caller-owned transaction")
        self.ledger = ledger
        self.source_scope_key = _identity(source_scope_key, "source scope")
        self.source_family, self.source_product = _canonical_scope(self.source_scope_key, ledger)
        self.scope_key = ledger.scope.key
        for name, value in (("max_orders", max_orders), ("max_events", max_events),
                            ("max_document_bytes", max_document_bytes),
                            ("max_staleness_ns", max_staleness_ns)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(name + " must be a positive integer")
        self.max_orders, self.max_events = max_orders, max_events
        self.max_document_bytes, self.max_staleness_ns = max_document_bytes, max_staleness_ns
        if isinstance(operation_timeout_seconds, bool) or not isinstance(operation_timeout_seconds, (int, float)) or not 0 < operation_timeout_seconds <= 300:
            raise ValueError("operation_timeout_seconds must be in (0, 300]")
        self.operation_timeout_seconds = float(operation_timeout_seconds)
        self.clock_ns = clock_ns
        self._revision = 0
        self._generation = 0
        self._version = 0
        self._ready = False
        self._started = False
        self._last_snapshot_ns = None
        self._orders = {}
        self._events = []
        self._unknown_fees = set()
        self._baseline_account_version = None
        self._baseline_ledger_report = None
        self._fingerprint = json.dumps([self.scope_key, self.source_scope_key, max_orders,
                                        max_events, max_document_bytes, max_staleness_ns,
                                        operation_timeout_seconds],
                                       separators=(",", ":"))
        saved = ledger.load_reconciliation_state()
        self._revision = saved["revision"]
        previous = saved["state"] or {}
        if previous.get("fingerprint") not in (None, self._fingerprint):
            raise ValueError("Reconciliation source/config binding changed")
        if previous:
            persisted = json.dumps(previous, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                   allow_nan=False)
            if len(persisted.encode()) > max_document_bytes:
                raise ValueError("Persisted reconciliation document exceeds configured limit")
            # A process restart invalidates freshness and every bound order is
            # unknown until native facts are recovered again.
            self._generation = int(previous.get("generation", 0)) + 1
            self._version = int(previous.get("version", 0))
            self._orders = copy.deepcopy(previous.get("orders", {}))
            self._unknown_fees = set(previous.get("unknown_fees", []))
            for order in self._orders.values():
                if order.get("status") not in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                    order["status"] = "UNKNOWN"
            self._events = copy.deepcopy(previous.get("events", []))
            self._baseline_account_version = previous.get("baseline_account_version")
        else:
            self._generation = 1
        if self._orders and len(self._orders) > max_orders:
            raise ValueError("Persisted order state exceeds configured limit")
        if len(self._events) > max_events:
            raise ValueError("Persisted reconciliation events exceed configured limit")
        if previous.get("baseline_account_version") is not None:
            self._baseline_account_version = previous["baseline_account_version"]
        self._persist("restart", gap="process restarted; reconciliation required")

    @property
    def generation(self):
        return self._generation

    def _state(self):
        return {"fingerprint": self._fingerprint, "generation": self._generation,
                "version": self._version, "orders": self._orders, "events": self._events,
                "unknown_fees": sorted(self._unknown_fees), "ready": False,
                "baseline_account_version": self._baseline_account_version}

    def _persist(self, kind, *, gap=None, event=None, publish=True):
        candidate_events = list(self._events)
        candidate_version = self._version
        if event is not None:
            if len(candidate_events) >= self.max_events:
                raise RuntimeError("Reconciliation event capacity exhausted")
            candidate_version += 1
            candidate_events.append({"version": candidate_version, "kind": kind,
                                     "event": copy.deepcopy(event), "observed_ns": self.clock_ns()})
        state = self._state()
        state["version"] = candidate_version
        state["events"] = candidate_events
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                             allow_nan=False)
        if len(encoded.encode()) > self.max_document_bytes:
            raise RuntimeError("Reconciliation document capacity exhausted")
        with self.ledger.transaction():
            next_revision = self.ledger.save_reconciliation_state(state, self._revision)
            self.ledger.set_reconciliation_gap(gap)
        staged = (next_revision, candidate_events, candidate_version)
        if publish:
            self._publish(staged)
        return staged

    def _publish(self, staged):
        self._revision, self._events, self._version = staged
        self._ready = False

    def _public_write_guard(self):
        if self.ledger.transaction_active:
            raise RuntimeError("Reconciliation public mutation cannot join a caller-owned transaction")

    def bind_order(self, intent_id, native_order_id, *, symbol):
        self._public_write_guard()
        intent_id = _identity(intent_id, "intent id")
        if native_order_id is not None:
            native_order_id = _identity(native_order_id, "native order id")
        symbol = _identity(symbol, "native symbol")
        row = self.ledger.get(intent_id)
        if not row:
            raise ValueError("Order binding must match an admitted immutable instrument")
        from execution.trading_contracts import ProductKind, ProductSpec
        spec = ProductSpec.from_document(json.loads(row["rules"]))
        if spec.instrument.raw_symbol != symbol:
            raise ValueError("Native symbol differs from immutable admitted product")
        if self.source_family == "linear_perpetual" and spec.kind != ProductKind.LINEAR_PERPETUAL:
            raise ValueError("Native scope product family differs from admitted product")
        if self.source_family == "account":
            product_tag = self.source_product.strip().casefold().replace("-", "_")
            source_venue = json.loads(self.source_scope_key)[0]
            venue_matches = spec.instrument.venue.value == source_venue
            if source_venue == "kiwoom":
                venue_matches = (spec.kind == ProductKind.CASH
                                 and spec.instrument.venue.value in {"krx", "kiwoom"}
                                 and spec.instrument.asset_class.value in {"equity", "etf"})
            if not venue_matches:
                raise ValueError("Native source venue differs from admitted product")
            if product_tag in {"cash", "spot", "cash_spot"} and spec.kind != ProductKind.CASH:
                raise ValueError("Native AccountScope cash family differs from admitted product")
            if product_tag in {"linear_perpetual", "linearperpetual", "perp", "perpetual"} and spec.kind != ProductKind.LINEAR_PERPETUAL:
                raise ValueError("Native AccountScope perpetual family differs from admitted product")
            if product_tag not in {"cash", "spot", "cash_spot", "linear_perpetual", "linearperpetual", "perp", "perpetual"}:
                raise ValueError("Native AccountScope product family is not explicit")
        if self.source_family == "linear_perpetual":
            body = json.loads(self.source_scope_key)
            if spec.settlement_currency != body["settlement_currency"]:
                raise ValueError("Native PerpScope settlement differs from admitted product")
        if len(self._orders) >= self.max_orders and intent_id not in self._orders:
            raise RuntimeError("Reconciliation order capacity exhausted")
        for existing in self._orders.values():
            if native_order_id is not None and existing["native_order_id"] == native_order_id and existing["intent_id"] != intent_id:
                raise ValueError("Native order ID is already bound")
        old = self._orders.get(intent_id)
        item = {"intent_id": intent_id, "client_order_id": row["client_order_id"],
                "native_order_id": native_order_id, "symbol": symbol,
                "instrument_key": row["instrument"], "status": "UNKNOWN",
                "cumulative_quantity": "0"}
        if old:
            if (old["client_order_id"] != item["client_order_id"] or old["symbol"] != symbol
                    or old["instrument_key"] != row["instrument"]
                    or (old["native_order_id"] is not None and native_order_id not in (None, old["native_order_id"]))):
                raise ValueError("Immutable native order binding collision")
            item.update({"status": old["status"], "cumulative_quantity": old["cumulative_quantity"]})
            if native_order_id is None:
                native_order_id = old["native_order_id"]
            item["native_order_id"] = native_order_id
            if item == old:
                return copy.deepcopy(old)
        previous = copy.deepcopy(self._orders)
        self._orders[intent_id] = item
        try:
            self._persist("order_bound", gap="native order requires reconciliation", event=item)
        except BaseException:
            self._orders = previous
            self._ready = False
            raise
        return copy.deepcopy(item)

    def mark_gap(self, reason):
        self._public_write_guard()
        self._ready = False
        self._generation += 1
        self._started = False
        self._persist("gap", gap=_identity(str(reason), "gap reason"), event={"reason": str(reason)})

    async def start(self, authenticate, subscribe):
        self._public_write_guard()
        self._ready = False
        self._generation += 1
        generation = self._generation
        self._started = False
        self._persist("starting", gap="authentication and complete recovery required")
        try:
            auth = await asyncio.wait_for(authenticate(), timeout=self.operation_timeout_seconds)
            if generation != self._generation:
                raise RuntimeError("Stale reconciliation generation")
            self._check_ack(auth)
            sub = await asyncio.wait_for(subscribe(), timeout=self.operation_timeout_seconds)
            if generation != self._generation:
                raise RuntimeError("Stale reconciliation generation")
            self._check_ack(sub)
            self._started = True
            self._persist("subscribed", gap="complete history and snapshot required")
        except BaseException:
            self._ready = False
            self._started = False
            try:
                self._persist("start_failed", gap="authentication/subscription failed")
            except BaseException:
                pass
            raise
        return generation

    def _check_ack(self, result):
        if result is True:
            return
        if (isinstance(result, dict) and result.get("success") is True
                and result.get("scope_key") == self.source_scope_key):
            return
        raise ValueError("Source callback did not explicitly acknowledge this scope")

    async def stop(self):
        self._public_write_guard()
        self._generation += 1
        self._started = self._ready = False
        self._persist("stopped", gap="session stopped; reconciliation required")

    def _check_generation(self, generation):
        if generation is not None and (isinstance(generation, bool) or generation != self._generation):
            raise ValueError("Stale source generation")

    def ingest(self, event, generation=None):
        self._public_write_guard()
        try:
            self._check_generation(generation)
        except ValueError:
            self.mark_gap("stale source generation")
            raise
        try:
            _json_value(event)
            if len(json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                               allow_nan=False).encode()) > self.max_document_bytes:
                raise ValueError("Native event exceeds configured document limit")
        except BaseException as exc:
            self._ready = False
            try:
                self.mark_gap("invalid native event envelope: " + type(exc).__name__)
            except BaseException:
                pass
            raise
        if not isinstance(event, dict) or event.get("scope_key") != self.source_scope_key:
            self.mark_gap("missing or foreign source scope")
            raise ValueError("Native fact source scope mismatch")
        kind = event.get("kind")
        prior_orders = copy.deepcopy(self._orders)
        prior_unknown_fees = set(self._unknown_fees)
        staged = None
        try:
            with self.ledger.transaction():
                normalized = self._apply_fact(event, kind)
                if normalized is None:
                    return False
                staged = self._persist(kind, gap="private reconciliation pending", event=normalized,
                                        publish=False)
            self._publish(staged)
        except BaseException as exc:
            self._orders = prior_orders
            self._unknown_fees = prior_unknown_fees
            self._ready = False
            # Invalid/conflicting facts close the account gate; the financial
            # transaction itself rolls back before the durable gap is recorded.
            try:
                self._persist("invalid_fact", gap="invalid native fact: " + type(exc).__name__)
            except BaseException:
                # Memory remains closed. If storage itself is unavailable, the
                # next process always starts blocked even if no gap could commit.
                pass
            raise
        return True

    def _find_order(self, event):
        native_id, client_id = event.get("order_id"), event.get("client_order_id")
        if native_id is None and client_id is None:
            raise ValueError("Native fact requires an order or admitted client ID")
        by_native = [o for o in self._orders.values() if native_id is not None
                     and o["native_order_id"] == native_id]
        by_client = [o for o in self._orders.values() if client_id is not None
                     and o["client_order_id"] == client_id]
        if native_id is not None and not by_native:
            # A lost ACK may be repaired only by an explicit fact carrying both
            # the exact admitted client ID and a previously unbound native ID.
            if len(by_client) == 1 and by_client[0]["native_order_id"] is None:
                order = by_client[0]
                for other in self._orders.values():
                    if other["native_order_id"] == native_id:
                        raise ValueError("Native order ID collides with another binding")
                order["native_order_id"] = _identity(native_id, "native order id")
                return self._validate_order_symbol(order, event)
            raise ValueError("Unknown native order ID cannot be overridden by client ID")
        if client_id is not None and not by_client:
            raise ValueError("Unknown client order ID")
        if by_native and by_client and by_native[0]["intent_id"] != by_client[0]["intent_id"]:
            raise ValueError("Native and client IDs refer to different admitted orders")
        candidates = by_native or by_client
        if len(candidates) != 1:
            raise ValueError("Native order does not uniquely bind to an admitted intent")
        order = candidates[0]
        return self._validate_order_symbol(order, event)

    @staticmethod
    def _validate_order_symbol(order, event):
        if event.get("symbol") != order["symbol"]:
            raise ValueError("Native symbol differs from bound immutable product")
        return order

    def _apply_fact(self, event, kind):
        if kind == "gap":
            self._ready = False
            self._started = False
            self._generation += 1
            return {"reason": str(event.get("reason", "source gap"))}
        if kind == "account":
            self._ready = False
            return copy.deepcopy(event)
        if kind == "order":
            order = self._find_order(event)
            status = event.get("status")
            if status not in {"OPEN", "PARTIAL", "CANCEL_PENDING", "CANCELED", "FILLED", "REJECTED", "EXPIRED", "UNKNOWN"}:
                raise ValueError("Unknown native order status")
            cumulative = _number(event.get("cumulative_quantity"), "cumulative quantity", zero=True)
            proof = event.get("terminal_proven")
            if type(proof) is not bool:
                raise ValueError("Terminal proof must be explicit")
            row = self.ledger.get(order["intent_id"])
            if cumulative != Decimal(row["filled"]):
                raise ValueError("Native cumulative quantity does not match durable fills")
            terminal = status in {"CANCELED", "REJECTED", "EXPIRED"}
            if terminal and not proof:
                raise ValueError("Terminal outcome lacks native proof")
            if status == "FILLED" and not proof:
                raise ValueError("FILLED outcome lacks native proof")
            if status == "FILLED" and cumulative != Decimal(row["quantity"]):
                raise ValueError("FILLED status lacks complete admitted quantity")
            if order["status"] == "CANCEL_PENDING" and status in {"OPEN", "PARTIAL", "UNKNOWN"}:
                return {**copy.deepcopy(event), "stale_ack": True}
            if order["status"] in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"} and status in {
                    "OPEN", "PARTIAL", "CANCEL_PENDING", "UNKNOWN"}:
                # Delayed acknowledgements cannot resurrect an already factual
                # terminal outcome.
                return {**copy.deepcopy(event), "stale_ack": True}
            if terminal and row["state"] != status:
                outcome = {"CANCELED": self.ledger.cancel, "REJECTED": self.ledger.reject,
                           "EXPIRED": self.ledger.expire}[status]
                outcome(order["intent_id"], confirmed=True,
                        evidence={"native": event, "scope_key": self.source_scope_key})
            elif status == "CANCEL_PENDING":
                self.ledger.request_cancel(order["intent_id"], row["strategy"])
            if row["state"] == "FILLED":
                order["status"] = "FILLED"
            else:
                order["status"] = status
            order["cumulative_quantity"] = str(cumulative)
            return copy.deepcopy(event)
        if kind == "fill":
            # Configured Kiwoom CASH cost is normalized before immutable event
            # deduplication, so native fee fields never become authority.
            is_kiwoom = self.source_family == "account" and json.loads(self.source_scope_key)[0] == "kiwoom"
            if is_kiwoom:
                from execution.trading_contracts import ProductSpec
                from execution.account_fees import recover_account_fee_policy
                probe_order = self._find_order(event)
                probe_row = self.ledger.get(probe_order["intent_id"])
                probe_rules = ProductSpec.from_document(json.loads(probe_row["rules"]))
                account_fee_policy = recover_account_fee_policy(probe_rules, self.ledger.scope)
                if account_fee_policy is not None:
                    quantity = _number(event.get("quantity"), "fill quantity", positive=True)
                    price = _number(event.get("price"), "fill price", positive=True)
                    amount = account_fee_policy.calculate_fee(quantity, price, probe_rules.multiplier)
                    event = {**copy.deepcopy(event), "fee": str(amount), "fee_currency": probe_rules.settlement_currency,
                             "fee_known": True, "fee_basis": "ACCOUNT_CONFIG", "fee_rate": str(account_fee_policy.rate)}
                else:
                    event = {**copy.deepcopy(event), "fee": None, "fee_known": False,
                             "fee_currency": None, "fee_basis": "CONFIG_REQUIRED"}
            event_id = _identity(event.get("event_id"), "native fill event id")
            previous_fill = next((entry["event"] for entry in self._events
                                  if isinstance(entry.get("event"), dict)
                                  and entry.get("kind") == "fill"
                                  and entry["event"].get("event_id") == event_id), None)
            if previous_fill is not None:
                # Validate all supplied identities against the immutable
                # admitted binding. REST may omit client ID while private
                # stream supplies it for this same native fill.
                incoming_order = self._find_order(event)
                previous_order = self._find_order(previous_fill)
                if incoming_order["intent_id"] != previous_order["intent_id"]:
                    raise ValueError("Immutable native fill identity collision across orders")
                incoming_flag = event.get("fee_known")
                if incoming_flag is not None and type(incoming_flag) is not bool:
                    raise ValueError("fee_known must be an exact boolean")
                incoming_fee_known = event.get("fee") is not None and incoming_flag is not False
                if (previous_fill.get("fee_known") is not incoming_fee_known
                        or previous_fill.get("symbol") != event.get("symbol")
                        or previous_fill.get("quantity") != event.get("quantity")
                        or previous_fill.get("price") != event.get("price")
                        or previous_fill.get("fee") != event.get("fee")
                        or previous_fill.get("fee_currency") != event.get("fee_currency")):
                    raise ValueError("Immutable native fill identity collision")
                return None
            order = self._find_order(event)
            qty = _number(event.get("quantity"), "fill quantity", positive=True)
            price = _number(event.get("price"), "fill price", positive=True)
            explicit_fee_known = event.get("fee_known")
            if explicit_fee_known is not None and type(explicit_fee_known) is not bool:
                raise ValueError("fee_known must be an exact boolean")
            if explicit_fee_known is False and event.get("fee") is not None:
                raise ValueError("Explicitly unknown fee cannot carry a numeric fee")
            fee_known = event.get("fee") is not None and explicit_fee_known is not False
            fee = _number(event["fee"], "fee", zero=True) if fee_known else Decimal(0)
            fee_currency = event.get("fee_currency")
            if fee_known and not isinstance(fee_currency, str):
                raise ValueError("Known fee requires currency")
            with self.ledger.transaction():
                accepted = self.ledger.apply_fill(event_id, order["intent_id"], qty, price,
                                                  fee=fee, fee_currency=fee_currency,
                                                  order_id=order["native_order_id"])
            if not accepted:
                return None
            if not fee_known:
                self._unknown_fees.add(event_id)
            current = self.ledger.get(order["intent_id"])
            order["cumulative_quantity"] = current["filled"]
            if current["state"] == "FILLED":
                order["status"] = "FILLED"
            elif order["status"] not in {"CANCELED", "REJECTED", "EXPIRED", "CANCEL_PENDING"}:
                order["status"] = "PARTIAL"
            if not fee_known:
                return {**copy.deepcopy(event), "fee_known": False, "provisional_paper_fee": "0"}
            return {**copy.deepcopy(event), "fee_known": True}
        if kind == "funding":
            event_id = _identity(event.get("event_id"), "funding event id")
            amount = _number(event.get("amount"), "funding amount", zero=True)
            currency = _identity(event.get("currency"), "funding currency")
            symbol = _identity(event.get("symbol"), "funding symbol")
            matches = {order["instrument_key"] for order in self._orders.values()
                       if order["symbol"] == symbol}
            if len(matches) != 1:
                raise ValueError("Funding symbol does not uniquely map to an admitted product")
            with self.ledger.transaction():
                accepted = self.ledger.apply_funding(event_id, matches.pop(), amount, currency,
                                                     {"source_scope_key": self.source_scope_key})
            if not accepted:
                return None
            return copy.deepcopy(event)
        raise ValueError("Unsupported reconciliation fact kind")

    async def reconcile(self, source, *, start_ns, end_ns):
        self._public_write_guard()
        self._ready = False
        generation = self._generation
        if not self._started:
            raise RuntimeError("Session must authenticate and subscribe before recovery")
        for name, value in (("start_ns", start_ns), ("end_ns", end_ns)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(name + " must be an explicit timestamp")
        if start_ns > end_ns:
            raise ValueError("Invalid reconciliation time window")
        baseline_account_version = self.ledger.account_version()
        baseline_event_version = self._version
        try:
            history = await asyncio.wait_for(source.history(start_ns, end_ns),
                                             timeout=self.operation_timeout_seconds)
            self._check_generation(generation)
            if (self._version != baseline_event_version
                    or self.ledger.account_version() != baseline_account_version):
                raise ValueError("Concurrent mutation invalidated pending history")
            _json_value(history)
            if len(json.dumps(history, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False).encode()) > self.max_document_bytes:
                raise ValueError("History document exceeds configured limit")
            if (not isinstance(history, dict) or history.get("scope_key") != self.source_scope_key
                    or history.get("complete") is not True or history.get("start_ns") != start_ns
                    or history.get("end_ns") != end_ns):
                raise ValueError("Native history is incomplete or mismatched")
            for event in list(history.get("fills", [])) + list(history.get("funding", [])):
                self._check_generation(generation)
                self.ingest(event, generation)
            base_version = self.ledger.account_version()
            base_event_version = self._version
            snapshot = await asyncio.wait_for(source.account_snapshot(),
                                              timeout=self.operation_timeout_seconds)
            self._check_generation(generation)
            _json_value(snapshot)
            if len(json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False).encode()) > self.max_document_bytes:
                raise ValueError("Snapshot document exceeds configured limit")
            if self._version != base_event_version or self.ledger.account_version() != base_version:
                raise ValueError("Concurrent account mutation invalidated snapshot")
            if (not isinstance(snapshot, dict) or snapshot.get("scope_key") != self.source_scope_key
                    or snapshot.get("complete") is not True or snapshot.get("identity_verified") is not True):
                raise ValueError("Account snapshot is incomplete or unverified")
            positions = snapshot.get("positions")
            expected_instruments = {order["instrument_key"] for order in self._orders.values()}
            if (not isinstance(positions, dict) or set(positions) != expected_instruments
                    or not isinstance(snapshot.get("account"), dict)
                    or not isinstance(snapshot.get("orders"), list)):
                raise ValueError("Snapshot must explicitly cover bound positions, account and orders")
            observed = snapshot.get("observed_ns")
            now = self.clock_ns()
            if isinstance(observed, bool) or not isinstance(observed, int) or observed > now or now - observed > self.max_staleness_ns:
                raise ValueError("Account snapshot is stale")
            for fact in snapshot.get("orders", []):
                self.ingest(fact, generation)
            covered = set()
            for fact in snapshot.get("orders", []):
                bound = self._find_order(fact)
                covered.add(bound["intent_id"])
            if covered != set(self._orders):
                raise ValueError("Snapshot does not cover every bound native order")
            # Applying explicit order facts changes session version, so fence
            # physical comparison on the now-current values.
            expected_version = self.ledger.account_version()
            previous_baseline = self._baseline_account_version
            self._baseline_account_version = expected_version
            with self.ledger.transaction():
                self.ledger.reconcile_positions("reconcile:" + str(generation) + ":" + str(observed),
                                                snapshot.get("positions", {}),
                                                {"source_scope_key": self.source_scope_key,
                                                 "observed_ns": observed},
                                                account_version=expected_version)
                remaining_gaps = set(self.ledger.report()["gaps"]) - {"@private"}
                if remaining_gaps:
                    raise ValueError("Ledger gaps remain after position comparison")
                if self._unknown_fees:
                    raise ValueError("Configured account fee is required for Kiwoom fill cost attribution" if self.source_family == "account" and json.loads(self.source_scope_key)[0] == "kiwoom" else "Native fill fee remains unknown")
                if any(o["status"] in {"UNKNOWN", "CANCEL_PENDING"} for o in self._orders.values()):
                    raise ValueError("An admitted native order remains unresolved")
                if self._revision != self.ledger.load_reconciliation_state()["revision"]:
                    raise ValueError("Reconciliation revision changed concurrently")
                next_revision = self.ledger.save_reconciliation_state(self._state(), self._revision)
                self.ledger.set_reconciliation_gap(None)
            self._revision = next_revision
            self._last_snapshot_ns = observed
            self._baseline_ledger_report = self.ledger.report()
            self._ready = True
            return self.snapshot()
        except BaseException as exc:
            if "previous_baseline" in locals():
                self._baseline_account_version = previous_baseline
            self._ready = False
            try:
                self._persist("reconcile_failed", gap="reconciliation failed: " + type(exc).__name__)
            except BaseException:
                pass
            raise

    def snapshot(self):
        self._public_write_guard()
        if self._ready:
            try:
                stored = self.ledger.load_reconciliation_state()
                if (self.ledger.account_version() != self._baseline_account_version
                        or self.ledger.report() != self._baseline_ledger_report
                        or stored["revision"] != self._revision):
                    self.mark_gap("external ledger version or reconciliation revision changed")
            except BaseException:
                self._ready = False
        age = None if self._last_snapshot_ns is None else self.clock_ns() - self._last_snapshot_ns
        return copy.deepcopy({"scope_key": self.source_scope_key, "projection_scope_key": self.scope_key,
                              "generation": self._generation, "version": self._version,
                              "ready": bool(self._ready and age is not None and 0 <= age <= self.max_staleness_ns
                                            and self.ledger.readiness()["ready"]),
                              "orders": self._orders, "account": self.ledger.report(),
                              "observed_ns": self._last_snapshot_ns})

    def events(self, after_version=0):
        self._public_write_guard()
        if isinstance(after_version, bool) or not isinstance(after_version, int) or after_version < 0:
            raise ValueError("after_version must be a nonnegative integer")
        return tuple(copy.deepcopy(e) for e in self._events if e["version"] > after_version)

    def require_ready(self):
        snapshot = self.snapshot()
        if not snapshot["ready"] or not self._started:
            raise RuntimeError("Reconciliation is not ready for new paper-projection risk")
        return snapshot
