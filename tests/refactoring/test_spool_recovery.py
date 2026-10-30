from __future__ import annotations

import errno
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import pytest

from storage import market_spool
from storage.market_spool import MarketSpool, SpoolCorruption, SpoolCursor, SpoolError, SpoolReader, SpoolRotateRequired, read_spool_records, scan_spool


def limits(**updates):
    values = dict(max_disk_bytes=1024 * 1024, max_records=32, max_frame_bytes=2048,
                  max_header_bytes=512, segment_max_bytes=4096, segment_max_records=8,
                  segment_max_age_seconds=60)
    values.update(updates)
    return values


def writer(root, **updates):
    return MarketSpool(root, "grvt-collector", "incarnation-A", **limits(**updates))


def read_limits(**updates):
    values = limits(**updates)
    return {key: values[key] for key in ("max_disk_bytes", "max_records", "max_frame_bytes", "max_header_bytes")}


def report(root, **updates):
    return scan_spool(root, producer_id="grvt-collector", incarnation="incarnation-A", **read_limits(**updates))


def incremental(root, after=None, **updates):
    return SpoolReader(root, "grvt-collector", "incarnation-A", after=after, **read_limits(**updates))


def abort_without_flush(spool):
    # Controlled file/lock release models process loss without invoking close's
    # flush. It does not claim to reproduce a physical power failure.
    if spool._file is not None:
        spool._file.close()
        spool._file = None
    spool._closed = True
    spool._release_lock()


def bytes_on_disk(root):
    return {path.name: path.read_bytes() for path in root.iterdir() if path.name != "writer.lock"}


def test_appended_is_not_durable_and_raw_bytes_survive_seal_restart_without_nats(tmp_path, monkeypatch):
    calls = []
    def unavailable(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("NATS is completely unavailable")
    monkeypatch.setitem(sys.modules, "nats", SimpleNamespace(connect=unavailable))
    payloads = [
        b'{"price":"0.123456789012345678","quantity":"0","source_ts_ns":null}',
        '{"raw_payload":"원문/交易","bids":[["1.000000000000000001","0"]]}'.encode(),
    ]
    spool = writer(tmp_path)
    for index, payload in enumerate(payloads, 1):
        receipt = spool.append(payload)
        assert receipt.status == "APPENDED" and not receipt.uncertain
        assert receipt.cursor == SpoolCursor("grvt-collector", "incarnation-A", index)
        assert receipt.cursor.producer_id == receipt.cursor.producer
        assert spool.durable_cursor.sequence == 0
    assert list(spool.iter_records()) == []
    assert [record.payload for record in spool.iter_records(durable_only=False)] == payloads
    assert spool.flush().sequence == 2
    assert [record.payload for record in spool.iter_records()] == payloads
    assert spool.seal().sequence == 2
    spool.close()
    reopened = writer(tmp_path)
    assert reopened.appended_cursor.sequence == reopened.durable_cursor.sequence == 2
    assert [record.payload for record in reopened.iter_records(after=SpoolCursor("grvt-collector", "incarnation-A", 1))] == payloads[1:]
    assert reopened.append(b'{"next":3}').cursor.sequence == 3
    reopened.close()
    assert calls == []
    assert report(tmp_path).record_count == 3


def test_append_never_fsyncs_and_explicit_flush_publishes_cursor_in_order(tmp_path, monkeypatch):
    spool = writer(tmp_path)
    calls = []
    original_sync = market_spool.os.fsync
    def sync(fd):
        calls.append(fd)
        return original_sync(fd)
    monkeypatch.setattr(market_spool.os, "fsync", sync)
    spool.append(b'{"one":1}')
    spool.append(b'{"two":2}')
    assert calls == []
    spool.flush()
    assert len(calls) >= 2  # Data file then checkpoint, plus Linux directory.
    assert spool.durable_cursor.sequence == 2
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_bytes())
    assert checkpoint["durable_sequence"] == 2
    assert spool.directory_fsync_supported == (market_spool.os.name != "nt")
    spool.close()


