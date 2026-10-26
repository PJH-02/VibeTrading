from __future__ import annotations

import asyncio
import json
import logging
import hashlib
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import aiohttp

from vibetrading.bus import EventBus
from vibetrading.models import (
    AssetClass,
    Bar1mEvent,
    EtfNavEvent,
    BookUpdateType,
    InstrumentId,
    OrderBookEvent,
    OrderBookLevel,
    Side,
    TradeEvent,
    Venue,
    utc_now_ns,
)
from vibetrading.settings import RuntimeSettings, get_settings
from vibetrading.universe_config import configured_instrument


KST = timezone(timedelta(hours=9))
TOKEN_EXPIRY_SKEW_SECONDS = 300
REALTIME_SYMBOL_LIMIT = 200


def _raise_websocket_closed(ws) -> None:
    code = ws.close_code
    # Preserve protocol/auth policy rejections as permanent failures.
    if code in {1002,1003,1007,1008,1009,1010}:
        raise RuntimeError(f'Kiwoom websocket policy/protocol rejection code={code}')
    error = ws.exception()
    if error is not None:
        raise error
    raise ConnectionResetError(f'Kiwoom websocket disconnected code={code}; reconnect required')


@dataclass(frozen=True)
class KiwoomSubscription:
    symbol: str
    market: str = "KRX"

    @property
    def rest_symbol(self) -> str:
        return f"{self.symbol}_NX" if self.market == "NXT" else self.symbol

    @property
    def ws_symbol(self) -> str:
        return self.rest_symbol


def parse_kiwoom_symbols(value: str | list[str] | None) -> list[KiwoomSubscription]:
    if value is None:
        return []
    tokens = value.split(",") if isinstance(value, str) else value
    subscriptions: list[KiwoomSubscription] = []
    for raw in tokens:
        token = str(raw or "").strip()
        if not token:
            continue
        market = "KRX"
        symbol = token
        if ":" in token:
            prefix, symbol = [part.strip() for part in token.split(":", 1)]
            prefix = prefix.upper()
            if prefix not in {"KRX", "NXT"}:
                raise ValueError(f"Unsupported Kiwoom market prefix: {prefix}")
            market = prefix
        if symbol.upper().endswith("_NX"):
            market = "NXT"
            symbol = symbol[:-3]
        symbol = symbol.removeprefix("A").zfill(6)
        if not symbol.isdigit() or len(symbol) != 6:
            raise ValueError(f"Kiwoom symbol must be a 6 digit Korean stock/ETF code: {symbol}")
        subscriptions.append(KiwoomSubscription(symbol=symbol, market=market))
    return subscriptions


