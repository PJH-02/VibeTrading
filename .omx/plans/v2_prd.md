# vibetrading_v2 PRD (KIS-only live, bars-only)
## Product Vision
vibetrading_v2 will be a deterministic, modular, bars-only auto-trading platform where core logic remains pure and interchangeable adapters deliver backtest, paper, and gated live execution using a single broker/data abstraction; correctness is anchored on deterministic Parquet+Paper backtests and safety is anchored on explicit live gating and lifecycle-consistent execution semantics (docs/context/00_repo_overview.md §Top-level shape; docs/context/30_gap_matrix.md §V2 Gap Matrix; docs/context/70_test_and_verification_matrix.md §Deterministic backtest verification; docs/context/60_order_and_safety_semantics.md §Live safety gates and default behavior).

## Goals and Non-Goals
- Goals (bullets)
  - Deliver two in-scope engines: `SingleStrategyEngine` and `RebalancingEngine` (docs/context/30_gap_matrix.md §Engine split).
  - Enforce 1-minute OHLCV-only bar contract and live↔parquet semantic parity (docs/context/30_gap_matrix.md §1-min OHLCV only; docs/context/50_bar_semantics.md §Decision, §Testable Invariants).
  - Establish deterministic backtest artifacts + rerun hash verification as primary DoD (docs/context/70_test_and_verification_matrix.md §Deterministic backtest verification).
  - Enforce broker lifecycle parity and idempotency across paper/live adapters (docs/context/40_interfaces_and_schemas.md §Order; docs/context/60_order_and_safety_semantics.md §Canonical lifecycle, §Idempotency strategy).
  - Use KIS-only live data/execution path once MCP-backed specs are available (KIS MCP evidence currently blocked: `smithery mcp search ...` connection error).
- Non-Goals (include arbitrage implementation, orderbook, websocket requirement)
  - No arbitrage engine implementation (interfaces only).
  - No orderbook/depth-based logic.
  - No websocket requirement in this phase; polling-based live bar ingestion is acceptable.

## Users and Use Cases
- Primary user workflows:
  - deterministic backtest workflow
    - Inputs: strategy bundle, symbols, parquet bars, policy overrides.
    - Outputs: deterministic artifacts (`orders/positions/pnl/risk`) + hash verification report.
    - Constraints: strict chronological bars, schema invariants, no live side effects (docs/context/70_test_and_verification_matrix.md §Deterministic backtest verification; docs/context/50_bar_semantics.md §Testable Invariants).
  - paper trading workflow
    - Inputs: live/polled bars, strategy config, paper broker config.
    - Outputs: lifecycle-consistent paper orders/fills/portfolio/risk logs.
    - Constraints: same lifecycle shape as live; idempotency required (docs/context/30_gap_matrix.md §Paper broker lifecycle parity; docs/context/60_order_and_safety_semantics.md §Canonical lifecycle, §Idempotency strategy).
  - live trading workflow (gated)
    - Inputs: same as paper + live credentials/connection context.
    - Outputs: live order state transitions + safety audit logs.
    - Constraints: blocked unless `LIVE_API=1` and `CONFIRM_LIVE=YES`; default behavior remains paper or hard-fail (docs/context/60_order_and_safety_semantics.md §Live safety gates and default behavior).

## Functional Requirements
Must include:
- Engines: SingleStrategyEngine + RebalancingEngine behaviors
  - FR-1: Implement `SingleStrategyEngine` event loop with data-readiness and risk hooks.
  - FR-2: Implement `RebalancingEngine` target-weight pipeline with turnover constraints.
  - FR-3: Implement Arbitrage interfaces only (no runtime implementation).
  - Evidence: docs/context/30_gap_matrix.md §Engine split; docs/context/40_interfaces_and_schemas.md §TargetWeights.
- Data: 1m OHLCV only
  - FR-4: Enforce `timeframe == "1m"` at schema/load boundaries.
  - FR-5: Enforce canonical bar semantics (`ts` = close time UTC, `is_closed` gating).
  - Evidence: docs/context/30_gap_matrix.md §1-min OHLCV only; docs/context/50_bar_semantics.md §Decision, §Testable Invariants.
