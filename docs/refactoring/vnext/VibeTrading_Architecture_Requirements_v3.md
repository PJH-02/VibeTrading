# VibeTrading v3 — 아키텍처·구현 요구사항 및 불변조건 상세 명세

> **문서 유형:** 규범적 제품/비기능 요구사항 (SHALL/MUST/SHOULD/MAY). 이 문서는 새 계획을 구두로 다시 설명하는 문서가 아니라 Codex가 개발자(GLM·DeepSeek)의 패치를 판정하는 데 사용하는 **검증 가능 계약**이다.
> **기준:** `PJH-02/VibeTrading` 기본 브랜치 SHA `a9a83af43897fdf8badc303aaf3b6ebf2eda87c5` (2026-10-21). 배포된 서버는 이 커밋과 다를 수 있다. 제품 코드는 이 파일을 작성하는 동안 수정하지 않았다.
> **관계:** 실행 순서 `VibeTrading_Refactoring_Final_Plan_v3.md`, 티켓별 정확한 작업·테스트 `VibeTrading_Developer_Ticket_Spec_v3.md`, 기존 C01–C09/P00–P12/VN/TX 계약·영수증. 이 문서가 원본 Ledger·Strategy Economics의 기존 안전 계약을 약화할 권한을 부여하지 않는다.
>
> **2026-10-23 binding scope correction (이 문서의 우선 권위; 문서 기준 SHA `4977ab509490173e312183f32d47f862d94ee0bf`, 위 2026-10-21 관측선은 역사 기록):** [tech_lead_scope_decisions.json](./reuse_audit/tech_lead_scope_decisions.json)·[revised_dag.json](./reuse_audit/revised_dag.json)·[VibeTrading_Minimal_Reuse_Plan_20261023.md](./VibeTrading_Minimal_Reuse_Plan_20261023.md)·[execution_reuse_matrix.md](./reuse_audit/execution_reuse_matrix.md)가 아래의 broad universal-platform 요구를 대체한다. 현재 동작하는 ETF/venue/cloud/strategy 경로를 보존하고, "모든 connector/모든 venue Private Stream/universal real margin/lifecycle/cross-venue atomic group/새 cloud uploader"를 **현재 필수 조건으로 요구하지 않는다.** 적용 대상은 선택된 local data/strategy-file/Paper/same-account/recovery/projection scope이며, 기존 안전 불변조건(cancel ACK≠terminal fill, UNKNOWN reservation 유지, TOSS DATA_ONLY, monotonic fill identity, per-mode one writer)은 그대로 유지한다. 정적 감사는 live 검증이 아니다. 제품 변경은 Tech Lead의 bounded gap ticket 이후에만 가능하다.

## 0. Scope와 비목표

### 0.1 목표

- 기존 `src/vibetrading` 패키지·런타임 경계를 재사용하고, 선택된 local scope만 최소 변경한다.
- KIS 기존 C3/KP/KD와 Mock/Mock2, 기존 증권사·TOSS 데이터 소스를 **손실 없이 유지**한다.
- Crypto는 **기존** Binance spot, Arcus/GRVT/Bybit/Hyperliquid 경로와 perp mapping/signing composition을 보존한다. all-five connector rebuild는 목표가 아니며, selected route만 함수·caller·환경 증거로 다룬다.
- 실시간 Market Data를 읽은 여러 독립 전략이 **기존** Shared MarketState/Paper runtime을 사용하고, **새 multi-strategy 연결에서** 전략별 논리 상태 격리와 같은 계좌 단일 writer를 입증한다.
- Paper Trade는 거래소 API Secret 없이 동작하며 실제 주문 Mutation 0건을 유지한다.
- 시장 데이터 내구성/복구/Parquet/Cloud는 **기존 구조를 재사용**하고, 누락된 local recovery/reader/strategy-file/Paper 연결만 완성한다.
- 운영 서버/실계좌/장중 검증은 별도 External Gate로 남기며, 이 문서는 live 검증 완료를 주장하지 않는다.

### 0.2 비목표 및 금지

- 새로운 전략 알파 연구/최적화, 운영 서버 내부의 대규모 Backtest, 실거래 자동 전략 승격, 자동 계좌 활성화.
- TOSS Trade API를 새로 구현하거나 TOSS Venue 실행을 라우팅하는 것.
- 검증된 기존 경로를 대체하는 새 exchange/client/signer/Private Stream/cloud uploader, all-five connector rebuild, universal real margin/lifecycle engine, cross-venue atomic execution group.
- NATS 외에 새 범용 Broker, Redis/PostgreSQL/QuestDB 서버, 새 총괄 Coordinator 서비스, 불필요한 RPC framework 생성.
- 시장 호가 데이터 전체를 매 주문 시마다 fsync하는 Policy B로 임의 전환.
- 기존 서버의 NATS service 종료, 원본 로컬 로그/계좌 DB 삭제, 운영 계좌 주문·취소·메시지 전송.
- 모델에게 기존 private SSH/API Key, 실제 계좌 번호나 비밀 결제/인증 정보 제공.

## 1. 모듈 경계·의존 방향

**제품 Python 패키지:** `src/vibetrading` (Python import root `vibetrading`). 서비스/프로세스는 실제 역할에 따라 나뉘되 패키지 도메인 폴더가 OS 프로세스 하나를 뜻하지 않는다. Dashboard는 Node/TypeScript 독립 프로젝트로 남긴다.

| 모듈 | 단일 권위 책임 | 외부 접근/금지 |
|---|---|---|
| `core` | ID/Event/Contract/ProductSpec/Clock/Decimal | Data DB/Broker API 접근 금지 |
| `feeds` | 수집자, native sequence, provider reconnect, schema normalize | 주문 제출/Portfolio Update 금지 |
| `market` | Native Delta/Book/Trade 상태, Data Quality, Version, History, Snapshot | Broker 계좌 정보 변경 금지 |
| `storage` | Spool, Reader, Checkpoint, Parquet/Archive, Verified GC proof | 신규 거래 판단 금지 |
| `strategies` | 등록된 전략별 순수 결정, 필요한 Feed/History 선언 | 주문 HTTP 직접 호출·공통 MarketState Mutate 금지 |
| `execution` | Account Owner, Risk Gate, Intent→Venue/Paper, Order State | 전략 수익성 판단 금지 |
| `portfolio` | 계좌·전략별 Position, Cash/Margin, Reservation, PnL | Broker Client 소유권 금지 (계좌 조회는 Adapter 사용) |
| `runtime` | DI/Wiring, Process Role, Reconnect/Start/Stop/Recovery | 전략 계산 및 다중 불변조건 중복 정의 금지 |
| `operations` | Telegram/Gateway, Dashboard/Health, Maintenance | Hot Path의 동기 운영 API 호출 금지 |

