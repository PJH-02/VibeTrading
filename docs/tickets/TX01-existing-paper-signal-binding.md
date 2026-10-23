# TX01 — finish the existing Signal/OrderIntent Paper binding

## Objective and reuse decision

TX00 is accepted. Codex inspected the current source at `fad4e81bdabbc689a8757ccec43da81acf5300d4`. The existing `PaperStrategyRuntime` already accepts the original `on_event -> list[Signal]`, calls the registered builder, validates `ExecutionAction`, and submits the existing `OrderIntent` into existing `PaperExecution`/`PortfolioLedger`. DO NOT add a bridge API, registry, new engine, connector, or replacement action contract.

Concrete missing checks in `PaperStrategyRuntime._validate`, NEW branch: only instrument.key is checked. Same key can hide different currency/raw symbol/security metadata; signal.instrument is not bound to the declared reference feed at all. OrderIntent.created_ts_ns is not checked against the causal signal/current snapshot, and intent/client IDs can be empty/unbounded. These are missing local contract guards, not a reason to rebuild exchange execution. Keep all unrelated behavior.

Selected role: refactor. Exact qualified model: `opencode/deepseek-v4.1-flash-free`. Codex owns architecture, independent tests and final acceptance. Two repair delegations maximum. No recursive delegation or self-approval.

## Exactly two writable paths

- `src/vibetrading/strategy_runtime.py`: **only the existing `_validate` method**, preferably a small additive guard block (roughly 15–30 lines). No other function or import changes needed.
- `tests/refactoring/test_tx01_signal_binding.py`: new scoped regression cases. Reuse existing test_strategy_runtime / test_portfolio_ledger / test_paper_execution fixtures/helpers rather than a second runtime or matcher.

Everything else is read-only, including models.py, trading_contracts.py, strategy_contract.py, strategies/contract.py, execution_owner.py, portfolio_ledger.py, paper_execution.py, exchange/cloud/ETF strategy functions, existing tests, Golden fixtures, configuration, canonical metadata and historical receipts. No installs, credentials, runtime_data, external accounts/orders, production deletion, push/merge/deploy, server or network actions. Shell test execution is offline in a credential-free OS sandbox. Do not run ls/grep/discovery or shell separators; read the explicit paths directly.

## Required implementation

Preserve all existing `_validate` checks, expiry/action/config/account checks and document format. For NEW only, before any `_execute` action claim / matcher admission:

1. Reuse existing product lookup and `reference_feeds.get(intent.instrument.key, intent.instrument.key)`. Full intent instrument must equal the product's full InstrumentId; full signal instrument must equal `self.known[reference_key]`. **Do not require signal.instrument == execution instrument:** the existing explicit same-security Kiwoom/KRX/TOSS reference -> KIS ETF Paper mapping must keep working. No extra reference mapping or implicit cross-product alias.
2. Reuse `bounded_identity` for existing intent_id, client_order_id, job_id, signal_id (including signal.signal_id); preserve existing job/signal/strategy matching. Do not regenerate, normalize, round or substitute IDs or payloads.
3. For NEW, reject bool/non-integer/negative intent.created_ts_ns, and require `signal.event_ts_ns <= intent.created_ts_ns <= now`. Keep existing signal expiry/causal checks; do not consult a separate wall clock or enforce alpha side/direction mappings. Require a valid nonnegative integer now at this validation boundary, not True-as-time.
4. Leave CANCEL/REPLACE economics and behavior alone. Do not rewrite callback claims, action dedup, recovery fences, leases, ledger schema, matcher state or risk math. `OrderDestination.REAL` in the legacy intent is a preserved wire descriptor; the PAPER runtime/matcher owns execution routing, so do not rewrite it or treat it as live permission.
5. Tick/lot/unsupported product/mode/Decimal/evidence collision checks remain authoritative in existing ProductSpec and PortfolioLedger/PaperExecution. Reuse and demonstrate them; do not duplicate their arithmetic in runtime or create a generic validator.

## Acceptance

- Reproducer cases fail on the unchanged base and pass on the candidate: same-key different full intent identity; wrong signal reference identity/currency/raw symbol; future/backdated/bool/noninteger creation; empty/oversized intent/client IDs.
- Valid original Signal/OrderIntent wire dictionaries (including Decimal values, legacy destination and metadata) are unchanged before/after validation. Ordinary direct instrument and explicit Kiwoom reference -> KIS ETF cases still dispatch through the existing Paper matcher.
- Rejected NEW binding raises before `_execute`, no `@action` receipt or order admission. Direct `_validate` is pure; a logical SQLite dump and matcher state remain unchanged. Existing event callback claim/fault handling is preserved and must not be falsely described as all-or-nothing callback rollback.
- Existing duplicate same-ID/same-payload remains deduplicated; changed payload remains rejected. Invalid tick/lot/product/mode admission retains existing transaction rollback. No new external mutation or venue client; TOSS DATA_ONLY, C3/KP/KD and Mock/Mock2 remain unchanged.
- No existing test/oracle edits. No fixes outside the two allowed paths. Codex independently reviews the diff, compares all unrelated runtime AST methods, executes tests and verifies actual worker/test exits.

## Exact test command

```
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_paper_execution.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_tx00_toss_data_only.py tests/refactoring/test_kr_strategy_extraction.py tests/refactoring/test_tx01_signal_binding.py
```

Return concise changed-path summary, exact command and actual exit, case counts and any limitations. Do not update status/acceptance metadata or claim live/production readiness. Stop after TX01; TX02/VN13 and any other ticket are out of scope.
