"""Coupled runtime checkpoint schema, staged Paper restore, and fail-closed gates."""
import asyncio
from dataclasses import replace
import hashlib
import json
import sqlite3
import threading
from decimal import Decimal as D
from types import ModuleType, SimpleNamespace

import pytest

from vibetrading.execution_ledger import AccountScope
from vibetrading.hot_path import HotPathStore
from core.models import AssetClass, InstrumentId, OrderBookEvent, OrderBookLevel, Side, Signal, SignalAction, Venue
from vibetrading.paper_execution import PaperExecution
from vibetrading.portfolio_ledger import PortfolioLedger
from vibetrading.runtime_checkpoint import (FORMAT, VERSION, RuntimeCheckpoint,
                                             RuntimeCheckpointError, _canonical, _etf_evidence,
                                             _load, _verify_evidence, journal_evidence)
from vibetrading.strategy_contract import StrategyModule
from vibetrading.strategy_runtime import (PaperStrategyRuntime, RuntimeBudgets,
                                          StrategyRegistration, StrategyRisk)
from vibetrading.trading_contracts import ProductKind, ProductSpec, TradingMode

T = 1_800_000_000_000_000_000
BTC = InstrumentId(venue=Venue.BYBIT, asset_class=AssetClass.CRYPTO_PERP,
                   raw_symbol="BTCUSDT", std_symbol="BTCUSDT", currency="USDT")


def ledger_at(root):
    return PortfolioLedger(root/"paper"/"account.sqlite",
        AccountScope(BTC.venue.value, "synthetic-paper", "perpetual", "paper"),
        owner_root=root, mode=TradingMode.PAPER, max_orders=100, max_fills=100,
        max_events=1000, max_document_bytes=1_000_000, max_db_bytes=16_000_000,
        max_wal_bytes=16_000_000)


def matcher_for(ledger):
    return PaperExecution(ledger, entry_latency_ns=10, cancel_latency_ns=5,
        max_open_orders=16, max_depth=8, max_receipts=100, max_state_bytes=500_000,
        stale_after_ns=1_000_000_000)


def spec():
    return ProductSpec(BTC, ProductKind.LINEAR_PERPETUAL, D(10), D(1), D(1), D(1),
        "USDT", "USDT", "USDT", "net", "isolated", "synthetic fee",
        "explicit simulated funding only", "synthetic margin", D(".001"), D(".002"))


def budgets():
    return RuntimeBudgets(max_jobs=4, max_accounts=2, max_required_feeds=8,
        max_signals_per_callback=2, max_actions_per_callback=2, max_pending_actions=4,
        max_seen_actions=100, max_config_bytes=32_000, max_signal_bytes=8192,
        max_action_bytes=16_000, max_callback_ns=1_000_000_000, max_feed_age_ns=1_000_000_000)


def module(sid, observed, *, bad_restore=False, restore_mode="sync"):
    source = ModuleType("checkpoint_"+sid)
    class Dummy:
        def __init__(self):
            self.state = {"count": 0}
        def checkpoint_state(self):
            return {"count": self.state["count"]}
        if restore_mode != "missing":
            def restore_state(self, state):
                if restore_mode == "async":
                    async def pending():
                        self.state = {"count": state["count"]}
                    return pending()
                self.state = {"count": (-1 if bad_restore else state["count"])}
        def on_event(self, event, data):
            self.state["count"] += 1
            observed.append((sid, self.state["count"]))
            return [Signal(strategy_id=sid, instrument=event.instrument,
                action=SignalAction.ENTER_LONG, event_ts_ns=event.receive_ts_ns,
                reference_price=D(100), valid_until_ts_ns=event.receive_ts_ns+100_000,
                source_event_id=event.event_id)]
    source.create_strategy = Dummy
    return StrategyModule(source, sid, [{"instrument_key": BTC.key, "event_type": "order_book"}],
                          {"bars": 0})


