"""Published-generation oracles on Linux; no production/readiness claims."""
import asyncio
import hashlib
import json
import os
import threading

import pytest

from vibetrading.checkpoint_journal import CheckpointJournal
from vibetrading.local_config import SpoolLimits
from vibetrading.local_pipeline import PipelineBudgets
from test_checkpoint_startup import LIMITS, append, capture, engine, event, producer, read_order


def journal(root,codec,**options):
    return CheckpointJournal(root,codec,SpoolLimits(**LIMITS),PipelineBudgets(16,65536,2048,16,1),
        **(dict(max_generations=3,max_root_bytes=8_388_608,interval_seconds=60,timeout_seconds=2)|options))


@pytest.mark.asyncio
async def test_rotation_preserves_old_generation_and_exact_new_checkpoint(tmp_path):
    item=producer(tmp_path)
    source,codec,_=capture([item])
    manager=journal(tmp_path/'journal',codec,max_generations=2)
    try:
        first=await manager.rotate(source)
        old=manager._writer
        source.on_frame(append(item,[event('a',2)])[0])
        second=await manager.rotate()
        pointer=json.loads((manager.root/'current.json').read_bytes())
        data=(manager.root/second/'checkpoint.json').read_bytes()
        assert first!=second and pointer['generation']==second
        assert pointer['checkpoint_sha256']==hashlib.sha256(data).hexdigest()
        assert (manager.root/first/'checkpoint.json').exists()
        assert old._cleanup_future.done() and len(manager._writers)==1
        assert manager.publication=='COMMITTED'
        assert len(read_order(old._spool).receipts)==1
        with pytest.raises(RuntimeError,match='reservation'):await manager.rotate()
        assert json.loads((manager.root/'current.json').read_bytes())==pointer
        with pytest.raises(RuntimeError):source.query_cursor('a')
    finally:await manager.close();await source.close()


@pytest.mark.asyncio
async def test_pointer_failure_never_reopens_old_authority(tmp_path,monkeypatch):
    import vibetrading.checkpoint_journal as module
    item=producer(tmp_path)
    source,codec,_=capture([item])
    manager=journal(tmp_path/'journal',codec)
    try:
        await manager.rotate(source)
        previous=(manager.root/'current.json').read_bytes()
        write=module._write_checkpoint_file
        def fail(path,*args):
            if path.name=='current.json':raise OSError('injected pointer failure')
            return write(path,*args)
        monkeypatch.setattr(module,'_write_checkpoint_file',fail)
        source.on_frame(append(item,[event('a',2)])[0])
        with pytest.raises(OSError):await manager.rotate()
        assert (manager.root/'current.json').read_bytes()==previous
        assert len(list(manager.root.glob('*/checkpoint.json')))==2
        assert all(f['mode']=='INVALID' for f in source.stats['feeds'].values())
        with pytest.raises(RuntimeError):source.query_cursor('a')
    finally:await manager.close();await source.close()


@pytest.mark.asyncio
async def test_cancel_before_publication_fences_and_retains_operation(tmp_path,monkeypatch):
    import vibetrading.checkpoint_journal as module
    entered=threading.Event();release=threading.Event()
    item=producer(tmp_path)
    source,codec,_=capture([item])
    manager=journal(tmp_path/'journal',codec)
    write=module._write_checkpoint_file
    def blocked(path,*args):
        if path.name=='checkpoint.json':
            entered.set()
            if not release.wait(5):raise RuntimeError('test gate expired')
        return write(path,*args)
    monkeypatch.setattr(module,'_write_checkpoint_file',blocked)
    caller=asyncio.create_task(manager.rotate(source))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait,2),3)
        with pytest.raises(RuntimeError,match='one rotation'):await manager.rotate()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):await caller
        assert not manager._operation.done()
        with pytest.raises(RuntimeError):source.query_cursor('a')
        release.set()
        await asyncio.gather(manager._operation,return_exceptions=True)
        assert not (manager.root/'current.json').exists()
    finally:
        release.set();await manager.close();await source.close()


