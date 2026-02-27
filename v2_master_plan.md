# vibetrading_v2 Master Plan (KIS-only live, bars-only)
## Executive Summary
- What will change / what will not change (cite)
  - **Will change:** v2 package boundaries will be refactored to explicit `core/ports/adapters/apps` and split into `SingleStrategyEngine` + `RebalancingEngine`, with arbitrage kept as interface-only stub (docs/context/20_v2_system_map.md §Current state snapshot; docs/context/30_gap_matrix.md §V2 Gap Matrix).
  - **Will change:** order model will move from `OrderIntent -> Fill` shortcut to lifecycle + idempotency model required by constitution-level semantics captured in context pack (docs/context/40_interfaces_and_schemas.md §Canonical Schemas (Target); docs/context/60_order_and_safety_semantics.md §Canonical broker-agnostic order lifecycle (proposed), §Idempotency strategy).
  - **Will not change:** `vibetrading_v1/**` remains reference-only and unmodified; v2 remains the implementation target (docs/context/00_repo_overview.md §Top-level shape; docs/context/20_v2_system_map.md §Current state snapshot).
- Success criteria (measurable artifacts + tests; cite)
  - Deterministic backtest emits canonical artifacts (`orders/positions/pnl/risk`) and reruns produce identical hashes under same input/config (docs/context/30_gap_matrix.md §Parquet deterministic backtest harness; docs/context/70_test_and_verification_matrix.md §Deterministic backtest verification).
  - Layered tests pass: core unit tests, adapter contract tests, deterministic backtest checks, and optional gated live smoke tests (docs/context/70_test_and_verification_matrix.md §Unit/contract/smoke matrix by layer, §Live smoke test policy).
  - Live trading path enforces dual gate (`LIVE_API=1` and `CONFIRM_LIVE=YES`) at app and adapter boundaries (docs/context/60_order_and_safety_semantics.md §Live safety gates and default behavior).

## Current State (Evidence Only)
- v1 reality snapshot (cite context pack)
  - v1 is a microservice-style runtime with data/signal/execution/risk services and broader infra coupling; it is not the v2 correctness target (docs/context/00_repo_overview.md §Top-level shape; docs/context/10_v1_system_map.md §Entry points and runtime orchestration, §Coupling hotspots).
- v2 current state + boundary violations (cite context pack)
  - v2 currently uses `core/data/execution/runner` with no explicit `ports/adapters/apps` directories; boundary intent is only partially encoded (docs/context/20_v2_system_map.md §Current state snapshot, §Ports/adapters alignment and boundary check).
  - v2 live entry path has no dual env live gate enforcement today (docs/context/30_gap_matrix.md §Live/paper safety gates; docs/context/60_order_and_safety_semantics.md §Observed).
- Gap summary keyed to gap matrix rows (cite)
  - P0 gaps: 1m-only enforcement, deterministic artifacts/hashes, broker lifecycle parity, live safety gates, bar semantics invariants, idempotency/state machine, artifact outputs (docs/context/30_gap_matrix.md §V2 Gap Matrix).
  - P1 gaps: engine family split, kill-switch runtime enforcement, full boundary/package split enforcement (docs/context/30_gap_matrix.md §V2 Gap Matrix).

## Target Architecture
- Directory layout (tree snippet)
```text
vibetrading_v2/
  core/
    engines/
      single_strategy.py
      rebalancing.py
    models/
      bar.py signal.py target_weights.py order.py fill.py portfolio_state.py risk_state.py
    services/
      order_state_machine.py risk_checks.py data_readiness.py
  ports/
    bar_data_source.py
    broker.py
    clock.py
    state_store.py
  adapters/
    datasource_parquet.py
    datasource_kis.py
    broker_paper.py
    broker_kis.py
    clock_live.py
    clock_backtest.py
  apps/
    backtest.py
    trade_paper.py
    trade_live.py
```
  (Target direction justified by v2 current mismatch and constitution-aligned gaps: docs/context/20_v2_system_map.md §Current state snapshot; docs/context/30_gap_matrix.md §Adapter boundaries.)
