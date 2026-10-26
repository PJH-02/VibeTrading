"""Linux market checkpoint generations; no deletion, fallback or LIVE authority."""
import asyncio
from dataclasses import asdict
import hashlib
import inspect
import json
import math
import os
import stat
from pathlib import Path
import uuid

from market.market_apply_order import ApplyOrder, ApplyOrderWriter
from .market_checkpoint import _canonical, _write_checkpoint_file
from .market_spool import MarketSpool, SpoolReader, _sync_directory


def _read_regular(path, limit):
    descriptor = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=limit:
            raise RuntimeError('invalid checkpoint journal metadata file')
        payload = stream.read(limit+1)
    if not 0<len(payload)<=limit:raise RuntimeError('checkpoint journal metadata byte limit')
    return payload


class CheckpointJournal:
    def __init__(self, root, codec, limits, budgets, *, max_generations, max_root_bytes,
                 interval_seconds, timeout_seconds=5):
        self.root = Path(root)
        if (not self.root.is_absolute() or any(type(n) is not int or n <= 0
                for n in (max_generations,max_root_bytes))
                or any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) or n <= 0
                       for n in (interval_seconds,timeout_seconds))
                or budgets.max_frame_bytes+limits.max_header_bytes+48 > limits.max_frame_bytes):
            raise ValueError('explicit absolute journal root and finite resource bounds required')
        self.codec,self.limits,self.budgets = codec,limits,budgets
        self.max_generations,self.max_root_bytes = max_generations,max_root_bytes
        self.interval_seconds,self.timeout_seconds = interval_seconds,timeout_seconds
        self._engine = self._loop = self._lock = self._writer = None
        self._operation = self._close_task = None
        self._writers = []
        self._closing = self._failed = False
        self.publication = 'NONE'

    def _acquire(self):
        import fcntl
        if self.root.is_symlink() or (self.root/'writer.lock').is_symlink():
            raise RuntimeError('checkpoint journal root/lock symlink')
        self.root.mkdir(exist_ok=True)
        _sync_directory(self.root.parent)
        descriptor = os.open(self.root/'writer.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        handle = os.fdopen(descriptor,'a+b')
        try:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):raise RuntimeError('journal lock must be regular')
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:
            handle.close()
            raise
        return handle

    def _inventory(self):
        size = nodes = generations = 0
        pending = [(self.root,0)]
        while pending:
            folder,depth = pending.pop()
            with os.scandir(folder) as entries:
                for p in entries:
                    nodes += 1
                    if p.is_symlink() or nodes>self.max_generations*(self.limits.max_records+6)+2:
                        raise RuntimeError('checkpoint journal inventory invalid')
                    if p.is_dir(follow_symlinks=False):
                        if depth==0:
                            generations += 1
                            if len(p.name)!=32 or any(c not in '0123456789abcdef' for c in p.name) or generations>self.max_generations:
                                raise RuntimeError('checkpoint journal generation reservation exceeded')
                        elif depth!=1 or p.name!='order':
                            raise RuntimeError('checkpoint journal inventory invalid')
                        pending.append((p.path,depth+1))
                    elif p.is_file(follow_symlinks=False):
                        if depth==0 and p.name not in {'writer.lock','current.json'}:
                            raise RuntimeError('checkpoint journal inventory invalid')
                        size += p.stat(follow_symlinks=False).st_size
                        if size>self.max_root_bytes:raise RuntimeError('checkpoint journal root byte budget exceeded')
                    else:raise RuntimeError('checkpoint journal inventory invalid')
        return generations,size

    def _reserve(self, generation):
        generations,size = self._inventory()
        if generations>=self.max_generations:raise RuntimeError('checkpoint journal generation reservation exceeded')
        reserve = self.limits.max_disk_bytes+self.codec.max_bytes
        if (generations+1)*reserve+8192>self.max_root_bytes or size+reserve+8192>self.max_root_bytes:
            raise RuntimeError('checkpoint journal root reservation exceeded')
        directory = self.root/generation
        directory.mkdir()
        return directory

    def _check(self):
        if self._closing or self._failed:
            raise RuntimeError('checkpoint journal authority closed')
        if self._writer is not None:self._writer._check()

    def _fence(self):
        self._failed = True
        if self._engine is not None:self._engine.fail_order_journal()

    @staticmethod
    def _observe(task):
        if not task.cancelled():task.exception()

    async def rotate(self, engine=None):
        return await self._perform(engine,self._rotate)

    async def restore(self, engine, producers, readiness):
        if not callable(readiness) or inspect.iscoroutinefunction(readiness):
            raise ValueError('checkpoint journal requires synchronous readiness')
        return await self._perform(engine,lambda:self._restore(producers,readiness))

    async def _perform(self, engine, operation):
        self._check()
        engine = engine or self._engine
        loop = asyncio.get_running_loop()
        if (engine is None or (self._engine is not None and engine is not self._engine)
                or (self._loop is not None and loop is not self._loop)
                or (self._operation is not None and not self._operation.done()) or len(self._writers)>1):
            raise RuntimeError('checkpoint journal requires one engine/loop and one rotation')
        self._engine,self._loop = engine,loop
        self._operation = asyncio.create_task(operation(),name='checkpoint-journal-operation')
        self._operation.add_done_callback(self._observe)
        try:return await asyncio.shield(self._operation)
        except asyncio.CancelledError:
            self._fence()
            raise

    def _read_bundle(self):
        self._inventory()
        path = self.root/'current.json'
        if not path.exists():
            if self._has_history():raise RuntimeError('checkpoint journal history has no published pointer')
            return None
        if path.is_symlink():raise RuntimeError('checkpoint journal pointer symlink')
        raw = _read_regular(path,4096)
        point = json.loads(raw)
        if (type(point) is not dict or set(point)!={'version','generation','checkpoint_sha256','engine_incarnation'}
                or type(point['version']) is not int or point['version']!=1 or _canonical(point,4096)!=raw
                or any(type(point[k]) is not str or len(point[k])!=n
                       or any(c not in '0123456789abcdef' for c in point[k])
                       for k,n in (('generation',32),('checkpoint_sha256',64)))
                or type(point['engine_incarnation']) is not str or not 0<len(point['engine_incarnation'].encode())<=128):
            raise RuntimeError('invalid checkpoint journal pointer')
        directory = self.root/point['generation']
        if any(p.is_symlink() for p in (directory,directory/'checkpoint.json',directory/'order')):
            raise RuntimeError('checkpoint journal selected path symlink')
        payload = _read_regular(directory/'checkpoint.json',self.codec.max_bytes)
        if hashlib.sha256(payload).hexdigest()!=point['checkpoint_sha256']:
            raise RuntimeError('checkpoint journal checkpoint digest mismatch')
        reader = SpoolReader(directory/'order','apply-order',point['generation'],**self.limits.reader_kwargs)
        proof = ApplyOrder.read(reader,max_bytes=self.codec.max_bytes)
        if proof.checkpoint_sha256!=point['checkpoint_sha256']:
            raise RuntimeError('checkpoint journal anchor mismatch')
        return point,payload,proof

    async def _restore(self, producers, readiness):
        try:
            if self._lock is not None:raise RuntimeError('checkpoint journal restore is single use')
            self._lock = await asyncio.to_thread(self._acquire)
            bundle = await asyncio.to_thread(self._read_bundle)
            self._check()
            if bundle is None:return False
            from vibetrading.local_runtime import replay_checkpoint_startup
            point,payload,proof = bundle
            await replay_checkpoint_startup(self._engine,producers,codec=self.codec,payload=payload,
                expected_engine_incarnation=point['engine_incarnation'],apply_order=proof)
            self._check()
            if self._engine.complete_checkpoint_recovery(readiness) is not True:
                raise RuntimeError('checkpoint journal readiness did not permit startup')
            return True
        except BaseException:
            self._fence()
            raise

    async def _rotate(self):
        old = self._writer
        try:
            if self._lock is None:
                self._lock = await asyncio.to_thread(self._acquire)
                if await asyncio.to_thread(self._has_history):
                    raise RuntimeError('existing checkpoint journal requires verified resume')
            self._check()
            generation = uuid.uuid4().hex
            directory = await asyncio.to_thread(self._reserve,generation)
            writer = ApplyOrderWriter(producer='apply-order',incarnation=generation,budgets=self.budgets,
                spool_factory=lambda:MarketSpool(directory/'order','apply-order',generation,**asdict(self.limits)),
                on_invalid=self._engine.fail_order_journal)
            self._writers.append(writer)
            await writer.connect()
            self._check()
            payload = self._engine.capture_checkpoint(self.codec,order_spool=writer)
            self._writer = writer
            anchor = await writer.flush(timeout_seconds=self.timeout_seconds)
            if anchor.sequence!=1:raise RuntimeError('checkpoint journal anchor is not exact')
            await asyncio.to_thread(_write_checkpoint_file,directory/'checkpoint.json',payload,self.codec.max_bytes)
            self._check()
            writer._check()
            self._engine.verify_order_journal(writer)
            pointer = _canonical({'version':1,'generation':generation,
                'checkpoint_sha256':hashlib.sha256(payload).hexdigest(),
                'engine_incarnation':self._engine.hot_path.engine_incarnation},4096)
            self.publication = 'AUTHORIZED'
            await asyncio.to_thread(_write_checkpoint_file,self.root/'current.json',pointer,4096)
            self.publication = 'COMMITTED'
            self._check()
            writer._check()
            if old is not None:
                await old.close(timeout_seconds=self.timeout_seconds)
                self._writers.remove(old)
            return generation
        except BaseException:
            self._fence()
            raise

    def _has_history(self):
        with os.scandir(self.root) as entries:
            return any(p.name!='writer.lock' for p in entries)

    async def run(self):
        while True:
            await asyncio.sleep(self.interval_seconds)
            await self.rotate()

    async def close(self):
        if self._close_task is None:
            self._closing = True
            if self._engine is not None:self._engine.fail_order_journal()
            self._close_task = asyncio.create_task(self._close(),name='checkpoint-journal-close')
            self._close_task.add_done_callback(self._observe)
        await asyncio.wait_for(asyncio.shield(self._close_task),self.timeout_seconds)

    async def _close(self):
        if self._operation is not None:
            await asyncio.gather(self._operation,return_exceptions=True)
        errors = await asyncio.gather(*(w.close(timeout_seconds=self.timeout_seconds) for w in self._writers),return_exceptions=True)
        pending = [asyncio.wrap_future(w._cleanup_future) for w in self._writers if w._cleanup_future is not None]
        if pending:errors += await asyncio.gather(*pending,return_exceptions=True)
        if self._lock is not None:
            await asyncio.to_thread(self._lock.close)
            self._lock = None
        if any(isinstance(error,BaseException) for error in errors):
            raise RuntimeError('checkpoint journal cleanup failed')
