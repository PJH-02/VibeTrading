from __future__ import annotations

import base64
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest

from vibetrading.local_storage import LocalSpoolStorage, LocalStorageError, StorageBudgets, fact_storage_reserve
from vibetrading.market_spool import MarketSpool, SpoolCursor, SpoolRecord
from vibetrading.models import AssetClass, DataGapEvent, InstrumentId, TradeEvent, Venue
from vibetrading.sqlite_store import RuntimeStore
from vibetrading.settings import RuntimeSettings


SPOOL = dict(max_disk_bytes=1024 * 1024, max_records=100, max_frame_bytes=16384,
             max_header_bytes=512, segment_max_bytes=32768, segment_max_records=32,
             segment_max_age_seconds=60)
READ = {key: value for key, value in SPOOL.items() if not key.startswith("segment_")}


def budgets(**updates):
    values = dict(batch_records=16, batch_bytes=65536, max_payload_bytes=8192,
                  max_writer_buffer_bytes=65536, max_fact_rows=128,
                  max_fact_db_bytes=512 * 1024, poll_interval_ms=10)
    values.update(updates)
    return StorageBudgets(**values)


def item(root, producer="collector"):
    return SimpleNamespace(producer=producer, incarnation="inc-A", spool_root=root,
                           limits=SimpleNamespace(reader_kwargs=READ))


def write_source(source, payloads):
    spool = MarketSpool(source.spool_root, source.producer, source.incarnation, **SPOOL)
    for raw in payloads:
        spool.append(raw)
    spool.close()


def trade(sequence, *, event_id=None):
    values = dict(instrument=InstrumentId(venue=Venue.GRVT, asset_class=AssetClass.CRYPTO_PERP,
        raw_symbol="BTC_USDT_Perp", std_symbol="BTCUSDT"), venue=Venue.GRVT,
        provider="grvt-perp", source_ts_ns=sequence, receive_ts_ns=sequence + 1,
        wallclock_ts_ns=sequence + 1, sequence=sequence,
        price=Decimal("100.123456789012345678"), quantity=Decimal("0.000000000000000001"))
    if event_id is not None:
        values["event_id"] = event_id
    return TradeEvent(**values)


def wire(event):
    return event.model_dump_json().encode("utf-8")


def gap(sequence):
    reference = trade(sequence)
    return DataGapEvent(instrument=reference.instrument, venue=reference.venue,
        provider=reference.provider, source_ts_ns=sequence, receive_ts_ns=sequence + 1,
        sequence=sequence, reason="원본 gap", resync_status="collector_reconnecting")


def observation(subject="system.collector_start", message_id="", payload=None):
    payload = payload or b'{"event_type":"system","kind":"collector_start","ts_ns":1}'
    raw = json.dumps(dict(version=1, subject=subject, msg_id=message_id,
                          payload_base64=base64.b64encode(payload).decode()), separators=(",", ":")).encode()
    return raw, payload


@pytest.fixture(params=("legacy", "compact"))
def context(tmp_path, request):
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\nDELIVERY_LEDGER_FORMAT={request.param}\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env)
    source = item(tmp_path / "input")
    plan = SimpleNamespace(producers=(source,), observation=None, storage=budgets().__dict__)
    store = RuntimeStore(settings)
    workers = []
    def create(**options):
        worker = LocalSpoolStorage(plan, settings, options.pop("limits", budgets()), store=store, **options)
        workers.append(worker)
        return worker
    try:
        yield SimpleNamespace(settings=settings, source=source, plan=plan, store=store, create=create)
    finally:
        for worker in workers:
            worker.close()
        store.close()


def test_direct_storage_keeps_historical_decimal_data_and_exact_manifest_coverage(context):
    raw = [wire(trade(1)), wire(trade(2))]
    write_source(context.source, raw)
    source_before = {p.name: p.read_bytes() for p in context.source.spool_root.glob("*.sealed")}
    worker = context.create()
    stats = worker.step()
    assert stats == {"records": 2, "market": 2, "facts": 0, "deduplicated": 0}
    rows = [row for manifest in context.store.list_manifest(active_only=True)
            for row in pq.ParquetFile(manifest["path"]).read().to_pylist()]
    assert len(rows) == 2
    assert all(row["price"] == Decimal("100.123456789012345678") for row in rows)
    assert all(row["quantity"] == Decimal("0.000000000000000001") for row in rows)
    expected = {("LOCAL:collector", "inc-A", 1), ("LOCAL:collector", "inc-A", 2)}
    assert context.store.committed_deliveries(expected) == expected
    progress = worker.progress("collector", "inc-A")
    assert progress["sequence"] == 2
    assert progress["payload_sha256"] == hashlib.sha256(raw[-1]).hexdigest()
    assert worker.step()["records"] == 0
    assert {p.name: p.read_bytes() for p in context.source.spool_root.glob("*.sealed")} == source_before


