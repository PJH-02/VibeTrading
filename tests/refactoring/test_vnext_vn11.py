"""Immutable ETF command/evidence and commit barriers, isolated journals only."""
import hashlib
import json
from decimal import Decimal

import pytest

from portfolio.etf_journal import EtfJournal
from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue


def command(**changes):
    value = OrderIntent(intent_id='immutable-one',job_id='C3',signal_id='decision',
        destination=OrderDestination.REAL,client_order_id='client-one',
        instrument=InstrumentId(venue=Venue.KIS,asset_class=AssetClass.ETF,raw_symbol='069500',std_symbol='069500'),
        side=Side.BUY,quantity=Decimal(2),limit_price=Decimal(110),
        metadata={'account_scope':'mock','source_provider':'kiwoom','direction':1,'safety_stop':None,
            'valid_until_ts_ns':1000,'intent_quote':{'bid':'100','ask':'105','received_ns':1,'intent_ns':2}})
    return value.model_copy(update=changes)


def evidence(value):
    return {'contract':'etf-dispatch-v1','intent_sha256':hashlib.sha256(value.model_dump_json().encode()).hexdigest(),
        'account_scope':value.metadata['account_scope'],'strategy':value.job_id,'signal_id':value.signal_id,
        'valid_until_ts_ns':value.metadata['valid_until_ts_ns'],'quote':value.metadata['intent_quote'],
        'source_provider':value.metadata['source_provider']}


@pytest.fixture
def journal(tmp_path):
    item = EtfJournal(tmp_path/'journal.sqlite3')
    item.initialize(Decimal(3000),'offline-physical-account')
    yield item
    item.db.close()


def stage(journal, value, proof=None):
    return journal.stage(value,'20261021',1,None,dispatch_evidence=proof,require_evidence=True)


def test_guarded_stage_requires_evidence_before_any_reservation(journal):
    with pytest.raises(ValueError, match='requires dispatch evidence'): stage(journal,command())
    assert journal.db.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0
    assert journal.book('C3')['reserved'] == 0


@pytest.mark.parametrize('change',[
    {'intent_sha256':'0'*64},{'strategy':'KP'},{'account_scope':'mock2'},
    {'signal_id':'other'},{'valid_until_ts_ns':True},{'source_provider':'other'},
    {'unexpected':'field'},{'quote':{'bid':'99','ask':'105','received_ns':1,'intent_ns':2}},
])
def test_evidence_cannot_diverge_from_exact_intent(journal, change):
    value = command()
    with pytest.raises(ValueError): stage(journal,value,evidence(value)|change)
    assert journal.book('C3')['reserved'] == 0


@pytest.mark.parametrize('quote',[
    {'bid':'NaN','ask':'105','received_ns':1,'intent_ns':2},
    {'bid':'bad','ask':'105','received_ns':1,'intent_ns':2},
    {'bid':'106','ask':'105','received_ns':1,'intent_ns':2},
    {'bid':'100','ask':'105','received_ns':True,'intent_ns':2},
    {'bid':'100','ask':'105','received_ns':1,'intent_ns':2,'extra':0},
    {'bid':'1'*17000,'ask':'2'*17000,'received_ns':1,'intent_ns':2},
])
def test_even_self_consistent_evidence_requires_valid_bounded_quote(journal, quote):
    original = command()
    value = original.model_copy(update={'metadata':original.metadata|{'intent_quote':quote}})
    with pytest.raises(ValueError): stage(journal,value,evidence(value))
    assert journal.db.execute('SELECT COUNT(*) FROM dispatch_attempts').fetchone()[0] == 0


def test_exact_retry_preserves_fill_and_reserve_but_changed_payload_collides(journal):
    value = command()
    proof = evidence(value)
    assert stage(journal,value,proof)
    assert journal.book('C3')['reserved'] == Decimal('221.10')
    journal.accepted(value.intent_id,{'ODNO':'offline-order'})
    journal.apply_status(value.intent_id,{'tot_ccld_qty':'1','tot_ccld_amt':'100','rmn_qty':'1'})
    before = dict(journal.db.execute('SELECT * FROM orders').fetchone()),journal.book('C3')
    assert stage(journal,value,proof) is False
    changed = value.model_copy(update={'quantity':Decimal(3)})
    with pytest.raises(ValueError, match='collision'): stage(journal,changed,evidence(changed))
    assert (dict(journal.db.execute('SELECT * FROM orders').fetchone()),journal.book('C3')) == before


def test_persisted_evidence_cannot_be_silently_replaced(journal):
    value = command()
    proof = evidence(value)
    stage(journal,value,proof)
    journal.db.execute('UPDATE dispatch_attempts SET evidence=?',(json.dumps(proof|{'strategy':'KP'}),))
    with pytest.raises(ValueError, match='collision'): stage(journal,value,proof)
    assert journal.book('C3')['reserved'] == Decimal('221.10')
