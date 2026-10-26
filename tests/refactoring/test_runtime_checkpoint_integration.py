"""Real local-engine coupled checkpoint: exact market and Paper plugin restore."""
import asyncio
import hashlib
from decimal import Decimal as D
from types import ModuleType, SimpleNamespace

import pytest

from vibetrading.execution_ledger import AccountScope
from feeds.feed_recovery import EngineCursor, RecoveryFrame
from market.hot_path import HotPathStore
from market.local_engine import EngineBudgets, LocalMarketEngine
from feeds.local_pipeline import PipelineBudgets
from storage.market_checkpoint import CheckpointCodec
from storage.market_spool import MarketSpool
from core.models import AssetClass, InstrumentId, OrderBookEvent, OrderBookLevel, OrderDestination, OrderIntent, Side, Signal, SignalAction, TradeEvent, Venue
from vibetrading.paper_execution import PaperExecution
from vibetrading.portfolio_ledger import PortfolioLedger
from vibetrading.runtime_checkpoint import RuntimeCheckpoint, RuntimeCheckpointError
from vibetrading.strategy_contract import StrategyModule
from vibetrading.strategy_runtime import (PaperStrategyRuntime, RuntimeBudgets,
                                          StrategyRegistration, StrategyRisk)
from vibetrading.trading_contracts import (ActionKind, ExecutionAction, ProductKind,
                                           ProductSpec, TradingMode)

T = 1_800_000_000_000_000_000
BTC = InstrumentId(venue=Venue.BYBIT, asset_class=AssetClass.CRYPTO_PERP,
                   raw_symbol="BTCUSDT", std_symbol="BTCUSDT", currency="USDT")
LIMITS = dict(max_disk_bytes=1_048_576, max_records=128, max_frame_bytes=4096,
    max_header_bytes=256, segment_max_bytes=65536, segment_max_records=64,
    segment_max_age_seconds=60)


def producer(root):
    return SimpleNamespace(producer="btc-source", provider="scripted", incarnation="boot",
        spool_root=root/"btc", limits=SimpleNamespace(reader_kwargs={key: LIMITS[key] for key in
            ("max_disk_bytes", "max_records", "max_frame_bytes", "max_header_bytes")}),
        pipeline=PipelineBudgets(16, 65536, 4096, 16, 1))


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


def module(sid):
    source = ModuleType("checkpoint_"+sid)
    class Dummy:
        def __init__(self):
            self.state = {"count": 0}
        def checkpoint_state(self):
            return {"count": self.state["count"]}
        def restore_state(self, state):
            self.state = {"count": state["count"]}
        def on_event(self, event, data):
            self.state["count"] += 1
            return [Signal(strategy_id=sid, instrument=event.instrument,
                action=SignalAction.ENTER_LONG, event_ts_ns=event.receive_ts_ns,
                reference_price=D(100), valid_until_ts_ns=event.receive_ts_ns+100_000,
                source_event_id=event.event_id)]
    source.create_strategy = Dummy
    return StrategyModule(source, sid, [{"instrument_key": BTC.key, "event_type": "order_book"}],
                          {"bars": 0})


def registration(ledger, sid, quantity=D(1)):
    def actions(signal, view, config):
        identity = sid+"-"+signal.signal_id
        intent = OrderIntent(intent_id=identity, job_id=sid, signal_id=signal.signal_id,
            destination=OrderDestination.REAL, instrument=BTC, side=Side.BUY,
            created_ts_ns=signal.event_ts_ns, client_order_id="client-"+identity,
            quantity=quantity, limit_price=D(99))
        return [ExecutionAction("action-"+identity, ActionKind.NEW, ledger.scope, sid,
            "config-1", TradingMode.PAPER, signal.valid_until_ts_ns, intent=intent,
            post_only=True)]
    return StrategyRegistration(sid, module(sid), "strategy-1", "config-1",
        {"quantity": "1"}, {"research_id": "synthetic-fixture"}, True, (BTC.key,), 0,
        (ledger.scope.key,), {ledger.scope.key: StrategyRisk(D(0), D(100000), D(100000),
            D(100000), D(100000), D(".1"))}, actions)


def book(now=T, sequence=1):
    return OrderBookEvent(instrument=BTC, venue=BTC.venue, provider="scripted",
        event_id="book-"+str(now), receive_ts_ns=now, wallclock_ts_ns=now,
        source_ts_ns=now, sequence=sequence, final_update_id=sequence,
        sync_generation=1, depth=1, bids=[OrderBookLevel(price=D(99), quantity=D(5))],
        asks=[OrderBookLevel(price=D(101), quantity=D(5))])


