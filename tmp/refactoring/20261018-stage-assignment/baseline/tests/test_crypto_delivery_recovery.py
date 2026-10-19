import hashlib
import json
import sqlite3
import time
import zlib

import pytest

from vibetrading.archive import ArchiveMaintenance
from vibetrading.bus import ConfirmedAckFloor
from vibetrading.delivery_recovery import read_document, restore_document
from vibetrading.parquet_store import ParquetBatchWriter
from vibetrading.sqlite_store import RuntimeStore
from vibetrading.storage_backend import archive_backend
from test_storage_pipeline import _event, _settings


ENV = ('DELIVERY_LEDGER_FORMAT=compact\nCRYPTO_EVENT_INDEX_FORMAT=immutable-runs-v1\n'
       'ARCHIVE_ENABLED=true\nARCHIVE_BACKEND=local\n')


def seeded(tmp_path, monkeypatch):
    real_now = time.time_ns()
    clock = [real_now - 3 * 86400 * 10**9]
    monkeypatch.setattr(time, 'time_ns', lambda: clock[0])
    settings = _settings(tmp_path, ENV)
    store = RuntimeStore(settings)
    writer = ParquetBatchWriter(settings, store)
    event = _event()
    writer.add(event, stream_name='CRYPTO_DATA', stream_generation='g', stream_sequence=1, event_id=event.event_id)
    writer.add_pending_delivery_alias(event.event_id, stream_name='CRYPTO_DATA', stream_generation='g', stream_sequence=3)
    writer.flush_all()
    store.commit_event_aliases([('CRYPTO_DATA', 'g', 10, event.event_id)])
    maintenance = ArchiveMaintenance(settings, store)
    monkeypatch.setattr(maintenance, '_resource_maintenance_allowed', lambda: (True, {'reason': 'quiet'}))
    clock[0] = real_now
    return store, maintenance, event, clock


def confirm(store, floor=10, generation='g', consumer='first'):
    store.record_confirmed_ack_floor(ConfirmedAckFloor('CRYPTO_DATA', generation, 'crypto-parquet-storage',
                                                     consumer, floor, time.time_ns()))


def archive_all(store, maintenance):
    for row in store.list_manifest():
        maintenance._upload(row)
    result = maintenance._archive_crypto_recovery()
    assert result['uploaded'] == 2


