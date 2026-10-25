"""Independent real strategy-file/configuration integration and negative facts."""
from decimal import Decimal as D
from pathlib import Path
import importlib
import pytest
from test_strategy_runtime import T,BTC,ledger_at,matcher_for,runtime,registration,deliver,book


def file_and_document(tmp_path,monkeypatch,ledger,sid):
    package=importlib.import_module("vibetrading.strategies")
    root=tmp_path/"owned_plugins";root.mkdir(exist_ok=True)
    monkeypatch.setattr(package,"__path__",[*package.__path__,str(root)])
    text="""from decimal import Decimal
from vibetrading.models import Signal,SignalAction
STRATEGY_ID = SID
SUBSCRIPTIONS = [{"instrument_key": FEED, "event_type":"order_book"}]
WARMUP = {"bars":0}
class Plugin:
    def on_event(self,event,data):
        self.snapshot=data.hot_path
        return [Signal(strategy_id=STRATEGY_ID,instrument=event.instrument,
            action=SignalAction.ENTER_LONG,event_ts_ns=event.receive_ts_ns,
            reference_price=Decimal(100),valid_until_ts_ns=event.receive_ts_ns+100000,
            source_event_id=event.event_id)]
def create_strategy(): return Plugin()
"""
    target=root/(sid+".py");target.write_text(text.replace("SID",repr(sid)).replace("FEED",repr(BTC.key)))
    importlib.invalidate_caches()
    reg=registration(ledger,sid,[])
    document={name:getattr(reg,name)for name in ("job_id","strategy_version","config_version","config","research_receipt","paper_permission","required_feeds","warmup_bars","accounts")}
    document.update(module="strategies/"+sid+".py",risk={key:dict(vars(value))for key,value in reg.risk.items()})
    return target,document,reg.action_builder


def test_real_two_files_share_applied_snapshot_and_keep_account_attribution(tmp_path,monkeypatch):
    ledger=ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT",D(100000),{"synthetic":True})
        host=runtime(ledger,matcher_for(ledger),[T])
        for sid in ("reviewer_alpha","reviewer_beta"):
            path,document,builder=file_and_document(tmp_path,monkeypatch,ledger,sid)
            host.register_configured(document,action_builder=builder)
            assert Path(host._jobs[sid].registration.module.module.__file__)==path
        assert len(deliver(host,[T],book()))==2
        assert {row["strategy"]for row in ledger.report()["orders"]}=={"reviewer_alpha","reviewer_beta"}
        assert D(ledger.report()["reserved"]["USDT"])==D("201.96")
        assert host._jobs["reviewer_alpha"].instance.snapshot is host._jobs["reviewer_beta"].instance.snapshot
        assert host.hot_path.apply_seq==1
    finally:ledger.close()

@pytest.mark.parametrize("change",["unknown","missing_permission","oversize","traversal","risk","account_string"])
def test_bad_configuration_writes_no_claim_or_job(tmp_path,monkeypatch,change):
    ledger=ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT",D(100000),{"synthetic":True})
        host=runtime(ledger,matcher_for(ledger),[T]);_,doc,builder=file_and_document(tmp_path,monkeypatch,ledger,"reviewer_reject")
        if change=="unknown":doc["live_permission"]=True
        elif change=="missing_permission":del doc["paper_permission"]
        elif change=="oversize":doc["config"]={"padding":"x"*40000}
        elif change=="traversal":doc["module"]="strategies/../os.py"
        elif change=="risk":doc["risk"][ledger.scope.key]["account_capital_limit"]="-1"
        else:doc["accounts"]=ledger.scope.key
        before=tuple(ledger._db.iterdump())
        with pytest.raises((ValueError,TypeError)):
            host.register_configured(doc,action_builder=builder)
        assert tuple(ledger._db.iterdump())==before
        assert not host._jobs
    finally:ledger.close()
