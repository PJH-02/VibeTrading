# R03C1 — strict path-map consumer and complete worker header
objective: Translate original VN/TX physical paths through R03 path_map without changing IDs/contracts/dependencies or weakening scope authorization.

base_sha: 04e4d57ff8ad375c3f79ff3f05f644bd40de0569
prerequisites: R03A/B1/B2 accepted; R03 only authorized.
model_profile: refactoring; exact model opencode/deepseek-v4.1-flash-free; role migrate.
forbidden_paths: any file outside explicit edit_paths; .env/credentials, data/DBs, product economics/order/strategy logic, historical receipts/goldens, active P02 rollback edits. No installs, Docker, server/account calls, recursive delegation, model substitution, commits or acceptance claims.
review_severity: bounded path compatibility reviewed independently by Codex.
stop_conditions: scope collision, missing dependency, ambiguous source path.

read_paths: scripts/dev/agent_task.py, tests/refactoring/test_agent_task.py, scripts/dev/agent_run.py, tests/refactoring/test_agent_run.py, scripts/dev/status.py. Current path_map.json is the format example; do not edit it.
edit_paths: scripts/dev/agent_task.py; scripts/dev/agent_run.py (ONLY _ticket_text); tests/refactoring/test_agent_task.py; tests/refactoring/test_agent_run.py.
implementation_steps:
1. Optional docs/refactoring/vnext/path_map.json schema1 contains root_moves [{from:"vibetrading/",to:"src/vibetrading/",applies_to:...}], files[{from,to,move_only_sha256,move_only_ast_sha256}], ambiguous_targets[]. Historical move hashes are evidence, NOT current code hashes. Validate safe relative paths, object/list types, no symlink traversal/blocked path injection. Fail closed for ambiguous_targets, two distinct target candidates, duplicate conflicting entries, overlapping conflicting root rules. Exact+prefix entries yielding same unique target are allowed. Never guess from filesystem existence.
2. Translate spec read/edit paths and canonical edit scopes (including original directory scopes and planned ADD via root prefix rule) consistently. Already-current src paths stay unchanged. Translate canonical forbidden patterns under old prefix as well, preserving their original deny rules and canonical contracts. Do not broaden a ticket's allowed logical scope. No map => legacy fixture behavior unchanged.
3. Pin path_map as canonical metadata when present; include map presence in the canonical generation fence, and verify_source rejects map change/removal or addition after a packet was pinned without map. Do not allow map hash to be omitted from a packet when it is present. Preserve all existing source pin, symlink and canonical generation race checks. Existing legacy tests must pass unchanged except adding scoped tests.
4. Change ONLY _ticket_text in agent_run: include exact base SHA, prerequisites, model/provider/profile, contracts, forbidden paths, review severity in worker markdown. Show first five prioritized read paths plus count of remaining read-only dependency files; retain full machine packet source hashes untouched. Header fixture tests should cover full fields and long dependency list. Do not repair O01 baseline-manifest or snapshot behavior here; it is a separate known rework item.
5. Add meaningful fixtures: exact mapping, old and new specs, prefix planned ADD, old forbidden translated, canonical scope not expanded, conflicting/malformed/unsafe/symlink map rejection, map hash/presence/generation drift, legacy no-map unchanged. Run existing core+adapter tests too.
acceptance: original canonical task IDs/deps/allowed logical files unchanged; current package path scopes work; every ambiguity fails closed; full requested tests pass; no worker self acceptance.
test_commands: python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_agent_task.py tests/refactoring/test_agent_run.py
