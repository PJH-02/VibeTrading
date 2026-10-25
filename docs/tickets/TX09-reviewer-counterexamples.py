"""Codex failure boundary: unauthorized/failed cold reads never reach an owner."""
from dataclasses import replace
import pytest
from test_outbox_isolation import _controller
from vibetrading.telegram import TelegramController

@pytest.mark.asyncio
async def test_failed_projection_keeps_secrets_private_and_never_dispatches_control(tmp_path):
    calls=[];replies=[]
    def reader(command):
        raise RuntimeError("do-not-expose-credential-marker")
    async def handler(body):
        calls.append(body)
        raise AssertionError("a read query cannot invoke control")
    bot=TelegramController(_controller(tmp_path).settings,projection_reader=reader,
        command_handler=handler,command_ttl_seconds=60)
    async def send(session,chat_id,text):replies.append(text)
    bot._send=send
    await bot._handle(None,{"message":{"from":{"id":123},"chat":{"id":123},"text":"/orders"}})
    assert not calls and len(replies)==1
    assert "UNAVAILABLE" in replies[0] and "credential-marker" not in replies[0]
    assert len(replies[0].encode())<=3500
    await bot._handle(None,{"message":{"from":{"id":True},"chat":{"id":123},"text":"/orders"}})
    assert len(replies)==1 and not calls

@pytest.mark.asyncio
async def test_oversized_projection_is_explicit_unavailable_not_silent_partial_money(tmp_path):
    replies=[]
    bot=TelegramController(_controller(tmp_path).settings,
        projection_reader=lambda command:{"amount":"9"*4000})
    async def send(session,chat_id,text):replies.append(text)
    bot._send=send
    await bot._handle(None,{"message":{"from":{"id":123},"chat":{"id":123},"text":"/pnl"}})
    assert len(replies)==1 and "UNAVAILABLE" in replies[0]
    assert len(replies[0].encode())<=3500


@pytest.mark.asyncio
async def test_missing_configured_source_is_not_reported_as_ok_empty_pnl(tmp_path):
    import json
    settings=_controller(tmp_path).settings
    settings=replace(settings,env={**settings.env,"PAPER_PROJECTION_DATABASES":json.dumps([str(tmp_path/"absent.sqlite")])})
    bot=TelegramController(settings)
    result=json.loads(await bot._projection_reply("pnl"))
    assert result["status"]=="UNAVAILABLE" and result["errors"]

@pytest.mark.asyncio
async def test_known_account_gap_remains_visible_in_pnl_header(tmp_path):
    import json
    from test_strategy_runtime import ledger_at
    ledger=ledger_at(tmp_path)
    try:
        ledger._db.execute("INSERT INTO portfolio_gaps VALUES(?,?)",("@review_gap",'{}'))
        settings=_controller(tmp_path).settings
        settings=replace(settings,env={**settings.env,"PAPER_PROJECTION_DATABASES":json.dumps([str(ledger.path)])})
        before=tuple(ledger._db.iterdump())
        bot=TelegramController(settings)
        result=json.loads(await bot._projection_reply("pnl"))
        assert result.get("recovery")=="UNRECONCILED"
        assert tuple(ledger._db.iterdump())==before
    finally:
        ledger.close()
