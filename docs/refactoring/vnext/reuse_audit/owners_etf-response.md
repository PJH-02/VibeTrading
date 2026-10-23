## REUSE audit — `owners_etf` at HEAD `4977ab509490173e312183f32d47f862d94ee0bf`

**Execution evidence:** No commands were run. No repository files were modified. Tool use was limited to `Read` of the ticket’s `read_paths`. No tests were executed; this report makes no pass/fail/duration claim. All findings below are static source evidence.

**Scope caution:** the inventory JSON is a single long line and was truncated by the read tool. Line-level evidence below therefore comes from the frozen source files, not from the inventory. Perp adapters (`perp_execution.py`) and `trading_contracts.py` internals are outside the read set; they are marked `UNKNOWN`, not “unsupported.”

---

## 1. Execution path chains by venue

Venue identity is not interchangeable: KIS mock ≠ KIS real; Kiwoom data ≠ Kiwoom COM execution; Binance spot ≠ Binance perpetual; paper ledger ≠ live; testnet ≠ live.

| # | Venue / path | Actual chain (exact source) | Caller class | Evidence type |
|---|---|---|---|---|
| 1 | KIS domestic cash/ETF — CLI | `etf_trading.main` (`src/vibetrading/etf_trading.py:954`) → `EtfTradingLoop.run` (`:855`) → `_run` (`:867`) | CLI direct | Direct |
| 2 | KIS ETF — event ingress | NATS subject `live.md.*.*.etf.>` (`etf_trading.py:878`) or injected `event_source` (`:872`) → `EtfTradingLoop.event` (`:612`) | Runtime direct | Direct |
| 3 | KIS ETF — decision | `EtfTradingLoop.event` → `enter` (`:436`) / `exit` (`:476`) / `evaluate_c3` (`:589`) | Runtime direct | Direct |
| 4 | KIS ETF — submit | `EtfTradingLoop.place` (`:387`) → `EtfJournal.stage` (`etf_journal.py:188`) → `_submit_staged` (`:344`) → `KisCashBroker.submit_order` (`kis_execution.py:137`) or `submit_order_with_permit` (`:140`) | Runtime direct | Direct |
| 5 | KIS ETF — guarded submit | `_submit_staged` (`etf_trading.py:344`) → `KisCashBroker.request` (`kis_execution.py:75`) → `permit()` → `EtfJournal.dispatch_handoff` (`etf_journal.py:238`) → `POST /uapi/domestic-stock/v1/trading/order-cash` (`kis_execution.py:104`) | Runtime direct | Direct |
| 6 | KIS ETF — cancel/status | `EtfTradingLoop.reconcile` (`:518`) → `KisCashBroker.cancel_order` (`:209`) / `order_status` (`:167`) | Runtime direct | Direct |
| 7 | KIS ETF — fills | `KisCashBroker.order_status` (`:167`) → `EtfJournal.apply_status` (`:312`); end-of-day `daily_fills` (`kis_execution.py:185`) | Runtime direct | Direct |
| 8 | KIS ETF — account/positions | `KisCashBroker.account_state` (`:115`) ← `EtfTradingLoop.initialize` (`:161`), `account_report` (`:708`) | Runtime direct | Direct |
| 9 | Kiwoom market data | `EtfTradingLoop.evaluate_c3` (`:589`) → `KiwoomAuthClient.post_api`; `EtfHistory` (`:74`) | Runtime direct | Direct, data-only |
| 10 | Toss backfill data | `EtfTradingLoop.event` (`:620`) accepts `provider=='toss'` only with `ingest_mode=='backfill'` | Runtime direct | Direct, data-only |
| 11 | Legacy KIS services | `services/broker_factory.create_broker(Market.KR)` (`services/broker_factory.py:23`) → `BrokerStub` (`:55`) by default | Factory direct | Direct |
| 12 | Legacy Kiwoom services | `create_broker(Market.KR)` when `kr_broker=="kiwoom"` → `KiwoomBrokerAdapter` (`broker_factory.py:46`) | Factory direct | Direct |
| 13 | Legacy crypto services | `create_broker(Market.CRYPTO)` (`broker_factory.py:36`) → `CryptoBinanceAdapter(testnet=settings.binance.testnet)` (`:40`) | Factory direct | Direct |
| 14 | Legacy order manager | `services/execution/order_manager.py:OrderManager` → `BrokerAdapter.submit_order` (`:175`), `cancel_order` (`:149`), callbacks (`:94-95`) | Caller not in read set | Dynamic `UNKNOWN` |
| 15 | Legacy signal ingress | NATS `Subjects.signals(...)` (`order_manager.py:106`) → `_on_signal_message` (`:130`) → `_process_signal` (`:153`) | Runtime direct when wired | Dynamic `UNKNOWN` wiring |
| 16 | Kiwoom COM execution | `KiwoomOpenAPI.send_order` (`kiwoom_broker.py:208`) ← `KiwoomBrokerAdapter.submit_order` (`:378`) / `cancel_order` (`:436`); fills via `_on_receive_chejan_data` (`:154`) | Adapter direct | Direct, Windows/COM |
| 17 | Binance spot stream | `CryptoBinanceAdapter.connect` (`crypto_binance.py:49`) → `_listen_user_stream` (`:88`) → `_handle_execution_report` (`:108`) → `_on_fill_callback` | Adapter direct | Direct |
| 18 | Paper execution | `PaperStrategyRuntime.process_event` (`strategy_runtime.py:251`) → `process_applied` (`:262`) → `_dispatch` (`:361`) → `PaperExecution.on_book`/`on_trade` (`paper_execution.py:294`/`:375`) | Runtime direct | Direct |
| 19 | Paper order lifecycle | `PaperStrategyRuntime._execute` (`:456`) → `PaperExecution.submit` (`:98`) / `cancel` (`:154`) → `PortfolioLedger.admit_order` (`portfolio_ledger.py:381`) / `apply_fill` (`:485`) | Runtime direct | Direct |
| 20 | Offline permit owner | `ExecutionOwner.dispatch` (`execution_owner.py:218`) → `adapter.submit_order_with_permit` (`:252`) | Adapter not in read set | Imported-only / `UNKNOWN` adapter |
| 21 | Cloud archive | `storage_backend.archive_backend` (`storage_backend.py:178`) → `LocalStorageBackend` (`:34`) or `NcloudStorageBackend` (`:95`) | Caller not in read set | Imported-only / `UNKNOWN` caller |

