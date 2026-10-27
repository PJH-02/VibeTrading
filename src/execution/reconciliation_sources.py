"""Bounded, read-only bridges from existing venue adapters to reconciliation."""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import time
from datetime import datetime
from decimal import Decimal

from execution.brokers import BinanceBrokerAdapter, KiwoomBrokerAdapter
from execution.perp_execution import (
    ArcusPerpAdapter, BinancePerpAdapter, BybitPerpAdapter, GrvtPerpAdapter,
    HyperliquidPerpAdapter, IncompleteHistory,
)


_PERPS = (BinancePerpAdapter, BybitPerpAdapter, GrvtPerpAdapter,
          ArcusPerpAdapter, HyperliquidPerpAdapter)
_SUPPORTED = _PERPS + (BinanceBrokerAdapter, KiwoomBrokerAdapter)


class NativePollingSource:
    """A bounded polling source over an already constructed native adapter.

    No client, credentials, socket, or polling task is created by construction.
    """
    def __init__(self, adapter, bindings, *, identity_verifier, clock_ns,
                 max_pages=32, max_rows=4096, max_document_bytes=1048576,
                 poll_interval_ns=1000000000, max_backoff_ns=30000000000, read_timeout_s=10):
        if type(adapter) not in _SUPPORTED:
            raise TypeError("Unsupported existing native adapter")
        if not callable(identity_verifier) or inspect.iscoroutinefunction(identity_verifier):
            raise TypeError("A synchronous trusted identity verifier is required")
        if not callable(clock_ns):
            raise TypeError("clock_ns must be callable")
        for name, value, ceiling in (("max_pages", max_pages, 128), ("max_rows", max_rows, 10000),
                                     ("max_document_bytes", max_document_bytes, 8_000_000),
                                     ("poll_interval_ns", poll_interval_ns, 3_600_000_000_000),
                                     ("max_backoff_ns", max_backoff_ns, 86_400_000_000_000)):
            if type(value) is not int or value < 1 or value > ceiling:
                raise ValueError(f"{name} outside supported finite bounds")
        if not isinstance(bindings, (list, tuple)) or not 1 <= len(bindings) <= max_rows:
            raise ValueError("Explicit bounded binding list required")
        scope = adapter.scope
        scope_key = scope.key
        validated = []
        symbol_instruments = {}
        seen_order_ids = set()
        for binding in bindings:
            if not isinstance(binding, dict):
                raise ValueError("Each binding must be a mapping")
            symbol, instrument = binding.get("symbol"), binding.get("instrument_key")
            if not isinstance(symbol, str) or not symbol or len(symbol) > 64 or not isinstance(instrument, str) or not instrument or len(instrument) > 256:
                raise ValueError("Binding requires exact native symbol and instrument_key")
            key_parts = instrument.split(":")
            if len(key_parts) < 3 or any(not part or len(part) > 128 for part in key_parts):
                raise ValueError("instrument_key must be a canonical venue:asset_class:std_symbol key")
            key_venue, key_class = key_parts[0], key_parts[1]
            native_venue = getattr(scope, "venue", None)
            if type(adapter) in _PERPS:
                allowed = key_venue == native_venue and key_class == "crypto_perp"
            elif type(adapter) is BinanceBrokerAdapter:
                allowed = key_venue == "binance" and key_class == "crypto_spot"
            else:
                allowed = key_venue in {"krx", "kiwoom"} and key_class in {"equity", "etf"}
            if not allowed:
                raise ValueError("instrument_key venue/product family differs from the native adapter")
            if symbol in symbol_instruments and symbol_instruments[symbol] != instrument:
                raise ValueError("A native symbol cannot map to multiple projection instruments")
            symbol_instruments[symbol] = instrument
            if type(adapter) is KiwoomBrokerAdapter:
                adapter._kiwoom_symbol(symbol)
                if not isinstance(binding.get("day"), str) or len(binding["day"]) != 8 or not binding["day"].isdigit():
                    raise ValueError("Kiwoom binding requires an explicit YYYYMMDD day")
                if binding.get("native_order_id") is not None:
                    adapter._native_order_id(binding["native_order_id"])
            elif type(adapter) in _PERPS:
                if binding.get("client_order_id") is not None:
                    adapter._client_id(binding["client_order_id"])
            elif type(adapter) is BinanceBrokerAdapter:
                for key in ("native_order_id",):
                    if binding.get(key) is not None and (type(binding[key]) is not int or binding[key] < 0):
                        raise ValueError("Spot binding native order ID must be a nonnegative integer")
            for id_name in ("native_order_id", "client_order_id"):
                native_value = binding.get(id_name)
                if native_value is not None and (type(native_value) not in (str, int) or not str(native_value) or len(str(native_value)) > 128):
                    raise ValueError("Native order identifiers must be nonempty bounded strings or integers")
            native_key = binding.get("native_order_id", binding.get("client_order_id"))
            if native_key is not None:
                identity = (symbol, str(native_key))
                if identity in seen_order_ids:
                    raise ValueError("Duplicate native order binding")
                seen_order_ids.add(identity)
            if type(adapter) is BinanceBrokerAdapter and (not isinstance(binding.get("base_asset"), str) or not binding["base_asset"]):
                raise ValueError("Spot binding requires an explicit exact base_asset")
            row = copy.deepcopy(binding)
            validated.append(row)
        if adapter.scope.key != scope_key:
            raise ValueError("Adapter scope changed during binding validation")
        self.adapter, self.bindings = adapter, tuple(validated)
        self.scope_key = scope_key
        self.identity_verifier, self.clock_ns = identity_verifier, clock_ns
        self.max_pages, self.max_rows = max_pages, max_rows
        if type(read_timeout_s) not in (int, float) or not 0 < read_timeout_s <= 60:
            raise ValueError("read_timeout_s outside supported finite bounds")
        self.read_timeout_s = read_timeout_s
        self.max_document_bytes = max_document_bytes
        self.poll_interval_ns, self.max_backoff_ns = poll_interval_ns, max_backoff_ns
        self._last_poll_ns = None
        self._backoff_ns = 0
        self._stopped = False
        self._sequence_generation = None
        self._sequence_last = None

    def _check_scope(self):
        if self.adapter.scope.key != self.scope_key:
            raise IncompleteHistory("Native adapter scope identity changed")

    def _encoded_size(self, value):
        try:
            return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise IncompleteHistory("Native response is not serializable") from exc

    def _append_bounded(self, target, additions, other=()):
        if len(target) + len(additions) + len(other) > self.max_rows:
            raise IncompleteHistory("Aggregate native row bound exceeded")
        candidate = target + additions
        if self._encoded_size({"rows": candidate, "other": list(other)}) > self.max_document_bytes:
            raise IncompleteHistory("Aggregate native document byte bound exceeded")
        target.extend(additions)

    def _bounded(self, value):
        size = self._encoded_size(value)
        if size > self.max_document_bytes:
            raise IncompleteHistory("Native response document byte bound exceeded")
        return value

    async def _await(self, awaitable):
        self._check_scope()
        result = await asyncio.wait_for(awaitable, timeout=self.read_timeout_s)
        self._check_scope()
        return result

    def _verify_identity(self, facts):
        self._check_scope()
        verified = self.identity_verifier(copy.deepcopy(facts))
        self._check_scope()
        if inspect.isawaitable(verified):
            if inspect.iscoroutine(verified):
                verified.close()
            elif callable(getattr(verified, "cancel", None)):
                verified.cancel()
            raise TypeError("identity_verifier must be synchronous")
        if type(verified) is not bool or verified is not True:
            raise IncompleteHistory("Native account identity was not verified")
        return True

    async def authenticate(self):
        facts = await self._await(self.adapter.account_state())
        if not isinstance(facts, dict):
            raise IncompleteHistory("Native account facts are not a mapping")
        self._bounded(facts)
        detached = copy.deepcopy(facts)
        self._verify_identity(detached)
        self._account_facts = detached
        return {"scope_key": self.scope_key, "success": True, "mode": "BOUNDED_REST_POLL",
                "identity_verified": True, "limits": {"max_pages": self.max_pages,
                "max_rows": self.max_rows, "max_document_bytes": self.max_document_bytes}}

    async def subscribe(self):
        self._check_scope()
        return {"scope_key": self.scope_key, "success": True, "mode": "BOUNDED_REST_POLL",
                "proof": "local bounded polling selection; no private websocket handshake"}

    def _binding(self, symbol):
        for binding in self.bindings:
            if binding["symbol"] == symbol:
                return binding
        raise IncompleteHistory("Native response symbol has no exact instrument binding")

    def _project_rows(self, rows, kind):
        if not isinstance(rows, list) or len(rows) > self.max_rows:
            raise IncompleteHistory("Native row count bound exceeded")
        mapped = []
        for original in rows:
            if not isinstance(original, dict):
                raise IncompleteHistory("Malformed native fact row")
            row = copy.deepcopy(original)
            symbol = row.get("symbol")
            binding = self._binding(symbol)
            if row.get("scope_key") != self.scope_key:
                raise IncompleteHistory("Native fact scope mismatch")
            row["instrument_key"] = binding["instrument_key"]
            row["kind"] = kind
            if kind == "fill":
                if not row.get("event_id"):
                    raise IncompleteHistory("Native fill lacks stable execution identity")
                if row.get("fee") is None:
                    row["fee_known"] = False
                elif type(row.get("fee_known", True)) is not bool:
                    raise IncompleteHistory("Native fee-known marker must be boolean")
            mapped.append(row)
        return mapped

    async def _collect(self, method, symbol, start_ns, end_ns, kind):
        cursor, seen, rows, complete = None, set(), [], False
        total_bytes = 0
        for _ in range(self.max_pages):
            if cursor is not None:
                fingerprint = json.dumps(cursor, sort_keys=True, allow_nan=False)
                if fingerprint in seen:
                    raise IncompleteHistory("Native history cursor cycle")
                seen.add(fingerprint)
            page = await self._await(method(symbol, start_ns=start_ns, end_ns=end_ns, cursor=cursor))
            self._bounded(page)
            page_size = self._encoded_size(page)
            total_bytes += page_size
            self._history_bytes_this_call += page_size
            if total_bytes > self.max_document_bytes or self._history_bytes_this_call > self.max_document_bytes:
                raise IncompleteHistory("Native history aggregate byte bound exceeded")
            if not isinstance(page, dict) or type(page.get("complete")) is not bool:
                raise IncompleteHistory("Malformed native history page")
            cursor_fact = page.get("cursor")
            if not isinstance(cursor_fact, dict) or cursor_fact.get("scope_key") != self.scope_key or cursor_fact.get("symbol") != symbol or cursor_fact.get("start_ns") != start_ns or cursor_fact.get("end_ns") != end_ns:
                raise IncompleteHistory("Native history page lacks exact requested scope/window proof")
            current = page.get("rows")
            if not isinstance(current, list):
                raise IncompleteHistory("Native history rows are absent")
            if len(rows) + len(current) > self.max_rows:
                raise IncompleteHistory("Native history row bound exceeded")
            rows.extend(current)
            next_cursor = cursor_fact
            token = next_cursor.get("token")
            if page.get("complete") is True:
                complete = True
                break
            if not isinstance(token, str) or not token or token in seen:
                raise IncompleteHistory("Native history continuation is absent or did not advance")
            seen.add(token)
            cursor = next_cursor
        if not complete:
            raise IncompleteHistory("Native history page budget exhausted")
        selected = []
        for row in rows:
            stamp = row.get("exchange_ns")
            if type(stamp) is not int:
                raise IncompleteHistory("Native history fact has no verified nanosecond timestamp")
            if not start_ns <= stamp <= end_ns:
                raise IncompleteHistory("Native adapter returned a fact outside the requested window")
            selected.append(row)
        return self._project_rows(selected, kind)

    async def history(self, start_ns, end_ns):
        if type(start_ns) is not int or type(end_ns) is not int or start_ns < 0 or end_ns < start_ns:
            raise ValueError("Explicit ordered nanosecond recovery interval required")
        fills, funding, coverage_proofs = [], [], []
        self._history_bytes_this_call = 0
        if type(self.adapter) is KiwoomBrokerAdapter:
            unique_queries = {(b["symbol"], b["day"]): b for b in self.bindings}
        else:
            unique_queries = {b["symbol"]: b for b in self.bindings}
        for binding in unique_queries.values():
            symbol = binding["symbol"]
            if type(self.adapter) in _PERPS:
                new_fills = await self._collect(self.adapter.fills, symbol, start_ns, end_ns, "fill")
                self._append_bounded(fills, new_fills, funding)
                new_funding = await self._collect(self.adapter.funding, symbol, start_ns, end_ns, "funding")
                self._append_bounded(funding, new_funding, fills)
            elif type(self.adapter) is BinanceBrokerAdapter:
                # The existing adapter's fromId pagination is bounded at ten native
                # pages. Require a source budget at least as large, and explicitly
                # start at native ID zero to prove oldest-to-newest exhaustion.
                if self.max_pages < 10:
                    raise IncompleteHistory("Spot source page budget is below the adapter's ten-page bound")
                result = await self._await(self.adapter.fills(symbol, from_id=0, limit=min(1000, self.max_rows)))
                self._bounded(result)
                self._history_bytes_this_call += self._encoded_size(result)
                if self._history_bytes_this_call > self.max_document_bytes:
                    raise IncompleteHistory("Aggregate native history document byte bound exceeded")
                raw = result.get("rows")
                if result.get("page_complete") is not True or not isinstance(raw, list):
                    return {"scope_key": self.scope_key, "fills": [], "funding": [], "complete": False,
                            "start_ns": start_ns, "end_ns": end_ns,
                            "reason": "Spot fromId=0 query did not prove bounded exhaustion"}
                if len(raw) > self.max_rows:
                    raise IncompleteHistory("Spot history row bound exceeded")
                normalized, previous_id = [], -1
                for item in raw:
                    native_id = item.get("id")
                    if type(native_id) is not int or native_id < 0 or native_id <= previous_id:
                        raise IncompleteHistory("Spot native trade IDs are not strictly advancing from zero")
                    previous_id = native_id
                    stamp_ms = item.get("time")
                    if type(stamp_ms) is not int or stamp_ms < 0:
                        raise IncompleteHistory("Spot native trade timestamp is invalid")
                    stamp = stamp_ms * 1_000_000
                    if start_ns <= stamp <= end_ns:
                        normalized.append({"scope_key": self.scope_key, "kind": "fill",
                            "event_id": f"{symbol}:{native_id}", "symbol": symbol,
                            "order_id": str(item["orderId"]), "quantity": item["qty"],
                            "price": item["price"], "fee": item["commission"],
                            "fee_currency": item["commissionAsset"], "exchange_ns": stamp})
                self._append_bounded(fills, self._project_rows(normalized, "fill"), funding)
                coverage_proofs.append({"symbol": symbol, "query": "myTrades fromId=0",
                                        "page_complete": True, "max_native_pages": 10})
            else:
                result = await self._await(self.adapter.execution_fills(symbol, binding["day"],
                    None, max_pages=self.max_pages, max_rows=self.max_rows))
                self._bounded(result)
                self._history_bytes_this_call += self._encoded_size(result)
                if self._history_bytes_this_call > self.max_document_bytes:
                    raise IncompleteHistory("Aggregate native history document byte bound exceeded")
                if result.get("complete") is not True:
                    raise IncompleteHistory("Kiwoom execution history incomplete")
                # Date and time are only second-precision provenance; the execution ID
                # remains day/order/cntr_no. Window filtering needs an explicit date anchor.
                day = datetime.strptime(binding["day"], "%Y%m%d").date()
                epoch_days = day.toordinal() - datetime(1970, 1, 1).date().toordinal()
                day_start = epoch_days * 86_400_000_000_000 - 9 * 3_600_000_000_000
                day_end = day_start + 86_400_000_000_000
                if start_ns != day_start or end_ns != day_end - 1:
                    raise IncompleteHistory("Kiwoom day query cannot prove a different requested window")
                self._append_bounded(fills, self._project_rows(result["rows"], "fill"), funding)
        result = {"scope_key": self.scope_key, "fills": fills, "funding": funding,
                  "complete": True, "start_ns": start_ns, "end_ns": end_ns,
                  "coverage_proofs": coverage_proofs}
        self._bounded(result)
        return result

    def _row_count(self, value):
        if isinstance(value, dict):
            return sum(self._row_count(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return len(value) + sum(self._row_count(item) for item in value)
        return 0

    def _require_bound_order(self, fact):
        symbol = fact.get("symbol")
        native_id = fact.get("order_id")
        client_id = fact.get("client_order_id")
        matched = any(
            b["symbol"] == symbol
            and not (b.get("native_order_id") is not None and native_id not in (None, "")
                     and str(b["native_order_id"]) != str(native_id))
            and not (b.get("client_order_id") is not None and client_id is not None
                     and b["client_order_id"] != client_id)
            and ((b.get("native_order_id") is not None and str(b["native_order_id"]) == str(native_id))
                 or (b.get("client_order_id") is not None and b["client_order_id"] == client_id))
            for b in self.bindings)
        if not matched:
            raise IncompleteHistory("Native open order does not match an exact bound order identity")
        return fact

    async def account_snapshot(self):
        observations = []
        def record(value):
            self._bounded(value)
            observations.append(value)
            if sum(self._encoded_size(item) for item in observations) > self.max_document_bytes:
                raise IncompleteHistory("Aggregate account snapshot byte bound exceeded")
            if sum(self._row_count(item) for item in observations) > self.max_rows:
                raise IncompleteHistory("Aggregate account snapshot row bound exceeded")

        account = await self._await(self.adapter.account_state())
        record(account)
        detached = copy.deepcopy(account)
        self._verify_identity(detached)
        positions, orders, incomplete_hints = {}, [], []
        for binding in {b["symbol"]: b for b in self.bindings}.values():
            symbol, instrument = binding["symbol"], binding["instrument_key"]
            if type(self.adapter) in _PERPS:
                p = await self._await(self.adapter.positions(symbol))
                record(p)
                o = await self._await(self.adapter.working_orders(symbol))
                record(o)
                positions[instrument] = "0"
                for row in p:
                    if row.get("scope_key") != self.scope_key or row.get("symbol") != symbol:
                        raise IncompleteHistory("Foreign native position facts")
                    qty = Decimal(str(row["quantity"]))
                    if not qty.is_finite(): raise IncompleteHistory("Invalid native position quantity")
                    positions[instrument] = str(qty)
                for row in o:
                    fact = self._require_bound_order(self._native_order(row, instrument, symbol))
                    # Some open-order lists omit original/cumulative quantity.
                    # Every bound order is queried below; only that explicit
                    # query may resolve an incomplete but identified list hint.
                    if "cumulative_quantity" in fact:
                        orders.append(fact)
                    else:
                        incomplete_hints.append(fact)
            elif type(self.adapter) is BinanceBrokerAdapter:
                for row in account.get("balances", []):
                    # Spot balances are asset facts, not guessed symbol positions.
                    if row.get("asset") == binding["base_asset"]:
                        try:
                            free, locked = Decimal(str(row["free"])), Decimal(str(row["locked"]))
                        except Exception as exc:
                            raise IncompleteHistory("Malformed Spot balance facts") from exc
                        if not free.is_finite() or not locked.is_finite() or free < 0 or locked < 0:
                            raise IncompleteHistory("Invalid Spot balance facts")
                        positions[instrument] = str(free + locked)
                positions.setdefault(instrument, "0")
                result = await self._await(self.adapter.list_open_orders(symbol))
                record(result)
                if result.get("complete") is not True: raise IncompleteHistory("Spot open orders incomplete")
                for row in result["rows"]:
                    fact = self._require_bound_order(self._native_order(row, instrument, symbol))
                    # Some open-order lists omit original/cumulative quantity.
                    # Every bound order is queried below; only that explicit
                    # query may resolve an incomplete but identified list hint.
                    if "cumulative_quantity" in fact:
                        orders.append(fact)
                    else:
                        incomplete_hints.append(fact)
            else:
                if account.get("complete") is not True: raise IncompleteHistory("Kiwoom account snapshot incomplete")
                positions[instrument] = "0"
                bound_symbols = {b["symbol"] for b in self.bindings}
                for row in account.get("positions", []):
                    row_symbol = self.adapter._row_symbol(row)
                    if row_symbol not in bound_symbols:
                        raise IncompleteHistory("Kiwoom account contains an unbound native position")
                    if row_symbol != symbol:
                        continue
                    try:
                        quantity = Decimal(str(row["rmnd_qty"]))
                    except Exception as exc:
                        raise IncompleteHistory("Malformed Kiwoom position quantity") from exc
                    if not quantity.is_finite():
                        raise IncompleteHistory("Invalid Kiwoom position quantity")
                    positions[instrument] = str(quantity)
                result = await self._await(self.adapter.list_open_orders(symbol))
                record(result)
                if result.get("complete") is not True: raise IncompleteHistory("Kiwoom open orders incomplete")
                for row in result["rows"]:
                    if self.adapter._row_symbol(row) == symbol:
                        fact = {"kind": "order", "scope_key": self.scope_key,
                            "order_id": str(row.get("ord_no", "")), "symbol": symbol,
                            "instrument_key": instrument, "status": "OPEN",
                            "terminal_proven": False, "raw": row}
                        incomplete_hints.append(self._require_bound_order(fact))
        bound = [b for b in self.bindings if b.get("native_order_id") or b.get("client_order_id")]
        for binding in bound:
            order_id = binding.get("native_order_id") or binding.get("client_order_id")
            order = await self.query_order(order_id, symbol=binding["symbol"])
            record(order)
            for hint in incomplete_hints:
                if (hint["symbol"] == binding["symbol"]
                        and (str(hint.get("order_id")) == str(binding.get("native_order_id"))
                             or (binding.get("client_order_id") is not None
                                 and hint.get("client_order_id") == binding["client_order_id"]))
                        and str(hint.get("order_id")) != str(order.get("order_id"))):
                    raise IncompleteHistory("Native query conflicts with identified open-order hint")
            orders.append(order)
            if len(positions) + len(orders) > self.max_rows:
                raise IncompleteHistory("Aggregate snapshot projection row bound exceeded")
        self._check_scope()
        snapshot = {"scope_key": self.scope_key, "account": detached, "positions": positions,
                    "orders": orders, "complete": True, "observed_ns": self.clock_ns(),
                    "identity_verified": True}
        self._bounded(snapshot)
        if self._row_count(snapshot) > self.max_rows:
            raise IncompleteHistory("Aggregate account snapshot row bound exceeded")
        return snapshot

    def _native_order(self, raw, instrument, symbol):
        if type(self.adapter) is BinanceBrokerAdapter:
            if not isinstance(raw, dict) or raw.get("symbol") != symbol:
                raise IncompleteHistory("Spot order symbol differs from its explicit binding")
            native_id = raw.get("orderId")
            client_id = raw.get("clientOrderId")
            if type(native_id) is not int or native_id < 0:
                raise IncompleteHistory("Spot order lacks a valid native order ID")
            if client_id is not None and (not isinstance(client_id, str) or not client_id):
                raise IncompleteHistory("Spot order has a malformed native client ID")
            status = str(raw.get("status", "UNKNOWN")).upper()
            aliases = {"NEW": "OPEN", "PARTIALLY_FILLED": "PARTIAL", "CANCELED": "CANCELED",
                       "CANCELLED": "CANCELED", "PENDING_CANCEL": "CANCEL_PENDING"}
            status = aliases.get(status, status)
            if status not in {"OPEN", "PARTIAL", "CANCEL_PENDING", "CANCELED", "FILLED", "REJECTED", "EXPIRED", "UNKNOWN"}:
                status = "UNKNOWN"
            cumulative = raw.get("executedQty")
            result = {"kind":"order", "scope_key":self.scope_key, "order_id":str(native_id),
                      "client_order_id":client_id, "symbol":symbol, "instrument_key":instrument,
                      "status":status, "terminal_proven":status in {"CANCELED", "FILLED", "REJECTED", "EXPIRED"}
                      and cumulative is not None, "raw":copy.deepcopy(raw)}
            if cumulative is not None:
                try:
                    quantity = Decimal(str(cumulative))
                except Exception as exc:
                    raise IncompleteHistory("Malformed Spot cumulative quantity") from exc
                if not quantity.is_finite() or quantity < 0:
                    raise IncompleteHistory("Invalid Spot cumulative quantity")
                result["cumulative_quantity"] = str(quantity)
            if status in {"PARTIAL", "CANCELED", "FILLED", "REJECTED", "EXPIRED"} and cumulative is None:
                result["status"] = "UNKNOWN"
                result["terminal_proven"] = False
            return result
        if not isinstance(raw, dict) or raw.get("scope_key") != self.scope_key:
            raise IncompleteHistory("Native order lacks exact scope proof")
        if raw.get("symbol") != symbol:
            raise IncompleteHistory("Native order symbol differs from its explicit binding")
        status = str(raw.get("status", "UNKNOWN")).upper()
        aliases = {"NEW": "OPEN", "PARTIALLY_FILLED": "PARTIAL", "PARTIAL_FILL": "PARTIAL",
                   "PARTIALLYFILLED": "PARTIAL", "PARTIALLYFILLEDCANCELED": "CANCELED",
                   "PARTIALLY_FILLED_CANCELED": "CANCELED", "PARTIALLYFILLED_CANCELED": "CANCELED",
                   "DEACTIVATED": "CANCELED", "MARGINCANCELED": "CANCELED",
                   "TRIGGERED": "OPEN", "PENDINGCANCEL": "CANCEL_PENDING",
                   "CANCELED": "CANCELED", "CANCELLED": "CANCELED", "PENDING_CANCEL": "CANCEL_PENDING"}
        status = aliases.get(status, status)
        if status not in {"OPEN", "PARTIAL", "CANCEL_PENDING", "CANCELED", "FILLED", "REJECTED", "EXPIRED", "UNKNOWN"}:
            status = "UNKNOWN"
        native_id = raw.get("order_id", raw.get("orderId"))
        client_id = raw.get("client_order_id", raw.get("clientOrderId"))
        if native_id is not None and (not isinstance(native_id, (str, int)) or str(native_id) == ""):
            raise IncompleteHistory("Malformed native order identity")
        if client_id is not None and (not isinstance(client_id, str) or not client_id):
            raise IncompleteHistory("Malformed native client order identity")
        native = raw.get("raw")
        if isinstance(native, dict):
            venue = getattr(self.adapter.scope, "venue", None)
            if venue == "binance":
                cumulative = native.get("executedQty")
            elif venue == "bybit":
                cumulative = native.get("cumExecQty")
            elif venue == "grvt":
                traded = native.get("state", {}).get("traded_size")
                cumulative = traded[0] if isinstance(traded, list) and traded else None
            elif venue == "arcus":
                try:
                    cumulative = str(Decimal(str(native["originalSize"])) - Decimal(str(native["remainingSize"])))
                except (KeyError, ValueError, ArithmeticError):
                    cumulative = None
            elif venue == "hyperliquid":
                try:
                    original = Decimal(str(native["origSz"]))
                    leaves = Decimal(str(native["sz"]))
                    if (not original.is_finite() or not leaves.is_finite() or original <= 0
                            or leaves < 0 or leaves > original):
                        raise ValueError("invalid Hyperliquid order size evidence")
                    cumulative = str(original - leaves)
                except (KeyError, ValueError, ArithmeticError):
                    cumulative = None
            else:
                cumulative = None
        else:
            cumulative = raw.get("cumulative_quantity")
        result = {"kind": "order", "scope_key": self.scope_key,
                  "order_id": str(native_id) if native_id is not None else "",
                  "client_order_id": client_id, "symbol": symbol, "instrument_key": instrument,
                  "status": status, "terminal_proven": raw.get("terminal") is True and cumulative is not None,
                  "raw": copy.deepcopy(raw)}
        if cumulative is None and status in {"PARTIAL", "CANCELED", "FILLED", "REJECTED", "EXPIRED"}:
            result["status"] = "UNKNOWN"
        if cumulative is not None:
            try:
                quantity = Decimal(str(cumulative))
            except Exception as exc:
                raise IncompleteHistory("Malformed native cumulative quantity") from exc
            if not quantity.is_finite() or quantity < 0:
                raise IncompleteHistory("Invalid native cumulative quantity")
            result["cumulative_quantity"] = str(quantity)
        if not result["order_id"] and not client_id:
            result["status"] = "UNKNOWN"
            result["terminal_proven"] = False
        return result

    async def _kiwoom_order_fact(self, binding):
        symbol, day, target = binding["symbol"], binding["day"], binding["native_order_id"]
        raw_result = await self._await(self.adapter.query_order(symbol, day, target))
        self._bounded(raw_result)
        execution_result = await self._await(self.adapter.execution_fills(
            symbol, day, None, max_pages=self.max_pages, max_rows=self.max_rows))
        self._bounded(execution_result)
        if raw_result.get("complete") is not True or execution_result.get("complete") is not True:
            return {"scope_key": self.scope_key, "kind": "order", "order_id": target,
                    "symbol": symbol, "status": "UNKNOWN", "terminal_proven": False,
                    "instrument_key": binding["instrument_key"], "raw": {"order_facts": raw_result}}
        rows = raw_result.get("rows")
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise IncompleteHistory("Malformed Kiwoom order facts")
        exact = [r for r in rows if str(r.get("stk_cd", "")).removeprefix("A") == symbol
                 and ("ord_dt" not in r or str(r.get("ord_dt")) == day)
                 and ("dmst_stex_tp" not in r or r.get("dmst_stex_tp") == "KRX")
                 and (str(r.get("ord_no", "")) == target or str(r.get("ori_ord", "")) == target)]
        originals = [r for r in exact if str(r.get("ord_no", "")) == target
                     and str(r.get("ori_ord", "0000000")) in {"", "0000000"}]
        cancels = [r for r in exact if str(r.get("ori_ord", "")) == target
                   and r.get("mdfy_cncl") == "취소확인"]
        if len(originals) == 1 and originals[0].get("io_tp_nm") not in {"현금매수", "현금매도"}:
            originals = []
        if len(cancels) == 1 and (cancels[0].get("io_tp_nm") not in {"현금매수", "현금매도"}
                                  or len(originals) != 1
                                  or cancels[0].get("io_tp_nm") != originals[0].get("io_tp_nm")):
            cancels = []
        try:
            execution_rows = execution_result["rows"]
            filled = sum((Decimal(str(r["quantity"])) for r in execution_rows
                          if str(r.get("order_id")) == target and r.get("symbol") == symbol), Decimal(0))
            original_qty = Decimal(str(originals[0]["ord_qty"])) if len(originals) == 1 else None
            confirmed = Decimal(str(cancels[0]["cnfm_qty"])) if len(cancels) == 1 else None
            remainder = Decimal(str(cancels[0]["ord_remnq"])) if len(cancels) == 1 else None
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise IncompleteHistory("Malformed Kiwoom native order quantities") from exc
        if original_qty is None or confirmed is None or remainder is None:
            return {"scope_key": self.scope_key, "kind": "order", "order_id": target,
                    "symbol": symbol, "instrument_key": binding["instrument_key"], "status": "UNKNOWN",
                    "terminal_proven": False, "cumulative_quantity": str(filled),
                    "raw": {"order_facts": raw_result, "execution_facts": execution_result}}
        valid = (original_qty.is_finite() and confirmed.is_finite() and remainder.is_finite()
                 and filled.is_finite() and original_qty > 0 and confirmed > 0 and remainder == 0
                 and all(value == value.to_integral_value() for value in (original_qty, confirmed, remainder, filled))
                 and filled >= 0 and filled + confirmed == original_qty)
        return {"scope_key": self.scope_key, "kind": "order", "order_id": target,
                "symbol": symbol, "instrument_key": binding["instrument_key"],
                "status": "CANCELED" if valid else "UNKNOWN", "terminal_proven": valid,
                "cumulative_quantity": str(filled),
                "raw": {"order_facts": raw_result, "execution_facts": execution_result,
                        "proof": "kt00007 linked 취소확인 + complete kt00009 native executions" if valid else None}}

    async def query_order(self, native_order_id, symbol=None):
        binding = next((b for b in self.bindings if (symbol is None or b["symbol"] == symbol)
                        and (b.get("native_order_id") == native_order_id or b.get("client_order_id") == native_order_id)), None)
        if binding is None:
            return {"scope_key": self.scope_key, "status": "UNKNOWN", "terminal_proven": False}
        if type(self.adapter) in _PERPS:
            if not binding.get("client_order_id"):
                return {"scope_key": self.scope_key, "kind": "order", "order_id": str(native_order_id),
                        "symbol": binding["symbol"], "instrument_key": binding["instrument_key"],
                        "status": "UNKNOWN", "terminal_proven": False}
            raw = await self._await(self.adapter.order_status(binding["symbol"], binding["client_order_id"]))
        elif type(self.adapter) is BinanceBrokerAdapter:
            raw = await self._await(self.adapter.query_order(binding["symbol"], order_id=binding.get("native_order_id"),
                                                            client_order_id=binding.get("client_order_id")))
            if isinstance(raw, dict) and raw.get("status") == "UNKNOWN":
                if raw.get("symbol") != binding["symbol"]:
                    raise IncompleteHistory("Spot unknown-order response differs from exact symbol query")
                return {"scope_key":self.scope_key, "kind":"order",
                        "order_id":str(binding["native_order_id"]) if binding.get("native_order_id") is not None else "",
                        "client_order_id":binding.get("client_order_id"), "symbol":binding["symbol"],
                        "instrument_key":binding["instrument_key"], "status":"UNKNOWN",
                        "terminal_proven":False, "raw":copy.deepcopy(raw)}
        else:
            if not binding.get("native_order_id"):
                return {"scope_key": self.scope_key, "kind": "order", "client_order_id": binding.get("client_order_id"),
                        "symbol": binding["symbol"], "instrument_key": binding["instrument_key"],
                        "status": "UNKNOWN", "terminal_proven": False}
            return await self._kiwoom_order_fact(binding)
        fact = self._native_order(raw, binding["instrument_key"], binding["symbol"])
        if type(self.adapter) is BinanceBrokerAdapter:
            if binding.get("native_order_id") is not None and fact["order_id"] != str(binding["native_order_id"]):
                raise IncompleteHistory("Spot query returned a different native order ID")
            if binding.get("client_order_id") is not None and fact.get("client_order_id") != binding["client_order_id"]:
                raise IncompleteHistory("Spot query returned a different native client order ID")
        return fact

    async def poll_once(self, session, start_ns, end_ns):
        now = self.clock_ns()
        if self._stopped:
            raise RuntimeError("Polling source has been stopped")
        if self._last_poll_ns is not None and now - self._last_poll_ns < max(self.poll_interval_ns, self._backoff_ns):
            return {"scope_key": self.scope_key, "polled": False, "reason": "RATE_LIMITED"}
        self._last_poll_ns = now
        try:
            result = await session.reconcile(self, start_ns=start_ns, end_ns=end_ns)
            self._check_scope()
            self._backoff_ns = 0
            return result
        except Exception:
            self._backoff_ns = min(self.max_backoff_ns, max(self.poll_interval_ns, self._backoff_ns * 2))
            raise

    def _private_frame_gap(self, session):
        try:
            marker = getattr(session, "mark_gap", None)
            if callable(marker):
                result = marker("PRIVATE_FRAME_REJECTED")
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    elif callable(getattr(result, "cancel", None)):
                        result.cancel()
        except BaseException:
            # Preserve the original frame failure; the core's own gap path remains
            # the authority when its marker cannot be committed.
            pass

    def ingest_frame(self, session, frame, generation):
        is_grvt = type(self.adapter) is GrvtPerpAdapter
        try:
            self._check_scope()
            self._bounded(frame)
            normalizer = getattr(self.adapter, "normalize_private_event", None)
            if not callable(normalizer):
                raise IncompleteHistory("Adapter has no private event normalizer")
            rows = normalizer(frame)
            self._check_scope()
            if not isinstance(rows, list) or len(rows) > self.max_rows:
                raise IncompleteHistory("Malformed private event normalization")
            next_sequence = None
            if is_grvt and rows:
                if generation is None or type(generation) is bool or not isinstance(generation, (str, int)):
                    raise IncompleteHistory("GRVT private sequence requires an explicit connection generation")
                sequence = rows[0].get("connection_sequence")
                if type(sequence) is int:
                    next_sequence = sequence
                elif isinstance(sequence, str) and sequence.isdigit() and (sequence == "0" or not sequence.startswith("0")):
                    next_sequence = int(sequence)
                if next_sequence is None or next_sequence <= 0:
                    raise IncompleteHistory("GRVT private event lacks a canonical positive connection sequence")
                if rows[0].get("sequence_requires_connection_scope") is not True:
                    raise IncompleteHistory("GRVT sequence is not bound to its authenticated connection generation")
                if generation == self._sequence_generation and self._sequence_last is not None and next_sequence != self._sequence_last + 1:
                    raise IncompleteHistory("GRVT private connection sequence gap, duplicate, or reordering")
            accepted = 0
            for row in rows:
                kind = row.get("kind", "order")
                mapped = self._project_rows([row], kind)[0]
                self._check_scope()
                if kind == "order":
                    mapped = self._native_order(mapped, self._binding(mapped.get("symbol"))["instrument_key"], mapped.get("symbol"))
                self._check_scope()
                session.ingest(mapped, generation=generation)
                accepted += 1
            self._check_scope()
            if is_grvt and rows:
                self._sequence_generation, self._sequence_last = generation, next_sequence
            return accepted
        except BaseException:
            if is_grvt:
                self._sequence_generation, self._sequence_last = generation, None
            self._private_frame_gap(session)
            raise

    async def stop(self):
        self._stopped = True
        return None
