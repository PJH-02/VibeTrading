import asyncio
from types import SimpleNamespace

import aiohttp
import pytest
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
from websockets.frames import Close

from runtime.bus import NatsJetStreamBus
from feeds.providers.toss import TossAuthClient, TossCollector, TossUnauthorized, parse_toss_symbols
from runtime.runtime import _collector_error_details
from runtime.settings import RuntimeSettings


def test_publish_rate_is_bounded_without_rate_reads(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("runtime.bus.time.monotonic", lambda: clock[0])
    bus = NatsJetStreamBus()
    for second in range(180):
        clock[0] = 100.0 + second
        for _ in range(1000):
            bus._record_crypto_publish_size(17)
        assert len(bus._crypto_publish_sizes) <= 61
    assert bus.recent_publish_bytes_per_second == pytest.approx(61 * 17000 / 60)
    clock[0] += 62
    assert bus.recent_publish_bytes_per_second == 0


def test_normal_gateway_rotation_is_transient_but_policy_rejection_is_not():
    assert _collector_error_details(ConnectionClosedOK(Close(1001, 'proxy restarting'), Close(1001, ''), True))[2]
    assert not _collector_error_details(ConnectionClosedError(Close(1008, 'policy violation'), Close(1008, ''), True))[2]


def client_for(tmp_path):
    env = tmp_path / '.env'
    env.write_text('VIBETRADING_DATA_ROOT=' + tmp_path.as_posix())
    return TossAuthClient(RuntimeSettings.from_env(env))


def test_rejected_cache_refresh_is_serialized_and_keeps_newer_token(tmp_path, monkeypatch):
    client = client_for(tmp_path)
    client._write_token_cache('rejected', 9999999999)
    issued = []
    async def issue():
        issued.append(1)
        await asyncio.sleep(0)
        client._write_token_cache('fresh', 9999999999)
        return 'fresh'
    monkeypatch.setattr(client, '_access_token_locked', issue)
    async def check():
        assert await asyncio.gather(*(client.refresh_rejected_token('rejected') for _ in range(10))) == ['fresh'] * 10
    asyncio.run(check())
    assert len(issued) == 1


@pytest.mark.parametrize('method,errors,expected_calls', [('GET',1,2),('GET',2,2)])
def test_rest_refresh_bounded_and_orders_not_replayed(tmp_path, monkeypatch, method, errors, expected_calls):
    client = client_for(tmp_path)
    calls = []
    async def access(): return 'old'
    async def refresh(token):
        assert token == 'old'
        return 'new'
    async def request_once(*args, **kwargs):
        calls.append(kwargs['token'])
        if len(calls) <= errors: raise TossUnauthorized('401')
        return {'result': 'ok'}
    monkeypatch.setattr(client, 'access_token', access)
    monkeypatch.setattr(client, 'refresh_rejected_token', refresh)
    monkeypatch.setattr(client, '_request_once', request_once)
    if errors < expected_calls:
        assert asyncio.run(client.request(method, '/api/v1/candles')) == {'result': 'ok'}
    else:
        with pytest.raises(TossUnauthorized): asyncio.run(client.request(method, '/api/v1/orders'))
    assert len(calls) == expected_calls


def test_data_only_rejects_post_before_token_refresh_and_transport(tmp_path, monkeypatch):
    client = client_for(tmp_path)
    calls = {'access': 0, 'refresh': 0, 'request_once': 0}
    async def access():
        calls['access'] += 1
        raise AssertionError('access_token must not be called')
    async def refresh(token):
        calls['refresh'] += 1
        raise AssertionError('refresh_rejected_token must not be called')
    async def request_once(*args, **kwargs):
        calls['request_once'] += 1
        raise AssertionError('_request_once must not be called')
    monkeypatch.setattr(client, 'access_token', access)
    monkeypatch.setattr(client, 'refresh_rejected_token', refresh)
    monkeypatch.setattr(client, '_request_once', request_once)
    with pytest.raises(RuntimeError, match='TOSS DATA_ONLY: request rejected'):
        asyncio.run(client.request('POST', '/api/v1/orders'))
    assert calls == {'access': 0, 'refresh': 0, 'request_once': 0}


@pytest.mark.parametrize('status,fail_twice', [(401,False),(401,True),(403,False)])
def test_websocket_refresh_only_once_for_401(monkeypatch, status, fail_twice):
    collector = TossCollector(parse_toss_symbols('US:SPY'), publish_rest_snapshot=False)
    calls = []
    class Client:
        has_credentials = True
        async def access_token(self): return 'old'
        async def refresh_rejected_token(self, token): return 'new'
    collector.client = Client()
    async def run_once(bus, token, duration):
        calls.append(token)
        if len(calls) == 1 or fail_twice:
            raise aiohttp.WSServerHandshakeError(SimpleNamespace(real_url='wss://example.test'), (), status=status)
    monkeypatch.setattr(collector, '_run_websocket', run_once)
    if status == 401 and not fail_twice:
        asyncio.run(collector.run(object()))
    else:
        with pytest.raises(aiohttp.WSServerHandshakeError): asyncio.run(collector.run(object()))
    assert calls == (['old','new'] if status == 401 else ['old'])
