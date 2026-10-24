Implemented TX08A.

**Changed files (scoped):**
- `src/vibetrading/strategy_runtime.py` — added only the bounded guard inside the existing `register` method. Before any durable claim or plugin creation, it compares the incoming `account_notional_limit` and `account_capital_limit` against every existing `_jobs` registration sharing the exact account key. Any numeric mismatch (higher or lower) raises `ValueError`; equal Decimal values such as `100000` and `100000.00` pass. No helpers, imports, schema, or other methods changed.
- `tests/refactoring/test_tx08a_account_policy.py` — new scoped tests for higher/lower conflicts on both shared fields, no claim/create on rejection, equal numeric caps with independent strategy caps/funding buffers, paused/blocked/faulted jobs still enforcing the shared policy, distinct physical accounts with distinct caps, exact shared-budget admission without optimistic netting, over-budget sibling fault isolation, and denied sibling isolation.

**Exact command run:**
```bash
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_tx02a_owner_fence.py tests/refactoring/test_tx02b_attribution.py tests/refactoring/test_vn13_file_registration.py tests/refactoring/test_tx07a_fact_contract.py tests/refactoring/test_runtime_checkpoint_integration.py tests/refactoring/test_tx08a_account_policy.py
```

**Result:** exit `0` — `130 passed in 3.05s`.

No metadata, config, Golden, or unrelated product changes made.
