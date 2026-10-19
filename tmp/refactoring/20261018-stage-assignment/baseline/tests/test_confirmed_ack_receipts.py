import asyncio
import time
from types import SimpleNamespace as NS

import pytest

from vibetrading.bus import ConfirmedAckFloor, NatsJetStreamBus
from vibetrading.sqlite_store import RuntimeStore
from test_storage_pipeline import _settings


def consumer(created='consumer-1', floor=7, policy='explicit'):
    return NS(created=created, ack_floor=NS(stream_seq=floor), delivered=NS(stream_seq=10),
              config=NS(durable_name='crypto-parquet-storage', filter_subject='crypto.>',
                        filter_subjects=None, ack_policy=policy, deliver_policy='all', headers_only=False))


@pytest.mark.parametrize('fault', ['none', 'stream_recreated', 'consumer_recreated', 'missing_generation', 'bad_policy', 'bad_floor'])
def test_receipt_brackets_stream_and_consumer_identity(tmp_path, fault):
    class JetStream:
        def __init__(self):
            self.stream_calls = self.consumer_calls = 0

        async def stream_info(self, stream):
            self.stream_calls += 1
            created = '' if fault == 'missing_generation' else 'stream-1'
            if fault == 'stream_recreated' and self.stream_calls == 2:
                created = 'stream-2'
            return NS(created=created, state=NS(last_seq=10))

        async def consumer_info(self, stream, durable):
            self.consumer_calls += 1
            return consumer(created='consumer-2' if fault == 'consumer_recreated' and self.consumer_calls == 2 else 'consumer-1',
                            policy='none' if fault == 'bad_policy' else 'explicit',
                            floor=20 if fault == 'bad_floor' else 7)

    bus = NatsJetStreamBus(_settings(tmp_path))
    bus._js = JetStream()
    operation = bus.confirmed_ack_floor('CRYPTO_DATA', 'crypto-parquet-storage', 'crypto.>')
    if fault == 'none':
        receipt = asyncio.run(operation)
        assert receipt.sequence == 7 and receipt.generation == 'stream-1'
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(operation)


def test_confirmed_floor_never_inherits_legacy_or_previous_consumer_max(tmp_path):
    store = RuntimeStore(_settings(tmp_path, 'DELIVERY_LEDGER_FORMAT=compact\n'))
    store.record_delivery_ack_floor('CRYPTO_DATA', 'g', 999999)
    now = time.time_ns()
    for incarnation, floor, offset in [('first', 100, 0), ('second', 2, 1)]:
        store.record_confirmed_ack_floor(ConfirmedAckFloor('CRYPTO_DATA', 'g', 'crypto-parquet-storage', incarnation, floor, now + offset))
    row = store._conn.execute('SELECT consumer_generation,ack_floor FROM delivery_confirmed_floor').fetchone()
    assert tuple(row) == ('second', 2)
    store.record_confirmed_ack_floor(ConfirmedAckFloor('CRYPTO_DATA', 'g', 'crypto-parquet-storage', 'first', 1000, now))
    assert store._conn.execute('SELECT ack_floor FROM delivery_confirmed_floor').fetchone()[0] == 2
    store.close()
