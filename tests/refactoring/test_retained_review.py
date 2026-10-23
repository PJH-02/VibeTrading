"""Independent VN08 fault probes using exact v1 spool frames, no accounts."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from test_spool_recovery import writer, read_limits, bytes_on_disk
from vibetrading import market_spool as spool
from vibetrading.market_spool import (SpoolCursor, SpoolReader, SpoolError, SpoolCorruption,
    RetainedPrefix, capture_retained_prefix, read_spool_records, scan_spool)

P, I = "grvt-collector", "incarnation-A"


def setup_tail(root):
    source, tail = root/"original", root/"retained"
    w = writer(source, segment_max_records=1)
    for payload in (b"first", b"second", b"third"):
        w.append(payload)
        w.seal()
    w.close()
    proof = capture_retained_prefix(source, P, I, first_retained_segment=2,
        generation="retention-1", consumer_cursors=(SpoolCursor(P,I,1),), **read_limits())
    tail.mkdir()
    for f in source.iterdir():
        if not f.name.endswith((".sealed", ".active")) or int(f.name.split(".")[0])>=2:
            shutil.copy2(f, tail/f.name)
    return source, tail, proof


def reader(tail, proof, sequence=1):
    return SpoolReader(tail,P,I,after=SpoolCursor(P,I,sequence),
                       retained_prefix=proof,**read_limits())


def checksum_valid_change(path):
    raw=bytearray(path.read_bytes())
    magic,hn,pn,_=spool._PREFIX.unpack(raw[:spool._PREFIX.size])
    offset=spool._PREFIX.size+hn
    raw[offset] ^= 32
    raw[:spool._PREFIX.size]=spool._PREFIX.pack(magic,hn,pn,
        hashlib.sha256(bytes(raw[spool._PREFIX.size:spool._PREFIX.size+hn+pn])).digest())
    path.write_bytes(raw)


def test_independent_trimmed_read_requires_explicit_proof_and_cursor(tmp_path):
    original, tail, proof=setup_tail(tmp_path)
    before=bytes_on_disk(original)
    with pytest.raises((SpoolError,ValueError)):
        SpoolReader(tail,P,I,**read_limits())
    with pytest.raises((SpoolError,ValueError)):
        SpoolReader(tail,P,I,retained_prefix=proof,**read_limits())
    with pytest.raises((SpoolError,ValueError)):
        reader(tail,proof,0)
    report=scan_spool(tail,producer_id=P,incarnation=I,retained_prefix=proof,**read_limits())
    assert report.base_sequence==1 and report.record_count==2 and report.last_sequence==3
    assert [r.payload for r in reader(tail,proof).read_batch(max_records=8,max_bytes=8192)]==[b"second",b"third"]
    assert bytes_on_disk(original)==before


def test_independent_checksum_valid_anchor_rewrite_is_not_a_verified_base(tmp_path):
    _,tail,proof=setup_tail(tmp_path)
    checksum_valid_change(tail/"00000000000000000002.sealed")
    with pytest.raises((SpoolError,ValueError)):
        reader(tail,proof)


def test_independent_anchor_fault_after_startup_keeps_batch_cursor_and_is_sticky(tmp_path):
    _,tail,proof=setup_tail(tmp_path)
    r=reader(tail,proof);start=r.cursor
    anchor=tail/"00000000000000000002.sealed";good=anchor.read_bytes()
    checksum_valid_change(anchor)
    with pytest.raises(SpoolError):
        r.read_batch(max_records=8,max_bytes=8192)
    assert r.cursor==start
    anchor.write_bytes(good)
    with pytest.raises(SpoolError):
        r.read_batch(max_records=8,max_bytes=8192)
    assert r.cursor==start


def test_independent_external_generation_and_hash_are_required(tmp_path):
    _,tail,proof=setup_tail(tmp_path);raw=proof.to_bytes();digest=hashlib.sha256(raw).hexdigest()
    with pytest.raises((SpoolError,ValueError)):
        RetainedPrefix.from_bytes(raw,expected_sha256="0"*64,expected_generation="retention-1")
    with pytest.raises((SpoolError,ValueError)):
        RetainedPrefix.from_bytes(raw,expected_sha256=digest,expected_generation="retention-2")
    reopened=RetainedPrefix.from_bytes(raw,expected_sha256=digest,expected_generation="retention-1")
    assert len(reader(tail,reopened).read_batch(max_records=8,max_bytes=8192))==2


def test_independent_capture_refuses_to_certify_an_already_missing_prefix(tmp_path):
    _,tail,proof=setup_tail(tmp_path)
    with pytest.raises((SpoolError,ValueError)):
        capture_retained_prefix(tail,P,I,first_retained_segment=3,generation="retention-2",
            consumer_cursors=(SpoolCursor(P,I,2),),**read_limits())


def test_independent_lagging_consumer_prevents_cut_capture(tmp_path):
    original,_,_=setup_tail(tmp_path)
    with pytest.raises((SpoolError,ValueError)):
        capture_retained_prefix(original,P,I,first_retained_segment=3,generation="retention-2",
            consumer_cursors=(SpoolCursor(P,I,2),SpoolCursor(P,I,1)),**read_limits())


def test_independent_missing_last_segment_is_not_successful_recovery(tmp_path):
    original,_,proof=setup_tail(tmp_path);tail=tmp_path/"missing-last";tail.mkdir()
    for name in ("checkpoint.json","00000000000000000002.sealed"):
        shutil.copy2(original/name,tail/name)
    with pytest.raises((SpoolError,ValueError)):
        reader(tail,proof)


def test_independent_durable_checkpoint_regression_rejected(tmp_path):
    _,tail,proof=setup_tail(tmp_path);cp=tail/"checkpoint.json";d=json.loads(cp.read_bytes())
    d["durable_sequence"]=2;cp.write_bytes(spool._json(d))
    with pytest.raises((SpoolError,ValueError)):
        reader(tail,proof)



def frame(sequence,payload):
    header=spool._json({"producer_id":P,"incarnation":I,"sequence":sequence})
    return spool._PREFIX.pack(spool._MAGIC,len(header),len(payload),
        hashlib.sha256(header+payload).digest())+header+payload


def set_durable(tail,sequence):
    cp=tail/"checkpoint.json";d=json.loads(cp.read_bytes());d["durable_sequence"]=sequence
    cp.write_bytes(spool._json(d))


def active_tail_copy(root):
    source,_,proof=setup_tail(root);tail=root/"active-copy";tail.mkdir()
    shutil.copy2(source/"checkpoint.json",tail/"checkpoint.json")
    shutil.copy2(source/"00000000000000000002.sealed",tail/"00000000000000000002.sealed")
    shutil.copy2(source/"00000000000000000003.sealed",tail/"00000000000000000003.active")
    return source,tail,proof


@pytest.mark.parametrize("reuse_object", [False, True])
def test_independent_same_confirmed_position_for_two_consumers_is_valid(tmp_path,reuse_object):
    source,_,_=setup_tail(tmp_path)
    position=SpoolCursor(P,I,1)
    positions=(position,position if reuse_object else SpoolCursor(P,I,1))
    proof=capture_retained_prefix(source,P,I,first_retained_segment=2,generation="two-readers",
        consumer_cursors=positions,**read_limits())
    assert proof.base_sequence==1


def test_independent_forward_growth_cannot_exceed_retained_record_capacity(tmp_path):
    _,tail,proof=active_tail_copy(tmp_path)
    r=SpoolReader(tail,P,I,after=SpoolCursor(P,I,1),retained_prefix=proof,
                  **read_limits(max_records=2))
    with (tail/"00000000000000000003.active").open("ab") as f:
        f.write(frame(4,b"fourth"))
    set_durable(tail,4)
    with pytest.raises(SpoolError):
        r.read_batch(max_records=8,max_bytes=8192)
    assert r.cursor.sequence==1


def test_independent_reported_durable_high_water_never_regresses(tmp_path):
    _,tail,proof=active_tail_copy(tmp_path);r=reader(tail,proof)
    with (tail/"00000000000000000003.active").open("ab") as f:
        f.write(frame(4,b"fourth")+frame(5,b"fifth"))
    set_durable(tail,5)
    assert [row.cursor.sequence for row in r.read_batch(max_records=1,max_bytes=8192)]==[2]
    assert r.durable_cursor.sequence==5
    set_durable(tail,4)
    with pytest.raises(SpoolError):
        r.read_batch(max_records=1,max_bytes=8192)
    assert r.cursor.sequence==2



def test_independent_capture_cannot_certify_consumer_ahead_of_producer(tmp_path):
    source,_,_=setup_tail(tmp_path)
    with pytest.raises((SpoolError,ValueError)):
        capture_retained_prefix(source,P,I,first_retained_segment=2,generation="ahead-consumer",
            consumer_cursors=(SpoolCursor(P,I,4),),**read_limits())


def test_independent_absolute_cursor_overflow_is_rejected_by_scan(tmp_path):
    _,tail,proof=setup_tail(tmp_path);maximum=2**63-1
    (tail/"00000000000000000002.sealed").write_bytes(frame(maximum,b"second"))
    (tail/"00000000000000000003.sealed").write_bytes(frame(maximum+1,b"third"))
    set_durable(tail,maximum+1)
    d=json.loads(proof.to_bytes());d.update(base_sequence=maximum-1,durable_sequence=maximum,
        consumer_cursors=[[P,I,maximum-1]],first_retained_segment_sha256=hashlib.sha256(
            (tail/"00000000000000000002.sealed").read_bytes()).hexdigest())
    raw=spool._json(d)
    bounded=RetainedPrefix.from_bytes(raw,expected_sha256=hashlib.sha256(raw).hexdigest(),
                                      expected_generation="retention-1")
    with pytest.raises((SpoolError,ValueError)):
        scan_spool(tail,producer_id=P,incarnation=I,retained_prefix=bounded,**read_limits())


def test_independent_inventory_enforces_capacity_before_accumulating_all_entries(tmp_path,monkeypatch):
    from types import SimpleNamespace
    _,tail,proof=setup_tail(tmp_path)
    class Entry:
        def __init__(self,n): self.name=f"{n:020d}.sealed"
        def is_file(self,**kwargs): return True
        def stat(self,**kwargs): return SimpleNamespace(st_size=1)
    class Inventory:
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def __iter__(self):
            for n in range(2,20):
                if n>5: raise AssertionError("inventory accumulated more than bounded segment capacity")
                yield Entry(n)
    monkeypatch.setattr(spool.os,"scandir",lambda *args: Inventory())
    with pytest.raises(SpoolError):
        scan_spool(tail,producer_id=P,incarnation=I,retained_prefix=proof,
                   **read_limits(max_records=2))
