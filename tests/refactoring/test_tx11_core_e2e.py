"""Codex critical combined local gate. UDS role coverage is VN17/VN18;
this test restores an exact-cut direct engine, not graceful-role checkpointing.
"""
import asyncio
from decimal import Decimal as D
import json

import pytest
from test_strategy_runtime import T, BTC, book, trade, ledger_at, matcher_for, runtime
from test_vn13_file_registration import strategy_files
from test_tx08_same_account_gate import strategy_source, document_for, actions_for
from test_tx05_file_paper_e2e import live_engine, restore_engine
from test_runtime_checkpoint_integration import producer, append, codec
from test_tx08_same_account_gate import spooled_frame
from test_tx10_manifest_registration import export_row, export_manifest, descriptor, RELATIVE
from test_tx09_paper_queries import paper_bot, query_update
from operations.dashboard.projection import paper_projection
from core.models import Side
from runtime.runtime_checkpoint import RuntimeCheckpoint


@pytest.mark.asyncio
async def test_two_real_files_manifest_paper_exact_cut_restart_and_cold_outbox(tmp_path,strategy_files):
    definitions=(('tx11_alpha','ENTER_LONG','99',Side.BUY),
                 ('tx11_beta','ENTER_SHORT','101',Side.SELL))
    names={}
    for sid,action,price,side in definitions:
        source=strategy_source(sid,action,price).replace(
            '        return [Signal(', '        if self.state["count"] != 1: return []\n        return [Signal(')
        names[sid]=strategy_files(sid+'_file',source)
    export=tmp_path/'export';row=export_row(export,RELATIVE,b'owned export object','tx11-data')
    manifest=export_manifest(export,[row]);proof=descriptor(manifest)
    now,clock=[T],[0];item=producer(tmp_path);ledger=ledger_at(tmp_path);engine=None
    def register(host):
        for sid,action,price,side in definitions:
            doc=document_for(ledger,names[sid],sid+'-job',price,sid)
            doc['config']['quantity']='1'
            doc['research_receipt']['data_manifest']=proof
            host.register_configured(doc,action_builder=actions_for(ledger,sid,sid+'-job',side))
            job=host._jobs[sid]
            assert job.registration.module.module.__file__==str(tmp_path/(names[sid]+'.py'))
            assert dict(job.registration.research_receipt['data_manifest'])==proof
    def deliver(event):
        now[0]=event.receive_ts_ns;clock[0]+=1
        payload=event.model_dump_json().encode();receipt=append(item,payload)
        frame=spooled_frame(receipt,payload);engine.on_frame(frame)
        return frame
    def economics():
        report=ledger.report()
        assert len(report['orders'])==2 and all(r['state']=='FILLED' for r in report['orders'])
        assert matcher.report()['fills']==2
        assert D(report['balances']['USDT'])==D('100018')
        assert D(report['reserved']['USDT'])==0 and D(report['simulated_margin'])==200
        assert ledger.position('@physical',BTC.key)==0
        assert ledger.position('tx11_alpha',BTC.key)==1
        assert ledger.position('tx11_beta',BTC.key)==-1
        cash={r['strategy']:r for r in report['strategy_cash']}
        assert D(cash['tx11_alpha']['fees'])==D('.99')
        assert D(cash['tx11_beta']['fees'])==D('1.01')
        assert D(cash['@physical_netting_adjustment']['realized'])==20
        return report
    try:
        ledger.seed_balance('USDT',D(100000),{'source':'tx11 owned synthetic'})
        matcher=matcher_for(ledger);host=runtime(ledger,matcher,now);register(host)
        engine=live_engine(item,host,now,clock)
        deliver(book())
        assert len(ledger.report()['orders'])==2
        deliver(trade(T+11,side=Side.SELL,price=99).model_copy(update={'quantity':D(6)}))
        old=trade(T+12,side=Side.BUY,price=101).model_copy(update={'quantity':D(6)})
        deliver(old);economics()
        assert host.hot_path.apply_seq==3
        checkpoint=RuntimeCheckpoint(tmp_path/'runtime.json',codec(engine),max_bytes=262144,
            max_journal_rows=10000,max_journal_bytes=4_000_000)
        await checkpoint.save(engine,paper_runtime=host)
        await engine.close();engine=None;ledger.close()
        ledger=ledger_at(tmp_path);matcher=matcher_for(ledger);host=runtime(ledger,matcher,now);register(host)
        assert all(host.report()['strategies'][sid]['state']=='BLOCKED' for sid in names)
        engine=restore_engine(item,host,now,clock);seen=[]
        def readiness(current,snapshot,receipts):
            assert current.post_apply_callbacks==() and host.recovery_state=='RECOVERING'
            assert snapshot.phase=='REPLAY' and snapshot.cursor==(current.hot_path.engine_incarnation,3)
            assert all(host._jobs[sid].instance.state['count']==1 for sid in names)
            assert all(host.report()['strategies'][sid]['callbacks']==1 for sid in names)
            seen.append(True);return True
        await checkpoint.restore(engine,(item,),paper_runtime=host,readiness=readiness)
        assert seen==[True] and checkpoint.state=='READY' and host.recovery_state=='READY'
        before=economics();matcher_before=matcher.report()
        frame=deliver(old);assert frame.cursor.sequence==4
        assert host.hot_path.apply_seq==3 and ledger.report()==before and matcher.report()==matcher_before
        fresh=deliver(book(T+20,sequence=2));assert fresh.cursor.sequence==5
        assert host.hot_path.apply_seq==4
        assert all(host._jobs[sid].instance.state['count']==2 for sid in names)
        assert all(host.report()['strategies'][sid]['callbacks']==2 for sid in names)
        assert all(host.report()['strategies'][sid]['actions']==1 for sid in names)
        before=economics();engine.on_frame(fresh)
        assert ledger.report()==before and host.hot_path.apply_seq==4
        dump=tuple(ledger._db.iterdump())
        projected=await asyncio.to_thread(paper_projection,[ledger.path],limit=100)
        assert projected['evidence']=='PAPER_SIMULATION' and projected['freshness']=='UNVERIFIED'
        positions={(r['category'],r['strategy']):D(r['quantity']) for r in projected['positions']}
        assert positions['physical','@physical']==0
        assert positions['virtual','tx11_alpha']==1 and positions['virtual','tx11_beta']==-1
        pnl={r['strategy']:r for r in projected['pnl']}
        assert D(pnl['@physical_netting_adjustment']['realized_pnl'])==20
        for sid in names:
            assert pnl[sid]['currency']=='USDT' and D(pnl[sid]['funding'])==0
            assert pnl[sid]['mark'] is None and pnl[sid]['unrealized_pnl_status']=='UNAVAILABLE'
        for index,command in enumerate(('pnl','positions')):
            bot_root=tmp_path/('bot-'+str(index));bot_root.mkdir()
            bot=paper_bot(bot_root,ledger.path);bot._outbox_enabled=True
            update=query_update('/'+command,update_id=index+1);bot._state.receive(update)
            await bot._handle_pending_update(None,update)
            reply=json.loads(bot._state.next_message()['text'])
            assert bot._state.pending()==[] and reply['evidence']=='PAPER_SIMULATION'
            assert reply['freshness']=='UNVERIFIED' and reply[command]==projected[command]
        assert tuple(ledger._db.iterdump())==dump
    finally:
        if engine is not None:await engine.close()
        ledger.close()
