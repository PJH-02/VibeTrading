"""Independent adverse STR-02 checks against the real local PAPER runtime."""
from dataclasses import replace
from decimal import Decimal as D
from types import ModuleType

import pytest

import test_strategy_runtime as base
from core.models import OrderDestination, OrderIntent, Side
from execution.trading_contracts import ActionKind, ExecutionAction, StrategyActionPlan, TradingMode


def protocol_module(strategy_id, hook, *, event_result=None):
    source = ModuleType("adverse_" + strategy_id)
    source.ACTION_PROTOCOL_VERSION = 1

    class Policy:
        def on_event(self, event, data):
            return [] if event_result is None else event_result(event, data)

        def on_order_event(self, event, context):
            return hook(event, context)

        def checkpoint_state(self):
            return {"callbacks": 0}

        def restore_state(self, state):
            self.restored_state = state

    source.create_strategy = Policy
    return base.StrategyModule(source, strategy_id,
        [{"instrument_key": base.BTC.key, "event_type": "order_book"}], {"bars": 0})


def opt_registration(ledger, strategy_id, hook, *, accounts=None, risk=None, event_result=None):
    observed = []
    registration = base.registration(ledger, strategy_id, observed)
    registration = replace(registration, module=protocol_module(strategy_id, hook, event_result=event_result))
    if accounts is not None:
        registration = replace(registration, accounts=tuple(accounts), risk=risk)
    return registration, observed


def fresh_host(ledger, now, *, budgets=None, other_accounts=(), callback_clock_ns=lambda: 0):
    matcher = base.matcher_for(ledger)
    accounts = {ledger.scope.key: matcher}
    for extra in other_accounts:
        accounts[extra.scope.key] = base.matcher_for(extra)
    return base.PaperStrategyRuntime(hot_path=base.HotPathStore(), accounts=accounts,
        products={base.BTC.key: base.spec()}, known_instruments={base.BTC.key: base.BTC},
        reference_feeds={}, budgets=budgets or base.budget(),
        settings={"fixture": "synthetic local facts"}, mode=TradingMode.PAPER,
        phase="paper", now_ns=lambda: now[0], callback_clock_ns=callback_clock_ns)


def local_rejection(ledger, identity, strategy, *, stamp=None):
    intent = OrderIntent(intent_id=identity, job_id=strategy, signal_id="signal-"+identity,
        destination=OrderDestination.REAL, instrument=base.BTC, side=Side.BUY,
        created_ts_ns=base.T, client_order_id="client-"+identity,
        quantity=D(1), limit_price=D(99))
    ledger.record_order_rejection(identity, strategy, "scripted local reject", intent=intent,
                                  now_ns=base.T if stamp is None else stamp)
    return intent


def matcher_order(host, ledger, identity, strategy, *, quantity="1", side=Side.BUY):
    matcher = host.accounts[ledger.scope.key]
    matcher.on_book(base.book())
    intent = OrderIntent(intent_id=identity, job_id=strategy, signal_id="signal-"+identity,
        destination=OrderDestination.REAL, instrument=base.BTC, side=side,
        created_ts_ns=base.T, client_order_id="client-"+identity,
        quantity=D(quantity), limit_price=D(99 if side == Side.BUY else 101))
    matcher.submit(intent, strategy, {"source": "adverse matcher fixture"}, base.T+1_000_000,
        base.spec(), base.T, reference_price=D(100), margin_rate=D(".1"),
        funding_buffer=D(0), account_notional_limit=D(100_000),
        strategy_notional_limit=D(100_000), account_capital_limit=D(100_000),
        strategy_capital_limit=D(100_000), post_only=True, time_in_force="GTC")
    return intent


def canceled_partial_matcher_order(host, ledger, identity, strategy):
    target = matcher_order(host, ledger, identity, strategy, quantity="2")
    matcher = host.accounts[ledger.scope.key]
    matcher.advance(base.T+10)
    trade = base.trade(base.T+11, side=Side.SELL, price=99).model_copy(update={"quantity": D(6)})
    matcher.on_trade(trade)
    assert ledger.get(identity)["filled"] == "1"
    matcher.cancel(identity, strategy, base.T+12)
    matcher.advance(base.T+17)
    assert ledger.get(identity)["state"] == "CANCELED"
    return target


