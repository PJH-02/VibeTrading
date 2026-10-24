Implemented TX04B within the writable scope.

## Command run
```
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider \
  tests/refactoring/test_strategy_runtime.py \
  tests/refactoring/test_strategy_restart_barrier.py \
  tests/refactoring/test_local_engine.py \
  tests/refactoring/test_local_runtime.py \
  tests/refactoring/test_runtime_checkpoint_integration.py \
  tests/refactoring/test_paper_execution.py \
  tests/refactoring/test_tx02_joint_gate.py \
  tests/refactoring/test_tx04a_alignment.py \
  tests/refactoring/test_tx04b_continuous.py
```

Result: **exit 0, 138 passed** (baseline before change was 130 passed; 8 new tests added).

## Scoped diff
- `src/vibetrading/local_engine.py`
  - Added optional synchronous `on_tick` callback, validated with existing callback rules.
  - Watchdog now calls `check_health()`, validates a strict positive `int` epoch from `snapshot_clock`, then invokes `on_tick` using the existing `budgets.tick_seconds`; no new task/scheduler/budget.
  - Default behavior remains unchanged when `on_tick` is absent.
- `src/vibetrading/local_runtime.py`
  - `run_engine` binds `paper_runtime.advance` as the existing engine watchdog `on_tick` only when Paper is selected; otherwise `None`.
- `src/vibetrading/strategy_runtime.py`
  - `advance` now verifies the actual execution reference book’s receipt/native-source freshness via existing `_fresh_input` / `max_feed_age_ns`.
  - Added one narrow `_fence_feed` helper that invalidates the exact feed for admissions and calls existing `matcher.on_gap` from the matcher’s last durable clock before advancement.
  - `_market` follows the same fence-before-advance ordering for late stale/invalid/missing applied input; trades cannot clear the admission fence.
  - No new persisted state, schema, clock, queue, or matcher `_state` edits.
- `tests/refactoring/test_tx04b_continuous.py`
  - Added 8 tests covering watchdog epoch/callback/cleanup, real `run_engine` wiring, idle expiry, runtime-freshness IOC fencing, late stale event fencing, fresh-book-only reopening, independent second feed, and replay/recovery/permission no-new-risk fences.

## Limitations
Local cooperative Paper only; no live readiness, process isolation, network, deployment, metadata, or Golden changes. Existing replay/restore/permission and checkpoint regressions remain covered by the unchanged prior tests.