**의존성 원칙:** `core`→하위 없음, `feeds/market/storage/strategies/execution/portfolio`는 공통 `core` 계약을 참조. `runtime`이 Composition Root에서 이를 연결. Order Execution→Portfolio의 권위 있는 상태 Commit은 좁은 seam, Storage는 Event/Fact를 별도 소비. Circular Import 없도록 검사. 실제 호출 그래프가 다르면 우선 코드의 확정 사실을 기록한 후 단계별 해체.

**패키지 이전 계약:** `git mv`로 byte/AST 동등을 우선 검증. `vibetrading` importable name을 보존. `services/`, `shared/`, `strategy/`, `strategies/`의 **활성 caller가 0인 것이 증명되기 전**에는 기능을 삭제하지 않는다. R03 단계에 제품 로직 의미 변경을 섞지 않는다. 기존 `main.py`, Docker/Compose path, `python -m vibetrading.*`, P02 rollback 도구와 Release Image CLI를 검증한다.

## 2. Market Data / OrderBook / History 구조

### 2.1 Canonical Event

권고 Envelope (기존 모델 스키마를 깨지 않는 내부 Wrapper):

```text
source_provider, venue, instrument, channel, asset_class
native_symbol, native_sequence/cursor, native_generation, event_id
event_time, receive_wall_time, receive_monotonic_ns, available_at
schema_version, normalized_payload, raw_ref_or_digest
source_quality, quality_reason, ingest_cursor, durable_cursor, applied_cursor
```

- `receive_monotonic_ns`는 같은 OS 프로세스 안에서 지연 계산용이고 별도 프로세스/재시작 시 영구 시각으로 단순 비교하지 않는다.
- 여러 Provider의 `native_sequence`를 글로벌 하나의 Sequence로 오인하지 않는다. Producer별 Incarnation/Generation 구분.
- 기존 Schema v2·extra=forbid·`Decimal`·거래소 원시 정밀도·Raw Provenance·Arrow(38,18)를 유지. 권위 있는 Wire Schema 변경은 Migration+Reader 호환 증명 후 별도 티켓.
- 수집 이벤트의 `event_time`과 미래를 관측할 수 있게 된 시각 `available_at`을 분리. 한국 ETF 1분봉 start/end label 정렬과 과거 보조 선물 분봉은 무조건 `ffill`하지 않는다.

### 2.2 OrderBook Delta

- `(venue,instrument,channel,generation)`별 한 명의 Writer가 Native Book State를 유지한다. Snapshot 적용→연속 Delta; gap/duplicate/overlap의 Vendor-specific 규칙은 별도 adapter.
- 가격·수량은 정수 Tick/Scaled Integer가 해당 계약의 정밀도를 정확히 보존할 수 있을 때만 선택. 환산 과정에서 어림·float rounding 발생하면 현행 Decimal 유지.
- 기본 Lookup은 `price -> qty` Map, best-bid/ask 또는 price index는 Sorted Array/`bisect`/tree 등 후보를 **실제 book-depth/burst로 benchmark**하여 결정. **기존 `_apply_crypto_book()`의 delta마다 모든 가격 정렬과 대량 Top-50 Snapshot 복제를 무조건 유지하지 말 것**. 단, 성능 이득 미입증 상태에서 복잡한 자료구조 도입 금지.
- Book Quality=`COLD|WARMING|LIVE|STALE|GAP|INVALID` 등 제품 계약상의 실제 Enum으로 매핑. `GAP/INVALID`에서 의존 전략의 신규 위험 주문 차단.
- Top-K, BBO, imbalance 등 공용 View는 `book_version`으로 캐시. 모든 전략이 동일 자료를 재계산하거나 전 이력을 복제하지 않음. Snapshot은 immutable/일관된 version.
- 전체 native book과 가공된 Top-K UI View를 구별. Checkpoint가 과거 필요한 Native Levels/Sequence를 잃지 않는다.

### 2.3 History 및 Memory

- 현재 필요한 근접 체결/호가/BBO는 bounded Ring/Deque. 개별 시장별 `max_events`, `max_age`, `max_bytes` 상한을 명시하고 비용 계측.
- 종목/거래소별 `deque(maxlen=512)`에 Top-50 Book 객체를 매 Delta마다 저장하는 것은 메모리 비용을 검증해야 한다. 일반 전략의 요구가 현재 BBO/Book/Features면 **원본 Book 한 벌 + bounded BBO/Trade/Feature 이력** 우선.
- C3/KP/KD에 필요한 15거래일/252거래일 History 등 **현재 전략별 실제 계약**은 메모리 절감을 이유로 줄이지 않는다. 복구 시 Parquet/Loader로 재구성 가능한 장기 이력은 Checkpoint에 그대로 중복 저장하지 않는 방향 검토.
- Feature 계산은 하나의 shared writer가 event 순서로 수행. Reader는 이후의 이벤트를 보지 못하는 causal barrier를 둔다.

## 3. Ingest/Queue/UDS/Durability

- 기존 VN01–05와 C01/C05/C08 계약을 재사용한다. 수신량이 같아도 JSON serialize/deserialize, fsync, UDS ack, Feature, Matcher, Portfolio 비용이 추가되므로 **데이터량 동일 ≠ CPU 비용 동일**.
- Queue는 Count뿐 아니라 Bytes/OldestAge/In-flight/Bounded Publish Tasks/Per-feed Lag 상한. 메모리 Queue Overflow는 silent drop 대신 Data Quality/Gap/Backpressure로 명시. Raw delta를 알 수 없는 순서로 병합/압축하는 최적화는 불허.
- `ACCEPTED_RAM`, `APPENDED`, `DURABLE`, `ENGINE_APPLIED`, `STRATEGY_DELIVERED`, `ARCHIVE_COVERED`는 서로 다른 상태. Ack/Receipt에 관련 근거를 남김. Producer Incarnation 다른 Watermark를 비교하지 않음.
- A1(Durable-first)은 현재 기본 후보. 모든 Market Event가 `Spool append + group fsync` 후 전달된다는 현재 semantics 유지. A2 Hot-first는 **A1 latency/freshness 기준 위반과 저장 대기 기여가 계측된 경우에만** 별도 비교. A2 도입 시 허용 데이터 유실 창, 전략 데이터 근거, ordering & resume 경계 추가 구현 요구.
- Collector는 Engine 단절 중에도 **검증된 용량 내에서** durable spool을 계속 기록할 수 있어야 한다. 유한 자원이 고갈되면 명시 STOP/GAP/Fail-closed; 불가능한 무한 무손실 수집 약속 금지.
- Feed 장애 격리는 Source / Instrument Channel / Strategy Dependency 층. GRVT BTC Gap이 Binance BTC 단독 전략을 중단시키지 않음. 단일 shared state 저장소가 오염되면 global safety gate 차단 가능.
- Commit-before-mutation: market durable write와 별개로 `Intent + DecisionEvidence + Reservation + ClientID + Attempt`는 실제 mutation call 전에 권위 원장에 반영. 동기 시장 전체 fsync per order는 금지. 원장 Commit Fail/UNKNOWN 시 submit 0.

