from dataclasses import FrozenInstanceError
from decimal import Decimal as D

import pytest

from execution.execution_ledger import AccountScope
from execution.trading_contracts import StrategyActionPlan, StrategyOrderContext, StrategyOrderEvent, TradingMode, validate_scope
from test_portfolio_ledger import admit, ledger, order, seeded


def test_order_event_freezes_context_and_rejects_actor_references():
    scope = AccountScope("kis", "acct", "cash", "mock")
    event = StrategyOrderEvent("e1", "alpha", scope, "ord", "ACCEPTED", None,
                               D(2), D(0), D(2), metadata={"facts": [1, {"x": 2}]})
    assert event.metadata["facts"] == (1, {"x": 2})
    with pytest.raises(TypeError):
        event.metadata["facts"][1]["x"] = 3
    with pytest.raises(TypeError):
        StrategyOrderContext(None, {"bad": object()}, 1, None, "ok")
    assert validate_scope(scope, TradingMode.KIS_MOCK) == TradingMode.KIS_MOCK
    with pytest.raises(ValueError):
        validate_scope(AccountScope("kis", "acct", "perp", "mock"), TradingMode.KIS_MOCK)


def test_feedback_is_opt_in_transactional_bounded_and_deduped(tmp_path):
    with seeded(tmp_path) as book:
        admit(book)
        assert book.order_feedback("alpha") == ()
        book.enable_order_feedback("alpha")
        intent = order()
        # The pre-opt-in admission does not retroactively publish a receipt.
        assert book.order_feedback("alpha") == ()
        second = order(identity="two")
        admit(book, second)
        records = book.order_feedback("alpha", limit=1)
        assert len(records) == 1 and records[0][1].status == "ACCEPTED"
        cursor = records[0][0]
        assert book.order_feedback("beta") == ()
        assert book.request_cancel("two", "alpha")["state"] == "CANCEL_PENDING"
        assert book.order_feedback("alpha", after=cursor)[0][1].status == "CANCEL_PENDING"
        book.apply_fill("fill-1", "two", D("0.4"), D("99"), fee=D("-0.01"), fee_currency="USDT")
        latest = book.order_feedback("alpha", after=cursor, limit=8)
        assert [event.status for _, event in latest] == ["CANCEL_PENDING", "PARTIAL_FILL"]
        assert latest[-1][1].metadata["source_ts_ns"] is None
        assert not book.apply_fill("fill-1", "two", D("0.4"), D("99"), fee=D("-0.01"), fee_currency="USDT")
        assert len(book.order_feedback("alpha", after=cursor, limit=8)) == 2


def test_feedback_rolls_back_with_owner_transaction_and_local_reject(tmp_path):
    with seeded(tmp_path) as book:
        book.enable_order_feedback("alpha")
        with pytest.raises(RuntimeError):
            with book.transaction():
                admit(book)
                raise RuntimeError("rollback")
        assert book.get("one") is None
        assert book.order_feedback("alpha") == ()
        rejected = order(identity="local")
        book.record_order_rejection("local", "alpha", "unsupported FOK", intent=rejected, now_ns=55)
        cursor, event = book.order_feedback("alpha")[0]
        assert cursor > 0 and event.status == "REJECTED"
        assert event.event_ts_ns == 55 and event.metadata["provenance"] == "LOCAL_NOT_SENT"
        assert book.get("local") is None


def test_local_rejection_is_clock_idempotent_and_venue_bound(tmp_path):
    from core.models import Venue
    with seeded(tmp_path) as book:
        book.enable_order_feedback("alpha")
        candidate = order(identity="local")
        book.record_order_rejection("local", "alpha", "unsupported FOK", intent=candidate, now_ns=10)
        book.record_order_rejection("local", "alpha", "unsupported FOK", intent=candidate, now_ns=20)
        records = book.order_feedback("alpha")
        assert len(records) == 1 and records[0][1].event_ts_ns == 10
        foreign = candidate.model_copy(update={"instrument": candidate.instrument.model_copy(update={"venue": Venue.BYBIT})})
        with pytest.raises(ValueError, match="venue"):
            book.record_order_rejection("local", "alpha", "unsupported FOK", intent=foreign, now_ns=30)
        assert len(book.order_feedback("alpha")) == 1
        altered = candidate.model_copy(update={"quantity": D("2")})
        with pytest.raises(ValueError, match="collision"):
            book.record_order_rejection("local", "alpha", "unsupported FOK", intent=altered, now_ns=40)


def test_repeated_cancel_and_terminal_outcome_keep_single_fact_receipts(tmp_path):
    with seeded(tmp_path) as book:
        book.enable_order_feedback("alpha")
        admit(book)
        book.request_cancel("one", "alpha")
        book.request_cancel("one", "alpha")
        book.cancel("one", confirmed=True, evidence={"id": "native-1"})
        book.cancel("one", confirmed=True, evidence={"id": "native-1"})
        assert [event.status for _, event in book.order_feedback("alpha")] == [
            "ACCEPTED", "CANCEL_PENDING", "CANCELLED"]


def test_unknown_repeated_around_late_fill_has_distinct_cumulative_receipts(tmp_path):
    with seeded(tmp_path) as book:
        book.enable_order_feedback("alpha")
        admit(book, order(quantity="1"))
        book.cancel("one", confirmed=False)
        book.apply_fill("late-fill", "one", D("0.4"), D("99"), fee=D("0"), fee_currency="USDT")
        book.cancel("one", confirmed=False)
        book.cancel("one", confirmed=False)
        book.request_cancel("one", "alpha")
        events = [event for _, event in book.order_feedback("alpha")]
        assert [event.status for event in events] == [
            "ACCEPTED", "UNKNOWN", "PARTIAL_FILL", "UNKNOWN", "CANCEL_PENDING"]
        assert events[1].filled_quantity == D(0)
        assert events[3].filled_quantity == D("0.4")


def test_context_freezes_nested_cursor_and_rejects_nonfinite_cursor():
    cursor = ([1, 2],)
    context = StrategyOrderContext(None, {}, 1, cursor, "VALID")
    cursor[0].append(3)
    assert context.cursor == ((1, 2),)
    with pytest.raises((ValueError, TypeError)):
        StrategyOrderContext(None, {}, 1, (float("nan"),), "VALID")
