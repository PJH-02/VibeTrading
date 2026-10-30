from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import struct

import pytest

from storage.market_spool import MarketSpool, RetainedPrefix, SpoolCorruption, SpoolCursor, SpoolError, SpoolReader, SpoolRotateRequired, capture_retained_prefix, read_spool_records, scan_spool


PRODUCER = "retained-collector"
INCARNATION = "incarnation-R"
SEGMENT = re.compile(r"([0-9]{20})\.(sealed|active)\Z")


def limits(**updates):
    values = dict(max_disk_bytes=1024 * 1024, max_records=64, max_frame_bytes=2048,
                  max_header_bytes=512, segment_max_bytes=4096, segment_max_records=1,
                  segment_max_age_seconds=60)
    values.update(updates)
    return values


def read_limits(**updates):
    values = limits(**updates)
    return {key: values[key] for key in
            ("max_disk_bytes", "max_records", "max_frame_bytes", "max_header_bytes")}


def build_full(root: Path, payloads, *, segment_max_records=1):
    spool = MarketSpool(root, PRODUCER, INCARNATION,
                        **limits(segment_max_records=segment_max_records))
    try:
        for payload in payloads:
            spool.append(payload)
            spool.seal()
    finally:
        spool.close()


def capture(root: Path, first: int, consumers=None):
    if consumers is None:
        consumers = (SpoolCursor(PRODUCER, INCARNATION, first - 1),)
    return capture_retained_prefix(
        root, PRODUCER, INCARNATION, first_retained_segment=first,
        generation="generation-1",
        consumer_cursors=tuple(consumers),
        max_disk_bytes=1024 * 1024, max_records=64, max_frame_bytes=2048,
        max_header_bytes=512)


def trimmed_copy(source: Path, target: Path, first: int):
    target.mkdir(parents=True)
    for path in source.iterdir():
        if path.name == "writer.lock":
            continue
        if path.name == "checkpoint.json":
            shutil.copy2(path, target / path.name)
            continue
        match = SEGMENT.fullmatch(path.name)
        if match is not None and int(match[1]) >= first:
            shutil.copy2(path, target / path.name)


def scan(root: Path, proof=None, **updates):
    return scan_spool(root, producer_id=PRODUCER, incarnation=INCARNATION,
                      retained_prefix=proof, **read_limits(**updates))


def reader(root: Path, proof, after, **updates):
    return SpoolReader(root, PRODUCER, INCARNATION, after=after,
                       retained_prefix=proof, **read_limits(**updates))


PAYLOADS = [b"one", b"two", b"three", b"four", b"five"]


def test_default_v1_scan_fields_and_reader_behavior_are_unchanged(tmp_path):
    build_full(tmp_path, PAYLOADS[:4])
    report = scan(tmp_path)
    assert (report.base_sequence, report.first_segment_id, report.last_segment_id) == (0, 1, 4)
    assert report.segment_count == report.record_count == report.last_sequence == 4
    assert [row.payload for row in read_spool_records(
        tmp_path, producer_id=PRODUCER, incarnation=INCARNATION, **read_limits())] == PAYLOADS[:4]


def test_capture_is_read_only_and_roundtrips_with_external_pins(tmp_path):
    build_full(tmp_path, PAYLOADS[:4])
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    proof = capture(tmp_path, 2)
    after = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert before == after
    assert proof.base_sequence == 1 and proof.first_retained_segment == 2
    assert proof.generation == "generation-1"
    payload = proof.to_bytes()
    assert len(payload) <= 8192
    assert proof.sha256 == hashlib.sha256(payload).hexdigest()
    restored = RetainedPrefix.from_bytes(
        payload, expected_sha256=proof.sha256, expected_generation="generation-1")
    assert restored == proof
    assert restored.consumer_cursors == proof.consumer_cursors
    with pytest.raises(ValueError, match="hash"):
        RetainedPrefix.from_bytes(payload, expected_sha256="0" * 64,
                                  expected_generation="generation-1")
    with pytest.raises(ValueError, match="generation"):
        RetainedPrefix.from_bytes(payload, expected_sha256=proof.sha256,
                                  expected_generation="generation-2")


