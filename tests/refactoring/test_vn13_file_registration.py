"""VN13 explicit strategy-file configuration through the existing Paper runtime."""
from copy import deepcopy
from decimal import Decimal as D
import importlib
import sys

import pytest

from test_strategy_runtime import T, BTC, book, deliver, ledger_at, matcher_for, runtime
from core.models import Bar1mEvent, OrderDestination, OrderIntent, Side
from execution.trading_contracts import ActionKind, ExecutionAction, TradingMode


def strategy_source(strategy_id, *, warmup=0, action="ENTER_LONG", price="100", extra=""):
    return f'''from decimal import Decimal as D
from vibetrading.models import Signal, SignalAction

STRATEGY_ID = {strategy_id!r}
SUBSCRIPTIONS = [{{"instrument_key": {BTC.key!r}, "event_type": "order_book"}}]
WARMUP = {{"bars": {warmup}}}
OBSERVED = []
{extra}
class Strategy:
    def on_event(self, event, data):
        OBSERVED.append((event.event_id, data.ready, data.phase, id(data.hot_path)))
        return [Signal(strategy_id=STRATEGY_ID, instrument=event.instrument,
            action=SignalAction.{action}, event_ts_ns=event.receive_ts_ns,
            reference_price=D({price!r}), valid_until_ts_ns=event.receive_ts_ns + 100_000,
            source_event_id=event.event_id)]

def create_strategy():
    return Strategy()
'''


@pytest.fixture
def strategy_files(tmp_path, monkeypatch):
    package = importlib.import_module("strategies")
    monkeypatch.setattr(package, "__path__", [*package.__path__, str(tmp_path)])
    created = []

    def write(name, source):
        (tmp_path / (name + ".py")).write_text(source, encoding="utf-8")
        monkeypatch.delitem(sys.modules, "strategies." + name, raising=False)
        created.append(name)
        return name

    yield write
    for name in created:
        sys.modules.pop("strategies." + name, None)


def loaded(name):
    return importlib.import_module("strategies." + name)


def configured(ledger, module, job_id, *, config_version="config-1", permission=True,
               warmup=0, config=None, risk=None, **changes):
    document = {
        "job_id": job_id,
        "module": module,
        "strategy_version": "strategy-1",
        "config_version": config_version,
        "config": config if config is not None else {"quantity": "1", "limit_price": "99"},
        "research_receipt": {"research_id": "vn13-synthetic-fixture"},
        "paper_permission": permission,
        "required_feeds": [BTC.key],
        "warmup_bars": warmup,
        "accounts": [ledger.scope.key],
        "risk": risk if risk is not None else {ledger.scope.key: {
            "funding_buffer": "0",
            "account_notional_limit": "100000",
            "strategy_notional_limit": "100000",
            "account_capital_limit": "100000",
            "strategy_capital_limit": "100000",
            "margin_rate": ".1",
        }},
    }
    document.update(changes)
    return document


def actions_for(ledger, strategy_id, job_id, side, *, config_version="config-1"):
    def build(signal, view, config):
        identity = job_id + "-" + signal.signal_id
        intent = OrderIntent(intent_id=identity, job_id=job_id, signal_id=signal.signal_id,
            destination=OrderDestination.REAL, instrument=BTC, side=side,
            created_ts_ns=signal.event_ts_ns, client_order_id="client-" + identity,
            quantity=D(config["quantity"]), limit_price=D(config["limit_price"]))
        return [ExecutionAction("action-" + identity, ActionKind.NEW, ledger.scope, strategy_id,
            config_version, TradingMode.PAPER, signal.valid_until_ts_ns,
            intent=intent, post_only=True)]
    return build


def strategy_receipts(ledger):
    return ledger._db.execute(
        "SELECT COUNT(*) FROM portfolio_receipts WHERE kind='strategy_event'").fetchone()[0]


