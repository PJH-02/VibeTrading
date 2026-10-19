import asyncio
from decimal import Decimal

import pytest

from test_execution_owner import build_owner, evidence, intent, send
from vibetrading.runtime_control import GateBlocked


@pytest.mark.asyncio
@pytest.mark.parametrize("after_commit", [False, True])
async def test_authorization_commit_failure_or_uncertainty_never_submits(tmp_path, monkeypatch, after_commit):
    owner, adapter, ready, now = build_owner(tmp_path)
    original = owner.ledger._commit
    def fail_authorization():
        row = owner.ledger._db.execute("SELECT state FROM execution_intents WHERE intent_id='one'").fetchone()
        if row and row[0] == "AUTHORIZED":
            if after_commit:
                original()
            raise OSError("synthetic commit failure/uncertainty")
        original()
    monkeypatch.setattr(owner.ledger, "_commit", fail_authorization)
    with pytest.raises(OSError):
        await send(owner, ready, now)
    assert adapter.connects == 0 and adapter.mutations == []
    monkeypatch.setattr(owner.ledger, "_commit", original)
    assert all(owner.control.snapshot("strategy")["closed"].values())
    await owner.close()
    reopened, _, _, _ = build_owner(tmp_path, resume=False)
    row = reopened.ledger.get("one")
    assert (row is not None) is after_commit
    if row:
        assert row["state"] == "UNKNOWN" and Decimal(row["reservation"]) == Decimal("20")
    await reopened.close()


@pytest.mark.asyncio
async def test_ack_loss_keeps_unknown_reservation_and_never_retries(tmp_path):
    owner, adapter, ready, now = build_owner(tmp_path)
    async def lose_ack():
        raise TimeoutError("fake ACK lost after send")
    adapter.after_permit = lose_ack
    first = await send(owner, ready, now)
    second = await send(owner, ready, now)
    assert first["status"] == second["status"] == "UNKNOWN"
    assert second["duplicate"] and len(adapter.mutations) == 1
    assert owner.ledger.reserved() == Decimal("20")
    with pytest.raises(GateBlocked, match="Unresolved"):
        owner.resume("strategy", expected_epochs=owner.control.snapshot("strategy")["epochs"], readiness=ready)
    await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("handed_off", [False, True])
async def test_cancellation_distinguishes_before_and_after_handoff(tmp_path, handed_off):
    owner, adapter, ready, now = build_owner(tmp_path)
    entered = asyncio.Event()
    async def wait_forever():
        entered.set()
        await asyncio.Event().wait()
    if handed_off:
        adapter.after_permit = wait_forever
    else:
        adapter.before_permit = wait_forever
    task = asyncio.create_task(send(owner, ready, now))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = owner.ledger.get("one")
    assert row["state"] == ("UNKNOWN" if handed_off else "PRE_SEND_FAILED")
    assert owner.ledger.reserved() == (Decimal("20") if handed_off else Decimal("0"))
    assert len(adapter.mutations) == int(handed_off)
    await owner.close()


@pytest.mark.asyncio
async def test_observation_failure_before_owner_is_not_handoff(tmp_path, monkeypatch):
    from vibetrading.real_runner import RealExecutionRunner, RealExecutionRunnerConfig
    from vibetrading.settings import RuntimeSettings
    from vibetrading.sqlite_store import RuntimeStore
    from vibetrading.models import Signal, SignalAction
    owner, adapter, ready, now = build_owner(tmp_path / "owner")
    monkeypatch.setattr("vibetrading.execution.utc_now_ns", lambda: now[0])
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\nLIVE_ORDER_ENABLED=true\nLIVE_ORDER_ARMED_SYNTHETIC-ACCOUNT=1\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env)
    store = RuntimeStore(settings, tmp_path / "catalog.sqlite3")
    class Observation:
        async def publish_order_intent(self, value):
            raise RuntimeError("fake reporting outage")
    config = RealExecutionRunnerConfig("strategy", "synthetic-account", "binance", Decimal("100"), Decimal("1000"),
                                       default_quantity=Decimal("2"), submit=True, execution_environment="offline")
    runner = RealExecutionRunner(config, store=store, bus=Observation(), broker=adapter, owner=owner, readiness=ready, settings=settings)
    signal = Signal(strategy_id="strategy", instrument=intent().instrument, action=SignalAction.ENTER_LONG,
                    event_ts_ns=now[0], reference_price=Decimal("10"), valid_until_ts_ns=now[0]+1_000_000_000,
                    metadata={"decision_evidence": evidence()})
    result = await runner._handle_signal(signal)
    assert result["status"] == "SUBMITTED" and len(adapter.mutations) == 1
    assert (await runner._handle_signal(signal))["duplicate"]
    assert len(adapter.mutations) == 1
    settings.env["LIVE_ORDER_ARMED_SYNTHETIC-ACCOUNT"] = "0"
    changed = signal.model_copy(update={"signal_id": "second"})
    assert (await runner._handle_signal(changed))["status"] == "BLOCKED"
    assert len(adapter.mutations) == 1
    store.close()
    await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_status", ["created", "submitted", "unknown"])
