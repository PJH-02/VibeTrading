"""PERF-01 auxiliary A1 load, PAPER callback, and local fault fixtures.

This file composes existing production classes around bounded synthetic input.
Provider-shaped rows are explicitly labelled normalized equivalents: these six
auxiliary cohorts do not claim the primary Binance parser's native-wire path.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import errno
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from types import ModuleType, SimpleNamespace


AUX = (
    ("binance-eth", "binance", "binance-perp", "ETHUSDT", "ETHUSDT", "USDT", "crypto_perp"),
    ("arcus-eth", "arcus", "arcus-perp", "ETH-USD", "ETHUSDT", "USD", "crypto_perp"),
    ("grvt-eth", "grvt", "grvt-perp", "ETH_USDT_Perp", "ETHUSDT", "USDT", "crypto_perp"),
    ("kis-etf", "kis", "kis", "069500", "069500", "KRW", "etf"),
    ("kiwoom-etf", "kiwoom", "kiwoom", "069500", "069500", "KRW", "etf"),
    ("toss-us-qqq", "toss", "toss", "QQQ", "TOSS:US:QQQ", "USD", "equity"),
)


def _instrument(spec):
    from core.models import AssetClass, InstrumentId, Venue
    _, venue, _provider, raw, standard, currency, asset = spec
    return InstrumentId(venue=Venue(venue), asset_class=AssetClass(asset), raw_symbol=raw,
                        std_symbol=standard, currency=currency,
                        security_kind="perpetual" if asset == "crypto_perp" else "equity")


def _normalized_event(spec, index, *, book=False):
    """Make a finite native-equivalent row with a distinct provider source ID."""
    from core.models import Bar1mEvent, OrderBookEvent, OrderBookLevel, TradeEvent
    now = time.time_ns()
    instrument = _instrument(spec)
    producer = spec[0]
    if producer == "toss-us-qqq":
        close = now - (now % 60_000_000_000)
        if index == 1:
            close -= 60_000_000_000
        return Bar1mEvent(instrument=instrument, venue=instrument.venue, provider=spec[2],
            event_id=f"toss:US:QQQ:1m:{close}", source_ts_ns=close,
            receive_ts_ns=now, wallclock_ts_ns=now, sequence=index,
            open_ts_ns=close - 60_000_000_000, close_ts_ns=close,
            open=Decimal("500"), high=Decimal("501"), low=Decimal("499"),
            close=Decimal("500.5"), volume=Decimal("100"), trade_count=1)
    if book:
        return OrderBookEvent(
            instrument=instrument, venue=instrument.venue, provider=spec[2],
            event_id=f"{producer}:book:{index}", source_ts_ns=now,
            receive_ts_ns=now, wallclock_ts_ns=now, sequence=index,
            final_update_id=index, sync_generation=1, depth=1,
            bids=[OrderBookLevel(price=Decimal("99"), quantity=Decimal("5"))],
            asks=[OrderBookLevel(price=Decimal("101"), quantity=Decimal("5"))],
        )
    return TradeEvent(
        instrument=instrument, venue=instrument.venue, provider=spec[2],
        event_id=f"{producer}:trade:{index}", native_event_id=f"{producer}-native-{index}",
        source_ts_ns=now, receive_ts_ns=now, wallclock_ts_ns=now,
        sequence=index, price=Decimal("100.25"), quantity=Decimal("0.01"),
    )


class PerfAuxLoad:
    """Six real local spool/UDS producers plus one real selected PAPER strategy."""

    def __init__(self, root, hot_path, *, duration_seconds=60):
        if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)) or duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive and finite")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.hot_path = hot_path
        self.duration_seconds = float(duration_seconds)
        self.sources = tuple(AUX)
        # Under-second invocations are harness smokes, not capacity profiles.
        # Do not force a 100-row burst into the unchanged 50 ms delivery ACK
        # budget just to finish a fixture quickly.
        count = 1 if self.duration_seconds < 1 else 101
        self.target_counts = {row[0]: (2 if row[0] == "toss-us-qqq" and count > 1 else
            (1 if row[0] == "toss-us-qqq" else count)) for row in self.sources}
        if self.duration_seconds < 1:
            self.target_counts["binance-eth"] = 2
        self.producer_providers = {row[0]: row[2] for row in self.sources}
        self.fresh_starts = {row[0]: f"perf01-aux-{row[0]}-g1" for row in self.sources}
        self._publishers = []
        self._clients = {}
        self._client_attempts = {row[0]: [] for row in self.sources}
        self._store_executor = None
        self._storage = None
        self._stop_storage = threading.Event()
        self._flush_entered = threading.Event()
        self._flush_release = threading.Event()
        self._gate_start_mono_ns = {}
        self._invalidations = []
        self._counts = {row[0]: {"published": 0, "applied": 0,
            "paper_callback_events": 0} for row in self.sources}
        self._paper_ledger = None
        self._paper_runtime = self._make_paper_runtime()
        self._closed = False

    def _make_paper_runtime(self):
        from core.models import AssetClass, InstrumentId, Venue
        from execution.paper_execution import PaperExecution
        from execution.trading_contracts import ProductKind, ProductSpec, TradingMode
        from portfolio.portfolio_ledger import PortfolioLedger
        from runtime.strategy_runtime import (PaperStrategyRuntime, RuntimeBudgets,
            StrategyRegistration, StrategyRisk)
        from strategies.contract import StrategyModule
        from execution.execution_ledger import AccountScope

        instrument = InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_PERP,
            raw_symbol="ETHUSDT", std_symbol="ETHUSDT", currency="USDT", security_kind="perpetual")
        paper_root = self.root / "paper-mm"
        scope = AccountScope("binance", "perf01-paper-eth", "perpetual", "paper")
        ledger = PortfolioLedger(paper_root / "paper" / "account.sqlite", scope, owner_root=paper_root,
            mode=TradingMode.PAPER, max_orders=128, max_fills=512, max_events=4096,
            max_document_bytes=1_000_000, max_db_bytes=16_000_000, max_wal_bytes=16_000_000)
        ledger.seed_balance("USDT", Decimal("100000"), {"source": "perf01 synthetic paper seed"})
        matcher = PaperExecution(ledger, entry_latency_ns=10, cancel_latency_ns=5,
            max_open_orders=64, max_depth=8, max_receipts=1024,
            max_state_bytes=1_000_000, stale_after_ns=60_000_000_000)
        product = ProductSpec(instrument, ProductKind.LINEAR_PERPETUAL, Decimal(1),
            Decimal("0.01"), Decimal("0.001"), Decimal("5"), "USDT", "USDT", "USDT",
            "net", "isolated", "synthetic fee", "synthetic funding", "synthetic margin",
            Decimal("0.001"), Decimal("0.002"))
        host = PaperStrategyRuntime(hot_path=self.hot_path, accounts={scope.key: matcher},
            products={instrument.key: product}, known_instruments={instrument.key: instrument},
            reference_feeds={}, budgets=RuntimeBudgets(max_jobs=2, max_accounts=2,
                max_required_feeds=8, max_signals_per_callback=2, max_actions_per_callback=2,
                max_pending_actions=4, max_seen_actions=512, max_config_bytes=32768,
                max_signal_bytes=8192, max_action_bytes=16000, max_callback_ns=1_000_000_000,
                max_feed_age_ns=60_000_000_000), settings={"fixture": "perf01 local PAPER"},
            mode=TradingMode.PAPER, phase="paper", now_ns=time.time_ns)
        source = __import__("strategies.crypto.example_order_policy", fromlist=["*"])
        selected_module = ModuleType("perf01_selected_example_order_policy")
        selected_module.STRATEGY_ID = source.STRATEGY_ID
        selected_module.ACTION_PROTOCOL_VERSION = source.ACTION_PROTOCOL_VERSION
        selected_module.SUBSCRIPTIONS = [{"venue": "binance", "symbol": "ETHUSDT", "event_type": "order_book"}]
        selected_module.WARMUP = {"bars": 0}
        selected_module.create_strategy = source.create_strategy
        module = StrategyModule(selected_module, source.STRATEGY_ID,
            selected_module.SUBSCRIPTIONS, selected_module.WARMUP)
        registration = StrategyRegistration(source.STRATEGY_ID, module, "example-v1", "perf01-paper-v1",
            {"account_key": scope.key, "config_version": "perf01-paper-v1", "quantity": "0.1",
             "maker_offset": "0", "action_ttl_ns": 60_000_000_000},
            {"research_id": "perf01 local synthetic"}, True, (instrument.key,), 0, (scope.key,),
            {scope.key: StrategyRisk(Decimal(0), Decimal("10000"), Decimal("10000"),
                Decimal("10000"), Decimal("10000"), Decimal("0.1"))}, lambda *_: [])
        host.register(registration)
        self._paper_ledger = ledger
        return host

    def post_apply(self, result, snapshot):
        """Synchronous second PAPER file-policy consumer of the shared applied frame."""
        if result.event.instrument.key != _instrument(AUX[0]).key or result.event.event_type.value != "order_book":
            return ()
        generated = self._paper_runtime.process_applied(result, snapshot)
        self._counts["binance-eth"]["paper_callback_events"] += 1
        return generated

    @staticmethod
    def _make_settings(root):
        """Create settings from a fixed mapping, temporarily isolating ambient env reads."""
        import runtime.settings as settings_module
        original = settings_module._load_env
        fixed = {
            "VIBETRADING_DATA_ROOT": str(root / "data"),
            "VIBETRADING_STATE_DIR": str(root / "state"),
            "VIBETRADING_LOG_DIR": str(root / "logs"),
            "PARQUET_DATA_ROOT": str(root / "parquet"),
            "ARCHIVE_BACKEND": "local",
            "DELIVERY_LEDGER_FORMAT": "legacy",
            "PARQUET_FLUSH_SECONDS": "3600",
            "PARQUET_FLUSH_BYTES": "1048576",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_USER_ID": "",
        }
        try:
            settings_module._load_env = lambda _path=None: dict(fixed)
            return settings_module.RuntimeSettings.from_env(root / "unused.env")
        finally:
            settings_module._load_env = original

    async def run(self, endpoint, transport_budgets):
        from feeds.local_pipeline import DeliveryBudgets, PipelineBudgets, SpoolingMarketPublisher
        from runtime.local_transport import LocalTransportClient
        from storage.local_storage import LocalSpoolStorage, StorageBudgets
        from storage.market_spool import MarketSpool, SpoolCursor, SpoolReader
        from storage.sqlite_store import RuntimeStore
        from storage.parquet_store import ParquetBatchWriter
        from storage.archive import ArchiveMaintenance
        from storage.storage_backend import LocalStorageBackend
        from core.models import AssetClass, InstrumentId, TradeEvent, Venue

        settings = self._make_settings(self.root)
        spool_opts = {"max_disk_bytes": 16 * 1024 * 1024, "max_records": 2048,
            "max_frame_bytes": 8192, "max_header_bytes": 1024,
            "segment_max_bytes": 262144, "segment_max_records": 256,
            "segment_max_age_seconds": 600}
        reader_opts = {key: value for key, value in spool_opts.items() if not key.startswith("segment_")}
        pipeline = PipelineBudgets(256, 2 * 1024 * 1024, 8192, 32, 2)
        delivery = DeliveryBudgets(32, 256 * 1024, .001, .05, 5.0)
        sources = []
        for spec in self.sources:
            producer = spec[0]
            sources.append(SimpleNamespace(producer=producer,
                incarnation=self.fresh_starts[producer], spool_root=self.root / "spools" / producer,
                limits=SimpleNamespace(reader_kwargs=reader_opts)))
        plan = SimpleNamespace(producers=tuple(sources), observation=None,
            storage={"batch_records": 64, "batch_bytes": 262144, "max_payload_bytes": 8192,
                "max_writer_buffer_bytes": 262144, "max_fact_rows": 4096,
                "max_fact_db_bytes": 8 * 1024 * 1024, "poll_interval_ms": 10})

        # Seed one valid local Parquet row for a real ArchiveMaintenance upload.
        seed_settings = replace(settings, parquet=replace(settings.parquet,
            data_root=self.root / "seed-parquet"))
        seed_store = RuntimeStore(seed_settings, self.root / "seed-catalogue.sqlite")
        seed_writer = ParquetBatchWriter(seed_settings, seed_store)
        seed_instrument = InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_PERP,
            raw_symbol="ETHUSDT", std_symbol="ETHUSDT", currency="USDT")
        seed_writer.add(TradeEvent(instrument=seed_instrument, venue=Venue.BINANCE,
            provider="perf01-archive-seed", source_ts_ns=time.time_ns(), receive_ts_ns=time.time_ns(),
            wallclock_ts_ns=time.time_ns(), sequence=1, price=Decimal("100"), quantity=Decimal("0.01"),
            event_id="perf01-archive-seed:1"))
        seed_writer.flush_all()
        seed_rows = seed_store.list_manifest(active_only=True)
        seed_store.close()

        # Initialize LocalSpoolStorage and Parquet writer on their dedicated
        # persistent thread; all step/close calls stay on that same thread.
        self._store_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="perf01-local-storage")
        self._storage = await asyncio.wrap_future(self._store_executor.submit(
            LocalSpoolStorage, plan, settings, StorageBudgets(**plan.storage)))
        original_flush = self._storage.writer.flush_all
        def gated_flush():
            self._gate_start_mono_ns.setdefault("parquet",time.monotonic_ns())
            self._flush_entered.set()
            if not self._flush_release.wait(self.duration_seconds + 10):
                raise TimeoutError("bounded PERF-01 Parquet gate timed out")
            return original_flush()
        self._storage.writer.flush_all = gated_flush

        publishers = []
        async def create_publisher(spec):
            producer = spec[0]
            incarnation = self.fresh_starts[producer]
            root = self.root / "spools" / producer
            def spool_factory():
                return MarketSpool(root, producer, incarnation, **spool_opts)
            def reader_factory(cursor):
                return SpoolReader(root, producer, incarnation,
                    after=SpoolCursor(cursor.producer, cursor.incarnation, cursor.sequence), **reader_opts)
            def transport_factory():
                client = LocalTransportClient(endpoint, producer=producer, incarnation=incarnation,
                    budgets=transport_budgets,
                    on_gap=lambda *row, name=producer: self._invalidations.append((name, row)),
                    allowed_uid=os.getuid())
                self._clients[producer] = client
                self._client_attempts[producer].append(client)
                return client
            publisher = SpoolingMarketPublisher(producer=producer, incarnation=incarnation,
                spool_factory=spool_factory, transport=None, budgets=pipeline, env={},
                on_invalid=lambda reason, name=producer: self._invalidations.append((name, reason)),
                delivery_budgets=delivery, reader_factory=reader_factory,
                transport_factory=transport_factory)
            await publisher.connect()
            # Establish the real two-lane UDS session at cursor zero before
            # admitting the first frame. This makes the actual one-use fresh
            # start proof observable; it avoids turning an ordinary setup race
            # into an accidental replay workload.
            client_deadline = asyncio.get_running_loop().time() + 10
            while producer not in self._clients or not self._clients[producer].valid:
                if asyncio.get_running_loop().time() >= client_deadline:
                    raise TimeoutError(f"UDS fresh-start proof did not complete for {producer}")
                await asyncio.sleep(.001)
            return publisher

        publishers = await asyncio.gather(*(create_publisher(spec) for spec in self.sources))
        self._publishers = publishers
        # Start the storage worker only after each producer has one durable
        # record, ensuring the gate is on the actual Parquet flush path.
        async def publish_row(publisher, spec, index):
            event = _normalized_event(spec, index, book=(spec[0] == "binance-eth"))
            await publisher.publish_market_event(event)
            self._counts[spec[0]]["published"] += 1

        await asyncio.gather(*(publish_row(p, s, 1) for p, s in zip(publishers, self.sources)))
        async def wait_durable_first():
            deadline = asyncio.get_running_loop().time() + 10
            while any(p.stats["durable"] < 1 for p in publishers):
                if asyncio.get_running_loop().time() >= deadline:
                    raise RuntimeError(json.dumps({"pub": {p.producer:p.stats for p in publishers},
                        "client": {k:(v._last_ack_phase,v._last_ack_reason,v._faulted) for k,v in self._clients.items()},
                        "invalid": self._invalidations[-10:]}))
                await asyncio.sleep(.001)
        await wait_durable_first()

        storage_stats = {"records": 0, "market": 0, "facts": 0, "deduplicated": 0}
        def storage_step():
            while not self._stop_storage.is_set():
                stats = self._storage.step()
                for key, value in stats.items(): storage_stats[key] += value
                time.sleep(.002)
        storage_future = self._store_executor.submit(storage_step)
        await asyncio.wait_for(asyncio.to_thread(self._flush_entered.wait, 10), 12)

        # Actual ArchiveMaintenance lease/catalogue/sidecar path. Backend is a
        # local filesystem subclass and its put_file gate adds no external I/O.
        archive_started = threading.Event()
        archive_release = threading.Event()
        self._archive_release = archive_release
        class GatedLocalBackend(LocalStorageBackend):
            def put_file(inner, source, key, *, sha256):
                self._gate_start_mono_ns.setdefault("archive",time.monotonic_ns())
                archive_started.set()
                if not archive_release.wait(self.duration_seconds + 10):
                    raise TimeoutError("bounded PERF-01 archive gate timed out")
                return super().put_file(source, key, sha256=sha256)
        archive_store = RuntimeStore(seed_settings, self.root / "seed-catalogue.sqlite")
        maintenance = ArchiveMaintenance(settings, archive_store)
        maintenance._backend = GatedLocalBackend(self.root / "archive-local")
        archive_task = asyncio.create_task(asyncio.to_thread(maintenance._upload, seed_rows[0]))
        await asyncio.wait_for(asyncio.to_thread(archive_started.wait, 10), 12)

        # Actual TelegramController._send_direct/_api control flow with an
        # awaitable fake session; its request never leaves this process.
        telegram_started = asyncio.Event()
        telegram_release = asyncio.Event()
        self._telegram_release = telegram_release
        class FakeResponse:
            status = 200
            async def __aenter__(self):
                self_owner._gate_start_mono_ns.setdefault("telegram",time.monotonic_ns())
                telegram_started.set()
                await telegram_release.wait()
                return self
            async def __aexit__(self, *_): return False
            def raise_for_status(self): return None
            async def json(self): return {"ok": True, "result": {"message_id": 1}}
        class FakeSession:
            def get(self, *_args, **_kwargs): return FakeResponse()
        from operations.telegram import TelegramController
        telegram = TelegramController(settings)
        self_owner = self
        telegram_task = asyncio.create_task(telegram._send_direct(FakeSession(), "fixture-chat", "perf01 bounded local gate"))
        await asyncio.wait_for(telegram_started.wait(), 10)

        # Continue the bounded producer streams concurrently during the
        # controlled downstream/storage stall. TOSS is one native closed bar;
        # other cohorts contain 100 frames each.
        start_ns = time.monotonic_ns()
        interval = self.duration_seconds / max(1, max(self.target_counts.values()) - 1)
        async def feed_source(publisher, spec):
            for index in range(2, self.target_counts[spec[0]] + 1):
                await publish_row(publisher, spec, index)
                delay = interval - 0.0001
                if delay > 0:
                    await asyncio.sleep(delay)
        feeds_task = asyncio.gather(*(feed_source(p, s) for p, s in zip(publishers, self.sources)))
        await feeds_task
        stall_elapsed = (time.monotonic_ns() - start_ns) / 1e9
        async def wait_applied_cut():
            deadline = asyncio.get_running_loop().time() + 30
            while any(p.stats["last_applied_sequence"] < self.target_counts[p.producer] for p in publishers):
                if asyncio.get_running_loop().time() >= deadline:
                    raise RuntimeError(json.dumps({"publisher": {p.producer:p.stats for p in publishers},
                        "clients": {k:[(v._accepted,v._applied,v._last_ack_phase,v._last_ack_reason)
                            for v in rows] for k,rows in self._client_attempts.items()}}))
                await asyncio.sleep(.002)
        await wait_applied_cut()
        storage_step_totals_before_release=dict(storage_stats)
        pressure_before_release = {
            publisher.producer:{"accepted":publisher.stats["accepted"],
                "durable":publisher.stats["durable"],
                "applied_cursor":publisher.stats["last_applied_sequence"],
                "live_ack_count":publisher.stats["applied"],
                "durable_applied_lag_records":publisher.stats["durable_applied_lag_records"]}
            for publisher in publishers}
        downstream_pending_before_release={"parquet_flush_blocked":self._flush_entered.is_set()
                and not self._flush_release.is_set(),
            "archive_upload_pending":not archive_task.done(),
            "telegram_send_pending":not telegram_task.done()}
        client_sessions_at_ack_cut = {name:[{"accepted": client._accepted,
            "applied_ack_count": client._applied, "last_ack_phase": client._last_ack_phase,
            "last_ack_reason": client._last_ack_reason,
            "faulted_before_intentional_close": client._faulted}
            for client in attempts] for name,attempts in self._client_attempts.items()}
        latest_gate_start = max(self._gate_start_mono_ns.values())
        gate_deadline = latest_gate_start + int(self.duration_seconds * 1_000_000_000)
        remaining_gate_ns = gate_deadline - time.monotonic_ns()
        if remaining_gate_ns > 0:
            await asyncio.sleep(remaining_gate_ns / 1_000_000_000)
        gate_release_mono_ns = time.monotonic_ns()
        self._flush_release.set(); archive_release.set(); telegram_release.set()
        await asyncio.wait_for(archive_task, self.duration_seconds + 15)
        await asyncio.wait_for(telegram_task, self.duration_seconds + 15)
        self._stop_storage.set()
        await asyncio.wrap_future(storage_future)
        # Drain local storage checkpoints through the complete six-source cut.
        def drain_storage():
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                progressed = False
                for source in sources:
                    progress = self._storage.progress(source.producer, source.incarnation)
                    if progress is None or progress["sequence"] < self.target_counts[source.producer]:
                        result = self._storage.step()
                        for key, value in result.items(): storage_stats[key] += value
                        progressed = True
                if not progressed: return
                time.sleep(.002)
            raise TimeoutError("storage consumer did not drain all source prefixes")
        await asyncio.wrap_future(self._store_executor.submit(drain_storage))
        storage_checkpoints = await asyncio.wrap_future(self._store_executor.submit(lambda: {
            source.producer: (dict(self._storage.progress(source.producer, source.incarnation))
                if self._storage.progress(source.producer, source.incarnation) is not None else None)
            for source in sources}))
        await asyncio.wait_for(asyncio.gather(*(p._settled.wait() for p in publishers)), 15)
        await asyncio.gather(*(p.close(timeout_seconds=10) for p in publishers))
        for publisher in publishers:
            self._counts[publisher.producer]["applied"] = publisher.stats["last_applied_sequence"]
        worker_stats = await asyncio.wrap_future(self._store_executor.submit(self._storage.close))
        self._store_executor.shutdown(wait=True)
        manifests = archive_store.list_manifest(active_only=True)
        archive_upload = {"completed": archive_task.done(), "seed_sha256": seed_rows[0]["file_sha256"],
            "local_backend": True, "external_provider_calls": 0}
        archive_store.close()
        maintenance.close()
        return {
            "status": "MEASURED_LOCAL_AUX", "normalized_native_equivalent_stubs": [
                {"source": row[0], "venue":row[1], "provider": row[2]} for row in self.sources],
            "counts_by_source": {row[0]: dict(self._counts[row[0]]) for row in self.sources},
            "target_count_by_source": dict(self.target_counts),
            "publisher_final": {p.producer: p.stats for p in publishers},
            "client_attempts_at_ack_cut": client_sessions_at_ack_cut,
            "storage": {"step_totals": storage_stats, "worker_close": worker_stats,
                "manifest_rows": len(manifests), "source_checkpoint_sequences": self.target_counts,
                "source_progress": storage_checkpoints,
                "storage_step_totals_before_release":storage_step_totals_before_release,
                "all_sources_checkpointed": all(storage_checkpoints[name] is not None
                    and storage_checkpoints[name]["sequence"] == count
                    for name,count in self.target_counts.items())},
            "paper_mm": {"strategy_id": "example_crypto_order_policy",
                "job_callbacks": self._paper_runtime.report()["strategies"]["example_crypto_order_policy"]["callbacks"],
                "job_actions": self._paper_runtime.report()["strategies"]["example_crypto_order_policy"]["actions"],
                "job_state": self._paper_runtime.report()["strategies"]["example_crypto_order_policy"]["state"],
                "job_reason": self._paper_runtime.report()["strategies"]["example_crypto_order_policy"]["reason"],
                "portfolio_orders": len(self._paper_ledger.report()["orders"]),
                "real_owner_or_external_calls": 0,
                "binance_eth_market_callback_status": "MEASURED"},
            "downstream_gates": {"parquet_actual_flush_block": True,
                "archive_actual_maintenance_local_backend": archive_upload,
                "telegram_actual_controller_fake_session": {"completed": telegram_task.done(), "external_calls": 0},
                "stall_target_seconds": self.duration_seconds, "ingress_elapsed_seconds": stall_elapsed,
                "gate_start_mono_ns": dict(self._gate_start_mono_ns),
                "gate_hold_seconds": {name:(gate_release_mono_ns-stamp)/1e9
                    for name,stamp in self._gate_start_mono_ns.items()},
                "gate_release_mono_ns": gate_release_mono_ns,
                "measured_after_setup_count_by_source": {name:max(0,count-1)
                    for name,count in self.target_counts.items()},
                "producer_receipts_before_release":pressure_before_release,
                "downstream_pending_before_release":downstream_pending_before_release,
                "ingress_wallclock_start_mono_ns": start_ns},
            "invalidations": list(self._invalidations), "external_calls": 0,
            "live_orders": 0, "cross_process_clock_subtraction": False,
        }

    async def close(self):
        if self._closed:
            return
        self._closed = True
        self._flush_release.set()
        if hasattr(self,"_archive_release"): self._archive_release.set()
        if hasattr(self,"_telegram_release"): self._telegram_release.set()
        self._stop_storage.set()
        if self._publishers:
            await asyncio.gather(*(p.close(timeout_seconds=5) for p in self._publishers), return_exceptions=True)
        if self._store_executor is not None:
            self._store_executor.shutdown(wait=True, cancel_futures=True)
        if self._paper_ledger is not None:
            self._paper_ledger.close()


async def run_fault_scenarios(root):
    """Bounded real spool fault evidence. No process-wide disk or OS patching."""
    from storage.market_spool import MarketSpool, SpoolCursor, SpoolReader
    from core.models import AssetClass, InstrumentId, TradeEvent, Venue
    from feeds.local_pipeline import DeliveryBudgets, PipelineBudgets, SpoolingMarketPublisher
    from market.hot_path import HotPathStore
    from market.local_engine import EngineBudgets, LocalMarketEngine
    from runtime.local_transport import LocalTransportClient, LocalTransportServer, TransportBudgets
    root = Path(root) / "fault-scenarios"
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    instrument = InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_PERP,
        raw_symbol="ETHUSDT", std_symbol="ETHUSDT", currency="USDT")
    def raw(sequence):
        return TradeEvent(instrument=instrument, venue=Venue.BINANCE,
            provider="perf01-fault-fixture", event_id=f"perf01-fault:{sequence}",
            source_ts_ns=time.time_ns(), receive_ts_ns=time.time_ns(), wallclock_ts_ns=time.time_ns(),
            sequence=sequence, price=Decimal("100"), quantity=Decimal("0.01")).model_dump_json().encode()

    outcomes = {}

    async def pipeline_fault(label, number):
        producer, incarnation = "pipeline-fault", f"{label.lower()}-g1"
        spool_root = root / ("pipeline-" + label.lower())
        engine_calls, invalidations = [], []
        post_apply_calls=[]
        engine = LocalMarketEngine(producer_providers={producer: "binance-perp"},
            budgets=EngineBudgets(max_producers=2, max_instruments=4, max_frame_bytes=4096,
                max_records=32, max_bytes=262144, max_age_ns=60_000_000_000,
                max_digests_per_feed=128, max_state_callbacks=2,
            feed_lease_ns=60_000_000_000, tick_seconds=.01),
            on_invalidate=lambda *row: invalidations.append(row), hot_path=HotPathStore(),
            post_apply_callbacks=(lambda result,snapshot:post_apply_calls.append(
                (result.event.event_id,snapshot.phase,result.applied)),),snapshot_clock=time.time_ns,
            verified_fresh_start=True, fresh_starts={producer: incarnation}, clock=time.time_ns)
        engine._fixture_apply_count = 0
        socket_root=Path("/tmp")/f"pfa-{os.getpid()}-{label.lower()}"
        socket_root.mkdir(mode=0o700,parents=True,exist_ok=True)
        endpoint = str(socket_root / "s")
        transport_budgets = TransportBudgets(4096, 512, 128, 262144, 15.0, 32, 32768, 4, 5.0)
        def on_frame(frame):
            engine_calls.append(frame.cursor.sequence)
            engine._fixture_apply_count += 1
            return engine.on_frame(frame)
        server = LocalTransportServer(endpoint, budgets=transport_budgets,
            on_frame=on_frame, on_gap=engine.on_gap, on_resume=engine.resume_receipt,
            on_replay=engine.apply_replay, on_recovery_query=engine.begin_delivery_recovery,
            on_recovery_commit=lambda cursor,digest: engine.commit_recovery(cursor,digest,ready=True),
            allowed_uid=os.getuid())
        await server.start()
        opened = {}
        def spool_factory():
            spool = MarketSpool(spool_root, producer, incarnation, max_disk_bytes=1_000_000,
                max_records=16, max_frame_bytes=4096, max_header_bytes=512,
                segment_max_bytes=16384, segment_max_records=8, segment_max_age_seconds=60)
            original_flush=spool.flush
            def fail_flush():
                raise OSError(number, os.strerror(number))
            spool.flush = fail_flush
            opened["spool"] = spool
            opened["original_flush"] = original_flush
            return spool
        def reader_factory(cursor):
            return SpoolReader(spool_root, producer, incarnation,
                after=SpoolCursor(cursor.producer,cursor.incarnation,cursor.sequence),
                max_disk_bytes=1_000_000,max_records=16,max_frame_bytes=4096,max_header_bytes=512)
        attempts=[]
        def transport_factory():
            client=LocalTransportClient(endpoint,producer=producer,incarnation=incarnation,
                budgets=transport_budgets,on_gap=lambda *row: invalidations.append(row),allowed_uid=os.getuid())
            attempts.append(client);return client
        publisher=SpoolingMarketPublisher(producer=producer,incarnation=incarnation,
            spool_factory=spool_factory,transport=None,
            budgets=PipelineBudgets(16,65536,4096,8,2),env={},
            on_invalid=lambda reason: invalidations.append((reason,)),
            delivery_budgets=DeliveryBudgets(4,16384,.001,.05,5.0),
            reader_factory=reader_factory,transport_factory=transport_factory)
        caught=None
        try:
            await publisher.connect()
            deadline=asyncio.get_running_loop().time()+5
            while (not attempts or not attempts[-1].valid) and asyncio.get_running_loop().time()<deadline:
                await asyncio.sleep(.001)
            await publisher.publish_market_event(_normalized_event(("pipeline-fault","binance","binance-perp","ETHUSDT","ETHUSDT","USDT","crypto_perp"),1))
            await asyncio.wait_for(publisher._failure.wait(),5)
            caught=publisher.stats["fault_reason"]
            stats=publisher.stats
            appended=opened["spool"].appended_cursor.sequence
            durable=opened["spool"].durable_cursor.sequence
            applied_callbacks=list(engine_calls)
            actual_post_callbacks=list(post_apply_calls)
            before_close={"attempts":len(attempts),"last_faulted":attempts[-1]._faulted if attempts else None}
        finally:
            if "original_flush" in opened:
                opened["spool"].flush=opened["original_flush"]
            try:
                await publisher.close(timeout_seconds=5)
            except RuntimeError:
                # The expected injected pre-DUR failure leaves ingress
                # unconfirmed; preserve that fact in the returned fault row.
                pass
            await server.close()
            import shutil
            shutil.rmtree(socket_root,ignore_errors=True)
        return {"actual_spooling_publisher_uds_engine":True,"injected_before_marketspool_flush":True,
            "errno":label,"publisher_fault_reason":caught,"publisher_appended":stats["appended"],
            "publisher_durable":stats["durable"],"spool_appended_cursor":appended,
            "spool_durable_cursor":durable,
            "engine_on_frame_count":len(applied_callbacks),"engine_applied":engine.stats["applied"],
            "engine_post_apply_callback_count":len(actual_post_callbacks),
            "watermark_capture_stage":"failure-before-cleanup-restore",
            "transport_sessions_before_intentional_close":before_close,
            "invalidations":invalidations,"claim":"local pre-DUR failure only; no Owner claim"}

    for label, number in (("ENOSPC", errno.ENOSPC), ("EIO", errno.EIO)):
        outcomes[label] = await pipeline_fault(label, number)

    # Real UDS startup recovery over an already-flushed prefix. Replay warms
    # only LocalMarketEngine state; a subsequent exact duplicate is harmless,
    # and one fresh post-commit frame reaches the live callback once.
    restart_root=root/"pipeline-restart"
    producer,incarnation="pipeline-replay","prefix-g1"
    replay_spool_root=restart_root/"spool"
    replay_spool_root.mkdir(parents=True,exist_ok=True)
    os.chmod(replay_spool_root,0o700)
    prefix_events=[TradeEvent(instrument=instrument,venue=Venue.BINANCE,
        provider="binance-perp",event_id=f"pipeline-prefix:{i}",native_event_id=f"native:{i}",
        source_ts_ns=time.time_ns(),receive_ts_ns=time.time_ns(),wallclock_ts_ns=time.time_ns(),
        sequence=i,price=Decimal("100"),quantity=Decimal("0.01")) for i in (1,2)]
    prefix_spool=MarketSpool(replay_spool_root,producer,incarnation,max_disk_bytes=1_000_000,
        max_records=16,max_frame_bytes=4096,max_header_bytes=512,
        segment_max_bytes=16384,segment_max_records=8,segment_max_age_seconds=60)
    for event in prefix_events: prefix_spool.append(event.model_dump_json().encode())
    prefix_floor=prefix_spool.flush();prefix_spool.close()
    source_rows=[]
    engine=LocalMarketEngine(producer_providers={producer:"binance-perp"},
        budgets=EngineBudgets(max_producers=2,max_instruments=4,max_frame_bytes=4096,
            max_records=32,max_bytes=262144,max_age_ns=60_000_000_000,
            max_digests_per_feed=128,max_state_callbacks=2,feed_lease_ns=60_000_000_000,
            tick_seconds=.01),on_invalidate=lambda *_:None,hot_path=HotPathStore(),
        post_apply_callbacks=(lambda result,snapshot: source_rows.append(
            (result.event.event_id,snapshot.phase,result.applied,result.duplicate)),),snapshot_clock=time.time_ns,
        verified_fresh_start=True,fresh_starts={producer:incarnation},clock=time.time_ns)
    socket_root=Path("/tmp")/f"pfr-{os.getpid()}"
    socket_root.mkdir(mode=0o700,parents=True,exist_ok=True)
    replay_endpoint=str(socket_root/"s")
    replay_transport=TransportBudgets(4096,512,128,262144,15.0,32,32768,4,5.0)
    replay_server=LocalTransportServer(replay_endpoint,budgets=replay_transport,
        on_frame=engine.on_frame,on_gap=engine.on_gap,on_resume=engine.resume_receipt,
        on_replay=engine.apply_replay,on_recovery_query=engine.begin_delivery_recovery,
        on_recovery_commit=lambda cursor,digest:engine.commit_recovery(cursor,digest,ready=True),
        allowed_uid=os.getuid())
    await replay_server.start()
    client_rows=[]
    def replay_transport_factory():
        client=LocalTransportClient(replay_endpoint,producer=producer,incarnation=incarnation,
            budgets=replay_transport,on_gap=lambda *_:None,allowed_uid=os.getuid())
        client_rows.append(client);return client
    def replay_spool_factory():
        return MarketSpool(replay_spool_root,producer,incarnation,max_disk_bytes=1_000_000,
            max_records=16,max_frame_bytes=4096,max_header_bytes=512,
            segment_max_bytes=16384,segment_max_records=8,segment_max_age_seconds=60)
    def replay_reader_factory(cursor):
        return SpoolReader(replay_spool_root,producer,incarnation,
            after=SpoolCursor(cursor.producer,cursor.incarnation,cursor.sequence),
            max_disk_bytes=1_000_000,max_records=16,max_frame_bytes=4096,max_header_bytes=512)
    replay_publisher=SpoolingMarketPublisher(producer=producer,incarnation=incarnation,
        spool_factory=replay_spool_factory,transport=None,
        budgets=PipelineBudgets(16,65536,4096,8,2),env={},
        on_invalid=lambda *_:None,delivery_budgets=DeliveryBudgets(4,16384,.001,.05,5.0),
        reader_factory=replay_reader_factory,transport_factory=replay_transport_factory,
        resume_existing=True)
    try:
        await replay_publisher.connect()
        deadline=asyncio.get_running_loop().time()+10
        while (engine.stats["replayed"]<2 or engine.stats["feeds"][producer]["mode"]!="LIVE"):
            if asyncio.get_running_loop().time()>=deadline:
                raise TimeoutError("real UDS replay did not commit retained prefix")
            await asyncio.sleep(.002)
        after_prefix=engine.stats
        live_rows_before_new=len([row for row in source_rows if row[1]=="LIVE" and row[2]])
        duplicate_event=prefix_events[0]
        fresh_event=TradeEvent(instrument=instrument,venue=Venue.BINANCE,
            provider="binance-perp",event_id="pipeline-prefix:fresh-3",native_event_id="native:fresh-3",
            source_ts_ns=time.time_ns(),receive_ts_ns=time.time_ns(),wallclock_ts_ns=time.time_ns(),
            sequence=3,price=Decimal("100.1"),quantity=Decimal("0.01"))
        await replay_publisher.publish_market_event(duplicate_event)
        await replay_publisher.publish_market_event(fresh_event)
        deadline=asyncio.get_running_loop().time()+10
        while replay_publisher.stats["last_applied_sequence"]<4:
            if asyncio.get_running_loop().time()>=deadline:
                raise TimeoutError("live duplicate/fresh delivery did not reach cursor four")
            await asyncio.sleep(.002)
        live_callbacks=[row for row in source_rows if row[1]=="LIVE" and row[2]]
        hotpath_duplicates=[row for row in source_rows if row[3]]
        outcomes["durable_prefix_restart"]={"actual_publisher_uds_engine":True,
            "durable_high_water":prefix_floor.sequence,"prefix_sha256":[hashlib.sha256(e.model_dump_json().encode()).hexdigest() for e in prefix_events],
            "replayed":after_prefix["replayed"],"replay_state_applied_rows":after_prefix["applied"],
            "live_rows_before_new_rows":live_rows_before_new,
            "callback_phase_rows_before_new_rows":source_rows[:len(source_rows)],
            "duplicate_frame_applied_sequence":3,"hotpath_exact_duplicate_count":len(hotpath_duplicates),
            "fresh_after_commit_live_callbacks":len(live_callbacks),
            "fresh_callback_event_ids":[row[0] for row in live_callbacks],
            "final_feed_cursor":engine.stats["feeds"][producer]["applied_sequence"],
            "final_feed_mode":engine.stats["feeds"][producer]["mode"],
            "transport_session_faulted_before_intentional_close":client_rows[-1]._faulted if client_rows else None,
            "claim":"local retained-prefix replay/fresh handoff only; no Owner rewind"}
    finally:
        await replay_publisher.close(timeout_seconds=5)
        await replay_server.close()
        import shutil
        shutil.rmtree(socket_root,ignore_errors=True)

    for label, number in (("ENOSPC", errno.ENOSPC), ("EIO", errno.EIO)):
        spool_root = root / label
        spool = MarketSpool(spool_root, "fault-source", label.lower(), max_disk_bytes=1_000_000,
            max_records=16, max_frame_bytes=4096, max_header_bytes=512,
            segment_max_bytes=16384, segment_max_records=8, segment_max_age_seconds=60)
        original_append = spool.append
        def injected(payload, code=number):
            raise OSError(code, os.strerror(code))
        spool.append = injected
        durable_before = spool.durable_cursor.sequence
        caught = None
        try:
            spool.append(raw(1))
        except OSError as exc:
            caught = errno.errorcode.get(exc.errno, "OSError")
        durable_after = spool.durable_cursor.sequence
        spool.append = original_append
        spool.close()
        outcomes[label+"_direct_spool_microcase"] = {"injected_at_actual_market_spool_append": True,
            "exception": caught, "durable_before": durable_before,
            "durable_after": durable_after, "engine_callbacks": 0,
            "claim": "direct append boundary only; no UDS callback ran in this microcase"}

    # Real append/flush, prefix reopen/readback, and temporary-tail corruption.
    restart_root = root / "restart-prefix"
    spool = MarketSpool(restart_root, "prefix-source", "prefix-g1", max_disk_bytes=1_000_000,
        max_records=16, max_frame_bytes=4096, max_header_bytes=512,
        segment_max_bytes=16384, segment_max_records=8, segment_max_age_seconds=60)
    payloads = [raw(1), raw(2)]
    for payload in payloads: spool.append(payload)
    spool.flush(); spool.close()
    reader = SpoolReader(restart_root, "prefix-source", "prefix-g1",
        max_disk_bytes=1_000_000, max_records=16, max_frame_bytes=4096, max_header_bytes=512)
    replayed = reader.read_batch(max_records=16, max_bytes=16384)
    prefix_hashes = [hashlib.sha256(item.payload).hexdigest() for item in replayed]
    segments = list(restart_root.glob("*.sealed"))
    corrupt_result = {"failed_closed": False, "error_type": None}
    if segments:
        original = segments[0].read_bytes()
        segments[0].write_bytes(original[:-1] + bytes([original[-1] ^ 0x01]))
        try:
            SpoolReader(restart_root, "prefix-source", "prefix-g1",
                max_disk_bytes=1_000_000, max_records=16, max_frame_bytes=4096,
                max_header_bytes=512).read_batch(max_records=16, max_bytes=16384)
        except Exception as exc:
            corrupt_result = {"failed_closed": True, "error_type": type(exc).__name__}
        finally:
            # The directory is private fixture-owned; restore exact bytes before cleanup.
            segments[0].write_bytes(original)

    # A second, publisher-level corrupt-tail attempt fences the source owner.
    # Only this fixture's final sealed segment is modified and restored after
    # the fail-closed result has been captured.
    corrupt_root=root/"corrupt-source"
    corrupt_root.mkdir(parents=True,exist_ok=True);os.chmod(corrupt_root,0o700)
    corrupt_producer,corrupt_incarnation="corrupt-source","corrupt-g1"
    corrupt_spool=MarketSpool(corrupt_root,corrupt_producer,corrupt_incarnation,
        max_disk_bytes=1_000_000,max_records=16,max_frame_bytes=4096,max_header_bytes=512,
        segment_max_bytes=16384,segment_max_records=1,segment_max_age_seconds=60)
    from storage.market_spool import SpoolRotateRequired
    for payload in payloads:
        try: corrupt_spool.append(payload)
        except SpoolRotateRequired:
            corrupt_spool.seal();corrupt_spool.append(payload)
    corrupt_spool.flush();corrupt_spool.seal();corrupt_spool.close()
    verified_prefix=SpoolReader(corrupt_root,corrupt_producer,corrupt_incarnation,
        max_disk_bytes=1_000_000,max_records=16,max_frame_bytes=4096,
        max_header_bytes=512).read_batch(max_records=16,max_bytes=16384)
    corrupt_segments=sorted(corrupt_root.glob("*.sealed"))
    corrupt_path=corrupt_segments[-1]
    intact_bytes=corrupt_path.read_bytes()
    corrupt_path.write_bytes(intact_bytes[:-1]+bytes([intact_bytes[-1]^0x01]))
    invalidations=[];corrupt_engine=LocalMarketEngine(
        producer_providers={corrupt_producer:"binance-perp"},
        budgets=EngineBudgets(max_producers=2,max_instruments=4,max_frame_bytes=4096,
            max_records=32,max_bytes=262144,max_age_ns=60_000_000_000,
            max_digests_per_feed=128,max_state_callbacks=2,feed_lease_ns=60_000_000_000,
            tick_seconds=.01),on_invalidate=lambda *row:invalidations.append(row),
        hot_path=HotPathStore(),verified_fresh_start=True,
        fresh_starts={corrupt_producer:corrupt_incarnation},clock=time.time_ns)
    corrupt_socket_root=Path("/tmp")/f"pfc-{os.getpid()}"
    corrupt_socket_root.mkdir(mode=0o700,parents=True,exist_ok=True)
    corrupt_endpoint=str(corrupt_socket_root/"s")
    corrupt_server=LocalTransportServer(corrupt_endpoint,budgets=replay_transport,
        on_frame=corrupt_engine.on_frame,on_gap=corrupt_engine.on_gap,
        on_resume=corrupt_engine.resume_receipt,on_replay=corrupt_engine.apply_replay,
        on_recovery_query=corrupt_engine.begin_delivery_recovery,
        on_recovery_commit=lambda cursor,digest:corrupt_engine.commit_recovery(cursor,digest,ready=True),
        allowed_uid=os.getuid())
    await corrupt_server.start()
    corrupt_notice=[]
    def invalidate_corrupt_source(reason):
        corrupt_notice.append(reason)
        corrupt_engine.invalidate(corrupt_producer,"spool_corruption:"+str(reason))
    def corrupt_transport_factory():
        return LocalTransportClient(corrupt_endpoint,producer=corrupt_producer,
            incarnation=corrupt_incarnation,budgets=replay_transport,
            on_gap=lambda *_:None,allowed_uid=os.getuid())
    corrupt_publisher=SpoolingMarketPublisher(producer=corrupt_producer,
        incarnation=corrupt_incarnation,
        spool_factory=lambda:MarketSpool(corrupt_root,corrupt_producer,corrupt_incarnation,
            max_disk_bytes=1_000_000,max_records=16,max_frame_bytes=4096,max_header_bytes=512,
            segment_max_bytes=16384,segment_max_records=1,segment_max_age_seconds=60),
        transport=None,budgets=PipelineBudgets(16,65536,4096,8,2),env={},
        on_invalid=invalidate_corrupt_source,
        delivery_budgets=DeliveryBudgets(4,16384,.001,.05,5.0),
        reader_factory=lambda cursor:SpoolReader(corrupt_root,corrupt_producer,
            corrupt_incarnation,after=SpoolCursor(cursor.producer,cursor.incarnation,cursor.sequence),
            max_disk_bytes=1_000_000,max_records=16,max_frame_bytes=4096,max_header_bytes=512),
        transport_factory=corrupt_transport_factory,resume_existing=True)
    reader_error=None
    try:
        try: await corrupt_publisher.connect()
        except BaseException as exc: reader_error=type(exc).__name__
        if reader_error is None:
            try: await asyncio.wait_for(corrupt_publisher._failure.wait(),5)
            except asyncio.TimeoutError: pass
        reader_result={"pre_corruption_verified_rows":len(verified_prefix),
            "pre_corruption_durable_sequence":len(verified_prefix),
            "corrupted_segment":corrupt_path.name,"reader_error_type":reader_error,
            "source_invalid_reasons":list(corrupt_notice),"engine_source_mode":
                corrupt_engine.stats["feeds"][corrupt_producer]["mode"],
            "engine_source_cursor":corrupt_engine.stats["feeds"][corrupt_producer]["applied_sequence"],
            "engine_live_callbacks":0,"exact_loss_after_corruption":"UNKNOWN; sealed tail corrupt",
            "claim":"publisher-level fail-closed source invalidation; prefix loss not inferred"}
    finally:
        try: await corrupt_publisher.close(timeout_seconds=5)
        except BaseException: pass
        await corrupt_server.close()
        corrupt_path.write_bytes(intact_bytes)
        import shutil
        shutil.rmtree(corrupt_socket_root,ignore_errors=True)
    outcomes["corrupt_source_tail"] = reader_result
    outcomes["durable_prefix_reader_microcase"] = {"written": len(payloads), "replayed": len(replayed),
        "prefix_sha256": prefix_hashes, "corrupt_tail": corrupt_result,
        "scope": "actual MarketSpool append/flush and SpoolReader replay; no Owner/T5 claim"}
    return {"status": "MEASURED_PIPELINE_FAULT_AND_REPLAY", "scenarios": outcomes,
        "temporary_root_only": str(root), "source_rows": 2,
        "claims_excluded": ["engine callback suppression for these direct append microcases",
            "Owner mutation recovery", "live or external storage"]}


async def _run_aux_smoke(root):
    from market.hot_path import HotPathStore
    from market.local_engine import EngineBudgets, LocalMarketEngine
    from runtime.local_transport import LocalTransportServer, TransportBudgets
    root = Path(root)
    aux = PerfAuxLoad(root / "aux", HotPathStore(), duration_seconds=.15)
    invalid = []
    transport = TransportBudgets(8192, 1024, 256, 4 * 1024 * 1024, 15.0,
        128, 262144, 8, 5.0)
    engine = LocalMarketEngine(producer_providers=aux.producer_providers,
        budgets=EngineBudgets(max_producers=8, max_instruments=16, max_frame_bytes=8192,
            max_records=512, max_bytes=4 * 1024 * 1024, max_age_ns=60_000_000_000,
            max_digests_per_feed=4096, max_state_callbacks=4,
            feed_lease_ns=600_000_000_000, tick_seconds=.01),
        on_invalidate=lambda *row: invalid.append(row), hot_path=aux.hot_path,
        post_apply_callbacks=(aux.post_apply,), snapshot_clock=time.time_ns,
        verified_fresh_start=True, fresh_starts=aux.fresh_starts, clock=time.time_ns)
    endpoint = str(root / "aux.sock")
    server_errors = []
    def apply_frame(frame):
        try:
            return engine.on_frame(frame)
        except BaseException as exc:
            server_errors.append((type(exc).__name__, str(exc)[:200]))
            raise
    server = LocalTransportServer(endpoint, budgets=transport,
        on_frame=apply_frame, on_gap=engine.on_gap, on_resume=engine.resume_receipt,
        on_replay=engine.apply_replay,
        on_recovery_query=engine.begin_delivery_recovery,
        on_recovery_commit=lambda cursor, digest: engine.commit_recovery(
            cursor, digest, ready=True),
        allowed_uid=os.getuid())
    await server.start()
    try:
        report = await aux.run(endpoint, transport)
        faults = await run_fault_scenarios(root)
        return report, faults, engine.stats, invalid, server_errors
    finally:
        await aux.close()
        await server.close()


def test_perf01_aux_load_and_fault_fixtures_are_real_bounded_smokes(tmp_path):
    report, faults, engine_stats, invalid, server_errors = asyncio.run(_run_aux_smoke(tmp_path))
    assert report["status"] == "MEASURED_LOCAL_AUX"
    expected = tuple(report["target_count_by_source"].values())
    assert all(row["published"] == aux_count for row, aux_count in zip(
        report["counts_by_source"].values(), expected))
    assert all(row["applied"] == aux_count for row, aux_count in zip(
        report["counts_by_source"].values(), expected)), json.dumps({"publisher": report["publisher_final"], "clients": report["client_attempts_at_ack_cut"], "server_errors": server_errors, "invalid": invalid})
    assert report["paper_mm"]["job_callbacks"] > 0
    assert report["paper_mm"]["portfolio_orders"] > 0, json.dumps(report["paper_mm"])
    assert report["storage"]["all_sources_checkpointed"] is True
    assert all(seconds >= .15 for seconds in report["downstream_gates"]["gate_hold_seconds"].values()), json.dumps(report["downstream_gates"])
    assert all(report["downstream_gates"]["downstream_pending_before_release"].values())
    assert all(row["applied_cursor"] == expected_count for row,expected_count in zip(
        report["downstream_gates"]["producer_receipts_before_release"].values(),expected))
    assert faults["scenarios"]["ENOSPC_direct_spool_microcase"]["durable_after"] == 0
    assert faults["scenarios"]["EIO_direct_spool_microcase"]["durable_after"] == 0
    for kind in ("ENOSPC", "EIO"):
        fault = faults["scenarios"][kind]
        assert fault["actual_spooling_publisher_uds_engine"] is True
        assert fault["spool_durable_cursor"] == fault["engine_applied"] == 0
        assert fault["spool_appended_cursor"] == 1
        assert fault["engine_on_frame_count"] == 0
        assert fault["engine_post_apply_callback_count"] == 0
        assert fault["watermark_capture_stage"] == "failure-before-cleanup-restore"
    assert faults["scenarios"]["durable_prefix_restart"]["replayed"] == 2
    replay = faults["scenarios"]["durable_prefix_restart"]
    assert replay["actual_publisher_uds_engine"] is True
    assert replay["replay_state_applied_rows"] == 2
    assert replay["live_rows_before_new_rows"] == 0, json.dumps(replay)
    assert replay["hotpath_exact_duplicate_count"] == 1
    assert replay["fresh_after_commit_live_callbacks"] == 1
    assert faults["scenarios"]["durable_prefix_reader_microcase"]["corrupt_tail"]["failed_closed"] is True
    assert faults["scenarios"]["corrupt_source_tail"]["engine_source_mode"] == "INVALID"
    assert faults["scenarios"]["corrupt_source_tail"]["engine_live_callbacks"] == 0
    assert faults["scenarios"]["corrupt_source_tail"]["source_invalid_reasons"]
    assert faults["scenarios"]["corrupt_source_tail"]["exact_loss_after_corruption"] == "UNKNOWN; sealed tail corrupt"
    assert engine_stats["applied"] >= sum(expected)
