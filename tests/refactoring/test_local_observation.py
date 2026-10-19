"""Local durable observations preserve existing wire bodies without NATS."""
import asyncio
import base64
from dataclasses import replace
import hashlib
import json

import pytest

from vibetrading.bus import dumps, subject_for_signal
from vibetrading.local_observation import LocalObservationBus, ObservationBudgets, encode_observation
from vibetrading.market_spool import MarketSpool, SpoolReader
from vibetrading.models import AssetClass, InstrumentId, Signal, SignalAction, Venue


LIMITS=dict(max_disk_bytes=131072,max_records=64,max_frame_bytes=8192,max_header_bytes=512,
    segment_max_bytes=16384,segment_max_records=8,segment_max_age_seconds=60)


def make(tmp_path,*,budgets=None):
    errors=[]
    bus=LocalObservationBus(producer='observations',incarnation='run',
        spool_factory=lambda:MarketSpool(tmp_path/'spool','observations','run',**LIMITS),
        budgets=budgets or ObservationBudgets(8,32768,4096,8,1),on_invalid=errors.append)
    return bus,errors


def records(tmp_path):
    reader=SpoolReader(tmp_path/'spool','observations','run',**{k:v for k,v in LIMITS.items() if not k.startswith('segment_')})
    return reader.read_batch(max_records=64,max_bytes=131072)


@pytest.mark.asyncio
async def test_exact_signal_order_and_system_body_durable_receipts_without_nats(tmp_path,monkeypatch):
    async def forbidden(*args,**kwargs):raise AssertionError('NATS must not be used')
    monkeypatch.setattr('nats.connect',forbidden)
    monkeypatch.setattr('vibetrading.local_observation.time.time_ns',lambda:123456789)
    bus,errors=make(tmp_path)
    signal=Signal(strategy_id='C3',instrument=InstrumentId(venue=Venue.KIWOOM,asset_class=AssetClass.ETF,
        raw_symbol='069500',std_symbol='069500'),action=SignalAction.ENTER_LONG,event_ts_ns=1,
        reference_price='123.000000000000000001',valid_until_ts_ns=2)
    order={'intent_id':'intent-1','nested':{'price':'123.000000000000000001'}}
    await bus.connect()
    try:
        admissions=[await bus.publish_signal(signal),await bus.publish_order_intent(order),
            await bus.publish_system('collector_error',{'provider':'kiwoom'})]
        order['nested']['price']='caller-mutated'
        receipts=[await bus.wait_durable(admission) for admission in admissions]
        assert [r.cursor.sequence for r in receipts]==[1,2,3]
        assert all(r.status=='DURABLE' for r in receipts)
    finally:await bus.close()
    envelopes=[json.loads(row.payload) for row in records(tmp_path)]
    assert [row['subject'] for row in envelopes]==[subject_for_signal(signal),'order.intent','system.collector_error']
    assert [row['msg_id'] for row in envelopes]==[signal.signal_id,'intent-1',None]
    assert base64.b64decode(envelopes[0]['payload_base64'])==dumps(signal)
    assert json.loads(base64.b64decode(envelopes[1]['payload_base64']))['nested']['price']=='123.000000000000000001'
    assert base64.b64decode(envelopes[2]['payload_base64'])==dumps({'event_type':'system','kind':'collector_error',
        'ts_ns':123456789,'provider':'kiwoom'})
    assert bus.stats['durable']==3 and bus.stats['pending']==0 and 'applied' not in bus.stats and not errors


@pytest.mark.asyncio
async def test_observation_restart_continues_sequence_without_republishing_old_records(tmp_path):
    first,_=make(tmp_path)
    await first.connect()
    admission=await first.publish_system('first',{})
    await first.wait_durable(admission)
    await first.close()
    second,_=make(tmp_path)
    await second.connect()
    try:
        next_admission=await second.publish_order_intent({'intent_id':''})
        assert next_admission.cursor.sequence==2
        await second.wait_durable(next_admission)
        with pytest.raises(ValueError,match='belong'):
            await second.wait_durable(admission)
    finally:await second.close()
    assert [row.cursor.sequence for row in records(tmp_path)]==[1,2]


@pytest.mark.parametrize('subject,payload,identity',[
    ('md.trade.BAD',b'{}','x'),('system.*',b'{}','x'),('system.ok',b'',None),
    ('order.intent',b'{}','bad\nheader'),('order.intent',bytearray(b'{}'),'id')])
def test_invalid_observation_envelope_rejected_before_ownership(subject,payload,identity):
    with pytest.raises(ValueError):encode_observation(subject,payload,identity,max_payload_bytes=32)


def test_oversized_raw_body_is_rejected_before_base64_allocation(monkeypatch):
    def forbidden(*args):raise AssertionError('must reject before encoding')
    monkeypatch.setattr(base64,'b64encode',forbidden)
    with pytest.raises(ValueError):encode_observation('order.intent',b'x'*33,'id',max_payload_bytes=32)


@pytest.mark.asyncio
async def test_full_observation_queue_fails_closed_and_does_not_claim_unconfirmed_receipt(tmp_path):
    bus,errors=make(tmp_path,budgets=ObservationBudgets(1,4096,2048,1,100))
    await bus.connect()
    first=await bus.publish_order_intent({'intent_id':'first'})
    with pytest.raises(RuntimeError,match='budget'):
        await bus.publish_order_intent({'intent_id':'second'})
    with pytest.raises(RuntimeError,match='unconfirmed'):
        await bus.wait_durable(first)
    with pytest.raises(RuntimeError):await bus.close()
    assert errors and bus.stats['durable']==0 and bus.stats['pending']==0


@pytest.mark.asyncio
async def test_forged_receipt_content_cannot_become_durable_proof(tmp_path):
    bus,_=make(tmp_path)
    await bus.connect()
    try:
        admission=await bus.publish_order_intent({'intent_id':'first'})
        await bus.wait_durable(admission)
        with pytest.raises(ValueError,match='content mismatch'):
            await bus.wait_durable(replace(admission,digest=hashlib.sha256(b'other').hexdigest()))
    finally:await bus.close()
