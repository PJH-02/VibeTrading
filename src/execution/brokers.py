from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import inspect
import re
import time
import weakref
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiohttp

from execution.contracts import BrokerAdapter
from execution.perp_execution import HttpResponse, IncompleteHistory, OutcomeUnknown, VenueRejected
from execution.execution_ledger import AccountScope
from core.models import AssetClass, OrderIntent, Side, Venue
from feeds.providers.kis_client import KisAuthClient
from feeds.providers.toss import TossAuthClient
from runtime.settings import RuntimeSettings, get_settings


def _bool(value: str | None, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _exact_decimal(value: Any, label: str, *, positive: bool = False) -> Decimal:
    if value is None or isinstance(value, (bool, float)) or (isinstance(value, str) and len(value) > 64):
        raise ValueError(f"{label} requires an exact decimal string/integer")
    try:
        result = Decimal(value)
    except (ArithmeticError, TypeError, ValueError) as error:
        raise ValueError(f"{label} is not an exact decimal") from error
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"{label} must be finite" + (" and positive" if positive else ""))
    return result


def _header(headers: dict[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    lowered = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered:
            return None if value is None else str(value)
    return None


def _response_body(response: HttpResponse) -> Any:
    return response.body


class _OwnerDispatchGuard:
    """One-use synchronous owner gate with observable dispatch state."""
    def __init__(self, adapter, source, validate_scope=None):
        if (not callable(source) or inspect.iscoroutinefunction(source)
                or inspect.iscoroutinefunction(getattr(source, "__call__", None))):
            raise TypeError("Mutation requires a synchronous one-use owner permit")
        try:
            weakref.ref(source)
            hash(source)
        except (TypeError, ValueError) as exc:
            raise TypeError("Owner permit must support weak identity tracking") from exc
        used = adapter._used_permits
        if source in used: raise RuntimeError("Permit already consumed")
        self._used = used
        self._adapter = adapter
        self._source = source
        self._validate_scope = validate_scope
        self._context = getattr(adapter, "account_context", None)
        self._context_snapshot = dict(self._context) if isinstance(self._context, dict) else self._context
        self._route_snapshot = (getattr(adapter, "use_mock", None), getattr(adapter, "use_testnet", None), getattr(adapter, "base_url", None))
        self.attempted = False
        self.authorized = False

    def _check_scope(self):
        current = getattr(self._adapter, "account_context", None)
        if current is not self._context or (isinstance(current, dict) and current != self._context_snapshot):
            raise RuntimeError("Account context changed before native dispatch")
        route = (getattr(self._adapter, "use_mock", None), getattr(self._adapter, "use_testnet", None), getattr(self._adapter, "base_url", None))
        if route != self._route_snapshot: raise RuntimeError("Venue environment or host changed before native dispatch")
        if self._validate_scope: self._validate_scope()


    def __call__(self):
        if self.attempted or self._source in self._used:
            raise RuntimeError("Permit already consumed")
        self._check_scope()
        self.attempted = True
        self._used.add(self._source)
        result = self._source()
        if inspect.isawaitable(result):
            close = getattr(result, "close", None)
            if close: close()
            raise TypeError("Owner permit callback must be synchronous")
        self._check_scope()
        self.authorized = True
        return result


class BinanceBrokerAdapter:
    """Binance Spot adapter; scoped routes require a caller supplied context.

    Context keys: venue, product, environment, account_alias,
    account_identity_verified (caller attestation only), account_id, optional
    strategy_id/native_account_id. Environment is sandbox for testnet and live
    for the official production host.
    """
    name = "binance"

    def __init__(self, settings: RuntimeSettings | None = None, *, transport=None, account_context=None) -> None:
        self.settings = settings or get_settings()
        self.transport = transport
        self.account_context = account_context
        self._used_permits = weakref.WeakSet()
        env = self.settings.env
        self.use_testnet = _bool(env.get("BINANCE_USE_TESTNET"), default=False)
        if self.use_testnet:
            self.api_key = env.get("BINANCE_TESTNET_API_KEY") or env.get("BINANCE_API_KEY")
            self.api_secret = env.get("BINANCE_TESTNET_API_SECRET") or env.get("BINANCE_API_SECRET")
            self.base_url = "https://testnet.binance.vision"
        else:
            self.api_key = env.get("BINANCE_API_KEY")
            self.api_secret = env.get("BINANCE_API_SECRET")
            self.base_url = env.get("BINANCE_BASE_URL", "https://api.binance.com").rstrip("/")

    @property
    def scope(self):
        """Canonical routing identity; alias is a label, not a native account proof."""
        self._validate_context()
        return AccountScope(self.name, self.account_context["account_id"], "spot",
                            self.account_context["environment"])

    async def connect(self) -> None:
        self._validate_context()
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Binance API key/secret are not configured")
        await self._signed_request("GET", "/api/v3/account", {})

    async def account_state(self) -> dict:
        self._validate_context()
        payload = await self._signed_request("GET", "/api/v3/account", {})
        if not isinstance(payload, dict) or type(payload.get("canTrade")) is not bool: raise IncompleteHistory("Malformed Binance account facts")
        balances = payload.get("balances")
        if not isinstance(balances, list) or any(not isinstance(item, dict) or not {"asset", "free", "locked"} <= item.keys() for item in balances):
            raise IncompleteHistory("Malformed Binance account balances")
        return {"provider": self.name, "testnet": self.use_testnet, "balances": balances, "can_trade": payload.get("canTrade"), "raw": payload}

    async def submit_order(self, intent: OrderIntent) -> dict:
        raise RuntimeError("Binance Spot submit requires submit_order_with_permit and an explicit verified scope")

    async def submit_order_with_permit(self, intent: OrderIntent, *, permit) -> dict:
        self._validate_spot_scope(intent)
        if intent.side not in {Side.BUY, Side.SELL}: raise ValueError("Binance Spot side must be exact BUY or SELL")
        if intent.instrument.asset_class not in {AssetClass.CRYPTO_SPOT}:
            raise RuntimeError(f"Binance adapter only supports crypto spot for now: {intent.instrument.asset_class}")
        if intent.quantity is None:
            raise RuntimeError("Binance spot order requires quantity")
        symbol = self._spot_symbol(intent.instrument.raw_symbol)
        quantity = _exact_decimal(intent.quantity, "quantity", positive=True)
        tif = str(intent.metadata.get("time_in_force", "GTC")).upper()
        if tif not in {"GTC", "IOC", "FOK", "POST_ONLY"}: raise ValueError("Unsupported Binance Spot time in force")
        if len(intent.client_order_id) > 36 or not re.fullmatch(r"[A-Za-z0-9_-]+", intent.client_order_id):
            raise ValueError("Binance client order ID must be valid and at most 36 characters")
        guard = self._permit_once(permit, lambda: self._validate_spot_scope(intent))
        spec = await self.instrument_metadata(symbol)
        filters = {row.get("filterType"): row for row in spec.get("filters", []) if isinstance(row, dict)}
        lot = filters.get("LOT_SIZE") if intent.limit_price is not None else filters.get("MARKET_LOT_SIZE")
        price_filter = filters.get("PRICE_FILTER")
        if (spec.get("symbol") != symbol or spec.get("status") != "TRADING" or spec.get("isSpotTradingAllowed") is not True
                or lot is None or "PRICE_FILTER" not in filters
                or not {"LOT_SIZE", "PRICE_FILTER"} <= filters.keys()
                or "orderTypes" in spec and (not isinstance(spec["orderTypes"], list) or not spec["orderTypes"])
                or "permissions" in spec and (not isinstance(spec["permissions"], list) or "SPOT" not in spec["permissions"])):
            raise RuntimeError("Binance Spot symbol is not verified tradable from exchangeInfo")
        step = _exact_decimal(lot.get("stepSize"), "lot step", positive=True)
        minimum = _exact_decimal(lot.get("minQty"), "minimum quantity", positive=True)
        maximum = _exact_decimal(lot.get("maxQty"), "maximum quantity", positive=True)
        if quantity < minimum or quantity > maximum or quantity % step != 0: raise ValueError("Quantity violates fetched Binance Spot lot filters")
        if intent.limit_price is not None:
            if price_filter is None: raise RuntimeError("Binance Spot price filter missing")
            tick = _exact_decimal(price_filter.get("tickSize"), "price tick", positive=True)
            price = _exact_decimal(intent.limit_price, "limit_price", positive=True)
            minimum_price = _exact_decimal(price_filter.get("minPrice"), "minimum price")
            maximum_price = _exact_decimal(price_filter.get("maxPrice"), "maximum price")
            if price % tick != 0 or (minimum_price and price < minimum_price) or (maximum_price and maximum_price > 0 and price > maximum_price): raise ValueError("Price violates fetched Binance Spot tick filters")
            self._validate_notional(filters, quantity, price)
        elif any(k in filters for k in ("MIN_NOTIONAL", "NOTIONAL")):
            raise RuntimeError("Market order blocked: notional constraint requires an explicitly proven reference price")
        native_type = "LIMIT_MAKER" if tif == "POST_ONLY" else ("LIMIT" if intent.limit_price is not None else "MARKET")
        if "orderTypes" in spec and native_type not in set(spec["orderTypes"]): raise ValueError("Native Binance order type is not enabled for this symbol")
        body = {
            "symbol": symbol,
            "side": "BUY" if intent.side is Side.BUY else "SELL",
            "type": "LIMIT_MAKER" if tif == "POST_ONLY" else ("LIMIT" if intent.limit_price is not None else "MARKET"),
            "quantity": format(quantity, "f"),
            "newClientOrderId": intent.client_order_id,
        }
        if intent.limit_price is not None:
            body["price"] = format(_exact_decimal(intent.limit_price, "limit_price", positive=True), "f")
            if tif != "POST_ONLY": body["timeInForce"] = tif
        elif tif != "GTC":
            raise ValueError("Market orders support GTC only in this adapter")
        result = await self._signed_request("POST", "/api/v3/order", body, before_send=guard)
        if not isinstance(result, dict) or not re.fullmatch(r"[0-9]+", str(result.get("orderId", ""))):
            raise OutcomeUnknown("Binance order response lacked a valid native order ID")
        return result

    async def order_status(self, client_order_id: str) -> dict:
        raise RuntimeError("Binance Spot order_status requires explicit symbol; use query_order")

    async def query_order(self, symbol: str, *, order_id=None, client_order_id=None) -> dict:
        self._validate_context()
        symbol = self._spot_symbol(symbol)
        params = {"symbol": symbol, "orderId": order_id, "origClientOrderId": client_order_id}
        if order_id is None and client_order_id is None: raise ValueError("Exact native order or client ID required")
        try:
            row = await self._signed_request("GET", "/api/v3/order", params)
            if not isinstance(row, dict) or row.get("symbol") != symbol or (order_id is not None and row.get("orderId") != order_id) or (client_order_id is not None and row.get("clientOrderId") != client_order_id):
                raise IncompleteHistory("Binance order facts do not match exact query identity")
            return row
        except Exception as exc: return {"provider": self.name, "status": "UNKNOWN", "symbol": symbol, "reason": type(exc).__name__}

    async def cancel_order_with_permit(self, symbol: str, *, permit, order_id=None, client_order_id=None) -> dict:
        self._validate_context()
        symbol = self._spot_symbol(symbol)
        if order_id is None and client_order_id is None: raise ValueError("Exact native order or client ID required")
        if order_id is not None and (isinstance(order_id,bool) or not isinstance(order_id,int) or order_id < 0): raise ValueError("Native Binance order ID must be a nonnegative integer")
        if client_order_id is not None and (len(str(client_order_id)) > 36 or not re.fullmatch(r"[A-Za-z0-9_-]+", str(client_order_id))): raise ValueError("Invalid Binance client order ID")
        guard = self._permit_once(permit, self._validate_context)
        result = await self._signed_request("DELETE", "/api/v3/order", {"symbol": symbol, "orderId": order_id, "origClientOrderId": client_order_id}, before_send=guard)
        if not isinstance(result, dict) or not result.get("orderId") or order_id is not None and result.get("orderId") != order_id:
            raise OutcomeUnknown("Binance cancel response lacked native order identity")
        return result

    async def list_open_orders(self, symbol: str) -> dict:
        self._validate_context()
        symbol = self._spot_symbol(symbol)
        rows = await self._signed_request("GET", "/api/v3/openOrders", {"symbol": symbol})
        if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get("symbol") != symbol for row in rows):
            raise IncompleteHistory("Malformed or foreign Binance open-order response")
        return {"provider": self.name, "symbol": symbol, "rows": rows, "complete": True}

    async def fills(self, symbol: str, *, from_id=None, limit=1000) -> dict:
        self._validate_context()
        symbol = self._spot_symbol(symbol)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000: raise ValueError("limit must be integer in [1,1000]")
        if from_id is not None and (isinstance(from_id, bool) or not isinstance(from_id, int) or from_id < 0): raise ValueError("from_id must be a nonnegative integer")
        result, cursor, seen = [], from_id, set()
        for _ in range(10):
            if cursor is not None and cursor in seen: raise IncompleteHistory("Binance trade cursor cycle")
            if cursor is not None: seen.add(cursor)
            page = await self._signed_request("GET", "/api/v3/myTrades", {"symbol": symbol, "fromId": cursor, "limit": limit})
            if not isinstance(page, list): raise IncompleteHistory("Malformed Binance fills response")
            if any(not isinstance(row, dict) or row.get("symbol") != symbol or not all(k in row for k in ("id","orderId","qty","price","quoteQty","commission","commissionAsset","time")) for row in page): raise IncompleteHistory("Malformed or foreign Binance fill facts")
            for row in page:
                for key in ("qty", "price", "quoteQty", "commission"):
                    _exact_decimal(row[key], "Binance fill " + key, positive=(key in {"qty", "price"}))
            result.extend(page)
            if len(result) > 10000: raise IncompleteHistory("Binance fill row bound exceeded")
            if len(page) < limit: return {"provider": self.name, "symbol": symbol, "rows": result, "page_complete": True, "coverage": "RECENT_OR_FROM_ID_ONLY", "account_history_complete": False}
            if not page: raise IncompleteHistory("Empty full Binance page has no cursor advancement")
            last = page[-1].get("id")
            if isinstance(last, bool) or not isinstance(last, int): raise IncompleteHistory("Invalid Binance fill cursor")
            cursor = last + 1
        return {"provider":self.name,"symbol":symbol,"rows":result,"page_complete":False,"coverage":"RECENT_OR_FROM_ID_ONLY","account_history_complete":False}

    async def instrument_metadata(self, symbol: str) -> dict:
        self._validate_context()
        symbol = self._spot_symbol(symbol)
        data = await self._signed_request("GET", "/api/v3/exchangeInfo", {"symbol": symbol})
        if not isinstance(data, dict): raise IncompleteHistory("Malformed Binance exchangeInfo response")
        rows = data.get("symbols") or []
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows): raise IncompleteHistory("Malformed Binance exchangeInfo symbols")
        row = next((r for r in rows if r.get("symbol") == symbol), None)
        if row is None: raise IncompleteHistory("Symbol absent from explicit exchangeInfo")
        return row

    @staticmethod
    def _spot_symbol(value):
        text = str(value)
        if not re.fullmatch(r"[A-Z0-9]{5,24}", text): raise ValueError("Explicit exact Binance Spot symbol required")
        return text

    def _validate_context(self):
        ctx = getattr(self, "account_context", None)
        environment = "sandbox" if getattr(self, "use_testnet", False) else "live"
        host = "https://testnet.binance.vision" if getattr(self, "use_testnet", False) else "https://api.binance.com"
        allowed = {"venue","product","environment","account_alias","account_identity_verified","account_id","strategy_id","native_account_id"}
        if (not isinstance(ctx, dict) or not set(ctx) <= allowed or ctx.get("venue") != "binance" or ctx.get("product") != "spot"
                or ctx.get("environment") != environment or type(getattr(self,"use_testnet",None)) is not bool or not isinstance(ctx.get("account_alias"), str)
                or not ctx.get("account_alias") or len(ctx["account_alias"]) > 128
                or ctx.get("account_identity_verified") is not True or not isinstance(ctx.get("account_id"),str) or not ctx.get("account_id") or len(ctx["account_id"]) > 128
                or ctx.get("strategy_id") is not None and (not isinstance(ctx.get("strategy_id"),str) or len(ctx["strategy_id"]) > 128)
                or getattr(self, "base_url", "").rstrip("/") != host):
            raise RuntimeError("Explicit Binance Spot account context required")

    def _validate_spot_scope(self, intent):
        self._validate_context()
        if intent.instrument.venue is not Venue.BINANCE or intent.metadata.get("product") != "spot": raise ValueError("Explicit Binance Spot product scope required")
        if intent.metadata.get("reduce_only") or intent.instrument.asset_class is AssetClass.CRYPTO_PERP: raise ValueError("Perpetual fields are unsupported by Spot")
        if (intent.metadata.get("account_id") != self.account_context.get("account_id")
                or intent.metadata.get("environment") != self.account_context.get("environment")
                or self.account_context.get("strategy_id") is not None and intent.metadata.get("strategy_id") != self.account_context.get("strategy_id")
                or not isinstance(intent.metadata.get("strategy_id"), str) or not intent.metadata["strategy_id"]
                or len(intent.metadata["strategy_id"]) > 128): raise ValueError("Spot intent account/environment/strategy scope mismatch")

    @staticmethod
    def _validate_notional(filters, quantity, price):
        rule = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL")
        if rule is None: raise RuntimeError("Enabled Binance notional rule is missing")
        notional = quantity * price
        minimum = _exact_decimal(rule.get("minNotional"), "minimum notional", positive=True)
        maximum = _exact_decimal(rule.get("maxNotional"), "maximum notional") if rule.get("maxNotional") is not None else Decimal("0")
        if notional < minimum or maximum > 0 and notional > maximum: raise ValueError("Order violates fetched Binance notional filters")

    async def close(self):
        transport = getattr(self, "transport", None)
        if transport is not None: await transport.close()

    def _permit_once(self, permit, validate_scope=None):
        return _OwnerDispatchGuard(self, permit, validate_scope)

    async def _signed_request(self, method: str, path: str, params: dict[str, Any], *, before_send=None) -> Any:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Binance API key/secret are not configured")
        payload = {key: value for key, value in params.items() if value not in (None, "")}
        payload["timestamp"] = int(time.time() * 1000)
        query = urlencode(payload)
        signature = hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        query = f"{query}&signature={signature}"
        headers = {"X-MBX-APIKEY": self.api_key}
        url = f"{self.base_url}{path}"
        if self.transport is None:
            from execution.transports import AioHttpTransport
            self.transport = AioHttpTransport({self.base_url})
        if before_send is not None and not isinstance(before_send, _OwnerDispatchGuard): raise TypeError("Prevalidated synchronous owner dispatch guard required")
        if before_send is not None and getattr(self.transport, "mutation_permit_protocol", None) != 1:
            raise RuntimeError("Binance mutation transport lacks the synchronous dispatch permit protocol")
        try:
            response = await self.transport.request(method, f"{url}?{query}", headers=headers, before_send=before_send)
            data = _response_body(response)
            if before_send is not None and not before_send.authorized:
                raise OutcomeUnknown("Binance mutation transport returned without consuming the owner permit")
            if response.status >= 500:
                if before_send is not None and before_send.authorized: raise OutcomeUnknown("Binance mutation received an indeterminate server response")
                raise RuntimeError("Binance request failed before mutation dispatch")
            code = data.get("code") if isinstance(data, dict) else None
            if response.status >= 400 or (type(code) is int and code < 0):
                if type(code) is int and code in {-1001,-1006,-1007} and before_send is not None and before_send.authorized:
                    raise OutcomeUnknown("Binance reported an indeterminate execution outcome")
                safe_code = code if type(code) is int else "invalid"
                raise VenueRejected(f"Binance rejected request: status={response.status} code={safe_code}")
            if code is not None and type(code) is not int:
                if before_send is not None and before_send.authorized: raise OutcomeUnknown("Binance native response code is malformed")
                raise IncompleteHistory("Binance native response code is malformed")
            if not isinstance(data, (dict, list)):
                if before_send is not None and before_send.authorized: raise OutcomeUnknown("Malformed Binance response after mutation dispatch")
                raise IncompleteHistory("Malformed Binance response")
            return data
        except (TimeoutError, OSError, aiohttp.ClientError, asyncio.CancelledError) as exc:
            if before_send is not None and before_send.authorized: raise OutcomeUnknown("Binance mutation lost its response; reconcile without retry") from exc
            raise
        except (VenueRejected, OutcomeUnknown):
            raise
        except Exception as exc:
            if before_send is not None and before_send.authorized: raise OutcomeUnknown("Malformed Binance mutation response; reconcile without retry") from exc
            raise