## 4. Strategy Plug-in / Lifecycle / Hard Isolation

### 4.1 개발·승격 워크플로

```text
strategy research (external)
→ implementation of Strategy Contract
→ export historical data, backtest off-server
→ explicit Paper registration + warmup
→ live market PAPER execution + performance review
→ user-approved promotion + reconcile → Live
```

- Testnet/Mock는 주문 API 서명·응답 상태/Latency 검증 도구이며 모든 전략의 필수 Paper 승격 단계가 아니다.
- 개발자 테스트용 Dummy/Oracle Strategy는 정상 제품 Alpha 전략으로 자동 등록되지 않는다.
- 전략 설정은 `strategy_id/config_hash/version/market_dependencies/history_warmup/account_scope/execution_mode/capital/limits/paper_approval`를 선언한다.
- 옛 `on_event()`→`Signal` 계약을 보존. Order/Cancel/Replace Intent는 안전하게 확장하되 신호/Market Event를 상위 Owner 상태로 재해석하여 중복 주문하지 않는다.
- `REGISTERED`, `WARMING`, `PAPER_READY`, `PAPER_RUNNING`, `PAUSED`, `HALTED`, `RECOVERING`, `BLOCKED` 등 실제 코드가 지원하는 명시적 상태를 적용. **Strategy Ready와 Account Ready는 별도.**

### 4.2 동시 실행/고립성

- Shared MarketState apply≤1회/Event. 각 전략은 immutable snapshot 및 필요한 과거 이력만 읽는다. 블로킹 네트워크·DB·Cloud/Telegram I/O 금지.
- Python Callback의 실행시간을 반환 후 측정하는 것은 **Hard Timeout이 아니다**. 무한루프·blocking call이 다른 전략을 멈추지 않는 실제 증거가 없다면 Production-grade 격리 `FAIL`.
- 선택지: 공통 Engine와 별도 Strategy Worker Process/제한된 pool로 isolation **또는** 설계상 신뢰할 수 있는 cooperative plugin 계약을 사용자에게 명시. 다중 전략 운영 목표상 하나의 미검증 Callback이 전체 Market Apply/Order Cancel을 가로막는 구성은 수락 불가.
- 별도 프로세스 선택 시 기존 2vCPU에서 IPC·serialization·event copy 비용이 감당되는지 측정하고 생성 수에 상한. 모든 전략별 OS process 1개라는 강제 일반화 금지.
- 전략별 Pending Actions/Callback Duration/Queue Depth/Order Rate/Max Quoted Orders budget과 공정성(Fairness)을 관리. 타 전략의 기능적 오류는 영향 범위만 차단.

## 5. Execution Mode·계좌 권위·주문 상태

### 5.1 실행 모드 분리

`MARKET_DATA_MODE`와 `EXECUTION_MODE`는 직교한다.

| 실행 모드 | 시장 데이터 | Order Path | 외부 Mutation |
|---|---|---|---|
| DISABLED | live/replay 가능 | 검증 시그널 로그만 | 0 |
| PAPER | live/replay(주문은 live-phase만) | 원장 + local matcher | **0** |
| SANDBOX | 테스트넷 시장 등 | permit된 대상 테스트 환경 | 승인된 Testnet만 |
| LIVE | 실시간 | permit + broker API | 사용자 별도 승인 후에만 |

- PAPER의 런타임 객체 그래프에 Real Venue Mutating Client가 기본 생성되지 않는다. Mock+Credential은 리스크가 있으므로 허가된 대상으로 구분.
- `execution_mode=LIVE` 문자열을 Config에 변경하는 것만으로 주문 전송을 허용하지 않음. Account/Adapter/Trade Scope/Release Manifest/Owner Lease/Manual Approval/Reconciliation 조건 충족 후 permit.
- Runtime/Adapter는 testnet vs live URL이 다르며, 실계좌와 paper 원장 경로가 완전히 다르다.

### 5.2 Account Scope와 Strategy Attribution

**권위 있는 계좌 키:** `(venue, environment, account/subaccount, product/margin domain, broker account product-code-if-applicable)`. 같은 계좌 키에 정확히 하나의 유효 Owner. 거래소의 net/hedge position mode에 따라 Physical Position granularity 다름.

**가상 전략 장부:** `strategy_id`, `instrument`, `position_side`, `quantity`, `avg_entry`, `realized_pnl`, `fee`, `funding`, `reserved`, `external_adjustment_ref`. 체결 시 strategy attribution이 영속 기록되고 arbitrary reassignment 금지. 신규 strategy entry마다 별도 broker 계좌가 필요한 것은 아님.

**귀속 불변식:**

```text
Physical confirmed position
  = Σ confirmed strategy virtual positions
    + explicitly-attributed external/unallocated positions
    + documented reconciliation difference
```

미확인 Fill/Unknown을 근거 없이 특정 Strategy에 할당하지 않음. `net_position=0`이 계좌 위험 0을 의미하지 않음: gross orders, working orders, offsetting pending, cancel-pending, UNKNOWN worst case 반영.

### 5.3 주문 입력과 상태 전이

Canonical Intent 최소: `scope+strategy_id+intent_id+client_order_id+instrument(native/normalized)+position_side+buy/sell+price+qty+TIF+post_only+reduce_only+expiry+decision_evidence+epoch+expected_account_version`.

```text
CREATED → RESERVED → AUTHORIZED → HANDOFF → UNKNOWN/ACK_OPEN
ACK_OPEN → PARTIAL → FILLED
ACK_OPEN/PARTIAL → CANCEL_PENDING → CANCELLED/EXPIRED
UNKNOWN → (positive-order/fill evidence) → ACK_OPEN/PARTIAL/FILLED
UNKNOWN → (exhaustive negative evidence & venue-approved) → SAFE_RESOLVED
```

Canonical Event는 Venue Native API와 분리. `ACK-after-FILL`에도 terminal 상태를 되돌리지 않는다. Cumulative Fill은 단조 증가. 주문 상태 조회 시 일부 History page에서 '없음'은 미제출 증거가 아님. UNKNOWN은 예약을 보존하고 신규 주문 자동 재제출하지 않는다.

`/halt`는 **신규 위험 주문 차단 + Receipt**이며 포지션 자동 청산을 의미하지 않는다. `reduce-only` 허용 여부는 Scope Owner/Account/Halt 정책의 명시된 계약 따름. 현존 ETF의 검증된 청산 예외는 보존하고 모든 Venue로 묵시적 복사 금지.

## 6. Perpetual Product/Risk/Position