def test_same_event_gets_exact_delivery_aliases_within_and_across_batches(context):
    raw = wire(trade(1))
    write_source(context.source, [raw, raw, raw])
    worker = context.create(limits=budgets(batch_records=2))
    first = worker.step(); second = worker.step()
    assert first["deduplicated"] == second["deduplicated"] == 1
    assert sum(meta["rows"] for meta in context.store.list_manifest()) == 1
    expected = {("LOCAL:collector", "inc-A", seq) for seq in (1, 2, 3)}
    assert context.store.committed_deliveries(expected) == expected
    assert worker.progress("collector", "inc-A")["sequence"] == 3


def test_mixed_gap_batch_commits_progress_and_facts_once_after_all_manifest_coverage(context):
    payloads = [wire(trade(1)), wire(gap(2)), wire(trade(3))]
    write_source(context.source, payloads)
    worker = context.create()
    trace = []
    worker._ledger.connection.set_trace_callback(trace.append)
    stats = worker.step()
    assert stats["records"] == 3 and stats["market"] == 2 and stats["facts"] == 1
    assert sum(statement.strip().upper() == "COMMIT" for statement in trace) == 1
    # Pre-BEGIN capacity gate and post-COMMIT checkpoint; still one FULL facts
    # transaction for the complete mixed source batch.
    assert sum("WAL_CHECKPOINT(TRUNCATE)" in statement.upper() for statement in trace) == 2
    facts = worker._ledger.connection.execute("SELECT * FROM local_fact ORDER BY fact_id").fetchall()
    assert len(facts) == 1 and facts[0]["kind"] == "control"
    assert bytes(facts[0]["raw_payload"]) == payloads[1]
    assert facts[0]["decoded_payload"] is None
    assert facts[0]["subject"] == "crypto.data_gap.grvt.crypto_perp.BTCUSDT"
    assert worker.progress("collector", "inc-A")["sequence"] == 3
    assert not context.store.delivery_committed("LOCAL:collector", "inc-A", 2)


def test_observation_envelope_and_decoded_bytes_are_durable_without_market_scan(context):
    source = item(context.settings.data_root / "observations", "observations")
    raw, decoded = observation(payload='{"event_type":"system","원문":"알림"}'.encode())
    write_source(source, [raw])
    # The market source is empty but still has a verified producer directory.
    write_source(context.source, [])
    context.plan.observation = source
    worker = context.create()
    assert worker.step()["records"] == 0
    assert worker.step()["facts"] == 1
    fact = worker._ledger.connection.execute("SELECT * FROM local_fact").fetchone()
    assert bytes(fact["raw_payload"]) == raw and bytes(fact["decoded_payload"]) == decoded
    assert fact["msg_id"] == "" and fact["subject"] == "system.collector_start"
    generation = worker._ledger.connection.execute("SELECT value FROM facts_metadata WHERE key='generation'").fetchone()[0]
    assert len(generation) == 32
    worker.close()
    reopened = context.create()
    assert reopened.step()["records"] == reopened.step()["records"] == 0
    assert reopened._ledger.connection.execute("SELECT COUNT(*) FROM local_fact").fetchone()[0] == 1
    assert reopened._ledger.connection.execute("SELECT value FROM facts_metadata WHERE key='generation'").fetchone()[0] == generation


def test_crash_after_manifest_before_progress_replays_without_duplicate_parquet(context, monkeypatch):
    write_source(context.source, [wire(trade(1)), wire(gap(2)), wire(trade(3))])
    worker = context.create()
    def interrupted(_entries):
        raise OSError("offline loss after manifest coverage")
    monkeypatch.setattr(worker._ledger, "commit_batch", interrupted)
    with pytest.raises(OSError, match="after manifest"):
        worker.step()
    assert worker._ledger.progress("collector", "inc-A") is None
    assert worker._ledger.connection.execute("SELECT COUNT(*) FROM local_fact").fetchone()[0] == 0
    assert sum(meta["rows"] for meta in context.store.list_manifest()) == 2
    worker.close()
    recovered = context.create()
    assert recovered.step()["deduplicated"] == 2
    assert sum(meta["rows"] for meta in context.store.list_manifest()) == 2
    assert recovered.progress("collector", "inc-A")["sequence"] == 3
    assert recovered._ledger.connection.execute("SELECT COUNT(*) FROM local_fact").fetchone()[0] == 1


