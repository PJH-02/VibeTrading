# TX02A — reuse the existing physical account fence for Paper

Base: `3de567280cdf20b6533b91373d4dd231d1e5ee72`. Prerequisites TX01 and VN10 accepted. Role `refactor`, qualified model `opencode/deepseek-v4.1-flash-free`. Codex owns interfaces/security and independent acceptance. Stop after this ticket; no other stage or recursive delegation.

## Concrete gap and minimal reuse decision

PortfolioLedger currently locks only scope.key (includes product) and checks only thread/open state. Different product labels can create two Paper writers on the same physical account/shared root; a lost/replaced lease or a forked inherited ledger can still use checked APIs. Existing ExecutionLedger and ETF already use ProcessLease.account, lease.check and safe close ordering. Reuse those primitives; do not create an owner service, new epoch engine, registry, exchange client, database schema or generic trading framework.

Exactly two writable files:

* `src/vibetrading/portfolio_ledger.py`: existing `__init__`, `_check`, `transaction`, `close` plus a small `os` import / docstring clarification only. All economics, schema, receipts, matcher and other functions unchanged.
* `tests/refactoring/test_tx02a_owner_fence.py`: new bounded tests, reuse current test_portfolio_ledger / test_paper_execution fixtures where practical.

Read first: portfolio_ledger.py, process_lease.py, execution_ledger.py (constructor/check/close pattern), tests/refactoring/test_vnext_vn10.py, tests/refactoring/test_portfolio_ledger.py. Other copied files are read-only dependency inputs; do not explore them unnecessarily. Existing tests, contracts, metadata, ETF/Mock/Mock2, process_lease.py, all exchange/cloud/strategy code, credentials, original runtime data and .git are read-only. No installs/network/accounts/orders/deploy/delete/push/merge. Shell is restricted to the exact pytest command; no ls/grep, separators or alternate processes except bounded synthetic child-process tests.

## Required implementation

1. Preserve the old scope lease path and permanent `.binding` contents/format exactly. In PAPER mode add `self._authority = ProcessLease.account(self.owner_root, scope.venue, scope.account, 'paper')`, before the old scope lease or any binding/SQLite work. The **same explicit protected shared owner_root** is the physical authority root, so this reuses the key also understood by ExecutionLedger(authority_root=owner_root, environment='paper'). Product labels must not split this physical key. Keep account/environment separation. Do not introduce a new root parameter, infer physical parent aliases, normalize IDs, or alter existing non-PAPER mode behavior; use no added physical authority for unselected sandbox/live modes.
2. Acquire physical then scope lease; unwind both on partial acquisition/constructor failure, closing an opened DB before releasing held leases. A refused competitor must not create a DB or permanent scope binding. Preserve original binding/path mismatch rejection and schema/state restart semantics. Do not remove stale lock or binding files or replace old data. Reuse ProcessLease.account canonical identity validation for selected PAPER.
3. Store the creator PID alongside the owning thread. `_check` must verify open/thread/PID and both existing scope lease and optional physical authority, using ProcessLease.check. Observed lease loss stays blocked via the existing sticky lost flag; putting the old inode back does not rearm the ledger. No new epoch counter is needed. Checked reads/mutations reject inherited fork use.
   Move the existing function-local `import os` (binding fsync block) to module level if using os earlier in `__init__`; do not leave a local-name shadow causing UnboundLocalError.
4. Recheck ownership immediately before outer COMMIT and nested SAVEPOINT RELEASE. If it was lost during a transaction, roll back through existing rollback code; never commit the pending data. No SQL/economic/receipt behavior changes for a valid owner, and no pretending lock-file checks atomically fence hostile directory writers or remote accounts.
5. `close` permits cleanup even after lease-path identity is lost, but refuses foreign PID/thread **before touching the DB or unlocking**. Do not call the full ownership `_check` as cleanup's prerequisite. Retain both leases if backend close fails; retry must work after restoration. Release old scope then physical authority only after DB close succeeds, mark closed at a safe point, preserve idempotent close. A fork child cannot unlock the parent. Do not change ProcessLease itself or ETF's proven close behavior.

## Acceptance / limits

* Same canonical physical account/environment + same shared root cannot admit two cooperating writers, even with alternate DB/product labels or PortfolioLedger vs existing ExecutionLedger. Different accounts and environments do not take a global lock.
* Existing `.portfolio-owners/<scopehash>.binding` prevents relocating a previously bound scope after close. Existing ledgers restart with latest balances/orders/UNKNOWN state, not a restored old DB. No schema migration.
* Failure while acquiring scope lease, connecting/opening/initializing DB releases new physical authority safely, so a subsequent valid constructor can succeed. Backend close failure retains exclusion until successful retry.
* Loss/replacement/close of either lease blocks checked API calls; loss during outer or nested transaction rolls back uncommitted facts. Cleanup after observed loss succeeds. Fork use/close is refused without releasing the parent's lock. Bounded synthetic subprocess tests must use timeout and reap children; no real account/process operations.
* C3/KP/KD Golden, Mock/Mock2, TOSS DATA_ONLY, exact Decimal/reservation/fill/dedup/matcher semantics stay unchanged. Only the selected local Paper fence is completed. Shared protected root + cooperating versions + local volume are prerequisites; no cross-host, hostile-directory, alternate-root global authority, mixed-old-version cross-product guarantee or live exchange readiness claim.
* Tests and source diff, actual worker/test exit, independent fault probes and final PASS/FAIL belong to Codex, not the worker. At most two repair delegations. Never edit metadata/test expectations to obtain PASS.

Exact command (pytest_asyncio explicitly loaded; offline guard is injected by host):

```
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_vnext_vn10.py tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_paper_execution.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_tx01_signal_binding.py tests/refactoring/test_tx00_toss_data_only.py tests/refactoring/test_kr_strategy_extraction.py tests/refactoring/test_tx02a_owner_fence.py
```

Return exact files, real test command/exit/count and limitations. No self-approval. Preserve all unrelated source/tests/status; no additional tickets.