def trade(now, sequence, quantity=D("0.25"), price=D(98)):
    return TradeEvent(instrument=BTC, venue=BTC.venue, provider="scripted",
        event_id="trade-"+str(sequence), receive_ts_ns=now, wallclock_ts_ns=now,
        source_ts_ns=now, sequence=sequence, price=price, quantity=quantity,
        aggressor_side=Side.SELL)


def append(item, payload):
    spool = MarketSpool(item.spool_root, item.producer, item.incarnation, **LIMITS)
    try:
        receipt = spool.append(payload)
        spool.flush()
        return receipt
    finally:
        spool.close()


def codec(source):
    return CheckpointCodec(max_bytes=262144, max_event_bytes=8192, max_producers=4,
        config_sha256=hashlib.sha256(b"runtime-fixture").hexdigest(),
        capacity=source.hot_path.checkpoint_capacity())


async def run_branch(root, *, restore, partial=False):
    now = [T]
    item = producer(root)
    payload1 = book().model_dump_json().encode()
    first = append(item, payload1)
    ledger = ledger_at(root)
    source_engine = None
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher = matcher_for(ledger)
        host = PaperStrategyRuntime(hot_path=HotPathStore(), accounts={ledger.scope.key: matcher},
            products={BTC.key: spec()}, known_instruments={BTC.key: BTC},
            reference_feeds={}, budgets=budgets(), settings={"fixture": "coupled"},
            mode=TradingMode.PAPER, phase="paper", now_ns=lambda: now[0])
        order_quantity = D(2) if partial else D(1)
        host.register(registration(ledger, "one", quantity=order_quantity))
        source_engine = LocalMarketEngine(producer_providers={item.producer: item.provider},
            budgets=EngineBudgets(4, 16, 4096, 16, 65536, 60_000_000_000, 32, 2,
                60_000_000_000, .01), on_invalidate=lambda *args: None,
            hot_path=host.hot_path, post_apply_callbacks=(host.process_applied,),
            snapshot_clock=lambda: now[0], clock=lambda: 100,
            fresh_starts={item.producer: item.incarnation}, verified_fresh_start=True)
        source_engine.on_frame(RecoveryFrame(EngineCursor(item.producer, item.incarnation,
            first.cursor.sequence), payload1))
        assert host.hot_path.apply_seq == 1 and host.report()["strategies"]["one"]["callbacks"] == 1
        if partial:
            matcher.advance(T+10)
            open_orders = matcher.report()["open_orders"]
            assert len(open_orders) == 1
            identity = next(iter(open_orders))
            assert matcher.on_trade(trade(T+10, 1, quantity=D(1)))
            row = ledger.get(identity)
            assert row["state"] == "OPEN"
            assert D(row["filled"]) == D(1)
            assert D(row["reservation"]) > 0
            assert D(matcher.report()["open_orders"][identity]["leaves"]) == D(1)
        checkpoint = RuntimeCheckpoint(root/"runtime-checkpoint.json", codec(source_engine),
            max_bytes=262144, max_journal_rows=10000, max_journal_bytes=4_000_000)
        await checkpoint.save(source_engine, paper_runtime=host)
        if not restore:
            now[0] = T+20
            second = book(T+20, sequence=2).model_dump_json().encode()
            source_engine.on_frame(RecoveryFrame(EngineCursor(item.producer, item.incarnation, 2), second))
            expected = ledger.report()
            expected_state = dict(host._jobs["one"].instance.state)
            await source_engine.close()
            source_engine = None
            ledger.close()
            ledger = None
            return expected, expected_state
        await source_engine.close()
        source_engine = None
        ledger.close()
        ledger = ledger_at(root)
        matcher = matcher_for(ledger)
        host = PaperStrategyRuntime(hot_path=HotPathStore(), accounts={ledger.scope.key: matcher},
            products={BTC.key: spec()}, known_instruments={BTC.key: BTC},
            reference_feeds={}, budgets=budgets(), settings={"fixture": "coupled"},
            mode=TradingMode.PAPER, phase="paper", now_ns=lambda: now[0])
        host.register(registration(ledger, "one", quantity=order_quantity))
        target = LocalMarketEngine(producer_providers={item.producer: item.provider},
            budgets=EngineBudgets(4, 16, 4096, 16, 65536, 60_000_000_000, 32, 2,
                60_000_000_000, .01), on_invalidate=lambda *args: None,
            hot_path=host.hot_path, snapshot_clock=lambda: now[0], clock=lambda: 100)
        await checkpoint.restore(target, (item,), paper_runtime=host,
            readiness=lambda engine, snapshot, receipts: snapshot.phase == "REPLAY")
        assert checkpoint.state == "READY" and host.recovery_state == "READY"
        assert target.post_apply_callbacks == (host.process_applied,)
        # A verified restore is a valid new causal cut for a later quiescent
        # save, but the same manager instance can never restore twice.
        await checkpoint.save(target, paper_runtime=host)
        with pytest.raises(RuntimeCheckpointError, match="already"):
            await checkpoint.restore(target, (item,), paper_runtime=host,
                readiness=lambda engine, snapshot, receipts: True)
        now[0] = T+20
        second = book(T+20, sequence=2).model_dump_json().encode()
        target.on_frame(RecoveryFrame(EngineCursor(item.producer, item.incarnation, 2), second))
        actual = ledger.report()
        actual_state = dict(host._jobs["one"].instance.state)
        await target.close()
        ledger.close()
        ledger = None
        return actual, actual_state
    finally:
        if source_engine is not None:
            await source_engine.close()
        if ledger is not None:
            ledger.close()