- Dependency rules (allowed imports / banned imports)
  - `core/**` imports only stdlib + `vibetrading_v2.core.*` + `vibetrading_v2.ports.*`; no network/db SDK imports (docs/context/20_v2_system_map.md §Ports/adapters alignment and boundary check; docs/context/60_order_and_safety_semantics.md §Live safety gates and default behavior).
  - `adapters/**` may import SDK/network/IO; `apps/**` are composition roots only (docs/context/20_v2_system_map.md §Current state snapshot).
- Ports/adapters diagram (ASCII ok)
```text
apps(backtest/paper/live)
  -> core.engines (single_strategy | rebalancing)
    -> ports.BarDataSource / ports.Broker / ports.Clock / ports.StateStore
      -> adapters.datasource_parquet | adapters.datasource_kis
      -> adapters.broker_paper | adapters.broker_kis
```
- Explicit “core purity” enforcement plan (lint rules or import checks if present in context pack; cite)
  - Reuse strategy sandbox denylist patterns as baseline and extend to repo-wide import-boundary tests (docs/context/20_v2_system_map.md §Ports/adapters alignment and boundary check; docs/context/30_gap_matrix.md §Adapter boundaries).

## Canonical Schemas (Authoritative)
- Bar / Signal / TargetWeights / Order / Fill / PortfolioState / RiskState
  - Authoritative target schema set is defined in context pack and used as baseline for implementation (docs/context/40_interfaces_and_schemas.md §Canonical Schemas (Target)).
- For each: fields/types/invariants + citations (context pack)
  - `Bar`: UTC, close-time semantics, timeframe=`1m`, `is_closed`; invariants from bar semantics doc (docs/context/40_interfaces_and_schemas.md §Bar (1-minute OHLCV); docs/context/50_bar_semantics.md §Decision: Canonical Timestamp Convention, §Testable Invariants).
  - `Signal`, `TargetWeights`, `Order`, `Fill`, `PortfolioState`, `RiskState`: use field sets defined in schema table; order lifecycle states and risk kill-switch fields are required (docs/context/40_interfaces_and_schemas.md §Signal/TargetWeights/Order/Fill/PortfolioState/RiskState; docs/context/60_order_and_safety_semantics.md §Canonical broker-agnostic order lifecycle).
- Any change must be labeled PROPOSED + justified + migration impact
  - **PROPOSED:** rename current `Bar.timestamp` to canonical `Bar.ts` alias while preserving backward-compatible loader mapping during migration (docs/context/40_interfaces_and_schemas.md §Mapping to Existing Code and Mismatches).

## Engine Designs
### SingleStrategyEngine
- Lifecycle, inputs/outputs, event loop
  - Input: normalized closed 1m bars; strategy outputs signals/order intents; engine maps to broker order requests through lifecycle service (docs/context/40_interfaces_and_schemas.md §Signal, §Order; docs/context/50_bar_semantics.md §Testable Invariants).
- Risk hooks + sizing hooks
  - Pre-submit checks: sizing policy, max position/drawdown, kill-switch status; post-fill reconciliation updates `PortfolioState`/`RiskState` (docs/context/40_interfaces_and_schemas.md §RiskState; docs/context/60_order_and_safety_semantics.md §Kill-switch semantics).
- Determinism requirements (cite)
  - Backtest mode must process chronologically and emit deterministic artifacts/hash outputs (docs/context/70_test_and_verification_matrix.md §Deterministic backtest verification; docs/context/50_bar_semantics.md §Deterministic Data Quality Rules).

### RebalancingEngine
- Target weights flow
  - Strategy/optimizer emits `TargetWeights`; engine computes delta orders, applies turnover/risk constraints, then submits idempotent order requests (docs/context/40_interfaces_and_schemas.md §TargetWeights, §Order; docs/context/60_order_and_safety_semantics.md §Idempotency strategy).