---

## 2. Per-venue/path capability comparison

Disposition meanings: `KEEP` = preserve unchanged; `REUSE` = call from new code; `ADAPT` = local bridge/safety integration only; `COMPLETE_MISSING` = scoped actual gap; `REPLACE_PROVEN_DEFECT` = concrete defect, no deletion; `RETIRE` = plan only.

### 2.1 KIS domestic cash/ETF (`KisCashBroker` + `EtfTradingLoop`)

| Capability | Existing function / line | Disposition | Finding |
|---|---|---|---|
| submit | `KisCashBroker.submit_order` `kis_execution.py:137`; `_submit_order` `:145` | KEEP | Domestic stock/ETF only; whole-share and integral KRW validation. |
| guarded submit | `submit_order_with_permit` `:140`; permit consumed pre-POST `:95-105` | KEEP | One-use authority check + permit at mutation seam. |
| cancel | `cancel_order` `:209` | KEEP | Uses `order-rvsecncl`, all-quantity cancel. |
| status | `order_status` `:167` | KEEP | Broker query by day/order number, paginated. |
| fills | `EtfJournal.apply_status` `etf_journal.py:312`; `daily_fills` `kis_execution.py:185` | KEEP | Cumulative fill delta, notional/fee accounting, dedup by intent. |
| private stream | none in scoped files | `UNKNOWN` / scoped gap | Current evidence is REST polling. TX07B private cursor/sequence is not implemented here; do not invent a stream. |
| positions | `account_state` `kis_execution.py:115`; startup compare `etf_trading.py:190-196` | KEEP | Rejects broker positions differing from strategy-owned positions. |
| reconciliation | `EtfTradingLoop.reconcile` `etf_trading.py:518` | KEEP | Real state apply through `apply_status`; ambiguous order without number remains pending. |
| account/wallet | `account_state` `:115`; `buying_power` `:132` | KEEP | Cash/ETF account summary and orderable cash. |
| funding | N/A for cash ETF | N/A | Not a missing feature; no derivative funding in this product. |
| environment gate | `_acquire_ownership` `etf_trading.py:95`; `tr` `kis_execution.py:68` | KEEP | ETF loop requires exactly `mock`/`mock2`; real TR prefix exists but is not reached by ETF routes. |
| test/live guard | `ETF_EXECUTION_GUARDS` `etf_trading.py:292`; kill switch `:296-310` | KEEP | Guarded path requires paired dispatch permit; kill switch is read-only here. |
| product kind | `_submit_order` `kis_execution.py:146-159` | KEEP | `AssetClass.ETF/EQUITY`; no perp. |

