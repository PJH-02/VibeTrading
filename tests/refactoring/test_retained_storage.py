from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from types import SimpleNamespace

import pytest

# Test runs must not inherit a deployment data root that the per-test .env
# fixture intentionally replaces. This keeps the credential-free local ledgers
# isolated to each tmp_path.
os.environ.pop("VIBETRADING_DATA_ROOT", None)
os.environ.pop("DELIVERY_LEDGER_FORMAT", None)

from test_local_storage import budgets, observation, trade, wire  # noqa: E402

from vibetrading.local_storage import LocalSpoolStorage, LocalStorageError  # noqa: E402
from vibetrading.market_spool import (  # noqa: E402
    MarketSpool, RetainedPrefix, SpoolCursor, capture_retained_prefix,
)
from vibetrading.sqlite_store import RuntimeStore  # noqa: E402
from vibetrading.settings import RuntimeSettings  # noqa: E402


SEGMENT = re.compile(r"([0-9]{20})\.(sealed|active)\Z")
SPOOL = dict(max_disk_bytes=1024 * 1024, max_records=100, max_frame_bytes=16384,
             max_header_bytes=512, segment_max_bytes=32768, segment_max_records=1,
             segment_max_age_seconds=60)
READ = {key: value for key, value in SPOOL.items() if not key.startswith("segment_")}
PRODUCER = "collector"
INCARNATION = "inc-A"


def item(root, producer=PRODUCER):
    return SimpleNamespace(producer=producer, incarnation=INCARNATION, spool_root=root,
                           limits=SimpleNamespace(reader_kwargs=READ))


def write_source(source, payloads):
    spool = MarketSpool(source.spool_root, source.producer, source.incarnation, **SPOOL)
    try:
        for raw in payloads:
            spool.append(raw)
            spool.seal()
    finally:
        spool.close()


def capture(root, first, consumers=None, *, producer=PRODUCER, incarnation=INCARNATION):
    if consumers is None:
        consumers = (SpoolCursor(producer, incarnation, first - 1),)
    return capture_retained_prefix(
        root, producer, incarnation, first_retained_segment=first,
        generation="retained-generation", consumer_cursors=tuple(consumers),
        max_disk_bytes=SPOOL["max_disk_bytes"], max_records=SPOOL["max_records"],
        max_frame_bytes=SPOOL["max_frame_bytes"], max_header_bytes=SPOOL["max_header_bytes"])


def trimmed_copy(source: Path, target: Path, first: int):
    target.mkdir(parents=True)
    for path in source.iterdir():
        if path.name == "writer.lock":
            continue
        if path.name == "checkpoint.json":
            shutil.copy2(path, target / path.name)
            continue
        match = SEGMENT.fullmatch(path.name)
        if match is not None and int(match[1]) >= first:
            shutil.copy2(path, target / path.name)


