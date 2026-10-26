from decimal import Decimal

import pytest

from market.hot_path import HotPathStore
from core.models import AssetClass, BookUpdateType, IndexQuoteEvent, InstrumentId, OrderBookEvent, OrderBookLevel, Venue

NOW = 1_792_281_600_000_000_000


def book(identity="s", *, update=BookUpdateType.SNAPSHOT, final=100, previous=None,
         bids=(("100", "2"), ("99", "3")), asks=(("101", "4"), ("102", "5"))):
    instrument = InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_PERP,
                              raw_symbol="BTCUSDT", std_symbol="BTCUSDT")
    return OrderBookEvent(event_id=identity, instrument=instrument, venue=Venue.BINANCE,
                          provider="binance-perp", source_ts_ns=NOW, receive_ts_ns=NOW,
                          update_type=update, depth=2, sync_generation=1,
                          final_update_id=final, previous_update_id=previous,
                          bids=[OrderBookLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in bids],
                          asks=[OrderBookLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in asks])


def test_full_delta_atomic_versions_and_old_snapshot_are_immutable():
    store = HotPathStore()
    original = book()
    store.apply_versioned(original)
    first = store.snapshot(NOW)
    key = original.instrument.key
    original.bids.clear()
    with pytest.raises((AttributeError, TypeError)):
        first.latest_book(key).bids.append(OrderBookLevel(price=Decimal(1), quantity=Decimal(1)))
    with pytest.raises(TypeError):
        first.versions[key] = 999
    delta = book("d", update=BookUpdateType.DELTA, final=101, previous=100,
                 bids=(("100", "4"), ("99", "0")), asks=(("102", "6"),))
    store.apply_versioned(delta)
    current = store.snapshot(NOW)
    assert store._crypto_books[key][0] == {Decimal(100): Decimal(4)}
    assert store._crypto_books[key][1] == {Decimal(101): Decimal(4), Decimal(102): Decimal(6)}
    assert [(x.price, x.quantity) for x in current.latest_book(key).bids] == [(Decimal(100), Decimal(4))]
    assert [(x.price, x.quantity) for x in first.latest_book(key).bids] == [(Decimal(100), Decimal(2)), (Decimal(99), Decimal(3))]
    assert (first.apply_seq, current.apply_seq) == (1, 2)
    assert current.book_versions[key] == first.book_versions[key] + 1


def test_duplicate_does_not_advance_but_native_gap_removes_old_projection():
    store = HotPathStore()
    event = book()
    store.apply(event)
    assert store.apply_versioned(event).duplicate
    assert store.apply_seq == 1
    store.apply(book("d", update=BookUpdateType.DELTA, final=101, previous=100))
    result = store.apply_versioned(book("bad", update=BookUpdateType.DELTA, final=103, previous=99))
    assert not result.applied and result.reason == "native_book_invalid"
    assert store.latest_book(event.instrument.key) is None
    assert store.snapshot(NOW).quality[event.instrument.key] == "INVALID"


def test_native_depth_and_existing_ring_limits_are_preserved():
    store = HotPathStore()
    event = book(bids=tuple((str(10000-i), "1") for i in range(1005)),
                 asks=tuple((str(10001+i), "1") for i in range(1005)))
    store.apply(event)
    key = event.instrument.key
    assert len(store._crypto_books[key][0]) == len(store._crypto_books[key][1]) == 1000
    projection = store.snapshot(NOW).latest_book(key)
    assert len(projection.bids) == len(projection.asks) == 50
    assert store._books[key].maxlen == 512
    assert projection.bids[0].price == Decimal(10000)


def test_quiet_expiry_changes_version_once_without_mutating_snapshot():
    store = HotPathStore()
    event = book()
    store.apply(event)
    before = store.snapshot(NOW)
    after = store.snapshot(NOW + 1_000_000_001)
    repeated = store.snapshot(NOW + 2_000_000_000)
    assert before.latest_book(event.instrument.key) is not None
    assert after.latest_book(event.instrument.key) is None
    assert after.quality[event.instrument.key] == "STALE"
    assert after.clock_version == repeated.clock_version == before.clock_version + 1


def test_index_metadata_is_frozen_recursively():
    instrument = InstrumentId(venue=Venue.KIS, asset_class=AssetClass.INDEX,
                              raw_symbol="2001", std_symbol="KOSPI200")
    event = IndexQuoteEvent(instrument=instrument, venue=Venue.KIS, provider="kis",
                           source_ts_ns=NOW, receive_ts_ns=NOW, value=Decimal("100"),
                           metadata={"nested": {"values": [1, 2]}})
    store = HotPathStore()
    store.apply(event)
    value = store.snapshot(NOW).latest_index_quote(instrument.key)
    event.metadata["nested"]["values"].append(3)
    assert value.metadata["nested"]["values"] == (1, 2)
    with pytest.raises(TypeError):
        value.metadata["nested"]["new"] = 1


def test_raw_only_book_does_not_create_a_new_version():
    store = HotPathStore()
    event = book()
    store.apply(event)
    before = store.snapshot(NOW)
    store.apply(book("raw").model_copy(update={"fast": False}))
    after = store.snapshot(NOW)
    assert after.versions == before.versions
    assert after.book_versions == before.book_versions
    assert after.apply_seq == before.apply_seq + 1


def test_conflicting_content_under_one_event_id_invalidates_instead_of_dedup():
    store = HotPathStore()
    event = book()
    store.apply(event)
    altered = event.model_copy(update={"raw_payload": "different-wire-frame"})
    result = store.apply_versioned(altered)
    assert not result.applied and not result.duplicate
    assert result.reason == "event_id_conflict"
    assert store.latest_book(event.instrument.key) is None
    assert store.snapshot(NOW).quality[event.instrument.key] == "EVENT_ID_CONFLICT"
    assert len(store._fingerprints) <= store._seen.max_entries
    store.apply(book("fresh-snapshot"))
    assert store.snapshot(NOW).latest_book(event.instrument.key) is None


def test_freeze_failure_does_not_consume_event_identity():
    instrument = InstrumentId(venue=Venue.KIS, asset_class=AssetClass.INDEX,
                              raw_symbol="2001", std_symbol="KOSPI200")
    event = IndexQuoteEvent(event_id="retry", instrument=instrument, venue=Venue.KIS, provider="kis",
                           source_ts_ns=NOW, receive_ts_ns=NOW, value=Decimal(100), metadata={"bad": object()})
    store = HotPathStore()
    with pytest.raises(TypeError, match="Unsupported mutable"):
        store.apply_versioned(event)
    assert store.apply_seq == 0 and "retry" not in store._seen
    assert store.apply_versioned(event.model_copy(update={"metadata": {"corrected": True}})).applied


def test_model_copy_of_internal_book_is_refrozen_before_callback_sharing():
    store = HotPathStore()
    event = book()
    store.apply(event)
    copied = store.snapshot(NOW).latest_book(event.instrument.key).model_copy(
        update={"event_id": "copied", "bids": list(event.bids)})
    result = store.apply_versioned(copied)
    copied.bids.clear()
    assert isinstance(result.event.bids, tuple) and len(result.event.bids) == 2
