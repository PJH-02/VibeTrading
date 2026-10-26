"""TX01 regression cases for the existing Signal/OrderIntent PAPER binding.

The production runtime, matcher, and ledger are reused unchanged; these cases
only exercise the NEW-action contract guards and the existing dispatch path.
"""
from dataclasses import replace
from decimal import Decimal as D
import json

import pytest

from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Signal, SignalAction, Venue
from vibetrading.trading_contracts import ActionKind, ExecutionAction, TradingMode

from test_strategy_runtime import (BTC, T, book, deliver, ledger_at, matcher_for,
                                   registration, runtime)


SID = "alpha"


def host_for(tmp_path, *, instrument=BTC, reference=None):
    ledger = ledger_at(tmp_path, instrument)
    ledger.seed_balance(instrument.currency, D("100000"), {"source": "tx01 fixture"})
    matcher = matcher_for(ledger)
    now = [T]
    host = runtime(ledger, matcher, now, instrument=instrument, reference=reference)
    host.register(registration(ledger, SID, [], instrument=instrument, reference=reference))
    return ledger, matcher, now, host


def signal_for(instrument=BTC, *, event_ts=T, valid_until=T+10_000, signal_id=None,
               metadata=None):
    signal = Signal(strategy_id=SID, instrument=instrument, action=SignalAction.ENTER_LONG,
        event_ts_ns=event_ts, reference_price=D("100"), valid_until_ts_ns=valid_until,
        source_event_id="source-1", metadata=metadata or {})
    return signal if signal_id is None else signal.model_copy(update={"signal_id": signal_id})


def action_for(ledger, signal, *, instrument=BTC, created_ts_ns=None, intent_id="intent-1",
               client_order_id="client-1", signal_id=None, action_id="action-1", expiry=None,
               metadata=None, quantity=D("1"), limit_price=D("99")):
    intent = OrderIntent(intent_id=intent_id, job_id=SID,
        signal_id=signal.signal_id if signal_id is None else signal_id,
        destination=OrderDestination.REAL, instrument=instrument, side=Side.BUY,
        created_ts_ns=signal.event_ts_ns, client_order_id=client_order_id,
        quantity=quantity, limit_price=limit_price,
        metadata=metadata or {"source": "tx01", "weight": D("0.5")})
    if created_ts_ns is not None:
        # Bypass model coercion so bool/non-integer wire values reach validation.
        intent = intent.model_copy(update={"created_ts_ns": created_ts_ns})
    return ExecutionAction(action_id, ActionKind.NEW, ledger.scope, SID, "config-1",
        TradingMode.PAPER, signal.valid_until_ts_ns if expiry is None else expiry,
        intent=intent, post_only=True)


def action_receipts(ledger):
    rows = ledger._db.execute(
        "SELECT document FROM portfolio_receipts WHERE kind='strategy_event'")
    documents = [json.loads(row[0]) for row in rows]
    return [document for document in documents
            if document["event_id"].startswith("@action:")]


def assert_validate_pure(ledger, matcher, before_dump, before_matcher):
    assert "\n".join(ledger._db.iterdump()) == before_dump
    assert matcher.report() == before_matcher


@pytest.mark.parametrize("intent_changes,signal_changes,match", [
    ({"raw_symbol": "BTCUSD", "currency": "USD"}, None, "execution product"),
    ({"exchange": "SYNTHETIC", "security_kind": "different"}, None, "execution product"),
    (None, {"currency": "USD"}, "reference feed"),
    (None, {"raw_symbol": "BTC-OTHER", "exchange": "SYNTHETIC"}, "reference feed"),
])
def test_new_binding_requires_full_instrument_identity(tmp_path, intent_changes,
                                                       signal_changes, match):
    ledger, matcher, now, host = host_for(tmp_path)
    try:
        signal_instrument = BTC.model_copy(update=signal_changes or {})
        intent_instrument = BTC.model_copy(update=intent_changes or {})
        signal = signal_for(signal_instrument)
        action = action_for(ledger, signal, instrument=intent_instrument)
        before_dump = "\n".join(ledger._db.iterdump())
        before_matcher = matcher.report()
        with pytest.raises(ValueError, match=match):
            host._validate(host._jobs[SID], action, signal, T)
        assert_validate_pure(ledger, matcher, before_dump, before_matcher)
    finally:
        ledger.close()


