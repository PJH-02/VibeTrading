"""VN09 dry-run spool GC planner: proof intersection, no deletion authority."""
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

from vibetrading import market_checkpoint  # noqa: E402
from vibetrading.archive import ArchiveMaintenance  # noqa: E402
from vibetrading.compactor import ParquetCompactor  # noqa: E402
from vibetrading.local_storage import LocalSpoolStorage  # noqa: E402
from vibetrading.market_spool import (  # noqa: E402
    MarketSpool, SpoolCursor, capture_retained_prefix,
)
from vibetrading.settings import RuntimeSettings  # noqa: E402
from vibetrading.sqlite_store import RuntimeStore  # noqa: E402
from vibetrading.storage_backend import LocalStorageBackend, sha256_file  # noqa: E402


PRODUCER = "collector"
INCARNATION = "inc-A"
SPOOL = dict(max_disk_bytes=1024 * 1024, max_records=100, max_frame_bytes=16384,
             max_header_bytes=512, segment_max_bytes=32768, segment_max_records=1,
             segment_max_age_seconds=60)
READ = {key: value for key, value in SPOOL.items() if not key.startswith("segment_")}


class ReadOnlyBackend(LocalStorageBackend):
    def __init__(self, root):
        super().__init__(root)
        self.put_calls = 0

    def put_file(self, source, key, *, sha256):
        self.put_calls += 1
        raise AssertionError("planner must never upload")

    def put_bytes(self, payload, key, *, sha256):
        self.put_calls += 1
        raise AssertionError("planner must never upload")


class Bundle(SimpleNamespace):
    pass


def _build(tmp_path, payloads, *, producer=PRODUCER, observation_mode=False,
           ledger="compact", batch_records=16, first=3):
    tmp_path.mkdir(parents=True, exist_ok=True)
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n"
                   f"ARCHIVE_BACKEND=local\nDELIVERY_LEDGER_FORMAT={ledger}\n",
                   encoding="utf-8")
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
    catalogue_generation = store.collection_generation()
    source = SimpleNamespace(producer=producer, incarnation=INCARNATION,
                             spool_root=root,
                             limits=SimpleNamespace(reader_kwargs=READ))
    plan = SimpleNamespace(
        producers=() if observation_mode else (source,),
        observation=source if observation_mode else None,
        storage=budgets(batch_records=batch_records).__dict__)
    worker = LocalSpoolStorage(plan, settings, budgets(batch_records=batch_records),
                               store=store)
    for _ in range(50):
        if worker.step()["records"] == 0:
            break
    backend = LocalStorageBackend(tmp_path / "remote")
    maintenance = ArchiveMaintenance(settings, store)
    maintenance._backend = backend
    if not observation_mode:
        for row in store.list_manifest(active_only=True):
            maintenance._upload(row)
    proof = capture_retained_prefix(
        root, producer, INCARNATION, first_retained_segment=first,
        generation="retained-generation",
        consumer_cursors=(SpoolCursor(producer, INCARNATION, first - 1),), **READ)
    remote_keys = {}
    for segment_id in range(1, first):
        path = root / f"{segment_id:020d}.sealed"
        key = (f"{settings.archive.prefix}/generations/{catalogue_generation}/"
               f"spool/{segment_id}.sealed")
        backend.put_file(path, key, sha256=sha256_file(path))
        remote_keys[segment_id] = key
    actual = worker.progress(producer, INCARNATION)
    readers = {
        "storage": SpoolCursor(producer, INCARNATION, int(actual["sequence"])),
        "reader-a": SpoolCursor(producer, INCARNATION, first - 1),
    }
    return Bundle(settings=settings, store=store, worker=worker, root=root,
                  producer=producer, proof=proof, backend=backend,
                  maintenance=maintenance, remote_keys=remote_keys,
                  readers=readers, catalogue_generation=catalogue_generation)


def make_bundle(tmp_path, *, observation_mode=False, payloads=None, first=3,
                batch_records=16, ledger="compact"):
    if payloads is None:
        payloads = [wire(trade(number)) for number in range(1, 5)]
    return _build(tmp_path, payloads, observation_mode=observation_mode,
                  first=first, batch_records=batch_records, ledger=ledger)


