# TX04A — complete the existing Paper durable state alignment

Ticket3/final of authorized batch TX02B -> TX02 joint gate -> TX04A. Dispatch only after prior two acceptances. Role refactor, exact model opencode/deepseek-v4.1-flash-free. No recursive agents, status editing or self-approval. Codex independently verifies/accepts, max two repairs.

Existing PaperExecution stages deepcopy state, runs one existing PortfolioLedger transaction, saves matcher state and publishes memory only after successful commit. Keep this design/version2/schema/dedup/fill IDs/economics; no new matcher/queue/replay engine. Demonstrated gap: _aligned checks leaves/strategy/instrument/side/rules but ignores persisted protective price, expiry and execution flags. One-sided saved matcher corruption can silently change order matching semantics on restart. _transition also lacks an alignment check after the operation, before commit.

Only writable:
* src/vibetrading/paper_execution.py: existing _aligned and _transition only; a narrowly required stdlib import if necessary. No other method changes.
* tests/refactoring/test_tx04a_alignment.py: scoped tests using existing test_paper_execution fixture/helpers.

Read: paper_execution.py, tests/refactoring/test_paper_execution.py, portfolio_ledger.py admission/payload/state methods, tests/refactoring/test_tx02_joint_gate.py, existing runtime-checkpoint tests only if required. Others are readonly dependencies. All prior tests, Golden, models, ledger/owner/ETF/exchange/cloud/config/metadata readonly. No installs, live credentials/orders/network/deploy/push/merge or original-data deletion.

Implement small checks in existing _aligned:
1. For each existing active order, compare its saved protective price to the admitted immutable payload.limit_price with exact Decimal equality; never round/replace/normalize values. Check saved expiry against admitted row.expiry_ns, and saved post_only/reduce_only/time_in_force/group_id against the original enriched payload.metadata values. Use actual legacy field names. Reject missing/malformed/conflicting fields clearly/fail-closed; compare types as needed so bool/int or other coercions cannot hide a conflict. Preserve all valid supported version2 states. There is no new version/migration/repair/default inference. Do not enforce born==intent.created: existing entry latency uses actual submit time. No new alpha/order policy or risk arithmetic.
2. Existing leaves/strategy/instrument/side/rules comparison stays intact. Add _aligned(staged) after operation(staged), before check_bound/save_paper_state/COMMIT. If an operation stages a mismatch, existing transaction rollback must leave DB and original matcher memory unchanged. Do not weaken pre-operation alignment or join-outer-transaction refusal. No catch-and-retry / restore-old-data path.
3. Tests must reproduce saved price/expiry/flag/group conflicts failing constructor without changing latest DB; malformed flags/expiry must not pass Python equality coercions. Check staged mismatch is rejected before persistence/publication. Existing successful partial fill/cancel/restart/dedup and commit-failure/disk-budget cases retain exact economics. Internal matcher state corruption is tested only on owned synthetic copies; fail closed without automatic repair or deleting data.
4. This establishes agreement between the saved matcher and existing authoritative order facts, not cryptographic authentication of a coordinated forged DB, cross-process atomic commits, live orders, crash recovery of arbitrary unsent strategy callbacks or continuous input integration. TX04B is out of scope. Preserve existing helper arithmetic/receipt/fill identities, source format and all other methods.

Exact command:
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_paper_execution.py tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_runtime_checkpoint_integration.py tests/refactoring/test_tx02a_owner_fence.py tests/refactoring/test_tx02b_attribution.py tests/refactoring/test_tx02_joint_gate.py tests/refactoring/test_tx04a_alignment.py

Return scoped diff, exact command/actual test exits/count and limits; stop after TX04A. Codex updates status and publishes after independent acceptance.
