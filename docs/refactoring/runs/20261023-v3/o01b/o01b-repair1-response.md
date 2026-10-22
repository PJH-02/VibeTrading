Implemented O01B repair scope only.

### Files changed
- `scripts/dev/agent_run.py` — added private execution adapter with:
  - `run_ticket(...)` and `cancel_run(...)`
  - CLI `run` / `cancel`
  - packet/source/scope/test gates via public `agent_task` APIs
  - private state layout, per-origin `flock`, TTL validation, duplicate detection, cancel polling
  - workspace snapshot, Git baseline, bwrap sandbox command construction
  - worker process-group termination and preserved partial artifacts
  - candidate patch generation with true ADD/DELETE `/dev/null` semantics
  - host-side pytest argv execution and `check_tests` verification
  - FreeLLM loopback relay with exact path/model enforcement, host-side bearer injection, status/Retry-After preservation, served-model observation and secret redaction
  - native `opencode` config with provider secrets excluded
- `tests/refactoring/test_agent_run.py` — added 20 focused tests covering success/pending review, duplicate suppression, lock contention, TTL rejection, timeout preservation, cancel, source-change refusal, unknown routes, secret/symlink refusal, ADD semantics, failed tests, empty test commands, nonzero workers, native config secrecy, relay behavior, and a real bwrap namespace/write-denial probe.

### Commands run / real results
1. Exact required command:
```bash
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_agent_run.py tests/refactoring/test_agent_task.py tests/refactoring/test_dev_status.py
```
Result: **129 passed, 1 failed**
Failure: `tests/refactoring/test_dev_status.py::test_real_copied_canonical_cli` expects host canonical file `docs/refactoring/vnext/tickets_production.json`, which is absent in this worker workspace. This is outside the permitted edit scope and is a host-input dependency noted in the repair ticket.

2. Scoped new test file:
```bash
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_agent_run.py -ra
```
Result: **20 passed**, including the real bwrap probe.

### Limitations / not claimed
- Did not run real model inference or the required later two independent synthetic model tasks.
- Did not apply patches, commit, merge, deploy, or record acceptance.
- Success receipts remain `PENDING_CODEX_REVIEW` with `UNTRUSTED_INPUT` provenance.
- The single failing pre-existing `test_dev_status` case depends on canonical metadata unavailable from this worker context.
