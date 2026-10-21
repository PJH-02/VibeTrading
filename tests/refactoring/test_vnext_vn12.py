"""Delayed ACK and cumulative fill facts must never reopen confirmed terminal state."""
from decimal import Decimal

import pytest

from vibetrading.etf_journal import EtfJournal
from vibetrading.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue


@pytest.fixture
def journal(tmp_path):
    item = EtfJournal(tmp_path/'journal.sqlite3')
    item.initialize(Decimal(6000),'offline-account')
    value = OrderIntent(intent_id='one',job_id='C3',signal_id='signal',client_order_id='client-one',destination=OrderDestination.REAL,
        instrument=InstrumentId(venue=Venue.KIS,asset_class=AssetClass.ETF,raw_symbol='069500',std_symbol='069500'),
        side=Side.BUY,quantity=Decimal(10),limit_price=Decimal(100))
    item.stage(value,'20261021',1,None)
    yield item
    item.db.close()


def snapshot(journal):
    return dict(journal.db.execute('SELECT * FROM orders').fetchone()),journal.book('C3')


@pytest.mark.parametrize('filled,notional,remaining',[(4,'396',6),(10,'990',0)])
def test_fill_before_ack_preserves_accounting_and_completion(journal, filled, notional, remaining):
    journal.apply_status('one',{'tot_ccld_qty':str(filled),'tot_ccld_amt':notional,'rmn_qty':str(remaining)})
    before = journal.book('C3')
    journal.accepted('one',{'ODNO':'native-one','KRX_FWDG_ORD_ORGNO':'org'})
    row, book = snapshot(journal)
    assert book == before
    assert row['status'] == ('complete' if remaining == 0 else 'pending')
    assert book['cash'] == Decimal(2000)-Decimal(notional)*Decimal('1.00015')
    assert book['reserved'] == Decimal(remaining)*Decimal('100.5')
    assert book['positions'] == {'069500':filled}


def test_duplicate_ack_cannot_extend_replacement_deadline_or_erase_organization(journal, monkeypatch):
    monkeypatch.setattr('vibetrading.etf_journal.time.time_ns',lambda:100)
    journal.accepted('one',{'ODNO':'native-one','KRX_FWDG_ORD_ORGNO':'org'})
    monkeypatch.setattr('vibetrading.etf_journal.time.time_ns',lambda:999)
    journal.accepted('one',{'ODNO':'native-one'})
    row, _ = snapshot(journal)
    assert row['accepted_ns'] == 100 and row['organization'] == 'org'
    assert journal.db.execute("SELECT COUNT(*) FROM notifications WHERE kind='mock_order'").fetchone()[0] == 1


@pytest.mark.parametrize('ack',[{'ODNO':'different'},{'ODNO':'native-one','KRX_FWDG_ORD_ORGNO':'different'},
    {}, {'ODNO':True},{'ODNO':'   '},{'ODNO':'native-one','KRX_FWDG_ORD_ORGNO':' '}])
def test_conflicting_ack_rolls_back_without_rebinding_account_order(journal, ack):
    journal.accepted('one',{'ODNO':'native-one','KRX_FWDG_ORD_ORGNO':'org'})
    before = snapshot(journal)
    with pytest.raises(ValueError): journal.accepted('one',ack)
    assert snapshot(journal) == before


def test_cancel_fact_survives_late_ack_and_late_fill_applied_once(journal):
    journal.apply_status('one',{'tot_ccld_qty':'4','tot_ccld_amt':'396','rmn_qty':'6','cncl_yn':'Y'})
    journal.accepted('one',{'ODNO':'native-one'})
    stale = {'tot_ccld_qty':'4','tot_ccld_amt':'396','rmn_qty':'6'}
    journal.apply_status('one',stale)
    assert snapshot(journal)[0]['status'] == 'complete'
    assert journal.book('C3')['reserved'] == 0
    late = {'tot_ccld_qty':'5','tot_ccld_amt':'495','rmn_qty':'5'}
    journal.apply_status('one',late)
    before = snapshot(journal)
    journal.apply_status('one',late)
    assert snapshot(journal) == before
    assert before[0]['status'] == 'complete' and before[1]['reserved'] == 0
    assert before[1]['cash'] == Decimal('1504.92575')
    assert before[1]['positions'] == {'069500':5}


def test_cancel_request_ack_alone_never_releases_reservation(journal):
    journal.accepted('one',{'ODNO':'native-one'})
    journal.db.execute('UPDATE orders SET cancel_requested=1')
    journal.apply_status('one',{'tot_ccld_qty':'4','tot_ccld_amt':'396','rmn_qty':'6'})
    row, book = snapshot(journal)
    assert row['status'] == 'pending' and book['reserved'] == Decimal(603)


@pytest.mark.parametrize('filled,notional',[('3','396'),('11','1000'),('4','395')])
def test_terminal_fill_regressions_rollback_without_accounting_changes(journal, filled, notional):
    journal.apply_status('one',{'tot_ccld_qty':'4','tot_ccld_amt':'396','rmn_qty':'6','cncl_yn':'Y'})
    before = snapshot(journal)
    with pytest.raises(RuntimeError, match='inconsistent'):
        journal.apply_status('one',{'tot_ccld_qty':filled,'tot_ccld_amt':notional,'rmn_qty':'0'})
    assert snapshot(journal) == before