def test_two_real_files_share_snapshot_and_link_to_existing_paper(tmp_path, strategy_files):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now)
        alpha = strategy_files("vn13_alpha_file",
            strategy_source("vn13_alpha", price="100"))
        beta = strategy_files("vn13_beta_file",
            strategy_source("vn13_beta", action="ENTER_SHORT", price="101"))
        host.register_configured(
            configured(ledger, "strategies/" + alpha + ".py", "vn13-alpha-job",
                       config={"quantity": "1", "limit_price": "99"}),
            action_builder=actions_for(ledger, "vn13_alpha", "vn13-alpha-job", Side.BUY))
        host.register_configured(
            configured(ledger, "strategies." + beta, "vn13-beta-job",
                       config={"quantity": "2", "limit_price": "101"}),
            action_builder=actions_for(ledger, "vn13_beta", "vn13-beta-job", Side.SELL))

        assert len(deliver(host, now, book())) == 2
        orders = matcher.report()["open_orders"]
        alpha_id = next(key for key, order in orders.items()
                        if order["strategy"] == "vn13_alpha")
        beta_id = next(key for key, order in orders.items()
                       if order["strategy"] == "vn13_beta")
        assert D(ledger.get(alpha_id)["quantity"]) == D("1")
        assert D(ledger.get(beta_id)["quantity"]) == D("2")

        alpha_observed, beta_observed = loaded(alpha).OBSERVED, loaded(beta).OBSERVED
        assert len(alpha_observed) == len(beta_observed) == 1
        assert alpha_observed[0][0] == beta_observed[0][0]
        assert alpha_observed[0][1:3] == beta_observed[0][1:3] == (True, "PAPER")
        assert alpha_observed[0][3] == beta_observed[0][3]
        report = host.report()
        assert report["strategies"]["vn13_alpha"]["callbacks"] == 1
        assert report["strategies"]["vn13_beta"]["callbacks"] == 1
        assert report["strategies"]["vn13_alpha"]["actions"] == 1
        assert report["strategies"]["vn13_beta"]["actions"] == 1
        assert report["production_gate"] == "BLOCKED"
    finally:
        ledger.close()


def test_warmup_file_waits_for_history_then_places_paper_order(tmp_path, strategy_files):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now)
        name = strategy_files("vn13_warm_file",
            strategy_source("vn13_warm", warmup=1))
        host.register_configured(configured(ledger, name, "vn13-warm-job", warmup=1),
            action_builder=actions_for(ledger, "vn13_warm", "vn13-warm-job", Side.BUY))

        deliver(host, now, book())
        assert not ledger.report()["orders"]
        assert not loaded(name).OBSERVED

        host.prepare_history(BTC.key, [Bar1mEvent(instrument=BTC, venue=BTC.venue,
            provider="scripted", receive_ts_ns=T-1, wallclock_ts_ns=T-1, source_ts_ns=T-1,
            open_ts_ns=T-60_000_000_001, close_ts_ns=T-1, open=D(100), high=D(101),
            low=D(99), close=D(100), volume=D(1))])
        assert len(deliver(host, now, book(T+1, sequence=2))) == 1
        assert len(ledger.report()["orders"]) == 1
        assert len(loaded(name).OBSERVED) == 1
    finally:
        ledger.close()


def test_false_permission_registers_blocked_and_never_orders(tmp_path, strategy_files):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now)
        name = strategy_files("vn13_denied_file",
            strategy_source("vn13_denied"))
        host.register_configured(configured(ledger, name, "vn13-denied-job", permission=False),
            action_builder=actions_for(ledger, "vn13_denied", "vn13-denied-job", Side.BUY))

        strategy = host.report()["strategies"]["vn13_denied"]
        assert (strategy["state"], strategy["reason"]) == ("BLOCKED", "paper_permission_missing")
        durable_before = strategy_receipts(ledger)
        assert deliver(host, now, book()) == ()
        assert not ledger.report()["orders"]
        assert strategy_receipts(ledger) == durable_before
        assert not loaded(name).OBSERVED
        assert host.report()["strategies"]["vn13_denied"]["state"] == "BLOCKED"
    finally:
        ledger.close()


