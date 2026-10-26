"""Ticker sharing, exchange close gates and recoverable CSV commits."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from test_etf_execution_update import make_references
from vibetrading.etf_csv_history import CsvStrategyHistory, CSV_FILES, digest_file
from feeds.etf_csv_sync import completed_sp500_rows, latest_completed_session, session_minute_rows, EtfCsvSync
from vibetrading.etf_history import EtfHistory


def candle(day, price=102):
    return {'timestamp':day+' 09:01:00','date':day,'symbol':'069500','source':'KIWOOM',
        'open':price,'high':price+1,'low':price-1,'close':price,'volume':10,'timestamp_semantics':'bar_end'}


def test_close_gates_exclude_us_live_candle_and_support_early_close():
    date = pd.Timestamp('2026-10-13 09:30',tz='America/New_York')
    result = {'timestamp':[int(date.timestamp())],'indicators':{'quote':[{k:[100] for k in ['open','high','low','close','volume']}]}}
    assert completed_sp500_rows(result,datetime(2026,10,13,19,59,tzinfo=timezone.utc))==[]
    assert completed_sp500_rows(result,datetime(2026,10,13,20,9,tzinfo=timezone.utc))==[]
    assert completed_sp500_rows(result,datetime(2026,10,13,20,10,tzinfo=timezone.utc))[0]['date']=='2026-10-13'
    # Black Friday closes at 13:00 ET (18:00 UTC), even after DST has ended.
    date = pd.Timestamp('2026-11-27 09:30',tz='America/New_York')
    result['timestamp'] = [int(date.timestamp())]
    assert completed_sp500_rows(result,datetime(2026,11,27,18,9,tzinfo=timezone.utc))==[]
    assert completed_sp500_rows(result,datetime(2026,11,27,18,10,tzinfo=timezone.utc))
    assert latest_completed_session('XKRX',pd.Timestamp('2026-10-13 15:34',tz='Asia/Seoul'),5)=='2026-10-12'
    assert latest_completed_session('XKRX',pd.Timestamp('2026-10-13 15:35',tz='Asia/Seoul'),5)=='2026-10-13'


def test_csv_append_preserves_history_and_is_idempotent(tmp_path):
    reference = make_references(tmp_path/'history')
    path = reference.root/CSV_FILES['069500_minute']
    original = path.read_bytes()
    result = reference.append_closed('069500_minute',[candle('2026-09-30')])
    assert result['appended_rows']==1 and path.read_bytes().startswith(original)
    assert reference.append_closed('069500_minute',[candle('2026-09-30',999)])['appended_rows']==0
    reloaded = CsvStrategyHistory(reference.root)
    assert reloaded.minute('069500_minute',{'2026-09-30'}).close.iloc[0]==102


def test_csv_recovers_crash_between_file_and_manifest_replace(tmp_path,monkeypatch):
    import vibetrading.etf_csv_history as module
    reference = make_references(tmp_path/'history')
    normal = module.atomic_json
    def fail_manifest(path,data):
        if path.name=='manifest.json': raise RuntimeError('simulated crash')
        return normal(path,data)
    monkeypatch.setattr(module,'atomic_json',fail_manifest)
    with pytest.raises(RuntimeError,match='simulated crash'):
        reference.append_closed('069500_minute',[candle('2026-09-30')])
    monkeypatch.setattr(module,'atomic_json',normal)
    reloaded = CsvStrategyHistory(reference.root)
    assert reloaded.manifest['files']['069500_minute']['last_date']=='2026-09-30'
    assert reloaded.append_closed('069500_minute',[candle('2026-09-30')])['appended_rows']==0


def test_ticker_reader_accepts_numeric_ticker_and_mixed_clock_format(tmp_path):
    reference = make_references(tmp_path/'history')
    path = reference.root/CSV_FILES['069500_minute']
    path.write_text(path.read_text().replace('09:01:00','9:01').replace(',069500,',',69500,'))
    manifest = json.loads((reference.root/'manifest.json').read_text())
    manifest['files']['069500_minute']['sha256'] = digest_file(path)
    (reference.root/'manifest.json').write_text(json.dumps(manifest))
    loaded = CsvStrategyHistory(reference.root).minute('069500_minute',{'2026-09-29'})
    assert loaded.symbol.iloc[0]=='069500' and loaded.timestamp.iloc[0].hour==9


def test_native_minute_labels_and_closing_auction_are_not_shifted(tmp_path):
    rows = [{'cntr_tm':'20261013090000','open_pric':'100','high_pric':'101','low_pric':'99','cur_prc':'100','trde_qty':'1'},
        {'cntr_tm':'20261013152900','open_pric':'100','high_pric':'100','low_pric':'100','cur_prc':'100','trde_qty':'0'},
        {'cntr_tm':'20261013153000','open_pric':'102','high_pric':'102','low_pric':'102','cur_prc':'102','trde_qty':'5'}]
    parsed = session_minute_rows(rows,'069500','2026-10-13')
    assert parsed[0]['timestamp']=='2026-10-13 09:01:00'
    assert len(parsed)==2 and parsed[-1]['timestamp']=='2026-10-13 15:30:00' and parsed[-1]['close']==102


@pytest.mark.asyncio
async def test_warmup_loads_shared_ticker_files_without_rest_or_nats_history(tmp_path):
    root = tmp_path/'history'
    reference = make_references(root)
    dates = pd.bdate_range('2025-09-01','2026-10-13')
    manifest = reference.manifest
    for key,relative in CSV_FILES.items():
        symbol = key.rsplit('_',1)[0]
        if key.endswith('_daily'):
            data = pd.DataFrame({'date':dates.strftime('%Y-%m-%d'),'open':100.,'high':102.,'low':99.,
                'close':100.+(pd.Series(range(len(dates)))%7)/10,'volume':10})
            if symbol=='SP500': data=data[data.date<'2026-10-13']
        else:
            stamps = [dt+pd.Timedelta(hours=9,minutes=i) for dt in dates[-17:] for i in range(1,31)]
            data = pd.DataFrame({'timestamp':stamps,'date':[str(dt.date()) for dt in stamps],
                'open':100.,'high':102.,'low':99.,'close':101.,'volume':10,'symbol':symbol,
                'source':'TOSS','timestamp_semantics':'bar_end'})
        data.to_csv(root/relative,index=False)
        manifest['files'][key].update(sha256=digest_file(root/relative),last_date=data.date.iloc[-1],rows=len(data))
    (root/'manifest.json').write_text(json.dumps(manifest))
    class NoRest:
        async def post_api(self,*args,**kwargs): raise AssertionError('CSV warmup must not query REST')
    history = EtfHistory(NoRest(),tmp_path/'warmup')
    await history.prepare('2026-10-13')
    assert history.reference_usage['C3']['shared_etf_path']==history.reference_usage['069500']['daily_path']
    assert history.reference_usage['C3']['shared_leverage_path']==history.reference_usage['122630']['daily_path']
    assert set(history.daily)=={'069500','122630','229200','251340'}
    assert len(history.minutes['122630'].date.unique())==15


@pytest.mark.asyncio
async def test_no_closed_session_write_or_provider_query_when_csv_is_current(tmp_path):
    reference = make_references(tmp_path/'history')
    for row in reference.manifest['files'].values(): row['last_date']='2026-10-13'
    reference.manifest['files']['SP500_daily']['last_date']='2026-10-12'
    service = EtfCsvSync.__new__(EtfCsvSync)
    service.store=reference
    service.settings=SimpleNamespace(state_dir=tmp_path)
    (tmp_path/'etf-csv-sync').mkdir()
    class NoRest:
        async def post_api(self,*args,**kwargs): raise AssertionError('Already-current dataset should not be queried')
    service.client=NoRest()
    result=await service.sync_once(datetime(2026,10,13,14,tzinfo=timezone.utc))
    assert result['us_completed_session']=='2026-10-12' and result['receipts']==[]
