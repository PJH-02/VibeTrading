import sqlite3
import concurrent.futures
import threading
import time

from storage.gap_ledger import CollectionGapLedger


def test_stalled_feed_gap_starts_at_last_published_event(tmp_path) -> None:
    path = tmp_path / "gaps.sqlite3"
    ledger = CollectionGapLedger(path)
    ledger.start("bybit-perp", "@crypto", "market_event_stalled", 12345)
    ledger.start("bybit-perp", "@crypto", "retry", 67890)
    ledger.end("bybit-perp")
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT started_ns,ended_ns,reason FROM gaps").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 12345
    assert rows[0][1] is not None
    assert rows[0][2] == "market_event_stalled"


def test_concurrent_gap_updates_are_serialized_and_connections_close(tmp_path, monkeypatch):
    path=tmp_path/'gaps.sqlite3'
    opened=[]
    connect=sqlite3.connect
    def tracked(*args, **kwargs):
        db=connect(*args, **kwargs);opened.append(db);return db
    monkeypatch.setattr("storage.gap_ledger.sqlite3.connect", tracked)
    ledger=CollectionGapLedger(path)
    ledger.start('bybit-perp','@crypto','original',123)
    barrier=threading.Barrier(3)
    def update(index):
        barrier.wait()
        for count in range(10):
            ledger.start('bybit-perp','@crypto','retry',unsent_events=index*10+count,details={'attempt':count})
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(update,range(3)))
    for db in opened:
        try: db.execute('SELECT 1')
        except sqlite3.ProgrammingError: pass
        else: raise AssertionError('gap connection leaked')
    db=connect(path)
    try:
        assert db.execute('SELECT count(*),reason,started_ns,unsent_events FROM gaps').fetchone()==(1,'original',123,29)
    finally: db.close()


def test_gap_waits_for_existing_writer_before_reading(tmp_path):
    path=tmp_path/'gaps.sqlite3';ledger=CollectionGapLedger(path)
    ledger.start('binance-perp','@crypto','original')
    db=sqlite3.connect(path);db.execute('BEGIN IMMEDIATE')
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        task=executor.submit(ledger.start,'bitget-perp','@crypto','timeout')
        time.sleep(.1)
        assert not task.done()
        db.commit();db.close()
        task.result(timeout=5)