@pytest.mark.asyncio
async def test_root_lock_rejects_second_manager_until_workers_close(tmp_path):
    item=producer(tmp_path)
    source,codec,_=capture([item])
    other=engine([item])
    first=journal(tmp_path/'journal',codec)
    second=journal(first.root,codec)
    try:
        await first.rotate(source)
        with pytest.raises(OSError):await second.rotate(other)
        assert source.query_cursor('a').cursor.sequence==1
    finally:
        await first.close();await second.close();await source.close();await other.close()


@pytest.mark.asyncio
async def test_cancel_after_publication_authorization_may_commit_but_never_reopen(tmp_path,monkeypatch):
    import vibetrading.checkpoint_journal as module
    entered=threading.Event();release=threading.Event()
    item=producer(tmp_path)
    source,codec,_=capture([item])
    manager=journal(tmp_path/'journal',codec)
    write=module._write_checkpoint_file
    def blocked(path,*args):
        if path.name=='current.json':
            entered.set()
            if not release.wait(5):raise RuntimeError('test gate expired')
        return write(path,*args)
    monkeypatch.setattr(module,'_write_checkpoint_file',blocked)
    caller=asyncio.create_task(manager.rotate(source))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait,2),3)
        assert manager.publication=='AUTHORIZED'
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):await caller
        release.set()
        await asyncio.gather(manager._operation,return_exceptions=True)
        assert manager.publication=='COMMITTED'
        assert (manager.root/'current.json').exists()
        with pytest.raises(RuntimeError):source.query_cursor('a')
    finally:release.set();await manager.close();await source.close()


@pytest.mark.asyncio
async def test_root_byte_reservation_rejects_before_generation_creation(tmp_path):
    item=producer(tmp_path)
    source,codec,_=capture([item])
    manager=journal(tmp_path/'journal',codec,max_root_bytes=1000)
    try:
        with pytest.raises(RuntimeError,match='reservation'):await manager.rotate(source)
        assert not list(manager.root.glob('*/checkpoint.json'))
        assert not (manager.root/'current.json').exists()
        with pytest.raises(RuntimeError):source.query_cursor('a')
    finally:await manager.close();await source.close()


@pytest.mark.asyncio
async def test_close_timeout_retains_root_lock_until_actual_spool_cleanup(tmp_path,monkeypatch):
    import vibetrading.checkpoint_journal as module
    entered=threading.Event();release=threading.Event();blocked=[False]
    original=module.MarketSpool
    class Blocked(original):
        def flush(self):
            if blocked[0]:
                entered.set()
                if not release.wait(5):raise RuntimeError('test gate expired')
            return super().flush()
    monkeypatch.setattr(module,'MarketSpool',Blocked)
    item=producer(tmp_path)
    source,codec,_=capture([item])
    other=engine([item])
    manager=journal(tmp_path/'journal',codec)
    second=journal(manager.root,codec)
    try:
        await manager.rotate(source)
        manager.timeout_seconds=.05  # Short deadline targets close, not initial anchor fsync.
        blocked[0]=True
        source.on_frame(append(item,[event('a',2)])[0])
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait,2),3)
        with pytest.raises((TimeoutError,RuntimeError)):await manager.close()
        assert manager._lock is not None
        with pytest.raises(OSError):await second.rotate(other)
        release.set()
        await asyncio.wait_for(asyncio.gather(manager._close_task,return_exceptions=True),3)
        assert manager._lock is None
    finally:
        release.set()
        await asyncio.gather(manager.close(),second.close(),source.close(),other.close(),return_exceptions=True)


@pytest.mark.asyncio
async def test_unpublished_orphan_generation_cannot_become_a_virgin_start(tmp_path):
    item=producer(tmp_path)
    source,codec,_=capture([item])
    root=tmp_path/'journal';(root/('a'*32)).mkdir(parents=True)
    manager=journal(root,codec)
    try:
        with pytest.raises(RuntimeError,match='verified resume'):await manager.rotate(source)
        assert not (root/'current.json').exists()
    finally:await manager.close();await source.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('location',['root','order'])
