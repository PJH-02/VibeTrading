"""Latest-state read views and local dashboard wiring, no external services."""
import asyncio
from dataclasses import asdict, replace
from decimal import Decimal
import json
import threading
import time
from types import SimpleNamespace

import pytest

from vibetrading.hot_path import HotPathStore
from vibetrading.local_snapshot import (
    LocalSnapshotWriter, SnapshotBudgets, SnapshotError, atomic_write_snapshot,
    capture_market_snapshot, local_snapshot_config, read_market_snapshot,
)
from core.models import AssetClass, BookUpdateType, DataGapEvent, InstrumentId, OrderBookEvent, OrderBookLevel, TradeEvent, Venue


def budgets(**updates):
    return replace(SnapshotBudgets(65536, 16, 2, .01, 1.0), **updates)


def engine(*, now=None):
    now = time.time_ns() if now is None else now
    instrument = InstrumentId(venue=Venue.KIWOOM, asset_class=AssetClass.ETF,
                              raw_symbol="069500", std_symbol="069500", currency="KRW")
    common = dict(instrument=instrument, venue=Venue.KIWOOM, provider="kiwoom",
                  receive_ts_ns=now, wallclock_ts_ns=now)
    hot = HotPathStore()
    hot.apply(OrderBookEvent(**common, bids=[OrderBookLevel(price=str(100-index), quantity="2.000000000000000001") for index in range(3)],
        asks=[OrderBookLevel(price=str(101+index), quantity="3") for index in range(3)], depth=3))
    hot.apply(TradeEvent(**common, price="100.000000000000000001", quantity="1.000000000000000001", trade_id="fixture-trade"))
    health = {"closed": False, "feeds": {"fixture": {"provider": "kiwoom", "mode": "LIVE",
        "reason": "verified", "generation": 1, "applied_sequence": 2}}}
    return SimpleNamespace(hot_path=hot, stats=health), common, now


def write_document(path, document):
    atomic_write_snapshot(path, json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode())


def test_unrelated_feed_failure_preserves_valid_market_projection(tmp_path):
    owner, _, now = engine()
    owner.stats["feeds"]["unrelated-crypto"] = {
        "provider": "arcus-perp", "mode": "INVALID", "reason": "transport_gap",
        "generation": 2, "applied_sequence": 1,
    }
    document = capture_market_snapshot(owner, budgets(), now_ns=now)
    assert document["engine"]["healthy"] is False
    assert document["markets"][0]["book_valid"] is True
    path = tmp_path / "partial-health.json"
    write_document(path, document)
    assert read_market_snapshot(path, budgets(), now_ns=now)["markets"][0]["book_valid"] is True


def test_snapshot_reuses_existing_book_and_preserves_dashboard_row_schema(tmp_path, monkeypatch):
    owner, common, now = engine()
    def forbidden(*args, **kwargs):
        raise AssertionError("read views must not reconstruct or apply another book")
    monkeypatch.setattr(owner.hot_path, "apply_versioned", forbidden)
    document = capture_market_snapshot(owner, budgets(), now_ns=now)
    path = tmp_path / "market.json"
    write_document(path, document)
    before = path.read_bytes()
    loaded = read_market_snapshot(path, budgets(), now_ns=now)
    assert path.read_bytes() == before
    assert loaded["engine"]["apply_seq"] == 2 and loaded["engine"]["healthy"]
    row = loaded["markets"][0]
    assert row["key"] == common["instrument"].key and row["section"] == "stocks"
    assert row["price"] == "100.000000000000000001"
    assert row["book_valid"] and row["book"]["bids"] == [["100", "2.000000000000000001"], ["99", "2.000000000000000001"]]
    assert row["book"]["source_ms"] is None
    assert row["trades"][0]["source_ms"] is None and len(row["trades"]) == 1


