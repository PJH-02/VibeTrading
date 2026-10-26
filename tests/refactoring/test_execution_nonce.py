"""Nonce continuity and cooperative ownership; no wallet SDK or network."""
import threading

import pytest

from execution.execution_nonce import SignerNonceStore


KEY = "sandbox:0x" + "a" * 40


def store(root, **changes):
    values=dict(environment="sandbox", max_signers=2,max_db_bytes=1_048_576,max_wal_bytes=1_048_576)
    return SignerNonceStore(root, **(values|changes))


def test_nonce_commit_survives_restart_same_millisecond_and_backward_clock(tmp_path):
    with store(tmp_path) as nonce:
        assert nonce.reserve(KEY, 1000) == 1000
        assert nonce.reserve(KEY, 1000) == 1001
    with store(tmp_path) as nonce:
        assert nonce.reserve(KEY, 999) == 1002
        # Simulates signing/transport failure after reservation. Never reuse.
        nonce.reserve(KEY, 999)
    with store(tmp_path) as nonce:
        assert nonce.reserve(KEY, 1000) == 1004


def test_one_owner_per_environment_and_signer_count_is_finite(tmp_path):
    with store(tmp_path) as nonce:
        with pytest.raises(RuntimeError, match="another process"):
            store(tmp_path)
        assert nonce.reserve(KEY, 1000) == 1000
        assert nonce.reserve("sandbox:0x" + "b" * 40, 1000) == 1000
        with pytest.raises(RuntimeError, match="capacity"):
            nonce.reserve("sandbox:0x" + "c" * 40, 1000)
        assert nonce.reserve(KEY, 1000) == 1001


@pytest.mark.parametrize("key,now", [("live:0x"+"a"*40, 1000),
    ("sandbox:0x"+"A"*40, 1000), (KEY+":account-2", 1000), (KEY, True), (KEY, -1)])
def test_scope_and_clock_validation_precedes_any_nonce_reservation(tmp_path, key, now):
    with store(tmp_path) as nonce:
        with pytest.raises(ValueError):
            nonce.reserve(key, now)
        assert nonce.reserve(KEY, 1000) == 1000


def test_future_watermark_and_non_owner_thread_fail_closed(tmp_path):
    with store(tmp_path) as nonce:
        assert nonce.reserve(KEY, 100_000_000) == 100_000_000
        with pytest.raises(RuntimeError, match="clock moved"):
            nonce.reserve(KEY, 1000)
        errors=[]
        def wrong_thread():
            try:nonce.reserve(KEY, 100_000_000)
            except RuntimeError as error:errors.append(str(error))
        thread=threading.Thread(target=wrong_thread)
        thread.start();thread.join(timeout=2)
        assert errors==["Nonce store requires its open owning thread"]
        assert nonce.reserve(KEY, 100_000_000) == 100_000_001


def test_failure_after_commit_never_recycles_the_unreturned_nonce(tmp_path,monkeypatch):
    with store(tmp_path) as nonce:
        original=nonce._bounds
        checks=[]
        def fail_after_commit():
            checks.append(True)
            original()
            if len(checks)==2:raise RuntimeError('injected post-commit capacity failure')
        monkeypatch.setattr(nonce,'_bounds',fail_after_commit)
        with pytest.raises(RuntimeError,match='post-commit'):
            nonce.reserve(KEY,1000)
    with store(tmp_path) as reopened:
        assert reopened.reserve(KEY,1000)==1001


def test_existing_sqlite_page_size_and_commit_headroom_are_validated(tmp_path):
    import sqlite3
    folder=tmp_path/'sandbox'
    folder.mkdir()
    connection=sqlite3.connect(folder/'execution-nonces.sqlite3')
    connection.execute('PRAGMA page_size=8192')
    connection.execute('CREATE TABLE preexisting(value TEXT)')
    connection.commit();connection.close()
    with store(tmp_path) as nonce:
        assert nonce._db.execute('PRAGMA page_size').fetchone()[0]==8192
        assert nonce._db.execute('PRAGMA max_page_count').fetchone()[0]==128
        assert nonce.reserve(KEY,1000)==1000
    with pytest.raises(ValueError,match='headroom'):
        store(tmp_path,max_wal_bytes=32768)
