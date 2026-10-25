"""Codex registration oracle: bad provenance fails before import or durable claim."""
from decimal import Decimal as D
import hashlib,json
import pytest
from test_strategy_runtime import ledger_at,matcher_for,runtime,T
from test_vn13_file_registration import configured
import vibetrading.strategy_runtime as mod

@pytest.mark.parametrize("failure",["manifest_tamper","object_tamper","future_availability","bool_limit","path_alias"])
def test_untrusted_manifest_never_reaches_module_loader_or_claim(tmp_path,monkeypatch,failure):
    root=tmp_path/"export";root.mkdir();obj=root/"a.parquet";obj.write_bytes(b"abc")
    row={"relative_path":"a.parquet","file_id":"one","byte_size":3,
         "sha256":hashlib.sha256(b"abc").hexdigest(),"min_ts_ns":1,"max_ts_ns":2}
    manifest=root/"manifest.jsonl";raw=(json.dumps(row)+"\n").encode();manifest.write_bytes(raw)
    desc={"path":str(manifest),"sha256":hashlib.sha256(raw).hexdigest(),
          "available_at_ns":T,"max_manifest_bytes":4096,"max_files":2,"max_total_bytes":100}
    if failure=="manifest_tamper":manifest.write_bytes(raw+b" ")
    elif failure=="object_tamper":obj.write_bytes(b"abd")
    elif failure=="future_availability":desc["available_at_ns"]=T+1
    elif failure=="bool_limit":desc["max_files"]=True
    if failure=="path_alias":
        other=dict(row,file_id="two",relative_path="./a.parquet")
        altered=(json.dumps(row)+"\n"+json.dumps(other)+"\n").encode()
        manifest.write_bytes(altered);desc["sha256"]=hashlib.sha256(altered).hexdigest()
    ledger=ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT",D(10000),{"source":"synthetic"})
        host=runtime(ledger,matcher_for(ledger),[T]);document=configured(ledger,"never_import_this","one")
        document["research_receipt"]["data_manifest"]=desc
        def forbidden(*args):raise AssertionError("invalid manifest reached module import")
        monkeypatch.setattr(mod,"load_strategy",forbidden)
        before=tuple(ledger._db.iterdump())
        with pytest.raises((ValueError,RuntimeError)):
            host.register_configured(document,action_builder=lambda *args:[])
        assert not host._jobs and tuple(ledger._db.iterdump())==before
    finally:
        ledger.close()
