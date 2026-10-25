"""Codex critical acceptance of real local failure boundaries; no product edits."""
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest
import vibetrading.local_runtime as local
from vibetrading.feed_recovery import EngineCursor, RecoveryFrame
from vibetrading.local_config import LocalRuntimePlan
from vibetrading.runtime_checkpoint import RuntimeCheckpoint
from test_local_runtime import document
from test_strategy_runtime import T, book, trade
from test_vn13_file_registration import strategy_files
from test_vn17_role_assembly import real_assembly
from test_tx05_file_paper_e2e import wait_until
from test_runtime_checkpoint_integration import save_exact_cut, target_runtime
from vibetrading.models import Side


@pytest.mark.asyncio
async def test_real_uds_gap_preserves_unresolved_exposure_and_shutdown_cleans_owned_socket(
        tmp_path, monkeypatch, strategy_files):
    async with real_assembly(tmp_path, monkeypatch, strategy_files) as env:
        await env.publish(book(T, instrument=env.instrument).model_copy(update={'provider':'kiwoom'}))
        await env.publish(trade(T+11, side=Side.SELL, price=99, instrument=env.instrument).model_copy(
            update={'provider':'kiwoom','quantity':D(5)}))
        order=env.ledger.report()['orders'][0]
        assert D(order['filled'])==0 and D(order['reservation'])>0
        before=env.ledger.report();callbacks=env.host.report()['strategies'][env.sid]['callbacks']
        # A rejected producer sequence emits GAP on the real UDS control lane.
        # No direct engine invalidation call substitutes for this fault.
        payload=book(T+12, instrument=env.instrument).model_dump_json().encode()
        assert not await env.client.publish(RecoveryFrame(EngineCursor(
            env.plan.producers[0].producer,env.plan.producers[0].incarnation,4),payload))
        await wait_until(lambda:env.host.report()['input_phase']=='INVALID')
        after=env.ledger.report();row=after['orders'][0]
        assert D(row['filled'])==0 and D(row['reservation'])==D(order['reservation'])
        assert row['state'] in {'UNKNOWN','CANCEL_PENDING'}
        assert after['balances']==before['balances']
        assert env.ledger.position('@physical',env.execution.key)==0
        assert env.host.report()['strategies'][env.sid]['callbacks']==callbacks
        endpoint=env.plan.endpoint
    assert not Path(str(endpoint)+'.control').exists()
    assert not Path(str(endpoint)+'.data').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['corrupt_bundle','readiness_denied'])
async def test_actual_run_engine_failed_coupled_restore_never_opens_listener_or_callbacks(
        tmp_path, monkeypatch, fault):
    item, checkpoint=await save_exact_cut(tmp_path)
    target=target_runtime(tmp_path,item)
    item.delivery=None
    raw=document(tmp_path);raw['allowed_uid']=__import__('os').getuid()
    plan=LocalRuntimePlan.from_document(raw)
    # Reuse the exact producer/checkpoint owner, retaining the plan's transport
    # budgets and private endpoint, rather than a checkpoint stub.
    from dataclasses import replace
    plan=replace(plan,producers=(item,))
    before=target.ledger.report();engines=[];actual=local.LocalMarketEngine
    def capture(**kwargs):
        engine=actual(**kwargs);engines.append(engine);return engine
    monkeypatch.setattr(local,'LocalMarketEngine',capture)
    monkeypatch.setattr(local.time,'time_ns',lambda:T)
    if fault=='corrupt_bundle':checkpoint.path.write_bytes(b'{"torn":')
    try:
        with pytest.raises((ValueError,RuntimeError)):
            await local.run_engine(plan,SimpleNamespace(env={},state_dir=tmp_path),
                paper_runtime=target.host,runtime_checkpoint=checkpoint,
                recovery_readiness=lambda *args:False)
        assert checkpoint.state=='QUARANTINED' and target.host.recovery_state=='QUARANTINED'
        assert engines[0].post_apply_callbacks==()
        assert not Path(str(plan.endpoint)+'.control').exists()
        assert not Path(str(plan.endpoint)+'.data').exists()
        assert target.host.report()['strategies']['one']['callbacks']==0
        after=target.ledger.report()
        assert after['balances']==before['balances']
        assert [(r['intent_id'],r['filled'],r['reservation']) for r in after['orders']]==[
            (r['intent_id'],r['filled'],r['reservation']) for r in before['orders']]
    finally:
        await target.engine.close();target.ledger.close()