def test_replay_configured_file_warms_without_orders_or_claims(tmp_path, strategy_files):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now, phase="replay")
        name = strategy_files("vn13_replay_file",
            strategy_source("vn13_replay"))
        host.register_configured(configured(ledger, name, "vn13-replay-job"),
            action_builder=actions_for(ledger, "vn13_replay", "vn13-replay-job", Side.BUY))

        assert deliver(host, now, book()) == ()
        assert not ledger.report()["orders"]
        assert strategy_receipts(ledger) == 0
        assert len(loaded(name).OBSERVED) == 1
        strategy = host.report()["strategies"]["vn13_replay"]
        assert (strategy["state"], strategy["callbacks"], strategy["actions"]) == (
            "REPLAY", 1, 0)
        evidence = host.report()["restart_evidence"]
        assert evidence["strategy_state_recovery"] == "UNVERIFIED"
        assert evidence["full_restart_gate"] == "BLOCKED"
    finally:
        ledger.close()


def test_latest_ledger_restart_remains_unverified_without_callback_replay(tmp_path, strategy_files):
    now = [T]
    ledger = ledger_at(tmp_path)
    ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
    name = strategy_files("vn13_restart_file",
        strategy_source("vn13_restart"))
    module = "vibetrading.strategies." + name
    host = runtime(ledger, matcher_for(ledger), now)
    host.register_configured(configured(ledger, module, "vn13-restart-job"),
        action_builder=actions_for(ledger, "vn13_restart", "vn13-restart-job", Side.BUY))
    deliver(host, now, book())
    assert len(ledger.report()["orders"]) == 1
    observed = len(loaded(name).OBSERVED)
    ledger.close()

    ledger = ledger_at(tmp_path)
    try:
        host = runtime(ledger, matcher_for(ledger), now)
        host.register_configured(configured(ledger, module, "vn13-restart-job"),
            action_builder=actions_for(ledger, "vn13_restart", "vn13-restart-job", Side.BUY))
        strategy = host.report()["strategies"]["vn13_restart"]
        assert (strategy["state"], strategy["reason"]) == (
            "BLOCKED", "strategy_state_restore_unverified")
        orders_before = len(ledger.report()["orders"])
        deliver(host, now, book(T+1, sequence=2))
        assert len(ledger.report()["orders"]) == orders_before
        assert len(loaded(name).OBSERVED) == observed
        assert host.report()["strategies"]["vn13_restart"]["callbacks"] == 0
        assert host.report()["restart_evidence"]["full_restart_gate"] == "BLOCKED"
    finally:
        ledger.close()


def test_duplicate_module_strategy_id_cannot_replace_running_job(tmp_path, strategy_files):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now)
        first = strategy_files("vn13_dup_first",
            strategy_source("vn13_dup"))
        second = strategy_files("vn13_dup_second",
            strategy_source("vn13_dup"))
        host.register_configured(configured(ledger, first, "vn13-first-job"),
            action_builder=actions_for(ledger, "vn13_dup", "vn13-first-job", Side.BUY))
        deliver(host, now, book())
        assert len(ledger.report()["orders"]) == 1

        durable_before = strategy_receipts(ledger)
        with pytest.raises(ValueError, match="duplicate or exceeds capacity"):
            host.register_configured(
                configured(ledger, second, "vn13-second-job", config_version="config-2"),
                action_builder=actions_for(ledger, "vn13_dup", "vn13-second-job", Side.SELL,
                                           config_version="config-2"))
        assert strategy_receipts(ledger) == durable_before
        assert set(host.report()["strategies"]) == {"vn13_dup"}
        assert not loaded(second).OBSERVED

        deliver(host, now, book(T+1, sequence=2))
        assert len(ledger.report()["orders"]) == 2
        assert host.report()["strategies"]["vn13_dup"]["callbacks"] == 2
        assert len(loaded(first).OBSERVED) == 2
    finally:
        ledger.close()