def test_proof_rejects_wrong_generation_duplicate_schema_type_and_forgery(tmp_path):
    build_full(tmp_path, PAYLOADS[:4])
    proof = capture(tmp_path, 2)
    payload = proof.to_bytes()
    document = json.loads(payload)

    duplicate = b'{"version":2,"version":2,"producer_id":"x"}'
    with pytest.raises(ValueError):
        RetainedPrefix.from_bytes(duplicate,
                                  expected_sha256=hashlib.sha256(duplicate).hexdigest(),
                                  expected_generation="generation-1")

    nonfinite = payload.replace(b'"max_records":64', b'"max_records":NaN')
    with pytest.raises(ValueError):
        RetainedPrefix.from_bytes(nonfinite,
                                  expected_sha256=hashlib.sha256(nonfinite).hexdigest(),
                                  expected_generation="generation-1")

    missing = dict(document)
    del missing["checkpoint_sha256"]
    bad_schema = json.dumps(missing, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ValueError, match="schema"):
        RetainedPrefix.from_bytes(bad_schema,
                                  expected_sha256=hashlib.sha256(bad_schema).hexdigest(),
                                  expected_generation="generation-1")

    changed = dict(document)
    changed["base_sequence"] = True
    bad_type = json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ValueError):
        RetainedPrefix.from_bytes(bad_type,
                                  expected_sha256=hashlib.sha256(bad_type).hexdigest(),
                                  expected_generation="generation-1")

    forged = bytearray(payload)
    forged[forged.find(b'"base_sequence":1') + len(b'"base_sequence":')] = ord("2")
    with pytest.raises(ValueError):
        RetainedPrefix.from_bytes(bytes(forged), expected_sha256=proof.sha256,
                                  expected_generation="generation-1")
    with pytest.raises(TypeError):
        RetainedPrefix(proof)
    with pytest.raises(AttributeError):
        proof.generation = "changed"


def test_retained_reader_starts_at_base_and_polls_only_new_records(tmp_path):
    full = tmp_path / "full"
    trimmed = tmp_path / "trimmed"
    build_full(full, PAYLOADS)
    proof = capture(full, 3)
    trimmed_copy(full, trimmed, 3)
    report = scan(trimmed, proof)
    assert (report.base_sequence, report.first_segment_id, report.last_segment_id) == (2, 3, 5)
    assert report.segment_count == report.record_count == 3
    assert report.last_sequence == 5
    assert [row.payload for row in read_spool_records(
        trimmed, producer_id=PRODUCER, incarnation=INCARNATION,
        after=SpoolCursor(PRODUCER, INCARNATION, 2), retained_prefix=proof,
        **read_limits())] == PAYLOADS[2:]
    for after in (None, SpoolCursor(PRODUCER, INCARNATION, 1),
                  SpoolCursor(PRODUCER, INCARNATION, 6)):
        with pytest.raises(SpoolCorruption):
            reader(trimmed, proof, after)
    with pytest.raises(SpoolCorruption):
        list(read_spool_records(trimmed, producer_id=PRODUCER, incarnation=INCARNATION,
                                retained_prefix=proof, **read_limits()))
    live = reader(trimmed, proof, SpoolCursor(PRODUCER, INCARNATION, 2))
    assert [row.payload for row in live.read_batch(max_records=8, max_bytes=8192)] == PAYLOADS[2:]
    assert live.read_batch(max_records=8, max_bytes=8192) == ()