async def save_exact_cut(root):
    item = producer(root)
    payload = book().model_dump_json().encode()
    append(item, payload)
    ledger = ledger_at(root)
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher = matcher_for(ledger)
        host = PaperStrategyRuntime(hot_path=HotPathStore(),
            accounts={ledger.scope.key: matcher}, products={BTC.key: spec()},
            known_instruments={BTC.key: BTC}, reference_feeds={}, budgets=budgets(),
            settings={"fixture": "coupled"}, mode=TradingMode.PAPER, phase="paper",
            now_ns=lambda: T)
        host.register(registration(ledger, "one"))
        engine = LocalMarketEngine(producer_providers={item.producer: item.provider},
            budgets=EngineBudgets(4, 16, 4096, 16, 65536, 60_000_000_000, 32, 2,
                60_000_000_000, .01), on_invalidate=lambda *args: None,
            hot_path=host.hot_path, post_apply_callbacks=(host.process_applied,),
            snapshot_clock=lambda: T, clock=lambda: 100,
            fresh_starts={item.producer: item.incarnation}, verified_fresh_start=True)
        engine.on_frame(RecoveryFrame(EngineCursor(item.producer, item.incarnation, 1),
                                      payload))
        checkpoint = RuntimeCheckpoint(root/"runtime-checkpoint.json", codec(engine),
            max_bytes=262144, max_journal_rows=10000, max_journal_bytes=4_000_000)
        await checkpoint.save(engine, paper_runtime=host)
        await engine.close()
        return item, checkpoint
    finally:
        ledger.close()


def target_runtime(root, item):
    ledger = ledger_at(root)
    matcher = matcher_for(ledger)
    host = PaperStrategyRuntime(hot_path=HotPathStore(), accounts={ledger.scope.key: matcher},
        products={BTC.key: spec()}, known_instruments={BTC.key: BTC}, reference_feeds={},
        budgets=budgets(), settings={"fixture": "coupled"}, mode=TradingMode.PAPER,
        phase="paper", now_ns=lambda: T)
    host.register(registration(ledger, "one"))
    engine = LocalMarketEngine(producer_providers={item.producer: item.provider},
        budgets=EngineBudgets(4, 16, 4096, 16, 65536, 60_000_000_000, 32, 2,
            60_000_000_000, .01), on_invalidate=lambda *args: None,
        hot_path=host.hot_path, snapshot_clock=lambda: T, clock=lambda: 100)
    return SimpleNamespace(ledger=ledger, host=host, engine=engine)


@pytest.mark.asyncio
async def test_market_suffix_beyond_c_quarantines_selected_consumer_without_callback(tmp_path):
    item, checkpoint = await save_exact_cut(tmp_path)
    second = book(T+20, sequence=2).model_dump_json().encode()
    append(item, second)
    target = target_runtime(tmp_path, item)
    before = target.ledger.report()["orders"]
    try:
        with pytest.raises((ValueError, RuntimeError), match="suffix"):
            await checkpoint.restore(target.engine, (item,), paper_runtime=target.host,
                readiness=lambda *args: True)
        assert checkpoint.state == "QUARANTINED"
        assert target.host.recovery_state == "QUARANTINED"
        assert target.engine.post_apply_callbacks == ()
        assert target.ledger.report()["orders"] == before
    finally:
        await target.engine.close()
        target.ledger.close()


