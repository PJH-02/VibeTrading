import asyncio
import json
import time
from types import SimpleNamespace

import aiohttp
import pytest

from test_telegram_status import _controller
from vibetrading.health import touch
from feeds.providers.kiwoom import KiwoomAuthClient, KiwoomCollector


def client(tmp_path):
    c = KiwoomAuthClient(_controller(tmp_path).settings)
    c.app_key, c.app_secret = 'dummy-key', 'dummy-secret'
    return c


def test_token_cache_is_bound_to_credentials(tmp_path):
    c = client(tmp_path)
    c._write_token_cache('old', '20400101000000')
    assert c._read_token_cache() == 'old'
    c.app_secret = 'rotated-secret'
    assert c._read_token_cache() is None


@pytest.mark.asyncio
async def test_rest_and_ws_share_one_refresh(tmp_path):
    c = client(tmp_path); issued = []
    async def issue():
        cached = c._read_token_cache()
        if cached: return cached
        issued.append(True)
        await asyncio.sleep(.01)
        c._write_token_cache('fresh', '20400101000000')
        return 'fresh'
    c._access_token_locked = issue
    assert await asyncio.gather(*[c.access_token() for _ in range(6)]) == ['fresh']*6
    assert len(issued) == 1


@pytest.mark.asyncio
async def test_rejected_read_token_is_refreshed_once_and_pagination_retained(tmp_path):
    c = client(tmp_path); calls = []
    c._write_token_cache('rejected', '20400101000000')
    async def issue():
        token = c._read_token_cache()
        if token: return token
        c._write_token_cache('fresh', '20400101000000')
        return 'fresh'
    async def post(path, api_id, body, token, **kwargs):
        calls.append((token, kwargs['next_key']))
        if token == 'rejected': return {'return_code':3,'return_msg':'[8005:Token이 유효하지 않습니다]'}, '', 200
        return {'return_code':0,'data':[1]}, 'page-2', 200
    c._access_token_locked, c._post_api_page_once = issue, post
    result, next_key = await c.post_api_page('/api/dostk/chart', 'ka10080', {}, next_key='page-1')
    assert result['data'] == [1] and next_key == 'page-2'
    assert calls == [('rejected','page-1'), ('fresh','page-1')]


@pytest.mark.asyncio
async def test_refresh_does_not_replay_orders_or_transport_timeouts(tmp_path):
    c = client(tmp_path); calls = []
    c._write_token_cache('token', '20400101000000')
    async def post(*args, **kwargs):
        calls.append(True)
        return {'return_code':805004,'return_msg':'Token invalid'}, '', 200
    c._post_api_page_once = post
    with pytest.raises(RuntimeError):
        await c.post_api_page('/api/dostk/ordr', 'kt10000', {})
    assert len(calls) == 1
    c._write_token_cache('token', '20400101000000')
    async def timeout(*args, **kwargs):
        calls.append(True)
        raise asyncio.TimeoutError()
    c._post_api_page_once = timeout
    with pytest.raises(asyncio.TimeoutError):
        await c.post_api_page('/api/dostk/chart', 'ka10080', {})
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_stale_ws_rejection_does_not_delete_newer_token(tmp_path):
    c = client(tmp_path)
    c._write_token_cache('newer', '20400101000000')
    await c.invalidate_token('older')
    assert c._read_token_cache() == 'newer'
    await c.invalidate_token('newer')
    assert c._read_token_cache() is None


@pytest.mark.asyncio
async def test_ws_login_rejection_invalidates_token_before_reconnect(tmp_path):
    c = client(tmp_path); c._write_token_cache('bad', '20400101000000')
    class WS:
        async def send_json(self, data): pass
        async def receive(self, **kwargs):
            return SimpleNamespace(type=aiohttp.WSMsgType.TEXT,
                data=json.dumps({'trnm':'LOGIN','return_code':805004,'return_msg':'Token invalid'}))
    collector = KiwoomCollector(); collector.client = c
    with pytest.raises(RuntimeError, match='805004'):
        await collector._login_ws(WS())
    assert c._read_token_cache() is None