def _bool(value: str | None, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value in (None, ""):
        value = default
    text = str(value).replace(",", "").strip()
    try:
        return Decimal(text)
    except InvalidOperation:
        return Decimal(text.replace("+", ""))


def _abs_decimal(value: Any) -> Decimal:
    return abs(_decimal(value))


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if row.get(key) not in (None, ""):
            return row[key]
    return None


def _first_list(data: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    for value in data.values():
        if isinstance(value, list):
            return value
    return []


def _kst_to_ns(value: Any) -> int:
    text = str(value or "").strip()
    if len(text) >= 14 and text[:14].isdigit():
        dt = datetime.strptime(text[:14], "%Y%m%d%H%M%S").replace(tzinfo=KST)
        return int(dt.astimezone(timezone.utc).timestamp() * 1_000_000_000)
    if len(text) >= 6 and text[:6].isdigit():
        now = datetime.now(KST)
        dt = now.replace(hour=int(text[0:2]), minute=int(text[2:4]), second=int(text[4:6]), microsecond=0)
        if dt - now > timedelta(hours=12):
            dt -= timedelta(days=1)
        elif now - dt > timedelta(hours=12):
            dt += timedelta(days=1)
        return int(dt.astimezone(timezone.utc).timestamp() * 1_000_000_000)
    return utc_now_ns()


class KiwoomAuthClient:
    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or get_settings()
        provider = self.settings.providers["kiwoom"]
        self.app_key = provider.values.get("KIWOOM_APP_KEY")
        self.app_secret = provider.values.get("KIWOOM_APP_SECRET")
        self.use_mock = _bool(provider.values.get("KIWOOM_USE_MOCK"), default=True)
        host = "mockapi.kiwoom.com" if self.use_mock else "api.kiwoom.com"
        self.rest_url = f"https://{host}"
        self.ws_url = f"wss://{host}:10000/api/dostk/websocket"
        self._token_lock = asyncio.Lock()

    @property
    def has_credentials(self) -> bool:
        return bool(self.app_key and self.app_secret)

    async def auth_check(self) -> dict[str, Any]:
        if not self.has_credentials:
            return {"provider": "kiwoom", "status": "SKIPPED_MISSING_CREDENTIALS"}
        token = await self.access_token()
        return {"provider": "kiwoom", "status": "ok", "token": {"ok": bool(token)}, "mock": self.use_mock}

    async def access_token(self) -> str:
        async with self._cache_lock():
            return await self._access_token_locked()

    @asynccontextmanager
    async def _cache_lock(self):
        """Serialize REST/WS and Linux workers sharing an expiring token."""
        async with self._token_lock:
            path = self._cache_path().with_suffix('.lock')
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                try:
                    import fcntl
                except ImportError:
                    fcntl = None
                if fcntl is not None:
                    deadline = time.monotonic()+30
                    while True:
                        try:
                            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if time.monotonic()>deadline:
                                raise RuntimeError('Kiwoom token refresh lock timed out')
                            await asyncio.sleep(.05)
                yield
            finally:
                os.close(handle)

    async def invalidate_token(self, rejected: str) -> None:
        async with self._cache_lock():
            try:
                payload = json.loads(self._cache_path().read_text(encoding='utf-8'))
                # A different worker may already have replaced this token.
                if payload.get('token') == rejected:
                    self._cache_path().unlink(missing_ok=True)
            except (OSError, ValueError):
                pass

    @staticmethod
    def token_rejected(data: dict[str, Any], status: int = 200) -> bool:
        message = str(data.get('return_msg') or '').lower()
        return status == 401 or str(data.get('return_code')) in {'805004','8005'} or '8005' in message or (
            ('token' in message or '토큰' in message) and any(s in message for s in ['유효하지','만료','invalid','expired']))

    def _credential_fingerprint(self) -> str:
        return hashlib.sha256(json.dumps([self.rest_url,self.app_key,self.app_secret]).encode()).hexdigest()

    async def _access_token_locked(self) -> str:
        cached = self._read_token_cache()
        if cached:
            return cached
        if not self.app_key or not self.app_secret:
            raise RuntimeError("KIWOOM_APP_KEY/KIWOOM_APP_SECRET are not configured")
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.rest_url}/oauth2/token",
                json={"grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self.app_secret},
                headers={"Content-Type": "application/json;charset=UTF-8"},
            ) as response:
                data = await _safe_json(response)
                if response.status >= 400 or str(data.get("return_code", "0")) not in {"0", ""}:
                    raise RuntimeError(
                        f"Kiwoom token failed status={response.status} code={data.get('return_code')} msg={data.get('return_msg')}"
                    )
                token = data.get("token")
                expires_dt = str(data.get("expires_dt") or "")
                if not token:
                    raise RuntimeError("Kiwoom token response missing token")
                self._write_token_cache(str(token), expires_dt)
                return str(token)

    async def post_api(self, path: str, api_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return (await self.post_api_page(path, api_id, body))[0]

    async def post_api_page(self, path: str, api_id: str, body: dict[str, Any], *, next_key: str = "") -> tuple[dict[str, Any], str]:
        """Return continuation keys for finite history warmup requests."""
        readonly = path in {'/api/dostk/chart','/api/dostk/stkinfo','/api/dostk/mrkcond','/api/dostk/acnt'}
        for attempt in range(2 if readonly else 1):
            token = await self.access_token()
            result = await self._post_api_page_once(path, api_id, body, token, next_key=next_key)
            data, next_page, status = result
            if self.token_rejected(data, status):
                await self.invalidate_token(token)
                if readonly and attempt == 0:
                    continue
            if status >= 400 or str(data.get('return_code', '0')) not in {'0',''}:
                raise RuntimeError(f"Kiwoom {api_id} failed status={status} code={data.get('return_code')} msg={data.get('return_msg')}")
            return data, next_page
        raise RuntimeError('Kiwoom authentication refresh failed')

    async def _post_api_page_once(self, path, api_id, body, token, *, next_key=''):
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": api_id,
            "cont-yn": "Y" if next_key else "N",
            "next-key": next_key,
        }
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{self.rest_url}{path}", json=body, headers=headers) as response:
                data = await _safe_json(response)
                return data, response.headers.get("next-key", "") if response.headers.get("cont-yn") == "Y" else "", response.status

    async def account_state_summary(self) -> dict[str, Any]:
        body = await self.post_api("/api/dostk/acnt", "kt00018", {"qry_tp": "1", "dmst_stex_tp": "KRX"})
        rows = body.get("acnt_evlt_remn_indv_tot") or []
        return {
            "provider": "kiwoom",
            "mock": self.use_mock,
            "positions_count": len(rows) if isinstance(rows, list) else 0,
            "return_code": body.get("return_code"),
        }

    def _cache_path(self) -> Path:
        suffix = "mock" if self.use_mock else "real"
        return self.settings.state_dir / f"kiwoom_token_{suffix}.json"

    def _read_token_cache(self) -> str | None:
        path = self._cache_path()
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get('credential_fingerprint') != self._credential_fingerprint():
                return None
            token = payload.get("token")
            expires_dt = str(payload.get("expires_dt") or "")
            expires = datetime.strptime(expires_dt, "%Y%m%d%H%M%S").replace(tzinfo=KST)
            if expires.timestamp() - TOKEN_EXPIRY_SKEW_SECONDS <= time.time():
                return None
            return str(token) if token else None
        except Exception:
            return None

    def _write_token_cache(self, token: str, expires_dt: str) -> None:
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"token": token, "expires_dt": expires_dt,
            'credential_fingerprint':self._credential_fingerprint()}, ensure_ascii=False), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)


