"""Boundary checks for the bounded market checkpoint codec."""
import hashlib
import json
import os

import pytest

from feeds.feed_recovery import EngineCursor
from market.hot_path import HotPathStore
from storage.market_checkpoint import CheckpointCodec, CheckpointError, CheckpointProducer


CONFIG_SHA256 = "a" * 64
EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()


def checkpoint_codec(*, max_bytes=65_536, max_event_bytes=16_384):
    store = HotPathStore()
    codec = CheckpointCodec(
        max_bytes=max_bytes,
        max_event_bytes=max_event_bytes,
        max_producers=4,
        config_sha256=CONFIG_SHA256,
        capacity=store.checkpoint_capacity(),
    )
    producer = valid_producer("feed-a")
    payload = codec.encode(
        store,
        producers=(producer,),
        capture_epoch_ns=100,
        capture_monotonic_ns=200,
    )
    return codec, payload


def valid_producer(name):
    return CheckpointProducer(EngineCursor(name, "incarnation-a", 0), EMPTY_DIGEST, "INVALID")


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def replace_body(payload, update):
    envelope = json.loads(payload)
    body = envelope["body"]
    update(body)
    envelope["sha256"] = hashlib.sha256(canonical(body)).hexdigest()
    return canonical(envelope)


def test_decode_rejects_payload_over_document_bound():
    codec, payload = checkpoint_codec()
    bounded = CheckpointCodec(
        max_bytes=len(payload) - 1,
        max_event_bytes=1,
        max_producers=4,
        config_sha256=CONFIG_SHA256,
        capacity=codec.capacity,
    )

    with pytest.raises(CheckpointError, match="byte bound"):
        bounded.decode(payload)


def test_decode_rejects_truncated_payload():
    codec, payload = checkpoint_codec()

    with pytest.raises(CheckpointError):
        codec.decode(payload[:-1])


def test_decode_rejects_noncanonical_valid_json():
    codec, payload = checkpoint_codec()

    with pytest.raises(CheckpointError, match="canonical"):
        codec.decode(payload + b" ")


def test_decode_rejects_checksum_mismatch():
    codec, payload = checkpoint_codec()
    envelope = json.loads(payload)
    envelope["sha256"] = "0" * 64

    with pytest.raises(CheckpointError, match="checksum"):
        codec.decode(canonical(envelope))


def test_decode_rejects_malformed_producer_metadata():
    codec, payload = checkpoint_codec()
    malformed = replace_body(
        payload,
        lambda body: body["producers"].__setitem__(0, {
            "producer": "feed-a",
            "incarnation": "incarnation-a",
            "sequence": 0,
            "digest": "f" * 64,
            "quality": "INVALID",
        }),
    )

    with pytest.raises(CheckpointError, match="digest"):
        codec.decode(malformed)


def test_decode_rejects_configuration_mismatch():
    codec, payload = checkpoint_codec()
    mismatched = replace_body(payload, lambda body: body.__setitem__("config_sha256", "b" * 64))

    with pytest.raises(CheckpointError, match="config mismatch"):
        codec.decode(mismatched)


def test_decode_rejects_wall_clock_regression():
    codec, payload = checkpoint_codec()

    with pytest.raises(CheckpointError, match="clock regressed"):
        codec.decode(payload, restore_epoch_ns=99, restore_monotonic_ns=300)


def test_encode_rejects_store_capacity_mismatch():
    codec, _ = checkpoint_codec()
    mismatched_store = HotPathStore(max_trades=7)

    with pytest.raises(CheckpointError, match="capacity/config mismatch"):
        codec.encode(
            mismatched_store,
            producers=(valid_producer("feed-a"),),
            capture_epoch_ns=100,
            capture_monotonic_ns=200,
        )


def test_encode_rejects_producer_vector_over_bound():
    store = HotPathStore()
    codec = CheckpointCodec(
        max_bytes=65_536,
        max_event_bytes=16_384,
        max_producers=1,
        config_sha256=CONFIG_SHA256,
        capacity=store.checkpoint_capacity(),
    )

    with pytest.raises(CheckpointError):
        codec.encode(
            store,
            producers=(valid_producer("feed-a"), valid_producer("feed-b")),
            capture_epoch_ns=100,
            capture_monotonic_ns=200,
        )


def test_encode_rejects_duplicate_producer_metadata():
    codec, _ = checkpoint_codec()
    store = HotPathStore()
    duplicate = valid_producer("feed-a")

    with pytest.raises(CheckpointError, match="duplicate producer"):
        codec.encode(
            store,
            producers=(duplicate, duplicate),
            capture_epoch_ns=100,
            capture_monotonic_ns=200,
        )


def test_write_fails_closed_when_directory_fsync_is_unavailable(tmp_path, monkeypatch):
    codec, payload = checkpoint_codec()
    target = tmp_path / "checkpoint.json"
    prior = b"previous checkpoint remains intact"
    target.write_bytes(prior)
    fsync_calls = []
    replace_calls = []
    real_replace = os.replace

    def unavailable_fsync(descriptor):
        fsync_calls.append(descriptor)
        raise OSError("directory fsync unavailable")

    def record_replace(source, destination):
        replace_calls.append((source, destination))
        return real_replace(source, destination)

    monkeypatch.setattr("storage.market_checkpoint.os.fsync", unavailable_fsync)
    monkeypatch.setattr("storage.market_checkpoint.os.replace", record_replace)

    with pytest.raises(OSError):
        codec.write(target, payload)

    assert target.read_bytes() == prior
    assert replace_calls == []
    # Some platforms cannot open a directory handle; that must also fail before replace.
    assert len(fsync_calls) <= 1
