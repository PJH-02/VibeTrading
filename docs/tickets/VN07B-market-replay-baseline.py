async def replay_checkpoint_startup(engine,producers,*,codec,payload,expected_engine_incarnation,apply_order=None):
    """Restore market state only, with independently checked C and fixed H.

    Multiple nonempty suffixes require an exact durable apply-order proof.
    This function never arms initial resume or grants execution readiness.
    """
    if not isinstance(producers,(tuple,list)) or not 0<len(producers)<=engine.budgets.max_producers:
        raise ValueError('Checkpoint startup requires a bounded producer inventory')
    metadata=engine.inspect_checkpoint(codec,payload)
    checkpoints={item.cursor.producer:item for item in metadata.producers}
    items={item.producer:item for item in producers}
    if len(items)!=len(producers) or set(items)!=set(checkpoints):
        raise RuntimeError('checkpoint producer inventory mismatch')
    highwaters={}
    verified={}
    readers={}
    for producer,item in items.items():
        point=checkpoints[producer]
        report=await asyncio.to_thread(_scan,item)
        if report.uncertain:
            raise RuntimeError('uncertain producer tail requires explicit repair')
        if (point.cursor.incarnation!=item.incarnation
                or point.cursor.sequence>report.durable_sequence):
            raise RuntimeError('checkpoint cursor exceeds matching durable producer prefix')
        highwaters[producer]=EngineCursor(producer,item.incarnation,report.durable_sequence)
        proofs=await asyncio.to_thread(_capture_segment_proofs,item.spool_root,report)
        after=(SpoolCursor(item.producer,item.incarnation,point.cursor.sequence-1)
               if point.cursor.sequence else None)
        reader=await asyncio.to_thread(lambda item=item,proofs=proofs,after=after:_CheckpointSpoolReader(
            item.spool_root,item.producer,item.incarnation,proofs=proofs,after=after,
            **item.limits.reader_kwargs))
        readers[producer]=reader
        if point.cursor.sequence==0:
            digest=hashlib.sha256(b'').hexdigest()
        else:
            rows=await asyncio.to_thread(reader.read_batch,max_records=1,max_bytes=item.pipeline.max_bytes)
            if len(rows)!=1 or rows[0].cursor.sequence!=point.cursor.sequence:
                raise RuntimeError('checkpoint original cursor is not retained')
            digest=hashlib.sha256(rows[0].payload).hexdigest()
        if digest!=point.digest:
            raise RuntimeError('checkpoint original cursor digest mismatch')
        verified[producer]=AppliedReceipt(point.cursor,digest)
    if apply_order is not None:
        if type(apply_order) is not ApplyOrder:
            raise ValueError('checkpoint startup requires an apply order proof')
        apply_order.validate(payload,[(point.cursor,highwaters[p]) for p,point in checkpoints.items()])
    elif sum(highwaters[p].sequence>checkpoints[p].cursor.sequence for p in items)>1:
        raise RuntimeError('multiple checkpoint suffixes require a verified global apply order')
    # The frozen inventory is rechecked before installing target state. A
    # pre-install failure leaves the still-virgin engine to its caller.
    for reader in readers.values():
        await asyncio.to_thread(reader.validate_inventory)
    handoffs=engine.restore_virgin(codec,payload,highwaters,
        verified_cursors=verified,expected_engine_incarnation=expected_engine_incarnation,apply_order=apply_order)
    try:
        # A restore hook may have replaced C=0 or an unopened tail. Recheck
        # before applying any frame; later opens still check their descriptor.
        for reader in readers.values():
            await asyncio.to_thread(reader.validate_inventory)
        if apply_order is not None:
            for receipt in apply_order.receipts:
                c=receipt.cursor
                item=items[c.producer]
                reader=readers.get(c.producer)
                if reader is None:
                    raise RuntimeError('checkpoint reader authority missing')
                rows=await asyncio.to_thread(reader.read_batch,max_records=1,max_bytes=item.pipeline.max_bytes)
                if len(rows)!=1:
                    raise RuntimeError('durable checkpoint suffix is incomplete')
                row=rows[0]
                frame=RecoveryFrame(EngineCursor(row.cursor.producer,row.cursor.incarnation,row.cursor.sequence),row.payload)
                if AppliedReceipt(frame.cursor,frame.digest)!=receipt:
                    raise RuntimeError('checkpoint apply order payload mismatch')
                engine.apply_replay(frame)
            return handoffs
        for initial,high in handoffs:
            if initial==high:continue
            item=items[initial.producer]
            reader=readers.get(initial.producer)
            if reader is None:
                raise RuntimeError('checkpoint reader authority missing')
            while reader.cursor.sequence<high.sequence:
                rows=await asyncio.to_thread(reader.read_batch,
                    max_records=min(item.pipeline.batch_records,high.sequence-reader.cursor.sequence),
                    max_bytes=item.pipeline.max_bytes)
                if not rows:raise RuntimeError('durable checkpoint suffix is incomplete')
                for row in rows:
                    engine.apply_replay(RecoveryFrame(EngineCursor(row.cursor.producer,
                        row.cursor.incarnation,row.cursor.sequence),row.payload))
        return handoffs
    except BaseException:
        # A partially restored candidate is never reused as a virgin fallback.
        await engine.close()
        raise
