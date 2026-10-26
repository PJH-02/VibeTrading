# CAP-00 — original capability contract and source-grounded gap freeze

Base 271a72f9d8544e9a3ca7432e08194b4dad167d55; MOV-07 accepted. Codex owns
requirements/default selection, final03 Required Capability Matrix, gap ownership,
independent verification and acceptance. No product code/registration changes.
The existing 47-file reuse inventory has 692 enumerated functions, 601 core-audited
functions (plus namespace inventory), not a new 712-function audit. Reuse old
source/caller evidence and verify only current selected functions/direct callers.

Read-only worker packet. Role audit(plan), exact opencode/deepseek-v4.1-flash-free
because same-session GLM429 was verified without edits. Use /work/ paths. No model
switching, edits, authority/status writes, delegation, installs, credentials,
production/account/network calls, Git mutation or self-approval.
Read /work/cap-context.json first. Then only the selected functions and 1-hop
callers in /work/src/execution/{brokers,kis_execution,perp_execution,extended_brokers,
bybit_execution,execution_owner,execution_groups}.py, /work/src/runtime/{etf_trading,
strategy_runtime}.py, /work/src/timefolio/{runner,client}.py,
/work/src/feeds/providers/{kiwoom,toss}.py, /work/services/execution/kiwoom_broker.py,
and relevant test functions named in context. Never read all older plans or all
functions again. Archived fv_mm/venues/private and liquidity_mm are historical
reuse evidence only, not shipped/default execution. Source model and tests do not
certify external/live support.

Report <=1500 words with exact path:line qualified functions and direct callers:
1. KIS ETF MOCK/MOCK2 uses KisCashBroker through EtfTradingLoop, not BrokerStub;
C3/KP/KD helpers are not full file action-feedback plugins.
2. Kiwoom REST submit plus exact query/cancel/open/fills/account/reconcile gaps;
order_status NOT_IMPLEMENTED, distinguish Windows COM environment. Cite only
verified existing APIs. Mark missing contract BLOCKED_API_SPEC_REQUIRED instead
of inventing URLs/TR IDs. External authentication is a separate gate, not an
excuse for absent local protocol code/tests.
3. Five linear-perp primitives and native Bybit/GRVT historical testnet code,
Binance Spot != USD-M; injected transport/normalizers/signers != working owner or
live private connection. Identify exact missing composition, no blanket rebuild.
4. TOSS required supported data + mutation DATA_ONLY; generic paper support;
Cross-venue code remains REQUIRED_CORE_CODE even without a real pair; observed
PaperExecutionGroups isn't actual concurrent/sequential order execution.
5. NH/Namoo/new venue EXTENSION_FIXTURE_ONLY, no live implementation claim;
Timefolio is separate contest route with submit false/account-arm fences, not an
ordinary venue signer. Unsupported inverse/margin/HIP3/HL spot must stay blocked.
No alpha development, new strategy engine, API guesses or scope downgrades.
Existing full Linux accepted2509 tests prove only the recorded local functions.
Codex independently builds per-row symbolic account/strategy/env/order-mode
records, source hashes/function ASTs, actual accepted test nodeids, external gates,
gap ticket IDs and default required scope. All implementation gaps remain visible
for VEN/STR/EXEC/CAP tickets; CAP-00 freezes a contract, not platform completion.