def test_aliases_and_holes_roundtrip_without_original_database(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    store, maintenance, event, _ = seeded(source, monkeypatch)
    # Parquet availability and ACK alone do not prove alias recovery coverage.
    confirm(store)
    assert maintenance._archive_crypto_recovery()['uploaded'] == 0
    assert store.prune_delivery_history() == 0
    archive_all(store, maintenance)
    receipts = store._conn.execute('SELECT remote_key,remote_sha256 FROM crypto_delivery_recovery ORDER BY block_id').fetchall()
    target = tmp_path / 'target'
    target.mkdir()
    recovered = RuntimeStore(_settings(target, ENV))
    for number, receipt in enumerate(receipts):
        downloaded = tmp_path / f'recovery-{number}.zlib'
        archive_backend(store.settings).download_file(receipt[0], downloaded)
        packed = downloaded.read_bytes()
        document, rows = read_document(packed, receipt[1])
        assert document['streams'] == {'1': ['CRYPTO_DATA', 'g']}
        assert restore_document(recovered, packed, receipt[1])['restored_deliveries'] == len(rows)
        assert restore_document(recovered, packed, receipt[1])['already_restored'] == 1
    assert recovered.event_committed(event.event_id)
    for sequence in (1, 3, 10):
        assert recovered.delivery_committed('CRYPTO_DATA', 'g', sequence)
    for sequence in (2, 4, 9):
        assert not recovered.delivery_committed('CRYPTO_DATA', 'g', sequence)
    assert recovered._conn.execute('SELECT COUNT(*) FROM delivery_confirmed_floor').fetchone()[0] == 0
    assert all(row['local_present'] == 0 and str(target) in row['path'] for row in recovered.list_manifest())
    # Exact batch/cursor deletion is resumable and leaves the event index.
    assert store.prune_delivery_history(limit=1) == 1
    assert store.prune_delivery_history(limit=1) == 1
    assert store.prune_delivery_history(limit=1) == 1
    assert store.prune_delivery_history(limit=1) == 0
    assert store.event_committed(event.event_id)
    store.close()
    recovered.close()


@pytest.mark.parametrize('missing', ['receipt', 'ack', 'generation', 'age'])
def test_prune_requires_every_independent_proof(tmp_path, monkeypatch, missing):
    store, maintenance, _, clock = seeded(tmp_path, monkeypatch)
    if missing != 'receipt':
        archive_all(store, maintenance)
    else:
        for row in store.list_manifest():
            maintenance._upload(row)
    if missing != 'ack':
        confirm(store, generation='other' if missing == 'generation' else 'g')
    if missing == 'age':
        clock[0] -= 3 * 86400 * 10**9
    assert store.prune_delivery_history() == 0
    assert store._conn.execute('SELECT COUNT(*) FROM parquet_delivery_compact').fetchone()[0] == 3
    store.close()


@pytest.mark.parametrize('crash_sequence', [3, 10])
def test_consumer_reset_and_failed_delete_preserve_progress(tmp_path, monkeypatch, crash_sequence):
    store, maintenance, _, clock = seeded(tmp_path, monkeypatch)
    archive_all(store, maintenance)
    confirm(store)
    clock[0] += 1
    confirm(store, floor=0, consumer='replacement')
    assert store.prune_delivery_history() == 0
    clock[0] += 1
    confirm(store, consumer='replacement')
    scan_before = store._conn.execute("SELECT value FROM runtime_metadata WHERE key='crypto_recovery_prune_scan_cursor'").fetchone()[0]
    store._conn.execute("CREATE TRIGGER reject_prune BEFORE DELETE ON parquet_delivery_compact "
                        f"WHEN OLD.stream_sequence={crash_sequence} BEGIN SELECT RAISE(ABORT,'delete crash'); END")
    with pytest.raises(sqlite3.IntegrityError, match='delete crash'):
        store.prune_delivery_history(limit=10)
    assert store.delivery_committed('CRYPTO_DATA', 'g', 1)
    assert store._conn.execute('SELECT SUM(prune_cursor) FROM crypto_delivery_recovery').fetchone()[0] == 0
    assert store._conn.execute("SELECT value FROM runtime_metadata WHERE key='crypto_recovery_prune_scan_cursor'").fetchone()[0] == scan_before
    store._conn.execute('DROP TRIGGER reject_prune')
    assert store.prune_delivery_history(limit=10) == 3
    store.close()


def test_failed_remote_verification_cannot_authorize_prune(tmp_path, monkeypatch):
    store, maintenance, _, _ = seeded(tmp_path, monkeypatch)
    for row in store.list_manifest():
        maintenance._upload(row)
    confirm(store)
    backend = maintenance.backend
    monkeypatch.setattr(backend, 'put_bytes', lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError('verify failed')))
    with pytest.raises(RuntimeError, match='verify failed'):
        maintenance._archive_crypto_recovery()
    assert store.prune_delivery_history() == 0
    assert store._conn.execute('SELECT COUNT(*) FROM crypto_delivery_recovery WHERE verified_ts_ns IS NOT NULL').fetchone()[0] == 0
    store.close()


def test_corrupted_or_cross_generation_restore_is_atomic(tmp_path, monkeypatch):
    store, maintenance, _, _ = seeded(tmp_path, monkeypatch)
    archive_all(store, maintenance)
    receipt = store._conn.execute('SELECT remote_key,remote_sha256 FROM crypto_delivery_recovery LIMIT 1').fetchone()
    downloaded = tmp_path / 'one.zlib'
    archive_backend(store.settings).download_file(receipt[0], downloaded)
    packed = downloaded.read_bytes()
    target = tmp_path / 'restore'
    target.mkdir()
    recovered = RuntimeStore(_settings(target, ENV))
    with pytest.raises(RuntimeError, match='checksum'):
        restore_document(recovered, packed[:-1] + bytes([packed[-1] ^ 1]), receipt[1])
    generation = recovered.collection_generation()
    assert generation != store.collection_generation()
    with pytest.raises(RuntimeError, match='generation collision'):
        restore_document(recovered, packed, receipt[1])
    assert recovered.list_manifest() == []
    assert recovered._conn.execute('SELECT COUNT(*) FROM parquet_delivery_compact').fetchone()[0] == 0
    document = json.loads(zlib.decompress(packed))
    document['catalogue_generation'] = generation
    del document['manifests']
    malformed = zlib.compress(json.dumps(document).encode())
    with pytest.raises((KeyError, RuntimeError)):
        restore_document(recovered, malformed, hashlib.sha256(malformed).hexdigest())
    assert recovered.list_manifest() == []
    recovered.close()
    store.close()


