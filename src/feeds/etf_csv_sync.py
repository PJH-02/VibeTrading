"""Close-only ticker CSV updates, independent of the mock trading process."""
import argparse
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import nats
import pandas as pd

from storage.etf_csv_history import CsvStrategyHistory, ETF_HISTORY_ROLES, atomic_json
from vibetrading.etf_history import native_frame
from vibetrading.health import touch
from core.models import EventType, event_from_dict
from .providers.kiwoom import KiwoomAuthClient, KST
from vibetrading.session_alerts import _calendar
from vibetrading.settings import get_settings

log = logging.getLogger(__name__)


def completed_session(name, day, now, grace_minutes=10):
    calendar = _calendar(name)
    return calendar.is_session(day) and calendar.session_close(day)+pd.Timedelta(minutes=grace_minutes)<=pd.Timestamp(now)


def latest_completed_session(name, now, grace_minutes=10):
    calendar = _calendar(name)
    current = pd.Timestamp(now).tz_convert('UTC')
    days = calendar.sessions_in_range(str((current-pd.Timedelta(days=14)).date()),str(current.date()))
    finished = [day for day in days if calendar.session_close(day)+pd.Timedelta(minutes=grace_minutes)<=current]
    if not finished: raise RuntimeError('No completed exchange session found')
    return str(finished[-1].date())


def completed_sp500_rows(result, now):
    """Yahoo can include today's moving candle; calendar close gates each row."""
    dates = pd.to_datetime(result['timestamp'],unit='s',utc=True).tz_convert('America/New_York')
    quote = result['indicators']['quote'][0]
    rows = []
    for i,dt in enumerate(dates):
        day = str(dt.date())
        if not completed_session('XNYS',day,now): continue
        values = {key:quote.get(key,[None]*len(dates))[i] for key in ('open','high','low','close','volume')}
        if any(v is None for v in values.values()): continue
        if not all(pd.notna(v) for v in values.values()): continue
        rows.append({'date':day,**values})
    return rows


def session_minute_rows(rows, symbol, day):
    """Native start labels -> completed end labels; keep auction at 15:30."""
    output = {}
    for row in sorted(rows,key=lambda r:r['cntr_tm']):
        stamp = pd.to_datetime(row['cntr_tm'],format='%Y%m%d%H%M%S')
        if str(stamp.date())!=day or not '09:00'<=stamp.strftime('%H:%M')<='15:30': continue
        auction = stamp.strftime('%H:%M')=='15:30'
        end = stamp if auction else stamp+pd.Timedelta(minutes=1)
        if end.strftime('%H:%M')>'15:30': continue
        values = {key:abs(float(row[native])) for key,native in
            [('open','open_pric'),('high','high_pric'),('low','low_pric'),('close','cur_prc'),('volume','trde_qty')]}
        if min(values[k] for k in ('open','high','low','close'))<=0: continue
        output[str(end)] = {'timestamp':str(end),'date':day,'symbol':symbol,'source':'KIWOOM',
            'timestamp_semantics':'bar_end','trading_value':'',**values}
    return list(output.values())


