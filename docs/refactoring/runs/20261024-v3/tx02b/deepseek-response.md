Implemented TX02B within the authorized scope.

### Exact edits

**`src/vibetrading/portfolio_ledger.py`**
- `admit_order`: replaced the `strategy == "@physical"` check with:
  ```python
  if strategy.startswith("@"):
      raise ValueError("Reserved strategy namespace")
  ```
  This preserves the existing `@physical` error message while rejecting every reserved user strategy identity after bounded identity validation.
- `claim_strategy_event`: after the existing bounded identity loop, added:
  ```python
  if strategy.startswith("@"):
      raise ValueError("Reserved strategy namespace")
  ```
  This rejects reserved strategy IDs before evidence handling, identity derivation, or any receipt write. Event IDs such as `@registration` and `@action:new` remain allowed for normal strategies.

**`tests/refactoring/test_tx02b_attribution.py`** (new)
- Reserved identity spoofing via `admit_order` and `claim_strategy_event` creates no order, claim, reservation, balance, account-version, or strategy-cash change.
- Normal strategy registration/action claims and partial fills retain exact Decimal attribution across restart.
- Internal `@physical_netting_adjustment` and `@unallocated_funding` writers/history remain readable and unchanged across restart, while spoofing those strategy IDs is rejected.
- Opposite strategies preserve `physical == sum(virtual)`, gross pending risk, cancel/UNKNOWN/restart behavior, and exact Decimal values.

### Command and result

```bash
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_paper_execution.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_tx02a_owner_fence.py tests/refactoring/test_tx01_signal_binding.py tests/refactoring/test_tx00_toss_data_only.py tests/refactoring/test_kr_strategy_extraction.py tests/refactoring/test_tx02b_attribution.py
```

Actual exit code: `0`
Result: `290 passed in 13.66s`

### Limitations
- No migration, normalization, schema change, or reassignment of persisted strategy/category facts.
- Internal credit/funding writers remain directly available; the fence applies only to new user strategy identities entering `admit_order` and `claim_strategy_event`.
- No ETF/runtime/exchange/cloud behavior or existing Golden files were modified.
- No live orders, network activity, deployment, or destructive operations were performed.
