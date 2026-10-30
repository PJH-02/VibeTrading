# VN07/segment-identity FINAL independent source audit
Read-onlyroleaudit, exactqualifiedopencode/nemotron-3-ultra-free, differentfamilyfromDeepSeekimplementer. ALLexactreviewedsourceunitsareINLINEbelow; no tools/explorationneeded. Provideconcretematerialfindings(function/lines/mechanism/repro), orNO_CRITICAL_FINDINGS. DoNOTinventbehaviorofomittedunchangedhelpers: identifyassumptionifneeded. No edits/tests/agents/externalcallsorclaimsworkaccepted; Codexaloneaccepts.
ScopeONLYlocal_runtime privatecheckpointreader/replaychanges; parentgeneralSpoolReader and_inventoryunchangedpredecessoracceptedcontracts. Existing scan_spool verifiesCRC/identity/contiguousdurableprefix and boundsreport.segment_count through_inventory max_records; _frames validatesheaders/frameCRC/sequence, consumesprovidedopened_handle; unchangedhelpercontracttrustedhere. Noaccount/strategy/GC/schemachanges, parentVN07stillincomplete.
Newguard: actualopeneddescriptorfstat pinsmetadataforallfrozensegments BEFORECverification/virginrestore, includingC0. Everylateropen andpre/postrestoreinventoryvalidate againstpins. Sealedimmutable; activecangrowandsealSAMEinode, neverbecomeactiveafteranysealedobservation. FrozenHcomesfrominitialdurablescan; futureappenddoesnotreplay. Missingretainedprefixiscorruption; no retained-baseGCfallback. Postrestorefailureclosesengine, no executionorders/readiness. NotadversarialhostFSsandbox/crossproduceratomictransaction. Verifyresourcebounds/closures, timing/cursorlimitsandactualhandleauthority.
Independent183scopedtestsPASSincludingdeterministicC/newestunopenedseg/C0/latersegmentreplacement, missingprefix, sealedmtimechange, activegrow/sameinodesealpositive, plusregressionactive->sealed->activerenamebetweensealedobservationandsuffixopen. Lastconditionnowchecksnumberinself._sealed_observations, notcapturedmtimeonly. PriorGeminiauditincorrectlypassedoldcodeonthatcaseandisNOTfinalacceptanceevidence. Reviewactualcurrentcodebelowratherthanassumeoldverdict.

BEGIN EXACT SOURCE
FILE vibetrading/local_runtime.py SHA256 45d850cc31ef1a696cba5ccecb9fc36b902d25de95dae4d3c1fadd48135bf740

81: def _scan(item):
82:     return scan_spool(item.spool_root,producer_id=item.producer,incarnation=item.incarnation,
83:                       **item.limits.reader_kwargs)

86: @dataclass(frozen=True)
87: class _SegmentProof:
88:     dev: int
89:     ino: int
90:     size: int
91:     mtime_ns: int | None
92:     active: bool

95: def _open_checkpoint_segment(root,number):
96:     sealed=Path(root)/f"{number:020d}.sealed"
97:     active=Path(root)/f"{number:020d}.active"
98:     # Reuse the bounded active/sealed rename-open sequence. Descriptor
99:     # metadata, not a path re-stat, authorizes the frozen segment.
100:     for path,is_sealed in ((sealed,True),(active,False),(sealed,True)):
101:         try:
102:             return path,is_sealed,path.open("rb")
103:         except FileNotFoundError:
104:             continue
105:     raise SpoolCorruption("checkpoint spool segment is missing")

108: def _capture_segment_proofs(root,report):
109:     """Pin only metadata for the frozen scan inventory; no payloads/index."""
110:     proofs={}
111:     for number in range(1,report.segment_count+1):
112:         _,sealed,opened=_open_checkpoint_segment(root,number)
113:         try:
114:             stat=os.fstat(opened.fileno())
115:             was_active=number==report.active_segment_id
116:             if not was_active and not sealed:
117:                 raise SpoolCorruption("checkpoint spool sealed segment regressed")
118:             proofs[number]=_SegmentProof(stat.st_dev,stat.st_ino,stat.st_size,
119:                 stat.st_mtime_ns if sealed else None,was_active)
120:         finally:
121:             opened.close()
122:     return proofs

