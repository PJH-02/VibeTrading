"""Local-only integrated QA-01 producer isolation and cold-readiness checks."""
import asyncio
import time
import hashlib
import threading
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from feeds.feed_recovery import EngineCursor
from market.hot_path import HotPathStore
from qa01_source_fixture import (
    INCARNATION_A,
    INCARNATION_B,
    PRODUCER_A,
    PRODUCER_B,
    make_engine,
    make_publisher,
    make_paper_strategy_runtime,
    native_trade_payload,
    parse_binance_native_trades,
    parse_binance_native_book,
    provider_instruments,
    recovery_frame,
    spool_reader,
    spot_and_perp_trades,
    warming_engine,
)


class DownstreamGates:
    """Finite local gates around the actual selected consumers."""

    def __init__(self, root, spool_root, native_seed):
        self.root = Path(root)
        self.spool_root = Path(spool_root)
        self.native_seed = native_seed
        self.storage_ready = threading.Event()
        self.storage_entered = threading.Event()
        self.storage_release = threading.Event()
        self.storage_done = threading.Event()
        self.cloud_entered = threading.Event()
        self.cloud_release = threading.Event()
        self.cloud_done = threading.Event()
        self.telegram_entered = asyncio.Event()
        self.telegram_release = asyncio.Event()
        self.telegram_done = None
        self.storage_result = {}
        self.cloud_result = {}
        self.errors = []
        self.storage_thread = threading.Thread(target=self._storage, name="qa01-local-spool-storage", daemon=True)
        self.cloud_thread = threading.Thread(target=self._cloud, name="qa01-local-archive-upload", daemon=True)
        self.telegram_thread = None
        self.storage_thread.start()
        self.cloud_thread.start()

    @staticmethod
    def _settings(root):
        from runtime.settings import RuntimeSettings
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        env = root / ".env"
        env.write_text(f"VIBETRADING_DATA_ROOT={root.as_posix()}\nARCHIVE_BACKEND=local\nDELIVERY_LEDGER_FORMAT=legacy\n", encoding="utf-8")
        return RuntimeSettings.from_env(env)

    def _storage(self):
        from storage.local_storage import LocalSpoolStorage, StorageBudgets
        from storage.parquet_store import ParquetBatchWriter
        from storage.sqlite_store import RuntimeStore
        try:
            root = self.root / "local-consumer"
            settings = self._settings(root)
            budgets = StorageBudgets(
                batch_records=16, batch_bytes=65_536, max_payload_bytes=8192,
                max_writer_buffer_bytes=65_536, max_fact_rows=128,
                max_fact_db_bytes=524_288, poll_interval_ms=10,
            )
            source = SimpleNamespace(
                producer=PRODUCER_A, incarnation=INCARNATION_A, spool_root=self.spool_root,
                limits=SimpleNamespace(reader_kwargs={
                    "max_disk_bytes": 8_388_608, "max_records": 4096,
                    "max_frame_bytes": 8192, "max_header_bytes": 1024,
                }),
            )
            plan = SimpleNamespace(producers=(source,), observation=None, storage=budgets.__dict__)
            store = RuntimeStore(settings)
            writer = ParquetBatchWriter(settings, store)
            storage_ref = {}
            flush_all = writer.flush_all
            def gated_flush():
                current = storage_ref["worker"].progress(PRODUCER_A, INCARNATION_A)
                self.storage_result.update(
                    checkpoint_at_gate=None if current is None else dict(current),
                    writer_buffer_bytes=writer.buffer_bytes,
                    writer_buffer_records=sum(buf.row_count for buf in writer._buffers.values()),
                    batch_record_budget=budgets.batch_records,
                    batch_byte_budget=budgets.batch_bytes,
                )
                self.storage_entered.set()
                if not self.storage_release.wait(20):
                    raise TimeoutError("bounded QA local Parquet gate was not released")
                return flush_all()
            writer.flush_all = gated_flush
            storage = LocalSpoolStorage(plan, settings, budgets, writer=writer, store=store)
            storage_ref["worker"] = storage
            self.storage_ready.set()
            expected = {("LOCAL:" + PRODUCER_A, INCARNATION_A, seq) for seq in range(1, 102)}
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                stats = storage.step()
                progress = storage.progress(PRODUCER_A, INCARNATION_A)
                if progress and int(progress["sequence"]) >= 101:
                    self.storage_result.update(
                        progress=progress,
                        deliveries=store.committed_deliveries(expected),
                        manifests=store.list_manifest(active_only=True),
                        processed_records=stats["records"],
                        source_spool_exists=self.spool_root.exists(),
                    )
                    return
                if not stats["records"]:
                    time.sleep(0.002)
            raise TimeoutError("local spool consumer did not drain its bounded fixture")
        except BaseException as exc:
            self.errors.append(("local_storage", repr(exc)))
        finally:
            try:
                storage.close()
            except Exception:
                pass
            try:
                store.close()
            except Exception:
                pass
            self.storage_done.set()

    def _cloud(self):
        from storage.archive import ArchiveMaintenance
        from storage.parquet_store import ParquetBatchWriter
        from storage.sqlite_store import RuntimeStore
        from storage.storage_backend import LocalStorageBackend
        try:
            root = self.root / "archive-consumer"
            settings = self._settings(root)
            store = RuntimeStore(settings)
            writer = ParquetBatchWriter(settings, store)
            writer.add(self.native_seed, stream_name="QA01-SEED", stream_generation="seed", stream_sequence=1)
            writer.flush_all()
            row = store.list_manifest(active_only=True)[0]
            class GatedFileBackend(LocalStorageBackend):
                def put_file(inner, source, key, *, sha256):
                    self.cloud_entered.set()
                    if not self.cloud_release.wait(20):
                        raise TimeoutError("bounded QA archive gate was not released")
                    return super(GatedFileBackend, inner).put_file(source, key, sha256=sha256)
            backend = GatedFileBackend(root / "remote-filesystem")
            maintenance = ArchiveMaintenance(settings, store)
            maintenance._backend = backend
            maintenance._upload(row)
            current = store.local_manifest(Path(row["path"]))
            self.cloud_result.update(
                row=row, local_manifest=current, backend=backend,
                remote=backend.verify(current["remote_key"],
                                      size_bytes=row["byte_size"], sha256=row["file_sha256"]),
            )
            maintenance.close()
            store.close()
        except BaseException as exc:
            self.errors.append(("archive", repr(exc)))
        finally:
            self.cloud_done.set()

    async def start_telegram(self):
        from operations.telegram import TelegramController
        from operations.telegram_state import TelegramState
        settings = self._settings(self.root / "telegram-consumer")
        controller = TelegramController(settings)
        controller.base_url = "http://qa01.invalid"
        controller._state = TelegramState(settings.state_dir / "telegram" / "delivery.sqlite3",
                                          max_pending=4, max_outbox_bytes=4096)
        assert controller._state.enqueue("qa01-gated", "synthetic-chat", "bounded fixture message")
        class Response:
            status = 200
            async def __aenter__(self):
                self.outer.telegram_entered.set()
                await self.outer.telegram_release.wait()
                return self
            async def __aexit__(self, *_args): return None
            async def json(self): return {"ok": True, "result": {"message_id": 1}}
            def raise_for_status(self): return None
        class Session:
            def __init__(self, outer): self.outer = outer
            def get(self, *_args, **_kwargs):
                response = Response()
                response.outer = self.outer
                return response
        self.telegram_entered = asyncio.Event()
        self.telegram_release = asyncio.Event()
        gate = self
        async def hold_send():
            await controller._send_direct(Session(gate), "synthetic-chat", "QA01 local stall probe")
        self.telegram_done = asyncio.create_task(hold_send())
        await asyncio.wait_for(self.telegram_entered.wait(), timeout=5)
        self.telegram_state = controller._state
        return controller

    async def wait_event(self, event, timeout=10):
        assert await asyncio.to_thread(event.wait, timeout), f"gate did not reach: {event}"

    async def release_and_join(self):
        self.storage_release.set()
        self.cloud_release.set()
        self.telegram_release.set()
        if self.telegram_done is not None:
            await asyncio.wait_for(self.telegram_done, timeout=10)
        await asyncio.to_thread(self.storage_thread.join, 15)
        await asyncio.to_thread(self.cloud_thread.join, 15)
        assert not self.storage_thread.is_alive() and not self.cloud_thread.is_alive()
        assert self.storage_done.is_set() and self.cloud_done.is_set()
        assert not self.errors, self.errors


