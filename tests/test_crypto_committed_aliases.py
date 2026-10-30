import asyncio
import sqlite3

import pytest

from runtime.bus import BusMessage, InMemoryEventBus
from storage.parquet_store import ParquetBatchWriter
from storage.sqlite_store import RuntimeStore
from storage.storage_worker import StorageWorker
from test_storage_pipeline import _event, _settings


@pytest.fixture(params=['btree', 'immutable-runs-v1'])
def format_env(request):
    return f'DELIVERY_LEDGER_FORMAT=compact\nCRYPTO_EVENT_INDEX_FORMAT={request.param}\n'


def test_committed_event_alias_is_durable_before_ack(tmp_path, format_env):
    settings = _settings(tmp_path, format_env)
    store = RuntimeStore(settings)
    writer = ParquetBatchWriter(settings, store)
    event = _event()
    writer.add(event, stream_name='CRYPTO_DATA', stream_generation='g', stream_sequence=1, event_id=event.event_id)
    writer.flush_all()
    checked = []

    class Raw:
        async def ack(self):
            assert store.delivery_committed('CRYPTO_DATA', 'g', 2)
            checked.append(True)

    bus = InMemoryEventBus()
    bus.messages.append(BusMessage(event.model_dump(mode='json'), 'crypto.trade.test', event.event_id,
                                   stream_name='CRYPTO_DATA', stream_generation='g', stream_sequence=2, _raw=Raw()))
    asyncio.run(StorageWorker(bus, writer, domain='crypto').run(0.1))
    assert checked == [True]
    assert len(store.list_manifest()) == 1
    store.close()
    reopened = RuntimeStore(settings)
    assert reopened.delivery_committed('CRYPTO_DATA', 'g', 2)
    reopened.close()


def test_alias_batch_rolls_back_if_any_canonical_event_is_missing(tmp_path, format_env):
    settings = _settings(tmp_path, format_env)
    store = RuntimeStore(settings)
    writer = ParquetBatchWriter(settings, store)
    event = _event()
    writer.add(event, stream_name='CRYPTO_DATA', stream_generation='g', stream_sequence=1, event_id=event.event_id)
    writer.flush_all()
    with pytest.raises(RuntimeError, match='canonical manifest'):
        store.commit_event_aliases([('CRYPTO_DATA', 'g', 2, event.event_id), ('CRYPTO_DATA', 'g', 3, 'missing')])
    assert not store.delivery_committed('CRYPTO_DATA', 'g', 2)
    store.commit_event_aliases([('CRYPTO_DATA', 'g', 2, event.event_id)] * 2)
    assert store.delivery_committed('CRYPTO_DATA', 'g', 2)
    with pytest.raises(RuntimeError):
        store.commit_event_aliases([('CRYPTO_DATA', 'g', 2, 'different')])
    store.close()


def test_failed_alias_transaction_never_acks_duplicate(tmp_path, format_env):
    settings = _settings(tmp_path, format_env)
    store = RuntimeStore(settings)
    writer = ParquetBatchWriter(settings, store)
    event = _event()
    writer.add(event, stream_name='CRYPTO_DATA', stream_generation='g', stream_sequence=1, event_id=event.event_id)
    writer.flush_all()
    store._conn.execute("CREATE TRIGGER reject_alias BEFORE INSERT ON parquet_delivery_compact "
                        "WHEN NEW.stream_sequence=2 BEGIN SELECT RAISE(ABORT,'injected alias failure'); END")
    acked = []

    class Raw:
        async def ack(self):
            acked.append(True)

    bus = InMemoryEventBus()
    bus.messages.append(BusMessage(event.model_dump(mode='json'), 'crypto.trade.test', event.event_id,
                                   stream_name='CRYPTO_DATA', stream_generation='g', stream_sequence=2, _raw=Raw()))
    with pytest.raises(sqlite3.IntegrityError, match='injected alias failure'):
        asyncio.run(StorageWorker(bus, writer, domain='crypto').run(0.1))
    assert not acked
    assert not store.delivery_committed('CRYPTO_DATA', 'g', 2)
    store.close()