### 2.2 Kiwoom COM execution (legacy `services/execution/kiwoom_broker.py`)

| Capability | Existing function / line | Disposition | Finding |
|---|---|---|---|
| submit | `KiwoomBrokerAdapter.submit_order` `:378`; `KiwoomOpenAPI.send_order` `:208` | REPLACE_PROVEN_DEFECT (later) | Returns `SUBMITTED` on COM `ret==0` but never writes `_order_map` or assigns `external_id`. |
| cancel | `cancel_order` `:436` | REPLACE_PROVEN_DEFECT (later) | Sends `order.external_id or ""`; because submit never sets `external_id`, cancel has no native order id. |
| status | `get_order_status` `:477` | ADAPT / scoped gap | Cache-only; no broker query. Missed chejan leaves stale state. |
| fills | `_handle_chejan_sync` `:317` | REPLACE_PROVEN_DEFECT (later) | Looks up `order_no` in `_order_map` (`:334`), but submit never inserts it; fill gets a new random order id and never updates the submitted order (`:355`). |
| private stream | `_on_receive_chejan_data` `:154` | KEEP as legacy | Chejan transport + normalization exist in one callback; not durable. |
| positions | none | COMPLETE_MISSING (legacy only) | No position query; not a live replacement. |
| reconciliation | none | COMPLETE_MISSING (legacy only) | No REST recovery or unknown-order reconciliation. |
| account/wallet | `get_account_balance` `:496`; `_get_balance_sync` `:509` | REPLACE_PROVEN_DEFECT (later) | Always returns `Decimal("0")` and logs “미구현”. |
| environment gate | `connect` `:278`; `_handle_chejan_sync` `:330` | KEEP as legacy | Windows/COM only; `use_mock` is only a mode label. Cannot replace Linux live path. |
| product kind | `submit_order` `:385-395` | KEEP as legacy | KR equity/ETF-shaped orders only. |

### 2.3 Kiwoom REST market data

| Capability | Existing function / line | Disposition | Finding |
|---|---|---|---|
| market data | `KiwoomAuthClient` used by `EtfTradingLoop.evaluate_c3` `etf_trading.py:589-601`; `EtfHistory` `:74` | KEEP | Data-only; must not be conflated with Kiwoom COM execution. |
| order mutation | none in `KiwoomAuthClient` usage | N/A | No execution path through this client in scope. |
| Toss | `EtfTradingLoop.event` `:620` | KEEP | `TOSS=DATA_ONLY`; backfill only. |

### 2.4 Binance spot (`services/execution/crypto_binance.py`)