def plan(bundle, **overrides):
    kwargs = dict(
        storage=bundle.worker, producer=bundle.producer,
        retained_prefix=bundle.proof, reader_progress=bundle.readers,
        required_readers=("storage", "reader-a"),
        lease_snapshot=lambda: (bundle.proof.generation, ()),
        remote_keys=bundle.remote_keys, expected_root=bundle.root,
        expected_device=bundle.root.stat().st_dev, now_ns=1_900_000_000_000_000_000,
        retention_ns=1, max_segments=8, max_records=100,
        max_bytes=64 * 1024 * 1024)
    kwargs.update(overrides)
    return bundle.maintenance.plan_spool_gc(**kwargs)


@pytest.fixture(params=("legacy", "compact"))
def market_bundle(tmp_path, request):
    bundle = make_bundle(tmp_path, ledger=request.param)
    try:
        yield bundle
    finally:
        bundle.worker.close()
        bundle.store.close()


def test_market_eligible_cut_is_prefix_only_and_read_only(market_bundle):
    before_files = {path.name: path.read_bytes()
                    for path in market_bundle.root.iterdir()}
    before_rows = market_bundle.store.list_manifest()
    result = plan(market_bundle)
    assert result["dry_run"] is True
    assert result["deleted"] == 0
    assert result["executable"] is False
    assert result["blocked"] == []
    assert [row["segment_id"] for row in result["eligible"]] == [1, 2]
    assert result["producer"] == PRODUCER
    assert result["incarnation"] == INCARNATION
    assert result["proof"]["generation"] == "retained-generation"
    assert result["checkpoint"]["durable_sequence"] == 4
    assert len(result["facts_generation"]) == 32
    assert result["catalogue_generation"] == market_bundle.catalogue_generation
    assert result["evidence"]["record_count"] == 2
    assert len(result["plan_sha256"]) == 64
    # No eligible mutation or deletion, and no catalogue/lease write.
    assert {path.name: path.read_bytes()
            for path in market_bundle.root.iterdir()} == before_files
    assert market_bundle.store.list_manifest() == before_rows
    assert market_bundle.worker.writer.buffer_bytes == 0


def test_gap_and_observation_facts_are_exact_provenance(tmp_path):
    payloads = [wire(trade(1)), wire(gap(2)), wire(trade(3))]
    bundle = make_bundle(tmp_path / "gap", payloads=payloads, first=3)
    try:
        result = plan(bundle)
        assert result["blocked"] == []
        assert [row["segment_id"] for row in result["eligible"]] == [1, 2]
        assert result["evidence"]["record_count"] == 2
    finally:
        bundle.worker.close()
        bundle.store.close()

    raws = [observation(payload=json.dumps({"event_type": "system", "n": number}).encode())[0]
            for number in range(1, 4)]
    observations = make_bundle(tmp_path / "obs", observation_mode=True,
                               payloads=raws, first=2)
    try:
        result = plan(observations)
        assert result["blocked"] == []
        assert [row["segment_id"] for row in result["eligible"]] == [1]
        assert result["evidence"]["record_count"] == 1
    finally:
        observations.worker.close()
        observations.store.close()


def test_reader_inventory_lagging_extra_missing_and_wrong_incarnation_block(market_bundle):
    actual = market_bundle.readers["storage"]
    cases = [
        ({"storage": actual}, ("storage", "reader-a"), "reader_inventory_mismatch"),
        ({**market_bundle.readers, "extra": actual},
         ("storage", "reader-a"), "reader_inventory_mismatch"),
        ({"storage": SpoolCursor(PRODUCER, INCARNATION, actual.sequence - 1),
          "reader-a": market_bundle.readers["reader-a"]},
         ("storage", "reader-a"), "storage_progress_mismatch"),
        ({"storage": SpoolCursor(PRODUCER, "other-incarnation", actual.sequence),
          "reader-a": market_bundle.readers["reader-a"]},
         ("storage", "reader-a"), "reader_progress_invalid"),
    ]
    for readers, required, reason in cases:
        result = plan(market_bundle, reader_progress=readers, required_readers=required)
        assert result["eligible"] == []
        assert result["blocked"][0]["reason"] == reason
    # Identical positions, including the same cursor object, are valid.
    same = market_bundle.readers["storage"]
    result = plan(market_bundle,
                  reader_progress={"storage": same, "reader-a": same})
    assert result["blocked"] == []
    assert result["eligible"]


