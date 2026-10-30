"""Bounded local-only fixtures for the QA-01 integration packet."""
from __future__ import annotations

import asyncio
import json
import os
import time
from decimal import Decimal
from types import ModuleType

from feeds.feed_recovery import EngineCursor, RecoveryFrame
from feeds.providers.binance import BinancePerpCollector
from market.hot_path import HotPathStore
from market.local_engine import EngineBudgets, LocalMarketEngine
from runtime.local_transport import LocalTransportClient, LocalTransportServer, TransportBudgets
from storage.market_spool import MarketSpool, SpoolReader
from feeds.local_pipeline import PipelineBudgets, SpoolingMarketPublisher


PRODUCER_A = "binance-perp"
PRODUCER_B = "bybit-perp"
INCARNATION_A = "qa01-binance-local-run"
INCARNATION_B = "qa01-bybit-local-run"


class NativeTradeSocket:
    """Finite fake Binance market socket; it never opens a network connection."""

    def __init__(self, count: int):
        self.rows = [json.dumps({"data": {
            "e": "aggTrade", "s": "BTCUSDT", "T": int(time.time() * 1000) - count + i,
            "a": 50_000 + i, "p": f"{100 + i / 100:.2f}",
            "q": "0.1250", "m": bool(i % 2),
        }}) for i in range(count)]

    async def recv(self):
        if self.rows:
            return self.rows.pop(0)
        raise NativeBatchComplete


class NativeBatchComplete(Exception):
    """Deterministic end-of-fixture signal; no wall-clock parser timeout."""


class NativeEventSink:
    def __init__(self):
        self.events = []

    async def publish_market_event(self, event):
        self.events.append(event)


async def parse_binance_native_trades(count: int):
    """Use the existing Binance perp native parser and return validated models."""
    sink = NativeEventSink()
    collector = BinancePerpCollector(symbols=("BTCUSDT",))
    try:
        await collector._binance_market(NativeTradeSocket(count), sink, time.monotonic() + 60)
    except NativeBatchComplete:
        pass
    assert len(sink.events) == count
    assert all(event.provider == PRODUCER_A for event in sink.events)
    assert [event.trade_id for event in sink.events] == [str(50_000 + i) for i in range(count)]
    return sink.events


async def parse_binance_native_book():
    """Build one real normalized USDM depth snapshot through the collector helper."""
    from core.models import BookUpdateType

    sink = NativeEventSink()
    collector = BinancePerpCollector(symbols=("BTCUSDT",))
    stamp_ms = int(time.time() * 1000)
    raw = {"lastUpdateId": 700, "bids": [["100.00", "2.000"]],
           "asks": [["101.00", "3.000"]]}
    await collector._book(
        sink, "BTCUSDT", stamp_ms, raw["bids"], raw["asks"],
        BookUpdateType.SNAPSHOT, 700, channel="books",
        raw_payload=json.dumps(raw, separators=(",", ":")),
    )
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.provider == PRODUCER_A and event.final_update_id == 700
    return event


def engine_budgets():
    return EngineBudgets(
        max_producers=2, max_instruments=8, max_frame_bytes=8192,
        max_records=128, max_bytes=1_048_576, max_age_ns=10_000_000_000,
        max_digests_per_feed=128, max_state_callbacks=8,
        feed_lease_ns=20_000_000_000, tick_seconds=0.01,
    )


def transport_budgets():
    return TransportBudgets(
        max_frame_bytes=8192, max_header_bytes=1024, max_records=128,
        max_bytes=1_048_576, max_age_seconds=10.0,
        max_control_records=64, max_control_bytes=65_536,
        max_sessions=2, io_timeout_seconds=2.0,
    )


def pipeline_budgets():
    return PipelineBudgets(
        max_records=128, max_bytes=1_048_576, max_frame_bytes=8192,
        batch_records=16, group_sync_ms=2,
    )