async def test_dry_run_never_overwrites_legacy_execution_facts(tmp_path, monkeypatch, legacy_status):
    from vibetrading.real_runner import RealExecutionRunner, RealExecutionRunnerConfig
    from vibetrading.execution import AccountExecutionConfig
    from vibetrading.settings import RuntimeSettings
    from vibetrading.sqlite_store import RuntimeStore
    from vibetrading.models import Signal, SignalAction, OrderDestination, Side
    now = 1_000_000_000
    monkeypatch.setattr("vibetrading.execution.utc_now_ns", lambda: now)
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env)
    store = RuntimeStore(settings, tmp_path / "catalog.sqlite3")
    class Observation:
        async def publish_order_intent(self, value):
            pass
    config = RealExecutionRunnerConfig("strategy", "synthetic-account", "binance", Decimal("100"), Decimal("1000"),
                                       default_quantity=Decimal("2"), execution_environment="offline")
    runner = RealExecutionRunner(config, store=store, bus=Observation(), settings=settings)
    signal = Signal(strategy_id="strategy", instrument=intent().instrument, action=SignalAction.ENTER_LONG,
        event_ts_ns=now, reference_price=Decimal("10"), valid_until_ts_ns=now+1_000_000_000)
    original = runner.intent_factory.from_signal(signal, AccountExecutionConfig("strategy", OrderDestination.REAL,
        "synthetic-account", Decimal("100"), Decimal("1000"), environment="offline"), side=Side.BUY, quantity=Decimal("2"))
    store.update_order_intent(original.intent_id, legacy_status, {"durable_fact": "must survive dry run"})
    before = tuple(store._conn.execute("SELECT status,payload FROM order_intents WHERE intent_id=?", (original.intent_id,)).fetchone())
    assert (await runner._handle_signal(signal))["status"] == "DUPLICATE_SIGNAL"
    after = tuple(store._conn.execute("SELECT status,payload FROM order_intents WHERE intent_id=?", (original.intent_id,)).fetchone())
    assert after == before
    store.close()


@pytest.mark.asyncio
async def test_generic_runner_keeps_quiet_subscription_alive_and_zero_duration_is_finite(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from vibetrading.real_runner import RealExecutionRunner, RealExecutionRunnerConfig
    from vibetrading.settings import RuntimeSettings
    from vibetrading.sqlite_store import RuntimeStore
    from vibetrading.models import Signal, SignalAction
    now = 1_000_000_000
    monkeypatch.setattr("vibetrading.execution.utc_now_ns", lambda: now)
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env)
    store = RuntimeStore(settings, tmp_path / "catalog.sqlite3")
    signal = Signal(strategy_id="strategy", instrument=intent().instrument, action=SignalAction.ENTER_LONG,
        event_ts_ns=now, reference_price=Decimal("10"), valid_until_ts_ns=now+1_000_000_000)
    class Bus:
        def __init__(self):
            self.acked = 0
            self.closed = self.iterator_closed = False
        async def connect(self):
            pass
        async def close(self):
            self.closed = True
        async def publish_order_intent(self, value):
            pass
        async def subscribe(self, *args, **kwargs):
            try:
                await asyncio.sleep(1.02)
                async def ack():
                    self.acked += 1
                async def nak(**kwargs):
                    raise AssertionError("valid dry-run message must be acknowledged")
                yield SimpleNamespace(data=signal.model_dump(mode="json"), ack=ack, nak=nak)
                await asyncio.Event().wait()
            finally:
                self.iterator_closed = True
    config = RealExecutionRunnerConfig("strategy", "synthetic-account", "binance", Decimal("100"), Decimal("1000"), default_quantity=Decimal("2"))
    bus = Bus()
    runner = RealExecutionRunner(config, store=store, bus=bus, settings=settings)
    assert (await runner.run(1.15))[0]["status"] == "DRY_RUN"
    assert bus.acked == 1 and bus.closed and bus.iterator_closed
    zero = Bus()
    runner = RealExecutionRunner(config, store=store, bus=zero, settings=settings)
    assert await runner.run(0) == []
    assert zero.closed and zero.acked == 0
    store.close()
