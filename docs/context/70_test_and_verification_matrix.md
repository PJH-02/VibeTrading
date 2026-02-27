# 70) Test and Verification Matrix

## Current observed test surface

## Existing automated tests
- v2 has 5 pytest modules (8 tests total) focused on:
  - parquet adapter behavior (`test_parquet_source.py`)
  - policy merge logic (`test_policy_merge.py`)
  - runner flow (`test_runner_flow.py`)
  - strategy bundle schema and sandbox checks (`test_strategy_bundle_schema.py`, `test_strategy_forbidden_imports.py`) (`vibetrading_V2/tests/*.py`).
- v1 has no standard pytest test suite discovered by file pattern; validation is primarily script-driven (`vibetrading_V1/scripts/validate_system.py:37-269`).

## Deterministic backtest verification (status and recommended runbook)

| Area | Current status | Evidence | Gap | Recommended command/runbook |
|---|---|---|---|---|
| Data fixture determinism | Partial | sample parquet exists and has fixed rows/columns (`data/curated/BTC-USD.parquet`) | only one tiny fixture; no fixture catalog/versioning | keep canonical fixtures under `data/curated/<symbol>.parquet` + checksum manifest |
| Backtest execution determinism | Partial | backtest runner exists and processes sorted bars (`cli/backtest.py:30-50`, `vibetrading_V2/data/parquet_source.py:56-77`) | no artifact writer for orders/positions/pnl/risk | add deterministic artifact sink (jsonl/parquet) with stable ordering |
| Deterministic assertion | Missing | `RunnerResult` returns counters/fills only (`vibetrading_V2/runner/runtime.py:23-30`) | no hash/diff assertion of run outputs | compute `sha256` over canonicalized artifacts and compare across reruns |
| Tolerances policy | Missing | no tolerance config found in v2 runner/tests | floating-point tolerance policy undefined | define exact hash for integer/decimal-serialized outputs; documented epsilon only where unavoidable |

### Suggested deterministic run steps (proposed)
1. `PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q vibetrading_V2/tests`
2. Run backtest CLI twice with same inputs.
3. Persist ordered artifacts: orders, positions, pnl, risk snapshots.
4. `sha256sum` artifacts from run-1 and run-2 and assert equality.

## Unit/contract/smoke matrix by layer

| Layer | Current coverage | Evidence | Missing coverage |
|---|---|---|---|
| Core contracts/types | Moderate (schema + policy merge + sandbox) | `test_policy_merge.py`, `test_strategy_bundle_schema.py`, `test_strategy_forbidden_imports.py` | no explicit tests for order lifecycle/idempotency (not implemented) |
| Runner orchestration | Basic | `test_runner_flow.py` validates bars->orders->fills loop | no tests for kill-switch, retries, live safety gates |
| Data adapter | Basic | `test_parquet_source.py` checks sorting + required columns | no duplicate/missing/out-of-order policy tests |
| Ports contract tests (fake adapters) | Partial | `_DataSource`, `_Execution`, `_Clock`, `_Logger` doubles in `test_runner_flow.py:11-61` | no formal protocol contract test suite per adapter |
| Live/paper adapter smoke tests | Missing | no dedicated tests for `execution/live_adapter.py` or `execution/paper_adapter.py` behavior under real API modes | add smoke tests guarded by env gates |
| v1 legacy integration validation | Script-only | `vibetrading_V1/scripts/validate_system.py` E2E checks | no CI-managed pytest suite in v1 |

## Live smoke test policy (required-safe)

### Required gate
- Live smoke execution must be blocked unless both env vars are present: `LIVE_API=1` and `CONFIRM_LIVE=YES` (`AGENTS.md:58-64,81-84`).

### Minimal safe smoke scope (proposed)
1. connect/auth only
2. fetch one recent 1m bar
3. place one tiny sandbox/paper-capable order
4. cancel order
5. disconnect

### Current implementation status
- No code-level check for `LIVE_API`/`CONFIRM_LIVE` found in v1/v2/cli code search.
- `cli/live.py` starts live runtime unconditionally (`cli/live.py:30-47`).

## CI recommendations

## Observed CI state
- `.github/workflows` directory not found.

## Recommended CI split
1. **Default CI (always on):** deterministic/unit tests only
   - `PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q vibetrading_V2/tests`
   - optional static checks (ruff/mypy) once environment is normalized.
2. **Manual secured CI (protected):** live smoke tests
   - requires secured secrets + manual dispatch + env gate assertion.

## Verification command outcomes observed in this audit
- `pytest --collect-only -q` failed due external pytest plugin dependency (`pytest_asyncio` missing in ambient environment).
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest --collect-only -q` then failed import path without `PYTHONPATH=.`.
- `PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest --collect-only -q` succeeded with 8 collected tests.
- `PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q vibetrading_V2/tests` passed (8 passed).

## Evidence
- Commands run:
  - `find vibetrading_V1 -type f \( -name 'test_*.py' -o -name '*_test.py' -o -name 'conftest.py' \) | sort`
  - `find vibetrading_V2 -type f \( -name 'test_*.py' -o -name '*_test.py' -o -name 'conftest.py' \) | sort`
  - `pytest --collect-only -q`
  - `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest --collect-only -q`
  - `PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest --collect-only -q`
  - `PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q vibetrading_V2/tests`
  - `python - <<'PY' ... pandas.read_parquet('data/curated/BTC-USD.parquet') ... PY`
  - `if [ -d .github/workflows ]; then find .github/workflows -type f | sort; else echo '.github/workflows not found'; fi`
  - `rg -n "LIVE_API|CONFIRM_LIVE" -S vibetrading_V1 vibetrading_V2 cli AGENTS.md`
- Key output excerpts:
  - `PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest --collect-only -q` => `8 tests collected in 0.05s`.
  - `PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q vibetrading_V2/tests` => `8 passed in 0.03s`.
  - parquet fixture probe => `rows 2`, columns `['timestamp','open','high','low','close','volume']`.
  - CI probe => `.github/workflows not found`.
  - env-gate search => only `AGENTS.md` matches for `LIVE_API`/`CONFIRM_LIVE`.
- Concrete references:
  - `vibetrading_V2/tests/test_parquet_source.py:26-70`
  - `vibetrading_V2/tests/test_policy_merge.py:1-26`
  - `vibetrading_V2/tests/test_runner_flow.py:11-110`
  - `vibetrading_V2/tests/test_strategy_bundle_schema.py:11-67`
  - `vibetrading_V2/tests/test_strategy_forbidden_imports.py:11-37`
  - `vibetrading_V2/data/parquet_source.py:34-77`
  - `vibetrading_V2/runner/runtime.py:23-30,53-94`
  - `cli/backtest.py:30-50`
  - `cli/live.py:30-47`
  - `vibetrading_V1/scripts/validate_system.py:37-269`
  - `AGENTS.md:58-64,77-84`