def test_reference_binding_compares_signal_to_reference_not_execution_instrument(tmp_path):
    execution = InstrumentId(venue=Venue.KIS, asset_class=AssetClass.ETF,
        raw_symbol="069500", std_symbol="069500", currency="KRW")
    reference = execution.model_copy(update={"venue": Venue.KIWOOM})
    ledger, matcher, now, host = host_for(tmp_path, instrument=execution, reference=reference)
    try:
        job = host._jobs[SID]
        valid_signal = signal_for(reference)
        valid_action = action_for(ledger, valid_signal, instrument=execution)
        document = host._validate(job, valid_action, valid_signal, T)
        assert document["intent"]["instrument"]["venue"] == "kis"
        bad_signal = signal_for(execution)
        bad_action = action_for(ledger, bad_signal, instrument=execution)
        with pytest.raises(ValueError, match="reference feed"):
            host._validate(job, bad_action, bad_signal, T)
    finally:
        ledger.close()


@pytest.mark.parametrize("created_ts_ns", [T+1, T-1, True, T+0.5, "not-an-int", -1])
def test_new_creation_time_must_be_causally_bounded(tmp_path, created_ts_ns):
    ledger, matcher, now, host = host_for(tmp_path)
    try:
        signal = signal_for()
        action = action_for(ledger, signal, created_ts_ns=created_ts_ns)
        before_dump = "\n".join(ledger._db.iterdump())
        before_matcher = matcher.report()
        with pytest.raises(ValueError, match="causally bounded"):
            host._validate(host._jobs[SID], action, signal, T)
        assert_validate_pure(ledger, matcher, before_dump, before_matcher)
    finally:
        ledger.close()


@pytest.mark.parametrize("now", [True, -1, T+0.5, "not-an-int", None])
def test_new_validation_boundary_rejects_invalid_now(tmp_path, now):
    ledger, matcher, now_host, host = host_for(tmp_path)
    try:
        signal = signal_for()
        action = action_for(ledger, signal)
        with pytest.raises(ValueError, match="nonnegative integer"):
            host._validate(host._jobs[SID], action, signal, now)
    finally:
        ledger.close()


@pytest.mark.parametrize("field", ["intent_id", "client_order_id", "signal_id"])
@pytest.mark.parametrize("value", ["", "x" * 513])
def test_new_identities_are_bounded(tmp_path, field, value):
    ledger, matcher, now, host = host_for(tmp_path)
    try:
        if field == "signal_id":
            signal = signal_for(signal_id=value)
            action = action_for(ledger, signal)
        else:
            signal = signal_for()
            action = action_for(ledger, signal, **{field: value})
        before_dump = "\n".join(ledger._db.iterdump())
        before_matcher = matcher.report()
        with pytest.raises(ValueError, match=field):
            host._validate(host._jobs[SID], action, signal, T)
        assert_validate_pure(ledger, matcher, before_dump, before_matcher)
    finally:
        ledger.close()


def test_valid_legacy_wire_is_unchanged_by_validation(tmp_path):
    ledger, matcher, now, host = host_for(tmp_path)
    try:
        metadata = {"tag": "legacy", "weight": D("0.5")}
        signal = signal_for(metadata=dict(metadata))
        action = action_for(ledger, signal, metadata=dict(metadata))
        intent_before = action.intent.model_dump(mode="json")
        signal_before = signal.model_dump(mode="json")
        dump_before, matcher_before = "\n".join(ledger._db.iterdump()), matcher.report()
        document = host._validate(host._jobs[SID], action, signal, T)
        assert action.intent.model_dump(mode="json") == intent_before
        assert signal.model_dump(mode="json") == signal_before
        assert document["intent"] == intent_before
        assert document["intent"]["destination"] == "real"
        assert document["intent"]["limit_price"] == "99"
        assert document["intent"]["metadata"] == {"tag": "legacy", "weight": "0.5"}
        assert_validate_pure(ledger, matcher, dump_before, matcher_before)
    finally:
        ledger.close()


