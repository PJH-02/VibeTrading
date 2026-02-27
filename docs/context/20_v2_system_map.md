# 20) V2 System Map (Observed Current State)

## Current state snapshot
- Package root is accessible as both `vibetrading_v2/` and `vibetrading_V2/` on this filesystem (same inode), and all runtime imports use `vibetrading_V2...` names (evidence: `ls -ldi vibetrading_V2 vibetrading_v2`; `cli/backtest.py:14-17`).
- Current V2 layout is:
  - `core/`, `data/`, `execution/`, `policies/`, `runner/`, `strategy/`, `strategies/`, `tests/` (evidence: `find vibetrading_v2 -maxdepth 1 -type d | sort`).
- This is **partially aligned** with the constitution intent, but it does **not** yet use explicit `ports/`, `adapters/`, `apps/` packages (evidence: missing dirs check; `vibetrading_v2/core/ports.py:10-28`; `vibetrading_v2/data/parquet_source.py:14-78`; `vibetrading_v2/execution/paper_adapter.py:11-25`).

## Module-by-module responsibilities, key symbols, dependencies

### Root and core
| Module | Responsibility | Key symbols | Dependencies observed |
|---|---|---|---|
| `vibetrading_v2/__init__.py` | Package marker for V2 scaffold. | module docstring | None (`vibetrading_v2/__init__.py:1`). |
| `vibetrading_v2/core/__init__.py` | Re-exports typed V2 errors. | `V2Error`, `StrategyLoadError`, `StrategySandboxError`, `StrategyValidationError`, aliases | Imports from `core.errors` only (`vibetrading_v2/core/__init__.py:3-21`). |
| `vibetrading_v2/core/errors.py` | Typed error hierarchy for strategy load/sandbox/schema flows. | `V2Error`, `StrategyLoadError`, `StrategySandboxError`, `StrategyValidationError`, alias classes | Pure Python exception classes only (`vibetrading_v2/core/errors.py:4-29`). |
| `vibetrading_v2/core/ports.py` | Current core protocols for data/execution/clock/logging. | `DataSource.get_bars`, `ExecutionPort.execute`, `Clock.now`, `Logger.info` | `typing.Protocol` + `core.types` (`vibetrading_v2/core/ports.py:5-28`). |
| `vibetrading_v2/core/types.py` | Core dataclasses for bars/order intents/fills. | `Bar`, `OrderIntent`, `Fill` | `dataclasses`, `datetime` only (`vibetrading_v2/core/types.py:5-34`). |

### Data and execution adapters
| Module | Responsibility | Key symbols | Dependencies observed |
|---|---|---|---|
| `vibetrading_v2/data/__init__.py` | Re-export data adapter. | `ParquetDataSource` | Imports `data.parquet_source` (`vibetrading_v2/data/__init__.py:3-5`). |
| `vibetrading_v2/data/catalog.py` | Resolves symbol→Parquet path. | `DataCatalog.path_for_symbol` | `pathlib.Path` (`vibetrading_v2/data/catalog.py:5-15`). |
| `vibetrading_v2/data/parquet_source.py` | Parquet-backed bar loader + row-to-`Bar` mapping. | `ParquetDataSource.get_bars`, `_frame_to_bars`, `REQUIRED_COLUMNS` | Uses `core.ports.DataSource`, `core.types.Bar`, lazy `pandas.read_parquet` (`vibetrading_v2/data/parquet_source.py:9-11,29-37,39-78`). |
| `vibetrading_v2/execution/__init__.py` | Re-exports execution adapters. | `SimulatedExecution`, `PaperExecutionAdapter`, `LiveExecutionAdapter` | Imports execution modules (`vibetrading_v2/execution/__init__.py:3-7`). |
| `vibetrading_v2/execution/simulator.py` | Backtest simulated execution adapter. | `SimulatedExecution.execute` | Implements `ExecutionPort`, returns `Fill` (`vibetrading_v2/execution/simulator.py:7-25`). |
| `vibetrading_v2/execution/paper_adapter.py` | Paper execution scaffold. | `PaperExecutionAdapter.execute` | Implements `ExecutionPort`, marks price via injected callback (`vibetrading_v2/execution/paper_adapter.py:7-25`). |
| `vibetrading_v2/execution/live_adapter.py` | Live execution scaffold. | `LiveExecutionAdapter.execute` | Implements `ExecutionPort`, uses injected `submit_order` callback (`vibetrading_v2/execution/live_adapter.py:7-29`). |