def test_fact_row_budget_rolls_back_entire_mixed_gap_progress_batch(context):
    write_source(context.source, [wire(trade(1)), wire(gap(2)), wire(gap(3))])
    worker = context.create(limits=budgets(max_fact_rows=1))
    with pytest.raises(LocalStorageError, match="row budget"):
        worker.step()
    assert worker._ledger.progress("collector", "inc-A") is None
    assert worker._ledger.connection.execute("SELECT COUNT(*) FROM local_fact").fetchone()[0] == 0
    # Market bytes may already be durably stored; retaining source and missing
    # consumer progress ensures replay, rather than pretending facts committed.
    assert context.store.delivery_committed("LOCAL:collector", "inc-A", 1)
    with pytest.raises(LocalStorageError, match="fail-closed"):
        worker.step()


def test_injected_sequence_gap_rolls_back_facts_and_never_advances_checkpoint(context):
    write_source(context.source, [])
    worker = context.create()
    raw = wire(gap(1))
    records = tuple(SpoolRecord(SpoolCursor("collector", "inc-A", seq), raw, 1, 0, len(raw)) for seq in (1, 3))
    rows = [(record, "control", "id", ("system.gap", "id", None)) for record in records]
    with pytest.raises(LocalStorageError, match="skip/regress"):
        worker._ledger.commit_batch(rows)
    assert worker._ledger.progress("collector", "inc-A") is None
    assert worker._ledger.connection.execute("SELECT COUNT(*) FROM local_fact").fetchone()[0] == 0


def test_missing_manifest_coverage_cannot_advance_local_checkpoint(context, monkeypatch):
    write_source(context.source, [wire(trade(1))])
    worker = context.create()
    monkeypatch.setattr(context.store, "committed_deliveries", lambda _keys: set())
    with pytest.raises(LocalStorageError, match="did not cover"):
        worker.step()
    assert worker._ledger.progress("collector", "inc-A") is None


def test_malformed_observation_is_fail_closed_without_fact_or_checkpoint(context):
    source = item(context.settings.data_root / "observations", "observations")
    raw, _ = observation(subject="live.crypto.trade")
    write_source(source, [raw]); write_source(context.source, [])
    context.plan.observation = source
    worker = context.create()
    worker.step()
    with pytest.raises(LocalStorageError, match="namespace"):
        worker.step()
    assert worker._ledger.progress("observations", "inc-A") is None
    assert worker._ledger.connection.execute("SELECT COUNT(*) FROM local_fact").fetchone()[0] == 0


def test_second_storage_writer_cannot_claim_same_fact_database(context):
    write_source(context.source, [])
    worker = context.create()
    with pytest.raises((OSError, LocalStorageError)):
        context.create()
    assert worker.step()["records"] == 0


def test_restart_rejects_missing_manifest_or_changed_progress_digest(context):
    write_source(context.source, [wire(trade(1))])
    worker = context.create(); worker.step(); worker.close()
    path = context.settings.state_dir / "local_storage_facts.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE local_progress SET payload_sha256=?", ("0" * 64,))
    with pytest.raises(LocalStorageError, match="does not match"):
        context.create()


@pytest.mark.parametrize("field,value", (("batch_records", 0), ("batch_bytes", True),
    ("max_payload_bytes", -1), ("max_writer_buffer_bytes", 0), ("max_fact_rows", 0),
    ("max_fact_db_bytes", 4096), ("poll_interval_ms", 0)))
def test_storage_budgets_are_explicit_finite_positive(field, value):
    with pytest.raises(ValueError):
        budgets(**{field: value})


def test_same_event_id_with_changed_economic_content_is_rejected_before_any_batch_commit(context):
    first=trade(1)
    changed=first.model_copy(update={'price':Decimal('999')})
    write_source(context.source,[wire(first),wire(changed)])
    worker=context.create()
    with pytest.raises(LocalStorageError,match='conflicting canonical content'):
        worker.step()
    assert worker._ledger.progress('collector','inc-A') is None
    assert not context.store.list_manifest()
    assert not context.store.delivery_committed('LOCAL:collector','inc-A',2)