def spool_limits():
    return dict(
        max_disk_bytes=8_388_608, max_records=4096, max_frame_bytes=8192,
        max_header_bytes=1024, segment_max_bytes=262_144,
        segment_max_records=512, segment_max_age_seconds=60,
    )


def make_engine(*, endpoint, applied_callbacks=(), hot_path=None, fresh_b=False,
                invalidation_callback=None, snapshot_clock=None):
    invalid = []
    def on_invalidate(producer, reason):
        invalid.append((producer, reason))
        if invalidation_callback is not None:
            invalidation_callback(producer, reason)
    hot_path = hot_path or HotPathStore()
    budgets = engine_budgets()
    engine = LocalMarketEngine(
        producer_providers={PRODUCER_A: PRODUCER_A, PRODUCER_B: PRODUCER_B},
        budgets=budgets, on_invalidate=on_invalidate,
        hot_path=hot_path, post_apply_callbacks=tuple(applied_callbacks),
        snapshot_clock=snapshot_clock or time.time_ns,
        trading_filter=lambda event: event.event_type.value == "trade",
        fresh_starts=({PRODUCER_A: INCARNATION_A, PRODUCER_B: INCARNATION_B}
                      if fresh_b else {PRODUCER_A: INCARNATION_A}),
        verified_fresh_start=True,
    )
    server = LocalTransportServer(
        endpoint, budgets=transport_budgets(), on_frame=engine.on_frame,
        on_gap=engine.on_gap, on_resume=engine.request_resume, allowed_uid=os.getuid(),
    )
    return engine, server, invalid


def make_publisher(*, root, endpoint, invalid):
    transport = LocalTransportClient(
        endpoint, producer=PRODUCER_A, incarnation=INCARNATION_A,
        budgets=transport_budgets(), on_gap=lambda *args: invalid.append(args),
        allowed_uid=os.getuid(),
    )
    limits = spool_limits()
    publisher = SpoolingMarketPublisher(
        producer=PRODUCER_A, incarnation=INCARNATION_A,
        spool_factory=lambda: MarketSpool(root, PRODUCER_A, INCARNATION_A, **limits),
        transport=transport, budgets=pipeline_budgets(), env={},
        on_invalid=lambda reason: invalid.append((PRODUCER_A, reason)),
    )
    return publisher, limits


def warming_engine():
    """Create an exact C/H=0 recovery with both producers registered and warming."""
    engine = LocalMarketEngine(
        producer_providers={PRODUCER_A: PRODUCER_A, PRODUCER_B: PRODUCER_B},
        budgets=engine_budgets(), on_invalidate=lambda *args: None,
        hot_path=HotPathStore(), trading_filter=lambda event: event.event_type.value == "trade",
    )
    a = EngineCursor(PRODUCER_A, INCARNATION_A, 0)
    b = EngineCursor(PRODUCER_B, INCARNATION_B, 0)
    engine.begin_recovery(a, a, verified_checkpoint=True)
    engine.begin_recovery(b, b, verified_checkpoint=True)
    return engine, a, b


def provider_instruments():
    from core.models import AssetClass, InstrumentId, Venue

    a = InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_PERP,
                     raw_symbol="BTCUSDT", std_symbol="BTCUSDT", currency="USDT",
                     security_kind="perpetual")
    b = InstrumentId(venue=Venue.BYBIT, asset_class=AssetClass.CRYPTO_PERP,
                     raw_symbol="BTCUSDT", std_symbol="BTCUSDT", currency="USDT",
                     security_kind="perpetual")
    return a, b