### Policy modules
| Module | Responsibility | Key symbols | Dependencies observed |
|---|---|---|---|
| `vibetrading_v2/policies/__init__.py` | Re-export policy set/merge API. | `PolicySet`, `default_policy_set`, `merge_policy_overrides` | Imports from `policies.merge` (`vibetrading_v2/policies/__init__.py:3-9`). |
| `vibetrading_v2/policies/defaults.py` | Baseline policy dataclasses. | `CostPolicy`, `RiskPolicy`, `SizingPolicy`, `DefaultPolicies` | `dataclasses` only (`vibetrading_v2/policies/defaults.py:5-35`). |
| `vibetrading_v2/policies/cost.py` | Cost defaults and override merge. | `CostPolicy`, `default_cost_policy`, `merge_cost_override` | Depends on `strategy.bundle.CostOverride` (`vibetrading_v2/policies/cost.py:7-37`). |
| `vibetrading_v2/policies/risk.py` | Risk defaults and override merge. | `RiskPolicy`, `default_risk_policy`, `merge_risk_override` | Depends on `strategy.bundle.RiskOverride` (`vibetrading_v2/policies/risk.py:7-47`). |
| `vibetrading_v2/policies/sizing.py` | Sizing defaults and override merge. | `SizingPolicy`, `default_sizing_policy`, `merge_sizing_override` | Depends on `strategy.bundle.SizingOverride` (`vibetrading_v2/policies/sizing.py:7-39`). |
| `vibetrading_v2/policies/merge.py` | Compose defaults + apply strategy overrides. | `PolicySet`, `default_policy_set`, `merge_policy_overrides` | Depends on `policies.*` + `strategy.bundle.PolicyOverrides` (`vibetrading_v2/policies/merge.py:7-39`). |

### Strategy loading and runtime orchestration
| Module | Responsibility | Key symbols | Dependencies observed |
|---|---|---|---|
| `vibetrading_v2/strategy/__init__.py` | Re-exports bundle types and loader shim. | `load_strategy_bundle` wrapper + bundle symbols | Lazy import from `strategy.registry` (`vibetrading_v2/strategy/__init__.py:3-27`). |
| `vibetrading_v2/strategy/base.py` | Base `Strategy` contract used by runner. | `Strategy.attach_ports`, `on_bar`, `on_fill`, `finalize` | Depends on `core.ports` + `core.types` (`vibetrading_v2/strategy/base.py:5-31`). |
| `vibetrading_v2/strategy/bundle.py` | Strategy metadata + override schema definitions. | `StrategyMeta`, `StrategyBundle`, override dataclasses, `Timeframe` literal | Depends on `strategy.base.Strategy` (`vibetrading_v2/strategy/bundle.py:8-56`). |
| `vibetrading_v2/strategy/sandbox.py` | Static strategy import policy checker. | `validate_strategy_imports`, allow/deny prefix lists | Uses `ast`, `Path`, `core.errors` (`vibetrading_v2/strategy/sandbox.py:5-10,11-42,66-109`). |
| `vibetrading_v2/strategy/registry.py` | Strategy file resolution, import, bundle validation, CLI validator. | `_resolve_strategy_path`, `load_strategy_bundle`, `validate_all_strategies`, `main` | Uses `importlib.util`, `strategy.sandbox`, `strategy.bundle`, `core.errors` (`vibetrading_v2/strategy/registry.py:5-25,26-154`). |
| `vibetrading_v2/runner/__init__.py` | Re-export backtest/paper/live wrappers + result/ports types. | `run_backtest`, `run_paper`, `run_live`, `RunnerPorts`, `RunnerResult` | Imports runner modules (`vibetrading_v2/runner/__init__.py:3-8`). |
| `vibetrading_v2/runner/runtime.py` | Shared loop used by all modes. | `RunnerPorts`, `RunnerResult`, `_inject_ports`, `run_mode` | Depends on `core.ports`, `core.types`, `policies.merge`, `strategy.registry` (`vibetrading_v2/runner/runtime.py:8-12,14-94`). |
| `vibetrading_v2/runner/backtest.py` | Thin backtest wrapper. | `run_backtest` | Delegates to `run_mode("backtest",...)` (`vibetrading_v2/runner/backtest.py:5-14`). |
| `vibetrading_v2/runner/paper.py` | Thin paper wrapper. | `run_paper` | Delegates to `run_mode("paper",...)` (`vibetrading_v2/runner/paper.py:5-14`). |
| `vibetrading_v2/runner/live.py` | Thin live wrapper. | `run_live` | Delegates to `run_mode("live",...)` (`vibetrading_v2/runner/live.py:5-14`). |
| `vibetrading_v2/strategies/__init__.py` | Sample strategy package marker. | module docstring | None (`vibetrading_v2/strategies/__init__.py:1`). |
| `vibetrading_v2/strategies/my_strategy_a.py` | Example plugin strategy bundle for 1m bars. | `MyStrategyA`, `get_bundle` | Depends on `core.types`, `core.ports`, `strategy.base`, `strategy.bundle` (`vibetrading_v2/strategies/my_strategy_a.py:10-18,21-60`). |