def action_plan(strategy, scope, now, action, *, target=None, intent=None, tif="GTC", action_id="planned"):
    item = ExecutionAction(action_id, action, scope, strategy, "config-1", TradingMode.PAPER,
        now+100_000, intent=intent, target_order_id=target, post_only=True, time_in_force=tif)
    return StrategyActionPlan("plan-"+action_id, strategy, "config-1", now, (item,))


def test_feedback_callback_claim_precedes_user_code_and_cursor_survives_checkpoint(tmp_path):
    ledger = base.ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100_000), {"source": "synthetic"})
    now, seen = [base.T], []

    def hook(event, context):
        receipts = [__import__("json").loads(row[0]) for row in ledger._db.execute(
            "SELECT document FROM portfolio_receipts WHERE kind='strategy_event'")]
        seen.append((event.event_id, context.cursor, any(
            receipt.get("event_id") == "@order-feedback:"+event.event_id for receipt in receipts)))
        return []

    host = fresh_host(ledger, now)
    registration, _ = opt_registration(ledger, "alpha", hook)
    host.register(registration)
    resumed = fresh_host(ledger, now)
    resumed.register(registration)
    local_intent = OrderIntent(intent_id="order-1", job_id="alpha", signal_id="signal-order-1",
        destination=OrderDestination.REAL, instrument=base.BTC, side=Side.BUY,
        created_ts_ns=base.T, client_order_id="client-order-1", quantity=D(1), limit_price=D(99))
    ledger.record_order_rejection("order-1", "alpha", "scripted local reject",
                                  intent=local_intent, now_ns=base.T)
    host._drain_feedback(host.hot_path.snapshot(now[0], "PAPER"), "PAPER")
    assert seen and all(claimed for _, _, claimed in seen)
    callback_count = len(seen)
    host._drain_feedback(host.hot_path.snapshot(now[0], "PAPER"), "PAPER")
    assert len(seen) == callback_count
    state = host.capture_runtime_state(max_bytes=1_000_000)
    saved = state["jobs"][0]["feedback_cursors"]
    assert saved[ledger.scope.key] == max(rowid for rowid, _ in ledger.order_feedback("alpha"))

    resumed.begin_checkpoint_recovery()
    malformed = __import__("copy").deepcopy(state)
    malformed["jobs"][0]["feedback_cursors"][ledger.scope.key] = -1
    with pytest.raises(ValueError, match="feedback cursor schema"):
        resumed.restore_runtime_state(malformed, max_bytes=1_000_000)
    ledger.close()


def test_foreign_strategy_and_unbound_account_cancel_are_rejected(tmp_path):
    ledger = base.ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100_000), {"source": "synthetic"})
    now, calls = [base.T], []
    foreign_target = None

    def foreign_cancel(event, context):
        calls.append(event.status)
        return action_plan("alpha", ledger.scope, context.now_ns, ActionKind.CANCEL,
                           target=foreign_target.intent_id, action_id="cancel-beta")

    host = fresh_host(ledger, now)
    registration, _ = opt_registration(ledger, "alpha", foreign_cancel)
    host.register(registration)
    foreign_target = matcher_order(host, ledger, "beta-order", "beta")
    matcher_order(host, ledger, "alpha-order", "alpha")
    base.deliver(host, now, base.book())
    assert host.report()["strategies"]["alpha"]["state"] == "FAULTED"
    assert ledger.get("beta-order")["state"] == "OPEN"
    assert calls

    second = base.PortfolioLedger(tmp_path/"paper"/"other.sqlite",
        base.AccountScope("bybit", "other-account", "perpetual", "paper"), owner_root=tmp_path,
        mode=TradingMode.PAPER, max_orders=100, max_fills=100, max_events=1000,
        max_document_bytes=1_000_000, max_db_bytes=16_000_000, max_wal_bytes=16_000_000)
    try:
        second.seed_balance("USDT", D(100_000), {"source": "synthetic"})
        now[0] = base.T+10
        host2 = fresh_host(ledger, now, other_accounts=(second,))
        bad_scope_registration, _ = opt_registration(ledger, "gamma",
            lambda event, context: action_plan("gamma", second.scope, context.now_ns,
                ActionKind.CANCEL, target="missing", action_id="cancel-other-account"),
            accounts=(ledger.scope.key,), risk={ledger.scope.key: base.StrategyRisk(
                D(0), D(100_000), D(100_000), D(100_000), D(100_000), D(".1"))})
        host2.register(bad_scope_registration)
        local_intent = OrderIntent(intent_id="gamma-order", job_id="gamma", signal_id="signal-gamma",
            destination=OrderDestination.REAL, instrument=base.BTC, side=Side.BUY,
            created_ts_ns=base.T, client_order_id="client-gamma", quantity=D(1), limit_price=D(99))
        ledger.record_order_rejection("gamma-order", "gamma", "scripted local reject",
                                      intent=local_intent, now_ns=base.T)
        base.deliver(host2, now, base.book(base.T+10, sequence=2))
        assert host2.report()["strategies"]["gamma"]["state"] == "FAULTED"
        assert second.get("missing") is None
    finally:
        second.close()
        ledger.close()


