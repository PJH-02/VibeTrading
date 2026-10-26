"""Local control scope invariants; no external commands, cancellation or flatten."""
import sqlite3
import time

import pytest

from test_execution_owner import build_owner
from runtime.runtime_control import GateBlocked


@pytest.mark.asyncio
@pytest.mark.parametrize('halt_scope,resume_scope',[('global','strategy'),('global','account'),('account','strategy')])
async def test_narrow_resume_cannot_clear_a_broader_halt(tmp_path, halt_scope, resume_scope):
    owner, adapter, ready, _ = build_owner(tmp_path)
    try:
        owner.control.halt('strategy',scope=halt_scope)
        before = owner.control.snapshot('strategy')
        with pytest.raises(GateBlocked,match='broader'):
            owner.resume('strategy',expected_epochs=before['epochs'],readiness=ready,scope=resume_scope)
        assert owner.control.snapshot('strategy') == before
        assert adapter.connects == 0 and adapter.mutations == []
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_startup_requires_explicit_parent_then_child_scope_resume(tmp_path):
    owner, adapter, ready, _ = build_owner(tmp_path,resume=False)
    try:
        keys = ('global','account:synthetic-account','strategy:strategy')
        for index, scope in enumerate(('global','account','strategy')):
            before = owner.control.snapshot('strategy')
            result = owner.resume('strategy',expected_epochs=before['epochs'],readiness=ready,scope=scope)
            assert result['epochs'] == {key:int(i<=index) for i,key in enumerate(keys)}
            assert result['closed'] == {key:i>index for i,key in enumerate(keys)}
        assert adapter.connects == 0 and adapter.mutations == []
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_strategy_resume_does_not_reopen_another_strategy(tmp_path):
    owner, _, ready, _ = build_owner(tmp_path)
    try:
        owner.control.halt('other',scope='strategy')
        owner.control.halt('strategy',scope='strategy')
        before = owner.control.snapshot('other')
        owner.resume('strategy',expected_epochs=owner.control.snapshot('strategy')['epochs'],readiness=ready)
        assert owner.control.snapshot('other') == before
        assert owner.control.snapshot('strategy')['closed']['strategy:strategy'] is False
    finally:
        await owner.close()


def command(identity='halt-1', action='halt', **changes):
    return dict(command_id=identity, action=action, strategy='strategy', scope='strategy',
                expires_ns=time.time_ns() + 60_000_000_000, **changes)


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['halt', 'pause'])
async def test_exact_retry_after_resume_expiry_and_restart_is_only_a_receipt(tmp_path, monkeypatch, action):
    owner, adapter, ready, _ = build_owner(tmp_path)
    request = command(action=action)
    result = owner.control.apply_command(**request)
    assert result == {'command_id': 'halt-1', 'status': 'ACCEPTED', 'action': action,
                      'scope': 'strategy:strategy', 'epoch': 2, 'duplicate': False}
    owner.resume('strategy', expected_epochs=owner.control.snapshot('strategy')['epochs'], readiness=ready)
    before = owner.control.snapshot('strategy')
    monkeypatch.setattr("runtime.runtime_control.time.time_ns", lambda: request['expires_ns'] + 1)
    assert owner.control.apply_command(**request) == {**result, 'duplicate': True}
    assert owner.control.snapshot('strategy') == before
    assert adapter.connects == 0 and adapter.mutations == []
    await owner.close()
    reopened, _, _, _ = build_owner(tmp_path, resume=False)
    try:
        before = reopened.control.snapshot('strategy')
        assert reopened.control.apply_command(**request) == {**result, 'duplicate': True}
        assert reopened.control.snapshot('strategy') == before
        assert all(before['closed'].values())
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_resume_retry_after_later_halt_never_reopens_gate(tmp_path):
    owner, _, _, _ = build_owner(tmp_path)
    try:
        owner.control.halt('strategy', scope='strategy')
        request = command(identity='resume-1', action='resume',
                          expected_epochs=owner.control.snapshot('strategy')['epochs'], readiness_verified=True)
        receipt = owner.control.apply_command(**request)
        assert receipt['epoch'] == 3
        owner.control.halt('strategy', scope='global')
        before = owner.control.snapshot('strategy')
        assert owner.control.apply_command(**request) == {**receipt, 'duplicate': True}
        assert owner.control.snapshot('strategy') == before
    finally:
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [{'action': 'pause'}, {'scope': 'account'}, {'strategy': 'other'},
                                     {'readiness_verified': True}, {'expires_ns': 1}])
