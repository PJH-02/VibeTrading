"""Independent local owner/fencing probes; no real accounts or original data."""
from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
import selectors

import pytest

from vibetrading.execution_ledger import AccountScope, ExecutionLedger
from vibetrading.portfolio_ledger import PortfolioLedger
from vibetrading.process_lease import ProcessLease
from test_portfolio_ledger import ledger


def open_scope(root, scope, name):
    return PortfolioLedger(root / 'paper' / name, scope, owner_root=root, mode='paper',
        max_orders=30, max_fills=30, max_events=30, max_document_bytes=100_000,
        max_db_bytes=4_000_000, max_wal_bytes=8_000_000)


@pytest.mark.parametrize('product', ['other', 'cash', 'linear_perpetual'])
def test_alternate_product_and_db_cannot_create_second_writer(tmp_path, product):
    first = ledger(tmp_path)
    path = tmp_path / 'paper' / 'second.sqlite'
    try:
        bindings = sorted((tmp_path / '.portfolio-owners').glob('*.binding'))
        with pytest.raises(RuntimeError, match='another process'):
            other = open_scope(tmp_path, replace(first.scope, product=product), path.name)
            other.close()
        assert not path.exists()
        assert sorted((tmp_path / '.portfolio-owners').glob('*.binding')) == bindings
    finally:
        first.close()


@pytest.mark.parametrize('paper_first', [True, False])
def test_reuses_physical_key_with_existing_execution_ledger(tmp_path, paper_first):
    scope = AccountScope('binance', 'acct', 'another-product', 'paper')

    def execution():
        return ExecutionLedger(tmp_path / 'execution.sqlite', scope, authority_root=tmp_path,
            max_intents=8, max_evidence_bytes=4096, max_fills=8)

    first = ledger(tmp_path) if paper_first else execution()
    try:
        with pytest.raises(RuntimeError, match='another process'):
            other = execution() if paper_first else ledger(tmp_path)
            other.close()
    finally:
        first.close()


def test_other_accounts_and_environments_are_independent(tmp_path):
    first = ledger(tmp_path)
    second = open_scope(tmp_path, replace(first.scope, account='other'), 'other.sqlite')
    third = ExecutionLedger(tmp_path / 'offline.sqlite', replace(first.scope, environment='offline'),
        authority_root=tmp_path, max_intents=8, max_evidence_bytes=4096, max_fills=8)
    for item in [third, second, first]:
        item.close()


@pytest.mark.parametrize('mutation', ['move', 'close'])
def test_scope_lease_loss_blocks_reads_and_allows_cleanup(tmp_path, mutation):
    book = ledger(tmp_path)
    try:
        if mutation == 'move':
            moved = tmp_path / 'synthetic-held-inode'
            book._lease.path.rename(moved)
        else:
            book._lease.close()
        with pytest.raises(RuntimeError):
            book.account_version()
        if mutation == 'move':
            moved.rename(book._lease.path)
            with pytest.raises(RuntimeError):
                book.account_version()
    finally:
        book.close()


def test_physical_lease_loss_blocks_work_and_is_sticky(tmp_path):
    book = ledger(tmp_path)
    expected = ProcessLease.account(tmp_path, 'binance', 'acct', 'paper').path
    try:
        assert expected.is_file(), 'Missing shared physical account lease'
        moved = tmp_path / 'synthetic-physical-inode'
        expected.rename(moved)
        with pytest.raises(RuntimeError):
            book.seed_balance('USDT', Decimal(100), {'probe': True})
        moved.rename(expected)
        with pytest.raises(RuntimeError):
            book.account_version()
    finally:
        book.close()


@pytest.mark.parametrize('nested', [False, True])
def test_ownership_loss_during_transaction_never_commits(tmp_path, nested):
    book = ledger(tmp_path)
    before = tuple(book._db.iterdump())
    moved = tmp_path / 'synthetic-transaction-inode'
    try:
        with pytest.raises(RuntimeError):
            with book.transaction():
                book._db.execute("INSERT INTO portfolio_balances VALUES('OUTER','1')")
                if nested:
                    try:
                        with book.transaction():
                            book._db.execute("INSERT INTO portfolio_balances VALUES('INNER','2')")
                            book._lease.path.rename(moved)
                    except RuntimeError:
                        pass  # Swallowing inner failure must not permit outer commit.
                else:
                    book._lease.path.rename(moved)
        assert tuple(book._db.iterdump()) == before
    finally:
        if moved.exists():
            moved.rename(book._lease.path)
        book.close()


