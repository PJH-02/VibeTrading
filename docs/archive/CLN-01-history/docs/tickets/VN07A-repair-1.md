# VN07A repair 1 — Independent acceptance rejected two counterexamples

Keep all original VN07A constraints and existing test assertions. Initial worker process exit0 / worker regression63 PASS was NOT acceptance: independent suite65 PASS,2 FAIL. Repair only src/vibetrading/strategy_runtime.py. Read that file and the frozen docs/tickets/VN07A-reviewer-counterexamples.py plus original VN07A ticket; other inputs are import/test dependencies only. Do not edit ledger, tests, metadata or evidence. No new protocol/API/layers or unrelated refactoring.

1. test_permission_block_must_not_mask_sticky_restart_fence: durable history exists, but paper_permission=False selects the first branch, leaving job.restart_blocked false. pause/resume/halt sequences can then run the callback on a NEW event. Record durable restart evidence independently of its displayed permission label and make the fence sticky. Permission missing must continue blocking callbacks/actions even for REPLAY; warmup exception must not override permission. No bypass via control operations. Add no automatic unblock.
2. test_operator_halt_still_requests_cancel_for_restored_owned_order: the new halt early-continue skips _cancel_owned for a restart-blocked job. Preserve existing explicit operator halt/pause cancel-owned behavior while retaining the sticky no-new-risk barrier. Registration itself must never cancel existing orders. Normal jobs retain existing behavior. Stop/halt is a risk-reducing user operation, and must continue requesting cancel through the existing matcher. No new order/cancel implementation.

Preserve existing shared-engine REPLAY/backfill warmup tests (no claims/actions/Paper advancement), production/full restart BLOCKED and missing strategy-state restore evidence. Existing replay callback faults must not cancel live Paper orders. Keep prior malformed receipt fail-closed validation unchanged.

Run exact command and report actual exit:
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_paper_execution.py docs/tickets/VN07A-reviewer-counterexamples.py

Worker is not acceptance authority. No network/trading/deploy/Git/metadata/subagents or edits outside the one source file. Exact role refactor, model opencode/deepseek-v4.1-flash-free.
