"""Read-only delivery/provenance helpers used by the VN09 planner."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.pop("VIBETRADING_DATA_ROOT", None)
os.environ.pop("DELIVERY_LEDGER_FORMAT", None)

from test_local_storage import budgets, gap, observation, trade, wire  # noqa: E402

from storage.local_storage import LocalSpoolStorage, LocalStorageError  # noqa: E402
from storage.market_spool import MarketSpool, SpoolCursor, SpoolRecord, read_spool_records
from runtime.settings import RuntimeSettings  # noqa: E402
from storage.sqlite_store import RuntimeStore  # noqa: E402


PRODUCER = "collector"
INCARNATION = "inc-A"
SPOOL = dict(max_disk_bytes=1024 * 1024, max_records=100, max_frame_bytes=16384,
             max_header_bytes=512, segment_max_bytes=32768, segment_max_records=1,
             segment_max_age_seconds=60)
READ = {key: value for key, value in SPOOL.items() if not key.startswith("segment_")}


def _build(tmp_path, payloads, *, producer=PRODUCER, observation_mode=False,
           ledger="compact"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n"
                   f"DELIVERY_LEDGER_FORMAT={ledger}\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env)
    root = tmp_path / "spool"
    spool = MarketSpool(root, producer, INCARNATION, **SPOOL)
    try:
        for raw in payloads:
            spool.append(raw)
            spool.seal()
    finally:
        spool.close()
    store = RuntimeStore(settings)
    store.collection_generation()
    source = SimpleNamespace(producer=producer, incarnation=INCARNATION,
                             spool_root=root,
                             limits=SimpleNamespace(reader_kwargs=READ))
    plan = SimpleNamespace(
        producers=() if observation_mode else (source,),
        observation=source if observation_mode else None,
        storage=budgets().__dict__)
    worker = LocalSpoolStorage(plan, settings, budgets(), store=store)
    for _ in range(50):
        if worker.step()["records"] == 0:
            break
    return SimpleNamespace(settings=settings, store=store, worker=worker,
                           root=root, producer=producer)


def _records(bundle):
    return tuple(read_spool_records(
        bundle.root, producer_id=bundle.producer, incarnation=INCARNATION,
        after=None, durable_only=True, **READ))


@pytest.fixture(params=("legacy", "compact"))
def market(tmp_path, request):
    bundle = _build(tmp_path, [wire(trade(1)), wire(trade(2))],
                    ledger=request.param)
    try:
        yield bundle
    finally:
        bundle.worker.close()
        bundle.store.close()


@pytest.fixture(params=("legacy", "compact"))
def mixed(tmp_path, request):
    bundle = _build(tmp_path, [wire(trade(1)), wire(gap(2)), wire(trade(3))],
                    ledger=request.param)
    try:
        yield bundle
    finally:
        bundle.worker.close()
        bundle.store.close()


def test_delivery_manifest_resolves_exact_delivery_only(market):
    records = _records(market)
    first = records[0]
    event = trade(1)
    manifest = market.store.delivery_manifest(
        "LOCAL:" + market.producer, INCARNATION, first.cursor.sequence, event.event_id)
    assert manifest is not None
    assert manifest["file_id"]
    assert market.store.local_manifest(Path(manifest["path"]))["file_id"] == manifest["file_id"]
    # Event-ID existence alone is never enough; it must be the exact mapping.
    assert market.store.delivery_manifest(
        "LOCAL:" + market.producer, INCARNATION,
        first.cursor.sequence, trade(99).event_id) is None
    assert market.store.delivery_manifest(
        "LOCAL:" + market.producer, INCARNATION, 99, event.event_id) is None
    assert market.store.delivery_manifest(
        "LOCAL:" + market.producer, "other-generation",
        first.cursor.sequence, event.event_id) is None
    # The result is a detached row, not shared catalogue state.
    manifest["upload_status"] = "tampered"
    fresh = market.store.delivery_manifest(
        "LOCAL:" + market.producer, INCARNATION, first.cursor.sequence, event.event_id)
    assert fresh["upload_status"] != "tampered"
    with pytest.raises(ValueError):
        market.store.delivery_manifest("", INCARNATION, 1, event.event_id)
    with pytest.raises(ValueError):
        market.store.delivery_manifest("LOCAL:" + market.producer, INCARNATION, True,
                                       event.event_id)


def test_spool_gc_evidence_market_and_gap_bind_exact_rows(mixed):
    records = _records(mixed)
    facts_before = mixed.worker._ledger.connection.execute(
        "SELECT * FROM local_fact ORDER BY fact_id").fetchall()
    manifests_before = mixed.store.list_manifest()
    evidence = mixed.worker.spool_gc_evidence(mixed.producer, records)
    assert evidence["progress"]["sequence"] == 3
    assert evidence["progress"]["receipt_kind"] == "market"
    assert len(evidence["facts_generation"]) == 32
    assert len(evidence["catalogue_generation"]) == 32
    assert [row["kind"] for row in evidence["records"]] == ["market", "control", "market"]
    control = evidence["records"][1]
    assert control["manifest"] is None
    assert control["fact"]["kind"] == "control"
    assert control["fact"]["decoded_sha256"] is None
    assert "data_gap" in control["fact"]["subject"]
    assert control["fact"]["payload_sha256"] == hashlib.sha256(records[1].payload).hexdigest()
    for row in evidence["records"]:
        if row["kind"] == "market":
            assert row["fact"] is None
            assert row["manifest"]["file_id"]
            assert row["manifest"]["file_id"] == mixed.store.delivery_manifest(
                "LOCAL:" + mixed.producer, INCARNATION,
                row["sequence"], row["event_id"])["file_id"]
    assert mixed.worker._ledger.connection.execute(
        "SELECT * FROM local_fact ORDER BY fact_id").fetchall() == facts_before
    assert mixed.store.list_manifest() == manifests_before


def test_spool_gc_evidence_observation_is_fact_only(tmp_path):
    raws = [observation(payload=json.dumps({"event_type": "system", "n": number}).encode())[0]
            for number in range(1, 4)]
    bundle = _build(tmp_path, raws, producer="observations", observation_mode=True)
    try:
        evidence = bundle.worker.spool_gc_evidence("observations", _records(bundle))
        assert evidence["progress"]["receipt_kind"] == "observation"
        assert len(evidence["records"]) == 3
        for row in evidence["records"]:
            assert row["kind"] == "observation"
            assert row["event_id"] == ""
            assert row["manifest"] is None
            assert row["fact"]["kind"] == "observation"
            assert row["fact"]["decoded_sha256"] is not None
            assert row["fact"]["payload_sha256"] == row["payload_sha256"]
    finally:
        bundle.worker.close()
        bundle.store.close()


def test_spool_gc_evidence_bound_identity_and_contiguity(mixed):
    records = _records(mixed)
    many = tuple(SpoolRecord(SpoolCursor(mixed.producer, INCARNATION, number),
                             b"x", 1, 0, 1) for number in range(1, 502))
    with pytest.raises(LocalStorageError, match="record bound"):
        mixed.worker.spool_gc_evidence(mixed.producer, many)
    with pytest.raises(LocalStorageError, match="contiguous"):
        mixed.worker.spool_gc_evidence(mixed.producer, (records[0], records[2]))
    with pytest.raises(LocalStorageError, match="identity"):
        mixed.worker.spool_gc_evidence(
            mixed.producer,
            (SpoolRecord(SpoolCursor(mixed.producer, "other", 1), b"x", 1, 0, 1),))
    empty = mixed.worker.spool_gc_evidence(mixed.producer, ())
    assert empty["record_count"] == 0
    assert empty["progress"]["sequence"] == 3
    assert mixed.worker.spool_gc_evidence(mixed.producer, records)["record_count"] == 3


def test_spool_gc_evidence_rejects_quarantine_close_and_missing_exact_rows(mixed):
    records = _records(mixed)
    mixed.worker._source_failures[mixed.producer] = {
        "stage": "test", "error": "Synthetic", "reason": "quarantined"}
    with pytest.raises(LocalStorageError, match="quarantined"):
        mixed.worker.spool_gc_evidence(mixed.producer, records)
    mixed.worker._source_failures.clear()

    # A tampered fact row is never blessed by a cached boolean.
    mixed.worker._ledger.connection.execute(
        "UPDATE local_fact SET payload_sha256=? WHERE producer=? AND sequence=2",
        ("0" * 64, mixed.producer))
    with pytest.raises(LocalStorageError, match="missing or corrupt"):
        mixed.worker.spool_gc_evidence(mixed.producer, records)


def test_spool_gc_evidence_rejects_missing_manifest_mapping(tmp_path):
    bundle = _build(tmp_path, [wire(trade(1))])
    try:
        if bundle.store.compact_delivery_ledger:
            bundle.store._write("UPDATE parquet_delivery_compact SET event_key=?",
                                (b"\x00" * 32,))
        else:
            bundle.store._write("UPDATE parquet_delivery SET event_id='wrong'")
        with pytest.raises(LocalStorageError, match="no exact source manifest"):
            bundle.worker.spool_gc_evidence(bundle.producer, _records(bundle))
    finally:
        bundle.worker.close()
        bundle.store.close()


def test_spool_gc_evidence_close_is_fail_closed(tmp_path):
    bundle = _build(tmp_path, [wire(trade(1))])
    records = _records(bundle)
    bundle.worker.close()
    try:
        with pytest.raises(LocalStorageError, match="closed/fail-closed"):
            bundle.worker.spool_gc_evidence(bundle.producer, records)
    finally:
        bundle.store.close()
