"""Cooperative authority on one protected shared Linux volume, offline only."""
from dataclasses import replace
import asyncio
import os
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from vibetrading.execution_ledger import AccountScope, ExecutionLedger
from vibetrading.process_lease import ProcessLease
from vibetrading.etf_trading import EtfDispatchBlocked, EtfTradingLoop


SCOPE = AccountScope('binance', '001-physical-parent', 'spot', 'offline')


def ledger(path, root, scope=SCOPE):
    return ExecutionLedger(path, scope, authority_root=root,
                           max_intents=8, max_evidence_bytes=4096, max_fills=8)


@pytest.mark.parametrize('product', ['spot', 'cash', 'cross-margin', 'linear-perpetual'])
def test_same_physical_parent_blocks_alternate_db_and_product(tmp_path, product):
    first = ledger(tmp_path / 'first.db', tmp_path / 'authority')
    try:
        with pytest.raises(RuntimeError, match='another process'):
            ledger(tmp_path / 'second.db', tmp_path / 'authority', replace(SCOPE, product=product))
        assert not (tmp_path / 'second.db').exists()
    finally:
        first.close()
    ledger(tmp_path / 'second.db', tmp_path / 'authority').close()


def test_distinct_accounts_and_environments_remain_independent(tmp_path):
    first = ledger(tmp_path / 'one.db', tmp_path / 'authority')
    second = ledger(tmp_path / 'two.db', tmp_path / 'authority', replace(SCOPE, account='002-physical-parent'))
    third = ledger(tmp_path / 'three.db', tmp_path / 'authority', replace(SCOPE, environment='paper'))
    for item in (third, second, first):
        item.close()


def test_same_db_still_fenced_and_partial_acquisition_released(tmp_path):
    root = tmp_path / 'authority'
    first = ledger(tmp_path / 'one.db', root)
    other = replace(SCOPE, account='002-physical-parent')
    try:
        with pytest.raises(RuntimeError, match='another process'):
            ledger(first.path, root, other)
        ledger(tmp_path / 'two.db', root, other).close()
    finally:
        first.close()


def test_constructor_failure_releases_authority_without_creating_db(tmp_path, monkeypatch):
    root = tmp_path / 'authority'
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', lambda *a, **k: (_ for _ in ()).throw(OSError('disk unavailable')))
        with pytest.raises(OSError, match='disk unavailable'):
            ledger(tmp_path / 'one.db', root)
    ledger(tmp_path / 'one.db', root).close()


@pytest.mark.parametrize('venue,account,environment', [
    ('BINANCE', '001', 'offline'), ('binance', ' 001', 'offline'),
    ('binance', '001\n', 'offline'), ('binance', '001', 'live'),
    ('kis', '001', 'mock2'),
])
def test_scope_aliases_rejected(tmp_path, venue, account, environment):
    with pytest.raises(ValueError):
        ProcessLease.account(tmp_path, venue, account, environment)


def test_root_and_lock_symlinks_and_fifo_rejected(tmp_path):
    root = tmp_path / 'authority'
    root.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match='Authority root'):
        ProcessLease.account(alias, 'kis', '001', 'mock')
    with pytest.raises(ValueError, match='Authority root'):
        ProcessLease.account('relative', 'kis', '001', 'mock')
    lease = ProcessLease.account(root, 'kis', '001', 'mock')
    target = root / 'target'
    target.touch()
    lease.path.symlink_to(target)
    with pytest.raises(OSError):
        lease.acquire()
    lease.path.unlink()
    os.mkfifo(lease.path)
    with pytest.raises(ValueError, match='regular'):
        lease.acquire()


def test_lost_authority_blocks_work_but_allows_cleanup(tmp_path):
    first = ledger(tmp_path / 'one.db', tmp_path / 'authority')
    first._authority.path.unlink()
    with pytest.raises(RuntimeError, match='identity was lost'):
        first.pragmas()
    first.close()


def test_lost_lease_cannot_rearm_by_putting_file_back(tmp_path):
    lease = ProcessLease.account(tmp_path,'kis','001','mock')
    lease.acquire()
    moved = tmp_path/'moved'
    lease.path.rename(moved)
    with pytest.raises(RuntimeError, match='identity was lost'): lease.check()
    moved.rename(lease.path)
    with pytest.raises(RuntimeError, match='identity was lost'): lease.check()
    lease.close()


def test_fork_cannot_claim_or_unlock_parent_authority(tmp_path):
    # Fork a fresh single-thread process, not pytest's active executor threads.
    script = """
import os,sys
from pathlib import Path
from vibetrading.process_lease import ProcessLease
assert getattr(sys,'_vibetrading_offline_guard',False)
lease=ProcessLease.account(Path(sys.argv[1]),'kis','001','mock');lease.acquire()
pid=os.fork()
if pid==0:
    try: lease.check()
    except RuntimeError: pass
    else: os._exit(1)
    lease.close();os._exit(0)
assert os.waitpid(pid,0)[1]==0
try: ProcessLease.account(Path(sys.argv[1]),'kis','001','mock').acquire()
except RuntimeError: pass
else: raise AssertionError('child unlocked parent lease')
lease.close()
"""
    subprocess.run([sys.executable,'-c',script,str(tmp_path)],check=True,timeout=10)