@pytest.mark.asyncio
async def test_corrupt_newest_real_bundle_fences_before_callback_publication(tmp_path):
    item, checkpoint = await save_exact_cut(tmp_path)
    checkpoint.path.write_bytes(b'{"torn":')
    target = target_runtime(tmp_path, item)
    before = target.ledger.report()["orders"]
    try:
        with pytest.raises((ValueError, RuntimeError)):
            await checkpoint.restore(target.engine, (item,), paper_runtime=target.host,
                readiness=lambda *args: True)
        assert checkpoint.state == "QUARANTINED"
        assert target.host.recovery_state == "QUARANTINED"
        assert target.engine.post_apply_callbacks == ()
        assert target.ledger.report()["orders"] == before
    finally:
        await target.engine.close()
        target.ledger.close()


@pytest.mark.asyncio
async def test_run_engine_requires_readiness_before_inspecting_runtime_fields(tmp_path):
    from vibetrading.local_runtime import run_engine
    codec = type("Codec", (), {"max_bytes": 262144, "capacity": 1})()
    checkpoint = RuntimeCheckpoint(tmp_path/"runtime.json", codec, max_bytes=262144,
        max_journal_rows=100, max_journal_bytes=10000)
    with pytest.raises(ValueError, match="(?i)readiness"):
        await run_engine(SimpleNamespace(etf_enabled=False),
                         SimpleNamespace(state_dir=tmp_path, env={}),
                         runtime_checkpoint=checkpoint)


@pytest.mark.asyncio
async def test_restored_runtime_matches_uninterrupted_next_event_and_has_no_duplicate(tmp_path):
    live_root, restored_root = tmp_path/"live", tmp_path/"restored"
    live_root.mkdir(); restored_root.mkdir()
    expected, expected_plugin = await run_branch(live_root, restore=False)
    actual, actual_plugin = await run_branch(restored_root, restore=True)
    assert actual_plugin == expected_plugin == {"count": 2}
    # Paths/timestamps differ per root; the durable economic facts and dedup set
    # must be byte-for-byte equivalent apart from those root-local fields.
    def economic(report):
        return {
            "orders": sorted(({**{key: row[key] for key in
                ("intent_id", "strategy", "quantity", "filled", "reservation")},
                "state": ("@UNRESOLVED_ACTIVE" if row["state"] in
                          {"OPEN", "CANCEL_PENDING", "UNKNOWN"} else row["state"])}
                for row in report["orders"]), key=lambda row: row["intent_id"]),
            "virtual_positions": report["virtual_positions"],
            "physical_positions": report["physical_positions"],
            "account_version": report["account_version"],
        }
    assert economic(actual) == economic(expected)
    assert len(actual["orders"]) == 2
    assert actual["readiness"]["ready"] is True


@pytest.mark.asyncio
async def test_partial_fill_reservation_and_reopened_next_event_are_economic_parity(tmp_path):
    live_root, restored_root = tmp_path/"live", tmp_path/"restored"
    live_root.mkdir(); restored_root.mkdir()
    expected, expected_plugin = await run_branch(live_root, restore=False, partial=True)
    actual, actual_plugin = await run_branch(restored_root, restore=True, partial=True)
    assert actual_plugin == expected_plugin == {"count": 2}

    def economic(report):
        return {
            "orders": sorted(({**{key: row[key] for key in
                ("intent_id", "strategy", "quantity", "filled", "reservation")},
                "state": ("@UNRESOLVED_ACTIVE" if row["state"] in
                          {"OPEN", "CANCEL_PENDING", "UNKNOWN"} else row["state"])}
                for row in report["orders"]), key=lambda row: row["intent_id"]),
            "reserved": report["reserved"],
            "balances": report["balances"],
            "strategy_cash": report["strategy_cash"],
            "virtual_positions": report["virtual_positions"],
            "physical_positions": report["physical_positions"],
            "account_version": report["account_version"],
        }

    assert economic(actual) == economic(expected)
    assert any(D(row["filled"]) == D(1) and D(row["reservation"]) > 0
               for row in actual["orders"])
    assert actual["readiness"]["ready"] is True