class EtfCsvSync:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.root = self.settings.state_dir/'etf-mock'/'history'
        self.client = KiwoomAuthClient(self.settings)
        self.store = CsvStrategyHistory(self.root)
        self.spool = self.settings.state_dir/'etf-csv-sync'/'sessions'
        self.spool.mkdir(parents=True,exist_ok=True)
        self.last_attempt = 0.
        self.result = {}
        self.error = None

    def capture(self, data):
        event = event_from_dict(data)
        symbol = event.instrument.raw_symbol
        if event.event_type!=EventType.BAR_1M or symbol not in ETF_HISTORY_ROLES: return
        stamp = pd.Timestamp(event.close_ts_ns,unit='ns',tz='UTC').tz_convert('Asia/Seoul').tz_localize(None)
        row = {'timestamp':str(stamp),'date':event.trading_date,'symbol':symbol,'source':event.provider.upper(),
            'timestamp_semantics':'bar_end','trading_value':'',
            **{k:float(getattr(event,k)) for k in ('open','high','low','close','volume')}}
        from storage.etf_intraday_store import merge_session_rows
        merge_session_rows(self.spool,event.trading_date,symbol,[row],replace=True)

    def captured(self, symbol, day):
        path = self.spool/(day+'-'+symbol+'.jsonl')
        output = {}
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                try: row = json.loads(line)
                except ValueError: continue  # REST close snapshot repairs a torn final line.
                if row['date']==day: output[row['timestamp']]=row
        return output

    async def sync_once(self, now=None):
        instant = now or datetime.now(timezone.utc)
        domestic = latest_completed_session('XKRX',instant,grace_minutes=5)
        us_day = latest_completed_session('XNYS',instant)
        receipts = []
        # Use manifest watermarks. No intraday CSV writes or repeated full-history loads.
        for symbol,(daily_key,minute_key) in ETF_HISTORY_ROLES.items():
            watermark = self.store.manifest['files'][minute_key]['last_date']
            if watermark<domestic:
                rows,key = [],''
                for _ in range(100):
                    page,key = await self.client.post_api_page('/api/dostk/chart','ka10080',
                        {'stk_cd':symbol,'tic_scope':'1','upd_stkpc_tp':'0','base_dt':domestic.replace('-','')},next_key=key)
                    found = page.get('stk_min_pole_chart_qry') or []
                    rows.extend(found)
                    if not key or not found or min(r['cntr_tm'][:8] for r in found)<=watermark.replace('-',''): break
                    await asyncio.sleep(.3)
                dates = sorted({str(pd.to_datetime(r['cntr_tm'],format='%Y%m%d%H%M%S').date()) for r in rows})
                expected = [str(d.date()) for d in _calendar('XKRX').sessions_in_range(watermark,domestic) if str(d.date())>watermark]
                if not set(expected)<=set(dates): raise RuntimeError(f'{symbol}: native minute recovery missing sessions')
                for day in expected:
                    combined = self.captured(symbol,day)
                    combined.update({r['timestamp']:r for r in session_minute_rows(rows,symbol,day)})
                    values = [r for r in combined.values() if '09:01'<=pd.Timestamp(r['timestamp']).strftime('%H:%M')<='15:30']
                    if not values: raise RuntimeError(f'{symbol}: no closed session bars')
                    receipts.append(await asyncio.to_thread(self.store.append_closed,minute_key,values))
                await asyncio.sleep(.3)
            if self.store.manifest['files'][daily_key]['last_date']<domestic:
                data = await self.client.post_api('/api/dostk/chart','ka10081',
                    {'stk_cd':symbol,'base_dt':domestic.replace('-',''),'upd_stkpc_tp':'0'})
                daily = native_frame(data.get('stk_dt_pole_chart_qry') or [],symbol)
                daily = daily[daily.date<=domestic]
                if domestic not in set(daily.date): raise RuntimeError(f'{symbol}: completed daily candle missing')
                values = daily.to_dict('records')
                for r in values:
                    r['timestamp']=r['date']; r['trading_value']=''
                receipts.append(await asyncio.to_thread(self.store.append_closed,daily_key,values))
                await asyncio.sleep(.3)
        if self.store.manifest['files']['KOSPI200_daily']['last_date']<domestic:
            data = await self.client.post_api('/api/dostk/chart','ka20006',{'inds_cd':'201','base_dt':domestic.replace('-','')})
            index = native_frame(data.get('inds_dt_pole_qry') or [],'KOSPI200',index=True)
            index = index[index.date<=domestic]
            if domestic not in set(index.date): raise RuntimeError('Completed KOSPI200 daily candle missing')
            receipts.append(await asyncio.to_thread(self.store.append_closed,'KOSPI200_daily',index.to_dict('records')))
        if self.store.manifest['files']['SP500_daily']['last_date']<us_day:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.get('https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC',
                    params={'range':'2y','interval':'1d'},headers={'User-Agent':'Mozilla/5.0'}) as response:
                    response.raise_for_status()
                    result = (await response.json())['chart']['result'][0]
            rows = completed_sp500_rows(result,instant)
            if us_day not in {r['date'] for r in rows}: raise RuntimeError('Latest completed SP500 daily candle missing')
            receipts.append(await asyncio.to_thread(self.store.append_closed,'SP500_daily',rows))
        self.result = {'checked_ns':time.time_ns(),'domestic_completed_session':domestic,'us_completed_session':us_day,
            'receipts':receipts,'files':{k:r['last_date'] for k,r in self.store.manifest['files'].items()}}
        atomic_json(self.settings.state_dir/'etf-csv-sync'/'last-sync.json',self.result)
        return self.result

    async def run(self):
        connection = await nats.connect(self.settings.nats.url)
        async def receive(msg):
            await asyncio.to_thread(self.capture,json.loads(msg.data))
        subscription = await connection.subscribe('live.md.bar_1m.*.etf.>',cb=receive)
        try:
            while True:
                if time.monotonic()-self.last_attempt>=60:
                    try:
                        await self.sync_once()
                        self.error = None
                    except Exception as exc:
                        self.error = str(exc)
                        log.exception('Closed-session CSV synchronization failed; retrying')
                    self.last_attempt=time.monotonic()
                touch(self.settings,'etf-csv-sync',state='degraded' if self.error else 'running',detail={'phase':'error' if self.error else 'waiting_close',
                    'last_sync':self.result,'error':self.error})
                await asyncio.sleep(10)
        finally:
            await subscription.unsubscribe()
            await connection.close()


async def main_async(command):
    service = EtfCsvSync()
    if command=='sync': print(json.dumps(await service.sync_once()))
    else: await service.run()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['sync','run'])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async(args.command))


if __name__=='__main__': main()