def test_unknown_order_cannot_be_blindly_retried_from_order_callback(tmp_path):
    ledger = base.ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100_000), {"source": "synthetic"})
    now, called = [base.T], []

    def retry_unknown(event, context):
        called.append(event.status)
        if event.status != "UNKNOWN":
            return []
        retry = OrderIntent(intent_id="blind-retry", job_id="alpha", signal_id="retry-signal",
            destination=OrderDestination.REAL, instrument=base.BTC, side=Side.BUY,
            created_ts_ns=context.now_ns, client_order_id="client-blind-retry",
            quantity=D(1), limit_price=D(99))
        return action_plan("alpha", ledger.scope, context.now_ns, ActionKind.NEW,
                           intent=retry, action_id="blind-retry-action")

    host = fresh_host(ledger, now)
    registration, _ = opt_registration(ledger, "alpha", retry_unknown)
    host.register(registration)
    original = matcher_order(host, ledger, "unknown-order", "alpha")
    ledger.cancel(original.intent_id, confirmed=False)
    base.deliver(host, now, base.book())
    assert "UNKNOWN" in called
    assert ledger.get("blind-retry") is None
    assert host.report()["strategies"]["alpha"]["state"] == "FAULTED"
    ledger.close()


def test_feedback_fault_and_count_byte_caps_are_isolated_and_bounded(tmp_path):
    ledger = base.ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100_000), {"source": "synthetic"})
    now, invoked = [base.T], []
    host = fresh_host(ledger, now)
    bad, _ = opt_registration(ledger, "bad", lambda event, context: (_ for _ in ()).throw(RuntimeError("fixture")))
    good, _ = opt_registration(ledger, "good", lambda event, context: invoked.append(event.event_id) or [])
    host.register(bad)
    host.register(good)
    local_rejection(ledger, "bad-order", "bad")
    local_rejection(ledger, "good-order", "good")
    base.deliver(host, now, base.book())
    assert host.report()["strategies"]["bad"]["state"] == "FAULTED"
    assert host.report()["strategies"]["good"]["state"] != "FAULTED"
    assert invoked
    ledger.close()

    ledger = base.ledger_at(tmp_path/"count")
    ledger.seed_balance("USDT", D(100_000), {"source": "synthetic"})
    count, now = [], [base.T+100]
    bounded = fresh_host(ledger, now)
    registration, _ = opt_registration(ledger, "many", lambda event, context: count.append(event.event_id) or [])
    bounded.register(registration)
    for i in range(65):
        intent = OrderIntent(intent_id=f"reject-{i}", job_id="many", signal_id=f"s-{i}",
            destination=OrderDestination.REAL, instrument=base.BTC, side=Side.BUY,
            created_ts_ns=base.T, client_order_id=f"c-{i}", quantity=D(1), limit_price=D(99))
        ledger.record_order_rejection(intent.intent_id, "many", "scripted local reject", intent=intent, now_ns=base.T+i)
    base.deliver(bounded, now, base.book(base.T+100))
    assert len(count) == 64
    now[0] = base.T+101
    base.deliver(bounded, now, base.book(base.T+101, sequence=2))
    assert len(count) == 65
    ledger.close()

    ledger = base.ledger_at(tmp_path/"bytes")
    ledger.seed_balance("USDT", D(100_000), {"source": "synthetic"})
    invoked = []
    now = [base.T]
    small = fresh_host(ledger, now, budgets=base.budget(max_action_bytes=64))
    registration, _ = opt_registration(ledger, "large-context", lambda event, context: invoked.append(event.event_id) or [])
    small.register(registration)
    local_rejection(ledger, "large-context-order", "large-context")
    assert ledger.order_feedback("large-context")
    small._drain_feedback(small.hot_path.snapshot(now[0], "PAPER"), "PAPER")
    assert not invoked
    assert small.report()["strategies"]["large-context"]["reason"] == "order_feedback_deferred_capacity"
    assert small._jobs["large-context"].feedback_cursors[ledger.scope.key] == 0
    ledger.close()


