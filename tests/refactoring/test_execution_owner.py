"""Offline-only account owner assertions. Execute from the guarded source copy."""
import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest

from vibetrading.execution_ledger import AccountScope, ExecutionLedger
from vibetrading.execution_owner import ExecutionBudgets, ExecutionOwner, ExecutionReadiness
from vibetrading.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
from vibetrading.runtime_control import GateBlocked, RuntimeControl


class FakeAdapter:
    offline_only = True
    execution_handoff_protocol = "offline-permit-v1"

    def __init__(self):
        self.preparing = None
        self.before_permit = None
        self.after_permit = None
        self.connects = 0
        self.mutations = []

    async def connect(self):
        self.connects += 1
        if self.preparing:
            await self.preparing()

    async def submit_order_with_permit(self, order, permit):
        if self.before_permit:
            await self.before_permit()
        permit()
        self.mutations.append(order.client_order_id)
        if self.after_permit:
            await self.after_permit()
        return {"status": "ACCEPTED", "order_id": "fake-" + order.client_order_id}


def intent(identity="one", quantity="2", **updates):
    base = OrderIntent(intent_id=identity, job_id="strategy", signal_id="signal-" + identity,
        destination=OrderDestination.REAL, client_order_id="client-" + identity,
        instrument=InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_SPOT,
                                raw_symbol="BTCUSDT", std_symbol="BTCUSDT"),
        side=Side.BUY, quantity=Decimal(quantity), limit_price=Decimal("10"),
        metadata={"account_id": "synthetic-account", "environment": "offline"})
    return base.model_copy(update=updates)


def evidence(**changes):
    return {"contract_version": "offline-test-1", "config_version": "fixture-1",
        "engine_incarnation": "synthetic-engine", "apply_seq": 1,
        "event_ids": ["synthetic-event"], "input_versions": {"fixture-feed": 1},
        "expected_epochs": {"global": 1, "account:synthetic-account": 1, "strategy:strategy": 1},
        "quality": "LIVE", "mode": "offline", **changes}


def build_owner(tmp_path, *, adapter=None, resume=True, clock=None):
    now = clock if clock is not None else [1_000_000_000]
    scope = AccountScope("binance", "synthetic-account", "spot", "offline")
    ledger = ExecutionLedger(tmp_path / "orders.sqlite3", scope,
                             max_intents=16, max_evidence_bytes=16384, max_fills=32)
    control = RuntimeControl(ledger)
    transport = adapter if adapter is not None else FakeAdapter()
    budgets = ExecutionBudgets(Decimal("100"), Decimal("1000"), 4,
                               10_000_000_000, 1.0, 1.0)
    owner = ExecutionOwner(ledger, control, transport, budgets, clock=lambda: now[0])
    # Capture the reconciled account version; do not silently refresh it when
    # the ledger learns a fill that this external snapshot has not observed.
    reconciled_version = ledger.account_version()
    ready = lambda: ExecutionReadiness(scope.key, True, True, True, now[0] + 100_000_000,
        Decimal("0"), Decimal("1000"), account_version=reconciled_version,
        allowed_instruments=(intent().instrument.key,),
        market_price_caps=((intent().instrument.key, Decimal("10")),))
    if resume:
        owner.resume("strategy", expected_epochs=control.snapshot("strategy")["epochs"], readiness=ready)
    return owner, transport, ready, now


async def send(owner, ready, now, order=None, **changes):
    return await owner.dispatch(order or intent(), expiry_ns=now[0] + 1_000_000_000,
        evidence=evidence(), reserve_unit=Decimal("10"), readiness=ready, **changes)


@pytest.mark.asyncio
async def test_atomic_claim_and_restart_duplicate_never_resubmit(tmp_path):
    owner, adapter, ready, now = build_owner(tmp_path)
    assert owner.ledger.pragmas() == {"journal_mode": "wal", "synchronous": 2}
    first, duplicate = await asyncio.gather(send(owner, ready, now), send(owner, ready, now))
    assert first["status"] == duplicate["status"] == "SUBMITTED"
    assert duplicate["duplicate"] is True
    assert len(adapter.mutations) == 1
    assert owner.ledger.reserved() == Decimal("20")
    await owner.close()
    reopened, second, ready2, now2 = build_owner(tmp_path, resume=False, clock=now)
    duplicate = await send(reopened, ready2, now2)
    assert duplicate["status"] == "SUBMITTED" and duplicate["duplicate"]
    assert second.mutations == []
    with pytest.raises(GateBlocked, match="Unresolved"):
        reopened.resume("strategy", expected_epochs=reopened.control.snapshot("strategy")["epochs"], readiness=ready2)
    await reopened.close()


