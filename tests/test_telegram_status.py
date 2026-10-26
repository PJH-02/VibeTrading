from __future__ import annotations

import asyncio
import time
import aiohttp
import pytest
from dataclasses import replace
from types import SimpleNamespace

from vibetrading.health import touch
from vibetrading.health import check
from vibetrading.settings import RuntimeSettings
from vibetrading.telegram import TelegramController


@pytest.mark.asyncio
@pytest.mark.parametrize('error',[asyncio.TimeoutError(),aiohttp.ClientConnectionResetError('closing transport'),
    aiohttp.ClientResponseError(SimpleNamespace(real_url='https://example.test'),(),status=502)])
async def test_poll_timeout_does_not_restart_bot_or_lose_offset(tmp_path,monkeypatch,error):
    bot=_controller(tmp_path); calls=[]; handled=[]; delays=[]
    async def api(session,method,params):
        calls.append(dict(params))
        if len(calls)==1: raise error
        if len(calls)==2: return {'result':[{'update_id':42}]}
        raise asyncio.CancelledError()
    async def handle(session,update): handled.append(update)
    async def sleep(delay): delays.append(delay)
    bot._api=api; bot._handle=handle
    monkeypatch.setattr('vibetrading.telegram.asyncio.sleep',sleep)
    with pytest.raises(asyncio.CancelledError): await bot._poll_loop(None,25)
    assert handled==[{'update_id':42}] and calls[-1]['offset']==43
    assert delays==[2]


def _controller(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n"
        "TELEGRAM_BOT_TOKEN=test-token\nTELEGRAM_USER_ID=123\n",
        encoding="utf-8",
    )
    # Container environment variables must never redirect test heartbeat
    # writes to the production data volume or supply real bot credentials.
    settings=RuntimeSettings.from_env(env)
    settings=replace(settings,data_root=tmp_path,state_dir=tmp_path/'state',telegram_user_id='123',
        env={'TELEGRAM_BOT_TOKEN':'test-token','TELEGRAM_USER_ID':'123',
             'CRYPTO_COLLECTOR_PROVIDERS':'binance-perp,arcus-perp,grvt-perp'})
    for provider in ('kis','kiwoom','toss'):
        touch(settings,'collectors-'+provider,detail={'phase':'market_closed'})
    return TelegramController(settings)


def test_status_does_not_claim_configured_crypto_is_collecting(tmp_path):
    bot = _controller(tmp_path)
    for name in ("collectors", "storage", "maintenance"):
        touch(bot.settings, name, detail={"providers": ["binance"]} if name == "collectors" else {})
    touch(bot.settings, "collectors-binance", detail={"phase": "publishing"})
    message = bot._compact_status()
    assert "서비스 3/3 정상" in message
    assert "메모리" in message and "저장공간" in message
    assert "Binance: 중지" in message
    assert "Arcus: 중지" in message
    assert "Grvt: 중지" in message
    assert "실제 수집 종목: 없음" in message
    assert len(message) < 2000


def test_status_reports_only_recent_symbols(tmp_path):
    bot = _controller(tmp_path)
    for name in ("collectors", "storage", "maintenance"):
        touch(bot.settings, name, detail={"providers": ["binance"]} if name == "collectors" else {})
    touch(bot.settings, "collectors-binance", detail={"phase": "publishing"})
    touch(bot.settings, "crypto-collectors", detail={"providers": ["binance-perp", "arcus-perp", "grvt-perp"]})
    now = time.time_ns()
    touch(bot.settings, "collectors-binance-perp", detail={"phase": "publishing", "symbol_last_seen_ns": {"BTCUSDT": now}})
    touch(bot.settings, "collectors-arcus-perp", detail={"phase": "publishing", "symbol_last_seen_ns": {"ETHUSDT": now - 180_000_000_000}})
    touch(bot.settings, "collectors-grvt-perp", state="error", detail={"phase": "retrying"})
    message = bot._compact_status()
    assert "Binance: BTCUSDT" in message
    assert "📊 서버 주의" in message
    assert "Arcus: 연결됨 · 종목 확인 중" in message
    assert "Grvt: 오류" in message
    assert "실제 수집 종목: BTCUSDT" in message
    assert "ETHUSDT" not in message


def test_stale_exchange_does_not_hide_other_live_exchanges(tmp_path):
    import json
    bot=_controller(tmp_path)
    for provider in ('binance-perp','arcus-perp','grvt-perp'):
        touch(bot.settings,'collectors-'+provider,detail={'phase':'publishing','symbol_last_seen_ns':{'BTCUSDT':time.time_ns()}})
    path=bot.settings.state_dir/'health/collectors-grvt-perp.json'
    data=json.loads(path.read_text()); data['ts_ns']=time.time_ns()-180_000_000_000
    path.write_text(json.dumps(data),encoding='utf-8')
    message=bot._compact_status()
    assert 'Binance: BTCUSDT' in message and 'Arcus: BTCUSDT' in message
    assert 'Grvt: 응답 없음' in message


