import asyncio
import json
import time
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from vibetrading.bus import NatsJetStreamBus, NatsPublishError
from portfolio.etf_journal import EtfJournal
from vibetrading.etf_trading import EtfTradingLoop
from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, TradeEvent, Venue
from feeds.providers.universe import InstrumentUniverseResolver
from vibetrading.runtime import _CollectorReadinessBus
from vibetrading.settings import RuntimeSettings
from vibetrading.telegram import TelegramController


def settings(tmp_path):
    path = tmp_path/'.env'
    path.write_text('VIBETRADING_DATA_ROOT='+tmp_path.as_posix(),encoding='utf-8')
    return replace(RuntimeSettings.from_env(path), data_root=tmp_path, state_dir=tmp_path/'state',
        telegram_user_id='123',env={'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_USER_ID':'123'})


def test_universe_refresh_does_not_open_shared_database(tmp_path,monkeypatch):
    def blocked(*args,**kwargs):
        raise AssertionError('Collector attempted shared SQLite write')
    monkeypatch.setattr("feeds.providers.universe.RuntimeStore",blocked)
    resolver = InstrumentUniverseResolver(settings=settings(tmp_path))
    resolver._persist('kis_futures',{'front':'101V12'})
    data = json.loads((tmp_path/'state/universe_cache/kis_futures.json').read_text())
    assert data['items']=={'front':'101V12'}


@pytest.mark.asyncio
@pytest.mark.parametrize('subject,message_id,failures,calls',[
    ('md.kiwoom.etf.trade','same-event',1,2),
    ('md.toss.equity.trade','same-event',9,3),
    ('crypto.binance.trade','same-event',1,2),
    ('order.intent.created','same-event',1,1),
    ('md.kiwoom.etf.trade','',1,1)])
async def test_only_identified_market_events_retry_with_same_id(tmp_path,subject,message_id,failures,calls):
    attempts=[]
    class JS:
        async def publish(self,*args,**kwargs):
            attempts.append((args,kwargs))
            if len(attempts)<=failures: raise asyncio.TimeoutError()
    bus=NatsJetStreamBus(settings(tmp_path)); bus._js=JS()
    if failures>=calls:
        with pytest.raises(NatsPublishError): await bus._publish(subject,b'fixed',message_id)
    else:
        await bus._publish(subject,b'fixed',message_id)
    assert len(attempts)==calls
    assert all(a==attempts[0] for a in attempts)


@pytest.mark.asyncio
async def test_domestic_status_requires_successful_fresh_publish(tmp_path):
    state=[False]
    async def publish(event):
        if state[0]: raise RuntimeError('publish failed')
    inner=SimpleNamespace(settings=settings(tmp_path),publish_market_event=publish)
    bus=_CollectorReadinessBus(inner,None,'kiwoom')
    instrument=InstrumentId(venue=Venue.KIWOOM,asset_class=AssetClass.ETF,raw_symbol='069500',std_symbol='KIWOOM:069500')
    event=TradeEvent(instrument=instrument,venue=Venue.KIWOOM,provider='kiwoom',
        source_ts_ns=time.time_ns(),price=Decimal(100),quantity=Decimal(1),aggressor_side=Side.BUY)
    await bus.publish_market_event(event)
    assert set(bus.symbol_last_seen_ns)=={'069500'}
    await bus.publish_market_event(event.model_copy(update={'instrument':instrument.model_copy(update={'raw_symbol':'122630'}),
        'source_ts_ns':time.time_ns()-180_000_000_000}))
    assert '122630' not in bus.symbol_last_seen_ns
    state[0]=True
    with pytest.raises(RuntimeError): await bus.publish_market_event(event)
    assert bus.published_count==2


def staged_journal(tmp_path):
    journal=EtfJournal(tmp_path/'state/etf-mock/journal.sqlite3')
    journal.initialize(Decimal(6000),'mock'); journal.roll_day('2026-10-14')
    order=OrderIntent(intent_id='L1',client_order_id='L1',job_id='KP',signal_id='S1',destination=OrderDestination.REAL,
        instrument=InstrumentId(venue=Venue.KIS,asset_class=AssetClass.ETF,raw_symbol='069500',std_symbol='069500'),
        side=Side.BUY,quantity=Decimal(10),limit_price=Decimal(100))
    journal.stage(order,'2026-10-14',1,None)
    return journal


