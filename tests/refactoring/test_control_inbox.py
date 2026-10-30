import json
import sqlite3

import pytest

from execution.process_lease import ProcessLease
from operations.telegram_state import OutboxFull, TelegramState


def test_receive_is_durable_before_offset_and_duplicate_identity_conflicts(tmp_path):
    path = tmp_path / "telegram.sqlite3"
    state = TelegramState(path)
    update = {"update_id":42,"message":{"text":"status"}}
    state.receive(update)
    assert TelegramState(path).offset() == 43
    assert TelegramState(path).pending() == [update]
    state.receive(update)
    assert len(state.pending()) == 1
    with pytest.raises(ValueError, match="conflicting content"):
        state.receive({"update_id":42,"message":{"text":"different"}})
    state.complete(42)
    state.receive(update)
    assert state.pending() == []


def test_inbox_capacity_failure_never_advances_offset(tmp_path):
    state = TelegramState(tmp_path/"telegram.sqlite3", max_pending=1)
    state.receive({"update_id":10})
    with pytest.raises(OutboxFull):
        state.receive({"update_id":12})
    assert state.offset() == 11
    assert state.pending() == [{"update_id":10}]
    state.complete(10)
    state.receive({"update_id":12})
    assert state.offset() == 13


def test_sqlite_failure_rolls_back_update_and_offset(tmp_path):
    state = TelegramState(tmp_path/"telegram.sqlite3")
    with state.transaction() as db:
        db.execute("CREATE TRIGGER fail_cursor BEFORE UPDATE ON cursor BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        state.receive({"update_id":42})
    assert state.offset() == 0
    assert state.pending() == []


def test_only_one_cooperating_poller_owns_volume(tmp_path):
    first = ProcessLease(tmp_path/"poller.lock")
    second = ProcessLease(tmp_path/"poller.lock")
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="another process"):
            second.acquire()
    finally:
        first.close()
    second.acquire()
    second.close()
