"""Full market-state codec oracles; engine recovery/LIVE is deliberately absent."""
from decimal import Decimal
import hashlib
import json
import os

import pytest

from vibetrading.dedup import BoundedDedup
from vibetrading.feed_recovery import EngineCursor
from vibetrading.hot_path import HotPathStore, PreparedHistory
from vibetrading.market_checkpoint import CheckpointCodec, CheckpointError, CheckpointProducer
from vibetrading.models import (AssetClass, Bar1mEvent, BookUpdateType, IndexQuoteEvent,
                               InstrumentId, OrderBookEvent, OrderBookLevel, TradeEvent, Venue)


NOW = 1_792_281_600_000_000_000
MONOTONIC = 100_000_000_000
CONFIG = hashlib.sha256(b'vn06-fixed-offline-config').hexdigest()
VECTOR = (CheckpointProducer(EngineCursor('collector', 'run', 12),
                             hashlib.sha256(b'exact-producer-record-12').hexdigest(), 'VALID'),)


def instrument(symbol='BTCUSDT', *, venue=Venue.BINANCE, asset=AssetClass.CRYPTO_PERP):
    return InstrumentId(venue=venue, asset_class=asset, raw_symbol=symbol, std_symbol=symbol)


def book(identity='native-snapshot', *, final=100, previous=None, update=BookUpdateType.SNAPSHOT,
         bids=None, asks=None):
    return OrderBookEvent(event_id=identity, instrument=instrument(), venue=Venue.BINANCE,
        provider='binance-perp', source_ts_ns=None, receive_ts_ns=NOW, wallclock_ts_ns=NOW,
        receive_monotonic_ns=91, sequence='000100', native_event_id='00100',
        update_type=update, depth=80, sync_generation=7, final_update_id=final,
        previous_update_id=previous, raw_payload='{"wire":"원문","price":"0.123456789012345678"}',
        bids=[OrderBookLevel(price=Decimal(1000-index), quantity=Decimal('2.000000000000000001'))
              for index in range(80)] if bids is None else [OrderBookLevel(price=p, quantity=q) for p,q in bids],
        asks=[OrderBookLevel(price=Decimal(1001+index), quantity=Decimal('3.000000000000000002'))
              for index in range(80)] if asks is None else [OrderBookLevel(price=p, quantity=q) for p,q in asks])


def trade(index, symbol='BTCUSDT'):
    return TradeEvent(event_id=f'{symbol}-trade-{index}', instrument=instrument(symbol), venue=Venue.BINANCE,
        provider='binance-perp', source_ts_ns=None, receive_ts_ns=NOW, wallclock_ts_ns=NOW,
        receive_monotonic_ns=83+index, sequence=f'000{index}', trade_id=f'wire-{index}',
        price=Decimal(f'0.12345678901234567{index}'), quantity=Decimal('1.000000000000000001'))


def bar(index):
    end = NOW-(3-index)*60_000_000_000
    return Bar1mEvent(event_id=f'bar-{index}', instrument=instrument('069500', venue=Venue.KIWOOM, asset=AssetClass.ETF),
        venue=Venue.KIWOOM, provider='kiwoom', source_ts_ns=end, wallclock_ts_ns=end+5,
        receive_ts_ns=end+5, open_ts_ns=end-60_000_000_000, close_ts_ns=end,
        open=Decimal('99.000000000000000001'), high=Decimal(101), low=Decimal(98),
        close=Decimal(100+index), volume=Decimal('10.000000000000000001'))


def configured_store():
    store = HotPathStore(max_trades=3, max_books=3, max_index_quotes=3)
    store.history = PreparedHistory(max_rows=3, max_feeds=8, max_bytes=100_000)
    store._seen = BoundedDedup(max_entries=32, ttl_seconds=10)
    return store


def codec(store, **updates):
    values = dict(max_bytes=1_000_000, max_event_bytes=100_000, max_producers=4,
                  config_sha256=CONFIG, capacity=store.checkpoint_capacity())
    return CheckpointCodec(**(values | updates))


def encode(serializer, store):
    return serializer.encode(store, producers=VECTOR, capture_epoch_ns=NOW, capture_monotonic_ns=MONOTONIC)