def test_lease_snapshot_missing_async_malformed_mismatch_live_and_changed(market_bundle):
    for callback, reason in (
            (None, "lease_snapshot_missing"),
            (lambda: None, "lease_snapshot_malformed"),
            (lambda: ("other-generation", ()), "lease_snapshot_generation_mismatch"),
            (lambda: (market_bundle.proof.generation, (1,)), "candidate_segment_leased"),
    ):
        result = plan(market_bundle, lease_snapshot=callback)
        assert result["eligible"] == []
        assert result["blocked"][0]["reason"] == reason

    async def asynchronous():
        return market_bundle.proof.generation, ()

    result = plan(market_bundle, lease_snapshot=asynchronous)
    assert result["eligible"] == []
    assert result["blocked"][0]["reason"] == "lease_snapshot_async"

    calls = {"n": 0}

    def changing():
        calls["n"] += 1
        return market_bundle.proof.generation, (() if calls["n"] == 1 else (2,))

    result = plan(market_bundle, lease_snapshot=changing)
    assert result["eligible"] == []
    assert result["blocked"][0]["reason"] == "lease_snapshot_changed"


def test_root_device_and_budget_reject_before_backend_lookup(market_bundle):
    with pytest.raises(ValueError, match="expected_device"):
        plan(market_bundle,
             expected_device=market_bundle.root.stat().st_dev + 1)
    with pytest.raises(ValueError, match="selected spool root|below"):
        plan(market_bundle, expected_root=market_bundle.root.parent / "other")
    for overrides, reason in (
            ({"max_bytes": 16}, "read_budget_exceeded_before_hashing"),
            ({"max_segments": 1}, "segment_budget_exceeded"),
            ({"max_records": 1}, "record_budget_exceeded"),
            ({"retention_ns": 2**63 - 1}, "retention_not_met"),
    ):
        result = plan(market_bundle, **overrides)
        assert result["eligible"] == []
        assert result["blocked"][0]["reason"] == reason
    with pytest.raises(ValueError):
        plan(market_bundle, dry_run=False)
    with pytest.raises(ValueError):
        plan(market_bundle, dry_run=1)


def test_future_timestamp_blocks(market_bundle):
    path = market_bundle.root / "00000000000000000001.sealed"
    future = 1_900_000_001_000_000_000
    os.utime(path, ns=(future, future))
    result = plan(market_bundle)
    assert result["eligible"] == []
    assert result["blocked"][0]["reason"] == "segment_timestamp_in_future"


def test_remote_raw_segment_missing_namespace_wrong_hash_and_unavailable(market_bundle):
    missing = dict(market_bundle.remote_keys)
    missing.pop(1)
    result = plan(market_bundle, remote_keys=missing)
    assert result["blocked"][0]["reason"] == "raw_segment_archive_missing"

    namespace = {**market_bundle.remote_keys, 1: "other/raw.sealed"}
    result = plan(market_bundle, remote_keys=namespace)
    assert result["blocked"][0]["reason"] == "raw_segment_key_namespace"

    key = market_bundle.remote_keys[1]
    market_bundle.backend.put_bytes(b"wrong segment bytes", key,
                                    sha256=hashlib.sha256(b"wrong segment bytes").hexdigest())
    result = plan(market_bundle)
    assert result["blocked"][0]["reason"] == "remote_verification_failed"