### Test modules (v2-specific behavior checks)
| Module | Responsibility | Key symbols | Dependencies observed |
|---|---|---|---|
| `vibetrading_v2/tests/test_parquet_source.py` | Validates Parquet source sorting + required OHLCV columns. | `test_parquet_source_reads_and_sorts_rows`, `test_parquet_source_requires_ohlcv_columns` | Uses `ParquetDataSource` (`vibetrading_v2/tests/test_parquet_source.py:7,26-70`). |
| `vibetrading_v2/tests/test_policy_merge.py` | Validates merge behavior for partial policy overrides. | `test_merge_none_overrides_keeps_defaults`, `test_merge_partial_overrides_updates_only_specified_fields` | Uses `policies.merge` + bundle overrides (`vibetrading_v2/tests/test_policy_merge.py:1-25`). |
| `vibetrading_v2/tests/test_runner_flow.py` | Validates runner loads bundle and submits orders through ports. | `test_backtest_runner_loads_bundle_and_executes_orders` | Uses `run_backtest`, `RunnerPorts`, core dataclasses (`vibetrading_v2/tests/test_runner_flow.py:6-8,63-110`). |
| `vibetrading_v2/tests/test_strategy_bundle_schema.py` | Validates bundle schema constraints. | `test_registry_loads_valid_bundle`, `test_registry_rejects_empty_required_fields` | Uses `load_strategy_bundle` + `StrategyValidationError` (`vibetrading_v2/tests/test_strategy_bundle_schema.py:7-9,11-67`). |
| `vibetrading_v2/tests/test_strategy_forbidden_imports.py` | Validates forbidden import sandbox behavior. | `test_registry_blocks_forbidden_strategy_imports` | Uses `load_strategy_bundle` + `StrategySandboxError` (`vibetrading_v2/tests/test_strategy_forbidden_imports.py:7-9,11-37`). |

## Ports/adapters alignment and boundary check
- **Matches intent (partial):** Core files are currently pure and avoid direct network/DB SDK usage (`vibetrading_v2/core/types.py:5-7`; `vibetrading_v2/core/ports.py:5-7`; `rg -n "requests|websocket|sqlalchemy|psycopg|redis|aiohttp|httpx" vibetrading_v2/core -S` had no hits).
- **Mismatch with required package split:** there is no `vibetrading_v2/ports/**`, `vibetrading_v2/adapters/**`, or `vibetrading_v2/apps/**`; instead current split is `core/ports.py`, `data/*`, `execution/*`, and `cli/*` (`find vibetrading_v2 -maxdepth 1 -type d | sort`; missing-dir check; `cli/backtest.py:14-17`).
- **Boundary policy exists but narrow scope:** import allow/deny is enforced for strategy files via sandbox/registry, not as a global boundary for all modules (`vibetrading_v2/strategy/registry.py:110`; `vibetrading_v2/strategy/sandbox.py:66-109`).

## Intentionally excluded / placeholder areas (observed)
- Live and paper execution are placeholders with injectable callbacks and immediate `Fill` return; no broker API integration in-tree (`vibetrading_v2/execution/live_adapter.py:12-29`; `vibetrading_v2/execution/paper_adapter.py:12-25`).
- Data ingestion is currently Parquet-only in V2 modules; no KIS/Kiwoom adapters found (`vibetrading_v2/data/parquet_source.py:14-37`; `rg -n "kiwoom|kis" vibetrading_v2 -S` had no adapter hits).
- DB/state-store integrations are intentionally absent in current modules; only deny-list mentions appear in sandbox (`vibetrading_v2/strategy/sandbox.py:33-37`).

