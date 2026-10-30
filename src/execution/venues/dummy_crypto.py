"""Offline-only synthetic USDT linear perpetual fixture.

This module owns an explicit example adapter but never registers itself and
never constructs a transport, signer, feed, account session, or SDK client.
Its state is synthetic fixture data, not a production account store.
"""
from __future__ import annotations

import inspect
from decimal import Decimal, InvalidOperation
from typing import Mapping

from execution.execution_ledger import AccountScope
from .registry import Capabilities


OPERATIONS = {
    "submit": "submit_with_permit",
    "cancel": "cancel_with_permit",
    "query_order": "order_status",
    "list_open_orders": "working_orders",
    "get_fills": "fills",
    "account_state": "account_state",
    "positions": "positions",
    "reconcile": "reconcile",
    "instrument_spec": "instrument_metadata",
    "private_or_poll": "normalize_private_event",
}

CRYPTO_CAPABILITIES = Capabilities(
    products=frozenset({"linear_perpetual"}),
    environments=frozenset({"sandbox", "offline"}),
    time_in_force=frozenset({"GTC", "IOC", "POST_ONLY"}),
    post_only=True,
    reduce_only=True,
    funding=False,
    private_or_poll=True,
)

DEFAULT_CRYPTO_SCOPE = AccountScope(
    "dummy-crypto", "dummy-crypto-account", "linear_perpetual", "sandbox")


def _permit(permit):
    if not callable(permit) or inspect.iscoroutinefunction(permit) or inspect.iscoroutinefunction(
            getattr(permit, "__call__", None)):
        raise TypeError("Synthetic mutation requires a synchronous one-use permit")


def _decimal(value, name, *, positive=False):
    if isinstance(value, bool) or value is None:
        raise ValueError(name + " requires an exact decimal")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError(name + " is not decimal") from error
    if not number.is_finite() or (positive and number <= 0):
        raise ValueError(name + " must be finite" + (" and positive" if positive else ""))
    return number


def _view(order):
    if isinstance(order, Mapping):
        client_id = order.get("client_order_id")
        symbol = order.get("symbol")
        side = order.get("side")
        quantity = order.get("quantity")
        price = order.get("limit_price")
        tif = str(order.get("time_in_force", "GTC")).upper()
        post_only = order.get("post_only", False)
        reduce_only = order.get("reduce_only", False)
        action = str(order.get("action", "NEW")).upper()
        target = order.get("target_order_id")
        strategy_id = order.get("strategy_id")
    else:
        intent = getattr(order, "intent", None)
        if intent is None:
            raise ValueError("Synthetic order requires an intent/order payload")
        client_id = getattr(intent, "client_order_id", None)
        instrument = getattr(intent, "instrument", None)
        symbol = getattr(instrument, "raw_symbol", None) or getattr(instrument, "std_symbol", None)
        side_value = getattr(intent, "side", None)
        side = getattr(side_value, "value", side_value)
        quantity = getattr(intent, "quantity", None)
        price = getattr(intent, "limit_price", None)
        tif = str(getattr(order, "time_in_force", "GTC")).upper()
        post_only = getattr(order, "post_only", False)
        reduce_only = getattr(order, "reduce_only", False)
        action = str(getattr(getattr(order, "action", "NEW"), "value", getattr(order, "action", "NEW"))).upper()
        target = getattr(order, "target_order_id", None)
        strategy_id = getattr(order, "strategy_id", None)
    if not isinstance(client_id, str) or not client_id or len(client_id) > 128:
        raise ValueError("Synthetic order requires a bounded client_order_id")
    if not isinstance(symbol, str) or not symbol or len(symbol) > 64:
        raise ValueError("Synthetic order requires a bounded symbol")
    if str(side).lower() not in {"buy", "sell"}:
        raise ValueError("Synthetic order requires buy/sell")
    if type(post_only) is not bool or type(reduce_only) is not bool:
        raise ValueError("post_only/reduce_only must be explicit booleans")
    if action not in {"NEW", "REPLACE", "CANCEL"}:
        raise ValueError("Unsupported synthetic action")
    return {
        "client_order_id": client_id,
        "symbol": symbol,
        "side": str(side).lower(),
        "quantity": _decimal(quantity, "quantity", positive=True),
        "limit_price": _decimal(price, "limit_price", positive=True) if price is not None else None,
        "time_in_force": tif,
        "post_only": post_only or tif == "POST_ONLY",
        "reduce_only": reduce_only,
        "action": action,
        "target_order_id": target,
        "strategy_id": strategy_id,
    }