@pytest.mark.asyncio
async def test_exact_fill_once_and_ack_after_fill_preserve_terminal_state(tmp_path):
    owner, adapter, ready, now = build_owner(tmp_path)
    async def fill_before_ack():
        assert owner.ledger.apply_fill_once("one", "provider-fill-1", quantity="2", price="10.000000000000000001", fee="0.0001")
    adapter.after_permit = fill_before_ack
    result = await send(owner, ready, now)
    assert result["status"] == "FILLED"
    assert owner.ledger.position(intent().instrument.key) == Decimal("2")
    row = owner.ledger.get("one")
    assert Decimal(row["notional"]) == Decimal("20.000000000000000002")
    assert Decimal(row["fees"]) == Decimal("0.0001")
    assert owner.ledger.reserved() == 0
    assert not owner.ledger.apply_fill_once("one", "provider-fill-1", quantity="2", price="10.000000000000000001", fee="0.0001")
    with pytest.raises(ValueError, match="conflicts"):
        owner.ledger.apply_fill_once("one", "provider-fill-1", quantity="1", price="10", fee="0")
    with pytest.raises(ValueError, match="exceeds"):
        owner.ledger.apply_fill_once("one", "another-fill", quantity="1", price="10", fee="0")
    assert owner.ledger.get("one") == row
    await owner.close()


@pytest.mark.asyncio
async def test_payload_collision_and_risk_limit_cannot_make_new_mutation(tmp_path):
    owner, adapter, ready, now = build_owner(tmp_path)
    await send(owner, ready, now)
    with pytest.raises(ValueError, match="conflicts"):
        await send(owner, ready, now, intent(quantity="3"))
    with pytest.raises(GateBlocked, match="notional"):
        await send(owner, ready, now, intent("large", quantity="11"))
    assert len(adapter.mutations) == 1
    assert owner.ledger.get("large") is None
    await owner.close()


@pytest.mark.asyncio
async def test_one_file_lease_and_account_environment_binding(tmp_path):
    owner, _, _, _ = build_owner(tmp_path)
    with pytest.raises(RuntimeError, match="another process"):
        ExecutionLedger(owner.ledger.path, owner.ledger.scope, max_intents=16, max_evidence_bytes=16384, max_fills=32)
    await owner.close()
    with pytest.raises(ValueError, match="different account"):
        ExecutionLedger(tmp_path / "orders.sqlite3", AccountScope("binance", "other", "spot", "offline"),
                        max_intents=16, max_evidence_bytes=16384, max_fills=32)


@pytest.mark.asyncio
async def test_unverified_readiness_and_real_adapter_are_never_armed(tmp_path):
    owner, adapter, _, now = build_owner(tmp_path, resume=False)
    missing = lambda: ExecutionReadiness(owner.ledger.scope.key, True, False, True, now[0] + 100,
                                         Decimal("0"), Decimal("1000"))
    with pytest.raises(GateBlocked, match="readiness"):
        owner.resume("strategy", expected_epochs=owner.control.snapshot("strategy")["epochs"], readiness=missing)
    assert adapter.connects == 0
    await owner.close()
    class CurrentAdapter:
        async def connect(self):
            raise AssertionError("actual adapter must not be touched")
        async def submit_order(self, order):
            raise AssertionError("actual mutation must not be touched")
    other, _, ready, _ = build_owner(tmp_path / "other", adapter=CurrentAdapter(), resume=False)
    with pytest.raises(GateBlocked, match="Production activation"):
        other.resume("strategy", expected_epochs=other.control.snapshot("strategy")["epochs"], readiness=ready)
    await other.close()


@pytest.mark.asyncio
async def test_new_timefolio_destination_cannot_use_generic_owner(tmp_path):
    owner, adapter, ready, now = build_owner(tmp_path)
    with pytest.raises(GateBlocked, match="destination"):
        await send(owner, ready, now, intent(destination=OrderDestination.TIMEFOLIO))
    assert owner.ledger.get("one") is None
    assert adapter.connects == 0 and adapter.mutations == []
    await owner.close()


@pytest.mark.asyncio
async def test_legacy_created_has_no_absence_of_send_proof(tmp_path):
    owner, adapter, ready, now = build_owner(tmp_path)
    result = await send(owner, ready, now, legacy_present=True)
    assert result["status"] == "LEGACY_UNKNOWN"
    assert adapter.connects == 0 and adapter.mutations == []
    assert owner.ledger.reserved() == Decimal("20")
    with pytest.raises(GateBlocked, match="Unresolved"):
        owner.resume("strategy", expected_epochs=owner.control.snapshot("strategy")["epochs"], readiness=ready)
    await owner.close()


@pytest.mark.asyncio
async def test_limit_price_cannot_exceed_reserved_unit(tmp_path):
    owner, adapter, ready, now = build_owner(tmp_path)
    with pytest.raises(GateBlocked, match="reservation.*limit"):
        await send(owner, ready, now, intent(limit_price=Decimal("1000")))
    assert owner.ledger.get("one") is None and adapter.mutations == []
    await owner.close()