## Key files/symbols (>=15)
- `vibetrading_v2/core/types.py:10-17 (Bar)` — OHLCV bar entity used by strategies/runners.
- `vibetrading_v2/core/types.py:21-25 (OrderIntent)` — minimal order request intent.
- `vibetrading_v2/core/types.py:28-34 (Fill)` — minimal execution outcome.
- `vibetrading_v2/core/ports.py:10-12 (DataSource.get_bars)` — data read contract.
- `vibetrading_v2/core/ports.py:15-17 (ExecutionPort.execute)` — execution contract.
- `vibetrading_v2/core/ports.py:20-22 (Clock.now)` — clock contract.
- `vibetrading_v2/core/ports.py:25-27 (Logger.info)` — logging contract.
- `vibetrading_v2/data/catalog.py:14-15 (DataCatalog.path_for_symbol)` — symbol to parquet path resolver.
- `vibetrading_v2/data/parquet_source.py:34-37 (ParquetDataSource.get_bars)` — bar loading entrypoint.
- `vibetrading_v2/data/parquet_source.py:39-78 (ParquetDataSource._frame_to_bars)` — normalization and conversion path.
- `vibetrading_v2/execution/simulator.py:18-25 (SimulatedExecution.execute)` — backtest execution adapter behavior.
- `vibetrading_v2/execution/paper_adapter.py:18-25 (PaperExecutionAdapter.execute)` — paper execution scaffold behavior.
- `vibetrading_v2/execution/live_adapter.py:22-29 (LiveExecutionAdapter.execute)` — live execution scaffold behavior.
- `vibetrading_v2/policies/merge.py:24-39 (default_policy_set, merge_policy_overrides)` — policy composition.
- `vibetrading_v2/policies/risk.py:11-16 (RiskPolicy)` — risk policy including kill-switch threshold field.
- `vibetrading_v2/strategy/base.py:9-31 (Strategy)` — strategy lifecycle hooks.
- `vibetrading_v2/strategy/bundle.py:10-56 (Timeframe, StrategyMeta, StrategyBundle)` — plugin schema.
- `vibetrading_v2/strategy/registry.py:98-114 (load_strategy_bundle)` — load + validate strategy pipeline.
- `vibetrading_v2/strategy/sandbox.py:66-109 (validate_strategy_imports)` — strategy import sandbox.
- `vibetrading_v2/runner/runtime.py:53-94 (run_mode)` — mode-agnostic orchestrator.
- `vibetrading_v2/runner/runtime.py:23-30 (RunnerResult)` — current runtime output model.
- `vibetrading_v2/strategies/my_strategy_a.py:47-60 (get_bundle)` — sample strategy bundle config.

## Evidence
### Commands run
```bash
ls -ldi vibetrading_V2 vibetrading_v2
find vibetrading_v2 -maxdepth 1 -type d | sort
find vibetrading_v2 -type f -name '*.py' | sort
for f in $(find vibetrading_v2 -type f -name '*.py' | sort); do echo "## $f"; rg -n "^(class|def) " "$f" || true; done
rg -n "requests|websocket|sqlalchemy|psycopg|redis|aiohttp|httpx|os\.environ|dotenv" vibetrading_v2/core -S
rg -n "kiwoom|kis|broker|database|sqlite|postgres|mysql|redis|websocket|ws" vibetrading_v2 cli -S
```

### Key command excerpts
- `ls -ldi vibetrading_V2 vibetrading_v2` returned the same inode (`33836452`) for both names.
- `find vibetrading_v2 -maxdepth 1 -type d` returned `core,data,execution,policies,runner,strategies,strategy,tests`; no `ports/adapters/apps` directories.
- Symbol scan confirms loader/runtime-centric architecture with `run_mode`, `load_strategy_bundle`, `validate_strategy_imports`, and three execution adapters.
- Core forbidden-import scan produced no hits.

### Concrete code references
- `vibetrading_v2/core/ports.py:10-28`
- `vibetrading_v2/core/types.py:10-34`
- `vibetrading_v2/data/parquet_source.py:14-78`
- `vibetrading_v2/execution/live_adapter.py:11-29`
- `vibetrading_v2/execution/paper_adapter.py:11-25`
- `vibetrading_v2/runner/runtime.py:14-94`
- `vibetrading_v2/strategy/bundle.py:10-56`
- `vibetrading_v2/strategy/registry.py:98-154`
- `vibetrading_v2/strategy/sandbox.py:11-42,66-109`
- `cli/backtest.py:14-46`
