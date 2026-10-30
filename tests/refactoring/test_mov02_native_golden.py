import asyncio
import json
from decimal import Decimal
from unittest.mock import patch
import pytest
from core.models import BookUpdateType, EventType, Venue
from feeds.providers import create_perp_collector
RECV = 1_700_000_123_456_789_012
MONO = 9_876_543_210

class CaptureBus:
    def __init__(self):
        self.events = []

    async def publish_market_event(self, event):
        self.events.append(event)


def _patched():
    return patch.multiple("time", time_ns=lambda: RECV, monotonic_ns=lambda: MONO)


def test_bitget_snapshot_delta_then_previous_id_gap():
    async def scenario():
        bus = CaptureBus()
        collector = create_perp_collector(Venue.BITGET, ("BTCUSDT",))
        collector.connection_id = "conn-bitget"
        snap_raw = '{"action":"snapshot","arg":{"topic":"books","symbol":"BTCUSDT"}}'
        with _patched():
            await collector._book(bus, "BTCUSDT", 1_700_000_000_123, [["64000.10", "1.2345"]],
                                  [["64000.20", "2.5"]], BookUpdateType.SNAPSHOT, 100,
                                  channel="books", raw_payload=snap_raw)
        snap = bus.events[0]
        assert (snap.event_type, snap.update_type) == (EventType.ORDER_BOOK, BookUpdateType.SNAPSHOT)
        assert snap.source_ts_ns == 1_700_000_000_123_000_000
        assert (snap.receive_ts_ns, snap.receive_monotonic_ns) == (RECV, MONO)
        assert (snap.connection_id, snap.receive_sequence) == ("conn-bitget", 1)
        assert (snap.instrument.raw_symbol, snap.instrument.std_symbol) == ("BTCUSDT", "BTCUSDT")
        assert (snap.final_update_id, snap.previous_update_id, snap.first_update_id) == (100, None, None)
        assert (snap.sync_generation, snap.channel, snap.raw_payload) == (1, "books", snap_raw)
        assert (snap.bids[0].price, snap.bids[0].quantity) == (Decimal("64000.10"), Decimal("1.2345"))
        assert (snap.asks[0].price, snap.asks[0].quantity) == (Decimal("64000.20"), Decimal("2.5"))
        delta_raw = '{"action":"update","arg":{"topic":"books","symbol":"BTCUSDT"}}'
        with _patched():
            await collector._book(bus, "BTCUSDT", 1_700_000_000_124, [["64000.11", "1.5"]],
                                  [], BookUpdateType.DELTA, 101, previous=100,
                                  channel="books", raw_payload=delta_raw)
        delta = bus.events[1]
        assert delta.update_type == BookUpdateType.DELTA
        assert (delta.final_update_id, delta.previous_update_id) == (101, 100)
        assert (delta.sync_generation, delta.raw_payload, delta.receive_sequence) == (1, delta_raw, 2)
        assert (delta.bids[0].price, delta.asks) == (Decimal("64000.11"), [])
        gap_raw = '{"action":"update","arg":{"topic":"books","symbol":"BTCUSDT"}}'
        with _patched(), pytest.raises(RuntimeError, match="book sequence gap"):
            await collector._book(bus, "BTCUSDT", 1_700_000_000_125, [["64000.09", "1"]],
                                  [], BookUpdateType.DELTA, 103, previous=99,
                                  channel="books", raw_payload=gap_raw)
        rejected, gap = bus.events[-2], bus.events[-1]
        assert (rejected.final_update_id, rejected.previous_update_id, rejected.sync_generation) == (103, 99, 0)
        assert rejected.raw_payload == gap_raw
        assert (gap.event_type, gap.reason, gap.resync_status) == (
            EventType.DATA_GAP, "book sequence gap previous=99 last=101", "book_invalid_reconnect")
        assert "BTCUSDT" not in collector.last_book
        assert collector.book_generation["BTCUSDT"] == 2
    asyncio.run(scenario())

