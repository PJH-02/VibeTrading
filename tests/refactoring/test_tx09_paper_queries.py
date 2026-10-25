"""TX09: cold Telegram paper queries beside the existing owner control path.

The query tests read the real VN15 Paper database and the real local market
snapshot.  The joint test uses the existing Telegram inbox/outbox, LocalTransport
UDS server and ExecutionOwner; no Telegram HTTP, provider or live account exists.
"""
import asyncio
from dataclasses import asdict, replace
from decimal import Decimal
import json
import sqlite3
import time

import pytest

from test_execution_owner import build_owner
from test_outbox_isolation import _controller
from test_vnext_vn14b import endpoint, update
from test_vn15_paper_projection import (T, _write_market_snapshot, book, engine,
                                        open_ledger, submit, trade)
from vibetrading.telegram import TelegramController


def paper_bot(tmp_path, database=None, *, reader=None):
    base = _controller(tmp_path)
    env = dict(base.settings.env)
    if database is not None:
        env["PAPER_PROJECTION_DATABASES"] = json.dumps([str(database)])
    settings = replace(base.settings, env=env)
    return TelegramController(settings, projection_reader=reader)


def query_update(command, identity=123, update_id=1):
    return {"update_id": update_id, "message": {"from": {"id": identity},
            "chat": {"id": identity}, "date": int(time.time()), "text": command}}


def paper_ledger(tmp_path):
    ledger = open_ledger(tmp_path / "owner")
    ledger.seed_balance("USDT", Decimal("100000"), {"fixture": "paper only"})
    matcher = engine(ledger)
    matcher.on_book(book())
    submit(matcher, "paper-order", quantity=2)
    matcher.advance(T + 10)
    matcher.on_trade(trade(quantity=6))
    ledger.cancel("paper-order")
    return ledger


@pytest.mark.parametrize("invalid", [object(), 3, "orders"])
def test_projection_reader_must_be_synchronous_callable(tmp_path, invalid):
    base = _controller(tmp_path)
    with pytest.raises(TypeError):
        TelegramController(base.settings, projection_reader=invalid)


def test_async_projection_reader_is_rejected_at_construction(tmp_path):
    base = _controller(tmp_path)

    async def reader(command):
        return {}

    class AsyncCallable:
        async def __call__(self, command):
            return {}

    for invalid in (reader, AsyncCallable()):
        with pytest.raises(TypeError):
            TelegramController(base.settings, projection_reader=invalid)


@pytest.mark.asyncio
async def test_explicit_vn15_database_answers_each_query_with_labels(tmp_path):
    ledger = paper_ledger(tmp_path)
    try:
        bot = paper_bot(tmp_path, ledger.path)
        sent = []

        async def capture(_session, chat_id, text):
            sent.append((chat_id, text))

        bot._send = capture
        for command in ("strategies", "orders", "positions", "pnl"):
            before = len(sent)
            await bot._handle(None, query_update("/" + command))
            assert len(sent) == before + 1
            reply = json.loads(sent[-1][1])
            assert reply["status"] == "OK"
            assert reply["mode"] == "paper" and reply["evidence"] == "PAPER_SIMULATION"
            assert reply["freshness"] == "UNVERIFIED"
            assert reply[command]
            assert all(row["mode"] == "paper" and row["evidence"] == "PAPER_SIMULATION"
                       for row in reply[command])

        strategies = json.loads(sent[0][1])["strategies"]
        assert strategies[0]["running"] is None and strategies[0]["status"] == "UNVERIFIED"
        orders = json.loads(sent[1][1])["orders"]
        assert orders[0]["status"] == "UNKNOWN"
        assert orders[0]["recovery"] == "UNRECONCILED"
        positions = json.loads(sent[2][1])["positions"]
        assert positions[0]["quantity"] == "1"
        pnl = json.loads(sent[3][1])["pnl"]
        assert {row["currency"] for row in pnl} == {"USDT"}
        for row in pnl:
            assert row["mark"] is None
            assert row["unrealized_pnl"] is None
            assert row["unrealized_pnl_status"] == "UNAVAILABLE"
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_unconfigured_paper_query_is_explicit_unavailable(tmp_path):
    bot = paper_bot(tmp_path)
    reply = await bot._projection_reply("orders")
    payload = json.loads(reply)
    assert payload["status"] == "UNAVAILABLE"
    assert payload["freshness"] == "UNAVAILABLE"
    assert payload["errors"][0]["reason"] == "not_configured"