def test_authority_retained_until_backend_close_succeeds(tmp_path):
    first = ledger(tmp_path / 'one.db', tmp_path / 'authority')
    db = first._db
    class FailingClose:
        def close(self):
            raise OSError('close failed')
    first._db = FailingClose()
    with pytest.raises(OSError, match='close failed'):
        first.close()
    with pytest.raises(RuntimeError, match='another process'):
        ledger(tmp_path / 'two.db', tmp_path / 'authority')
    first._db = db
    first.close()
    ledger(tmp_path / 'two.db', tmp_path / 'authority').close()


@pytest.fixture
def etf_factory(tmp_path, monkeypatch):
    class Broker:
        def __init__(self, settings, *, mode):
            self.mode = mode
            self.account = settings.accounts[mode]
        def account_params(self):
            return {'CANO':self.account,'ACNT_PRDT_CD':'01'}
        async def connect(self):
            raise RuntimeError('offline startup failure')
    class Bus:
        async def close(self): pass
    monkeypatch.setattr('vibetrading.etf_trading.KisCashBroker',Broker)
    monkeypatch.setattr('vibetrading.etf_trading.KiwoomAuthClient',lambda *a,**k:None)
    monkeypatch.setattr('vibetrading.etf_trading.KisAuthClient',lambda *a,**k:None)
    owners = []
    def create(name='one', accounts=None, bus=None, root=None):
        settings = SimpleNamespace(state_dir=tmp_path/name,env={},
                                   accounts=accounts or {'mock':'001','mock2':'002'})
        item = EtfTradingLoop(settings=settings,bus=bus or Bus(),authority_root=root or tmp_path/'authority')
        owners.append(item)
        return item
    yield create
    for item in owners:
        item.journal.db.close()
        for lease in item._owner_leases.values(): lease.close()


def test_etf_same_account_route_alias_fails_before_journal(etf_factory, tmp_path):
    with pytest.raises(ValueError, match='distinct physical accounts'):
        etf_factory(accounts={'mock':'001','mock2':'001'})
    assert not (tmp_path/'one/etf-mock/journal.sqlite3').exists()
    etf_factory()


def test_etf_alternate_state_dir_cannot_duplicate_owner(etf_factory, tmp_path):
    etf_factory()
    with pytest.raises(RuntimeError, match='another process'):
        etf_factory('two')
    assert not (tmp_path/'two/etf-mock/journal.sqlite3').exists()


def test_etf_second_lease_failure_unwinds_first(etf_factory, tmp_path):
    leases = sorted([ProcessLease.account(tmp_path/'authority','kis',n,'mock') for n in ('001','002')],key=lambda x:str(x.path))
    leases[1].acquire()
    try:
        with pytest.raises(RuntimeError, match='another process'):
            etf_factory()
        leases[0].acquire()
        leases[0].close()
    finally:
        leases[1].close()
    etf_factory()


@pytest.mark.asyncio
async def test_etf_startup_failure_releases_both_accounts_after_cleanup(etf_factory):
    item = etf_factory()
    with pytest.raises(RuntimeError, match='offline startup failure'):
        await item.run()
    with pytest.raises(EtfDispatchBlocked, match='closed'):
        item._check_ownership()
    successor = etf_factory('two')
    await successor.close()


@pytest.mark.asyncio
async def test_etf_cancelled_close_retains_lease_until_bus_and_db_closed(etf_factory):
    started, finish = asyncio.Event(),asyncio.Event()
    class Bus:
        async def close(self):
            started.set()
            await finish.wait()
    item = etf_factory(bus=Bus())
    task = asyncio.create_task(item.close())
    await asyncio.wait_for(started.wait(),1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    with pytest.raises(RuntimeError, match='another process'): etf_factory('two')
    with pytest.raises(EtfDispatchBlocked, match='closed'): item._check_ownership()
    assert item.journal.db.execute('SELECT 1').fetchone()[0] == 1
    finish.set()
    await item.close()
    with pytest.raises(sqlite3.ProgrammingError): item.journal.db.execute('SELECT 1')
    await etf_factory('two').close()


@pytest.mark.asyncio
async def test_etf_close_waits_for_active_runner_and_background_cleanup(etf_factory):
    started, cleanup, finish = asyncio.Event(),asyncio.Event(),asyncio.Event()
    item = etf_factory()
    async def active(**kwargs):
        started.set()
        try: await asyncio.Event().wait()
        finally:
            cleanup.set()
            await finish.wait()
    item._run = active
    async def background(): await asyncio.Event().wait()
    item._warmup_task = asyncio.create_task(background())
    item._report_task = asyncio.create_task(background())
    running = asyncio.create_task(item.run(initialized=True))
    await asyncio.wait_for(started.wait(),1)
    closing = asyncio.create_task(item.close())
    await asyncio.wait_for(cleanup.wait(),1)
    with pytest.raises(RuntimeError, match='another process'): etf_factory('two')
    finish.set()
    await asyncio.wait_for(closing,1)
    with pytest.raises(asyncio.CancelledError): await running
    assert item._warmup_task.done() and item._report_task.done()
    await etf_factory('two').close()


def test_etf_changed_physical_account_permanently_fences_owner(etf_factory):
    item = etf_factory()
    item.brokers['mock'].account = '999'
    with pytest.raises(EtfDispatchBlocked, match='verification failed'): item._check_ownership()
    item.brokers['mock'].account = '001'
    with pytest.raises(EtfDispatchBlocked, match='closed'): item._check_ownership()


def test_etf_requires_explicit_shared_root(tmp_path):
    with pytest.raises(ValueError, match='EXECUTION_AUTHORITY_ROOT'):
        EtfTradingLoop(settings=SimpleNamespace(state_dir=tmp_path,env={}))
