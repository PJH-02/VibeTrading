# Worker Assignment: worker-4

**Team:** context-pack-generation-read-o
**Role:** explore
**Worker Name:** worker-4

## Your Assigned Tasks

- **Task 4**: Worker 4 bootstrap
  Description: Coordinate on: CONTEXT PACK GENERATION (READ-ONLY AUDIT; NO CODE CHANGES)

REPO SHAPE
- vibetrading_v1/ = legacy system (source of truth for current behavior)
- vibetrading_v2/ = target system (in-progress)
GOAL
- Produce a docs-only “context pack” that captures CURRENT REALITY: entrypoints, runtime flows, contracts/interfaces, and gaps—so a v2 master plan can be written without missing repo details.

NON-NEGOTIABLE RULES
1) WRITE SCOPE: You may ONLY create/modify files under: docs/context/*.md
   - If any non-doc file changes for ANY reason:
     a) Immediately REVERT: git checkout -- .
     b) Report what changed and why (include `git status --porcelain` output).
2) SAFETY:
   - Do NOT run live orders. Do NOT require secrets. Do NOT print secrets.
   - Do NOT add prompts that require API keys. Redact any accidental secret output immediately.
3) ALLOWED COMMANDS (read-only / check-only):
   - Inspection: ls, tree, fd, rg, cat, sed/head/tail, git grep, git show
   - Python import checks: python -c "import ..."
   - Tests discovery only: pytest --collect-only
   - Linters/type check in check-only mode: ruff check, mypy (no auto-fix)
   - Git status/diff: git status, git diff (for docs/context only)
4) EVIDENCE STANDARD (no guessing):
   - Every claim MUST cite concrete evidence: file paths + symbol names + (preferably) line ranges OR command output snippets.
   - Use citations like: `path/to/file.py:123-156 (SymbolName)` or fenced command outputs.
5) EACH MARKDOWN FILE must end with a section titled exactly: `## Evidence`
   - Include:
     - Commands run (exact commands)
     - Key outputs/findings (short excerpts)
     - At least 5 concrete references to repo files/symbols per file

WORKER OWNERSHIP (NO OVERLAP)
- Worker1: 00_repo_overview.md + 10_v1_system_map.md
- Worker2: 20_v2_system_map.md + 30_gap_matrix.md
- Worker3: 40_interfaces_and_schemas.md + 50_bar_semantics.md
- Worker4: 60_order_and_safety_semantics.md + 70_test_and_verification_matrix.md + 80_risk_register.md
Rules:
- Only edit the files you own.
- You may READ anything in repo, but only WRITE your assigned markdown files.

OUTPUT DIRECTORY
- Ensure docs/context/ exists (create if missing).
- Create EXACTLY these 9 files (no extras):
  1) docs/context/00_repo_overview.md
  2) docs/context/10_v1_system_map.md
  3) docs/context/20_v2_system_map.md
  4) docs/context/30_gap_matrix.md
  5) docs/context/40_interfaces_and_schemas.md
  6) docs/context/50_bar_semantics.md
  7) docs/context/60_order_and_safety_semantics.md
  8) docs/context/70_test_and_verification_matrix.md
  9) docs/context/80_risk_register.md

GLOBAL WORKFLOW (ALL WORKERS)
A) Establish repo map:
   - tree/ls at root and inside vibetrading_v1/ and vibetrading_v2/
   - find entrypoints: pyproject scripts, __main__.py, cli modules, bin scripts
   - find config/env usage: rg for os.environ, dotenv, config loaders, YAML/TOML/JSON
B) Gather evidence BEFORE writing conclusions:
   - For each section you write, collect at least 2-3 direct code references.
C) Write your markdown file(s), then run:
   - git diff -- docs/context
   - Ensure your file ends with `## Evidence`
D) NEVER run anything that could place orders; keep tests to collect-only.

FILE REQUIREMENTS (STRICT)

(00) docs/context/00_repo_overview.md  [Worker1]
Include:
- Top-level tree summary (v1 vs v2): key packages/modules and likely entrypoints.
- Discovered run paths: CLIs/scripts (python -m ..., console_scripts, entry modules), config files, referenced env vars.
- Dependency/tooling: pyproject/requirements, linters/tests, how invoked.
- CI config (GitHub Actions etc.) and current test invocation.

(10) docs/context/10_v1_system_map.md  [Worker1]
Include:
- Entry points: where v1 starts; major runtime loops/schedulers.
- Data path: how v1 obtains OHLCV (or nearest equivalent), frequency, caching, persistence (DB/files), schemas.
- Strategy path: signal generation modules, portfolio/position representation, risk constraints.
- Execution path: order placement workflow, idempotency (if any), retries/rate-limits, error handling, kill-switches.
- Coupling hotspots: circular imports, global state, shared mutable singletons, IO tightly coupled to logic.
- Bullet list of key files/symbols (>=15) with one-line roles.
All claims must be backed by code refs.

(20) docs/context/20_v2_system_map.md  [Worker2]
Include:
- Current state: folder layout, existing modules, what matches ports/adapters, what violates boundaries.
- For each existing v2 module: responsibilities + key classes/functions + dependencies.
- What was intentionally excluded (data collection, DB) and current placeholders/stubs.
- Bullet list of key files/symbols (>=15) with one-line roles.

(30) docs/context/30_gap_matrix.md  [Worker2]
Provide a table with columns:
Requirement | Current Status | Evidence (file/symbol) | Gap | Proposed Fix (high-level) | Priority
Must include rows for:
- 1-min OHLCV only (no orderbook)
- Parquet deterministic backtest harness
- Paper broker with same order lifecycle shape as live
- Live/paper safety gates (LIVE_API=1 & CONFIRM_LIVE=YES)
- Engine split: RebalancingEngine vs SingleStrategyEngine (Arb stub only)
- Bar semantics: timestamp meaning, timezone, bar-close rules, missing/dup/out-of-order handling
- Order state machine + idempotency
- Kill-switch policy
- Adapter boundaries (core import bans)
- Backtest report outputs (orders/positions/pnl/risk artifacts)
Each row must cite evidence, even if status is “missing”.

(40) docs/context/40_interfaces_and_schemas.md  [Worker3]
Include canonical schemas (fields + meaning + types):
- Bar (1m OHLCV), Signal, TargetWeights, Order, Fill, PortfolioState, RiskState
Include canonical ports (Python Protocol/ABC signatures):
- BarDataSource (historical + live), Broker (paper + live), Clock, StateStore
Map each schema/port to existing code references (v1/v2) and call out mismatches.
Include: `## Compatibility Notes` for Kiwoom/KIS constraints discovered in code (if any). If none found, say so and cite your search (rg results).

(50) docs/context/50_bar_semantics.md  [Worker3]
Define ONE canonical timestamp convention (recommend bar CLOSE time) and justify using repo evidence.
Specify:
- Timezone policy, session boundary assumptions, holidays/weekends handling if present in code/config.
- Deterministic rules:
  - missing bars (explicit policy: skip/mark gap/fill and how)
  - duplicate bars (dedup key + winner rule)
  - out-of-order bars (buffer/sort/reject rule)
- Testable invariants (monotonic ts, fixed 1m spacing, no duplicates, etc.)
- Parquet storage spec (schema/columns) matching live semantics
Back every “current behavior” statement with v1/v2 references; clearly label “proposed” vs “observed”.

(60) docs/context/60_order_and_safety_semantics.md  [Worker4]
Define:
- Broker-agnostic order lifecycle state machine: states + transitions + terminal states.
- Idempotency strategy: key generation rules, storage, enforcement points (app vs adapter vs core).
- Retry/rate-limit policy: which errors retry, backoff, max attempts, fail-fast conditions.
- Kill-switch: triggers, actions (cancel/flatten/stop), enforcement location(s).
- Live safety gates: exact env gates and exactly where enforced (apps layer + adapters).
- Default behavior: paper unless gated.
Wherever possible, tie to existing patterns in v1 and current v2 direction; cite both.

(70) docs/context/70_test_and_verification_matrix.md  [Worker4]
Include:
- Deterministic backtest verification:
  - how to run, artifact locations, hash/diff approach, tolerances (if any)
  - required fixtures / example parquet layout
- Unit tests by layer:
  - core (strategy/engine/risk/order state machine)
  - ports contract tests (fake adapters)
  - adapter smoke tests (no live by default)
- Live smoke tests:
  - only under env gates, minimal safe ops (connect, fetch bars, paper/sandbox order if available, cancel)
- CI recommendations:
  - default job runs deterministic tests only
  - separate manual/secured job for live smoke
Cite existing test structure/CI files; if missing, cite the absence (searched paths/patterns).

(80) docs/context/80_risk_register.md  [Worker4]
Risk register table:
Risk | Severity | Likelihood | Detection | Mitigation | Owner (lane)
Must include:
- bar semantics mismatch
- look-ahead/leakage
- data gaps/dup/out-of-order
- idempotency bugs
- duplicate orders
- rate-limit lockout
- timezone bugs
- partial fills handling
- silent failures
- config mistakes
- dependency drift
- unsafe live trading defaults
Tie each risk to at least one repo reference or explicit “not found” evidence.

FINAL CHECKS (MANDATORY, DO NOT SKIP)
1) Verify all 9 files exist (list them).
2) Verify each file ends with `## Evidence`.
3) Verify each file contains >=5 concrete repo references (paths/symbols/keys) and command evidence.
4) Verify git status shows ONLY docs/context/* changes:
   - Run: git status --porcelain
   - Report the summary in the final response (include the output).

Report findings/results back to the lead and keep task updates current.
  Status: pending

## Instructions

1. Load and follow `skills/worker/SKILL.md`
2. Send startup ACK to the lead mailbox using MCP tool `team_send_message` with `to_worker="leader-fixed"`
3. Start with the first non-blocked task
4. Read the task file for your selected task id at `.omx/state/team/context-pack-generation-read-o/tasks/task-<id>.json` (example: `task-1.json`)
5. Task id format:
   - State/MCP APIs use `task_id: "<id>"` (example: `"1"`), not `"task-1"`.
6. Request a claim via state API (`claimTask`) to claim it
7. Complete the work described in the task
8. Write `{"status": "completed", "result": "brief summary"}` to the task file
9. Write `{"state": "idle"}` to `.omx/state/team/context-pack-generation-read-o/workers/worker-4/status.json`
10. Wait for the next instruction from the lead
11. For team_* MCP tools, do not pass `workingDirectory` unless the lead explicitly asks

## Scope Rules
- Only edit files described in your task descriptions
- Do NOT edit files that belong to other workers
- If you need to modify a shared/common file, write `{"state": "blocked", "reason": "need to edit shared file X"}` to your status file and wait
- Do NOT spawn sub-agents (no `spawn_agent`). Complete work in this worker session.
