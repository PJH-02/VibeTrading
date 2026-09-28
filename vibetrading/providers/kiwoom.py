from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import aiohttp

from vibetrading.bus import EventBus
from vibetrading.models import (
    AssetClass,
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


KST = timezone(timedelta(hours=9))
TOKEN_EXPIRY_SKEW_SECONDS = 300
REALTIME_SYMBOL_LIMIT = 200


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

    @property
    def has_credentials(self) -> bool:
        return bool(self.app_key and self.app_secret)

    async def auth_check(self) -> dict[str, Any]:
        if not self.has_credentials:
            return {"provider": "kiwoom", "status": "SKIPPED_MISSING_CREDENTIALS"}
        token = await self.access_token()
        return {"provider": "kiwoom", "status": "ok", "token": {"ok": bool(token)}, "mock": self.use_mock}

    async def access_token(self) -> str:
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
        token = await self.access_token()
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": api_id,
            "cont-yn": "N",
            "next-key": "",
        }
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{self.rest_url}{path}", json=body, headers=headers) as response:
                data = await _safe_json(response)
                if response.status >= 400 or str(data.get("return_code", "0")) not in {"0", ""}:
                    raise RuntimeError(
                        f"Kiwoom {api_id} failed status={response.status} code={data.get('return_code')} msg={data.get('return_msg')}"
                    )
                return data

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
        tmp.write_text(json.dumps({"token": token, "expires_dt": expires_dt}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


class KiwoomCollector:
    name = "kiwoom"

    def __init__(self, subscriptions: list[KiwoomSubscription] | None = None, *, publish_rest_snapshot: bool = True) -> None:
        self.subscriptions = subscriptions or []
        self.publish_rest_snapshot = publish_rest_snapshot
        self.client = KiwoomAuthClient()

    async def run(self, bus: EventBus, duration_sec: float | None = None) -> None:
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
                        "data": [{"item": [item.ws_symbol for item in self.subscriptions], "type": ["0B", "0D"]}],
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
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        raise ws.exception() or RuntimeError("Kiwoom websocket closed")
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
        await ws.send_json({"trnm": "LOGIN", "token": await self.client.access_token()})
        deadline = asyncio.get_running_loop().time() + 15.0
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RuntimeError("Kiwoom websocket login timed out")
            timeout = max(0.05, remaining)
            msg = await ws.receive(timeout=timeout)
            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                raise ws.exception() or RuntimeError("Kiwoom websocket closed during login")
            if msg.type not in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                continue
            data = json.loads(msg.data.decode() if isinstance(msg.data, bytes) else msg.data)
            if data.get("trnm") == "PING":
                await ws.send_json(data)
                continue
            if data.get("trnm") != "LOGIN":
                continue
            if str(data.get("return_code", "0")) not in {"0", ""}:
                raise RuntimeError(f"Kiwoom websocket login failed: {data}")
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
        return events

    def _subscription_from_item(self, item_code: str) -> KiwoomSubscription:
        market = "NXT" if item_code.upper().endswith("_NX") else "KRX"
        symbol = item_code.upper().removesuffix("_NX").removeprefix("A").zfill(6)
        return KiwoomSubscription(symbol=symbol, market=market)

    def instrument(self, subscription: KiwoomSubscription) -> InstrumentId:
        return InstrumentId(
            venue=Venue.KIWOOM,
            asset_class=AssetClass.EQUITY,
            raw_symbol=subscription.symbol,
            std_symbol=f"KIWOOM:{subscription.market}:{subscription.symbol}",
            exchange=subscription.market,
            currency="KRW",
        )

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
