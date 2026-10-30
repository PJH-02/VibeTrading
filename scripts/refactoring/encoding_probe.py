"""Deterministic fake-transport transcript; safe only in an offline source copy."""
from __future__ import annotations

import asyncio
import base64
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from runtime import bus as bus_module
from runtime import runtime
from core.models import AssetClass, Bar1mEvent, BookUpdateType, DataGapEvent, EtfNavEvent, IndexQuoteEvent, InstrumentId, OrderBookEvent, OrderBookLevel, TradeEvent, Venue

NOW = 1792281600000000000


def cases():
    crypto = InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_PERP,
                          raw_symbol="BTCUSDT", std_symbol="BTCUSDT")
    etf = InstrumentId(venue=Venue.KIWOOM, asset_class=AssetClass.ETF,
                       raw_symbol="069500", std_symbol="KIWOOM:069500")
    common = dict(source_ts_ns=NOW, receive_ts_ns=NOW, wallclock_ts_ns=NOW,
                  receive_monotonic_ns=123, connection_id="fixture", receive_sequence=1,
                  session="fixture", sequence=7, trading_date="2026-10-18")
    trade = TradeEvent(instrument=crypto, venue=Venue.BINANCE, provider="binance-perp",
                       price=Decimal("0.123456789012345678"), quantity=Decimal("2.000000000000000001"), **common)
    book = OrderBookEvent(instrument=crypto, venue=Venue.BINANCE, provider="binance-perp",
        depth=2, bids=[OrderBookLevel(price=Decimal("10.00"), quantity=Decimal("2.00"), order_count=3),
                       OrderBookLevel(price=Decimal("9.0"), quantity=Decimal("0"))],
        asks=[OrderBookLevel(price=Decimal("11.000"), quantity=Decimal("3"))],
        raw_payload='{"native":"한글","b":[["9.0","0"]]}', fast=True,
        first_update_id=6, final_update_id=7, previous_update_id=5, sync_generation=1, **common)
    bar = Bar1mEvent(instrument=etf, venue=Venue.KIWOOM, provider="kiwoom",
        open_ts_ns=NOW-60_000_000_000, close_ts_ns=NOW,
        open=Decimal("100"), high=Decimal("102"), low=Decimal("99"), close=Decimal("101"),
        volume=Decimal("50"), **common)
    index = InstrumentId(venue=Venue.KIS, asset_class=AssetClass.INDEX, raw_symbol="2001", std_symbol="KOSPI200")
    return {
        "crypto_trade": trade,
        "null_source": trade.model_copy(update={"source_ts_ns":None}),
        "raw_snapshot": book,
        "raw_delta": book.model_copy(update={"update_type":BookUpdateType.DELTA}),
        "durable_only_book": book.model_copy(update={"fast":False}),
        "etf_trade": trade.model_copy(update={"instrument":etf,"venue":Venue.KIWOOM,"provider":"kiwoom"}),
        "etf_book": book.model_copy(update={"instrument":etf,"venue":Venue.KIWOOM,"provider":"kiwoom"}),
        "etf_bar": bar,
        "etf_nav": EtfNavEvent(instrument=etf, venue=Venue.KIWOOM, provider="kiwoom", nav=Decimal("100.0100"), **common),
        "index_supplement": IndexQuoteEvent(instrument=index, venue=Venue.KIS, provider="kis", value=Decimal("300.01"), **common),
        "backfill": bar.model_copy(update={"instrument":index,"venue":Venue.KIS,"provider":"kis","ingest_mode":"backfill"}),
        "retired": trade.model_copy(update={"provider":"bitget-perp"}),
        "disallowed_symbol": trade.model_copy(update={"instrument":crypto.model_copy(update={"std_symbol":"NOPE"})}),
        "gap": DataGapEvent(instrument=crypto, venue=Venue.BINANCE, provider="binance-perp", reason="fixture gap", **common),
        "stale": trade.model_copy(update={"source_ts_ns":NOW-200000*1_000_000_000}),
        "future": trade.model_copy(update={"source_ts_ns":NOW+301*1_000_000_000}),
    }


async def collect_transcript():
    transcript = {}
    for name, event in cases().items():
        calls = []
        class JS:
            async def publish(self, subject, payload, **kwargs):
                calls.append(dict(lane="js", subject=subject, payload=base64.b64encode(payload).decode(), **kwargs))
        class NC:
            async def publish(self, subject, payload):
                calls.append(dict(lane="live", subject=subject, payload=base64.b64encode(payload).decode()))
        sink = bus_module.NatsJetStreamBus(SimpleNamespace(env={}))
        sink._js, sink._nc = JS(), NC()
        readiness = runtime._CollectorReadinessBus(sink, None, event.provider)
        with patch.object(bus_module.time, "time_ns", return_value=NOW), patch.object(bus_module, "utc_now_ns", return_value=NOW), patch.object(runtime, "health_touch"):
            await readiness.publish_market_event(event)
        transcript[name] = dict(calls=calls, count=readiness.published_count,
            bytes=readiness.published_bytes, last_event_ts_ns=readiness.last_event_ts_ns,
            symbol_last_seen_ns=readiness.symbol_last_seen_ns)
    return transcript


if __name__ == "__main__":
    import json
    import sys
    assert getattr(sys, "_vibetrading_offline_guard", False)
    print(json.dumps(asyncio.run(collect_transcript()), ensure_ascii=False, sort_keys=True))
