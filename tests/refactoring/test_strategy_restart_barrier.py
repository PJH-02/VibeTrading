"""Restart barrier regressions for durable Paper strategy-event evidence."""
from dataclasses import replace
from decimal import Decimal as D
import hashlib

import pytest

from test_strategy_runtime import (T, BTC, budget, book, deliver, ledger_at, matcher_for,
                                   registration, runtime, spec)
from vibetrading.execution_ledger import AccountScope
from market.hot_path import HotPathStore
from vibetrading.portfolio_ledger import PortfolioLedger
from vibetrading.strategy_runtime import PaperStrategyRuntime, StrategyRisk
from vibetrading.trading_contracts import TradingMode


def account_ledger(root, account):
    base = root / account
    return PortfolioLedger(base / "paper" / "account.sqlite",
        AccountScope(BTC.venue.value, account, "perpetual", "paper"), owner_root=base,
        mode=TradingMode.PAPER, max_orders=100, max_fills=100, max_events=1000,
        max_document_bytes=1_000_000, max_db_bytes=16_000_000, max_wal_bytes=16_000_000)


def multi_runtime(ledgers, now):
    accounts = {ledger.scope.key: matcher_for(ledger) for ledger in ledgers}
    return PaperStrategyRuntime(hot_path=HotPathStore(), accounts=accounts,
        products={BTC.key: spec()}, known_instruments={BTC.key: BTC}, reference_feeds={},
        budgets=budget(), settings={"fixture": "explicit local inputs"},
        mode=TradingMode.PAPER, phase="paper", now_ns=lambda: now[0])


def multi_registration(ledgers, sid, observed):
    template = registration(ledgers[0], sid, observed)
    risk = next(iter(template.risk.values()))
    return replace(template, accounts=tuple(ledger.scope.key for ledger in ledgers),
        risk={ledger.scope.key: risk for ledger in ledgers})


def strategy_receipt_count(ledger):
    return ledger._db.execute(
        "SELECT COUNT(*) FROM portfolio_receipts WHERE kind='strategy_event'").fetchone()[0]


def test_new_event_after_restart_has_no_callback_or_new_order_and_matcher_is_intact(tmp_path):
    now, observed = [T], []
    ledger = ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
    matcher = matcher_for(ledger)
    host = runtime(ledger, matcher, now)
    host.register(registration(ledger, "one", observed))
    deliver(host, now, book())
    matching_before = matcher.report()
    ledger.close()

    ledger = ledger_at(tmp_path)
    try:
        matcher = matcher_for(ledger)
        host = runtime(ledger, matcher, now)
        host.register(registration(ledger, "one", observed))
        # Registration alone must not disturb restored matcher state.
        assert matcher.report() == matching_before
        before_orders = len(ledger.report()["orders"])
        # A genuinely new event must not reset/replay the strategy after restart.
        deliver(host, now, book(T+1, sequence=2))
        assert len(observed) == 1
        assert len(ledger.report()["orders"]) == before_orders
        report = host.report()
        assert report["strategies"]["one"]["state"] == "BLOCKED"
        assert report["strategies"]["one"]["reason"] == "strategy_state_restore_unverified"
        assert report["strategies"]["one"]["callbacks"] == 0
        assert report["strategies"]["one"]["actions"] == 0
        evidence = report["restart_evidence"]
        assert evidence["blocked_strategies"] == ("one",)
        assert evidence["strategy_state_recovery"] == "UNVERIFIED"
        assert evidence["continuous_input_cursor"] == "MISSING_EVIDENCE"
        assert evidence["full_restart_gate"] == "BLOCKED"
        assert report["production_gate"] == "BLOCKED"
    finally:
        ledger.close()


