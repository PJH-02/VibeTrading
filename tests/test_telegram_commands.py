import asyncio
import json
import time
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import aiohttp
import pytest

from portfolio.etf_journal import EtfJournal
from vibetrading.telegram_trade import trade_report
from execution.kis_execution import KisCashBroker
from test_telegram_status import _controller


def test_trade_rows_daily_and_total_never_double_count(tmp_path):
    bot = _controller(tmp_path)
    journal = EtfJournal(tmp_path/'state/etf-mock/journal.sqlite3')
    for day, net, fees in [('2026-10-13','100','2'), ('2026-10-14','-10','3')]:
        payload={'day':day,'net_pnl':net,'fees':fees,'adjusted_net_pnl':str(Decimal(net)-20),'adjusted_fees':str(Decimal(fees)+1)}
        journal.notify('daily:'+day, 'mock_daily', payload)
    journal.db.execute('INSERT INTO account_reports VALUES(?,?,?)', ('2026-10-14',json.dumps(payload),time.time_ns()))
    for symbol in ['122630','251340']:
        journal.db.execute('INSERT INTO assumed_fills VALUES(?,?,?,?,?,?,?,?,?,?)',
            (symbol,'KP',symbol,'buy',2,'100','1','2026-10-14',time.time_ns(),'permission'))
    text=trade_report(bot.settings,'2026-10-14'); lines=text.splitlines()
    assert lines[1].startswith('122630 ') and lines[2].startswith('251340 ')
    assert '가정 매수 2주' in lines[1]
    assert lines[-2].startswith('당일 순수익') and '-30.00원' in lines[-2] and '4.00원' in lines[-2]
    assert lines[-1].startswith('전체 기록기간') and '+50.00원' in lines[-1] and '7.00원' in lines[-1]
    journal.db.close()


def test_missing_valuation_never_reports_zero_profit(tmp_path):
    bot = _controller(tmp_path)
    assert '조회 자료 없음' in trade_report(bot.settings,'2026-10-14')
    assert not (tmp_path/'state/etf-mock/journal.sqlite3').exists()


@pytest.mark.asyncio
async def test_reply_failure_retries_before_advancing_update(tmp_path, monkeypatch):
    bot = _controller(tmp_path); handled=[]; offsets=[]
    async def api(session, method, params):
        offsets.append(params['offset'])
        if len(offsets)>1: raise asyncio.CancelledError()
        return {'result':[{'update_id':42}]}
    async def handle(session, update):
        handled.append(update)
        if len(handled)==1: raise aiohttp.ClientConnectionError('offline')
    async def sleep(delay): pass
    bot._api=api; bot._handle=handle
    monkeypatch.setattr('vibetrading.telegram.asyncio.sleep',sleep)
    with pytest.raises(asyncio.CancelledError): await bot._poll_loop(None,25)
    assert len(handled)==2 and offsets==[0,43]


@pytest.mark.asyncio
@pytest.mark.parametrize('text',['trade','/trade','/trade@MyBot',' TRADE '])
async def test_trade_command_aliases_and_allowlist(tmp_path,text):
    bot=_controller(tmp_path); sent=[]
    async def send(session,chat_id,text): sent.append(text)
    bot._send=send
    update={'message':{'from':{'id':123},'chat':{'id':123},'text':text}}
    await bot._handle(None,update)
    assert len(sent)==1 and sent[0].startswith('거래 내역')
    update['message']['from']['id']=456
    await bot._handle(None,update)
    assert len(sent)==1


@pytest.mark.asyncio
async def test_only_collector_errors_send_lifecycle_notification(tmp_path):
    bot=_controller(tmp_path); sent=[]; acked=[]
    async def send(session,chat_id,text): sent.append(text)
    async def ack(): acked.append(True)
    bot._send=send
    class Bus:
        async def subscribe(self,*args,**kwargs):
            for kind in ['collector_start','collector_stop','collector_error']:
                yield SimpleNamespace(subject='system.'+kind,data={'kind':kind,'provider':'kiwoom',
                    'reason':'market_closed','error':'transport failed','created_ts_ns':time.time_ns()},ack=ack)
    await bot._event_bridge_subject(None,Bus(),'system.>','test')
    assert len(acked)==3 and len(sent)==1 and 'COLLECTOR ERROR' in sent[0]