| Capability | Existing function / line | Disposition | Finding |
|---|---|---|---|
| submit | `CryptoBinanceAdapter.submit_order` `:134` | KEEP for spot | Uses `python-binance`; `newClientOrderId=str(order.id)`. |
| cancel | `cancel_order` `:185` | KEEP for spot | `origClientOrderId` matches submit. |
| status | `get_order_status` `:208` | KEEP for spot | Queries by client id, updates filled quantity. |
| fills | `_handle_execution_report` `:108` | ADAPT | Normalizes `executionReport`/`TRADE`; no durable dedup/persistence. |
| private stream | `_listen_user_stream` `:88` | KEEP transport | Single user socket; exception only logged. |
| open orders | `get_open_orders` `:228` | ADAPT | On `BinanceAPIException` returns `[]` (`:259-260`) → false absence risk. |
| positions | none | N/A for spot | Not missing spot functionality; this is not a perp adapter. |
| reconciliation | none | COMPLETE_MISSING (scoped) | No restart/unknown-order recovery. |
| account/wallet | `get_account_balance` `:262` | ADAPT | On API error returns `Decimal("0")` (`:273-274`) → false zero balance. |
| funding | none | N/A for spot | Not required for spot; not evidence for perp support. |
| environment gate | `__init__` `:36-47`; mode mapping `:117`, `:244` | KEEP with caution | `testnet=True` default; testnet fills labeled `PAPER`. Flag-only switch; never auto-promote to live. |
| product kind | class `:28`; order mapping `:141` | KEEP for spot | Spot/margin-shaped; no `positionSide`, `reduceOnly`, perp TIF. |
| disconnect race | `disconnect` `:76-86` vs `_listen_user_stream` `:88-99` | Risk | Manual `__aexit__` on a socket also used in `async with`; double-exit/race possible. |

### 2.5 Internal paper path (`PortfolioLedger` + `PaperExecution` + `PaperStrategyRuntime`)

| Capability | Existing function / line | Disposition | Finding |
|---|---|---|---|
| submit | `PaperExecution.submit` `paper_execution.py:98`; `PortfolioLedger.admit_order` `portfolio_ledger.py:381` | KEEP / REUSE | Worst-case reservation, budgets, immutable fingerprints. |
| cancel | `PaperExecution.cancel` `:154`; `PortfolioLedger.request_cancel` `:544` | KEEP / REUSE | Cancel becomes effective at `cancel_ns`; no immediate false cancel. |
| status | `PaperExecution.report` `:409`; `PortfolioLedger.active_orders` `:354` | KEEP / REUSE | Durable state plus in-memory matcher state. |
| fills | `PaperExecution._fill` `:239`; `PortfolioLedger.apply_fill` `:485` | KEEP / REUSE | Fill id `intent+serial+token`; fees, physical/virtual positions, realized PnL. |
| private stream | N/A; `on_book` `:294`, `on_trade` `:375` | KEEP / REUSE | Local market events only; no network. |
| positions | `PortfolioLedger.position` `:312`; `report` `:627` | KEEP / REUSE | Physical `@physical` plus per-strategy virtual positions. |
| reconciliation | `PortfolioLedger.reconcile_positions` `:599` | ADAPT | Compares supplied external facts and records gaps; does **not** apply external state. |
| account/wallet | `seed_balance` `:296`; `_balance` `:286` | KEEP / REUSE | Paper-only immutable seed; no venue equity claim. |
| funding | `PortfolioLedger.apply_funding` `:578` | KEEP / REUSE | Linear perpetual settlement only; duplicate identity rejected. |
| environment gate | `PortfolioLedger.__init__` `:65-68`; `readiness` `:619`; `PaperStrategyRuntime.__init__` `:109` | KEEP | Hard PAPER only; sandbox/live margin `UNVERIFIED`; `external_positions_verified=False`. |
| product kind | `PortfolioLedger.CAPABILITIES` `:61-63` | KEEP | `cash` paper; `linear_perpetual` paper simulation; `inverse_perpetual`/`hedge` unsupported; live margin unverified. |
| matching defect | `PaperExecution._sweep` `:263-274` | REPLACE_PROVEN_DEFECT (later) | Uses `str(price)` to index `levels`, while book keys are built with `_price()` (`:312-323`); scientific/alternative Decimal strings can cause `KeyError`. |
| concurrency | `PortfolioLedger._check` `:136-138` | Risk | Single-thread owner enforced; multi-thread use raises, not silently unsafe. |
| callback isolation | `PaperStrategyRuntime.report` `:574-590` | Known limitation | Reports `cooperative_return_time_only`, `production_gate: BLOCKED`. |

### 2.6 Legacy paper `BrokerStub`

