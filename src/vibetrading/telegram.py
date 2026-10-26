from __future__ import annotations

import asyncio
import inspect
import logging
import shutil
import time
import json
import sqlite3
import uuid
from contextvars import ContextVar
from decimal import Decimal
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import psutil

from .bus import NatsJetStreamBus
from .dashboard.projection import paper_projection, paper_projection_paths
from .health import check as health_check
from .health import configured_crypto_providers
from market.local_snapshot import local_snapshot_config, read_market_snapshot
from .session_alerts import SESSIONS, session_state
from .settings import RuntimeSettings, get_settings
from .telegram_trade import trade_report
from .trade_reporting import daily_report_view
from execution.process_lease import ProcessLease
from .telegram_state import OutboxFull, TelegramState
from .local_transport import TransportError, validate_command_result, _object_pairs

logger = logging.getLogger(__name__)
_reply_identity: ContextVar[str | None] = ContextVar("telegram_reply_identity", default=None)


@contextmanager
def _event_reply_context(message):
    # Local facts have an immutable generation/fact-ID delivery identity. Keep
    # the same outbox key when enqueue commits but the subsequent source ACK
    # is lost. HTTP delivery remains at least once; it is not a source ACK.
    token = None
    if getattr(message, "stream_name", "") == "LOCAL_FACTS" and getattr(message, "commit_id", ""):
        token = _reply_identity.set("fact:" + message.commit_id)
    try:
        yield
    finally:
        if token is not None:
            _reply_identity.reset(token)


class TelegramRateLimit(RuntimeError):
    def __init__(self, retry_seconds: float):
        self.retry_seconds = max(1, min(3600, retry_seconds))
        super().__init__("Telegram rate limited")


def _numeric_ids(raw, *, chats=False):
    if not isinstance(raw, str) or len(raw) > 1024:
        raise ValueError('Invalid numeric Telegram allowlist')
    entries = raw.split(',') if raw else []
    if len(entries) > 32:
        raise ValueError('Telegram allowlist exceeds 32 identities')
    result = set()
    for text in entries:
        text = text.strip()
        digits = text[1:] if chats and text.startswith('-') else text
        if (not digits or any(c not in '0123456789' for c in digits) or len(digits) > 19
                or str(int(text)) != text or not 0 < abs(int(text)) <= 2**63-1):
            raise ValueError('Telegram identities must be canonical integers')
        result.add(int(text))
    return frozenset(result)