def test_retained_anchor_corruption_is_sticky_and_cursor_rolls_back(tmp_path):
    full = tmp_path / "full"
    trimmed = tmp_path / "trimmed"
    build_full(full, PAYLOADS[:4])
    proof = capture(full, 2)
    trimmed_copy(full, trimmed, 2)
    live = reader(trimmed, proof, SpoolCursor(PRODUCER, INCARNATION, 1))
    anchor = trimmed / "00000000000000000002.sealed"
    data = bytearray(anchor.read_bytes())
    data[-1] ^= 1
    anchor.write_bytes(data)
    with pytest.raises(SpoolCorruption):
        live.read_batch(max_records=8, max_bytes=8192)
    assert live.cursor.sequence == 1
    with pytest.raises(SpoolError, match="fail-closed"):
        live.read_batch(max_records=8, max_bytes=8192)
    with pytest.raises(SpoolCorruption):
        scan(trimmed, proof)


def test_missing_gap_extra_prefix_and_original_writer_reject_trimmed_root(tmp_path):
    full = tmp_path / "full"
    trimmed = tmp_path / "trimmed"
    build_full(full, PAYLOADS[:5])
    proof = capture(full, 3)
    trimmed_copy(full, trimmed, 3)
    (trimmed / "00000000000000000004.sealed").unlink()
    with pytest.raises(SpoolCorruption, match="missing|contiguous"):
        scan(trimmed, proof)
    with pytest.raises(SpoolCorruption):
        MarketSpool(trimmed, PRODUCER, INCARNATION, **limits())

    extra = tmp_path / "extra"
    trimmed_copy(full, extra, 3)
    shutil.copy2(full / "00000000000000000002.sealed", extra / "00000000000000000002.sealed")
    with pytest.raises(SpoolCorruption, match="first"):
        scan(extra, proof)

    boundary = tmp_path / "boundary"
    trimmed_copy(full, boundary, 3)
    with pytest.raises(ValueError):
        capture(full, 1)
    with pytest.raises(SpoolCorruption):
        capture(full, 9)


def test_capture_rejects_missing_prefix_and_cut_not_on_sealed_boundary(tmp_path):
    partial = tmp_path / "partial"
    build_full(partial, PAYLOADS[:4])
    (partial / "00000000000000000002.sealed").unlink()
    with pytest.raises(SpoolCorruption):
        capture(partial, 3)

    live = tmp_path / "live"
    spool = MarketSpool(live, PRODUCER, INCARNATION, **limits(segment_max_records=8))
    try:
        spool.append(b"one")
        spool.seal()
        spool.append(b"two")
        spool.flush()
        with pytest.raises(SpoolCorruption, match="sealed"):
            capture(live, 2)
    finally:
        spool.close()


def test_checkpoint_hash_and_actual_forward_growth(tmp_path):
    full = tmp_path / "full"
    trimmed = tmp_path / "trimmed"
    build_full(full, PAYLOADS[:3])
    proof = capture(full, 2)
    checkpoint = full / "checkpoint.json"
    document = json.loads(checkpoint.read_bytes())
    checkpoint.write_text(json.dumps(document, indent=1), encoding="utf-8")
    trimmed_copy(full, trimmed, 2)
    with pytest.raises(SpoolCorruption, match="checkpoint"):
        scan(trimmed, proof)

    growth = tmp_path / "growth"
    project = tmp_path / "project"
    build_full(project, PAYLOADS[:3])
    proof = capture(project, 2)
    spool = MarketSpool(project, PRODUCER, INCARNATION,
                        **limits(segment_max_records=2))
    try:
        spool.append(PAYLOADS[3])
        spool.append(PAYLOADS[4])
        spool.flush()
    finally:
        spool.close()
    trimmed_copy(project, growth, 2)
    report = scan(growth, proof)
    assert report.durable_sequence == 5 and report.last_sequence == 5
    assert [row.payload for row in read_spool_records(
        growth, producer_id=PRODUCER, incarnation=INCARNATION,
        after=SpoolCursor(PRODUCER, INCARNATION, 1), retained_prefix=proof,
        **read_limits())] == PAYLOADS[1:]