def test_confirmed_replace_requires_exact_remaining_quantity_side_and_instrument(tmp_path):
    ledger = base.ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100_000), {"source": "synthetic"})
    now, calls = [base.T], []
    target = None

    def replacement(event, context):
        calls.append(event.status)
        if event.status != "CANCELLED":
            return []
        replacement_intent = OrderIntent(intent_id="replacement", job_id="alpha", signal_id="new-signal",
            destination=OrderDestination.REAL, instrument=base.BTC, side=Side.BUY,
            created_ts_ns=context.now_ns, client_order_id="client-replacement",
            quantity=D(1), limit_price=D(98))
        return action_plan("alpha", ledger.scope, context.now_ns, ActionKind.REPLACE,
            target=target.intent_id, intent=replacement_intent, action_id="replacement-action")

    host = fresh_host(ledger, now)
    registration, _ = opt_registration(ledger, "alpha", replacement)
    host.register(registration)
    target = canceled_partial_matcher_order(host, ledger, "replace-target", "alpha")
    now[0] = base.T+20
    base.deliver(host, now, base.book(base.T+20, sequence=2))
    assert "CANCELLED" in calls
    assert ledger.get("replacement") is not None, host.report()["strategies"]
    assert ledger.get("replacement")["quantity"] == "1"
    assert __import__("json").loads(ledger.get("replacement")["payload"])["metadata"]["replacement_of"] == target.intent_id
    ledger.close()


@pytest.mark.parametrize("alteration", ["excess_quantity", "wrong_side", "wrong_instrument"])
def test_confirmed_replace_rejects_excess_or_changed_side_or_instrument(tmp_path, alteration):
    from core.models import InstrumentId
    ledger = base.ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100_000), {"source": "synthetic"})
    now = [base.T]
    target = None

    def replacement(event, context):
        if event.status != "CANCELLED":
            return []
        instrument, side, quantity = base.BTC, Side.BUY, D(1)
        if alteration == "excess_quantity":
            quantity = D(2)
        elif alteration == "wrong_side":
            side = Side.SELL
        else:
            instrument = InstrumentId(venue=base.BTC.venue, asset_class=base.BTC.asset_class,
                raw_symbol="ETHUSDT", std_symbol="ETHUSDT", currency="USDT")
        intent = OrderIntent(intent_id="replacement", job_id="alpha", signal_id="new-signal",
            destination=OrderDestination.REAL, instrument=instrument, side=side,
            created_ts_ns=context.now_ns, client_order_id="client-replacement",
            quantity=quantity, limit_price=D(99))
        return action_plan("alpha", ledger.scope, context.now_ns, ActionKind.REPLACE,
            target=target.intent_id, intent=intent, action_id="replacement-action")

    host = fresh_host(ledger, now)
    registration, _ = opt_registration(ledger, "alpha", replacement)
    host.register(registration)
    target = canceled_partial_matcher_order(host, ledger, "replace-target", "alpha")
    now[0] = base.T+20
    base.deliver(host, now, base.book(base.T+20, sequence=2))
    assert host.report()["strategies"]["alpha"]["state"] == "FAULTED"
    assert ledger.get("replacement") is None
    ledger.close()


def test_fok_rejection_is_delivered_without_core_ioc_substitution(tmp_path):
    ledger = base.ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100_000), {"source": "synthetic"})
    now, events = [base.T], []
    intent = OrderIntent(intent_id="fok", job_id="alpha", signal_id="fok-signal",
        destination=OrderDestination.REAL, instrument=base.BTC, side=Side.BUY,
        created_ts_ns=base.T, client_order_id="client-fok", quantity=D(1), limit_price=D(99))

    def initial(event, data):
        plan = action_plan("alpha", ledger.scope, event.receive_ts_ns, ActionKind.NEW,
                           intent=intent, tif="FOK", action_id="fok-action")
        return plan

    host = fresh_host(ledger, now)
    registration, _ = opt_registration(ledger, "alpha", lambda event, context: events.append(event) or [], event_result=initial)
    host.register(registration)
    base.deliver(host, now, base.book())
    assert len(events) == 1 and events[0].status == "REJECTED"
    assert events[0].metadata["provenance"] == "LOCAL_NOT_SENT"
    assert ledger.get("fok") is None
    assert ledger.order_feedback("alpha")[-1][1].status == "REJECTED"
    ledger.close()


