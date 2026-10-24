Implemented TX02A within the two file scope.

## Files changed

- `src/vibetrading/portfolio_ledger.py`
  - Added module-level `import os`.
  - PAPER mode now acquires a `ProcessLease.account(self.owner_root, scope.venue, scope.account, "paper")` physical authority before the existing scope lease, binding, or SQLite work.
  - Constructor unwinds DB, scope lease, and physical authority on partial acquisition/initialization failure.
  - Stores creator PID; `_check` now validates open/thread/PID plus both leases via `ProcessLease.check`.
  - Rechecks ownership immediately before outer `COMMIT` and nested `SAVEPOINT RELEASE`, using the existing rollback paths.
  - `close` refuses foreign PID/thread before touching DB/leases, survives lease-path loss, retains leases on DB-close failure, and releases scope then physical authority only after DB close succeeds.
  - Docstring clarified; no schema/economics/receipt/matcher changes.

- `tests/refactoring/test_tx02a_owner_fence.py`
  - Added 18 bounded tests covering shared physical account fences, alternate product/DB labels, PortfolioLedger vs ExecutionLedger, account/environment separation, PAPER identity validation, constructor/DB failure unwind, binding relocation, restart state, lost/closed lease blocking, outer/nested rollback, backend close retry, foreign-thread refusal, and fork refusal without unlocking the parent.

## Test command and result

```
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_vnext_vn10.py tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_paper_execution.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_tx01_signal_binding.py tests/refactoring/test_tx00_toss_data_only.py tests/refactoring/test_kr_strategy_extraction.py tests/refactoring/test_tx02a_owner_fence.py
```

Result: **307 passed**, exit code **0**.

## Limitations

This remains a cooperative local Paper fence on one protected shared root and local volume. It does not claim cross-host, hostile-directory, alternate-root, mixed-old-version, or live-exchange authority. No metadata, contracts, schedules, ETF/Mock/Mock2, ProcessLease, or exchange/cloud code was changed. No self-approval; independent acceptance remains with Codex.