def test_existing_recent_40_trade_ring_is_projected_without_reapplication(tmp_path, monkeypatch):
    owner, common, now = engine()
    for index in range(100):
        owner.hot_path.apply(TradeEvent(**common, price="100.000000000000000001",
            quantity="1.000000000000000001", trade_id="tail-"+str(index)))
    expected = owner.hot_path.recent_trades(common["instrument"].key, limit=40)
    before = owner.hot_path.apply_seq
    def forbidden(*args, **kwargs):
        raise AssertionError("snapshot must only read the existing ring")
    monkeypatch.setattr(owner.hot_path, "apply_versioned", forbidden)
    document = capture_market_snapshot(owner, budgets(), now_ns=now)
    path = tmp_path / "market.json"
    write_document(path, document)
    row = read_market_snapshot(path, budgets(), now_ns=now)["markets"][0]
    assert len(row["trades"]) == 40
    assert [item["id"] for item in row["trades"]] == [item.event_id for item in expected]
    assert expected[0].trade_id == "tail-99" and expected[-1].trade_id == "tail-60"
    assert owner.hot_path.apply_seq == before


@pytest.mark.parametrize("failure", ["engine", "gap", "old_book"])
def test_invalid_or_stale_books_are_never_presented_as_valid(failure):
    owner, common, now = engine()
    if failure == "engine":
        owner.stats["feeds"]["fixture"]["mode"] = "INVALID"
    elif failure == "gap":
        owner.hot_path.apply(DataGapEvent(**common, reason="disconnected"))
    else:
        now += 2_000_000_000
    document = capture_market_snapshot(owner, budgets(), now_ns=now)
    row = document["markets"][0]
    assert row["book"] is None and row["book_valid"] is False and row["gap_reason"]


def test_stale_oversized_ambiguous_or_contradictory_files_fail_closed(tmp_path):
    owner, _, now = engine()
    original = capture_market_snapshot(owner, budgets(), now_ns=now)
    path = tmp_path / "market.json"
    write_document(path, original)
    with pytest.raises(SnapshotError, match="stale"):
        read_market_snapshot(path, budgets(), now_ns=now+1_000_000_001)
    path.write_bytes(b"x" * (budgets().max_bytes+1))
    with pytest.raises(SnapshotError, match="byte_budget"):
        read_market_snapshot(path, budgets(), now_ns=now)
    path.write_bytes(b'{"schema_version":1,"schema_version":1}')
    with pytest.raises(SnapshotError, match="duplicate"):
        read_market_snapshot(path, budgets(), now_ns=now)
    bad = json.loads(json.dumps(original))
    bad["engine"]["healthy"] = False
    write_document(path, bad)
    with pytest.raises(SnapshotError, match="invalid_book"):
        read_market_snapshot(path, budgets(), now_ns=now)


def test_snapshot_bounds_and_explicit_configuration(tmp_path):
    owner, common, now = engine()
    with pytest.raises(SnapshotError, match="byte_budget"):
        capture_market_snapshot(owner, budgets(max_bytes=100), now_ns=now)
    other = common["instrument"].model_copy(update={"raw_symbol": "229200", "std_symbol": "229200"})
    owner.hot_path.apply(TradeEvent(**dict(common, instrument=other), price="50", quantity="1"))
    with pytest.raises(SnapshotError, match="instrument_budget"):
        capture_market_snapshot(owner, budgets(max_instruments=1), now_ns=now)
    with pytest.raises(ValueError):
        local_snapshot_config({})
    with pytest.raises(ValueError):
        local_snapshot_config({"LOCAL_MARKET_SNAPSHOT_FILE": "relative.json", "LOCAL_SNAPSHOT_BUDGETS": json.dumps(asdict(budgets()))})
    path, config = local_snapshot_config({"LOCAL_MARKET_SNAPSHOT_FILE": str(tmp_path / "market.json"),
                                         "LOCAL_SNAPSHOT_BUDGETS": json.dumps(asdict(budgets()))})
    assert path.is_absolute() and config == budgets()
    with pytest.raises(ValueError):
        budgets(max_instruments=513)


