# Agent operating rules (final refactoring)

- Product and execution authority exists only in these five files:
  - `docs/final/01_FINAL_PLAN.md`: scope, priority, sequence, release gates.
  - `docs/final/02_DEVELOPER_TICKETS.md`: ticket edits, tests, acceptance.
  - `docs/final/03_ARCHITECTURE_REQUIREMENTS.md`: safety and architecture.
  - `docs/final/04_COMPLETED_MANIFEST.json`: immutable historical completion.
  - `docs/final/05_TASK_STATUS.json`: sole mutable task status and DAG.
- Older v3/vnext, Minimal Reuse, VN/TX/R/O plans and status are read-only history;
  never treat them as parallel current authority or rewrite accepted work.
- Resume from HEAD/worktree, 05, last acceptance receipt and current ticket;
  read only its relevant 02/03 sections and direct source/callers/tests.
- Codex owns requirements, ownership, bounded packets, review and acceptance.
  Workers never update authority/status, self-approve or recursively delegate.
- Use `~/.local/bin/delegate-opencode ROLE TICKET MODEL`; workers never switch models.
  Verify exact IDs using `opencode models freellmapi` and `opencode models opencode`.
  Priority: 1 `freellmapi/glm-4.5-flash`, 2 `opencode/deepseek-v4.1-flash-free`,
  3 FreeLLMAPI Gemini (pin verified exact ID, initially `freellmapi/gemini-2.5-flash`).
  Codex may advance after inspecting failure/partial edits; announce and record it.
  Do not change Codex model/authentication or use a new paid route.
- Pass one ticket, base SHA, exact model, contracts, read/edit/forbidden paths,
  direct callers, acceptance and exact tests. Use credential-free OS isolation.
  One writer per repository; inspect partial work before at most two repairs.
- Preserve baseline dirty changes and diagnostics; record independent exits,
  diff/hash provenance and private artifact paths. New diagnostics fail acceptance.
- Strategy owns every economic action; execution owns factual state and safety.
  Preserve A1 durable-before-strategy, source independence and bounded queues.
- Preserve C3/KP/KD, Mock/Mock2 and TOSS collection with DATA_ONLY order blocking.
  Flat `src/` is the final target; use accepted move maps before changing imports.
- KR-DATA-01 is opt-in and not required for offline release; do not activate it.
  Latency numeric targets are observational; structural safety remains required.
- LOCAL/PAPER/FAKE/static validation only. No live orders/cancels, production
  accounts, server changes, public dashboard, original-data deletion, push,
  merge, deployment or new external services without explicit user approval.
  Never print credentials; do not install packages or make unscoped rewrites.
- A ready ticket is sufficient authority: verify, record receipt/status, continue
  through dependencies; stop and record concrete safety/provider blockers.
