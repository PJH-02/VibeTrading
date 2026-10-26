import os
import subprocess
import sys

import pytest

from runtime.settings import RuntimeSettings
from storage.sqlite_store import RuntimeStore


def settings(tmp_path, extra=''):
    env=tmp_path/'.env'
    env.write_text(f'VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n'+extra,encoding='utf-8')
    return RuntimeSettings.from_env(env)


def test_default_market_budget_and_explicit_crypto_budget(tmp_path):
    default=RuntimeStore(settings(tmp_path))
    assert default._conn.execute('PRAGMA cache_size').fetchone()[0]==-2000
    assert default._conn.execute('PRAGMA wal_autocheckpoint').fetchone()[0]==1000
    default.close()
    tuned=RuntimeStore(settings(tmp_path,'SQLITE_CACHE_KIB=65536\nSQLITE_WAL_AUTOCHECKPOINT_PAGES=16384\n'))
    assert tuned._conn.execute('PRAGMA cache_size').fetchone()[0]==-65536
    assert tuned._conn.execute('PRAGMA wal_autocheckpoint').fetchone()[0]==16384
    assert tuned._conn.execute('PRAGMA synchronous').fetchone()[0]==2
    tuned.close()


@pytest.mark.parametrize('extra',['SQLITE_CACHE_KIB=999999','SQLITE_WAL_AUTOCHECKPOINT_PAGES=0','SQLITE_WAL_AUTOCHECKPOINT_PAGES=-1'])
def test_invalid_budgets_fail_before_opening_database(tmp_path,extra):
    with pytest.raises(ValueError):RuntimeStore(settings(tmp_path,extra))
    assert not (tmp_path/'state'/'vibetrading.sqlite3').exists()


def test_full_wal_commit_recovers_after_process_exit_without_checkpoint(tmp_path):
    configuration=settings(tmp_path,'SQLITE_CACHE_KIB=65536\nSQLITE_WAL_AUTOCHECKPOINT_PAGES=16384\n')
    code='''
import os,sys
from pathlib import Path
from vibetrading.settings import RuntimeSettings
from vibetrading.sqlite_store import RuntimeStore
s=RuntimeStore(RuntimeSettings.from_env(Path(sys.argv[1])))
with s.transaction() as db:
    db.execute("INSERT INTO runtime_metadata VALUES('crash-probe','committed',1)")
os._exit(0)
'''
    subprocess.run([sys.executable,'-c',code,str(tmp_path/'.env')],check=True,env=dict(os.environ,PYTHONPATH=os.pathsep.join((os.getcwd(), os.path.join(os.getcwd(), "src")))))
    reopened=RuntimeStore(configuration)
    assert reopened._conn.execute("SELECT value FROM runtime_metadata WHERE key='crash-probe'").fetchone()[0]=='committed'
    assert reopened._conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    reopened.close()