def decode(serializer, payload, *, elapsed=0, monotonic=1_000_000_000):
    return serializer.decode(payload, restore_epoch_ns=NOW+elapsed, restore_monotonic_ns=monotonic)


def rewrite(payload, change):
    document = json.loads(payload)
    change(document['body']['state'])
    canonical = lambda value:json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    document['sha256'] = hashlib.sha256(canonical(document['body'])).hexdigest()
    return canonical(document)


@pytest.fixture
def frozen_clock(monkeypatch):
    clock = [MONOTONIC/1_000_000_000]
    monkeypatch.setattr('vibetrading.dedup.monotonic', lambda:clock[0])
    return clock


def test_full_state_roundtrip_preserves_rings_native_depth_history_metadata_and_cursor(frozen_clock):
    store = configured_store()
    source = book()
    store.apply(source)
    for index in range(4):store.apply(trade(index))
    for index in range(3):store.apply(bar(index))
    index = IndexQuoteEvent(event_id='index', instrument=instrument('KOSPI200', venue=Venue.KIS, asset=AssetClass.INDEX),
        venue=Venue.KIS, provider='kis', source_ts_ns=None, receive_ts_ns=NOW, wallclock_ts_ns=NOW,
        value=Decimal('123.123456789012345678'), metadata={
            '$decimal':'ordinary-key', 'nested':[Decimal('1.000000000000000001'), {'array':['decimal','plain']}]})
    store.apply(index)
    store.history.invalidate(bar(0).instrument.key, 'REVISION_UNVERIFIED')
    # LRU order differs from initial insertion order and must survive exactly.
    assert store.apply_versioned(trade(1)).duplicate
    serializer = codec(store)
    payload = encode(serializer, store)
    original_state = store.export_checkpoint_state(capture_monotonic_ns=MONOTONIC,
        max_event_bytes=serializer.max_event_bytes, max_bytes=serializer.max_bytes)
    checkpoint = decode(serializer, payload, monotonic=MONOTONIC)
    restored = checkpoint.store
    assert restored is not store and restored.history is not store.history
    assert checkpoint.producers == VECTOR and checkpoint.config_sha256 == CONFIG
    assert checkpoint.captured_epoch_ns == NOW
    assert restored.export_checkpoint_state(capture_monotonic_ns=MONOTONIC,
        max_event_bytes=serializer.max_event_bytes, max_bytes=serializer.max_bytes) | {'dedup': []} == original_state | {'dedup': []}
    assert (restored.engine_incarnation, restored.apply_seq, restored.clock_version) == (
        store.engine_incarnation, store.apply_seq, store.clock_version)
    assert [event.event_id for event in restored._trades[source.instrument.key]] == [
        'BTCUSDT-trade-1', 'BTCUSDT-trade-2', 'BTCUSDT-trade-3']
    assert len(restored._crypto_books[source.instrument.key][0]) == 80
    assert len(restored._books[source.instrument.key][-1].bids) == 50
    assert source.instrument.key in restored._crypto_first
    assert list(restored._fingerprints) == list(store._fingerprints)
    assert list(restored._seen._items) == list(store._seen._items)
    quote = restored.latest_index_quote(index.instrument.key)
    assert quote.metadata['nested'][0] == Decimal('1.000000000000000001')
    assert quote.metadata['$decimal'] == 'ordinary-key'
    with pytest.raises(TypeError):quote.metadata['nested'][1]['new'] = 'mutation'
    assert quote.source_ts_ns is None and quote.receive_ts_ns == NOW
    assert restored._books[source.instrument.key][-1].raw_payload == source.raw_payload
    assert restored._books[source.instrument.key][-1].sequence == '000100'
    assert restored.history.byte_size == store.history.byte_size
    assert restored.history._sizes == store.history._sizes
    assert restored.history._quality == store.history._quality
    assert encode(serializer, store) == payload  # Capture never mutates the owner.


