# TX04B repair1 — reference-book freshness before a fresh trade advances Paper

Same TX04B, first of at most two repair delegations. Role refactor, exact opencode/deepseek-v4.1-flash-free. Existing initial source356b234 is scoped-tested, but Codex extended review produced 140 PASS/1 FAIL: a fresh TradeEvent at T+11 can advance pending IOC into a book from T, while runtime max_feed_age_ns=5 and native matcher stale budget is 1e9. Trade freshness does not make that book fresh. No third/fourth ticket dispatch now.

Only writable src/vibetrading/strategy_runtime.py (_market only) and tests/refactoring/test_tx04b_continuous.py (one regression for this case). All other product methods, engine/wiring, existing tests/Golden/metadata readonly. Do not modify Codex reviewer test. No new helper/state/schema/runtime/account code or cleanup, no live/network/accounts/keys/install/deploy/push/merge/data deletion.

In existing _market, BEFORE matcher.on_trade advances time or consumes evidence, require snapshot.latest_book(reference_key) to exist and satisfy current _fresh_input at snapshot.now_ns, in addition to existing quality/input checks. Missing/stale reference book must call existing _fence_feed from the last durable clock then advance cancellation/expiry as already implemented, never consume cached taker depth. Fresh full OrderBookEvent should keep using its projected post-apply book; preserve cross-feed and explicit Korean reference mapping, replay/duplicate/recovery/permission gates, economics/IDs and old valid maker/taker behavior. No new sizing/normalization/default readiness.

Reproduce exact independent case: bookT, registered strategy creates post_only=False IOC BUY1 limit101 with arrivalT+10, freshness budget5, seed100000, fresh tradeT+11/sourceT+11/price100. It must have zero fills, position0 and cash100000. Existing stale-tick/stale-source cases and all prior tests stay unchanged.

Exact command:
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_local_engine.py tests/refactoring/test_local_runtime.py tests/refactoring/test_runtime_checkpoint_integration.py tests/refactoring/test_paper_execution.py tests/refactoring/test_tx02_joint_gate.py tests/refactoring/test_tx04a_alignment.py tests/refactoring/test_tx04b_continuous.py

Report actual exit/count/diff and stop; no self-approval or recursive delegation. Codex independently verifies.
