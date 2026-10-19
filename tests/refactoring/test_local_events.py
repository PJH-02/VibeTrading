"""Operational facts are read-only; Telegram owns ACKs and its local outbox."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from vibetrading.local_events import FactsEventBudgets, LocalFactsEventBus
from vibetrading.telegram import TelegramController


def budgets(**updates):
    values=dict(max_records=2,max_bytes=8192,max_frame_bytes=4096,max_consumers=4,
        poll_seconds=.01,query_timeout_seconds=.2,max_nak_seconds=.01,max_checkpoint_bytes=131072)
    return FactsEventBudgets(**(values|updates))


def facts(path, subjects=('system.storage_error','signal.C3.kis.etf.069500','system.storage_error')):
    with sqlite3.connect(path) as db:
        db.executescript('''PRAGMA user_version=1;
            CREATE TABLE facts_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO facts_metadata VALUES('generation','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
            CREATE TABLE local_fact(fact_id INTEGER PRIMARY KEY,producer TEXT,incarnation TEXT,sequence INTEGER,kind TEXT,subject TEXT,msg_id TEXT,raw_payload BLOB,payload_sha256 TEXT,decoded_payload BLOB,UNIQUE(producer,incarnation,sequence));
            CREATE INDEX local_fact_subject_id ON local_fact(subject,fact_id);''')
        for identity,subject in enumerate(subjects,1):
            body=json.dumps({'event_type':'system','kind':'storage_error','error':'fixture','ts_ns':time.time_ns()},separators=(',',':')).encode()
            raw=json.dumps({'version':1,'subject':subject,'msg_id':'id-'+str(identity),
                'payload_base64':base64.b64encode(body).decode()},separators=(',',':')).encode()
            db.execute('INSERT INTO local_fact VALUES(?,?,?,?,?,?,?,?,?,?)',
                (identity,'producer','run',identity,'observation',subject,'id-'+str(identity),raw,hashlib.sha256(raw).hexdigest(),body))


def bus(tmp_path, **updates):
    return LocalFactsEventBus(tmp_path/'facts.sqlite3',tmp_path/'telegram/events.sqlite3',budgets=budgets(**updates))


def cursor(path,durable='fixture'):
    if not path.exists():return None
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT cursor FROM consumer WHERE durable=?',(durable,)).fetchone()
        return row[0] if row else None


def test_checkpoint_pin_blocks_new_writes_and_restarts_without_wal_growth(tmp_path):
    from vibetrading.local_events import _FactsSession, checkpoint_storage_reserve
    source=tmp_path/'facts.sqlite3';checkpoint=tmp_path/'events.sqlite3'
    facts(source)
    session=_FactsSession(source,checkpoint,budgets())
    assert session._consumer('fixture','system.storage_error')==0
    pin=sqlite3.connect(checkpoint.as_uri()+'?mode=ro',uri=True,isolation_level=None)
    pin.execute('BEGIN')
    assert pin.execute('SELECT cursor FROM consumer').fetchone()[0]==0
    try:
        with pytest.raises(RuntimeError,match='WAL pinned'):
            session.ack('fixture','system.storage_error',1)
        assert cursor(checkpoint)==1  # COMMIT survived; post-COMMIT ACK is uncertain.
        wal=checkpoint.with_name(checkpoint.name+'-wal')
        first=wal.stat().st_size
        assert 0<first<checkpoint_storage_reserve(budgets().max_checkpoint_bytes)
        session.close()
        for _ in range(3):
            with pytest.raises(RuntimeError,match='WAL pinned'):
                _FactsSession(source,checkpoint,budgets())
            assert wal.stat().st_size==first
            assert cursor(checkpoint)==1
    finally:
        pin.execute('ROLLBACK');pin.close();session.close()
    restored=_FactsSession(source,checkpoint,budgets())
    try:
        assert restored._consumer('fixture','system.storage_error')==1
        restored.ack('fixture','system.storage_error',2)
        assert cursor(checkpoint)==2
        assert not wal.exists() or wal.stat().st_size==0
    finally:restored.close()


def test_checkpoint_oversize_wal_is_rejected_before_opening_writer(tmp_path):
    from vibetrading.local_events import _FactsSession
    source=tmp_path/'facts.sqlite3';checkpoint=tmp_path/'events.sqlite3'
    facts(source)
    wal=checkpoint.with_name(checkpoint.name+'-wal')
    wal.write_bytes(b'X'*300000)
    with pytest.raises(RuntimeError,match='file capacity'):
        _FactsSession(source,checkpoint,budgets())
    assert not checkpoint.exists() and wal.stat().st_size==300000


def test_checkpoint_space_reservation_precedes_database_creation(tmp_path,monkeypatch):
    from vibetrading.local_events import _FactsSession
    source=tmp_path/'facts.sqlite3';checkpoint=tmp_path/'events.sqlite3'
    facts(source)
    monkeypatch.setattr('vibetrading.local_events.shutil.disk_usage',lambda _path:SimpleNamespace(free=1))
    with pytest.raises(RuntimeError,match='free space'):
        _FactsSession(source,checkpoint,budgets())
    assert not checkpoint.exists()


async def until(predicate):
    async def wait():
        while not predicate():await asyncio.sleep(.005)
    await asyncio.wait_for(wait(),timeout=2)


@pytest.fixture(autouse=True)
def no_nats(monkeypatch):
    def unavailable(*args,**kwargs):
        raise AssertionError('local facts cannot construct or connect NATS')
    monkeypatch.setattr('nats.connect',unavailable)
    monkeypatch.setattr('vibetrading.telegram.NatsJetStreamBus',unavailable)


@pytest.mark.asyncio
async def test_ack_cursor_survives_restart_and_source_is_unchanged(tmp_path):
    path=tmp_path/'facts.sqlite3';facts(path)
    original=path.read_bytes()
    first=bus(tmp_path)
    await first.connect()
    stream=first.subscribe('system.>','fixture')
    message=await anext(stream)
    assert message.stream_sequence==1 and message.subject=='system.storage_error'
    assert message.stream_generation=='a'*32 and message.data['error']=='fixture'
    assert cursor(first.checkpoint_path)==0
    await message.ack()
    assert cursor(first.checkpoint_path)==1
    await stream.aclose();await first.close()
    second=bus(tmp_path)
    await second.connect()
    stream=second.subscribe('system.>','fixture')
    try:
        message=await anext(stream)
        assert message.stream_sequence==3
        await message.ack()
        assert cursor(second.checkpoint_path)==3
    finally:
        await stream.aclose();await second.close()
    assert path.read_bytes()==original


@pytest.mark.asyncio
async def test_nak_keeps_source_cursor_and_replays_same_identity(tmp_path):
    facts(tmp_path/'facts.sqlite3',('system.storage_error',))
    local=bus(tmp_path);await local.connect()
    stream=local.subscribe('system.>','fixture')
    try:
        first=await anext(stream)
        await first.nak(delay=30)
        assert cursor(local.checkpoint_path)==0
        retried=await anext(stream)
        assert retried.commit_id==first.commit_id and retried.data==first.data
        await retried.ack()
        assert cursor(local.checkpoint_path)==1
    finally:
        await stream.aclose();await local.close()


@pytest.mark.asyncio
async def test_operational_gap_reads_raw_body_without_observation_envelope(tmp_path):
    path=tmp_path/'facts.sqlite3';facts(path,())
    body=b'{"event_type":"data_gap","provider":"kiwoom","reason":"gap"}'
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO local_fact VALUES(?,?,?,?,?,?,?,?,?,?)',
            (1,'collector','run',1,'control','md.data_gap.kiwoom.etf.069500','gap-id',body,hashlib.sha256(body).hexdigest(),None))
    local=bus(tmp_path);await local.connect()
    stream=local.subscribe('md.data_gap.>','gap')
    try:
        message=await anext(stream)
        assert message.data==json.loads(body)
        await message.ack()
    finally:
        await stream.aclose();await local.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('subject',['md.>','md.trade.>','crypto.order_book.>','>'])
async def test_high_volume_market_subscriptions_are_rejected_before_reads(tmp_path,subject):
    local=bus(tmp_path)
    with pytest.raises(ValueError,match='operational'):
        await anext(local.subscribe(subject,'bad'))
    assert not local.checkpoint_path.exists()


@pytest.mark.asyncio
async def test_oversized_or_corrupted_facts_never_advance_checkpoint(tmp_path):
    path=tmp_path/'facts.sqlite3';facts(path,('system.storage_error',))
    for kind in ('oversize','corrupt'):
        local=bus(tmp_path,max_frame_bytes=64 if kind=='oversize' else 4096)
        await local.connect()
        if kind=='corrupt':
            with sqlite3.connect(path) as db:db.execute("UPDATE local_fact SET payload_sha256=?",('0'*64,))
        stream=local.subscribe('system.>','fixture')
        try:
            with pytest.raises(RuntimeError,match='capacity' if kind=='oversize' else 'checksum'):
                await anext(stream)
            assert cursor(local.checkpoint_path)==0
        finally:
            await stream.aclose();await local.close()


@pytest.mark.asyncio
async def test_fetch_respects_combined_raw_and_decoded_byte_budget(tmp_path):
    path=tmp_path/'facts.sqlite3';facts(path,('system.storage_error',)*3)
    with sqlite3.connect(path) as db:
        size=db.execute('SELECT length(raw_payload)+length(decoded_payload) FROM local_fact LIMIT 1').fetchone()[0]
    local=bus(tmp_path,max_bytes=size+5,max_frame_bytes=size)
    await local.connect()
    try:
        rows=await local._call('fetch','fixture','system.>',2,epoch=local._epoch)
        assert len(rows)==1
        assert sum(len(row['raw_payload'])+len(row['decoded_payload']) for row in rows)<=local.budgets.max_bytes
        assert cursor(local.checkpoint_path)==0
    finally:await local.close()


@pytest.mark.asyncio
async def test_generation_change_and_subject_change_require_explicit_recovery(tmp_path):
    path=tmp_path/'facts.sqlite3';facts(path)
    local=bus(tmp_path);await local.connect()
    stream=local.subscribe('system.>','fixture')
    message=await anext(stream);await message.ack();await stream.aclose()
    changed=local.subscribe('signal.>','fixture')
    with pytest.raises(RuntimeError,match='subject/generation'):
        await anext(changed)
    with sqlite3.connect(path) as db:db.execute("UPDATE facts_metadata SET value=? WHERE key='generation'",('b'*32,))
    stream=local.subscribe('system.>','fixture')
    with pytest.raises(RuntimeError,match='generation changed'):
        await anext(stream)
    assert cursor(local.checkpoint_path)==1
    await local.close()
    restarted=bus(tmp_path);await restarted.connect()
    try:
        with pytest.raises(RuntimeError,match='subject/generation'):
            await anext(restarted.subscribe('system.>','fixture'))
    finally:await restarted.close()


@pytest.mark.asyncio
async def test_checkpoint_has_one_cooperating_writer_and_consumer_count_is_bounded(tmp_path):
    facts(tmp_path/'facts.sqlite3',())
    first=bus(tmp_path,max_consumers=1);await first.connect()
    other=bus(tmp_path)
    with pytest.raises(RuntimeError,match='another process'):
        await other.connect()
    await first._call('fetch','first','system.>',1,epoch=first._epoch)
    with pytest.raises(RuntimeError,match='capacity'):
        await first._call('fetch','second','order.>',1,epoch=first._epoch)
    await first.close()
    recovered=bus(tmp_path,max_consumers=1);await recovered.connect();await recovered.close()


@pytest.mark.asyncio
async def test_missing_source_is_never_created_and_eof_poll_does_not_spin(tmp_path,monkeypatch):
    local=bus(tmp_path)
    with pytest.raises(RuntimeError,match='missing'):
        await local.connect()
    assert not local.facts_path.exists() and not local.checkpoint_path.exists()
    facts(local.facts_path,())
    await local.connect()
    calls=[]
    original=local._session.fetch

    def fetch(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(local._session,'fetch',fetch)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(anext(local.subscribe('system.>','fixture')),timeout=.055)
    assert 1<=len(calls)<=8
    await local.close()


def controller(tmp_path,event_bus):
    settings=SimpleNamespace(state_dir=tmp_path/'telegram-state',telegram_user_id='123',
        env={'TELEGRAM_BOT_TOKEN':'fake'})
    return TelegramController(settings,bus=event_bus)


@pytest.mark.asyncio
async def test_bridge_ack_follows_durable_outbox_and_storage_failure_naks(tmp_path,monkeypatch):
    facts(tmp_path/'facts.sqlite3',('system.storage_error',))
    local=bus(tmp_path);await local.connect()
    bot=controller(tmp_path,local);bot._outbox_enabled=True
    original=bot._state.enqueue;attempts=[]

    def enqueue(*args):
        attempts.append(True)
        assert cursor(local.checkpoint_path,'bridge')==0
        if len(attempts)==1:raise sqlite3.OperationalError('injected disk fault')
        return original(*args)

    monkeypatch.setattr(bot._state,'enqueue',enqueue)
    task=asyncio.create_task(bot._event_bridge_subject(None,local,'system.>','bridge'))
    try:
        await until(lambda:cursor(local.checkpoint_path,'bridge')==1)
        assert len(attempts)==2
        with bot._state.transaction() as db:
            assert db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==1
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True);await local.close()


@pytest.mark.asyncio
async def test_telegram_run_uses_injected_bus_without_nats_constructor(tmp_path,monkeypatch):
    class Injected:
        closed=False
        async def close(self):self.closed=True

    class Session:
        def __init__(self,*args,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass

    event_bus=Injected();bot=controller(tmp_path,event_bus)
    async def stop(*args):raise RuntimeError('fixture shutdown')
    async def wait(*args):await asyncio.Event().wait()
    bot._poll_loop=stop
    bot._event_bridge_loop=bot._crypto_health_loop=bot._market_health_loop=bot._mock_notifications_loop=bot._sender_loop=wait
    monkeypatch.setattr('vibetrading.telegram.aiohttp.ClientSession',Session)
    with pytest.raises(RuntimeError,match='fixture shutdown'):
        await bot.run()
    assert event_bus.closed


@pytest.mark.parametrize('value',[True,0,-1,float('inf')])
def test_facts_budgets_require_explicit_finite_bounds(value):
    with pytest.raises(ValueError):budgets(max_records=value)
    with pytest.raises(ValueError):budgets(poll_seconds=value)


@pytest.mark.asyncio
async def test_cancelled_slow_fetch_keeps_executor_slot_until_real_worker_finishes(tmp_path,monkeypatch):
    facts(tmp_path/'facts.sqlite3',('system.storage_error',))
    local=bus(tmp_path,max_consumers=1);await local.connect()
    started=threading.Event();release=threading.Event()
    original=local._session.fetch
    submitted=[]
    original_submit=local._executor.submit
    def submit(*args,**kwargs):
        submitted.append(args[0].__name__)
        return original_submit(*args,**kwargs)
    def slow(*args):
        started.set()
        if not release.wait(3):raise RuntimeError('fixture worker release missing')
        return original(*args)
    monkeypatch.setattr(local._executor,'submit',submit)
    monkeypatch.setattr(local._session,'fetch',slow)
    stream=local.subscribe('system.>','fixture')
    request=asyncio.create_task(anext(stream))
    try:
        await until(started.is_set)
        request.cancel();await asyncio.gather(request,return_exceptions=True)
        await stream.aclose()
        assert local.stats['active_consumers']==0
        assert local.stats['pending_requests']==1
        for _ in range(20):
            retry=local.subscribe('system.>','fixture')
            with pytest.raises(RuntimeError,match='capacity occupied'):
                await anext(retry)
            await retry.aclose()
        assert submitted==['slow']
        assert local.stats['pending_requests']==1
        release.set()
        await until(lambda:local.stats['pending_requests']==0)
        recovered=local.subscribe('system.>','fixture')
        message=await anext(recovered)
        assert message.stream_sequence==1
        await message.ack();await recovered.aclose()
    finally:
        release.set()
        request.cancel();await asyncio.gather(request,return_exceptions=True)
        await stream.aclose();await local.close()


@pytest.mark.asyncio
async def test_stale_ack_and_old_generator_finalizer_cannot_modify_new_epoch_consumer(tmp_path):
    facts(tmp_path/'facts.sqlite3',('system.storage_error',))
    local=bus(tmp_path);await local.connect()
    old=local.subscribe('system.>','fixture')
    old_message=await anext(old)
    old_epoch=local._epoch
    await local.close();await local.connect()
    assert local._epoch>old_epoch
    current=local.subscribe('system.>','fixture')
    try:
        message=await anext(current)
        with pytest.raises(RuntimeError,match='epoch'):
            await old_message.ack()
        assert cursor(local.checkpoint_path)==0
        await old.aclose()
        assert local.stats['active_consumers']==1
        await message.ack()
        assert cursor(local.checkpoint_path)==1
    finally:
        await old.aclose();await current.aclose();await local.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('delivered_before_retry',(False,True))
async def test_outbox_committed_then_ack_lost_reuses_fact_identity_even_after_delivery(tmp_path,monkeypatch,delivered_before_retry):
    facts(tmp_path/'facts.sqlite3',('system.storage_error',))
    first=bus(tmp_path);await first.connect()
    bot=controller(tmp_path,first);bot._outbox_enabled=True
    def lost_ack(*args):
        raise sqlite3.OperationalError('injected ACK checkpoint failure')
    monkeypatch.setattr(first._session,'ack',lost_ack)
    with pytest.raises(sqlite3.OperationalError,match='ACK checkpoint'):
        await bot._event_bridge_subject(None,first,'system.>','bridge')
    assert cursor(first.checkpoint_path,'bridge')==0
    with bot._state.transaction() as db:
        rows=db.execute('SELECT identity FROM outbox').fetchall()
        assert len(rows)==1
        identity=rows[0][0]
        assert identity.startswith('fact:LOCAL_FACTS:')
    if delivered_before_retry:
        bot._state.delivered(identity)
    await first.close()
    second=bus(tmp_path);await second.connect()
    task=asyncio.create_task(bot._event_bridge_subject(None,second,'system.>','bridge'))
    try:
        await until(lambda:cursor(second.checkpoint_path,'bridge')==1)
        with bot._state.transaction() as db:
            assert db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==int(not delivered_before_retry)
            if delivered_before_retry:
                assert db.execute('SELECT COUNT(*) FROM delivered WHERE identity=?',(identity,)).fetchone()[0]==1
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True);await second.close()


@pytest.mark.asyncio
async def test_bus_factory_recovers_when_facts_appear_after_missing_source_startup(tmp_path,monkeypatch):
    created=[]
    ready=asyncio.Event()
    def factory():
        if len(created)==1:facts(tmp_path/'facts.sqlite3',('system.storage_error',))
        instance=bus(tmp_path);created.append(instance)
        return instance
    settings=SimpleNamespace(state_dir=tmp_path/'telegram-state',telegram_user_id='123',env={'TELEGRAM_BOT_TOKEN':'fake'})
    bot=TelegramController(settings,bus_factory=factory)
    async def bridge(_session,current):
        assert current is created[-1] and current.stats['ready']
        ready.set();await asyncio.Event().wait()
    original_sleep=asyncio.sleep
    async def short_sleep(seconds):
        await original_sleep(min(seconds,.01))
    monkeypatch.setattr(bot,'_event_bridge',bridge)
    monkeypatch.setattr('vibetrading.telegram.asyncio.sleep',short_sleep)
    task=asyncio.create_task(bot._event_bridge_loop(None))
    try:
        await asyncio.wait_for(ready.wait(),timeout=2)
        assert len(created)==2 and created[0] is not created[1]
        assert not created[0].stats['ready']
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True)
    assert not created[-1].stats['ready']


@pytest.mark.asyncio
async def test_event_bridge_failure_cancels_all_other_subject_tasks_before_return(tmp_path,monkeypatch):
    bot=controller(tmp_path,SimpleNamespace())
    started=[];closed=[]
    async def subject(_session,_bus,pattern,_durable):
        started.append(pattern)
        try:
            await asyncio.sleep(0)
            if pattern=='system.>':raise RuntimeError('fixture subject failed')
            await asyncio.Event().wait()
        finally:closed.append(pattern)
    monkeypatch.setattr(bot,'_event_bridge_subject',subject)
    with pytest.raises(RuntimeError,match='subject failed'):
        await bot._event_bridge(None,SimpleNamespace())
    assert len(started)==len(closed)==4


def test_telegram_bus_and_factory_are_mutually_exclusive(tmp_path):
    settings=SimpleNamespace(state_dir=tmp_path/'telegram-state',telegram_user_id='123',env={})
    with pytest.raises(ValueError,match='either bus or bus_factory'):
        TelegramController(settings,bus=SimpleNamespace(),bus_factory=lambda:SimpleNamespace())