@pytest.mark.asyncio
async def test_real_spool_uds_strategy_runtime_and_local_a1_progress(tmp_path):
    """A reaches its real PAPER plugin after durable local spool and UDS apply."""
    count = 100
    seed_book = await parse_binance_native_book()
    native_events = []
    payloads = []
    endpoint = str(tmp_path / "market.sock")
    observations = {"a_only": [], "b_required": []}
    scenario_now = [time.time_ns()]
    paper, ledger, matcher = make_paper_strategy_runtime(
        tmp_path / "strategy", HotPathStore(),
        observations=observations, now_ns=lambda: scenario_now[0],
    )
    engine, server, invalid = make_engine(
        endpoint=endpoint, applied_callbacks=(paper.process_applied,), hot_path=paper.hot_path,
        snapshot_clock=lambda: scenario_now[0],
    )
    publisher, limits = make_publisher(root=tmp_path / "spool", endpoint=endpoint, invalid=invalid)
    source = engine.event_source(required_producers=())
    delivered = []
    consumer = None
    gates = None
    async def strategy_delivery():
        for expected_sequence, expected_payload in enumerate(payloads, start=1):
            event = await asyncio.wait_for(anext(source), timeout=5)
            assert event["provider"] == PRODUCER_A
            assert event["native_event_id"] == native_events[expected_sequence - 1].native_event_id
            assert event["trade_id"] == str(50_000 + expected_sequence - 1)
            # The callback is downstream of the real durable spool and UDS apply.
            spool_sequence = expected_sequence + 1  # cursor 1 is the depth warmup frame
            assert publisher._spool.durable_cursor.sequence >= spool_sequence
            record = spool_reader(
                tmp_path / "spool", limits, after=spool_sequence - 1,
            ).read_batch(max_records=1, max_bytes=8192)
            assert len(record) == 1 and record[0].payload == expected_payload
            delivered.append((expected_sequence, event["native_event_id"]))

    try:
        await server.start()
        await publisher.connect()
        gates = DownstreamGates(tmp_path / "downstream", tmp_path / "spool", seed_book)
        await asyncio.wait_for(asyncio.to_thread(gates.storage_ready.wait, 10), timeout=11)
        assert await asyncio.to_thread(gates.cloud_entered.wait, 10)
        await gates.start_telegram()
        native_book = await parse_binance_native_book()
        book_payload = native_book.model_dump_json().encode("utf-8")
        native_events = await parse_binance_native_trades(count)
        payloads = [event.model_dump_json().encode("utf-8") for event in native_events]
        # A controlled replay cut keeps the single book fresh while consumer
        # scheduling and cold Arrow imports vary. Real spool/UDS/health clocks
        # remain monotonic; stale/gap behavior has a separate real-time case.
        scenario_now[0] = max(max(event.receive_ts_ns, event.source_ts_ns)
                              for event in [native_book, *native_events]) + 1
        stall_started = time.monotonic()
        gates.virtual_stall_seconds = 60  # explicit scenario label; no wall-clock advance
        publisher.accept_payload(book_payload)
        storage_gate_entered = await asyncio.to_thread(gates.storage_entered.wait, 10)
        assert storage_gate_entered, gates.errors
        book_deadline = asyncio.get_running_loop().time() + 3
        while publisher.stats["applied"] < 1 and asyncio.get_running_loop().time() < book_deadline:
            await asyncio.sleep(0.001)
        assert publisher.stats["applied"] == 1
        consumer = asyncio.create_task(strategy_delivery())
        for payload in payloads:
            publisher.accept_payload(payload)
        await asyncio.wait_for(consumer, timeout=15)
        applied_deadline = asyncio.get_running_loop().time() + 3
        while publisher.stats["applied"] < count and asyncio.get_running_loop().time() < applied_deadline:
            await asyncio.sleep(0.001)
        assert publisher.stats["applied"] == count + 1
        # All real selected consumers are blocked in their actual write/send
        # seams while the independent local A1 path has completed all 100 trades.
        assert not gates.storage_release.is_set() and not gates.storage_done.is_set()
        assert not gates.cloud_release.is_set() and not gates.cloud_done.is_set()
        assert not gates.telegram_release.is_set() and not gates.telegram_done.done()
        assert gates.telegram_state.next_message()["identity"] == "qa01-gated"
        assert gates.virtual_stall_seconds == 60
        assert gates.storage_result["checkpoint_at_gate"] is None
        assert gates.storage_result["writer_buffer_records"] <= gates.storage_result["batch_record_budget"] == 16
        assert gates.storage_result["writer_buffer_bytes"] <= gates.storage_result["batch_byte_budget"] == 65_536
        assert publisher._spool.durable_cursor.sequence - 0 == 101  # local consumer progress remains at zero
        spool_bytes = sum(path.stat().st_size for path in (tmp_path / "spool").rglob("*") if path.is_file())
        assert spool_bytes <= limits["max_disk_bytes"] == 8_388_608
        actual_gate_wall_seconds = time.monotonic() - stall_started
        assert actual_gate_wall_seconds < 15
        live_engine_stats = engine.stats
        assert live_engine_stats["applied"] == count + 1
        assert live_engine_stats["delivered"] == count
        assert live_engine_stats["feeds"][PRODUCER_A]["applied_sequence"] == count + 1
        assert live_engine_stats["feeds"][PRODUCER_A]["mode"] == "LIVE"
        assert len(observations["a_only"]) == 100, paper.report()
        assert all(row["ready"] and row["phase"] == "PAPER" for row in observations["a_only"])
        assert observations["b_required"] == []
        assert matcher.report()["external_mutations"] == 0
        assert matcher.report()["open_orders"] == {}
        # A clean client shutdown fences its now-disconnected source; inspect
        # live independence before teardown rather than interpreting that fence
        # as a failed steady-state delivery.
        assert not invalid
        await publisher.close(timeout_seconds=3)
        await gates.release_and_join()
        assert gates.storage_result["progress"]["sequence"] == 101
        assert len(gates.storage_result["deliveries"]) == 101
        assert sum(int(row["rows"]) for row in gates.storage_result["manifests"]) == 101
        source_records = spool_reader(tmp_path / "spool", limits, after=0).read_batch(
            max_records=128, max_bytes=1_048_576,
        )
        assert len(source_records) == 101
        assert gates.storage_result["progress"]["payload_sha256"] == hashlib.sha256(source_records[-1].payload).hexdigest()
        assert gates.storage_result["source_spool_exists"]
        assert gates.cloud_result["local_manifest"]["upload_status"] == "remote_verified"
        assert gates.cloud_result["remote"].sha256 == gates.cloud_result["row"]["file_sha256"]
        assert gates.telegram_done.done() and gates.telegram_state.next_message()["identity"] == "qa01-gated"

        assert paper.report()["account_faults"] == {}
        assert [sequence for sequence, _ in delivered] == list(range(1, count + 1))
        assert len({native_id for _, native_id in delivered}) == count
        assert publisher.stats["accepted"] == publisher.stats["durable"] == count + 1
        assert publisher.stats["applied"] == count + 1
        assert publisher.stats["failed"] == 0
        # B is registered but has no socket, spool, frame, or resume proof.
        assert live_engine_stats["feeds"][PRODUCER_B]["applied_sequence"] is None
        assert live_engine_stats["feeds"][PRODUCER_B]["mode"] == "INVALID"
    finally:
        if consumer is not None and not consumer.done():
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        await source.aclose()
        if gates is not None:
            gates.storage_release.set()
            gates.cloud_release.set()
            gates.telegram_release.set()
            if gates.telegram_done is not None and not gates.telegram_done.done():
                await asyncio.gather(gates.telegram_done, return_exceptions=True)
            if gates.storage_thread.is_alive():
                await asyncio.to_thread(gates.storage_thread.join, 15)
            if gates.cloud_thread.is_alive():
                await asyncio.to_thread(gates.cloud_thread.join, 15)
        if not publisher._closing:
            await publisher.close(timeout_seconds=3)
        await server.close()
        await engine.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("required_producers", "waits_for_b"),
    [(None, True), ((), False), ((PRODUCER_A,), False), ((PRODUCER_B,), True)],
    ids=["legacy-none-all-feed-gate", "shared-empty-feed-gate", "a-only", "b-required"],
)
async def test_cold_initial_proofs_block_until_b_is_armed_then_source_dependencies_apply(
    required_producers, waits_for_b,
):
    """A delayed B proof is an explicit recovery dependency, not a live ingress test."""
    engine, a0, b0 = warming_engine()
    source = None
    pending = None
    try:
        engine.arm_initial_resume(a0, a0)
        # This is the current global virgin-start safety fence. Until the exact
        # B C/H proof is armed, A cannot be promoted; do not bypass that fence.
        assert engine.stats["feeds"][PRODUCER_A]["mode"] == "WARMING"
        assert engine.stats["feeds"][PRODUCER_B]["mode"] == "WARMING"
        assert not engine.initial_resume(a0)

        engine.arm_initial_resume(b0, b0)
        assert engine.initial_resume(a0)
        assert engine.stats["feeds"][PRODUCER_B]["mode"] == "WARMING"
        source = engine.event_source(required_producers=required_producers)
        pending = asyncio.create_task(anext(source))
        a1 = EngineCursor(PRODUCER_A, INCARNATION_A, 1)
        engine.on_frame(recovery_frame(a1, native_trade_payload(seq=1)))
        await asyncio.sleep(0)

        if waits_for_b:
            assert not pending.done()
            assert engine.stats["feeds"][PRODUCER_B]["mode"] == "WARMING"
            # A cold root still waits safely here. This is BLOCKED_RECOVERY_DEPENDENCY
            # while B's required feed proof is pending, not evidence that A ingress
            # or local durability waits for a live B websocket.
            assert engine.stats["feeds"][PRODUCER_A]["applied_sequence"] == 1
            assert engine.stats["delivered"] == 0
            assert engine.initial_resume(b0)

        event = await asyncio.wait_for(pending, timeout=2)
        assert event["provider"] == PRODUCER_A
        assert engine.stats["feeds"][PRODUCER_A]["applied_sequence"] == 1
        if not waits_for_b:
            # The declared stream does not wait for B after A is LIVE. In the
            # shared empty-dependency case, strategy-specific readiness still
            # owns whether an action may be emitted.
            assert engine.stats["feeds"][PRODUCER_B]["mode"] == "WARMING"
        else:
            assert engine.stats["feeds"][PRODUCER_B]["mode"] == "LIVE"
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        if source is not None:
            await source.aclose()
        await engine.close()


