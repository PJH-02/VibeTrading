"""Explicit perpetual execution boundaries, with no settings or account discovery.

This module imports no runtime settings, runner, feeds, or optional signing SDK.
HTTP is supplied by the caller. A transport declaring mutation_permit_protocol=1
must call before_send synchronously at its actual mutation dispatch seam, after
all queue/auth/connection waits, and must not dispatch when that callback raises.
The existing ExecutionOwner remains offline-only; these adapters do not activate it.

Code receipts cover HTTP lifecycle and native private-frame normalization only.
They do not attest stream connectivity, account identity, sandbox or live trading.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import inspect
import json
import re
import time
from typing import Any, Callable, Protocol
from urllib.parse import urlencode
import weakref


class CapabilityBlocked(RuntimeError):
    pass


class OutcomeUnknown(RuntimeError):
    """The mutation may have reached the venue. Reconcile; never blind retry."""


class VenueRejected(RuntimeError):
    """An explicit vendor rejection, with no signed/auth response echoed."""


class IncompleteHistory(RuntimeError):
    pass


@dataclass(frozen=True)
class PerpScope:
    venue: str
    product: str
    environment: str
    account_alias: str
    settlement_currency: str
    subaccount: str = ""

    def __post_init__(self):
        if self.venue not in {"binance", "bybit", "grvt", "arcus", "hyperliquid"}:
            raise CapabilityBlocked("Unsupported execution venue; TOSS is DATA_ONLY")
        if self.product != "linear_perpetual":
            raise CapabilityBlocked("Only explicit linear_perpetual is supported; Spot/COIN-M/inverse are unsupported")
        if self.environment not in {"sandbox", "live"}:
            raise ValueError("Explicit sandbox/live environment is required")
        if not self.account_alias or len(self.account_alias) > 128 or not self.settlement_currency:
            raise ValueError("Bounded account alias and settlement currency are required")
        allowed = {"binance": {"USDT", "USDC"}, "bybit": {"USDT", "USDC"},
                   "grvt": {"USDT"}, "arcus": {"USDG"}, "hyperliquid": {"USDC"}}
        if self.settlement_currency not in allowed[self.venue]:
            raise CapabilityBlocked("Settlement currency is outside this adapter's product boundary")
        if self.venue == "grvt" and (not self.subaccount.isdigit() or not 0 < int(self.subaccount) < 2**64):
            raise ValueError("GRVT requires an explicit uint64 subaccount")

    @property
    def key(self):
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Capability:
    scope: PerpScope
    code_complete: bool
    blockers: tuple[str, ...]
    official_sources: tuple[str, ...]
    request_mapping_complete: bool = False
    sandbox_verified: bool = False
    live_verified: bool = False
    owner_integrated: bool = False
    private_stream: str = "NATIVE_FRAME_NORMALIZER_ONLY"
    implementation_status: str = "REQUEST_MAPPING_ONLY"
    runtime_readiness: str = "BLOCKED_UNTIL_OWNER_TRANSPORT_ACCOUNT_SIGNER_PROOF"

    def receipt(self):
        return asdict(self)


SOURCES = {
    "binance": ("https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info",),
    "bybit": ("https://bybit-exchange.github.io/docs/v5/guide",
              "https://bybit-exchange.github.io/docs/v5/account/transaction-log"),
    "grvt": ("https://api-docs.grvt.io/trading_api/",
             "https://api-docs.grvt.io/schemas/signature/",
             "https://api-docs.grvt.io/schemas/funding_payment/"),
    "arcus": ("https://docs.arcus.xyz/llms.txt",
              "https://docs.arcus.xyz/api-reference/authentication.md",
              "https://docs.arcus.xyz/guides/websocket-trading"),
    "hyperliquid": ("https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint",
                   "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets",
                   "https://github.com/hyperliquid-dex/hyperliquid-python-sdk"),
}
BLOCKERS = {}
ADAPTER_COMPLETION_BLOCKERS = (
    "Concrete venue transport with a verified mutation-permit dispatch seam is not implemented here",
    "Private stream connection, authentication/subscription, reconnect and gap recovery lifecycle is not implemented here",
    "Perpetual account execution-owner and reconciliation integration is not implemented here",
)


def capability_for(scope: PerpScope) -> Capability:
    # Request/signing/frame mappings are independently usable with an injected
    # scripted transport. They do not satisfy VENUE_ADAPTER_CODE_COMPLETE.
    mapping_complete = scope.venue not in BLOCKERS
    return Capability(scope=scope, code_complete=False,
        blockers=BLOCKERS.get(scope.venue, ()) + ADAPTER_COMPLETION_BLOCKERS,
        official_sources=SOURCES[scope.venue], request_mapping_complete=mapping_complete)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: Any
    headers: dict[str, str] | None = None


class Transport(Protocol):
    mutation_permit_protocol: int

    async def request(self, method: str, url: str, *, params: dict | None = None,
                      json_body: dict | None = None, data: str | None = None,
                      headers: dict | None = None, before_send: Callable | None = None) -> HttpResponse: ...


def _number(value, name, *, positive=False):
    if value is None or isinstance(value, (bool, float)):
        raise ValueError(name + " requires an exact decimal string/integer")
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(name + " is not decimal") from error
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(name + " must be finite" + (" and positive" if positive else ""))
    return result


def _text(value, name):
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(name + " requires a bounded explicit string")
    return value


def _wire(value):
    return format(value, "f")


def _json(body):
    return json.dumps(body, separators=(",", ":"), ensure_ascii=True)


def _uint(value, bits, name):
    if isinstance(value, bool) or not str(value).isdigit() or not 0 <= int(value) < 2**bits:
        raise ValueError(name + " must be uint" + str(bits))
    return int(value)


class GrvtSigningComposition:
    """Reuse an explicitly constructed existing GrvtBrokerAdapter signer.

    intent_factory(normalized_order) supplies the existing OrderIntent type.
    No constructor or get_settings call is made here. Authentication/transport
    remain in this module; the composed adapter is used only to prepare a signature.
    """
    def __init__(self, adapter, intent_factory: Callable):
        self.adapter, self.intent_factory = adapter, intent_factory

    def prepare(self, scope, order, instrument, now_ns):
        expected_chain = 326 if scope.environment == "sandbox" else 325
        if (self.adapter.chain_id != expected_chain
                or str(self.adapter.sub_account) != scope.subaccount
                or bool(self.adapter.use_testnet) != (scope.environment == "sandbox")):
            raise CapabilityBlocked("Composed GRVT signer scope differs from execution scope")
        self.adapter.instruments[order["symbol"]] = deepcopy(instrument["raw"])
        message = self.adapter.prepare_order(self.intent_factory(deepcopy(order)), now_ns=now_ns)
        return message["params"]["order"]


class _Adapter:
    def __init__(self, scope, transport, *, clock_ns=time.time_ns, timeout_seconds=15, max_pages=32):
        self.scope, self.transport, self.clock_ns = scope, transport, clock_ns
        if getattr(transport, "mutation_permit_protocol", None) != 1:
            raise CapabilityBlocked("Transport must implement the one-use network-seam permit protocol")
        if not 0 < timeout_seconds <= 60 or not isinstance(max_pages, int) or not 0 < max_pages <= 128:
            raise ValueError("Transport timeout and page count must be explicitly bounded")
        self.timeout_seconds, self.max_pages = timeout_seconds, max_pages
        self._metadata = {}
        self._used_permits = weakref.WeakSet()

    @property
    def capability(self):
        return capability_for(self.scope)

    @staticmethod
    def _require_permit(permit):
        if (not callable(permit) or inspect.iscoroutinefunction(permit)
                or inspect.iscoroutinefunction(getattr(permit, "__call__", None))):
            raise TypeError("Mutation requires a synchronous one-use owner permit")

    def _scope_row(self, row):
        return {"scope_key": self.scope.key, "venue": self.scope.venue,
                "product": self.scope.product, "environment": self.scope.environment, **row}

    async def _request(self, method, url, *, params=None, json_body=None, data=None,
                       headers=None, permit=None, rpc_message=None):
        consumed = False
        mutation = permit is not None
        if mutation:
            if not callable(permit) or inspect.iscoroutinefunction(permit):
                raise TypeError("Owner permit must be synchronous and one-use")
            try:
                if permit in self._used_permits:
                    raise CapabilityBlocked("Owner permit has already been used")
                weakref.ref(permit)
                hash(permit)
            except TypeError as error:
                raise TypeError("Owner permit must support weak identity tracking") from error

        def before_send():
            nonlocal consumed
            if consumed or permit in self._used_permits:
                raise CapabilityBlocked("Transport attempted duplicate mutation dispatch")
            result = permit()
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError("Owner permit must not return an awaitable")
            self._used_permits.add(permit)
            consumed = True

        try:
            if rpc_message is None:
                operation = self.transport.request(method, url, params=params, json_body=json_body,
                    data=data, headers=headers, before_send=before_send if mutation else None)
            else:
                operation = self.transport.rpc(url, rpc_message,
                    before_send=before_send if mutation else None)
            response = await asyncio.wait_for(operation, self.timeout_seconds)
        except BaseException as error:
            if consumed:
                raise OutcomeUnknown("Mutation outcome unknown; retain reservation and reconcile") from error
            raise
        if mutation and not consumed:
            raise OutcomeUnknown("Transport violated mutation permit protocol; quarantine transport")
        if not isinstance(response, HttpResponse):
            if mutation:
                raise OutcomeUnknown("Malformed mutation response")
            raise ValueError("Transport must return HttpResponse")
        if response.status >= 500:
            if mutation:
                raise OutcomeUnknown("Venue server error after mutation dispatch")
            raise RuntimeError("Venue read unavailable")
        if response.status >= 400:
            raise VenueRejected("Venue rejected request: HTTP " + str(response.status))
        return response

    def _metadata_row(self, symbol, raw, *, tick, lot, minimum, notional, base, quote, settlement,
                      distinct_quote=False):
        if settlement != self.scope.settlement_currency or (quote != settlement and not distinct_quote):
            raise CapabilityBlocked("Cross-currency or wrong-settlement perpetual is unsupported")
        row = self._scope_row({"symbol": symbol, "kind": "linear_perpetual", "multiplier": "1",
            "tick_size": _wire(_number(tick, "tick", positive=True)),
            "lot_size": _wire(_number(lot, "lot", positive=True)),
            "min_quantity": _wire(_number(minimum, "minimum", positive=True)),
            "min_notional": _wire(_number(notional, "min_notional")),
            "base_currency": base, "quote_currency": quote, "settlement_currency": settlement,
            "collateral_currency": settlement, "position_mode": None, "margin_mode": None,
            "account_configuration_verified": False,
            "raw": deepcopy(raw)})
        if Decimal(row["min_notional"]) < 0:
            raise ValueError("Negative instrument minimum")
        self._metadata[symbol] = row
        return deepcopy(row)

    def _order(self, order):
        if not isinstance(order, dict) or order.get("scope_key") != self.scope.key:
            raise ValueError("Order must carry exact execution scope_key")
        symbol = _text(order.get("symbol"), "symbol")
        if symbol not in self._metadata:
            raise CapabilityBlocked("Fetch and validate instrument metadata before submission")
        result = deepcopy(order)
        if result.get("side") not in {"buy", "sell"} or type(result.get("reduce_only")) is not bool:
            raise ValueError("Explicit side and reduce_only boolean required")
        if result.get("time_in_force") not in {"GTC", "IOC", "FOK", "POST_ONLY"}:
            raise ValueError("Explicit supported time_in_force required")
        quantity = _number(result.get("quantity"), "quantity", positive=True)
        price = _number(result.get("limit_price"), "limit_price", positive=True)
        metadata = self._metadata[symbol]
        if quantity % Decimal(metadata["lot_size"]) or price % Decimal(metadata["tick_size"]):
            raise ValueError("Price/quantity is not an exact instrument increment")
        if quantity < Decimal(metadata["min_quantity"]):
            raise ValueError("Quantity is below instrument minimum")
        if quantity * price < Decimal(metadata["min_notional"]):
            raise ValueError("Order notional is below instrument minimum")
        result.update(quantity=_wire(quantity), limit_price=_wire(price))
        self._client_id(result.get("client_order_id"))
        return result

    def _client_id(self, value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,36}", value):
            raise ValueError("Client ID must fit venue format without truncation")
        return value

    def _unknown_order(self, symbol, client_id):
        return self._scope_row({"symbol": symbol, "client_order_id": client_id,
            "status": "UNKNOWN", "history_complete": False, "terminal": False, "raw": None})

    def _cursor(self, stream, symbol, start_ns, end_ns, cursor):
        if (isinstance(start_ns, bool) or isinstance(end_ns, bool) or not isinstance(start_ns, int)
                or not isinstance(end_ns, int) or not 0 <= start_ns <= end_ns):
            raise ValueError("Explicit ordered nanosecond history interval required")
        expected = {"scope_key": self.scope.key, "stream": stream, "symbol": symbol,
                    "start_ns": start_ns, "end_ns": end_ns}
        if cursor:
            if any(cursor.get(k) != v for k, v in expected.items()) or cursor.get("complete"):
                raise ValueError("History cursor scope/window differs or is already complete")
            if not isinstance(cursor.get("page"), int) or not 1 <= cursor["page"] < self.max_pages:
                raise IncompleteHistory("Bounded history page budget exhausted")
        return expected

    def _page(self, rows, identity, next_token, previous):
        seen = list(previous.get("seen", [])) if previous else []
        if next_token:
            if str(next_token) in seen:
                raise IncompleteHistory("History cursor did not advance or cycled")
            seen.append(str(next_token))
        return {"rows": rows, "cursor": {**identity, "token": next_token,
                "page": (previous["page"] if previous else 0) + 1, "seen": seen,
                "complete": not bool(next_token)}, "complete": not bool(next_token)}

    async def reconcile(self, symbol, *, start_ns, end_ns, fill_cursor=None, funding_cursor=None):
        # Caller persists each returned row before acknowledging its cursor.
        # No local watermark is promoted here and empty results never reject an intent.
        account = await self.account_state()
        positions = await self.positions(symbol)
        orders = await self.working_orders(symbol)
        fills = await self.fills(symbol, start_ns=start_ns, end_ns=end_ns, cursor=fill_cursor)
        funding = await self.funding(symbol, start_ns=start_ns, end_ns=end_ns, cursor=funding_cursor)
        return self._scope_row({"account": account, "positions": positions, "working_orders": orders,
            "fills": fills, "funding": funding, "cursor_requires_durable_apply": True,
            "complete": fills["complete"] and funding["complete"], "absence_resolves_unknown": False})

    def normalize_gap(self, reason, *, stream="private"):
        return self._scope_row({"kind": "gap", "stream": _text(stream, "stream"),
            "reason": _text(reason, "gap reason"), "requires_reconciliation": True,
            "ready": False, "absence_resolves_unknown": False})


class BinancePerpAdapter(_Adapter):
    """USDⓈ-M perpetuals only; net/cross margin. No /api/v3 or /dapi calls."""
    def __init__(self, scope, transport, *, api_key, api_secret, **options):
        if scope.venue != "binance":
            raise ValueError("Binance scope required")
        super().__init__(scope, transport, **options)
        self.api_key, self.api_secret = _text(api_key, "API key"), _text(api_secret, "API secret")
        self.base = "https://demo-fapi.binance.com" if scope.environment == "sandbox" else "https://fapi.binance.com"

    async def _call(self, method, path, params=None, *, public=False, permit=None):
        body = dict(params or {})
        headers = {}
        if not public:
            body.update(timestamp=self.clock_ns() // 10**6, recvWindow=5000)
            body["signature"] = hmac.new(self.api_secret.encode(), urlencode(body).encode(), hashlib.sha256).hexdigest()
            headers["X-MBX-APIKEY"] = self.api_key
        response = await self._request(method, self.base + path, params=body, headers=headers, permit=permit)
        data = response.body
        if isinstance(data, dict) and isinstance(data.get("code"), int) and data["code"] < 0:
            if permit and data["code"] in {-1001, -1006, -1007}:
                raise OutcomeUnknown("Binance returned an indeterminate mutation result")
            raise VenueRejected("Binance rejected request code " + str(data["code"]))
        return data

    async def instrument_metadata(self, symbol):
        rows = (await self._call("GET", "/fapi/v1/exchangeInfo", public=True))["symbols"]
        row = next((r for r in rows if r["symbol"] == symbol), None)
        if not row or row.get("contractType") != "PERPETUAL" or row.get("status") != "TRADING":
            raise CapabilityBlocked("Instrument is not an active Binance USDⓈ-M perpetual")
        filters = {r["filterType"]: r for r in row["filters"]}
        return self._metadata_row(symbol, row, tick=filters["PRICE_FILTER"]["tickSize"],
            lot=filters["LOT_SIZE"]["stepSize"], minimum=filters["LOT_SIZE"]["minQty"],
            notional=filters["MIN_NOTIONAL"]["notional"], base=row["baseAsset"],
            quote=row["quoteAsset"], settlement=row["marginAsset"])

    async def account_state(self):
        row = await self._call("GET", "/fapi/v3/account")
        asset = next(r for r in row["assets"] if r["asset"] == self.scope.settlement_currency)
        return self._scope_row({"equity": str(_number(asset["marginBalance"], "equity")),
            "available": str(_number(asset["availableBalance"], "available")),
            "initial_margin": str(_number(asset["initialMargin"], "initial margin")),
            "maintenance_margin": str(_number(asset["maintMargin"], "maintenance margin")),
            "currency": self.scope.settlement_currency, "raw": row})

    async def positions(self, symbol):
        rows = await self._call("GET", "/fapi/v3/positionRisk", {"symbol": symbol})
        if any(r.get("positionSide") != "BOTH" or r.get("marginType", "cross") != "cross" for r in rows):
            raise CapabilityBlocked("Binance hedge/isolated position mode is outside net/cross contract")
        return [self._scope_row({"symbol": r["symbol"], "quantity": str(_number(r["positionAmt"], "position")),
                "entry_price": r["entryPrice"], "raw": r}) for r in rows]

    def _normalize_order(self, row):
        status = row["status"]
        return self._scope_row({"symbol": row["symbol"], "order_id": str(row["orderId"]),
            "client_order_id": row.get("clientOrderId"), "status": status,
            "terminal": status in {"FILLED", "CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH"},
            "cumulative_quantity": str(_number(row.get("executedQty", "0"), "executed quantity")), "raw": row})

    async def working_orders(self, symbol):
        return [self._normalize_order(r) for r in await self._call("GET", "/fapi/v1/openOrders", {"symbol": symbol})]

    async def submit_with_permit(self, order, permit):
        self._require_permit(permit)
        row = self._order(order)
        payload = {"symbol": row["symbol"], "side": row["side"].upper(), "positionSide": "BOTH",
            "type": "LIMIT", "quantity": row["quantity"], "price": row["limit_price"],
            "timeInForce": "GTX" if row["time_in_force"] == "POST_ONLY" else row["time_in_force"],
            "reduceOnly": str(row["reduce_only"]).lower(), "newClientOrderId": row["client_order_id"]}
        result = await self._call("POST", "/fapi/v1/order", payload, permit=permit)
        if not isinstance(result, dict) or not result.get("orderId"):
            raise OutcomeUnknown("Binance accepted response missing order identity")
        return self._normalize_order(result)

    async def cancel_with_permit(self, symbol, client_order_id, permit):
        self._require_permit(permit)
        self._client_id(client_order_id)
        row = await self._call("DELETE", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_order_id}, permit=permit)
        return self._scope_row({"acknowledged": True, "terminal_proven": False, "raw": row})

    async def order_status(self, symbol, client_order_id):
        self._client_id(client_order_id)
        try:
            return self._normalize_order(await self._call("GET", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_order_id}))
        except VenueRejected:
            # A retention-limited or not-found lookup is not proof of non-submission.
            return self._unknown_order(symbol, client_order_id)

    def _fill(self, row):
        return self._scope_row({"kind": "fill", "event_id": str(row["symbol"]) + ":" + str(row["id"]),
            "symbol": row["symbol"], "order_id": str(row["orderId"]), "side": row["side"].lower(),
            "quantity": str(_number(row["qty"], "fill quantity", positive=True)),
            "price": str(_number(row["price"], "fill price", positive=True)),
            "fee": str(_number(row["commission"], "fee")), "fee_currency": row["commissionAsset"],
            "exchange_ns": int(row["time"]) * 10**6, "is_maker": bool(row["maker"]), "raw": row})

    async def fills(self, symbol, *, start_ns, end_ns, cursor=None):
        identity = self._cursor("fills", symbol, start_ns, end_ns, cursor)
        params = {"symbol": symbol, "limit": 1000}
        if cursor:
            params["fromId"] = cursor["token"]
        else:
            if end_ns - start_ns > 7 * 86400 * 10**9:
                raise ValueError("Binance trade history window must be at most seven days")
            params.update(startTime=start_ns // 10**6, endTime=end_ns // 10**6)
        rows = await self._call("GET", "/fapi/v1/userTrades", params)
        selected = [r for r in rows if start_ns <= int(r["time"]) * 10**6 <= end_ns]
        token = str(int(rows[-1]["id"]) + 1) if len(rows) == 1000 and int(rows[-1]["time"]) * 10**6 <= end_ns else None
        return self._page([self._fill(r) for r in selected], identity, token, cursor)

    async def funding(self, symbol, *, start_ns, end_ns, cursor=None):
        identity = self._cursor("funding", symbol, start_ns, end_ns, cursor)
        page = int(cursor["token"]) if cursor else 1
        rows = await self._call("GET", "/fapi/v1/income", {"symbol": symbol, "incomeType": "FUNDING_FEE",
            "startTime": start_ns // 10**6, "endTime": end_ns // 10**6, "limit": 1000, "page": page})
        normalized = [self._scope_row({"kind": "funding", "event_id": "FUNDING_FEE:" + str(r["tranId"]),
            "symbol": r["symbol"], "amount": str(_number(r["income"], "funding cashflow")),
            "currency": r["asset"], "exchange_ns": int(r["time"]) * 10**6, "raw": r}) for r in rows]
        return self._page(normalized, identity, str(page + 1) if len(rows) == 1000 else None, cursor)

    def normalize_private_event(self, frame):
        if frame.get("e") != "ORDER_TRADE_UPDATE":
            return []
        row = frame["o"]
        if row.get("ps") != "BOTH":
            raise CapabilityBlocked("Private event has unsupported hedge position side")
        if row.get("x") == "TRADE" and Decimal(row["l"]) > 0:
            fill = self._fill({"symbol": row["s"], "id": row["t"], "orderId": row["i"], "side": row["S"],
                "qty": row["l"], "price": row["L"], "commission": row["n"], "commissionAsset": row["N"],
                "time": row["T"], "maker": row["m"]})
            fill["client_order_id"] = row["c"]
            return [fill]
        return [self._normalize_order({"symbol": row["s"], "orderId": row["i"], "clientOrderId": row["c"],
                                      "status": row["X"], "executedQty": row["z"]})]


class BybitPerpAdapter(_Adapter):
    """V5 linear perpetuals; explicit unified net/cross account boundary."""
    def __init__(self, scope, transport, *, api_key, api_secret, **options):
        if scope.venue != "bybit":
            raise ValueError("Bybit scope required")
        super().__init__(scope, transport, **options)
        self.api_key, self.api_secret = _text(api_key, "API key"), _text(api_secret, "API secret")
        self.base = "https://api-testnet.bybit.com" if scope.environment == "sandbox" else "https://api.bybit.com"

    async def _call(self, method, path, payload=None, *, public=False, permit=None):
        payload = dict(payload or {})
        body = _json(payload) if method == "POST" else None
        headers = {"Content-Type": "application/json"}
        if not public:
            timestamp, window = str(self.clock_ns() // 10**6), "5000"
            message = timestamp + self.api_key + window + (body if body is not None else urlencode(payload))
            headers.update({"X-BAPI-API-KEY": self.api_key, "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": window,
                "X-BAPI-SIGN": hmac.new(self.api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()})
        result = (await self._request(method, self.base + path, params=payload if method == "GET" else None,
                       data=body, headers=headers, permit=permit)).body
        if not isinstance(result, dict) or "retCode" not in result:
            if permit:
                raise OutcomeUnknown("Malformed Bybit mutation response")
            raise ValueError("Malformed Bybit response")
        if result["retCode"] != 0:
            if permit and result["retCode"] in {10000, 10016}:
                raise OutcomeUnknown("Bybit mutation returned timeout/server error")
            raise VenueRejected("Bybit rejected request code " + str(result["retCode"]))
        return result["result"]

    async def _rows(self, path, params):
        rows, cursor, seen = [], None, set()
        for _ in range(self.max_pages):
            result = await self._call("GET", path, {**params, **({"cursor": cursor} if cursor else {})})
            rows.extend(result["list"])
            cursor = result.get("nextPageCursor")
            if not cursor:
                return rows
            if cursor in seen:
                raise IncompleteHistory("Bybit pagination cycle")
            seen.add(cursor)
        raise IncompleteHistory("Bybit bounded pagination exhausted")

    async def instrument_metadata(self, symbol):
        rows = (await self._call("GET", "/v5/market/instruments-info", {"category": "linear", "symbol": symbol}, public=True))["list"]
        if len(rows) != 1 or rows[0]["symbol"] != symbol or rows[0].get("contractType") != "LinearPerpetual" or rows[0].get("status") != "Trading":
            raise CapabilityBlocked("Instrument is not an active Bybit linear perpetual")
        row = rows[0]
        return self._metadata_row(symbol, row, tick=row["priceFilter"]["tickSize"],
            lot=row["lotSizeFilter"]["qtyStep"], minimum=row["lotSizeFilter"]["minOrderQty"],
            notional=row["lotSizeFilter"]["minNotionalValue"], base=row["baseCoin"],
            quote=row["quoteCoin"], settlement=row["settleCoin"])

    async def account_state(self):
        rows = (await self._call("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": self.scope.settlement_currency}))["list"]
        if len(rows) != 1 or rows[0]["accountType"] != "UNIFIED":
            raise CapabilityBlocked("Bybit unified account required")
        row = rows[0]
        return self._scope_row({"equity": str(_number(row["totalEquity"], "equity")),
            "available": str(_number(row["totalAvailableBalance"], "available")),
            "initial_margin": str(_number(row["totalInitialMargin"], "initial margin")),
            "maintenance_margin": str(_number(row["totalMaintenanceMargin"], "maintenance margin")),
            "currency": "USD", "settlement_currency": self.scope.settlement_currency, "raw": row})

    async def positions(self, symbol):
        rows = await self._rows("/v5/position/list", {"category": "linear", "symbol": symbol})
        if any(int(r["positionIdx"]) != 0 for r in rows):
            raise CapabilityBlocked("Bybit hedge mode unsupported")
        return [self._scope_row({"symbol": r["symbol"],
            "quantity": str(_number(r["size"], "position") * (-1 if r["side"] == "Sell" else 1)),
            "entry_price": r.get("avgPrice"), "raw": r}) for r in rows]

    def _normalize_order(self, row):
        return self._scope_row({"symbol": row["symbol"], "order_id": row["orderId"],
            "client_order_id": row["orderLinkId"], "status": row["orderStatus"],
            "terminal": row["orderStatus"] in {"Filled", "Cancelled", "Rejected", "Deactivated", "PartiallyFilledCanceled"},
            "cumulative_quantity": str(_number(row.get("cumExecQty", "0"), "cumulative fill")), "raw": row})

    async def working_orders(self, symbol):
        return [self._normalize_order(r) for r in await self._rows("/v5/order/realtime", {"category": "linear", "symbol": symbol, "openOnly": 0})]

    async def submit_with_permit(self, order, permit):
        self._require_permit(permit)
        row = self._order(order)
        payload = {"category": "linear", "symbol": row["symbol"], "side": row["side"].title(),
            "orderType": "Limit", "qty": row["quantity"], "price": row["limit_price"],
            "timeInForce": "PostOnly" if row["time_in_force"] == "POST_ONLY" else row["time_in_force"],
            "reduceOnly": row["reduce_only"], "positionIdx": 0, "orderLinkId": row["client_order_id"]}
        result = await self._call("POST", "/v5/order/create", payload, permit=permit)
        if not result.get("orderId") or result.get("orderLinkId") != row["client_order_id"]:
            raise OutcomeUnknown("Bybit mutation ACK identity missing or different")
        return self._scope_row({"symbol": row["symbol"], "order_id": result["orderId"],
            "client_order_id": result["orderLinkId"], "status": "ACKNOWLEDGED", "terminal": False, "raw": result})

    async def cancel_with_permit(self, symbol, client_order_id, permit):
        self._require_permit(permit)
        self._client_id(client_order_id)
        row = await self._call("POST", "/v5/order/cancel", {"category": "linear", "symbol": symbol, "orderLinkId": client_order_id}, permit=permit)
        return self._scope_row({"acknowledged": True, "terminal_proven": False, "raw": row})

    async def order_status(self, symbol, client_order_id):
        self._client_id(client_order_id)
        query = {"category": "linear", "symbol": symbol, "orderLinkId": client_order_id}
        rows = await self._rows("/v5/order/realtime", query)
        if not rows:
            rows = await self._rows("/v5/order/history", query)
        return self._normalize_order(rows[0]) if rows else self._unknown_order(symbol, client_order_id)

    def _fill(self, row):
        if row.get("category", "linear") != "linear":
            raise CapabilityBlocked("Private execution category is not linear")
        return self._scope_row({"kind": "fill", "event_id": _text(row["execId"], "execId"),
            "symbol": row["symbol"], "order_id": row["orderId"], "client_order_id": row["orderLinkId"],
            "side": row["side"].lower(), "quantity": str(_number(row["execQty"], "fill quantity", positive=True)),
            "price": str(_number(row["execPrice"], "fill price", positive=True)),
            "fee": str(_number(row["execFee"], "fee")),
            "fee_currency": row.get("feeCurrency") or self.scope.settlement_currency,
            "exchange_ns": int(row["execTime"]) * 10**6, "is_maker": bool(row["isMaker"]), "raw": row})

    async def _history(self, stream, symbol, start_ns, end_ns, cursor):
        identity = self._cursor(stream, symbol, start_ns, end_ns, cursor)
        if end_ns - start_ns > 7 * 86400 * 10**9:
            raise ValueError("Bybit history window must be at most seven days")
        params = {"category": "linear", "startTime": start_ns // 10**6, "endTime": end_ns // 10**6,
                  "limit": 50 if stream == "funding" else 100}
        if stream == "fills":
            params["symbol"] = symbol
        else:
            params.update(accountType="UNIFIED", type="SETTLEMENT", currency=self.scope.settlement_currency)
        if cursor:
            params["cursor"] = cursor["token"]
        result = await self._call("GET", "/v5/execution/list" if stream == "fills" else "/v5/account/transaction-log", params)
        if stream == "fills":
            rows = [self._fill(r) for r in result["list"] if r.get("execType", "Trade") == "Trade"]
        else:
            rows = [self._scope_row({"kind": "funding", "event_id": r["id"], "symbol": r["symbol"],
                "amount": str(_number(r["funding"], "funding cashflow")), "currency": r["currency"],
                "exchange_ns": int(r["transactionTime"]) * 10**6, "raw": r})
                for r in result["list"] if r["symbol"] == symbol and r.get("category") == "linear"]
        return self._page(rows, identity, result.get("nextPageCursor") or None, cursor)

    async def fills(self, symbol, *, start_ns, end_ns, cursor=None):
        return await self._history("fills", symbol, start_ns, end_ns, cursor)

    async def funding(self, symbol, *, start_ns, end_ns, cursor=None):
        return await self._history("funding", symbol, start_ns, end_ns, cursor)

    def normalize_private_event(self, frame):
        topic = frame.get("topic", "").split(".")[0]
        if topic == "execution":
            return [self._fill(r) for r in frame["data"] if r.get("execType", "Trade") == "Trade"]
        if topic == "order":
            if any(r.get("category") != "linear" for r in frame["data"]):
                raise CapabilityBlocked("Private order category must be linear")
            return [self._normalize_order(r) for r in frame["data"]]
        return []


class GrvtPerpAdapter(_Adapter):
    """GRVT perpetual REST lifecycle; EIP-712 signing is explicitly composed."""
    def __init__(self, scope, transport, *, session_headers: dict, signer, **options):
        if scope.venue != "grvt":
            raise ValueError("GRVT scope required")
        super().__init__(scope, transport, **options)
        if not session_headers.get("Cookie") or not session_headers.get("X-Grvt-Account-Id"):
            raise CapabilityBlocked("Explicit GRVT authenticated session headers required")
        if not callable(getattr(signer, "prepare", None)):
            raise CapabilityBlocked("Explicit composed GRVT order signer required")
        self.headers, self.signer = dict(session_headers), signer
        domain = "testnet.grvt.io" if scope.environment == "sandbox" else "grvt.io"
        self.base, self.market = "https://trades." + domain + "/full/v1/", "https://market-data." + domain + "/full/v1/"

    def _client_id(self, value):
        native = _uint(value, 64, "GRVT client_order_id")
        if str(native) != value:
            raise ValueError("GRVT client ID must be canonical uint64 decimal text")
        return value

    async def _call(self, path, params=None, *, public=False, permit=None):
        payload = dict(params or {})
        if not public:
            payload = {"sub_account_id": self.scope.subaccount, **payload}
        result = (await self._request("POST", (self.market if public else self.base) + path,
                    json_body=payload, headers={} if public else self.headers, permit=permit)).body
        if not isinstance(result, dict) or "result" not in result:
            if permit:
                raise OutcomeUnknown("GRVT mutation response lacks authoritative result")
            raise VenueRejected("GRVT read rejected or malformed")
        return result

    async def instrument_metadata(self, symbol):
        row = (await self._call("instrument", {"instrument": symbol}, public=True))["result"]
        if row.get("instrument") != symbol or row.get("kind") != "PERPETUAL" or not symbol.endswith("_USDT_Perp"):
            raise CapabilityBlocked("GRVT instrument must explicitly be a USDT perpetual")
        if row.get("settlement_period") != "PERPETUAL" or "ORDERBOOK" not in row.get("venues", []):
            raise CapabilityBlocked("GRVT instrument must settle perpetually on ORDERBOOK")
        return self._metadata_row(symbol, row, tick=row["tick_size"], lot=row["min_size"], minimum=row["min_size"],
            notional=row.get("min_notional", "0"), base=row["base"], quote=row["quote"], settlement="USDT")

    async def account_state(self):
        row = (await self._call("account_summary"))["result"]
        return self._scope_row({"equity": str(_number(row["total_equity"], "equity")),
            "available": str(_number(row["available_balance"], "available")),
            "initial_margin": str(_number(row["initial_margin"], "initial margin")),
            "maintenance_margin": str(_number(row["maintenance_margin"], "maintenance margin")),
            "currency": self.scope.settlement_currency, "raw": row})

    async def positions(self, symbol):
        rows = (await self._call("positions"))["result"]
        return [self._scope_row({"symbol": r["instrument"], "quantity": str(_number(r["size"], "position")),
            "entry_price": r.get("entry_price"), "raw": r}) for r in rows if r["instrument"] == symbol]

    def _normalize_order(self, row):
        state = row["state"]
        size = row["legs"][0]["size"]
        traded = (state.get("traded_size") or ["0"])[0]
        return self._scope_row({"symbol": row["legs"][0]["instrument"], "order_id": row.get("order_id"),
            "client_order_id": row["metadata"]["client_order_id"], "status": state["status"],
            "terminal": state["status"] in {"FILLED", "REJECTED", "CANCELLED"},
            "cumulative_quantity": str(_number(traded, "cumulative fill")),
            "leaves_quantity": str(_number(size, "size") - _number(traded, "traded size")), "raw": row})

    async def working_orders(self, symbol):
        rows = (await self._call("open_orders", {"kind": ["PERPETUAL"]}))["result"]
        return [self._normalize_order(r) for r in rows if r["legs"][0]["instrument"] == symbol]

    async def submit_with_permit(self, order, permit):
        self._require_permit(permit)
        row = self._order(order)
        # The original owner's signed-attempt persistence owns nonce uniqueness.
        # This seam never regenerates a signature after a possible handoff.
        signed = self.signer.prepare(self.scope, row, deepcopy(self._metadata[row["symbol"]]), self.clock_ns())
        signature = signed["signature"]
        _uint(signature["nonce"], 32, "GRVT signature nonce")
        chain = 326 if self.scope.environment == "sandbox" else 325
        leg = signed["legs"][0]
        tif = {"GTC": "GOOD_TILL_TIME", "POST_ONLY": "GOOD_TILL_TIME", "IOC": "IMMEDIATE_OR_CANCEL", "FOK": "FILL_OR_KILL"}[row["time_in_force"]]
        if (str(signature["chain_id"]) != str(chain) or signed["sub_account_id"] != self.scope.subaccount
                or str(signed["metadata"]["client_order_id"]) != row["client_order_id"]
                or len(signed["legs"]) != 1 or leg["instrument"] != row["symbol"]
                or _number(leg["size"], "signed size") != Decimal(row["quantity"])
                or _number(leg["limit_price"], "signed price") != Decimal(row["limit_price"])
                or leg["is_buying_asset"] is not (row["side"] == "buy")
                or signed["is_market"] is not False or signed["reduce_only"] is not row["reduce_only"]
                or signed["post_only"] is not (row["time_in_force"] == "POST_ONLY")
                or signed["time_in_force"] != tif
                or not self.clock_ns() < int(signature["expiration"]) <= self.clock_ns() + 30 * 86400 * 10**9):
            raise CapabilityBlocked("GRVT signed identity/economics differs from submitted scope/order")
        result = (await self._call("create_order", {"order": signed}, permit=permit))["result"]
        if not isinstance(result, dict) or not result.get("order_id"):
            raise OutcomeUnknown("GRVT ACK missing broker order identity")
        return self._scope_row({"symbol": row["symbol"], "order_id": result["order_id"],
            "client_order_id": row["client_order_id"], "status": "ACKNOWLEDGED", "terminal": False, "raw": result})

    async def cancel_with_permit(self, symbol, client_order_id, permit):
        self._require_permit(permit)
        self._client_id(client_order_id)
        result = (await self._call("cancel_order", {"client_order_id": client_order_id,
                               "time_to_live_ms": "5000"}, permit=permit))["result"]
        return self._scope_row({"acknowledged": True, "terminal_proven": False, "raw": result})

    async def order_status(self, symbol, client_order_id):
        self._client_id(client_order_id)
        result = (await self._call("order", {"client_order_id": client_order_id}))["result"]
        if not result:
            return self._unknown_order(symbol, client_order_id)
        if result["legs"][0]["instrument"] != symbol:
            raise CapabilityBlocked("GRVT client ID belongs to another instrument")
        return self._normalize_order(result)

    def _fill(self, row):
        if str(row["sub_account_id"]) != self.scope.subaccount:
            raise CapabilityBlocked("GRVT fill has another subaccount")
        return self._scope_row({"kind": "fill", "event_id": str(row["order_id"]) + ":" + str(row["trade_id"]),
            "symbol": row["instrument"], "order_id": row["order_id"], "client_order_id": row["client_order_id"],
            "side": "buy" if row["is_buyer"] else "sell", "quantity": str(_number(row["size"], "fill quantity", positive=True)),
            "price": str(_number(row["price"], "fill price", positive=True)),
            "fee": str(_number(row["fee"], "fee") + _number(row.get("builder_fee", "0"), "builder fee")),
            "fee_currency": row["fee_currency"], "exchange_ns": int(row["event_time"]),
            "is_maker": not row["is_taker"], "raw": row})

    async def _history(self, stream, symbol, start_ns, end_ns, cursor):
        identity = self._cursor(stream, symbol, start_ns, end_ns, cursor)
        result = await self._call("fill_history" if stream == "fills" else "funding_payment_history",
            {"instrument": symbol, "start_time": str(start_ns), "end_time": str(end_ns), "limit": 500,
             "cursor": cursor["token"] if cursor else "", "kind": ["PERPETUAL"]})
        if stream == "fills":
            rows = [self._fill(r) for r in result["result"]]
        else:
            rows = []
            for row in result["result"]:
                if str(row["sub_account_id"]) != self.scope.subaccount or row["instrument"] != symbol:
                    raise CapabilityBlocked("GRVT funding scope differs")
                rows.append(self._scope_row({"kind": "funding", "event_id": symbol + ":" + str(row["tx_id"]) + ":" + str(row["event_time"]),
                    "symbol": symbol, "amount": str(-_number(row["amount"], "funding payment")),
                    "currency": row["currency"], "exchange_ns": int(row["event_time"]), "raw": row}))
        return self._page(rows, identity, result.get("next") or None, cursor)

    async def fills(self, symbol, *, start_ns, end_ns, cursor=None):
        return await self._history("fills", symbol, start_ns, end_ns, cursor)

    async def funding(self, symbol, *, start_ns, end_ns, cursor=None):
        return await self._history("funding", symbol, start_ns, end_ns, cursor)

    def normalize_private_event(self, frame):
        if frame.get("stream") not in {"v1.order", "v1.fill"}:
            return []
        row = frame["feed"]
        result = self._fill(row) if frame["stream"] == "v1.fill" else self._normalize_order(row)
        result["connection_sequence"] = frame.get("sequence_number")
        result["sequence_requires_connection_scope"] = True
        return [result]


def _address(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        raise ValueError("Explicit 0x-prefixed account/signer address required")
    return value.lower()


class ArcusSigningComposition:
    """Compose existing ArcusBrokerAdapter.prepare_order and its Ed25519 signer.

    intent_factory must map normalized TIF POST_ONLY/GTC to Arcus ALO/GTT and
    set reduce_only in intent metadata. The caller constructs the legacy adapter
    with explicit settings; this class never discovers credentials or accounts.
    """
    def __init__(self, adapter, intent_factory):
        self.adapter, self.intent_factory = adapter, intent_factory

    def _scope(self, scope, address):
        if (bool(self.adapter.use_testnet) != (scope.environment == "sandbox")
                or _address(self.adapter.address) != address
                or str(self.adapter.env.get("ARCUS_ACCOUNT_INDEX", "0")) != scope.subaccount):
            raise CapabilityBlocked("Composed Arcus signer differs from account/environment")

    def prepare(self, scope, address, order, instrument, now_ns):
        self._scope(scope, address)
        self.adapter.markets[order["symbol"]] = deepcopy(instrument["raw"])
        return self.adapter.prepare_order(self.intent_factory(deepcopy(order)), now_ns=now_ns)

    def cancel(self, scope, address, symbol, instrument, *, client_id, order_id, now_ns):
        self._scope(scope, address)
        if bool(client_id) == bool(order_id):
            raise ValueError("Arcus cancel requires exactly one of order ID/client ID")
        market_id = int(instrument["raw"]["marketId"])
        canonical = {"ad": address, "ai": int(scope.subaccount), "ct": now_ns,
                     "m": market_id, "op": 2, "v": 1}
        canonical["c" if client_id else "id"] = client_id or order_id
        payload = {"address": address, "accountIndex": int(scope.subaccount), "marketId": market_id,
            "kind": "clientId" if client_id else "orderId", "timestamp": now_ns,
            "clientId" if client_id else "orderId": client_id or order_id}
        signature = self.adapter._signer().sign(json.dumps(canonical, sort_keys=True,
            separators=(",", ":")).encode()).hex()
        return {"type": "post", "request": {"type": "cancelOrder", "payload": payload,
            "apiKey": self.adapter.api_key, "timestamp": str(now_ns), "signature": signature}}


class ArcusPerpAdapter(_Adapter):
    """Arcus perpetual WS mutation and public account-scoped REST recovery."""
    def __init__(self, scope, transport, *, account_address, signer, **options):
        if scope.venue != "arcus" or not scope.subaccount.isdigit() or not 0 <= int(scope.subaccount) <= 9:
            raise ValueError("Arcus scope requires explicit subaccount 0..9")
        super().__init__(scope, transport, **options)
        if not callable(getattr(transport, "rpc", None)):
            raise CapabilityBlocked("Arcus requires injected WS RPC transport with permit seam")
        if not callable(getattr(signer, "prepare", None)) or not callable(getattr(signer, "cancel", None)):
            raise CapabilityBlocked("Arcus existing Ed25519 signing composition required")
        self.address, self.signer = _address(account_address), signer
        host = "api.testnet.arcus.xyz" if scope.environment == "sandbox" else "api.arcus.xyz"
        self.base, self.ws_url = "https://" + host, "wss://" + host + "/v1/ws"

    def _client_id(self, value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", value):
            raise ValueError("Arcus client ID requires 1..36 case-preserved letters/numbers/_/-")
        return value

    async def _get(self, path, params=None, *, account=True):
        query = {"address": self.address, "accountIndex": int(self.scope.subaccount)} if account else {}
        return (await self._request("GET", self.base + path, params={**query, **(params or {})})).body

    def _account_identity(self, row):
        if "address" in row and _address(row["address"]) != self.address:
            raise CapabilityBlocked("Arcus response has another account address")
        if "accountIndex" in row and int(row["accountIndex"]) != int(self.scope.subaccount):
            raise CapabilityBlocked("Arcus response has another subaccount")

    async def instrument_metadata(self, symbol):
        rows = (await self._get("/v1/markets", {"market": symbol}, account=False))["markets"]
        row = next((r for r in rows if r["marketDisplayName"] == symbol), None)
        if not row or row["status"] != "ONLINE" or not symbol.endswith("-USD"):
            raise CapabilityBlocked("Active Arcus USD perpetual market required")
        # /v1/markets is explicitly the perpetual market namespace; the spot
        # collateral asset IDs belong to a different endpoint and are not reused.
        result = self._metadata_row(symbol, row, tick=row["tickSize"], lot=row["stepSize"],
            minimum=row["minOrderSize"], notional=row["minOrderNotional"], base=row["baseAsset"],
            quote=row["quoteAsset"], settlement="USDG", distinct_quote=True)
        result["valuation_conversion_required"] = result["quote_currency"] != result["settlement_currency"]
        return result

    async def account_state(self):
        row = await self._get("/v1/account")
        self._account_identity(row)
        return self._scope_row({"equity": str(_number(row["equity"], "equity")),
            "available": str(_number(row["freeCollateral"], "free collateral")),
            "currency": "USD", "settlement_currency": "USDG", "margin_state": row.get("marginState"),
            "initial_margin": None, "maintenance_margin": None,
            "margin_totals_proven": False, "account_sequence": row["sequenceNumber"], "raw": row})

    async def native_risk_snapshot(self, symbol, *, native_identity, local_ledger_version, max_age_ns):
        """Read a bounded, double-cut Arcus native USD quote risk snapshot.

        This is intentionally separate from legacy account_state(), whose wire
        shape remains unchanged. The REST endpoints are public; this evidence
        does not assert authentication/session permissions.
        """
        from .arcus_risk import decode_arcus_quote_proof
        if (not isinstance(native_identity, dict)
                or native_identity.get("native_account_id") != self.address
                or native_identity.get("scope_key") != self.scope.key
                or native_identity.get("source") != "authenticated_native_session"
                or not {"account_read", "trade"}.issubset(set(native_identity.get("permissions", ())))
                or not native_identity.get("authenticated_session_id")):
            raise CapabilityBlocked("Arcus snapshot requires independently verified authenticated session identity")
        if (type(local_ledger_version) is not int or local_ledger_version < 0
                or type(max_age_ns) is not int or not 0 < max_age_ns <= 60_000_000_000):
            raise CapabilityBlocked("Arcus snapshot requires explicit bounded local-version and freshness cuts")
        observed_ns = self.clock_ns()
        if type(observed_ns) is not int or observed_ns <= 0:
            raise CapabilityBlocked("Arcus snapshot clock is invalid")
        first = await self._get("/v1/account")
        self._account_identity(first)
        markets_doc = await self._get("/v1/markets", account=False)
        leverages_doc = await self._get("/v1/leverages")
        last = await self._get("/v1/account")
        self._account_identity(last)
        # The account endpoint is not an atomic snapshot with the other reads;
        # reject any observable sequence or economic-cut change.
        cut_fields = ("sequenceNumber", "equity", "freeCollateral", "marginState", "positions",
                      "netDepositsByAsset", "pendingDeposits", "pendingWithdrawals",
                      "borrowHeadroom", "loanBalance", "unsettledInterest", "pendingInterest",
                      "interestAccrued", "spotPositions")
        if any(((k in first) != (k in last)) or first.get(k) != last.get(k) for k in cut_fields):
            raise CapabilityBlocked("Arcus account economic cut changed during native risk reads")
        observed_end_ns = self.clock_ns()
        if (type(observed_end_ns) is not int or observed_end_ns < observed_ns
                or observed_end_ns - observed_ns > max_age_ns):
            raise CapabilityBlocked("Arcus snapshot read span is nonmonotone or exceeds freshness bound")
        markets = markets_doc.get("markets") if isinstance(markets_doc, dict) else None
        leverages = leverages_doc.get("leverages") if isinstance(leverages_doc, dict) else None
        proof = decode_arcus_quote_proof(first, markets, leverages, address=self.address,
                                         account_index=int(self.scope.subaccount), symbol=symbol,
                                         local_ledger_version=local_ledger_version)
        market = next(m for m in markets if m.get("marketDisplayName") == symbol)
        product = self._metadata_row(symbol, market, tick=market["tickSize"], lot=market["stepSize"],
            minimum=market["minOrderSize"], notional=market["minOrderNotional"],
            base=market["baseAsset"], quote="USD", settlement="USDG", distinct_quote=True)
        product["valuation_conversion_required"] = True
        product["margin_mode"] = proof["margin_mode"]
        product["position_mode"] = "net"
        normalized = self._scope_row({"equity": str(proof["equityUSD"]), "available": str(proof["availableUSD"]),
            "currency": "USD", "settlement_currency": "USDG", "initial_margin": str(proof["initialMarginUSD"]),
            "maintenance_margin": str(proof["maintenanceMarginUSD"]), "margin_state": first.get("marginState"),
            "account_sequence": first["sequenceNumber"], "margin_totals_proven": True, "raw": first})
        return {"account_facts": {"normalized": normalized, "observed_ns": observed_ns,
            "account_version": "arcus:" + str(first["sequenceNumber"]), "reconciliation_state": "RECONCILED",
            "unknown": False, "position_mode": "net", "margin_mode": proof["margin_mode"],
            "local_ledger_version": local_ledger_version,
            "native_identity": deepcopy(native_identity),
            "arcus_quote_proof": {"account": first, "markets": markets, "leverages": leverages,
                                  "decoded": proof}}, "product_specs": {symbol: product},
                "max_age_ns": max_age_ns}

    async def positions(self, symbol):
        rows = (await self._get("/v1/positions", {"market": symbol}))["positions"]
        result = []
        for row in rows.values():
            self._account_identity(row)
            if row["marketDisplayName"] != symbol:
                raise CapabilityBlocked("Arcus positions query returned another market")
            result.append(self._scope_row({"symbol": symbol, "quantity": str(_number(row["size"], "position")),
                "entry_price": row["averageEntryPrice"], "margin_mode": row["marginMode"],
                "initial_margin": row["marginUsed"], "raw": row}))
        return result

    def _normalize_order(self, row):
        self._account_identity(row)
        return self._scope_row({"symbol": row["marketDisplayName"], "order_id": row["orderId"],
            "client_order_id": row.get("clientId"), "status": row["status"],
            "terminal": row["status"] in {"FILLED", "CANCELED", "REJECTED", "LIQUIDATED", "ADL"},
            "cumulative_quantity": str(_number(row["originalSize"], "original size") - _number(row["remainingSize"], "remaining size")),
            "exchange_ns": int(row["updatedAt"]) * 1000, "account_sequence": row.get("sequenceNumber"), "raw": row})

    async def working_orders(self, symbol):
        rows = (await self._get("/v1/openOrders", {"market": symbol, "limit": 1000}))["orders"]
        if len(rows) >= 1000:
            raise IncompleteHistory("Arcus working-order response hit cap; full baseline unproven")
        if any(row["marketDisplayName"] != symbol for row in rows):
            raise CapabilityBlocked("Arcus working-order response has another market")
        return [self._normalize_order(r) for r in rows]

    async def _rpc(self, message, permit):
        frame = (await self._request("WS", self.ws_url, rpc_message=message, permit=permit)).body
        if not isinstance(frame, dict) or "status" not in frame:
            raise OutcomeUnknown("Malformed Arcus WS acknowledgement")
        try:
            status = int(frame["status"])
        except (ValueError, TypeError, OverflowError) as error:
            raise OutcomeUnknown("Malformed Arcus WS acknowledgement status") from error
        if status >= 500:
            raise OutcomeUnknown("Arcus WS server error after dispatch")
        if frame.get("error") or status >= 400:
            raise VenueRejected("Arcus rejected signed WS request")
        if not isinstance(frame.get("result"), dict):
            raise OutcomeUnknown("Arcus acknowledgement missing result")
        return frame["result"]

    async def submit_with_permit(self, order, permit):
        self._require_permit(permit)
        row = self._order(order)
        metadata = self._metadata[row["symbol"]]
        raw = metadata["raw"]
        price = Decimal(row["limit_price"])
        if Decimal(row["quantity"]) > _number(raw["maxOrderSize"], "maximum order size", positive=True):
            raise ValueError("Arcus order exceeds maximum size")
        for tier in raw.get("tickTiers", []):
            if "upToPrice" not in tier or price < Decimal(tier["upToPrice"]):
                if price % Decimal(tier["tick"]):
                    raise ValueError("Arcus limit price violates tier grid")
                break
        now = self.clock_ns()
        message = self.signer.prepare(self.scope, self.address, row, deepcopy(metadata), now)
        request, payload = message["request"], message["request"]["payload"]
        tif = {"GTC": "GTT", "POST_ONLY": "ALO", "IOC": "IOC", "FOK": "FOK"}[row["time_in_force"]]
        if (request["type"] != "placeOrder" or str(request["timestamp"]) != str(now)
                or int(payload.get("clientTime", payload.get("timestamp", 0))) != now
                or _address(payload["address"]) != self.address or int(payload["accountIndex"]) != int(self.scope.subaccount)
                or payload["clientId"] != row["client_order_id"] or payload["marketId"] != raw["marketId"]
                or payload["orderSide"] != row["side"].upper() or payload["orderType"] != "LIMIT"
                or payload["timeInForce"] != tif or payload["reduceOnly"] is not row["reduce_only"]
                or Decimal(payload["price"]) != price or Decimal(payload["quantity"]) != Decimal(row["quantity"])
                or int(payload["goodTilTime"]) * 1000 < now + 32 * 86400 * 10**9):
            raise CapabilityBlocked("Arcus signed request differs from order or expiry/time units")
        result = await self._rpc(message, permit)
        if not result.get("orderId"):
            raise OutcomeUnknown("Arcus ACK missing broker order identity")
        return self._scope_row({"symbol": row["symbol"], "order_id": result["orderId"],
            "client_order_id": row["client_order_id"], "status": "ACKNOWLEDGED", "terminal": False, "raw": result})

    async def cancel_with_permit(self, symbol, client_order_id, permit, *, order_id=None):
        self._require_permit(permit)
        if bool(client_order_id) == bool(order_id):
            raise ValueError("Arcus cancel requires exactly one of order ID/client ID")
        if client_order_id:
            self._client_id(client_order_id)
        else:
            _text(order_id, "order ID")
        if symbol not in self._metadata:
            raise CapabilityBlocked("Arcus cancel requires validated market ID")
        message = self.signer.cancel(self.scope, self.address, symbol, deepcopy(self._metadata[symbol]),
            client_id=client_order_id, order_id=order_id, now_ns=self.clock_ns())
        request, payload = message["request"], message["request"]["payload"]
        if (request["type"] != "cancelOrder" or _address(payload["address"]) != self.address
                or int(payload["accountIndex"]) != int(self.scope.subaccount)
                or payload["marketId"] != self._metadata[symbol]["raw"]["marketId"]
                or payload.get("kind") != ("clientId" if client_order_id else "orderId")
                or payload.get("clientId" if client_order_id else "orderId") != (client_order_id or order_id)):
            raise CapabilityBlocked("Arcus signed cancel differs from order/account scope")
        result = await self._rpc(message, permit)
        return self._scope_row({"acknowledged": True, "terminal_proven": False, "raw": result})

    async def order_status(self, symbol, client_order_id):
        self._client_id(client_order_id)
        upper = self.clock_ns() // 1000
        for _ in range(self.max_pages):
            rows = (await self._get("/v1/orders", {"market": symbol, "limit": 1000, "to": upper}))["orders"]
            matches = [r for r in rows if r.get("clientId") == client_order_id]
            if matches:
                if matches[0]["marketDisplayName"] != symbol:
                    raise CapabilityBlocked("Arcus order lookup returned another market")
                return self._normalize_order(matches[0])
            if len(rows) < 1000:
                return self._unknown_order(symbol, client_order_id)
            next_upper = min(int(r["updatedAt"]) for r in rows)
            if next_upper >= upper:
                raise IncompleteHistory("Arcus inclusive history bound cannot advance without loss")
            upper = next_upper  # Inclusive overlap: never subtract a microsecond and lose tied rows.
        raise IncompleteHistory("Arcus order recovery exhausted page budget")

    def _fill(self, row):
        self._account_identity(row)
        return self._scope_row({"kind": "fill", "event_id": str(row["orderId"]) + ":" + str(row["tradeId"]),
            "symbol": row["marketDisplayName"], "order_id": row["orderId"], "client_order_id": row.get("clientId"),
            "side": row["side"].lower(), "quantity": str(_number(row["size"], "fill quantity", positive=True)),
            "price": str(_number(row["price"], "fill price", positive=True)), "fee": str(_number(row["fee"], "fee")),
            "fee_currency": "USD", "exchange_ns": int(row["createdAt"]) * 1000,
            "is_maker": row["role"] == "MAKER", "account_sequence": row.get("sequenceNumber"), "raw": row})

    def _funding(self, row):
        self._account_identity(row)
        return self._scope_row({"kind": "funding", "event_id": str(row["marketId"]) + ":" + str(row["time"]),
            "symbol": row["marketDisplayName"], "amount": str(_number(row["payment"], "funding cashflow")),
            "currency": "USD", "exchange_ns": int(row["time"]) * 1000, "raw": row})

    async def _history(self, stream, symbol, start_ns, end_ns, cursor):
        identity = self._cursor(stream, symbol, start_ns, end_ns, cursor)
        if start_ns // 1000 < 10**14:
            raise ValueError("Arcus history requires epoch-microsecond bounds >=1e14")
        upper = int(cursor["token"]) if cursor else end_ns // 1000
        result = await self._get("/v1/fills" if stream == "fills" else "/v1/funding",
            {"market": symbol, "from": start_ns // 1000, "to": upper, "limit": 1000})
        rows = result["fills" if stream == "fills" else "fundingPayments"]
        field = "createdAt" if stream == "fills" else "time"
        token = str(min(int(r[field]) for r in rows)) if len(rows) == 1000 else None
        selected = [r for r in rows if start_ns <= int(r[field]) * 1000 <= end_ns]
        if any(r["marketDisplayName"] != symbol for r in selected):
            raise CapabilityBlocked("Arcus history returned another market")
        return self._page([(self._fill if stream == "fills" else self._funding)(r) for r in selected], identity, token, cursor)

    async def fills(self, symbol, *, start_ns, end_ns, cursor=None):
        return await self._history("fills", symbol, start_ns, end_ns, cursor)

    async def funding(self, symbol, *, start_ns, end_ns, cursor=None):
        return await self._history("funding", symbol, start_ns, end_ns, cursor)

    def normalize_private_event(self, frame):
        if frame.get("type") in {"gap", "degraded", "error"}:
            return [self.normalize_gap(str(frame.get("reason", frame["type"])), stream=frame.get("channel", "private"))]
        channel = frame.get("channel")
        if channel not in {"orders", "userFills", "funding", "account"}:
            return []
        if _address(frame.get("id")) != self.address or int(frame["accountIndex"]) != int(self.scope.subaccount):
            raise CapabilityBlocked("Arcus private frame belongs to another account/subaccount")
        contents = frame["contents"]
        snapshot = frame["type"] == "subscribed"
        if channel == "userFills":
            rows = contents.get("fills", []) if snapshot else [contents]
            result = [self._fill(r) for r in rows]
        elif channel == "orders":
            rows = contents.get("openOrders", []) + contents.get("recentClosedOrders", []) if snapshot else [contents]
            result = [self._normalize_order(r) for r in rows]
        elif channel == "funding":
            result = [self._funding(r) for r in contents.get("fundingPayments", [])] if snapshot else [self._funding(contents)]
        else:
            result = [self._scope_row({"kind": "account", "raw": contents})]
        for row in result:
            row["snapshot"] = snapshot
            row["snapshot_is_complete_history"] = False
        return result


class HyperliquidSdkSigningComposition:
    """Official SDK signer only. Injection supports offline reference vectors."""
    def __init__(self, wallet, *, sign_l1_action=None):
        self.wallet, self.signer_address = wallet, _address(wallet.address)
        if sign_l1_action is None:
            try:
                from hyperliquid.utils.signing import sign_l1_action
            except ImportError as error:
                raise CapabilityBlocked("Official hyperliquid-python-sdk signing dependency is missing") from error
        if not callable(sign_l1_action):
            raise CapabilityBlocked("Official sign_l1_action callable required")
        self.sign_l1_action = sign_l1_action

    def sign(self, action, *, vault_address, nonce, expires_after, is_mainnet):
        return self.sign_l1_action(self.wallet, action, vault_address, nonce, expires_after, is_mainnet)


class HyperliquidPerpAdapter(_Adapter):
    """Canonical first-dex USDC perpetuals. Spot and HIP-3 IDs are excluded."""
    def __init__(self, scope, transport, *, account_address, signer, nonce_source, vault_address=None, **options):
        if scope.venue != "hyperliquid":
            raise ValueError("Hyperliquid scope required")
        super().__init__(scope, transport, **options)
        self.address = _address(account_address)
        self.vault_address = _address(vault_address) if vault_address is not None else None
        if self.vault_address is not None and self.vault_address != self.address:
            raise ValueError("Vault/subaccount query address must equal the exchange vaultAddress")
        if not isinstance(signer, HyperliquidSdkSigningComposition):
            raise CapabilityBlocked("Hyperliquid requires official SDK signing composition")
        if getattr(nonce_source, "durable_nonce_protocol", None) != 1 or not callable(getattr(nonce_source, "reserve", None)):
            raise CapabilityBlocked("Hyperliquid requires a persisted signer-scoped nonce source")
        self.signer, self.nonce_source, self._last_nonce = signer, nonce_source, None
        self.base = "https://api.hyperliquid-testnet.xyz" if scope.environment == "sandbox" else "https://api.hyperliquid.xyz"

    def _client_id(self, value):
        if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{32}", value):
            raise ValueError("Hyperliquid cloid must be exactly 128-bit hex")
        if value != value.lower():
            raise ValueError("Hyperliquid cloid requires canonical lowercase hex")
        return value

    async def _info(self, kind, **params):
        return (await self._request("POST", self.base + "/info", json_body={"type": kind, **params})).body

    async def instrument_metadata(self, symbol):
        if not isinstance(symbol, str) or ":" in symbol or "@" in symbol or "/" in symbol:
            raise CapabilityBlocked("Only canonical first-dex perpetual names are supported")
        meta = await self._info("meta")
        selected = [(i, r) for i, r in enumerate(meta["universe"]) if r["name"] == symbol]
        if len(selected) != 1 or selected[0][1].get("isDelisted", False):
            raise CapabilityBlocked("Hyperliquid perpetual missing or delisted")
        index, row = selected[0]
        decimals = _uint(row["szDecimals"], 8, "size decimals")
        if decimals > 6:
            raise CapabilityBlocked("Unexpected Hyperliquid perpetual size precision")
        result = self._metadata_row(symbol, row, tick=Decimal(10) ** -(6 - decimals),
            lot=Decimal(10) ** -decimals, minimum=Decimal(10) ** -decimals, notional="10",
            base=symbol, quote="USDC", settlement="USDC")
        self._metadata[symbol]["asset_id"] = index
        result["asset_id"] = index
        result["price_significant_digits"] = 5
        return result

    async def account_state(self):
        row = await self._info("clearinghouseState", user=self.address)
        summary = row["marginSummary"]
        return self._scope_row({"equity": str(_number(summary["accountValue"], "equity")),
            "available": str(_number(row["withdrawable"], "withdrawable")),
            "initial_margin": str(_number(summary["totalMarginUsed"], "initial margin")),
            "maintenance_margin": str(_number(row["crossMaintenanceMarginUsed"], "maintenance margin")),
            "currency": "USDC", "account_address": self.address, "raw": row})

    async def positions(self, symbol):
        rows = (await self._info("clearinghouseState", user=self.address))["assetPositions"]
        return [self._scope_row({"symbol": p["position"]["coin"], "quantity": str(_number(p["position"]["szi"], "position")),
            "entry_price": p["position"].get("entryPx"), "margin_mode": p["position"]["leverage"]["type"], "raw": p})
            for p in rows if p["position"]["coin"] == symbol]

    def _normalize_order(self, row, status="OPEN"):
        return self._scope_row({"symbol": row["coin"], "order_id": str(row["oid"]),
            "client_order_id": row.get("cloid"), "status": status,
            "terminal": status not in {"open", "OPEN", "triggered"},
            "leaves_quantity": str(_number(row["sz"], "order leaves")), "raw": row})

    async def working_orders(self, symbol):
        rows = await self._info("openOrders", user=self.address)
        return [self._normalize_order(r) for r in rows if r["coin"] == symbol]

    async def _exchange(self, action, permit):
        self._require_permit(permit)
        now_ms = self.clock_ns() // 10**6
        # reserve() MUST commit before return; its key excludes account/subaccount
        # because all subaccounts signed by one API wallet share nonce state.
        signer_key = self.scope.environment + ":" + self.signer.signer_address
        nonce = self.nonce_source.reserve(signer_key, now_ms)
        if inspect.isawaitable(nonce):
            if inspect.iscoroutine(nonce):
                nonce.close()
            raise TypeError("Persisted nonce reserve must be synchronous")
        nonce = _uint(nonce, 64, "signer nonce")
        if not now_ms - 2 * 86400_000 < nonce < now_ms + 86400_000 or (self._last_nonce is not None and nonce <= self._last_nonce):
            raise CapabilityBlocked("Signer nonce source returned stale/reused/out-of-window nonce")
        self._last_nonce = nonce
        expires = now_ms + 10_000
        signature = self.signer.sign(deepcopy(action), vault_address=self.vault_address, nonce=nonce,
                                     expires_after=expires, is_mainnet=self.scope.environment == "live")
        if not isinstance(signature, dict) or not {"r", "s", "v"} <= signature.keys():
            raise CapabilityBlocked("Official SDK returned malformed signature")
        payload = {"action": action, "nonce": nonce, "signature": signature,
                   "vaultAddress": self.vault_address, "expiresAfter": expires}
        result = (await self._request("POST", self.base + "/exchange", json_body=payload, permit=permit)).body
        if not isinstance(result, dict) or "status" not in result:
            raise OutcomeUnknown("Malformed Hyperliquid exchange result")
        if result["status"] != "ok":
            raise VenueRejected("Hyperliquid rejected exchange action")
        try:
            statuses = result["response"]["data"]["statuses"]
        except (KeyError, TypeError) as error:
            raise OutcomeUnknown("Hyperliquid exchange ACK missing action status") from error
        if not isinstance(statuses, list) or len(statuses) != 1:
            raise OutcomeUnknown("Hyperliquid single-action ACK cardinality differs")
        if isinstance(statuses[0], dict) and "error" in statuses[0]:
            raise VenueRejected("Hyperliquid rejected order/cancel action")
        return statuses[0]

    async def submit_with_permit(self, order, permit):
        self._require_permit(permit)
        row = self._order(order)
        if row["time_in_force"] == "FOK":
            raise CapabilityBlocked("Hyperliquid FOK is unsupported")
        price = Decimal(row["limit_price"])
        if price != price.to_integral_value() and len(price.normalize().as_tuple().digits) > 5:
            raise ValueError("Hyperliquid noninteger price exceeds five significant digits")
        tif = {"GTC": "Gtc", "IOC": "Ioc", "POST_ONLY": "Alo"}[row["time_in_force"]]
        action = {"type": "order", "orders": [{"a": self._metadata[row["symbol"]]["asset_id"],
            "b": row["side"] == "buy", "p": row["limit_price"], "s": row["quantity"],
            "r": row["reduce_only"], "t": {"limit": {"tif": tif}}, "c": row["client_order_id"]}], "grouping": "na"}
        result = await self._exchange(action, permit)
        identity = result.get("resting", result.get("filled")) if isinstance(result, dict) else None
        if not identity or "oid" not in identity:
            raise OutcomeUnknown("Hyperliquid order ACK missing broker order identity")
        return self._scope_row({"symbol": row["symbol"], "order_id": str(identity["oid"]),
            "client_order_id": row["client_order_id"], "status": "ACKNOWLEDGED", "terminal": False, "raw": result})

    async def cancel_with_permit(self, symbol, client_order_id, permit):
        self._require_permit(permit)
        self._client_id(client_order_id)
        if symbol not in self._metadata:
            raise CapabilityBlocked("Hyperliquid cancel requires validated perpetual asset ID")
        result = await self._exchange({"type": "cancelByCloid", "cancels": [{"asset": self._metadata[symbol]["asset_id"], "cloid": client_order_id}]}, permit)
        if result != "success":
            raise OutcomeUnknown("Hyperliquid cancel ACK is not an explicit success")
        return self._scope_row({"acknowledged": True, "terminal_proven": False, "raw": result})

    async def order_status(self, symbol, client_order_id):
        self._client_id(client_order_id)
        result = await self._info("orderStatus", user=self.address, oid=client_order_id)
        if result.get("status") != "order":
            return self._unknown_order(symbol, client_order_id)
        row = result["order"]
        if row["order"]["coin"] != symbol or row["order"].get("cloid") != client_order_id:
            raise CapabilityBlocked("Hyperliquid order lookup identity differs")
        return self._normalize_order(row["order"], row["status"])

    def _fill(self, row):
        symbol = row["coin"]
        if symbol not in self._metadata:
            raise CapabilityBlocked("Hyperliquid fill requires validated perpetual metadata; Spot/HIP-3 excluded")
        return self._scope_row({"kind": "fill", "event_id": symbol + ":" + str(row["oid"]) + ":" + str(row["tid"]),
            "symbol": symbol, "order_id": str(row["oid"]), "side": {"B": "buy", "A": "sell"}[row["side"]],
            "quantity": str(_number(row["sz"], "fill quantity", positive=True)), "price": str(_number(row["px"], "fill price", positive=True)),
            "fee": str(_number(row["fee"], "total inclusive fee")), "fee_currency": row["feeToken"].strip(),
            "exchange_ns": int(row["time"]) * 10**6, "is_maker": not row["crossed"], "raw": row})

    def _funding(self, row):
        delta = row.get("delta", row)
        if delta.get("type", "funding") != "funding":
            raise ValueError("Hyperliquid funding response contains another ledger event type")
        return self._scope_row({"kind": "funding", "event_id": delta["coin"] + ":" + str(row["time"]),
            "symbol": delta["coin"], "amount": str(_number(delta["usdc"], "funding cashflow")),
            "currency": "USDC", "exchange_ns": int(row["time"]) * 10**6, "raw": row})

    async def _history(self, stream, symbol, start_ns, end_ns, cursor):
        identity = self._cursor(stream, symbol, start_ns, end_ns, cursor)
        lower = int(cursor["token"]) if cursor else start_ns // 10**6
        params = {"user": self.address, "startTime": lower, "endTime": end_ns // 10**6}
        if stream == "fills":
            params["aggregateByTime"] = False
        rows = await self._info("userFillsByTime" if stream == "fills" else "userFunding", **params)
        selected = [r for r in rows if (r["coin"] if stream == "fills" else r.get("delta", r)["coin"]) == symbol
                    and start_ns <= int(r["time"]) * 10**6 <= end_ns]
        capped = len(rows) >= (2000 if stream == "fills" else 500) or len({r["time"] for r in rows}) >= 500
        token = str(max(int(r["time"]) for r in rows)) if capped else None
        result = self._page([(self._fill if stream == "fills" else self._funding)(r) for r in selected], identity, token, cursor)
        result.update(history_complete=False, retention_limited=True, absence_resolves_unknown=False)
        return result

    async def fills(self, symbol, *, start_ns, end_ns, cursor=None):
        return await self._history("fills", symbol, start_ns, end_ns, cursor)

    async def funding(self, symbol, *, start_ns, end_ns, cursor=None):
        return await self._history("funding", symbol, start_ns, end_ns, cursor)

    def normalize_private_event(self, frame):
        channel = frame.get("channel")
        if channel in {"gap", "error", "disconnected"}:
            return [self.normalize_gap(str(frame.get("reason", channel)))]
        data = frame.get("data")
        if channel in {"userFills", "userFundings"}:
            if _address(data["user"]) != self.address:
                raise CapabilityBlocked("Hyperliquid private frame has signer/other account address")
            rows = data["fills" if channel == "userFills" else "fundings"]
            result = [(self._fill if channel == "userFills" else self._funding)(r) for r in rows]
            for row in result:
                row["snapshot"] = bool(data.get("isSnapshot", False))
                row["snapshot_is_complete_history"] = False
            return result
        if channel == "orderUpdates":
            return [self._normalize_order(r["order"], r["status"]) for r in data
                    if r["order"]["coin"] in self._metadata]
        return []


def make_perp_adapter(scope: PerpScope, *, transport, **options):
    capability = capability_for(scope)
    if not capability.request_mapping_complete:
        raise CapabilityBlocked("; ".join(capability.blockers))
    return {"binance": BinancePerpAdapter, "bybit": BybitPerpAdapter, "grvt": GrvtPerpAdapter,
            "arcus": ArcusPerpAdapter, "hyperliquid": HyperliquidPerpAdapter}[scope.venue](scope, transport, **options)