- **Venue Product Catalog:** `(Venue, MarketSegment, Linear/Inverse, ContractSize, Tick/Lot, NotionalMin, MarginMode, PositionMode, Collateral, Funding, Settlement, FeeSchedule, API version)`. 기존 Binance Spot 주문 API는 Perp 지원이 아니다. **현재 binding 범위는 선택된 기존 product/mode뿐**이며, inverse/cross/hedge/portfolio margin은 명시 전략 수요 전까지 `UNSUPPORTED`/DEFER다. 이 절의 표는 selected product 검증 기준이지 all-product 완성 요구가 아니다.
- 모든 Quantity/Notional/Price를 `Decimal` 또는 정확 표현으로 다룬다. 수량/가격 값은 공급자 Native Tick·Lot/Contract Multiplier로 rounding rule을 표시한다. 금액 0·음수·NaN/Infinity/True-as-number는 입력에서 거절.
- `PositionNotional`, `InitialMargin`, `MaintenanceMargin`, `Funding`, `Realized/Unrealized`, `AvailableCollateral`, `WorstCaseNewOrderExposure`는 상품/마진 모드별 계산 정책. Linear isolated Sim 한정이면 정확한 모드로 표기한다.
- MarkPrice/Index/Collateral-Price/FX Evidence의 시각이 오래되었거나 누락되면 신규 레버리지 증액을 차단하고 평가 정보 `UNAVAILABLE` 출력.
- Real Margin Balance와 Paper Sim Margin Balance는 다를 수 있다. Testnet 응답으로 실제 거래소의 margin formula 충족을 입증하지 않는다. 외부 계좌 조회 권한 부재는 `EXTERNAL_ONLY`, 구현 가능한 field parser 및 안전 BLOCK 경로는 로컬에서 완성한다.
- Fee/Rebate/Funding이 계좌·통화마다 다르면 정규화 후 원본 거래통화 장부를 먼저 유지하고 보고 시점에 FX 환산한다. 필요한 FX가 없으면 의미 없는 합산 숫자를 생성하지 않는다.

## 7. Paper Execution / Causal Matching

- Paper Matcher는 실제 시장 데이터와 동일한 원본 Event/Instrument Metadata를 소비하지만 **사용하는 계좌와 주문 결과는 가상 원장**이다.
- `decision_ts`, `strategy_receive_ts`, `submit_queue_ts`, `paper_arrival_ts`, `cancel_requested_ts`, `cancel_effective_ts`, `market_event_receive_ts`를 명확히 구분한다.
- Maker가 BBO에 닿았다는 이유로 fill을 생성하지 않음. Queue Position은 실제로 관측 불가하므로 Conservative Queue Consumption 또는 Worst-case/parameterized lower-bound model 사용. 모델의 불확실성·미체결 우선 가정을 Report에 남긴다.
- Taker는 simulated arrival 당시의 유효 Depth와 protected limit, Fee/Slippage/Notional Cap을 기준으로. 잔여 수량에 대해 무관한 과거 Book을 사용하지 않는다.
- 같은 장부 트랜잭션에 Paper Working Order/Reservation/Fill/PnL/Queue state를 원자 기록하거나, Crash 복구 시 이중 반영을 원천 봉쇄. Paper Event ID/Order ID/Fill ID 모두 안정적이고 중복 검출 가능.
- 활성 주문 검사는 기본적으로 `(venue,instrument,side)`의 후보 집합만 스캔. 모든 외부 시장 Delta마다 전체 pending orders O(N_total)를 순회하는 구조는 다중 MM 부하 시험 전에 제거 또는 정량적으로 정당화한다.
- `GTC/IOC/FOK/Post-only`의 실제 지원은 Venue/Simulation Policy별로 분리. 모의 정책이 없는 주문은 거절, 임의 Fake fill 생성 금지.
- Paper 재시작 시 원장을 살려 초기 Balance/Position/Warmup을 복구하며 **과거 원장 Replay로 새로운 외부 주문 0건**. Live API mutation은 Dependency Injection 수준에서 접근 불가하도록 검증.

## 8. Venue Adapter 및 Reconciliation (selected existing route only)

**이 절의 capability 표는 HISTORICAL/REFERENCE이며, 모든 venue에 대한 현재 필수 구현 목록이 아니다.** 현재 binding 요구는 선택된 기존 runtime route의 보존과 입증된 gap에만 적용한다. 새 connector/transport/signer를 만들지 않는다.

지원 대상으로 선언한 상품/환경별 참조 Capability:

| API/기능 | 필수 구현 계약 |
|---|---|
| metadata | Native symbol, contract index, tick/lot/size/min notional, mode |
| auth/transport | signing timestamp/nonce, rate limits, safe retry classification |
| submit | Permit-before-send, dedup intention, native Client ID, protected order |
| cancel | Native ID/Client ID mapping, delayed/unknown cancel evidence |
| order status/open | pagination completeness, pending order native fields |
| fills | stable native fill ID, partial/cumulative/fee/time |
| private feed | reconnect, gap generation, snapshots/REST reconciliation |
| account & position | collateral/cash/margin/position mode, symbol, funding |
| recovery | UNKNOWN and externally opened positions reconciliation, no false-negative absence |

Venue-특수 규칙(예: Binance Futures 구분, Bybit positionIdx, GRVT typed signer, Arcus nonce, Hyperliquid signed action)은 각 Adapter의 Locale에서 구현. 공통 Canonical State Machine에 각 API의 모든 필드를 누적하지 않는다.

**외부 환경 제한:** 공식 문서/Fixture를 근거로 정상 파서·서명·모델·상태 전이를 구현하더라도, credential-less 로컬 Test로 실제 Live 가능을 입증할 수 없다. `CONNECTOR_CODE_COMPLETE`와 `SANDBOX_VERIFIED`/`LIVE_VERIFIED`를 분리. 아직 공식 명세를 검증하지 못한 Adapter의 지원 수준은 `BLOCKED_UNVERIFIED`, 설명뿐인 Stub는 `IMPLEMENTATION_INCOMPLETE`.

## 9. Cross-venue Execution Group (DEFERRED / NON-BINDING)

**이 절은 기존 prototype 설명이며 현재 필수 core가 아니다.** cross-exchange atomic execution group은 명시 전략 수요와 별도 Tech Lead ticket 전까지 구현·수락 요구가 아니다. 기존 `execution_groups.py` observational facts는 보존한다.

- Group Coordinator는 `group_id`, hedge ratio, Target delta, child Owner scopes, event IDs, TTL, residual exposure, decision evidence만 관리하며 계좌 원장을 직접 대체하지 않는다.
- Cross-venue 거래는 **분산 원자 트랜잭션이 아니다**. 한 Venue 주문이 체결되고 다른 쪽이 UNKNOWN이면 최악 잔여 포지션·Margin Buffer를 기준으로 해당 Group에 새 위험을 차단.
- 두 번째 Leg 재전송은 Positive Evidence 또는 Venue의 안전 재시도 계약을 충족할 때만; 새 ID로 반복 제출은 금지.
- 같은 GRVT 계좌를 MM와 Arb가 사용해도 단일 Account Owner를 공유하고, 서로의 기존 주문을 자동 취소/내부 Matching하지 않는다.
- Group Timeout은 의사결정 제어이며 기존 주문의 자연 소멸을 의미하지 않는다. 주문 상태와 계좌 위험을 다시 대조해야 한다.