@pytest.mark.asyncio
async def test_feeds_read_real_snapshot_and_stale_snapshot_is_unavailable(tmp_path):
    path, limits = _write_market_snapshot(tmp_path)
    base = _controller(tmp_path)
    settings = replace(base.settings, env={**base.settings.env,
        "LOCAL_MARKET_SNAPSHOT_FILE": str(path),
        "LOCAL_SNAPSHOT_BUDGETS": json.dumps(asdict(limits))})
    bot = TelegramController(settings)
    reply = json.loads(await bot._projection_reply("feeds"))
    assert reply["status"] == "OK" and reply["freshness"] == "FRESH"
    assert reply["evidence"] == "MARKET_SNAPSHOT"
    assert reply["markets"][0]["symbol"] == "069500"
    assert reply["markets"][0]["book_valid"] is True

    document = json.loads(path.read_text(encoding="utf-8"))
    document["generated_ns"] = time.time_ns() - 10**18
    path.write_text(json.dumps(document), encoding="utf-8")
    stale = json.loads(await bot._projection_reply("feeds"))
    assert stale["status"] == "UNAVAILABLE"
    assert stale["freshness"] == "UNAVAILABLE"
    assert stale["errors"][0]["reason"] == "snapshot_unavailable"


@pytest.mark.asyncio
async def test_paper_order_reply_never_echoes_private_payload(tmp_path):
    ledger = paper_ledger(tmp_path)
    try:
        with sqlite3.connect(ledger.path) as db:
            db.execute("UPDATE portfolio_orders SET payload=? WHERE intent_id=?",
                       (json.dumps({"api_key": "must-not-leak",
                                    "secret": "must-not-leak"}), "paper-order"))
        bot = paper_bot(tmp_path, ledger.path)
        reply = await bot._projection_reply("orders")
        assert "must-not-leak" not in reply
        assert json.loads(reply)["orders"][0]["intent_id"] == "paper-order"
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_reader_failure_malformed_oversized_and_nonfinite_are_fixed_unavailable(tmp_path):
    base = _controller(tmp_path)

    def raises(command):
        raise RuntimeError("must-not-leak")

    def malformed(command):
        return ["not", "a", "dict"]

    def oversized(command):
        return {"orders": [{"intent_id": "x" * 300} for _ in range(40)]}

    def nonfinite(command):
        return {"orders": [{"quantity": float("nan")}]}

    expectations = [(raises, "reader_failed"), (malformed, "reader_failed"),
                    (nonfinite, "reader_failed"),
                    (oversized, "response_budget_exceeded")]
    for reader, reason in expectations:
        bot = TelegramController(base.settings, projection_reader=reader)
        reply = await bot._projection_reply("orders")
        payload = json.loads(reply)
        assert payload["status"] == "UNAVAILABLE"
        assert payload["errors"][0]["reason"] == reason
        assert "must-not-leak" not in reply
        assert len(reply.encode("utf-8")) <= 3500


@pytest.mark.asyncio
async def test_slow_synchronous_reader_does_not_block_the_event_loop(tmp_path):
    def slow(command):
        time.sleep(0.25)
        return {"orders": []}

    bot = TelegramController(_controller(tmp_path).settings, projection_reader=slow)
    task = asyncio.create_task(bot._projection_reply("orders"))
    ticked = asyncio.Event()

    async def tick():
        await asyncio.sleep(0.02)
        ticked.set()

    await asyncio.wait_for(tick(), timeout=0.15)
    assert ticked.is_set() and not task.done()
    assert json.loads(await task)["status"] == "OK"


@pytest.mark.asyncio
@pytest.mark.parametrize("sender,chat", [(True, 123), ("123", 123), (456, 123), (123, 456)])
async def test_query_commands_use_the_same_numeric_acl(tmp_path, sender, chat):
    bot = paper_bot(tmp_path)
    sent = []

    async def capture(_session, chat_id, text):
        sent.append((chat_id, text))

    bot._send = capture
    item = query_update("orders", identity=123)
    item["message"]["from"]["id"] = sender
    item["message"]["chat"]["id"] = chat
    await bot._handle(None, item)
    assert sent == []


