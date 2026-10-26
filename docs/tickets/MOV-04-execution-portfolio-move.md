# MOV-04 Execution/Portfolio Move Only, fake-mutation and SQLite parity

Origin source HEAD f49d97da8f4e07edc74fe61b8654e5136666d513. Exact worker opencode/mimo-v2.6-flash-free, role test.
MiMo completed MOV-03 worker exit0. User requested other free models; Gemini
actual timeout/502/404, MiniMax503, Kimi absent. No model switching by workers.

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
JSON. Return paths relative to root, never absolute temp roots. No selective row
filtering, UUID/path/timestamp masking or golden value rebaselining. Clock makes
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