def test_two_new_events_in_the_same_runtime_remain_allowed(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        host = runtime(ledger, matcher, now)
        host.register(registration(ledger, "one", observed))
        assert len(deliver(host, now, book(T, sequence=1))) == 1
        assert len(deliver(host, now, book(T+1, sequence=2))) == 1
        assert len(observed) == 2
        assert len(ledger.report()["orders"]) == 2
        assert host.report()["strategies"]["one"]["state"] == "PAPER"
    finally:
        ledger.close()


def test_registration_only_reopen_and_a_different_strategy_are_not_fenced(tmp_path):
    ledger = ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
    now, observed = [T], []
    host = runtime(ledger, matcher_for(ledger), now)
    host.register(registration(ledger, "one", observed))
    assert ledger.has_strategy_dispatch("one") is False
    ledger.close()
    try:
        ledger = ledger_at(tmp_path)
        host = runtime(ledger, matcher_for(ledger), now)
        host.register(registration(ledger, "one", observed))
        assert host.report()["strategies"]["one"]["state"] != "BLOCKED"
        assert len(deliver(host, now, book())) == 1
    finally:
        ledger.close()

    other = tmp_path / "other-strategy"
    ledger = ledger_at(other)
    ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
    observed_other = []
    now_other = [T]
    host = runtime(ledger, matcher_for(ledger), now_other)
    host.register(registration(ledger, "one", observed_other))
    deliver(host, now_other, book())
    ledger.close()
    try:
        ledger = ledger_at(other)
        host = runtime(ledger, matcher_for(ledger), now_other)
        host.register(registration(ledger, "two", observed_other))
        assert host.report()["strategies"]["two"]["state"] != "BLOCKED"
        assert len(deliver(host, now_other, book(T+20, sequence=2))) == 1
    finally:
        ledger.close()


def test_action_only_evidence_fences_all_config_versions_and_new_config_cannot_bypass(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.claim_strategy_event("one", "config-9", "@action:durable", {"action": "fixture"})
        assert ledger.has_strategy_dispatch("one") is True
        assert ledger.has_strategy_dispatch("two") is False
        with pytest.raises(ValueError, match="strategy"):
            ledger.has_strategy_dispatch("")
        now, observed = [T], []
        host = runtime(ledger, matcher_for(ledger), now)
        host.register(registration(ledger, "one", observed))
        assert host.report()["strategies"]["one"]["state"] == "BLOCKED"
    finally:
        ledger.close()

    bypass = tmp_path / "config-bypass"
    ledger = ledger_at(bypass)
    ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
    now, observed = [T], []
    host = runtime(ledger, matcher_for(ledger), now)
    host.register(registration(ledger, "one", observed))
    deliver(host, now, book())
    ledger.close()
    try:
        ledger = ledger_at(bypass)
        host = runtime(ledger, matcher_for(ledger), now)
        changed = replace(registration(ledger, "one", observed), config_version="config-2")
        host.register(changed)
        assert host.report()["strategies"]["one"]["state"] == "BLOCKED"
        assert host.report()["strategies"]["one"]["reason"] == "strategy_state_restore_unverified"
    finally:
        ledger.close()


def test_same_config_registration_collision_still_fails_closed(tmp_path):
    ledger = ledger_at(tmp_path)
    now, observed = [T], []
    host = runtime(ledger, matcher_for(ledger), now)
    host.register(registration(ledger, "one", observed))
    ledger.close()
    try:
        ledger = ledger_at(tmp_path)
        host = runtime(ledger, matcher_for(ledger), now)
        changed_config = replace(registration(ledger, "one", observed), config={"quantity": "2"})
        with pytest.raises(ValueError, match="Immutable strategy_event receipt collision"):
            host.register(changed_config)
        assert host.report()["strategies"] == {}
    finally:
        ledger.close()


def test_evidence_in_one_of_multiple_accounts_fences_the_whole_registration(tmp_path):
    first, second = account_ledger(tmp_path, "first"), account_ledger(tmp_path, "second")
    try:
        first.seed_balance("USDT", D(100000), {"source": "synthetic"})
        second.seed_balance("USDT", D(100000), {"source": "synthetic"})
        first.claim_strategy_event("one", "config-1", "@action:prior", {"action": "fixture"})
        now, observed = [T], []
        host = multi_runtime([first, second], now)
        host.register(multi_registration([first, second], "one", observed))
        assert host.report()["strategies"]["one"]["state"] == "BLOCKED"
        assert host.report()["restart_evidence"]["blocked_strategies"] == ("one",)
        assert len(deliver(host, now, book())) == 0
        assert not first.report()["orders"] and not second.report()["orders"]
    finally:
        first.close()
        second.close()


def test_malformed_claimed_receipts_fail_closed(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        with ledger.transaction():
            ledger._db.execute("INSERT INTO portfolio_receipts VALUES(?,?,?,?)",
                ("strategy_event", "bad-json", "not-a-fingerprint", "{not-json"))
        with pytest.raises(ValueError, match="Malformed strategy event receipt"):
            ledger.has_strategy_dispatch("one")
    finally:
        ledger.close()

    ledger = ledger_at(tmp_path / "schema")
    try:
        document = ('{"strategy":"one","config_version":"config-1",'
                    '"event_id":"@action:missing-evidence"}')
        fingerprint = hashlib.sha256(document.encode()).hexdigest()
        with ledger.transaction():
            ledger._db.execute("INSERT INTO portfolio_receipts VALUES(?,?,?,?)",
                ("strategy_event", "bad-schema", fingerprint, document))
        with pytest.raises(ValueError, match="Malformed strategy event receipt"):
            ledger.has_strategy_dispatch("one")
    finally:
        ledger.close()


def test_evidence_read_failure_rejects_registration_before_any_claim(tmp_path):
    first, second = account_ledger(tmp_path, "first"), account_ledger(tmp_path, "second")
    now, observed = [T], []
    host = multi_runtime([first, second], now)
    try:
        def fail(_strategy):
            raise RuntimeError("scripted evidence read failure")
        second.has_strategy_dispatch = fail
        with pytest.raises(RuntimeError, match="scripted evidence read failure"):
            host.register(multi_registration([first, second], "one", observed))
        assert host.report()["strategies"] == {}
        assert strategy_receipt_count(first) == 0
        assert strategy_receipt_count(second) == 0
    finally:
        first.close()
        second.close()


def test_replay_remains_non_durable_and_cannot_clear_the_paper_barrier(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        now, observed = [T], []
        matcher = matcher_for(ledger)
        host = runtime(ledger, matcher, now)
        host.register(registration(ledger, "one", observed))
        deliver(host, now, book())
        matching_before = matcher.report()
        durable_before = strategy_receipt_count(ledger)
        replay = runtime(ledger, matcher, now, phase="replay")
        replay.register(registration(ledger, "one", observed))
        assert replay.report()["strategies"]["one"]["state"] != "BLOCKED"
        deliver(replay, now, book(T+1, sequence=2))
        assert len(observed) == 2
        assert matcher.report() == matching_before
        assert strategy_receipt_count(ledger) == durable_before
        # Replay warmed callbacks but supplied no restored-strategy proof.
        paper = runtime(ledger, matcher, now)
        paper.register(registration(ledger, "one", observed))
        assert paper.report()["strategies"]["one"]["state"] == "BLOCKED"
    finally:
        ledger.close()


def test_pause_resume_and_halt_cannot_clear_the_restart_barrier(tmp_path):
    now, observed = [T], []
    ledger = ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
    host = runtime(ledger, matcher_for(ledger), now)
    host.register(registration(ledger, "one", observed))
    deliver(host, now, book())
    ledger.close()

    ledger = ledger_at(tmp_path)
    try:
        matcher = matcher_for(ledger)
        host = runtime(ledger, matcher, now)
        host.register(registration(ledger, "one", observed))
        for operation in (lambda: host.pause("one", T+2), lambda: host.resume("one"),
                          lambda: host.halt("operator_halt", T+3, "one"),
                          lambda: host.pause("one", T+4), lambda: host.resume("one")):
            operation()
            strategy = host.report()["strategies"]["one"]
            assert strategy["state"] == "BLOCKED"
            assert strategy["reason"] == "strategy_state_restore_unverified"
        before_orders = len(ledger.report()["orders"])
        deliver(host, now, book(T+5, sequence=2))
        assert len(observed) == 1
        assert len(ledger.report()["orders"]) == before_orders
    finally:
        ledger.close()


def test_predicate_requires_an_open_owned_paper_ledger(tmp_path, monkeypatch):
    ledger = ledger_at(tmp_path)
    try:
        with pytest.raises(ValueError, match="strategy"):
            ledger.has_strategy_dispatch("")
        monkeypatch.setattr(ledger, "mode", TradingMode.SANDBOX)
        with pytest.raises(ValueError, match="paper"):
            ledger.has_strategy_dispatch("one")
    finally:
        ledger.close()
    with pytest.raises(RuntimeError, match="open owning thread"):
        ledger.has_strategy_dispatch("one")