def test_cross_batch_conflict_cannot_claim_existing_manifest_coverage(context):
    first=trade(1)
    changed=first.model_copy(update={'quantity':Decimal('3')})
    write_source(context.source,[wire(first),wire(changed)])
    worker=context.create(limits=budgets(batch_records=1))
    worker.step()
    with pytest.raises(LocalStorageError,match='conflicting canonical content'):
        worker.step()
    assert worker._ledger.progress('collector','inc-A')['sequence']==1
    assert not context.store.delivery_committed('LOCAL:collector','inc-A',2)
    assert sum(meta['rows'] for meta in context.store.list_manifest())==1


@pytest.mark.parametrize('changed',[False,True])
def test_restart_committed_id_new_cursor_is_unverifiable_without_attestation(context,changed):
    first=trade(1)
    second=first.model_copy(update={'price':Decimal('999')}) if changed else first
    write_source(context.source,[wire(first),wire(second)])
    worker=context.create(limits=budgets(batch_records=1));worker.step();worker.close()
    restarted=context.create(limits=budgets(batch_records=1))
    with pytest.raises(LocalStorageError,match='unverifiable.*cache miss'):
        restarted.step()
    assert restarted._ledger.progress('collector','inc-A')['sequence']==1
    assert not context.store.delivery_committed('LOCAL:collector','inc-A',2)


def test_attestation_cache_is_bounded_and_eviction_never_invents_alias_proof(context):
    first=trade(1)
    values=[first,trade(2),trade(3),first]
    write_source(context.source,[wire(value) for value in values])
    worker=context.create(limits=budgets(batch_records=2))
    worker.step()
    # The second source batch is prepared against the existing two-entry
    # attestation cache, then new entries may evict its oldest fingerprint.
    worker.step()
    assert len(worker._fingerprints)<=2
    assert worker._ledger.progress('collector','inc-A')['sequence']==4
    assert context.store.delivery_committed('LOCAL:collector','inc-A',4)


def test_pinned_reader_cannot_grow_wal_through_repeated_storage_restart(context,monkeypatch):
    from vibetrading import local_storage
    original=local_storage._FactLedger._checkpoint_gate

    def fast_checkpoint(self):
        self.connection.execute('PRAGMA busy_timeout=1')
        return original(self)

    monkeypatch.setattr(local_storage._FactLedger,'_checkpoint_gate',fast_checkpoint)
    write_source(context.source,[wire(trade(1))])
    worker=context.create()
    path=context.settings.state_dir/'local_storage_facts.sqlite3'
    reader=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,isolation_level=None)
    try:
        reader.execute('BEGIN')
        reader.execute('SELECT COUNT(*) FROM local_fact').fetchone()
        with pytest.raises(LocalStorageError,match='checkpoint blocked'):
            worker.step()
        # The batch COMMIT preceded the blocked post-checkpoint; its progress
        # remains durable, but every restarted writer must stop before writing.
        assert worker._ledger.progress('collector','inc-A')['sequence']==1
        wal=path.with_name(path.name+'-wal')
        size=wal.stat().st_size
        assert size>0
        worker.close()
        for _ in range(3):
            with pytest.raises(LocalStorageError,match='checkpoint blocked'):
                context.create()
            assert wal.stat().st_size==size
    finally:
        reader.execute('ROLLBACK');reader.close()
    recovered=context.create()
    assert recovered.step()['records']==0
    assert recovered._ledger.progress('collector','inc-A')['sequence']==1


def test_wal_capacity_and_free_space_gate_precede_next_transaction(context,monkeypatch):
    write_source(context.source,[wire(gap(1))])
    worker=context.create()
    monkeypatch.setattr(worker._ledger,'_wal_size',lambda:worker._ledger._wal_limit+1)
    with pytest.raises(LocalStorageError,match='WAL exceeds'):
        worker.step()
    assert worker._ledger.progress('collector','inc-A') is None
    assert worker._ledger.connection.execute('SELECT COUNT(*) FROM local_fact').fetchone()[0]==0


def test_fact_wal_space_reservation_blocks_commit_before_disk_exhaustion(context,monkeypatch):
    write_source(context.source,[wire(gap(1))])
    worker=context.create()
    monkeypatch.setattr('vibetrading.local_storage.shutil.disk_usage',lambda path:SimpleNamespace(free=0))
    with pytest.raises(LocalStorageError,match='insufficient free space'):
        worker.step()
    assert worker._ledger.progress('collector','inc-A') is None
    assert worker._ledger.connection.execute('SELECT COUNT(*) FROM local_fact').fetchone()[0]==0


def test_fact_reservation_accounts_for_wal_frames_and_shm_regions():
    maximum=512*1024
    assert fact_storage_reserve(maximum)>2*maximum+32768
    assert fact_storage_reserve(maximum)>=fact_storage_reserve(maximum,4096)