## 10. Checkpoint / Replay / Storage / GC

- Market Checkpoint는 실제 Native Book, Native Sequence, Quality, Book Version, bounded recent windows, producer cursor vector를 **같은 일관된 경계에서** 캡처한다. 별도 계좌 원장/전략 Claim과의 연속성도 검증.
- `Checkpoint Bound`는 생성 시 Hot Path 정지시간/CPU/메모리/파일 크기를 동시에 제한. 기존 Parquet에서 재구성 가능한 장기 이력은 통째로 중복 보관하지 않음.
- Replay는 `state_only`이며 원래의 신규 주문/Telegram 명령을 다시 발생시키지 않는다. Checkpoint 상태와 retained Spool prefix 및 Delta handoff에 대한 digest/Incarnation/Cursor 증거가 있어야 LIVE/READY 승격.
- Spool Reader는 첫 retained segment가 0부터가 아닐 수 있음을 명시적 Base 계약으로 다룬다. Prefix 삭제 증거 없이 원본 누락을 정상 Base로 인정하지 않는다.
- GC는 `verified_checkpoint ∧ all_reader_progress ∧ downstream_commit ∧ archive_checksum_readback ∧ retention_elapsed ∧ no_lease/no_unique_copy`의 교집합. 비교나 체크섬이 실패하면 회수하지 않는다.
- 2vCPU/3.8GiB에서 Cloud/Parquet/Snapshot CPU 경쟁을 측정. OS root disk나 20GB급 운영 디스크에 대규모 Spool을 잘못 쓰지 않도록 mount/device preflight. 유한 부하 시험에서 storage backlog 증가하면 자동으로 무기한 데이터 수집을 약속하지 않는다.

## 11. Telegram/Dashboard/Operations

- Telegram은 `inbox/update-id dedup`, numeric allowlist, command TTL, signed/validated scope 및 권위 Owner Receipt 기반. `/halt all`은 다수 계좌의 **일부 실패 가능**을 구조적으로 보고하고 무조건 Global Success로 표시하지 않는다.
- `/pause`, `/halt`, `/resume`, `/pnl`, `/positions`, `/orders`, `/status`와 기존 `/trade`의 구문·권한·기존 Mock2 보고 보존. Read-only 명령은 cold read model과 그 값의 freshness/currency/quality를 노출.
- `/resume`은 상위 전역/계좌 Halt를 임의로 재개하지 않는다. `halt`는 자동 cancel-all/flatten을 뜻하지 않는다. 과거 command retry가 현재 계좌를 열지 않음.
- Telegram Gateway/Outbox/HTTP429/Chat API 오류가 Market Event 처리나 Risk Owner의 필수 동기 경로를 막지 않음. 관측 이벤트가 빠질 경우 read model quality/lag를 드러냄.
- 운영 명령은 Order Owner의 권위 상태를 조작하지만 새로운 Broker Mutation 권한을 자동으로 부여하지 않는다. 빈번한 PnL 조회는 주문 경로의 SQLite transaction lock을 점유하지 않도록 snapshot 또는 분리된 read-only connection을 사용.

## 12. Runtime·동시성·자원 상한

**배포 대상 운영 서버 관측 기준:** 2 vCPU, 물리 RAM 약 3.823GiB; 과거 읽기 전용 표본에서 Swap 사용 약 1.28GiB 및 CPU 압박 관측. 이는 현재 시점의 장기 부하를 나타내지 않으며 **이미 서버가 충분한 여유를 가졌다고 가정하지 말라는 경고 근거**다.

| 자원 | 설계 요구 | 수락 근거 |
|---|---|---|
| Market Ingest | bounded count/bytes/lag/oldest age/inflight | Burst, producer-stall, consumer-stall, crash tests |
| Shared Book | 단일 native book / instrument, delta update, cached best/top K | event/sec benchmark 및 RSS peak |
| Strategy Callback | bounded duration/queue/inflight; hard fault-isolation | hung callback and unrelated strategy continuation |
| Order Queue | per-account serialized ownership, cancel/risk priority, fairness | simultaneous strategies, 429/slow venue, TTL |
| Account Ledger | WAL/FULL 규칙 유지, finite transactions and busy timeout | concurrent reserve/fill/cancel/restart |
| Paper Matching | affected market order index, bounded orders/receipts | 1/10/100/1000 working-order scaling |
| Checkpoint | bounded pause/peak-copy/write/retention | p99 during snapshot, corruption/restart |
| Parquet/Cloud | 별도 cold path, bounded batch / Arrow threads | 15-min Parquet stall, Cloud outage |
| Disk | device/mount proof, backup/GC-safe retention, free reserve | ENOSPC/partial archive/rollback coverage |
| CPU/RAM | 모든 실제 역할 합산 예산, high-watermark alert | 2vCPU synthetic simultaneous workload; true server deferred |

**참고 운영 목표(아직 PASS가 아님):** Host peak Python/Docker RSS/PSS 합과 OS Page Cache·Swap를 구별하여 실측; 메모리 서비스 합계를 과대 또는 과소 계상하지 않는다. 평균 CPU 70% 부근의 운영 여유를 초기 희망치로 사용하되 실제 합격 임계값은 입력 event-rate·strategy count·order-rate·p99 결과를 포함한 명시적인 `CapacityProfile`로 Codex가 승인한다. 근거 없이 전체 메모리 3GiB 이하라고 주장하지 않는다.

테스트 매트릭스의 **필수 core**는 `기존 Golden + NATS-free data/recovery + strategy file plugin + existing Paper + same-account safety`다. `3 KIS + GRVT MM + GRVT/Binance arb + other perp`는 **legacy/reference 선택 시나리오**이며 필수 완료 조건이 아니다. all-five private stream을 동시에 구독하는 구성은 이 계획의 요구가 아니며, 선택할 경우에만 데이터/CPU를 별도 계산한다.

## 13. Quality, Testing & Acceptance Oracle

### 13.1 엄격한 Gate