def test_failed_backend_close_keeps_physical_exclusion_until_retry(tmp_path):
    book = ledger(tmp_path)
    backend = book._db

    class BrokenClose:
        def close(self):
            raise OSError('synthetic close failure')

    book._db = BrokenClose()
    try:
        with pytest.raises(OSError, match='synthetic close'):
            book.close()
        with pytest.raises(RuntimeError, match='another process'):
            other = open_scope(tmp_path, replace(book.scope, product='alternate'), 'alternate.sqlite')
            other.close()
    finally:
        book._db = backend
        book.close()
    open_scope(tmp_path, replace(book.scope, product='alternate'), 'alternate.sqlite').close()


def test_scope_lock_acquisition_failure_releases_new_authority(tmp_path):
    old = ledger(tmp_path)
    lock = old._lease.path
    old.close()
    held = ProcessLease(lock)
    held.acquire()
    try:
        with pytest.raises(RuntimeError, match='another process'):
            ledger(tmp_path)
        with ProcessLease.account(tmp_path, 'binance', 'acct', 'paper'):
            pass
    finally:
        held.close()
    ledger(tmp_path).close()


def test_binding_restart_and_constructor_failure_release(tmp_path, monkeypatch):
    book = ledger(tmp_path)
    book.seed_balance('USDT', Decimal(123), {'source': 'review fixture'})
    binding = book._lease.path.with_suffix('.binding')
    raw = binding.read_bytes()
    scope = book.scope
    book.close()
    with pytest.raises(ValueError, match='bound'):
        open_scope(tmp_path, scope, 'relocated.sqlite')
    assert not (tmp_path / 'paper' / 'relocated.sqlite').exists()
    with ledger(tmp_path) as fresh:
        assert fresh.report()['balances']['USDT'] == '123'
        assert binding.read_bytes() == raw
    import sqlite3
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', lambda *a, **k: (_ for _ in ()).throw(OSError('connect failure')))
        with pytest.raises(OSError, match='connect failure'):
            ledger(tmp_path)
    with ProcessLease.account(tmp_path, 'binance', 'acct', 'paper'):
        pass
    ledger(tmp_path).close()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='POSIX fork probe')
def test_fork_inherited_ledger_cannot_use_or_close_parent(tmp_path):
    script = r'''
import os,sys
from pathlib import Path
from test_portfolio_ledger import ledger
assert getattr(sys,'_vibetrading_offline_guard',False)
root=Path(sys.argv[1]); book=ledger(root)
pid=os.fork()
if pid==0:
    blocked=[]
    for operation in (book.account_version,book.close):
        try: operation()
        except RuntimeError: blocked.append(True)
        else: blocked.append(False)
    os._exit(0 if all(blocked) else 42)
status=os.waitpid(pid,0)[1]
assert status==0, 'fork child used or closed inherited ledger'
book.account_version()
try: ledger(root)
except RuntimeError: pass
else: raise AssertionError('child released parent lease')
book.close(); ledger(root).close()
'''
    subprocess.run([sys.executable, '-c', script, str(tmp_path)], check=True, timeout=15)


def test_owned_process_crash_releases_fence_and_recovers_latest_reservation(tmp_path):
    script = r'''
import sys
from pathlib import Path
from decimal import Decimal
from test_portfolio_ledger import ledger,admit
assert getattr(sys,'_vibetrading_offline_guard',False)
book=ledger(Path(sys.argv[1]))
book.seed_balance('USDT',Decimal(1000),{'source':'synthetic crash fixture'})
admit(book)
print('READY',flush=True)
sys.stdin.read()
'''
    process = subprocess.Popen([sys.executable, '-c', script, str(tmp_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(process.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=8), 'synthetic owner failed to reach ready state'
        assert process.stdout.readline().strip() == 'READY'
        with pytest.raises(RuntimeError, match='another process'):
            competitor = open_scope(tmp_path,
                AccountScope('binance', 'acct', 'alternate', 'paper'), 'competing.sqlite')
            competitor.close()
    finally:
        if process.poll() is None:
            process.kill()  # Only this test's own synthetic child.
        process.communicate(timeout=5)
    with ledger(tmp_path) as recovered:
        row = recovered.get('one')
        assert row['state'] == 'UNKNOWN'
        assert Decimal(row['reservation']) == Decimal('100.200')
        assert recovered.report()['balances']['USDT'] == '1000'
