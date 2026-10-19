"""Transport recovery classification and suppression of market-open notices."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import aiohttp
import pytest

from vibetrading.providers.kiwoom import _raise_websocket_closed
from vibetrading.runtime import _collector_error_details
from vibetrading.settings import RuntimeSettings
from vibetrading.telegram import TelegramController


@pytest.mark.parametrize('exc',[
    aiohttp.ClientConnectionResetError('Cannot write to closing transport'),
    aiohttp.ServerDisconnectedError('Server disconnected'),
    ConnectionResetError('Kiwoom websocket disconnected code=1000; reconnect required'),
])
def test_aiohttp_and_normal_close_are_transient(exc):
    reason,_,transient=_collector_error_details(exc)
    assert transient and type(exc).__name__ in reason


@pytest.mark.parametrize('code',[None,1000,1001,1006,1011,1012,1013])
def test_closed_socket_requests_supervised_reconnect(code):
    ws=SimpleNamespace(close_code=code,exception=lambda:None)
    with pytest.raises(ConnectionResetError) as caught: _raise_websocket_closed(ws)
    assert _collector_error_details(caught.value)[2]


@pytest.mark.parametrize('code',[1002,1003,1007,1008,1009,1010])
def test_protocol_and_policy_failures_remain_actionable(code):
    ws=SimpleNamespace(close_code=code,exception=lambda:aiohttp.ClientConnectionResetError('Cannot write to closing transport'))
    with pytest.raises(RuntimeError) as caught: _raise_websocket_closed(ws)
    assert not _collector_error_details(caught.value)[2]


def test_auth_rejection_is_not_classified_as_network_error():
    assert not _collector_error_details(RuntimeError('Kiwoom websocket login failed return_code=8005'))[2]


@pytest.mark.asyncio
async def test_restart_and_open_transition_never_send_market_open(tmp_path,monkeypatch):
    path=tmp_path/'.env'; path.write_text('',encoding='utf-8')
    settings=replace(RuntimeSettings.from_env(path),data_root=tmp_path,state_dir=tmp_path/'state',
        telegram_user_id='123',env={'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_USER_ID':'123'})
    state=[False]; messages=[]
    monkeypatch.setattr('vibetrading.telegram.SESSIONS',{'KR_EQUITY_ETF':()})
    monkeypatch.setattr('vibetrading.telegram.session_state',lambda group:(state[0],'2026-10-14'))
    for start_open in [False,True]:
        bot=TelegramController(settings)
        state[0]=start_open
        async def send(session,chat_id,text): messages.append(text)
        bot._send=send
        passes=[0]
        async def tick(delay):
            passes[0]+=1
            state[0]={1:True,2:True,3:False}.get(passes[0],False)
            if passes[0]>=4: raise asyncio.CancelledError()
        monkeypatch.setattr('vibetrading.telegram.asyncio.sleep',tick)
        with pytest.raises(asyncio.CancelledError): await bot._session_loop(None)
    assert messages==[]