@pytest.mark.parametrize("rotation", ("records", "bytes", "age"))
def test_segment_limits_require_explicit_seal_and_preserve_fifo(tmp_path, monkeypatch, rotation):
    options = {"segment_max_records": 1} if rotation == "records" else {}
    if rotation == "bytes":
        options = {"segment_max_bytes": 2048}
    spool = writer(tmp_path, **options)
    first = b"x" * 1100 if rotation == "bytes" else b"first"
    second = b"y" * 1100 if rotation == "bytes" else b"second"
    spool.append(first)
    if rotation == "age":
        monkeypatch.setattr(market_spool.time, "monotonic", lambda: spool._active_since + 61)
    with pytest.raises(SpoolRotateRequired):
        spool.append(second)
    assert spool.appended_cursor.sequence == 1 and spool.durable_cursor.sequence == 0
    spool.seal()
    # Restore age clock: the second segment starts a fresh age interval.
    monkeypatch.undo()
    spool.append(second)
    spool.close()
    assert [record.payload for record in read_spool_records(
        tmp_path, producer_id="grvt-collector", incarnation="incarnation-A", **read_limits())] == [first, second]
    assert len(list(tmp_path.glob("*.sealed"))) == 2


def test_active_partial_and_uncheckpointed_tail_are_reported_and_preserved(tmp_path):
    spool = writer(tmp_path)
    spool.append(b"durable")
    spool.flush()
    spool.append(b"complete-but-uncheckpointed")
    abort_without_flush(spool)
    active = next(tmp_path.glob("*.active"))
    with active.open("ab") as handle:
        handle.write(b"VTSP")
    before = bytes_on_disk(tmp_path)
    scan = report(tmp_path)
    assert scan.last_sequence == 2 and scan.durable_sequence == 1
    assert scan.partial_tail_bytes == 4 and scan.partial_tail_offset is not None
    assert scan.uncertain_from_sequence == 2 and scan.uncertain_to_sequence == 2
    assert "active_partial_tail" in scan.uncertainty_reason
    assert "complete_records_beyond_durable_checkpoint" in scan.uncertainty_reason
    assert [record.payload for record in read_spool_records(
        tmp_path, producer_id="grvt-collector", incarnation="incarnation-A", **read_limits())] == [b"durable"]
    for _ in range(2):
        with pytest.raises(SpoolError, match="uncertain-tail") as caught:
            writer(tmp_path)
        assert caught.value.receipt.status == "UNCERTAIN"
        assert bytes_on_disk(tmp_path) == before


@pytest.mark.parametrize("kind", ("sealed_truncation", "sealed_checksum", "active_checksum", "oversize_header"))
def test_complete_frame_corruption_and_sealed_truncation_fail_closed(tmp_path, kind):
    spool = writer(tmp_path)
    spool.append(b"first frame")
    spool.append(b"second frame")
    if kind.startswith("sealed"):
        spool.close()
        path = next(tmp_path.glob("*.sealed"))
    else:
        spool.flush()
        abort_without_flush(spool)
        path = next(tmp_path.glob("*.active"))
    data = bytearray(path.read_bytes())
    if kind == "sealed_truncation":
        del data[-3:]
    elif kind == "oversize_header":
        data[8:12] = struct.pack(">I", 513)
    else:
        header_size = struct.unpack(">I", data[8:12])[0]
        data[48 + header_size] ^= 1
    path.write_bytes(data)
    before = bytes_on_disk(tmp_path)
    with pytest.raises(SpoolCorruption):
        report(tmp_path)
    with pytest.raises(SpoolCorruption):
        writer(tmp_path)
    assert bytes_on_disk(tmp_path) == before