- Backtest determinism artifacts + verification
  - FR-6: Persist deterministic artifact set (`orders`, `positions`, `pnl`, `risk`).
  - FR-7: Provide repeat-run hash comparison command and fail-on-diff behavior.
  - Evidence: docs/context/70_test_and_verification_matrix.md §Deterministic backtest verification.
- Paper broker lifecycle parity with live
  - FR-8: Paper broker must use canonical lifecycle states identical to live broker model.
  - Evidence: docs/context/60_order_and_safety_semantics.md §Canonical lifecycle; docs/context/30_gap_matrix.md §Paper broker lifecycle parity.
- KIS-only live market data ingestion (polling <60s)
  - FR-9: Live/paper market data adapter is KIS-only and polling-based with configured cadence under 60s.
  - FR-10: Scheduler must maintain Tier-1/Tier-2 queues without dropping Tier-2.
  - Evidence: KIS MCP endpoint/limit details currently blocked by connectivity (`smithery mcp search ...` connection error); repo confirms no current KIS adapter in v2 (docs/context/20_v2_system_map.md §Intentionally excluded / placeholder areas).
- KIS-only order execution (paper/sandbox/live gated)
  - FR-11: Live execution adapter uses KIS-only endpoint surface and supports submit/cancel/status/fills mapping.
  - FR-12: Optional smoke path uses sandbox/paper where KIS supports it.
  - Evidence: KIS MCP capability confirmation pending (connection error evidence); interface requirements from docs/context/40_interfaces_and_schemas.md §Canonical Ports.
- Limit handling:
  - do NOT drop Tier-2
    - FR-13: Tier-2 remains scheduled; frequency may degrade but membership remains.
  - must emit LIMIT HIT notification (design hook for Telegram later)
    - FR-14: Emit structured `LIMIT HIT` log and artifact event; notifier port allows future Telegram adapter.
  - Evidence: docs/context/80_risk_register.md §Rate-limit lockout / API throttling.
- Safety gates, kill-switch, idempotency
  - FR-15: Dual env-gate enforced at app and adapter layers.
  - FR-16: Kill-switch must stop new submissions and cancel open orders.
  - FR-17: Idempotency keys mandatory with replay-safe behavior.
  - Evidence: docs/context/60_order_and_safety_semantics.md §Canonical action on trigger, §Live safety gates, §Idempotency strategy.

## Non-Functional Requirements
- Determinism
  - NFR-1: Same dataset + config => identical artifacts/hash outputs (docs/context/70_test_and_verification_matrix.md §Deterministic backtest verification).
- Safety (no live by default; explicit gating)
  - NFR-2: live disabled unless dual env gates pass (docs/context/60_order_and_safety_semantics.md §Live safety gates and default behavior).
- Observability (logs/artifacts; limit-hit messages)
  - NFR-3: lifecycle transitions, gate denials, and limit-hit events are auditable.
- Reliability under rate limits
  - NFR-4: bounded retry/backoff and throttle classification policy implemented.
- Maintainability (ports/adapters boundaries)
  - NFR-5: core has no external I/O imports; all integration lives in adapters.
- Performance constraints (symbol counts, polling cadence)
  - NFR-6: configured poll cycle <60s; final symbol/cadence matrix contingent on KIS MCP constraints.

## Constraints and Assumptions (Evidence-based)
- Extract KIS constraints from MCP (rate limits, symbol caps, session rules)
  - **Constraint status: BLOCKED.** KIS MCP not reachable in current environment.
  - Evidence:
    - `smithery mcp search "kis-code-assistant-mcp" --table` → `Failed to search servers: Connection error.`
    - `smithery mcp add KISOpenAPI/kis-code-assistant-mcp --table` → `Connection error.`
    - `smithery auth whoami --table` → `Not logged in`
- Extract repo constraints from context pack (current layout, tooling)
  - Current v2 structure lacks required `ports/adapters/apps` split (docs/context/20_v2_system_map.md §Current state snapshot).
  - Existing tests validate only limited flow and need expansion to lifecycle/safety/determinism artifacts (docs/context/70_test_and_verification_matrix.md §Current observed test surface).
  - Live gating currently absent in runtime code (docs/context/30_gap_matrix.md §Live/paper safety gates).
- Anything not evidenced must be an Open Question
  - All KIS endpoint/rate/error/session specifics are open questions pending MCP access.