| Capability | Existing function / line | Disposition | Finding |
|---|---|---|---|
| submit/cancel/status | `BrokerStub.submit_order` `broker_stub.py:69`; `cancel_order` `:143`; `get_order_status` `:164` | KEEP as legacy | In-memory; immediate market fill. |
| fills | `_execute_fill` `:92` | KEEP as legacy | Uses `shared.fill_logic`; not durable. |
| positions/reconcile | none | COMPLETE_MISSING (legacy only) | Duplicates paper functionality without durable accounting. |
| account | `get_account_balance` `:187` | KEEP as legacy | In-memory balance only. |
| product/environment | `__init__` `:29-49` | KEEP as legacy | Generic paper; no venue/product gate. |
| ownership | class `:21` | Duplicate owner | Candidate for later integration study; no deletion recommendation. |

### 2.7 Offline permit owner

| Capability | Existing function / line | Disposition | Finding |
|---|---|---|---|
| submit | `ExecutionOwner.dispatch` `execution_owner.py:218` | KEEP | Offline only; single-use permit; ledger outcome. |
| cancel/status/fills | none / ledger only | N/A | Not a venue adapter; no live cancel or fill stream. |
| environment gate | `supports_offline_handoff` `:94-99`; `_validate` `:135-136` | KEEP | Requires `environment=="offline"` and adapter protocol `offline-permit-v1`. |
| product gate | `_validate` `:143-144` | KEEP | Rejects derivatives; cash/spot only. |
| live reuse | no concrete adapter in scope | `UNKNOWN` | Do not wire to KIS/Binance without a new explicit capability + readiness proof. |

### 2.8 Cloud transport (`storage_backend.py`)

| Capability | Existing function / line | Disposition | Finding |
|---|---|---|---|
| local archive | `LocalStorageBackend.put_file` `:46`; `put_bytes` `:57`; `verify` `:65`; `download_file` `:74` | KEEP | Content verification preserved. |
| local durability risk | `put_bytes` `:57-63` | Risk | No `fsync` before `os.replace`; `put_file` does `fsync` (`:50-54`). Crash window differs. |
| Ncloud archive | `NcloudStorageBackend.put_file` `:123`; `put_bytes` `:134`; `verify` `:143`; `download_file` `:165` | KEEP | S3-compatible upload + SHA-256 verification; single-thread transfer config. |
| backend selection | `archive_backend` `:178` | KEEP | Explicit `local` or `ncloud_storage`; no implicit remote uploader. |

---

## 3. TX00–TX12 instructions already duplicated by existing methods

