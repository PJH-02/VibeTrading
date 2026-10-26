"""TX08A: one explicit existing-account policy across strategy registrations.

These tests pin the bounded registration guard only.  Account-wide notional and
capital limits are shared by every strategy on the same physical account, while
strategy-scoped limits and funding buffers remain independent.
"""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from test_strategy_runtime import (T, BTC, book, budget, deliver, ledger_at, matcher_for,
    module, registration, runtime, spec)
from execution.execution_ledger import AccountScope
from market.hot_path import HotPathStore
from core.models import OrderDestination, OrderIntent, Side
from portfolio.portfolio_ledger import PortfolioLedger
from runtime.strategy_runtime import PaperStrategyRuntime, StrategyRisk
from execution.trading_contracts import TradingMode


def ledger_for(root, account, instrument=BTC):
    return PortfolioLedger(
        root / "paper" / (account + ".sqlite"),
        AccountScope(instrument.venue.value, account,
                     "perpetual" if instrument.asset_class.value == "crypto_perp" else "cash",
                     "paper"),
        owner_root=root, mode=TradingMode.PAPER, max_orders=100, max_fills=100,
        max_events=1000, max_document_bytes=1_000_000, max_db_bytes=16_000_000,
        max_wal_bytes=16_000_000)


def registration_with(ledger, sid, observed, *, risk=None, **changes):
    base = registration(ledger, sid, observed, **changes)
    if risk:
        key = base.accounts[0]
        values = dict(vars(base.risk[key]))
        values.update(risk)
        base = replace(base, risk={key: StrategyRisk(**values)})
    return base


def registration_for_accounts(ledgers, sid, observed, *, risk=None, **changes):
    base = registration(ledgers[0], sid, observed, **changes)
    template = dict(vars(base.risk[base.accounts[0]]))
    overrides = risk or {}
    values = {}
    for ledger in ledgers:
        account = dict(template)
        account.update(overrides.get(ledger.scope.key, {}))
        values[ledger.scope.key] = StrategyRisk(**account)
    return replace(base, accounts=tuple(ledger.scope.key for ledger in ledgers), risk=values)


def receipt_count(ledger):
    return ledger._db.execute(
        "SELECT COUNT(*) FROM portfolio_receipts").fetchone()[0]


def durable_snapshot(ledger):
    return (ledger.account_version(), receipt_count(ledger),
            tuple(sorted((row["intent_id"], row["state"])
                         for row in ledger.report()["orders"])))


@pytest.mark.parametrize("field", ("account_notional_limit", "account_capital_limit"))
@pytest.mark.parametrize("value", ("1000", "200000"))
def test_higher_or_lower_shared_account_limit_is_rejected_before_claim(tmp_path, field, value):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed, created = matcher_for(ledger), [T], [], []
        host = runtime(ledger, matcher, now)
        host.register(registration_with(ledger, "alpha", observed))
        beta = registration_with(ledger, "beta", observed, risk={field: value})

        def spy_create():
            created.append("beta")
            return module("beta", observed).create()

        beta.module.module.create_strategy = spy_create
        alpha_state = host.report()["strategies"]["alpha"]
        before = durable_snapshot(ledger)
        with pytest.raises(ValueError, match="Shared account risk limits"):
            host.register(beta)
        assert durable_snapshot(ledger) == before
        assert host.report()["strategies"] == {"alpha": alpha_state}
        assert not ledger.has_strategy_dispatch("beta")
        assert not created
    finally:
        ledger.close()


