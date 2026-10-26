> FROZEN PRE-CORRECTION AUDIT INPUT — NON-BINDING. Source HEAD4977ab509490173e312183f32d47f862d94ee0bf. The historical instructions below were supplied only to detect duplication; they authorize no implementation. Current scope is docs/refactoring/vnext/VibeTrading_Minimal_Reuse_Plan_20261023.md.

## D. TX 기존/신규 Trading Infrastructure 세부 명세

> TX 상위 집계 단계(예: TX02, TX03, TX04, TX06–08)는 하위 티켓이 구현되었다고 `PASS`가 되지 않는다. **실제 같은 계좌·전략·Replay E2E**에서 수락해야 한다. 아래 테스트 파일은 기존 티켓에서 제안된 경로를 우선 사용하고, 없는 파일은 ADD로 명시한다. 기존 제품 코드에 없는 심볼은 `(ADD)`로 구분한다.

### TX00 — Venue/Product/Account Capability Audit 확정

- **소유:** `docs/refactoring/adapter_capabilities.json`, vNext capability matrix, `trading_contracts.py`, 공급자별 실제 Adapter. `TOSS=DATA_ONLY` 적용.
- **선행:** R00/VN00. **현재:** Perp 상품/계좌 지원 실증 부족.
- **구현:** `(venue, product family, environment, account domain, position mode, margin mode)` 차원의 registry. Binance USDT-M/COIN-M 및 Venue별 Linear/Inverse는 미증명 교차 지원 금지. 각 필드에 `SUPPORTED_CODE`, `SUPPORTED_FIXTURE`, `SANDBOX_REQUIRED`, `LIVE_REQUIRED`, `UNSUPPORTED`, `UNKNOWN` 구분. TOSS mutation capabilities=false. Official API URL·버전·확인일 기록.
- **수락:** 5 Perp Venue + KIS/Kiwoom/TOSS 데이터/거래 역할의 표 생성; Unknown이 True로 기본설정되지 않음; Secret 없이 실행 가능한 Contract Tests 식별.

### TX01 — 단일 Strategy Action/Mode/Product 계약

- **소유:** `vibetrading/trading_contracts.py`, `strategy_contract.py`, `execution_owner.py`의 현재 입력 seam; `tests/refactoring/test_portfolio_ledger.py`와 새 `test_tx01.py`.
- **선행:** TX00. **현재:** Cash/Paper 초안.
- **구현:** immutable `OrderIntent`, `CancelIntent`, optional scoped `ReplaceIntent`, `ExecutionGroupIntent`의 식별자/Version/Schema. `execution_mode ∈ {DISABLED,PAPER,SANDBOX,LIVE}`와 `market_data_mode ∈ {REPLAY,SHADOW,LIVE_MARKET_DATA}` 직교. 최소 `(strategy_id,account_scope,venue,instrument,side,position_side,qty,limit_or_protection,TIF,reduce_only,post_only,expiry,evidence,cfg_hash,epoch)`. 계약 수량·Decimal 원값 보존. 동일 ID 다른 payload 불변 충돌로 거절.
- **수락:** Paper에서 모든 외부 mutate entrypoint unreachable; incompatible Spot vs Perp reject, invalid Tick/Lot reject, mode/promotion isolation; 기존 Signal 호환 Fixture 유지. 타입 스키마 증분은 원본 event v2 wire 변경과 구분.

### TX02A — Account Scope/Owner Lease/Legacy ETF Fencing

- **소유:** `portfolio_ledger.py`, `process_lease.py`, `execution_owner.py`, `etf_journal.py`.
- **선행:** TX01, VN10. **현재:** Paper scope owner lease만 구현, 기존 ETF/Generic owner의 실제 격리 통합 부족.
- **구현:** canonical scope key에 Environment/Account/Subaccount/Venue/Product Domain 포함. 하나의 계좌 위험/주문 원장 authority, 다른 DB 파일 경로·중복 process start·stale epoch를 거절. ETF `Mock`와 `Mock2` 구분 보존. Non-cooperating 다른 Host는 로컬 file lease만으로 안전 보장하지 못하므로 전환 별도 External Gate.
- **수락:** 같은 계좌에 2개 Owner가 별도 경로로 동시에 허가 0, 특정 Owner 장애 후 Grace/unknown fence와 재시작, Ledger mismatch 거절; 계좌 다른 경우 불필요한 전역 Lock 없음.

