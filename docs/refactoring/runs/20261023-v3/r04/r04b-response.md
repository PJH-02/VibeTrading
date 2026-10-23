## Objective
- Complete ticket R04B only: canonical modern strategy loading plus compatibility adapters for pre-moved legacy paths, then add ownership tests.
- Preserve behavior/public contracts; no economic changes, no R05 extraction, no acceptance self-approval.

## Important Details
- Authoritative ticket: `/work/docs/tickets/R04B-strategy-adapters.md`; move map: `/work/docs/tickets/R04-scope.json`; base_sha `b3409b17f9cfe863528a6a9cecde7a4aa9aaab4b`.
- Codex pre-moved 12 source files. Old paths are empty ADD placeholders; implementation/tests are worker-owned.
- Canonical modern contract: `vibetrading.strategies.contract`; only one `load_strategy` implementation.
- Loader must accept bare names, `strategies.<name>`, `vibetrading.strategies.<name>`, and `.py` slash/backslash spellings; reject traversal, absolute paths, unrelated namespaces, and modern `legacy`/`research` imports.
- Old paths must be pure module aliases (`sys.modules[__name__] = _canonical`) so old/new modules, classes, functions, monkeypatches, and caches are identical.
- Immutable modules must stay byte-identical: `strategies/example_signal`, `legacy/candle/turtle_breakout`, research state S1-S4, frozen fair-value S1-S4.
- Canonical candle loader must import only `vibetrading.strategies.legacy.candle.<name>` and preserve `get_strategy`, team/cache, `StrategyWrapper`, backtest API.
- Runtime dynamic imports must target `vibetrading.strategies.research.state.*`; `mm_state.features.legacy_instance` targets `vibetrading.strategies.legacy.fair_value.*`.
- `run_strategy.scan_strategies` must discover only canonical legacy candle files using module `__file__`, not cwd.
- Provenance `source_hash` must hash actual strategy implementation files, not adapter bodies; preserve non-strategy coverage, filename ordering, and frozen-flat-export fallback.
- Bash is denied except `python3 -m pytest*`; no grep/glob tools available. Use `read`/`edit`/`write` only.
- Test command is blocked in this sandbox: `python3 -m pytest ...` returns `Refactoring offline guard failed to initialize`; `sitecustomize.py` requires env var `REFACTOR_GUARD_RECEIPT`. Report exact failure; do not claim PASS.
- `pytest.ini` sets `pythonpath = src`; old tests import `strategy.s1_net_liquidity` etc., so adapters are essential.

## Work State
### Completed
- Read canonical modules, old placeholders, callers, scripts, tests, `sitecustomize.py`, `pytest.ini`.
- Edited `/work/src/vibetrading/strategies/contract.py`:
  - imported from `vibetrading.hot_path` / `vibetrading.models`;
  - added `_MODERN_STRATEGY_NAME` regex and `_canonical_module_name`;
  - replaced `load_strategy` normalization with canonical namespace mapping/rejection rules.
- Wrote `/work/src/vibetrading/strategies/__init__.py` with ownership docstring and public reexports from `.contract`: `CompiledSubscription`, `StrategyInstance`, `StrategyModule`, `compile_subscriptions`, `compile_warmup`, `load_strategy`.
- Wrote compatibility alias adapters:
  - `/work/src/vibetrading/strategy_contract.py`
  - `/work/services/signal_gen/strategy_loader.py`
  - `/work/strategies/example_signal.py`
  - `/work/strategies/turtle_breakout.py`
  - `/work/strategy/s1_net_liquidity.py`, `s2_external_anchor.py`, `s3_aggressive_flow.py`, `s4_obi.py`
  - `/work/src/vibetrading/fv_mm/legacy/s1_net_liquidity.py`, `s2_external_anchor.py`, `s3_aggressive_flow.py`, `s4_obi.py`

### Active
- `/work/src/vibetrading/strategies/legacy/candle_loader.py` still needs canonical import path update and traversal-safe bare-name resolution.
- Package `__init__.py` ownership docstrings still needed for `strategies/legacy`, `strategies/legacy/candle`, `strategies/legacy/fair_value`, `strategies/research`, `strategies/research/state`; no auto-imports.
- Callers still need canonical import updates:
  - `src/vibetrading/strategy_runner.py`, `strategy_runtime.py`, `timefolio/runner.py`
  - `src/vibetrading/fv_mm/market.py`, `fv_pilot/market.py`, `mm_state/coordinator.py`, `mm_state/features.py`
  - `services/signal_gen/engine.py`
  - `scripts/rebuild_fv_pilot_calibration.py`, `diagnose_mm_state_no_intents.py`, `replay_mm_state_revision.py`
  - `scripts/run_backtest.py`, `scripts/validate_system.py`
- `src/vibetrading/fv_mm/runner.py` and `fv_pilot/runner.py` still need `source_hash` path selection updated to canonical state/fair-value with flat-export fallback.
- `scripts/build_all_strategy_report.py` `SOURCES` C01/C03-C06/C08-C12 still point at old/adaptor paths.
- `scripts/report_mm_state_revision.py` source globs still include `ROOT/'strategy'` and `src/vibetrading/fv_mm/legacy`; update to canonical state/fair-value with fallback.
- `run_strategy.py` still scans `PROJECT_ROOT/"strategies"`; update discovery path/messages to canonical candle package.
- `/work/tests/refactoring/test_strategy_ownership.py` is currently empty and still must be written.

### Blocked
- Exact pytest command cannot initialize:
  - command: `python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/refactoring/test_strategy_ownership.py ...`
  - observed: `Refactoring offline guard failed to initialize`
  - cause: `/work/sitecustomize.py` requires `REFACTOR_GUARD_RECEIPT`; sandbox does not provide it and bash env assignment is denied.