def test_restored_native_accumulator_accepts_first_bridge_and_deletes_hidden_depth(frozen_clock):
    store = configured_store()
    source = book()
    store.apply(source)
    restored = decode(codec(store), encode(codec(store), store)).store
    frozen_clock[0] = 1.0
    # Previous 99 bridges snapshot100 on the first delta only; level930 is
    # outside the 50-level projection and proves full accumulator retention.
    delta = book('first-delta', final=101, previous=99, update=BookUpdateType.DELTA,
                 bids=[('930', '0'), ('1000', '8.000000000000000001')], asks=[])
    result = restored.apply_versioned(delta)
    assert result.applied and result.apply_seq == store.apply_seq+1
    bids, asks, generation, final = restored._crypto_books[source.instrument.key]
    assert Decimal(930) not in bids and len(bids) == 79 and len(asks) == 80
    assert bids[Decimal(1000)] == Decimal('8.000000000000000001')
    assert (generation, final) == (7,101)
    assert source.instrument.key not in restored._crypto_first
    bad = book('bad-second-bridge', final=102, previous=99, update=BookUpdateType.DELTA,
               bids=[('1000', '9')], asks=[])
    assert restored.apply_versioned(bad).reason == 'native_book_invalid'
    assert restored.snapshot(NOW).latest_book(source.instrument.key) is None


def test_dedup_rebases_remaining_ttl_without_making_restored_records_younger(frozen_clock):
    store = configured_store()
    event = trade(0)
    store.apply(event)
    serializer = codec(store)
    restored = decode(serializer, encode(serializer, store), elapsed=2_000_000_000, monotonic=1_000_000_000).store
    stamp = restored._seen._items[event.event_id]
    assert stamp < 0  # New boot's monotonic domain may begin below old uptime.
    frozen_clock[0] = 8.0
    assert restored.apply_versioned(event).duplicate
    assert restored.apply_seq == store.apply_seq
    frozen_clock[0] = 10.0
    assert restored.apply_versioned(event).applied
    assert restored.apply_seq == store.apply_seq+1
    with pytest.raises(CheckpointError):decode(serializer, encode(serializer, store), elapsed=10_000_000_000)


def test_history_availability_quality_latch_and_stale_books_are_preserved(frozen_clock):
    store = configured_store()
    native = book()
    store.apply(native)
    future = bar(2).model_copy(update={'receive_ts_ns':NOW+500_000_000})
    store.load_history(future.instrument.key, [future])
    store.history.register('prepared-but-empty', 2)
    serializer = codec(store)
    restored = decode(serializer, encode(serializer, store)).store
    assert restored.snapshot(NOW).closed_bars(future.instrument.key) == ()
    assert restored.snapshot(NOW+500_000_000).closed_bars(future.instrument.key) == (future,)
    assert restored.history._limits['prepared-but-empty'] == 2
    expired = decode(serializer, encode(serializer, store), elapsed=1_000_000_001).store
    snapshot = expired.snapshot(NOW+1_000_000_001)
    assert snapshot.latest_book(native.instrument.key) is None
    assert snapshot.quality[native.instrument.key] == 'STALE'
    assert snapshot.clock_version == store.clock_version+1
    assert expired._books[native.instrument.key][-1].receive_ts_ns == NOW
    assert len(expired._crypto_books[native.instrument.key][0]) == 80


def test_identity_conflict_latch_survives_decode_and_source_is_not_repaired(frozen_clock):
    store = configured_store()
    original = book()
    store.apply(original)
    assert store.apply_versioned(original.model_copy(update={'raw_payload':'conflicting'})).reason == 'event_id_conflict'
    serializer = codec(store)
    restored = decode(serializer, encode(serializer, store)).store
    frozen_clock[0] = 1.0
    assert restored.apply_versioned(book('new-snapshot')).reason == 'identity_conflict_latched'
    assert restored.snapshot(NOW).quality[original.instrument.key] == 'EVENT_ID_CONFLICT'
    assert original.instrument.key in store._identity_conflicts
    assert not restored._crypto_books and not restored._books


@pytest.mark.parametrize('damage', ['projection', 'native_cursor', 'native_duplicate', 'history_provider',
                                   'event_timestamp', 'event_identity', 'event_bool', 'fingerprint', 'dedup_order'])