@pytest.mark.asyncio
async def test_closed_heartbeat_does_not_hide_stopped_process_at_next_open(tmp_path):
    bot = _controller(tmp_path); sent = []
    path = bot.settings.state_dir/'health/collectors-kis.json'
    row = json.loads(path.read_text()); row['ts_ns'] = time.time_ns()-5*3600*10**9
    path.write_text(json.dumps(row))
    async def send(session, chat, text): sent.append(text)
    bot._send = send
    await bot._market_health_once(None, {'kis':False})
    assert sent == []
    await bot._market_health_once(None, {'kis':True})
    assert len(sent) == 1 and 'heartbeat' in sent[0]
    await bot._market_health_once(None, {'kis':True})
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_fresh_heartbeat_with_stalled_market_events_alerts(tmp_path):
    bot = _controller(tmp_path); sent = []
    touch(bot.settings, 'collectors-kiwoom', detail={'phase':'publishing',
        'symbol_last_seen_ns':{'069500':time.time_ns()-240*10**9}})
    async def send(session, chat, text): sent.append(text)
    bot._send = send
    await bot._market_health_once(None, {'kiwoom':True})
    assert len(sent) == 1 and '실제 데이터 수신 중단' in sent[0]


@pytest.mark.asyncio
async def test_missing_heartbeat_alerts_even_without_nats(tmp_path):
    bot = _controller(tmp_path); sent = []
    (bot.settings.state_dir/'health/collectors-toss.json').unlink()
    async def send(session, chat, text): sent.append(text)
    bot._send = send
    await bot._market_health_once(None, {'toss':True})
    assert len(sent) == 1 and 'toss' in sent[0]


@pytest.mark.asyncio
async def test_failed_delivery_is_not_marked_sent_and_closed_session_is_quiet(tmp_path):
    bot = _controller(tmp_path)
    touch(bot.settings, 'collectors-kis', state='error', detail={'phase':'paused'})
    async def fail(*args): raise aiohttp.ClientConnectionError('offline')
    bot._send = fail
    await bot._market_health_once(None, {'kis':False})
    with pytest.raises(aiohttp.ClientConnectionError):
        await bot._market_health_once(None, {'kis':True})
    assert 'kis' not in bot._market_alert_at


@pytest.mark.asyncio
async def test_connecting_and_wrong_market_closed_phase_cannot_silently_persist(tmp_path, monkeypatch):
    bot = _controller(tmp_path); sent = []; clock = [1000.]
    monkeypatch.setattr('vibetrading.telegram.time.monotonic', lambda:clock[0])
    async def send(session, chat, text): sent.append(text)
    bot._send = send
    await bot._market_health_once(None, {'kiwoom':True})
    assert not sent
    clock[0] += 121
    await bot._market_health_once(None, {'kiwoom':True})
    assert len(sent) == 1 and '수집 미시작' in sent[0]


@pytest.mark.asyncio
async def test_kiwoom_does_not_connect_or_report_stall_during_futures_only_session(tmp_path, monkeypatch):
    from vibetrading import runtime
    settings = _controller(tmp_path).settings; phases = []
    monkeypatch.setattr(runtime, 'session_state', lambda group:(group=='KR_DERIVATIVES','2026-10-15'))
    monkeypatch.setattr(runtime, 'health_touch', lambda *args, **kwargs:phases.append(kwargs['detail']['phase']))
    async def stop_sleep(*args): raise asyncio.CancelledError()
    monkeypatch.setattr(runtime.asyncio, 'sleep', stop_sleep)
    with pytest.raises(asyncio.CancelledError):
        await runtime._collector_loop(runtime.RuntimeJob('kiwoom'), SimpleNamespace(settings=settings, _js=None))
    assert phases == ['connecting','market_closed']