def test_oversize_document_is_rejected_before_import_or_claim(tmp_path, strategy_files):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now)
        marker = "from pathlib import Path\nPath(__file__).with_suffix('.imported').write_text('imported')\n"
        name = strategy_files("vn13_oversize_file",
            strategy_source("vn13_oversize", extra=marker))
        document = configured(ledger, name, "vn13-oversize-job",
            config={"quantity": "1", "limit_price": "99", "padding": "x" * 40000})

        with pytest.raises(ValueError, match="capacity exceeded"):
            host.register_configured(document,
                action_builder=actions_for(ledger, "vn13_oversize", "vn13-oversize-job", Side.BUY))
        assert not (tmp_path / (name + ".imported")).exists()
        assert strategy_receipts(ledger) == 0
        assert host.report()["strategies"] == {}
    finally:
        ledger.close()


@pytest.mark.parametrize("case", [
    "not-mapping", "missing-field", "unknown-field", "module-not-string",
    "module-empty", "config-list", "receipt-string", "feeds-string", "accounts-string",
    "permission-string", "risk-not-mapping", "risk-account-mismatch",
    "risk-value-not-mapping", "risk-missing-field", "risk-extra-field",
    "risk-bad-decimal", "warmup-bool", "absolute", "traversal", "legacy",
    "research", "namespace",
])
def test_invalid_configuration_documents_leave_no_durable_claim(
        tmp_path, strategy_files, case):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher, now = matcher_for(ledger), [T]
        host = runtime(ledger, matcher, now)
        name = strategy_files("vn13_invalid_file",
            strategy_source("vn13_invalid"))
        document = configured(ledger, name, "vn13-invalid-job")
        if case == "not-mapping":
            document = []
        elif case == "missing-field":
            document.pop("job_id")
        elif case == "unknown-field":
            document["unexpected"] = "value"
        elif case == "module-not-string":
            document["module"] = 123
        elif case == "module-empty":
            document["module"] = "   "
        elif case == "config-list":
            document["config"] = []
        elif case == "receipt-string":
            document["research_receipt"] = "receipt"
        elif case == "feeds-string":
            document["required_feeds"] = BTC.key
        elif case == "accounts-string":
            document["accounts"] = ledger.scope.key
        elif case == "permission-string":
            document["paper_permission"] = "yes"
        elif case == "risk-not-mapping":
            document["risk"] = []
        elif case == "risk-account-mismatch":
            document["risk"] = {"other-account": next(iter(document["risk"].values()))}
        elif case == "risk-value-not-mapping":
            document["risk"][ledger.scope.key] = "risk"
        elif case == "risk-missing-field":
            del document["risk"][ledger.scope.key]["margin_rate"]
        elif case == "risk-extra-field":
            document["risk"][ledger.scope.key]["unexpected"] = "0"
        elif case == "risk-bad-decimal":
            document["risk"][ledger.scope.key]["funding_buffer"] = "not-a-number"
        elif case == "warmup-bool":
            document["warmup_bars"] = True
        elif case == "absolute":
            document["module"] = "/tmp/vn13_strategy.py"
        elif case == "traversal":
            document["module"] = "../vn13_strategy.py"
        elif case == "legacy":
            document["module"] = "strategies/legacy/vn13_strategy.py"
        elif case == "research":
            document["module"] = "vibetrading.strategies.research.vn13_strategy"
        elif case == "namespace":
            document["module"] = "other.namespace.vn13_strategy"

        durable_before = strategy_receipts(ledger)
        with pytest.raises(ValueError):
            host.register_configured(deepcopy(document),
                action_builder=actions_for(ledger, "vn13_invalid", "vn13-invalid-job", Side.BUY))
        assert strategy_receipts(ledger) == durable_before
        assert host.report()["strategies"] == {}
        assert not ledger.report()["orders"]
    finally:
        ledger.close()
