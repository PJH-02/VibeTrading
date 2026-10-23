"""Independent TX01 causal identity probes against existing real local fixtures."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
import json

import pytest

from vibetrading.models import Signal, SignalAction
from vibetrading.trading_contracts import ActionKind
from test_strategy_runtime import (BTC, T, book, deliver, ledger_at, matcher_for,
                                   registration, runtime)


@pytest.fixture
def bound(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "independent local probe"})
        matcher = matcher_for(ledger)
        host = runtime(ledger, matcher, [T])
        reg = registration(ledger, "alpha", [])
        host.register(reg)
        signal = Signal(strategy_id="alpha", instrument=BTC,
                        action=SignalAction.ENTER_LONG, event_ts_ns=T,
                        reference_price=D(100), valid_until_ts_ns=T+100_000,
                        source_event_id="independent-source")
        action = reg.action_builder(signal, None, {"quantity": "1"})[0]
        yield ledger, matcher, host, host._jobs["alpha"], signal, action
    finally:
        ledger.close()


CASES = [
    "intent_currency", "intent_raw", "signal_currency", "signal_raw", "signal_symbol",
    "future_creation", "pre_signal_creation", "negative_creation", "boolean_creation",
    "float_creation", "empty_intent", "oversized_intent", "empty_client", "space_client",
    "oversized_client", "boolean_now", "float_now", "negative_now",
]


@pytest.mark.parametrize("case", CASES)
def test_invalid_new_binding_is_rejected_without_persistent_or_matcher_changes(bound, case):
    ledger, matcher, host, job, signal, action = bound
    now = T
    intent_updates = {}
    if case == "intent_currency":
        intent_updates["instrument"] = BTC.model_copy(update={"currency": "USD"})
    elif case == "intent_raw":
        intent_updates["instrument"] = BTC.model_copy(update={"raw_symbol": "OTHER"})
    elif case in {"signal_currency", "signal_raw", "signal_symbol"}:
        name, value = {"signal_currency": ("currency", "USD"),
                       "signal_raw": ("raw_symbol", "OTHER"),
                       "signal_symbol": ("std_symbol", "ETHUSDT")}[case]
        signal = signal.model_copy(update={"instrument": BTC.model_copy(update={name: value})})
    elif case.endswith("creation"):
        intent_updates["created_ts_ns"] = {
            "future_creation": T+1, "pre_signal_creation": T-1, "negative_creation": -1,
            "boolean_creation": True, "float_creation": float(T),
        }[case]
    elif case.endswith("intent"):
        intent_updates["intent_id"] = "" if case == "empty_intent" else "i" * 513
    elif case.endswith("client"):
        intent_updates["client_order_id"] = {"empty_client": "", "space_client": "  ",
                                               "oversized_client": "c" * 513}[case]
    else:
        now = {"boolean_now": True, "float_now": float(T), "negative_now": -1}[case]
    if intent_updates:
        action = replace(action, intent=action.intent.model_copy(update=intent_updates))
    before = tuple(ledger._db.iterdump()), deepcopy(matcher.report()), dict(host._seen_actions)
    with pytest.raises(ValueError):
        host._validate(job, action, signal, now)
    assert (tuple(ledger._db.iterdump()), matcher.report(), host._seen_actions) == before


def test_valid_legacy_wire_and_exact_decimal_values_are_not_rewritten(bound):
    ledger, matcher, host, job, signal, action = bound
    intent = action.intent.model_copy(update={"quantity": D("1.000000000000000001"),
                                              "limit_price": D("99.000000000000000001"),
                                              "metadata": {"legacy": ["kept", {"n": "7"}]}})
    action = replace(action, intent=intent)
    before_signal, before_intent = signal.model_dump(mode="json"), intent.model_dump(mode="json")
    database = tuple(ledger._db.iterdump())
    document = host._validate(job, action, signal, T)
    assert signal.model_dump(mode="json") == before_signal
    assert intent.model_dump(mode="json") == before_intent
    assert document["intent"] == before_intent
    assert tuple(ledger._db.iterdump()) == database
    assert intent.quantity == D("1.000000000000000001")
    assert intent.destination.value == "real"


def test_cancel_keeps_existing_validation_without_new_intent_requirements(bound):
    ledger, matcher, host, job, signal, action = bound
    cancel = replace(action, action=ActionKind.CANCEL, intent=None,
                     target_order_id="already-existing-order")
    before = tuple(ledger._db.iterdump()), deepcopy(matcher.report())
    document = host._validate(job, cancel, signal, T)
    assert document["action"] == "CANCEL" and document["intent"] is None
    assert (tuple(ledger._db.iterdump()), matcher.report()) == before


def test_invalid_second_action_blocks_the_entire_preflight_batch(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "independent local probe"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now)
        reg = registration(ledger, "alpha", [])
        original_builder = reg.action_builder

        def builder(signal, view, config):
            first = original_builder(signal, view, config)[0]
            second = replace(first, action_id=first.action_id+"-second", intent=
                first.intent.model_copy(update={"intent_id": first.intent.intent_id+"-second",
                    "client_order_id": first.intent.client_order_id+"-second", "created_ts_ns": T+1}))
            return [first, second]

        host.register(replace(reg, action_builder=builder))
        assert deliver(host, now, book()) == ()
        assert not ledger.report()["orders"] and not host._seen_actions
        claims = [json.loads(row[0]) for row in ledger._db.execute(
            "SELECT document FROM portfolio_receipts WHERE kind='strategy_event'")]
        assert not any(c["event_id"].startswith("@action:") for c in claims)
    finally:
        ledger.close()


@pytest.mark.parametrize("fault", ["signal_currency", "future_intent"])
def test_real_runtime_faults_before_action_claim_or_order_admission(tmp_path, fault):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "independent local probe"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now)
        reg = registration(ledger, "alpha", [])
        original_builder = reg.action_builder

        def builder(signal, view, config):
            actions = original_builder(signal, view, config)
            if fault == "signal_currency":
                return actions
            return [replace(a, intent=a.intent.model_copy(update={"created_ts_ns": T+1}))
                    for a in actions]

        if fault == "signal_currency":
            create = reg.module.module.create_strategy

            class WrongReference:
                def on_event(self, event, data):
                    emitted = create().on_event(event, data)
                    return [s.model_copy(update={"instrument": BTC.model_copy(update={"currency": "USD"})})
                            for s in emitted]

            reg.module.module.create_strategy = WrongReference
        host.register(replace(reg, action_builder=builder))
        assert deliver(host, now, book()) == ()
        assert host.report()["strategies"]["alpha"]["state"] == "FAULTED"
        assert not ledger.report()["orders"]
        assert not host._seen_actions
        claims = [json.loads(row[0]) for row in ledger._db.execute(
            "SELECT document FROM portfolio_receipts WHERE kind='strategy_event'")]
        assert not any(claim["event_id"].startswith("@action:") for claim in claims)
        # Existing registration/event callback claims are deliberately retained.
        assert claims
    finally:
        ledger.close()