def test_equal_numeric_shared_caps_and_distinct_strategy_caps_are_allowed(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        host = runtime(ledger, matcher, now)
        host.register(registration_with(ledger, "alpha", observed))
        host.register(registration_with(
            ledger, "beta", observed,
            risk={"account_notional_limit": "100000.00",
                  "account_capital_limit": "100000.000",
                  "strategy_notional_limit": "7",
                  "strategy_capital_limit": "9",
                  "funding_buffer": "5"}))
        assert set(host.report()["strategies"]) == {"alpha", "beta"}
    finally:
        ledger.close()


@pytest.mark.parametrize("lifecycle", ("paused", "blocked", "faulted"))
def test_lifecycle_state_cannot_loosen_shared_account_policy(tmp_path, lifecycle):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        host = runtime(ledger, matcher, now)
        changes = {"risk": {"account_notional_limit": "1000",
                            "account_capital_limit": "1000"}}
        if lifecycle == "blocked":
            changes["permission"] = False
        if lifecycle == "faulted":
            changes["fault"] = True
        host.register(registration_with(ledger, "alpha", observed, **changes))
        if lifecycle == "paused":
            host.pause("alpha", T)
        elif lifecycle == "faulted":
            deliver(host, now, book())
        expected = {"paused": "PAUSED", "blocked": "BLOCKED", "faulted": "FAULTED"}[lifecycle]
        assert host.report()["strategies"]["alpha"]["state"] == expected
        before = durable_snapshot(ledger)
        with pytest.raises(ValueError, match="Shared account risk limits"):
            host.register(registration_with(ledger, "beta", observed))
        assert durable_snapshot(ledger) == before
        assert not ledger.has_strategy_dispatch("beta")
    finally:
        ledger.close()


def test_distinct_physical_accounts_can_declare_distinct_caps(tmp_path):
    first = ledger_for(tmp_path, "synthetic-a")
    second = ledger_for(tmp_path, "synthetic-b")
    try:
        first.seed_balance("USDT", D(100000), {"source": "synthetic"})
        second.seed_balance("USDT", D(100000), {"source": "synthetic"})
        now, observed = [T], []
        host = PaperStrategyRuntime(
            hot_path=HotPathStore(),
            accounts={first.scope.key: matcher_for(first),
                      second.scope.key: matcher_for(second)},
            products={BTC.key: spec()}, known_instruments={BTC.key: BTC},
            reference_feeds={}, budgets=budget(),
            settings={"fixture": "explicit local inputs"}, mode=TradingMode.PAPER,
            phase="paper", now_ns=lambda: now[0])
        first_policy = {"account_notional_limit": "1000",
                        "account_capital_limit": "2000"}
        second_policy = {"account_notional_limit": "100000",
                         "account_capital_limit": "200000"}
        host.register(registration_for_accounts(
            [first], "alpha", observed, risk={first.scope.key: first_policy}))
        host.register(registration_for_accounts(
            [second], "beta", observed, risk={second.scope.key: second_policy}))
        host.register(registration_for_accounts(
            [first, second], "gamma", observed,
            risk={first.scope.key: first_policy, second.scope.key: second_policy}))
        assert set(host.report()["strategies"]) == {"alpha", "beta", "gamma"}

        before = durable_snapshot(first)
        with pytest.raises(ValueError, match="Shared account risk limits"):
            host.register(registration_for_accounts(
                [first, second], "delta", observed,
                risk={first.scope.key: first_policy,
                      second.scope.key: {**second_policy,
                                         "account_notional_limit": "99999"}}))
        assert durable_snapshot(first) == before
        assert not first.has_strategy_dispatch("delta")
        assert not second.has_strategy_dispatch("delta")
    finally:
        first.close()
        second.close()


def paper_order(identity, side):
    return OrderIntent(
        intent_id=identity, job_id="job", signal_id="signal-" + identity,
        destination=OrderDestination.REAL, instrument=BTC, side=side,
        created_ts_ns=1, client_order_id="client-" + identity,
        quantity=D("1"), limit_price=D("100"))


def admit_shared(book, identity, side, strategy):
    rules = spec()
    return book.admit_order(
        paper_order(identity, side), strategy, {"source": "tx08a local test"},
        T + 1_000_000, rules, funding_buffer=D(0),
        account_notional_limit=D("2000"), strategy_notional_limit=D("5000"),
        account_capital_limit=D("100000"), strategy_capital_limit=D("5000"),
        margin_rate=D(".1"), now_ns=T)


def test_consistent_shared_cap_admits_exact_budget_and_never_nets_opposing_risk(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        created, _ = admit_shared(ledger, "long", Side.BUY, "alpha")
        assert created
        created, _ = admit_shared(ledger, "short", Side.SELL, "beta")
        assert created
        with pytest.raises(ValueError, match="notional"):
            admit_shared(ledger, "third", Side.BUY, "gamma")
        assert len(ledger.report()["orders"]) == 2
    finally:
        ledger.close()


def test_over_budget_sibling_fault_does_not_spend_or_cancel_healthy_orders(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        host = runtime(ledger, matcher, now)
        host.register(registration_with(ledger, "alpha", observed))
        deliver(host, now, book())
        assert len([row for row in ledger.report()["orders"]
                    if row["strategy"] == "alpha"]) == 1

        host.register(registration_with(
            ledger, "beta", observed, side=Side.SELL,
            risk={"strategy_notional_limit": "10"}))
        deliver(host, now, book(T + 1, sequence=2))
        assert host.report()["strategies"]["beta"]["state"] == "FAULTED"
        assert host.report()["strategies"]["alpha"]["state"] == "PAPER"
        alpha_rows = [row for row in ledger.report()["orders"]
                      if row["strategy"] == "alpha"]
        assert len(alpha_rows) == 2
        assert all(row["state"] == "OPEN" for row in alpha_rows)
        assert not [row for row in ledger.report()["orders"]
                    if row["strategy"] == "beta"]
    finally:
        ledger.close()


def test_denied_sibling_does_not_corrupt_healthy_dispatch(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now, observed = matcher_for(ledger), [T], []
        host = runtime(ledger, matcher, now)
        host.register(registration_with(ledger, "alpha", observed))
        host.register(registration_with(ledger, "denied", observed, permission=False))
        deliver(host, now, book())
        deliver(host, now, book(T + 1, sequence=2))
        assert host.report()["strategies"]["denied"]["state"] == "BLOCKED"
        assert host.report()["strategies"]["alpha"]["state"] == "PAPER"
        assert [row for row in ledger.report()["orders"]
                if row["strategy"] == "denied"] == []
        assert len([row for row in ledger.report()["orders"]
                    if row["strategy"] == "alpha"]) == 2
    finally:
        ledger.close()
