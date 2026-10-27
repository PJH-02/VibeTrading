# STR-01 — C3/KP/KD native file plugins

Base af0210f71732d95c8224cce9912717b9fda75600. Prerequisites MOV-05/CAP-00.
Authority: final/02 STR-01 and final/03 ARCH-ACTION/STRAT/EXEC. LOCAL/PAPER/FAKE
only; no live accounts, installs, server work, push/merge/deploy or source-data
deletion. Existing KIS Mock/Mock2 API/physical owner and frozen economics remain.

Objective: each native C3/KP/KD file explicitly loads and produces source-owned
signals/order plans, using the existing Paper host and same original ETF policy.
No new strategy engine/registry/client or automatic registration. Original
C3 regression, native session/foreign cutoff, KP/KD minute/30m stops, prices,
weights/quantities, account routes and5sec cancel/confirmed market remainder
remain in the strategy package; execution owns journal/permits/risk/fill facts.

Scope: src/strategies/kr/{c3,kp,kd,etf_history,_etf_plugin}.py;
src/runtime/{strategy_runtime,etf_trading}.py;src/market/hot_path.py;
src/execution/paper_execution.py (explicit opt-in owner capabilities only);
new STR01 tests, bounded example JSON and exact touched-source CRLF attributes.
No historical oracles/04 edits. Default Paper behavior remains compatible.

Native inputs: exact Kiwoom KRX prefixed ETF identifiers and KIS index raw2001;
checksum-bound CSV daily/minute/US history is prepared outside callbacks. Missing
history/mode/account/product/feed/capability bindings reject before claims.
Optional synchronous configure/registration validation repeats on checkpoint
recreation. Context is immutable factual account/position/order data only.

Acceptance: all3 real files load/register; native CSV/raw events produce real
simulated fills; original KIS decision/sizing/price/route/cutoff oracles unchanged;
non30m KD stop/session policy, positive-volume native C3 open/delayed index,
sticky NO_ENTRY, zero/sub-lot skip, partial GTC→cancel→confirmed market remainder,
UNKNOWN fences, restart/default compatibility, callback no I/O, per-file removal
and offline installed imports. New diff diagnostics0.

Worker route: prior OpenCode repair allowance exhausted. User explicitly
permitted gpt-6-luna native fallback on2026-10-27;3 isolated writer slices, Codex
review/independent tests and bounded integration corrections. Lead auth unchanged.
Executed slice packets/private hashes live in the private run receipt.

Exact independent tests:
- python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin --ignore=tests/refactoring/test_vnext_vn10.py tests
- python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/refactoring/test_vnext_vn10.py (credential-free network/process guard)
- Codex private original-runtime39 plus owner differential cases.
- python3 -m pip wheel . --no-deps --no-build-isolation --no-index (private offline build), outside-root installed imports and c3/kp/kd removal imports.
