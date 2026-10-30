from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from storage.market_spool import MarketSpool, SpoolCursor
from storage.storage_backend import LocalStorageBackend

PRODUCER = "grvt-collector"
INCARNATION = "incarnation-A"
LIMITS = dict(max_disk_bytes=1024 * 1024, max_records=32, max_frame_bytes=2048,
              max_header_bytes=512, segment_max_bytes=4096, segment_max_records=8,
              segment_max_age_seconds=60)
PAYLOADS = [
    b'{"price":"0.123456789012345678","quantity":"0","source_ts_ns":null}',
    '{"raw_payload":"원문/交易","bids":[["1.000000000000000001","0"]]}'.encode(),
]
OBJECT_KEY = "mov03/golden/archive-object-v1.bin"
SOURCE = b"abc"
SOURCE_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def _spool(root: Path) -> MarketSpool:
    return MarketSpool(root, PRODUCER, INCARNATION, **LIMITS)


def capture_storage_golden(root: Path) -> dict:
    spool_root = Path(root) / "spool"
    durable_before = []
    spool = _spool(spool_root)
    try:
        for payload in PAYLOADS:
            receipt = spool.append(payload)
            assert receipt.status == "APPENDED" and not receipt.uncertain
            assert receipt.cursor.producer_id == receipt.cursor.producer == PRODUCER
            assert spool.durable_cursor.sequence == 0
            durable_before.append(receipt.cursor.sequence)
        flush = spool.flush()
        assert flush.sequence == 2
        assert [r.payload for r in spool.iter_records()] == PAYLOADS
        seal = spool.seal()
        assert seal.sequence == 2
    finally:
        spool.close()
    reopened = _spool(spool_root)
    try:
        assert reopened.appended_cursor.sequence == reopened.durable_cursor.sequence == 2
        records = list(reopened.iter_records())
        assert [r.payload for r in records] == PAYLOADS
        cursors = [(r.cursor.producer, r.cursor.incarnation, r.cursor.sequence) for r in records]
        durable = reopened.durable_cursor.sequence
    finally:
        reopened.close()
    files = {
        path.relative_to(spool_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(spool_root.rglob("*"))
        if path.is_file() and path.name != "writer.lock"
    }
    backend = LocalStorageBackend(Path(root) / "remote")
    source = Path(root) / "archive-source.bin"
    source.write_bytes(SOURCE)
    put = backend.put_file(source, OBJECT_KEY, sha256=SOURCE_SHA256)
    verified = backend.verify(OBJECT_KEY, size_bytes=len(SOURCE), sha256=SOURCE_SHA256)
    downloaded = Path(root) / "downloads" / "archive-copy.bin"
    backend.download_file(OBJECT_KEY, downloaded)
    assert downloaded.read_bytes() == SOURCE
    assert (put.key, put.size_bytes, put.sha256) == (OBJECT_KEY, len(SOURCE), SOURCE_SHA256)
    assert (verified.key, verified.size_bytes, verified.sha256) == (
        OBJECT_KEY, len(SOURCE), SOURCE_SHA256)
    return {
        "files": files,
        "cursors": cursors,
        "payload_hex": [p.hex() for p in PAYLOADS],
        "durable": {"appended": durable_before, "flush": flush.sequence,
                    "seal": seal.sequence, "reopened": durable, "records": len(records)},
        "archive": {"key": put.key, "size_bytes": put.size_bytes, "sha256": put.sha256,
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "downloaded_sha256": hashlib.sha256(downloaded.read_bytes()).hexdigest()},
    }


def test_mov03_golden_literals_and_deterministic_capture(tmp_path):
    golden = capture_storage_golden(tmp_path)
    assert [bytes.fromhex(item) for item in golden["payload_hex"]] == [
        b'{"price":"0.123456789012345678","quantity":"0","source_ts_ns":null}',
        '{"raw_payload":"원문/交易","bids":[["1.000000000000000001","0"]]}'.encode(),
    ]
    assert golden["cursors"] == [
        (PRODUCER, INCARNATION, 1), (PRODUCER, INCARNATION, 2)]
    assert SpoolCursor(PRODUCER, INCARNATION, 1).producer_id == PRODUCER
    assert golden["durable"] == {
        "appended": [1, 2], "flush": 2, "seal": 2, "reopened": 2, "records": 2}
    assert golden["archive"] == {
        "key": "mov03/golden/archive-object-v1.bin", "size_bytes": 3,
        "sha256": SOURCE_SHA256, "source_sha256": SOURCE_SHA256,
        "downloaded_sha256": SOURCE_SHA256,
    }
    again = capture_storage_golden(tmp_path / "independent")
    assert (again["files"], again["cursors"], again["payload_hex"]) == (
        golden["files"], golden["cursors"], golden["payload_hex"])
    assert (again["durable"], again["archive"]) == (golden["durable"], golden["archive"])


def test_mov03_corrupt_archived_object_fails_verify_source_unchanged(tmp_path):
    capture_storage_golden(tmp_path)
    source = tmp_path / "archive-source.bin"
    assert source.read_bytes() == SOURCE
    backend = LocalStorageBackend(tmp_path / "remote")
    objects = [path for path in (tmp_path / "remote").rglob("*") if path.is_file()]
    assert len(objects) == 1
    raw = bytearray(objects[0].read_bytes())
    raw[0] ^= 0xFF
    objects[0].write_bytes(bytes(raw))
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        backend.verify(OBJECT_KEY, size_bytes=len(SOURCE), sha256=SOURCE_SHA256)
    assert source.read_bytes() == SOURCE
    assert hashlib.sha256(source.read_bytes()).hexdigest() == SOURCE_SHA256
