"""Async journal ownership and durability; Linux fixtures, no live authority."""
import asyncio
import threading

import pytest

from vibetrading.market_apply_order import ApplyOrderWriter
from vibetrading.market_spool import MarketSpool
from vibetrading.local_pipeline import PipelineBudgets
from test_checkpoint_startup import LIMITS, append, capture, engine, event, producer, read_order


def writer(root, *, factory=None, records=16, on_invalid=lambda reason:None):
    return ApplyOrderWriter(producer='apply-order',incarnation='epoch',
        spool_factory=factory or (lambda:MarketSpool(root,'apply-order','epoch',**LIMITS)),
        budgets=PipelineBudgets(records,65536,4096,records,1),on_invalid=on_invalid)


@pytest.mark.asyncio
async def test_async_order_has_exact_durable_raw_prefix_and_one_owner(tmp_path):
    threads=[]
    class Tracked(MarketSpool):
        def append(self,payload):
            threads.append(threading.get_ident())
            return super().append(payload)
        def flush(self):
            threads.append(threading.get_ident())
            return super().flush()
    journal=writer(tmp_path/'order',factory=lambda:Tracked(tmp_path/'order','apply-order','epoch',**LIMITS))
    await journal.connect()
    assert (await journal.flush()).sequence==0
    item=producer(tmp_path)
    source,codec,_=capture([item],order_spool=journal)
    try:
        source.on_frame(append(item,[event('a',2)])[0])
        assert journal.stats['accepted']==2
        assert (await journal.flush()).sequence==2
        proof=read_order(journal._spool)
        assert [r.cursor.sequence for r in proof.receipts]==[2]
        assert len(set(threads))==1 and threads[0]!=threading.get_ident()
        with pytest.raises(RuntimeError,match='unused'):journal.claim(source)
        other=engine([item])
        try:
            with pytest.raises(RuntimeError):other.capture_checkpoint(codec,order_spool=journal)
        finally:await other.close()
    finally:
        await journal.close();await source.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['overflow','fsync'])
async def test_async_order_failure_fences_every_feed_even_after_applied(tmp_path,fault):
    entered=threading.Event();release=threading.Event()
    class Broken(MarketSpool):
        def flush(self):
            entered.set()
            if not release.wait(5):raise RuntimeError('test gate expired')
            if fault=='fsync':raise OSError('injected fsync failure')
            return super().flush()
    journal=writer(tmp_path/'order',records=1 if fault=='overflow' else 16,
        factory=lambda:Broken(tmp_path/'order','apply-order','epoch',**LIMITS),
        on_invalid=lambda reason:(_ for _ in ()).throw(AssertionError('caller callback is not authority')))
    await journal.connect()
    items=[producer(tmp_path,'a'),producer(tmp_path,'b')]
    source,codec,_=capture(items,order_spool=journal)
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait,2),3)
        frame=append(items[0],[event('a',2)])[0]
        if fault=='overflow':
            with pytest.raises(RuntimeError):source.on_frame(frame)
        else:
            source.on_frame(frame)
            assert source.query_cursor('a').cursor.sequence==2
        release.set()
        with pytest.raises(RuntimeError):await journal.flush()
        assert all(f['mode']=='INVALID' for f in source.stats['feeds'].values())
        for item in items:
            with pytest.raises(RuntimeError):source.query_cursor(item.producer)
        with pytest.raises(RuntimeError):source.capture_checkpoint(codec)
    finally:
        release.set()
        await asyncio.gather(journal.close(),source.close(),return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_flush_retains_writer_and_close_drains(tmp_path):
    entered=threading.Event();release=threading.Event()
    class Blocked(MarketSpool):
        def flush(self):
            entered.set()
            if not release.wait(5):raise RuntimeError('test gate expired')
            return super().flush()
    journal=writer(tmp_path/'order',factory=lambda:Blocked(tmp_path/'order','apply-order','epoch',**LIMITS))
    await journal.connect()
    item=producer(tmp_path)
    source,_,_=capture([item],order_spool=journal)
    try:
        caller=asyncio.create_task(journal.flush())
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait,2),3)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):await caller
        source.on_frame(append(item,[event('a',2)])[0])
        release.set()
        assert (await journal.flush()).sequence==2
        await journal.close()
        assert journal.stats['queued_bytes']==0
        assert len(read_order(journal._spool).receipts)==1
    finally:
        release.set();await journal.close();await source.close()


@pytest.mark.asyncio
async def test_flush_keeps_requested_ordinal_when_new_input_arrives(tmp_path,monkeypatch):
    journal=writer(tmp_path/'order')
    await journal.connect()
    item=producer(tmp_path)
    source,_,_=capture([item],order_spool=journal)
    frame=append(item,[event('a',2)])[0]
    wait=journal.wait_durable
    async def later(admission,**kwargs):
        assert admission.cursor.sequence==1
        source.on_frame(frame)
        return await wait(admission,**kwargs)
    monkeypatch.setattr(journal,'wait_durable',later)
    try:
        assert (await journal.flush()).sequence==1
        assert journal.appended_cursor.sequence==2
    finally:await journal.close();await source.close()


@pytest.mark.asyncio
async def test_nonempty_journal_and_wrong_loop_cannot_claim(tmp_path):
    root=tmp_path/'old'
    spool=MarketSpool(root,'apply-order','epoch',**LIMITS)
    spool.append(b'[1]');spool.close()
    old=writer(root)
    with pytest.raises(RuntimeError,match='empty journal'):await old.connect()
    journal=writer(tmp_path/'new');await journal.connect()
    item=producer(tmp_path)
    source=engine([item])
    async def wrong():
        with pytest.raises(RuntimeError,match='owner-loop'):journal.claim(source)
    try:
        await asyncio.to_thread(lambda:asyncio.run(wrong()))
        assert getattr(journal,'_claimed_engine',None) is None
    finally:await journal.close();await source.close()


@pytest.mark.asyncio
async def test_close_timeout_fences_owner_and_retains_late_cleanup(tmp_path):
    entered=threading.Event();release=threading.Event()
    class Blocked(MarketSpool):
        def flush(self):
            entered.set()
            if not release.wait(5):raise RuntimeError('test gate expired')
            return super().flush()
    journal=writer(tmp_path/'order',factory=lambda:Blocked(tmp_path/'order','apply-order','epoch',**LIMITS))
    await journal.connect()
    item=producer(tmp_path)
    source,_,_=capture([item],order_spool=journal)
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait,2),3)
        with pytest.raises(RuntimeError):await journal.close(timeout_seconds=.01)
        with pytest.raises(RuntimeError):source.query_cursor('a')
        assert journal._cleanup_future is not None
        release.set()
        await asyncio.wait_for(asyncio.wrap_future(journal._cleanup_future),3)
    finally:
        release.set();await asyncio.gather(journal.close(),source.close(),return_exceptions=True)
