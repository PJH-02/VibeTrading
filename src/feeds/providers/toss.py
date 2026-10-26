from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiohttp

from runtime.bus import EventBus
from core.models import AssetClass, Bar1mEvent, BookUpdateType, InstrumentId, OrderBookEvent, OrderBookLevel, Side, TradeEvent, Venue, utc_now_ns
from runtime.settings import RuntimeSettings, get_settings


KST = timezone(timedelta(hours=9))
MINUTE_NS = 60 * 1_000_000_000
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TossSubscription:
    symbol: str
    market: str

    @property
    def ws_symbol(self) -> str:
        return self.symbol.zfill(6) if self.market == "kr" and self.symbol.isdigit() else self.symbol.upper()


def parse_toss_symbols(value: str | list[str] | None) -> list[TossSubscription]:
    if value is None:
        return []
    tokens = value.split(",") if isinstance(value, str) else value
    subscriptions: list[TossSubscription] = []
    for raw in tokens:
        token = str(raw or "").strip()
        if not token:
            continue
        market = ""
        symbol = token
        if ":" in token:
            prefix, symbol = [part.strip() for part in token.split(":", 1)]
            prefix = prefix.lower()
            if prefix not in {"kr", "us"}:
                raise ValueError(f"Unsupported Toss market prefix: {prefix}")
            market = prefix
        else:
            market = "kr" if token.isdigit() else "us"
        if market == "kr":
            symbol = symbol.upper().removeprefix("A")
            if not symbol.isdigit():
                raise ValueError(f"Toss KR symbol must be a 6 digit stock/ETF code: {symbol}")
            symbol = symbol.zfill(6)
        else:
            symbol = symbol.upper()
        subscriptions.append(TossSubscription(symbol=symbol, market=market))
    return subscriptions


