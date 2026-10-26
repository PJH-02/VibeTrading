"""Independent proof-intersection probes; all data/backends are owned local copies."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_retained_storage import item, write_source, capture, budgets, observation, trade, wire
from storage.archive import ArchiveMaintenance
from storage.local_storage import LocalSpoolStorage
from storage.market_spool import SpoolCursor
from vibetrading.settings import RuntimeSettings
from storage.sqlite_store import RuntimeStore
from storage.storage_backend import LocalStorageBackend, sha256_file


@pytest.fixture(params=("legacy","compact"))
def setup(request,tmp_path):
    def create(*,facts=False):
        folder=tmp_path/("facts" if facts else "market");folder.mkdir()
        data=folder/"data";env=folder/"test.env"
        env.write_text(f"VIBETRADING_DATA_ROOT={data}\nDELIVERY_LEDGER_FORMAT={request.param}\nARCHIVE_BACKEND=local\n")
        settings=RuntimeSettings.from_env(env);source=item(data/"spools"/"source")
        payloads=[observation(payload=json.dumps({"event_type":"system","probe":n}).encode())[0]
                  if facts else wire(trade(n)) for n in range(1,4)]
        write_source(source,payloads)
        plan=SimpleNamespace(producers=() if facts else (source,),
            observation=source if facts else None,storage=budgets().__dict__)
        store=RuntimeStore(settings);storage=LocalSpoolStorage(plan,settings,budgets(),store=store)
        assert storage.step()["records"]==3
        maintenance=ArchiveMaintenance(settings,store);backend=LocalStorageBackend(folder/"remote")
        maintenance._backend=backend
        for row in store.list_manifest(active_only=True): maintenance._upload(row)
        now=2_000_000_000_000_000_000
        for path in source.spool_root.glob("*.sealed"):
            os.utime(path,ns=(now-10_000_000_000,now-10_000_000_000))
        progress=SpoolCursor(source.producer,source.incarnation,3)
        proof=capture(source.spool_root,3,consumers=(progress,progress))
        generation=store.collection_generation();prefix=settings.archive.prefix
        remote_keys={}
        for n in (1,2):
            path=source.spool_root/f"{n:020d}.sealed"
            key=f"{prefix}/generations/{generation}/spool/{source.producer}/{n}-{sha256_file(path)}.sealed"
            backend.put_file(path,key,sha256=sha256_file(path));remote_keys[n]=key
        readers={"storage":progress,"engine":progress};leases=[()]
        options=dict(producer=source.producer,retained_prefix=proof,reader_progress=readers,
            required_readers=("storage","engine"),lease_snapshot=lambda:(proof.generation,leases[0]),
            remote_keys=remote_keys,expected_root=source.spool_root.resolve(),
            expected_device=source.spool_root.stat().st_dev,now_ns=now,retention_ns=1_000_000_000,
            max_segments=8,max_records=128,max_bytes=2_000_000)
        state=SimpleNamespace(folder=folder,settings=settings,source=source,store=store,storage=storage,
            maintenance=maintenance,backend=backend,options=options,readers=readers,leases=leases,
            payloads=payloads,facts=facts)
        created.append(state);return state
    created=[]
    yield create
    for state in created:
        state.storage.close();state.store.close()


def call(s,**changes):
    return s.maintenance.plan_spool_gc(s.storage,**{**s.options,**changes})


def accepted(s):
    result=call(s)
    assert result["dry_run"] is True and result["executable"] is False
    assert result["deleted"]==0 and len(result["eligible"])==2,result
    return result


def preserved(s):
    return ({p.name:hashlib.sha256(p.read_bytes()).hexdigest()
             for p in s.source.spool_root.iterdir() if p.is_file()},
            s.store._conn.total_changes,s.storage._ledger.connection.total_changes,
            list(s.store._conn.iterdump()),list(s.storage._ledger.connection.iterdump()))


def blocked(s,result):
    assert result["eligible"]==[] and result["deleted"]==0 and result["executable"] is False,result


def test_independent_market_dry_run_is_read_only_and_entire_cut(setup):
    s=setup();before=preserved(s);accepted(s);assert preserved(s)==before


def test_independent_fact_dry_run_requires_actual_raw_fact(setup):
    s=setup(facts=True);accepted(s)
    s.storage._ledger.connection.execute("UPDATE local_fact SET raw_payload=? WHERE sequence=1",(b"tampered",))
    before=preserved(s);blocked(s,call(s));assert preserved(s)==before


def test_independent_remote_failure_never_leaves_a_partial_eligible_prefix(setup):
    s=setup();accepted(s);path=s.backend._target(s.options["remote_keys"][2]);raw=path.read_bytes()
    path.write_bytes(b"X"+raw[1:]);before=preserved(s)
    blocked(s,call(s));assert preserved(s)==before


def test_independent_wrong_event_at_valid_cursor_is_not_commit_provenance(setup):
    s=setup();accepted(s);ids=[json.loads(raw)["event_id"] for raw in s.payloads]
    if s.store.compact_delivery_ledger:
        s.store._conn.execute("UPDATE parquet_delivery_compact SET event_key=? WHERE stream_sequence=1",(s.store._event_key(ids[1]),))
        s.store._conn.execute("UPDATE parquet_delivery_compact SET event_key=? WHERE stream_sequence=3",(s.store._event_key(ids[0]),))
    else:
        s.store._conn.execute("UPDATE parquet_delivery SET event_id=? WHERE stream_sequence=1",(ids[1],))
        s.store._conn.execute("UPDATE parquet_delivery SET event_id=? WHERE stream_sequence=3",(ids[0],))
    assert s.store.event_committed(ids[0])
    assert s.store.delivery_committed("LOCAL:"+s.source.producer,s.source.incarnation,1)
    before=preserved(s);blocked(s,call(s));assert preserved(s)==before


def changing_verify(s,operation):
    original=s.backend.verify;invoked=[]
    def verify(key,**kwargs):
        result=original(key,**kwargs)
        if not invoked:
            invoked.append(key);operation()
        return result
    s.backend.verify=verify
    return invoked


def test_independent_ack_mutation_during_readback_invalidates_whole_plan(setup):
    s=setup();accepted(s)
    invoked=changing_verify(s,lambda:s.readers.update(engine=SpoolCursor(s.source.producer,s.source.incarnation,1)))
    result=call(s);assert invoked;blocked(s,result)


def test_independent_new_lease_during_readback_invalidates_whole_plan(setup):
    s=setup();accepted(s)
    invoked=changing_verify(s,lambda:s.leases.__setitem__(0,(1,)))
    result=call(s);assert invoked;blocked(s,result)


def test_independent_same_connection_progress_mutation_is_detected(setup):
    s=setup();accepted(s)
    invoked=changing_verify(s,lambda:s.storage._ledger.connection.execute(
        "UPDATE local_progress SET payload_sha256=?",("0"*64,)))
    result=call(s);assert invoked;blocked(s,result)


def test_independent_checkpoint_rollover_during_readback_keeps_every_source(setup):
    s=setup();accepted(s);cp=s.source.spool_root/"checkpoint.json"
    def change():
        d=json.loads(cp.read_bytes());d["durable_sequence"]=2
        cp.write_text(json.dumps(d,sort_keys=True,separators=(",",":")))
    invoked=changing_verify(s,change);result=call(s);assert invoked;blocked(s,result)
    assert len(list(s.source.spool_root.glob("*.sealed")))==3


def test_independent_false_dry_run_never_reaches_backend(setup):
    s=setup();accepted(s);before=preserved(s);called=[]
    def forbidden(*args,**kwargs):called.append(True);raise AssertionError("readback after invalid request")
    s.backend.verify=forbidden
    with pytest.raises(ValueError):call(s,dry_run=False)
    assert not called and preserved(s)==before


def test_independent_intent_inside_data_root_is_rejected_before_io(setup):
    s=setup();accepted(s);path=s.settings.data_root/"proposed-gc.json";before=preserved(s);called=[]
    def forbidden(*args,**kwargs):called.append(True);raise AssertionError("readback after bad output path")
    s.backend.verify=forbidden
    with pytest.raises(ValueError):call(s,intent_path=path)
    assert not called and not path.exists() and preserved(s)==before


def test_independent_foreign_device_is_rejected_before_remote_io(setup):
    s=setup();accepted(s);before=preserved(s);called=[]
    def forbidden(*args,**kwargs):called.append(True);raise AssertionError("readback with wrong device")
    s.backend.verify=forbidden
    try:result=call(s,expected_device=s.options["expected_device"]+1)
    except ValueError:pass
    else:blocked(s,result)
    assert not called and preserved(s)==before



def test_independent_intent_destination_appearing_during_verify_is_preserved(setup):
    s=setup();accepted(s);evidence=s.folder/"evidence";evidence.mkdir();target=evidence/"plan.json"
    invoked=changing_verify(s,lambda:target.write_bytes(b"other-owner-evidence"))
    result=call(s,intent_path=target);assert invoked;blocked(s,result)
    assert target.read_bytes()==b"other-owner-evidence"


def test_independent_intent_parent_rebinding_cannot_overwrite_spool_checkpoint(setup):
    s=setup();accepted(s);evidence=s.folder/"evidence";evidence.mkdir();target=evidence/"checkpoint.json"
    source_checkpoint=s.source.spool_root/"checkpoint.json";original=source_checkpoint.read_bytes()
    def rebind():
        evidence.rename(s.folder/"old-evidence")
        evidence.symlink_to(s.source.spool_root,target_is_directory=True)
    invoked=changing_verify(s,rebind)
    result=call(s,intent_path=target);assert invoked;blocked(s,result)
    assert source_checkpoint.read_bytes()==original



def test_independent_evidence_iterable_capacity_is_checked_before_materialization(setup):
    from storage.market_spool import read_spool_records
    s=setup();accepted(s)
    record=next(read_spool_records(s.source.spool_root,producer_id=s.source.producer,
        incarnation=s.source.incarnation,**s.source.limits.reader_kwargs))
    def excessive():
        for n in range(1000):
            if n>500:raise AssertionError("unbounded evidence iterable consumed")
            yield record
    before=preserved(s)
    with pytest.raises((ValueError,RuntimeError)):
        s.storage.spool_gc_evidence(s.source.producer,excessive())
    assert preserved(s)==before



def test_independent_plan_records_exact_raw_archive_recovery_locations(setup):
    s=setup();result=accepted(s);serialized=json.dumps(result,sort_keys=True)
    for key in s.options["remote_keys"].values():
        assert key in serialized,"audit plan omitted verified raw spool archive location"


def test_independent_raw_remote_mapping_change_during_verify_invalidates_plan(setup):
    s=setup();accepted(s)
    def remap():s.options["remote_keys"][2] += ".changed"
    invoked=changing_verify(s,remap);result=call(s);assert invoked;blocked(s,result)