def test_unavailable_old_canonical_does_not_starve_later_recovery(tmp_path, monkeypatch):
    store, maintenance, _, _ = seeded(tmp_path, monkeypatch)
    store.settings.env['CRYPTO_RECOVERY_ARCHIVE_BATCH'] = '1'
    writer = ParquetBatchWriter(store.settings, store)
    other = _event().model_copy(update={'event_id': 'independent-canonical'})
    writer.add(other, stream_name='CRYPTO_DATA', stream_generation='g', stream_sequence=20, event_id=other.event_id)
    writer.flush_all()
    maintenance._upload(store.list_manifest()[0])
    first = maintenance._archive_crypto_recovery()
    assert first['uploaded'] == 0 and first['scan_progressed'] is True
    second = maintenance._archive_crypto_recovery()
    assert second['uploaded'] == 0 and second['scan_progressed'] is True
    assert maintenance._archive_crypto_recovery()['uploaded'] == 1
    wrapped = maintenance._archive_crypto_recovery()
    assert wrapped['uploaded'] == 0 and wrapped['scan_progressed'] is False
    assert store._conn.execute('SELECT COUNT(*) FROM crypto_delivery_recovery WHERE verified_ts_ns IS NULL').fetchone()[0] == 2
    store.close()


def test_unavailable_canonical_cache_skips_payload_across_reopen_and_resumes(tmp_path, monkeypatch):
    from vibetrading import delivery_recovery
    store, maintenance, _, _ = seeded(tmp_path, monkeypatch)
    original = delivery_recovery.decode_records
    decoded = []
    def tracked(*args):
        decoded.append(True)
        return original(*args)
    monkeypatch.setattr(delivery_recovery, 'decode_records', tracked)
    assert maintenance._archive_crypto_recovery()['uploaded'] == 0
    assert len(decoded) == 2
    settings = store.settings
    store.close()
    store = RuntimeStore(settings)
    maintenance = ArchiveMaintenance(settings, store)
    monkeypatch.setattr(maintenance, '_resource_maintenance_allowed', lambda: (True, {'reason': 'quiet'}))
    decoded.clear()
    statements = []
    store._conn.set_trace_callback(statements.append)
    assert maintenance._archive_crypto_recovery()['uploaded'] == 0
    assert not decoded
    assert not any('SELECT * FROM crypto_delivery_recovery' in sql for sql in statements)
    archive_all(store, maintenance)
    assert len(decoded) >= 2
    store.close()


def test_recovered_canonical_cache_revalidates_payload_checksum(tmp_path, monkeypatch):
    store, maintenance, _, _ = seeded(tmp_path, monkeypatch)
    assert maintenance._archive_crypto_recovery()['uploaded'] == 0
    for row in store.list_manifest():
        maintenance._upload(row)
    store._conn.execute('DROP TRIGGER crypto_recovery_immutable')
    store._conn.execute("UPDATE crypto_delivery_recovery SET payload=x'00' WHERE block_id=1")
    with pytest.raises((RuntimeError, zlib.error)):
        maintenance._archive_crypto_recovery()
    assert store._conn.execute('SELECT COUNT(*) FROM crypto_delivery_recovery WHERE verified_ts_ns IS NOT NULL').fetchone()[0] == 0
    store.close()


@pytest.mark.parametrize('scan_batch', [16, 64])
def test_unconfirmed_old_generation_does_not_starve_verified_new_generation_prune(tmp_path, monkeypatch, scan_batch):
    store, maintenance, event, clock = seeded(tmp_path, monkeypatch)
    store.settings.env['CRYPTO_RECOVERY_PRUNE_SCAN_BATCH'] = str(scan_batch)
    now = clock[0]
    clock[0] = now - 3 * 86400 * 10**9
    # More than the bounded candidate scan, each in its own exact block.
    for sequence in range(11, 28):
        store.commit_event_aliases([('CRYPTO_DATA', 'g', sequence, event.event_id)])
    store.commit_event_aliases([('CRYPTO_DATA', 'new', 1, event.event_id)])
    clock[0] = now
    for row in store.list_manifest():
        maintenance._upload(row)
    assert maintenance._archive_crypto_recovery()['uploaded'] == 20
    confirm(store, floor=1, generation='new')
    # Old rows deliberately lack their own generation's confirmed receipt.
    assert store.prune_delivery_history(limit=10) == 0
    assert store.prune_delivery_history(limit=10) == 1
    assert not store.delivery_committed('CRYPTO_DATA', 'new', 1)
    assert store._conn.execute('SELECT COUNT(*) FROM parquet_delivery_compact').fetchone()[0] == 20
    for sequence in (1, 3, 10, *range(11, 28)):
        assert store.delivery_committed('CRYPTO_DATA', 'g', sequence)
    # Once its independent proof becomes available, the older generation is
    # revisited on wrap instead of being permanently excluded from pruning.
    confirm(store, floor=27, generation='g')
    assert sum(store.prune_delivery_history(limit=100) for _ in range(2)) == 20
    assert store.event_committed(event.event_id)
    store.close()