def test_order_callback_time_budget_faults_only_the_slow_source_job(tmp_path):
    ledger = base.ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100_000), {"source": "synthetic"})
    now, called = [base.T], []
    clock_calls = iter((0, 1000))
    def callback_clock():
        return next(clock_calls, 1000)
    host = fresh_host(ledger, now, budgets=base.budget(max_callback_ns=10),
                      callback_clock_ns=callback_clock)
    slow, _ = opt_registration(ledger, "slow", lambda event, context: called.append("slow") or [])
    good, _ = opt_registration(ledger, "good", lambda event, context: called.append("good") or [])
    host.register(slow)
    host.register(good)
    local_rejection(ledger, "slow-event", "slow")
    local_rejection(ledger, "good-event", "good")
    base.deliver(host, now, base.book())
    report = host.report()["strategies"]
    assert report["slow"]["state"] == "FAULTED" and report["slow"]["reason"] == "TimeoutError"
    assert report["good"]["state"] != "FAULTED" and "good" in called
    ledger.close()


def test_post_only_ioc_gets_local_rejection_feedback_without_route_substitution(tmp_path):
    ledger = base.ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100_000), {"source": "synthetic"})
    now, feedback = [base.T], []
    intent = OrderIntent(intent_id="post-only-ioc", job_id="alpha", signal_id="signal-ioc",
        destination=OrderDestination.REAL, instrument=base.BTC, side=Side.BUY,
        created_ts_ns=base.T, client_order_id="client-post-only-ioc", quantity=D(1), limit_price=D(99))

    def initial(event, data):
        item = ExecutionAction("action-ioc", ActionKind.NEW, ledger.scope, "alpha", "config-1",
            TradingMode.PAPER, event.receive_ts_ns+100_000, intent=intent,
            post_only=True, time_in_force="IOC")
        return StrategyActionPlan("plan-ioc", "alpha", "config-1", event.receive_ts_ns, (item,))

    host = fresh_host(ledger, now)
    registration, _ = opt_registration(ledger, "alpha",
        lambda event, context: feedback.append(event) or [], event_result=initial)
    host.register(registration)
    errors = []
    original_consumer = host._consume_callback_result
    def inspect_consumer(*args, **kwargs):
        try:
            return original_consumer(*args, **kwargs)
        except Exception as exc:
            errors.append(str(exc))
            raise
    host._consume_callback_result = inspect_consumer
    base.deliver(host, now, base.book())
    assert len(feedback) == 1 and feedback[0].status == "REJECTED", (host.report()["strategies"], errors)
    assert feedback[0].metadata["provenance"] == "LOCAL_NOT_SENT"
    assert ledger.get("post-only-ioc") is None
    assert host.accounts[ledger.scope.key].report()["open_orders"] == {}
    ledger.close()


def test_stale_unavailable_order_context_cannot_dispatch_new_risk(tmp_path):
    ledger = base.ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100_000), {"source": "synthetic"})
    now, contexts = [base.T], []

    def new_from_feedback(event, context):
        contexts.append(context)
        retry = OrderIntent(intent_id="stale-retry", job_id="alpha", signal_id="retry",
            destination=OrderDestination.REAL, instrument=base.BTC, side=Side.BUY,
            created_ts_ns=context.now_ns, client_order_id="client-stale-retry",
            quantity=D(1), limit_price=D(99))
        return action_plan("alpha", ledger.scope, context.now_ns, ActionKind.NEW,
                           intent=retry, action_id="stale-retry-action")

    host = fresh_host(ledger, now)
    registration, _ = opt_registration(ledger, "alpha", new_from_feedback)
    host.register(registration)
    matcher = host.accounts[ledger.scope.key]
    matcher.on_book(base.book())
    local_rejection(ledger, "stale-event", "alpha")
    host.hot_path.apply_versioned(base.book())
    now[0] = base.T+2_000_000_000
    stale_snapshot = host.hot_path.snapshot(now[0], "PAPER")
    host._drain_feedback(stale_snapshot, "PAPER")
    assert contexts and contexts[0].data is None
    assert contexts[0].quality == "UNAVAILABLE"
    assert ledger.get("stale-retry") is None
    assert host.report()["strategies"]["alpha"]["state"] == "FAULTED"
    ledger.close()
