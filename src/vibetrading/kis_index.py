"""KIS KOSPI200 cash index snapshots and completed native minute candles."""
from __future__ import annotations
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .models import AssetClass, InstrumentId, Venue, Bar1mEvent
from .etf_csv_history import atomic_json
from .etf_intraday_store import merge_session_rows

KST = timezone(timedelta(hours=9))
log = logging.getLogger(__name__)


def normalize_index_minutes(rows, day, cutoff_ns, *, provider='KIS'):
    output = {}
    for row in rows:
        if provider == 'KIS':
            date = row['stck_bsop_date']; clock = row['stck_cntg_hour'].zfill(6)
            names = ('bstp_nmix_oprc','bstp_nmix_hgpr','bstp_nmix_lwpr','bstp_nmix_prpr','cntg_vol')
            scale = 1
        else:
            date, clock = row['cntr_tm'][:8],row['cntr_tm'][8:]
            names = ('open_pric','high_pric','low_pric','cur_prc','trde_qty'); scale = 100
        start = datetime.strptime(date+clock,'%Y%m%d%H%M%S').replace(tzinfo=KST)
        if start.strftime('%Y-%m-%d') != day or not '09:00' <= clock[:2]+':'+clock[2:4] < '15:30':
            continue
        end = start+timedelta(minutes=1)
        if int(end.timestamp()*1e9) > cutoff_ns:
            continue
        values = {k:abs(float(row[n]))/(scale if k!='volume' else 1)
                  for k,n in zip(('open','high','low','close','volume'),names)}
        output[str(end.replace(tzinfo=None))] = {
            'timestamp':str(end.replace(tzinfo=None)),'date':day,'symbol':'KOSPI200',
            'source':provider,'timestamp_semantics':'bar_end','trading_value':'',**values}
    return list(output.values())


async def index_snapshot(client):
    result = await client.get('/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice',
        tr_id='FHKUP03500200',params={'FID_COND_MRKT_DIV_CODE':'U','FID_ETC_CLS_CODE':'0',
        'FID_INPUT_ISCD':'2001','FID_INPUT_HOUR_1':'60','FID_PW_DATA_INCU_YN':'N'})
    return result['body']


def index_bar(row, *, received_ns=None):
    end = datetime.fromisoformat(row['timestamp']).replace(tzinfo=KST)
    end_ns = int(end.timestamp()*1e9)
    provider = row['source'].lower()
    return Bar1mEvent(instrument=InstrumentId(venue=Venue(provider),asset_class=AssetClass.INDEX,
        raw_symbol='2001' if provider=='kis' else '201',std_symbol='KOSPI200',exchange='KRX',currency='KRW'),
        provider=provider,venue=Venue(provider),source_ts_ns=end_ns,
        receive_ts_ns=received_ns or time.time_ns(),session='kr_cash_index',
        open_ts_ns=end_ns-60_000_000_000,close_ts_ns=end_ns,
        sequence='KOSPI200:'+str(end_ns),ingest_mode='backfill',
        **{k:Decimal(str(row[k])) for k in ('open','high','low','close','volume')})


class KisIndexFeed:
    def __init__(self, client, settings):
        self.client,self.settings = client,settings

    async def run(self,bus):
        failures = 0
        while True:
            now = datetime.now(KST)
            if now.weekday()<5 and '09:00'<=now.strftime('%H:%M')<='15:35':
                try:
                    body = await index_snapshot(self.client)
                    snapshot = {'day':str(now.date()),'received_ns':time.time_ns(),
                                'source':'KIS','symbol':'KOSPI200',**body.get('output1',{})}
                    path = self.settings.state_dir/'kis-index'/'snapshot.json'
                    path.parent.mkdir(parents=True,exist_ok=True)
                    await asyncio.to_thread(atomic_json,path,snapshot)
                    rows = normalize_index_minutes(body.get('output2',[]),str(now.date()),time.time_ns())
                    added = await asyncio.to_thread(merge_session_rows,
                        self.settings.state_dir/'etf-csv-sync'/'sessions',str(now.date()),'KOSPI200',rows)
                    for row in added:
                        await bus.publish_market_event(index_bar(row))
                    failures = 0
                except Exception:
                    failures += 1
                    log.exception('KIS cash-index minute collection failed')
                    if failures>=3:
                        raise
            await asyncio.sleep(max(1.,63-time.time()%60))
