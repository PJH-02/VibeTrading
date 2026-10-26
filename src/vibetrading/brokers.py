from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiohttp

from vibetrading.execution import BrokerAdapter
from core.models import AssetClass, OrderIntent, Side
from vibetrading.providers.kis_client import KisAuthClient
from vibetrading.providers.toss import TossAuthClient
from vibetrading.settings import RuntimeSettings, get_settings


def _bool(value: str | None, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


class BinanceBrokerAdapter:
    name = "binance"

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or get_settings()
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

    async def connect(self) -> None:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Binance API key/secret are not configured")
        await self._signed_request("GET", "/api/v3/account", {})

    async def account_state(self) -> dict:
        payload = await self._signed_request("GET", "/api/v3/account", {})
        balances = payload.get("balances") or []
        nonzero = [
            {
                "asset": item.get("asset"),
                "free": item.get("free"),
                "locked": item.get("locked"),
            }
            for item in balances
            if Decimal(str(item.get("free", "0"))) != 0 or Decimal(str(item.get("locked", "0"))) != 0
        ]
        return {"provider": self.name, "testnet": self.use_testnet, "balances_count": len(nonzero), "can_trade": payload.get("canTrade")}

    async def submit_order(self, intent: OrderIntent) -> dict:
        if intent.instrument.asset_class not in {AssetClass.CRYPTO_SPOT}:
            raise RuntimeError(f"Binance adapter only supports crypto spot for now: {intent.instrument.asset_class}")
        if intent.quantity is None:
            raise RuntimeError("Binance spot order requires quantity")
        body = {
            "symbol": intent.instrument.raw_symbol.upper(),
            "side": "BUY" if intent.side is Side.BUY else "SELL",
            "type": "LIMIT" if intent.limit_price is not None else "MARKET",
            "quantity": format(intent.quantity, "f"),
            "newClientOrderId": intent.client_order_id[:32],
        }
        if intent.limit_price is not None:
            body["price"] = format(intent.limit_price, "f")
            body["timeInForce"] = "GTC"
        return await self._signed_request("POST", "/api/v3/order", body)

    async def order_status(self, client_order_id: str) -> dict:
        return await self._signed_request("GET", "/api/v3/order", {"origClientOrderId": client_order_id})

    async def _signed_request(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Binance API key/secret are not configured")
        payload = {key: value for key, value in params.items() if value not in (None, "")}
        payload["timestamp"] = int(time.time() * 1000)
        query = urlencode(payload)
        signature = hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        query = f"{query}&signature={signature}"
        headers = {"X-MBX-APIKEY": self.api_key}
        url = f"{self.base_url}{path}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            if method == "GET":
                response_ctx = session.get(f"{url}?{query}", headers=headers)
            else:
                response_ctx = session.post(f"{url}?{query}", headers=headers)
            async with response_ctx as response:
                data = await _safe_json(response)
                if response.status >= 400:
                    raise RuntimeError(f"Binance {path} failed status={response.status} code={data.get('code')} msg={data.get('msg')}")
                return data


class KiwoomBrokerAdapter:
    name = "kiwoom"

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or get_settings()
        provider = self.settings.providers["kiwoom"]
        self.app_key = provider.values.get("KIWOOM_APP_KEY")
        self.app_secret = provider.values.get("KIWOOM_APP_SECRET")
        self.use_mock = _bool(provider.values.get("KIWOOM_USE_MOCK"), default=True)
        self.base_url = "https://mockapi.kiwoom.com" if self.use_mock else "https://api.kiwoom.com"

    async def connect(self) -> None:
        await self._access_token()

    async def account_state(self) -> dict:
        body = await self._post_api(
            "/api/dostk/acnt",
            "kt00018",
            {
                "qry_tp": "1",
                "dmst_stex_tp": "KRX",
            },
        )
        rows = body.get("acnt_evlt_remn_indv_tot") or []
        return {
            "provider": self.name,
            "mock": self.use_mock,
            "positions_count": len(rows) if isinstance(rows, list) else 0,
            "return_code": body.get("return_code"),
        }

    async def submit_order(self, intent: OrderIntent) -> dict:
        if intent.instrument.asset_class not in {AssetClass.EQUITY, AssetClass.ETF}:
            raise RuntimeError(f"Kiwoom adapter only supports domestic stock/ETF orders for now: {intent.instrument.asset_class}")
        if intent.quantity is None:
            raise RuntimeError("Kiwoom order requires quantity")
        body = {
            "dmst_stex_tp": "KRX",
            "stk_cd": intent.instrument.raw_symbol.removeprefix("A").zfill(6),
            "ord_qty": format(intent.quantity, "f"),
            "trde_tp": "0" if intent.limit_price is not None else "3",
            "ord_uv": format(intent.limit_price, "f") if intent.limit_price is not None else "",
            "cond_uv": "",
        }
        api_id = "kt10000" if intent.side is Side.BUY else "kt10001"
        return await self._post_api("/api/dostk/ordr", api_id, body)

    async def order_status(self, client_order_id: str) -> dict:
        return {"provider": self.name, "status": "NOT_IMPLEMENTED", "client_order_id": client_order_id}

    async def _post_api(self, path: str, api_id: str, body: dict[str, Any]) -> dict[str, Any]:
        token = await self._access_token()
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": api_id,
            "cont-yn": "N",
            "next-key": "",
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(f"{self.base_url}{path}", json=body, headers=headers) as response:
                data = await _safe_json(response)
                if response.status >= 400 or data.get("return_code") not in (None, 0):
                    raise RuntimeError(f"Kiwoom {api_id} failed status={response.status} code={data.get('return_code')} msg={data.get('return_msg')}")
                return data

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


from vibetrading.kis_execution import KisCashBroker


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