| TX | Existing source that already implements the instruction | Recommendation |
|---|---|---|
| TX00 | `PortfolioLedger.CAPABILITIES` `portfolio_ledger.py:61`; `PortfolioLedger.readiness` `:619`; `KisCashBroker.account_params` `kis_execution.py:50`; `PaperStrategyRuntime.__init__` `strategy_runtime.py:109`; `CryptoBinanceAdapter.__init__` `crypto_binance.py:36` | REUSE as the capability inputs. Add a registry around them; do not create a second capability matrix. |
| TX01 | `OrderIntent` construction `etf_trading.py:423`; `ExecutionOwner.dispatch` `execution_owner.py:218`; `EtfJournal.stage` `etf_journal.py:188`; `PortfolioLedger.admit_order` `portfolio_ledger.py:381`; `PaperStrategyRuntime._validate` `strategy_runtime.py:428` | REUSE `OrderIntent`/`ExecutionAction` and immutable-collision checks. Shrink new contract work to missing fields. |
| TX02A | `EtfTradingLoop._acquire_ownership` `etf_trading.py:95`; `_check_ownership` `:116`; `PortfolioLedger.__init__` lease/binding `portfolio_ledger.py:80-94`; `EtfJournal.initialize` `etf_journal.py:43` | REUSE both lease implementations. ETF `mock`/`mock2` isolation already exists; do not add a third owner lease. |
| TX02B | `PortfolioLedger.apply_fill` `portfolio_ledger.py:485`; `PortfolioLedger.report` `:627`; `EtfJournal.apply_status` `etf_journal.py:312`; `EtfJournal.logical_book` `:98` | REUSE. Choose one owner per path: ETF journal for ETF, PortfolioLedger for new PAPER. Do not duplicate fill→physical/virtual accounting. |
| TX03A | `ProductSpec` use `paper_execution.py:104/240`; `PortfolioLedger.admit_order` `:381`; `_update_position` `:466`; `CAPABILITIES` `:61` | REUSE isolated-linear/cash model. Keep inverse/hedge explicitly unsupported; do not fake cross/hedge formulas. |
| TX03B | `PortfolioLedger.apply_fill` `:485`; `_strategy_credit` `:316`; `_margin` `:330`; `_capital` `:338`; `apply_funding` `:578`; `EtfJournal.apply_status` `:312` | REUSE fee/realized/funding identities. Mark price/index timestamp and FX conversion are genuinely absent; report `UNAVAILABLE` rather than synthesizing. |
| TX04A | `PaperExecution._transition` `paper_execution.py:64`; `PortfolioLedger.transaction` `:164`; `save_paper_state` `:222`; `apply_fill` `:485` | REUSE atomic commit and `_aligned` restart check. Do not add a second matcher transaction layer. |
| TX04B | `PaperExecution.submit` `:98`; `cancel` `:154`; `advance` `:170`; `_arrive` `:221`; `_sweep` `:263`; `_receipt` `:276`; `on_book` `:294`; `on_trade` `:375` | REUSE. Existing code already separates arrival/cancel/source time and post-only crossing. Fix narrow defects, do not rewrite. |
| TX05 | `PaperStrategyRuntime.register` `strategy_runtime.py:170`; `process_event` `:251`; `process_applied` `:262`; `_dispatch` `:361`; `report` `:574` | REUSE. The real plugin contract is `strategies/contract.py`; R05 KR functions are calculators, not pluggable strategy files. Do not create `SharedStrategyRunner` as a second live authority. |
| TX06A | `CryptoBinanceAdapter` is spot-oriented `crypto_binance.py:28`, `:134` | REUSE for spot only. Do not treat it as Binance perp. Perp `perp_execution.py` is outside read scope → `UNKNOWN`. |
| TX06B–E | No Arcus/GRVT/Bybit/Hyperliquid adapter in read set | `UNKNOWN`. Do not mark unsupported merely because one wrapper lacks a method. |
| TX07A | `EtfJournal.apply_status` `etf_journal.py:312`; `PaperExecution._fill` `paper_execution.py:239`; `PortfolioLedger.apply_fill` `:485`; `ExecutionOwner.dispatch` outcome mapping `execution_owner.py:256-273` | REUSE existing fact identities/terminal rules. Add canonical event names only where the existing facts cannot be projected. |
| TX07B | `EtfTradingLoop.reconcile` `etf_trading.py:518`; `KisCashBroker.order_status` `:167`; `daily_fills` `:185`; `PortfolioLedger.reconcile_positions` `:599`; `PaperStrategyRuntime.report` `:574` | REUSE KIS REST reconciliation; fill the private-stream cursor and paper external-apply gaps only where required. |
| TX08A | `PortfolioLedger.admit_order` `:381`; `_gross_notional` `:365`; `_reserved` `:322`; `StrategyRuntime.register` risk binding `:170-204`; `ExecutionOwner` budgets `:24` | REUSE account/strategy budget and gross-exposure logic. No new universal risk engine. |
| TX08B | `PortfolioLedger.record_group_fact` `:249`; `group_facts` `:277` | REUSE observational group ledger. It deliberately does not mutate child orders; do not add atomic cross-account commit fantasy. |
| TX09 | `EtfTradingLoop.account_report` `:708`; `report_daily` `:698`; `EtfJournal.notify` `:290` | REUSE projection data. Telegram/ACL is outside read scope; do not duplicate an account owner there. |
| TX10 | `archive_backend` `storage_backend.py:178`; `NcloudStorageBackend` `:95`; SHA-256 verification `:143` | REUSE transport and checksum. No new cloud uploader by default. |
| TX11 | `PaperStrategyRuntime` + `PaperExecution` + `PortfolioLedger` chains above | REUSE as the E2E substrate; do not build a separate state machine for the benchmark. |
| TX12 | `PaperStrategyRuntime.report` `:574-590`; `PortfolioLedger.readiness` `:619`; `ExecutionOwner.supports_offline_handoff` `:94` | Static evidence only: production gate is explicitly `BLOCKED`, external mutations `0`, offline handoff only. No live/deployed claim. |