def registration(ledger, sid, observed, *, bad_restore=False, restore_mode="sync"):
    return StrategyRegistration(sid, module(sid, observed, bad_restore=bad_restore,
                                           restore_mode=restore_mode),
        "strategy-1", "config-1", {"quantity": "1"}, {"research_id": "synthetic-fixture"},
        True, (BTC.key,), 0, (ledger.scope.key,),
        {ledger.scope.key: StrategyRisk(D(0), D(100000), D(100000), D(100000),
                                        D(100000), D(".1"))},
        lambda signal, view, config: [])


def host_for(ledger, matcher, now):
    return PaperStrategyRuntime(hot_path=HotPathStore(), accounts={ledger.scope.key: matcher},
        products={BTC.key: spec()}, known_instruments={BTC.key: BTC}, reference_feeds={},
        budgets=budgets(), settings={"fixture": "runtime-checkpoint"},
        mode=TradingMode.PAPER, phase="paper", now_ns=lambda: now[0])


def book(now=T, sequence=1):
    return OrderBookEvent(instrument=BTC, venue=BTC.venue, provider="scripted",
        event_id="book-"+str(now), receive_ts_ns=now, wallclock_ts_ns=now,
        source_ts_ns=now, sequence=sequence, final_update_id=sequence,
        sync_generation=1, depth=1, bids=[OrderBookLevel(price=D(99), quantity=D(5))],
        asks=[OrderBookLevel(price=D(101), quantity=D(5))])


def valid_payload():
    body = {"market": {}, "market_identity": {}, "paper": None, "etf": None,
            "journals": {"paper": [], "etf": None}}
    return _canonical({"format": FORMAT, "version": VERSION,
        "sha256": hashlib.sha256(_canonical(body, 262144)).hexdigest(), "body": body},
        262144)


def test_runtime_envelope_rejects_duplicate_noncanonical_and_checksum_tamper():
    payload = valid_payload()
    assert _load(payload, 262144)["paper"] is None
    with pytest.raises(RuntimeCheckpointError, match="not canonical"):
        _load(json.dumps(json.loads(payload)).encode(), 262144)
    duplicate = payload.replace(b'"market":{}', b'"market":{},"market":{}', 1)
    with pytest.raises(RuntimeCheckpointError, match="duplicate"):
        _load(duplicate, 262144)
    document = json.loads(payload)
    document["body"]["paper"] = []
    with pytest.raises(RuntimeCheckpointError, match="checksum"):
        _load(_canonical(document, 262144), 262144)
    with pytest.raises(RuntimeCheckpointError, match="byte bound"):
        _load(payload, 10)


def test_recovery_gate_blocks_callbacks_and_advance_until_verified_publication(tmp_path):
    now, observed = [T], []
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher = matcher_for(ledger)
        host = host_for(ledger, matcher, now)
        host.register(registration(ledger, "one", observed))
        host.begin_checkpoint_recovery()
        assert host.recovery_state == "RECOVERING"
        before = matcher.report()
        assert host.process_event(book()) == ()
        host.advance(T+10)
        assert observed == [] and matcher.report() == before
        assert ledger.report()["orders"] == []
        host.quarantine_checkpoint_recovery("checksum")
        assert host.recovery_state == "QUARANTINED"
        now[0] = T+20
        assert host.process_event(book(T+20, sequence=2)) == ()
        assert observed == [] and ledger.report()["orders"] == []
        with pytest.raises(RuntimeError, match="quarantined"):
            host.begin_checkpoint_recovery()
    finally:
        ledger.close()


