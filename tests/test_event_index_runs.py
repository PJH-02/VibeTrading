import hashlib
import sqlite3

import pytest

from storage.event_index_runs import ExactEventRuns, append_runs, ensure_schema


def key(value):
    return hashlib.sha256(str(value).encode()).digest()


def database(path):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=FULL')
    ensure_schema(conn)
    return conn


def commit(conn, values):
    conn.execute('BEGIN IMMEDIATE')
    try:
        append_runs(conn, values)
    except Exception:
        conn.execute('ROLLBACK')
        raise
    else:
        conn.execute('COMMIT')


def test_runs_exact_lookup_after_restart_and_new_commits(tmp_path):
    path = tmp_path / 'index.db'
    conn = database(path)
    index = ExactEventRuns(conn, global_filter_bytes=128)
    expected = {key(i): i + 1 for i in range(400)}
    assert index.lookup(expected) == {}
    for start in range(0, 400, 40):
        commit(conn, [(key(i), i + 1) for i in range(start, start + 40)])
        assert index.lookup([key(start)]) == {key(start): start + 1}
    # The tiny global filter has many collisions. Exact lookup must still
    # reject absent keys and preserve all historical matches across runs.
    assert index.lookup([*expected, *[key(i) for i in range(400, 800)]]) == expected
    assert len(index._global) == 128
    conn.close()
    conn = database(path)
    assert ExactEventRuns(conn, global_filter_bytes=128).lookup(expected) == expected
    conn.close()


def test_rollback_does_not_advance_cache_coverage(tmp_path):
    conn = database(tmp_path / 'index.db')
    index = ExactEventRuns(conn)
    conn.execute('BEGIN IMMEDIATE')
    append_runs(conn, [(key('rolled-back'), 1)])
    with pytest.raises(RuntimeError, match='committed catalogue'):
        index.lookup([key('rolled-back')])
    conn.execute('ROLLBACK')
    commit(conn, [(key('committed'), 2)])
    assert index.lookup([key('rolled-back'), key('committed')]) == {key('committed'): 2}
    conn.close()


@pytest.mark.parametrize('field', ['bloom', 'payload'])
def test_corrupt_filter_or_payload_fails_closed(tmp_path, field):
    conn = database(tmp_path / 'index.db')
    commit(conn, [(key('event'), 1)])
    table = 'event_index_runs' if field == 'bloom' else 'event_index_run_payload'
    # Fault injection deliberately bypasses the immutable-table trigger.
    conn.execute(f'DROP TRIGGER {table}_immutable')
    conn.execute(f'UPDATE {table} SET {field}=zeroblob(length({field}))')
    with pytest.raises(RuntimeError, match='integrity'):
        ExactEventRuns(conn).lookup([key('event')])
    conn.close()


def test_immutable_run_cannot_be_replaced_behind_cache(tmp_path):
    conn = database(tmp_path / 'index.db')
    commit(conn, [(key('event'), 1)])
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        conn.execute('UPDATE event_index_runs SET min_key=max_key')
    with pytest.raises(sqlite3.IntegrityError, match='verified retention'):
        conn.execute('DELETE FROM event_index_run_payload')
    conn.close()


def test_run_and_manifest_transaction_roll_back_together(tmp_path):
    conn = database(tmp_path / 'index.db')
    conn.execute('CREATE TABLE manifest(id INTEGER PRIMARY KEY)')
    conn.execute('BEGIN IMMEDIATE')
    conn.execute('INSERT INTO manifest VALUES(1)')
    append_runs(conn, [(key('event'), 1)])
    conn.execute('ROLLBACK')
    assert conn.execute('SELECT count(*) FROM manifest').fetchone()[0] == 0
    assert ExactEventRuns(conn).lookup([key('event')]) == {}
    with pytest.raises(RuntimeError, match='catalogue transaction'):
        append_runs(conn, [(key('event'), 1)])
    conn.close()


@pytest.mark.parametrize('size,hashes', [(8, 7), (128, 12), (32768, 12)])
def test_vector_filter_is_byte_identical_to_scalar_algorithm(size, hashes):
    from storage.event_index_runs import RECORD, _add, _add_records
    values = [(key(i), i + 1) for i in range(1024)]
    expected = bytearray(size)
    for event_key, _ in values:
        _add(expected, event_key, hashes)
    actual = bytearray(size)
    _add_records(actual, b''.join(RECORD.pack(*entry) for entry in values), hashes)
    assert actual == expected
