"""Independent role boundary: borrowed Paper cannot leak into another role."""
import json
from decimal import Decimal as D
import pytest
from test_local_runtime import document
from test_strategy_runtime import ledger_at,matcher_for,runtime,T
import vibetrading.local_runtime as module

@pytest.mark.parametrize("role",["check","collector","storage","telegram","dashboard"])
def test_borrowed_paper_role_is_rejected_before_settings_side_effects(tmp_path,monkeypatch,role):
    config=tmp_path/"plan.json";config.write_text(json.dumps(document(tmp_path)))
    ledger=ledger_at(tmp_path)
    try:
        host=runtime(ledger,matcher_for(ledger),[T])
        def forbidden(*args,**kwargs):raise AssertionError("invalid role reached settings/directory helper")
        monkeypatch.setattr(module.RuntimeSettings,"from_env",forbidden)
        with pytest.raises(ValueError):module.main([role,"--config",str(config)],paper_runtime=host)
    finally:ledger.close()

def test_replay_consumer_is_not_silently_promoted_by_engine_entrypoint(tmp_path,monkeypatch):
    config=tmp_path/"plan.json";config.write_text(json.dumps(document(tmp_path)))
    ledger=ledger_at(tmp_path)
    try:
        host=runtime(ledger,matcher_for(ledger),[T],phase="replay")
        def forbidden(*args,**kwargs):raise AssertionError("invalid phase reached settings/directory helper")
        monkeypatch.setattr(module.RuntimeSettings,"from_env",forbidden)
        with pytest.raises(ValueError):module.main(["engine","--config",str(config)],paper_runtime=host)
    finally:ledger.close()
