# VN08 repair2 — Remove object-identity test workaround

Final allowed repair delegation for VN08. Parent VN08 contract remains binding. Repair1 fixed cursor/record/inventory bounds and monotonicity; its original14 reviewer probes pass. A15th independent probe exposes an object-identity workaround.

## Allowed writes (only)
- src/vibetrading/market_spool.py
- tests/refactoring/test_retained_spool.py (NEW test file introduced by this VN08 worker, not a historical user/Golden test)
All other source/tests/docs read-only. Do not change existing tests from base e934af7, metadata, schemas, old APIs, configuration, brokers or data. No network, git mutations, packages or subagents.
Read only RetainedPrefix.__init__ and test_consumer_cursor_identity_and_bound_checks, plus the reviewer test_same_confirmed_position... in docs/tickets/VN08-reviewer-counterexamples.py.

## Required fix
Confirmed cursor positions are values, not Python memory identities. These are semantically identical and both VALID:
(a) (SpoolCursor(P,I,1), SpoolCursor(P,I,1))
(b) cursor=SpoolCursor(P,I,1); (cursor,cursor)
Both represent two declared consumers at the same confirmed position. Serialized proofs cannot encode Python pointer identity.
Remove seen_objects/id(cursor) rejection in RetainedPrefix.__init__. Keep cursor tuple count1..17, identities, base<=cursor<=durable and all six repaired integrity/capacity checks.

The NEW test_consumer_cursor_identity_and_bound_checks incorrectly expected [same_cursor]*2 to be invalid. Correct only that new assertion to capture successfully and assert two consumer cursor entries at sequence1 (keep all its other invalid cases and >=18 rejection). This is an explicitly authorized correction of the worker's new test to the binding contract, NOT permission to weaken any historical test or production check. Do not keep a memory-id hack just to satisfy this incorrect new assertion. No other refactoring.

## Exact command
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_retained_spool.py tests/refactoring/test_retained_storage.py tests/refactoring/test_spool_recovery.py tests/refactoring/test_local_storage.py tests/refactoring/test_checkpoint_startup.py tests/refactoring/test_local_pipeline.py tests/refactoring/test_local_engine.py docs/tickets/VN08-reviewer-counterexamples.py

Run once after the two small fixes, return actual exits and STOP. No exploration or self-approval. Codex will independently run the15 reviewer probes and regressions.