### TX02B — 전략별 Virtual Book과 Physical Account 귀속

- **소유:** `portfolio_ledger.py::{position,admit_order,active_orders}`, `trading_contracts.py`.
- **선행:** TX02A. **현재:** fixture 단계.
- **구현:** `Fill→(physical aggregate update,strategy virtual allocation,fees,bal, reservations)` 하나의 권위 Commit. 주문 전략 귀속은 Fill 생성 시 고정. 외부 수동 거래·기존 포지션은 `UNALLOCATED/EXTERNAL` 등 별도 영역으로 유지. 계좌 Physical Net=전략 Virtual 합 + 외부/미해결/조정 오프셋을 감사 가능한 equation으로 표시.
- **수락:** 두 전략 반대 포지션 +0.4/−0.3→physical +0.1(모드/계약일치 전제), 각 PnL 별도, 미체결 주문의 Worst-case 노출 미상쇄, external fill 임의 strategy 귀속 0.

### TX02 — Owner + Virtual/Physical Portfolio Aggregate Gate

- **선행:** TX02A/B, 관련 VN10–12. **구현 중복 금지:** 소유권과 장부 상태를 Portfolio/ETF path에 둘로 다시 만들지 않는다.
- **수락:** 동일 계좌 두 전략 동시 reserve/partial-fill/cancel/unknown/owner-restart 시 모든 현금·마진·pos 재계산 일치; `sum(released reservations)≤sum(original reservations)`.

### TX03A — Perp ProductSpec 및 Margin Model Capability

- **소유:** `trading_contracts.py`, `portfolio_ledger.py`.
- **선행:** TX01. **현재:** CASH 및 isolated linear paper 모델만 확인.
- **구현:** Linear/Inverse별 노출량/계약 승수/Collateral/Settlement/Funding와 필요한 마진 모델 **분리**. 모드를 지원하지 않는 상품은 `UNSUPPORTED`, 계산 불충분하면 신규 주문을 차단. Cross·Hedge·Portfolio Margin의 정확한 실 API 검증이 없으면 간단한 비율 공식으로 지원 표기 금지.
- **수락:** 0/음수/NaN/nonrepresentable qty, collateral mismatch, base/quote currency mismatch, inverse PnL 계산 검증. 기존 isolated-linear fixture 그대로 PASS. **새 범위에 대해 지원하지 않는 것을 정확히 차단하는 것도 안전성 수락 조건**이지만 최종 5 Venue 목표에서 미지원 범위는 명시적 남은 구현으로 표시.

### TX03B — Fee·Funding·Equity·Margin Accounting

- **소유:** `portfolio_ledger.py::{_balance,_position,_strategy_credit,_margin}`, `trading_contracts.py`, test_portfolio_ledger.
- **선행:** TX02B, TX03A. **현재:** Paper 수수료/리베이트/Funding 초안.
- **구현:** confirmed fill·fee asset·maker rebate·funding settlement의 유일 identity별 1회 적용. Mark Price/Index Timestamp와 Currency Conversion 근거 없는 Unrealized PnL은 `UNAVAILABLE`. Order reservation과 실제 Position Margin을 분리. Equity/Available/Buffer Worst-case를 같은 원장에서 계산.
- **수락:** non-base fee currency, funding 중복/역순, mark stale, external deposit/withdraw, cancel/fill 경쟁·rollback 후 sum invariant. 특정 사업자 cross margin formula 미확인 상태에서 fake value 0 또는 `FULL_READY` 금지.

### TX03 — Perp Portfolio Aggregate Gate

- **선행:** TX03A/B. **수락:** explicit 지원한 Venue×Product×Mode에서 계좌/전략별 margin/position/equity/fees/funding 체결 순서 불변, profile 누락시 위험 주문 0. Unsupported/Provider-dependent modes는 capability blocked로 표시.

