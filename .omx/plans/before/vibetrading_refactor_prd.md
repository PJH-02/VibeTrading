# VibeTrading Refactor PRD (Architect Pass)

## 0) Inputs / scope
- Canonical spec used: `docs/vibetrading_refactor_spec.md`
- Goal: strategy-plugin architecture where user edits only `strategies/my_strategy_a.py`
- This PRD pairs with `.omx/plans/vibetrading_import_hotspots.md`

### Implementation mode (mandatory)
- Side-by-side reimplementation.
- **Create new files only under `vibetrading_V2/` and `cli/`.**
- **Do not modify or delete any existing file** in `backtest/`, `services/`, `shared/`, `scripts/`, `run_strategy.py`, or existing strategies.

---

## 1) Current tree → target tree mapping

### Current major zones
- `shared/`: mixed domain types + config + DB + messaging + fill logic
- `services/`: strategy loading/execution, data feeds, execution adapters, risk/monitoring
- `backtest/`: engine + data loader + walk-forward
- `run_strategy.py` and `scripts/run_backtest.py`: composition + orchestration
- `strategies/`: strategy files coupled to `shared.models`

### Target tree (V2 side-by-side)
```text
vibetrading_V2/
  core/      # types + ports + errors only (pure)
  data/      # parquet source + data catalog (read-only)
  strategy/  # base, bundle, registry, sandbox
  policies/  # default cost/risk/sizing + pure merge
  execution/ # simulator + broker adapters
  runner/    # backtest/paper/live runners using ports+bundle
strategies/
  my_strategy_a.py
cli/
  backtest.py
  paper.py
  live.py
```

### Proposed “conceptual move map” (do NOT modify existing files)

| Current concept | V2 location | Notes |
|---|---|---|
| Domain types from `shared/models.py` | `vibetrading_V2/core/types.py` (+ strategy schema in `vibetrading_V2/strategy/bundle.py`) | Pure contracts only. |
| Strategy interface from `shared/strategy_contracts.py` | `vibetrading_V2/strategy/base.py` | Interface-only. |
| Strategy loader from `services/signal_gen/strategy_loader.py` | `vibetrading_V2/strategy/registry.py` | Typed validation + sandbox. |
| Strategy import gating | `vibetrading_V2/strategy/sandbox.py` | Allowlist-first + fail-fast. |
| Defaults/thresholds from `shared/config.py` | `vibetrading_V2/policies/*` + CLI wiring | Defaults + pure merge. |
| Fill/sim logic from `shared/fill_logic.py` | `vibetrading_V2/execution/simulator.py` | Deterministic simulation. |
| Data load from `backtest/data_loader.py` | `vibetrading_V2/data/parquet_source.py` | Read-only curated parquet per spec. |
| Backtest/paper/live orchestration | `vibetrading_V2/runner/*` | Consume ports + bundle only. |
| Composition in `run_strategy.py` / scripts | `cli/*` | Composition root only. |

---

## 2) Module boundaries and one-way dependency DAG

### Boundary contracts

- `vibetrading_V2/core`: pure, no filesystem/network/env branching.
- `vibetrading_V2/strategy`: plugin loading + schema + sandbox.
- `vibetrading_V2/policies`: defaults + merge logic only.
- `vibetrading_V2/data`: read-only adapters implementing ports.
- `vibetrading_V2/execution`: execution adapters implementing ports.
- `vibetrading_V2/runner`: orchestrates via ports + `StrategyBundle`.
- `cli/*`: only place that binds env/config/path/policy selection.

### Package DAG (V2)

```text
strategies/*
  -> vibetrading_V2.core.*
  -> vibetrading_V2.strategy.base
  -> vibetrading_V2.strategy.bundle

vibetrading_V2.strategy.registry
  -> vibetrading_V2.strategy.sandbox
  -> vibetrading_V2.strategy.bundle
  -> vibetrading_V2.core.errors

vibetrading_V2.policies.*
  -> vibetrading_V2.core.types (if needed)

vibetrading_V2.data.*
  -> vibetrading_V2.core.ports/types

vibetrading_V2.execution.*
  -> vibetrading_V2.core.ports/types

vibetrading_V2.runner.*
  -> vibetrading_V2.core.*
  -> vibetrading_V2.strategy.registry
  -> vibetrading_V2.policies.*
  -> vibetrading_V2.data.*
  -> vibetrading_V2.execution.*

cli/*
  -> vibetrading_V2.runner.*
  -> vibetrading_V2.data.*
  -> vibetrading_V2.execution.*
  -> vibetrading_V2.policies.*
```

No reverse edges from core/policies/data/execution back into strategy internals.

---

## 3) Strategy interface (minimal, to unblock implementation)

Define a minimal strategy contract in `vibetrading_V2/strategy/base.py` (keep small and stable):

- `Strategy` must be deterministic and must not perform external I/O.
- Runner invokes:
  - `on_bar(bar: Bar) -> list[OrderIntent]` (or `Signal`), required
  - `on_fill(fill: Fill) -> None`, optional
  - `finalize() -> None`, optional
- Ports (`Clock`, `Logger`, etc.) are injected by runner (constructor injection or `attach(ports)`; choose one).

---

## 4) StrategyBundle schema decisions (exact)

Adopt spec schema with frozen dataclasses and explicit optional override fields:

- `StrategyMeta`, `CostOverride`, `RiskOverride`, `SizingOverride`, `PolicyOverrides`, `StrategyBundle`
- Export contract in `strategies/my_strategy_a.py`:
  - Preferred: `get_bundle() -> StrategyBundle`
  - Supported fallback: module constant `BUNDLE: StrategyBundle`

---

## 5) Sandbox allow/deny and validation plan

### Allowed imports in `strategies/*` (allowlist-first)

- `vibetrading_V2.core.*`
- `vibetrading_V2.strategy.base`
- `vibetrading_V2.strategy.bundle`
- optional pure-compute: `numpy`, `pandas`, plus Python stdlib `typing`, `dataclasses`, `math`

### Denied imports in `strategies/*` (non-exhaustive; mainly for clearer errors)

- `vibetrading_V2.runner.*`, `vibetrading_V2.execution.*`, `vibetrading_V2.data.*`
- `cli.*`
- infra/io/sdk: `os`, `pathlib`, `io`, `socket`, `asyncio`, `sqlalchemy`, `psycopg*`, `redis`, `nats`, `requests`, `httpx`, `websockets`, broker SDKs

### Validation plan

1. `sandbox.py`: AST pre-scan each strategy module for `Import`/`ImportFrom`; fail if any import is outside allowlist.
2. `registry.py`: load via sandbox gate, then extract `get_bundle()` or `BUNDLE`.
3. Bundle checks:
   - export exists and returns/contains `StrategyBundle`
   - `meta.universe` non-empty
   - `meta.required_fields` non-empty
   - override object types valid
4. Failures use typed errors in `vibetrading_V2/core/errors.py`.

---

## 6) Policy defaults and merge semantics

Defaults in `vibetrading_V2/policies/{cost,risk,sizing}.py` (+ optional `defaults.py`).

Pure merge rule (unit-tested):

- override object is `None` => keep default object
- override field is `None` => keep default field
- override field is set => replace that field only

---

## 7) Minimal acceptance verification (for executor)

- `pytest -q`
- `ruff check .`
- `mypy vibetrading_V2`
- registry validation command (to be added): `python -m vibetrading_V2.strategy.registry --validate-all`
- behavior checks:
  - `strategies/my_strategy_a.py` loads via V2 registry
  - forbidden imports fail at load-time with clear error
  - absent overrides keep defaults
  - partial overrides overwrite only specified fields
