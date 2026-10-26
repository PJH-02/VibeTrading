"""Disposable real-NATS proof of generation-isolated, non-starving pruning."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import time

from storage.archive import ArchiveMaintenance
from runtime.bus import NatsJetStreamBus, dumps, subject_for_event
from storage.delivery_recovery import prune_verified
from core.models import AssetClass, InstrumentId, TradeEvent, Venue
from storage.parquet_store import ParquetBatchWriter
from runtime.settings import RuntimeSettings
from storage.sqlite_store import RuntimeStore
from storage.storage_worker import StorageWorker


async def main():
    if os.environ.get('R2_TEST_NATS_URL') != 'nats://r2-prune-nats:4222':
        raise RuntimeError('isolated r2-prune-nats required')
    with tempfile.TemporaryDirectory(prefix='r2-prune-') as temporary:
        root = Path(temporary)
        env = root / '.env'
        env.write_text(f'NATS_URL=nats://r2-prune-nats:4222\nVIBETRADING_DATA_ROOT={root}\n'
                       'NATS_MAX_BYTES=8388608\nCRYPTO_NATS_MAX_BYTES=8388608\n'
                       'NATS_OPERATIONS_MAX_BYTES=1048576\nNATS_ACK_WAIT_SECONDS=2\n'
                       'STORAGE_FLUSH_SECONDS=0.1\nPARQUET_FLUSH_SECONDS=0.1\n'
                       'ARCHIVE_ENABLED=true\nARCHIVE_BACKEND=local\n'
                       'DELIVERY_LEDGER_FORMAT=compact\nCRYPTO_EVENT_INDEX_FORMAT=immutable-runs-v1\n')
        settings = RuntimeSettings.from_env(env)
        producer = NatsJetStreamBus(settings)
        await producer.connect(initialize=True)
        initial = await producer._js.stream_info('CRYPTO_DATA')
        if initial.state.last_seq or initial.state.messages:
            raise RuntimeError('test NATS must be pristine')
        store = RuntimeStore(settings)
        event = TradeEvent(instrument=InstrumentId(venue=Venue.BYBIT, asset_class=AssetClass.CRYPTO_PERP,
                                                  raw_symbol='BTCUSDT', std_symbol='BTCUSDT'),
                           venue=Venue.BYBIT, provider='bybit-perp', source_ts_ns=time.time_ns(),
                           price='10', quantity='1', sequence=1)
        worker = asyncio.create_task(StorageWorker(NatsJetStreamBus(settings),
                                    ParquetBatchWriter(settings, store), domain='crypto').run())
        try:
            for sequence in range(1, 21):
                await producer._js.publish(subject_for_event(event), dumps(event))
                deadline = time.monotonic() + 5
                while True:
                    if worker.done():
                        await worker
                        raise RuntimeError('storage stopped prematurely')
                    info = await producer._js.consumer_info('CRYPTO_DATA', 'crypto-parquet-storage')
                    if info.ack_floor.stream_seq >= sequence:
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError('delivery did not reach server-confirmed ACK')
                    await asyncio.sleep(.1)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        old_receipt = await producer.confirmed_ack_floor('CRYPTO_DATA', 'crypto-parquet-storage', 'crypto.>')
        assert old_receipt.sequence == 20
        assert store._conn.execute('SELECT COUNT(*) FROM crypto_delivery_recovery').fetchone()[0] == 20
        maintenance = ArchiveMaintenance(settings, store)
        for row in store.list_manifest():
            maintenance._upload(row)
        maintenance._resource_maintenance_allowed = lambda: (True, {'reason': 'isolated-test'})
        assert maintenance._archive_crypto_recovery()['uploaded'] == 20
        # Simulate a lost local receipt at a restart boundary. Keep all old
        # deliveries and remote recovery data; the new receipt cannot replace it.
        with store.transaction() as conn:
            conn.execute('DELETE FROM delivery_confirmed_floor')
        await producer._js.delete_stream('CRYPTO_DATA')
        await producer.close()
        producer = NatsJetStreamBus(settings)
        await producer.connect(initialize=True)
        await producer._js.publish(subject_for_event(event), dumps(event))
        await StorageWorker(NatsJetStreamBus(settings), ParquetBatchWriter(settings, store), domain='crypto').run(2)
        new_receipt = await producer.confirmed_ack_floor('CRYPTO_DATA', 'crypto-parquet-storage', 'crypto.>')
        assert new_receipt.generation != old_receipt.generation and new_receipt.sequence == 1
        store.record_confirmed_ack_floor(new_receipt)
        assert maintenance._archive_crypto_recovery()['uploaded'] == 1
        removed = []
        for _ in range(2):
            with store.transaction() as conn:
                # Unit tests exercise the production 24-hour cutoff. Only the
                # isolated test accelerates eligibility; all other proofs apply.
                removed.append(prune_verified(conn, cutoff_ns=time.time_ns(), limit=100))
        assert removed == [0, 1]
        assert store._conn.execute('SELECT COUNT(*) FROM parquet_delivery_compact').fetchone()[0] == 20
        assert not store.delivery_committed('CRYPTO_DATA', new_receipt.generation, 1)
        assert store.event_committed(event.event_id)
        for sequence in range(1, 21):
            assert store.delivery_committed('CRYPTO_DATA', old_receipt.generation, sequence)
        # Independently retained confirmation from the original server era
        # restores permission for only that era's exact rows on the next wrap.
        store.record_confirmed_ack_floor(old_receipt)
        healed = 0
        for _ in range(2):
            with store.transaction() as conn:
                healed += prune_verified(conn, cutoff_ns=time.time_ns(), limit=100)
        assert healed == 20 and store.event_committed(event.event_id)
        info = await producer._js.consumer_info('CRYPTO_DATA', 'crypto-parquet-storage')
        assert info.num_pending == info.num_ack_pending == 0
        result = {'actual_nats_stream_recreated': True, 'old_generation': old_receipt.generation,
                  'new_generation': new_receipt.generation, 'old_confirmed_ack': 20, 'new_confirmed_ack': 1,
                  'bounded_scan_results': removed, 'unconfirmed_old_deliveries_retained': 20,
                  'old_deliveries_pruned_after_independent_proof': healed,
                  'canonical_rows': sum(row['rows'] for row in store.list_manifest()),
                  'event_index_retained': True, 'pending': 0, 'ack_pending': 0,
                  'archive_backend': 'local-test', 'production_24h_cutoff_accelerated': True}
        assert result['canonical_rows'] == 1
        print(json.dumps(result), flush=True)
        store.close()
        await producer.close()


if __name__ == '__main__':
    asyncio.run(main())