async def test_inventory_stops_at_explicit_root_and_nested_bounds(tmp_path,location):
    item=producer(tmp_path)
    source,codec,_=capture([item])
    manager=journal(tmp_path/'journal',codec)
    try:
        generation=await manager.rotate(source)
        directory=manager.root if location=='root' else manager.root/generation/'order'
        for n in range(410):
            if location=='root':(directory/f'{n:032x}').mkdir()
            else:(directory/f'unexpected-{n}').write_bytes(b'x')
        with pytest.raises(RuntimeError,match='inventory|reservation'):await manager.rotate()
    finally:
        await asyncio.gather(manager.close(),source.close(),return_exceptions=True)
        assert manager._lock is None


@pytest.mark.asyncio
async def test_published_interleaved_suffix_restores_and_moves_to_new_generation(tmp_path):
    items=[producer(tmp_path,'a'),producer(tmp_path,'b')]
    source,codec,_=capture(items)
    first=journal(tmp_path/'journal',codec)
    target=engine(items)
    second=journal(first.root,codec)
    try:
        old=await first.rotate(source)
        for item in reversed(items):source.on_frame(append(item,[event(item.producer,2)])[0])
        await first.close()
        assert await second.restore(target,items,lambda *args:True) is True
        assert target.hot_path.apply_seq==4 and target.stats['replayed']==2
        assert target.stats['delivered']==target.stats['queued_records']==0
        assert target._checkpoint_restored is True
        assert target.initial_resume_receipt(target.query_cursor('a').cursor) is False
        new=await second.rotate()
        assert new!=old and (second.root/old/'checkpoint.json').exists()
        assert json.loads((second.root/'current.json').read_bytes())['generation']==new
    finally:
        await asyncio.gather(first.close(),second.close(),source.close(),target.close(),return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['missing_pointer','missing_checkpoint','digest','traversal','incarnation','version','unrecorded','readiness'])
async def test_invalid_selected_generation_never_becomes_a_cold_fallback(tmp_path,fault):
    from vibetrading.market_checkpoint import _canonical
    item=producer(tmp_path)
    source,codec,_=capture([item])
    first=journal(tmp_path/'journal',codec)
    target=engine([item]);second=journal(first.root,codec)
    try:
        generation=await first.rotate(source)
        await first.close()
        path=first.root/'current.json'
        point=json.loads(path.read_bytes())
        if fault=='missing_pointer':path.unlink()
        if fault=='missing_checkpoint':(first.root/generation/'checkpoint.json').unlink()
        if fault=='digest':point['checkpoint_sha256']='0'*64
        if fault=='traversal':point['generation']='../outside'
        if fault=='incarnation':point['engine_incarnation']='different'
        if fault=='version':point['version']=True
        if fault in {'digest','traversal','incarnation','version'}:path.write_bytes(_canonical(point,4096))
        if fault=='unrecorded':append(item,[event('a',2)])
        with pytest.raises((RuntimeError,ValueError,OSError)):
            await second.restore(target,[item],lambda *args:fault!='readiness')
        assert all(f['mode']!='LIVE' for f in target.stats['feeds'].values())
        with pytest.raises(RuntimeError):target.query_cursor('a')
        if fault!='readiness':assert target.hot_path.apply_seq==0
    finally:
        await asyncio.gather(first.close(),second.close(),source.close(),target.close(),return_exceptions=True)


@pytest.mark.asyncio
async def test_only_lock_only_root_is_virgin_and_fifo_metadata_cannot_block(tmp_path):
    from vibetrading.checkpoint_journal import _read_regular
    item=producer(tmp_path)
    source,codec,_=capture([item])
    target=engine([item]);manager=journal(tmp_path/'empty',codec)
    fifo=tmp_path/'fifo';os.mkfifo(fifo)
    try:
        with pytest.raises(RuntimeError,match='metadata file'):_read_regular(fifo,4096)
        assert await manager.restore(target,[item],lambda *args:True) is False
        assert target.hot_path.apply_seq==0
        assert {p.name for p in manager.root.iterdir()}=={'writer.lock'}
    finally:await manager.close();await target.close();await source.close()
