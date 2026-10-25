## TX04A completed

### Scoped diff
**`src/vibetrading/paper_execution.py`**
- Extended existing `_aligned` only:
  - Parses the authoritative `payload` / `payload.metadata` fail-closed.
  - Compares saved protective price to `payload.limit_price` using exact `Decimal` equality.
  - Compares saved expiry to `row["expiry_ns"]`.
  - Compares saved `post_only`, `reduce_only`, `time_in_force`, and `group_id` to the enriched payload metadata.
  - Rejects missing, malformed, and conflicting values with explicit `ValueError`s.
  - Enforces type checks (`bool` identity, `int` expiry, `str`/`None` group) so Python equality coercions cannot hide conflicts.
  - Preserved the existing leaves/strategy/instrument/side/rules comparison unchanged.
- Extended existing `_transition` only: added `self._aligned(staged)` after `operation(staged)` and before `_check_bound` / `save_paper_state` / commit.
- No other methods, versioning, migrations, defaults, economics, or fill identities changed.

**`tests/refactoring/test_tx04a_alignment.py`**
- Added constructor conflict tests for saved price, expiry, both boolean flags, time-in-force, and group ID, verifying failed construction leaves the latest DB state unchanged.
- Added malformed price/expiry/flag tests that would otherwise pass Python equality coercions.
- Added staged-mismatch tests across all aligned fields, verifying no save, no matcher publication, and unchanged DB/matcher memory.
- Added a valid restart test preserving supported flags, expiry, and group state.

### Exact command run
```bash
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_paper_execution.py tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_runtime_checkpoint_integration.py tests/refactoring/test_tx02a_owner_fence.py tests/refactoring/test_tx02b_attribution.py tests/refactoring/test_tx02_joint_gate.py tests/refactoring/test_tx04a_alignment.py
```

### Result
- Exit code: **0**
- **113 passed in 3.05s**

### Limits observed
No new matcher/queue/replay/version/migration/repair/default inference, no economic changes, no status edits, no commits/pushes/merges/deploys, no live credentials/orders/network. TX04B not touched.
