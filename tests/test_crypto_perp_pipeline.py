import asyncio
import json
from decimal import Decimal

import pytest

from vibetrading.bus import InMemoryEventBus, subject_for_event
from vibetrading.bybit_execution import BybitAudit, BybitExecutionProbe, _market_trigger_price
from vibetrading.hot_path import HotPathStore
from vibetrading.market_source_status import source_status
from vibetrading.models import BookUpdateType, OrderBookEvent, OrderBookLevel, Venue
from vibetrading.providers.crypto_perp import CryptoPerpCollector, _instrument


def test_market_trigger_quote_keeps_postonly_buy_non_crossing():
    bid, ask = Decimal("125.01"), Decimal("125.03")
    assert _market_trigger_price("maker", "Buy", bid, ask) == bid
    assert _market_trigger_price("taker", "Buy", bid, ask) == ask


def test_book_original_frame_reaches_parquet(tmp_path):
    import pyarrow.parquet as pq

    from vibetrading.parquet_store import ParquetBatchWriter
    from vibetrading.settings import RuntimeSettings
    from vibetrading.sqlite_store import RuntimeStore

    async def collect():
        bus = InMemoryEventBus()
        await CryptoPerpCollector(Venue.BYBIT, ("BTCUSDT",))._book(
            bus, "BTCUSDT", 1, [["100", "2"]], [["101", "3"]],
            BookUpdateType.SNAPSHOT, 10, raw_payload='{"topic":"orderbook.50.BTCUSDT"}',
        )
        return bus.messages[0].data

    event = asyncio.run(collect())
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env)
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    try:
        writer = ParquetBatchWriter(settings, store)
        assert writer.add(event)
        path = writer.flush_all()[0]
        assert pq.ParquetFile(path).read(columns=["raw_payload"]).column(0)[0].as_py() == event["raw_payload"]
    finally:
        store.close()


def test_raw_crypto_book_and_live_book_gap(tmp_path):
    async def scenario():
        bus = InMemoryEventBus()
        collector = CryptoPerpCollector(Venue.BITGET, ("BTCUSDT",))
        await collector._book(bus, "BTCUSDT", 1, [["100", "2"]], [["101", "3"]],
                              BookUpdateType.SNAPSHOT, 10, channel="books")
        await collector._book(bus, "BTCUSDT", 2, [["100", "0"], ["99", "4"]], [],
                              BookUpdateType.DELTA, 11, previous=10, channel="books")
        assert [x.data["update_type"] for x in bus.messages] == ["snapshot", "delta"]
        assert all(x.subject.startswith("crypto.order_book.bitget.crypto_perp") for x in bus.messages)
        hot = HotPathStore()
        for message in bus.messages:
            hot.apply(message.data)
        latest = hot.latest_book(_instrument(Venue.BITGET, "BTCUSDT").key)
        assert latest.bids[0].price == Decimal("99")
        ignored=OrderBookEvent(**collector._common("BTCUSDT",4),update_type=BookUpdateType.DELTA,
            bids=[OrderBookLevel(price=Decimal("100"),quantity=Decimal("10"))],asks=[],depth=1,
            final_update_id=9,sync_generation=0,fast=False)
        assert not hot.apply(ignored)
        assert hot.latest_book(_instrument(Venue.BITGET, "BTCUSDT").key).bids[0].price == Decimal("99")
        with pytest.raises(RuntimeError):
            await collector._book(bus, "BTCUSDT", 3, [], [], BookUpdateType.DELTA,
                                  14, previous=12, channel="books")
        hot.apply(bus.messages[-1].data)
        assert hot.latest_book(_instrument(Venue.BITGET, "BTCUSDT").key) is None
    asyncio.run(scenario())


def test_bybit_audit_only_counts_verified_fills(tmp_path):
    audit=BybitAudit(tmp_path/"testnet.sqlite3")
    try:
        audit.create("a","SOLUSDT","Buy","maker","0.1","100",1000)
        audit.transition("a","SUBMITTED",field="submit_ns",timestamp=1100)
        audit.transition("a","ACKNOWLEDGED",field="ack_ns",timestamp=1200)
        audit.transition("a","POSTED",field="posted_ns",timestamp=1300)
        assert audit.fill("a",{"execId":"exec-a","execQty":"0.1","execPrice":"100","isMaker":True,"execTime":"1"},1400)
        assert not audit.fill("a",{"execId":"exec-a","execQty":"0.1","execPrice":"100","isMaker":True,"execTime":"1"},1500)
        audit.transition("a","FILLED",field="first_fill_ns",timestamp=1400)
        audit.transition("a","FILLED",field="final_fill_ns",timestamp=1400)
        audit.transition("a","ACKNOWLEDGED",field="ack_ns",timestamp=1500)
        assert audit.report("a")["status"] == "FILLED"
        audit.create("b","SOLUSDT","Buy","maker","0.1","100",2000)
        audit.transition("b","TIMEOUT",timestamp=2100)
        assert audit.summary("maker")["filled"] == 1
        assert audit.summary("maker")["other"] == 1
        assert audit.summary("maker")["decision_to_stage"]["final_fill_ms"]["samples"] == 1
    finally:
        audit.close()


def test_unverified_paid_macro_sources_disabled():
    status=source_status()
    assert all(not item["enabled"] for item in status.values())
    assert status["KOSPI200_FOREIGN_FINAL"]["status"] == "KRX_OFFICIAL_API_UNAVAILABLE"


def test_private_fill_requires_posted_maker_and_matching_is_maker(tmp_path):
    async def messages():
        yield json.dumps({"topic":"order.linear","data":[{"orderLinkId":"maker-a","orderStatus":"New"}]})
        yield json.dumps({"topic":"execution.linear","data":[{"orderLinkId":"maker-a","execType":"Trade",
            "execId":"exec-a","execQty":"0.1","execPrice":"100","execTime":"1","leavesQty":"0","isMaker":True}]})
    async def scenario():
        probe=object.__new__(BybitExecutionProbe)
        probe.audit=BybitAudit(tmp_path/"probe.sqlite3")
        try:
            probe.audit.create("maker-a","SOLUSDT","Buy","maker","0.1","100",1)
            done=asyncio.Event();posted=asyncio.Event()
            await probe._listen(messages(),"maker-a",True,done,posted)
            assert done.is_set() and posted.is_set()
            assert probe.audit.report("maker-a")["status"] == "FILLED"
        finally:
            probe.audit.close()
    asyncio.run(scenario())
