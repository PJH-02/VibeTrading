# VibeTrading V2 Production-Readiness Work Plan

## Context
User-selected plan scope: **Production-readiness** for V2 scaffold.
Canonical strategy path is fixed to: `vibetrading_V2/strategies/my_strategy_a.py`.
Planning scope focuses on `vibetrading_V2/` and `cli/` plus minimal release/CI metadata needed to make readiness verifiable.

## Work Objectives
1. Define objective, reproducible quality gates (tests/lint/type/strategy validation).
2. Enforce deterministic backtest behavior and explicit runtime data contract.
3. Establish CI/release hygiene so V2 readiness is measurable and repeatable.

## Guardrails
### Must Have
- All readiness checks are pass/fail and command-verifiable.
- Canonical strategy location and validation targets are consistent across CLI/docs/tests.
- Backtest determinism is explicitly defined and tested.
- Strategy sandbox + bundle validation remain enforced at load-time.

### Must NOT Have
- No V1/legacy architecture rewrite.
- No scope expansion into broker production integrations.
- No ETL/data-write responsibilities added to runtime code.
- No ambiguous “looks good” quality criteria.

## Task Flow (5 steps)

### Step 1 — Freeze readiness contract and scope boundaries
**TODOs**
- Consolidate source-of-truth statements for:
  - canonical strategy path (`vibetrading_V2/strategies/my_strategy_a.py`)
  - validation directory defaults
  - in-scope directories for quality gates
- Record explicit out-of-scope list (legacy `strategies/`, V1 modules, external broker rollout).

**Acceptance criteria**
- One written readiness contract exists (plan-linked doc/update) with no path ambiguity.
- Gate scope (included/excluded paths) is explicitly listed and unambiguous.

---

### Step 2 — Define and wire mandatory quality gates
**TODOs**
- Standardize required commands and exit behavior for:
  - unit tests
  - lint
  - type-check
  - strategy validation
- Add missing tooling configuration needed to run gates consistently in clean environments.
- Define waiver policy (when a gate may be temporarily bypassed and who approves).

**Acceptance criteria**
- A documented gate matrix exists with exact commands + expected pass conditions.
- Running the full gate set in a clean environment yields deterministic pass/fail output.
- Gate failures return non-zero exits and clear operator-facing messages.

---

### Step 3 — Determinism and runtime contract hardening
**TODOs**
- Define mode-specific clock policy:
  - backtest: replay/fixed deterministic clock
  - paper/live: realtime policy
- Specify runtime data contract (required fields, timestamp expectations, ordering).
- Add tests proving deterministic repeatability and required-field enforcement behavior.

**Acceptance criteria**
- Re-running backtest with identical inputs produces identical result artifacts/metrics.
- Missing required runtime fields fails fast with typed, documented errors.
- Timestamp/order handling behavior is documented and test-covered.

---

### Step 4 — CI pipeline and release hygiene
**TODOs**
- Create/align CI workflow(s) to run all mandatory gates on declared matrix.
- Define release checklist (versioning/changelog/provenance minimums).
- Ensure CI artifacts/logs are sufficient for go/no-go decisions.

**Acceptance criteria**
- CI runs all mandatory gates and blocks merge/release on failures.
- Release checklist exists and is executable without tribal knowledge.
- A dry-run release pass confirms checklist completeness.

---

### Step 5 — Production-readiness verification and sign-off
**TODOs**
- Run full readiness suite end-to-end on target matrix.
- Capture evidence bundle (gate outputs, determinism rerun evidence, validation logs).
- Execute final go/no-go checklist with explicit owner sign-off.

**Acceptance criteria**
- All mandatory gates pass on the declared matrix.
- Determinism checks pass with reproducible evidence.
- Final sign-off artifact marks V2 status as Ready / Not Ready with reasons.

## Success Criteria
- 100% mandatory gates pass on declared matrix.
- Backtest determinism and strategy validation are both proven by test evidence.
- Release process is reproducible and documented.
- No unresolved P0 open question remains at sign-off time.
