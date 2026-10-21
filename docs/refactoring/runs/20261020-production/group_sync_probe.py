"""Bounded synthetic A/B probe: real spool fsync, fake APPLIED, no engine/venue.

Run only through the offline source-copy launchers with -s. Each output line is
an independent repeat; it is not the production NATS/E2E/capacity acceptance.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

import pytest

assert getattr(sys, "_vibetrading_offline_guard", False), "Use the isolated launcher"

from vibetrading import local_pipeline
from vibetrading.local_pipeline import PipelineBudgets, SpoolingMarketPublisher
from vibetrading.local_transport import TransportAck
from vibetrading.market_spool import MarketSpool


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat", range(3))
@pytest.mark.parametrize("profile", ["burst8", "sparse1"])
async def test_group_sync_probe(tmp_path, repeat, profile):
    assert Path(local_pipeline.__file__).resolve().is_relative_to(Path.cwd())
    samples, flush_ns, accepted = [], [], {}
    count = 192 if profile == "burst8" else 24
    width = 8 if profile == "burst8" else 1
    delivered = asyncio.Event()

    class Spool(MarketSpool):
        def flush(self):
            start = time.perf_counter_ns()
            result = super().flush()
            flush_ns.append(time.perf_counter_ns() - start)
            return result

    class Transport:
        def __init__(self):
            self.acks = asyncio.Queue()
            self.sent = 0

        async def connect(self):
            pass

        async def request_resume(self, cursor):
            return TransportAck("RESUMED", cursor, hashlib.sha256(b"").hexdigest())

        def try_send(self, frame):
            samples.append(time.perf_counter_ns() - accepted[frame.cursor.sequence])
            self.sent += 1
            self.acks.put_nowait(TransportAck("APPLIED", frame.cursor, frame.digest))
            if self.sent % width == 0:
                delivered.set()
            return True

        async def receive_ack(self):
            return await self.acks.get()

        async def close(self):
            pass

    bus = SpoolingMarketPublisher(
        producer="probe", incarnation="run", transport=Transport(), env={},
        on_invalid=lambda reason: pytest.fail(reason),
        budgets=PipelineBudgets(16, 16384, 512, 8, 20),
        spool_factory=lambda: Spool(tmp_path, "probe", "run", max_disk_bytes=1048576,
            max_records=1024, max_frame_bytes=512, max_header_bytes=256,
            segment_max_bytes=262144, segment_max_records=512, segment_max_age_seconds=60),
    )
    await bus.connect()
    cpu_start, wall_start = time.process_time_ns(), time.perf_counter_ns()
    try:
        for first in range(0, count, width):
            delivered.clear()
            for index in range(first, first + width):
                accepted[index + 1] = time.perf_counter_ns()
                bus.accept_payload(str(index).encode().ljust(64, b"x"))
            await asyncio.wait_for(delivered.wait(), 10)
            while bus.stats["applied"] < first + width:
                await asyncio.sleep(0)
        elapsed, cpu = time.perf_counter_ns() - wall_start, time.process_time_ns() - cpu_start
        assert bus.stats["accepted"] == bus.stats["durable"] == bus.stats["applied"] == count
        assert bus.stats["pending"] == bus.stats["failed"] == 0
    finally:
        await bus.close()
    ordered = sorted(samples)
    quantile = lambda p: ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))] / 1e6
    print("VN05_PROBE=" + json.dumps({
        "profile": profile, "repeat": repeat, "events": count, "payload_bytes": 64,
        "group_sync_ms": 20, "batch_records": 8, "python": platform.python_version(),
        "pipeline_sha256": hashlib.sha256(Path(local_pipeline.__file__).read_bytes()).hexdigest(),
        "wall_ms": elapsed / 1e6, "process_cpu_ns_per_event": cpu / count,
        "admission_to_fake_send_ms": {"mean": sum(samples) / count / 1e6,
            "p50": quantile(.5), "p95": quantile(.95), "p99": quantile(.99)},
        "raw_admission_to_fake_send_ns": samples, "raw_flush_ns": flush_ns,
        "scope": "Synthetic local fsync/fake ACK only; warm-state order; no p99.9 or production gate",
    }))