@pytest.mark.asyncio
async def test_account_product_cannot_admit_other_instruments_or_nonlinear_products(tmp_path):
    owner, adapter, ready, now = build_owner(tmp_path)
    other = intent().instrument.model_copy(update={"raw_symbol": "ETHUSDT", "std_symbol": "ETHUSDT"})
    with pytest.raises(GateBlocked, match="instrument scope"):
        await send(owner, ready, now, intent(instrument=other))
    perp = intent().instrument.model_copy(update={"asset_class": AssetClass.CRYPTO_PERP})
    with pytest.raises(GateBlocked, match="cash/spot"):
        await send(owner, ready, now, intent(instrument=perp))
    assert owner.ledger.get("one") is None and adapter.mutations == []
    await owner.close()


@pytest.mark.asyncio
async def test_known_fill_invalidates_older_account_risk_snapshot(tmp_path):
    owner, adapter, ready, now = build_owner(tmp_path)
    await send(owner, ready, now)
    assert owner.ledger.apply_fill_once("one", "scoped-fill", quantity="2", price="10", fee="0")
    with pytest.raises(GateBlocked, match="account version"):
        await send(owner, ready, now, intent("two"))
    assert len(adapter.mutations) == 1 and owner.ledger.get("two") is None
    await owner.close()


@pytest.mark.asyncio
async def test_market_order_requires_explicit_cap_without_changing_order_type(tmp_path):
    owner, adapter, ready, now = build_owner(tmp_path)
    market = intent(limit_price=None)
    for snapshot, reason in (
        (replace(ready(), market_price_caps=()), "market price cap"),
        (replace(ready(), market_price_caps=((market.instrument.key, Decimal("11")),)), "reservation.*cap"),
    ):
        with pytest.raises(GateBlocked, match=reason):
            await send(owner, lambda: snapshot, now, market)
    assert owner.ledger.get("one") is None and adapter.mutations == []
    assert (await send(owner, ready, now, market))["status"] == "SUBMITTED"
    assert '"limit_price":null' in owner.ledger.get("one")["payload"]
    await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["account_version", "instrument_scope"])
async def test_missing_reconciled_scope_facts_block_before_connect(tmp_path, missing):
    owner, adapter, ready, now = build_owner(tmp_path)
    state = replace(ready(), account_version=None) if missing == "account_version" else replace(ready(), allowed_instruments=())
    with pytest.raises(GateBlocked):
        await send(owner, lambda: state, now)
    assert owner.ledger.get("one") is None and adapter.connects == 0
    await owner.close()


@pytest.mark.asyncio
async def test_account_version_fill_dedupe_restart_and_explicit_reconciliation(tmp_path):
    owner, adapter, ready, now = build_owner(tmp_path)
    await send(owner, ready, now)
    owner.control.halt("strategy")
    # Known fills remain facts while gates and risk readiness are blocked.
    assert owner.ledger.account_version() == 0
    assert owner.ledger.apply_fill_once("one", "scope-fill-1", quantity="1", price="10", fee="0")
    assert owner.ledger.account_version() == 1
    assert not owner.ledger.apply_fill_once("one", "scope-fill-1", quantity="1", price="10", fee="0")
    assert owner.ledger.account_version() == 1
    assert owner.ledger.apply_fill_once("one", "scope-fill-2", quantity="1", price="10", fee="0")
    assert owner.ledger.account_version() == 2
    with pytest.raises(GateBlocked, match="account version"):
        owner.resume("strategy", expected_epochs=owner.control.snapshot("strategy")["epochs"], readiness=ready)
    await owner.close()
    reopened, _, ready2, _ = build_owner(tmp_path, resume=False, clock=now)
    assert reopened.ledger.account_version() == 2
    snapshot = replace(ready2(), position_notional=Decimal("20"), available_notional=Decimal("980"))
    reopened.resume("strategy", expected_epochs=reopened.control.snapshot("strategy")["epochs"], readiness=lambda: snapshot)
    await reopened.close()


@pytest.mark.asyncio
async def test_failed_fill_commit_rolls_back_account_version_with_position(tmp_path, monkeypatch):
    owner, _, ready, now = build_owner(tmp_path)
    await send(owner, ready, now)
    before = owner.ledger.get("one")
    original = owner.ledger._commit
    def fail():
        raise OSError("synthetic fill commit failure")
    monkeypatch.setattr(owner.ledger, "_commit", fail)
    with pytest.raises(OSError):
        owner.ledger.apply_fill_once("one", "scope-fill-1", quantity="2", price="10", fee="0")
    monkeypatch.setattr(owner.ledger, "_commit", original)
    assert owner.ledger.get("one") == before
    assert owner.ledger.account_version() == 0
    assert owner.ledger.position(intent().instrument.key) == 0
    assert owner.ledger.apply_fill_once("one", "scope-fill-1", quantity="2", price="10", fee="0")
    assert owner.ledger.account_version() == 1
    await owner.close()
