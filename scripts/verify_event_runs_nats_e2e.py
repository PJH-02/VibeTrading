"""Bounded integration test; refuses production NATS addresses and data paths."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq

from runtime.bus import BusMessage, NatsJetStreamBus, dumps, subject_for_event
from storage.archive import ArchiveMaintenance
from storage.delivery_recovery import prune_verified, restore_document, retire_runs
from core.models import AssetClass, InstrumentId, TradeEvent, Venue
from storage.parquet_store import ParquetBatchWriter
from runtime.settings import RuntimeSettings
from storage.sqlite_store import RuntimeStore
from storage.storage_worker import StorageWorker
from storage.storage_backend import archive_backend


async def verify():
    url = os.environ.get('R2_TEST_NATS_URL')
    if url != 'nats://r2-test-nats:4222':
        raise RuntimeError('disposable r2-test-nats required')
    report = []
    with tempfile.TemporaryDirectory(prefix='r2-e2e-') as temporary:
        for domain in ('market', 'crypto'):
            root = Path(temporary) / domain
            root.mkdir()
            env = root / '.env'
            env.write_text(f'NATS_URL={url}\nVIBETRADING_DATA_ROOT={root}\n'
                           'NATS_MAX_BYTES=8388608\nCRYPTO_NATS_MAX_BYTES=8388608\n'
                           'NATS_OPERATIONS_MAX_BYTES=1048576\nNATS_ACK_WAIT_SECONDS=2\n'
                           'STORAGE_FLUSH_SECONDS=0.2\nPARQUET_FLUSH_SECONDS=0.2\n'
                           'ARCHIVE_ENABLED=true\nARCHIVE_BACKEND=local\n'
                           + ('DELIVERY_LEDGER_FORMAT=compact\nCRYPTO_EVENT_INDEX_FORMAT=immutable-runs-v1\n'
                              'CRYPTO_EVENT_RUN_RETENTION_ENABLED=true\nCRYPTO_ENFORCE_LIVE_EVENT_WINDOW=true\n'
                              if domain == 'crypto' else ''), encoding='utf-8')
            settings = RuntimeSettings.from_env(env)
            producer = NatsJetStreamBus(settings)
            await producer.connect(initialize=True)
            stream, durable = ('CRYPTO_DATA', 'crypto-parquet-storage') if domain == 'crypto' else ('MARKET_DATA', 'parquet-storage')
            expected = set()
            events = []
            for sequence in range(180):
                venue = (Venue.BINANCE, Venue.BITGET, Venue.BYBIT)[sequence % 3] if domain == 'crypto' else Venue.KIS
                symbol = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT')[(sequence // 3) % 3] if domain == 'crypto' else 'ABC'
                instrument = InstrumentId(venue=venue, asset_class=AssetClass.CRYPTO_PERP if domain == 'crypto' else AssetClass.EQUITY,
                                          raw_symbol=symbol, std_symbol=symbol)
                event = TradeEvent(instrument=instrument, venue=venue, provider=venue.value, source_ts_ns=time.time_ns(),
                                   sequence=sequence, price=Decimal('10'), quantity=Decimal('1'))
                events.append(event)
                expected.add(event.event_id)
                await producer.publish_market_event(event)
                if domain == 'crypto' and sequence % 10 == 0:
                    # No message-id header: exercise a genuinely new delivery.
                    await producer._js.publish(subject_for_event(event), dumps(event))
            store = RuntimeStore(settings)
            original_ack = BusMessage.ack
            checked_sequences = set()
            dropped_ack = False
            redeliveries = 0

            async def checked_ack(message):
                nonlocal dropped_ack, redeliveries
                assert store.delivery_committed(message.stream_name, message.stream_generation, message.stream_sequence), 'ACK before delivery commit'
                checked_sequences.add(message.stream_sequence)
                redeliveries += int(message.delivery_count > 1)
                if message.stream_sequence == 1 and not dropped_ack:
                    dropped_ack = True
                    return  # Simulate an ACK lost after durable commit.
                await original_ack(message)

            BusMessage.ack = checked_ack
            try:
                first = await StorageWorker(NatsJetStreamBus(settings), ParquetBatchWriter(settings, store), domain=domain).run(4)
                store.close()
                store = RuntimeStore(settings)
                if domain == 'crypto':
                    for event in events[::10]:
                        await producer._js.publish(subject_for_event(event), dumps(event))
                second = await StorageWorker(NatsJetStreamBus(settings), ParquetBatchWriter(settings, store), domain=domain).run(3)
                await producer._nc.flush()
                info = await producer._js.consumer_info(stream, durable)
                assert info.num_pending == info.num_ack_pending == 0
                rows = [row for manifest in store.list_manifest() for row in pq.ParquetFile(manifest['path']).read().to_pylist()]
                assert len(rows) == len(expected) == 180
                assert {row['event_id'] for row in rows} == expected
                deliveries = 216 if domain == 'crypto' else 180
                assert len(checked_sequences) == deliveries
                assert redeliveries >= 1, 'lost ACK was not redelivered'
                if domain == 'crypto':
                    receipt = await producer.confirmed_ack_floor(stream, durable, 'crypto.>')
                    assert receipt.sequence == deliveries
                    store.record_confirmed_ack_floor(receipt)
                    assert store._conn.execute('SELECT ack_floor FROM delivery_confirmed_floor').fetchone()[0] == deliveries
                    assert store._conn.execute('SELECT COUNT(*) FROM parquet_delivery_compact').fetchone()[0] == deliveries
                    maintenance = ArchiveMaintenance(settings, store)
                    maintenance._resource_maintenance_allowed = lambda: (True, {'reason': 'isolated_test'})
                    for manifest in store.list_manifest():
                        maintenance._upload(manifest)
                    archived = maintenance._archive_crypto_recovery()
                    assert archived['uploaded'] > 0
                    restored_root = root / 'restore'
                    restored_root.mkdir()
                    restored_env = restored_root / '.env'
                    restored_env.write_text(f'VIBETRADING_DATA_ROOT={restored_root}\nDELIVERY_LEDGER_FORMAT=compact\n'
                                            'CRYPTO_EVENT_INDEX_FORMAT=immutable-runs-v1\n', encoding='utf-8')
                    recovered = RuntimeStore(RuntimeSettings.from_env(restored_env))
                    restored_count = 0
                    recovery_documents = []
                    for block in store._conn.execute('SELECT block_id,remote_key,remote_sha256 FROM crypto_delivery_recovery'):
                        downloaded = root / f'{block[0]}.recovery'
                        archive_backend(settings).download_file(block[1], downloaded)
                        packed=downloaded.read_bytes()
                        recovery_documents.append((packed,block[2]))
                        restored_count += restore_document(recovered, packed, block[2])['restored_deliveries']
                    assert restored_count == deliveries
                    assert len(recovered.committed_event_ids(expected)) == len(expected)
                    assert recovered._conn.execute('SELECT COUNT(*) FROM delivery_confirmed_floor').fetchone()[0] == 0
                    # The unit tests cover the real 24-hour clock gate. This
                    # isolated transaction exercises exact batched deletion.
                    with store.transaction() as conn:
                        removed = prune_verified(conn, cutoff_ns=time.time_ns(), limit=1000)
                    assert removed == deliveries
                    assert len(store.committed_event_ids(expected)) == len(expected)
                    with store.transaction() as conn:
                        retired=retire_runs(conn,now_ns=time.time_ns()+3*86400*10**9,max_age_seconds=172800,limit=16)
                    assert retired['retired_runs']>0 and not store.committed_event_ids(expected)
                    historical=events[0]
                    await producer._js.publish(subject_for_event(historical),dumps(historical))
                    try:
                        await StorageWorker(NatsJetStreamBus(settings),ParquetBatchWriter(settings,store),domain='crypto').run(2)
                    except RuntimeError as exc:
                        assert str(exc)=='crypto_historical_replay_requires_index_restore'
                    else:
                        raise AssertionError('expired historical event became a new canonical row')
                    blocked=await producer._js.consumer_info(stream,durable)
                    assert blocked.num_ack_pending+blocked.num_pending==1
                    assert not store.delivery_committed(stream,receipt.generation,deliveries+1)
                    for packed,sha in recovery_documents:
                        restore_document(store,packed,sha)
                    await StorageWorker(NatsJetStreamBus(settings),ParquetBatchWriter(settings,store),domain='crypto').run(4)
                    info=await producer._js.consumer_info(stream,durable)
                    assert info.num_pending==info.num_ack_pending==0
                    assert store.delivery_committed(stream,receipt.generation,deliveries+1)
                    assert sum(row['rows'] for row in store.list_manifest())==180
                    recovered.close()
                report.append({'domain': domain, 'parquet_rows': len(rows), 'durable_deliveries_before_ack': deliveries,
                               'redeliveries': redeliveries, 'nats_pending': info.num_pending,
                               'nats_ack_pending': info.num_ack_pending, 'restart_verified': True,
                               'remote_recovery_backend': 'local-test' if domain == 'crypto' else None,
                               'exact_deliveries_restored': restored_count if domain == 'crypto' else None,
                               'exact_deliveries_pruned': removed if domain == 'crypto' else None,
                               'retirement_replay_blocked_then_recovered': domain=='crypto',
                               'first': first, 'second': second})
            finally:
                BusMessage.ack = original_ack
                store.close()
                await producer.close()
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    asyncio.run(verify())
