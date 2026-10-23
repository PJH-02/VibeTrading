# VN07B repair 2 — only two remaining independent boundary failures

This is the FINAL allowed repair delegation (2/2) for VN07B. Previous independent selected suite:229 PASS/2 FAIL. Real ETF/Paper journal recovery, real run_engine success/corruption gating and all14 original reviewer probes now pass. Do not redesign or touch working strategy restore/economic code. Exact qualified role refactor/model opencode/deepseek-v4.1-flash-free unchanged.

Allowed edits ONLY:
- src/vibetrading/runtime_checkpoint.py
- src/vibetrading/local_runtime.py
- src/vibetrading/etf_trading.py
All tests (including the two recent test files) and reviewer probes, metadata/status/goldens/other sources are READ-ONLY. No installs, network/account calls, subagents, Git/commit/push/delete/deploy.

1. REMOVE unsafe clock clamping. Current RuntimeCheckpoint.restore added _market_captured_epoch and calls replay_checkpoint_startup(... restore_epoch_ns=max(current_epoch,captured_epoch)). This bypasses the EXISTING wall-clock-regression rejection. A saved snapshot at T with current clock T-1 currently incorrectly grants READY. No policy allows replacing current time with saved time, refreshing stale input, or special-casing fixtures. Use the engine/codec's ORIGINAL current-clock validation, which already works for valid supplied snapshot_clock values. Remove the added restore_epoch_ns extension from local_runtime.replay_checkpoint_startup if its only caller was this clamp, restoring the exact original helper (frozen source provided in docs/tickets/VN07B-market-replay-baseline.py) while PRESERVING the newly correct run_engine branch. Do NOT weaken codec/engine checks and do NOT touch tests to hide a regression. Existing positive fixtures already supply consistent clocks (real driver test patches time.time_ns to T), so no production clock workaround is needed.

2. In EtfTradingLoop._submit_staged, the unconditional checkpoint gate is checked before await bus.publish_order_intent. While that await yields, state can change READY->RECOVERING. With ETF_EXECUTION_GUARDS disabled, the method proceeds to broker.submit_order without another check. Add a narrow synchronous _check_checkpoint_dispatch() immediately before the legacy unguarded broker.submit_order call (or after observation await before either dispatch branch), preserving the existing paired-permit path and ALL economic/journal/cancel behavior. No new broker API/state machine or broader rewrite. Existing conservative UNKNOWN handling on a blocked staged attempt remains; never release uncertain reservation or claim a terminal result.

The frozen reviewer file now has16 cases: the additional two at its end are test_wall_clock_regression_is_rejected_instead_of_clamped_to_saved_time and test_unguarded_etf_rechecks_checkpoint_gate_after_observation_await. Both must pass without weakening the already-passing cases. Avoid debug output/exploration; read selected functions + last two cases, apply minimal edits and execute tests.

The prior worker's baseline UDS failure was an ORCHESTRATOR environment omission, not product code: the unchanged offline guard requires TMP=/tmp for local UDS listeners. THIS worker environment now explicitly supplies TMP/TEMP/TMPDIR=/tmp. Do not alter guard or production socket code.

Exact command:
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_runtime_checkpoint.py tests/refactoring/test_runtime_checkpoint_integration.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_paper_execution.py tests/refactoring/test_checkpoint_startup.py tests/refactoring/test_checkpoint_runtime.py tests/refactoring/test_checkpoint_journal.py tests/refactoring/test_vnext_vn07.py tests/refactoring/test_local_runtime.py docs/tickets/VN07B-reviewer-counterexamples.py docs/tickets/VN07A-reviewer-counterexamples.py

Return exact changed scope and actual pytest/worker exits. Codex alone accepts the entire VN07 after independent full regression. Do not claim acceptance or start VN08.