| Gate | 필수 시험 및 차단 조건 |
|---|---|
| PACKAGING | `pip install -e .`(격리 개발 환경), module import, CLI, Compose config, Docker build, P02 rollback entrypoint |
| KR_STRATEGY | 이전 Fixture의 C3/KP/KD decision/qty/price/account_mode 비교 및 Mock/Mock2 golden |
| MARKET | native L2 seq gap/overlap/checksum, vendor reset, stale, shared apply 1회, older available_at 차단 |
| TRANSPORT | engine disconnect 중 spool, queue full/ENOSPC/ACK gap, bounded reconnect, duplicate accept |
| CHECKPOINT | crash every stage, corrupted/foreign checkpoint, Delta handoff, zero replay orders |
| GC | retained-base Reader v1/v2 호환, remote proof loss, unique-copy deletion 0 |
| PAPER (core) | no broker mutate, maker/taker causal receipts, late-cancel/fill, fee/funding, restart same ledger, cancel ACK≠terminal |
| ACCOUNT (core) | duplicate Owner rejection for new multi-strategy connection, strategy virtual vs physical, margin reservations, unknown/no blind retry, one writer |
| PERP VENUE (optional selected route) | 선택된 기존 route의 함수/fixture만; all-five Official Fixture gate는 현재 필수 아님, unsupported fail-closed |
| ARBITRAGE (DEFERRED) | 기존 group prototype 보존; cross-venue core acceptance 아님 |
| OPS | Telegram dedup/allowlist/TTL/halt/resume cold read, HTTP429, stale report |
| LOAD | 2vCPU·~3.8GiB synthetic representative concurrency, CPU/event, p99/p99.9, memory and disk |
| RELEASE | 정확 source SHA, test result, account data migration compatibility, rollforward/backout proof |

**Oracle 독립성:** 테스트 예상 출력은 구현자가 방금 작성한 후보 코드 자체에서 생성한 것만으로는 충분하지 않다. 기존 provider/native reference, 과거 C3/KP/KD fixture, 공식 Broker 응답 구조, 독립 계산식 또는 Property Invariant와 대조해야 한다.

### 13.2 대표 상태기계 Fault 시나리오

- `Market Event accepted but crash before durable spool`; Accepted 상태와 confirmed durable watermark 불일치 → strategy data quality 및 replay 일관 처리.
- `Spool persisted but UDS send lost`; Reconnect with same incarnation/cursor → 중복 Market Apply/신호/주문 0.
- `Engine restarted; checkpoint partially written`; Verified older checkpoint + retained prefix 또는 fail-closed.
- `Paper fill commit succeeds but process dies before ACK`; 원장 채택 fill 1회, restart 시 balance unchanged.
- `Live order HTTP timeout but broker executed`; UNKNOWN 보존, client ID/positive evidence 대조, 새 ID 재전송 0.
- `Cancel reply arrives after partial/full fill`; 누적 Filled 상태/예약이 과거 값으로 회귀하지 않음.
- `Crypto MM and Arb share account`; 한쪽 order replace/partial fill이 타 전략의 귀속·risk budget을 침범하지 않음.
- `Perp mark stale / collateral unverified`; 추가 leverage risk 0.
- `One strategy hangs forever`; Collector/other strategies/Owner risk/cancel able to progress or system explicitly fails closed; 테스트가 hang으로 무한 대기하지 않음.
- `Cloud 24h unavailable`, `Parquet 15min unavailable`; bounded data/spool growth or explicit load shedding and halt without silent drop.

### 13.3 Evidence/상태 표기 규약

`IMPLEMENTED`, `SCOPED_TEST_PASS`, `INTEGRATION_TEST_PASS`, `LOCAL_VERIFIED`, `MODEL_PROVIDER_VERIFIED`, `CONNECTOR_CODE_COMPLETE`, `SANDBOX_VERIFIED`, `LIVE_CAPABILITY_VERIFIED`, `SERVER_DEPLOYED`, `LIVE_ACTIVE`, `EXTERNAL_ONLY_PENDING`, `BLOCKED`를 독립 축으로 기록. "전체 PASS"는 해당 정확 Source Set의 전체 scope/명령/skip/마운트를 명시해야 한다. 기록된 `1532 PASS/2 제외`는 2026-10-21 당시 snapshot이지 새 Candidate의 수락 증거가 아니다.

## 14. OpenCode + FreeLLMAPI 개발 환경 계약

### 14.1 역할 분리 (둘 중 하나만 선택하는 구조가 아니다)

```text
Codex (Tech lead: contracts / task packet / critical diff review)
  ↓ small scoped packet
OpenCode (coding/QA/structure/runtime developer-agent)
  ↓ OpenAI-compatible API
FreeLLMAPI (localhost:3001, routing, key quotas, retry+provider identity)
  ↓ selected + verified provider
GLM | DeepSeek | approved alternatives
  ↓
isolated git worktree → deterministic tests → receipt → Codex merge gate
```

**주요 차이:** OpenCode가 Git 파일을 직접 수정하고 Tool을 실행한다. FreeLLMAPI는 단순 라우팅/키·할당량 관리 계층이므로 FreeLLMAPI 자체에 파일 수정 기능이나 Codex와 동일한 Sandbox를 기대하지 않는다.

### 14.2 실제 구성·Smoke Test