- No source-tree search tool available; `grep`/`bash` denied except pytest.

## Next Move
1. Finish implementation: update canonical candle loader, package `__init__` docstrings, all caller imports, script path selection, runner `source_hash`, and `run_strategy.py`.
2. Write `/work/tests/refactoring/test_strategy_ownership.py` covering module/class/monkeypatch identity; loader aliases/invalid paths; modern vs legacy distinction; no dummy/example auto-registration; S1-S4 state vs frozen fair-value identity; canonical dynamic imports; legacy team/cache/backtest API; CLI candle discovery.
3. Attempt exact ticket pytest command and record the guard failure verbatim; report no PASS claim.

## Relevant Files
- `/work/docs/tickets/R04B-strategy-adapters.md`: authoritative scope/instructions/acceptance.
- `/work/docs/tickets/R04-scope.json`: exact move map and edit paths.
- `/work/src/vibetrading/strategies/contract.py`: canonical modern contract; edited loader/imports.
- `/work/src/vibetrading/strategies/__init__.py`: canonical public reexports; written.
- `/work/src/vibetrading/strategies/legacy/candle_loader.py`: canonical explicit candle API; still needs canonical import change.
- `/work/src/vibetrading/strategies/research/state/s1_net_liquidity.py` … `s4_obi.py`: immutable canonical state strategies.
- `/work/src/vibetrading/strategies/legacy/fair_value/s1_net_liquidity.py` … `s4_obi.py`: immutable frozen fair-value strategies.
- `/work/src/vibetrading/strategies/legacy/candle/turtle_breakout.py`: immutable legacy candle strategy.
- `/work/src/vibetrading/strategies/example_signal.py`: immutable modern example strategy.
- `/work/src/vibetrading/strategy_contract.py`: alias adapter to canonical contract; written.
- `/work/services/signal_gen/strategy_loader.py`: alias adapter to canonical candle loader; written.
- `/work/strategies/example_signal.py`, `/work/strategies/turtle_breakout.py`: old-path alias adapters; written.
- `/work/strategy/s1_net_liquidity.py` … `s4_obi.py`: old-path alias adapters to research state; written.
- `/work/src/vibetrading/fv_mm/legacy/s1_net_liquidity.py` … `s4_obi.py`: old-path alias adapters to frozen fair value; written.
- `/work/src/vibetrading/strategy_runner.py`, `/work/src/vibetrading/strategy_runtime.py`, `/work/src/vibetrading/timefolio/runner.py`: active contract callers using old import path.
- `/work/src/vibetrading/fv_mm/market.py`, `/work/src/vibetrading/fv_pilot/market.py`, `/work/src/vibetrading/mm_state/coordinator.py`: dynamic `strategy.<module>` imports.
- `/work/src/vibetrading/mm_state/features.py`: `legacy_instance` uses old `vibetrading.fv_mm.legacy.<module>`.
- `/work/services/signal_gen/engine.py`: old service candle loader caller.
- `/work/scripts/rebuild_fv_pilot_calibration.py`, `/work/scripts/diagnose_mm_state_no_intents.py`, `/work/scripts/replay_mm_state_revision.py`: dynamic state strategy imports.
- `/work/scripts/run_backtest.py`, `/work/scripts/validate_system.py`: old candle loader callers.
- `/work/scripts/build_all_strategy_report.py`: `SOURCES` strategy paths need canonical updates.
- `/work/scripts/report_mm_state_revision.py`: source glob/path prose needs canonical updates.
- `/work/src/vibetrading/fv_mm/runner.py`, `/work/src/vibetrading/fv_pilot/runner.py`: `source_hash` path selection.
- `/work/run_strategy.py`: legacy candle CLI discovery and guidance.
- `/work/tests/refactoring/test_strategy_ownership.py`: empty; required test file.
- `/work/tests/refactoring/test_strategy_dispatch.py`, `test_strategy_runtime.py`: existing tests importing old contract path and monkeypatching `strategies.example_signal`.
- `/work/tests/test_fv_mm.py`, `/work/tests/test_mm_state_revision.py`: old tests importing `strategy.*` adapters.
- `/work/sitecustomize.py`, `/work/pytest.ini`: sandbox guard and `pythonpath = src`.
Continue if you have next steps, or stop and ask for clarification if you are unsure how to proceed.
Implemented the R04B scope.

### Changes
- Canonicalized `vibetrading.strategies.contract` imports and single modern `load_strategy`.
- Exported the public contract objects from `vibetrading.strategies`.
- Added compatibility aliases for all old paths:
  - `vibetrading.strategy_contract`
  - `services.signal_gen.strategy_loader`
  - `strategies.example_signal`, `strategies.turtle_breakout`
  - `strategy.s1_*` through `s4_*`
  - `vibetrading.fv_mm.legacy.s1_*` through `s4_*`
- Updated canonical candle loader to import only `vibetrading.strategies.legacy.candle.<name>`.
- Added ownership-only package `__init__.py` files for legacy/research packages with no auto-imports.
- Updated active callers, scripts, runtime dynamic imports, CLI discovery, and provenance path selection.
- Added `tests/refactoring/test_strategy_ownership.py` covering identity, loader aliases, invalid paths, modern/legacy separation, S1-S4 family distinction, dynamic imports, cache/team behavior, and CLI discovery.

### Test Result
Ran the exact required command from `/work`. It could not initialize in this sandbox:

```text
Refactoring offline guard failed to initialize
```

Cause: `/work/sitecustomize.py` requires `REFACTOR_GUARD_RECEIPT`, which is not provided. The same guard failure occurred on retry, so no passing result is claimed.