125: class _CheckpointSpoolReader(SpoolReader):
126:     def __init__(self,root,producer_id,incarnation,*,proofs,**kwargs):
127:         # Proofs precede the superclass constructor, which may open segments
128:         # while locating C.
129:         self._proofs=dict(proofs)
130:         self._sealed_observations={number:(proof.size,proof.mtime_ns)
131:             for number,proof in self._proofs.items() if proof.mtime_ns is not None}
132:         super().__init__(root,producer_id,incarnation,**kwargs)
133:
134:     def _open_segment(self,number):
135:         proof=self._proofs.get(number)
136:         if proof is None:
137:             raise SpoolCorruption("checkpoint spool segment is not in frozen inventory")
138:         path,sealed,opened=super()._open_segment(number)
139:         try:
140:             stat=os.fstat(opened.fileno())
141:             if (stat.st_dev,stat.st_ino)!=(proof.dev,proof.ino):
142:                 raise SpoolCorruption("checkpoint spool segment identity changed")
143:             if proof.active:
144:                 # The active prefix may legitimately grow and later be sealed
145:                 # under the same inode. Never require active size/mtime equality.
146:                 if stat.st_size<proof.size:
147:                     raise SpoolCorruption("checkpoint spool active segment shrank")
148:                 if sealed:
149:                     signature=(stat.st_size,stat.st_mtime_ns)
150:                     observed=self._sealed_observations.get(number)
151:                     if observed is None:
152:                         self._sealed_observations[number]=signature
153:                     elif signature!=observed:
154:                         raise SpoolCorruption("checkpoint spool sealed segment changed")
155:                 elif number in self._sealed_observations:
156:                     raise SpoolCorruption("checkpoint spool sealed segment regressed")
157:             else:
158:                 if not sealed:
159:                     raise SpoolCorruption("checkpoint spool sealed segment regressed")
160:                 if (stat.st_size,stat.st_mtime_ns)!=(proof.size,proof.mtime_ns):
161:                     raise SpoolCorruption("checkpoint spool sealed segment changed")
162:             return path,sealed,opened
163:         except BaseException:
164:             opened.close()
165:             raise
166:
167:     def validate_inventory(self):
168:         for number in sorted(self._proofs):
169:             _,_,opened=self._open_segment(number)
170:             opened.close()