def test_unknown_incarnation_and_out_of_range_cursor_are_never_silently_replayed(tmp_path):
    spool = writer(tmp_path)
    spool.append(b"one"); spool.close()
    with pytest.raises(SpoolCorruption, match="incarnation"):
        MarketSpool(tmp_path, "grvt-collector", "other-incarnation", **limits())
    for cursor in (SpoolCursor("other-producer", "incarnation-A", 0),
                   SpoolCursor("grvt-collector", "other", 0),
                   SpoolCursor("grvt-collector", "incarnation-A", 2)):
        with pytest.raises(SpoolCorruption):
            list(read_spool_records(tmp_path, producer_id="grvt-collector", incarnation="incarnation-A",
                                    after=cursor, **read_limits()))


def test_second_writer_is_rejected_without_disrupting_owner(tmp_path):
    spool = writer(tmp_path)
    with pytest.raises(SpoolError, match="already has a writer"):
        writer(tmp_path)
    spool.append(b"still owned"); spool.close()
    assert report(tmp_path).durable_sequence == 1


@pytest.mark.parametrize("budget", ("frame", "records", "disk"))
def test_capacity_rejection_is_finite_and_fail_closed_without_deletion(tmp_path, budget):
    options = {"max_records": 1} if budget == "records" else {"max_disk_bytes": 5000} if budget == "disk" else {}
    spool = writer(tmp_path, **options)
    if budget == "frame":
        payload = b"x" * 2048
    elif budget == "records":
        spool.append(b"accepted"); payload = b"rejected"
    else:
        # Keep both records in one legal segment; leave insufficient room for
        # the third plus the mandatory checkpoint staging reservation.
        for _ in range(3):
            spool.append(b"x" * 1000)
        payload = b"x" * 1000
    before = bytes_on_disk(tmp_path)
    with pytest.raises(SpoolError, match="limit") as caught:
        spool.append(payload)
    assert caught.value.receipt.status == "REJECTED" and not caught.value.receipt.uncertain
    with pytest.raises(SpoolError, match="fail-closed"):
        spool.append(b"cannot bypass")
    assert bytes_on_disk(tmp_path) == before
    with pytest.raises(SpoolError):
        spool.close()


@pytest.mark.parametrize("error_number", (errno.EIO, errno.ENOSPC))
def test_partial_write_error_latches_uncertainty_and_preserves_bytes(tmp_path, error_number):
    spool = writer(tmp_path)
    spool.append(b"first"); spool.flush()
    actual = spool._file
    class BrokenWriter:
        def write(self, view):
            actual.write(view[:7])
            raise OSError(error_number, "offline write failure")
        def close(self):
            actual.close()
    spool._file = BrokenWriter()
    with pytest.raises(SpoolError, match="I/O uncertain") as caught:
        spool.append(b"uncertain")
    assert caught.value.receipt.uncertain and caught.value.receipt.cursor.sequence == 2
    with pytest.raises(SpoolError, match="fail-closed"):
        spool.flush()
    with pytest.raises(SpoolError):
        spool.close()
    scan = report(tmp_path)
    assert scan.durable_sequence == 1 and scan.partial_tail_bytes == 7
    assert scan.uncertain


@pytest.mark.parametrize("failure_stage", ("data_fsync", "checkpoint_replace", "directory_fsync"))
def test_failed_flush_never_advances_in_memory_durable_cursor(tmp_path, monkeypatch, failure_stage):
    spool = writer(tmp_path)
    spool.append(b"one")
    if failure_stage == "data_fsync":
        def failed(_fd):
            raise OSError(errno.EIO, "offline fsync failed")
        monkeypatch.setattr(market_spool.os, "fsync", failed)
    elif failure_stage == "checkpoint_replace":
        def failed(_source, _destination):
            raise OSError(errno.EIO, "offline replace failed")
        monkeypatch.setattr(market_spool.os, "replace", failed)
    else:
        def failed(_root):
            raise OSError(errno.EIO, "offline directory sync failed")
        monkeypatch.setattr(market_spool, "_sync_directory", failed)
    with pytest.raises(SpoolError, match="durability uncertain") as caught:
        spool.flush()
    assert caught.value.receipt.status == "UNCERTAIN"
    assert spool.durable_cursor.sequence == 0
    with pytest.raises(SpoolError):
        spool.close()
    assert next(tmp_path.glob("*.active")).read_bytes()