def test_direct_instrument_dispatches_through_existing_paper_matcher(tmp_path):
    ledger, matcher, now, host = host_for(tmp_path)
    try:
        assert len(deliver(host, now, book(T))) == 1
        assert len(ledger.report()["orders"]) == 1
        assert len(matcher.report()["open_orders"]) == 1
    finally:
        ledger.close()


def test_kiwoom_reference_maps_to_kis_etf_paper_dispatch(tmp_path):
    execution = InstrumentId(venue=Venue.KIS, asset_class=AssetClass.ETF,
        raw_symbol="069500", std_symbol="069500", currency="KRW")
    reference = execution.model_copy(update={"venue": Venue.KIWOOM})
    ledger, matcher, now, host = host_for(tmp_path, instrument=execution, reference=reference)
    try:
        assert len(deliver(host, now, book(instrument=reference))) == 1
        orders = ledger.report()["orders"]
        assert len(orders) == 1
        assert orders[0]["instrument"] == execution.key
        assert len(matcher.report()["open_orders"]) == 1
    finally:
        ledger.close()


def test_rejected_new_binding_raises_before_execute_or_action_receipt(tmp_path):
    ledger = ledger_at(tmp_path)
    ledger.seed_balance("USDT", D("100000"), {"source": "tx01 fixture"})
    matcher, now = matcher_for(ledger), [T]
    host = runtime(ledger, matcher, now)
    bad_instrument = BTC.model_copy(update={"raw_symbol": "BTCUSD", "currency": "USD"})

    def builder(signal, view, config):
        intent = OrderIntent(intent_id="intent-bad", job_id=SID, signal_id=signal.signal_id,
            destination=OrderDestination.REAL, instrument=bad_instrument, side=Side.BUY,
            created_ts_ns=signal.event_ts_ns, client_order_id="client-bad",
            quantity=D("1"), limit_price=D("99"))
        return [ExecutionAction("action-bad", ActionKind.NEW, ledger.scope, SID, "config-1",
            TradingMode.PAPER, signal.valid_until_ts_ns, intent=intent, post_only=True)]

    host.register(registration(ledger, SID, [], builder=builder))
    try:
        assert deliver(host, now, book(T)) == ()
        assert host.report()["strategies"][SID]["state"] == "FAULTED"
        assert host.report()["strategies"][SID]["reason"] == "ValueError"
        assert ledger.report()["orders"] == []
        assert matcher.report()["open_orders"] == {}
        assert action_receipts(ledger) == []
    finally:
        ledger.close()


def test_execute_dedups_same_payload_and_rejects_changed_payload(tmp_path):
    ledger, matcher, now, host = host_for(tmp_path)
    try:
        job = host._jobs[SID]
        signal = signal_for()
        action = action_for(ledger, signal)
        document = host._validate(job, action, signal, T)
        snapshot = host.hot_path.snapshot(T, "PAPER")
        assert host._execute(job, action, signal, document, snapshot) is True
        before = matcher.report()
        assert host._execute(job, action, signal, document, snapshot) is False
        assert matcher.report() == before
        changed = replace(action, intent=action.intent.model_copy(
            update={"limit_price": D("98")}))
        changed_document = host._validate(job, changed, signal, T)
        with pytest.raises(ValueError, match="collision"):
            host._execute(job, changed, signal, changed_document, snapshot)
        assert len(ledger.report()["orders"]) == 1
        assert matcher.report() == before
    finally:
        ledger.close()


def test_invalid_lot_admission_rolls_back_order_and_matcher_state(tmp_path):
    ledger, matcher, now, host = host_for(tmp_path)
    try:
        job = host._jobs[SID]
        signal = signal_for()
        action = action_for(ledger, signal, quantity=D("0.5"))
        document = host._validate(job, action, signal, T)
        snapshot = host.hot_path.snapshot(T, "PAPER")
        with pytest.raises(ValueError, match="lot/tick"):
            host._execute(job, action, signal, document, snapshot)
        assert ledger.report()["orders"] == []
        assert ledger.position(SID, BTC.key) == 0
        assert ledger.paper_state() is None
        assert matcher.report()["open_orders"] == {}
    finally:
        ledger.close()
