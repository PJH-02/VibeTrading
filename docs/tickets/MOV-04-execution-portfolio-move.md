# MOV-04 Execution/Portfolio Move Only, fake-mutation and SQLite parity

Origin source HEAD f49d97da8f4e07edc74fe61b8654e5136666d513. Initial Gemini worker failed test and outer timeout before completion.
Current exact worker opencode/glm-5.3-flash-free, role test, repair2 FINAL.
Gemini initial failed; MiMo repair1 exited124 without any edits. Both drafts
remain unapplied; current worker writes a fresh implementation, not a patch loop.
MiMo completed MOV-03 worker exit0. User requested Gemini/MiniMax/Kimi.
Additional Gemini3.1-Flash-Lite actual request HTTP200, exact served model. Other
Gemini requests failed timeout/502/404; MiniMax503; Kimi absent. Codex selects
Gemini BEFORE any MOV-04 worker starts. No worker-side model switching.

Codex uses accepted map and flat_domain_move.py --owners execution portfolio.
Exactly16 product sources: trading_contracts, brokers, bybit_execution,
execution.py -> execution/contracts.py, execution_groups, execution_ledger,
execution_nonce, execution_owner, extended_brokers, kis_execution,
paper_execution, perp_execution, process_lease; etf_journal, etf_recovery_ledger,
portfolio_ledger -> portfolio/. Import/path/packaging only; no HTTP/signer/order
logic, IDs/reserve/UNKNOWN/nonce/fill/fee/funding/attribution/economics changes.
No old module alias copies or automatic live/testnet activation. Worker receives
an isolated AFTER-move source snapshot, product readonly. Codex accepts only.

## Worker only editable paths
 tests/refactoring/test_mov04_execution_golden.py (new <=160 lines)
 Dockerfile.crypto (only add two COPY lines after storage COPY):
COPY --chown=10001:10001 src/execution /app/src/execution
COPY --chown=10001:10001 src/portfolio /app/src/portfolio
No image build/pull/run/install, other file, authority, config, metadata or product
edits. No agents, account APIs, orders, credentials, network, broad exploration.

Read this ticket, worker-context.txt, and exact helper source only if needed:
 tests/refactoring/test_execution_owner.py lines1-125,
 tests/refactoring/test_portfolio_ledger.py lines1-65 and100-155,
 tests/refactoring/test_execution_nonce.py lines1-35.
Reuse these existing OFFLINE helpers via `from test_execution_owner import
build_owner,intent,send` and `from test_portfolio_ledger import seeded,admit`.
This repository already uses direct sibling test helper imports. Do not duplicate
full owner setup. New test imports canonical execution/portfolio modules only.
No broad searches or whole product source reads. No new runtime framework.

Implement capture_execution_golden(root: Path) -> dict with deterministic clock
patch('time.time_ns',return_value=1700000000000000000). Exact scripted fixture:
1. asyncio.run internal scenario uses build_owner(root/'owner'), send twice
with same intent (created_ts_ns=1), assert SUBMITTED+duplicate and exactly1 fake
adapter mutation; apply_fill_once full quantity2 at price10.000000000000000001,
fee0.0001 then same fill again returnsFalse, rowFILLED/reserved0/position2.
Close owner. Capture exact fake mutation IDs/count and row/position/reserve.
2. with seeded(root/'portfolio') as book: admit default order; apply_fill0.4 at99
fee-0.02 USDT and duplicateFalse; request_cancel one alpha, cancel(one), then
confirmed cancel with terminal evidence; apply late fill0.1 at100. Expected
stateCANCELED, cumulative0.5, balanceUSDT950.42, physical=virtual position0.5,
reservation0. Capture book.report()/order row and close.
3. SignerNonceStore(root/'nonce',environment='sandbox',max_signers=2,
max_db_bytes=1048576,max_wal_bytes=1048576), signer key sandbox:0x +40 'a'; reserve
1000 twice then reopen/reserve999 ->1002. Capture returned values [1000,1001,1002].
4. After closing, capture ALL SQLite schema and rows for fixture orders.sqlite3,
portfolio.sqlite and nonce DB(s). Discover only root-owned *.sqlite*, exclude
-wal/-shm/lock files; DBs readonly. Sort schema by type/name and rows by canonical
JSON. Return paths relative to root, never absolute temp roots. All rows must be retained. Only portfolio_metadata key=path has an absolute
fixture path: assert it equals that DB absolute path, then normalize the exact
fixture-root prefix to <FIXTURE_ROOT> in this one cell for cross-temp comparison.
No other row filtering, UUID/timestamp masking or golden rebaselining. Clock makes
all SQL outcomes deterministic; dump blobs as hex if any. Capture schema+all rows,
not physical SQLite page bytes (WAL checkpoint layout is not an economic state).