def test_status_contains_all_domestic_and_active_crypto_collectors(tmp_path):
    bot=_controller(tmp_path)
    now=time.time_ns()
    touch(bot.settings,'collectors-kis',detail={'phase':'publishing','symbol_last_seen_ns':{'101V12':now}})
    touch(bot.settings,'collectors-kiwoom',detail={'phase':'publishing',
        'symbol_last_seen_ns':{'069500':now,'122630':now-180_000_000_000}})
    touch(bot.settings,'collectors-toss',state='error',detail={'phase':'retrying'})
    message=bot._compact_status()
    for label in ('KIS:','Kiwoom:','TOSS:','Binance:','Arcus:','Grvt:'):
        assert label in message
    assert 'KIS: 수집 정상\n  최근 수신: 101V12' in message
    assert 'Kiwoom: 수집 정상\n  최근 수신: 069500\n' in message
    assert 'TOSS: 오류/응답 없음' in message
    assert 'Bitget' not in message and 'Bybit' not in message


def test_status_command_and_crypto_alert_deduplication(tmp_path):
    bot = _controller(tmp_path)
    sent = []

    async def fake_send(_session, chat_id, message):
        sent.append((chat_id, message))

    bot._send = fake_send

    async def exercise():
        await bot._handle(None, {"message": {"from": {"id": 123}, "chat": {"id": 123}, "text": "status"}})
        await bot._crypto_issue(None, "arcus-perp", "WebSocket error")
        await bot._crypto_issue(None, "arcus-perp", "WebSocket error")
        await bot._crypto_recovered(None, "arcus-perp")

    asyncio.run(exercise())
    assert sent[0][1].startswith("📊 서버")
    assert len(sent) == 2
    assert "Arcus 선물 수집 오류" in sent[1][1]
    assert not bot._crypto_alerted


def test_status_warns_when_crypto_storage_is_down(tmp_path, monkeypatch):
    # Exercise storage state independently of the test PC's available RAM.
    monkeypatch.setattr('vibetrading.telegram.psutil.virtual_memory',
                        lambda: SimpleNamespace(total=4 * 1024**3, available=2 * 1024**3))
    bot = _controller(tmp_path)
    for name in ("collectors", "storage", "maintenance"):
        touch(bot.settings, name, detail={"providers": ["binance"]} if name == "collectors" else None)
    touch(bot.settings, "collectors-binance")
    providers = ("binance-perp", "arcus-perp", "grvt-perp")
    touch(bot.settings, "crypto-collectors", detail={"providers": providers})
    seen = {symbol: time.time_ns() for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")}
    for provider in providers:
        touch(bot.settings, f"collectors-{provider}", detail={"phase": "publishing", "symbol_last_seen_ns": seen})
    assert "📊 서버 주의" in bot._compact_status()
    assert "코인 저장 오류 · 보관 오류" in bot._compact_status()

    crypto_settings = replace(bot.settings, state_dir=bot.settings.data_root / "crypto_state")
    touch(crypto_settings, "crypto-storage")
    touch(crypto_settings, "maintenance")
    assert "📊 서버 정상" in bot._compact_status()


def test_retired_heartbeats_and_legacy_group_do_not_trigger_errors(tmp_path,monkeypatch):
    import json
    monkeypatch.setattr('vibetrading.telegram.psutil.virtual_memory',
        lambda:SimpleNamespace(total=4*1024**3,available=2*1024**3))
    bot=_controller(tmp_path)
    for name in ('collectors','storage','maintenance','collectors-binance'):
        touch(bot.settings,name,detail={'providers':['binance']} if name=='collectors' else {})
    for provider in ('binance-perp','arcus-perp','grvt-perp'):
        touch(bot.settings,'collectors-'+provider,detail={'phase':'publishing',
            'symbol_last_seen_ns':{s:time.time_ns() for s in ('BTCUSDT','ETHUSDT','SOLUSDT')}})
    touch(bot.settings,'crypto-collectors',detail={'providers':['binance-perp','bitget-perp','bybit-perp']})
    for provider in ('bitget-perp','bybit-perp'):
        touch(bot.settings,'collectors-'+provider,state='error')
        path=bot.settings.state_dir/'health'/('collectors-'+provider+'.json')
        data=json.loads(path.read_text()); data['ts_ns']=time.time_ns()-30_000_000_000_000
        path.write_text(json.dumps(data))
    crypto_settings=replace(bot.settings,state_dir=bot.settings.data_root/'crypto_state')
    touch(crypto_settings,'crypto-storage'); touch(crypto_settings,'maintenance')
    group=check(bot.settings,'crypto-collectors')
    assert group['ok'] and group['providers']==['binance-perp','arcus-perp','grvt-perp']
    message=bot._compact_status()
    assert '📊 서버 정상' in message and 'Bitget' not in message and 'Bybit' not in message
    sent=[]
    async def send(*args): sent.append(args)
    bot._send=send
    async def exercise():
        await bot._crypto_issue(None,'bitget-perp','stale heartbeat')
        await bot._crypto_issue(None,'bybit-perp','stale heartbeat')
    asyncio.run(exercise())
    assert sent==[]


def test_retired_lifecycle_event_is_acked_without_notification(tmp_path):
    bot=_controller(tmp_path)
    sent=[]; acked=[]
    async def send(*args): sent.append(args)
    async def ack(): acked.append(True)
    bot._send=send
    class Bus:
        async def subscribe(self,*args,**kwargs):
            yield SimpleNamespace(subject='system.collector_error',data={'kind':'collector_error',
                'provider':'bybit-perp','created_ts_ns':time.time_ns(),'error':'stopped'},ack=ack)
    asyncio.run(bot._event_bridge_subject(None,Bus(),'system.>','test'))
    assert acked==[True] and sent==[]