### TX04A — Durable Local Paper Account/Matching 원자 커밋

- **소유:** `portfolio_ledger.py`, `paper_execution.py::{_transition,submit,cancel,_fill}`.
- **선행:** TX02B, TX03B. **현재:** matcher durable state, fill dedup 초안.
- **구현:** Matching State Update는 Portfolio Reservation/Fill/Position과 같은 transaction에 적용되거나 재시작 복구 시 중복이 정확히 차단되어야 한다. `intent_id+fill_serial+source_event` 기반 식별과 Crash-after-write/Crash-before-visible 경우 테스트.
- **수락:** Submit/Cancel/Fill 중 임의 exception·disk full·process kill, 재시작 후 잔고/pos/open orders 일치, fill 1회, LIVE mutation 0.

### TX04B — 인과적인 Paper Matching 및 연속 LIVE 입력

- **소유:** `paper_execution.py::{submit,cancel,advance,_arrive,_sweep,_receipt,on_book,on_trade}`, `market` snapshot seam.
- **선행:** TX04A. **현재:** Book/Trade 기반 인과적 Fixture, 전체 input continuity 미완료.
- **구현:** `received_at`, `decision_at`, `submitted_at`, `arrives_at`, `cancel_effective_at`, `market_event_ts`를 별도 사용. OrderBook Gap/Stale/Epoch/Feed Reset에서 새 risk admission 차단. Maker 체결에 Queue Position 불확실성 반영, touch→무조건 체결 금지. Taker는 arrival 시점 유효 Depth/Protection 기반, Qty > visible liquidity 시 불리한 잔여 처리. 활성 주문을 `venue+instrument+side+price` 등으로 bounded index하여 비관련 Venue 전체 순회 방지(먼저 benchmark).
- **수락:** cross-spread post-only reject; cancel-before-fill / fill-during-cancel; source event gap과 중복/순서역전; replay result deterministic; venue fee/funding accounting; 2개의 분리 Paper 전략이 실제 외부 API를 호출 0건.

### TX04 — Paper Engine E2E Aggregate Gate

- **선행:** TX04A/B. **수락:** Event→virtual exchange admission/match→single durable Fill→strategy+account PnL→restart continuity; intentional conservative fill uncertainty 표시. 전략별 Dummy 및 제약 기반 Fixture 모두 PASS.

### TX05 — Shared Paper Strategy Runtime (VN13과 공동 수락)

- **소유:** `strategy_runtime.py::{PaperStrategyRuntime.register,process_event,process_applied,_dispatch}`, `strategy_contract.py`, Local Runtime wiring.
- **선행:** TX04B, TX07A, VN13. **현재:** cooperative callback, isolated paper fixture. 소스 문서상 Callback이 영구 블로킹하면 강제 중단 불가능.
- **구현:** Strategy Register/Unregister/Dependency/Warmup/Exception/TTL/Bounded Queue. Instrument dependency별 invalid; shared State 한 번 apply; 동일 Event에 중복 signal/intent 없음. **강제 Callback 차단의 운영 계약 결정 필수:** (1) Process isolation(우선 Candidate) vs (2) 사용자 승인된 bounded cooperative strategies; 측정해 위험/CPU 허용되지 않으면 `PAPER_READY` 차단. GIL/thread timer만으로 hard kill 보장 주장 금지.
- **수락:** strategy A 무한 loop→다른 전략·수집기가 계속 진전하거나 해당 runtime에서 안전하게 fail-closed. slow callback/raised exception/out-of-date snapshot; 신규 dummy register→Paper action→PnL without broker edits. `SharedStrategyRunner` 별도 NATS Runner와 중복 Live 권위 생성 금지.

### TX06A — Binance Perpetual Venue Adapter 완성

