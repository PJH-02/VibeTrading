from __future__ import annotations

import asyncio
import logging
import time

from .bus import BusMessage, EventBus, NatsJetStreamBus
from .parquet_store import ParquetBatchWriter
from .settings import RuntimeSettings, get_settings
from .health import touch as health_touch

logger=logging.getLogger(__name__)


class StorageWorker:
    """ACK only delivery rows committed with a durable Parquet manifest."""
    def __init__(self,bus:EventBus|None=None,writer:ParquetBatchWriter|None=None,settings:RuntimeSettings|None=None,domain:str="market")->None:
        self.settings=settings or (writer.settings if writer is not None else get_settings()); self.bus=bus or NatsJetStreamBus(self.settings); self.writer=writer or ParquetBatchWriter(self.settings); self._pending:dict[str,BusMessage]={}; self._pending_event_ids:dict[str,str]={}
        if domain not in {"market", "crypto"}: raise ValueError("unsupported storage domain")
        self.domain=domain
        # A 64k JetStream window is for outage tolerance, not a target batch
        # size. Large batches make Parquet finalization and consumer control
        # traffic monopolize this small VM.
        configured=int(self.settings.env.get("STORAGE_MAX_PENDING_BEFORE_FLUSH","16384" if domain=="crypto" else "2048"))
        self._flush_pending_limit=max(64,min(configured,int(self.settings.nats.max_ack_pending*.8)))
        self._flush_byte_limit=max(1024*1024,int(self.settings.env.get("STORAGE_FLUSH_BUFFER_BYTES",str(64*1024*1024 if domain=="crypto" else 16*1024*1024))))
        self._flush_seconds=max(0.1,float(self.settings.env.get("STORAGE_FLUSH_SECONDS","60" if domain=="crypto" else "5")))

    async def _flush(self,stats:dict[str,int],force:bool=False)->None:
        paths=await asyncio.to_thread(self.writer.flush_all if force else self.writer.flush_due)
        if not paths:return
        stats['files']+=len(paths); stats['flushes']+=1
        committed=set(self.writer.last_committed_ids); self.writer.last_committed_ids.clear()
        ack=[]
        for cid in committed:
            msg=self._pending.pop(cid,None)
            if msg:
                if msg.event_id and self._pending_event_ids.get(msg.event_id)==cid: self._pending_event_ids.pop(msg.event_id,None)
                ack.append(msg)
        # ACKs are best effort after a durable manifest commit. Failed ACKs
        # safely redeliver and are deduplicated from parquet_delivery.
        results=[]
        for start in range(0,len(ack),256):
            results.extend(await asyncio.gather(*(m.ack() for m in ack[start:start+256]),return_exceptions=True))
        # A failed post-commit ACK is deliberately not NAKed: redelivery is
        # deduplicated from parquet_delivery and ACKed next time.
        stats['acked']+=sum(not isinstance(x,Exception) for x in results)

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
            # memory.current includes reclaimable Parquet page cache. Using it
            # as process pressure repeatedly restarted storage while RSS was
            # modest, allowing CRYPTO_DATA to fill and pause all collectors.
            inactive_file=0
            for line in (root/"memory.stat").read_text().splitlines():
                if line.startswith("inactive_file "):
                    inactive_file=int(line.split()[1]);break
            working_set=max(0,used-inactive_file)
            return working_set, int(limit_text) if limit_text != "max" else 0
        except (OSError,ValueError):return 0,0

    async def run(self,duration_sec:float|None=None)->dict[str,int]:
        subject,durable,component=("crypto.>","crypto-parquet-storage","crypto-storage") if self.domain=="crypto" else ("md.>","parquet-storage","storage")
        await self.bus.connect(); deadline=time.monotonic()+duration_sec if duration_sec else None; stats={'messages':0,'acked':0,'deduplicated':0,'flushes':0,'files':0}; batches=self.bus.subscribe_batches(subject,durable=durable,batch=128); last_progress=time.monotonic(); last_flush=time.monotonic()

        async def heartbeat() -> None:
            while True:
                cgroup_used,cgroup_limit=self._cgroup_memory()
                health_touch(self.settings,component,detail={"pending":len(self._pending),"buffer_bytes":self.writer.buffer_bytes,
                    "rss_bytes":self._rss(),"cgroup_used_bytes":cgroup_used,"cgroup_limit_bytes":cgroup_limit,**stats})
                await asyncio.sleep(20)

        heartbeat_task=asyncio.create_task(heartbeat(),name=f"{component}-heartbeat")
        try:
            while deadline is None or time.monotonic()<deadline:
                batch=await anext(batches)
                committed_deliveries = await asyncio.to_thread(
                    self.writer.store.committed_deliveries,
                    ((m.stream_name, m.stream_generation, m.stream_sequence) for m in batch if m.stream_sequence is not None),
                )
                candidate_event_ids = [
                    m.event_id for m in batch
                    if m.event_id and m.commit_id not in self._pending
                    and (m.stream_name, m.stream_generation, int(m.stream_sequence or 0)) not in committed_deliveries
                ]
                committed_event_ids = await asyncio.to_thread(self.writer.store.committed_event_ids, candidate_event_ids)
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
                    if message.event_id and (message.event_id in committed_event_ids or message.event_id in self._pending_event_ids):
                        try:
                            await message.ack()
                        except Exception:
                            logger.warning("failed to ACK deduplicated event; waiting for redelivery",exc_info=True)
                        stats['deduplicated']+=1; continue
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
                rss=self._rss()
                cgroup_used,cgroup_limit=self._cgroup_memory()
                if cgroup_limit and cgroup_used>=int(cgroup_limit*.75):
                    await self._flush(stats,True); last_flush=time.monotonic(); await asyncio.sleep(0.25)
                    after,_=self._cgroup_memory()
                    if after>=int(cgroup_limit*.9):
                        raise RuntimeError("storage cgroup remains above 90% after forced flush")
                    continue
                if rss>=self.settings.parquet.rss_pause_bytes:
                    await self._flush(stats,True); await asyncio.sleep(0.25)
                    if self._rss()>=self.settings.parquet.rss_pause_bytes:
                        raise RuntimeError("storage RSS remains above pause threshold after forced flush")
                    continue
                if (rss>=self.settings.parquet.rss_flush_bytes or len(self._pending)>=self._flush_pending_limit
                    or self.writer.buffer_bytes>=self._flush_byte_limit
                    or self._pending and time.monotonic()-last_flush>=self._flush_seconds):
                    await self._flush(stats,True); last_flush=time.monotonic()
                else: await self._flush(stats)
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
            try: await self._flush(stats,True)
            finally:
                if self._pending: await asyncio.gather(*(m.nak(5) for m in self._pending.values()),return_exceptions=True)
                await self.bus.close()
        return stats