class KiwoomBrokerAdapter:
    """Kiwoom KRX cash adapter with explicit mock/live account context.

    Context keys: venue, product (cash/krx_stock/krx_etf), environment
    (mock/live), account_alias, account_identity_verified (caller attestation
    only), account_id, optional strategy_id/native_account_id. Alias and native
    acctNo remain separate facts.
    """
    name = "kiwoom"

    def __init__(self, settings: RuntimeSettings | None = None, *, transport=None, account_context=None) -> None:
        self.settings = settings or get_settings()
        self.transport = transport
        self.account_context = account_context
        self._used_permits = weakref.WeakSet()
        provider = self.settings.providers["kiwoom"]
        self.app_key = provider.values.get("KIWOOM_APP_KEY")
        self.app_secret = provider.values.get("KIWOOM_APP_SECRET")
        self.use_mock = _bool(provider.values.get("KIWOOM_USE_MOCK"), default=True)
        self.base_url = "https://mockapi.kiwoom.com" if self.use_mock else "https://api.kiwoom.com"

    @property
    def scope(self):
        """Canonical cash routing identity, separate from native acctNo evidence."""
        self._validate_scope_context()
        return AccountScope(self.name, self.account_context["account_id"], "cash",
                            self.account_context["environment"])

    async def connect(self) -> None:
        self._validate_scope_context()
        await self._access_token()

    async def account_state(self) -> dict:
        self._validate_scope_context()
        identity = await self._post_api("/api/dostk/acnt", "ka00001", {})
        account_no = identity.get("acctNo")
        if not isinstance(account_no, str) or not re.fullmatch(r"[0-9]{10}", account_no): raise IncompleteHistory("Malformed Kiwoom native account identity")
        ctx = self.account_context
        if ctx.get("native_account_id") and ctx["native_account_id"] != account_no: raise IncompleteHistory("Kiwoom native account identity mismatch")
        facts = await self._paged_post("kt00018", {"qry_tp":"1", "dmst_stex_tp":"KRX"}, "acnt_evlt_remn_indv_tot")
        rows = facts["rows"]
        if any(not isinstance(row, dict) or not all(k in row for k in ("stk_cd", "rmnd_qty", "trde_able_qty")) for row in rows): raise IncompleteHistory("Malformed Kiwoom position facts")
        return {
            "provider": self.name,
            "mock": self.use_mock,
            "account_identity": {"native_account_id": account_no, "alias": ctx["account_alias"], "identity_proven": bool(ctx.get("native_account_id") == account_no)},
            "balances": facts.get("top_level_facts", {}), "positions": rows, "complete": facts["complete"],
        }

    async def submit_order(self, intent: OrderIntent) -> dict:
        raise RuntimeError("Kiwoom submit requires submit_order_with_permit and explicit scope")

    async def submit_order_with_permit(self, intent: OrderIntent, *, permit) -> dict:
        self._validate_scope(intent)
        if intent.side not in {Side.BUY, Side.SELL}: raise ValueError("Kiwoom side must be exact BUY or SELL")
        if intent.instrument.asset_class not in {AssetClass.EQUITY, AssetClass.ETF}:
            raise RuntimeError(f"Kiwoom adapter only supports domestic stock/ETF orders for now: {intent.instrument.asset_class}")
        if intent.quantity is None:
            raise RuntimeError("Kiwoom order requires quantity")
        symbol = self._kiwoom_symbol(intent.instrument.raw_symbol)
        qty = _exact_decimal(intent.quantity, "quantity", positive=True)
        if qty != qty.to_integral_value() or len(str(int(qty))) > 12: raise ValueError("Kiwoom quantity must fit whole-share native field")
        qty_text = str(int(qty))
        if not intent.client_order_id or len(intent.client_order_id) > 128: raise ValueError("Bounded local correlation ID required")
        body = {
            "dmst_stex_tp": "KRX",
            "stk_cd": symbol,
            "ord_qty": qty_text,
            "trde_tp": "0" if intent.limit_price is not None else "3",
            "ord_uv": format(intent.limit_price, "f") if intent.limit_price is not None else "",
            "cond_uv": "",
        }
        api_id = "kt10000" if intent.side is Side.BUY else "kt10001"
        if intent.limit_price is not None:
            price = _exact_decimal(intent.limit_price, "limit_price", positive=True)
            if price != price.to_integral_value() or len(str(int(price))) > 12: raise ValueError("Kiwoom cash limit price must be whole KRW within native field width")
            body["ord_uv"] = str(int(price))
        guard = self._permit_once(permit, lambda: self._validate_scope(intent))
        result = await self._post_api("/api/dostk/ordr", api_id, body, before_send=guard)
        native = result.get("ord_no") if isinstance(result, dict) else None
        if not isinstance(native, str) or not re.fullmatch(r"[0-9]{7}", native): raise OutcomeUnknown("Kiwoom submit response lacked a valid native order ID")
        return result

    async def order_status(self, client_order_id: str, *, symbol=None, day=None, order_id=None) -> dict:
        if symbol is None or day is None or order_id is None:
            return {"provider": self.name, "status": "UNKNOWN", "client_order_id": client_order_id, "absence_resolves_unknown": False, "reason": "explicit symbol/day/native order ID required"}
        return await self.query_order(symbol, day, order_id)

    @staticmethod
    def _kiwoom_symbol(value):
        text = str(value)
        if text.startswith("A"): text = text[1:]
        if not re.fullmatch(r"[0-9]{6}", text): raise ValueError("Kiwoom requires exact six-digit KRX stock symbol")
        return text

    @staticmethod
    def _native_order_id(value):
        text = str(value)
        if not re.fullmatch(r"[0-9]{7}", text): raise ValueError("Kiwoom native order number must be exactly seven digits")
        return text

    def _validate_scope(self, intent):
        ctx = getattr(self, "account_context", None)
        product = "krx_stock" if intent.instrument.asset_class is AssetClass.EQUITY else "krx_etf"
        allowed = {"venue","product","environment","account_alias","account_identity_verified","account_id","strategy_id","native_account_id"}
        if (not isinstance(ctx, dict) or not set(ctx) <= allowed or ctx.get("venue") != "kiwoom" or ctx.get("product") not in {"cash", "krx_stock", "krx_etf"}
                or ctx.get("environment") != ("mock" if self.use_mock else "live") or not isinstance(ctx.get("account_alias"), str)
                or not ctx.get("account_alias") or len(ctx["account_alias"]) > 128 or ctx.get("account_identity_verified") is not True
                or not isinstance(ctx.get("account_id"), str) or not ctx.get("account_id")
                or getattr(self, "base_url", "") != ("https://mockapi.kiwoom.com" if self.use_mock else "https://api.kiwoom.com")):
            raise RuntimeError("Explicit Kiwoom account context required")
        if intent.instrument.venue not in {Venue.KRX, Venue.KIWOOM}: raise ValueError("Unsupported Kiwoom venue")
        if (intent.instrument.asset_class not in {AssetClass.EQUITY,AssetClass.ETF} or intent.metadata.get("product") not in {product,"cash"}
                or intent.metadata.get("account_id") != ctx.get("account_id") or intent.metadata.get("environment") != ctx.get("environment")
                or not isinstance(intent.metadata.get("strategy_id"), str) or not intent.metadata["strategy_id"] or len(intent.metadata["strategy_id"]) > 128
                or ctx.get("strategy_id") is not None and intent.metadata.get("strategy_id") != ctx.get("strategy_id")
                or intent.metadata.get("reduce_only") or intent.metadata.get("post_only")):
            raise ValueError("Explicit KRX product scope required; unsupported order flags")
        if str(intent.metadata.get("time_in_force", "GTC")).upper() != "GTC":
            raise ValueError("Kiwoom adapter supports only explicitly mapped limit/market orders")

    def _permit_once(self, permit, validate_scope=None):
        return _OwnerDispatchGuard(self, permit, validate_scope)

    async def cancel_order_with_permit(self, symbol, order_id, *, permit):
        self._validate_scope_context()
        guard = self._permit_once(permit, self._validate_scope_context)
        body = {"dmst_stex_tp": "KRX", "orig_ord_no": self._native_order_id(order_id), "stk_cd": self._kiwoom_symbol(symbol), "cncl_qty": "0"}
        result = await self._post_api("/api/dostk/ordr", "kt10003", body, before_send=guard)
        if not isinstance(result, dict) or not isinstance(result.get("ord_no"), str) or not re.fullmatch(r"[0-9]{7}", result["ord_no"]) or result.get("base_orig_ord_no") != order_id:
            raise OutcomeUnknown("Kiwoom cancel response lacks matching native request/original IDs")
        return {"provider": self.name, "status": "CANCEL_PENDING", "original_order_id": order_id, "cancel_request_id": result.get("ord_no"), "raw": result}

    async def modify_order_with_permit(self, symbol, order_id, quantity, price, *, permit):
        self._validate_scope_context()
        qty = _exact_decimal(quantity, "quantity", positive=True)
        if qty != qty.to_integral_value() or len(str(int(qty))) > 12: raise ValueError("Kiwoom quantity must fit whole-share native field")
        qty_text = str(int(qty))
        px = _exact_decimal(price, "price", positive=True)
        if px != px.to_integral_value() or len(str(int(px))) > 12: raise ValueError("Kiwoom price must be whole KRW within native field width")
        body = {"dmst_stex_tp": "KRX", "orig_ord_no": self._native_order_id(order_id), "stk_cd": self._kiwoom_symbol(symbol), "mdfy_qty": qty_text, "mdfy_uv": str(int(px)), "mdfy_cond_uv": ""}
        guard = self._permit_once(permit, self._validate_scope_context)
        result = await self._post_api("/api/dostk/ordr", "kt10002", body, before_send=guard)
        if not isinstance(result, dict) or not isinstance(result.get("ord_no"), str) or not re.fullmatch(r"[0-9]{7}", result["ord_no"]) or result.get("base_orig_ord_no") != order_id:
            raise OutcomeUnknown("Kiwoom modify response lacks matching native request/original IDs")
        return {"provider": self.name, "status": "MODIFY_PENDING", "original_order_id": order_id, "modify_request_id": result.get("ord_no"), "raw": result}

    def _validate_scope_context(self):
        ctx = getattr(self, "account_context", None)
        allowed = {"venue","product","environment","account_alias","account_identity_verified","account_id","strategy_id","native_account_id"}
        if (not isinstance(ctx, dict) or not set(ctx) <= allowed or ctx.get("venue") != "kiwoom" or ctx.get("product") not in {"cash", "krx_stock", "krx_etf"}
                or type(getattr(self,"use_mock",None)) is not bool or ctx.get("environment") != ("mock" if self.use_mock else "live") or not isinstance(ctx.get("account_alias"), str)
                or not ctx.get("account_alias") or len(ctx["account_alias"]) > 128 or ctx.get("account_identity_verified") is not True
                or not isinstance(ctx.get("account_id"), str) or not ctx.get("account_id")
                or getattr(self, "base_url", "") != ("https://mockapi.kiwoom.com" if self.use_mock else "https://api.kiwoom.com")):
            raise RuntimeError("Explicit Kiwoom account context required")

    async def query_order(self, symbol, day, order_id):
        self._validate_scope_context()
        symbol, order_id = self._kiwoom_symbol(symbol), self._native_order_id(order_id)
        if not re.fullmatch(r"[0-9]{8}", str(day)): raise ValueError("Explicit YYYYMMDD day required")
        pages = await self._paged_post("kt00007", {"qry_tp":"1", "stk_bond_tp":"1", "sell_tp":"0", "stk_cd":symbol, "dmst_stex_tp":"KRX", "ord_dt":str(day)}, "acnt_ord_cntr_prps_dtl")
        rows = [r for r in pages["rows"] if (str(r.get("ord_no", "")) == order_id or str(r.get("ori_ord", "")) == order_id) and self._row_symbol(r) == symbol]
        if not rows: return {"provider":self.name,"status":"UNKNOWN","complete":pages["complete"],"absence_resolves_unknown":False,"rows":[]}
        return {"provider":self.name,"status":"FACTS","complete":pages["complete"],"rows":rows,"identity_proven":True}

    @staticmethod
    def _row_symbol(row):
        value = str(row.get("stk_cd", ""))
        return value[1:] if value.startswith("A") else value

    async def list_open_orders(self, symbol=None):
        self._validate_scope_context()
        body = {"all_stk_tp":"0","trde_tp":"0","stex_tp":"1"}
        expected = self._kiwoom_symbol(symbol) if symbol is not None else None
        if expected: body["stk_cd"] = expected
        result = await self._paged_post("ka10075", body, "oso")
        rows = [r for r in result["rows"] if expected is None or self._row_symbol(r) == expected]
        return {"provider":self.name,"rows":rows,"complete":result["complete"]}

    async def fills(self, symbol=None, order_id=None):
        self._validate_scope_context()
        body = {"qry_tp":"0","sell_tp":"0","stex_tp":"1"}
        expected = self._kiwoom_symbol(symbol) if symbol is not None else None
        native = self._native_order_id(order_id) if order_id is not None else None
        if expected: body["stk_cd"] = expected
        result = await self._paged_post("ka10076", body, "cntr")
        rows = []
        for row in result["rows"]:
            if native and str(row.get("ord_no", "")) != native: continue
            if expected and self._row_symbol(row) != expected: continue
            fact = {"raw":row,"event_id":None,"identity_proven":False,"quantity_semantics":"VENDOR_REPORTED_UNVERIFIED"}
            if "tdy_trde_cmsn" in row: fact["fee"] = row["tdy_trde_cmsn"]; fact["fee_basis"] = "VENDOR_DAY_REPORTED"
            if "tdy_trde_tax" in row: fact["tax"] = row["tdy_trde_tax"]; fact["tax_basis"] = "VENDOR_DAY_REPORTED"
            rows.append(fact)
        return {"provider":self.name,"rows":rows,"complete":result["complete"]}

    async def execution_fills(self, symbol, day, order_id=None, *, max_pages=32, max_rows=4096):
        """Read native per-execution rows from kt00009; this endpoint has no fee field."""
        self._validate_scope_context()
        symbol = self._kiwoom_symbol(symbol)
        if not isinstance(day, str) or not re.fullmatch(r"[0-9]{8}", day):
            raise ValueError("Kiwoom execution history requires YYYYMMDD")
        if order_id is not None:
            order_id = self._native_order_id(order_id)
        if type(max_pages) is not int or not 1 <= max_pages <= 128 or type(max_rows) is not int or not 1 <= max_rows <= 10000:
            raise ValueError("Invalid bounded Kiwoom history limits")
        body = {"ord_dt": day, "stk_bond_tp": "1", "mrkt_tp": "0", "sell_tp": "0",
                "qry_tp": "1", "dmst_stex_tp": "KRX", "stk_cd": symbol}
        # kt00009 has no native order-number filter; query the complete day and filter locally.
        result = await self._paged_post("kt00009", body, "acnt_ord_cntr_prst_array",
                                        max_pages=max_pages, max_rows=max_rows)
        rows = []
        seen_executions = {}
        for raw in result["rows"]:
            raw_symbol = self._row_symbol(raw)
            if raw_symbol != symbol:
                continue
            native_order = str(raw.get("ord_no", ""))
            if not re.fullmatch(r"[0-9]{7}", native_order):
                raise IncompleteHistory("Kiwoom row lacks a seven-digit native order ID")
            if order_id is not None and native_order != order_id:
                continue
            if raw.get("io_tp_nm") not in {"현금매수", "현금매도"} or raw.get("mdfy_cncl_tp") not in (None, "", "0"):
                continue
            execution_id = str(raw.get("cntr_no", ""))
            if not re.fullmatch(r"[0-9]{7}", execution_id) or execution_id == "0000000":
                raise IncompleteHistory("Kiwoom cash execution row lacks a valid native execution ID")
            try:
                quantity, price = Decimal(str(raw.get("cntr_qty", ""))), Decimal(str(raw.get("cntr_uv", "")))
            except (InvalidOperation, ValueError) as exc:
                raise IncompleteHistory("Malformed Kiwoom execution quantity or price") from exc
            if not quantity.is_finite() or not price.is_finite() or quantity <= 0 or price <= 0:
                raise IncompleteHistory("Invalid Kiwoom execution quantity or price")
            if quantity != quantity.to_integral_value() or price != price.to_integral_value():
                raise IncompleteHistory("Kiwoom execution quantity and price must be whole units")
            fact = {"kind": "fill", "scope_key": self.scope.key,
                    "event_id": f"kiwoom:{day}:{native_order}:{execution_id}",
                    "symbol": symbol, "order_id": native_order,
                    "side": "buy" if raw["io_tp_nm"] == "현금매수" else "sell",
                    "quantity": str(quantity), "price": str(price), "fee": None,
                    "fee_known": False, "fee_currency": None,
                    "exchange_time": raw.get("cntr_tm"), "exchange_time_precision": "1s", "raw": raw}
            previous = seen_executions.get(fact["event_id"])
            if previous is not None:
                if previous != fact:
                    raise IncompleteHistory("Conflicting Kiwoom facts share one native execution ID")
                continue
            seen_executions[fact["event_id"]] = fact
            rows.append(fact)
        return {"provider": self.name, "scope_key": self.scope.key, "symbol": symbol,
                "day": day, "rows": rows, "complete": result["complete"]}

    async def _paged_post(self, api_id, body, row_key, *, max_pages=20, max_rows=5000):
        self._validate_scope_context()
        all_rows, seen = [], set(); cont, key = "N", ""; complete = False; top_level_facts = {}; total_bytes = 0
        for _ in range(max_pages):
            response = await self._post_api("/api/dostk/acnt", api_id, body, cont_yn=cont, next_key=key, with_headers=True)
            page, headers = response
            if not isinstance(page, dict) or not isinstance(headers, dict): raise IncompleteHistory("Malformed Kiwoom page response")
            rows = page.get(row_key)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows): raise IncompleteHistory("Kiwoom response missing valid expected row table")
            total_bytes += len(json.dumps(page, ensure_ascii=False, separators=(",", ":"), default=_json_default).encode("utf-8"))
            if total_bytes > 2_000_000: raise IncompleteHistory("Kiwoom response byte bound exceeded")
            top_level_facts.update({k:v for k,v in page.items() if k != row_key})
            all_rows.extend(rows)
            if len(all_rows) > max_rows: raise IncompleteHistory("Kiwoom row bound exceeded")
            cont = _header(headers, "cont-yn"); key = _header(headers, "next-key")
            if cont is None or cont.upper() not in {"Y","N"}: raise IncompleteHistory("Kiwoom continuation evidence is absent or invalid")
            if cont.upper() == "N": complete = True; break
            token = (cont, key)
            if not key or token in seen: raise IncompleteHistory("Kiwoom continuation cycle or missing next-key")
            seen.add(token)
        return {"rows":all_rows,"complete":complete,"top_level_facts":top_level_facts}

    async def _post_api(self, path: str, api_id: str, body: dict[str, Any], *, before_send=None, cont_yn="N", next_key="", with_headers=False):
        if before_send is not None and not isinstance(before_send, _OwnerDispatchGuard): raise TypeError("Prevalidated synchronous owner dispatch guard required")
        if self.transport is None:
            from execution.transports import AioHttpTransport
            self.transport = AioHttpTransport({self.base_url})
        if before_send is not None and getattr(self.transport, "mutation_permit_protocol", None) != 1: raise RuntimeError("Kiwoom mutation transport lacks the synchronous dispatch permit protocol")
        token = await self._access_token()
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": api_id,
            "cont-yn": cont_yn,
            "next-key": next_key,
        }
        try:
            response = await self.transport.request("POST", f"{self.base_url}{path}", json_body=body, headers=headers, before_send=before_send)
            data = _response_body(response)
            if before_send is not None and not before_send.authorized: raise OutcomeUnknown("Kiwoom mutation transport returned without consuming owner permit")
            if response.status >= 500:
                if before_send is not None and before_send.authorized: raise OutcomeUnknown("Kiwoom mutation received indeterminate server response")
                raise RuntimeError("Kiwoom request failed before mutation dispatch")
            if response.status >= 400 or not isinstance(data, dict) or (data.get("return_code") is not None and (type(data["return_code"]) is not int or data["return_code"] != 0)):
                code = data.get("return_code") if isinstance(data, dict) else None
                if before_send is not None and before_send.authorized and (response.status >= 500 or not isinstance(data, dict)): raise OutcomeUnknown("Kiwoom mutation response was malformed")
                safe_code = code if type(code) is int else "invalid"
                if response.status < 400 and type(code) is not int:
                    if before_send is not None and before_send.authorized: raise OutcomeUnknown("Kiwoom native response code is malformed")
                    raise IncompleteHistory("Kiwoom native response code is malformed")
                raise VenueRejected(f"Kiwoom rejected request: status={response.status} code={safe_code}")
            return (data, response.headers) if with_headers else data
        except (TimeoutError, OSError, aiohttp.ClientError, asyncio.CancelledError) as exc:
            if before_send is not None and before_send.authorized: raise OutcomeUnknown("Kiwoom mutation lost its response; reconcile without retry") from exc
            raise
        except (VenueRejected, OutcomeUnknown):
            raise
        except Exception as exc:
            if before_send is not None and before_send.authorized: raise OutcomeUnknown("Malformed Kiwoom mutation response; reconcile without retry") from exc
            raise

    async def close(self):
        transport = getattr(self, "transport", None)
        if transport is not None: await transport.close()

    async def _access_token(self) -> str:
        cached = self._read_token_cache()
        if cached:
            return cached
        if not self.app_key or not self.app_secret:
            raise RuntimeError("KIWOOM_APP_KEY/KIWOOM_APP_SECRET are not configured")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(
                f"{self.base_url}/oauth2/token",
                json={"grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self.app_secret},
                headers={"Content-Type": "application/json;charset=UTF-8"},
            ) as response:
                data = await _safe_json(response)
                if response.status >= 400 or data.get("return_code") not in (None, 0):
                    raise RuntimeError(f"Kiwoom token failed status={response.status} code={data.get('return_code')} msg={data.get('return_msg')}")
                token = data.get("token")
                expires_dt = data.get("expires_dt")
                if not token or not expires_dt:
                    raise RuntimeError("Kiwoom token response missing token/expires_dt")
                self._write_token_cache(str(token), str(expires_dt))
                return str(token)

    def _cache_path(self) -> Path:
        suffix = "mock" if self.use_mock else "real"
        return self.settings.state_dir / f"kiwoom_token_{suffix}.json"

    def _read_token_cache(self) -> str | None:
        path = self._cache_path()
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            token = payload.get("token")
            expires_dt = str(payload.get("expires_dt") or "")
            expiry = datetime.strptime(expires_dt, "%Y%m%d%H%M%S")
            if expiry <= datetime.now() + timedelta(minutes=10):
                return None
            return str(token) if token else None
        except Exception:
            return None

    def _write_token_cache(self, token: str, expires_dt: str) -> None:
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"token": token, "expires_dt": expires_dt}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


