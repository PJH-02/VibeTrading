"""Independent recovery counterexamples: frozen expectations, no live calls."""
import json
from decimal import Decimal
import pytest
from test_strategy_runtime import ledger_at, matcher_for, runtime, registration, deliver, book, T


def test_new_event_after_real_reopen_cannot_run_reset_strategy(tmp_path):
    now, observed = [T], []
    ledger = ledger_at(tmp_path)
    ledger.seed_balance("USDT", Decimal(100000), {"source": "synthetic"})
    host = runtime(ledger, matcher_for(ledger), now)
    host.register(registration(ledger, "one", observed, builder=lambda *args: []))
    deliver(host, now, book())
    deliver(host, now, book(T+1, sequence=2))
    assert len(observed) == 2
    ledger.close()
    ledger = ledger_at(tmp_path)
    try:
        restarted = runtime(ledger, matcher_for(ledger), now)
        restarted.register(registration(ledger, "one", observed, builder=lambda *args: []))
        deliver(restarted, now, book(T+2, sequence=3))
        assert len(observed) == 2, "new event ran a fresh instance without restored state"
        assert restarted.report()["strategies"]["one"]["state"] == "BLOCKED"
    finally:
        ledger.close()


def test_json_valid_receipt_corruption_cannot_hide_dispatch_as_registration(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.claim_strategy_event("one", "config-1", "event-before-crash", {"proof": "callback-started"})
        row = ledger._db.execute("SELECT id,document FROM portfolio_receipts WHERE kind='strategy_event'").fetchone()
        document = json.loads(row["document"])
        document["event_id"] = "@registration"
        # Model a JSON-valid corrupt journal cell without forging its stored fingerprint.
        ledger._db.execute("UPDATE portfolio_receipts SET document=? WHERE kind='strategy_event' AND id=?",
                           (json.dumps(document), row["id"]))
        host = runtime(ledger, matcher_for(ledger), [T])
        with pytest.raises((ValueError, RuntimeError)):
            host.register(registration(ledger, "one", [], builder=lambda *args: []))
        assert not host.report()["strategies"]
    finally:
        ledger.close()


def test_permission_block_must_not_mask_sticky_restart_fence(tmp_path):
    now, observed = [T], []
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", Decimal(100000), {"source": "synthetic"})
        ledger.claim_strategy_event("one", "config-1", "before-crash", {"proof": "callback-started"})
        blocked = runtime(ledger, matcher_for(ledger), now)
        blocked.register(registration(ledger, "one", observed, builder=lambda *args: [], permission=False))
        for _ in range(2):
            blocked.pause("one", T+1)
            try:
                blocked.resume("one")
            except ValueError:
                pass
            blocked.halt("operator-stop", T+1, "one")
        blocked.pause("one", T+1)
        try:
            blocked.resume("one")
        except ValueError:
            pass
        deliver(blocked, now, book(T+2, sequence=2))
        assert len(observed) == 0, "permission label hid the required restart fence"
    finally:
        ledger.close()


def test_operator_halt_still_requests_cancel_for_restored_owned_order(tmp_path):
    now, observed = [T], []
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", Decimal(100000), {"source": "synthetic"})
        original = runtime(ledger, matcher_for(ledger), now)
        original.register(registration(ledger, "one", observed))
        deliver(original, now, book())
        matcher = matcher_for(ledger)
        blocked = runtime(ledger, matcher, now)
        blocked.register(registration(ledger, "one", observed))
        identity = next(iter(matcher.report()["open_orders"]))
        assert matcher.report()["open_orders"][identity]["cancel_ns"] is None
        blocked.halt("operator-stop", T+1, "one")
        assert matcher.report()["open_orders"][identity]["cancel_ns"] is not None
        assert len(observed) == 1
    finally:
        ledger.close()