@pytest.fixture
def context(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n"
                   "DELIVERY_LEDGER_FORMAT=compact\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env)
    source = item(tmp_path / "full")
    plan = SimpleNamespace(producers=(source,), observation=None,
                           storage=budgets().__dict__)
    store = RuntimeStore(settings)
    workers = []
    tried = []

    class Factory:
        def __init__(self):
            self.settings = settings
            self.source = source
            self.plan = plan
            self.store = store

        def create(self, *, plan=None, limits=None, **options):
            target = plan or self.plan
            tried.append(target)
            worker = LocalSpoolStorage(target, settings, limits or budgets(),
                                       store=store, **options)
            workers.append(worker)
            return worker

    factory = Factory()
    try:
        yield factory
    finally:
        for worker in workers:
            worker.close()
        store.close()


def test_market_progress_exactly_at_base_resumes_without_duplicate_output(context, tmp_path):
    payloads = [wire(trade(sequence)) for sequence in range(1, 5)]
    write_source(context.source, payloads)
    proof = capture(context.source.spool_root, 2)
    worker = context.create(limits=budgets(batch_records=1))
    assert worker.step()["records"] == 1
    assert worker.progress(PRODUCER, INCARNATION)["sequence"] == 1
    worker.close()
    assert context.store.delivery_committed("LOCAL:" + PRODUCER, INCARNATION, 1)

    trimmed = tmp_path / "trimmed"
    trimmed_copy(context.source.spool_root, trimmed, 2)
    context.source.spool_root = trimmed
    restarted = context.create(retained_prefixes={PRODUCER: proof},
                               limits=budgets(batch_records=1))
    assert restarted.progress(PRODUCER, INCARNATION)["sequence"] == 1
    assert restarted.step()["records"] == 1
    assert restarted.step()["records"] == 1
    assert restarted.step()["records"] == 1
    assert restarted.step()["records"] == 0
    assert restarted.progress(PRODUCER, INCARNATION)["sequence"] == 4
    assert sum(meta["rows"] for meta in context.store.list_manifest()) == 4
    before = {path.name: path.read_bytes() for path in trimmed.iterdir()}
    restarted.step()
    assert {path.name: path.read_bytes() for path in trimmed.iterdir()} == before


def test_observation_fact_progress_exactly_at_base_resumes_without_duplicate_fact(context, tmp_path):
    source = item(tmp_path / "observations", "observations")
    raws = [observation(payload=json.dumps({"event_type": "system", "n": number}).encode())[0]
            for number in range(1, 4)]
    write_source(source, raws)
    proof = capture(source.spool_root, 2, producer="observations")
    plan = SimpleNamespace(producers=(), observation=source,
                           storage=budgets().__dict__)
    worker = context.create(plan=plan, limits=budgets(batch_records=1))
    assert worker.step()["facts"] == 1
    assert worker.progress("observations", INCARNATION)["sequence"] == 1
    worker.close()

    trimmed = tmp_path / "trimmed-obs"
    trimmed_copy(source.spool_root, trimmed, 2)
    source.spool_root = trimmed
    restarted = context.create(plan=plan, retained_prefixes={"observations": proof},
                               limits=budgets(batch_records=1))
    assert restarted.progress("observations", INCARNATION)["sequence"] == 1
    assert restarted.step()["facts"] == 1
    assert restarted.step()["facts"] == 1
    assert restarted.step()["records"] == 0
    assert restarted.progress("observations", INCARNATION)["sequence"] == 3
    count = restarted._ledger.connection.execute(
        "SELECT COUNT(*) FROM local_fact").fetchone()[0]
    assert count == 3


def test_no_progress_or_progress_below_retained_base_refuses_startup(context, tmp_path):
    payloads = [wire(trade(sequence)) for sequence in range(1, 5)]
    write_source(context.source, payloads)
    proof = capture(context.source.spool_root, 3)
    trimmed = tmp_path / "trimmed"
    trimmed_copy(context.source.spool_root, trimmed, 3)
    context.source.spool_root = trimmed
    with pytest.raises(LocalStorageError, match="no persisted progress"):
        context.create(retained_prefixes={PRODUCER: proof})
    assert not context.store.list_manifest()


def test_progress_below_retained_base_refuses_startup(context, tmp_path):
    payloads = [wire(trade(sequence)) for sequence in range(1, 5)]
    write_source(context.source, payloads)
    proof = capture(context.source.spool_root, 3)
    worker = context.create(limits=budgets(batch_records=1))
    worker.step()
    assert worker.progress(PRODUCER, INCARNATION)["sequence"] == 1
    worker.close()
    trimmed = tmp_path / "trimmed"
    trimmed_copy(context.source.spool_root, trimmed, 3)
    context.source.spool_root = trimmed
    with pytest.raises(LocalStorageError, match="no persisted progress"):
        context.create(retained_prefixes={PRODUCER: proof})
    assert sum(meta["rows"] for meta in context.store.list_manifest()) == 1


def test_progress_above_base_revalidates_one_prior_record_and_resumes(context, tmp_path):
    payloads = [wire(trade(sequence)) for sequence in range(1, 5)]
    write_source(context.source, payloads)
    proof = capture(context.source.spool_root, 2)
    worker = context.create(limits=budgets(batch_records=1))
    worker.step()
    worker.step()
    assert worker.progress(PRODUCER, INCARNATION)["sequence"] == 2
    worker.close()
    trimmed = tmp_path / "trimmed"
    trimmed_copy(context.source.spool_root, trimmed, 2)
    context.source.spool_root = trimmed
    restarted = context.create(retained_prefixes={PRODUCER: proof},
                               limits=budgets(batch_records=1))
    assert restarted.progress(PRODUCER, INCARNATION)["sequence"] == 2
    assert restarted.step()["records"] == 1
    assert restarted.step()["records"] == 1
    assert restarted.step()["records"] == 0
    assert restarted.progress(PRODUCER, INCARNATION)["sequence"] == 4
    assert sum(meta["rows"] for meta in context.store.list_manifest()) == 4


def test_wrong_base_digest_fails_before_new_delivery(context, tmp_path, monkeypatch):
    payloads = [wire(trade(sequence)) for sequence in range(1, 4)]
    write_source(context.source, payloads)
    proof = capture(context.source.spool_root, 2)
    worker = context.create(limits=budgets(batch_records=1))
    worker.step()
    worker._ledger.connection.execute(
        "UPDATE local_progress SET payload_sha256=? WHERE producer=?",
        ("0" * 64, PRODUCER))
    worker.close()
    trimmed = tmp_path / "trimmed"
    trimmed_copy(context.source.spool_root, trimmed, 2)
    context.source.spool_root = trimmed
    manifests = context.store.list_manifest()
    with pytest.raises(LocalStorageError, match="digest"):
        context.create(retained_prefixes={PRODUCER: proof})
    assert context.store.list_manifest() == manifests


def test_missing_manifest_coverage_at_base_fails(context, tmp_path, monkeypatch):
    payloads = [wire(trade(sequence)) for sequence in range(1, 4)]
    write_source(context.source, payloads)
    proof = capture(context.source.spool_root, 2)
    worker = context.create(limits=budgets(batch_records=1))
    worker.step()
    worker.close()
    trimmed = tmp_path / "trimmed"
    trimmed_copy(context.source.spool_root, trimmed, 2)
    context.source.spool_root = trimmed
    monkeypatch.setattr(context.store, "delivery_committed", lambda *_keys: False)
    with pytest.raises(LocalStorageError, match="manifest"):
        context.create(retained_prefixes={PRODUCER: proof})


def test_missing_fact_at_observation_base_fails(context, tmp_path):
    source = item(tmp_path / "observations", "observations")
    raws = [observation(payload=json.dumps({"event_type": "system", "n": number}).encode())[0]
            for number in range(1, 4)]
    write_source(source, raws)
    proof = capture(source.spool_root, 2, producer="observations")
    plan = SimpleNamespace(producers=(), observation=source,
                           storage=budgets().__dict__)
    worker = context.create(plan=plan, limits=budgets(batch_records=1))
    worker.step()
    worker._ledger.connection.execute(
        "DELETE FROM local_fact WHERE producer=? AND sequence=1", ("observations",))
    worker.close()
    trimmed = tmp_path / "trimmed-obs"
    trimmed_copy(source.spool_root, trimmed, 2)
    source.spool_root = trimmed
    with pytest.raises(LocalStorageError, match="missing or corrupt"):
        context.create(plan=plan, retained_prefixes={"observations": proof})


def test_unknown_identity_type_and_unselected_keys_fail_before_shared_writer(context, tmp_path):
    payloads = [wire(trade(sequence)) for sequence in range(1, 4)]
    write_source(context.source, payloads)
    proof = capture(context.source.spool_root, 2)

    with pytest.raises(ValueError, match="unselected"):
        context.create(retained_prefixes={"unselected": proof})
    with pytest.raises(ValueError, match="validated"):
        context.create(retained_prefixes={PRODUCER: object()})
    assert not context.store.list_manifest()
    assert not (context.settings.state_dir / "local_storage_facts.sqlite3").exists()

    document = json.loads(proof.to_bytes())
    document["incarnation"] = "other-incarnation"
    document["consumer_cursors"] = [[PRODUCER, "other-incarnation", proof.base_sequence]]
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    forged = RetainedPrefix.from_bytes(
        payload, expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_generation=proof.generation)
    with pytest.raises(ValueError, match="identity"):
        context.create(retained_prefixes={PRODUCER: forged})