def test_missing_segment_and_invalid_checkpoint_do_not_authorize_recovery(tmp_path):
    spool = writer(tmp_path, segment_max_records=1)
    spool.append(b"one"); spool.seal(); spool.append(b"two"); spool.close()
    # The fixture owns these files. Production spool has no deletion API.
    (tmp_path / "00000000000000000001.sealed").unlink()
    with pytest.raises(SpoolCorruption, match="missing"):
        report(tmp_path)


def test_checkpoint_ahead_of_complete_records_is_corruption_and_gc_stays_disabled(tmp_path):
    spool = writer(tmp_path)
    spool.append(b"one"); spool.close()
    assert spool.gc_disabled()["deleted"] == 0
    checkpoint = tmp_path / "checkpoint.json"
    document = json.loads(checkpoint.read_bytes()); document["durable_sequence"] = 2
    checkpoint.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SpoolCorruption, match="exceeds"):
        report(tmp_path)


@pytest.mark.parametrize("parameter,value", (("max_disk_bytes", 0), ("max_records", -1),
    ("max_frame_bytes", True), ("max_header_bytes", 0), ("segment_max_records", 0),
    ("segment_max_bytes", 1024), ("segment_max_age_seconds", float("inf"))))
def test_limits_are_required_finite_and_positive(tmp_path, parameter, value):
    with pytest.raises(ValueError):
        writer(tmp_path, **{parameter: value})


def test_incremental_reader_polls_only_new_offsets_and_survives_seal_rotation(tmp_path, monkeypatch):
    spool = writer(tmp_path, segment_max_records=1)
    spool.append(b"one"); spool.seal()
    spool.append(b"two"); spool.seal()
    spool.append(b"three"); spool.flush()
    reader = incremental(tmp_path, SpoolCursor("grvt-collector", "incarnation-A", 2))
    calls = []
    original_frames = market_spool._frames
    def counted(path, *args, **kwargs):
        calls.append((path.name, kwargs.get("start_offset", 0)))
        yield from original_frames(path, *args, **kwargs)
    monkeypatch.setattr(market_spool, "_frames", counted)
    assert [row.payload for row in reader.read_batch(max_records=8, max_bytes=8192)] == [b"three"]
    assert reader.cursor.sequence == 3
    assert all(not name.startswith("00000000000000000001") for name, _ in calls)
    assert calls[0][0].endswith("2.sealed") and calls[0][1] > 0
    count = len(calls)
    for _ in range(3):
        assert reader.read_batch(max_records=8, max_bytes=8192) == ()
    assert len(calls) == count  # Empty polls do not reopen historical records.
    spool.seal(); spool.append(b"four"); spool.flush()
    assert [row.payload for row in reader.read_batch(max_records=8, max_bytes=8192)] == [b"four"]
    assert reader.cursor.sequence == reader.durable_cursor.sequence == 4
    assert all(not name.startswith("00000000000000000001") for name, _ in calls)
    spool.close()


def test_incremental_reader_batch_limits_and_first_oversize_leave_cursor_retryable(tmp_path):
    spool = writer(tmp_path)
    spool.append(b"one"); spool.append(b"two"); spool.flush()
    rows = list(spool.iter_records())
    reader = incremental(tmp_path)
    with pytest.raises(SpoolRotateRequired, match="first frame"):
        reader.read_batch(max_records=2, max_bytes=rows[0].frame_bytes - 1)
    assert reader.cursor.sequence == 0
    first = reader.read_batch(max_records=2, max_bytes=rows[0].frame_bytes)
    assert tuple(row.payload for row in first) == (b"one",)
    assert sum(row.frame_bytes for row in first) <= rows[0].frame_bytes
    assert reader.cursor.sequence == 1
    second = reader.read_batch(max_records=1, max_bytes=8192)
    assert tuple(row.payload for row in second) == (b"two",)
    assert reader.cursor.sequence == 2
    spool.close()