def test_partial_tail_and_unfinished_checkpoint_fail_retained_startup(tmp_path):
    full = tmp_path / "full"
    trimmed = tmp_path / "trimmed"
    build_full(full, PAYLOADS[:4])
    proof = capture(full, 2)
    trimmed_copy(full, trimmed, 2)
    last = trimmed / "00000000000000000004.sealed"
    last.rename(trimmed / "00000000000000000004.active")
    with (trimmed / "00000000000000000004.active").open("ab") as handle:
        handle.write(b"VTSP")
    with pytest.raises(SpoolCorruption, match="partial"):
        scan(trimmed, proof)

    other = tmp_path / "other"
    trimmed_copy(full, other, 2)
    (other / "checkpoint.json.tmp").write_bytes(b"{}")
    with pytest.raises(SpoolCorruption, match="checkpoint"):
        scan(other, proof)


def test_checkpoint_identity_and_durable_regression_reject_retained_startup(tmp_path):
    full = tmp_path / "full"
    build_full(full, PAYLOADS[:4])
    proof = capture(full, 2)
    trimmed = tmp_path / "trimmed"
    trimmed_copy(full, trimmed, 2)
    checkpoint = trimmed / "checkpoint.json"
    document = json.loads(checkpoint.read_bytes())
    document["durable_sequence"] = 1
    checkpoint.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")),
                          encoding="utf-8")
    with pytest.raises(SpoolCorruption, match="regressed|cover"):
        scan(trimmed, proof)
    document["incarnation"] = "other"
    checkpoint.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")),
                          encoding="utf-8")
    with pytest.raises(SpoolCorruption):
        scan(trimmed, proof)


def test_consumer_cursor_identity_and_bound_checks(tmp_path):
    build_full(tmp_path, PAYLOADS[:4])
    with pytest.raises(ValueError):
        capture(tmp_path, 2, consumers=())
    with pytest.raises(ValueError):
        capture(tmp_path, 2, consumers=[SpoolCursor(PRODUCER, "other", 1)])
    with pytest.raises(ValueError):
        capture(tmp_path, 2, consumers=[SpoolCursor(PRODUCER, INCARNATION, 0)])
    same_cursor = SpoolCursor(PRODUCER, INCARNATION, 1)
    reused = capture(tmp_path, 2, consumers=[same_cursor, same_cursor])
    assert [cursor.sequence for cursor in reused.consumer_cursors] == [1, 1]
    many = [SpoolCursor(PRODUCER, INCARNATION, 1) for _ in range(18)]
    with pytest.raises(ValueError):
        capture(tmp_path, 2, consumers=many)
    proof = capture(tmp_path, 2, consumers=[SpoolCursor(PRODUCER, INCARNATION, 1),
                                           SpoolCursor(PRODUCER, INCARNATION, 4)])
    assert len(proof.consumer_cursors) == 2


def test_spool_reader_batch_byte_budget_still_leaves_cursor_retryable(tmp_path):
    full = tmp_path / "full"
    trimmed = tmp_path / "trimmed"
    build_full(full, PAYLOADS[:3])
    proof = capture(full, 2)
    trimmed_copy(full, trimmed, 2)
    rows = list(read_spool_records(
        trimmed, producer_id=PRODUCER, incarnation=INCARNATION,
        after=SpoolCursor(PRODUCER, INCARNATION, 1), retained_prefix=proof,
        **read_limits()))
    live = reader(trimmed, proof, SpoolCursor(PRODUCER, INCARNATION, 1))
    with pytest.raises(SpoolRotateRequired):
        live.read_batch(max_records=2, max_bytes=rows[0].frame_bytes - 1)
    assert live.cursor.sequence == 1
    assert tuple(row.payload for row in live.read_batch(
        max_records=2, max_bytes=rows[0].frame_bytes)) == (PAYLOADS[1],)
    assert live.cursor.sequence == 2