def make_paper_strategy_runtime(root, hot_path, *, observations, now_ns=None, include_both=True):
    """Actual PAPER runtime registrations whose inputs use explicit feed keys."""
    from core.models import Side
    from execution.execution_ledger import AccountScope
    from execution.paper_execution import PaperExecution
    from execution.trading_contracts import ProductKind, ProductSpec, TradingMode
    from portfolio.portfolio_ledger import PortfolioLedger
    from runtime.strategy_runtime import (
        PaperStrategyRuntime, RuntimeBudgets, StrategyRegistration, StrategyRisk,
    )
    from strategies import StrategyModule

    a, b = provider_instruments()
    account = AccountScope("binance", "qa01-paper", "perpetual", "paper")
    ledger = PortfolioLedger(
        root / "paper" / "paper.sqlite", account, owner_root=root, mode=TradingMode.PAPER,
        max_orders=100, max_fills=100, max_events=1000,
        max_document_bytes=1_000_000, max_db_bytes=16_000_000, max_wal_bytes=16_000_000,
    )
    ledger.seed_balance("USDT", Decimal("100000"), {"source": "qa01-synthetic"})
    matcher = PaperExecution(
        ledger, entry_latency_ns=10, cancel_latency_ns=5, max_open_orders=16,
        max_depth=8, max_receipts=512, max_state_bytes=500_000,
        stale_after_ns=1_000_000_000,
    )
    product = ProductSpec(
        a, ProductKind.LINEAR_PERPETUAL, Decimal("10"), Decimal("1"),
        Decimal("1"), Decimal("1"), "USDT", "USDT", "USDT", "net",
        "isolated", "synthetic fee", "explicit simulated funding",
        "synthetic margin", Decimal(".001"), Decimal(".002"),
    )
    budgets = RuntimeBudgets(
        max_jobs=4, max_accounts=1, max_required_feeds=8,
        max_signals_per_callback=2, max_actions_per_callback=2,
        max_pending_actions=4, max_seen_actions=100, max_config_bytes=32_000,
        max_signal_bytes=8192, max_action_bytes=16_000,
        max_callback_ns=1_000_000_000, max_feed_age_ns=5_000_000_000,
    )
    runtime = PaperStrategyRuntime(
        hot_path=hot_path, accounts={account.key: matcher}, products={a.key: product},
        known_instruments={a.key: a, b.key: b}, reference_feeds={}, budgets=budgets,
        settings={"fixture": "QA-01 local PAPER readiness"}, mode=TradingMode.PAPER,
        phase="paper", now_ns=now_ns or time.time_ns,
    )
    required_by_job = [("a_only", (a.key,))]
    if include_both:
        required_by_job.append(("b_required", (a.key, b.key)))
    risk = StrategyRisk(
        Decimal("0"), Decimal("100000"), Decimal("100000"),
        Decimal("100000"), Decimal("100000"), Decimal(".1"),
    )
    for strategy_id, required in required_by_job:
        module = ModuleType("qa01_plugin_" + strategy_id)

        class Plugin:
            def on_event(self, event, view, _sid=strategy_id):
                observations[_sid].append({
                    "event_id": event.event_id, "feed": event.instrument.key,
                    "ready": view.ready, "phase": view.phase,
                    "snapshot_cursor": view.cursor,
                })
                return []

        module.create_strategy = Plugin
        definition = StrategyModule(
            module, strategy_id,
            [{"venue": "binance", "symbol": "BTCUSDT", "event_type": "trade"}], {},
        )
        runtime.register(StrategyRegistration(
            job_id=strategy_id, module=definition, strategy_version="fixture-v1",
            config_version="fixture-config-v1", config={},
            research_receipt={"fixture": "no-alpha/no-order"}, paper_permission=True,
            required_feeds=required, warmup_bars=0, accounts=(account.key,),
            risk={account.key: risk}, action_builder=lambda signal, view, config: [],
        ))
    return runtime, ledger, matcher


