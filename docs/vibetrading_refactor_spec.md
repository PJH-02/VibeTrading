# vibetrading_V2 Refactor Spec (Python)

**Purpose:** Define a modular architecture where **strategies are loaded as plugins** for backtest/paper/live, and the **only user-editable surface is a single file**: `strategies/my_strategy_a.py`.

This document is the **canonical spec** for both **ARCHITECT** (analysis/design) and **EXECUTOR** (implementation). Keep the implementation faithful to this spec; avoid scope creep.

---

## 1. Non‑Negotiables (Acceptance Criteria)

1. **Single Editable Surface**
   - A user can run backtest/paper/live while modifying **only** `strategies/my_strategy_a.py`.
   - The strategy file can declare:
     - **Meta**: universe, timeframe, required columns/features, trading session constraints (optional)
     - **Overrides (optional)**: cost model params, risk limits, position sizing params
   - If a field is not overridden, **system defaults apply** unchanged.

2. **Strategy Isolation**
   - `strategies/*` may import **ONLY**:
     - `vibetrading_V2.core.*`
     - `vibetrading_V2.strategy.base` and `vibetrading_V2.strategy.bundle`
     - Optional *pure compute* libs explicitly allowed (e.g. `numpy`, `pandas`) — no I/O.
   - `strategies/*` must **NOT** import:
     - `vibetrading_V2.runner.*`, `vibetrading_V2.execution.*`, `vibetrading_V2.data.*`, `cli/*`
     - any file/network I/O, DB drivers, broker SDKs, async event loops
   - Violations must **fail fast at load time** with a clear error.

3. **Core Purity**
   - `vibetrading_V2/core` contains **types, ports, and errors only**. No filesystem, no networking, no environment branching.

4. **Read‑Only Runtime Data**
   - Runtime consumes **curated parquet** under `data/curated/` as **read‑only**.
   - ETL is out of scope and lives separately.

5. **Composition Root Only in `cli/`**
   - Object wiring (paths, environment, policy selection, adapter selection) happens only in `cli/*`.
   - Runners consume only *ports* and the `StrategyBundle` contract.

6. **New folder**
   - Implementation must be side-by-side under vibetrading_V2/ and cli/. Do not modify existing files.(OK to create folders/files there)

---

## 2. Target Repository Layout

```text
vibetrading_V2/
├── vibetrading_V2/
│   ├── core/           # types + ports + errors (pure)
│   ├── data/           # catalog + parquet source (read-only)
│   ├── strategy/       # interface + bundle schema + registry + sandbox
│   ├── policies/       # default cost/risk/sizing (config-driven)
│   ├── execution/      # simulator + optional broker adapter
│   └── runner/         # backtest/paper/live (consume StrategyBundle only)
├── strategies/
│   └── my_strategy_a.py   # the only user-editable file
├── data/
│   └── curated/           # parquet outputs from ETL (read-only)
└── cli/
    ├── backtest.py
    ├── paper.py
    └── live.py
```

---

## 3. Dependency Rules (One‑Way Import Graph)

The import graph must remain **one-way** (DAG) at the package level.

**Mandatory directionality (high level):**
- `strategies/*  →  vibetrading_V2.core.*`
- `strategies/*  →  vibetrading_V2.strategy.(base|bundle)` *(schema/contracts only)*
- `vibetrading_V2.runner/*  →  vibetrading_V2.strategy.registry` *(loading only via registry)*
- `vibetrading_V2.policies/*` must not import `strategies/*`

**Runner rule:** runners must not import strategy internals; they consume only the `StrategyBundle` contract.

---

## 4. Strategy Plugin Contract

### 4.1 Strategy Module Export (required)

`strategies/my_strategy_a.py` must provide **exactly one** of the following exports:

- `def get_bundle() -> "StrategyBundle": ...`  *(preferred)*  
  - should be lightweight and deterministic at import time
  - heavy initialization should happen inside the returned `Strategy` instance

**or**

- `BUNDLE: "StrategyBundle" = ...`

The registry must support both patterns but should encourage `get_bundle()`.

### 4.2 What the Strategy Bundle Must Contain

The bundle is a **pure declaration**: meta + optional overrides + a strategy factory.

#### Minimal Python typing (schema intent)

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence, Optional, Literal

Timeframe = Literal["1m","5m","15m","1h","1d"]  # extend as needed

@dataclass(frozen=True)
class StrategyMeta:
    name: str
    universe: Sequence[str]                 # symbols / tickers
    timeframe: Timeframe
    required_fields: Sequence[str]          # columns/features required at runtime
    session: Optional[str] = None           # e.g. "24x7", "NYSE", optional

@dataclass(frozen=True)
class CostOverride:
    commission_bps: Optional[float] = None
    slippage_bps: Optional[float] = None
    min_fee: Optional[float] = None

