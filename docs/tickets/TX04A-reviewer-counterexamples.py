"""Independent one-sided durable matcher conflicts; owned synthetic databases only."""
from copy import deepcopy
from decimal import Decimal as D
import pytest
from test_paper_execution import T, INSTRUMENT, open_ledger, engine, submit, book, trade

CONFLICTS = [
    ("price", "98"), ("price", "99.000000000000000000000000000000000001"),
    ("price", None), ("price", "NaN"),
    ("expiry_ns", T+1001), ("expiry_ns", float(T+1000)), ("expiry_ns", True),
    ("post_only", False), ("post_only", 1), ("reduce_only", True),
    ("reduce_only", 0), ("time_in_force", "IOC"), ("group_id", "forged"),
]

@pytest.mark.parametrize("field,value", CONFLICTS)
def test_restart_rejects_one_sided_conflict_without_database_repair(tmp_path, field, value):
    ledger = open_ledger(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"synthetic": True})
        matcher = engine(ledger)
        matcher.on_book(book())
        submit(matcher)
        corrupted = deepcopy(ledger.paper_state())
        corrupted["orders"]["one"][field] = value
        ledger.save_paper_state(corrupted)
        before = tuple(ledger._db.iterdump())
        with pytest.raises((ValueError, TypeError, ArithmeticError)):
            engine(ledger)
        assert tuple(ledger._db.iterdump()) == before
    finally:
        ledger.close()

@pytest.mark.parametrize("field", ["price", "expiry_ns", "post_only", "reduce_only", "time_in_force", "group_id"])
def test_restart_rejects_missing_order_facts_without_default_inference(tmp_path, field):
    ledger = open_ledger(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"synthetic": True})
        matcher = engine(ledger)
        matcher.on_book(book())
        submit(matcher)
        corrupted = deepcopy(ledger.paper_state())
        del corrupted["orders"]["one"][field]
        ledger.save_paper_state(corrupted)
        before = tuple(ledger._db.iterdump())
        with pytest.raises((ValueError, TypeError, KeyError, ArithmeticError)):
            engine(ledger)
        assert tuple(ledger._db.iterdump()) == before
    finally:
        ledger.close()

@pytest.mark.parametrize("field,value", [("price", "98"), ("expiry_ns", T+1001), ("post_only", False), ("group_id", "forged")])
def test_post_operation_conflict_rolls_back_book_and_unpublished_memory(tmp_path, field, value):
    ledger = open_ledger(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"synthetic": True})
        matcher = engine(ledger)
        matcher.on_book(book())
        submit(matcher)
        before_db = tuple(ledger._db.iterdump())
        before_memory = deepcopy(matcher._state)
        def operation(staged):
            # Real SQL mutation verifies transaction rollback, not only a read-only failure.
            ledger.request_cancel("one", "strategy")
            staged["orders"]["one"][field] = value
        with pytest.raises((ValueError, TypeError, ArithmeticError)):
            matcher._transition(operation)
        assert tuple(ledger._db.iterdump()) == before_db
        assert matcher._state == before_memory
    finally:
        ledger.close()


def test_valid_later_submit_time_and_committed_partial_fill_reopen_dedup(tmp_path):
    ledger = open_ledger(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"synthetic": True})
        matcher = engine(ledger)
        matcher.on_book(book())
        submit(matcher, quantity=2, now=T+1, group_id="group")
        matcher.advance(T+11)
        evidence = trade(T+12, quantity=6)
        assert matcher.on_trade(evidence)
        assert ledger.position("strategy", INSTRUMENT.key) == 1
        assert D(ledger.report()["balances"]["USDT"]) == D("99999.01")
        expected = deepcopy(matcher._state)
    finally:
        ledger.close()
    ledger = open_ledger(tmp_path)
    try:
        restored = engine(ledger)
        before_db = tuple(ledger._db.iterdump())
        assert restored._state == expected
        assert not restored.on_trade(evidence)
        assert tuple(ledger._db.iterdump()) == before_db
        assert restored._state == expected
    finally:
        ledger.close()
