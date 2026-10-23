"""Pre-open Kiwoom warmup and confirmed US/index inputs for frozen ETF rules."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

import aiohttp
import numpy as np
import pandas as pd

from .etf_reference import make_features, rolling_z1
from .etf_csv_history import CsvStrategyHistory, ETF_HISTORY_ROLES
from .strategies.kr.c3 import open_decision
from .providers.kiwoom import KiwoomAuthClient, KST, _abs_decimal


def native_frame(rows: list[dict], symbol: str, *, minute: bool = False, index: bool = False) -> pd.DataFrame:
    out = []
    for row in rows:
        timestamp = pd.to_datetime(row['cntr_tm'] if minute else row['dt'], format='%Y%m%d%H%M%S' if minute else '%Y%m%d')
        if minute:
            timestamp += pd.Timedelta(minutes=1)
        scale = 100 if index else 1
        values = {name: float(_abs_decimal(row[key])) / scale for name, key in
                  [('open','open_pric'), ('high','high_pric'), ('low','low_pric'), ('close','cur_prc')]}
        out.append({'timestamp': timestamp, 'date': timestamp.strftime('%Y-%m-%d'), 'symbol': symbol,
                    'source': 'KIWOOM', **values, 'volume': float(_abs_decimal(row.get('trde_qty', 0))), 'trading_value': np.nan})
    return pd.DataFrame(out).sort_values('timestamp').drop_duplicates('timestamp') if out else pd.DataFrame()


class EtfHistory:
    def __init__(self, client: KiwoomAuthClient, root: Path):
        self.client, self.root = client, root
        self.daily: dict[str, pd.DataFrame] = {}
        self.minutes: dict[str, pd.DataFrame] = {}
        self.index = pd.DataFrame()
        self.us = pd.Series(dtype=float)
        self.reference_usage = {}
        self.day = ''
        self.root.mkdir(parents=True, exist_ok=True)

    async def prepare(self, day: str):
        """Load the shared ticker CSVs; live events supply only today's bars."""
        reference = await asyncio.to_thread(CsvStrategyHistory,self.root.parent/'history')
        daily, minutes = {}, {}
        for symbol,(daily_key,minute_key) in ETF_HISTORY_ROLES.items():
            daily[symbol] = await asyncio.to_thread(reference.daily,daily_key,symbol)
            prior = daily[symbol][daily[symbol].date < day]
            if len(prior)<22:
                raise RuntimeError(f'{symbol}: fewer than 22 prior sessions')
            selected = set(prior.date.tail(15))
            minutes[symbol] = await asyncio.to_thread(reference.minute,minute_key,selected)
            if minutes[symbol].empty or minutes[symbol].date.nunique()<14:
                raise RuntimeError(f'{symbol}: minute warmup has fewer than 14 prior sessions')
            self.reference_usage[symbol] = {'daily_rows':len(daily[symbol]),'minute_rows':len(minutes[symbol]),
                'daily_path':reference.manifest['files'][daily_key]['path'],
                'minute_path':reference.manifest['files'][minute_key]['path']}
        self.daily,self.minutes,self.day = daily,minutes,day
        # C3 and KP use this same ticker dataset.
        self.index = await asyncio.to_thread(reference.daily,'KOSPI200_daily','KOSPI200')
        us = await asyncio.to_thread(reference.daily,'SP500_daily','SP500')
        self.us = us.set_index('timestamp').close.dropna().sort_index()
        self.reference_usage['C3'] = {'shared_etf_path':reference.manifest['files']['069500_daily']['path'],
            'shared_leverage_path':reference.manifest['files']['122630_daily']['path'],
            'index_path':reference.manifest['files']['KOSPI200_daily']['path'],
            'sp500_path':reference.manifest['files']['SP500_daily']['path']}
        if len(self.index)<252 or len(self.us)<252:
            raise RuntimeError('C3 index/SP500 history incomplete')
        self.save()

    def save(self):
        for symbol in self.daily:
            self.daily[symbol].to_json(self.root / f'{symbol}-daily.json', orient='table', date_format='iso')
            self.minutes[symbol].to_json(self.root / f'{symbol}-minute.json', orient='table', date_format='iso')
        (self.root / 'warmup.json').write_text(json.dumps({'day':self.day, 'prepared_ns':time.time_ns(),
            'sessions':{s:int(x.date.nunique()) for s,x in self.minutes.items()}, 'c3_ready':not self.index.empty,
            'reference_usage':self.reference_usage,'history_route':'shared ticker CSV + live today; append after exchange close'}))

    def intraday_features(self, symbol: str, today: pd.DataFrame) -> pd.DataFrame:
        if symbol not in {'069500','229200'}:
            raise ValueError('KP/KD signals must use the unleveraged underlying ETF')
        if self.day == '' or today.empty:
            return pd.DataFrame()
        traded = today[today.volume > 0]
        if traded.empty:
            return pd.DataFrame()
        daily = self.daily[symbol][self.daily[symbol].date < self.day].copy()
        one = {'date':self.day, 'timestamp':pd.Timestamp(self.day), 'symbol':symbol, 'source':'KIWOOM',
            'open':traded.open.iloc[0], 'high':today.high.max(), 'low':today.low.min(),
            'close':today.close.iloc[-1], 'volume':today.volume.sum(), 'trading_value':np.nan}
        # At least the actual first 09:00 minute is required for the day open.
        if today.timestamp.iloc[0].strftime('%H:%M') != '09:01':
            return pd.DataFrame()
        daily = pd.concat([daily, pd.DataFrame([one])], ignore_index=True)
        minute = pd.concat([self.minutes[symbol], today], ignore_index=True)
        minute['time'] = minute.timestamp.dt.strftime('%H:%M')
        return make_features(minute, daily).query('date == @self.day')

    def vehicle_reference(self, symbol: str) -> dict:
        """Causal vehicle price context for order audit, never an assumed fill."""
        daily = self.daily.get(symbol, pd.DataFrame())
        minute = self.minutes.get(symbol, pd.DataFrame())
        if not self.day or daily.empty or minute.empty:
            raise RuntimeError(f'Execution-vehicle history missing for {symbol}')
        prior = daily[daily.date < self.day].sort_values('timestamp')
        prior_minute = minute[minute.date < self.day].sort_values('timestamp')
        if prior.empty or prior_minute.empty:
            raise RuntimeError(f'Prior execution-vehicle prices missing for {symbol}')
        daily_row, minute_row = prior.iloc[-1], prior_minute.iloc[-1]
        return {'symbol':symbol,'daily_date':daily_row.date,'previous_close':float(daily_row.close),
            'minute_timestamp':str(minute_row.timestamp),'minute_close':float(minute_row.close),
            'daily_provider':daily_row.source,'minute_provider':minute_row.source}

    def c3(self, etf_open: float, index_open: float) -> tuple[float, dict]:
        if self.index.empty or self.us.empty:
            raise RuntimeError('C3 index/SPX data is unavailable')
        etf = self.daily['069500'].set_index('timestamp').sort_index()
        ix = self.index.set_index('timestamp').sort_index().reindex(etf.index)
        keep = etf.index < pd.Timestamp(self.day)
        etf, ix = etf.loc[keep].tail(252), ix.loc[keep].tail(252)
        calendar = etf.index.append(pd.DatetimeIndex([pd.Timestamp(self.day)]))
        # An incomplete US tail cannot be replaced with a stale last price.
        from .etf_csv_sync import latest_completed_session
        latest = self.us.index.max()
        instant = pd.Timestamp(self.day,tz='Asia/Seoul') + pd.Timedelta(hours=9)
        required = latest_completed_session('XNYS',instant.to_pydatetime(),grace_minutes=10)
        if latest < pd.Timestamp(required):
            raise RuntimeError('Latest completed SPX session is missing')
        pos = np.searchsorted(self.us.index.values, calendar.values, side='left') - 1
        if (pos < 0).any():
            raise RuntimeError('SPX warmup does not cover index calendar')
        us_close = pd.Series(np.log(self.us.to_numpy()[pos]), index=calendar)
        us = us_close.diff()
        opens = pd.concat([ix.open, pd.Series([index_open], index=calendar[-1:])])
        closes = ix.close.reindex(calendar)
        g = np.log(opens / closes.shift(1))
        z = float(rolling_z1(g, us, 250, 200).iloc[-1])
        ratios = np.log(etf.close / ix.close).tail(5)
        if len(ratios) != 5 or ratios.isna().any():
            raise RuntimeError('C3 discount history incomplete')
        mis = float(np.log(etf_open / index_open) - ratios.median())
        sigma = float(ix.close.pct_change(fill_method=None).tail(20).std(ddof=1) * np.sqrt(252))
        return open_decision(z, mis, sigma)