261: async def replay_checkpoint_startup(engine,producers,*,codec,payload,expected_engine_incarnation,apply_order=None):
262:     """Restore market state only, with independently checked C and fixed H.
263:
264:     Multiple nonempty suffixes require an exact durable apply-order proof.
265:     This function never arms initial resume or grants execution readiness.
266:     """
267:     if not isinstance(producers,(tuple,list)) or not 0<len(producers)<=engine.budgets.max_producers:
268:         raise ValueError('Checkpoint startup requires a bounded producer inventory')
269:     metadata=engine.inspect_checkpoint(codec,payload)
270:     checkpoints={item.cursor.producer:item for item in metadata.producers}
271:     items={item.producer:item for item in producers}
272:     if len(items)!=len(producers) or set(items)!=set(checkpoints):
273:         raise RuntimeError('checkpoint producer inventory mismatch')
274:     highwaters={}
275:     verified={}
276:     readers={}
277:     for producer,item in items.items():
278:         point=checkpoints[producer]
279:         report=await asyncio.to_thread(_scan,item)
280:         if report.uncertain:
281:             raise RuntimeError('uncertain producer tail requires explicit repair')
282:         if (point.cursor.incarnation!=item.incarnation
283:                 or point.cursor.sequence>report.durable_sequence):
284:             raise RuntimeError('checkpoint cursor exceeds matching durable producer prefix')
285:         highwaters[producer]=EngineCursor(producer,item.incarnation,report.durable_sequence)
286:         proofs=await asyncio.to_thread(_capture_segment_proofs,item.spool_root,report)
287:         after=(SpoolCursor(item.producer,item.incarnation,point.cursor.sequence-1)
288:                if point.cursor.sequence else None)
289:         reader=await asyncio.to_thread(lambda item=item,proofs=proofs,after=after:_CheckpointSpoolReader(
290:             item.spool_root,item.producer,item.incarnation,proofs=proofs,after=after,
291:             **item.limits.reader_kwargs))
292:         readers[producer]=reader
293:         if point.cursor.sequence==0:
294:             digest=hashlib.sha256(b'').hexdigest()
295:         else:
296:             rows=await asyncio.to_thread(reader.read_batch,max_records=1,max_bytes=item.pipeline.max_bytes)
297:             if len(rows)!=1 or rows[0].cursor.sequence!=point.cursor.sequence:
298:                 raise RuntimeError('checkpoint original cursor is not retained')
299:             digest=hashlib.sha256(rows[0].payload).hexdigest()
300:         if digest!=point.digest:
301:             raise RuntimeError('checkpoint original cursor digest mismatch')
302:         verified[producer]=AppliedReceipt(point.cursor,digest)
303:     if apply_order is not None:
304:         if type(apply_order) is not ApplyOrder:
305:             raise ValueError('checkpoint startup requires an apply order proof')
306:         apply_order.validate(payload,[(point.cursor,highwaters[p]) for p,point in checkpoints.items()])
307:     elif sum(highwaters[p].sequence>checkpoints[p].cursor.sequence for p in items)>1:
308:         raise RuntimeError('multiple checkpoint suffixes require a verified global apply order')
309:     # The frozen inventory is rechecked before installing target state. A
310:     # pre-install failure leaves the still-virgin engine to its caller.
311:     for reader in readers.values():
312:         await asyncio.to_thread(reader.validate_inventory)
313:     handoffs=engine.restore_virgin(codec,payload,highwaters,
314:         verified_cursors=verified,expected_engine_incarnation=expected_engine_incarnation,apply_order=apply_order)
315:     try:
316:         # A restore hook may have replaced C=0 or an unopened tail. Recheck
317:         # before applying any frame; later opens still check their descriptor.
318:         for reader in readers.values():
319:             await asyncio.to_thread(reader.validate_inventory)
320:         if apply_order is not None:
321:             for receipt in apply_order.receipts:
322:                 c=receipt.cursor
323:                 item=items[c.producer]
324:                 reader=readers.get(c.producer)
325:                 if reader is None:
326:                     raise RuntimeError('checkpoint reader authority missing')
327:                 rows=await asyncio.to_thread(reader.read_batch,max_records=1,max_bytes=item.pipeline.max_bytes)
328:                 if len(rows)!=1:
329:                     raise RuntimeError('durable checkpoint suffix is incomplete')
330:                 row=rows[0]
331:                 frame=RecoveryFrame(EngineCursor(row.cursor.producer,row.cursor.incarnation,row.cursor.sequence),row.payload)
332:                 if AppliedReceipt(frame.cursor,frame.digest)!=receipt:
333:                     raise RuntimeError('checkpoint apply order payload mismatch')
334:                 engine.apply_replay(frame)
335:             return handoffs
336:         for initial,high in handoffs:
337:             if initial==high:continue
338:             item=items[initial.producer]
339:             reader=readers.get(initial.producer)
340:             if reader is None:
341:                 raise RuntimeError('checkpoint reader authority missing')
342:             while reader.cursor.sequence<high.sequence:
343:                 rows=await asyncio.to_thread(reader.read_batch,
344:                     max_records=min(item.pipeline.batch_records,high.sequence-reader.cursor.sequence),
345:                     max_bytes=item.pipeline.max_bytes)
346:                 if not rows:raise RuntimeError('durable checkpoint suffix is incomplete')
347:                 for row in rows:
348:                     engine.apply_replay(RecoveryFrame(EngineCursor(row.cursor.producer,
349:                         row.cursor.incarnation,row.cursor.sequence),row.payload))
350:         return handoffs
351:     except BaseException:
352:         # A partially restored candidate is never reused as a virgin fallback.
353:         await engine.close()
354:         raise