def test_staged_restore_is_atomic_and_bad_plugin_roundtrip_never_publishes(tmp_path):
    from types import SimpleNamespace
    now, observed = [T], []
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        source = host_for(ledger, matcher_for(ledger), now)
        source.register(registration(ledger, "one", observed))
        now[0] = T
        source.process_event(book())
        assert observed == [("one", 1)]
        source._checkpoint_capture_guard(SimpleNamespace(hot_path=source.hot_path))
        state = source.capture_runtime_state(max_bytes=262144)
        assert state["jobs"][0]["lifecycle"]["callbacks"] == 1
        source_plugin = source._jobs["one"].instance
        ledger.close()
        ledger = None

        ledger = ledger_at(tmp_path)
        host = host_for(ledger, matcher_for(ledger), now)
        host.register(registration(ledger, "one", observed, bad_restore=True))
        host.begin_checkpoint_recovery()
        untouched = host._jobs["one"].instance
        with pytest.raises(ValueError, match="roundtrip differs"):
            host.restore_runtime_state(state, verify=lambda: True, max_bytes=262144)
        assert host._jobs["one"].instance is untouched
        assert untouched.state == {"count": 0}
        assert host.recovery_state == "RECOVERING"

        healthy = host_for(ledger, matcher_for(ledger), now)
        # Re-registering on the same durable ledger is idempotent for the same
        # registration evidence, so the checkpoint cut remains comparable.
        healthy.register(registration(ledger, "one", observed))
        healthy.begin_checkpoint_recovery()
        proof = healthy.restore_runtime_state(state, verify=lambda: True, max_bytes=262144)
        assert healthy.recovery_state == "RECOVERING"
        assert healthy._jobs["one"].instance.state == source_plugin.state == {"count": 1}
        assert healthy.publish_checkpoint_restore(healthy.hot_path, proof) is True
        assert healthy.recovery_state == "READY"
        now[0] = T+20
        assert healthy.process_event(book(T+20, sequence=2)) == ()
        assert healthy._jobs["one"].instance.state == {"count": 2}
        assert healthy.report()["restart_evidence"]["strategy_state_recovery"] == "VERIFIED_LOCAL_EXACT_CUT"
    finally:
        if ledger is not None:
            ledger.close()


