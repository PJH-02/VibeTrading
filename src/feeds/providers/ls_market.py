"""LS Securities read-only derivatives collectors.

Only public LS TR documentation is used here.  Both collectors fail closed
until a human has verified that the account's particular market-data rights
carry no additional charge.  No LS order TR is exposed by this module.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, time as clock_time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

import aiohttp

from core.models import AssetClass, InstrumentId, ParticipantFlowEvent, TradeEvent, Venue, stable_hash
from operations.session_alerts import KST, _calendar, session_state
from runtime.settings import RuntimeSettings, get_settings


REST_URL = "https://openapi.ls-sec.co.kr:8080"
WS_URL = "wss://openapi.ls-sec.co.kr:9443/websocket"
KOSPI200_FUTURES = InstrumentId(
    venue=Venue.KRX, asset_class=AssetClass.FUTURE,
    raw_symbol="K2I", std_symbol="KOSPI200_FUTURES",
    underlying="KOSPI200", currency="KRW", exchange="KRX_DERIVATIVES",
)
_FOREIGN_FIELDS = ("msvolume2", "mdvolume2", "msvol2", "msvalue2", "mdvalue2", "msval2")


def _true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"LS {field} missing or invalid") from exc
    if not result.is_finite():
        raise ValueError(f"LS {field} must be finite")
    return result


def krx_futures_session(now: datetime | None = None) -> tuple[str, str] | None:
    """Return LS DBT subscription key and KRX trading date.

    KRX night trading runs 18:00–06:00 KST and belongs to the next regular
    trading session.  The start date must itself be an XKRX session.
    """
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    local = instant.astimezone(KST)
    day_open, trading_day = session_state("KR_DERIVATIVES", instant)
    if day_open:
        return "DFK2I", trading_day
    local_time = local.time()
    if local_time >= clock_time(18):
        start = local.date()
    elif local_time < clock_time(6):
        start = local.date() - timedelta(days=1)
    else:
        return None
    cal = _calendar("XKRX")
    if not cal.is_session(start.isoformat()):
        return None
    next_day = cal.next_session(start.isoformat()).date().isoformat()
    return "NFK2I", next_day


def parse_dbt_foreign(payload: Mapping[str, Any], key: str, trading_day: str,
                      receive_ns: int | None = None) -> ParticipantFlowEvent | None:
    """DBT field group 2 is explicitly the foreign investor in the LS guide."""
    header = payload.get("header") or {}
    if header.get("tr_cd") != "DBT":
        return None
    response_key = str(header.get("tr_key") or payload.get("tr_key") or "")
    if response_key and response_key != key:
        return None
    body = payload.get("body") or {}
    if isinstance(body, Mapping) and isinstance(body.get("DBTOutBlock"), Mapping):
        body = body["DBTOutBlock"]
    if not isinstance(body, Mapping) or not all(body.get(field) not in (None, "") for field in _FOREIGN_FIELDS):
        return None
    if key not in {"DFK2I", "NFK2I"}:
        raise ValueError("unsupported LS KOSPI200 futures session key")
    buy_qty, sell_qty, net_qty, buy_value, sell_value, net_value = (
        _decimal(body[field], field) for field in _FOREIGN_FIELDS
    )
    if buy_qty - sell_qty != net_qty:
        raise ValueError("LS DBT foreign buy/sell and net volumes disagree")
    received = receive_ns or time.time_ns()
    return ParticipantFlowEvent(
        instrument=KOSPI200_FUTURES, venue=Venue.KRX,
        provider="ls-kospi200-foreign", session="KR_DERIVATIVES_DAY" if key[0] == "D" else "KR_DERIVATIVES_NIGHT",
        trading_date=trading_day, market_code="K2I", market_subcode=key[0],
        participant="frgn", participant_label="foreign",
        buy_volume=buy_qty, sell_volume=sell_qty, net_volume=net_qty,
        buy_value=buy_value, sell_value=sell_value, net_value=net_value,
        quantity_unit="contract", value_currency="KRW", value_unit="LS_native_unverified",
        observation_method="ls_dbt_websocket_provisional",
        source_ts_ns=None, source_timestamp_kind="provider_reception_time_only",
        receive_ts_ns=received,
        raw_payload=json.dumps({"tr_cd": "DBT", "tr_key": key, "body": dict(body)},
                               ensure_ascii=False, sort_keys=True),
        native_event_id=stable_hash([key, trading_day, dict(body)]),
    )


def parse_korean_trade_time(row: Mapping[str, Any]) -> int:
    """o3105 names KorDate/RcvTm as Korea trade date/time."""
    ymd = str(row.get("KorDate") or "").strip()
    hms = "".join(ch for ch in str(row.get("RcvTm") or "") if ch.isdigit())
    if len(ymd) != 8 or len(hms) < 6:
        raise ValueError("LS o3105 has no complete Korean trade timestamp")
    local = datetime.strptime(ymd + hms[:6], "%Y%m%d%H%M%S").replace(tzinfo=KST)
    return int(local.timestamp() * 1_000_000_000)


def parse_delayed_future_trade(row: Mapping[str, Any], product: str,
                               master: Mapping[str, Any], receive_ns: int,
                               min_delay_seconds: int = 600,
                               max_delay_seconds: int = 3600) -> TradeEvent | None:
    product = product.upper()
    if product not in {"ES", "NQ"}:
        raise ValueError("only CME ES/NQ are configured")
    symbol = str(master.get("Symbol") or "").strip().upper()
    if not symbol or str(row.get("Symbol") or "").strip().upper() != symbol:
        raise ValueError("LS current-price symbol differs from validated master")
    if str(master.get("BscGdsCd") or master.get("GdsCd") or "").strip().upper() != product:
        raise ValueError("LS master product differs from ES/NQ")
    source_ns = parse_korean_trade_time(row)
    delay_ms = (receive_ns - source_ns) // 1_000_000
    if delay_ms < min_delay_seconds * 1000 or delay_ms > max_delay_seconds * 1000:
        return None
    price = _decimal(row.get("TrdP"), "TrdP")
    qty = _decimal(row.get("TrdQ"), "TrdQ")
    if price <= 0 or qty <= 0:
        return None
    year = str(master.get("LstngYr") or "").strip()
    month = str(master.get("LstngM") or "").strip()
    contract_month = f"{int(year):04d}{int(month):02d}" if year.isdigit() and month.isdigit() and 1 <= int(month) <= 12 else None
    instrument = InstrumentId(
        venue=Venue.CME, asset_class=AssetClass.FUTURE,
        raw_symbol=symbol, std_symbol=product, contract_month=contract_month,
        underlying="SP500" if product == "ES" else "NASDAQ100",
        currency="USD", exchange="CME",
    )
    return TradeEvent(
        instrument=instrument, venue=Venue.CME, provider="ls-overseas-delayed",
        session="US_FUTURES_DELAYED", source_ts_ns=source_ns,
        source_timestamp_kind="ls_korean_trade_time", observed_delay_ms=int(delay_ms),
        receive_ts_ns=receive_ns, price=price, quantity=qty,
        native_event_id=stable_hash([symbol, row.get("KorDate"), row.get("RcvTm"), str(price), str(qty)]),
    )


class LSReadOnlyClient:
    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self.env = self.settings.env
        self._token = ""
        self._expiry_monotonic = 0.0

    def require_entitlement(self, feed: str) -> None:
        flag = {
            "kr_foreign": "LS_KR_FUTURES_NO_EXTRA_FEE_CONFIRMED",
            "overseas_delayed": "LS_OVERSEAS_DELAYED_NO_EXTRA_FEE_CONFIRMED",
        }[feed]
        if not _true(self.env.get(flag)):
            raise RuntimeError(f"LS collection disabled: {flag} must be verified from account terms before any API call")
        if feed == "overseas_delayed" and not _true(self.env.get("LS_OVERSEAS_DELAYED_ENTITLEMENT_CONFIRMED")):
            raise RuntimeError("LS delayed ES/NQ entitlement has not been verified")
        if not self.env.get("LS_APP_KEY") or not self.env.get("LS_APP_SECRET"):
            raise RuntimeError("LS_APP_KEY and LS_APP_SECRET are required")

    async def token(self, session: aiohttp.ClientSession) -> str:
        if self._token and time.monotonic() < self._expiry_monotonic:
            return self._token
        async with session.post(
            REST_URL + "/oauth2/token",
            data={"grant_type": "client_credentials", "appkey": self.env["LS_APP_KEY"],
                  "appsecretkey": self.env["LS_APP_SECRET"], "scope": "oob"},
        ) as response:
            response.raise_for_status()
            data = await response.json()
        token = str(data.get("access_token") or "")
        if not token:
            raise RuntimeError("LS token response has no access_token")
        self._token = token
        self._expiry_monotonic = time.monotonic() + max(1, int(data.get("expires_in") or data.get("expire_in") or 3600) - 120)
        return token

    async def request(self, session: aiohttp.ClientSession, code: str, path: str,
                      body: Mapping[str, Any]) -> dict[str, Any]:
        headers = {"authorization": "Bearer " + await self.token(session),
                   "content-type": "application/json; charset=UTF-8", "tr_cd": code,
                   "tr_cont": "N", "tr_cont_key": ""}
        async with session.post(REST_URL + path, headers=headers, json={f"{code}InBlock": dict(body)}) as response:
            response.raise_for_status()
            data = await response.json()
        if not isinstance(data, dict) or str(data.get("rsp_cd") or "00000") not in {"00000", "0"}:
            raise RuntimeError(f"LS {code} request rejected: {str(data.get('rsp_cd'))[:24] if isinstance(data, dict) else 'invalid response'}")
        return data


class LSKospi200ForeignCollector:
    name = "ls-kospi200-foreign"

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.client = LSReadOnlyClient(settings)

    async def run(self, bus: Any, duration_sec: float | None = None) -> None:
        self.client.require_entitlement("kr_foreign")
        deadline = time.monotonic() + duration_sec if duration_sec else None
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            while deadline is None or time.monotonic() < deadline:
                active = krx_futures_session()
                if active is None:
                    return
                key, trading_day = active
                token = await self.client.token(session)
                async with session.ws_connect(WS_URL, heartbeat=30, timeout=20) as ws:
                    await ws.send_json({"header": {"token": token, "tr_type": "3"},
                                        "body": {"tr_cd": "DBT", "tr_key": key}})
                    while krx_futures_session() == active and (deadline is None or time.monotonic() < deadline):
                        try:
                            msg = await ws.receive(timeout=30)
                        except asyncio.TimeoutError:
                            continue
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            payload = json.loads(msg.data)
                            if not isinstance(payload, dict):
                                continue
                            event = parse_dbt_foreign(payload, key, trading_day)
                            if event is not None:
                                await bus.publish_market_event(event)
                        elif msg.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR}:
                            raise RuntimeError("LS DBT websocket disconnected")
                return


class LSOverseasDelayedCollector:
    name = "ls-overseas-delayed"

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.client = LSReadOnlyClient(settings)
        self.env = self.client.env

    async def _master(self, session: aiohttp.ClientSession, product: str) -> Mapping[str, Any]:
        response = await self.client.request(session, "o3121", "/overseas-futureoption/market-data",
                                             {"MktGb": "F", "BscGdsCd": product})
        candidates = response.get("o3121OutBlock")
        if isinstance(candidates, Mapping):
            candidates = [candidates]
        if not isinstance(candidates, list):
            raise RuntimeError(f"LS o3121 returned no {product} contract master")
        rows = [row for row in candidates if isinstance(row, Mapping)
                and str(row.get("BscGdsCd") or row.get("GdsCd") or "").strip().upper() == product
                and str(row.get("Symbol") or "").strip()]
        configured = str(self.env.get(f"LS_{product}_SYMBOL") or "").strip().upper()
        if configured:
            rows = [row for row in rows if str(row["Symbol"]).strip().upper() == configured]
        if not rows:
            raise RuntimeError(f"LS {product} contract not present in official o3121 master")
        if not configured and len(rows) != 1:
            raise RuntimeError(f"LS {product} has multiple contracts; set LS_{product}_SYMBOL after master review")
        return rows[0]

    async def run(self, bus: Any, duration_sec: float | None = None) -> None:
        self.client.require_entitlement("overseas_delayed")
        if not session_state("US_FUTURES")[0]:
            return
        interval = max(10.0, float(self.env.get("LS_OVERSEAS_POLL_SECONDS") or "30"))
        deadline = time.monotonic() + duration_sec if duration_sec else None
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            masters = {}
            for product in ("ES", "NQ"):
                masters[product] = await self._master(session, product)
                await asyncio.sleep(1.1)  # o3121 allows one request per second.
            while session_state("US_FUTURES")[0] and (deadline is None or time.monotonic() < deadline):
                for product, master in masters.items():
                    response = await self.client.request(session, "o3105", "/overseas-futureoption/market-data",
                                                         {"symbol": master["Symbol"]})
                    row = response.get("o3105OutBlock")
                    if not isinstance(row, Mapping):
                        raise RuntimeError("LS o3105 current-price response is missing")
                    event = parse_delayed_future_trade(row, product, master, time.time_ns())
                    if event is not None:
                        await bus.publish_market_event(event)
                    await asyncio.sleep(0.55)  # o3105 allows two requests per second.
                await asyncio.sleep(interval)