class TelegramController:
    """Minimal allowlisted control/observability loop; inactive without credentials."""

    def __init__(self, settings: RuntimeSettings | None = None, *, bus=None, bus_factory=None,
                 command_handler=None, command_ttl_seconds=None, projection_reader=None) -> None:
        if bus is not None and bus_factory is not None:
            raise ValueError("select either bus or bus_factory")
        if bus_factory is not None and (not callable(bus_factory) or inspect.iscoroutinefunction(bus_factory)):
            raise TypeError("bus_factory must be a synchronous callable")
        if projection_reader is not None and (
                not callable(projection_reader) or inspect.iscoroutinefunction(projection_reader)
                or inspect.iscoroutinefunction(getattr(projection_reader, '__call__', None))):
            raise TypeError("projection_reader must be a synchronous callable")
        self._projection_reader = projection_reader
        self.settings = settings or get_settings()
        self.token = self.settings.env.get("TELEGRAM_BOT_TOKEN")
        self.allowed_id = str(self.settings.telegram_user_id or "")
        self.allowed_users = _numeric_ids(self.settings.env.get('TELEGRAM_USER_IDS', self.allowed_id))
        self.allowed_chats = _numeric_ids(self.settings.env.get('TELEGRAM_CHAT_IDS',
            self.settings.env.get('TELEGRAM_CHAT_ID') or self.allowed_id), chats=True)
        if not self.allowed_id and self.allowed_chats:
            # Multi-list-only setups use the first explicitly approved chat for alerts.
            self.allowed_id = (self.settings.env.get('TELEGRAM_CHAT_IDS') or self.settings.env['TELEGRAM_CHAT_ID']).split(',')[0].strip()
        if command_handler is not None and (not callable(command_handler)
                or type(command_ttl_seconds) is not int or not 0 < command_ttl_seconds <= 300):
            raise ValueError('Control requires a handler and explicit TTL within 300 seconds')
        self._command_handler, self._command_ttl_seconds = command_handler, command_ttl_seconds
        self.base_url = f"https://api.telegram.org/bot{self.token}" if self.token else ""
        self._offset = 0
        self._sent_session: set[str] = set()
        self._crypto_alerted: set[str] = set()
        self._crypto_alert_at: dict[str, float] = {}
        self._market_alert_at: dict[str, float] = {}
        self._market_unready_since: dict[str, float] = {}
        self._state = TelegramState(self.settings.state_dir / "telegram" / "delivery.sqlite3")
        self._outbox_enabled = False
        self._event_bus = bus
        self._event_bus_factory = bus_factory

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.allowed_users and self.allowed_chats)

    async def run(self, poll_seconds: int = 25) -> None:
        if not self.enabled:
            logger.warning("Telegram controller disabled: TELEGRAM_BOT_TOKEN/TELEGRAM_USER_ID missing")
            return
        timeout = aiohttp.ClientTimeout(total=poll_seconds + 10)
        with ProcessLease(self.settings.state_dir / "telegram" / "poller.lock"):
            self._outbox_enabled = True
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    bus = self._event_bus
                    if bus is None and self._event_bus_factory is None:
                        bus = NatsJetStreamBus(self.settings)
                    tasks = [asyncio.create_task(coro) for coro in (
                        self._poll_loop(session, poll_seconds),
                        self._event_bridge_loop(session, bus),
                        self._crypto_health_loop(session),
                        self._market_health_loop(session),
                        self._mock_notifications_loop(session),
                        self._sender_loop(session),
                    )]
                    try:
                        await asyncio.gather(*tasks)
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        if bus is not None:
                            await bus.close()
            finally:
                self._outbox_enabled = False

    async def _poll_loop(self, session: aiohttp.ClientSession, poll_seconds: int) -> None:
        failures=0
        self._offset = max(self._offset, await asyncio.to_thread(self._state.offset))
        while True:
            for update in await asyncio.to_thread(self._state.pending):
                await self._handle_pending_update(session, update)
            try:
                response = await self._api(session, "getUpdates", {"timeout": poll_seconds, "offset": self._offset})
            except TelegramRateLimit as exc:
                await asyncio.sleep(exc.retry_seconds)
                continue
            except (asyncio.TimeoutError,aiohttp.ClientConnectionError) as exc:
                failures+=1
                logger.warning('Telegram polling will retry (%s)',type(exc).__name__)
                await asyncio.sleep(min(30,2**min(failures,5)))
                continue
            except aiohttp.ClientResponseError as exc:
                if exc.status!=429 and exc.status<500: raise
                failures+=1
                logger.warning('Telegram polling will retry status=%s',exc.status)
                await asyncio.sleep(min(30,2**min(failures,5)))
                continue
            failures=0
            for update in sorted(response.get("result", []), key=lambda row: row["update_id"]):
                await asyncio.to_thread(self._state.receive, update)
                self._offset = await asyncio.to_thread(self._state.offset)

    async def _handle_pending_update(self, session, update):
        failures = 0
        token = _reply_identity.set("reply:" + str(update["update_id"]))
        try:
            while True:
                try:
                    await self._handle(session, update)
                    await asyncio.to_thread(self._state.complete, update["update_id"])
                    return
                except (asyncio.TimeoutError, aiohttp.ClientError, OutboxFull, TransportError, OSError, sqlite3.Error) as exc:
                    failures += 1
                    logger.warning('Telegram reply deferred (%s)', type(exc).__name__)
                    await asyncio.sleep(min(30, 2**min(failures, 5)))
        finally:
            _reply_identity.reset(token)

    async def _event_bridge_loop(self, session, bus=None):
        while True:
            current = bus
            try:
                if self._event_bus_factory is not None:
                    candidate = self._event_bus_factory()
                    if inspect.isawaitable(candidate):
                        if inspect.iscoroutine(candidate):
                            candidate.close()
                        raise TypeError("bus_factory returned an awaitable")
                    current = candidate
                if current is None:
                    raise RuntimeError("event bridge has no configured bus")
                await current.connect(initialize=False)
                await self._event_bridge(session, current)
            except Exception as exc:
                logger.warning('Telegram event bridge reconnecting (%s)', type(exc).__name__)
            finally:
                if current is not None:
                    await current.close()
            await asyncio.sleep(10)

    async def _event_bridge(self, session: aiohttp.ClientSession, bus: NatsJetStreamBus) -> None:
        # Never consume the high-volume market-data subjects here.  A single
        # catch-all consumer can get buried behind ticks and then discard
        # collector lifecycle events as "old" before Telegram sees them.
        # Operational subjects have independent durable cursors so a market
        # burst cannot delay alerts or order/signal notifications.
        subjects = ("system.>", "signal.>", "order.>", "md.data_gap.>")
        tasks = [asyncio.create_task(self._event_bridge_subject(session, bus, subject,
                    f"telegram-event-bridge-{index}")) for index, subject in enumerate(subjects)]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _event_bridge_subject(self, session: aiohttp.ClientSession, bus: NatsJetStreamBus, subject: str, durable: str) -> None:
        async for message in bus.subscribe(subject, durable=durable, batch=100):
            with _event_reply_context(message):
                data = message.data
                provider = str(data.get('provider') or '')
                if provider.endswith('-perp') and provider not in configured_crypto_providers(self.settings):
                    await message.ack()
                    continue
                # A durable bridge may be recreated after a long outage.  Do not
                # replay historical market ticks into Telegram; storage consumers
                # retain the complete history in Parquet.
                kind = str(data.get("kind") or data.get("event_type") or "")
                if message.subject.startswith("md.trade.") or message.subject.startswith("md.order_book.") or message.subject.startswith("md.index_quote."):
                    # Market data is stored in Parquet, not pushed to Telegram.
                    await message.ack()
                    continue
                elif message.subject.startswith("md.data_gap"):
                    kind = "gap"
                elif message.subject.startswith("signal."):
                    kind = "signal"
                elif message.subject.startswith("order."):
                    destination = str(data.get("destination", "")).lower()
                    # Timefolio intents are operationally important even before
                    # submission; real-account notifications exclude blocked and
                    # pre-submit records.
                    if destination == "timefolio":
                        kind = "timefolio_intent"
                    elif destination == "real" and str(data.get("status", "")).lower() not in {"created", "blocked"}:
                        kind = "execution"
                    else:
                        await message.ack()
                        continue
                elif message.subject.startswith("system."):
                    kind = str(data.get("kind") or "system")
                    if kind == 'collector_start':
                        self._crypto_alerted.discard(provider)
                        self._crypto_alert_at.pop(provider, None)
                    if kind not in {"collector_error", "storage_error", "compactor_error"}:
                        await message.ack()
                        continue
                else:
                    await message.ack(); continue
                # Operational events must not be dropped merely because the
                # market-data bridge was behind. They are isolated now, so a
                # bounded replay window safely prevents stale deployment-era
                # errors from being delivered as current incidents.
                try:
                    ts_ns = int(data.get("created_ts_ns") or data.get("event_ts_ns") or data.get("ts_ns") or 0)
                except (ValueError, TypeError):
                    logger.warning("Malformed Telegram event timestamp subject=%s", message.subject)
                    await message.ack()
                    continue
                try:
                    max_age_seconds = max(60, int(self.settings.env.get("TELEGRAM_EVENT_MAX_AGE_SECONDS", "300")))
                except ValueError:
                    max_age_seconds = 300
                if ts_ns and time.time_ns() - ts_ns > max_age_seconds * 1_000_000_000:
                    await message.ack()
                    continue
                try:
                    provider = str(data.get("provider") or "")
                    if kind == "collector_error" and provider in configured_crypto_providers(self.settings):
                        await self._crypto_issue(session, provider, str(data.get("error") or "수집 오류"))
                    elif kind == 'collector_error' and provider in {'kis','kiwoom','toss'}:
                        await self._market_issue(session, provider, str(data.get('error') or data.get('reason') or '수집 오류'))
                    elif kind == "collector_start" and provider in configured_crypto_providers(self.settings):
                        await self._crypto_recovered(session, provider)
                    else:
                        await self._send(session, self.allowed_id, self._format_event(kind, data))
                except (OutboxFull, sqlite3.Error, OSError) as exc:
                    # ACK only after the local outbox owns the message. Local
                    # capacity/storage failures must retain the durable NATS copy.
                    logger.warning("Telegram event enqueue deferred subject=%s (%s)", message.subject, type(exc).__name__)
                    await message.nak(delay=30)
                    continue
                except (ValueError, TypeError, KeyError):
                    # Invalid content cannot be repaired by retrying the same
                    # event. Isolate it without stalling operational notifications.
                    logger.warning("Malformed Telegram event subject=%s", message.subject)
                except aiohttp.ClientResponseError as exc:
                    # Telegram allows only a small outbound rate.  A throttled
                    # notification is intentionally dropped; the market event is
                    # already durable in NATS/Parquet and should not block acks.
                    if exc.status != 429:
                        logger.warning("Telegram event push failed subject=%s status=%s", message.subject, exc.status)
                except Exception:
                    logger.exception("Telegram event push failed subject=%s", message.subject)
                await message.ack()

    async def _crypto_issue(self, session: aiohttp.ClientSession, provider: str, reason: str) -> None:
        if provider not in configured_crypto_providers(self.settings):
            return
        now = time.monotonic()
        if now - self._crypto_alert_at.get(provider, -1e9) < 300:
            return
        name = provider.split("-", 1)[0].title()
        await self._send(session, self.allowed_id, f"🚨 {name} 선물 수집 오류\n{reason[:180]}\n재연결 상태는 status로 확인")
        self._crypto_alerted.add(provider)
        self._crypto_alert_at[provider] = now

    async def _crypto_recovered(self, session: aiohttp.ClientSession, provider: str) -> None:
        if provider not in configured_crypto_providers(self.settings):
            self._crypto_alerted.discard(provider)
            self._crypto_alert_at.pop(provider,None)
            return
        self._crypto_alerted.discard(provider)
        self._crypto_alert_at.pop(provider, None)

    async def _crypto_health_loop(self, session: aiohttp.ClientSession) -> None:
        while True:
            try:
                group = health_check(self.settings, "crypto-collectors", 90)
                # An absent heartbeat means the optional crypto profile is off.
                if group.get("reason") != "heartbeat_missing":
                    for provider in configured_crypto_providers(self.settings):
                        if provider not in group.get("providers", []):
                            continue
                        worker = health_check(self.settings, f"collectors-{provider}", 90)
                        if float(group.get("age_seconds") or 0) > 90:
                            await self._crypto_issue(session, provider, "코인 수집기 응답 없음")
                        elif worker.get("state") == "error" or worker.get("phase") == "paused":
                            await self._crypto_issue(session, provider, str(worker.get("reason") or "수집 중단"))
                        elif float(worker.get("age_seconds") or 0) > 90:
                            await self._crypto_issue(session, provider, "수집 상태 갱신 중단")
                        elif worker.get("phase") == "publishing" and float(worker.get("event_age_seconds") or 0) <= 90:
                            await self._crypto_recovered(session, provider)
            except Exception:
                logger.exception("Telegram crypto health alert check failed")
            await asyncio.sleep(30)

    async def _market_issue(self, session, provider: str, reason: str) -> None:
        now = time.monotonic()
        if now-self._market_alert_at.get(provider, -1e9)<300:
            return
        await self._send(session, self.allowed_id, f'[COLLECTOR ERROR] provider={provider} reason={reason[:350]}')
        self._market_alert_at[provider] = now
        logger.info('Telegram collector alert delivered provider=%s', provider)

    @staticmethod
    def _expected_market_collectors() -> dict[str, bool]:
        equity = session_state('KR_EQUITY_ETF')[0]
        return {'kis':equity or session_state('KR_DERIVATIVES')[0], 'kiwoom':equity,
                'toss':equity or session_state('US_EQUITY_ETF')[0]}

    async def _market_health_once(self, session, expected: dict[str, bool]) -> None:
        now_ns, monotonic = time.time_ns(), time.monotonic()
        for provider, active in expected.items():
            if not active:
                self._market_unready_since.pop(provider, None)
                self._market_alert_at.pop(provider, None)
                continue
            worker = health_check(self.settings, 'collectors-'+provider, 120)
            age = float(worker.get('age_seconds') or 0)
            phase = worker.get('phase')
            reason = None
            if worker.get('reason') == 'heartbeat_missing' or age>120:
                reason = '장중 수집기 heartbeat 없음/갱신 중단'
            elif worker.get('state') == 'error' or phase in {'paused','retrying','error'}:
                reason = str(worker.get('reason') or '장중 수집 오류')
            else:
                seen = max([int(v) for v in (worker.get('symbol_last_seen_ns') or {}).values()] or [0])
                last = seen or int(worker.get('last_event_ts_ns') or 0)
                if last and now_ns-last>180_000_000_000:
                    received = int(worker.get('last_event_ts_ns') or 0)
                    if received and now_ns-received < 120_000_000_000:
                        reason = f'장중 시세 지연: 메시지는 수신되나 최신 시세 미갱신 ({int((now_ns-last)/1e9)}초)'
                    else:
                        reason = f'장중 실제 데이터 수신 중단 ({int((now_ns-last)/1e9)}초)'
                elif phase != 'publishing' or not last:
                    since = self._market_unready_since.setdefault(provider, monotonic)
                    if monotonic-since>=120:
                        reason = '개장 후 수집 미시작/실제 데이터 수신 없음'
                else:
                    self._market_unready_since.pop(provider, None)
                    self._market_alert_at.pop(provider, None)
            if reason:
                guard_path = self.settings.state_dir/'health'/'os-disk-guard.json'
                try:
                    guard = json.loads(guard_path.read_text(encoding='utf-8'))
                    if guard.get('paused'):
                        reason += f"; OS 디스크 보호 중지, 여유 {int(guard.get('free_bytes',0))/1024**3:.2f}GiB"
                except (OSError, ValueError, TypeError):
                    pass
                await self._market_issue(session, provider, reason)

    async def _market_health_loop(self, session) -> None:
        # Independent of both the producer process and the NATS event bridge:
        # a stopped process cannot publish its own collector_error event.
        while True:
            try:
                expected = await asyncio.to_thread(self._expected_market_collectors)
                await self._market_health_once(session, expected)
            except Exception:
                logger.exception('Telegram market collector health check failed')
            await asyncio.sleep(30)

    def _compact_status(self) -> str:
        now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%m/%d %H:%M")
        workers = {name: health_check(self.settings, name, 120 if name != "maintenance" else 420)
                   for name in ("collectors", "storage", "maintenance")}
        healthy = sum(bool(item.get("ok")) for item in workers.values())
        memory = psutil.virtual_memory()
        used_gib = (memory.total - memory.available) / 1024**3
        available_gib = memory.available / 1024**3
        disk = shutil.disk_usage(self.settings.data_root)
        crypto = health_check(self.settings, "crypto-collectors", 90)
        crypto_expected = crypto.get("reason") != "heartbeat_missing"
        profile_running = crypto_expected and float(crypto.get("age_seconds", 1e9)) <= 90
        if crypto_expected:
            crypto_settings = replace(self.settings, state_dir=self.settings.data_root / "crypto_state")
            crypto_storage = health_check(crypto_settings, "crypto-storage", 120)
            crypto_archive = health_check(crypto_settings, "maintenance", 420)
            crypto_services_ok = bool(crypto_storage.get("ok") and crypto_archive.get("ok"))
            crypto_service_line = (f"코인 저장 {'정상' if crypto_storage.get('ok') else '오류'}"
                                   f" · 보관 {'정상' if crypto_archive.get('ok') else '오류'}")
        else:
            crypto_services_ok = True
            crypto_service_line = "코인 저장·보관: 중지"
        venue_lines: list[str] = []
        live_symbols: set[str] = set()
        all_venues_collecting = True
        for provider in configured_crypto_providers(self.settings):
            venue = provider.split('-',1)[0].title()
            # One stale venue must not hide the other venues' live symbols.
            worker = health_check(self.settings, f"collectors-{provider}", 90)
            if worker.get('reason') == 'heartbeat_missing':
                label = "중지"
            elif float(worker.get('age_seconds', 1e9)) > 90:
                label = '응답 없음'
            elif worker.get('phase') == 'paused':
                label = '일시 중지'
            elif not worker.get("ok"):
                label = "오류" if worker.get("state") == "error" else "응답 없음"
            elif worker.get("phase") != "publishing":
                label = "연결 중"
            elif float(worker.get("event_age_seconds") or 0) > 90:
                label = "데이터 지연"
            else:
                seen = worker.get("symbol_last_seen_ns") or {}
                symbols = sorted(symbol for symbol, ts in seen.items()
                                 if symbol in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
                                 and time.time_ns() - int(ts) <= 120 * 1_000_000_000)
                live_symbols.update(symbols)
                label = ", ".join(symbols) if symbols else "연결됨 · 종목 확인 중"
            if label != "BTCUSDT, ETHUSDT, SOLUSDT":
                all_venues_collecting = False
            venue_lines.append(f"{venue}: {label}")
        overall = "정상" if healthy == 3 and available_gib >= 0.5 and crypto_services_ok and (not crypto_expected or (profile_running and all_venues_collecting)) else "주의"
        domestic_lines=[]
        from feeds.universe_config import configured_symbols
        for provider,label in [('kis','KIS'),('kiwoom','Kiwoom'),('toss','TOSS')]:
            worker=health_check(self.settings,'collectors-'+provider,120)
            phase=worker.get('phase','')
            fresh=sorted(s for s,ts in (worker.get('symbol_last_seen_ns') or {}).items()
                if time.time_ns()-int(ts)<=120_000_000_000)
            if phase=='market_closed' and worker.get('ok'): state='장 종료'
            elif not worker.get('ok'): state='오류/응답 없음'
            elif phase=='paused': state='일시 중지'
            elif fresh: state='수집 정상'
            elif phase in {'connecting','recovering'}: state='연결/복구 중'
            else: state='최근 수신 없음'
            if state not in {'수집 정상','장 종료'}: overall='주의'
            configured=', '.join(configured_symbols(provider)) or ('근월·차근월 선물' if provider=='kis' else '없음')
            domestic_lines.append(f'{label}: {state}\n  최근 수신: {", ".join(fresh) or "없음"}\n  설정: {configured}')
        return "\n".join((
            f"📊 서버 {overall} · {now} KST",
            f"서비스 {healthy}/3 정상 · 메모리 {used_gib:.1f}/{memory.total / 1024**3:.1f} GiB (가용 {available_gib:.1f})",
            f"저장공간 {(disk.total - disk.free) / 1024**3:.1f}/{disk.total / 1024**3:.1f} GiB",
            crypto_service_line,
            '국내·미국 수집기 (최근 120초 실제 수신):',
            *domestic_lines,
            "코인 무기한 선물:",
            *venue_lines,
            f"실제 수집 종목: {', '.join(sorted(live_symbols)) if live_symbols else '없음'}",
        ))

    def _pending_mock_notifications(self):
        path=self.settings.state_dir/'etf-mock'/'journal.sqlite3'
        return self._state.pending_notifications(path)

    def _mark_mock_notification(self, identity):
        self._state.delivered("mock:" + identity, identity)

    @staticmethod
    def _format_mock_notification(kind, data):
        side='매수' if data.get('side')=='buy' else '매도'
        if kind=='mock_order':
            price='시장가' if data['order_type']=='market' else f"지정가 {Decimal(data['price']):,.0f}원"
            return f"[KIS 모의 주문 접수 · {data.get('account_scope','mock')}]\n{data['strategy']} · {data['symbol']} {side} {data['quantity']}주\n{price}\n주문번호 {data['order_number']}"
        if kind=='mock_fill':
            state='전량 체결' if data['complete'] else '부분 체결'
            return f"[KIS 모의 {state} · {data.get('account_scope','mock')}]\n{data['strategy']} · {data['symbol']} {side} {data['filled_quantity']}주\n체결가 {Decimal(data['fill_price']):,.2f}원\n누적 {data['total_filled']}/{data['ordered_quantity']}주 · 주문번호 {data['order_number']}"
        if kind=='mock_assumed':
            stamp=datetime.fromtimestamp(data['event_ns']/1e9,ZoneInfo('Asia/Seoul')).strftime('%m/%d %H:%M:%S') if data.get('event_ns') else ''
            return f"[전략 평가용 가정 체결 · 실제 체결 아님]\n{data['strategy']} · {data['symbol']} {side} {data['quantity']}주\n가정 시각 {stamp} KST · Best quote {Decimal(data['price']):,.0f}원\n{data['reason']}"
        if kind=='mock_reconstructed':
            stamp=datetime.fromtimestamp(data['event_ns']/1e9,ZoneInfo('Asia/Seoul')).strftime('%m/%d %H:%M:%S')
            return (f"[장애 복구 모의 체결 · 가정]\n{data['strategy']} · {data['symbol']} {side} {data['quantity']}주\n"
                    f"신호·가정 체결 {stamp} KST · {Decimal(data['price']):,.0f}원\n"
                    f"가격 기준: {data['price_basis']}\nKIS 소급 체결과 구분하여 기록")
        if kind=='mock_rejected':
            return f"[KIS 모의 주문 거절]\n{data['strategy']} · {data['symbol']} {side} {data['quantity']}주\n{data['reason']}"
        if kind=='mock_daily':
            fees='조회 불가' if data.get('fees') is None else f"{Decimal(data['fees']):,.0f}원"
            positions=', '.join(f'{s} {q}주' for s,q in data['positions'].items()) or '없음'
            text=f"[KIS 모의 장 마감 보고 · {data['day']}]\n실제 계좌 순수익 {Decimal(data['net_pnl']):+,.0f}원\n수수료·제비용(KIS) {fees}\n계좌 평가액 {Decimal(data['equity']):,.0f}원\n미체결 {data['pending_orders']}건 · 보유 {positions}\n순수익 기준: 전일 대비 계좌 평가액 증감(보유 시 평가손익 포함)"
            if data.get('recovery'):
                text=text.replace('실제 계좌 순수익','보고 대상 실제 순수익')
            for scope,account in data.get('accounts',{}).items():
                text+=f"\n{scope} 순수익 {Decimal(account['net_pnl']):+,.0f}원"
            assumed=data.get('assumed')
            if assumed:
                label='복구·권한 거절 가정'if data.get('recovery')else'권한 거절 가정'
                text+=f"\n{label} 순수익 {Decimal(assumed['net_pnl']):+,.0f}원 ({assumed['fill_count']}건)\n가정 수수료(추정) {Decimal(assumed['fees']):,.2f}원\n보정 순수익(실제+가정) {Decimal(data['adjusted_net_pnl']):+,.0f}원"
                if data.get('adjusted_fees') is not None: text+=f"\n합산 수수료·제비용 {Decimal(data['adjusted_fees']):,.2f}원"
            if data.get('validation'):
                text+=f"\n실제 합계에 포함된 1주 검증 손익 {Decimal(data['validation']['net_pnl']):+,.0f}원 · 수수료 {Decimal(data['validation']['fees']):,.0f}원"
            if data.get('test_trades_excluded'):
                text+='\n사용자 지정 테스트 거래의 손익·수수료 제외'
            return text
        raise ValueError('Unknown mock notification kind')

    async def _deliver_mock_notifications(self, session):
        for row in await asyncio.to_thread(self._pending_mock_notifications):
            data = await asyncio.to_thread(daily_report_view,self.settings,row['payload']) if row['kind']=='mock_daily' else row['payload']
            text = self._format_mock_notification(row['kind'],data)
            if self._outbox_enabled:
                await asyncio.to_thread(self._state.enqueue, "mock:"+row['id'], self.allowed_id, text, row['id'])
            else:
                await self._send(session,self.allowed_id,text)
                await asyncio.to_thread(self._mark_mock_notification,row['id'])

    async def _mock_notifications_loop(self, session):
        while True:
            try: await self._deliver_mock_notifications(session)
            except Exception as exc:
                logger.warning('Mock account notifications deferred; unsent records retained (%s)',type(exc).__name__)
            await asyncio.sleep(1)

    async def _session_loop(self, session: aiohttp.ClientSession) -> None:
        # Market open/close is normal operation and never pushes a notice.
        while True:
            await asyncio.sleep(20)

    @staticmethod
    def _format_event(kind: str, data: dict[str, Any]) -> str:
        if kind == "gap":
            return f"[DATA GAP] provider={data.get('provider')} instrument={data.get('instrument', {}).get('std_symbol', '?')} reason={data.get('reason', '')} seq={data.get('missing_from_sequence')}..{data.get('missing_to_sequence')}"
        if kind == "signal":
            text = f"[SIGNAL · 체결 아님] {data.get('strategy_id')} {data.get('action')}\n기준 종목: {data.get('instrument', {}).get('std_symbol', '?')}"
            weights = data.get('metadata',{}).get('weights',{})
            targets = ', '.join(f'{symbol} ({Decimal(str(weight))*100:g}%)' for symbol,weight in weights.items() if Decimal(str(weight))>0)
            return text + (f'\n주문 대상: {targets}' if targets else '') + '\n주문 접수·체결 여부는 별도 알림과 trade에서 확인'
        if kind == "timefolio_intent":
            return f"[TIMEFOLIO INTENT] job={data.get('job_id')} {data.get('side')} {data.get('instrument', {}).get('std_symbol', '?')} status={data.get('status')}"
        if kind == "execution":
            return f"[REAL ORDER] job={data.get('job_id')} {data.get('side')} {data.get('instrument', {}).get('std_symbol', '?')} status={data.get('status')} client={data.get('client_order_id')}"
        if kind == "collector_start":
            return f"[COLLECTOR START] provider={data.get('provider')} symbols={data.get('symbols') or 'auto'}"
        if kind == "collector_stop":
            suffix = f" reason={data.get('reason')}" if data.get("reason") else ""
            return f"[COLLECTOR STOP] provider={data.get('provider')} symbols={data.get('symbols') or 'auto'}{suffix}"
        reason = data.get("error") or data.get("message") or data.get("kind", "")
        cause = f" cause={data.get('cause')}" if data.get("cause") else ""
        failures = data.get("consecutive_failures")
        outage = data.get("outage_seconds")
        state = f" failures={failures} outage={outage}s" if failures is not None else ""
        return f"[COLLECTOR ERROR] provider={data.get('provider', '')} reason={reason}{cause}{state}"

    async def _handle(self, session: aiohttp.ClientSession, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        sender = (message.get("from") or {}).get("id")
        chat_id = message.get("chat", {}).get("id")
        if (type(sender) is not int or type(chat_id) is not int
                or sender not in self.allowed_users or chat_id not in self.allowed_chats):
            return
        text = str(message.get("text") or "").strip().lower()
        command = text.split(maxsplit=1)[0].split('@', 1)[0].lstrip('/') if text else ''
        if command == 'status':
            try:
                reply = await asyncio.to_thread(self._compact_status)
            except Exception as exc:
                logger.warning('Collector status unavailable (%s)', type(exc).__name__)
                reply = '수집기 상태 조회 실패. 서버 상태 파일을 확인해야 합니다.'
            if (self._projection_reader is not None
                    or 'PAPER_PROJECTION_DATABASES' in self.settings.env):
                summary = await self._projection_reply('status')
                addition = '\npaper: ' + summary
                if len((reply + addition).encode('utf-8')) <= 3500:
                    reply += addition
                else:
                    reply += '\npaper: UNAVAILABLE response_budget_exceeded'
            await self._send(session, chat_id, reply)
            logger.info('Telegram command replied command=status')
        elif command == 'trade':
            try:
                reply = await asyncio.to_thread(trade_report, self.settings)
            except Exception as exc:
                logger.warning('Trade report unavailable (%s)', type(exc).__name__)
                reply = '거래 내역 조회 실패. 거래 기록을 확인해야 합니다.'
            await self._send(session, chat_id, reply)
            logger.info('Telegram command replied command=trade')
        elif command in {'feeds', 'strategies', 'orders', 'positions', 'pnl'}:
            # Reporting is a cold read. It never shares the owner command path
            # and a reader failure cannot create, expire or acknowledge control.
            reply = await self._projection_reply(command)
            await self._send(session, chat_id, reply)
            logger.info('Telegram command replied command=%s', command)
        elif command in {'help', 'start'}:
            await self._send(session, chat_id, 'status: 수집기별 상태·수집 종목\ntrade: 당일 종목별 거래·당일 및 전체 기록기간 순수익·수수료\nfeeds: 로컬 시세 스냅샷\nstrategies: Paper 전략\norders: Paper 주문\npositions: Paper 포지션\npnl: Paper 통화별 손익')
        elif command in {'pause', 'halt', 'resume'}:
            await self._control_command(session, update, command, chat_id)

    def _read_projection(self, command: str) -> dict:
        """Synchronous cold read used off the event loop by _projection_reply."""
        if command == 'feeds':
            now = time.time_ns()
            try:
                path, budgets = local_snapshot_config(self.settings.env)
                document = read_market_snapshot(path, budgets)
            except Exception:
                return {"command": "feeds", "status": "UNAVAILABLE", "mode": "LOCAL_MARKET_SNAPSHOT",
                        "evidence": "MARKET_SNAPSHOT", "freshness": "UNAVAILABLE",
                        "read_at_ns": now, "fact_asof_ns": None, "engine": {}, "markets": [],
                        "errors": [{"source": "local_market_snapshot", "type": "UNAVAILABLE",
                                    "reason": "snapshot_unavailable"}]}
            engine = document.get("engine") if isinstance(document.get("engine"), dict) else {}
            feeds = []
            raw_feeds = engine.get("feeds") if isinstance(engine.get("feeds"), dict) else {}
            for name, item in raw_feeds.items():
                if not isinstance(item, dict):
                    continue
                feeds.append({"name": name, "provider": item.get("provider"),
                              "mode": item.get("mode"), "reason": item.get("reason"),
                              "generation": item.get("generation"),
                              "applied_sequence": item.get("applied_sequence")})
            markets = []
            for row in document.get("markets") or []:
                if not isinstance(row, dict):
                    continue
                compact = {key: row.get(key) for key in
                           ("key", "venue", "symbol", "provider", "asset_class", "currency",
                            "section", "price", "last_event_ms", "session", "book_valid",
                            "gap_reason", "quote_kind")}
                book = row.get("book")
                if isinstance(book, dict):
                    for side in ("bids", "asks"):
                        levels = book.get(side)
                        compact[side[:-1]] = (levels[0][0] if isinstance(levels, list) and levels
                                              and isinstance(levels[0], list) and levels[0] else None)
                markets.append(compact)
            age = max(0, now - int(document.get("generated_ns") or 0))
            freshness = "FRESH" if age <= budgets.max_age_seconds * 1_000_000_000 else "STALE"
            return {"command": "feeds", "status": "OK", "mode": "LOCAL_MARKET_SNAPSHOT",
                    "evidence": "MARKET_SNAPSHOT", "freshness": freshness,
                    "read_at_ns": now, "fact_asof_ns": document.get("generated_ns"),
                    "engine": {"incarnation": engine.get("incarnation"),
                               "apply_seq": engine.get("apply_seq"),
                               "healthy": engine.get("healthy"), "feeds": feeds},
                    "markets": markets, "errors": []}
        try:
            paths = paper_projection_paths(self.settings.env)
        except Exception:
            return {"command": command, "status": "UNAVAILABLE", "mode": "PAPER_SIMULATION",
                    "evidence": "PAPER_SIMULATION", "freshness": "UNAVAILABLE",
                    "read_at_ns": time.time_ns(), "fact_asof_ns": None,
                    "accounts": [], "orders": [], "strategies": [], "positions": [],
                    "pnl": [], "errors": [{"type": "UNAVAILABLE", "reason": "config_invalid"}]}
        if not paths:
            return {"command": command, "status": "UNAVAILABLE", "mode": "PAPER_SIMULATION",
                    "evidence": "PAPER_SIMULATION", "freshness": "UNAVAILABLE",
                    "read_at_ns": time.time_ns(), "fact_asof_ns": None,
                    "accounts": [], "orders": [], "strategies": [], "positions": [],
                    "pnl": [], "errors": [{"type": "UNAVAILABLE", "reason": "not_configured"}]}
        try:
            document = paper_projection(paths, limit=500)
        except Exception:
            return {"command": command, "status": "UNAVAILABLE", "mode": "UNAVAILABLE",
                    "evidence": "UNAVAILABLE", "freshness": "UNAVAILABLE",
                    "read_at_ns": time.time_ns(), "fact_asof_ns": None,
                    "accounts": [], "orders": [], "strategies": [], "positions": [],
                    "pnl": [], "errors": [{"type": "UNAVAILABLE", "reason": "unavailable"}]}
        result = dict(document)
        result["command"] = command
        result["status"] = ("PARTIAL" if result.get("accounts") else "UNAVAILABLE") if result.get("errors") else "OK"
        return result

    async def _projection_reply(self, command: str) -> str:
        """Bounded JSON reply; reader work never runs on the event-loop thread."""
        def unavailable(reason: str) -> str:
            return json.dumps({"command": command, "status": "UNAVAILABLE", "mode": "UNAVAILABLE",
                "evidence": "UNAVAILABLE", "freshness": "UNAVAILABLE",
                "read_at_ns": time.time_ns(), "fact_asof_ns": None,
                "errors": [{"type": "UNAVAILABLE", "reason": reason}]},
                ensure_ascii=False, allow_nan=False, separators=(",", ":"))

        def text(value):
            return value if isinstance(value, str) and len(value) <= 512 else None

        def clean(row, fields):
            if not isinstance(row, dict):
                return None
            return {key: row.get(key) for key in fields}

        reader = self._projection_reader or self._read_projection
        try:
            document = await asyncio.to_thread(reader, command)
        except asyncio.CancelledError:
            raise
        except Exception:
            return unavailable("reader_failed")
        if not isinstance(document, dict):
            return unavailable("reader_failed")

        safe_reasons = {"missing", "nonpaper", "schema", "corrupt_or_schema", "row_limit",
                        "unavailable", "value", "not_configured", "config_invalid",
                        "snapshot_unavailable", "response_budget_exceeded", "reader_failed"}
        errors = []
        raw_errors = document.get("errors")
        if isinstance(raw_errors, list):
            for item in raw_errors[:32]:
                if not isinstance(item, dict):
                    continue
                reason = item.get("reason")
                error = {"type": "UNAVAILABLE",
                         "reason": reason if isinstance(reason, str) and reason in safe_reasons
                         else "unavailable"}
                source = text(item.get("source"))
                if source is not None:
                    error["source"] = source
                errors.append(error)

        response = {
            "command": text(document.get("command")) or command,
            "status": document.get("status") if document.get("status") in ("OK", "PARTIAL", "UNAVAILABLE") else "OK",
            "mode": text(document.get("mode")) or "UNAVAILABLE",
            "evidence": text(document.get("evidence")) or "UNAVAILABLE",
            "freshness": text(document.get("freshness")) or "UNVERIFIED",
            "fact_asof_ns": document.get("fact_asof_ns"),
            "read_at_ns": document.get("read_at_ns"),
        }
        if command != "feeds":
            accounts = document.get("accounts", [])
            if not isinstance(accounts, list):
                return unavailable("reader_failed")
            response["recovery"] = "UNRECONCILED" if any(
                isinstance(row, dict) and row.get("recovery") == "UNRECONCILED"
                for row in accounts) else "UNVERIFIED"
        if command == "status":
            for section in ("accounts", "orders", "strategies", "positions", "pnl"):
                rows = document.get(section)
                response[section + "_count"] = len(rows) if isinstance(rows, list) else 0
            response["errors"] = errors
        elif command == "feeds":
            engine = document.get("engine") if isinstance(document.get("engine"), dict) else {}
            response["engine"] = {
                "incarnation": text(engine.get("incarnation")),
                "apply_seq": engine.get("apply_seq"),
                "healthy": engine.get("healthy") if isinstance(engine.get("healthy"), bool) else None,
                "feeds": [clean(item, ("name", "provider", "mode", "reason", "generation",
                                       "applied_sequence"))
                          for item in engine.get("feeds") or [] if isinstance(item, dict)][:32],
            }
            response["markets"] = [clean(row, ("key", "venue", "symbol", "provider", "asset_class",
                                               "currency", "section", "price", "last_event_ms",
                                               "session", "book_valid", "gap_reason", "quote_kind",
                                               "bid", "ask"))
                                   for row in document.get("markets") or [] if isinstance(row, dict)][:128]
            response["errors"] = errors
        elif command in {"strategies", "orders", "positions", "pnl"}:
            fields = {
                "strategies": ("id", "name", "strategy", "account", "mode", "evidence",
                               "running", "status", "recovery", "updated_ms"),
                "orders": ("id", "intent_id", "strategy", "account", "section", "mode", "evidence",
                           "instrument", "symbol", "venue", "asset_class", "side", "quantity",
                           "filled", "reservation", "reserve_currency", "currency", "status",
                           "recovery", "created_ms", "updated_ms"),
                "positions": ("category", "strategy", "account", "instrument", "symbol", "venue",
                              "asset_class", "quantity", "average", "margin", "currency", "mode",
                              "evidence", "recovery"),
                "pnl": ("strategy", "account", "currency", "realized_pnl", "fees", "funding",
                        "unrealized_pnl", "unrealized_pnl_status", "unrealized_pnl_reason",
                        "mark", "mark_asof", "mode", "evidence", "recovery"),
            }[command]
            rows = document.get(command)
            if not isinstance(rows, list):
                return unavailable("reader_failed")
            cleaned = [clean(row, fields) for row in rows if isinstance(row, dict)]
            if command == "pnl":
                for row in cleaned:
                    row.setdefault("unrealized_pnl", None)
                    row.setdefault("mark", None)
                    if row.get("unrealized_pnl_status") is None:
                        row["unrealized_pnl_status"] = "UNAVAILABLE"
            response[command] = cleaned
            response["errors"] = errors
        else:
            return unavailable("reader_failed")

        try:
            encoded = json.dumps(response, ensure_ascii=False, allow_nan=False,
                                 separators=(",", ":"))
        except (TypeError, ValueError):
            return unavailable("reader_failed")
        if len(encoded.encode("utf-8")) > 3500:
            return unavailable("response_budget_exceeded")
        return encoded

    async def _control_command(self, session, update, action, chat_id):
        if self._command_handler is None:
            await self._send(session, chat_id, '제어 owner가 연결되지 않았습니다.')
            return
        try:
            message = update['message']
            date, identity = message.get('date'), update.get('update_id')
            if (type(date) is not int or date <= 0 or date * 1_000_000_000 > time.time_ns()
                    or type(identity) is not int or identity < 0):
                raise ValueError('Invalid command identity or original timestamp')
            fields = message['text'].strip().split(maxsplit=3)
            if (len(fields) != (4 if action == 'resume' else 3) or len(fields[1]) > 128
                    or fields[2] not in {'global','account','strategy'}):
                raise ValueError('Explicit strategy and scope required')
            request = dict(command_id=f'telegram:{chat_id}:{identity}', action=action,
                strategy=fields[1], scope=fields[2], expires_ns=(date + self._command_ttl_seconds)*1_000_000_000,
                expected_epochs=json.loads(fields[3], object_pairs_hook=_object_pairs) if action == 'resume' else None)
            if action == 'resume' and (type(request['expected_epochs']) is not dict
                    or len(request['expected_epochs']) != 3
                    or any(len(k) > 208 or type(v) is not int or v < 0 for k,v in request['expected_epochs'].items())):
                raise ValueError('Full epoch vector required')
            request = await asyncio.to_thread(self._state.command_request, update, request)
        except (ValueError, TypeError, KeyError, TransportError):
            await self._send(session, chat_id, '명령 형식·원래 시각·ID를 확인하세요. resume에는 전체 epoch JSON이 필요합니다.')
            return
        result = validate_command_result(request, await self._command_handler(request))
        text = ('명령 거절: owner의 만료·범위·준비 상태를 확인하세요.' if result['status'] == 'REJECTED' else
                f"명령 ACCEPTED: {result['command_id']} · {result['scope']} · epoch {result['epoch']} (과거 처리 결과; 현재 권한 아님)")
        await self._send(session, chat_id, text)

    async def _send(self, session: aiohttp.ClientSession, chat_id: Any, text: str) -> None:
        if self._outbox_enabled:
            identity = _reply_identity.get() or "notice:" + uuid.uuid4().hex
            await asyncio.to_thread(self._state.enqueue, identity, str(chat_id), text)
            return
        await self._send_direct(session, chat_id, text)

    async def _sender_loop(self, session) -> None:
        while True:
            row = await asyncio.to_thread(self._state.next_message)
            if row is None:
                await asyncio.sleep(.5)
                continue
            try:
                await self._send_direct(session, row['chat_id'], row['text'])
            except (asyncio.TimeoutError, aiohttp.ClientError, RuntimeError) as exc:
                delay = min(300, 2**min(row['attempts']+1, 8))
                if isinstance(exc, TelegramRateLimit):
                    delay = max(delay, exc.retry_seconds)
                if isinstance(exc, aiohttp.ClientResponseError) and exc.status == 429:
                    try:
                        delay = max(delay, float((exc.headers or {}).get('Retry-After', delay)))
                    except (ValueError, TypeError):
                        pass
                await asyncio.to_thread(self._state.retry, row['identity'], delay)
                logger.warning('Telegram outbound delivery retained (%s)', type(exc).__name__)
                continue
            await asyncio.to_thread(self._state.delivered, row['identity'], row['notification_id'])

    async def _send_direct(self, session: aiohttp.ClientSession, chat_id: Any, text: str) -> None:
        # Split at line boundaries so trade rows and summary lines are retained.
        parts = []
        for line in text.splitlines():
            while len(line) > 3900:
                parts.append(line[:3900]); line = line[3900:]
            if parts and len(parts[-1]) + len(line) + 1 <= 3900:
                parts[-1] += '\n' + line
            else:
                parts.append(line)
        for part in parts:
            await self._api(session, "sendMessage", {"chat_id": chat_id, "text": part})

    async def _api(self, session: aiohttp.ClientSession, method: str, params: dict[str, Any]) -> dict[str, Any]:
        async with session.get(f"{self.base_url}/{method}", params=params) as response:
            if response.status == 429:
                try:
                    data = await response.json()
                    delay = float(data.get("parameters", {}).get("retry_after", 30))
                except (ValueError, TypeError, aiohttp.ClientError):
                    delay = 30
                raise TelegramRateLimit(delay)
            response.raise_for_status()
            payload = await response.json()
            if not payload.get("ok"):
                raise RuntimeError(f"Telegram API failed method={method} code={payload.get('error_code')}")
            return payload