- **소유:** `perp_execution.py::BinancePerpAdapter`, Native metadata/client order/status/cancel/account/position/fills handlers 및 관련 tests.
- **선행:** TX01, TX03B, TX07A. **현재:** request/sign/query mapping 중심, real HTTP transport/private lifecycle 미완료.
- **구현:** 기존 `BinanceBrokerAdapter` Spot와 분리, 명시한 USDT-M 또는 COIN-M subset에 한정; OrderId/ClientId/Side/PositionSide/reduceOnly/TimeInForce 변환, exchange-specific timestamp/recvWindow/signature, open order/status/fill/position/funding and pagination, user-data private stream gap→REST reconciliation. symbol 필수 조회 인자 검증.
- **시험:** signed canonical REST fixtures, 400/429/500/timeout, server date skew, immediate fill-before-ACK, duplicate WebSocket trade, restarted cursor, unsupported hedge/cross. 실 API mutation 없음.

### TX06B — Arcus Perpetual Venue Adapter 완성

- **소유:** `perp_execution.py::ArcusPerpAdapter` 및 기존 Arcus signer/nonce/chain-specific helper.
- **구현:** 실제 지원되는 Arcus 상품·계정·Order Type/Cancel/Fill/Position/Collateral API를 공식 문서·Fixture에 매핑. Nonce/Signer lifecycle, expired signature, client ID, pagination, private stream/reconnect, unknown order risk reservation. SDK·서명 코드가 필요한 경우 기존 검증 함수 재사용, 막연한 Binance API 필드 차용 금지.
- **시험:** signature prepared bytes→reference fixture, nonce collisions, chain/market id mismatch, cancel timeout, missing private event and REST recovery. 적절한 Testnet이 없으면 `EXTERNAL_VALIDATION_PENDING`, 코드 구현은 계속.

### TX06C — GRVT Perpetual Venue Adapter 완성

- **소유:** `perp_execution.py::GrvtPerpAdapter`, `GrvtSigningComposition`, 과거 GRVT 서명/수집 모듈.
- **구현:** signed payload/typed data/nonce·expiry, client order id→broker id, cancel/status/open orders, fills/funding/margin/position API, private event gap and reconciliation, post-only/reduce-only/tif 정확히 매핑. Cross-exchange의 GRVT account scope 권위 유지.
- **시험:** signer expected bytes, stale nonce, canceled after partial fill, unknown with no broker ID, >1 strategy same account ordering; 무허가 mutation 0.

### TX06D — Bybit Perpetual Venue Adapter 완성

- **소유:** `perp_execution.py::BybitPerpAdapter`, `liquidity_mm`의 기존 Testnet Execution Probe(검증된 부분만 재사용).
- **구현:** category linear/inverse scope, positionIdx/one-way/hedge, Create/Amend/Cancel/GetOrders/Execution/Position/Wallet, Private order/execution stream, recv-window/timestamp/sign, rate-limit budget. 기존 Testnet Probe의 주문/취소 로직을 복사하지 않고 호출 seam 재사용 가능성을 확인.
- **시험:** duplicate execution ID, filled-before-ACK, cancel race, positionIdx mistake, maker post-only crossing, unknown timeout; Testnet Probe 코드를 실행 경로의 유일 권위로 만들지는 않음.

### TX06E — Hyperliquid Perpetual Venue Adapter 완성

- **소유:** `perp_execution.py::HyperliquidPerpAdapter`, 기존 독립 signer nonce journal 및 네트워크 adapter.
- **구현:** Hyperliquid API native action payload, subaccount/vault/nonce isolation, asset index/meta, perp leverage/position/open orders/fills/funding/fills-paging, cancel and failure statuses, private subscriptions, explicit wallet vs API wallet 권한. 기존 generic Hyperliquid `order unsupported` stub가 있으면 명시된 계약 구현 또는 제거 검토.
- **시험:** native signer nonce persisted across restart, duplicate nonce reject, wrong chain/domain reject, execution group partial fill, missing/private gap→safe reconcile. 실 API mutation은 별도 승인.

### TX06 — 5개 Venue의 Capability Matrix 집계 Gate