---

## 4. Preserved production behavior and environment differences

| Item | Static finding |
|---|---|
| ETF mock/mock2 isolation | `EtfTradingLoop._acquire_ownership` requires exactly two distinct KIS mock physical accounts (`etf_trading.py:95-114`); journal identity is bound (`:175-183`). Preserve. |
| ETF real account | `KisCashBroker.tr` can emit `TTTC` for non-mock modes (`kis_execution.py:68-69`), but the ETF loop rejects any route other than `mock`/`mock2` (`etf_trading.py:99-100`) and records `account_mode:'mock'` (`:426`). There is no ETF real-account execution path in this code. |
| KIS market-data vs execution | `KisAuthClient(..., mode='real')` is created for ETF quote/report reads (`etf_trading.py:71`, `:732-735`) while orders use `KisCashBroker(mode='mock'/'mock2')` (`:66-67`). Read-only real data must not be mistaken for active live trading. |
| Kiwoom | COM adapter is Windows/COM-only (`kiwoom_broker.py:278-279`); REST `KiwoomAuthClient` is data-only. Do not promote test/probe or COM code to a Linux live replacement. |
| Binance testnet | `testnet` defaults `True` (`crypto_binance.py:36-44`); testnet fills are labeled `PAPER` (`:117`, `:244`). A live toggle exists but is explicit. Never auto-promote. |
| Paper | `PaperStrategyRuntime` rejects any mode other than `TradingMode.PAPER` (`strategy_runtime.py:109-110`); `PortfolioLedger.readiness` requires PAPER and no gaps (`portfolio_ledger.py:619-625`). |
| Offline owner | `ExecutionOwner` rejects production activation (`execution_owner.py:200-208`) and requires an offline-permit adapter (`:94-99`). |
| Cloud | `LocalStorageBackend` is documented for tests/local dev (`storage_backend.py:35`); `NcloudStorageBackend` is explicit and checksum-verifies (`:95-163`). Preserve both. |
| Live verification | Static code cannot prove current production behavior. No account/API calls or tests were run. |

---

## 5. Minimal-diff recommendation

1. **Keep `EtfJournal` authoritative for ETF.** Do not replace it with `PortfolioLedger`; its stage→handoff→accepted→apply_status path is already the ETF order/fill authority (`etf_journal.py:188-346`).
2. **Keep `PortfolioLedger` + `PaperExecution` authoritative for new PAPER.** Reuse the existing transaction, reservation, fill, and matcher code instead of a new order engine.
3. **Keep `ExecutionOwner` offline-only** until a venue-specific capability and readiness proof exists. It is a permit seam, not a live adapter.
4. **Use one normalized local event boundary only where needed:** `PaperStrategyRuntime.process_event`/`process_applied` for the PAPER path. Keep the ETF `EtfTradingLoop.event` path separate; merging them now would risk ETF behavior.
5. **Reuse the existing plugin contract in `strategies/contract.py`.** Do not convert R05 calculator functions into “strategy plugins.”
6. **Reuse KIS REST order/status/fill methods.** Do not add another signer/REST/private-API wrapper. If a private KIS stream is required, it is a scoped gap, not a reason to replace the REST path.
7. **Reuse Binance spot adapter only for spot.** Keep perp work in the assigned `perp_execution.py`; do not infer perp support from the spot adapter.
8. **Reuse the existing cloud backends.** No new universal uploader; add only missing durability/verification fixes.
9. **Fix proven defects narrowly, not by rewrite:**
   - Kiwoom `_order_map`/`external_id` association (`kiwoom_broker.py:378-427`, `:317-369`).
   - Kiwoom balance stub (`:509-519`).
   - `OrderManager` ambiguous submit marked `REJECTED` (`order_manager.py:181-185`).
   - Binance swallowed API errors returning empty/zero (`crypto_binance.py:259-260`, `:273-274`).
   - `PaperExecution._sweep` Decimal string key (`paper_execution.py:263-274`).
   - `LocalStorageBackend.put_bytes` missing fsync (`storage_backend.py:57-63`).
