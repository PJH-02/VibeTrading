## Objective
- Complete the rejected VN07B coupled runtime recovery implementation per `/work/docs/tickets/VN07B-repair-1.md`, fixing concrete runtime/ETF/restore defects, adding meaningful acceptance tests, and running the exact scoped pytest command.
- Report real exits and remaining gaps; do not touch metadata/status/acceptance/commits/push/live/network.

## Important Details
- Bash tool is denied except commands matching `python3 -m pytest*`; use `read`/`edit`/`write` only. No `ls`, `grep`, `git`, installs, or subagents.
- Allowed edits: `src/vibetrading/runtime_checkpoint.py`, `src/vibetrading/strategy_runtime.py`, `src/vibetrading/local_runtime.py`, `src/vibetrading/etf_trading.py`, `tests/refactoring/test_runtime_checkpoint.py`, `tests/refactoring/test_runtime_checkpoint_integration.py`.
- Read-only: all pre-existing tests/goldens, `docs/tickets/VN07B-reviewer-counterexamples.py`, KR five modules, and supplied import dependencies.
- Exact command:
  `python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_runtime_checkpoint.py tests/refactoring/test_runtime_checkpoint_integration.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_paper_execution.py tests/refactoring/test_checkpoint_startup.py tests/refactoring/test_checkpoint_runtime.py tests/refactoring/test_checkpoint_journal.py tests/refactoring/test_vnext_vn07.py tests/refactoring/test_local_runtime.py docs/tickets/VN07B-reviewer-counterexamples.py`
- Initial exact run: 9 failures. One was `tests/refactoring/test_local_runtime.py::test_1801_virtual_seconds_with_no_nats_durable_spool_uds_and_local_decisions`, likely environment-specific: offline UDS audit guard blocks `socket.connect` because `TMP` appears unset; repo code likely not at fault.
- After edits, targeted run (`test_runtime_checkpoint.py`, `test_runtime_checkpoint_integration.py`, reviewer probes): only `test_recovered_runtime_can_save_its_next_verified_cut` fails due to market checkpoint restore epoch regression (`T` restore vs saved capture epoch `T+1`), not logic/corruption.
- Use strict journal equality and preserve `_verify_evidence` final expected==actual semantics. Never rewind/reset SQLite ledgers; quarantine on legitimate mismatch.

## Work State
### Completed
- `strategy_runtime.py`
  - `_runtime_identity` now binds full `ProductSpec.to_document()`, full `InstrumentId.model_dump(mode="json")`, and job/module identity: `job_id`, `strategy_id`, `module_name`, `declared_version` (`STRATEGY_VERSION`/`VERSION`), `strategy_version`, `config_version`, `config`, `fingerprint`.
  - Added `_job_state_document()` and `_current_runtime_state()`; `capture_runtime_state()` now uses them.
  - Restore stores `_checkpoint_source_state`, normalized `_checkpoint_expected_state`, `_checkpoint_max_bytes`, and proof digest computed from actual normalized state.
  - `checkpoint_restore_proof(state=None)` recomputes actual staged plugin/lifecycle/map state and returns digest only if it still matches normalized expected state.
  - `publish_checkpoint_restore()` now calls no-arg `checkpoint_restore_proof()`.
- `runtime_checkpoint.py`
  - Imported `OrderIntent`; Paper pending rows now validate `OrderIntent.model_validate_json` and `intent_id` match.
  - `_etf_evidence` now calls existing `etf._check_ownership()` when present, reads hash identities from journal metadata keys `account_identity` and `mock2_account_identity` (not nonexistent `_account_ids`), validates pending ETF `OrderIntent` fields, and keeps real journal path evidence.
  - Capture rechecks Paper identity and journal evidence after plugin checkpoint hooks, and rechecks ETF identity/pending/journal after market capture.
  - `etf_state` is now full `dict(journals["etf"])` plus `journal_sha256`, so restore reads journal evidence from the same location.
  - Restore now calls `begin_checkpoint_recovery()` for Paper/ETF before first await/file read; closes engine and quarantines on read/load/restore failure or cancel.
  - ETF preflight uses `require_day=False`, checks account hash/routes/pending/journal; after initialize/replay requires exact day/account/routes/pending/journal.
  - After all proofs and publications, attaches exact callback `engine.post_apply_callbacks = (paper_runtime.process_applied,)` inside `_owner_operation(exclusive=True)` when available.
- `local_runtime.py`
  - `run_engine` runtime-checkpoint branch now requires explicit synchronous `recovery_readiness` before inspecting paper/setup/factory/transport; missing raises `ValueError` mentioning readiness.
  - Removed default `lambda ...: True`; passes `recovery_readiness` directly to restore.
  - Started modifying `replay_checkpoint_startup(...)`: added optional `restore_epoch_ns`, validates it, and passes `restore_epoch_ns` to `engine.inspect_checkpoint`.
- `etf_trading.py`
  - `mark_checkpoint_recovery_ready` no longer forces `self.phase = 'ready'`; preserves initialize phase for normal quote/history/timer readiness.

### Active
- Finish `replay_checkpoint_startup` clock-regression fix:
  - Pass `restore_epoch_ns` into `engine.restore_virgin(...)` as well as `inspect_checkpoint`.
  - In `RuntimeCheckpoint.restore`, extract saved market capture epoch from `body["market"]["body"]["captured_epoch_ns"]` and call `replay_checkpoint_startup(..., restore_epoch_ns=max(engine.snapshot_clock(), saved_epoch))` (or equivalent). This is needed for frozen reviewer `test_recovered_runtime_can_save_its_next_verified_cut`.