- FreeLLMAPI 공식 프로젝트 [GitHub](https://github.com/tashfeenahmed/freellmapi)는 Docker 또는 Desktop 로컬 설치, `http://localhost:3001/v1`, API Key 입력, `npx freellmapi setup-opencode` 및 `--dry-run`을 문서화한다. **소프트웨어는 개인 실험/학습용이며 무료 quota·model availability·ToS는 제공자마다 달라진다.** 운영 트레이딩 VM의 실행 의존성으로 추가 금지.
- OpenCode 공식 [Providers](https://opencode.ai/docs/providers/) 문서는 custom provider에 `@ai-sdk/openai-compatible`(Chat Completions)와 `options.baseURL` 지원을 안내한다. OpenCode [CLI](https://opencode.ai/docs/cli/)는 `opencode run --model provider/model --agent <agent> --format json`, `--session` 및 `--attach` 등을 제공한다. 설치된 버전의 CLI 도움말 우선.
- 현재 `/v1/models`의 `id`, advertised context, structured output 및 실제 round-trip tool call을 시험한다. `GLM`/`DeepSeek`가 Router Catalog에서 선택되더라도 그 제공자의 Tool call reliability는 별도 시험. Tool-less 모델은 파일 변경자 역할에 배정 금지.
- Dry-run으로 설치가 수정할 OpenCode 전역/프로젝트 설정 차이를 사용자에게 보여준 뒤 적용한다. **실제 Key를 Git에 커밋하거나 명령줄 로그에 출력하지 않는다**. 가능하면 로컬 interactive credential 저장 사용.
- Model provenance: `model_id, provider, route_profile, tool_support_test_sha, request_id, usage, retries, fallback, timestamp`. 서로 다른 Provider가 같은 Model ID를 제공하더라도 품질/동작이 동일하다고 전제하지 않는다.
- FreeLLMAPI Router의 **자동 Fallback은 오류 회복에 유용하지만** 도중에 모델이 바뀌면 안전성 리뷰와 맥락 일관성이 깨질 수 있다. Coding/Reviewer 단계는 pinned model/strict capability route 우선; Fallback 발생하면 Diff/Tests를 다시 수행하고 Receipt에 실제 Served Model 표기.
- 개발 PC가 Windows라면 FreeLLMAPI Docker Desktop 또는 Windows Desktop 설치를 사용할 수 있고 OpenCode의 Windows 별도 안내에 따른다. 개발 VM에 API Router를 상주시키지 않는다. Linux/WSL에서 사용할 경우 Config/credential/Worktree 경로 차이를 테스트.

### 14.3 보안·외부 코드 전송

- **위험:** `Private GitHub repo`를 외부 무료 LLM 제공자에게 보내는 것은 소스 코드/전략/계좌 구조가 제공자에 노출되는 행위다. 프로젝트 소유자 허가, 각 upstream ToS·Retention·Training 정책 검토가 선행해야 한다.
- `git worktree` 하나로 **비밀 파일의 노출이 자동으로 막히지 않는다**. 숨겨진 `.env`/SSH/Local DB는 포함될 수 있으므로 물리적으로 별도의 credential-free copy 또는 Sandbox container, 최소 Filesystem ACL을 적용. Worker가 Shell `cat ~/.ssh` 등을 실행하지 못하도록 sandbox/permissions 적용.
- 외부 모델에 전달되는 `read_paths`와 `edit_paths`를 별도로 제한, SSH/교환소 Credential/Production DB/실제 계좌 데이터는 API Call 또는 tool stdout으로 유출 금지. 비밀 검출 도구는 반드시 로컬 수행.
- FreeLLMAPI Gateway는 loopback-only; 공인 인터페이스 바인딩·공개 포트 노출 금지. Provider Key Encryption과 Proxy 자체의 신뢰성은 별개이며, 임의 다운로드 스크립트는 실행 전 검토.
- `opencode run` worker에게 `git push`, `rm -rf`, `docker compose up`, `ssh`, 실계좌 Mutation 명령 허용 금지. 동작 로그·응답은 기록·검토 가능하지만 비밀은 마스킹.

### 14.4 성능·비용 제어

- **토큰 비용과 중복 문서:** 모델에게 전체 repo·수만 줄 Tests·수십만 bytes JSON을 항상 제공하지 않는다. `AGENTS.md + current ticket + 1~5 relevant modules + contract/test summary`로 시작. Sufficient Context 증거 없으면 Required Path만 추가.
- **Task sizing:** 일반 티켓 1~3개 제품 파일/1~2개 테스트, 고위험 Ledger 단계 별도 review. 콜드 시작에 5k~15k 입력 토큰 목표(상한이 아니라 목표). 과다 읽기는 `why_more_context` 근거 기록.
- **Provider limits:** 429/TPM/시간 초과에 대한 Retry 최대수·backoff 및 expiry. FreeLLMAPI가 광고하는 "수십억 무료 토큰/월"은 사용자별 보장 무료량이 아님; 실제 `/usage`/dashboard 확인.
- **모델별 실패 관리:** 동작 확인 안 된 provider가 도구 호출을 성공한 듯 출력한 경우 `FAILED_EXECUTOR`; API 성공했지만 코드가 계약 위반이면 `FAILED_CODE`; Scope/설계 불명확하면 Codex `CONTRACT_DECISION`.
- **전체 회귀 예산:** 파일 이동/Owner/State/Market Checkpoint/Release Gate에는 전체. 단순 README/단일 모듈 변경 시 Scoped Tests. 실패 원인과 diff만 모델에 전달.

### 14.5 개발 표준 `opencode run` 예시(실제 모델 ID는 조회 후 대체)

```bash
# FreeLLMAPI는 로컬 개발 PC에서만 실행; 설치 후 Keys 화면에서 provider 연결
# 구성 생성기는 먼저 dry-run으로 확인. 실제 키는 채팅/레포에 적지 않음.
npx freellmapi setup-opencode --url http://127.0.0.1:3001 --dry-run

# 현재 설치 버전의 옵션 확인
opencode --help
opencode models

# task packet은 미리 로컬 파일로 작성하며, 비밀 없는 전용 worktree에서 실행
opencode run --model '<configured-provider>/<verified-model-id>' \
  --agent build --format json --file path/to/task_packet.md \
  'Implement only this approved ticket; run its scoped tests and return a receipt.'
```

위 코드는 **사용 예시**이며 API Router/모델 연결을 현재 세션에서 실사용 검증했다는 뜻이 아니다. FreeLLMAPI 설정 생성기는 버전별로 API Key 또는 다른 인수를 요구할 수 있으므로 `npx freellmapi setup-opencode --help`를 확인한다. CLI 출력에 Auth Key가 나오지 않도록 실제 실행 전 검토한다.

## 15. 문서·코드 정리의 삭제 검증

- 문서: `CURRENT`, `HISTORICAL_REFERENCE`, `EVIDENCE/RECEIPT`, `DUPLICATE_PROVEN`, `UNKNOWN`으로 분류. **과거 문서가 복잡하다는 이유만으로 증거를 삭제하지 않는다.** READMEs의 현재 상태와 과거 Reference를 구분.
- 코드: `ACTIVE`, `COMPAT_REQUIRED`, `RESEARCH`, `LEGACY_UNREFERENCED`, `UNKNOWN`. Dynamic Import와 Tests/CLI/Compose/Deploy/Rollback 모든 Caller를 찾을 때까지 제거 금지.
- Verbosity 축소: 유사 DTO/Serializer/Wrapper는 **같은 semantics의 경우만** 통합; Venue-specific constraints/risk data/checks는 아무리 길어도 의미가 다르면 유지. 불필요한 Generic Framework 대신 좁은 합성·함수로 구현.
- 안전한 삭제 검증: `active_imports==0 ∧ dynamic_loader_calls==0 ∧ runtime/deploy/rollback references==0 ∧ golden/tests unaffected`. 코드 경로 이동은 Diff에서 Rename 유사성을 보존하여 전체 재검토 비용을 줄인다.
- 단일 Status 파일은 기존 `stage_status.json`·`tickets_production.json`에서 생성. 새 상태 DB/하네스/복잡한 Event Bus 추가 금지.

## 16. 사용자 결정권 및 남겨야 할 외부 Gate

### 사용자가 명시적으로 정한 불변조건

- TOSS는 데이터 소스로만 사용.
- 기존 Crypto spot/perp 경로와 testnet/prototype evidence를 보존한다. **all-five connector rebuild는 사용자 최신 교정으로 필수 범위에서 제외**하며, 실제 상품/모드 지원은 선택된 route별로만 검증한다.
- 전략은 별도 개발·서버 외부 Backtest 후 Paper 일정기간 운영, 사용자 수동 Live 승격.
- **새 multi-strategy 동일계좌 연결의 safety**는 필수다. 기존 KIS ETF runtime과 GRVT MM/arb prototype은 보존하며 cross-venue atomic execution은 deferred다.
- Codex는 Tech Lead/PM·Critical Reviewer; OpenCode+FreeLLMAPI GLM/DeepSeek는 구현/테스트/런타임 관리/검증 담당.
- 리팩토링 종료 시 실제 서버/실계좌가 있어야만 입증 가능한 Gate를 제외한 **선택된 local core**를 완료해야 한다. 범용 Trading Platform 완성은 종료 조건이 아니다.

### 출시 전 외부에서 추가로 증명해야 하는 것(현재 구현 완료로 오인 금지)

1. 실제 서버의 정확 Source/Image/Mount/Volume/Account Owner와 신규 Candidate의 정합성.
2. 실제 Crypto Perp 계정/상품/마진 모드별 signer, native private fills, funding 및 equity response; 공식 Testnet/실계좌 API 차이.
3. 실제 국장 중 `C3/KP/KD`의 분봉 완성 시각·체결·Broker 응답/장중 연속성.
4. 실제 NATS-free 서버의 대표 30분 이상·24시간 Archive/15분 Parquet 장애·72시간 CPU/RSS/Swap/Volume 운영 자료. 로컬 synthetics은 이 실측을 대신하지 않음.
5. 계좌별 기존 Owner Stop→새 Owner Start Fencing, Live Permission, 적절한 Kill Switch, Zero-unreconciled positions 및 가용 증거금 확인.
6. 사용자 승인된 Cutover/Rollback 뒤 안정화; **VN20 서비스/Backlog 제거**.

### 최종 반증 가능 완료 판정

`Local 완료`는 다음이 모두 True인 상태: `source/packaging PASS`, `Market/Storage/Recovery PASS`, `strategy-file→existing Paper E2E PASS`, `selected same-account safety PASS`, `선택된 기존 route 보존 PASS`, `C3/KP/KD golden PASS`, `Security/Secrets PASS`, `Synthetic Capacity PASS`, `scoped/full regression PASS`, `Deployment package generated`, `remaining items exclusively external-dependent`. all-five/cross-venue/extra margin/new uploader는 이 조건에 포함하지 않는다.

**마지막 주의:** 개발 중 새로운 서명·마진·계좌 API가 공식 문서로 확인되지 않으면 이를 추측해 '완료'하지 않는다. 미확정 지원 범위를 막으면서 **검증 가능한 기본 범위부터 끝내고**, 범위 확대가 필요하면 Codex가 정확한 계약과 테스트를 추가하도록 승인한다.

---

## 출처 및 추적 링크 (문서 내 URL은 해당 코드 검토의 시작점)

- VibeTrading Repo: https://github.com/PJH-02/VibeTrading
- 최신 상태: `docs/refactoring/vnext/15_stage_status_and_next_steps.md`, `stage_status.json`, `tickets_production.json` (GitHub main 2026-10-21 관측)
- 원 계약: `refactoring_plan.md`, `refactoring_ai.md`, `refactoring_context.md`, `docs/refactoring/vnext/03_contract_delta.md`, `04_implementation_plan.md`, `12_production_acceptance_matrix.md`
- OpenCode Providers: https://opencode.ai/docs/providers/
- OpenCode CLI: https://opencode.ai/docs/cli/
- FreeLLMAPI repository and API/client setup: https://github.com/tashfeenahmed/freellmapi 및 `docs/en/clients/01-agent-clients.md`


2026-10-24 구현 추적: TX02B/TX02/TX04A는 기존 PortfolioLedger·PaperExecution·EtfJournal 재사용 계약으로 로컬 수락했다. 내부 예약 전략명 경계와 저장된 주문 사실의 커밋 전/재시작 정합성만 보완했으며 새 원장·matcher·거래소 계층은 추가하지 않았다. 전체2266 PASS/외부2건 제외는 선택된 로컬 증거다. TX04B 연속 입력·전략 파일 E2E·실운영 완료는 별도다. [증거](../runs/20261024-v3/batch-tx02b-tx04a/acceptance.json).

2026-10-24 후속 구현 추적: 기존 NATS-free post-apply 경로와 watchdog을 Paper에 연결하고 stale book을 시계 전진 전에 차단했다. 파일별 명시 configuration은 기존 loader/register를 재사용한다. 새 runtime·plugin manager·account engine은 없으며 기존 C3/KP/KD·ETF·거래소·cloud 경제/운영 경로는 보존했다. 전체2322 PASS/외부2건 제외는 로컬 선택 계약 증거이며 live/process-isolation/server 검증은 별도다. [증거](../runs/20261024-v3/batch-tx04b-vn13/acceptance.json).

2026-10-24 TX05 추적: 별도 파일 전략에서 Signal/Paper 체결/PnL/실제 체크포인트 복구까지 기존 구현을 연결한 로컬 경제 E2E를 수락했다. 제품 변경0, 전체2325 PASS/외부2건 제외. callback timeout은 반환 뒤 검사하는 기존 cooperative 범위이며 hard isolation/live/server 검증은 별도다. [증거](../runs/20261024-v3/tx05/acceptance.json).

2026-10-24 TX07A/TX08A/TX08 추적: 기존 fact 필드와 terminal/중복 규칙은 문서/테스트로 고정했다. 동일 Paper 계좌를 공유하는 모든 등록은 명시한 account notional/capital limit이 같아야 하며 claim/plugin 생성 전에 검사한다. 전략별 예산/funding buffer는 독립이고 fair scheduler/새 policy registry를 만들지 않는다. 실제 두 파일 전략의 spool·부분 체결·checkpoint 복구·취소 소유권·중복 체결을 기존 경로에서 검증했다. 전체 2348 PASS/외부2건 제외는 선택 로컬 계약 증거다. 기존 거래소·C3/KP/KD·Mock/Mock2·TOSS DATA_ONLY·cloud·Golden은 보존했다. [증거](../runs/20261024-v3/batch-tx07a-tx08/acceptance.json).

2026-10-25 VN15/TX09/TX10: Paper 원장은 별도 readonly connection/transaction으로 cold 조회하며 PAPER_SIMULATION과 UNVERIFIED/UNRECONCILED를 명시한다. 가격/FX/원천 fact timestamp 근거가 없으면 UNAVAILABLE이며 실제계좌로 승격하지 않는다. Telegram은 기존 ACL/TTL/epoch/dedup/권위 owner/outbox를 보존하고 cold 조회만 추가했다. export manifest는 명시적 strategy research receipt에서 제한된 파일/hash/시점으로 검증하며 기존 uploader/거래소 경로는 수정하지 않았다. 전체 2438 PASS/외부2건 제외는 로컬 계약 증거다. [수락](../runs/20261025-v3/batch-vn15-tx10/acceptance.json).