def test_plugin_checksum_tamper_is_rejected_before_any_publication(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        observed, now = [], [T]
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        host = host_for(ledger, matcher_for(ledger), now)
        host.register(registration(ledger, "one", observed))
        state = host.capture_runtime_state(max_bytes=262144)
        damaged = json.loads(json.dumps(state))
        damaged["jobs"][0]["plugin_sha256"] = "0"*64
        host.begin_checkpoint_recovery()
        with pytest.raises(ValueError, match="checksum"):
            host.restore_runtime_state(damaged, verify=lambda: True, max_bytes=262144)
        assert host._jobs["one"].instance.state == {"count": 0}
    finally:
        ledger.close()


def test_journal_evidence_hashes_schema_rows_and_enforces_budgets(tmp_path):
    connection = sqlite3.connect(tmp_path/"journal.sqlite3", isolation_level=None)
    try:
        connection.execute("CREATE TABLE sample(id TEXT PRIMARY KEY,value TEXT)")
        connection.execute("INSERT INTO sample VALUES('one','alpha')")
        first = journal_evidence(connection, kind="paper", scope="s", mode="paper",
            path=str(tmp_path/"journal.sqlite3"), max_rows=10, max_bytes=10000, paper=True)
        assert first["rows"] == 1 and len(first["sha256"]) == 64
        connection.execute("UPDATE sample SET value='beta' WHERE id='one'")
        second = journal_evidence(connection, kind="paper", scope="s", mode="paper",
            path=str(tmp_path/"journal.sqlite3"), max_rows=10, max_bytes=10000, paper=True)
        assert second["sha256"] != first["sha256"]
        with pytest.raises(RuntimeCheckpointError, match="row budget"):
            journal_evidence(connection, kind="paper", scope="s", mode="paper",
                path=str(tmp_path/"journal.sqlite3"), max_rows=0, max_bytes=10000)
        with pytest.raises(RuntimeCheckpointError, match="byte budget"):
            journal_evidence(connection, kind="paper", scope="s", mode="paper",
                path=str(tmp_path/"journal.sqlite3"), max_rows=10, max_bytes=10)
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_missing_or_corrupt_newest_source_quarantines_every_consumer(tmp_path):
    class Consumer:
        def __init__(self):
            self.state = "IDLE"
        def quarantine_checkpoint_recovery(self, reason):
            self.state = "QUARANTINED"
    codec = type("Codec", (), {"max_bytes": 262144, "capacity": 1})()
    paper, etf = Consumer(), Consumer()
    checkpoint = RuntimeCheckpoint(tmp_path/"missing-runtime.json", codec,
        max_bytes=262144, max_journal_rows=100, max_journal_bytes=10000)
    with pytest.raises(Exception):
        await checkpoint.restore(object(), (), paper_runtime=paper, etf=etf,
            readiness=lambda *args: True)
    assert checkpoint.state == "QUARANTINED"
    assert paper.state == etf.state == "QUARANTINED"
    with pytest.raises(RuntimeCheckpointError, match="permanently"):
        await checkpoint.restore(object(), (), paper_runtime=paper, etf=etf,
            readiness=lambda *args: True)


def test_nested_account_row_change_breaks_journal_equality_at_same_row_count(tmp_path):
    connection = sqlite3.connect(tmp_path/"accounts.sqlite3", isolation_level=None)
    try:
        connection.execute("CREATE TABLE accounts(id TEXT PRIMARY KEY,version INTEGER,cash TEXT)")
        connection.executemany("INSERT INTO accounts VALUES(?,?,?)",
            [("acct-a", 7, "100"), ("acct-b", 7, "200")])
        before = journal_evidence(connection, kind="paper", scope="s", mode="paper",
            path=str(tmp_path/"accounts.sqlite3"), max_rows=10, max_bytes=10000, paper=True)
        connection.execute("UPDATE accounts SET cash='201' WHERE id='acct-b'")
        after = journal_evidence(connection, kind="paper", scope="s", mode="paper",
            path=str(tmp_path/"accounts.sqlite3"), max_rows=10, max_bytes=10000, paper=True)
        assert before["rows"] == after["rows"] == 2
        assert before["sha256"] != after["sha256"]
        with pytest.raises(RuntimeCheckpointError, match="journal"):
            _verify_evidence(before, after)
    finally:
        connection.close()


@pytest.mark.parametrize("restore_mode,match", [("missing", "requires a synchronous restore_state"),
                                                ("async", "must be synchronous")])
def test_plugin_restore_rejects_missing_or_async_hook_before_staging(
        tmp_path, restore_mode, match):
    now, observed = [T], []
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        source = host_for(ledger, matcher_for(ledger), now)
        source.register(registration(ledger, "one", observed, restore_mode=restore_mode))
        state = source.capture_runtime_state(max_bytes=262144)
        target = host_for(ledger, matcher_for(ledger), now)
        target.register(registration(ledger, "one", observed, restore_mode=restore_mode))
        untouched = target._jobs["one"].instance
        target.begin_checkpoint_recovery()
        with pytest.raises(ValueError, match=match):
            target.restore_runtime_state(state, verify=lambda: True, max_bytes=262144)
        assert target._jobs["one"].instance is untouched
        assert target.recovery_state == "RECOVERING"
    finally:
        ledger.close()


def test_plugin_restore_binds_full_product_identity_permission_and_saved_lifecycle(tmp_path):
    now, observed = [T], []
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        source = host_for(ledger, matcher_for(ledger), now)
        source.register(registration(ledger, "one", observed))
        source._jobs["one"].state, source._jobs["one"].reason = "PAUSED", "operator_pause"
        state = source.capture_runtime_state(max_bytes=262144)

        changed = host_for(ledger, matcher_for(ledger), now)
        changed.products = type(changed.products)({BTC.key: replace(
            spec(), maker_fee_rate=D(".003"))})
        changed.register(registration(ledger, "one", observed))
        changed.begin_checkpoint_recovery()
        with pytest.raises(ValueError, match="identity mismatch"):
            changed.restore_runtime_state(state, verify=lambda: True, max_bytes=262144)
        assert changed.recovery_state == "RECOVERING"

        target = host_for(ledger, matcher_for(ledger), now)
        target.register(registration(ledger, "one", observed))
        target._jobs["one"].permission_blocked = True
        target.begin_checkpoint_recovery()
        target.restore_runtime_state(state, verify=lambda: True, max_bytes=262144)
        assert target._jobs["one"].state == "BLOCKED"
        assert target._jobs["one"].reason == "paper_permission_missing"
        assert target.recovery_state == "RECOVERING"
    finally:
        ledger.close()


@pytest.mark.parametrize("state", ["PREPARED", "HANDOFF", "UNKNOWN"])
def test_etf_unresolved_dispatch_state_quarantines_before_any_callback(tmp_path, state):
    from vibetrading.etf_journal import EtfJournal
    journal = EtfJournal(tmp_path/"etf.sqlite")
    try:
        journal.initialize(D(90000), "hashed-account")
        journal.db.execute("INSERT INTO metadata VALUES(?,?)",
                           ("mock2_account_identity", "hashed-account-2"))
        journal.db.execute("INSERT INTO dispatch_attempts"
            "(intent_id,evidence,state,prepared_ns,handoff_ns,updated_ns) VALUES(?,?,?,?,?,?)",
            ("intent-one", "{}", state, 1, 2, 3))
        etf = SimpleNamespace(journal=journal, _day="")
        with pytest.raises(RuntimeCheckpointError, match="unresolved handoff"):
            _etf_evidence(etf, 100, 100000, require_day=False)
    finally:
        journal.db.close()


def test_etf_malformed_pending_intent_is_rejected_even_with_pending_status(tmp_path):
    from vibetrading.etf_journal import EtfJournal
    journal = EtfJournal(tmp_path/"etf.sqlite")
    try:
        journal.initialize(D(90000), "hashed-account")
        journal.db.execute("INSERT INTO metadata VALUES(?,?)",
                           ("mock2_account_identity", "hashed-account-2"))
        journal.db.execute("INSERT INTO orders(intent_id,strategy,symbol,side,quantity,status,payload)"
                           " VALUES(?,?,?,?,?,?,?)",
                           ("bad-intent", "C3", "TEST", "buy", 1, "pending", "not-json"))
        etf = SimpleNamespace(journal=journal, _day="")
        with pytest.raises(RuntimeCheckpointError, match="malformed ETF pending"):
            _etf_evidence(etf, 100, 100000, require_day=False)
    finally:
        journal.db.close()


@pytest.mark.asyncio
async def test_async_save_is_single_flight_and_shielded_from_caller_cancellation(
        tmp_path, monkeypatch):
    import vibetrading.runtime_checkpoint as module_under_test
    payload = valid_payload()
    codec = type("Codec", (), {"max_bytes": 262144, "capacity": 1})()
    checkpoint = RuntimeCheckpoint(tmp_path/"runtime.json", codec,
        max_bytes=262144, max_journal_rows=100, max_journal_bytes=10000)
    monkeypatch.setattr(checkpoint, "_capture_sync", lambda *args: payload)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocked_writer(path, data, limit):
        entered.set()
        assert release.wait(10), "test failed to release the blocked writer"
        path.write_bytes(data)
        finished.set()

    monkeypatch.setattr(module_under_test, "_write_checkpoint_file", blocked_writer)
    task = asyncio.create_task(checkpoint.save(object()))
    assert await asyncio.to_thread(entered.wait, 10)
    with pytest.raises(RuntimeCheckpointError, match="pending"):
        await checkpoint.save(object())
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(finished.wait, 10)
    assert (tmp_path/"runtime.json").read_bytes() == payload


def test_atomic_writer_failure_preserves_previous_bundle_and_removes_temporary(
        tmp_path, monkeypatch):
    import vibetrading.market_checkpoint as market
    path = tmp_path/"market.json"
    path.write_bytes(b"previous")
    temporary = []

    def fail_replace(source, target):
        temporary.append(source)
        raise OSError("injected replace failure")

    monkeypatch.setattr(market.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        market._write_checkpoint_file(path, b"replacement", 1024)
    assert path.read_bytes() == b"previous"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["market.json"]
    from pathlib import Path
    assert temporary and not Path(temporary[0]).exists()