- Optimizer interface (pluggable), constraints, scheduling, turnover controls
  - Pluggable optimizer returns candidate weights; scheduler triggers at configured rebalance windows; turnover caps enforced before order generation (docs/context/40_interfaces_and_schemas.md §TargetWeights; docs/context/30_gap_matrix.md §Engine split).
- Determinism requirements (cite)
  - Same input bars + same optimizer config must reproduce same target weights and artifact hashes (docs/context/70_test_and_verification_matrix.md §Deterministic backtest verification).

### Arbitrage Stub (interfaces only)
- Stub interfaces only; no implementation
  - Add interface placeholders only; no websocket/live arbitrage runtime in current scope (docs/context/30_gap_matrix.md §Engine split: RebalancingEngine vs SingleStrategyEngine (Arb stub only)).

## Market Data Strategy (KIS-only)  (REQUIRED)
Must include (with KIS MCP citations):
- Which KIS endpoints provide 1m bars OR what must be combined to build 1m bars
  - **Blocked:** KIS MCP was unreachable in this environment; endpoint-level confirmation is pending (KIS MCP: `smithery mcp search "kis-code-assistant-mcp" --table` → `Failed to search servers: Connection error.`).
  - Interim repo evidence indicates existing KR/US historical paths in v1 map `1m` requests to daily codes, so v2 must rely on MCP-confirmed alternatives before implementation (docs/context/40_interfaces_and_schemas.md §Compatibility Notes; docs/context/50_bar_semantics.md §Observed in V1).
- Polling cadence (<60s) and scheduling across symbols
  - **PROPOSED plan default:** 5-second poll scheduler with per-symbol rotation window, configurable 1–10 seconds; final value gated on MCP-confirmed rate limits (docs/context/30_gap_matrix.md §Bar semantics; KIS MCP: `smithery mcp add KISOpenAPI/kis-code-assistant-mcp --table` → `Connection error.`).
- Universe management:
  - Tier-1/Tier-2 definitions
    - Tier-1: symbols with open positions + symbols with actionable signals in current cycle.
    - Tier-2: watchlist symbols without current position.
  - How to stay within KIS symbol/universe limits
    - Enforce adapter scheduler budget and interleave Tier-1/Tier-2 polling; never drop Tier-2 from scheduling queue, only reduce per-cycle quota when budget pressure exists (docs/context/80_risk_register.md §Rate-limit lockout / API throttling).
- Rate-limit strategy:
  - adapter-level budgeter (token bucket/leaky bucket)
    - Adapter-level token bucket with per-endpoint budgets; budget consumption audited each cycle (docs/context/80_risk_register.md §Rate-limit lockout / API throttling).
  - how to detect throttling/limit-hit responses
    - Parse MCP-confirmed error code/status taxonomy (blocked until KIS MCP access restored) (KIS MCP: `smithery mcp search "kis-code-assistant-mcp" --table` → `Failed to search servers: Connection error.`).
- Limit-hit behavior:
  - do NOT drop Tier-2
    - Keep Tier-2 queue resident; only degrade poll frequency, never remove from universe.
  - emit LIMIT HIT notification (logging/stdout and/or artifact file)
    - Emit `LIMIT HIT` structured log + append line to local artifact file `artifacts/limits/YYYYMMDD.log`.
  - optional future Telegram hook (design only)
    - Add notifier port (`LimitNotifier`) with initial stdout/file adapter and deferred Telegram adapter.
- Bar-close alignment:
  - exact definition of “closed” bar used for decisions
    - Decision bars are only bars with `is_closed=True` and `ts` at canonical close time UTC (docs/context/50_bar_semantics.md §Decision: Canonical Timestamp Convention, §Testable Invariants).
  - handling lag and partial minute data
    - Partial bars are retained internally but blocked from strategy execution until close criteria passes.
- Data readiness gates:
  - staleness thresholds
    - **PROPOSED:** block order actions when latest closed bar age exceeds 90s.
  - invariant checks that block trading
    - Block trading on duplicate keys, out-of-order timestamps, missing OHLCV fields, or timezone-naive timestamps (docs/context/50_bar_semantics.md §Deterministic Data Quality Rules, §Testable Invariants).

