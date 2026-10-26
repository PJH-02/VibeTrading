import asyncio
import sqlite3
from dataclasses import replace

import pytest

from portfolio.etf_journal import EtfJournal
from runtime.settings import RuntimeSettings
from operations.telegram import TelegramController
from operations.telegram_state import OutboxFull, TelegramState


def _controller(tmp_path):
    env = tmp_path/".env"
    env.write_text("VIBETRADING_DATA_ROOT="+tmp_path.as_posix(), encoding="utf-8")
    settings = replace(RuntimeSettings.from_env(env), data_root=tmp_path,
        state_dir=tmp_path/"state", telegram_user_id="123",
        env={"TELEGRAM_BOT_TOKEN":"fake","TELEGRAM_USER_ID":"123"})
    return TelegramController(settings)


def test_outbox_is_bounded_deduped_and_retained_after_retry(tmp_path):
    state = TelegramState(tmp_path/"telegram.sqlite3", max_pending=2, max_outbox_bytes=10)
    assert state.enqueue("one", "123", "hello")
    assert not state.enqueue("one", "123", "hello")
    assert state.enqueue("two", "123", "world")
    with pytest.raises(OutboxFull):
        state.enqueue("three", "123", "x")
    row = state.next_message()
    state.retry(row['identity'], 100)
    assert state.next_message()['identity'] == "two"
    state.delivered("two")
    assert not state.enqueue("two", "123", "world")


def test_notification_receipts_never_write_trading_journal_and_pass_first_fifty(tmp_path):
    journal = EtfJournal(tmp_path/"etf/journal.sqlite3")
    for i in range(65):
        journal.notify(str(i), "mock_daily", {"day":"fixture"})
    before = list(journal.db.execute("SELECT * FROM notifications ORDER BY id"))
    state = TelegramState(tmp_path/"telegram.sqlite3")
    first = state.pending_notifications(journal.path if hasattr(journal, "path") else tmp_path/"etf/journal.sqlite3")
    assert len(first) == 50
    for row in first:
        state.delivered("mock:"+row['id'], row['id'])
    assert len(state.pending_notifications(tmp_path/"etf/journal.sqlite3")) == 15
    assert list(journal.db.execute("SELECT * FROM notifications ORDER BY id")) == before
    journal.db.close()


@pytest.mark.asyncio
async def test_gateway_outage_does_not_block_command_receipt_and_reply_is_durable(tmp_path,monkeypatch):
    bot = _controller(tmp_path)
    bot._outbox_enabled = True
    update = {"update_id":42,"message":{"from":{"id":123},"chat":{"id":123},"text":"help"}}
    bot._state.receive(update)
    await bot._handle_pending_update(None, update)
    assert bot._state.pending() == []
    reply = bot._state.next_message()
    assert reply['identity'] == "reply:42"
    async def unavailable(*args):
        raise RuntimeError("gateway offline")
    bot._send_direct = unavailable
    loop=asyncio.get_running_loop()
    retried=asyncio.Event()
    original_retry=bot._state.retry
    def committed_retry(*args):
        original_retry(*args)
        loop.call_soon_threadsafe(retried.set)
    monkeypatch.setattr(bot._state,'retry',committed_retry)
    task = asyncio.create_task(bot._sender_loop(None))
    try:
        # Wait for the real worker commit, rather than assuming that a 30ms
        # timer also means its SQLite write has finished on a busy machine.
        await asyncio.wait_for(retried.wait(),timeout=5)
    finally:
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with bot._state.transaction() as db:
        assert db.execute("SELECT attempts FROM outbox WHERE identity='reply:42'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM delivered").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_allowlisted_user_cannot_run_command_in_unapproved_chat(tmp_path):
    bot = _controller(tmp_path)
    called = []
    async def send(*args):
        called.append(args)
    bot._send = send
    await bot._handle(None, {"message":{"from":{"id":123},"chat":{"id":456},"text":"help"}})
    assert called == []


class _BridgeMessage:
    subject = "system.storage_error"

    def __init__(self, data=None):
        self.data = data or {"kind":"storage_error","error":"fixture"}
        self.acks = 0
        self.naks = []

    async def ack(self):
        self.acks += 1

    async def nak(self, *, delay):
        self.naks.append(delay)


class _BridgeBus:
    def __init__(self, messages):
        self.messages = messages

    async def subscribe(self, *args, **kwargs):
        for message in self.messages:
            yield message


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [sqlite3.OperationalError("disk full"), OSError("disk unavailable"), OutboxFull("full")])
async def test_bridge_storage_failure_retains_original_until_outbox_owns_event(tmp_path, monkeypatch, failure):
    bot = _controller(tmp_path)
    bot._outbox_enabled = True
    message = _BridgeMessage()
    enqueue = bot._state.enqueue

    def fail(*args):
        raise failure

    monkeypatch.setattr(bot._state, "enqueue", fail)
    await bot._event_bridge_subject(None, _BridgeBus([message]), "system.>", "fixture")
    assert message.acks == 0
    assert message.naks == [30]
    monkeypatch.setattr(bot._state, "enqueue", enqueue)
    await bot._event_bridge_subject(None, _BridgeBus([message]), "system.>", "fixture")
    assert message.acks == 1
    assert bot._state.next_message()["text"].endswith("reason=fixture")


@pytest.mark.asyncio
async def test_malformed_timestamp_is_isolated_and_next_event_is_durable(tmp_path):
    bot = _controller(tmp_path)
    bot._outbox_enabled = True
    bad = _BridgeMessage({"kind":"storage_error","created_ts_ns":"invalid"})
    good = _BridgeMessage()
    await bot._event_bridge_subject(None, _BridgeBus([bad, good]), "system.>", "fixture")
    assert bad.acks == good.acks == 1
    assert bad.naks == good.naks == []
    with bot._state.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