def test_checksum_valid_but_incoherent_state_is_rejected_without_source_mutation(frozen_clock, damage):
    store = configured_store()
    native = book()
    store.apply(native)
    store.apply(trade(0));store.apply(trade(1));store.apply(bar(0))
    serializer = codec(store)
    payload = encode(serializer, store)
    key = native.instrument.key
    def corrupt(state):
        if damage == 'projection':state['books'][key][-1]['bids'][0]['quantity'] = '999'
        elif damage == 'native_cursor':state['crypto_books'][key]['final_update_id'] += 1
        elif damage == 'native_duplicate':state['crypto_books'][key]['bids'].append(state['crypto_books'][key]['bids'][0])
        elif damage == 'history_provider':state['history']['providers'][bar(0).instrument.key] = 'other'
        elif damage == 'event_timestamp':state['trades'][key][0].pop('receive_ts_ns')
        elif damage == 'event_identity':state['trades'][key][0]['event_id'] = ''
        elif damage == 'event_bool':state['trades'][key][0]['receive_monotonic_ns'] = True
        elif damage == 'fingerprint':state['fingerprints'][1][1] = '0'*64
        elif damage == 'dedup_order':state['dedup'].reverse()
    with pytest.raises(CheckpointError):decode(serializer, rewrite(payload, corrupt))
    assert encode(serializer, store) == payload


def test_direct_state_restore_rejects_nested_and_oversized_inputs(frozen_clock):
    store = configured_store()
    serializer = codec(store)
    payload = encode(serializer, store)
    state = json.loads(payload)['body']['state']
    deep = []
    for _ in range(80):deep = [deep]
    state['unknown'] = deep
    with pytest.raises(CheckpointError):
        HotPathStore.restore_checkpoint_state(state, expected_capacity=serializer.capacity,
            elapsed_ns=0, restore_monotonic_ns=1, max_event_bytes=100_000, max_bytes=1_000_000)
    state = json.loads(payload)['body']['state']
    state['x'*10_000] = None
    with pytest.raises(CheckpointError):
        HotPathStore.restore_checkpoint_state(state, expected_capacity=serializer.capacity,
            elapsed_ns=0, restore_monotonic_ns=1, max_event_bytes=100_000, max_bytes=1000)


def test_individually_bounded_events_exceed_aggregate_encode_budget_without_source_mutation(frozen_clock):
    store = configured_store()
    events = [trade(index) for index in range(3)]
    for event in events:store.apply(event)
    event_sizes = [len(event.model_dump_json().encode('utf-8')) for event in events]
    assert all(size < 2000 for size in event_sizes)
    assert sum(event_sizes) > 2400
    generous = codec(store)
    before = encode(generous, store)
    bounded = codec(store, max_bytes=2400, max_event_bytes=2000)
    with pytest.raises(CheckpointError):encode(bounded, store)
    assert encode(generous, store) == before


@pytest.mark.skipif(os.name == 'nt', reason='strict directory fsync is unavailable on Windows')
@pytest.mark.parametrize('failed_sync', [1,2,3])
def test_atomic_write_failure_does_not_report_success_or_leave_temporary_files(tmp_path, monkeypatch, failed_sync):
    store = configured_store()
    serializer = codec(store)
    payload = encode(serializer, store)
    path = tmp_path/'checkpoint.json'
    old = b'previous-checkpoint'
    path.write_bytes(old)
    original = os.fsync
    calls = []
    def fail(fd):
        calls.append(fd)
        if len(calls) == failed_sync:raise OSError('injected fsync failure')
        original(fd)
    monkeypatch.setattr('vibetrading.market_checkpoint.os.fsync', fail)
    with pytest.raises(OSError):serializer.write(path, payload)
    assert path.read_bytes() == (payload if failed_sync == 3 else old)
    assert sorted(item.name for item in tmp_path.iterdir()) == ['checkpoint.json']


@pytest.mark.skipif(os.name == 'nt', reason='strict directory fsync is unavailable on Windows')
def test_atomic_write_readback_and_read_cap(tmp_path):
    store = configured_store()
    serializer = codec(store)
    payload = encode(serializer, store)
    path = tmp_path/'checkpoint.json'
    serializer.write(path, payload)
    assert path.read_bytes() == payload
    assert serializer.read(path, restore_epoch_ns=NOW, restore_monotonic_ns=1).store.apply_seq == 0
    path.write_bytes(b'x'*(serializer.max_bytes+1))
    with pytest.raises(CheckpointError):serializer.read(path, restore_epoch_ns=NOW, restore_monotonic_ns=1)