## Bar Semantics (Live ↔ Parquet Parity)
- Timestamp convention + timezone policy (cite)
  - Canonical timestamp is bar close time, timezone-aware UTC, normalized at adapter boundary (docs/context/50_bar_semantics.md §Decision: Canonical Timestamp Convention; docs/context/40_interfaces_and_schemas.md §Bar (1-minute OHLCV)).
- Missing/dup/out-of-order rules (cite)
  - Missing bars are reported (not forward-filled), duplicates dedup by `(symbol, ts, timeframe)` with deterministic winner rule, and out-of-order bars sorted/rejected by stream policy (docs/context/50_bar_semantics.md §Deterministic Data Quality Rules).
- Parquet schema and storage expectations (cite)
  - Required columns: `timestamp, open, high, low, close, volume`; recommended columns include `timeframe`, `is_closed`, `source` for parity with live semantics (docs/context/50_bar_semantics.md §Parquet Storage Spec).
- Testable invariants (cite)
  - Strictly increasing timestamps, 60s spacing for contiguous segments, no duplicates, complete OHLCV, closed bars only for deterministic execution (docs/context/50_bar_semantics.md §Testable Invariants).

## Order & Safety Semantics (KIS-only execution)
- Broker-agnostic order lifecycle state machine (cite context pack; refine with KIS MCP where needed)
  - Use canonical lifecycle `Created -> Submitted -> Accepted/Rejected -> PartiallyFilled -> Filled/Cancelled/Expired` and mirror in Paper + KIS brokers (docs/context/60_order_and_safety_semantics.md §Canonical broker-agnostic order lifecycle).
  - KIS endpoint/state mapping is blocked pending MCP access restoration (KIS MCP: `smithery mcp add KISOpenAPI/kis-code-assistant-mcp --table` → `Connection error.`).
- Idempotency strategy (key rules + storage + enforcement points)
  - Mandatory idempotency key format and replay semantics at engine + broker + state store enforcement points (docs/context/60_order_and_safety_semantics.md §Idempotency strategy).
- Retry/rate-limit policy (tie to KIS MCP error taxonomy + limits)
  - Retry only transient errors with bounded backoff; classify non-retryable semantic rejects; finalize class map after MCP error taxonomy is fetched (docs/context/60_order_and_safety_semantics.md §Retry / rate-limit policy; KIS MCP connection-error evidence above).
- Kill-switch policy and enforcement points
  - Trigger stops new submissions, cancels open orders, persists kill-switch state, and requires manual reset (docs/context/60_order_and_safety_semantics.md §Canonical action on trigger).
- Live safety gates: LIVE_API=1 & CONFIRM_LIVE=YES with exact enforcement locations (apps + adapter)
  - Enforce gates in `apps/trade_live.py` startup preflight and `adapters/broker_kis.py` constructor preflight (defense-in-depth) (docs/context/60_order_and_safety_semantics.md §Canonical enforcement location).
- Default behavior: paper unless gated
  - `apps/trade_live.py` hard-fails or downgrades to paper with explicit warning if either variable missing (docs/context/60_order_and_safety_semantics.md §Live safety gates and default behavior).

## Test Strategy
- Deterministic backtest: artifacts + hash/diff method (cite)
  - Write sorted canonical artifacts and compare `sha256` across reruns; fail if mismatch (docs/context/70_test_and_verification_matrix.md §Deterministic backtest verification).
- Unit tests by layer (core / ports contracts / adapters smoke)
  - Extend from current v2 test baseline to include order lifecycle/idempotency/data-readiness contract suites (docs/context/70_test_and_verification_matrix.md §Unit/contract/smoke matrix by layer).
- Optional live smoke tests (gated; no real orders; cite KIS MCP capabilities)
  - Maintain minimal smoke scope (connect, fetch one recent 1m bar, tiny order, cancel, disconnect) behind dual env gate; concrete KIS capability mapping pending MCP access (docs/context/70_test_and_verification_matrix.md §Live smoke test policy; KIS MCP connection-error evidence above).

