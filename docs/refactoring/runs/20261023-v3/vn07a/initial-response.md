Implemented VN07A within the allowed scope.

## Files changed
- `src/vibetrading/portfolio_ledger.py`
  - Added read-only `has_strategy_dispatch(strategy)`.
  - Checks owner/open state, PAPER mode, and bounded strategy identity.
  - Streams only `strategy_event` receipts; validates claim schema/fingerprint and fails closed on malformed evidence.
  - Treats `@registration` as harmless; any other event ID, including `@action:`, under any config version, counts.
  - Performs no writes.
- `src/vibetrading/strategy_runtime.py`
  - PAPER registration queries all bound accounts before registration claims or plugin creation.
  - Evidence in any account blocks the job with `strategy_state_restore_unverified`.
  - Permission-denied jobs remain `paper_permission_missing`.
  - Added persistent `restart_blocked` job flag; PAPER dispatch is fenced even after halt/pause/resume.
  - Normal pause/halt behavior preserved for non-restart-blocked jobs.
  - Replay may still warm callbacks to preserve existing replay semantics, but cannot claim/action/advance Paper state or clear the PAPER barrier.
  - `restart_evidence` now includes `strategy_state_recovery: UNVERIFIED` and `blocked_strategies`.
- `tests/refactoring/test_strategy_restart_barrier.py`
  - New regression/fault tests covering restart fencing, fresh runs, registration-only reopen, other strategies, config changes, action-only receipts, multi-account evidence, malformed receipts, read failures, replay, and pause/resume/halt bypass.

## Commands and actual results
1. Before source edits, focused new tests:
   ```bash
   python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_strategy_restart_barrier.py
   ```
   Result: **9 failed, 2 passed**. The primary failure demonstrated the missing NEW-event restart case:
   `assert len(observed) == 1` failed with `assert 2 == 1`, showing a new event invoked the callback after restart.

2. After implementation, focused new tests:
   ```bash
   python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_strategy_restart_barrier.py
   ```
   Result: **11 passed**.

3. Required acceptance command:
   ```bash
   python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_paper_execution.py
   ```
   Result: **63 passed in 0.54s**.

## Limitations
- No explicit strategy checkpoint/restore exists; this change only fences unverified restarts.
- Replay snapshot dispatch may still warm callbacks for a restart-blocked job, preserving existing replay tests, but it cannot submit durable claims/actions, advance Paper matching, or clear the PAPER barrier.
- No production readiness claim; `production_gate` remains `BLOCKED` and `continuous_input_cursor` remains `MISSING_EVIDENCE`.
- No Git commits/pushes, metadata changes, service connections, or new tables/journals were introduced.
