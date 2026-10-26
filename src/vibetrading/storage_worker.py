from __future__ import annotations

import asyncio
import logging
import time

from .bus import BusMessage, EventBus, NatsJetStreamBus, dumps
from .parquet_store import ParquetBatchWriter
from .settings import RuntimeSettings, get_settings
from .health import touch as health_touch
from .storage_guard import StorageGuard

logger=logging.getLogger(__name__)


class StorageWorker:
    """ACK only delivery rows committed with a durable Parquet manifest."""
    def __init__(self,bus:EventBus|None=None,writer:ParquetBatchWriter|None=None,settings:RuntimeSettings|None=None,domain:str="market")->None:
        self.settings=settings or (writer.settings if writer is not None else get_settings()); self.bus=bus or NatsJetStreamBus(self.settings); self.writer=writer or ParquetBatchWriter(self.settings); self._pending:dict[str,BusMessage]={}; self._pending_event_ids:dict[str,str]={}
        self._pending_alias_sizes:dict[str,int]={};self._pending_alias_bytes=0;self._ack_floors:dict[tuple[str,str],int]={}
        if domain not in {"market", "crypto"}: raise ValueError("unsupported storage domain")
        self.domain=domain
        # A 64k JetStream window is for outage tolerance, not a target batch
        # size. Large batches make Parquet finalization and consumer control
        # traffic monopolize this small VM.
        configured=int(self.settings.env.get("STORAGE_MAX_PENDING_BEFORE_FLUSH","16384" if domain=="crypto" else "2048"))
        self._flush_pending_limit=max(64,min(configured,int(self.settings.nats.max_ack_pending*.8)))
        self._flush_byte_limit=max(1024*1024,int(self.settings.env.get("STORAGE_FLUSH_BUFFER_BYTES",str(64*1024*1024 if domain=="crypto" else 16*1024*1024))))
        # Arrow's allocator may retain released pages, so RSS can remain above
        # the soft threshold after a successful flush. Require a meaningful
        # live buffer before using RSS as a flush signal; otherwise every NATS
        # batch becomes a tiny Parquet file.
        self._rss_flush_buffer_floor=max(4*1024*1024,min(self._flush_byte_limit,self._flush_byte_limit//4))
        self._flush_seconds=max(0.1,float(self.settings.env.get("STORAGE_FLUSH_SECONDS","60" if domain=="crypto" else "5")))
        self._pending_alias_limit=max(1,min(self._flush_pending_limit,int(self.settings.env.get("STORAGE_MAX_PENDING_ALIAS_HANDLES","2048"))))
        self._pending_alias_bytes_limit=max(1024*1024,int(self.settings.env.get("STORAGE_MAX_PENDING_ALIAS_BYTES",str(32*1024*1024))))
        # These PRAGMAs are set once on this writer connection. Sampling the
        # actual startup values avoids a heartbeat waiting behind a checkpoint
        # or the initial index reconstruction just to reread unchanged values.
        self._writer_pragmas=self.writer.store.connection_pragmas()

    async def _flush(self,stats:dict[str,int],mode:str="due",target_buffer_bytes:int=0)->None:
        if mode=="all": operation=self.writer.flush_all
        elif mode=="aged": operation=lambda:self.writer.flush_aged(self._flush_seconds)
        elif mode=="largest": operation=lambda:self.writer.flush_largest(target_buffer_bytes)
        else: operation=self.writer.flush_due
        commit_started=time.monotonic()
        paths=await asyncio.to_thread(operation)
        stats['last_commit_ms']=int((time.monotonic()-commit_started)*1000)
        if not paths:return
        stats['files']+=len(paths); stats['flushes']+=1
        committed=set(self.writer.last_committed_ids); self.writer.last_committed_ids.clear()
        ack=[]
        for cid in committed:
            msg=self._pending.pop(cid,None)
            self._pending_alias_bytes-=self._pending_alias_sizes.pop(cid,0)
            if msg:
                if msg.event_id and self._pending_event_ids.get(msg.event_id)==cid: self._pending_event_ids.pop(msg.event_id,None)
                ack.append(msg)
        # ACKs are best effort after a durable manifest commit. Failed ACKs
        # safely redeliver and are deduplicated from parquet_delivery.
        ack_started=time.monotonic();results=[];ack_messages=[]
        for start in range(0,len(ack),256):
            chunk=ack[start:start+256]
            results.extend(await asyncio.gather(*(m.ack() for m in chunk),return_exceptions=True));ack_messages.extend(chunk)
        # A failed post-commit ACK is deliberately not NAKed: redelivery is
        # deduplicated from parquet_delivery and ACKed next time.
        stats['acked']+=sum(not isinstance(x,Exception) for x in results)
        stats['last_ack_ms']=int((time.monotonic()-ack_started)*1000)
        # A successful one-way ACK send is not server confirmation. Never
        # persist a locally inferred floor as if JetStream had confirmed it.
        confirmed_reader=getattr(self.bus,"confirmed_ack_floor",None)
        if self.domain=="crypto" and confirmed_reader is not None:
            try:
                receipt=await asyncio.wait_for(
                    confirmed_reader("CRYPTO_DATA","crypto-parquet-storage","crypto.>"),timeout=5)
                await asyncio.to_thread(self.writer.store.record_confirmed_ack_floor,receipt)
            except Exception:
                logger.warning("failed to persist confirmed crypto ACK receipt",exc_info=True)
            return
        floor_reader=getattr(self.bus,"consumer_ack_floor",None)
        if floor_reader is not None:
            stream,durable=("CRYPTO_DATA","crypto-parquet-storage") if self.domain=="crypto" else (self.settings.nats.stream_name,"parquet-storage")
            try:
                observed=await asyncio.wait_for(floor_reader(stream,durable),timeout=5)
                if observed is not None:
                    generation,ack_floor=observed;key=(stream,generation)
                    if ack_floor>self._ack_floors.get(key,0):
                        await asyncio.to_thread(self.writer.store.record_delivery_ack_floor,stream,generation,ack_floor)
                        self._ack_floors[key]=ack_floor
            except Exception:
                logger.warning("failed to persist JetStream consumer ACK floor",exc_info=True)

    @staticmethod
    def _rss()->int:
        try:
            import psutil
            return int(psutil.Process().memory_info().rss)
        except Exception:return 0

    @staticmethod
    def _cgroup_memory()->tuple[int,int]:
        try:
            from pathlib import Path
            root=Path("/sys/fs/cgroup")
            used=int((root/"memory.current").read_text().strip())
            limit_text=(root/"memory.max").read_text().strip()
            stats:dict[str,int]={}
            for line in (root/"memory.stat").read_text().splitlines():
                key, _, value=line.partition(" ")
                if key in {"file","file_dirty","file_writeback","shmem","inactive_file"}:
                    stats[key]=int(value)
            working_set=StorageWorker._cgroup_nonreclaimable_bytes(used,stats)
            return working_set, int(limit_text) if limit_text != "max" else 0
        except (OSError,ValueError):return 0,0

    @staticmethod
    def _cgroup_nonreclaimable_bytes(used:int, stats:dict[str,int])->int:
        # Both active_file and inactive_file are page cache. Counting only
        # inactive_file as reclaimable caused forced flushes with tiny Arrow
        # buffers and spurious restarts while clean Parquet cache filled the
        # cgroup. Dirty, writeback and shared memory stay in the pressure sum.
        if "file" in stats:
            reclaimable=max(0,stats["file"]-stats.get("file_dirty",0)-stats.get("file_writeback",0)-stats.get("shmem",0))
        else:
            reclaimable=stats.get("inactive_file",0)
        return max(0,used-reclaimable)

    def _rss_thresholds(self,cgroup_limit:int)->tuple[int,int]:
        flush=self.settings.parquet.rss_flush_bytes
        pause=self.settings.parquet.rss_pause_bytes
        # Crypto storage keeps a large steady Arrow/Python RSS baseline. Using
        # that RSS alone triggered repeated partial flushes even with ample
        # cgroup headroom. When a cgroup limit is available, let the explicit
        # cgroup working-set guard drive crypto memory pressure instead.
        if self.domain=="crypto" and cgroup_limit>0:
            flush=max(flush,int(cgroup_limit*.75))
            pause=max(pause,int(cgroup_limit*.90))
        return flush,pause

    async def run(self,duration_sec:float|None=None)->dict[str,int]:
        subject,durable,component=("crypto.>","crypto-parquet-storage","crypto-storage") if self.domain=="crypto" else ("md.>","parquet-storage","storage")
        await self.bus.connect(); deadline=time.monotonic()+duration_sec if duration_sec else None; stats={'messages':0,'acked':0,'deduplicated':0,'flushes':0,'files':0}; batches=self.bus.subscribe_batches(subject,durable=durable,batch=128); last_progress=time.monotonic(); last_flush=time.monotonic(); last_aged_scan=time.monotonic()
        index_ready=self.writer.store.event_index_format != 'immutable-runs-v1'
        aged_scan_interval=max(0.5,min(5.0,self._flush_seconds/4))

        def write_heartbeat() -> None:
            # SQLite's connection lock can be held throughout a slow commit
            # or checkpoint. Waiting for it on the event loop also stops NATS
            # heartbeats, ACKs and cancellation. Keep the entire blocking
            # health read/write off that loop, with at most one job in flight.
            cgroup_used,cgroup_limit=self._cgroup_memory()
            health_touch(self.settings,component,state='running' if index_ready else 'initializing',
                detail={"ready":index_ready,"pending":len(self._pending),"buffer_bytes":self.writer.buffer_bytes,
                    "pending_alias_handles":len(self._pending_alias_sizes),"pending_alias_bytes":self._pending_alias_bytes,
                    "rss_bytes":self._rss(),"cgroup_used_bytes":cgroup_used,"cgroup_limit_bytes":cgroup_limit,
                    "sqlite_writer_pragmas":self._writer_pragmas,
                    "event_index":self.writer.store.event_index_progress(),
                    "disk":StorageGuard(self.settings).evaluate().as_dict(),
                    **stats})

        heartbeat_lock=asyncio.Lock()

        async def publish_heartbeat() -> None:
            async with heartbeat_lock:
                await asyncio.to_thread(write_heartbeat)

        async def heartbeat() -> None:
            while True:
                await publish_heartbeat()
                await asyncio.sleep(20)

        heartbeat_task=asyncio.create_task(heartbeat(),name=f"{component}-heartbeat")
        try:
            if not index_ready:
                await publish_heartbeat()
                await asyncio.to_thread(self.writer.store.prepare_event_index)
                index_ready=True
                await publish_heartbeat()
                # A bounded run measures active consumption, not the one-time
                # recovery period during which publishers must remain paused.
                deadline=time.monotonic()+duration_sec if duration_sec else None
            while deadline is None or time.monotonic()<deadline:
                batch=await anext(batches)
                # First deliveries are new within this consumer session. Only
                # redeliveries need the durable delivery-key lookup; pending
                # first deliveries are deduplicated by the in-memory map.
                committed_deliveries = await asyncio.to_thread(
                    self.writer.store.committed_deliveries,
                    ((m.stream_name,m.stream_generation,m.stream_sequence) for m in batch
                     if m.stream_sequence is not None and m.delivery_count>1),
                )
                candidate_event_ids = [
                    m.event_id for m in batch
                    if m.event_id and m.commit_id not in self._pending
                    and (m.stream_name, m.stream_generation, int(m.stream_sequence or 0)) not in committed_deliveries
                ]
                committed_event_ids = await asyncio.to_thread(self.writer.store.committed_event_ids, candidate_event_ids)
                replay_cutoff = await asyncio.to_thread(self.writer.store.crypto_live_cutoff_ns) if self.domain == 'crypto' else None
                if self.domain == "crypto" and committed_event_ids:
                    # A new NATS sequence for a previously committed event
                    # still needs durable delivery coverage before its ACK.
                    await asyncio.to_thread(
                        self.writer.store.commit_event_aliases,
                        [(m.stream_name, m.stream_generation, int(m.stream_sequence or 0), m.event_id)
                         for m in batch if m.event_id in committed_event_ids
                         and (m.stream_name, m.stream_generation, int(m.stream_sequence or 0))
                         not in committed_deliveries],
                    )
                for message in batch:
                    delivery_key=(message.stream_name,message.stream_generation,int(message.stream_sequence or 0))
                    if message.stream_sequence is not None and delivery_key in committed_deliveries:
                        try:
                            await message.ack()
                        except Exception:
                            logger.warning("failed to ACK already committed delivery; waiting for redelivery",exc_info=True)
                        stats['deduplicated']+=1; continue
                    # JetStream may redeliver before the long Parquet flush.
                    # Retain the newest delivery handle but never append the
                    # same stream generation/sequence to the in-memory batch.
                    if message.commit_id in self._pending:
                        self._pending[message.commit_id]=message; stats['deduplicated']+=1; continue
                    if message.event_id and message.event_id in self._pending_event_ids:
                        # The canonical event is buffered but not durable yet.
                        # Record this delivery beside it; _flush will ACK both
                        # only after file fsync and manifest transaction.
                        alias_bytes=len(dumps(message.data))
                        if not self.writer.add_pending_delivery_alias(
                            message.event_id,stream_name=message.stream_name,
                            stream_generation=message.stream_generation,
                            stream_sequence=int(message.stream_sequence or 0),
                            event_key=message.event_id,
                        ):
                            raise RuntimeError("pending event has no durable-writer buffer")
                        self._pending[message.commit_id]=message
                        self._pending_alias_sizes[message.commit_id]=alias_bytes
                        self._pending_alias_bytes+=alias_bytes
                        stats['deduplicated']+=1; continue
                    if message.event_id and message.event_id in committed_event_ids:
                        try:
                            await message.ack()
                        except Exception:
                            logger.warning("failed to ACK deduplicated event; waiting for redelivery",exc_info=True)
                        stats['deduplicated']+=1; continue
                    if replay_cutoff is not None:
                        age=max(60,int(self.settings.env.get('MARKET_EVENT_MAX_AGE_SECONDS','172800')))
                        current_cutoff=max(replay_cutoff,time.time_ns()-age*1_000_000_000)
                        reference=int(message.data.get('source_ts_ns') or message.data.get('wallclock_ts_ns')
                                      or message.data.get('receive_ts_ns') or 0)
                        if reference<=current_cutoff:
                            # The message stays in JetStream. Historical
                            # replay requires restoring exact remote coverage;
                            # it must never become a fresh canonical row after
                            # its local index has been safely retired.
                            raise RuntimeError('crypto_historical_replay_requires_index_restore')
                    if self.writer.add(message.data,stream_name=message.stream_name,stream_generation=message.stream_generation,stream_sequence=message.stream_sequence,event_id=message.event_id):
                        self._pending[message.commit_id]=message
                        if message.event_id: self._pending_event_ids[message.event_id]=message.commit_id
                        stats['messages']+=1
                    else:
                        # only trade/book subjects can reach this durable stream
                        try:
                            await message.ack()
                        except Exception:
                            logger.warning("failed to ACK ignored market event; waiting for redelivery",exc_info=True)
                if (len(self._pending_alias_sizes)>=self._pending_alias_limit
                    or self._pending_alias_bytes>=self._pending_alias_bytes_limit):
                    await self._flush(stats,"all");last_flush=time.monotonic()
                rss=self._rss()
                cgroup_used,cgroup_limit=self._cgroup_memory()
                rss_flush_threshold,rss_pause_threshold=self._rss_thresholds(cgroup_limit)
                # High-volume partitions can keep the size-pressure branch
                # active indefinitely. Scan age independently so a sparse
                # funding/OI partition cannot pin the JetStream ACK floor.
                now=time.monotonic()
                if self._pending and now-last_aged_scan>=aged_scan_interval:
                    await self._flush(stats,"aged");last_aged_scan=now
                if cgroup_limit and cgroup_used>=int(cgroup_limit*.75):
                    await self._flush(stats,"largest",self.writer.buffer_bytes//2); last_flush=time.monotonic(); await asyncio.sleep(0.25)
                    after,_=self._cgroup_memory()
                    if after>=int(cgroup_limit*.9):
                        await self._flush(stats,"all")
                        after,_=self._cgroup_memory()
                        if after>=int(cgroup_limit*.9):raise RuntimeError("storage cgroup remains above 90% after forced flush")
                    continue
                if rss>=rss_pause_threshold:
                    await self._flush(stats,"largest",self.writer.buffer_bytes//2); await asyncio.sleep(0.25)
                    if self._rss()>=rss_pause_threshold:
                        await self._flush(stats,"all")
                        if self._rss()>=rss_pause_threshold:raise RuntimeError("storage RSS remains above pause threshold after forced flush")
                    continue
                if ((rss>=rss_flush_threshold and self.writer.buffer_bytes>=self._rss_flush_buffer_floor)
                    or len(self._pending)>=self._flush_pending_limit
                    or self.writer.buffer_bytes>=self._flush_byte_limit):
                    await self._flush(stats,"largest",self.writer.buffer_bytes//2); last_flush=time.monotonic()
                elif self._pending and time.monotonic()-last_flush>=self._flush_seconds:
                    await self._flush(stats,"aged");last_flush=time.monotonic()
                else: await self._flush(stats,"due")
                if self._pending and time.monotonic()-last_progress>=30:
                    # Never issue thousands of concurrent consumer-control
                    # requests. Regular bounded flushes keep most deliveries
                    # far below AckWait; this only protects a small sample of
                    # an unusually slow batch.
                    await asyncio.gather(*(m.in_progress() for m in list(self._pending.values())[:256]),return_exceptions=True); last_progress=time.monotonic()
        except Exception:
            logger.exception('storage worker failed before commit; requesting redelivery')
            await asyncio.gather(*(m.nak(5) for m in self._pending.values()),return_exceptions=True)
            raise
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task,return_exceptions=True)
            try: await self._flush(stats,"all")
            finally:
                if self._pending: await asyncio.gather(*(m.nak(5) for m in self._pending.values()),return_exceptions=True)
                await self.bus.close()
        return stats