- **선행:** TX06A–E. **수락:** 다섯 Venue 각각 최소 하나 이상의 **명시적 Perp Product/Environment supported scope**에 대해 metadata, submit, cancel, status, open orders, fills, positions, funding, wallet/margin and UNKNOWN local transport fixture PASS. 다른 모드 미지원은 명시; 모델이 임의로 테스트용 성공 응답을 합성하여 공식 응답이라 주장하면 FAIL. 실계좌 준비 상태 `LIVE_VERIFIED`는 현재 자동 불가.

### TX07A — Canonical Order/Fill/Cancel/Funding Fact Contract

- **소유:** `trading_contracts.py`, `execution_owner.py`, `execution_ledger.py` 및 `tests/refactoring/test_tx07a.py` (ADD).
- **선행:** TX01. **구현:** `OrderSubmitted/Accepted/Rejected/Unknown/PartiallyFilled/Filled/CancelPending/Cancelled/Expired/Funding/AccountChange`의 identity·state transitions·monotonic cumulative qty. `Broker ACK`와 `confirmed fill`이 역순 도착 가능. Out-of-order event가 Terminal을 역전시키지 않음. 원장 트랜잭션·reservation/physical/virtual accounting의 단일 권위.
- **수락:** 가장자리 case fixture table: `ACK→partial→cancel→latefill`, `fill→ACK`, double fill, reject after known fill, timeout UNKNOWN→positive match, unknown→incomplete pagination. 원장 checksum 동일.

### TX07B — Venue별 Private/REST/Timeout Reconciliation

- **소유:** `perp_execution.py::{_Adapter.reconcile,venue-specific reconcilers}`, `tests/refactoring/test_tx07b.py` (ADD), `execution_owner` seam.
- **선행:** TX06, TX07A. **구현:** `Private Streaming cursor+seq/generation`, REST pagination completeness proof, order status/cancel/fill/funding time-window. 조회가 일부만 성공하면 `absence != never submitted`. Credential-less Test Harness로 데이터 결과 정규화. Reconcile 전 ambiguous reservation 계속 유지.
- **수락:** unmatched native ID, pages incomplete, duplicate trade events, stream gap, reconciliation timeout, disconnect reconnection, API 429/expired session에서 신규 위험 handoff 차단.

### TX07 — Lifecycle Aggregate Gate

- **선행:** TX07A/B. **수락:** 모든 지원 Venue의 계약시험에서 주문/취소/UNKNOWN/fee/funding 중복반영 0, known positions/books 재구성, blocked 상황에서 unsupervised retry/false absence 0.

### TX08A — 멀티전략 동일 계좌 위험/귀속

- **소유:** `portfolio_ledger.py`, `strategy_runtime.py`, `tests/refactoring/test_tx08a.py`.
- **선행:** TX02/TX03/TX07A/TX05. **구현:** account-wide single serialized reservation, strategy-specific budget, intent/working/cancel-pending/UNKNOWN worst-case exposure. 두 전략의 반대 Position이 계좌상 net zero인 경우에도 미체결 주문은 별도 확률적 예상 대신 안전 Worst-case로 계산. 전략 과도한 quote replacement→취소/원장 처리 starvation 방지.
- **수락:** 2+ strategies same GRVT account, 3 concurrent KIS C3/KD/KP intent (Fixture), reservation race and crash, physical vs virtual reconciled equation. Strategy fairness 측정.

### TX08B — Cross-exchange Partial Fill/UNKNOWN Execution Group

- **소유:** `execution_groups.py`, Owner read-only group facts, `tests/refactoring/test_tx08b.py`.
- **선행:** TX08A/TX07A. **현재:** Paper 관측 Group Coordinator 초안.
- **구현:** immutable `group_id`, child intents, Venue Account Scopes, filled quantities, target hedge ratio, residual delta, group epoch/TTL. 실제 계좌 간 Atomic Commit 환상 금지. 하나만 체결된 경우 exposure 계산과 별도 승인된 reduce-only risk response. Group state는 account Owner의 권위 원장을 대체하지 않음.
- **수락:** GRVT confirmed long+Binance unknown short, partial fill, delayed hedge, duplicate reconciliation, opposite orders from other strategy; 무조건 반복/유휴 계좌 침범 0.

