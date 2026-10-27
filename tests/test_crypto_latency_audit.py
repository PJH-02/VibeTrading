import asyncio
import json
import runpy
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.measure_crypto_e2e import distribution, Audit, order_state
from runtime.bus import NatsJetStreamBus
from core.models import BaseMarketEvent, TradeEvent, Venue, Side
from feeds.providers.perp_common import _instrument


def test_latency_distribution_reports_count_and_interpolated_quantiles():
    result=distribution([30,10,20])
    assert result['n']==3 and result['p50_ms']==20 and result['p95_ms']==29
    assert distribution([])=={'n':0}


def test_grvt_nested_result_reconciliation_preserves_actual_filled_quantity():
    state={'status':'FILLED','traded_size':['0.002']}
    assert order_state({'result':{'state':state}})==state
    assert order_state({'order':{'state':state}})==state
    assert order_state({'state':state})==state


@pytest.mark.asyncio
async def test_raw_receive_is_carried_across_wait_for_without_changing_event(monkeypatch):
    import websockets
    original=(websockets.connect,json.loads,BaseMarketEvent.__init__,NatsJetStreamBus.publish_market_event)
    emitted=[]
    async def publish(self,event): pass
    async def side_channel(subject,payload): emitted.append((subject,original[1](payload)))
    monkeypatch.setenv('LATENCY_AUDIT_ID','test-run')
    NatsJetStreamBus.publish_market_event=publish
    try:
        scope=runpy.run_path(str(Path('scripts/latency_trace_sitecustomize.py')))
        raw='{"trade":1}'
        async def receive(): await asyncio.sleep(0); return raw
        socket=scope['TimedSocket'](SimpleNamespace(recv=receive))
        received=await asyncio.wait_for(socket.recv(),1)
        assert json.loads(received)=={'trade':1}
        event=TradeEvent(instrument=_instrument(Venue.GRVT,'BTCUSDT'),venue=Venue.GRVT,provider='grvt-perp',
            source_ts_ns=time.time_ns(),receive_monotonic_ns=time.monotonic_ns(),price=1,quantity=1,aggressor_side=Side.BUY)
        before=event.model_dump_json()
        await NatsJetStreamBus.publish_market_event(SimpleNamespace(_nc=SimpleNamespace(publish=side_channel)),event)
        assert event.model_dump_json()==before
        assert len(emitted)==1 and emitted[0][0]=='audit_latency.test-run.trace'
        assert emitted[0][1]['wire_receive_mono_ns']<=emitted[0][1]['normalized_mono_ns']
    finally:
        websockets.connect,json.loads,BaseMarketEvent.__init__,NatsJetStreamBus.publish_market_event=original


def test_summary_correlates_same_event_and_excludes_initial_snapshot(tmp_path):
    audit=Audit('run',tmp_path)
    common={'provider':'grvt-perp','symbol':'BTCUSDT','event_type':'order_book','venue':'grvt',
        'source_ts_ns':1_000_000_000,'wire_receive_wall_ns':1_010_000_000,
        'wire_receive_mono_ns':100_000_000,'normalized_mono_ns':101_000_000,'live_mono_ns':104_000_000,
        'intent_ready_mono_ns':104_500_000,'publish_enter_mono_ns':101_500_000,'publish_done_mono_ns':103_500_000}
    audit.rows={'new':dict(common),'initial':dict(common,snapshot=True),
        'untimed_book':dict(common,provider='arcus-perp',source_ts_ns=None)}
    result=audit.summary()
    group=result['pipeline']['grvt-perp']
    assert result['matched_sample_events']==2 and result['snapshots_excluded']==1
    assert group['receive_to_normalized_ms']['p50_ms']==1
    assert group['normalized_to_live_ms']['p50_ms']==3
    assert group['receive_to_intent_ms']['p50_ms']==4.5
    assert group['source_to_intent_unadjusted_ms']['p50_ms']==14.5
    assert result['pipeline']['arcus-perp']['receive_to_intent_ms']['n']==1
    assert 'source_to_intent_unadjusted_ms' not in result['pipeline']['arcus-perp']
