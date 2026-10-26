"""Run only against a disposable NATS server, never production subjects/data."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq

from vibetrading.archive import ArchiveMaintenance
from vibetrading.bus import BusMessage, NatsJetStreamBus
from core.models import AssetClass, InstrumentId, TradeEvent, Venue
from vibetrading.parquet_store import ParquetBatchWriter
from vibetrading.settings import RuntimeSettings
from vibetrading.sqlite_store import RuntimeStore
from vibetrading.storage_worker import StorageWorker


async def verify() -> None:
    # The isolated Docker network resolves this test-only service name.
    url = os.environ.get('MAINTENANCE_TEST_NATS_URL', '')
    if url != 'nats://maintenance-test-nats:4222':
        raise RuntimeError('This test requires the disposable maintenance-test-nats server')
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        report = []
        for domain in ('market', 'crypto'):
            directory = root / domain
            directory.mkdir()
            env = directory / '.env'
            env.write_text(f'NATS_URL={url}\nVIBETRADING_DATA_ROOT={directory}\n'
                           'NATS_MAX_BYTES=8388608\nNATS_OPERATIONS_MAX_BYTES=1048576\n'
                           'CRYPTO_NATS_MAX_BYTES=8388608\nARCHIVE_ENABLED=true\n'
                           'ARCHIVE_BACKEND=local\nSTORAGE_FLUSH_SECONDS=0.2\n'
                           'PARQUET_FLUSH_SECONDS=0.2\n'
                           + ('SQLITE_CACHE_KIB=65536\nSQLITE_WAL_AUTOCHECKPOINT_PAGES=16384\n' if domain == 'crypto' else '') +
                           f'DELIVERY_LEDGER_FORMAT={"compact" if domain == "crypto" else "legacy"}\n', encoding='utf-8')
            settings = RuntimeSettings.from_env(env)
            producer = NatsJetStreamBus(settings)
            await producer.connect(initialize=True)
            store = RuntimeStore(settings)
            writer = ParquetBatchWriter(settings, store)
            worker = StorageWorker(NatsJetStreamBus(settings), writer, domain=domain)
            original_ack = BusMessage.ack
            checked_acks = 0

            async def checked_ack(message):
                nonlocal checked_acks
                assert store.delivery_committed(message.stream_name,message.stream_generation,message.stream_sequence), 'ACK before catalogue commit'
                assert all(Path(row['path']).exists() for row in store.list_manifest())
                checked_acks += 1
                await original_ack(message)

            BusMessage.ack = checked_ack
            task = asyncio.create_task(worker.run(4))
            try:
                expected = set()
                venue = Venue.BYBIT if domain == 'crypto' else Venue.KIS
                asset = AssetClass.CRYPTO_PERP if domain == 'crypto' else AssetClass.EQUITY
                instrument = InstrumentId(venue=venue,asset_class=asset,raw_symbol='BTCUSDT' if domain == 'crypto' else 'ABC',std_symbol='BTCUSDT' if domain == 'crypto' else 'ABC')
                for sequence in range(200):
                    event = TradeEvent(instrument=instrument,venue=venue,provider=venue.value,
                                       source_ts_ns=time.time_ns(),sequence=sequence,price=Decimal('10'),quantity=Decimal('1'))
                    expected.add(event.event_id)
                    await producer.publish_market_event(event)
                maintenance = ArchiveMaintenance(settings)
                maintenance._resource_maintenance_allowed = lambda: (False, {'reason':'storage_or_io_busy'})
                deferred = await asyncio.to_thread(maintenance.run_once, True)
                assert not deferred['ran'] and maintenance._store is None
                stats = await task
                await producer._nc.flush()
                stream,durable = ('CRYPTO_DATA','crypto-parquet-storage') if domain == 'crypto' else ('MARKET_DATA','parquet-storage')
                info = await producer._js.consumer_info(stream,durable)
                assert info.num_ack_pending == 0 and info.num_pending == 0
                rows = [row for manifest in store.list_manifest() for row in pq.ParquetFile(manifest['path']).read().to_pylist()]
                assert {row['event_id'] for row in rows} == expected
                assert len(rows) == checked_acks == stats['acked'] == 200
                report.append({'domain':domain,'persisted_rows':len(rows),'checked_acks':checked_acks,
                               'nats_ack_pending':info.num_ack_pending,'nats_pending':info.num_pending,
                               'maintenance_deferred_without_sqlite':True})
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
                BusMessage.ack = original_ack
                await producer.close()
                store.close()
        print(json.dumps(report,indent=2))


if __name__ == '__main__':
    asyncio.run(verify())
