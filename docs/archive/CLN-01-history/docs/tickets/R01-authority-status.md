# R01 — Authority and status index

task_id: R01
base_sha: 440ef7e408be800d377f67373db2d3707d09de58
prerequisites: [R00 accepted at 440ef7e]
role: repository/documentation
model_profile: opencode/glm-5.3-flash-free (O00 WRITE/pytest/denial qualified)
scope: LOCAL_ONLY
read_paths: [context.json, docs/refactoring/vnext/tickets_production.json, docs/refactoring/vnext/stage_status.json]
edit_paths: [AGENTS.md, docs/status.md, scripts/dev/status.py, tests/refactoring/test_dev_status.py]
forbidden_paths: [all other repository paths, .env*, secrets, data, credentials, existing historical receipts, trading product code]
contract_ids: [v3 A1/A2, R01; preserve all C01-C09]
review_severity: low
stop_conditions: [schema ambiguity, scope drift, model failure, missing prerequisite]

## Objective
Create one deterministic read-only status projection from existing canonical JSON, plus concise repository guidance. NO new status database, no product edits. The orchestrator alone updates canonical JSON; do not edit it. Treat context.json as a bounded derived summary only. Avoid reading the large canonical JSON in a tool response; your Python code may parse it. No whole-repo scanning or agents.

## Implementation
Implement stdlib-only scripts/dev/status.py. API `load_status(root: Path) -> dict` reads canonical tickets_production.json and stage_status.json. Original 51 VN/TX records are in tasks; additional R/O records are only tickets_production.tooling_tasks. Stage task category COMPLETE_LOCAL_IMPLEMENTATION is local completion, other original stages remain incomplete. R/O ACCEPTED is completion. Validate duplicate IDs, missing dependencies, cycles and stage counts; fail closed with ValueError on malformed metadata. Preserve original IDs/dependencies/status details in the projection. Never infer completion from substring COMPLETE in a status such as INCOMPLETE. Counts reported for VN/TX must equal stage_status counts. A node is READY only if not complete/running/external, all exact dependencies complete, and BOTH canonical execution_control.start_next_ticket values are true. RUNNING remains RUNNING (no duplicate dispatch); missing deps -> BLOCKED. VN20 actual cutover/deletion is EXTERNAL, never runnable automatically; local preparation stays separately tracked, not silently complete. Emit `nodes`, `ready`, `counts`, `paused`, and `last_completed_ticket`. Caller should see original recorded status and missing deps.
CLI: `python3 scripts/dev/status.py --root . --json` (default JSON acceptable) and --markdown render concise status. CLI additionally records current Git HEAD when available, without importing product modules or reading env files. Unknown HEAD must be explicit. docs/status.md is a generated projection with current local/operational distinction, canonical source links, next READY and current O00/R01 state; the orchestrator will regenerate it on the real root after acceptance.
AGENTS.md <= about 2KB: scoped reading budget, three v3 documents are authoritative, canonical JSON/index command, required bounded task packet, explicit model profiles from tickets_production.json, reuse installed ~/.local/bin/delegate-opencode ROLE TICKET MODEL, workers only in credential-free OS sandbox with allowlisted edits, critical contracts require Codex review, no production/deploy/push/merge/live orders/NATS removal/secrets. Preserve C3/KP/KD, Mock/Mock2, TOSS DATA_ONLY. No user permission loop for already authorized local work.
Tests use pytest and temporary synthetic canonical files: READY dependency satisfaction; false predecessor not accepted; duplicate/dangling/cycle refusal; stage count mismatch refusal; paused execution blocks dispatch; RUNNING not READY; VN20 external; R/O union without changing VN/TX counts. Also run real copied canonical CLI to ensure 51 original +9 tooling nodes parse.

## Acceptance / exact tests
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_dev_status.py
python3 scripts/dev/status.py --root . --json
Only the four edit_paths may change; no tests/JSON weakening, network, installs, trading, extra scripts or subagents. Return real test counts, exit codes, changed symbols and any blocked condition; do not self-approve. At most two repairs.