FILE vibetrading/market_spool.py SHA256 0c735dbd5f03c9e915a18cc4b2c6a3056b8bff23955dc75fc58892082735a94b

57: @dataclass(frozen=True)
58: class SpoolScan:
59:     producer_id: str
60:     incarnation: str
61:     last_sequence: int
62:     durable_sequence: int
63:     record_count: int
64:     disk_bytes: int
65:     segment_count: int
66:     active_segment_id: int | None
67:     active_records: int
68:     active_bytes: int
69:     partial_tail_bytes: int
70:     partial_tail_offset: int | None
71:     uncertain_from_sequence: int | None
72:     uncertain_to_sequence: int | None
73:     uncertainty_reason: str | None
74:     directory_fsync_supported: bool
75:
76:     @property
77:     def uncertain(self) -> bool:
78:         return self.uncertainty_reason is not None
79:
80:     @property
81:     def durable_cursor(self) -> SpoolCursor:
82:         return SpoolCursor(self.producer_id, self.incarnation, self.durable_sequence)

126: def _inventory(root: Path, max_disk_bytes: int, max_records: int) -> tuple[int, int, int | None, bool]:
127:     """Constant-memory directory inventory; no list of historical records."""
128:     total = count = largest = 0
129:     active = None
130:     checkpoint_temp = False
131:     if not root.exists():
132:         return 0, 0, None, False
133:     with os.scandir(root) as entries:
134:         for entry in entries:
135:             if not entry.is_file(follow_symlinks=False):
136:                 raise SpoolCorruption(f"unexpected spool entry: {entry.name}")
137:             total += entry.stat(follow_symlinks=False).st_size
138:             if total > max_disk_bytes:
139:                 raise SpoolError("spool disk byte limit exceeded")
140:             if entry.name in {"writer.lock", _CHECKPOINT, _CHECKPOINT_TEMP}:
141:                 checkpoint_temp |= entry.name == _CHECKPOINT_TEMP
142:                 continue
143:             match = _SEGMENT.fullmatch(entry.name)
144:             if match is None:
145:                 raise SpoolCorruption(f"unknown spool file: {entry.name}")
146:             number = int(match[1])
147:             if number <= 0 or number > max_records + 1:
148:                 raise SpoolCorruption("invalid or unbounded segment number")
149:             count += 1
150:             largest = max(largest, number)
151:             if match[2] == "active":
152:                 if active is not None:
153:                     raise SpoolCorruption("multiple active segments")
154:                 active = number
155:     if count != largest or (active is not None and active != largest):
156:         raise SpoolCorruption("missing, duplicate or non-final active segment")
157:     return total, largest, active, checkpoint_temp