class CryptoDummyAdapter:
    """Synthetic USDT linear perpetual adapter with a real permit seam."""

    venue = "dummy-crypto"
    product = "linear_perpetual"
    environment = "sandbox"

    def __init__(self, scope: AccountScope | None = None, *, before_permit=None,
                 max_orders=256, max_fills=1024, max_events=2048):
        for name, value in (("max_orders", max_orders), ("max_fills", max_fills), ("max_events", max_events)):
            if type(value) is not int or value <= 0:
                raise ValueError(name + " requires an explicit positive integer")
        self.max_orders, self.max_fills, self.max_events = max_orders, max_fills, max_events
        self.scope = scope or DEFAULT_CRYPTO_SCOPE
        self.before_permit = before_permit
        self.orders: dict[tuple[str, str], dict] = {}
        self._fills: list[dict] = []
        self._fill_ids: set[str] = set()
        self._positions: dict[str, Decimal] = {}
        self.mutations: list[str] = []

    @property
    def mutation_count(self) -> int:
        return len(self.mutations)

    @property
    def event_count(self) -> int:
        return len(self.mutations) + len(self._fills)

    @staticmethod
    def _require_permit(permit):
        _permit(permit)

    async def submit_with_permit(self, order, permit):
        self._require_permit(permit)
        fields = _view(order)
        if self.scope.environment.casefold() == "live":
            raise ValueError("Synthetic fixture never permits LIVE mutations")
        if fields["action"] != "NEW":
            raise ValueError("Synthetic fixture supports NEW only; replacement economics are unsupported")
        if fields["time_in_force"] not in {"GTC", "IOC", "POST_ONLY"}:
            raise ValueError("Synthetic USDT linear route rejects this time_in_force")
        if fields["limit_price"] is None:
            raise ValueError("Synthetic fixture requires an explicit limit price")
        if len(self.orders) >= self.max_orders:
            raise ValueError("Synthetic order capacity exhausted before effects")
        if self.event_count >= self.max_events:
            raise ValueError("Synthetic event capacity exhausted before effects")
        if self.before_permit is not None:
            await self.before_permit()
        result = permit()
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("Synthetic permit must be synchronous")
        key = (fields["symbol"], fields["client_order_id"])
        order_id = "crypto-" + fields["client_order_id"]
        record = {
            "symbol": fields["symbol"],
            "client_order_id": fields["client_order_id"],
            "order_id": order_id,
            "side": fields["side"],
            "quantity": str(fields["quantity"]),
            "limit_price": str(fields["limit_price"]),
            "time_in_force": fields["time_in_force"],
            "post_only": fields["post_only"],
            "reduce_only": fields["reduce_only"],
            "status": "OPEN",
            "terminal": False,
            "synthetic": True,
            "strategy_id": fields["strategy_id"],
        }
        self.orders[key] = record
        self.mutations.append(fields["client_order_id"])
        return dict(record, status="ACKNOWLEDGED")

    async def cancel_with_permit(self, symbol, client_order_id, permit, *, strategy_id=None,
                                 account_scope=None, environment=None, product=None):
        self._require_permit(permit)
        key = (symbol, client_order_id)
        record = self.orders.get(key)
        if record is None:
            raise ValueError("Synthetic order is not owned by this fixture")
        if strategy_id is not None and record.get("strategy_id") != strategy_id:
            raise ValueError("Synthetic order belongs to another strategy")
        if account_scope is not None and getattr(account_scope, "key", None) != self.scope.key:
            raise ValueError("Synthetic cancel scope differs from fixture account")
        if environment is not None and environment != self.scope.environment:
            raise ValueError("Synthetic cancel environment differs from fixture account")
        if product is not None and product != self.product:
            raise ValueError("Synthetic cancel product differs from fixture account")
        if self.event_count >= self.max_events:
            raise ValueError("Synthetic event capacity exhausted before effects")
        if self.before_permit is not None:
            await self.before_permit()
        result = permit()
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("Synthetic permit must be synchronous")
        record["status"] = "CANCELED"
        record["terminal"] = True
        self.mutations.append(client_order_id)
        return {"symbol": symbol, "client_order_id": client_order_id,
                "acknowledged": True, "terminal_proven": False, "synthetic": True}

    async def order_status(self, symbol, client_order_id):
        record = self.orders.get((symbol, client_order_id))
        if record is None:
            return {"symbol": symbol, "client_order_id": client_order_id,
                    "status": "UNKNOWN", "terminal": False, "history_complete": False,
                    "synthetic": True}
        return dict(record)

    async def working_orders(self, symbol):
        return [dict(record) for (item_symbol, _), record in self.orders.items()
                if item_symbol == symbol and not record["terminal"]]

    async def account_state(self):
        return {"scope_key": self.scope.key, "equity": "1000", "available": "1000",
                "currency": "USDT", "synthetic": True}

    async def positions(self, symbol):
        return [{"symbol": symbol, "quantity": str(self._positions.get(symbol, Decimal(0))),
                 "synthetic": True}]

    async def instrument_metadata(self, symbol):
        if not isinstance(symbol, str) or not symbol or len(symbol) > 64:
            raise ValueError("Bounded synthetic symbol required")
        return {"symbol": symbol, "kind": "linear_perpetual", "tick_size": "0.1",
                "lot_size": "0.001", "min_notional": "5", "settlement_currency": "USDT",
                "synthetic": True}

    def fill(self, symbol, client_order_id, *, quantity, price, fee="0", fill_id=None):
        key = (symbol, client_order_id)
        record = self.orders[key]
        qty = _decimal(quantity, "fill quantity", positive=True)
        unit = _decimal(price, "fill price", positive=True)
        charge = _decimal(fee, "fee")
        identity = fill_id or client_order_id + ":" + str(len(self._fills) + 1)
        if not isinstance(identity, str) or not identity or len(identity.encode("utf-8")) > 200:
            raise ValueError("Synthetic fill identity must be bounded to 200 UTF-8 bytes")
        if identity in self._fill_ids:
            return False
        if len(self._fills) >= self.max_fills or self.event_count >= self.max_events:
            raise ValueError("Synthetic fill/event capacity exhausted before effects")
        position = self._positions.get(symbol, Decimal(0))
        self._positions[symbol] = position + (qty if record["side"] == "buy" else -qty)
        self._fills.append({
            "kind": "fill",
            "event_id": identity,
            "symbol": symbol,
            "client_order_id": client_order_id,
            "order_id": record["order_id"],
            "side": record["side"],
            "quantity": str(qty),
            "price": str(unit),
            "fee": str(charge),
            "synthetic": True,
        })
        self._fill_ids.add(identity)
        return True

    async def fills(self, symbol, *, start_ns=0, end_ns=2**63 - 1, cursor=None):
        rows = [dict(row) for row in self._fills if row["symbol"] == symbol]
        return {"rows": rows, "complete": True,
                "cursor": {"symbol": symbol, "start_ns": start_ns, "end_ns": end_ns,
                           "complete": True}}

    async def reconcile(self, symbol, **kwargs):
        return {
            "scope_key": self.scope.key,
            "account": await self.account_state(),
            "positions": await self.positions(symbol),
            "working_orders": await self.working_orders(symbol),
            "fills": await self.fills(symbol),
            "complete": True,
            "absence_resolves_unknown": False,
            "synthetic": True,
        }

    def normalize_private_event(self, frame):
        if not isinstance(frame, Mapping) or frame.get("type") != "fill":
            return []
        return [dict(frame, synthetic=True)]