class KiwoomCollector:
    name = "kiwoom"

    def __init__(self, subscriptions: list[KiwoomSubscription] | None = None, *, publish_rest_snapshot: bool = True) -> None:
        self.subscriptions = subscriptions or []
        self.publish_rest_snapshot = publish_rest_snapshot
        self.client = KiwoomAuthClient()
        self._bar_cursor: dict[str, int] = {}

    def realtime_registration(self) -> list[dict[str, Any]]:
        """Register ETF indicative NAV independently of stock trade/book feeds."""
        data = [{"item": [s.ws_symbol for s in self.subscriptions], "type": ["0B", "0D"]}]
        etfs = [s.ws_symbol for s in self.subscriptions if self.instrument(s).asset_class == AssetClass.ETF]
        if etfs:
            data.append({"item": etfs, "type": ["0G"]})
        return data

    async def run(self, bus: EventBus, duration_sec: float | None = None) -> None:
        poller = asyncio.create_task(self._poll_closed_bars(bus))
        websocket = asyncio.create_task(self._run_websocket(bus, duration_sec))
        try:
            done, _ = await asyncio.wait([poller, websocket], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in (poller, websocket):
                task.cancel()
            await asyncio.gather(poller, websocket, return_exceptions=True)

    async def _run_websocket(self, bus: EventBus, duration_sec: float | None = None) -> None:
        if not self.subscriptions:
            raise RuntimeError("Kiwoom collector needs --symbols, e.g. 005930,KRX:000660,NXT:005930")
        if len({item.symbol for item in self.subscriptions}) > REALTIME_SYMBOL_LIMIT:
            raise RuntimeError(f"Kiwoom realtime subscription exceeds {REALTIME_SYMBOL_LIMIT} unique symbols")
        if self.publish_rest_snapshot:
            await self._publish_rest_snapshot(bus)
        deadline = asyncio.get_running_loop().time() + duration_sec if duration_sec else None
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(self.client.ws_url, heartbeat=20) as ws:
                await self._login_ws(ws)
                await ws.send_json(
                    {
                        "trnm": "REG",
                        "grp_no": "1",
                        "refresh": "1",
                        "data": self.realtime_registration(),
                    }
                )
                while True:
                    if deadline and asyncio.get_running_loop().time() >= deadline:
                        break
                    receive_timeout = min(1.0, max(0.05, deadline - asyncio.get_running_loop().time())) if deadline else 1.0
                    try:
                        msg = await ws.receive(timeout=receive_timeout)
                    except asyncio.TimeoutError:
                        if deadline:
                            continue
                        continue
                    received_ns=time.time_ns(); received_monotonic_ns=time.monotonic_ns()
                    if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        _raise_websocket_closed(ws)
                    if msg.type not in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                        continue
                    text = msg.data.decode() if isinstance(msg.data, bytes) else msg.data
                    control = json.loads(text)
                    if control.get("trnm") == "PING":
                        await ws.send_json(control)
                        continue
                    for event in self.parse_ws_frame(text, receive_ts_ns=received_ns, receive_monotonic_ns=received_monotonic_ns):
                        await bus.publish_market_event(event)

    async def _publish_rest_snapshot(self, bus: EventBus) -> None:
        for subscription in self.subscriptions:
            try:
                trades = await self.client.post_api("/api/dostk/stkinfo", "ka10003", {"stk_cd": subscription.rest_symbol})
                for index, row in enumerate(reversed(_first_list(trades, "cntr_infr", "output", "data")[:5])):
                    await bus.publish_market_event(self.trade_event_from_rest(subscription, row, sequence=f"rest-{index}"))
            except Exception:
                pass
            try:
                orderbook = await self.client.post_api("/api/dostk/mrkcond", "ka10004", {"stk_cd": subscription.rest_symbol})
                await bus.publish_market_event(self.orderbook_event_from_rest(subscription, orderbook))
            except Exception:
                pass

    async def _login_ws(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        token = await self.client.access_token()
        await ws.send_json({"trnm": "LOGIN", "token": token})
        deadline = asyncio.get_running_loop().time() + 15.0
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RuntimeError("Kiwoom websocket login timed out")
            timeout = max(0.05, remaining)
            msg = await ws.receive(timeout=timeout)
            if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                _raise_websocket_closed(ws)
            if msg.type not in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                continue
            data = json.loads(msg.data.decode() if isinstance(msg.data, bytes) else msg.data)
            if data.get("trnm") == "PING":
                await ws.send_json(data)
                continue
            if data.get("trnm") != "LOGIN":
                continue
            if str(data.get("return_code", "0")) not in {"0", ""}:
                if self.client.token_rejected(data):
                    await self.client.invalidate_token(token)
                raise RuntimeError(f"Kiwoom websocket login failed code={data.get('return_code')} msg={data.get('return_msg')}")
            return

    def parse_ws_frame(self, raw: str | bytes, *, receive_ts_ns: int | None = None, receive_monotonic_ns: int | None = None) -> list[TradeEvent | OrderBookEvent]:
        data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        if data.get("trnm") == "PING":
            return []
        if data.get("trnm") in {"LOGIN", "REG", "REMOVE"}:
            if str(data.get("return_code", "0")) not in {"0", ""}:
                raise RuntimeError(f"Kiwoom websocket control error: {data}")
            return []
        if str(data.get("return_code", "0")) not in {"0", ""}:
            raise RuntimeError(f"Kiwoom websocket error: {data}")
        events: list[TradeEvent | OrderBookEvent] = []
        for item in data.get("data") or []:
            item_code = str(item.get("item") or "")
            values = item.get("values") or {}
            event_type = item.get("type") or ""
            if isinstance(event_type, list):
                event_type = event_type[0] if event_type else ""
            subscription = self._subscription_from_item(item_code)
            if event_type == "0B":
                events.append(self.trade_event_from_ws(subscription, values, receive_ts_ns=receive_ts_ns, receive_monotonic_ns=receive_monotonic_ns))
            elif event_type == "0D":
                events.append(self.orderbook_event_from_ws(subscription, values, receive_ts_ns=receive_ts_ns, receive_monotonic_ns=receive_monotonic_ns))
            elif event_type == "0G" and self.instrument(subscription).asset_class == AssetClass.ETF:
                if values.get("36") in (None, ""):
                    continue
                events.append(EtfNavEvent(
                    instrument=self.instrument(subscription), venue=Venue.KIWOOM, provider=self.name,
                    source_ts_ns=_kst_to_ns(values.get("20")), receive_ts_ns=receive_ts_ns or utc_now_ns(),
                    receive_monotonic_ns=receive_monotonic_ns, session="kiwoom_krx",
                    sequence=json.dumps(values, sort_keys=True), nav=_abs_decimal(values["36"]),
                    nav_change=_decimal(values["37"]) if values.get("37") else None,
                    nav_change_pct=_decimal(values["38"]) if values.get("38") else None,
                    index_disparity_pct=_decimal(values["265"]) if values.get("265") else None,
                    etf_disparity_pct=_decimal(values["266"]) if values.get("266") else None,
                    raw_payload=json.dumps(values, ensure_ascii=False)))
        return events

    def _subscription_from_item(self, item_code: str) -> KiwoomSubscription:
        market = "NXT" if item_code.upper().endswith("_NX") else "KRX"
        symbol = item_code.upper().removesuffix("_NX").removeprefix("A").zfill(6)
        return KiwoomSubscription(symbol=symbol, market=market)

    def instrument(self, subscription: KiwoomSubscription) -> InstrumentId:
        metadata = configured_instrument("kiwoom", subscription.symbol) or {}
        return InstrumentId(
            venue=Venue.KIWOOM,
            asset_class=AssetClass(metadata.get("asset_class", "equity")),
            raw_symbol=subscription.symbol,
            std_symbol=f"KIWOOM:{subscription.market}:{subscription.symbol}",
            exchange=subscription.market,
            currency="KRW",
        )

    def bar_event_from_rest(self, subscription: KiwoomSubscription, row: dict[str, Any], *, now_ns: int | None = None) -> Bar1mEvent | None:
        """ka10080 timestamps label the start; publish only completed native bars."""
        start = _kst_to_ns(row.get("cntr_tm"))
        dt = datetime.fromtimestamp(start / 1e9, KST)
        if dt.strftime("%H:%M") < "09:00" or dt.strftime("%H:%M") > "15:30":
            return None
        end = start + 60_000_000_000
        # Closing auction is a single print, with the source's 15:30 label.
        if dt.strftime("%H:%M") == "15:30":
            end = start
        now = now_ns or time.time_ns()
        if end > now:
            return None
        return Bar1mEvent(instrument=self.instrument(subscription), venue=Venue.KIWOOM,
            provider=self.name, source_ts_ns=end, receive_ts_ns=now, session="kiwoom_krx",
            sequence=f"ka10080:{subscription.ws_symbol}:{start}", open_ts_ns=start, close_ts_ns=end,
            open=_abs_decimal(row.get("open_pric")), high=_abs_decimal(row.get("high_pric")),
            low=_abs_decimal(row.get("low_pric")), close=_abs_decimal(row.get("cur_prc")),
            volume=_abs_decimal(row.get("trde_qty")), ingest_mode="backfill" if now-end > 120_000_000_000 else "realtime")

    async def _poll_closed_bars(self, bus: EventBus) -> None:
        failures = 0
        while True:
            now = datetime.now(KST)
            if now.weekday() < 5 and "08:59" <= now.strftime("%H:%M") <= "15:35":
                try:
                    for subscription in self.subscriptions:
                        if self.instrument(subscription).asset_class != AssetClass.ETF:
                            continue
                        data = await self.client.post_api("/api/dostk/chart", "ka10080", {
                            "stk_cd": subscription.rest_symbol, "tic_scope": "1", "upd_stkpc_tp": "0",
                            "base_dt": now.strftime("%Y%m%d")})
                        cursor = self._bar_cursor.get(subscription.ws_symbol, 0)
                        cursor_label = (datetime.fromtimestamp(cursor / 1_000_000_000, KST).strftime("%Y%m%d%H%M%S")
                                        if cursor else "")
                        for row in reversed(data.get("stk_min_pole_chart_qry") or []):
                            # REST repeats a large historical page every minute.
                            # Skip already accepted starts before constructing
                            # instruments/models on the shared collector loop.
                            label = str(row.get("cntr_tm") or "")
                            if (len(label) == 14 and label.isdigit()
                                    and (label[:8] != now.strftime("%Y%m%d") or label <= cursor_label)):
                                continue
                            event = self.bar_event_from_rest(subscription, row)
                            if event is None or event.trading_date != now.strftime("%Y-%m-%d"):
                                continue
                            if event.open_ts_ns <= self._bar_cursor.get(subscription.ws_symbol, 0):
                                continue
                            await bus.publish_market_event(event)
                            self._bar_cursor[subscription.ws_symbol] = event.open_ts_ns
                        await asyncio.sleep(0.25)
                    failures = 0
                except Exception:
                    failures += 1
                    logging.getLogger(__name__).exception("Kiwoom minute collection failed")
                    if failures >= 3:
                        raise
            await asyncio.sleep(max(1.0, 63 - time.time() % 60))

    def trade_event_from_ws(self, subscription: KiwoomSubscription, values: dict[str, Any], *, receive_ts_ns: int | None = None, receive_monotonic_ns: int | None = None) -> TradeEvent:
        event_time = _first_value(values, "20", "21")
        source_ts_ns = _kst_to_ns(event_time)
        quantity = abs(_decimal(_first_value(values, "15", "volume", "cntg_vol")))
        buy_one = abs(_decimal(_first_value(values, "1316"), default="0"))
        sell_one = abs(_decimal(_first_value(values, "1315"), default="0"))
        return TradeEvent(
            instrument=self.instrument(subscription),
            venue=Venue.KIWOOM,
            provider=self.name,
            source_ts_ns=source_ts_ns,
            receive_ts_ns=receive_ts_ns or utc_now_ns(),
            wallclock_ts_ns=receive_ts_ns,
            receive_monotonic_ns=receive_monotonic_ns,
            session=f"kiwoom_{subscription.market.lower()}",
            sequence=f"{subscription.ws_symbol}:{event_time}:{_first_value(values, '13', '14', '15')}",
            price=_abs_decimal(_first_value(values, "10", "price", "cur_prc")),
            quantity=quantity,
            aggressor_side=Side.BUY if buy_one > 0 and sell_one == 0 else Side.SELL if sell_one > 0 and buy_one == 0 else Side.UNKNOWN,
            trade_id=f"{subscription.ws_symbol}:{event_time}:{_first_value(values, '13', '15')}",
        )

    def trade_event_from_rest(self, subscription: KiwoomSubscription, row: dict[str, Any], *, sequence: str | int | None = None) -> TradeEvent:
        event_time = _first_value(row, "tm", "cntr_tm", "time")
        return TradeEvent(
            instrument=self.instrument(subscription),
            venue=Venue.KIWOOM,
            provider=self.name,
            source_ts_ns=_kst_to_ns(event_time),
            receive_ts_ns=utc_now_ns(),
            session=f"kiwoom_{subscription.market.lower()}",
            sequence=sequence or _first_value(row, "acc_trde_qty", "tm"),
            price=_abs_decimal(_first_value(row, "cur_prc", "price", "cntr_prc")),
            quantity=abs(_decimal(_first_value(row, "cntr_trde_qty", "trde_qty", "volume"))),
            aggressor_side=Side.UNKNOWN,
            trade_id=f"{subscription.ws_symbol}:{event_time}:{_first_value(row, 'acc_trde_qty', 'cntr_trde_qty')}",
        )

    def orderbook_event_from_ws(self, subscription: KiwoomSubscription, values: dict[str, Any], *, receive_ts_ns: int | None = None, receive_monotonic_ns: int | None = None) -> OrderBookEvent:
        event_time = _first_value(values, "21", "20")
        bids = [
            OrderBookLevel(
                price=_abs_decimal(_first_value(values, str(50 + index))),
                quantity=abs(_decimal(self._orderbook_quantity(values, "bid", subscription.market, index))),
            )
            for index in range(1, 11)
            if _first_value(values, str(50 + index)) not in (None, "")
        ]
        asks = [
            OrderBookLevel(
                price=_abs_decimal(_first_value(values, str(40 + index))),
                quantity=abs(_decimal(self._orderbook_quantity(values, "ask", subscription.market, index))),
            )
            for index in range(1, 11)
            if _first_value(values, str(40 + index)) not in (None, "")
        ]
        return OrderBookEvent(
            instrument=self.instrument(subscription),
            venue=Venue.KIWOOM,
            provider=self.name,
            source_ts_ns=_kst_to_ns(event_time),
            receive_ts_ns=receive_ts_ns or utc_now_ns(),
            wallclock_ts_ns=receive_ts_ns,
            receive_monotonic_ns=receive_monotonic_ns,
            session=f"kiwoom_{subscription.market.lower()}",
            sequence=f"{subscription.ws_symbol}:{event_time}",
            update_type=BookUpdateType.SNAPSHOT,
            depth=max(len(bids), len(asks)),
            bids=bids,
            asks=asks,
            is_full_depth=len(bids) >= 10 and len(asks) >= 10,
        )

    @staticmethod
    def _orderbook_quantity(values: dict[str, Any], side: str, market: str, index: int) -> Any:
        generic = str((70 if side == "bid" else 60) + index)
        if market == "NXT":
            exchange_specific = str((6075 if side == "bid" else 6065) + index)
        else:
            exchange_specific = str((6053 if side == "bid" else 6043) + index)
        return _first_value(values, generic, exchange_specific)

    def orderbook_event_from_rest(self, subscription: KiwoomSubscription, row: dict[str, Any]) -> OrderBookEvent:
        bids = [[_first_value(row, "buy_fpr_bid"), _first_value(row, "buy_fpr_req")]]
        asks = [[_first_value(row, "sel_fpr_bid"), _first_value(row, "sel_fpr_req")]]
        bids += [[_first_value(row, f"buy_{i}th_pre_bid"), _first_value(row, f"buy_{i}th_pre_req")] for i in range(2, 11)]
        asks += [[_first_value(row, f"sel_{i}th_pre_bid"), _first_value(row, f"sel_{i}th_pre_req")] for i in range(2, 11)]
        bid_levels = [OrderBookLevel(price=_abs_decimal(price), quantity=abs(_decimal(qty))) for price, qty in bids if price not in (None, "")]
        ask_levels = [OrderBookLevel(price=_abs_decimal(price), quantity=abs(_decimal(qty))) for price, qty in asks if price not in (None, "")]
        return OrderBookEvent(
            instrument=self.instrument(subscription),
            venue=Venue.KIWOOM,
            provider=self.name,
            source_ts_ns=utc_now_ns(),
            receive_ts_ns=utc_now_ns(),
            session=f"kiwoom_{subscription.market.lower()}",
            sequence="rest",
            update_type=BookUpdateType.SNAPSHOT,
            depth=max(len(bid_levels), len(ask_levels)),
            bids=bid_levels,
            asks=ask_levels,
            is_full_depth=len(bid_levels) >= 10 and len(ask_levels) >= 10,
        )


async def _safe_json(response: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        data = await response.json(content_type=None)
        return data if isinstance(data, dict) else {"payload": data}
    except Exception:
        text = await response.text()
        return {"text": text[:500]}