def _iso_to_ns(value: str | None) -> int:
    if not value:
        return utc_now_ns()
    text = str(value).strip()
    if not text:
        return utc_now_ns()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return utc_now_ns()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return int(dt.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value in (None, ""):
        value = default
    return Decimal(str(value).replace(",", "").replace("+", "").strip())


def _market_from_topic(topic: str) -> tuple[str, str, str]:
    parts = topic.split(":")
    if len(parts) != 3:
        raise ValueError(f"Unexpected Toss topic: {topic}")
    return parts[0], parts[1].lower(), parts[2].upper()


class TossAuthClient:
    rest_url = "https://openapi.tossinvest.com"
    ws_url = "wss://openapi-ws.tossinvest.com/ws/v1"

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or get_settings()
        env = self.settings.env
        self.client_id = env.get("TOSS_CLIENT_ID") or env.get("TOSS_API_KEY")
        self.client_secret = env.get("TOSS_CLIENT_SECRET") or env.get("TOSS_API_SECRET")
        self.account_seq = env.get("TOSS_ACCOUNT_SEQ")
        self._token_lock = asyncio.Lock()

    @property
    def has_credentials(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def auth_check(self, *, include_accounts: bool = True) -> dict[str, Any]:
        if not self.has_credentials:
            return {"provider": "toss", "status": "SKIPPED_MISSING_CREDENTIALS"}
        token = await self.access_token()
        result: dict[str, Any] = {
            "provider": "toss",
            "status": "ok",
            "token": {"ok": bool(token)},
        }
        if include_accounts:
            accounts = await self.accounts()
            result["accounts"] = {"ok": True, "count": len(accounts), "account_seq_configured": bool(self.account_seq)}
        return result

    async def accounts(self) -> list[dict[str, Any]]:
        cached = self._read_accounts_cache()
        if cached is not None:
            return cached
        try:
            payload = await self.request("GET", "/api/v1/accounts")
        except Exception:
            stale = self._read_accounts_cache(max_age_seconds=86_400)
            if stale is not None:
                return stale
            raise
        result = payload.get("result") or []
        accounts = result if isinstance(result, list) else []
        sanitized = [
            {
                "accountSeq": item.get("accountSeq"),
                "accountType": item.get("accountType"),
            }
            for item in accounts
            if isinstance(item, dict)
        ]
        self._write_accounts_cache(sanitized)
        return sanitized

    async def selected_account_seq(self) -> str:
        if self.account_seq:
            return str(self.account_seq)
        accounts = await self.accounts()
        if not accounts:
            raise RuntimeError("Toss account list is empty; cannot choose X-Tossinvest-Account")
        return str(accounts[0].get("accountSeq"))

    async def account_state_summary(self) -> dict[str, Any]:
        accounts = await self.accounts()
        if not accounts:
            return {"provider": "toss", "account_count": 0, "holdings_count": 0}
        account_seq = await self.selected_account_seq()
        try:
            holdings = await self.request("GET", "/api/v1/holdings", account_seq=account_seq)
            items = ((holdings.get("result") or {}).get("items") or []) if isinstance(holdings.get("result"), dict) else []
            holdings_count: int | None = len(items) if isinstance(items, list) else 0
            holdings_status = "ok"
        except Exception as exc:
            holdings_count = None
            holdings_status = f"unavailable: {exc}"
        return {
            "provider": "toss",
            "account_count": len(accounts),
            "selected_account_seq": account_seq,
            "holdings_count": holdings_count,
            "holdings_status": holdings_status,
        }

    async def access_token(self) -> str:
        async with self._token_lock:
            return await self._access_token_locked()

    async def refresh_rejected_token(self, rejected: str) -> str:
        async with self._token_lock:
            cached = self._read_token_cache()
            if cached and cached != rejected:
                return cached
            self._cache_path().unlink(missing_ok=True)
            return await self._access_token_locked()

    async def _access_token_locked(self) -> str:
        cached = self._read_token_cache()
        if cached:
            return cached
        if not self.client_id or not self.client_secret:
            raise RuntimeError("TOSS_API_KEY/TOSS_API_SECRET are not configured")
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.rest_url}/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as response:
                data = await _safe_json(response)
                if response.status >= 400:
                    raise RuntimeError(
                        f"Toss token failed status={response.status} error={data.get('error')} "
                        f"description={data.get('error_description')}"
                    )
                token = data.get("access_token")
                if not token:
                    raise RuntimeError("Toss token response missing access_token")
                expires_in = int(data.get("expires_in") or 86_400)
                self._write_token_cache(str(token), int(time.time()) + max(60, expires_in - 300))
                return str(token)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        account_seq: str | None = None,
    ) -> dict[str, Any]:
        # Early GET-only mutation gate for TOSS DATA_ONLY policy.  A
        # non-string method cannot be proven to be a read, so it is rejected
        # here before authentication or any transport access.
        normalized_method = method.upper() if isinstance(method, str) else None
        if normalized_method != "GET":
            raise RuntimeError("TOSS DATA_ONLY: request rejected - mutations not permitted")
        token = await self.access_token()
        for attempt in range(2):
            try:
                return await self._request_once(method, path, token=token, params=params,
                                                json_body=json_body, account_seq=account_seq)
            except TossUnauthorized:
                # Read requests can safely be repeated once. Do not replay
                # order POSTs or mask a rejected fresh credential.
                if attempt or method.upper() != "GET":
                    raise
                token = await self.refresh_rejected_token(token)
        raise AssertionError("unreachable")

    async def _request_once(self, method: str, path: str, *, token: str,
                            params: dict[str, Any] | None, json_body: dict[str, Any] | None,
                            account_seq: str | None) -> dict[str, Any]:
        # Early GET-only mutation gate for TOSS DATA_ONLY policy.  Runs
        # before headers or a ClientSession exist so a blocked method can
        # never touch the wire or leak a token.
        normalized_method = method.upper() if isinstance(method, str) else None
        if normalized_method != "GET":
            raise RuntimeError("TOSS DATA_ONLY: _request_once rejected - mutations not permitted")
        headers = {"Authorization": f"Bearer {token}"}
        if account_seq is not None:
            headers["X-Tossinvest-Account"] = str(account_seq)
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            request = session.get  # The validated DATA_ONLY transport is always a read.
            async with request(f"{self.rest_url}{path}", params=params, json=json_body, headers=headers) as response:
                data = await _safe_json(response)
                if response.status >= 400 or data.get("error"):
                    error = data.get("error") if isinstance(data.get("error"), dict) else data
                    error_type = TossUnauthorized if response.status == 401 else RuntimeError
                    raise error_type(
                        f"Toss {path} failed status={response.status} "
                        f"code={error.get('code') or error.get('error')} "
                        f"msg={error.get('message') or error.get('error_description')}"
                    )
                return data

    def _cache_path(self) -> Path:
        return self.settings.state_dir / "toss_access_token.json"

    def _read_token_cache(self) -> str | None:
        path = self._cache_path()
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if int(payload.get("expires_epoch") or 0) <= int(time.time()) + 60:
                return None
            token = payload.get("access_token")
            return str(token) if token else None
        except Exception:
            return None

    def _write_token_cache(self, token: str, expires_epoch: int) -> None:
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"access_token": token, "expires_epoch": expires_epoch}), encoding="utf-8")
        tmp.replace(path)

    def _accounts_cache_path(self) -> Path:
        return self.settings.state_dir / "toss_accounts.json"

    def _read_accounts_cache(self, *, max_age_seconds: int = 300) -> list[dict[str, Any]] | None:
        path = self._accounts_cache_path()
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if int(time.time()) - int(payload.get("saved_epoch") or 0) > max_age_seconds:
                return None
            accounts = payload.get("accounts")
            return accounts if isinstance(accounts, list) else None
        except Exception:
            return None

    def _write_accounts_cache(self, accounts: list[dict[str, Any]]) -> None:
        path = self._accounts_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"saved_epoch": int(time.time()), "accounts": accounts}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


