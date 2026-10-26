# TX08A — pin consistent existing account limits across strategy registrations

Ticket2 of TX07A -> TX08A -> TX08. Dispatch after TX07A independent acceptance. Role refactor, exact qualified opencode/deepseek-v4.1-flash-free, stable delegate ROLE TICKET MODEL. Codex alone accepts; no recursive agents/status edits/self-approval. Max two repair delegations.

Reuse current serialized PortfolioLedger reservation/owner/attribution and PaperStrategyRuntime; no universal account/risk/scheduling engine or live/ETF rewrite. Demonstrated gap to reproduce: register alpha with shared account_notional_limit1000, then beta same physical account with100000; both currently register. Each caller can redeclare the supposedly account-wide cap. Account_capital_limit has same gap. Existing per-strategy caps should remain independent.

ONLY writable:
- src/vibetrading/strategy_runtime.py: existing register method ONLY, no new helpers/dataclasses/state/schema/imports or other method changes.
- tests/refactoring/test_tx08a_account_policy.py: new scoped tests.
All other product/config, prior tests/Golden and metadata readonly. No installs, live/account/network/orders/credentials/deploy/push/merge/data deletion.

Before any registration claim or plugin creation, after current feed/account/risk binding validation, compare a new registration's account_notional_limit and account_capital_limit to all existing _jobs registrations for each overlapping exact account key. Any discrepancy (higher OR lower) raises ValueError with no DB/claims/_jobs/old-job changes. Compare existing validated Decimal values numerically;100000 and100000.00 are equal. Do not silently choose min/max or override any policy. Check all registered jobs, including paused/blocked/faulted ones; state changes cannot loosen shared account authority. Use existing bounded _jobs/accounts collections, no extra registry or persistence.

Strategy_notional_limit/strategy_capital_limit/funding_buffer are NOT shared policy fields and may differ. Different physical accounts may have different account caps. Preserve existing registration/claim/version/permission/restart/checkpoint semantics; no broader validation changes or inferred product/margin policy. Direct legacy APIs and venue/runtime code remain untouched. This is consistency of the explicit current Paper host profile, not a new persisted/live risk authority or fairness scheduler.

Tests: higher/lower conflicting account caps for both fields rejected before any durable claim/create_strategy; equal numeric caps and distinct strategy caps allowed; different physical accounts may differ using actual distinct AccountScope.account and existing owner roots; invalid/over-budget strategy cannot spend or cancel healthy sibling's allocation; both strategies sharing consistent cap can admit within exact Decimal budget, aggregate cap is never optimistic netted; denied/failed job does not corrupt healthy one. Reuse current helper registration/ledger/runtime and current existing portfolio/runtime/owner/ETF tests for late fill, CANCEL_PENDING/UNKNOWN, gross opposing positions, restart, one physical writer. Do not create multi-threaded SQLite access that violates the existing one-thread owner. Fairness claim is namespace/budget isolation, not max-min scheduling or guaranteed allocation when budgets overcommit.

Read1–5 relevant files at a time; execute EXACT pytest command, not collection/version/exploration:
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_tx02a_owner_fence.py tests/refactoring/test_tx02b_attribution.py tests/refactoring/test_vn13_file_registration.py tests/refactoring/test_tx07a_fact_contract.py tests/refactoring/test_runtime_checkpoint_integration.py tests/refactoring/test_tx08a_account_policy.py

Report actual command/exit/count/scoped diff and stop. Preserve all existing behaviors/Golden and actual runtime code except the bounded register guard. Codex runs independent counterexamples and records local-only acceptance; no live/process isolation/server claims.
