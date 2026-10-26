import os
import pytest

from feeds import universe_config as universe
from vibetrading.health import touch
from test_telegram_status import _controller


def test_unchanged_universe_parsed_once_and_caller_mutations_isolated(tmp_path, monkeypatch):
    path = tmp_path / 'universe.yaml'
    path.write_text('instruments:\n- symbol: "069500"\n  providers: [kiwoom]\n')
    original = universe.yaml.safe_load
    calls = []
    def parse(text):
        calls.append(True)
        return original(text)
    monkeypatch.setattr(universe.yaml, 'safe_load', parse)
    for _ in range(100):
        result = universe.load_universe_config(path)
        result['instruments'][0]['symbol'] = 'corrupted'
    assert universe.configured_instrument('kiwoom', '069500', path)['symbol'] == '069500'
    assert len(calls) == 1


def test_universe_edit_atomic_replace_and_missing_file_are_visible(tmp_path):
    path = tmp_path / 'universe.yaml'
    assert universe.load_universe_config(path) == {'instruments': []}
    path.write_text('instruments: [{symbol: "069500", providers: [kiwoom]}]')
    assert universe.configured_instrument('kiwoom', '069500', path)
    path.write_text('instruments: [{symbol: "229200", providers: [kiwoom]}]')
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns+1_000_000))
    assert universe.configured_instrument('kiwoom', '229200', path)
    replacement = tmp_path / 'replacement.yaml'
    replacement.write_text('instruments: [{symbol: "251340", providers: [kiwoom]}]')
    replacement.replace(path)
    assert universe.configured_instrument('kiwoom', '251340', path)
    assert universe.configured_instrument('kiwoom', '069500', path) is None


@pytest.mark.asyncio
async def test_delayed_source_is_distinguished_from_disconnected_transport(tmp_path):
    import time
    bot = _controller(tmp_path)
    touch(bot.settings, 'collectors-kis', detail={
        'phase':'publishing', 'last_event_ts_ns':time.time_ns(),
        'symbol_last_seen_ns':{'A01612':time.time_ns()-240*10**9}})
    sent = []
    async def send(session, chat, text): sent.append(text)
    bot._send = send
    await bot._market_health_once(None, {'kis':True})
    assert len(sent) == 1 and '시세 지연' in sent[0] and '수신 중단' not in sent[0]


@pytest.mark.asyncio
async def test_repeated_rest_page_skips_old_and_accepted_rows_before_normalization(monkeypatch):
    import asyncio
    from datetime import datetime
    from types import SimpleNamespace
    from feeds.providers import kiwoom
    from core.models import AssetClass
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 15, 10, 45, tzinfo=tz)
    monkeypatch.setattr(kiwoom, 'datetime', Clock)
    collector = kiwoom.KiwoomCollector.__new__(kiwoom.KiwoomCollector)
    collector.subscriptions = [kiwoom.KiwoomSubscription('069500', 'KRX')]
    collector._bar_cursor = {'069500':int(Clock(2026,10,15,9,tzinfo=kiwoom.KST).timestamp()*1e9)}
    collector.instrument = lambda sub:SimpleNamespace(asset_class=AssetClass.ETF)
    rows = [{'cntr_tm':label} for label in ['20261015090100','20261015090000','20261014090100']]
    async def page(*args, **kwargs): return {'stk_min_pole_chart_qry':rows}
    collector.client = SimpleNamespace(post_api=page)
    normalized = []
    def normalize(sub, row):
        normalized.append(row['cntr_tm'])
        return None
    collector.bar_event_from_rest = normalize
    async def stop(*args): raise asyncio.CancelledError()
    monkeypatch.setattr(kiwoom.asyncio, 'sleep', stop)
    with pytest.raises(asyncio.CancelledError):
        await collector._poll_closed_bars(None)
    assert normalized == ['20261015090100']