def test_remote_manifest_and_sidecar_wrong_hash_block(tmp_path):
    bundle = make_bundle(tmp_path)
    try:
        row = bundle.store.list_manifest(active_only=True)[0]
        bundle.backend.put_bytes(b"not the parquet bytes", row["remote_key"],
                                 sha256=hashlib.sha256(b"not the parquet bytes").hexdigest())
        result = plan(bundle)
        assert result["eligible"] == []
        assert result["blocked"][0]["reason"] == "remote_verification_failed"
    finally:
        bundle.worker.close()
        bundle.store.close()

    bundle = make_bundle(tmp_path / "sidecar")
    try:
        row = bundle.store.list_manifest(active_only=True)[0]
        bundle.backend.put_bytes(b"not the sidecar", row["remote_manifest_key"],
                                 sha256=hashlib.sha256(b"not the sidecar").hexdigest())
        result = plan(bundle)
        assert result["eligible"] == []
        assert result["blocked"][0]["reason"] == "remote_verification_failed"
    finally:
        bundle.worker.close()
        bundle.store.close()


def test_unavailable_remote_backend_preserves_all(tmp_path):
    bundle = make_bundle(tmp_path)
    try:
        def unavailable(*_args, **_kwargs):
            raise OSError("offline remote fixture")
        bundle.backend.verify = unavailable
        result = plan(bundle)
        assert result["eligible"] == []
        assert result["blocked"][0]["reason"] == "remote_verification_failed"
    finally:
        bundle.worker.close()
        bundle.store.close()


def test_same_connection_fact_and_reader_mutation_during_verify_returns_empty(tmp_path):
    payloads = [wire(gap(1)), wire(trade(2)), wire(trade(3))]
    bundle = make_bundle(tmp_path / "fact", payloads=payloads, first=3)
    calls = {"n": 0}

    def mutating():
        calls["n"] += 1
        if calls["n"] == 2:
            bundle.worker._ledger.connection.execute(
                "UPDATE local_fact SET payload_sha256=? WHERE producer=? AND sequence=1",
                ("0" * 64, PRODUCER))
        return bundle.proof.generation, ()

    try:
        result = plan(bundle, lease_snapshot=mutating)
        assert result["eligible"] == []
        assert result["blocked"][0]["reason"] == "storage_evidence_changed"
    finally:
        bundle.worker.close()
        bundle.store.close()

    bundle = make_bundle(tmp_path / "reader", payloads=[wire(trade(n)) for n in (1, 2, 3)],
                         first=3)
    calls = {"n": 0}

    def mutate_mapping():
        calls["n"] += 1
        if calls["n"] == 1:
            bundle.readers["reader-a"] = SpoolCursor(PRODUCER, INCARNATION, 3)
        return bundle.proof.generation, ()

    try:
        result = plan(bundle, lease_snapshot=mutate_mapping)
        assert result["eligible"] == []
        assert result["blocked"][0]["reason"] == "reader_progress_changed"
    finally:
        bundle.worker.close()
        bundle.store.close()


def test_compaction_chain_is_followed_and_corruption_blocks(tmp_path):
    bundle = make_bundle(tmp_path / "chain", batch_records=1, first=3)
    try:
        rows = bundle.store.list_manifest(active_only=True)
        assert len(rows) >= 2
        output = ParquetCompactor(bundle.settings, bundle.store)._compact(rows)
        assert output["ran"] is True
        terminal = bundle.store.list_manifest(active_only=True)
        assert len(terminal) == 1
        bundle.maintenance._upload(terminal[0])
        result = plan(bundle)
        assert result["blocked"] == []
        assert result["eligible"]

        terminal_row = bundle.store.list_manifest(active_only=True)[0]
        bundle.store._write("UPDATE parquet_manifest SET compacted_from='[]' WHERE file_id=?",
                            (terminal_row["file_id"],))
        result = plan(bundle)
        assert result["eligible"] == []
        assert result["blocked"][0]["reason"] == "compaction_membership_mismatch"
    finally:
        bundle.worker.close()
        bundle.store.close()


