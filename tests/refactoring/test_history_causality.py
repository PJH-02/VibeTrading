from decimal import Decimal

import pytest

from vibetrading.hot_path import HistoryNotPrepared, HotPathStore, PreparedHistory, StrategyDataView
from core.models import AssetClass, Bar1mEvent, InstrumentId, Venue

BASE = 1_792_281_600_000_000_000
MINUTE = 60_000_000_000


def bar(index, *, close="100", delay=5_000_000_000):
    instrument = InstrumentId(venue=Venue.KIWOOM, asset_class=AssetClass.ETF,
                              raw_symbol="069500", std_symbol="069500")
    end = BASE + (index+1)*MINUTE
    return Bar1mEvent(event_id=f"bar-{index}", instrument=instrument, venue=Venue.KIWOOM,
                      provider="kiwoom", source_ts_ns=end, receive_ts_ns=end+delay,
                      open_ts_ns=end-MINUTE, close_ts_ns=end, open=Decimal(99),
                      high=Decimal(101), low=Decimal(98), close=Decimal(close), volume=Decimal(10))


def test_bar_is_visible_only_after_actual_availability():
    event = bar(0)
    store = HotPathStore()
    store.load_history(event.instrument.key, [event])
    assert store.snapshot(event.close_ts_ns+4_000_000_000).closed_bars(event.instrument.key) == ()
    assert store.snapshot(event.receive_ts_ns).closed_bars(event.instrument.key) == (event,)


def test_future_close_is_hidden_even_when_receive_clock_is_earlier():
    event = bar(0).model_copy(update={"receive_ts_ns": BASE})
    store = HotPathStore()
    store.load_history(event.instrument.key, [event])
    assert store.snapshot(BASE).closed_bars(event.instrument.key) == ()
    assert store.snapshot(event.close_ts_ns).closed_bars(event.instrument.key) == (event,)


def test_live_bar_append_dedup_and_revision_latch():
    store = HotPathStore()
    event = bar(0)
    store.apply(event)
    store.apply(event.model_copy(update={"event_id": "duplicate-content"}))
    assert len(store.snapshot(event.receive_ts_ns).closed_bars(event.instrument.key)) == 1
    conflicting = event.model_copy(update={"event_id": "revision", "close": Decimal(102)})
    store.apply(conflicting)
    snapshot = store.snapshot(event.receive_ts_ns)
    assert snapshot.history_quality[event.instrument.key] == "REVISION_UNVERIFIED"
    assert snapshot.quality[event.instrument.key] == "REVISION_UNVERIFIED"
    assert snapshot.closed_bars(event.instrument.key)[0].close == Decimal(100)


def test_history_result_mutation_cannot_change_snapshot_or_do_reader_io():
    store = HotPathStore()
    event = bar(0)
    store.apply(event)
    class ForbiddenReader:
        def history(self, **kwargs):
            raise AssertionError("callback attempted disk I/O")
    view = StrategyDataView(store.snapshot(event.receive_ts_ns), ForbiddenReader())
    rows = view.history(event.instrument.key, "bar_1m")
    rows[0]["close"] = "broken"
    assert view.history(event.instrument.key, "bar_1m")[0]["close"] == "100"
    with pytest.raises(HistoryNotPrepared):
        view.history(event.instrument.key, "trade")


def test_flattened_preload_validates_identity_and_missing_availability():
    event = bar(0)
    row = event.model_dump(mode="json")
    instrument = row.pop("instrument")
    row.update({"instrument_"+key: value for key, value in instrument.items()})
    row.update(nats_stream="MARKET_DATA", nats_stream_sequence=1, sampled_at_ns=None)
    history = PreparedHistory()
    history.load(event.instrument.key, [row])
    assert history.snapshot(event.receive_ts_ns)[0][event.instrument.key] == (event,)
    del row["receive_ts_ns"]
    missing = PreparedHistory()
    missing.load(event.instrument.key, [row])
    assert missing.snapshot(event.receive_ts_ns)[1][event.instrument.key] == "AVAILABILITY_UNVERIFIED"
    with pytest.raises(ValueError, match="identity"):
        history.load("wrong-key", [event])


def test_prepared_history_has_record_and_byte_bounds():
    history = PreparedHistory(max_rows=2, max_feeds=1, max_bytes=1_000_000)
    events = [bar(i) for i in range(3)]
    for event in events:
        history.add(event)
    rows, _ = history.snapshot(events[-1].receive_ts_ns)
    assert rows[events[0].instrument.key] == tuple(events[1:])
    assert 0 < history.byte_size <= history.max_bytes
    with pytest.raises(ValueError, match="feed limit"):
        history.register("second")
    tiny = PreparedHistory(max_bytes=1)
    assert not tiny.add(events[0])
    assert tiny.snapshot(events[0].receive_ts_ns)[1][events[0].instrument.key] == "HISTORY_OVERFLOW"