def test_acceptance_and_incremental_fills_have_separate_durable_notifications(tmp_path):
    journal=staged_journal(tmp_path)
    assert journal.db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0]==0
    journal.accepted('L1',{'ODNO':'123'})
    assert journal.book('KP')['positions']=={}
    partial={'tot_ccld_qty':'4','tot_ccld_amt':'396','rmn_qty':'6'}
    journal.apply_status('L1',partial); journal.apply_status('L1',partial)
    journal.apply_status('L1',{'tot_ccld_qty':'10','tot_ccld_amt':'1002','rmn_qty':'0'})
    rows=list(journal.db.execute('SELECT kind,payload FROM notifications ORDER BY created_ns'))
    assert [r[0] for r in rows]==['mock_order','mock_fill','mock_fill']
    assert [json.loads(r[1])['filled_quantity'] for r in rows[1:]]==[4,6]
    assert json.loads(rows[-1][1])['fill_price']=='101'
    journal.db.close()


def test_broker_rejection_notifies_without_booking_a_fill(tmp_path):
    journal=staged_journal(tmp_path)
    journal.reject('L1','40070000: 매매불가 종목')
    row=journal.db.execute('SELECT kind,payload FROM notifications').fetchone()
    assert row[0]=='mock_rejected'
    assert '주문 거절' in TelegramController._format_mock_notification(row[0],json.loads(row[1]))
    assert journal.book('KP')['positions']=={} and journal.book('KP')['reserved']==0
    journal.db.close()


@pytest.mark.asyncio
async def test_delivery_failure_keeps_notification_and_success_is_not_repeated(tmp_path):
    journal=staged_journal(tmp_path); journal.accepted('L1',{'ODNO':'123'})
    bot=TelegramController(settings(tmp_path)); messages=[]
    async def fail(*args): raise RuntimeError('transport unavailable')
    bot._send=fail
    with pytest.raises(RuntimeError): await bot._deliver_mock_notifications(None)
    assert len(bot._pending_mock_notifications())==1
    async def send(session,chat_id,message): messages.append((chat_id,message))
    bot._send=send
    await bot._deliver_mock_notifications(None); await bot._deliver_mock_notifications(None)
    assert len(messages)==1 and messages[0][0]=='123'
    assert '모의 주문 접수' in messages[0][1] and '체결' not in messages[0][1]
    journal.db.close()


def test_restart_restores_today_opening_minutes_without_replaying_signals(tmp_path):
    loop=EtfTradingLoop.__new__(EtfTradingLoop); loop.settings=settings(tmp_path)
    loop.today={'069500':{}}
    root=loop.settings.state_dir/'etf-csv-sync/sessions'; root.mkdir(parents=True)
    rows=[{'timestamp':'2026-10-14 '+clock,'date':'2026-10-14','close':100} for clock in ['09:01:00','09:30:00','15:30:00']]
    (root/'2026-10-14-069500.jsonl').write_text('\n'.join(json.dumps(r) for r in rows)+'\ninvalid')
    loop.restore_today('2026-10-14')
    assert [str(r['timestamp']) for r in loop.today['069500'].values()]==['2026-10-14 09:01:00','2026-10-14 09:30:00']


@pytest.mark.asyncio
async def test_close_report_uses_broker_costs_and_is_once_per_trading_day(tmp_path,monkeypatch):
    loop=EtfTradingLoop.__new__(EtfTradingLoop); loop.journal=staged_journal(tmp_path)
    calls=[]; closed=[False]
    monkeypatch.setattr("feeds.etf_csv_sync.completed_session",lambda *args:closed[0])
    async def account():
        calls.append(True)
        return {'summary':{'tot_evlu_amt':'6100','bfdy_tot_asst_evlu_amt':'6000','thdt_tlex_amt':'23'},'positions':[]}
    loop.broker=SimpleNamespace(account_state=account)
    now=datetime(2026,10,14,15,35,tzinfo=ZoneInfo('Asia/Seoul'))
    await loop.report_daily('2026-10-14',now)
    assert calls==[]
    closed[0]=True
    await loop.report_daily('2026-10-14',now); await loop.report_daily('2026-10-14',now)
    assert len(calls)==1
    data=json.loads(loop.journal.db.execute("SELECT payload FROM notifications WHERE kind='mock_daily'").fetchone()[0])
    assert data['net_pnl']=='100' and data['fees']=='23'
    formatted=TelegramController._format_mock_notification('mock_daily',data)
    assert '+100원' in formatted and '23원' in formatted
    loop.journal.db.close()