class TossUnauthorized(RuntimeError):
    """The server rejected an access token, regardless of cache expiration."""


class TossCollector:
    name = "toss"
    keepalive_interval_sec = 60.0
    candle_poll_interval_sec = 60.0

    def __init__(self, subscriptions: list[TossSubscription] | None = None, *, publish_rest_snapshot: bool = True) -> None:
        self.subscriptions = subscriptions or []
        self.publish_rest_snapshot = publish_rest_snapshot
        self.client = TossAuthClient()
        self._published_bars: set[str] = set()

    async def run(self, bus: EventBus, duration_sec: float | None = None) -> None:
        if not self.subscriptions:
            raise RuntimeError("Toss collector needs --symbols, e.g. 005930,000660,AAPL")
        if not self.client.has_credentials:
            raise RuntimeError("TOSS_API_KEY/TOSS_API_SECRET are not configured")
        if self.publish_rest_snapshot:
            await self._publish_rest_snapshot(bus)
        token = await self.client.access_token()
        try:
            await self._run_websocket(bus, token, duration_sec)
        except aiohttp.WSServerHandshakeError as exc:
            if exc.status != 401:
                raise
            token = await self.client.refresh_rejected_token(token)
            await self._run_websocket(bus, token, duration_sec)

    async def _run_websocket(self, bus: EventBus, token: str, duration_sec: float | None) -> None:
        deadline = asyncio.get_running_loop().time() + duration_sec if duration_sec else None
        declarations = self._ws_declarations()
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(self.client.ws_url, headers={"Authorization": f"Bearer {token}"}, heartbeat=20) as ws:
                await ws.send_json(declarations)
                keepalive_task = asyncio.create_task(self._keepalive(ws), name="toss-ws-keepalive")
                candle_task = (asyncio.create_task(self._poll_us_candles(bus), name="toss-us-candles")
                               if any(item.market == "us" for item in self.subscriptions) else None)
                try:
                    while True:
                        if candle_task is not None and candle_task.done():
                            await candle_task
                        if deadline and asyncio.get_running_loop().time() >= deadline:
                            break
                        receive_timeout = min(1.0, max(0.05, deadline - asyncio.get_running_loop().time())) if deadline else 1.0
                        try:
                            msg = await ws.receive(timeout=receive_timeout)
                        except asyncio.TimeoutError:
                            continue
                        if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
                            raise RuntimeError(f"Toss websocket closed code={ws.close_code}")
                        if msg.type == aiohttp.WSMsgType.ERROR:
                            raise ws.exception() or RuntimeError("Toss websocket failed")
                        if msg.type not in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                            continue
                        received_ns=time.time_ns(); received_monotonic_ns=time.monotonic_ns()
                        text = msg.data.decode() if isinstance(msg.data, bytes) else msg.data
                        for event in self.parse_ws_frame(text, receive_ts_ns=received_ns, receive_monotonic_ns=received_monotonic_ns):
                            await bus.publish_market_event(event)
                finally:
                    keepalive_task.cancel()
                    if candle_task is not None:
                        candle_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await keepalive_task
                    if candle_task is not None:
                        with suppress(asyncio.CancelledError):
                            await candle_task

    async def _poll_us_candles(self, bus: EventBus) -> None:
        symbols = sorted({item.ws_symbol for item in self.subscriptions if item.market == "us"})
        failures = 0
        while True:
            try:
                for symbol in symbols:
                    payload = await self.client.request("GET", "/api/v1/candles", params={"symbol": symbol, "interval": "1m"})
                    rows = (payload.get("result") or {}).get("candles") or []
                    received_ns = time.time_ns()
                    monotonic_ns = time.monotonic_ns()
                    for row in reversed(rows):
                        event = self.candle_event_from_payload(symbol, row, receive_ts_ns=received_ns,
                                                               receive_monotonic_ns=monotonic_ns)
                        if event is None or event.event_id in self._published_bars:
                            continue
                        await bus.publish_market_event(event)
                        self._published_bars.add(event.event_id)
                    if len(self._published_bars) > 5_000:
                        self._published_bars.clear()
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                from market.market_policy import CollectionPaused
                if isinstance(exc, CollectionPaused):
                    raise
                failures += 1
                logger.exception("Toss US one-minute candle poll failed failures=%d", failures)
                if failures >= 3:
                    raise
            await asyncio.sleep(self.candle_poll_interval_sec)

    def candle_event_from_payload(self, symbol: str, row: dict[str, Any], *,
                                  receive_ts_ns: int | None = None,
                                  receive_monotonic_ns: int | None = None) -> Bar1mEvent | None:
        timestamp = row.get("timestamp")
        if not timestamp:
            raise ValueError("Toss candle missing timestamp")
        open_ns = _iso_to_ns(str(timestamp))
        if open_ns % MINUTE_NS:
            raise ValueError(f"Toss candle timestamp is not minute aligned: {timestamp}")
        received_ns = receive_ts_ns or time.time_ns()
        close_ns = open_ns + MINUTE_NS
        # The latest REST candle can still be changing.  Publish only after it closes.
        if close_ns + 2 * 1_000_000_000 > received_ns:
            return None
        # An offline weekend must not replay stale bars as current market data.
        if received_ns - close_ns > 24 * 60 * MINUTE_NS:
            return None
        return Bar1mEvent(
            instrument=self.instrument(symbol, "us", currency=row.get("currency")),
            venue=Venue.TOSS, provider=self.name, source_ts_ns=close_ns,
            source_timestamp_kind="provider_candle_close",
            receive_ts_ns=received_ns, wallclock_ts_ns=received_ns,
            receive_monotonic_ns=receive_monotonic_ns,
            ingest_mode="realtime" if received_ns-close_ns <= 120 * 1_000_000_000 else "backfill",
            session="toss_us", sequence=open_ns, native_event_id=f"bar1m:{symbol}:{open_ns}",
            open_ts_ns=open_ns, close_ts_ns=close_ns,
            open=_decimal(row.get("openPrice")), high=_decimal(row.get("highPrice")),
            low=_decimal(row.get("lowPrice")), close=_decimal(row.get("closePrice")),
            volume=_decimal(row.get("volume")),
        )

    async def _keepalive(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Keep the Toss session alive with its required application-level PING."""
        while True:
            await asyncio.sleep(self.keepalive_interval_sec)
            if ws.closed:
                return
            await ws.send_str("PING")

    def _ws_declarations(self) -> list[dict[str, Any]]:
        by_market: dict[str, list[str]] = {"kr": [], "us": []}
        for subscription in self.subscriptions:
            by_market[subscription.market].append(subscription.ws_symbol)
        declarations: list[dict[str, Any]] = [{"id": f"vibetrading-{int(time.time())}"}]
        for market, symbols in by_market.items():
            unique = sorted(set(symbols))
            if not unique:
                continue
            declarations.append({"type": f"trade:{market}", "codes": unique})
            declarations.append({"type": f"orderbook:{market}", "codes": unique})
        return declarations

    async def _publish_rest_snapshot(self, bus: EventBus) -> None:
        for subscription in self.subscriptions:
            symbol = subscription.ws_symbol
            try:
                orderbook = await self.client.request("GET", "/api/v1/orderbook", params={"symbol": symbol})
                await bus.publish_market_event(self.orderbook_event_from_payload(symbol, subscription.market, orderbook.get("result") or {}, sequence="rest"))
            except Exception:
                # The websocket path remains useful even when a single REST snapshot endpoint is unavailable.
                pass
            try:
                trades = await self.client.request("GET", "/api/v1/trades", params={"symbol": symbol, "count": 5})
                for index, row in enumerate(reversed(trades.get("result") or [])):
                    await bus.publish_market_event(self.trade_event_from_payload(symbol, subscription.market, row, sequence=f"rest-{index}"))
            except Exception:
                pass

    def parse_ws_frame(self, raw: str | bytes, *, receive_ts_ns: int | None = None, receive_monotonic_ns: int | None = None) -> list[TradeEvent | OrderBookEvent]:
        payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        if isinstance(payload, list):
            return []
        if payload.get("type") in {"subscriptions", "ack"}:
            rejected = payload.get("rejected") or []
            if not rejected and isinstance(payload.get("data"), dict):
                rejected = payload["data"].get("rejected") or []
            if rejected:
                raise RuntimeError(f"Toss websocket rejected subscriptions: {rejected}")
            return []
        if payload.get("type") == "error":
            raise RuntimeError(f"Toss websocket error: {payload}")
        if payload.get("type") != "message":
            return []
        topic = str(payload.get("topic") or "")
        kind, market, symbol = _market_from_topic(topic)
        data = payload.get("data") or {}
        if kind == "trade":
            return [self.trade_event_from_payload(symbol, market, data, sequence=topic, receive_ts_ns=receive_ts_ns, receive_monotonic_ns=receive_monotonic_ns)]
        if kind == "orderbook":
            return [self.orderbook_event_from_payload(symbol, market, data, sequence=topic, receive_ts_ns=receive_ts_ns, receive_monotonic_ns=receive_monotonic_ns)]
        return []

    def instrument(self, symbol: str, market: str, *, currency: str | None = None) -> InstrumentId:
        raw_symbol = symbol.upper()
        if market == "kr":
            raw_symbol = raw_symbol.removeprefix("A")
        return InstrumentId(
            venue=Venue.TOSS,
            asset_class=AssetClass.EQUITY,
            raw_symbol=raw_symbol,
            std_symbol=f"TOSS:{market.upper()}:{raw_symbol}",
            currency=currency or ("KRW" if market == "kr" else "USD"),
            exchange=market.upper(),
        )

    def trade_event_from_payload(self, symbol: str, market: str, data: dict[str, Any], *, sequence: str | int | None = None, receive_ts_ns: int | None = None, receive_monotonic_ns: int | None = None) -> TradeEvent:
        source_ts_ns = _iso_to_ns(data.get("timestamp"))
        return TradeEvent(
            instrument=self.instrument(symbol, market, currency=data.get("currency")),
            venue=Venue.TOSS,
            provider=self.name,
            source_ts_ns=source_ts_ns,
            receive_ts_ns=receive_ts_ns or utc_now_ns(),
            wallclock_ts_ns=receive_ts_ns,
            receive_monotonic_ns=receive_monotonic_ns,
            session=f"toss_{market}",
            sequence=sequence or data.get("timestamp"),
            price=_decimal(data.get("price")),
            quantity=abs(_decimal(data.get("volume"))),
            aggressor_side=Side.UNKNOWN,
            trade_id=f"{symbol}:{data.get('timestamp')}:{data.get('price')}:{data.get('volume')}",
        )

    def orderbook_event_from_payload(self, symbol: str, market: str, data: dict[str, Any], *, sequence: str | int | None = None, receive_ts_ns: int | None = None, receive_monotonic_ns: int | None = None) -> OrderBookEvent:
        bids = [
            OrderBookLevel(price=_decimal(level.get("price")), quantity=abs(_decimal(level.get("volume"))))
            for level in (data.get("bids") or [])
        ]
        asks = [
            OrderBookLevel(price=_decimal(level.get("price")), quantity=abs(_decimal(level.get("volume"))))
            for level in (data.get("asks") or [])
        ]
        source_ts_ns = _iso_to_ns(data.get("timestamp"))
        return OrderBookEvent(
            instrument=self.instrument(symbol, market, currency=data.get("currency")),
            venue=Venue.TOSS,
            provider=self.name,
            source_ts_ns=source_ts_ns,
            receive_ts_ns=receive_ts_ns or utc_now_ns(),
            wallclock_ts_ns=receive_ts_ns,
            receive_monotonic_ns=receive_monotonic_ns,
            session=f"toss_{market}",
            sequence=sequence or data.get("timestamp"),
            update_type=BookUpdateType.SNAPSHOT,
            depth=max(len(bids), len(asks)),
            bids=bids,
            asks=asks,
            is_full_depth=bool(data.get("isFullDepth", False)),
            provider_depth=max(len(bids), len(asks)),
            channel=f"orderbook:{market}",
        )


async def _safe_json(response: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        data = await response.json(content_type=None)
        return data if isinstance(data, dict) else {"payload": data}
    except Exception:
        text = await response.text()
        return {"text": text[:500]}
