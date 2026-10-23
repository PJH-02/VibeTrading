Implemented the R04B repair.

## Changes

**`src/vibetrading/strategies/contract.py`**
- Replaced whole-relative-name validation with per-component validation.
- Qualified modules after explicit `strategies.` or `vibetrading.strategies.` prefixes may now contain multiple valid identifier components (e.g. `plugins.custom`, `kr.c3`).
- Bare single-name aliases still map to `vibetrading.strategies.<name>`.
- Preserved rejection of absolute paths, traversal, unrelated namespaces, `os.path`, and top-level `legacy`/`research`.
- Single modern loader implementation retained.

**`tests/refactoring/test_strategy_ownership.py`**
- Added parametrized coverage for synthetic nested modules `plugins.custom` and `kr.c3`.
- Injects synthetic modules via `monkeypatch.setitem(sys.modules, ...)`.
- Verifies legacy/canonical/slash/backslash `.py` spellings resolve to the same module identity.
- Verifies `create_strategy` is not called during loading, only on `.create()`.
- Existing tests and assertions were left unchanged.

## Test command and result

```
python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/refactoring/test_strategy_ownership.py tests/refactoring/test_strategy_dispatch.py tests/refactoring/test_strategy_runtime.py tests/test_fv_mm.py tests/test_mm_state_revision.py tests/refactoring/test_vnext_vn11.py tests/refactoring/test_vnext_vn12.py tests/refactoring/test_etf_dispatch_guards.py tests/test_etf_execution_update.py tests/test_dual_mock_and_assumed.py
```

Actual exit code: `0` — **211 passed in 6.19s**.

No git mutations, installs, metadata edits, or other file writes were performed. No acceptance claim made; Codex owns acceptance.
