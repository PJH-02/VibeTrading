Implemented within scope: `scripts/dev/agent_run.py` and `tests/refactoring/test_agent_run.py` only.

Key changes:
- Full workspace manifest scope enforcement, including untracked/new/deleted/symlink paths and ADD placeholders; snapshot pin verification before inference.
- Origin source re-verification after tests, returning `SOURCE_CHANGED` while preserving the tested candidate.
- Worker env now disables project config/plugins/skills/LSP download via `OPENCODE_PURE`, `OPENCODE_DISABLE_PROJECT_CONFIG`, and related flags; no `--auto`.
- Independent tests use a fresh tmpfs HOME and system-only PATH, with read-only code/deps, private net/pid, and writable `/tmp`.
- Relay safety blockers now return `FAILURE / SAFETY_BLOCKER` before tests; dispatcher and OpenCode logs are scanned/redacted.
- Relay redirects are blocked; upstream base validation, Retry-After observations, and bounded timeout are enforced.
- Origin locks are keyed by resolved origin and shared across state dirs via a host lock root.
- TTL starts at state creation; expiry is polled before launch, during worker, and during tests; cancellation during tests is polled and returns `CANCELLED`.
- Relay cleanup on preparation failure; OpenCode log/metadata/token/tool-state collection; provider 429/401 classification; origin vs snapshot git baselines; NVM/`~/.opencode/bin` runtime discovery.

Command run:
`python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_agent_run.py tests/refactoring/test_agent_task.py tests/refactoring/test_dev_status.py docs/tickets/O01B-reviewer-counterexamples.py`

Result: **exit 0, 140 passed**.

All six read-only reviewer counterexamples pass. Remaining required external work, unchanged by this patch: Critical Codex review, separate model audit, and real two-model synthetic tasks.