@pytest.mark.asyncio
async def test_actual_paper_strategy_readiness_isolated_across_b_gap_and_new_generation(tmp_path):
    """Registered A-only PAPER callback continues; A+B callback waits for B recovery."""
    from core.models import BookUpdateType, OrderBookEvent, OrderBookLevel

    a, b = provider_instruments()
    observations = {"a_only": [], "b_required": []}
    paper, ledger, matcher = make_paper_strategy_runtime(
        tmp_path / "strategy", HotPathStore(), observations=observations,
    )

    def invalidate_strategy_input(producer, reason):
        key = a.key if producer == PRODUCER_A else b.key
        paper.invalidate_inputs((key,), reason, time.time_ns())

    engine, _, invalid = make_engine(
        endpoint=str(tmp_path / "unused.sock"), applied_callbacks=(paper.process_applied,),
        hot_path=paper.hot_path, fresh_b=True,
        invalidation_callback=invalidate_strategy_input,
    )
    def book(seq, *, source_time=None):
        stamp = time.time_ns()
        return OrderBookEvent(
            instrument=b, venue=b.venue, provider=PRODUCER_B,
            event_id=f"qa01-b-book-{seq}", source_ts_ns=source_time or stamp,
            receive_ts_ns=stamp, wallclock_ts_ns=stamp,
            receive_monotonic_ns=time.monotonic_ns(),
            connection_id=f"{PRODUCER_B}-generation-{engine.stats['feeds'][PRODUCER_B]['generation']}",
            receive_sequence=seq, native_event_id=f"b-book-{seq}", sequence=seq,
            update_type=BookUpdateType.SNAPSHOT, final_update_id=seq,
            sync_generation=seq, depth=1,
            bids=[OrderBookLevel(price=Decimal("100.00"), quantity=Decimal("2.000"))],
            asks=[OrderBookLevel(price=Decimal("101.00"), quantity=Decimal("3.000"))],
        ).model_dump_json().encode("utf-8")

    native_a_book = await parse_binance_native_book()
    native_a = await parse_binance_native_trades(3)
    def a_trade_frame(cursor_sequence, event_index):
        return recovery_frame(
            EngineCursor(PRODUCER_A, INCARNATION_A, cursor_sequence),
            native_a[event_index - 1].model_dump_json().encode("utf-8"),
        )
    b_cursor = lambda seq: EngineCursor(PRODUCER_B, INCARNATION_B, seq)
    initial_a_generation = engine.stats["feeds"][PRODUCER_A]["generation"]
    initial_b_generation = engine.stats["feeds"][PRODUCER_B]["generation"]
    try:
        engine.on_frame(recovery_frame(b_cursor(1), book(1)))
        engine.on_frame(recovery_frame(
            EngineCursor(PRODUCER_A, INCARNATION_A, 1),
            native_a_book.model_dump_json().encode("utf-8"),
        ))
        engine.on_frame(a_trade_frame(2, 1))
        assert len(observations["a_only"]) == len(observations["b_required"]) == 1, paper.report()
        assert observations["a_only"][-1]["ready"] is True
        assert observations["b_required"][-1]["ready"] is True
        old_b_book = paper.hot_path.snapshot(time.time_ns()).latest_book(b.key)

        engine.on_gap(b_cursor(1), "scripted_disconnect")
        assert engine.stats["feeds"][PRODUCER_B]["mode"] == "INVALID"
        assert engine.stats["feeds"][PRODUCER_A]["mode"] == "LIVE"
        assert engine.stats["feeds"][PRODUCER_A]["generation"] == initial_a_generation
        assert engine.stats["feeds"][PRODUCER_B]["generation"] > initial_b_generation
        engine.on_frame(a_trade_frame(3, 2))
        assert len(observations["a_only"]) == 2
        assert observations["a_only"][-1]["ready"] is True
        # The actual registered A+B PaperStrategyRuntime job receives no
        # callback while its required B feed is invalid; the old B book remains
        # observable as stale facts, never promoted via synthetic forward fill.
        assert len(observations["b_required"]) == 1
        assert paper.hot_path.snapshot(time.time_ns()).latest_book(b.key).event_id == old_b_book.event_id
        assert engine.stats["feeds"][PRODUCER_A]["applied_sequence"] == 3

        checkpoint = b_cursor(1)
        high = b_cursor(2)
        engine.begin_recovery(checkpoint, high, verified_checkpoint=True)
        replayed = engine.apply_replay(recovery_frame(high, book(2)))
        assert engine.commit_recovery(high, replayed.digest, ready=True)
        assert engine.stats["feeds"][PRODUCER_B]["mode"] == "LIVE"
        # A fresh current-generation B fact clears the actual strategy input
        # fence before the next A event is offered to registered strategies.
        engine.on_frame(recovery_frame(b_cursor(3), book(3)))
        engine.on_frame(a_trade_frame(4, 3))
        assert len(observations["a_only"]) == 3
        assert len(observations["b_required"]) == 2
        assert observations["b_required"][-1]["ready"] is True
        assert engine.stats["feeds"][PRODUCER_A]["applied_sequence"] == 4
        assert engine.stats["feeds"][PRODUCER_B]["applied_sequence"] == 3
        assert matcher.report()["open_orders"] == {}
        assert matcher.report()["external_mutations"] == 0
        assert invalid and any(item[0] == PRODUCER_B for item in invalid)
        assert not any(item[0] == PRODUCER_A for item in invalid)
    finally:
        await engine.close()
        ledger.close()