async def test_command_id_collision_has_no_effect(tmp_path, changes):
    owner, _, _, _ = build_owner(tmp_path)
    try:
        request = command()
        owner.control.apply_command(**request)
        before = owner.control.snapshot('strategy')
        with pytest.raises(GateBlocked, match='immutable'):
            owner.control.apply_command(**{**request, **changes})
        assert owner.control.snapshot('strategy') == before
        assert owner.ledger._db.execute('SELECT COUNT(*) FROM owner_commands').fetchone()[0] == 1
    finally:
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['expired', 'readiness', 'stale', 'ancestor', 'unresolved'])
async def test_invalid_fresh_command_cannot_commit_effect_or_receipt(tmp_path, failure):
    from test_execution_owner import send

    owner, _, ready, now = build_owner(tmp_path)
    try:
        if failure == 'ancestor':
            owner.control.halt('strategy', scope='global')
        if failure == 'unresolved':
            await send(owner, ready, now)
        before = owner.control.snapshot('strategy')
        request = command(action='resume', expected_epochs=before['epochs'], readiness_verified=True)
        if failure == 'expired': request['expires_ns'] = 1
        if failure == 'readiness': request['readiness_verified'] = False
        if failure == 'stale': request['expected_epochs'] = {key: 99 for key in before['epochs']}
        with pytest.raises(GateBlocked):
            owner.control.apply_command(**request)
        assert owner.control.snapshot('strategy') == before
        assert owner.ledger._db.execute('SELECT COUNT(*) FROM owner_commands').fetchone()[0] == 0
    finally:
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['halt', 'resume'])
async def test_commit_failure_rolls_back_receipt_and_epoch_and_keeps_memory_closed(tmp_path, monkeypatch, action):
    owner, _, _, _ = build_owner(tmp_path)
    if action == 'resume': owner.control.halt('strategy', scope='strategy')
    before = owner.control.snapshot('strategy')
    request = command(action=action)
    if action == 'resume': request.update(expected_epochs=before['epochs'], readiness_verified=True)
    commit = owner.ledger._commit
    def fail(): raise sqlite3.OperationalError('injected COMMIT failure')
    monkeypatch.setattr(owner.ledger, '_commit', fail)
    with pytest.raises(sqlite3.OperationalError, match='COMMIT'):
        owner.control.apply_command(**request)
    monkeypatch.setattr(owner.ledger, '_commit', commit)
    try:
        after = owner.control.snapshot('strategy')
        assert after['epochs'] == before['epochs']
        assert all(after['closed'].values())
        assert owner.ledger._db.execute('SELECT COUNT(*) FROM owner_commands').fetchone()[0] == 0
    finally:
        await owner.close()
    reopened, _, _, _ = build_owner(tmp_path, resume=False)
    try:
        assert reopened.ledger._db.execute('SELECT COUNT(*) FROM owner_commands').fetchone()[0] == 0
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_receipt_capacity_closes_new_risk_but_preserves_exact_retry(tmp_path):
    owner, _, _, _ = build_owner(tmp_path)
    try:
        owner.ledger.max_intents = 1
        request = command()
        receipt = owner.control.apply_command(**request)
        with pytest.raises(GateBlocked, match='budget'):
            owner.control.apply_command(**command(identity='next'))
        assert owner.control.apply_command(**request) == {**receipt, 'duplicate': True}
        assert all(owner.control.snapshot('strategy')['closed'].values())
    finally:
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [{'command_id': 'bad id'}, {'expires_ns': True},
                                     {'readiness_verified': 1}, {'expected_epochs': {'global': True}},
                                     {'action': 'flatten'}, {'command_id': 'x' * 257}])
async def test_malformed_commands_do_not_create_receipts(tmp_path, changes):
    owner, _, _, _ = build_owner(tmp_path)
    try:
        before = owner.control.snapshot('strategy')
        with pytest.raises(ValueError):
            owner.control.apply_command(**{**command(), **changes})
        assert owner.control.snapshot('strategy') == before
        assert owner.ledger._db.execute('SELECT COUNT(*) FROM owner_commands').fetchone()[0] == 0
    finally:
        await owner.close()