def test_unconfirmed_blocks_skip_payload_after_reopen_but_revalidate_on_ack(tmp_path, monkeypatch):
    from vibetrading import delivery_recovery
    store, maintenance, _, _ = seeded(tmp_path, monkeypatch)
    archive_all(store, maintenance)
    original = delivery_recovery.decode_records
    decoded = []

    def tracked(*args):
        decoded.append(args[1])
        return original(*args)

    monkeypatch.setattr(delivery_recovery, 'decode_records', tracked)
    assert store.prune_delivery_history() == 0
    assert len(decoded) == 2
    settings = store.settings
    store.close()
    store = RuntimeStore(settings)
    # Maintenance reopens RuntimeStore every cycle. Negative evidence must
    # survive that reopen, without conferring any authority to delete.
    statements = []
    store._conn.set_trace_callback(statements.append)
    assert store.prune_delivery_history() == 0
    assert len(decoded) == 2
    discoveries = [sql for sql in statements if 'SELECT' in sql.upper() and 'FROM crypto_delivery_recovery' in sql]
    assert discoveries and all('SELECT block_id FROM' in sql for sql in discoveries)
    plan = store._conn.execute('EXPLAIN QUERY PLAN ' + discoveries[0]).fetchall()
    assert any('USING INDEX idx_crypto_recovery_prune' in row[3] for row in plan)
    # SQLite versions may label this partial index merely USING INDEX. Its
    # bytecode must never read a table column (and thus BLOB overflow pages).
    bytecode = store._conn.execute('EXPLAIN ' + discoveries[0]).fetchall()
    root_page = store._conn.execute("SELECT rootpage FROM sqlite_master WHERE name='crypto_delivery_recovery'").fetchone()[0]
    table_cursors = {row[2] for row in bytecode if row[1] == 'OpenRead' and row[3] == root_page}
    assert not any(row[1] == 'Column' and row[2] in table_cursors for row in bytecode)
    store._conn.set_trace_callback(None)
    confirm(store)
    assert store.prune_delivery_history() == 3
    assert len(decoded) == 4
    store.close()


def test_cached_block_still_checks_payload_checksum_before_deleting(tmp_path, monkeypatch):
    from vibetrading import delivery_recovery
    store, maintenance, _, _ = seeded(tmp_path, monkeypatch)
    archive_all(store, maintenance)
    assert store.prune_delivery_history() == 0
    store._conn.execute('DROP TRIGGER crypto_recovery_immutable')
    block = store._conn.execute('SELECT block_id,payload FROM crypto_delivery_recovery LIMIT 1').fetchone()
    payload = bytes(block['payload'])
    raw = zlib.decompress(payload)
    # Same metadata/cache key, but invalid exact bytes: cached bounds may only
    # skip unsafe deletion, never bypass an eligible block's fresh validation.
    corrupt = zlib.compress(raw[:-1] + bytes([raw[-1] ^ 1]), 1)
    store._conn.execute('UPDATE crypto_delivery_recovery SET payload=? WHERE block_id=?', (corrupt, block['block_id']))
    assert store.prune_delivery_history() == 0
    confirm(store)
    with pytest.raises(RuntimeError, match='checksum'):
        store.prune_delivery_history()
    assert store._conn.execute('SELECT COUNT(*) FROM parquet_delivery_compact').fetchone()[0] == 3
    assert store._conn.execute('SELECT SUM(prune_cursor) FROM crypto_delivery_recovery').fetchone()[0] == 0
    store.close()