### TX08 — Shared Account/Cross Venue Aggregate Gate

- **선행:** TX08A/B. **수락:** 전략 귀속과 account physical state 일치, margin over-reservation 0, stale order>TTL risky admissions 0, strategy pause가 타 전략 안전 동작을 불필요하게 차단하지 않음.

### TX09 — Telegram/PnL/Portfolio Control Projection 통합

- **소유:** `telegram.py`, `telegram_trade.py`, `dashboard/bridge.py`, `local_observation.py`, `tests/refactoring/test_tx09.py`.
- **선행:** TX08, VN14B, VN15. **구현:** `/status`, `/feeds`, `/strategies`, `/orders`, `/positions`, `/pnl <strategy|all>`, `/pause`, `/halt`, `/resume` 수립. 기존 `/trade` 보존. Owner effect/receipt 같은 authoritative journal에 기록하고 Telegram은 ACL/TTL/중복 검증된 전달자. 상위 halt를 전략 resume으로 해제 불가. stale Mark/FX/Paper/Actual 명확.
- **수락:** allowed numeric identity만 명령, Telegram 중복/429/네트워크 끊김에서 account mutation 1회 이하, cold report 조회가 Hot Path 지연 증가시키지 않음, unknown position 읽기 Fail-visible.

### TX10 — Data Export 및 별도 Backtest→Paper Promotion Metadata

- **소유:** `strategy_registry.py` 또는 기존 Registry seam, `ParquetHistoryReader`, 검증 가능한 Export CLI, `tests/refactoring/test_tx10.py`.
- **선행:** TX01. **구현:** 운영 서버에서 Backtest 실행 금지. 서버가 내보내는 것은 immutable Parquet/DataManifest/Source ID/Bar Timestamp-Convention/Available-at/Native Sequence/Gap Flags/Contract Metadata/Schema Hash. 사용자 연구 결과의 `strategy_version, config_hash, data_manifest, approval record`와 Paper 계정 등록 권한의 연결. Live Promotion은 사용자 승인과 Account Reconciliation 뒤에만 허용.
- **수락:** Timestamp end/start 미혼동, 1분봉 available_at 미래정보 접근 차단, 결측값 조용한 ffill 없음, Export 재수행 시 checksum 동일. `PAPER→LIVE` 단순 config toggle만으로 Real API 전송 0.

### TX11 — 최종 다중 전략 로컬 End-to-End 및 Capacity

- **소유:** test-only Dummy Strategies/Fixture, `local_runtime.py` seam, `scripts/refactoring/benchmark_vnext.py` 등 기존 Driver.
- **선행:** TX05, TX06, TX07, TX08, TX09, TX10, VN18. **구현:** KIS 3전략 + GRVT MM dummy + GRVT–Binance arb dummy + 독립 Crypto venue strategy의 복합 synthetic/local-live-input E2E. `MarketEvent→one state apply→strategy intents→account risk→paper order→partial fill→virtual/physical ledger→report→crash/restart`.
- **수락:** 2vCPU/3.8GiB RAM와 비슷한 격리 환경에서 peak CPU/RSS/PSS/Swap, `receive→decision→admission` p95/p99/p99.9, queue age/lag/error, unknown/late-fill, batch backup/Parquet slowdown, storage/Telegram outage, model-free deterministic execution. 단일 Dummy 테스트만으로 ALL READY 불가.

### TX12 — 최종 로컬 Production Infrastructure Acceptance

- **소유:** Release candidate+Runner Gate, `docs/refactoring/vnext` current status, 서명되지 않은/비밀 없는 local runbook 및 Source Manifest.
- **선행:** TX11, VN19. **구현:** 현재 HEAD 기준 각 지원 Venue·상품·Paper/Live capability, Market, Data Export, Multi Strategy, Owner/Portfolio, Restore, GC, Telegram, Build, Full Linux Test, Fault Injection을 매트릭스에 연결. 진짜 서버·실계좌가 있어야만 수행 가능한 항목만 별도 External Gate 문서에 남김.
- **수락:** Candidate의 Local 기능 미구현 0(지원 선언 Scope 기준), Linux Full Test PASS, Callback isolation·Multi-account concurrency PASS, Paper 외부 mutation 0, Source/Build hash 고정, `server_deployed=false`, `live_active=false`.

