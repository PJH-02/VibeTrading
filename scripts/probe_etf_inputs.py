"""Check Kiwoom warmup independently of KIS mock account readiness."""
import asyncio
import json
from datetime import datetime
from pathlib import Path

from strategies.kr.etf_history import EtfHistory
from storage.etf_csv_history import ETF_HISTORY_ROLES
from feeds.providers.kiwoom import KiwoomAuthClient, KST
from vibetrading.settings import get_settings


async def main():
    settings = get_settings()
    client = KiwoomAuthClient(settings)
    history = EtfHistory(client,settings.state_dir/'etf-mock/warmup')
    day = datetime.now(KST).strftime('%Y-%m-%d')
    await history.prepare(day)
    result = {'day':day,'daily_rows':{s:len(d) for s,d in history.daily.items()},
        'minute_sessions':{s:d.date.nunique() for s,d in history.minutes.items()},
        'index_rows':len(history.index),'spx_rows':len(history.us),'reference_usage':history.reference_usage,
        'vehicle_history':{s:history.vehicle_reference(s) for s in ETF_HISTORY_ROLES}}
    # Exercise features with native current-day minute history after market
    # close. This read-only probe never publishes signals or submits orders.
    for symbol in ETF_HISTORY_ROLES:
        data = await client.post_api('/api/dostk/chart','ka10080',{'stk_cd':symbol,'tic_scope':'1','upd_stkpc_tp':'1','base_dt':day.replace('-','')})
        from strategies.kr.etf_history import native_frame
        today = native_frame(data.get('stk_min_pole_chart_qry') or [],symbol,minute=True)
        today = today[today.date.eq(day)]
        today = today[today.timestamp.dt.strftime('%H:%M').between('09:01','15:20')]
        if not today.empty:
            feature = history.intraday_features(symbol,today) if symbol in {'069500','229200'} else None
            result[symbol] = {'today_minute_rows':len(today),'latest_vehicle_close':float(today.close.iloc[-1]),
                'signal_etf':symbol in {'069500','229200'}}
            if feature is not None:
                result[symbol].update(decision_rows=len(feature),usable=int(feature.usable.sum()) if not feature.empty else 0)
    if not history.index.empty:
        index = await client.post_api('/api/dostk/sect','ka20001',{'mrkt_tp':'0','inds_cd':'201'})
        daily = await client.post_api('/api/dostk/chart','ka10081',{'stk_cd':'069500','base_dt':day.replace('-',''),'upd_stkpc_tp':'1'})
        row = next(r for r in daily['stk_dt_pole_chart_qry'] if r['dt']==day.replace('-',''))
        exposure, detail = history.c3(abs(float(row['open_pric'])),abs(float(index['open_pric'])))
        result['c3_observation'] = {'exposure':exposure,**detail}
    print(json.dumps(result,default=int),flush=True)
    (settings.state_dir/'etf-mock/input-probe.json').write_text(json.dumps(result,default=int))


asyncio.run(main())
