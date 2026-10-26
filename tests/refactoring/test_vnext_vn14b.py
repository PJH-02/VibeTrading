"""Opt-in Linux owner control; no Telegram HTTP, provider mutation or activation."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import os
import time

import pytest

from test_execution_owner import build_owner
from test_local_transport import budgets
from test_outbox_isolation import _controller
from feeds.feed_recovery import EngineCursor
from runtime.local_transport import LocalTransportServer, TransportError, encode_frame, read_frame, request_command, LocalTransportClient
from operations.telegram import TelegramController, TelegramRateLimit
from operations.telegram_state import TelegramState


def update(text='halt strategy strategy', **changes):
    return {'update_id':42, 'message':{'from':{'id':123}, 'chat':{'id':123},
            'date':int(time.time()), 'text':text}, **changes}


def request(identity='one', action='halt', **changes):
    return dict(command_id=identity, action=action, strategy='strategy', scope='strategy',
                expires_ns=time.time_ns()+60_000_000_000, expected_epochs=None, **changes)


@asynccontextmanager
async def endpoint(tmp_path, handler, limits=None):
    limits = limits or budgets()
    server = LocalTransportServer(tmp_path/'engine', budgets=limits, allowed_uid=os.getuid(),
        on_frame=lambda frame:None, on_gap=lambda *args:None, on_resume=lambda cursor:False,
        on_command=handler)
    await server.start()
    try:
        yield server, lambda body: request_command(tmp_path/'engine', body, budgets=limits, allowed_uid=os.getuid())
    finally:
        await server.close()


def controller(tmp_path, handler, ttl=60):
    bot = TelegramController(_controller(tmp_path).settings,
        command_handler=handler, command_ttl_seconds=ttl)
    bot._outbox_enabled = True
    return bot


@pytest.mark.asyncio
async def test_real_uds_owner_receipt_precedes_outbox_and_gateway_loss_never_repeats_effect(tmp_path, monkeypatch):
    owner, adapter, ready, _ = build_owner(tmp_path)
    try:
        async with endpoint(tmp_path, lambda body:owner.apply_command(body, readiness=ready)) as (server, send):
            bot = controller(tmp_path, send)
            item = update()
            bot._state.receive(item)
            def lost(*args): raise OSError('reply storage lost')
            monkeypatch.setattr(bot._state, 'enqueue', lost)
            with pytest.raises(OSError): await bot._handle(None, item)
            state = owner.control.snapshot('strategy')
            assert state['epochs']['strategy:strategy'] == 2
            assert len(bot._state.pending()) == 1
            assert owner.ledger._db.execute('SELECT COUNT(*) FROM owner_commands').fetchone()[0] == 1
            # Restart the gateway with changed TTL; the first persisted request wins.
            bot = controller(tmp_path, send, ttl=120)
            await bot._handle_pending_update(None, item)
            assert bot._state.pending() == []
            assert owner.control.snapshot('strategy') == state
            assert bot._state.next_message()['text'].startswith('명령 ACCEPTED: telegram:123:42')
            assert adapter.connects == 0 and adapter.mutations == []
            assert server._sessions == {}  # command did not create a market session
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_lost_uds_result_then_exact_retry_after_resume_has_no_effect(tmp_path):
    owner, _, ready, _ = build_owner(tmp_path)
    try:
        async with endpoint(tmp_path, lambda body:owner.apply_command(body, readiness=ready)) as (_, send):
            body = request()
            reader, writer = await asyncio.open_unix_connection(str(tmp_path/'engine.control'))
            writer.write(encode_frame(EngineCursor('control','command',0), json.dumps(body).encode(),
                session='a'*32, budgets=budgets(), kind='COMMAND'))
            await writer.drain()
            # Observe commit, then discard its result and close the socket.
            reply = await read_frame(reader, budgets())
            assert reply.kind == 'RESULT'
            writer.close(); await writer.wait_closed()
            owner.resume('strategy', expected_epochs=owner.control.snapshot('strategy')['epochs'], readiness=ready)
            before = owner.control.snapshot('strategy')
            assert (await send(body))['duplicate'] is True
            assert owner.control.snapshot('strategy') == before
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_fresh_resume_verifies_owner_readiness_in_transaction_and_duplicate_does_not(tmp_path):
    owner, _, ready, _ = build_owner(tmp_path)
    try:
        owner.control.halt('strategy', scope='strategy')
        body = request(action='resume')
        body['expected_epochs'] = owner.control.snapshot('strategy')['epochs']
        calls = []
        def verified():
            assert owner.ledger._db.in_transaction
            calls.append(1)
            return ready()
        async with endpoint(tmp_path, lambda body:owner.apply_command(body, readiness=verified)) as (_, send):
            result = await send(body)
            assert result['status'] == 'ACCEPTED' and calls == [1]
            owner.control.halt('strategy', scope='global')
            assert (await send(body))['duplicate'] is True and calls == [1]
            assert owner.control.snapshot('strategy')['closed']['global']
    finally:
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('initially_stale', [True,False])
async def test_resume_uses_the_epoch_vector_bound_in_payload_before_readiness_callback(tmp_path, initially_stale):
    from runtime.runtime_control import GateBlocked

    owner, _, ready, _ = build_owner(tmp_path)
    try:
        owner.control.halt('strategy', scope='strategy')
        current = owner.control.snapshot('strategy')['epochs']
        stale = {key:0 for key in current}
        body = request(action='resume')
        body['expected_epochs'] = (stale if initially_stale else current).copy()
        def provider():
            body['expected_epochs'].update(current if initially_stale else stale)
            return ready()
        if initially_stale:
            with pytest.raises(GateBlocked, match='stale'):
                owner.apply_command(body, readiness=provider)
            assert owner.ledger._db.execute('SELECT COUNT(*) FROM owner_commands').fetchone()[0] == 0
        else:
            assert owner.apply_command(body, readiness=provider)['status'] == 'ACCEPTED'
            payload = owner.ledger._db.execute('SELECT payload FROM owner_commands').fetchone()[0]
            assert json.loads(payload)[5] == current
    finally:
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['stale','expired','readiness','async_readiness','forged_readiness','nested_transaction',
                                    'lease_nan','lease_inf','lease_bool','lease_float','lease_overflow','clock_nan'])
async def test_rejected_resume_never_commits_or_replies_accepted(tmp_path, failure):
    owner, _, ready, _ = build_owner(tmp_path)
    try:
        owner.control.halt('strategy', scope='strategy')
        before = owner.control.snapshot('strategy')
        body = request(action='resume')
        body['expected_epochs'] = before['epochs']
        provider = ready
        if failure == 'stale': body['expected_epochs'] = {key:0 for key in before['epochs']}
        if failure == 'expired': body['expires_ns'] = 1
        if failure == 'readiness': provider = lambda:replace(ready(), feed_ready=False)
        bad_leases = {'lease_nan':float('nan'), 'lease_inf':float('inf'), 'lease_bool':True,
                      'lease_float':1e18, 'lease_overflow':2**63}
        if failure in bad_leases:
            provider = lambda:replace(ready(), lease_until_ns=bad_leases[failure])
        if failure == 'lease_bool': owner.clock = lambda:0
        if failure == 'clock_nan': owner.clock = lambda:float('nan')
        if failure == 'async_readiness':
            async def provider(): return ready()
        if failure == 'forged_readiness': body['readiness_verified'] = True
        if failure == 'nested_transaction':
            def provider():
                with owner.ledger.transaction(): return ready()
        async with endpoint(tmp_path, lambda body:owner.apply_command(body, readiness=provider)) as (_, send):
            if failure == 'nested_transaction':
                with pytest.raises(TransportError): await send(body)
            else:
                assert (await send(body))['status'] == 'REJECTED'
        assert owner.control.snapshot('strategy')['epochs'] == before['epochs']
        assert owner.control.snapshot('strategy')['closed']['strategy:strategy']
        assert owner.ledger._db.execute('SELECT COUNT(*) FROM owner_commands').fetchone()[0] == 0
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_expired_gateway_retry_returns_original_receipt_without_new_ttl(tmp_path, monkeypatch):
    owner, _, ready, _ = build_owner(tmp_path)
    try:
        async with endpoint(tmp_path, lambda body:owner.apply_command(body, readiness=ready)) as (_, send):
            bot = controller(tmp_path, send)
            item = update()
            await bot._handle(None, item)
            before = owner.control.snapshot('strategy')
            monkeypatch.setattr("operations.telegram.time.time_ns", lambda:(item['message']['date']+1000)*1_000_000_000)
            await bot._handle(None, item)
            assert owner.control.snapshot('strategy') == before
            assert owner.ledger._db.execute('SELECT COUNT(*) FROM owner_commands').fetchone()[0] == 1
    finally:
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('sender,chat', [(True,123),(123.0,123),('123',123),(123,True),
                                       (123,123.0),(123,'123'),(456,123),(123,456)])
async def test_noninteger_or_unapproved_identity_cannot_reach_owner(tmp_path, sender, chat):
    calls = []
    async def handler(body): calls.append(body)
    bot = controller(tmp_path, handler)
    item = update(); item['message']['from']['id']=sender; item['message']['chat']['id']=chat
    await bot._handle(None, item)
    assert calls == [] and bot._state.next_message() is None


@pytest.mark.asyncio
async def test_explicit_multiple_allowlists_and_negative_group_chat(tmp_path):
    calls = []
    async def handler(body):
        calls.append(body)
        return {'status':'REJECTED','reason':'command_rejected'}
    settings = _controller(tmp_path).settings
    settings = replace(settings, env={**settings.env, 'TELEGRAM_USER_IDS':'123,456', 'TELEGRAM_CHAT_IDS':'123,-100'})
    bot = TelegramController(settings, command_handler=handler, command_ttl_seconds=60)
    bot._outbox_enabled=True
    item = update(); item['message']['from']['id']=456; item['message']['chat']['id']=-100
    await bot._handle_pending_update(None,item)
    assert calls[0]['command_id'] == 'telegram:-100:42'
    assert bot._state.next_message()['chat_id'] == '-100'


@pytest.mark.parametrize('chat_key,chats',[('TELEGRAM_CHAT_IDS','-100,123'),('TELEGRAM_CHAT_ID','-100')])
def test_multi_allowlists_alone_enable_bot_with_one_explicit_alert_destination(tmp_path, chat_key, chats):
    settings = _controller(tmp_path).settings
    settings = replace(settings, telegram_user_id=None, env={**settings.env,
        'TELEGRAM_USER_IDS':'123,456', chat_key:chats})
    bot = TelegramController(settings)
    assert bot.enabled and bot.allowed_id == '-100'


@pytest.mark.asyncio
async def test_halt_is_admitted_with_all_market_sessions_and_partial_market_budget_full(tmp_path):
    owner, _, ready, _ = build_owner(tmp_path)
    limits = budgets(max_sessions=1, max_records=1)
    try:
        async with endpoint(tmp_path, lambda body:owner.apply_command(body, readiness=ready), limits) as (server,send):
            client = LocalTransportClient(tmp_path/'engine', producer='feed', incarnation='boot',
                budgets=limits, on_gap=lambda *args:None, allowed_uid=os.getuid())
            await client.connect()
            try:
                # A complete header reserves market capacity before the incomplete body arrives.
                raw = encode_frame(EngineCursor('feed','boot',1), b'x'*100, session=client.session, budgets=limits)
                writer = client._connections['data'].writer
                writer.write(raw[:-1]); await writer.drain()
                async with asyncio.timeout(1):
                    while server._in_flight_records != 1: await asyncio.sleep(0)
                before = (server._in_flight_bytes,server._in_flight_records)
                assert len(server._sessions)==1
                assert (await send(request()))['status']=='ACCEPTED'
                assert (server._in_flight_bytes,server._in_flight_records)==before
                assert owner.control.snapshot('strategy')['closed']['strategy:strategy']
            finally:
                await client.close()
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_partial_command_capacity_rejects_then_releases_without_owner_effect(tmp_path):
    owner, _, ready, _ = build_owner(tmp_path)
    limits = budgets(max_control_records=1)
    try:
        async with endpoint(tmp_path, lambda body:owner.apply_command(body, readiness=ready), limits) as (server,send):
            reader, writer = await asyncio.open_unix_connection(str(tmp_path/'engine.control'))
            raw = encode_frame(EngineCursor('control','command',0), json.dumps(request()).encode(),
                               session='a'*32, budgets=limits, kind='COMMAND')
            writer.write(raw[:-1]); await writer.drain()
            try:
                async with asyncio.timeout(1):
                    while server._command_records != 1: await asyncio.sleep(0)
                with pytest.raises(TransportError): await send(request())
                assert owner.ledger._db.execute('SELECT COUNT(*) FROM owner_commands').fetchone()[0]==0
                assert server._in_flight_records==0
            finally:
                writer.close(); await writer.wait_closed()
            async with asyncio.timeout(1):
                while server._command_records: await asyncio.sleep(0)
            assert server._command_bytes==0
            assert (await send(request()))['status']=='ACCEPTED'
    finally:
        await owner.close()


@pytest.mark.parametrize('field,value', [('TELEGRAM_USER_IDS','True'),('TELEGRAM_USER_IDS','0123'),
    ('TELEGRAM_USER_IDS','１２３'),('TELEGRAM_USER_IDS','1.0'),('TELEGRAM_CHAT_IDS','-0'),
    ('TELEGRAM_USER_IDS',','.join(str(i+1) for i in range(33)))])
def test_allowlist_config_fails_closed(tmp_path, field, value):
    settings = _controller(tmp_path).settings
    with pytest.raises(ValueError):
        TelegramController(replace(settings, env={**settings.env, field:value}))


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['timestamp','future','update','scope','length','epochs','duplicate_epochs'])
async def test_malformed_command_never_reaches_owner(tmp_path, bad):
    calls = []
    async def handler(body): calls.append(body)
    bot = controller(tmp_path, handler)
    item = update()
    if bad=='timestamp': item['message']['date']=True
    if bad=='future': item['message']['date']+=1000
    if bad=='update': item['update_id']=True
    if bad=='scope': item['message']['text']='halt strategy all'
    if bad=='length': item['message']['text']='halt '+('x'*129)+' strategy'
    if bad=='epochs': item['message']['text']='resume strategy strategy {"global":true}'
    if bad=='duplicate_epochs': item['message']['text']='resume strategy strategy {"global":1,"global":2}'
    await bot._handle(None,item)
    assert calls == []
    assert 'ACCEPTED' not in bot._state.next_message()['text']


def test_first_canonical_request_survives_restart_and_update_collision(tmp_path):
    path=tmp_path/'telegram.sqlite3'
    state=TelegramState(path, max_pending=1)
    item=update(); body=request()
    assert state.command_request(item,body)==body
    assert TelegramState(path).command_request(item,{**body,'expires_ns':1})==body
    with pytest.raises(ValueError,match='conflicting'):
        state.command_request(update('pause strategy global', message={**item['message'],'text':'pause strategy global'}),body)


@pytest.mark.asyncio
async def test_commands_disabled_by_default_and_wrong_uid_or_frame_budget_never_call_handler(tmp_path):
    calls=[]
    async with endpoint(tmp_path,None) as (_,send):
        with pytest.raises(TransportError): await send(request())
    async with endpoint(tmp_path,lambda body:calls.append(body)):
        with pytest.raises(TransportError,match='uid'):
            await request_command(tmp_path/'engine', request(), budgets=budgets(), allowed_uid=os.getuid()+1)
        with pytest.raises(TransportError):
            await request_command(tmp_path/'engine', request(), budgets=budgets(max_control_bytes=1024), allowed_uid=os.getuid())
    assert calls==[]


@pytest.mark.asyncio
async def test_gateway_cannot_accept_forged_receipt_and_429_only_retries_outbox(tmp_path, monkeypatch):
    async def forged(body):
        return dict(status='ACCEPTED', command_id='different', action=body['action'],
                    scope='strategy:strategy', epoch=1, duplicate=False)
    bot=controller(tmp_path,forged)
    with pytest.raises(TransportError): await bot._handle(None,update())
    assert bot._state.next_message() is None
    # The sender has no route back to owner command execution.
    bot._state.enqueue('reply:42','123','saved owner receipt')
    done=asyncio.Event(); retries=[]
    async def limited(*args): raise TelegramRateLimit(17)
    original=bot._state.retry
    loop=asyncio.get_running_loop()
    def retry(*args):
        original(*args); retries.append(args);loop.call_soon_threadsafe(done.set)
    bot._send_direct=limited;monkeypatch.setattr(bot._state,'retry',retry)
    task=asyncio.create_task(bot._sender_loop(None))
    try: await asyncio.wait_for(done.wait(),5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    assert retries==[('reply:42',17)]
    with bot._state.transaction() as db:
        assert db.execute('SELECT attempts FROM outbox').fetchone()[0]==1
