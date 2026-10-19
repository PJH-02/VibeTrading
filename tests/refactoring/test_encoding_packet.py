from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.refactoring.encoding_probe import NOW, cases, collect_transcript
from vibetrading import bus as module
from vibetrading.bus import BufferedMarketEventBus, NatsJetStreamBus, prepare_market_event, publish_prepared_market_event
from vibetrading.market_policy import MarketDataPolicyBus
from vibetrading.runtime import _CollectorReadinessBus


@pytest.mark.asyncio
async def test_wire_and_readiness_equal_frozen_baseline():
    path = Path(__file__).resolve().parents[2] / "docs/refactoring/runs/20261018-stage-assignment/encoding_oracle.json"
    payload = path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == "1bb9e0d00e2cbb1110e25cf85e5ac94fc97aeb42604c7ff658b2934f99bed513"
    assert await collect_transcript() == json.loads(payload)["transcript"]


def test_packet_owns_bytes_without_retaining_mutable_event_arrays():
    event = cases()["raw_snapshot"]
    packet = prepare_market_event(event)
    original = packet.payload
    event.bids.clear()
    event.asks.reverse()
    assert packet.payload == original
    assert b'"quantity":"0"' in packet.payload
    assert b'"raw_payload"' in packet.payload
    assert b'"raw_payload"' not in packet.live_payload
    assert not hasattr(packet.snapshot, "bids")
    with pytest.raises(FrozenInstanceError):
        packet.snapshot.event_id = "changed"
    assert packet.byte_length == len(original)


@pytest.mark.asyncio
async def test_explicit_opt_in_preserves_public_overrides_and_wrappers():
    received = []
    class Capable:
        _market_packet_protocol = 1
        async def publish_market_packet(self, packet):
            received.append("packet")
        async def publish_market_event(self, event):
            received.append("public")
        _packet_public_method = publish_market_event
    class Override(Capable):
        async def publish_market_event(self, event):
            received.append("subclass")
            event.bids.clear()
    class Wrapper:
        def __init__(self, inner):
            self.inner = inner
        def __getattr__(self, name):
            return getattr(self.inner, name)
        async def publish_market_event(self, event):
            received.append("wrapper")
    packet = prepare_market_event(cases()["raw_snapshot"])
    await publish_prepared_market_event(Capable(), packet)
    await publish_prepared_market_event(Override(), packet)
    await publish_prepared_market_event(Wrapper(Capable()), packet)
    patched = Capable()
    async def public(event):
        received.append("patched")
    patched.publish_market_event = public
    await publish_prepared_market_event(patched, packet)
    assert received == ["packet", "subclass", "wrapper", "patched"]
    assert json.loads(packet.payload)["bids"]


@pytest.mark.asyncio
async def test_class_level_public_instrumentation_is_preserved(monkeypatch):
    called = []
    original = NatsJetStreamBus.publish_market_event
    async def instrumented(self, event):
        called.append(event.event_id)
        await original(self, event)
    monkeypatch.setattr(NatsJetStreamBus, "publish_market_event", instrumented)
    terminal = NatsJetStreamBus(SimpleNamespace(env={}))
    # Retired event exercises interception without an external transport.
    packet = prepare_market_event(cases()["retired"])
    await publish_prepared_market_event(terminal, packet)
    assert called == [packet.snapshot.event_id]


@pytest.mark.asyncio
async def test_policy_wrapper_is_never_bypassed():
    terminal = NatsJetStreamBus(SimpleNamespace(env={}))
    terminal._js = SimpleNamespace(publish=lambda *a, **k: None)
    # Avoid guard construction/filesystem reads; exercise the actual interceptor.
    policy = object.__new__(MarketDataPolicyBus)
    policy.inner = terminal
    pressure = []
    async def checked(crypto):
        pressure.append(crypto)
    policy._check_pressure = checked
    packet = prepare_market_event(cases()["retired"])
    await publish_prepared_market_event(policy, packet)
    assert pressure == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,expected_encodes", [("crypto_trade", 1), ("raw_snapshot", 2), ("durable_only_book", 1)])
async def test_production_chain_encodes_once_and_reuses_bytes_on_retry(name, expected_encodes):
    attempts = []
    live = []
    class JS:
        async def publish(self, subject, payload, **kwargs):
            attempts.append((subject, payload, kwargs))
            if len(attempts) == 1:
                raise asyncio.TimeoutError()
    class NC:
        async def publish(self, subject, payload):
            live.append(payload)
    terminal = NatsJetStreamBus(SimpleNamespace(env={}))
    terminal._js, terminal._nc = JS(), NC()
    readiness = _CollectorReadinessBus(terminal, None, "binance-perp")
    buffered = BufferedMarketEventBus(readiness, shards=1)
    event = cases()[name]
    with patch.object(module, "dumps", wraps=module.dumps) as encoder, patch.object(module.time, "time_ns", return_value=NOW), patch("vibetrading.runtime.health_touch"):
        await buffered.publish_market_event(event)
        event.bids.clear() if hasattr(event, "bids") else None
        await buffered.close()
    assert encoder.call_count == expected_encodes
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert readiness.published_count == 1
    assert readiness.published_bytes == len(attempts[0][1])
    if name == "raw_snapshot":
        assert json.loads(attempts[0][1])["bids"]
        assert json.loads(live[0])["bids"]