## Milestone Plan (M0..Mn; Vertical Slices)
For EACH milestone:
- **M0 — Architecture boundary and schema lock**
  - Goal (one sentence)
    - Freeze canonical schemas/ports and enforce core boundary checks.
  - Definition of Done (measurable checklist)
    - Schema types and ports committed; boundary test fails on forbidden core imports; current tests still green.
  - Exact files/dirs to touch (paths/globs)
    - `vibetrading_v2/core/**`, `vibetrading_v2/ports/**`, `vibetrading_v2/tests/test_boundary_*`.
  - Risks mitigated (link to risk register; cite)
    - Bar semantics mismatch, timezone bugs, dependency drift (docs/context/80_risk_register.md §Risk table).
  - Acceptance evidence commands (reference section anchors in PRD and/or this doc)
    - See PRD §Acceptance Criteria command set A1/A2.
  - Cross-component coordination notes (interfaces)
    - Core schema and ports are the only contracts adapters may depend on.

- **M1 — Deterministic backtest vertical slice**
  - Goal
    - Produce deterministic artifacts for SingleStrategyEngine using Parquet + PaperBroker.
  - Definition of Done
    - Repeat-run hash equality for orders/positions/pnl/risk artifacts on same fixture.
  - Exact files/dirs to touch
    - `vibetrading_v2/adapters/datasource_parquet.py`, `vibetrading_v2/adapters/broker_paper.py`, `vibetrading_v2/apps/backtest.py`, `vibetrading_v2/tests/test_determinism_*`.
  - Risks mitigated (cite)
    - Look-ahead leakage, data gaps/dup/out-of-order, idempotency bugs (docs/context/80_risk_register.md §Risk table).
  - Acceptance evidence commands
    - See PRD §Acceptance Criteria command set A3/A4.
  - Coordination notes
    - Engine output schema must match artifact writer schema exactly.

- **M2 — Rebalancing engine vertical slice**
  - Goal
    - Add target-weight flow with deterministic order generation.
  - Definition of Done
    - Deterministic rebalance decisions and bounded turnover constraints validated by tests.
  - Exact files/dirs to touch
    - `vibetrading_v2/core/engines/rebalancing.py`, `vibetrading_v2/core/models/target_weights.py`, `vibetrading_v2/tests/test_rebalancing_*`.
  - Risks mitigated (cite)
    - Partial fills drift, silent failures (docs/context/80_risk_register.md §Risk table).
  - Acceptance evidence commands
    - See PRD §Acceptance Criteria command set A5.
  - Coordination notes
    - Shared order lifecycle service used by both engines.

- **M3 — KIS market data adapter + readiness gates**
  - Goal
    - Implement KIS-only live/paper market data ingest with readiness gating and limit-hit notifications.
  - Definition of Done
    - Polling scheduler under <60s cadence; readiness gate blocks stale/invalid data; `LIMIT HIT` notification produced.
  - Exact files/dirs to touch
    - `vibetrading_v2/adapters/datasource_kis.py`, `vibetrading_v2/core/services/data_readiness.py`, `vibetrading_v2/tests/test_kis_data_*`.
  - Risks mitigated (cite)
    - Rate-limit lockout, bar semantic mismatch, timezone bugs (docs/context/80_risk_register.md §Risk table).
  - Acceptance evidence commands
    - See PRD §Acceptance Criteria command set A6.
  - Coordination notes
    - Final endpoint/rate-limit values require KIS MCP evidence before coding.

- **M4 — KIS broker adapter + live safety gates**
  - Goal
    - Implement KIS execution adapter with lifecycle parity, idempotency, retries, and dual live env gates.
  - Definition of Done
    - All live submits blocked without dual vars; idempotency replay semantics pass; optional gated smoke runs pass.
  - Exact files/dirs to touch
    - `vibetrading_v2/adapters/broker_kis.py`, `vibetrading_v2/apps/trade_live.py`, `vibetrading_v2/apps/trade_paper.py`, `vibetrading_v2/tests/test_live_gate_*`.
  - Risks mitigated (cite)
    - Unsafe live defaults, duplicate orders, config mistakes, rate-limit lockout (docs/context/80_risk_register.md §Risk table).
  - Acceptance evidence commands
    - See PRD §Acceptance Criteria command sets A7/A8.
  - Coordination notes
    - App and adapter each enforce same gate policy (defense-in-depth).