def test_compaction_cycle_blocks(tmp_path):
    bundle = make_bundle(tmp_path / "cycle", batch_records=1, first=3)
    try:
        rows = bundle.store.list_manifest(active_only=True)
        output = ParquetCompactor(bundle.settings, bundle.store)._compact(rows)
        terminal = bundle.store.list_manifest(active_only=True)[0]
        bundle.maintenance._upload(terminal)
        source_id = rows[0]["file_id"]
        bundle.store._write(
            "UPDATE parquet_manifest SET superseded_by=? WHERE file_id=?",
            (source_id, terminal["file_id"]))
        bundle.store._write(
            "UPDATE parquet_manifest SET compacted_from=? WHERE file_id=?",
            (json.dumps([terminal["file_id"]]), source_id))
        result = plan(bundle)
        assert result["eligible"] == []
        assert result["blocked"][0]["reason"] == "compaction_cycle"
    finally:
        bundle.worker.close()
        bundle.store.close()


def test_intent_is_bounded_atomic_proposal_with_checksum_and_collision(tmp_path):
    bundle = make_bundle(tmp_path / "intent")
    evidence = tmp_path.parent / f"{tmp_path.name}-intent"
    evidence.mkdir()
    destination = evidence / "proposal.json"
    try:
        result = plan(bundle, intent_path=destination)
        assert result["blocked"] == []
        payload = destination.read_bytes()
        document = json.loads(payload)
        assert document["format"] == "vibetrading-spool-gc-intent"
        assert document["version"] == 1
        assert document["sha256"] == hashlib.sha256(
            json.dumps(document["body"], ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest()
        assert document["body"]["status"] == "PROPOSED_DRY_RUN"
        assert document["body"]["executable"] is False
        assert document["body"]["requires_revalidation"] is True
        assert document["body"]["plan_sha256"] == result["plan_sha256"]
        assert "secret" not in payload.decode().lower()
        with pytest.raises(ValueError, match="exists"):
            plan(bundle, intent_path=destination)
        with pytest.raises(ValueError):
            plan(bundle, intent_path=bundle.settings.data_root / "inside.json")
        with pytest.raises(ValueError):
            plan(bundle, intent_path=Path("relative.json"))
        with pytest.raises(ValueError):
            plan(bundle, intent_path=evidence / "proposal.txt")
    finally:
        bundle.worker.close()
        bundle.store.close()


def test_intent_publication_failure_cannot_return_eligible(tmp_path, monkeypatch):
    bundle = make_bundle(tmp_path / "intent-fail")
    evidence = tmp_path.parent / f"{tmp_path.name}-intent-fail"
    evidence.mkdir()

    def broken(*_args, **_kwargs):
        raise OSError("offline fsync failure")

    monkeypatch.setattr(market_checkpoint, "_write_checkpoint_file", broken)
    try:
        result = plan(bundle, intent_path=evidence / "proposal.json")
        assert result["eligible"] == []
        assert result["blocked"][0]["reason"] == "intent_publication_failed"
    finally:
        bundle.worker.close()
        bundle.store.close()


def test_planner_never_calls_legacy_maintenance_or_uploads(tmp_path, monkeypatch):
    bundle = make_bundle(tmp_path / "readonly")
    bundle.maintenance._backend = ReadOnlyBackend(bundle.backend.root)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("legacy maintenance or upload was called")

    monkeypatch.setattr(bundle.maintenance, "run_once", forbidden)
    monkeypatch.setattr(bundle.maintenance, "_reclaim_verified", forbidden)
    monkeypatch.setattr(bundle.maintenance, "_upload", forbidden)
    before = {path.name: path.read_bytes() for path in bundle.root.iterdir()}
    try:
        result = plan(bundle)
        assert result["blocked"] == []
        assert result["eligible"]
        assert bundle.maintenance._backend.put_calls == 0
        assert {path.name: path.read_bytes() for path in bundle.root.iterdir()} == before
        assert not hasattr(bundle.maintenance, "delete_spool")
        assert not hasattr(bundle.maintenance, "claim_spool_gc")
    finally:
        bundle.worker.close()
        bundle.store.close()
