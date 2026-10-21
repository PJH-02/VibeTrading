import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest

from test_execution_owner import build_owner, evidence, intent
from vibetrading.runtime_control import GateBlocked


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["connect", "before_permit"])
@pytest.mark.parametrize("change", ["halt", "expiry", "external_guard"])
async def test_gate_rechecked_after_every_pre_mutation_await(tmp_path, boundary, change):
    owner, adapter, ready, now = build_owner(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked():
        entered.set()
        await release.wait()
    if boundary == "connect":
        adapter.preparing = blocked
    else:
        adapter.before_permit = blocked
    killed = []
    expiry = now[0] + 1_000_000_000
    task = asyncio.create_task(owner.dispatch(intent(), expiry_ns=expiry, evidence=evidence(),
        reserve_unit=Decimal("10"), readiness=ready, guard=lambda: killed))
    await entered.wait()
    if change == "halt":
        accepted = owner.control.halt("strategy")
        assert accepted["status"] == "ACCEPTED" and not accepted["completed"]
    elif change == "expiry":
        now[0] = expiry
    else:
        killed.append("kill_switch_active")
    release.set()
    result = await task
    assert result["status"] == "BLOCKED"
    assert adapter.mutations == []
    assert owner.ledger.reserved() == 0
    await owner.close()


@pytest.mark.asyncio
async def test_stale_resume_and_restart_are_closed(tmp_path):
    owner, _, ready, _ = build_owner(tmp_path)
    before = owner.control.snapshot("strategy")["epochs"]
    owner.control.halt("strategy")
    with pytest.raises(GateBlocked, match="stale"):
        owner.resume("strategy", expected_epochs=before, readiness=ready)
    current = owner.control.snapshot("strategy")["epochs"]
    owner.resume("strategy", expected_epochs=current, readiness=ready,scope='global')
    await owner.close()
    reopened, _, _, _ = build_owner(tmp_path, resume=False)
    assert all(reopened.control.snapshot("strategy")["closed"].values())
    await reopened.close()


@pytest.mark.asyncio
async def test_old_decision_epochs_cannot_be_refreshed_after_halt_and_resume(tmp_path):
    owner, adapter, ready, now = build_owner(tmp_path)
    old_evidence = evidence(expected_epochs=owner.control.snapshot("strategy")["epochs"])
    owner.control.halt("strategy")
    owner.resume("strategy", expected_epochs=owner.control.snapshot("strategy")["epochs"], readiness=ready,scope='global')
    with pytest.raises(GateBlocked, match="epoch vector is stale"):
        await owner.dispatch(intent(), expiry_ns=now[0]+1_000_000_000, evidence=old_evidence,
                             reserve_unit=Decimal("10"), readiness=ready)
    assert adapter.connects == 0 and adapter.mutations == []
    assert owner.ledger.get("one") is None
    await owner.close()


@pytest.mark.asyncio
async def test_halt_commit_failure_keeps_memory_gate_closed(tmp_path, monkeypatch):
    owner, _, ready, _ = build_owner(tmp_path)
    original = owner.ledger._commit
    def fail():
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(owner.ledger, "_commit", fail)
    with pytest.raises(OSError):
        owner.control.halt("strategy")
    monkeypatch.setattr(owner.ledger, "_commit", original)
    assert all(owner.control.snapshot("strategy")["closed"].values())
    with pytest.raises(GateBlocked, match="healthy storage"):
        owner.resume("strategy", expected_epochs=owner.control.snapshot("strategy")["epochs"], readiness=ready)
    await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["connect", "before_permit"])
async def test_revised_market_price_cap_is_checked_at_mutation_seam(tmp_path, boundary):
    owner, adapter, ready, now = build_owner(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked():
        entered.set()
        await release.wait()
    if boundary == "connect":
        adapter.preparing = blocked
    else:
        adapter.before_permit = blocked
    state = [ready()]
    task = asyncio.create_task(owner.dispatch(intent(limit_price=None),
        expiry_ns=now[0] + 1_000_000_000, evidence=evidence(), reserve_unit=Decimal("10"), readiness=lambda: state[0]))
    await entered.wait()
    state[0] = replace(state[0], market_price_caps=((intent().instrument.key, Decimal("11")),))
    release.set()
    result = await task
    assert result["status"] == "BLOCKED" and adapter.mutations == []
    assert owner.ledger.reserved() == 0
    await owner.close()


@pytest.mark.asyncio
async def test_fill_during_preparation_invalidates_previously_accepted_snapshot(tmp_path):
    from test_execution_owner import send
    owner, adapter, ready, now = build_owner(tmp_path)
    await send(owner, ready, now)
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked():
        entered.set()
        await release.wait()
    adapter.before_permit = blocked
    task = asyncio.create_task(send(owner, ready, now, intent("two")))
    await entered.wait()
    assert owner.ledger.apply_fill_once("one", "scoped-fill", quantity="2", price="10", fee="0")
    release.set()
    result = await task
    assert result["status"] == "BLOCKED" and len(adapter.mutations) == 1
    assert owner.ledger.account_version() == 1 and owner.ledger.reserved() == 0
    await owner.close()