## Risk Register Mapping
- Bar semantics mismatch → M0/M3 + invariant tests in data adapters (docs/context/80_risk_register.md §Bar semantics mismatch).
- Look-ahead leakage → M1 deterministic chronological tests (docs/context/80_risk_register.md §Look-ahead / leakage in backtests).
- Data gaps/duplicates/out-of-order → M1/M3 adapter validators + gate metrics (docs/context/80_risk_register.md §Data gaps / duplicates / out-of-order bars).
- Idempotency and duplicate orders → M1/M4 idempotency key + replay tests (docs/context/80_risk_register.md §Idempotency bugs; §Duplicate orders from at-least-once messaging retries).
- Rate-limit lockout → M3/M4 budgeter + throttle classification tests (docs/context/80_risk_register.md §Rate-limit lockout / API throttling).
- Unsafe live defaults/config mistakes → M4 app+adapter preflight tests (docs/context/80_risk_register.md §Config mistakes; §Unsafe live trading defaults).

## Open Questions
- KIS MCP connectivity blocker
  - Missing: authoritative endpoint list, minute-bar acquisition method, order endpoint capabilities, error taxonomy, and explicit rate/symbol/session limits.
  - Why blocking: KIS-only adapter and safety/rate-limit implementation cannot be specified to executable detail without these specs.
  - EXACT read-only steps:
    1. `smithery auth login`
    2. `smithery mcp search "kis-code-assistant-mcp" --table`
    3. `smithery mcp add KISOpenAPI/kis-code-assistant-mcp --table`
    4. `smithery mcp list --table`
    5. `smithery tool --help` then run read-only tool queries for:
       - minute bar endpoint(s) or aggregation recipe,
       - order submit/cancel/status endpoints and order types,
       - throttle/rate-limit rules,
       - universe/symbol constraints,
       - session/auth refresh constraints,
       - retryable vs non-retryable error codes.
- Polling cadence finalization blocker
  - Missing: MCP-backed request budgets per endpoint/universe.
  - Why blocking: exact poll cadence and batch size must satisfy KIS limits without dropping Tier-2.
  - EXACT read-only steps: query KIS MCP for per-second/per-minute limits and response throttling signatures; then compute cadence budget table in this plan.

## Evidence Appendix (REQUIRED)
- Context pack sections used (list)
  - docs/context/00_repo_overview.md (§Top-level shape)
  - docs/context/20_v2_system_map.md (§Current state snapshot, §Ports/adapters alignment and boundary check)
  - docs/context/30_gap_matrix.md (§V2 Gap Matrix)
  - docs/context/40_interfaces_and_schemas.md (§Canonical Schemas, §Mapping to Existing Code and Mismatches)
  - docs/context/50_bar_semantics.md (§Decision: Canonical Timestamp Convention, §Deterministic Data Quality Rules, §Testable Invariants)
  - docs/context/60_order_and_safety_semantics.md (§Canonical lifecycle, §Idempotency, §Live safety gates)
  - docs/context/70_test_and_verification_matrix.md (§Deterministic backtest verification, §Unit/contract/smoke matrix, §Live smoke policy)
  - docs/context/80_risk_register.md (§Risk table)
- KIS MCP commands/queries used + short output excerpts (paste)
  - `smithery mcp search "kis-code-assistant-mcp" --table`
    - Output excerpt: `✗ Failed to search servers: Connection error.`
  - `smithery mcp add KISOpenAPI/kis-code-assistant-mcp --table`
    - Output excerpt: `Connection error.`
  - `smithery auth whoami --table`
    - Output excerpt: `Not logged in` / `Run 'smithery auth login' to authenticate`
