from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from decimal import Decimal

import aiohttp
import websockets

from execution.bybit_execution import BybitCredentials, BybitOrderClient
from vibetrading.settings import get_settings

from .config import Config
from .domain import Instrument, Order

REST = "https://api-testnet.bybit.com"
PUBLIC_WS = "wss://stream-testnet.bybit.com/v5/public/linear"
PRIVATE_WS = "wss://stream-testnet.bybit.com/v5/private"
PREFIX = "ulm-"


class ExchangeError(RuntimeError):
    pass


class TestnetClient(BybitOrderClient):
    """Reuse signing/clock code; forbid endpoint changes and unarmed mutations."""
    __test__ = False

    def __init__(self, session: aiohttp.ClientSession, *, trading: bool = False) -> None:
        credentials = BybitCredentials.from_settings(get_settings(), testnet=True)
        super().__init__(credentials, session)
        self.trading = trading

    async def request(self, method: str, path: str, payload: dict | None = None, *, public: bool = False) -> dict:
        if not self.credentials.testnet or self.base != REST:
            raise ExchangeError("Only the fixed Bybit testnet endpoint is permitted")
        if method != "GET" and (not self.trading or path not in {"/v5/order/create", "/v5/order/cancel"}):
            raise ExchangeError("Exchange mutation is disabled for this command")
        try:
            return await super().request(method, path, payload, public=public)
        except RuntimeError as exc:
            raise ExchangeError(str(exc)) from None

    async def rows(self, path: str, payload: dict) -> list[dict]:
        result, cursor = [], None
        for _ in range(20):
            params = dict(payload)
            if cursor:
                params["cursor"] = cursor
            data = (await self.request("GET", path, params))["result"]
            result.extend(data.get("list", []))
            next_cursor = data.get("nextPageCursor")
            if not next_cursor:
                return result
            if next_cursor == cursor:
                raise ExchangeError("REST pagination did not advance")
            cursor = next_cursor
        raise ExchangeError("REST pagination exceeded the bounded page limit")

    async def open_orders(self, symbol: str | None = None) -> list[dict]:
        return await self.rows("/v5/order/realtime", {"category": "linear", **({"symbol": symbol} if symbol else {"settleCoin": "USDT"}), "openOnly": 0, "limit": 50})

    async def positions(self, symbol: str | None = None) -> list[dict]:
        return await self.rows("/v5/position/list", {"category": "linear", **({"symbol": symbol} if symbol else {"settleCoin": "USDT"}), "limit": 200})

    async def wallet(self) -> dict:
        rows = await self.rows("/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDT"})
        if len(rows) != 1:
            raise ExchangeError("Expected a unified account wallet")
        row = rows[0]
        # Portfolio/cross margin rates must be available; don't invent zeroes.
        required = ("totalEquity", "totalAvailableBalance", "accountMMRate", "accountIMRate")
        if any(row.get(k) in (None, "") for k in required):
            raise ExchangeError("Account must expose cross-margin equity and margin rates")
        return {k: Decimal(row[k]) for k in required}

    async def preflight(self, config: Config) -> tuple[dict, Instrument, dict]:
        await self.sync_clock()
        metadata, fees, ticker, positions, orders, wallet, permissions = await asyncio.gather(
            self.request("GET", "/v5/market/instruments-info", {"category": "linear", "symbol": config.symbol}, public=True),
            self.request("GET", "/v5/account/fee-rate", {"category": "linear", "symbol": config.symbol}),
            self.request("GET", "/v5/market/tickers", {"category": "linear", "symbol": config.symbol}, public=True),
            self.positions(), self.open_orders(), self.wallet(),
            self.request("GET", "/v5/user/query-api"),
        )
        instrument = Instrument.from_api(metadata["result"]["list"][0])
        fee = fees["result"]["list"][0]
        maker, taker = Decimal(fee["makerFeeRate"]), Decimal(fee["takerFeeRate"])
        market_ticker = ticker["result"]["list"][0]
        nonzero = [p for p in positions if Decimal(p.get("size") or "0") != 0]
        symbol_positions = await self.positions(config.symbol)
        hedge = any(int(p.get("positionIdx", 0)) != 0 for p in symbol_positions)
        p = permissions["result"]
        write_permission = (str(p.get("readOnly", "1")) == "0"
                            and "Order" in p.get("permissions", {}).get("ContractTrade", []))
        unit = instrument.lots(config.unit_qty)
        price = instrument.ticks(market_ticker["lastPrice"])
        reasons = []
        if nonzero:
            reasons.append("existing_account_positions")
        if orders:
            reasons.append("existing_symbol_orders")
        if hedge:
            reasons.append("one_way_position_mode_required")
        if not write_permission:
            reasons.append("contract_order_permission_missing")
        if not instrument.valid_order(price, unit):
            reasons.append("configured_unit_below_exchange_minimum")
        if instrument.qty(unit) * instrument.price(price) > config.unit_notional_usdt:
            reasons.append("configured_unit_exceeds_notional_cap")
        if wallet["totalAvailableBalance"] < config.min_available_usdt:
            reasons.append("insufficient_testnet_balance")
        if max(wallet["accountIMRate"], wallet["accountMMRate"]) >= config.max_margin_rate:
            reasons.append("margin_rate_limit")
        result = {"environment": "bybit-testnet", "symbol": config.symbol, "ready": not reasons,
                  "reasons": reasons, "tick_size": str(instrument.tick), "lot_size": str(instrument.lot),
                  "min_qty": str(instrument.min_qty), "min_notional": str(instrument.min_notional),
                  "maker_fee_rate": str(maker), "taker_fee_rate": str(taker),
                  "contract_order_permission": write_permission, "one_way_mode": not hedge,
                  "nonzero_position_count": len(nonzero), "open_order_count": len(orders),
                  "wallet": {k: str(v) for k, v in wallet.items()},
                  "clock_offset_ms": self.server_clock_offset_ms, "clock_rtt_ms": round(self.clock_rtt_ms, 3),
                  "evidence_mode": config.evidence_mode, "statistically_validated": False,
                  "policy_sha256": config.policy_digest(),
                  "orders_submitted": 0}
        return result, instrument, {"maker": maker, "taker": taker, "ticker": market_ticker, "wallet": wallet}

    async def create(self, symbol: str, instrument: Instrument, order: Order, *, ioc: bool = False) -> dict:
        payload = {"category": "linear", "symbol": symbol, "side": order.side,
                   "orderType": "Limit", "qty": str(instrument.qty(order.qty)),
                   "price": str(instrument.price(order.price)), "timeInForce": "IOC" if ioc else "PostOnly",
                   "positionIdx": 0, "orderLinkId": order.client_id,
                   "reduceOnly": order.role != "ENTRY"}
        if order.role == "ENTRY":
            payload.update({"stopLoss": str(instrument.price(order.stop)), "slTriggerBy": "MarkPrice",
                            "tpslMode": "Full", "slOrderType": "Market"})
        return await self.request("POST", "/v5/order/create", payload)

    async def cancel(self, symbol: str, order: Order) -> dict:
        return await self.request("POST", "/v5/order/cancel", {"category": "linear", "symbol": symbol,
                                                               "orderLinkId": order.client_id})

    async def connect_private(self):
        ws = await websockets.connect(PRIVATE_WS, ping_interval=None, open_timeout=15, close_timeout=5, max_queue=2048)
        expires = self.now_ms() + 10000
        signature = hmac.new(self.credentials.api_secret.encode(), f"GET/realtime{expires}".encode(), hashlib.sha256).hexdigest()
        try:
            await ws.send(json.dumps({"op": "auth", "args": [self.credentials.api_key, expires, signature]}))
            response = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if response.get("op") != "auth" or response.get("success") is not True:
                raise ExchangeError("Testnet private WebSocket authentication failed")
            await ws.send(json.dumps({"op": "subscribe", "args": ["order", "execution", "position"]}))
            response = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if response.get("op") != "subscribe" or response.get("success") is not True:
                raise ExchangeError("Testnet private WebSocket subscription failed")
            return ws
        except BaseException:
            await ws.close()
            raise

    async def connect_public(self, config: Config):
        ws = await websockets.connect(PUBLIC_WS, ping_interval=None, open_timeout=15, close_timeout=5, max_queue=2048)
        await ws.send(json.dumps({"op": "subscribe", "args": [f"orderbook.{config.depth}.{config.symbol}",
                                                               f"publicTrade.{config.symbol}", f"tickers.{config.symbol}"]}))
        return ws