@pytest.mark.asyncio
async def test_status_and_trade_regressions_and_bounded_paper_status_summary(tmp_path):
    ledger = paper_ledger(tmp_path)
    try:
        plain = paper_bot(tmp_path)
        sent = []

        async def capture(_session, chat_id, text):
            sent.append((chat_id, text))

        plain._send = capture
        await plain._handle(None, query_update("status"))
        assert sent[-1][1].startswith("📊 서버")
        assert "\npaper:" not in sent[-1][1]
        await plain._handle(None, query_update("trade"))
        assert sent[-1][0] == 123 and sent[-1][1]

        bot = paper_bot(tmp_path, ledger.path)
        bot._send = capture
        await bot._handle(None, query_update("status"))
        assert sent[-1][1].startswith("📊 서버")
        assert "\npaper: " in sent[-1][1]
        summary = json.loads(sent[-1][1].split("\npaper: ", 1)[1])
        assert summary["status"] == "OK"
        assert summary["accounts_count"] == 1 and summary["orders_count"] == 1
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_reporting_failure_is_independent_of_control_and_owner_effect(tmp_path, monkeypatch):
    owner, adapter, ready, _ = build_owner(tmp_path)

    def broken(command):
        raise RuntimeError("reporting must not touch owner")

    try:
        async with endpoint(tmp_path, lambda body: owner.apply_command(body, readiness=ready)) as (_, send):
            base = _controller(tmp_path)
            bot = TelegramController(base.settings, command_handler=send,
                                     command_ttl_seconds=60, projection_reader=broken)
            bot._outbox_enabled = True
            await bot._handle(None, query_update("orders"))
            query_reply = json.loads(bot._state.next_message()["text"])
            assert query_reply["status"] == "UNAVAILABLE"
            assert owner.ledger._db.execute(
                "SELECT COUNT(*) FROM owner_commands").fetchone()[0] == 0
            before = owner.control.snapshot("strategy")

            item = update()
            bot._state.receive(item)
            await bot._handle(None, item)
            after = owner.control.snapshot("strategy")
            assert after != before and after["closed"]["strategy:strategy"]
            assert owner.ledger._db.execute(
                "SELECT COUNT(*) FROM owner_commands").fetchone()[0] == 1
            assert adapter.connects == 0 and adapter.mutations == []
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_reply_outage_retry_keeps_one_owner_effect_and_dedups(tmp_path, monkeypatch):
    owner, adapter, ready, _ = build_owner(tmp_path)
    try:
        async with endpoint(tmp_path, lambda body: owner.apply_command(body, readiness=ready)) as (_, send):
            base = _controller(tmp_path)
            bot = TelegramController(base.settings, command_handler=send, command_ttl_seconds=60)
            bot._outbox_enabled = True
            item = update()
            bot._state.receive(item)
            def lost(*args):
                raise OSError("reply storage lost")
            monkeypatch.setattr(bot._state, "enqueue", lost)
            with pytest.raises(OSError):
                await bot._handle(None, item)
            once = owner.control.snapshot("strategy")
            assert once["epochs"]["strategy:strategy"] == 2
            assert len(bot._state.pending()) == 1
            assert owner.ledger._db.execute(
                "SELECT COUNT(*) FROM owner_commands").fetchone()[0] == 1

            restarted = TelegramController(base.settings, command_handler=send,
                                           command_ttl_seconds=120)
            restarted._outbox_enabled = True
            await restarted._handle_pending_update(None, item)
            assert restarted._state.pending() == []
            assert owner.control.snapshot("strategy") == once
            assert restarted._state.next_message()["text"].startswith("명령 ACCEPTED")
            assert adapter.connects == 0 and adapter.mutations == []
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_invalid_acl_and_expired_control_reporting_never_commit(tmp_path):
    owner, _, ready, _ = build_owner(tmp_path)
    try:
        async with endpoint(tmp_path, lambda body: owner.apply_command(body, readiness=ready)) as (_, send):
            base = _controller(tmp_path)
            bot = TelegramController(base.settings, command_handler=send, command_ttl_seconds=60)
            bot._outbox_enabled = True
            unapproved = update()
            unapproved["message"]["from"]["id"] = 456
            await bot._handle(None, unapproved)
            assert bot._state.next_message() is None

            expired = update()
            expired["message"]["date"] -= 1000
            bot._state.receive(expired)
            await bot._handle(None, expired)
            assert "ACCEPTED" not in bot._state.next_message()["text"]
            assert owner.ledger._db.execute(
                "SELECT COUNT(*) FROM owner_commands").fetchone()[0] == 0
    finally:
        await owner.close()