## E. 파일/함수 소유권 및 합치면 안 되는 경계

| 현재 파일 (R03 후 `src/` 위치로 변환) | 최종 책임 | 주요 주의점 |
|---|---|---|
| `local_pipeline.py`, `local_transport.py`, `local_engine.py` | Market ingress/durability/Engine 순서 | Transport ACK는 전략 실행 확인과 다름 |
| `hot_path.py`, `market_checkpoint.py` | 현재 Native Book/History/Checkpoint | UI Snapshot과 복구 Checkpoint 다름; 매 이벤트 full sorted 금지 후보는 benchmark 후 |
| `market_spool.py`, `local_storage.py`, `archive.py` | Sequential Log→Parquet/Cloud & proof | GC delete는 운영 권한 아님 |
| `etf_trading.py`, `etf_journal.py` | 기존 ETF 전략 실행 및 authoritative journal | 경제적 판단·주문 계좌 보존; 순수 전략 계산만 단계적 분리 |
| `strategy_contract.py`, `strategy_runtime.py`, `strategy_runner.py` | Plugin contract & single Paper invocation owner | 중복 Runtime 중 어느 것이 실질 권위인지 결정, 중복 시장 apply/신호 생성 금지 |
| `execution_owner.py`, `execution_ledger.py`, `process_lease.py` | 전략 아닌 계좌별 주문 owner | offline-only 제한을 무단 제거해 실계좌 활성화 금지 |
| `trading_contracts.py`, `perp_execution.py` | Product/Execution Canonical Contract + Venue Adapters | API signature와 Canonical Order status 분리 |
| `portfolio_ledger.py`, `paper_execution.py` | 계좌별 Paper ledger + matcher | Paper network API zero, transaction+fill consistency |
| `execution_groups.py` | cross-venue observation/coordinator | 계좌 Owner보다 상위 주문 권한 없음 |
| `telegram.py`, `telegram_state.py`, `telegram_trade.py` | ACL/Command/Projection | cold read path only; halt != auto flatten |
| `services/`, `shared/`, `strategy/`, `strategies/` | 과거 세대 및 별도 전략 코드 | 실제 호출자/Research/Backtest 확인 후 `src/vibetrading` 통합 |
| `scripts/refactoring/deploy_p02_schema.py` | 현재 사용 중 P02 release rollback | **삭제 금지**, 정확 Release 호환 검사 |

## F. 최종 티켓 운영 예시와 종결 규칙

**GLM/DeepSeek에 보낼 좋은 티켓:** `R03`, `VN08`, `TX07A`처럼 목적·변경 파일·독립 Fixture와 수락 기준이 하나의 결과로 평가되는 단위. `전부 완성하라`는 개별 Worker에게 보내지 않는다.

```text
[ACCEPTANCE RECEIPT]
task_id: TX07A
base_sha: <before>
result_sha: <after>
worker_model: <actual served model>
status: PASS | FAIL | BLOCKED
changed_symbols: [exact list]
tests: [command, pass, fail, deselect, duration]
independent_review: [model/reviewer, key findings]
safety_invariants: [ack/fill/unknown/failed_commit]
external_only: [none or exact API check]
next_ready: [ids]
```

**종결 조건:** `R00–R06/O00–O01` + 기존 VN/TX에서 로컬 구현 가능한 범위가 새 Candidate SHA에서 모두 증명되고, 최종 TOSS DATA_ONLY / 5 Perp Venue capability / 3 KIS 전략 / NATS-free Paper / 2vCPU 리소스 범위가 실제 Fixture 및 패키지 검증으로 입증되어야 한다. Legacy 원본 서비스 삭제(VN20), 실계좌/서버 운영 Gate는 **별도 승인된 사후 작업**으로 남긴다.
