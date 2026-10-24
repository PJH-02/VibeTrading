"""TX04A: saved matcher facts must align with the authoritative admitted order.

The fixtures and constructors below reuse the existing paper-execution helpers so
this file only exercises the added alignment checks and the post-operation check.
"""
from decimal import Decimal

import pytest

from test_paper_execution import T, book, engine, local, open_ledger, submit


CONFLICTS = (
    ("price", "98", "protective price differs"),
    ("expiry_ns", T + 2000, "expiry differs"),
    ("post_only", False, "post_only differs"),
    ("reduce_only", True, "reduce_only differs"),
    ("time_in_force", "IOC", "time_in_force differs"),
    ("group_id", "group-b", "group_id differs"),
)
MALFORMED = (
    ("price", 99, "protective price.*malformed"),
    ("expiry_ns", float(T + 1000), "expiry.*malformed"),
    ("post_only", 1, "post_only.*malformed"),
    ("reduce_only", 0, "reduce_only.*malformed"),
)


def corrupt(ledger, field, value):
    state = ledger.paper_state()
    state["orders"]["one"][field] = value
    ledger.save_paper_state(state)
    return ledger.paper_state()


@pytest.mark.parametrize("field,value,match", CONFLICTS)
def test_saved_conflict_fails_constructor_without_repair(local, field, value, match):
    ledger, matcher = local
    submit(matcher, group_id="group-a")
    matcher.advance(T + 10)
    corrupted = corrupt(ledger, field, value)

    with pytest.raises(ValueError, match=match):
        engine(ledger)

    assert ledger.paper_state() == corrupted
    assert ledger.get("one")["state"] == "OPEN"


@pytest.mark.parametrize("field,value,match", MALFORMED)
def test_malformed_saved_field_does_not_pass_coercion(local, field, value, match):
    ledger, matcher = local
    submit(matcher)
    corrupted = corrupt(ledger, field, value)

    with pytest.raises(ValueError, match=match):
        engine(ledger)

    assert ledger.paper_state() == corrupted


@pytest.mark.parametrize("field,value,match", CONFLICTS)
def test_staged_mismatch_is_rejected_before_persistence(local, monkeypatch,
                                                        field, value, match):
    ledger, matcher = local
    submit(matcher, group_id="group-a")
    matcher.advance(T + 10)
    before, stored, durable = matcher.report(), ledger.paper_state(), ledger.get("one")
    saves = []
    monkeypatch.setattr(ledger, "save_paper_state", lambda state: saves.append(state))

    def operation(state):
        state["orders"]["one"][field] = value
        state["clock"] = T + 999
        return "must not be published"

    with pytest.raises(ValueError, match=match):
        matcher._transition(operation)

    assert saves == []
    assert matcher.report() == before
    assert ledger.paper_state() == stored
    assert ledger.get("one") == durable


def test_supported_flags_expiry_and_group_survive_restart(tmp_path):
    ledger = open_ledger(tmp_path)
    try:
        ledger.seed_balance("USDT", Decimal(100000), {"fixture": "tx04a restart"})
        matcher = engine(ledger)
        matcher.on_book(book())
        submit(matcher, group_id="group-a")
        matcher.advance(T + 10)
        expected = matcher.report()["open_orders"]["one"]
        ledger.close()

        ledger = open_ledger(tmp_path)
        matcher = engine(ledger)
        assert matcher.report()["open_orders"]["one"] == expected
    finally:
        ledger.close()
