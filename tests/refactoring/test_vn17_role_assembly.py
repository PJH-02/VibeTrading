"""Codex independent VN17 acceptance: borrowed owner, real file and real UDS."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal as D
import json
import os
from types import SimpleNamespace
from pathlib import Path

import pytest

import vibetrading.local_runtime as local
from vibetrading.dashboard.projection import paper_projection
from feeds.feed_recovery import EngineCursor, RecoveryFrame
from vibetrading.local_config import LocalRuntimePlan
from vibetrading.local_transport import LocalTransportClient
from vibetrading.market_spool import MarketSpool
from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
from vibetrading.trading_contracts import ActionKind, ExecutionAction, TradingMode
from test_local_runtime import document
from test_strategy_runtime import T, BTC, ledger_at, matcher_for, runtime, book, trade
from test_vn13_file_registration import configured, strategy_files, strategy_source, loaded
from test_tx05_file_paper_e2e import wait_until
from test_outbox_isolation import _controller


@pytest.fixture(autouse=True)
def preserve_role_environment(monkeypatch):
    for key in ('VIBETRADING_RUNTIME','LOCAL_SPOOL_DISK_BUDGET_BYTES',
                'LOCAL_MARKET_SNAPSHOT_FILE','LOCAL_SNAPSHOT_BUDGETS'):
        if key in os.environ:monkeypatch.setenv(key,os.environ[key])
        else:
            monkeypatch.setenv(key,'');monkeypatch.delenv(key)


@asynccontextmanager
async def real_assembly(tmp_path, monkeypatch, strategy_files):
    raw=document(tmp_path);raw['allowed_uid']=os.getuid()
    plan=LocalRuntimePlan.from_document(raw);item=plan.producers[0]
    instrument=InstrumentId(venue=Venue.KIWOOM,asset_class=AssetClass.ETF,
        raw_symbol='069500',std_symbol='069500',currency='KRW')
    execution=instrument.model_copy(update={'venue':Venue.KIS})
    ledger=ledger_at(tmp_path,execution);ledger.seed_balance('KRW',D(10000),{'source':'owned synthetic'})
    matcher=matcher_for(ledger);now=[T];host=runtime(ledger,matcher,now,instrument=execution,reference=instrument)
    sid='vn17_real_file';name=strategy_files(sid,strategy_source(sid).replace(BTC.key,instrument.key))
    doc=configured(ledger,name,'vn17-job',required_feeds=[instrument.key])
    doc['risk'][ledger.scope.key]['margin_rate']=None
    def actions(signal,view,config):
        identity='vn17-'+signal.signal_id
        intent=OrderIntent(intent_id=identity,job_id='vn17-job',signal_id=signal.signal_id,
            destination=OrderDestination.REAL,instrument=execution,side=Side.BUY,
            created_ts_ns=signal.event_ts_ns,client_order_id='client-'+identity,
            quantity=D(1),limit_price=D(99))
        return [ExecutionAction('action-'+identity,ActionKind.NEW,ledger.scope,sid,
            'config-1',TradingMode.PAPER,signal.valid_until_ts_ns,intent=intent,post_only=True)]
    host.register_configured(doc,action_builder=actions)
    assert host._jobs[sid].registration.module.module.__file__ == str(tmp_path/(name+'.py'))
    writer=local.create_spool(item);writer.close()
    engines=[];actual_engine=local.LocalMarketEngine
    def capture(**kwargs):
        engine=actual_engine(**kwargs);engines.append(engine);return engine
    monkeypatch.setattr(local,'LocalMarketEngine',capture)
    monkeypatch.setattr(local.time,'time_ns',lambda:now[0])
    state=tmp_path/'state';state.mkdir(exist_ok=True)
    task=asyncio.create_task(local.run_engine(plan,SimpleNamespace(env={},state_dir=state),
        paper_runtime=host,paper_feed_dependencies={item.producer:(instrument.key,)}))
    client=None;writer=None
    try:
        await wait_until(lambda: task.done() or Path(str(plan.endpoint)+'.control').exists())
        if task.done():task.result()
        client=LocalTransportClient(plan.endpoint,producer=item.producer,incarnation=item.incarnation,
            budgets=plan.transport,on_gap=lambda *args:None,allowed_uid=os.getuid())
        await client.connect()
        resumed=await client.request_resume(EngineCursor(item.producer,item.incarnation,0))
        assert resumed.phase=='RESUMED'
        writer=local.create_spool(item)
        async def publish(event):
            now[0]=event.receive_ts_ns
            payload=event.model_dump_json().encode();receipt=writer.append(payload);writer.flush()
            cursor=EngineCursor(receipt.cursor.producer,receipt.cursor.incarnation,receipt.cursor.sequence)
            assert await client.publish(RecoveryFrame(cursor,payload))
            await wait_until(lambda: engines[0].stats['feeds'][item.producer]['applied_sequence']==cursor.sequence)
            return receipt
        yield SimpleNamespace(plan=plan,host=host,ledger=ledger,matcher=matcher,now=now,
            engine=engines[0],client=client,publish=publish,instrument=instrument,execution=execution,name=name,sid=sid)
    finally:
        if client is not None:await client.close()
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        if writer is not None:writer.close()
        assert ledger.report()['mode']=='paper'  # borrowed owner was not closed
        ledger.close()


@pytest.mark.asyncio
async def test_real_file_spool_uds_paper_fill_and_cold_read(tmp_path,monkeypatch,strategy_files):
    async with real_assembly(tmp_path,monkeypatch,strategy_files) as env:
        assert env.engine.hot_path is env.host.hot_path
        await env.publish(book(T,instrument=env.instrument).model_copy(update={'provider':'kiwoom'}))
        assert len(env.ledger.report()['orders'])==1
        assert len(loaded(env.name).OBSERVED)==1
        await env.publish(trade(T+11,side=Side.SELL,price=99,instrument=env.instrument).model_copy(
            update={'provider':'kiwoom','quantity':D(6)}))
        report=env.ledger.report();assert report['orders'][0]['state']=='FILLED'
        assert D(report['balances']['KRW'])==D('9900.901')
        assert D(report['reserved']['KRW'])==0
        assert env.ledger.position('@physical',env.execution.key)==D(1)
        before=tuple(env.ledger._db.iterdump())
        projection=await asyncio.to_thread(paper_projection,[env.ledger.path],limit=50)
        assert not projection['errors'] and projection['evidence']=='PAPER_SIMULATION'
        assert tuple(env.ledger._db.iterdump())==before
        assert env.host.report()['strategies'][env.sid]['callbacks']==1


def cli_environment(tmp_path,monkeypatch):
    path=tmp_path/'plan.json';path.write_text(json.dumps(document(tmp_path)))
    settings=replace(_controller(tmp_path).settings,env={},state_dir=tmp_path/'state')
    monkeypatch.setattr(local.RuntimeSettings,'from_env',lambda:settings)
    monkeypatch.setattr(local,'validate_runtime',lambda *args:None)  # CLI plumbing only
    return path,settings


def test_main_forwards_same_borrowed_owner_and_preserves_default_call(tmp_path,monkeypatch):
    path,settings=cli_environment(tmp_path,monkeypatch);calls=[]
    async def engine(plan,selected,**kwargs):calls.append((selected,kwargs))
    monkeypatch.setattr(local,'run_engine',engine)
    assert local.main(['engine','--config',str(path)])==0 and calls[-1]==(settings,{})
    ledger=ledger_at(tmp_path)
    try:
        host=runtime(ledger,matcher_for(ledger),[T]);deps={'kiwoom':(BTC.key,)}
        assert local.main(['engine','--config',str(path)],paper_runtime=host,paper_feed_dependencies=deps)==0
        assert calls[-1][1]['paper_runtime'] is host and calls[-1][1]['paper_feed_dependencies'] is deps
    finally:ledger.close()


@pytest.mark.parametrize('role',['telegram','dashboard'])
def test_snapshot_settings_are_given_to_existing_local_observers(tmp_path,monkeypatch,role):
    path,settings=cli_environment(tmp_path,monkeypatch);seen=[]
    if role=='telegram':
        import vibetrading.telegram as telegram
        class Controller:
            def __init__(self,selected,**kwargs):seen.append(dict(selected.env))
            async def run(self):pass
        monkeypatch.setattr(telegram,'TelegramController',Controller)
    else:
        import vibetrading.dashboard.bridge as bridge
        async def observer():seen.append(dict(os.environ))
        monkeypatch.setattr(bridge,'run',observer)
    assert local.main([role,'--config',str(path)])==0
    assert seen[0]['LOCAL_MARKET_SNAPSHOT_FILE']==str(settings.state_dir/'local-market.json')
    assert json.loads(seen[0]['LOCAL_SNAPSHOT_BUDGETS'])==document(tmp_path)['snapshot']