@pytest.mark.asyncio
async def test_polling_runs_even_when_nats_never_connects(tmp_path,monkeypatch):
    bot=_controller(tmp_path); reached=[]
    class Bus:
        def __init__(self,*args): pass
        async def connect(self,**kwargs):
            await asyncio.Event().wait()
        async def close(self): pass
    monkeypatch.setattr('vibetrading.telegram.NatsJetStreamBus',Bus)
    async def poll(*args): reached.append(True)
    async def idle(*args): await asyncio.Event().wait()
    bot._poll_loop=poll; bot._crypto_health_loop=idle; bot._mock_notifications_loop=idle
    task=asyncio.create_task(bot.run())
    await asyncio.sleep(.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert reached==[True]


@pytest.mark.asyncio
async def test_broker_fills_include_all_pages_without_unfilled_orders():
    broker=KisCashBroker.__new__(KisCashBroker); broker.mode='mock2'
    broker.account_params=lambda: {}
    calls=[]
    async def request(method, endpoint, tr_id, params, **kwargs):
        calls.append((tr_id,dict(params),kwargs))
        row={'odno':str(len(calls)), 'pdno':'122630','tot_ccld_qty':'2',
             'sll_buy_dvsn_cd':'02' if len(calls)==1 else '01','tot_ccld_amt':'200'}
        return {'body':{'output1':[row,dict(row,tot_ccld_qty='0')],'ctx_area_fk100':'next'},
            'headers':{'tr_cont':'M' if len(calls)==1 else ''}}
    broker.request=request
    rows=await broker.daily_fills('2026-10-14')
    assert len(rows)==2 and rows[0]['side']=='buy' and rows[1]['side']=='sell'
    assert all(c[0]=='VTTC0081R' for c in calls)
    assert calls[1][1]['CTX_AREA_FK100']=='next' and calls[1][2]=={'tr_cont':'N'}
    assert calls[0][1]['INQR_STRT_DT']=='20261014'


def test_broker_complete_trade_rows_do_not_duplicate_validation(tmp_path):
    bot=_controller(tmp_path); journal=EtfJournal(tmp_path/'state/etf-mock/journal.sqlite3')
    payload={'day':'2026-10-14','net_pnl':'-2','fees':'2','broker_fills_complete':True}
    journal.db.execute('INSERT INTO account_reports VALUES(?,?,?)',('2026-10-14',json.dumps(payload),time.time_ns()))
    journal.db.execute('INSERT INTO broker_trades VALUES(?,?,?,?,?,?,?)',('mock2','2026-10-14','1','122630','buy',2,'200'))
    (tmp_path/'state/etf-mock/mock2-validation.json').write_text(json.dumps({'created_ns':datetime(2026,10,14,tzinfo=ZoneInfo('Asia/Seoul')).timestamp()*1e9,
        'complete':True,'orders':[{'symbol':'122630','side':'buy','filled':1,'notional':'100'}]}),encoding='utf-8')
    result=trade_report(bot.settings,'2026-10-14')
    assert '실제 매수 2주' in result and '검증 매수' not in result
    journal.db.close()


def test_close_report_wins_over_nightly_zero_balance_reset(tmp_path):
    bot=_controller(tmp_path); journal=EtfJournal(tmp_path/'state/etf-mock/journal.sqlite3')
    closed={'day':'2026-10-14','net_pnl':'-135','fees':'40'}
    journal.notify('daily:2026-10-14','mock_daily',closed)
    reset=dict(closed,net_pnl='0',fees='0',broker_fills_complete=True)
    journal.db.execute('INSERT INTO account_reports VALUES(?,?,?)',('2026-10-14',json.dumps(reset),time.time_ns()))
    result=trade_report(bot.settings,'2026-10-14')
    assert '-135.00원' in result and '40.00원' in result
    assert '실제 +0원' not in result
    journal.db.close()


@pytest.mark.asyncio
async def test_account_refresh_preserves_close_without_reading_reset_balance(tmp_path):
    from vibetrading.etf_trading import EtfTradingLoop
    loop=EtfTradingLoop.__new__(EtfTradingLoop)
    loop.journal=EtfJournal(tmp_path/'journal.sqlite3')
    data={'day':'2026-10-14','net_pnl':'-135','fees':'40'}
    loop.journal.notify('daily:2026-10-14','mock_daily',data)
    async def account(): raise AssertionError('Must retain the confirmed close')
    loop.broker=SimpleNamespace(account_state=account)
    assert await loop.account_report('2026-10-14') == data
    loop.journal.db.close()
