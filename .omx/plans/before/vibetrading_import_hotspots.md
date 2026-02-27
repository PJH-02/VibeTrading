# VibeTrading Import Hotspots (Architect Analysis)

## Scope / method
- Date: 2026-02-21
- Canonical spec reference: `docs/vibetrading_refactor_spec.md`
- Source scanned: `backtest/`, `services/`, `shared/`, `strategies/`, `scripts/`, `run_strategy.py`
- Static import graph snapshot: 38 modules, 109 internal edges
- Implementation mode: **side-by-side**. New code must be created under **`vibetrading_V2/`** and **`cli/`** only. Do not modify existing files.

## 1) SCC cycles
- Non-trivial SCCs (size > 1): none detected.
- Import graph is currently acyclic, but coupling is concentrated in a few hubs.

## 2) Hub modules (highest structural risk)

| Module | Fan-in | Fan-out | Risk |
|---|---:|---:|---|
| `shared.models` | 30 | 0 | Global schema hub; blocks separation of pure core vs infra/runtime. |
| `shared.config` | 17 | 1 | Env/config branch logic leaks into many modules. |
| `backtest.engine` | 9 | 5 | Backtest orchestration directly tied to signal/execution internals. |
| `shared.database` | 7 | 1 | DB surface sits near strategy/backtest execution path. |
| `shared.messaging` | 7 | 1 | Messaging concerns leak into service-layer business logic. |
| `services.signal_gen.engine` | 2 | 5 | Mixes strategy execution, DB, NATS, runtime state. |
| `services.broker_factory` | 1 | 11 | Market/broker branching concentrated outside a composition root. |

## 3) Package-level dependency edges (current)
- `strategies -> shared`
- `backtest -> shared, services`
- `services -> shared`
- `scripts -> backtest, services, shared`
- `run_strategy -> services, shared`

### Why this conflicts with target spec (V2)
- Strategies currently import `shared.*` directly; in V2 strategies must import only:
  - `vibetrading_V2.core.*` and `vibetrading_V2.strategy.(base|bundle)`
- Composition wiring currently exists in `run_strategy.py` and scripts; in V2 wiring must exist only in `cli/*` (new).
- DB/NATS/QuestDB concerns currently leak across layers; V2 requires ports + adapters.

## 4) Dependency cut points (high-leverage seams)

1. Strategy loading seam
   - Current: `services/signal_gen/strategy_loader.py`
   - V2: `vibetrading_V2/strategy/{registry,sandbox}.py`
   - Outcome: fail-fast import gating + typed bundle validation.

2. Domain type seam
   - Current: `shared/models.py`
   - V2: `vibetrading_V2/core/types.py` (+ strategy schema in `vibetrading_V2/strategy/bundle.py`)
   - Outcome: pure contracts separated from infra/runtime.

3. Policy seam
   - Current: defaults spread across `shared/config.py` and runtime code
   - V2: `vibetrading_V2/policies/*` + pure merge utility
   - Outcome: override semantics deterministic/testable.

4. Data seam
   - Current: `backtest/data_loader.py` (QuestDB/CSV)
   - V2: `vibetrading_V2/data/parquet_source.py` over read-only curated parquet (per spec)
   - Outcome: deterministic backtest input via `DataSource` port.

5. Composition seam
   - Current: `run_strategy.py`, `scripts/run_backtest.py`, `services/broker_factory.py`
   - V2: `cli/{backtest,paper,live}.py` (new)
   - Outcome: composition root confined to CLI only.

## 5) High-risk refactor zones
- `shared.models`: central fan-in means broad touch surface.
- `services.signal_gen.engine`: mixed concerns (strategy + infra + persistence).
- `backtest.engine`: coupled to specific service implementations.
- `shared.config.apply_strategy_config`: strategy-driven env mutation conflicts with plugin override model.
- `services.broker_factory`: runtime branching should move to CLI composition root.

## 6) Migration order (hotspot-first, V2 side-by-side)
1. Introduce V2 contracts (`vibetrading_V2/core`, `vibetrading_V2/strategy/{base,bundle}`).
2. Implement V2 sandbox + registry; route all V2 strategy loads through registry.
3. Extract V2 policies + pure merge rules.
4. Add V2 parquet DataSource (read-only curated parquet per spec).
5. Implement V2 runners consuming ports + `StrategyBundle` only.
6. Add `cli/*` composition root for V2 runners.
7. Enforce V2 strategy import allow/deny checks and lock single editable strategy surface.

## 7) Executor note
Detailed target tree, DAG, StrategyBundle schema, allow/deny policy, and acceptance checks are specified in:
- `.omx/plans/vibetrading_refactor_prd.md`