from execution.kis_execution import KisCashBroker


class KisBrokerAdapter(KisCashBroker):
    """Generic execution defaults to the configured mode; ETF loop uses mock."""

    def __init__(self, settings: RuntimeSettings | None = None, *, mode: str | None = None) -> None:
        settings = settings or get_settings()
        super().__init__(settings, mode=mode or ("mock" if _bool(settings.env.get("KIS_USE_MOCK"), default=True) else "real"))


class HyperliquidBrokerAdapter:
    name = "hyperliquid"

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self.user = self.settings.env.get("HYPERLIQUID_USER") or self.settings.env.get("HYPERLIQUID_API_KEY")

    async def connect(self) -> None:
        if not self.user:
            raise RuntimeError("HYPERLIQUID_USER or HYPERLIQUID_API_KEY is not configured")

    async def account_state(self) -> dict:
        if not self.user or not re.fullmatch(r"0x[a-fA-F0-9]{40}", self.user):
            return {"provider": self.name, "status": "SKIPPED_MISSING_PUBLIC_USER_ADDRESS"}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post("https://api.hyperliquid.xyz/info", json={"type": "clearinghouseState", "user": self.user}) as response:
                data = await _safe_json(response)
                if response.status >= 400:
                    raise RuntimeError(f"Hyperliquid account_state failed status={response.status}")
                positions = data.get("assetPositions") or []
                return {"provider": self.name, "positions_count": len(positions), "has_margin_summary": bool(data.get("marginSummary"))}

    async def submit_order(self, intent: OrderIntent) -> dict:
        del intent
        raise RuntimeError("Hyperliquid order submission requires wallet/private-key signing; current env only exposes API/public key")

    async def order_status(self, client_order_id: str) -> dict:
        return {"provider": self.name, "status": "NOT_IMPLEMENTED", "client_order_id": client_order_id}