def spot_and_perp_trades(*, now_ns=None):
    """Validated canonical models from Binance-like native aggTrade fields.

    The baseline has a Binance USDM parser but no Binance spot market-data
    collector. The perp event is parsed by the production collector in
    ``parse_binance_native_trades``; this helper makes that missing spot-source
    limitation explicit while exercising schema/state separation.
    """
    from core.models import AssetClass, InstrumentId, TradeEvent, Venue

    now = now_ns or time.time_ns()
    spot = InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_SPOT,
                        raw_symbol="BTCUSDT", std_symbol="BTCUSDT", currency="USDT")
    perp = InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_PERP,
                        raw_symbol="BTCUSDT", std_symbol="BTCUSDT", currency="USDT",
                        security_kind="perpetual")
    native_spot = {"e": "aggTrade", "s": "BTCUSDT", "T": now // 1_000_000,
                   "a": 7001, "p": "100.0100", "q": "0.005000", "m": False}
    spot_event = TradeEvent(
        instrument=spot, venue=Venue.BINANCE, provider="binance-spot-fixture",
        source_ts_ns=native_spot["T"] * 1_000_000,
        receive_ts_ns=now, wallclock_ts_ns=now,
        receive_monotonic_ns=time.monotonic_ns(), connection_id="spot-generation-1",
        receive_sequence=9, native_event_id=f"spot-aggtrade:{native_spot['a']}",
        sequence=native_spot["a"], trade_id=str(native_spot["a"]),
        price=Decimal(native_spot["p"]), quantity=Decimal(native_spot["q"]),
    )
    native_perp = {
        "data": {"e": "aggTrade", "s": "BTCUSDT", "T": native_spot["T"] - 200,
                 "a": 8001, "p": "100.0200", "q": "0.125000", "m": True}
    }
    # Reuse the real USDM normalizer by feeding exactly one native frame.
    class OneFrameSocket:
        def __init__(self): self.row = json.dumps(native_perp)
        async def recv(self):
            if self.row is not None:
                row, self.row = self.row, None
                return row
            raise NativeBatchComplete
    class OneEventSink(NativeEventSink):
        pass

    async def parse_perp():
        sink = OneEventSink()
        awaiter = BinancePerpCollector(symbols=("BTCUSDT",))
        try:
            await awaiter._binance_market(OneFrameSocket(), sink, time.monotonic() + 60)
        except NativeBatchComplete:
            pass
        return sink.events

    return spot_event, parse_perp


def native_trade_payload(*, producer=PRODUCER_A, seq=1):
    """Canonical model payload for cursor/readiness-only recovery assertions."""
    from core.models import AssetClass, InstrumentId, TradeEvent, Venue

    venue = Venue.BINANCE if producer == PRODUCER_A else Venue.BYBIT
    instrument = InstrumentId(
        venue=venue, asset_class=AssetClass.CRYPTO_PERP, raw_symbol="BTCUSDT",
        std_symbol="BTCUSDT", currency="USDT", security_kind="perpetual",
    )
    return TradeEvent(
        instrument=instrument, venue=venue, provider=producer,
        source_ts_ns=1_800_000_000_000_000_000 + seq,
        receive_ts_ns=1_800_000_000_000_000_000 + seq,
        receive_monotonic_ns=time.monotonic_ns(),
        connection_id=f"{producer}-fixture-generation-1", receive_sequence=seq,
        native_event_id=f"native-{producer}-{seq}", sequence=seq,
        price=Decimal("100.1250"), quantity=Decimal("0.1250"), trade_id=str(seq),
    ).model_dump_json().encode()


def recovery_frame(cursor: EngineCursor, payload: bytes | None = None):
    return RecoveryFrame(cursor, payload if payload is not None else native_trade_payload(
        producer=cursor.producer, seq=cursor.sequence))


def spool_reader(root, limits, *, after):
    from storage.market_spool import SpoolCursor

    return SpoolReader(
        root, PRODUCER_A, INCARNATION_A,
        after=SpoolCursor(PRODUCER_A, INCARNATION_A, after),
        max_disk_bytes=limits["max_disk_bytes"], max_records=limits["max_records"],
        max_frame_bytes=limits["max_frame_bytes"], max_header_bytes=limits["max_header_bytes"],
    )