Write two meaningful tests: capture expected economic/status/nonce constants and
repeat in an independent fresh root to prove full golden equality; duplicate or
UNKNOWN/ACK loss safety using existing fake helper (no second mutation/retry).
Do not change production code for a failing expectation; report uncertainty.
Exact worker command, once after implementation then stop:
python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/refactoring/test_mov04_execution_golden.py
Codex independently runs before/after golden, every logical DB row hash, schemas,
fake mutation count, nonce continuity, and critical code review + exact tests:
python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/test_dual_mock_and_assumed.py tests/test_etf_execution_update.py tests/test_etf_mock_trading.py tests/refactoring/test_execution_owner.py tests/refactoring/test_order_crashes.py tests/refactoring/test_execution_nonce.py tests/refactoring/test_execution_groups.py tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_paper_execution.py tests/refactoring/test_perp_execution.py tests/refactoring/test_tx02_joint_gate.py tests/refactoring/test_tx02a_owner_fence.py tests/refactoring/test_tx02b_attribution.py tests/refactoring/test_tx04_joint_gate.py tests/refactoring/test_tx04b_continuous.py tests/refactoring/test_tx07a_fact_contract.py tests/refactoring/test_tx08_same_account_gate.py tests/refactoring/test_tx08a_account_policy.py tests/test_crypto_perp_pipeline.py tests/test_exchange_expansion.py
Plus canonical imports16, offline wheel/outside repo imports, Docker COPY static.
No actual Broker/API/browser/production use. No push/merge/deploy. Codex final
PASS/FAIL/BLOCKED with real exits/model/diff/hash/private acceptance receipt.

## Repair1 — inspect/replace failed Gemini test draft, no product changes
Gemini partial draft is seeded into only allowed test file. It was NOT applied
or accepted. Replace it with correct <=160-line implementation, do not preserve
its bugs: await owner.close INSIDE one asyncio.run scenario; no arbitrary sleeps.
Reuse sibling helpers exactly as stated. Pass DIRECTORY root/'nonce' to
SignerNonceStore and reopen same directory. Do not pass a *.sqlite3 pathname.
Discover actual FILES for BOTH *.sqlite and *.sqlite3 (not directories, WAL, SHM).
Use readonly SQLite connections URI mode=ro; include all schema/rows. Only the
portfolio_metadata key='path' value may normalize its exact verified temp prefix.
Do not broad-replace strings throughout the snapshot. Return/assert execution
row/status/reserved/position, portfolio balance/position/cancel state, nonces,
and full DB content. Two sends -> one fake mutation. Fill duplicate ->False.
Portfolio cancellation must include confirmed terminal proof before late fill.
Assert FULL golden equality in independent fresh root and expected constants;
second test must prove fake ACK loss UNKNOWN retaining reserve/no retry.
Add the TWO allowed Docker COPY lines (Gemini did not add them). Run exact
new test command and stop. No product/authority/other files or self-approval.

## Repair2 final — fresh, bounded implementation
Discard the seeded Gemini draft; current new test file starts EMPTY. Implement
from worker-context.txt helper APIs and the specification. Do not inspect any
product source, pyproject, directories or unrelated files. Read tool access is
restricted to this ticket/context, two editable files and explicit fixture tests.
Start writing the allowed new test file after reading the context. Use sibling
helper imports, async scenario with await owner.close, fixed clock enclosing
ALL three fixture scenarios, readonly SQLite connections, BOTH .sqlite and
.sqlite3 file types, single metadata-path normalization as specified. Capture
all user tables/indices/schema and ALL sorted rows, return JSON-serializable
values. Clock/order.created_ts_ns must be deterministic. Full golden equality
and constants, second UNKNOWN/no retry safety test. No sleeps, package/runtime
changes, helper framework or source probing. Add the two Docker COPY lines.
Run the exact new test command; final report actual exit + count. Codex accepts.

## Codex final validation disposition (supersedes optional helper-file delivery)
The bounded additional persistent test-helper draft was not delivered after the
initial attempt and two repairs; no draft was applied. There is no new test file
in the active source tree. This does not weaken MOV-04's five-document acceptance:
the product move is the accepted worker-authored deterministic codemod, not a new
manual implementation. Codex independently executed existing254 owner/broker/
ledger/Mock tests,25 inherited-lease tests, and a separate temporary oracle that
compares all schema/rows of3 databases/16 tables, exact intent outcomes, late
fill/cancel/reserve, fake mutation count1 and nonce[1000,1001,1002]. The oracle
uses the same fresh tmpfs virtual root in separate before/after sandboxes and
needs NO path/timestamp/value masking. Whole serialized outcomes are hash-equal.
Artifacts: MOV-04.independent-golden-equality.json, MOV-04.independent-oracle.py,
MOV-04.after-tests-corrected.result.json, MOV-04.inherited-lease-tests.result.json.
All required acceptance commands exit0. Three embedded subprocess import paths
were corrected with nested business AST equality proof. Two Docker COPY lines
were added by Codex under the explicit small-edit allowance. No failed delegated
product logic was taken over; no broker/network/signing/economic code rewritten.
Private worker exits remain recorded as failures/timeouts, never as successes.