def test_empty_prepared_history_does_not_break_latest_market_snapshot():
    owner, _, now = engine()
    owner.hot_path.history.register("fixture-empty-history")
    assert len(capture_market_snapshot(owner, budgets(), now_ns=now)["markets"]) == 1


def test_failed_atomic_replace_preserves_previous_file_and_cleans_only_owned_temp(tmp_path, monkeypatch):
    import vibetrading.local_snapshot as module
    path = tmp_path / "market.json"
    path.write_bytes(b"old")
    def fail(*args):
        raise OSError("fixture replacement failed")
    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError):
        atomic_write_snapshot(path, b"new")
    assert path.read_bytes() == b"old" and list(tmp_path.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_writer_has_one_io_inflight_and_close_keeps_late_cleanup_tracked(tmp_path, monkeypatch):
    import vibetrading.local_snapshot as module
    owner, _, _ = engine()
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = module.atomic_write_snapshot
    def slow(path, raw):
        calls.append(True)
        entered.set()
        release.wait(5)
        original(path, raw)
    monkeypatch.setattr(module, "atomic_write_snapshot", slow)
    writer = LocalSnapshotWriter(owner, tmp_path / "market.json", budgets())
    await writer.start()
    try:
        for _ in range(200):
            if entered.is_set():
                break
            await asyncio.sleep(.001)
        assert entered.is_set()
        await asyncio.sleep(.04)
        assert len(calls) == 1
        with pytest.raises(SnapshotError, match="cleanup_pending"):
            await writer.close(timeout_seconds=.01)
        assert not writer._task.done()
    finally:
        release.set()
        await writer.close(timeout_seconds=2)
    assert len(calls) == writer.written == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [False, True])
async def test_local_dashboard_uses_snapshot_with_zero_nats_or_duplicate_collectors(tmp_path, monkeypatch, stale):
    import vibetrading.dashboard.bridge as bridge
    owner, _, now = engine()
    path = tmp_path / "market.json"
    document = capture_market_snapshot(owner, budgets(), now_ns=now)
    if stale:
        document["generated_ns"] -= 2_000_000_000
    write_document(path, document)
    env = {"VIBETRADING_RUNTIME": "local", "LOCAL_MARKET_SNAPSHOT_FILE": str(path),
        "LOCAL_SNAPSHOT_BUDGETS": json.dumps(asdict(budgets())), "DASHBOARD_SITE_URL": "https://fixture.invalid",
        "DASHBOARD_INGEST_TOKEN": "fixture", "DASHBOARD_SITE_BYPASS_TOKEN": "fixture",
        "DASHBOARD_DATA_ROOT": str(tmp_path / "data"), "DASHBOARD_MONITOR_ROOT": str(tmp_path / "monitor"),
        "DASHBOARD_CRYPTO_VENUES": "bybit,binance"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    forbidden_calls = []
    async def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("local dashboard must not connect NATS or collect another public feed")
    monkeypatch.setattr("nats.connect", forbidden)
    monkeypatch.setattr(bridge, "observe_public", forbidden)
    monkeypatch.setattr(bridge, "bybit_account", forbidden)
    monkeypatch.setattr(bridge, "MarketProjection", lambda: (_ for _ in ()).throw(AssertionError("no duplicate market state")))
    monkeypatch.setattr(bridge, "public_key", lambda path: {"kty": "fixture"})
    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda *args: None)
    stop, sent = asyncio.Event(), []
    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def json(self): return {"accounts": []}
    class Session:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def post(self, url, *, json, **kwargs):
            sent.append(json)
            stop.set()
            return Response()
    monkeypatch.setattr(bridge.aiohttp, "ClientSession", Session)
    await bridge.run(stop=stop)
    assert len(sent) == 1 and sent[0]["schema_version"] == 1
    assert forbidden_calls == []
    assert sent[0]["nats_connected"] is False
    assert sent[0]["markets"] == ([] if stale else document["markets"])
    assert any(item["type"] == "StrategyTelemetryNotConfigured" for item in sent[0]["errors"])
    assert bool([item for item in sent[0]["errors"] if item["source"] == "local-snapshot"]) is stale