10. **Duplicate wrappers/owners for later integration study (no deletion now):**
    - `services/execution/BrokerAdapter` vs `vibetrading.OrderIntent` paths.
    - `BrokerStub` vs `PaperExecution`.
    - `KiwoomBrokerAdapter` vs `KiwoomAuthClient` data path.
    - `EtfJournal` vs `PortfolioLedger` accounting.
    - NATS/Postgres `OrderManager` kill switch vs ETF file/SQL kill switch (`order_manager.py:140-151`, `etf_trading.py:296-310`).
11. **Do not unify** `services/execution/base.py:BrokerAdapter` with `vibetrading` contracts by renaming methods. `BrokerAdapter` takes `Order`; `KisCashBroker` takes `OrderIntent`; `ExecutionOwner` requires an offline permit. This is a real interface break, not cosmetic.
12. **No deletion/RETIRE execution.** Any retirement needs callers, rollback, and golden proof; none is proven from the read set.

---

## 6. Highest-value existing tests and unresolved questions

The frozen excerpt does not expose test function bodies. The following are the test paths named by the authoritative TX contract; exact test-function names are `UNKNOWN` unless the repository is read beyond this ticket. None was executed.

| Test path / target | Static value | Status |
|---|---|---|
| `tests/refactoring/test_portfolio_ledger.py` | TX01/TX03B ledger, reservation, PnL target | Referenced as existing |
| `tests/refactoring/test_tx01.py` | TX01 action/mode/product contract | Referenced as new |
| `tests/refactoring/test_tx07a.py` | TX07A late fill / double fill / terminal fact cases | Referenced as `ADD` |
| `tests/refactoring/test_tx07b.py` | TX07B stream gap / pagination / reconcile | Referenced as `ADD` |
| `tests/refactoring/test_tx08a.py` | TX08A multi-strategy same-account risk | Referenced |
| `tests/refactoring/test_tx08b.py` | TX08B partial-fill/unknown execution group | Referenced |
| `tests/refactoring/test_tx09.py` | TX09 Telegram projection/ACL | Referenced |
| `tests/refactoring/test_tx10.py` | TX10 export/promotion metadata | Referenced |

**Verified source findings**

- KIS ETF execution/reconcile/reporting is a complete direct chain in the scoped files.
- `EtfJournal` already implements durable stage/handoff/ACK/fill/unknown behavior.
- `PortfolioLedger` + `PaperExecution` already implement durable PAPER admission, matcher, fills, fees, funding, and single-thread ownership.
- `PaperStrategyRuntime` already implements the shared PAPER plugin host, but explicitly reports callback isolation and restart-continuity gaps.
- `ExecutionOwner` is offline-only and rejects derivatives.
- `CryptoBinanceAdapter` is spot-oriented; it is not a perpetual adapter.
- Kiwoom COM execution contains concrete order-association and balance defects.
- `storage_backend` preserves explicit local and Ncloud S3-compatible archive transports.

**Unresolved / not proven**

- Exact caller wiring for `services/broker_factory`/`OrderManager` and `PaperStrategyRuntime` is outside the read set → dynamic `UNKNOWN`.
- Exact test-function names and assertions were not read → do not claim test execution or coverage.
- `perp_execution.py`, `trading_contracts.py` internals, and perp venue adapters are outside the read set → `UNKNOWN`, not “unsupported.”
- KIS private streaming cursor/sequence and true account-wide external-fill attribution are not implemented in this path.
- `PortfolioLedger.reconcile_positions` is compare-only; applying external state remains an explicit gap.
- Mark-price/index-timestamp/FX conversion for unrealized PnL is absent; must be reported `UNAVAILABLE`, not synthesized.
- `EtfTradingLoop` logs and `KiwoomBrokerAdapter.connect` logs the account number (`kiwoom_broker.py:299`); no secret values were found in the read output, but account-number logging is a privacy consideration.
- No live, sandbox, testnet, or external-account verification was performed.

**Commands run:** none.
**Tests run:** none.
**Files modified:** none.