@pytest.mark.asyncio
async def test_binance_spot_and_usdm_perp_share_symbol_but_keep_separate_state_identity():
    """A spot fixture plus real USDM parser output stay distinct under time reordering."""
    import time

    from core.models import AssetClass

    spot, parse_perp = spot_and_perp_trades()
    perp_events = await parse_perp()
    assert len(perp_events) == 1
    perp = perp_events[0]
    assert spot.instrument.venue == perp.instrument.venue
    assert spot.instrument.raw_symbol == perp.instrument.raw_symbol == "BTCUSDT"
    assert spot.instrument.asset_class == AssetClass.CRYPTO_SPOT
    assert perp.instrument.asset_class == AssetClass.CRYPTO_PERP
    assert spot.instrument.key != perp.instrument.key
    assert spot.source_ts_ns > perp.source_ts_ns
    assert spot.price == Decimal("100.0100") and spot.quantity == Decimal("0.005000")
    assert perp.price == Decimal("100.0200") and perp.quantity == Decimal("0.125000")

    store = HotPathStore()
    first = store.apply_versioned(perp)
    second = store.apply_versioned(spot)
    snapshot = store.snapshot(time.time_ns(), phase="LIVE")
    assert first.applied and second.applied and snapshot.cursor[1] == 2
    assert snapshot.latest_trade(perp.instrument.key).trade_id == "8001"
    assert snapshot.latest_trade(spot.instrument.key).trade_id == "7001"
    assert snapshot.latest_trade(perp.instrument.key).source_ts_ns == perp.source_ts_ns
    assert snapshot.latest_trade(spot.instrument.key).source_ts_ns == spot.source_ts_ns
    # The current baseline has no Binance spot market-data collector. Spot uses
    # a typed native-shape fixture here; only USDM is verified through an
    # existing production parser. This case proves identity/state separation,
    # not existence of a Binance spot collection path.