def test_incremental_reader_excludes_uncheckpointed_active_tail(tmp_path):
    spool = writer(tmp_path)
    spool.append(b"durable"); spool.flush()
    reader = incremental(tmp_path)
    spool.append(b"appended")
    assert tuple(row.payload for row in reader.read_batch(max_records=8, max_bytes=8192)) == (b"durable",)
    assert reader.read_batch(max_records=8, max_bytes=8192) == ()
    spool.flush()
    assert tuple(row.payload for row in reader.read_batch(max_records=8, max_bytes=8192)) == (b"appended",)
    spool.close()


@pytest.mark.parametrize("change", ("regression", "incarnation", "partial_durable_tail", "checksum"))
def test_incremental_integrity_failure_keeps_entire_undelivered_batch_cursor(tmp_path, change):
    spool = writer(tmp_path)
    spool.append(b"one"); spool.flush()
    reader = incremental(tmp_path)
    assert len(reader.read_batch(max_records=8, max_bytes=8192)) == 1
    spool.append(b"two"); spool.append(b"three"); spool.flush()
    abort_without_flush(spool)
    checkpoint = tmp_path / "checkpoint.json"
    document = json.loads(checkpoint.read_bytes())
    active = next(tmp_path.glob("*.active"))
    if change == "regression":
        document["durable_sequence"] = 0
    elif change == "incarnation":
        document["incarnation"] = "other"
    else:
        data = bytearray(active.read_bytes())
        if change == "partial_durable_tail":
            del data[-2:]
        else:
            data[-1] ^= 1
        active.write_bytes(data)
    checkpoint.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SpoolCorruption):
        reader.read_batch(max_records=8, max_bytes=8192)
    # Even if row two was decoded before row three failed, neither was returned.
    assert reader.cursor.sequence == 1
    with pytest.raises(SpoolError, match="fail-closed"):
        reader.read_batch(max_records=8, max_bytes=8192)


def test_incremental_reader_detects_replaced_segment_even_with_valid_frame_bytes(tmp_path):
    spool = writer(tmp_path)
    spool.append(b"one"); spool.flush()
    reader = incremental(tmp_path)
    reader.read_batch(max_records=8, max_bytes=8192)
    spool.append(b"two"); spool.flush(); abort_without_flush(spool)
    active = next(tmp_path.glob("*.active"))
    replacement = tmp_path / "replacement.fixture"
    replacement.write_bytes(active.read_bytes())
    replacement.replace(active)
    with pytest.raises(SpoolCorruption, match="identity"):
        reader.read_batch(max_records=8, max_bytes=8192)
    assert reader.cursor.sequence == 1


def test_incremental_reader_rejects_unknown_start_cursor_and_keeps_identity_immutable(tmp_path):
    spool = writer(tmp_path)
    spool.append(b"one"); spool.flush()
    with pytest.raises(SpoolCorruption):
        incremental(tmp_path, SpoolCursor("grvt-collector", "foreign", 0))
    with pytest.raises(SpoolCorruption):
        incremental(tmp_path, SpoolCursor("grvt-collector", "incarnation-A", 2))
    reader = incremental(tmp_path)
    with pytest.raises(AttributeError):
        reader.incarnation = "foreign"
    with pytest.raises(AttributeError):
        reader.producer_id = "foreign"
    spool.close()


@pytest.mark.parametrize("parameter,value", (("max_records", 0), ("max_records", True),
    ("max_records", 1.5), ("max_bytes", 0), ("max_bytes", -1), ("max_bytes", True)))
def test_incremental_read_batch_requires_positive_integer_limits(tmp_path, parameter, value):
    spool = writer(tmp_path)
    reader = incremental(tmp_path)
    values = {"max_records": 8, "max_bytes": 8192, parameter: value}
    with pytest.raises(ValueError):
        reader.read_batch(**values)
    assert reader.cursor.sequence == 0
    spool.close()
