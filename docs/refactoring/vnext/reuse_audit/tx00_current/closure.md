# TX00 current-source reuse audit — closure

**Status:** static local evidence only. This is not a functional-completeness,
runtime, testnet, live, deployment, or order-execution certificate. Existing
product/cloud/strategy behavior remains authoritative; no product source,
tests, configuration, historical report, baseline, or canonical metadata was
modified.

## Scope and actual verification

- Audited source SHA: `fa2478e5ca15b92d1db69d1cc7e0d8f7266b7146`; frozen
  historical source SHA: `4977ab509490173e312183f32d47f862d94ee0bf`.
- Scope: the exact 47 baseline paths. Current AST: **712 functions**, of which
  **679 retained unchanged**, **13 retained changed**, **20 added**, **0
  removed**, compared with **692** historical definitions by
  `(path, definition_id)`.
- Exactly **3** source files differ from the frozen historical source:
  `src/vibetrading/etf_trading.py`, `src/vibetrading/portfolio_ledger.py`, and
  `src/vibetrading/strategy_runtime.py`. All added functions are helper-only
  `REUSE` entries in the accepted VN07/VN08 ETF/Paper recovery seams. No
  replacement, retirement, or deletion approval is implied.
- Caller evidence uses `git ls-files -- '*.py'` only: **510 tracked candidate
  inputs** under `src/`, `services/`, `scripts/`, and `tests/`, yielding **459
  direct import-qualified AST call references**. These are references, not
  runtime or execution counts. Untracked files, `.env`, credentials, and
  runtime stores are excluded. A `git ls-files` failure or any
  missing/unreadable/unparseable tracked candidate is a hard nonzero error.
- Commands actually run from `/work`:

  ```
  python3 docs/refactoring/vnext/reuse_audit/tx00_current/check.py --root . --write   # exit 0
  python3 docs/refactoring/vnext/reuse_audit/tx00_current/check.py --root . --check   # exit 0
  ```

  `--write` produced only the four declared JSON siblings; `--check` read only
  those four files and recomputed them without rewriting. No `/tmp` mirror or
  read-only workaround exists.

Generated evidence: [inventory.json](./inventory.json),
[dispositions.json](./dispositions.json), [callers.json](./callers.json),
[delta.json](./delta.json). Historical [execution_reuse_matrix.md](../execution_reuse_matrix.md)
and the prior raw reports remain unchanged historical evidence; line numbers
below are from the **current** inventory/source, not from the historical matrix.

## A. Existing runtime and venue routes (A1-A17)

Legend: `SUPPORTED_CODE`, `FIXTURE`, `SANDBOX_REQUIRED`, `LIVE_REQUIRED`,
`UNSUPPORTED`, `UNKNOWN` are evidence labels, never operational certification.
"Unknown" means not proven by this static audit, not an assertion that the
route is unsupported.

