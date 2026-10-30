"""Offline integration checks for the venue-owned public perpetual feeds."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.models import Venue
from feeds.providers import (
    ArcusPerpCollector, BinancePerpCollector, BitgetPerpCollector,
    BybitPerpCollector, GrvtPerpCollector, create_perp_collector,
)
from feeds.providers.perp_common import _instrument
from market.hot_path import HotPathStore
from runtime.bus import InMemoryEventBus


EXPECTED = {
    Venue.BYBIT: BybitPerpCollector,
    Venue.BITGET: BitgetPerpCollector,
    Venue.BINANCE: BinancePerpCollector,
    Venue.ARCUS: ArcusPerpCollector,
    Venue.GRVT: GrvtPerpCollector,
}


def test_factory_returns_real_collector_and_rejects_unknown_or_unlisted_symbol():
    for venue, implementation in EXPECTED.items():
        collector = create_perp_collector(venue, ("BTCUSDT",))
        assert type(collector) is implementation
        assert collector.venue is venue
        assert collector.symbols == ("BTCUSDT",)
        assert not hasattr(collector, "broker")
        assert not hasattr(collector, "auth_client")
    with pytest.raises(ValueError, match="unsupported"):
        create_perp_collector(Venue.TOSS)
    with pytest.raises(ValueError, match="allowlist"):
        create_perp_collector(Venue.BYBIT, ("SKHYUSDT",))


class _Socket:
    def __init__(self, frames=()):
        self.frames = list(frames)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def send(self, payload):
        self.sent.append(payload)

    async def recv(self):
        if self.frames:
            return self.frames.pop(0)
        await asyncio.sleep(.11)
        return "{}"


class _Response:
    def __init__(self, row):
        self.row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def raise_for_status(self):
        pass

    async def json(self):
        return self.row


class _Session:
    def __init__(self, **_):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/markets"):
            return _Response({"markets": [{"marketDisplayName": "BTC-USD", "status": "ONLINE"}]})
        if url.endswith("/depth"):
            return _Response({"lastUpdateId": 10, "bids": [["100", "2"]], "asks": [["101", "3"]]})
        if url.endswith("/openInterest"):
            return _Response({"time": 1700000000000, "openInterest": "12.3400"})
        raise AssertionError(f"unexpected HTTP request: {url}")


def test_bybit_run_subscribes_and_decodes_native_frames(monkeypatch):
    import feeds.providers.bybit as module
    frames = [
        {"topic": "orderbook.50.BTCUSDT", "type": "snapshot", "ts": 1700000000000,
         "data": {"u": 1, "b": [["100.10", "2.00"]], "a": [["100.20", "3"]]}},
        {"topic": "publicTrade.BTCUSDT", "data": [{"T": 1700000000001, "i": 42,
         "p": "100.15", "v": "0.125", "S": "Buy"}]},
        {"topic": "tickers.BTCUSDT", "ts": 1700000000002,
         "data": {"openInterest": "123.4500", "fundingRate": "0.000125", "fundingIntervalHour": "8"}},
    ]
    socket = _Socket(json.dumps(f) for f in frames)
    monkeypatch.setattr(module.websockets, "connect", lambda *a, **k: socket)
    bus = InMemoryEventBus()
    asyncio.run(create_perp_collector(Venue.BYBIT, ("BTCUSDT",)).run(bus, 1))
    sent = json.loads(socket.sent[0])
    assert "orderbook.50.BTCUSDT" in sent["args"] and "publicTrade.BTCUSDT" in sent["args"]
    trade = next(m.data for m in bus.messages if m.data["event_type"] == "trade")
    assert (trade["trade_id"], trade["price"], trade["quantity"]) == ("42", "100.15", "0.125")
    assert any(m.data["event_type"] == "funding_rate" and m.data["funding_rate"] == "0.000125" for m in bus.messages)
    assert any(m.data["event_type"] == "open_interest" and m.data["open_interest"] == "123.4500" for m in bus.messages)


def test_bitget_run_preserves_first_delta_bridge_trade_and_ticker(monkeypatch):
    import feeds.providers.bitget as module
    frames = [
        {"arg": {"symbol": "BTCUSDT", "topic": "books"}, "action": "snapshot",
         "data": [{"ts": "1700000000000", "seq": "100", "b": [["100", "1"]], "a": [["101", "1"]]}]},
        {"arg": {"symbol": "BTCUSDT", "topic": "books"}, "action": "update",
         "data": [{"ts": "1700000000001", "seq": "101", "pseq": "99", "b": [["100", "2"]], "a": []}]},
        {"arg": {"symbol": "BTCUSDT", "topic": "publicTrade"},
         "data": [{"T": 1700000000002, "i": "bg-7", "p": "100.25", "v": "0.005", "S": "buy"}]},
        {"arg": {"symbol": "BTCUSDT", "topic": "ticker"},
         "data": [{"ts": 1700000000003, "openInterest": "8.250", "fundingRate": "-0.0002"}]},
    ]
    socket = _Socket(json.dumps(f) for f in frames)
    monkeypatch.setattr(module.websockets, "connect", lambda *a, **k: socket)
    bus = InMemoryEventBus()
    asyncio.run(create_perp_collector(Venue.BITGET, ("BTCUSDT",)).run(bus, .1))
    assert json.loads(socket.sent[0])["args"][0] == {"instType": "usdt-futures", "topic": "books", "symbol": "BTCUSDT"}
    assert [m.data["final_update_id"] for m in bus.messages if m.data["event_type"] == "order_book"] == [100, 101]
    assert bus.messages[1].data["previous_update_id"] == 99
    trade = next(m.data for m in bus.messages if m.data["event_type"] == "trade")
    assert trade["trade_id"] == "bg-7" and trade["quantity"] == "0.005"
    assert any(m.data["event_type"] == "funding_rate" and m.data["funding_rate"] == "-0.0002" for m in bus.messages)
    assert any(m.data["event_type"] == "open_interest" and m.data["open_interest"] == "8.250" for m in bus.messages)


def test_binance_run_uses_snapshot_bridge_and_market_native_trade(monkeypatch):
    import feeds.providers.binance as module
    public = _Socket([json.dumps({"data": {"e": "depthUpdate", "s": "BTCUSDT", "E": 1700000000001,
        "U": 10, "u": 11, "pu": 9, "b": [["100", "4"]], "a": []}})])
    market = _Socket([json.dumps({"data": {"e": "aggTrade", "s": "BTCUSDT", "T": 1700000000002,
        "a": 9001, "p": "100.25", "q": "0.1250", "m": False}})])
    sockets = iter((public, market))
    monkeypatch.setattr(module.websockets, "connect", lambda *a, **k: next(sockets))
    monkeypatch.setattr(module.aiohttp, "ClientSession", _Session)
    bus = InMemoryEventBus()
    asyncio.run(create_perp_collector(Venue.BINANCE, ("BTCUSDT",)).run(bus, .1))
    books = [m.data for m in bus.messages if m.data["event_type"] == "order_book"]
    assert [b["final_update_id"] for b in books] == [10, 11]
    assert books[1]["raw_payload"] and books[1]["first_update_id"] == 10
    trade = next(m.data for m in bus.messages if m.data["event_type"] == "trade")
    assert trade["trade_id"] == "9001" and trade["quantity"] == "0.1250"
    assert any(m.data["event_type"] == "open_interest" and m.data["open_interest"] == "12.3400" for m in bus.messages)


def test_arcus_run_subscribes_and_keeps_microsecond_source_and_native_trade_id(monkeypatch):
    import feeds.providers.arcus as module
    frames = [
        {"type": "subscribed", "channel": "l2OrderbookUpdates", "id": "BTC-USD",
         "contents": {"lastSequenceId": 50, "timestamp": 1700000000000001,
                      "bids": [["100", "1"]], "asks": [["101", "2"]]}},
        {"channel": "trades", "id": "BTC-USD", "contents": [{"tradeId": "arc-2",
         "timestamp": 1700000000000002, "price": "100.5", "size": "0.04"}]},
    ]
    socket = _Socket(json.dumps(f) for f in frames)
    monkeypatch.setattr(module.websockets, "connect", lambda *a, **k: socket)
    monkeypatch.setattr(module.aiohttp, "ClientSession", _Session)
    bus = InMemoryEventBus()
    asyncio.run(create_perp_collector(Venue.ARCUS, ("BTCUSDT",)).run(bus, .1))
    requests = [json.loads(x) for x in socket.sent]
    assert any(r.get("channel") == "l2OrderbookUpdates" and r.get("id") == "BTC-USD" for r in requests)
    book = next(m.data for m in bus.messages if m.data["event_type"] == "order_book")
    assert book["source_ts_ns"] == 1700000000000001000 and book["final_update_id"] == 50
    trade = next(m.data for m in bus.messages if m.data["event_type"] == "trade")
    assert trade["trade_id"] == "arc-2" and trade["source_ts_ns"] == 1700000000000002000
    assert trade["aggressor_side"] == "unknown"


def test_grvt_run_subscribes_and_keeps_nanosecond_source_and_ack_baseline(monkeypatch):
    import feeds.providers.grvt as module
    frames = [
        {"result": {"stream": "v1.book.d", "subs": ["BTC_USDT_Perp@100"], "first_sequence_number": ["102"]}},
        {"stream": "v1.book.d", "sequence_number": "0", "prev_sequence_number": "0",
         "feed": {"instrument": "BTC_USDT_Perp", "event_time": "1700000000000000001",
                  "bids": [{"price": "100", "size": "1"}], "asks": [{"price": "101", "size": "2"}]}},
        {"stream": "v1.book.d", "sequence_number": "102", "prev_sequence_number": "101",
         "feed": {"instrument": "BTC_USDT_Perp", "event_time": "1700000000000000002",
                  "bids": [{"price": "100", "size": "2"}], "asks": []}},
        {"stream": "v1.trade", "feed": {"instrument": "BTC_USDT_Perp", "event_time": "1700000000000000003",
         "trade_id": "grvt-9", "price": "100.75", "size": "0.003", "is_taker_buyer": True}},
    ]
    socket = _Socket(json.dumps(f) for f in frames)
    monkeypatch.setattr(module.websockets, "connect", lambda *a, **k: socket)
    bus = InMemoryEventBus()
    asyncio.run(create_perp_collector(Venue.GRVT, ("BTCUSDT",)).run(bus, .1))
    requests = [json.loads(x) for x in socket.sent]
    assert {r["params"]["stream"] for r in requests} == {"v1.book.d", "v1.trade", "v1.ticker.s"}
    books = [m.data for m in bus.messages if m.data["event_type"] == "order_book"]
    assert [b["final_update_id"] for b in books] == [101, 102]
    assert books[0]["source_ts_ns"] == 1700000000000000001
    assert books[1]["previous_update_id"] == 101
    trade = next(m.data for m in bus.messages if m.data["event_type"] == "trade")
    assert trade["trade_id"] == "grvt-9" and trade["source_ts_ns"] == 1700000000000000003


def test_venue_decoder_does_not_hold_shared_lock_and_hot_path_applies_100(monkeypatch):
    async def scenario():
        blocked = asyncio.Event()
        release = asyncio.Event()
        arcus = create_perp_collector(Venue.ARCUS, ("BTCUSDT",))
        bitget = create_perp_collector(Venue.BITGET, ("BTCUSDT",))
        bus = InMemoryEventBus()
        hot = HotPathStore()
        applied = []

        async def slow_publish(event):
            if event.instrument.venue is Venue.ARCUS:
                blocked.set()
                await release.wait()
            data = event.model_dump(mode="json")
            bus.messages.append(SimpleNamespace(data=data))
            if event.instrument.venue is Venue.BITGET:
                result = hot.apply(data)
                assert result is True
                applied.append(result)

        bus.publish_market_event = slow_publish
        one = asyncio.create_task(arcus._arcus_message(bus, {"channel": "trades", "id": "BTC-USD",
            "contents": [{"tradeId": "held", "price": "100", "size": "1", "timestamp": 1}]}, "arcus"))
        await blocked.wait()
        import feeds.providers.bitget as bitget_module
        frames = []
        for i in range(100):
            frame = {"arg": {"symbol": "BTCUSDT", "topic": "publicTrade"},
                     "data": [{"T": i + 1, "i": str(i), "p": "100.01", "v": "0.01", "S": "buy"}]}
            frames.append(json.dumps(frame))
        socket = _Socket(frames)
        monkeypatch.setattr(bitget_module.websockets, "connect", lambda *a, **k: socket)
        await bitget.run(bus, .1)
        assert len(applied) == 100
        assert one.done() is False
        assert hot.snapshot().trades[_instrument(Venue.BITGET, "BTCUSDT").key].trade_id == "99"
        release.set()
        await one
        assert len(bus.messages) == 101
        assert len(applied) == 100

    # Keep this focused on the production collectors and real HotPathStore.
    asyncio.run(scenario())