class TossBrokerAdapter:
    name = "toss"

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = TossAuthClient(self.settings)
        self._account_seq: str | None = None

    async def connect(self) -> None:
        if not self.client.has_credentials:
            raise RuntimeError("TOSS_API_KEY/TOSS_API_SECRET are not configured")
        await self.client.access_token()
        self._account_seq = await self.client.selected_account_seq()

    async def account_state(self) -> dict:
        return await self.client.account_state_summary()

    async def submit_order(self, intent: OrderIntent) -> dict:
        raise RuntimeError("TOSS DATA_ONLY: submit_order rejected - mutations not permitted")

    async def order_status(self, client_order_id: str) -> dict:
        account_seq = self._account_seq or await self.client.selected_account_seq()
        matches: list[dict[str, Any]] = []
        for status in ("OPEN", "CLOSED"):
            payload = await self.client.request("GET", "/api/v1/orders", params={"status": status, "limit": 100}, account_seq=account_seq)
            result = payload.get("result") or {}
            orders = result.get("orders") if isinstance(result, dict) else []
            if isinstance(orders, list):
                matches.extend([order for order in orders if order.get("clientOrderId") == client_order_id])
        return {"provider": self.name, "client_order_id": client_order_id, "matches": matches}


def broker_for_name(name: str, settings: RuntimeSettings | None = None) -> BrokerAdapter:
    from .extended_brokers import ArcusBrokerAdapter, GrvtBrokerAdapter
    normalized = name.strip().lower()
    mapping = {
        "binance": BinanceBrokerAdapter,
        "kiwoom": KiwoomBrokerAdapter,
        "kis": KisBrokerAdapter,
        "hyperliquid": HyperliquidBrokerAdapter,
        "toss": TossBrokerAdapter,
        "arcus": ArcusBrokerAdapter,
        "grvt": GrvtBrokerAdapter,
    }
    if normalized not in mapping:
        raise ValueError(f"Unsupported broker: {name}")
    return mapping[normalized](settings)


async def _safe_json(response: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        data = await response.json(content_type=None)
        return data if isinstance(data, dict) else {"payload": data}
    except Exception:
        text = await response.text()
        return {"text": text[:500]}