| # | Venue / product / environment | Account, mode, position-margin domain | Current inventory functions and source links | Current caller | Evidence / honest limit |
|---|---|---|---|---|---|
| A1 | Bybit linear perp, **testnet** (`liquidity_mm`) | Testnet API credentials; net position via runner; no live claim | [inventory](./inventory.json): `TestnetClient.create` [exchange.py:131](../../../../../src/vibetrading/liquidity_mm/exchange.py#L131), `.cancel` [:142](../../../../../src/vibetrading/liquidity_mm/exchange.py#L142), `.positions` [:66](../../../../../src/vibetrading/liquidity_mm/exchange.py#L66), `.wallet` [:69](../../../../../src/vibetrading/liquidity_mm/exchange.py#L69), `.connect_private` [:146](../../../../../src/vibetrading/liquidity_mm/exchange.py#L146); `Runner.submit` [runner.py:299](../../../../../src/vibetrading/liquidity_mm/runner.py#L299), `.cancel` [:346](../../../../../src/vibetrading/liquidity_mm/runner.py#L346), `.order_event` [:161](../../../../../src/vibetrading/liquidity_mm/runner.py#L161), `.execution_event` [:184](../../../../../src/vibetrading/liquidity_mm/runner.py#L184), `.process` [:122](../../../../../src/vibetrading/liquidity_mm/runner.py#L122), `.reconcile` [:369](../../../../../src/vibetrading/liquidity_mm/runner.py#L369) | `liquidity_mm/cli.py:67/93` -> `execute runner.py:720`; `__main__.py:3` | `SANDBOX_REQUIRED` testnet code. Private stream and REST history are separate evidence paths. No live execution proof. |
| A2 | Bybit linear perp, **testnet** (`fv_mm`) | Testnet account; normalized private events; reconcile from venue facts | [inventory](./inventory.json): `Bybit.create` [venues.py:76](../../../../../src/vibetrading/fv_mm/venues.py#L76), `.cancel` [:85](../../../../../src/vibetrading/fv_mm/venues.py#L85), `.order` [:88](../../../../../src/vibetrading/fv_mm/venues.py#L88), `.fills` [:98](../../../../../src/vibetrading/fv_mm/venues.py#L98), `.positions` [:67](../../../../../src/vibetrading/fv_mm/venues.py#L67), `.account` [:63](../../../../../src/vibetrading/fv_mm/venues.py#L63); `bybit_private` [private.py:9](../../../../../src/vibetrading/fv_mm/private.py#L9); `Runner.create` [runner.py:228](../../../../../src/vibetrading/fv_mm/runner.py#L228), `.on_order` [:83](../../../../../src/vibetrading/fv_mm/runner.py#L83), `.on_fill` [:113](../../../../../src/vibetrading/fv_mm/runner.py#L113), `.reconcile` [:151](../../../../../src/vibetrading/fv_mm/runner.py#L151), `.account_update` [:193](../../../../../src/vibetrading/fv_mm/runner.py#L193) | `scripts/run_fv_testnets.py:10/25-29`; factory `fv_mm/runner.py:351` | `SANDBOX_REQUIRED`/`REUSE`; testnet adapter composition. No live proof. |
| A3 | Bybit probe, **testnet** | Probe-only credential/account; market-trigger probe; no in-repo `run` caller | [inventory](./inventory.json): `BybitExecutionProbe._run_market_trigger_order` [bybit_execution.py:344](../../../../../src/vibetrading/bybit_execution.py#L344), `._reconcile` [:305](../../../../../src/vibetrading/bybit_execution.py#L305), `._listen` [:273](../../../../../src/vibetrading/bybit_execution.py#L273), `BybitAudit.fill` [:130](../../../../../src/vibetrading/bybit_execution.py#L130); cancel probe [:406](../../../../../src/vibetrading/bybit_execution.py#L406), [:510](../../../../../src/vibetrading/bybit_execution.py#L510); position queries [:438](../../../../../src/vibetrading/bybit_execution.py#L438), [:460](../../../../../src/vibetrading/bybit_execution.py#L460) | `tests/test_crypto_perp_pipeline.py:8/112-122`; no in-repo runtime caller | `FIXTURE`/`SANDBOX_REQUIRED`. Cancel ACK is not terminal fill. Live kill path around `:329` is a reported defect claim only, not repaired here. |
| A4 | GRVT `*_USDT_Perp`, **testnet** (`fv_mm`) | Testnet account, subaccount/chain identity; net position | [inventory](./inventory.json): `Grvt.create` [venues.py:195](../../../../../src/vibetrading/fv_mm/venues.py#L195), `.signed_order` [:182](../../../../../src/vibetrading/fv_mm/venues.py#L182), `.cancel` [:198](../../../../../src/vibetrading/fv_mm/venues.py#L198), `.order` [:201](../../../../../src/vibetrading/fv_mm/venues.py#L201), `.fills` [:224](../../../../../src/vibetrading/fv_mm/venues.py#L224), `.history` [:211](../../../../../src/vibetrading/fv_mm/venues.py#L211), `.positions` [:175](../../../../../src/vibetrading/fv_mm/venues.py#L175), `.account` [:169](../../../../../src/vibetrading/fv_mm/venues.py#L169); `grvt_private` [private.py:35](../../../../../src/vibetrading/fv_mm/private.py#L35) | `scripts/run_fv_testnets.py:10/25-29`; factory `fv_mm/runner.py:351` | `SANDBOX_REQUIRED`/`REUSE`; signed testnet composition. No live proof. |
| A5 | GRVT **live-WS prototype**, legacy `extended_brokers` | Legacy adapter; account subaccount; environment selection not fully proven (`UNKNOWN` live/testnet) | [inventory](./inventory.json): `GrvtBrokerAdapter.submit_order` [extended_brokers.py:306](../../../../../src/vibetrading/extended_brokers.py#L306), `.prepare_order` [:262](../../../../../src/vibetrading/extended_brokers.py#L262), `.order_status` [:316](../../../../../src/vibetrading/extended_brokers.py#L316), `.account_state` [:312](../../../../../src/vibetrading/extended_brokers.py#L312); `_rpc` [:70](../../../../../src/vibetrading/extended_brokers.py#L70), `wss` [:227](../../../../../src/vibetrading/extended_brokers.py#L227), `connect` [:237](../../../../../src/vibetrading/extended_brokers.py#L237) | `brokers.broker_for_name` [brokers.py:300](../../../../../src/vibetrading/brokers.py#L300) is dynamic; `UNKNOWN` caller | `KEEP` legacy prototype. Fills/reconcile are `UNKNOWN` in the excerpt. No new wrapper or live claim. |
| A6 | Binance **spot**, legacy services | Spot, not perp; REST/listen user stream; account balance | [inventory](./inventory.json): `CryptoBinanceAdapter.submit_order` [crypto_binance.py:134](../../../../../services/execution/crypto_binance.py#L134), `.cancel_order` [:185](../../../../../services/execution/crypto_binance.py#L185), `.get_order_status` [:208](../../../../../services/execution/crypto_binance.py#L208), `._handle_execution_report` [:108](../../../../../services/execution/crypto_binance.py#L108), `._listen_user_stream` [:88](../../../../../services/execution/crypto_binance.py#L88), `.get_account_balance` [:262](../../../../../services/execution/crypto_binance.py#L262) | `services/broker_factory.py:36-40` legacy/dynamic | `KEEP` spot. Reconciliation/position are scoped `COMPLETE_MISSING`/N/A for spot; balance error-to-zero remains a risk note, not repaired. |
| A7 | Binance **spot**, `brokers.py` | Spot; account state only | [inventory](./inventory.json): `BinanceBrokerAdapter.submit_order` [brokers.py:70](../../../../../src/vibetrading/brokers.py#L70), `.order_status` [:87](../../../../../src/vibetrading/brokers.py#L87), `.account_state` [:56](../../../../../src/vibetrading/brokers.py#L56) | `broker_for_name` [brokers.py:300](../../../../../src/vibetrading/brokers.py#L300) is dynamic; `UNKNOWN` caller | `KEEP` spot. Private transport and fills are `UNKNOWN`; no perp claim. |
| A8 | Arcus linear perp, **live-WS prototype** | Legacy adapter; signer/account; environment not fully proven (`UNKNOWN` live/testnet) | [inventory](./inventory.json): `ArcusBrokerAdapter.submit_order` [extended_brokers.py:187](../../../../../src/vibetrading/extended_brokers.py#L187), `.prepare_order` [:148](../../../../../src/vibetrading/extended_brokers.py#L148), `.order_status` [:197](../../../../../src/vibetrading/extended_brokers.py#L197), `.account_state` [:192](../../../../../src/vibetrading/extended_brokers.py#L192); `_rpc` [:70](../../../../../src/vibetrading/extended_brokers.py#L70), `wss` [:78](../../../../../src/vibetrading/extended_brokers.py#L78), signer [:132](../../../../../src/vibetrading/extended_brokers.py#L132) | `broker_for_name` [brokers.py:300](../../../../../src/vibetrading/brokers.py#L300) is dynamic; `UNKNOWN` caller | `KEEP`/`REUSE signer`. Fills are history-only (`complete_history:False`); account granularity `UNKNOWN`. |
| A9 | KIS cash/ETF, actual ETF runtime **mock/mock2** | Distinct mock/mock2 account domains; cash/ETF logical strategy books; no live account | [inventory](./inventory.json): `KisCashBroker.submit_order` [kis_execution.py:137](../../../../../src/vibetrading/kis_execution.py#L137), `.submit_order_with_permit` [:140](../../../../../src/vibetrading/kis_execution.py#L140), `.cancel_order` [:209](../../../../../src/vibetrading/kis_execution.py#L209), `.order_status` [:167](../../../../../src/vibetrading/kis_execution.py#L167), `.account_state` [:115](../../../../../src/vibetrading/kis_execution.py#L115), `.buying_power` [:132](../../../../../src/vibetrading/kis_execution.py#L132); `EtfJournal.apply_status` [etf_journal.py:312](../../../../../src/vibetrading/etf_journal.py#L312), `KisCashBroker.daily_fills` [:185](../../../../../src/vibetrading/kis_execution.py#L185); `EtfTradingLoop.reconcile` [etf_trading.py:562](../../../../../src/vibetrading/etf_trading.py#L562), `.place` [:430](../../../../../src/vibetrading/etf_trading.py#L430), `._submit_staged` [:383](../../../../../src/vibetrading/etf_trading.py#L383), `.initialize` [:199](../../../../../src/vibetrading/etf_trading.py#L199), `.run` [:899](../../../../../src/vibetrading/etf_trading.py#L899), `._run` [:911](../../../../../src/vibetrading/etf_trading.py#L911); `main` [:998](../../../../../src/vibetrading/etf_trading.py#L998) | `etf_trading.main:998` -> `run:899` -> `_run:911`; `place:430` -> `_submit_staged:383` | `FIXTURE`/`SANDBOX_REQUIRED` mock/paper route, not live. No private stream in scoped files; REST polling only. Cancel ACK is not fill proof. Changed recovery helpers are code-only. |
| A10 | KIS legacy factory wrapper, `BrokerStub` | In-memory stub; no real account | [inventory](./inventory.json): `BrokerStub.submit_order` [broker_stub.py:69](../../../../../services/execution/broker_stub.py#L69), `.cancel_order` [:143](../../../../../services/execution/broker_stub.py#L143), `.get_order_status` [:164](../../../../../services/execution/broker_stub.py#L164), `._execute_fill` [:92](../../../../../services/execution/broker_stub.py#L92), `.get_account_balance` [:187](../../../../../services/execution/broker_stub.py#L187) | `services/broker_factory.py:23/55`; ETF uses `KisCashBroker` directly | `KEEP legacy`; must not be used as ETF runtime support evidence. Reconciliation is legacy-only `COMPLETE_MISSING`. |
| A11 | Kiwoom KRX equity/ETF **Windows COM** | Windows/COM account; legacy live-capable adapter; no durable private stream | [inventory](./inventory.json): `KiwoomBrokerAdapter.submit_order` [kiwoom_broker.py:378](../../../../../services/execution/kiwoom_broker.py#L378), `.cancel_order` [:436](../../../../../services/execution/kiwoom_broker.py#L436), `.get_order_status` [:477](../../../../../services/execution/kiwoom_broker.py#L477), `._handle_chejan_sync` [:317](../../../../../services/execution/kiwoom_broker.py#L317), `KiwoomOpenAPI._on_receive_chejan_data` [:154](../../../../../services/execution/kiwoom_broker.py#L154), `.get_account_balance` [:496](../../../../../services/execution/kiwoom_broker.py#L496), `._get_balance_sync` [:509](../../../../../services/execution/kiwoom_broker.py#L509); `KiwoomOpenAPI.send_order` [:208](../../../../../services/execution/kiwoom_broker.py#L208) | `services/broker_factory.py:46`; Windows/COM `:278-279` | `KEEP legacy`; status cache-only, reconcile `COMPLETE_MISSING`, COM chejan not durable. Defects are not approved for repair here. |
| A12 | Kiwoom REST market data | Data-only HTTP; no order account | [inventory](./inventory.json): `EtfTradingLoop.evaluate_c3` [etf_trading.py:633](../../../../../src/vibetrading/etf_trading.py#L633); `EtfHistory` [etf_history.py:74](../../../../../src/vibetrading/etf_history.py#L74) | ETF runtime `evaluate_c3:633`; TOSS backfill observation in `EtfTradingLoop.event` [:656](../../../../../src/vibetrading/etf_trading.py#L656) | `KEEP data-only`. Submit/cancel/fills/positions are N/A for this data route. |
| A13 | TOSS, **DATA_ONLY** | Data/backfill only; no active order account API | [inventory](./inventory.json): `TossBrokerAdapter.submit_order` [brokers.py:285](../../../../../src/vibetrading/brokers.py#L285) always rejects, `.order_status` [:288](../../../../../src/vibetrading/brokers.py#L288), `.account_state` [:282](../../../../../src/vibetrading/brokers.py#L282); `broker_for_name` [:300](../../../../../src/vibetrading/brokers.py#L300); ETF backfill at [etf_trading.py:664](../../../../../src/vibetrading/etf_trading.py#L664) | `broker_for_name:300` dynamic; ETF backfill path | `KEEP DATA_ONLY`. No active TOSS order API is claimed. Cancel/fills/private/position/reconcile are N/A. |
| A14 | Hyperliquid **read-only broker** | Public `/info`; no private trading transport | [inventory](./inventory.json): `HyperliquidBrokerAdapter.submit_order` [brokers.py:260](../../../../../src/vibetrading/brokers.py#L260) raises, `.order_status` [:264](../../../../../src/vibetrading/brokers.py#L264), `.account_state` [:249](../../../../../src/vibetrading/brokers.py#L249) | `broker_for_name` [brokers.py:300](../../../../../src/vibetrading/brokers.py#L300) is dynamic; `UNKNOWN` caller | `KEEP placeholder`; not venue-support evidence. Private transport/positions/reconcile are N/A or `UNKNOWN`. |
| A15 | Internal **PAPER only** | Paper accounts and per-strategy books; cash/linear-perp simulation, no live margin authority | [inventory](./inventory.json): `PaperExecution.submit` [:98](../../../../../src/vibetrading/paper_execution.py#L98); `PaperExecution.cancel` [:154](../../../../../src/vibetrading/paper_execution.py#L154); `PaperExecution._fill` [:239](../../../../../src/vibetrading/paper_execution.py#L239); `PaperExecution.on_book` [:294](../../../../../src/vibetrading/paper_execution.py#L294); `PaperExecution.on_trade` [:375](../../../../../src/vibetrading/paper_execution.py#L375); `PaperExecution.report` [:409](../../../../../src/vibetrading/paper_execution.py#L409); `PortfolioLedger.admit_order` [:416](../../../../../src/vibetrading/portfolio_ledger.py#L416); `PortfolioLedger.request_cancel` [:579](../../../../../src/vibetrading/portfolio_ledger.py#L579); `PortfolioLedger.active_orders` [:389](../../../../../src/vibetrading/portfolio_ledger.py#L389); `PortfolioLedger.apply_fill` [:520](../../../../../src/vibetrading/portfolio_ledger.py#L520); `PortfolioLedger.position` [:347](../../../../../src/vibetrading/portfolio_ledger.py#L347); `PortfolioLedger.report` [:662](../../../../../src/vibetrading/portfolio_ledger.py#L662); `PortfolioLedger.seed_balance` [:331](../../../../../src/vibetrading/portfolio_ledger.py#L331); `PortfolioLedger.apply_funding` [:613](../../../../../src/vibetrading/portfolio_ledger.py#L613); `PortfolioLedger.reconcile_positions` [:634](../../../../../src/vibetrading/portfolio_ledger.py#L634); `PaperStrategyRuntime.process_event` [:281](../../../../../src/vibetrading/strategy_runtime.py#L281); `PaperStrategyRuntime._execute` [:802](../../../../../src/vibetrading/strategy_runtime.py#L802) | Existing PaperStrategyRuntime -> PaperExecution and PortfolioLedger | `FIXTURE`/local PAPER; reconcile_positions compares only; no new matcher/ledger or live margin claim. |
| A16 | Offline permit owner | Offline budgets/ledger; adapter caller not in read set | [inventory](./inventory.json): `ExecutionOwner.dispatch` [execution_owner.py:218](../../../../../src/vibetrading/execution_owner.py#L218) -> adapter `submit_order_with_permit` call site [:252](../../../../../src/vibetrading/execution_owner.py#L252); budgets [:24](../../../../../src/vibetrading/execution_owner.py#L24) | Adapter caller not in read set: `UNKNOWN` | `KEEP offline-only`. No venue submit/cancel/status/fills/private/position proof. |
| A17 | Cloud storage | Storage account/runtime transport; no order domain | [inventory](./inventory.json): `archive_backend` [storage_backend.py:178](../../../../../src/vibetrading/storage_backend.py#L178); `NcloudStorageBackend.put_file` [:123](../../../../../src/vibetrading/storage_backend.py#L123), `.put_bytes` [:134](../../../../../src/vibetrading/storage_backend.py#L134), `.verify` [:143](../../../../../src/vibetrading/storage_backend.py#L143), `.download_file` [:165](../../../../../src/vibetrading/storage_backend.py#L165) | `archive_backend:178`; existing storage callers | `KEEP` existing upload/download/hash transport. No new uploader. N/A for orders/live trading. |

## B. Five normalized Perp adapters (test-visible composition)

All five are `REUSE` mapping/composition code with injected transport and
explicit `code_complete=False` / `REQUEST_MAPPING_ONLY` /
`BLOCKED_UNTIL_OWNER_TRANSPORT_ACCOUNT_SIGNER_PROOF` status. No `src/` runtime
or deployment caller is recorded; their runtime/owner wiring is `UNKNOWN`.
Current callers are tests and the factory at
`perp_execution.py:1308-1313`.

| Adapter | Product / environment / account-mode | Current inventory functions and source links | Evidence limits |
|---|---|---|---|
| **BinancePerpAdapter** | Binance USD-M linear perp; base demo/live at [perp_execution.py:369](../../../../../src/vibetrading/perp_execution.py#L369); account scope supplied by injected transport; **net/cross** only (hedge/isolated rejected) | [inventory](./inventory.json): `submit_with_permit` [:423](../../../../../src/vibetrading/perp_execution.py#L423), `_call` [:371](../../../../../src/vibetrading/perp_execution.py#L371), `cancel_with_permit` [:435](../../../../../src/vibetrading/perp_execution.py#L435), `order_status` [:441](../../../../../src/vibetrading/perp_execution.py#L441), `fills` [:457](../../../../../src/vibetrading/perp_execution.py#L457), `funding` [:471](../../../../../src/vibetrading/perp_execution.py#L471), `positions` [:406](../../../../../src/vibetrading/perp_execution.py#L406), `account_state` [:397](../../../../../src/vibetrading/perp_execution.py#L397), `normalize_private_event` [:481](../../../../../src/vibetrading/perp_execution.py#L481), inherited `_Adapter.reconcile` [:344](../../../../../src/vibetrading/perp_execution.py#L344) | `FIXTURE`; no private stream, no new REST wrapper. Cancel is ACK only; 4xx status stays `UNKNOWN`. No live proof. |
| **BybitPerpAdapter** | Bybit linear perp; base testnet/live at [:504](../../../../../src/vibetrading/perp_execution.py#L504); **net** only (hedge rejected) | [inventory](./inventory.json): `submit_with_permit` [:579](../../../../../src/vibetrading/perp_execution.py#L579), `cancel_with_permit` [:592](../../../../../src/vibetrading/perp_execution.py#L592), `order_status` [:598](../../../../../src/vibetrading/perp_execution.py#L598), `fills` [:639](../../../../../src/vibetrading/perp_execution.py#L639), `_history` [:617](../../../../../src/vibetrading/perp_execution.py#L617), `funding` [:642](../../../../../src/vibetrading/perp_execution.py#L642), `positions` [:562](../../../../../src/vibetrading/perp_execution.py#L562), `account_state` [:551](../../../../../src/vibetrading/perp_execution.py#L551), `normalize_private_event` [:645](../../../../../src/vibetrading/perp_execution.py#L645), inherited `_Adapter.reconcile` [:344](../../../../../src/vibetrading/perp_execution.py#L344) | `FIXTURE`; normalize-only private events, no stream. No live proof. |
| **GrvtPerpAdapter** | GRVT `*_USDT_Perp`; sandbox/prod chain selected at [:732](../../../../../src/vibetrading/perp_execution.py#L732); subaccount scope; net position | [inventory](./inventory.json): `submit_with_permit` [:724](../../../../../src/vibetrading/perp_execution.py#L724), `GrvtSigningComposition.prepare` [:181](../../../../../src/vibetrading/perp_execution.py#L181), `cancel_with_permit` [:752](../../../../../src/vibetrading/perp_execution.py#L752), `order_status` [:759](../../../../../src/vibetrading/perp_execution.py#L759), `fills` [:796](../../../../../src/vibetrading/perp_execution.py#L796), `_history` [:779](../../../../../src/vibetrading/perp_execution.py#L779), `funding` [:799](../../../../../src/vibetrading/perp_execution.py#L799), `positions` [:705](../../../../../src/vibetrading/perp_execution.py#L705), `account_state` [:697](../../../../../src/vibetrading/perp_execution.py#L697), `normalize_private_event` [:802](../../../../../src/vibetrading/perp_execution.py#L802), inherited `_Adapter.reconcile` [:344](../../../../../src/vibetrading/perp_execution.py#L344) | `FIXTURE`; signer/normalize reuse. Client-ID claim is `UNVERIFIED`; no live proof. |
| **ArcusPerpAdapter** | Arcus linear perp; testnet/live host at [:867](../../../../../src/vibetrading/perp_execution.py#L867); signer check at [:829](../../../../../src/vibetrading/perp_execution.py#L829); account scope supplied | [inventory](./inventory.json): `submit_with_permit` [:951](../../../../../src/vibetrading/perp_execution.py#L951), `ArcusSigningComposition.prepare` [:834](../../../../../src/vibetrading/perp_execution.py#L834), `cancel_with_permit` [:983](../../../../../src/vibetrading/perp_execution.py#L983), composition cancel [:839](../../../../../src/vibetrading/perp_execution.py#L839), `order_status` [:1005](../../../../../src/vibetrading/perp_execution.py#L1005), `fills` [:1053](../../../../../src/vibetrading/perp_execution.py#L1053), `_history` [:1038](../../../../../src/vibetrading/perp_execution.py#L1038), `funding` [:1056](../../../../../src/vibetrading/perp_execution.py#L1056), `positions` [:907](../../../../../src/vibetrading/perp_execution.py#L907), `account_state` [:898](../../../../../src/vibetrading/perp_execution.py#L898), `normalize_private_event` [:1059](../../../../../src/vibetrading/perp_execution.py#L1059), inherited `_Adapter.reconcile` [:344](../../../../../src/vibetrading/perp_execution.py#L344) | `FIXTURE`; no sequence consumption; transport protocol lacks an `rpc` declaration. No live proof. |
| **HyperliquidPerpAdapter** | Hyperliquid perp; testnet/live base at [:1117](../../../../../src/vibetrading/perp_execution.py#L1117), `is_mainnet` at [:1190](../../../../../src/vibetrading/perp_execution.py#L1190); account scope supplied | [inventory](./inventory.json): `submit_with_permit` [:1210](../../../../../src/vibetrading/perp_execution.py#L1210), SDK signer [:1085](../../../../../src/vibetrading/perp_execution.py#L1085), `cancel_with_permit` [:1229](../../../../../src/vibetrading/perp_execution.py#L1229), `order_status` [:1239](../../../../../src/vibetrading/perp_execution.py#L1239), `fills` [:1282](../../../../../src/vibetrading/perp_execution.py#L1282), `_history` [:1267](../../../../../src/vibetrading/perp_execution.py#L1267), `funding` [:1285](../../../../../src/vibetrading/perp_execution.py#L1285), `positions` [:1157](../../../../../src/vibetrading/perp_execution.py#L1157), `account_state` [:1148](../../../../../src/vibetrading/perp_execution.py#L1148), `normalize_private_event` [:1288](../../../../../src/vibetrading/perp_execution.py#L1288), inherited `_Adapter.reconcile` [:344](../../../../../src/vibetrading/perp_execution.py#L344) | `FIXTURE`; SDK composition only. Separate from the Hyperliquid read-only broker stub (A14). No live proof. |

## C. Current delta (generated)

`delta.json` compares at `(path, definition_id)`; no historical disposition
was silently dropped.

- **Changed retained functions (13):**
  - `src/vibetrading/etf_trading.py`: `EtfTradingLoop.__init__@1`,
    `EtfTradingLoop._check_dispatch@1`, `EtfTradingLoop._submit_staged@1`,
    `EtfTradingLoop.place@1`.
  - `src/vibetrading/strategy_runtime.py`: `PaperStrategyRuntime.__init__@1`,
    `.register@1`, `.process_applied@1`, `._dispatch@1`, `.pause@1`,
    `.resume@1`, `.halt@1`, `.advance@1`, `.report@1`.
  - These retain their historical preservation disposition (`REUSE`) and are
    explicitly marked changed since the historical audit. No blanket
    AST-preservation claim is made.
- **Added functions (20), all `REUSE` helper-only VN07/VN08 recovery context:**
  - `src/vibetrading/etf_trading.py`: `EtfTradingLoop.begin_checkpoint_recovery`,
    `.quarantine_checkpoint_recovery`, `.mark_checkpoint_recovery_ready`,
    `._check_checkpoint_dispatch`.
  - `src/vibetrading/portfolio_ledger.py`: `PortfolioLedger.has_strategy_dispatch`.
  - `src/vibetrading/strategy_runtime.py`: `PaperStrategyRuntime.recovery_state`,
    `._recovery_gate`, `.begin_checkpoint_recovery`,
    `.quarantine_checkpoint_recovery`, `._checkpoint_capture_guard`,
    `._json_state`, `._plugin_state`, `._runtime_identity`,
    `._job_state_document`, `._current_runtime_state`,
    `.capture_runtime_state`, `.restore_runtime_state`,
    `.checkpoint_restore_proof`, `.publish_checkpoint_restore`, `._hold_blocked`.
  - These are code-only static evidence of accepted recovery seams, not new
    runtime/platform functionality and not runtime/live proof.
- **Removed functions:** none.
- The other 44 scope paths, including exchange wrappers, are raw-source
  unchanged from the frozen historical source; their current line numbers equal
  the historical references used by the matrix.

## D. Limitations, labels, and next gap

- Caller collection is direct, import-qualified AST evidence only. A receiver
  that is itself a call has an unknown returned-object type: for example,
  `broker_for_name('toss').submit_order(...)` yields only the inner direct
  factory call, never an invented method target. The same applies to dynamic
  `self`/`cls`, reflection, `getattr`, duck-typed receivers, and runtime
  tracing.
- Wildcard imports in a relevant lexical scope/ancestor make name resolution
  `UNKNOWN`; conflicting same-name imports from different modules, including
  alternative branches, remain unresolved rather than taking the last
  AST-walk binding. Parameter, assignment, and lambda shadowing remain
  unresolved. Nested calls are visited once.
- Class constructor references prove class identity only, not that `__init__`
  or any method executes. Counts are static references, not execution counts.
- Candidate coverage is only tracked `.py` files returned by `git ls-files`
  under the four input roots. There is no filesystem/untracked fallback, no
  secret or runtime-data reading, and no implied tracked-only coverage after a
  git failure. Missing, unreadable, or unparseable tracked candidates block
  success with nonzero exit.
- Environment/account evidence is explicit above: testnet for A1-A4,
  fixture/test-only for A3 and PAPER, mock for A9, Windows COM for A11,
  data-only for A12/A13, read-only for A14, and offline for A16. Live/testnet
  status is not upgraded by static code inspection. Normalized Perp adapters
  have injected transport, no `src/` runtime caller, and `code_complete=False`.
- Cancel acknowledgements are not terminal fills; `UNKNOWN` preserves
  reservation rather than implying cancellation or rejection. Position/margin
  modes remain product-specific: cash for KIS/ETF and spot, net/cross or
  explicitly rejected hedge/isolated modes for the normalized Perps, and a
  paper simulation model-only margin for PAPER. No universal margin engine is
  claimed.
- TOSS remains `DATA_ONLY` with no active order API. Existing cloud
  upload/download/hash transport remains `KEEP`. Historical raw reports,
  `function_inventory.json`, `function_dispositions.json`, `direct_callers.json`,
  and the execution reuse matrix remain unchanged historical evidence; this
  directory provides the current-source translation.
- **Minimum subsequent gap (TX01):** the existing `Signal` / `OrderIntent` /
  `PaperExecution` bridge and identity/lifecycle expectations. This closure
  records evidence only; it grants no product work, replacement, retirement,
  deletion, deployment, or live-trading authority.

Reviewer clarification: EtfHistory is an auxiliary data-path source reference outside the 47-file execution inventory. Source hashes bind the canonical LF Git checkout; the primary Windows checkout retains preexisting CRLF edits and is checked separately for EOL-normalized equivalence.