def test_arcus_microseconds_native_sequence_and_raw_frame():
    async def scenario():
        bus = CaptureBus()
        collector = create_perp_collector(Venue.ARCUS, ("BTCUSDT",))
        collector.connection_id = "conn-arcus"
        raw = json.dumps({"type": "subscribed", "channel": "l2OrderbookUpdates", "id": "BTC-USD",
                          "contents": {"timestamp": 1_700_000_000_123_456, "lastSequenceId": 500,
                                       "bids": [["64000.123456789", "0.000123456789"]],
                                       "asks": [["64000.987654321", "2.000000001"]]}}, separators=(",", ":"))
        with _patched():
            await collector._arcus_message(bus, json.loads(raw), raw)
        event = bus.events[0]
        assert event.update_type == BookUpdateType.SNAPSHOT
        assert (event.instrument.raw_symbol, event.instrument.std_symbol) == ("BTC-USD", "BTCUSDT")
        assert event.source_ts_ns == 1_700_000_000_123_456_000
        assert (event.receive_ts_ns, event.receive_monotonic_ns, event.receive_sequence) == (RECV, MONO, 1)
        assert (event.final_update_id, event.previous_update_id, event.sync_generation) == (500, None, 1)
        assert (event.channel, event.raw_payload) == ("l2OrderbookUpdates", raw)
        assert (event.bids[0].price, event.bids[0].quantity) == (
            Decimal("64000.123456789"), Decimal("0.000123456789"))
        assert (event.asks[0].price, event.asks[0].quantity) == (
            Decimal("64000.987654321"), Decimal("2.000000001"))
        delta_raw = json.dumps({"type": "update", "channel": "l2OrderbookUpdates", "id": "BTC-USD",
                                "contents": {"timestamp": 1_700_000_000_223_456, "lastSequenceId": 501,
                                             "bids": [["64000.223456789", "3.000000003"]],
                                             "asks": []}}, separators=(",", ":"))
        with _patched():
            await collector._arcus_message(bus, json.loads(delta_raw), delta_raw)
        delta = bus.events[1]
        assert delta.update_type == BookUpdateType.DELTA
        assert delta.source_ts_ns == 1_700_000_000_223_456_000
        assert (delta.final_update_id, delta.previous_update_id, delta.sync_generation) == (501, None, 1)
        assert (delta.receive_sequence, delta.raw_payload) == (2, delta_raw)
        assert (delta.bids[0].price, delta.bids[0].quantity) == (
            Decimal("64000.223456789"), Decimal("3.000000003"))
    asyncio.run(scenario())

def test_grvt_nanoseconds_ack_baseline_and_decimal_precision():
    async def scenario():
        bus = CaptureBus()
        collector = create_perp_collector(Venue.GRVT, ("BTCUSDT",))
        collector.connection_id = "conn-grvt"
        ack_raw = json.dumps({"jsonrpc": "2.0", "result": {"stream": "v1.book.d",
                              "subs": ["BTC_USDT_Perp@100"],
                              "first_sequence_number": [12345]}}, separators=(",", ":"))
        await collector._grvt_message(bus, json.loads(ack_raw), ack_raw)
        assert collector._grvt_baselines == {"BTCUSDT": 12344}
        assert bus.events == []

        snap_raw = json.dumps({"stream": "v1.book.d", "sequence_number": 0,
                               "feed": {"instrument": "BTC_USDT_Perp",
                                        "event_time": 1_700_000_000_123_456_789,
                                        "bids": [{"price": "64000.123456789012345",
                                                  "size": "0.000000000000001"}],
                                        "asks": [{"price": "64000.987654321098765",
                                                  "size": "2.100000000000001"}]}}, separators=(",", ":"))
        with _patched():
            await collector._grvt_message(bus, json.loads(snap_raw), snap_raw)
        snapshot = bus.events[0]
        assert snapshot.update_type == BookUpdateType.SNAPSHOT
        assert snapshot.instrument.raw_symbol == "BTC_USDT_Perp"
        assert snapshot.source_ts_ns == 1_700_000_000_123_456_789
        assert (snapshot.final_update_id, snapshot.previous_update_id, snapshot.sync_generation) == (12344, None, 1)
        assert (snapshot.receive_sequence, snapshot.raw_payload) == (1, snap_raw)
        assert (snapshot.bids[0].price, snapshot.bids[0].quantity) == (
            Decimal("64000.123456789012345"), Decimal("0.000000000000001"))
        assert snapshot.asks[0].quantity == Decimal("2.100000000000001")
        live_raw = json.dumps({"stream": "v1.book.d", "sequence_number": 12345,
                               "prev_sequence_number": 12344,
                               "feed": {"instrument": "BTC_USDT_Perp",
                                        "event_time": 1_700_000_000_223_456_789,
                                        "bids": [{"price": "64000.223456789012345",
                                                  "size": "0.000000000000002"}],
                                        "asks": []}}, separators=(",", ":"))
        with _patched():
            await collector._grvt_message(bus, json.loads(live_raw), live_raw)
        live = bus.events[1]
        assert live.update_type == BookUpdateType.DELTA
        assert live.source_ts_ns == 1_700_000_000_223_456_789
        assert (live.final_update_id, live.previous_update_id, live.sync_generation) == (12345, 12344, 1)
        assert (live.receive_sequence, live.raw_payload) == (2, live_raw)
        assert live.bids[0].price == Decimal("64000.223456789012345")
    asyncio.run(scenario())