317: class SpoolReader:
318:     """Bounded incremental reader with a private read cursor.
319:
320:     Startup verifies history and locates ``after`` in constant frame memory.
321:     Later batches read only the current segment/offset and new frames. This
322:     cursor is delivery progress, not a consumer ACK or deletion authority.
323:     The caller separately persists its confirmed consumer cursor after ACK.
324:     """
325:
326:     def __init__(self, root: Path, producer_id: str, incarnation: str, *, max_disk_bytes: int,
327:                  max_records: int, max_frame_bytes: int, max_header_bytes: int,
328:                  after: SpoolCursor | None = None) -> None:
329:         self.root = Path(root)
330:         self._producer_id = _identity(producer_id, "producer_id")
331:         self._incarnation = _identity(incarnation, "incarnation")
332:         for name, value in (("max_disk_bytes", max_disk_bytes), ("max_records", max_records),
333:                             ("max_frame_bytes", max_frame_bytes), ("max_header_bytes", max_header_bytes)):
334:             setattr(self, name, _positive(value, name))
335:         report = scan_spool(self.root, producer_id=producer_id, incarnation=incarnation,
336:                             max_disk_bytes=max_disk_bytes, max_records=max_records,
337:                             max_frame_bytes=max_frame_bytes, max_header_bytes=max_header_bytes)
338:         if after is not None and (after.producer_id != producer_id or after.incarnation != incarnation
339:                 or isinstance(after.sequence, bool) or not isinstance(after.sequence, int)
340:                 or not 0 <= after.sequence <= report.durable_sequence):
341:             raise SpoolCorruption("reader start cursor exceeds durable prefix or has unknown incarnation")
342:         self._sequence = after.sequence if after is not None else 0
343:         self._durable_sequence = report.durable_sequence
344:         self._segment_id = 1
345:         self._offset = 0
346:         self._identity: tuple[int, int] | None = None
347:         self._seen_size = 0
348:         self._sealed_signature: tuple[int, int] | None = None
349:         self._error: SpoolError | None = None
350:         if self._sequence:
351:             self._locate_start(report)
352:
353:     def _path(self, number: int) -> tuple[Path, bool]:
354:         sealed = self.root / f"{number:020d}.sealed"
355:         if sealed.exists():
356:             return sealed, True
357:         return self.root / f"{number:020d}.active", False
358:
359:     def _open_segment(self, number: int):
360:         sealed = self.root / f"{number:020d}.sealed"
361:         active = self.root / f"{number:020d}.active"
362:         # A segment transitions from active to sealed exactly once. Three
363:         # bounded opens cover that rename without reopening historical data.
364:         for path, is_sealed in ((sealed, True), (active, False), (sealed, True)):
365:             try:
366:                 return path, is_sealed, path.open("rb")
367:             except FileNotFoundError:
368:                 continue
369:         raise SpoolCorruption("incremental reader segment is missing")
370:
371:     def _locate_start(self, report: SpoolScan) -> None:
372:         # One startup-only streaming location pass. No per-record index or
373:         # historical payload list is retained for later polling.
374:         for number in range(1, report.segment_count + 1):
375:             path, sealed, opened = self._open_segment(number)
376:             iterator = _frames(path, number, self.producer_id, self.incarnation,
377:                                self.max_frame_bytes, self.max_header_bytes,
378:                                allow_partial=not sealed, opened_handle=opened)
379:             try:
380:                 for record in iterator:
381:                     if isinstance(record, tuple):
382:                         break
383:                     if record.cursor.sequence == self._sequence:
384:                         stat = os.fstat(opened.fileno())
385:                         self._segment_id = number
386:                         self._offset = record.offset + record.frame_bytes
387:                         self._identity = (stat.st_dev, stat.st_ino)
388:                         self._seen_size = stat.st_size
389:                         self._sealed_signature = (stat.st_size, stat.st_mtime_ns) if sealed else None
390:                         return
391:             finally:
392:                 iterator.close()
393:                 opened.close()
394:         raise SpoolCorruption("durable start cursor was not found")
395:
396:     @property
397:     def producer_id(self) -> str:
398:         return self._producer_id
399:
400:     @property
401:     def incarnation(self) -> str:
402:         return self._incarnation
403:
404:     @property
405:     def cursor(self) -> SpoolCursor:
406:         return SpoolCursor(self.producer_id, self.incarnation, self._sequence)
407:
408:     @property
409:     def durable_cursor(self) -> SpoolCursor:
410:         return SpoolCursor(self.producer_id, self.incarnation, self._durable_sequence)
411:
412:     def read_batch(self, *, max_records: int, max_bytes: int) -> tuple[SpoolRecord, ...]:
413:         _positive(max_records, "batch max_records"); _positive(max_bytes, "batch max_bytes")
414:         if self._error is not None:
415:             raise SpoolError("incremental reader is fail-closed") from self._error
416:         # Work on local cursor state. If any frame/integrity operation fails,
417:         # the entire undelivered batch keeps the caller-visible cursor intact.
418:         sequence, number, offset = self._sequence, self._segment_id, self._offset
419:         identity, seen_size, sealed_signature = self._identity, self._seen_size, self._sealed_signature
420:         records: list[SpoolRecord] = []
421:         retained_bytes = 0
422:         try:
423:             durable = _checkpoint(self.root, self.producer_id, self.incarnation, self.max_header_bytes)
424:             if durable < self._durable_sequence or durable < sequence:
425:                 raise SpoolCorruption("durable checkpoint regressed")
426:             _, segments, _, _ = _inventory(self.root, self.max_disk_bytes, self.max_records)
427:             # A concurrent writer may be preparing the next checkpoint. Its
428:             # old published checkpoint remains the only authorization boundary.
429:             if durable > self.max_records:
430:                 raise SpoolCorruption("durable checkpoint exceeds record limit")
431:             self._durable_sequence = durable
432:             while sequence < durable and len(records) < max_records:
433:                 if number > segments:
434:                     raise SpoolCorruption("durable prefix references a missing segment")
435:                 path, sealed, opened = self._open_segment(number)
436:                 reached_eof = False
437:                 try:
438:                     stat = os.fstat(opened.fileno())
439:                     current_identity = (stat.st_dev, stat.st_ino)
440:                     if identity is not None and current_identity != identity:
441:                         raise SpoolCorruption("incremental reader segment identity changed")
442:                     if stat.st_size < seen_size or stat.st_size < offset:
443:                         raise SpoolCorruption("incremental reader segment shrank")
444:                     signature = (stat.st_size, stat.st_mtime_ns) if sealed else None
445:                     if sealed_signature is not None and signature != sealed_signature:
446:                         raise SpoolCorruption("immutable sealed segment changed")
447:                     identity, seen_size = current_identity, stat.st_size
448:                     sealed_signature = signature
449:                     iterator = _frames(path, number, self.producer_id, self.incarnation,
450:                                        self.max_frame_bytes, self.max_header_bytes,
451:                                        allow_partial=False, start_offset=offset, opened_handle=opened)
452:                     try:
453:                         while sequence < durable and len(records) < max_records:
454:                             record = next(iterator, None)
455:                             if record is None:
456:                                 reached_eof = True
457:                                 break
458:                             if isinstance(record, tuple) or record.cursor.sequence != sequence + 1:
459:                                 raise SpoolCorruption("non-contiguous incremental durable prefix")
460:                             if retained_bytes + record.frame_bytes > max_bytes:
461:                                 if not records:
462:                                     raise SpoolRotateRequired("batch byte budget cannot hold first frame")
463:                                 break
464:                             records.append(record)
465:                             retained_bytes += record.frame_bytes
466:                             sequence = record.cursor.sequence
467:                             offset = record.offset + record.frame_bytes
468:                     finally:
469:                         iterator.close()
470:                 finally:
471:                     opened.close()
472:                 if reached_eof:
473:                     # Only a sealed file can have a successor. An active tail
474:                     # before the durable boundary is not a harmless EOF.
475:                     if not sealed:
476:                         renamed = self.root / f"{number:020d}.sealed"
477:                         if not renamed.exists() or (renamed.stat().st_dev, renamed.stat().st_ino) != identity:
478:                             raise SpoolCorruption("active file ends before durable boundary")
479:                     number += 1; offset = 0
480:                     identity = None; seen_size = 0; sealed_signature = None
481:                     continue
482:                 break
483:         except SpoolRotateRequired:
484:             raise
485:         except SpoolError as exc:
486:             self._error = exc
487:             raise
488:         except OSError as exc:
489:             self._error = SpoolCorruption("incremental spool read failed")
490:             raise self._error from exc
491:         self._sequence, self._segment_id, self._offset = sequence, number, offset
492:         self._identity, self._seen_size, self._sealed_signature = identity, seen_size, sealed_signature
493:         return tuple(records)
END SOURCE
