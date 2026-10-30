# VN08 repair1 — Six independent failures; exact bounded fixes

Parent docs/tickets/VN08-retained-prefix-reader.md remains binding. Original selected260 tests passed, independent review268 PASS/6 FAIL. Initial worker intentionally stopped with exit-15 after its tests so these actual defects can be repaired. Codex accepts independently; no source takeover.

## Scope
MODIFY ONLY src/vibetrading/market_spool.py. All other source/test/docs read-only. Exact failing tests are docs/tickets/VN08-reviewer-counterexamples.py, provided read-only. Do not alter any tests or schema just to pass. No metadata, installs, deletion, accounts, network, git mutations or subagents. Read relevant functions and tests directly; no exploration.

## Required small corrections
1. Two consumers legitimately have identical confirmed cursor positions. Remove duplicate-position rejection; preserve both declared cursors in canonical proof. Still enforce tuple length1..17 and identity/bounds.
2. Confirmed consumer cursor must satisfy base<=sequence<=captured durable, both capture and proof deserialization. Future nonexistent progress must not be certified.
3. v2 incremental read must enforce durable-base<=self.max_records before admitting a batch, preserving sticky failure and no cursor advance. Forward producer growth cannot evade retained capacity by appending many records into one active segment.
4. v2 incremental durable must never regress from self._durable_sequence, even when the caller has only consumed an earlier cursor. Current check against initial proof/consumed cursor alone is insufficient.
5. All v2 scan/checkpoint/record absolute sequences must remain <=2**63-1 (nonbool, positive where appropriate), not just the proof fields and incremental read. scan_spool and iterator must reject overflowing checkpoint/complete frames before report/delivery; default v1 compatibility unchanged.
6. _inventory_retained currently accumulates all directory entries BEFORE checking the segment-count bound. Enforce max_records+1 while accumulating, prior to growing the dictionary past that limit; fail before consuming unbounded inventory. Correct its docstring to bounded O(segment-count) metadata, not constant memory. Keep finite streaming payload hashing; no whole-file/history buffering.

Do not add new layers or rewrite existing scanner/writer/storage. Keep the v1 branch identical. Keep new checksum-valid anchor fault, original source preservation, current storage attestation and exact cursor semantics.

## Exact verification
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_retained_spool.py tests/refactoring/test_retained_storage.py tests/refactoring/test_spool_recovery.py tests/refactoring/test_local_storage.py tests/refactoring/test_checkpoint_startup.py tests/refactoring/test_local_pipeline.py tests/refactoring/test_local_engine.py docs/tickets/VN08-reviewer-counterexamples.py

Fix production checks only, return actual commands/exits and stop. No self-acceptance. This is repair1 of maximum2 repair delegations for VN08.