## Acceptance Criteria
- Measurable PASS/FAIL criteria tied to:
  - deterministic backtest hashes
    - A1 (PASS): Run identical backtest twice; `sha256` on canonical artifacts matches exactly.
  - unit test suites
    - A2 (PASS): Core schema, lifecycle, idempotency, and readiness tests pass.
  - adapter contract tests
    - A3 (PASS): Parquet + Paper + KIS adapter contract tests pass with deterministic semantics and lifecycle parity.
  - optional gated live smoke tests (no real orders)
    - A4 (PASS): smoke suite runs only with `LIVE_API=1` and `CONFIRM_LIVE=YES`; without gates it hard-fails or downgrades to paper with explicit warning.
- Must cite context pack test matrix and KIS MCP capabilities
  - Test matrix basis: docs/context/70_test_and_verification_matrix.md §Unit/contract/smoke matrix by layer, §Live smoke test policy.
  - KIS capability mapping: pending MCP connectivity restoration (connection error evidence in appendix).

## Rollout Plan
- Phase 0: backtest correctness
  - Success metrics: deterministic artifact hash equality, invariant tests green.
  - Rollback criteria: any hash drift or invariant violation.
- Phase 1: paper trading with KIS data (if supported) or KIS data + PaperBroker
  - Success metrics: stable polling loop, readiness gates active, lifecycle parity under paper mode.
  - Rollback criteria: repeated limit-hit without controlled degradation, or lifecycle divergence.
- Phase 2: gated live trading with KIS
  - Success metrics: gate enforcement, bounded retries, optional tiny-order smoke success.
  - Rollback criteria: unsafe gate bypass, unclassified adapter errors, or kill-switch enforcement failures.

## Risks and Mitigations
- Pull top risks from docs/context/80_risk_register.md (cite)
  - Bar semantics mismatch → enforce canonical schema + invariants tests.
  - Data gaps/dup/out-of-order → adapter validators + readiness gate blocks.
  - Idempotency bugs/duplicate orders → mandatory idempotency key + replay checks.
  - Rate-limit lockout → budgeter + throttle classification + LIMIT HIT events.
  - Unsafe live defaults/config mistakes → dual live-gate preflight at app+adapter.
  - Evidence: docs/context/80_risk_register.md §Risk table.
- Map to PRD-level mitigations and required tests/metrics
  - Mapped to Acceptance A1–A4 plus milestone-specific tests in master plan.

## Open Questions
- KIS MCP access is currently unavailable; authoritative API details are missing.
  - Blockers:
    1. Minute-bar endpoint(s) or aggregation inputs.
    2. Order endpoints and supported order types.
    3. Rate-limit quotas and throttling signatures.
    4. Max symbols/universe constraints.
    5. Session/auth/token refresh constraints.
    6. Retryable vs non-retryable error taxonomy.
  - Required read-only resolution steps:
    1. `smithery auth login`
    2. `smithery mcp search "kis-code-assistant-mcp" --table`
    3. `smithery mcp add KISOpenAPI/kis-code-assistant-mcp --table`
    4. Run read-only Smithery tool queries for the six blocker topics above and append outputs to both evidence appendices.

## Evidence Appendix (REQUIRED)
- Context pack sections used (list)
  - docs/context/00_repo_overview.md (§Top-level shape)
  - docs/context/20_v2_system_map.md (§Current state snapshot; §Intentionally excluded / placeholder areas)
  - docs/context/30_gap_matrix.md (§V2 Gap Matrix)
  - docs/context/40_interfaces_and_schemas.md (§Canonical Schemas; §Canonical Ports)
  - docs/context/50_bar_semantics.md (§Decision; §Testable Invariants)
  - docs/context/60_order_and_safety_semantics.md (§Canonical lifecycle; §Idempotency; §Live safety gates)
  - docs/context/70_test_and_verification_matrix.md (§Deterministic verification; §Unit/contract/smoke matrix; §Live smoke policy)
  - docs/context/80_risk_register.md (§Risk table)
- KIS MCP commands/queries used + short output excerpts (paste)
  - `smithery mcp search "kis-code-assistant-mcp" --table`
    - `✗ Failed to search servers: Connection error.`
  - `smithery mcp add KISOpenAPI/kis-code-assistant-mcp --table`
    - `Connection error.`
  - `smithery auth whoami --table`
    - `Not logged in`