- Re-run targeted reviewer tests after the clock fix.
- Run full exact command and report exact failures/exits.
- Add missing bounded acceptance tests to the two new test files per ticket items 18-20 (partial fills/reservations/reopen parity, account-version quarantine, suffix/corrupt source no callback, plugin missing/async/bad-roundtrip/config/lifecycle/permission, malformed/unknown ETF handoff, cancellation/partial write/single-flight save, journal hooks/readiness mutations, real run_engine ordering).
- Add/keep tests that prove restore binds the real `paper.process_applied` callback, rather than manually attaching it in tests.

### Blocked
- `tests/refactoring/test_local_runtime.py::test_1801_virtual_seconds_with_no_nats_durable_spool_uds_and_local_decisions` fails in this environment; likely offline UDS permission guard because `TMP` is unset for `tmp_path`. Do not edit test or unrelated transport code; report as environment gap.
- No failure should remain in the scripted reviewer probes after restoring clock epoch.

## Next Move
1. Complete `local_runtime.replay_checkpoint_startup` to forward `restore_epoch_ns` to `engine.restore_virgin`, then update `RuntimeCheckpoint.restore` to compute/pass the saved-epoch floor.
2. Run: `python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_runtime_checkpoint.py tests/refactoring/test_runtime_checkpoint_integration.py docs/tickets/VN07B-reviewer-counterexamples.py`, then the full exact command.

## Relevant Files
- `/work/docs/tickets/VN07B-repair-1.md`: authoritative repair scope and acceptance requirements.
- `/work/docs/tickets/VN07B-coupled-runtime-recovery.md`: original binding architecture/design ticket.
- `/work/docs/tickets/VN07B-reviewer-counterexamples.py`: frozen independent acceptance probes; read-only.
- `/work/src/vibetrading/runtime_checkpoint.py`: coupled capture/save/restore, ETF/journal evidence, callback publication.
- `/work/src/vibetrading/strategy_runtime.py`: plugin identity/state proof, staged restore, readiness revalidation.
- `/work/src/vibetrading/local_runtime.py`: `run_engine` option guards, explicit readiness, `replay_checkpoint_startup`.
- `/work/src/vibetrading/etf_trading.py`: ETF recovery gate, initialize, `mark_checkpoint_recovery_ready`, dispatch guards.
- `/work/src/vibetrading/etf_journal.py`: real journal metadata/pending/dispatch schema used by ETF evidence.
- `/work/src/vibetrading/local_engine.py`: `inspect_checkpoint`, `restore_virgin`, `complete_checkpoint_recovery`, `_owner_operation`; do not edit.
- `/work/src/vibetrading/market_checkpoint.py`: checkpoint decode/wall-clock validation.
- `/work/src/vibetrading/trading_contracts.py`: `ProductSpec.to_document`; `InstrumentId` model dump.
- `/work/src/vibetrading/portfolio_ledger.py`: Paper ledger pending order rows.
- `/work/tests/refactoring/test_runtime_checkpoint.py`: new test file allowed for extensions.
- `/work/tests/refactoring/test_runtime_checkpoint_integration.py`: new integration test file allowed for extensions.
- `/work/tests/refactoring/test_local_runtime.py`: environment-failing UDS test; read-only.
Continue if you have next steps, or stop and ask for clarification if you are unsure how to proceed.
Implemented the remaining VN07B repair scope.

### Code fixes
- Completed restore-clock handling:
  - `replay_checkpoint_startup` forwards `restore_epoch_ns` to both `inspect_checkpoint` and `restore_virgin`.
  - Runtime restore extracts the saved market capture epoch and restores at `max(engine snapshot clock, saved epoch)`, preventing wall-clock regression without refreshing stale quotes.
- Preserved the exact callback publication path, including binding `paper.process_applied` after proofs.
- Finished earlier repair work in strategy identity/proof validation, ETF journal evidence, recovery gating, and explicit `run_engine` readiness requirements.

### Added acceptance coverage
- Unit tests:
  - Nested multi-account journal row mutation rejected at unchanged row count.
  - Missing/async plugin restore hooks fail before staging.
  - Full product identity, permission, and saved lifecycle enforcement.
  - ETF unresolved `PREPARED`/`HANDOFF`/`UNKNOWN` dispatch states rejected.
  - Malformed ETF pending `OrderIntent` rejected.
  - Async save single-flight and caller-cancellation shielding.
  - Atomic writer failure preserves the old bundle and removes temporary files.
- Integration tests:
  - Partial fill, working-order reservation, close/reopen, and next-event economic parity.
  - Market suffix beyond checkpoint C quarantines a selected consumer without callback publication.
  - Corrupt real runtime bundle fences before callback publication.
  - Real `run_engine` rejects missing recovery readiness before inspecting runtime fields.
- Removed the integration test’s manual `post_apply_callbacks` assignment; restore now proves the binding itself.

### Test results
- Targeted runtime/reviewer run: `21 passed`, exit `0`.
- Exact required command: `224 passed, 1 failed`, exit `1`.
- Sole failure:
  - `tests/refactoring/test_local_runtime.py::test_1801_virtual_seconds_with_no_nats_durable_spool_uds_and_local_decisions`
  - Environment-only offline UDS guard failure: the guard’s allowed bind root checks unset `TMP`, then blocks `socket.connect` to the test’s `/tmp` UDS path. No source or test change was made for this unrelated environmental failure.

No metadata, status, acceptance, commit, push, live, or network actions were performed.