@dataclass(frozen=True)
class RiskOverride:
    max_leverage: Optional[float] = None
    max_position_notional: Optional[float] = None
    max_drawdown: Optional[float] = None
    kill_switch_dd: Optional[float] = None

@dataclass(frozen=True)
class SizingOverride:
    target_vol: Optional[float] = None
    max_gross_exposure: Optional[float] = None
    per_trade_risk: Optional[float] = None

@dataclass(frozen=True)
class PolicyOverrides:
    cost: Optional[CostOverride] = None
    risk: Optional[RiskOverride] = None
    sizing: Optional[SizingOverride] = None

@dataclass(frozen=True)
class StrategyBundle:
    meta: StrategyMeta
    build: Callable[[], "Strategy"]         # no args; runner injects ports into Strategy later
    overrides: Optional[PolicyOverrides] = None
```

**Important:** strategies must not import concrete policy classes. They only provide **override values** (dataclasses/dicts).

---

## 5. Default Policies + Override Merge Semantics

- System provides default policies in `vibetrading_V2/policies/*` (cost/risk/sizing).
- Strategy overrides apply as **partial merges**:
  - if override is `None` → keep default
  - if override field is `None` → keep that default field
  - if override field is set → overwrite that default field

**Implementation requirement:** merging must be pure and testable (e.g., dataclass replace / dict overlay).

---

## 6. Strategy Loading + Validation + Sandbox

Loading is the gate that enforces “single editable surface” and safety.

### 6.1 Registry responsibilities (`vibetrading_V2/strategy/registry.py`)
- Load a strategy module from `strategies/<name>.py` (by path or module name).
- Extract the `StrategyBundle` via `get_bundle()` or `BUNDLE`.
- Validate:
  - bundle type + required fields present
  - `meta.required_fields` is non-empty
  - `meta.universe` non-empty
  - overrides types (if present)
- Fail with a **typed error** from `vibetrading_V2/core/errors.py`.

### 6.2 Sandbox responsibilities (`vibetrading_V2/strategy/sandbox.py`)
Enforce import allow/deny rules for strategy modules:
- Deny importing `vibetrading_V2.runner`, `vibetrading_V2.execution`, `vibetrading_V2.data`, `cli`, plus any I/O libs (configurable denylist).
- Suggested approaches (choose one):
  1) AST scan of `strategies/*.py` for `import` / `from ... import ...` before import
  2) Import hook that intercepts module imports during strategy load
- Must fail fast at load time with a clear message listing the forbidden import.

---

## 7. Data Access Model (Ports)

Strategy code must not know parquet paths or storage details.

- `vibetrading_V2/core/ports.py` defines `DataSource` (read-only), `Execution`, `Clock`, `Logger` interfaces.
- `vibetrading_V2/data/parquet_source.py` implements `DataSource` for `data/curated` parquet.
- Runner injects a `DataSource` instance; strategy consumes data only through ports.

---

## 8. Determinism & I/O Prohibitions

Backtests must be deterministic:
- Data is time-sorted; clock is controlled; random seed is fixed (if used).
- Strategy code must not perform external I/O (file/network/db/broker).
- Violations are prevented via sandbox denylist and/or runtime guards.

---

## 9. CLI and Runners (Composition Root)

- `cli/*` selects environment (backtest/paper/live), strategy name/path, and policy defaults.
- `vibetrading_V2/runner/*` runs an event loop:
  - loads bundle via registry
  - builds default policies; applies overrides (pure merge)
  - instantiates Strategy; injects ports (DataSource/Execution/Clock/Logger)
  - runs and records results

Runner/backtest/paper/live must remain interchangeable by swapping ports (Execution/DataSource).

---

## 10. Architect Deliverables (analysis artifacts)

ARCHITECT must write:
1) `.omx/plans/vibetrading_import_hotspots.md`
   - SCC cycles, hub modules, dependency cut points, high-risk refactor zones
2) `.omx/plans/vibetrading_refactor_prd.md`
   - mapping from current tree → target tree
   - module boundaries + one-way dependency DAG
   - proposed StrategyBundle exact schema decisions
   - sandbox allow/deny lists and validation plan
   - migration order (what moves first to break cycles)

EXECUTOR must read these artifacts before edits.

---

## 11. Minimal Verification Targets (human-run)

- `python -m pytest -q`
- `python -m ruff check .`
- `python -m mypy vibetrading_V2`
- `python -m vibetrading_V2.strategy.registry --validate-all` *(to be implemented)*

Done means:
- a sample strategy in `strategies/my_strategy_a.py` loads successfully
- forbidden imports fail at load time
- defaults apply when overrides are absent
- overrides correctly overwrite only specified fields
