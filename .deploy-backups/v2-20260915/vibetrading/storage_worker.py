from __future__ import annotations

import asyncio
import logging
import time

from .bus import BusMessage, EventBus, NatsJetStreamBus
from .parquet_store import ParquetBatchWriter
from .settings import RuntimeSettings, get_settings

logger=logging.getLogger(__name__)


class StorageWorker:
    """ACK only delivery rows committed with a durable Parquet manifest."""
    def __init__(self,bus:EventBus|None=None,writer:ParquetBatchWriter|None=None,settings:RuntimeSettings|None=None)->None:
        self.settings=settings or (writer.settings if writer is not None else get_settings()); self.bus=bus or NatsJetStreamBus(self.settings); self.writer=writer or ParquetBatchWriter(self.settings); self._pending:dict[str,BusMessage]={}; self._pending_event_ids:dict[str,str]={}

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
        results=await asyncio.gather(*(m.ack() for m in ack),return_exceptions=True)
        # A failed post-commit ACK is deliberately not NAKed: redelivery is
        # deduplicated from parquet_delivery and ACKed next time.
        stats['acked']+=sum(not isinstance(x,Exception) for x in results)

    @staticmethod
    def _rss()->int:
        try:
            import psutil
            return int(psutil.Process().memory_info().rss)
        except Exception:return 0

    async def run(self,duration_sec:float|None=None)->dict[str,int]:
        await self.bus.connect(); deadline=time.monotonic()+duration_sec if duration_sec else None; stats={'messages':0,'acked':0,'deduplicated':0,'flushes':0,'files':0}; batches=self.bus.subscribe_batches('md.>',durable='parquet-storage',batch=512); last_progress=time.monotonic()
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
                        await message.ack(); stats['deduplicated']+=1; continue
                    # JetStream may redeliver before the long Parquet flush.
                    # Retain the newest delivery handle but never append the
                    # same stream generation/sequence to the in-memory batch.
                    if message.commit_id in self._pending:
                        self._pending[message.commit_id]=message; stats['deduplicated']+=1; continue
                    if message.event_id and (message.event_id in committed_event_ids or message.event_id in self._pending_event_ids):
                        await message.ack(); stats['deduplicated']+=1; continue
                    if self.writer.add(message.data,stream_name=message.stream_name,stream_generation=message.stream_generation,stream_sequence=message.stream_sequence,event_id=message.event_id):
                        self._pending[message.commit_id]=message
                        if message.event_id: self._pending_event_ids[message.event_id]=message.commit_id
                        stats['messages']+=1
                    else:
                        # only trade/book subjects can reach this durable stream
                        await message.ack()
                rss=self._rss()
                if rss>=self.settings.parquet.rss_pause_bytes:
                    await self._flush(stats,True); await asyncio.sleep(0.25); continue
                if rss>=self.settings.parquet.rss_flush_bytes or len(self._pending)>=int(self.settings.nats.max_ack_pending*.8): await self._flush(stats,True)
                else: await self._flush(stats)
                if self._pending and time.monotonic()-last_progress>=30:
                    await asyncio.gather(*(m.in_progress() for m in self._pending.values()),return_exceptions=True); last_progress=time.monotonic()
        except Exception:
            logger.exception('storage worker failed before commit; requesting redelivery')
            await asyncio.gather(*(m.nak(5) for m in self._pending.values()),return_exceptions=True)
            raise
        finally:
            try: await self._flush(stats,True)
            finally:
                if self._pending: await asyncio.gather(*(m.nak(5) for m in self._pending.values()),return_exceptions=True)
                await self.bus.close()
        return stats
