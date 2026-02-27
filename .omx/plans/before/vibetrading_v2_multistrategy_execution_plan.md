# VibeTrading V2 Multi-Strategy + Multi-Broker Execution Plan

## Context
User requested a plan (no code) to add/check two features in V2:
1) Support three distinct strategy types with easy strategy-side engine selection in `vibetrading_V2/strategies/my_strategy_a.py`:
   - single-asset trading
   - portfolio rebalancing (wide universe)
   - arbitrage (2-3 assets, timestamp alignment + strict execution ordering)
2) Plan execution layer around Kiwoom/KIS/Binance/Bybit APIs.

Reference source is `vibetrading_V1` (for patterns), while keeping V2 minimal and less complex.

## Work Objectives
- Introduce explicit strategy-type contracts and deterministic engine routing in V2.
- Define execution-adapter boundaries and capability matrix for Kiwoom/KIS/Binance/Bybit.
- Keep strategy UX simple: strategy author picks engine/type in one place (`my_strategy_a.py`).

## Guardrails
### Must Have
- Canonical strategy file remains `vibetrading_V2/strategies/my_strategy_a.py`.
- One-way architecture remains: strategy declaration → router → engine → execution adapters.
- Arbitrage path has explicit timestamp alignment and leg-order policy.
- Broker API support is delivered via explicit capability matrix (implemented vs stubbed is declared, not implicit).

### Must NOT Have
- No V1 complexity copy-paste (event bus/DB/messaging coupling) into V2.
- No strategy import rule relaxation (sandbox stays enforced).
- No hidden runtime overrides that conflict with strategy-declared type.

## Task Flow (6 steps)

### Step 1 — Freeze contracts for strategy type and engine routing
**Detailed TODOs**
- Define typed strategy selector contract (single/portfolio/arbitrage) in V2 bundle/meta.
- Define per-type intent/output contract expected from strategies.
- Define router authority rule (strategy declaration vs CLI override policy).

**Acceptance criteria**
- Contract doc/update specifies exactly 3 allowed strategy types and invalid-type failure behavior.
- Each type has a pass/fail-defined output contract the engine can validate.
- Router source-of-truth is unambiguous.

---

### Step 2 — Plan minimal engine split and routing topology
**Detailed TODOs**
- Specify V2 engine layout for:
  - single-asset engine
  - portfolio engine
  - arbitrage engine
- Specify routing module and adapter interfaces using V1 engine router as reference pattern only.
- Define type-specific universe constraints.

**Acceptance criteria**
- Target module map exists with clear boundaries for router + 3 engines.
- Universe constraints are explicit and testable:
  - single: exactly 1 symbol
  - portfolio: multi-symbol
  - arbitrage: 2-3 symbols

---

### Step 3 — Define arbitrage invariants (alignment + ordered execution)
**Detailed TODOs**
- Define timestamp alignment rule (exact match or bounded tolerance).
- Define strict leg execution order and leg failure handling policy.
- Define deterministic behavior requirements for arbitrage backtest replay.

**Acceptance criteria**
- Alignment policy is numeric and testable (e.g., tolerance window or exact-match rule).
- Leg sequencing + failure policy is documented and maps to deterministic outcomes.
- Arbitrage edge cases have explicit fail-fast behavior.

---

### Step 4 — Execution adapter plan for Kiwoom/KIS/Binance/Bybit
**Detailed TODOs**
- Build broker capability matrix (submit/cancel/status/fill callbacks; backtest/paper/live applicability).
- Define common V2 execution adapter contract and provider-specific adapter obligations.
- Declare phase scope per broker (full vs scaffold) to prevent overcommit.

**Acceptance criteria**
- Capability matrix exists and is referenced by acceptance tests.
- Unsupported broker+mode combinations fail with explicit typed errors.
- Adapter contract is provider-agnostic and strategy code remains broker-agnostic.

---

### Step 5 — Strategy author UX and CLI wiring plan
**Detailed TODOs**
- Define minimal strategy author flow in `my_strategy_a.py` for selecting strategy type/engine.
- Define CLI behavior that respects strategy type and broker selection without hidden coupling.
- Define validation flow that checks strategy type, universe bounds, and broker compatibility before run.

**Acceptance criteria**
- One-file strategy edits can switch among the 3 strategy types.
- CLI preflight rejects invalid combinations before runtime loop starts.
- Validation command targets canonical V2 strategy path and returns deterministic pass/fail.

---

### Step 6 — Verification and rollout gates
**Detailed TODOs**
- Define test matrix for all three strategy types + broker capability checks.
- Define deterministic replay checks for backtest, especially arbitrage alignment/ordering.
- Define release gate evidence bundle (tests, validation logs, matrix outcomes).

**Acceptance criteria**
- Each strategy type has at least one passing end-to-end smoke path (backtest + signal + order execution flow).
- Determinism checks pass for repeated runs with identical fixtures.
- Release gate is pass/fail objective and auditable.

## Success Criteria
- V2 can backtest, generate signals, and execute orders across single/portfolio/arbitrage strategy types under explicit contracts.
- Broker execution architecture for Kiwoom/KIS/Binance/Bybit is planned with clear capability scope and validation gates.
- Strategy author experience remains simple and centered on one canonical file.
