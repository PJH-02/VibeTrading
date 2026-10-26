# VibeTrading — 최종 아키텍처 및 불변조건·Capability 요구사항

> **문서 역할**: 코드·데이터·거래 실행·기능 지원·운영 배포의 **바뀌지 않아야 할 계약(WHAT MUST HOLD)**. 목표 순서/범위는 `01_FINAL_PLAN.md`; 실제 파일·함수와 작업 순서는 `02_DEVELOPER_TICKETS.md`; 검증 상태는 04/05. 이 문서는 기술 명세이므로 어떤 거래소·계좌·서버를 실운영 검증했다고 주장하지 않는다.
>
> **기준**: GitHub `PJH-02/VibeTrading` main `2c1a82a52a6a2eed0c304f324f6c49e2987edfac`; 현재 Deploy NATS 배치의 last recorded shape와 로컬 NATS-free 후보는 서로 다른 배포 세대다. 새 import path는 `02`의 Move Matrix를 실행·수락한 이후만 권위가 된다. 아래에서 `현재 파일`은 변경 전 HEAD의 경로다.

## ARCH-00. 지원 Scope·품질 표현법

`CAPABILITY_CODE_COMPLETE`, `LOCAL_INTEGRATION_VERIFIED`, `EXTERNAL_VALIDATION_PENDING`, `PRODUCTION_VERIFIED`를 **서로 다른 속성**으로 보관한다. `IMPLEMENTED`와 `TESTED`도 다르다. 미검증/지원 범위가 없는 것을 `PASS`로 처리하지 않는다.

- **REQUIRED_CORE**: 모든 인스턴스가 충족해야 하는 NATS-free 데이터·복구·기존 ETF/수집/클라우드 보존, **활성 Source 독립 진행·전역 Cross-Source 대기 0**, 명시적 전략 파일+Paper, 단일 계좌 Owner, 보고, 로컬 통합/패키징/안전. **Broker Adapter 확장 seam**은 필수 코드 기능이며 NH 실 API 구현과는 다르다.
- **REQUIRED_FOR_SELECTED_ROUTE**: `venue×product×env×account×strategy`가 배포 설정에서 활성화되는 경우 submit/cancel/query/ack/fill/private/reconcile/positions/fees/funding/risk가 존재해야 한다. 사용하지 않는 상품·모드를 위해 새 범용 Adapter를 만들지 않는다.
- **REQUIRED_CORE_CODE — Cross-Venue Execution**: **CAP-05는 실제 활성화할 Venue 페어가 선택되지 않았어도 필수 코드 구현·로컬 E2E 검증 대상**이다. 개별 전략의 알파 로직 개발이나 특정 실계좌 페어 활성화는 필수가 아니다.
- **REQUIRED_WHEN_STRATEGY_USES_FEATURE**: 특정 Venue 페어의 TESTNET/LIVE **활성화**, live shared account, hard strategy isolation 등은 해당 형태를 운영한다고 주장하려면 환경별 검증·승인이 필요하다. Cross-venue 엔진 코드 자체를 여기로 격하하지 않는다.
- **UNSUPPORTED_BLOCKED**: 지원하지 않는 inverse/margin/position-mode/venue, 미완성 Private WS·시그널 인과·owner transport는 명시 reject. `CODE_MAPPING_ONLY`를 Order Live 가능으로 보고하지 않는다.
- **EXTERNAL_ONLY**: 인터넷/API 인증·실계좌 조회·서버 mount/자원/장중 feed/72시간 soak 같은 환경 증거. 오프라인 구현 가능한 코드·스크립트·fixture가 없다면 External-only로 분류 불가.

### ARCH-00A. 1차 Capability Matrix (CAP-00가 검증·확정)

현재 *관련 코드의 존재 수준*만 표시한다. 아래 `PENDING_SELECTION`은 PM이 원래 사용 목표를 보존해 support set을 확인해야 한다는 의미이며 자동 신규 개발 승인이나 선택 해제가 아니다.

| 대상 | Product·환경 | 현재 증거 (코드 경로) | 현 판정 | 필수 완료 방식 |
|---|---|---|---|---|
| KIS | ETF, MOCK/MOCK2 | `etf_trading.py`, `kis_execution.py`, `etf_journal.py` | EXISTING_LOCAL_REGRESSION; 서버 불명 | C3/KP/KD Golden, 실제 계좌별 Mock/Mock2, feed/order/reconcile 유지, deployed check external |
| 신규 국내주식 Broker (예: NH투자증권/나무) | 미래 국내주식/ETF Spot | 실제 Adapter 코드 없음, 이 릴리스에서 API 지원 구현 아님 | EXTENSION_POINT_ONLY | **Dummy Broker만 Local Add/Disable/Remove 수락**; 실제 NH 선택 시 별도 API 계약·인증·주문·취소·조회·체결·잔고·복구 구현/검증 후에야 TESTNET/LIVE 지원 선언 |
| Kiwoom | 국내주식/ETF Data + 주문 API | `providers/kiwoom.py`, `brokers.py::KiwoomBrokerAdapter.submit_order` (현재 `order_status` 미구현), 구 `services/execution/kiwoom_broker.py`의 COM legacy 여부 조사 | EXISTING_ORDER_SUBMIT_PARTIAL; full owner/reconcile 미완성 | 기존 REST 구현 재사용, **선택 KR cash route의 submit/cancel/query/open/fills/account/reconcile**를 제품별 capability와 credential-free scripted transport로 연결·검증. COM은 별도 OS/실행조건; real 인증/거래는 EXTERNAL_ONLY |
| TOSS | ETF/index Data 수집, 주문 미사용 | `providers/toss.py`, `brokers.py` | DATA_ONLY_FEED_REQUIRED | 설정된 Data 수집은 정상 지원, 주문 Mutation 금지. 기존 timestamp/Golden·rollback 보존 |
| Binance | Spot historical | `services/execution/crypto_binance.py`, `brokers.py` `/api/v3` | EXISTING_SPOT | Spot≠Perp, 기존 Spot API/client 유지·검증 |
| Binance | USDⓈ-M Linear Perp | `perp_execution.py::BinancePerpAdapter`, `/fapi` | REQUEST_MAPPING_ONLY/Owner wiring 미확인 | 선택 route에서 transport/private/reconcile/permit/funding code, fake integration; actual API external |
| Bybit | Linear USDT Testnet | `fv_mm/venues.py::Bybit`, `fv_mm/private.py`, `liquidity_mm`, `bybit_execution.py` | EXISTING_TESTNET_EXECUTION | 기존 create/cancel/fill/reconcile 그대로 재사용하고 selected integration만 추가 |
| Bybit | Linear USDT Live | `perp_execution.py::BybitPerpAdapter`, 기존 testnet client | LIVE_OPERATION_UNVERIFIED | 테스트넷 경로를 그대로 Live 입증으로 간주하지 않음, env/permissions/owner 검증 |
| GRVT | Perp Testnet | `fv_mm/venues.py::Grvt`, `fv_mm/private.py`, `extended_brokers.py` | EXISTING_TESTNET_EXECUTION | signed submit/cancel/order/fill/funding/protocol shape 보존, selected bridge |
| GRVT | Perp Live | `perp_execution.py::GrvtPerpAdapter` mapping/signing, existing testnet routes | LIVE_OPERATION_UNVERIFIED | 실제 환경의 identity/scope, private, owner, signature protocol proof |
| Arcus | Perp | `extended_brokers.py::ArcusBrokerAdapter`, `perp_execution.py::ArcusPerpAdapter` | PARTIAL_PROTOTYPE | 기존 WS/order signature 우선, 실제 selected cancel/fill/reconcile gap만 |
| Hyperliquid | First-dex Perp USDC | `providers/hyperliquid.py` read path, `perp_execution.py::HyperliquidPerpAdapter` SDK/nonce | REQUEST_MAPPING/SDK_COMPOSITION | 선택된 Signing/Transport/nonce durable/query/fill/funding integration; unsupported HIP-3/Spot 차단 |
| 범용 PAPER | ETF/Crypto 선택 선물/현물 | `strategy_runtime.py`, `paper_execution.py`, `portfolio_ledger.py`, VN13/TX05/TX08 | SELECTED_LOCAL_PASS | 외부 Mutation 0, 파일별 전략, 재시작 정합, 실제 market data input, 모드 분리 |
| **Cross-venue Execution Group (코드 필수)** | **선택 가능한 서로 다른 두 지원 Venue의 Perp**; 실제 pair 활성화는 별도 | `execution_groups.py::PaperExecutionGroups`는 관측 전용, 기존 MM runner 주문 경로·Owner는 별도 | **EXECUTION_NOT_COMPLETE** | **CAP-05 필수**: 단일 Group Intent → A BUY/B SELL 두 주문의 `CONCURRENT/SEQUENTIAL` 전송, leg별 실제 Owner/Adapter 매핑·ACK/Fill/UNKNOWN·부분 체결·대조·위험·재시작. 최소 서로 다른 두 실제 Adapter 코드 경로를 fake transport로 통합 검증. 실제 계좌 pair 설정은 opt-in |

### ARCH-00B. 최초 범위의 기본값 — 승인 없는 목표 축소 금지

CAP-00에서 별도 사용자 변경 승인 근거를 찾지 못하면 다음을 **DEFAULT_REQUIRED_CODE_SCOPE**로 유지한다. 이는 무조건 실제 거래를 켜라는 뜻이 아니다.

- **Perp 코드 지원 목표**: Binance USDⓈ-M, Bybit linear, GRVT Perp, Arcus Perp, Hyperliquid first-dex Perp의 명시한 **지원 상품/환경**에 대해 주문 제출·취소·조회, 상태/체결 정규화, 포지션·계좌, 필요한 수수료/Funding, UNKNOWN·복구·주문 Owner 연결을 *로컬에서 구현·Fake API로 검증 가능*한 상태까지 완성한다. 기존 구현을 재사용하며 Testnet+Live 지원을 주장하려면 환경별 인증/URL/서명 차이를 코드에서 차단/검증한다. 실제 Testnet/Live API 권한·실주문 결과는 EXTERNAL_ONLY. 외부 API가 문서화되지 않은 기능은 명시된 지원 모드를 좁히고 근거·사용자 결정을 기록할 것.
- **기존 전략 동작**: KIS C3/KP/KD/Mock/Mock2 및 기존 Bybit/GRVT testnet FV/MM runner는 경제 규칙을 바꾸지 않고 남겨야 한다. 신규 파일 전략은 공통 Paper 진입점에서 독립 등록·제거 가능하다.
- **포지션 관리**: 선택된 linear Perp의 real-account status/position reconciling과 Paper margin/fee/funding safety 계약. Universal inverse/portfolio margin 등은 구체적 전략 필요·상품 계약이 없는 한 unsupported.
- **거래소 간 실행 (필수)**: **CAP-05 `REQUIRED_CORE_CODE`**. 실제 Arb 전략 파일이 아직 없어도 Group 신호→서로 다른 두 거래소 주문의 **동시/순차 제출·Leg 생명주기·부분 체결·잔여 위험·헤지·복구를 구현·로컬 검증**한다. 전략별 실제 Venue A/B·계좌·허용 노출·헤지 정책 선택은 운영 활성화 조건이며, 미선택 시 외부 Mutation은 `BLOCKED`다. **선택된 실계좌 페어가 없다는 이유만으로 CAP-05를 `NOT_APPLICABLE_APPROVED` 처리하거나 오프라인 코드를 생략할 수 없다.**
- **모델/전략 연구**: 새로운 알파 성능 보장이나 신규 전략 개발은 본 리팩토링 완료 조건이 아니다. 구조와 실행 계약이 해당 전략을 파일로 탑재할 수 있어야 한다.

이 목록은 최초 v3 목표의 venue 집합과 원래 사용자 제약을 합쳐 정한 **요구 계약**이지 최신 GitHub 코드에 이미 구현된 기능이라는 주장이 아니다. 특정 Venue가 규정/접근 제한으로 지원할 수 없으면 CAP-00에 정확한 blocker를 기록해 **전체 기능 완료를 보류**하고 사용자 승인으로만 범위 수정한다.

**선택 범위 결정 레코드(요구)**: `venue`, `market_segment`, `product`, `environment`, `account_scope`, `strategy_id`, `execution_mode`, `claim`(`DATA_ONLY|PAPER|TESTNET|LIVE`), `required_mutations`, `source_functions`, `transport_owner`, `private_stream`, `reconcile_path`, `local_tests`, `external_gate`, `decision_owner`, `decision_status`. `DECISION_REQUIRED` 상태에서 전역 `ORIGINAL_PLATFORM_REQUIRED_FEATURES_COMPLETE`를 true로 둘 수 없다.

## ARCH-MOD. `src/` Direct Domain Packages — 최종 Authority 불변조건

**HEAD의 `src/vibetrading/**`는 오직 이동의 원본**. 릴리스 정식 구현은 `src/{core,feeds,market,storage,strategies,execution,portfolio,runtime,operations,timefolio}/**`이다. `src/vibetrading`/`vibetrading.*` Import·CLI·wheel canonical 패키지는 최종 이미지에서 사용하지 않는다. `src` 자체를 패키지(`import src.*`)로 만들지 않는다.

```text
src/
  core/                # Pure event/instrument/action identifiers/contracts
  feeds/providers/     # Independent venue/broker market data collectors
  market/              # Native L2, shared state, event consumption
  storage/             # spool/UDS-independent durable storage/Parquet/cloud/recovery
  strategies/          # KR/crypto plugin files, importable loader, research segregated
  execution/           # sole Owner, order facts, group coordinator, venue/broker adapters
    venues/            # canonical execution adapters or minimal registered modules
  portfolio/           # sole account/position/funding/reservation ledger
  runtime/             # process role wiring, CLI, config, checkpoint, orchestrated loops
  operations/          # Telegram, dashboard read endpoints, health, reporting
  timefolio/           # Official tournament order submission browser client/runner
```

**Packaging contract**:
- 기존 `pyproject.toml`은 `vibetrading="src/vibetrading"`을 제거하고 `where=["src"]`에서 실제 package 목록을 발견하게 한다. 프로젝트 distribution 이름 `vibetrading`은 유지 가능하나 import package `vibetrading`은 별개이며 제거 대상이다. wheel/editable 설치를 `/tmp` 등 repo 외 CWD에서 검증; 서비스 내 `python -m runtime.<entry>`와 legacy `main.py`가 새 canonical path만 불러야 한다.
- root `strategies/`는 `src/strategies/`와 충돌한다. 현재 root `strategies/`, `services/`, `shared/`, `strategy/`의 active contents는 owner로 이동 후 root 남은 import 경로를 수락 단계에서 0으로 만든다. 단순 sys.path 우선순위 변경/런타임 `sys.path.insert()`로 해결했다고 주장 금지. `importlib.util.find_spec`, `module.__file__`, wheel RECORD, `python -m` from root and outside repo의 모두 동일 canonical 확인.
- `core`는 Exchange/Storage/Strategy를 import하지 않는다. `feeds`는 주문/portfolio를 갱신하지 않고 `strategies`는 직접 네트워크 mutation을 호출하지 않는다. `runtime`은 조립 전용으로 venue 정책/경제 로직이나 과거 MM 앱의 무조건 보관소가 아니다. `operations`는 hot path에서 동기 DB/Cloud/HTTP 대기 금지.
- 기존 Function/Callback/Signer/Native WS 코드는 안전성 테스트 후 재사용. `bybit_execution.py`의 별도 Probe·`kis_execution.py`의 주문 구현·`perp_execution.py` 복수 Venue 클래스는 canonical `execution/venues` 구조로 통합/배치 가능하지만 **동일 Venue+Product+Environment 주문 전송 authority가 두 곳에 남으면 FAIL**. 이전 파일명 자체가 지원 기능 목록을 의미하지 않는다.
- `fv_mm`, `liquidity_mm`, `fv_pilot`, `mm_state`: 경제 로직 연구·과거 testnet runner는 필수 기능이 아니다. 필요한 adapter/pricing/matching/test oracle을 책임별로 이관한 뒤 CLI/active config/tests/rollback 참조가 0이고 사용자 활성 운영 대상이 아니면 `CLN-02`에서 제거. 과거 이력이 있다는 이유만으로 `runtime/apps`에 무조건 보관하면 `G-LEAN-CODE` 미충족.
- `timefolio`는 **삭제 대상 아님**. `src/timefolio/{client,runner}.py`로 정식 보존, 브라우저 대회 주문의 승인·로그인·중복 주문/UNKNOWN/페이지 응답 불확실성·NATS-free 시세 주입·위험 한도·중단을 검사한다. Timefolio를 Crypto Venue REST Client로 치환하지 않는다. Dashboard Timefolio monitor도 별도 필요한 호출자 유지.
- Python source ABI/직렬화의 `__module__`, importlib plugin id, old artifacts/serialized receipt는 마이그레이션 시 변할 수 있으므로 실제 구형 artifact readback/forward-conversion 또는 명시 blocker로 검증한다. 두 버전 패키지를 장기간 공존시켜 모호하게 만드는 해결책 금지.


### ARCH-MOD-1. Atomic Mechanical Move Proof

각 파일에 다음 프로토콜을 적용한다:

```text
path_id = sha256(old_path || git_blob_hash)
move_map[path_id] = {old_path, new_path, role, mode, active_refs,
                     dynamic_refs, entrypoints, tests, rollback_refs}
move := git mv(old_path,new_path)
static_change := import_fix | path_fix | build_fix | CLI_fix
assert AST_old_business_functions == AST_new_business_functions
assert canonical_source_module_identity == legacy_alias_identity (when required)
assert all_selected_tests_and_provenance_hashes_equal_or_expected_diff
```

- 파일의 위치 이전과 비즈니스 로직 수정을 한 커밋에 섞지 않는다. `git mv`만으로 Python package Import, Relative import·serialized `__module__`, Monkeypatch/Fixture 및 dynamic import가 자동 수정되는 것이 아니다.
- `__init__.py`는 side-effect-free. 모든 `src` 패키지 root의 auto-discovery/Implicit Strategy Registration 금지.
- `new_path`에 실제 기존 파일이 있으면 **충돌 BLOCK**, 덮어쓰기/맹목 merge 금지. Old/New normalized identity와 hash 비교 전 `git rm` 금지.
- 정적 import rewrite는 AST/LibCST 사용, raw sed는 관련 테스트로 문법·semantic 문제가 없음을 입증할 때만. Local script는 접근 가능한 repo 코드만 검사하며 사용자 홈/secret/worktrees 전역 grep 금지.
- 이동 완료 증거: changed bytes/statements가 import/path/packaging 보정 범위를 넘지 않음, mutation count/golden equal, 실제 소유 경계 alias policy 충족, `src/vibetrading` active package 0 및 `src/` 충돌/미분류 Python 0.

## ARCH-DATA. Feed/Market/Time Model

### ARCH-DATA-1. Event와 인과성

- 기존 `BaseMarketEvent`, `InstrumentId`, `OrderBookEvent`, `Bar1mEvent`, `Decimal`, native payload/provenance, Arrow Decimal(38,18) 보존. 새 전면적 wire envelope 도입 금지; 구형 필드를 의미에 따라 정확 매핑.
- Provider/Instrument/Channel/Connection/Generation/Native Sequence/Ingress Cursor/Record Index/`event_time`/`receive_wall_time`/`available_at`을 구분. 서로 다른 source의 sequence를 하나의 글로벌 값으로 비교하지 않는다.
- ETF bar-start/bar-end label(TOSS vs KIS/Kiwoom), 09:00 bar가 언제 확정되는지, Native Revision, C3/KP/KD history warmup·closed bar·외인 수급 사용 가능 시점 유지. 누락 1분봉 ffill로 허위 근거를 만들지 않는다.
- Raw market delta apply는 `(venue,instrument,channel,generation)`별 한 Writer, Snapshot→연속 delta, gap 발생 시 `INVALID` 및 impacted strategy 주문 risk fence. Raw L2 전체와 UI/Top-K cached projection을 혼동하지 않는다.
- Shared MarketState는 각 이벤트 1회 apply, bounded feature cache keyed by `book_version/config_version/window_version`, Reader immutable snapshot. 가격/수량 정밀도 보존 가능한 경우만 int-tick 변환.

### ARCH-DATA-2. Spool/UDS/Durable pipeline

```text
Existing source collectors → normalizer → bounded local queue
→ durable local spool append/group fsync → UDS LocalTransport
→ LocalMarketEngine/HotPath shared state
                 ├─ strategy dispatch/Paper/selected order owner
                 └─ existing Parquet writer → verified Ncloud/S3 archive
```

- `ACCEPTED_RAM`, `APPENDED`, `DURABLE`, `ENGINE_APPLIED`, `STRATEGY_DELIVERED`, `ARCHIVE_COVERED`의 ACK/Receipt는 다르다. Durable-first(A1) 선택, 측정 없이 Hot-first로 바꾸지 않는다.
- Count/Bytes/OldestAge/In-flight/MaxFrame bounds, producer generation, replay idempotency, truncated final segment/corruption, schema changes explicit; silent delta drop/coalesce 금지.
- `MarketSpool`, `SpoolReader`, `LocalTransportServer/Client`, `LocalMarketEngine`, `LocalStorage`의 기존 코드와 VN07–09 로컬 증거 재사용. 기존 NATS consumer/order process는 전환/롤백 필요성 소진 전 삭제 금지.
- 원격 Ncloud backend의 put/verify/download와 catalog hash/readback을 재구현하지 않는다. Parquet: temp→fsync→atomic rename→manifest commit→ACK, remote readback evidence 없으면 last unique copy 삭제 금지.
- GC는 retained checkpoint ∩ reader progress ∩ Parquet/remote proof ∩ retention ∩ lease-free ∩ full ownership proof의 교집합; 증거 부족 `PRESERVE`. 기존 VN09는 dry-run proof이고 운영 자동 삭제 구현/승인 아님.

### ARCH-DATA-3. Recovery

- Market Cursor C, handoff H, `(C,H]` Replay, bounded post-H ring 정렬, producer/incarnation/version vector, strategy state/etf/paper journal seals를 동일 일관 경계로 확인. 최신 원장을 과거 checkpoint로 rewind 금지.
- `REPLAY/WARMING/INVALID` 신규 위험주문 0. 늦은 fill/ACK/old sent UNKNOWN은 권위 원장+venue query로 대조하며 재전송 안 함.
- Checkpoint 손상·원장 ahead/behind·retained prefix/provenance missing·Cloud readback 실패면 local candidate fail closed. 복구되지 않은 data gap을 과거 입력으로 뭉개지 않는다.

### ARCH-DATA-4. No Cross-Venue Synchronization Barrier / Minimal Per-Source Normalization (P0)

**DATA-NB-01/02/03/04는 기존 ARCH-DATA-1~3의 추가 수락 조건이며 새로운 Data Engine을 요구하지 않는다.** **Source**는 최소 `(venue/provider, market_segment, instrument, channel, connection_generation)`으로 구분하고, 의미상 해당 Source의 Sequence/Book 상태에 필요한 작업만 수행한다.

1. **DATA-NB-01 — Ingress 독립**: Venue A가 정상 이벤트를 송신하면 Venue B의 업데이트, websocket 연결 성공, 거래소별 Book warmup, 동일 wall-clock timestamp, 공통 BAR/Candle 종료, 외부 reference price, global `min(watermark)`를 기다리지 않고 A의 `receive → Source-specific validate/normalize → bounded admission`이 진행된다. `asyncio.gather(*all_venues)`/thread.Barrier/global join/동일 interval 데이터 수집 종료는 오직 정적 연구용 offline join에 허용하며 운영 hot path에 불허. `startup`과 `strategy readiness`에서 B를 필요로 하는 특정 전략의 신규 주문 차단은 허용하지만 **A 수집/영속 기록/상태 갱신은 멈추면 안 된다**. A의 자체 broken sequence는 A만 `INVALID`; 공유 디스크 장애 같은 실제 공통 고장은 범위대로 fail closed 가능.
2. **DATA-NB-02 — 최소 정상화/원본 보존**: Native payload/Sequence/tick/lot/depth 순서를 단일 Producer에서 재해석 가능한 형태로 보존한다. 수신 시 필수 검증은 `venue/product/channel/instrument`, timestamp provenance(`event`, `receive_wall`, `available_at`), generation/sequence continuity, 가격·수량 정밀도, 지원 이벤트 타입. 시간대 변환·bar-start/end 변환은 정보 가용성 손실 없이 **해당 Provider의 선언된 convention**에 한 번만 수행. 불필요한 DataFrame 변환/전역 심볼 매핑/JSON serialize↔parse 왕복/공통 1초/1분 resample를 hot path에 추가 금지. Cross-Venue instrument equivalence/FV/Leg size/staleness based spread/hedge **경제적 정책**은 `strategies/`만 결정. 엔진은 마지막 유효 관측과 source quality/age를 사실로 제공한다.
3. **DATA-NB-03 — A1 durable-before-expose, per-source order**: `ACCEPTED_RAM → APPENDED → DURABLE → ENGINE_APPLIED → STRATEGY_DELIVERED` 순서 및 각 producer의 순차 cursor는 변경 불가. `local_pipeline.py::SpoolingMarketPublisher.accept_payload/_writer/_persist/_deliver_live`, `market_spool.py`, `local_transport.py`, `local_engine.py::LocalMarketEngine.on_frame` 기존 contract와 evidence를 보존. **`group_sync_ms`는 유한하고 계측 가능한 fsync grouping 대기**이지 전 거래소 모임 대기가 아니다. `RECEIVED`/`APPLIED` ACK는 transport·engine 사실이며 A1 durable의 대체 증거가 아니다. Storage/Parquet/Cloud의 원격 완료를 대기해 `STRATEGY_DELIVERED`를 지연시키지 말 것; 동시에 readback/GC safety는 보존. 동일 producer 회복 순서/여러 Producer 간 independent progress를 각각 증명.
   - **기존 발견된 숨은 전역 Ready Gate**: `local_engine.py::LocalMarketEngine.event_source(required_producers=None)`는 기본값에서 **모든 `_feeds` LIVE를 기다리도록 구현돼 있다**. 따라서 공유 스트림은 기존 `local_runtime.py`처럼 `event_source(required_producers=())` 또는 전략별 `required_producers`만 전달하고, 진짜 전략별 `DataRequirements`/feed age/quality 보호는 각 전략 Run/Execution Gate에서 수행해야 한다. `LocalMarketEngine._initial_proofs_ready/_initial_resume` 또한 **등록된 모든 feed의 초기 복구 proof를 확인**하므로, startup/recovery의 계좌·체크포인트 일관성 확인을 유지한 채 A 독립 feed/독립 전략을 재개할 수 있는지 `runtime_checkpoint.restore`, `complete_checkpoint_recovery`, `LocalMarketEngine._queue_admissible(producer)`를 연결해 별도 검증한다. **필요한 복구 증거가 없는 A에 주문을 허용해서는 안 된다**. Root의 전체 복구 수락 게이트가 의도상 계좌 전체를 차단해야 한다면 `BLOCKED_RECOVERY_DEPENDENCY`라고 정확히 명시하고 이를 수집·정규화 단계의 전역 동기화라고 오인하지 말 것. A만 사용 가능한 독립 전략에 대한 100% 완료를 주장하려면 복구 후 Source 단위 독립 진행 테스트를 통과해야 하며 안전 근거 없이 all-feed proof 검사를 삭제해선 안 된다.
4. **DATA-NB-04 — Failure isolation and bounded pressure**: B가 5분 무응답 또는 reconnect/sequence gap이어도 A 생산자의 정상 event이 A durable/market state까지 계속 처리되어야 한다. 다만 B 데이터가 필요한 전략은 `INPUT_STALE/BLOCKED`로 신규 위험 Action을 제출하지 않는다. 느린 Producer가 전체 공통 단일 큐의 head-of-line을 유발할 경우 먼저 queue/dequeue/flush/ACK span 계측, 그 후 기존 bounded path에서만 최소 수정한다. 무제한 buffer, silence 중 synthetic forward fill, silent delta-drop, replay 중 주문, A1 생략 금지. Disk ENOSPC 같은 shared-fate 오류는 별도 `COMMON_FAULT`로 기록하고 다른 Venue 독립성 테스트 성공으로 오인하지 않는다.

**P0 로컬 검증 시나리오 — 종속 티켓 MOV-02(이동 보존), QA-01(수락), PERF-01(병목)**

| ID | 입력/장애 주입 | 절대 수락 기준 |
|---|---|---|
| `DATA-TEST-01` | A에 100개 Source-valid event를 연속 입력; B 소켓 연결 0개 / B 이벤트 0개 | A에 `DURABLE`과 `ENGINE_APPLIED` 100개 순차, A only 전략 입력 발생; global join/대기 호출 0; B source 미유효 사실은 표시 |
| `DATA-TEST-02` | A 10개, B 1개 → B disconnect/gap → A 90개 | A 총 100개 정확 1회; B 의존 전략 risk Action 차단·B 상태 stale; A only 전략은 정상. B reconnect 후 generation 복원은 별도 |
| `DATA-TEST-03` | A/B 동일 symbol text, 서로 다른 market segment와 cadence, 수신 순서 임의 interleave | Native precision/order/segment 분리, Source cursor 간 잘못된 글로벌 ordering 없음; `available_at` 이후만 소비; spot/perp 혼동 없음 |
| `DATA-TEST-04` | A 정상, Parquet/Cloud 업로드 60초 지연 및 Telegram stalled | A 전략 전달은 **로컬 A1 durable + engine apply** 이후 Cloud ACK와 무관하게 진행; 무제한 큐/원본 손실 0 |
| `DATA-TEST-05` | first-record/low-rate + burst로 `group_sync_ms`, flush, UDS RECEIVED/APPLIED per-record ACK 프로파일 | 각각 queue wait/group wait/fsync/delivery_ack_wait/market_apply 시간을 분리 계측; mandatory all-source barrier = 0; p95/p99 목표 수치만으로 PASS/FAIL 판정 금지 |
| `DATA-TEST-06` | spool fsync 오류/ENOSPC, replay & duplicated packet | A1 이전 전략 공개 0; 관련 Source `INVALID`, recovery 후 cursor idempotency; shared disk fault를 Source isolation 기능 결함으로 잘못 분류하지 않음 |

테스트 이름은 **신규 후보 테스트 ID**다. 구현 시 현재 테스트 폴더를 먼저 확인하고 가장 가까운 기존 테스트에 작은 케이스로 병합. 신규 background joiner·중복 normalizer·별도 data fabric·forced cross-venue snapshot synchronizer를 만들면 scope 위반이다.

### ARCH-DATA-5. Feed Provider 이름이 Venue 경계를 의미하지 않는다는 사실 (P0)

- HEAD에는 `providers/binance.py`(특정 Binance TradFi Futures 수집), `providers/crypto_perp.py`(기존 복수 USDT Perp 공통 Collector), `providers/extended_perp.py`(Arcus/GRVT), `providers/hyperliquid.py`, `providers/kis.py`, `providers/kiwoom.py`, `providers/toss.py` 등이 이미 존재한다. 현 파일명을 그대로 유지해야 하는 것은 아니다. 빠진 거래소가 자동 미지원이라는 뜻도, `crypto_perp`에 모든 Crypto Venue가 포함됐다는 뜻도 아니다. 정확 지원은 `provider × market_segment × channel × symbol × environment` Matrix로 식별.
- `feeds/providers/`는 실제 지원 Source별 정규화·서브스크립션·연결·native sequence 정확성이 단일 소유자 아래 있으면 충분하며, 단순 균일한 디렉터리 모양 때문에 copy-paste provider를 만들지 않는다. 반대로 Venue-specific wire/sequence/gap 분기가 과도하고 독립 변경을 방해하면 **그 부분만** adapter별로 분리. Common book normalization/calendar/checksum을 중복 구현 금지.
- `execution/venues` 등록만으로 시세 Feed가 생성되는 것이 아니다. 신설 Venue의 전략이 시세를 사용한다면 `feeds/providers` 구현·Config·서브스크립션·응답 이벤트 정합·replay/independence 테스트까지 연결되어야 한다. 주문만/수집만을 각각 지원 가능한 Capability로 분리 표시한다.
- TOSS는 주문 DATA_ONLY이고 시세 Collector를 정식 유지한다. 실제 활성 종목·환경은 운영 Config에서 선택한다. TOSS 시세 수집은 제품 기능이므로 `CLN-02` 자동 삭제 대상에서 제외하며 Golden을 유지한다. User approval 없이 금융 원본 데이터·클라우드 실물 삭제 금지.


## ARCH-ACTION. 파일 기반 전략의 **완전한 주문 의사결정** 계약 (신규 필수)

### A. 단일 Strategy File / Strategy Package의 소유 범위

- **운영 활성 전략은 C3/KP/KD만 기존에 존재**. `fv_mm`, `liquidity_mm`, `fv_pilot` 등은 폐기된 경제 전략이며 자동 배포·자동 등록을 금지한다. 그 안의 Venue submit/cancel/private/reconcile 구현과 필수 회귀 fixture는 별도 재사용 대상이다.
- 전략은 직접 REST/SDK·credential·DB writer를 호출하지 않고, 기존 공유 시세·History·계좌/체결 사실을 **읽기 전용**으로 전달받아 `ActionPlan`을 산출한다. **어떤 거래소에, 어떤 계좌에서, 무엇을 어떤 주문으로 낼지**에 관한 결정은 전략 파일에만 존재한다.
- **전략 정책 완전 소유(P0)**: Cross-Venue 상품의 경제적 동등성·Leg 비율·스프레드/기대 수익성·거래소별 기존 재고 사용·헤지 대상/수량과, MM의 FV/스프레드/양방향 quote/size/reprice/cancel/park·목표 재고·Hedge Trigger는 모두 전략 파일이 결정한다. 공통 엔진은 이런 가격·수량·전략을 추론하지 않고 주문 사실·계좌 사실·검증/리스크 허용 여부만 소유한다. 전략 파일 외부에 MM 정책 엔진/독립 Inventory Target 서비스 신설 금지.
- 표준 Strategy Plugin 인터페이스에 기존 `on_event(event,data)`를 보존하면서, 주문 결과 수신을 위한 `on_order_event(event,context)`(정확한 이름/args는 기존 loader 계약과 호환되도록 한 번만 확정) 및 복구를 위한 `snapshot_state()/restore_state()` 선택 계약을 추가한다. `on_event`/`on_order_event`는 동일 전략 경제 상태의 소유자에게 직렬화/일관 순서로 전달하며, 중복 event는 중복 행동을 생성하지 않는다.
- 직접 Python 전략 파일에서 다음 항목을 정한다: `NEW|CANCEL|REPLACE`, `venue_id`, `product/segment`, `account_scope`, `symbol`, `BUY|SELL`, `quantity`, `limit_price` 또는 explicit market, `MAKER_POST_ONLY|TAKER`, `FOK|IOC|GTC` 지원 TIF, `PARK_GTC`(기존 주문을 호가에 유지하는 정책), `reduce_only`, `deadline`, `strategy_order_key`, 그룹 Leg 조합 및 **실패·부분 체결 시 후속 행동 정책**. Park는 별도의 Venue API 주문 타입이 아니라 **전략이 선택한 가격/유효기간/유지·교체 정책**이며 실제 Venue OrderIntent로 정확히 정규화한다.
- FOK는 **요청한 수량이 즉시 전량 체결되지 않으면 취소**하는 Venue TIF 의미. Maker/Post-only와 FOK 조합은 일반적으로 충돌하며 거래소별 명시적 Capability가 허용하지 않으면 즉시 차단한다. PARK_GTC 역시 Maker 보장을 암시하지 않는다: maker-only를 원하면 명시 post-only 플래그와 Venue 지원을 검증한다. Taker vs Maker 여부는 의도/flag이고 실제 체결 fee fact와 동일하지 않을 수 있다. 심볼/tick/lot/precision/API rate-limit/mode/market-hours 검사는 엔진이 별도 재검증한다.
- 주문 제출 실패 시 **확정 거절**/잔여 부분 체결/체결 지연/Cancel 완료 사실을 `OrderEvent`로 돌려주며 전략이 다음 Action을 선택한다. 엔진은 승인되지 않은 전략 정책을 추측하거나 자동으로 FOK→GTC/Market fallback 하지 않는다. 단, **네트워크 타임아웃·모호한 응답은 `UNKNOWN`** 이며 실계좌 조회/reconcile 전에는 전략이 새 위험 주문을 요구해도 공통 엔진에서 차단한다.
- `OPEN` 주문 수정은 strategy-owned working-order key+venue-native ID에 연결된 `CANCEL` 후 `NEW` 또는 해당 Venue가 실제로 지원하고 안전 계약이 증명된 `REPLACE`이다. 한 전략이 타 전략 소유 주문을 취소할 수 없다. 기존 주문·포지션·원장 사실의 최종 권위는 실행 엔진과 계좌 Owner에 있으며 전략 계산은 이를 변경할 수 없다.
- **읽기 전용 주문·재고 사실 피드백(P0)**: `on_order_event`의 컨텍스트(기존 DataView/ledger projection 재사용)는 Order/Fill ID, 전략 귀속 체결·잔여 주문, cancel-pending/late-fill, 실제 계좌·상품별 포지션, 사용 가능 잔고/예약/수수료 및 정보 시각·cursor·quality를 제공한다. 실제 계좌 잔고와 전략별 가상 귀속은 별도 필드로 구분하고, 조정되지 않은 수치/불명확한 attribution은 `UNKNOWN/UNAVAILABLE`로 표현한다. 전략은 이를 토대로 재호가·헤지를 결정하며 엔진은 별도 매매 판단을 내리지 않는다.

### B. 명시적 ActionPlan 스키마 (개념적 계약이며 기존 Signal ABI 강제 교체 아님)

```python
@dataclass(frozen=True)
class OrderAction:
    action_id: str             # replay-stable, idempotent
    strategy_id: str
    venue_id: str              # Config 등록된 Venue ID; 코드 분기하지 않음
    account_scope: str         # explicit PAPER/MOCK/TESTNET/LIVE scope
    instrument_id: str        # 전략 config alias를 검증·해석한 venue/segment/symbol 상품 식별자
    action: Literal['NEW', 'CANCEL', 'REPLACE']
    side: Literal['BUY', 'SELL'] | None
    quantity: Decimal | None
    price: Decimal | None
    liquidity_intent: Literal['MAKER_POST_ONLY', 'TAKER'] | None
    tif: Literal['FOK', 'IOC', 'GTC'] | None
    parking: bool              # 전략 GTC 유지 정책이며 venue 주문 type은 아님
    reduce_only: bool
    deadline_ns: int
    client_order_id: str | None
    target_order_id: str | None
    group_id: str | None

@dataclass(frozen=True)
class StrategyActionPlan:
    strategy_id: str
    decision_id: str
    data_available_at_ns: int
    actions: tuple[OrderAction, ...]
    group_submit_mode: Literal['CONCURRENT', 'SEQUENTIAL'] | None
```

위 유형은 **기능·검증 최소 필드**를 명시한 의사 코드이다. 전략별 Config에서 `instruments` alias(예: `spot: binance/spot/BTCUSDT`, `perp: binance/usdm_perp/BTCUSDT`)를 선언하고 기존 설정 파서/Adapter 메타데이터로 해석한다. 동일 ticker라도 시장 구분 없이 유일하다고 가정하지 않으며 신규 글로벌 상품 레지스트리나 전략별 엔진 분기를 만들지 않는다. 기존 `Signal`, `OrderIntent`, `ExecutionGroupSpec`가 이미 표현 가능한 속성은 어댑터로 재사용하고, 양쪽에 서로 다른 단일 권위를 만들지 않는다. 전략 파일 경제 결정은 `on_event` / `on_order_event`의 결과로 테스트할 수 있어야 하며 Venue ID나 비공개 키를 런타임 소스에 하드코딩하지 않는다.

### C. 결정을 돌려주는 이벤트와 주문 루프

1. Shared MarketState가 causal snapshot / per-venue BBO/Trade/ETF bar를 하나의 bounded async fanout으로 전달한다.
2. 전략 파일이 ActionPlan 생성: 예를 들어 한 Venue에 `PARK_GTC` Maker 매수/매도, 또는 두 Venue에 BUY FOK + SELL FOK. 실시간 호가를 사용한 park 취소/재제출 여부도 전략이 결정한다.
3. 공통 Execution Router는 순수 계획을 `venue_id / account_scope / instrument` 기준으로 검증·선택된 **기존 단일 Account Owner**에 전달. 원장 커밋 이후 지정된 Venue Adapter 메서드를 호출한다.
4. Adapter/Private/REST Reconciler가 `ACCEPTED / REJECTED / PARTIAL_FILL / FILLED / CANCEL_PENDING / CANCELLED / UNKNOWN / FUNDING / ACCOUNT`의 충분한 원본 정보·ID·시각·evidence를 정규화하고 durable journal을 거쳐 전략에 순서 있게 전달한다. 주문 업데이트와 연관된 검증된 working-order/position/balance/reservation 사실도 일관된 버전의 읽기 전용 컨텍스트로 전달한다.
5. 전략 파일의 주문/실패 콜백이 새로운 `OrderAction`을 낼 수 있다. 리스크/계좌 권위·중복·모드·UNKNOWN 차단은 항상 공통 엔진이 강제한다.
6. Restart 후 전략 State+Group+Owner/Account+input Cursor 일치 증명 전 신규 주문 금지. 과거 콜백을 Replay했다고 과거 주문을 재제출하지 않는다.

**완료 시나리오**: 새 crypto 전략 파일을 추가하면 **공통 Runtime 수정 없이** PAPER와 자격증명 없는 기존 구체 Venue Adapter 경로를 호출하고, 명시적으로 승인한 TESTNET/LIVE에서는 동일 의도 의미가 실제 주문 API로 이어진다. C3/KP/KD는 기존 KIS Mock/Mock2 주문 경로에서 새 파일의 유일한 의사결정 구현을 사용하되 Golden economics 불변. `fv_mm`/`liquidity_mm` 실전 자동 활성 0.

## ARCH-VENUE-EXT. Crypto Exchange + Domestic Stock Broker 공통 실행 확장 규약 (P0)

**목표는 `new adapter module + declarative config + integration test`로 확장하는 것이며, 모든 미래 API를 이미 구현했다는 주장이 아니다.** 신규 Factory/Instrument Discovery 서비스는 금지, 기존 `execution_owner.py`, `brokers.py`, `perp_execution.py`, `extended_brokers.py`, `kis_execution.py`의 입증된 코드를 활용한다.

### VEN-PROT-01 — 최소 공통 계약과 Ownership

- `VenueAdapter`는 명칭일 뿐 Crypto 전용이 아니다. KIS/키움/NH 같은 **국내주식 Broker도 같은 실행 Plugin 프로토콜**을 구현한다. 구체적으로 `submit`, `cancel`, `query_order`, `list_open_orders`, `get_fills`, `query_positions/balances`, `reconcile`, `get_instrument_spec`와 `normalize_private_event` **또는 검증된 bounded polling**을 capability/환경별로 연결한다. 펀딩/마진 등 Perp 전용 항목은 **Crypto Perp에만** 요구하고 Cash Broker에 거짓 구현하지 않는다. 실제 Broker의 미지원 상품/TIF/reduce_only/funding은 `UNSUPPORTED`로 거부한다.
- 내부 라우팅 Key는 `(venue_id, market_segment, product, environment, account_scope)`이며 symbol text만으로 시장 구분 금지. 전략 Config의 `instrument_alias → venue_id/market_segment/symbol/account_scope`를 **기존 Config/Adapter가 해석**한다. Strategy/Router에 거래소별 `if venue==...` 분기 금지. `KIS cash MOCK/MOCK2`와 `Kiwoom KR stock`의 모드 분리는 명시; Broker별 실계좌 인증/작동 조건은 Adapter만 안다.
- 동일 `OrderAction` → Common Owner → 선택된 Adapter, 접수·ACK/Fill/CANCEL/UNKNOWN → `OrderEvent` + 읽기 전용 `portfolio` facts → 동일 전략. CEX의 private WS/polling과 국내주식 Broker의 polling은 구현이 달라도 **상태·부분 체결·중복·취소-늦은 체결/UNKNOWN** 의미는 같아야 한다. `ExecutionOwner` 현재 fake/offline-only 경계는 `EXEC-01`의 local scripted adapter 검증 없이 실제 Live 지원으로 승격 금지.

### VEN-PROT-02 — 대상 범위와 현재 구현의 한계

- **REQUIRED CODE**: 기존 5개 Crypto linear Perp(Binance USDⓈ-M, Bybit, GRVT, Arcus, Hyperliquid) 계약 유지; Binance Spot 기존 코드 보존·선택 Spot route에 맞춰 검증. KIS 국내주식/ETF MOCK·MOCK2 기존 Golden 보존. **Kiwoom 국내주식/ETF 주문 경로도 Local scripted owner E2E 요구**: 현 `brokers.py::KiwoomBrokerAdapter.submit_order`는 존재하지만 `order_status`가 `NOT_IMPLEMENTED`이므로 해당 선택 상품의 cancel/query/open/fill/reconcile 경로를 현재 문서화된 API·기존 호출자 근거로 구현·검증해야 한다. API 명세에서 확정되지 않은 endpoint/request/response는 추측해 코드 작성 금지; `CAP-00`에 `BLOCKED_API_SPEC_REQUIRED`로 기록 후 근거 확보 전 기능 완료 주장 금지. 구형 Windows COM 클라이언트는 REST Adapter와 별도 환경/기능으로 표시.
- **EXTENSION ONLY (현 릴리스에서 실제 주문 API 미구현)**: NH투자증권/나무 등 신규 국내 Broker는 Dummy Stub을 이용해 **등록·연결·disable/remove·공통 Router 불변성**까지만 로컬 검증. 실제 NH 주문/체결/잔고/취소/인증/환경 차이는 추후 NH Adapter 구현·testnet/live 검증 없이는 지원 선언할 수 없다. 신규 Crypto Venue도 추가 당일 해당 Venue adapter를 직접 구현해야 하며 Config 선언만으로 실행 가능해지는 게 아니다.
- **TOSS**: 시세 수집기로 지속 사용하되 `DATA_ONLY`로 **주문 Mutation만 금지**한다. 신규 전략은 Config를 통해 TOSS DataSource를 명시적으로 구독할 수 있다. 기존 수집기·Golden·bar-end 정합을 보존하고 실제 사용 중인 TOSS Producer는 삭제 금지.

### VEN-PROT-03 — Adapter 등록·변경·제거 및 fail-closed

1. `ADD`: `execution/venues/<name>.py` 또는 증거가 있는 기존 canonical module(중복 wrapper 금지) + 설정/Factory 등록 + product/mode capability + mocked transport 테스트. Core `execution_owner`, `strategy_runner`, `strategies/*` 파일 수정은 0. 모듈 자동 발견/임의 import 실행 금지; 명시적 등록.
2. `ENABLED`: 기재된 계좌·상품·환경·권한 검증 후에만 주문 가능. 지원하지 않는 Cash/Perp 조합/모드/TIF는 `BEFORE_SEND_UNSUPPORTED`로 fail closed.
3. `QUARANTINED/DISABLED`: 새 위험 주문 0. **기존 미체결·UNKNOWN 주문, 늦은 체결, 주문 취소·read/query/reconcile Owner는 남아 있어야 한다.** 비활성화가 타 Venue의 신규 위험 주문까지 무조건 전역 차단해서는 안 됨(실제 shared risk fault는 예외).
4. `REMOVED`: open orders/UNKNOWN/unreconciled fills/strategy configured aliases/account dependency/rollback dependency = 0 증거 확보 후에만 registry에서 제외. 제품 코드 파일 제거 자체는 별도 CLN acceptance 대상.
5. `LIVE`: user explicit approval + 환경별 credential/권한 + kill/fencing + 실제 private/feed/reconcile 검증 전 live mutation 0. Dummy Plugin을 Production supported로 주장 금지.

### VEN-PROT-04 — 구현·테스트 시 필수 Matrix

| Route | local acceptance | external boundary |
|---|---|---|
| Existing KIS KR Stock/ETF | Mock/Mock2 Action→Owner→KIS real class stub→ACK/fill/reconcile; Golden unchanged | KIS real credentials/actual live orders |
| Existing Kiwoom KR Stock/ETF | `KiwoomBrokerAdapter` reusable REST submit + 구현된 cancel/query/fills/account/reconcile → Owner scripted adapter E2E; `NOT_IMPLEMENTED` returned on required route 0 | 실제 Kiwoom 인증/계좌/실주문/운영 Windows COM claim 별도 |
| Existing five Crypto Perp | 각 product/signature/native precision/query/private/reconcile/Owner fake integration local | real testnet/live API proof |
| New Dummy Crypto Venue | one adapter file + config only, existing Strategy/Router unchanged, `VEN-01`은 contract harness normalized events, `EXEC-01`은 실제 Strategy/Owner callback E2E verified | 실제 신규 거래소 API 구현 없음 |
| New Dummy Domestic Broker (NH-like fixture) | one adapter file + config only, KR cash capabilities, unsupported funding, `VEN-01` contract-harness stock events → `EXEC-01` Owner/Strategy feedback | 실제 NH API 미구현·미검증 |
| Disabled/remove fixture | no new risk send, read/cancel/reconcile preserved; outstanding/strategy dependency blocks remove | 실제 계좌 closure 별도 |

위 Matrix는 `VEN-01`(등록), `CAP-01/02`(실제 선택 구현), `EXEC-01`(Owner 실행), `QA-01`(통합 증거)의 분담이다. `CAP-00`에서 지원/보류를 명확히 기록하고 실제 필수 route 구현을 `EXTERNAL_ONLY`로 숨기지 않는다.

### VEN-PROT-05 — 중복 실행파일 퇴역 계약 / Single Canonical Mutation

- `bybit_execution.py`는 HEAD에서 주문 지연/체결 Probe, `kis_execution.py`는 국내주식 Cash 주문, `perp_execution.py`는 5개 Venue API mapping, `extended_brokers.py`는 Arcus/GRVT signed WS, `brokers.py`는 Kiwoom 등을 포함한다. 이 파일명을 `execution/`에서 영구적으로 유지하는 것은 **어떤 품질 요건도 아니다**.
- **하나의 canonical adapter per `(venue, market_segment, environment, account_scope)`**. 주문 signer/HTTP submit이 두 번 복제되거나 별도 Runner가 common Owner를 우회하면 실패. 필요한 transport, notifier, nonce, audit, KIS mock/real TR mapping, Bybit clock/latency probe를 잃지 말고 정확한 함수/소유자에 통합. 과거 독립 `bybit_execution.py`, `kis_execution.py`, `perp_execution.py` 및 `extended_brokers.py`는 active 호출이 사라진 뒤 별도 파일/호환 wrapper가 불필요하면 삭제 가능; filename presence 여부는 수락조건 아님.
- **퇴역 전 근거**: `git grep`, AST import graph, entrypoint/shell/Compose/cron, dynamic plugin and tests, source-hash provenance/rollback, golden ACK/FILL/PnL and target fake transport. 각 moved symbol의 source→canonical owner map 기록. CLI·test runner·unit fixture 참조를 수리한 후, 빈 wrapper 유지보다 삭제를 선호한다. 실계좌 mutation은 하지 않음.
- 신규 Venue 구현 시 market-data adapter와 execution adapter는 **서로 독립**이며, 단일 등록 구성만으로 그 Venue의 모든 제품/Feed/Private 기능이 자동 구현되지는 않는다. `Timefolio` 주문 경로는 별도 웹 주문 Client/Runner로 유지하고 공통 Portfolio/Ledger 사실을 통합해야 할 때만 안전하게 접목한다.


## ARCH-FASTPATH. Python Hot Path 측정·구조 효율성 (계측 필수, 수치 목표 비차단)

실제로 측정할 지점: `T0=WS/provider receive`, `T1=normalize+engine availability`, `T2=shared market state applied & strategy delivery`, `T3=strategy returns ActionPlan`, `T4=owner durable intent/reservation commit`, `T5=adapter network send invocation`, `T6=venue ACK`, `T7=private fill`. 같은 프로세스 monotonic clock만 차이 계산; 다른 프로세스/서버 monotonic 값 직접 빼지 않음(분산 clock offset 보정과 신뢰 근거 없는 계산 금지).

- **Performance reference target (2vCPU/3.8GiB 대표 환경; NON-BLOCKING)**: **`T0→T5`** (Python Provider Callback 이벤트 수신→필요한 Durable Spool/영속 Order Commit→전략 판단→Adapter 네트워크 전송 호출 시작) **p50 ≤2ms, p95 ≤5ms, p99 ≤10ms**. `T1→T5`는 원인을 분리하는 필수 보조 계측이며 같은 수 ms 목표의 내역으로 보고한다. WAN 수집에 걸리는 외부 전송 시간은 `T0` 이전으로 제외. `T2→T3` 순수 전략 추론과 `T3→T4` 원장 커밋, `T4→T5` routing은 별도 히스토그램으로 계측. 실제 Venue의 `T5→T6` RTT와 `T6→T7` fill, collector WAN 지연, exchange matching latency는 별도 보고하며 위 참고 지연시간 목표에서 제외. 한 모드의 결과로 다른 환경 PASS 선언 금지.
- p50≤2ms/p95≤5ms/p99≤10ms는 **진단용 참고값**이다. **초과만으로 티켓·기능·릴리스 FAIL/BLOCKED 금지**. 대신 계측 결과·환경·내부 병목을 기록하고 불필요한 동기 대기/중복 처리/큐 적체를 개선 대상으로 분류한다. 계측이 없으면 `NOT_RUN`; 5ms를 데이터 신호 창 파라미터로 하드코딩하지 않는다. 모의 Broker ACK 0ms나 Recorder no-op은 `T5` 종점의 증거가 아니다. **실제 venue Adapter의 send call 직전까지** 계측한다.
- 데이터 수집·Spool 지속·Parquet/Cloud 이동과 전략 계산은 파이프라인 단계에서 병렬/비동기 수행한다. 다만 기본 A1에서는 **Spool durable ACK 이전에 이를 소비하는 전략으로 데이터를 공개하지 않고**, durable barrier 이후 Strategy와 Parquet/Cloud consumer가 서로 동기 대기하지 않는다. Durable A1 contract(`APPENDED/DURABLE/ENGINE_APPLIED`)를 만족하고 주문 전 원장 commit을 지킨다. 영속 쓰기/JSON 직렬화/Cloud 동기 I/O/Telegram은 Fast Strategy Callback에서 실행하지 않는다. Trade WAL fsync를 제거해서 속도를 만족시킨 척하지 않는다. 영속 ACK 이전 거래 허용 정책 A2로 전환하려면 별도 crash-loss window 증명·사용자 승인; 기본 A1 유지.
- Event loop는 bounded fanout, per-venue/strategy 큐, immutable snapshot reference, BBO incremental cache, minimal serialization/copy, per-account owner dispatch, cancellation priority를 사용하되 CPU profiling 후만 구조 최적화한다. Python process 수·IPC·worker 수는 2vCPU에 맞춰 측정. **CPU-bound 파이썬 callback이 이벤트 루프를 막을 수 있으면 hard-isolation을 주장하지 않는다.**
- Benchmark는 local synthetic only, representative captured native market trace, mixed KR + 2 crypto symbols, 2+ strategies, 2 Venue Group, Paper and scripted existing Adapter, disk Spool/SQLite WAL/group fsync, Parquet compaction+Cloud stall, cold/warm/restart/burst를 구분한다. 스케줄링 지터, CPU steal, GC, cgroup throttling, disk stalls를 기록한다.
- **Source independence 계측 P0**: 기존 `local_pipeline.py::_writer`의 `group_sync_ms` bounded gather·`_persist`/flush 비용, `_deliver_live`의 이벤트별 `RECEIVED`→`APPLIED` ACK 직렬 대기, `market_spool.py::MarketSpool.flush`, `local_engine.py::LocalMarketEngine.on_frame` Apply, `local_transport.py` UDS와 전략 공급 시간, storage sync/Cloud stall을 단계별 구분. 특히 Venue B silent 테스트에서 Venue A 지연·event count가 B 연결 여부에 종속되는 **전역 장벽은 FAIL**. 그룹 fsync 자체와 same-producer ACK 순서는 **필수 정합 경계라 무조건 제거 금지**. 특정 병목이 측정되었을 때만 기존 primitive의 bounded parameter 조정 또는 ordered pipeline 좁은 수정(후속 ACK/durable-order/replay parity 테스트 필수) 허용. 새로운 Worker·Broker-dependent Data Barrier는 금지. 하루 중 실제 서버 장중/24x7 재계측은 외부 Gate다.
- Queue age/depth/bytes/backpressure/owner permit responsiveness와 tail sample count, p99 bootstrap CI를 기록한다. `T0→T5`는 Action을 생성한 이벤트만 측정하며 **signal 없음** 이벤트는 `T1→strategy evaluated`로 별도 집계해 선택편향을 방지한다. 모든 이벤트가 즉시 주문을 내는 테스트만으로 일반 Market Apply 경로를 검증했다고 주장하지 않는다.

## ARCH-STRAT. 파일 기반 전략 Plugin과 운영 상태

**전략 = 개별 import 가능한 Python 파일 또는 책임 단일 Python package**로서 `on_event` + `on_order_event`의 **완전한 경제적 의사결정(venue/order_type/tif/failure_policy 포함)**을 소유한다. 계산 helper만 추출한 것은 미완성이다.

HEAD canonical loader: `vibetrading.strategies.load_strategy` 및 `src/vibetrading/strategies/contract.py`. **최종 canonical loader**는 `src/strategies/` 내 importable `strategies.load_strategy`(또는 실제로 검증된 동등 entry)이며 `vibetrading.*`를 런타임에서 참조하지 않는다. 최소 파일 계약:

```python
STRATEGY_ID = "unique_strategy_id"
SUBSCRIPTIONS = (...,)  # venue, product, instrument, data kinds; existing loader schema
WARMUP = {...}          # bar/seconds/data dependency coverage

def create_strategy(config=None):
    # 기존 로더가 허용하는 실제 서명과 일치하도록 구현
    return StrategyInstance()

class StrategyInstance:
    def on_event(self, event, data):
        # causal, deterministic decision; returns existing Signal/Intent contract
        ...
```

위는 **개념적 형태**이며 `contract.py`의 실제 함수 서명을 깨라는 지시가 아니다. 단일 진짜 로더 재사용. 중앙 Runtime의 `if strategy == 'C3'`/신규 `if strategy == 'KP'`로 전략별 경제 로직 분기 금지. C3와 KP/KD는 독립 알고리즘·입력 시점·History 조건을 지닌다.

- R05 `strategies/kr/c3.py`는 현재 `open_decision` helper, `kp.py`·`kd.py`는 단순 `decide` wrapper이다. **독립 전략 Plugin이 이미 완성됐다고 보고하지 않는다.** STR-01에서 명시적 등록/단독 실행·전략별 데이터 의존/스케줄/리스크 경계 보강.
- 전략은 설정에 `strategy_id`, immutable config hash, version, subscribe requirements, warmup history, account scope, execution mode, allocation, risk limit, action protocol version`를 선언한다. 등록/비활성·테스트에서 다른 전략 로직 변경 없이 동작해야 한다.
- **전략 파일이 주문 제출 타입과 실패 후 후속 Action까지 결정하되 API 호출 자체는 실행 엔진이 담당.** Shared state 직접 변경/HTTP order 직접 제출 불가. 함수형 순수 계산은 내부 helper로 사용 가능하지만 유저가 전략을 파일 단위로 독립 교체·확장할 수 있어야 함.
- `RESEARCH`와 `LEGACY_CANDLE` 자동 실운영 등록 금지. C3/KP/KD 경제적 결과/모의계좌 분리/시작 종가/매매 창 지킨다. 기존 KIS 실계좌 활성화 자동 변경 금지.
- Trusted cooperative callback과 Preemptible hard isolation 구분. 사후 콜백 타이밍 측정은 infinite loop를 막지 못한다. 여러 신뢰 전략 동작이 공식 지원이면 운영 정책에 cooperative assumption 표기, 비신뢰 전략도 지원할 경우 별도 bounded worker/process + 측정 필요.

## ARCH-EXEC. 주문·취소·체결·계좌 불변조건

### ARCH-EXEC-1. Mode, Account, Owner

- `PAPER`: fake account+matcher/ledger, no real broker mutation client instantiation. `MOCK/MOCK2`: KIS API의 명시적 mode; `TESTNET`: 실제 시험 Venue 환경; `LIVE`: 특정 Venue credential, 실제 계좌 승인. **PAPER ≠ MOCK ≠ TESTNET ≠ LIVE**.
- 계좌 key: `(venue, credential identity, account/subaccount, margin scope, environment)`로 실제 외부 계좌 owner 충돌 회피. Root SQLite lease는 비협조 프로세스/다른 호스트를 전부 차단하지 못하므로 서버 Cutover 시 구버전 process와 restart manager를 실제 중지.
- Each strategy maintains logical attribution; physical account writer one. Opposite virtual strategies cannot net pending worst-case exposure before fills. Reserve budget across all strategies; fee/funding known/unknown included. Account limits require identity match across registrations.
- Startup/resume: broker balances, working orders, unknown sent attempts, latest fill facts reconciliation, feed quality, epoch/kill/pause gate verified. Agent/Codex가 자동 live permit 설정 금지.

### ARCH-EXEC-2. Commit / Handoff / Lifecycle

```text
Validate mode/capability/identity/feed/time/risk/nonce
→ choose stable client-order ID
→ durable commit (Intent, Evidence, Reservation, Attempt, Epoch)
→ atomic final permit/fence (after commit)
→ submit to existing selected venue adapter
→ ACK / PARTIAL_FILL / FILL / CANCEL_PENDING / UNKNOWN
→ private event and REST reconciliation → terminal proof
```

- `ACK` != fill; cancel ACK != cancel terminal. Timeout after network handoff ⇒ UNKNOWN, reservation retained, no blind retry. Broker rejection/HTTP5xx/ambiguous response semantics classified per venue. Cancel/fill race and historical partial fills supported.
- Fill idempotency scope is venue-native trade/exec IDs; zero/unknown ID doesn't justify timestamp-as-unique. SQLite read-only reporting cannot hold write lock for long. Real fee/funding/rebate are distinct fact types.
- `halt/resume` epoch scope global/account/strategy; safe cancel risk policy not inferred from user intent flag. Broker mutation request requires owner permit; no duplicate live owner processes.

### ARCH-EXEC-3. Existing Venue Code Reuse Rules

- Bybit `fv_mm/venues.py`, `fv_mm/private.py`, `liquidity_mm/exchange.py` and `bybit_execution.py` contain functioning **testnet oriented** execution code. Preserve until proven unused; not automatically live-ready.
- GRVT `fv_mm` signed order+cancel+fill/funding/history and extended broker path exist. Remove duplicated code only if actual active caller and wire contracts are proven identical. Unverified client ID defect assertions cannot authorize rewrite.
- Binance older `services/execution/crypto_binance.py` is Spot; `perp_execution.py` Binance `/fapi` is separate Perp request mapping. Do not reuse Spot endpoint for Futures.
- Arcus `extended_brokers` has signed order; selected cancel/private reconciliation may need bridge. Hyperliquid SDK Signing/nonce composition exists but generic `perp_execution` with injected `Transport` is **not proof of production network runtime**.
- For selected path capabilities check `submit`, `cancel`, `status`, `open_orders`, `fill/fee/funding`, `positions/balance`, `private_ws` or safe bounded polling, `reconcile`, `reconnect`, `instrument_metadata`, client ID/nonce, rate limit, `reduce_only/TIF`. Missing => `BLOCKED`, not silent stub.

## ARCH-PERP. Perp Equity·Margin·Funding·PnL

- Product info must include venue/segment/contract multiplier/settlement currency, linear/inverse, quantity/tick, min order/position, fee schedules, margin/position modes, funding indices, account currency. 전략에 제공되는 `account_position`은 검증된 실제 계좌 Fact이고 `strategy_attributed_fill/position`은 별도 귀속 Projection이다. 두 값은 서로 다른 권위/의미를 갖고, 특히 netting 계좌에서 귀속 Projection을 거래소의 실제 독립 포지션으로 표현하면 안 된다.
- Real and Paper ledger are separate. Linear cashflow is different from inverse or Unified Portfolio Margin. If no precise mode-specific exchange formula available, reject unsupported mode rather than extrapolate.
- Long/Short sign, entry/exit fee, maker/taker, realized/mark-to-market, trade settlement funding payments, commission currency, margin reserve and available collateral, partial fill and reduce-only *must* be accounted in selected products. Paper mark/FX absent => PnL `UNAVAILABLE`, not zeros silently.
- Unreconciled account or inconsistent `marginBalance/withdrawable` => fail closed for new orders. Funding history pagination incomplete => known missing range; do not label all settled funding.
### ARCH-CROSS. 필수 Cross-Venue 그룹 실행 계약

- **독립 전략 호출**: `strategies/contract.py`를 사용하는 전략 파일의 단일 인과적 신호/명시 Group Intent가 서로 다른 Venue A의 BUY와 Venue B의 SELL 두 Leg를 만든다(역방향도 허용). `group_id/strategy_id/config_hash/source_available_at/deadline`와 각 Leg의 `venue/product/account/symbol/side/quantity/price/TIF/reduce_only/client_id`는 영속 식별 가능해야 한다.
- **필수 코드 지원**: 임의의 *지원 가능한* 두 Venue 실행 경로를 구성을 통해 연결하는 Group Coordinator. `CONCURRENT`는 각자 다른 권위 Owner에 bounded async dispatch하여 **첫 Leg ACK를 기다리지 않고 두 호출을 시작**한다. `SEQUENTIAL`은 명시 순서대로 전송한다. 실계좌 Pair가 선택되지 않아도 두 모드 코드와 로컬 테스트는 반드시 존재해야 한다. **가짜 Venue 두 개만 만든 계약 테스트는 부족**하다: 최소 두 개의 서로 다른 **기존 구체 Venue Adapter의 Mapping/Owner 경로**를 Fake Transport로 통과시킨다.
- **전략/엔진 책임 분리**: 상품 간 경제적 동등성, Leg 크기·단위 환산의 경제적 기준, 보유 재고 활용, 진입 Edge, Side/가격/수량/TIF/FOK·Park/동시·순차·실패 시 후속 Hedge 상품/수량/시점은 **Cross-Venue 전략 파일**이 결정한다. 엔진은 Group Intent의 risk/capability/owner preflight 및 전송·상태추적·원장·복구만 소유하며 전략 대신 매매 결정을 생성하지 않는다. **Preflight/영속성**: 양쪽 Owner·feed freshness·가격/수량 정밀도·잔고/예약·Leg별 최악 원장/계좌 위험·API 한도·명시된 보호 제한을 첫 mutation 전에 판정한다. 엔진은 Instrument 간 경제적 동등성이나 예상 hedge 비율을 판정하지 않는다. Group, 양쪽 intent/client_id, reservation, attempt, evidence를 durable commit. 한 Leg라도 검사 실패 시 임의 발송 0. 전체 체결은 절대로 원자적이라고 선언하지 않는다.
- **주문 상태**: ACK·Filled·Cancelled·Rejected·UNKNOWN은 각 Leg별 구분; cancel ACK만으로 terminal 처리 금지. latency/timeouts/unknown/callback/restart 시 blind resend 금지. 실제 체결 수량·계약 승수·수수료/펀딩·계좌/Leg별 위험 사실과 잔여 예약을 정규화·검증해서 전략에 반환한다. Delta-neutral 판정·잔여 포지션의 경제적 Hedge 양은 전략이 계산. `WAIT/HALT_AND_RECONCILE/CANCEL_REMAINDER/HEDGE_BOUNDED/REDUCE_ONLY_FLATTEN`은 전략이 명시적으로 발행한 후속 Action에 한해 실행하고 공통 리스크 검증으로 제한한다. 자동 Hedge 가격/수량 선택 없음. 다른 전략 주문에 영향을 주지 않는다.
- **복구**: Crash 이후 Group 상태·최신 각 Venue 주문/체결·예약 복원 및 Reconcile 후만 추가 Action. 불확실하거나 History가 단절된 Group은 `BLOCKED`로 남으며 이 상태의 신규 위험 주문 0. 기존 `PaperExecutionGroups`의 관측-only 소유권을 실행 기능으로 오인하지 않는다. 기존 `ExecutionOwner`의 offline fake 전용 경계도 실제 라이브 승인으로 승격하지 않는다.
- **승인 경계**: `CROSS_VENUE_CODE_COMPLETE` 및 `CROSS_VENUE_LOCAL_E2E_PASS`는 **필수**, 실제 `(venue_A,venue_B,environment,account_A,account_B)` Pair Activation은 **OPT_IN_EXTERNAL**. 실주문 허가·키·계좌·API 권한·환경 조회는 별도. Pair 미지정은 실제 실행 차단 사유이지 코드 완료 제외 사유가 아니다.



## ARCH-OPS. Reporting·Telegram·Observability

- `Telegram` stable prior `/trade`, numeric user/chat ACL, command inbox dedupe, TTL, epoch, single poller/outbox + retry-after, no automatic venue mutation authorization. Telegram/HTTP failure does not block market apply/owner cancel gate.
- Dashboard/Telegram PnL must label `ACTUAL/ASSUMED/PAPER/RECOVERY/UNVERIFIED`, price/FX/funding evidence timestamps, reporting freshness; `PAPER_SIMULATION` cannot appear as real brokerage realized return.
- Actual system metrics: per producer gap/last receive, age/queue bytes, apply/event CPU, RSS/cgroup, source version/hash, SQLite WAL contention, order handoff/ACK/fill, cloud backlog, spool retention, error budgets. High-frequency synchronous reporting database queries forbidden.

## ARCH-CAPACITY. 2vCPU/3.8GiB 성능 요구

| Metric | Original candidate target / judgement |
|---|---|
| **Local Python decision→send latency** | **필수 계측, 수치 비차단**: T0→T5 p50≤2ms/p95≤5ms/p99≤10ms는 참고 목표. 미측정 `NOT_RUN`; 목표 초과 자체는 FAIL/BLOCKED 아님. 구조적 대기·버그·안전 결함은 별도 판정 |
| CPU/event | 동일 trace 기준 기존 대비 -20% 목표. 미달이면 이유/측정 결과 기록, 안전 조건 훼손하지 않음 |
| p99/p99.9 E2E | 같은 입력·환경에서 baseline 대비 10% 이내 유지 참고 목표(충분한 표본 전제); 초과 시 원인 계측·보고, 초과 자체로 FAIL 금지 |
| aggregated memory | app 전체 peak ≤3GiB 목표; 두 버전 동시 전체 가동이면 중복 메모리 계획 |
| baseline load | 평균 CPU 2 vCPU의 70% 아래 여유 목표, feed burst 별도 |
| Parquet writer | rate μ≥1.8λ 후보, 15분 중단 후 30분 내 회복 후보 |
| Cloud recovery | μ≥2.4λ 후보, Cloud 24h 중단 후 24h 회복 후보; 디스크 여유 함께 확인 |
| NATS independence | 최종 후보에 NATS 없이 required collector→market→Paper/owner data route. 실제 최소 30분 outage 증명은 운영/모의 장시간 구분 |
| Queue/Storage safety | EIO, ENOSPC, receiver lag, 429, backlog, bounded TTL; silent drop and root disk spill 0 |

이 값은 기존 `refactoring_plan.md`의 **설계 목표**이지 현재 실측 완료 수치가 아니다. 실제 Preflight threshold·host observation이 없으면 `NOT_RUN`이다. p99들을 더하여 end-to-end p99를 만들지 않는다. 한 번의 기능 테스트가 수시간/72시간 실환경 가동을 대체하지 않는다.

## ARCH-TEST. 테스트 커버리지·Fault Matrix

| Test family | Scenario | PASS predicate |
|---|---|---|
| Packaging | wheel/editable out-of-repo import, `python -m`, Docker/Compose, dynamic import | 새 정식 import 모두 resolve, legacy aliases authorized only |
| Strategy | C3/KP/KD old input=old output; separate file registration; NEW/CANCEL/REPLACE + FOK/park + reject/fill feedback; WARMUP/REPLAY | 동일 경제 결정과 주문 ABI, file-only policy and action result callback, no accidental live |
| **MM P0 (기존 Strategy/Owner/Perp 경로)** | 전략 양방향 Maker→부분/늦은 체결, cancel ACK/replace 경합, 전략 귀속 fill·working order 및 실제 계좌 position/balance 제공, 전략만 reprice/hedge, crash/restart | 재고·호가·hedge 정책의 공통 엔진 구현 0, 사실 중복 0, stale/UNKNOWN 엄격 표기, 새 Action의 전략 함수 trace |
| Feed/Book | duplicate/out-of-order/gap/reconnect/schema/timestamp availability | exact native depth state or fail closed, no future data |
| **Multi-source No-Barrier(P0)** | A active/B silent; A active/B reconnect; cross-market identical symbol; 60s Cloud stall; first-event group-fsync wait; per-record transport ACK wait | A independent source apply/strategy delivery persists after A1 without B/Cloud waiting; B-dependent strategy fail closed only; no global join; per-stage receipt/time evidence (`ARCH-DATA-4`) |
| Spool | tmp corruption, truncated tail, rotated segment, fsync ENOSPC, retained prefix | no silent loss, exact cursor/prefix, bounded retries |
| Recovery | crash at WAL commit/owner handoff/Checkpoint/C+H/full restart | no duplicate submissions, latest accepted ledger remains latest |
| Paper | maker/taker/post-only/no-cross, partial/cancel, stale data, restart | fee/fill position once, external API calls=0 |
| Owner | two processes same account, unknown, lease drift, halt/resume | second owner blocked, no new risk until reconcile |
| Venue | selected product auth/signature client_id, ACK/fill/private disconnect, REST pagination, dynamically add/disable/remove, unsupported FOK/POST_ONLY | normalized facts accurate, no live on install, disable risk orders but read/reconcile retained, missing history detected |
| **Domestic Broker Extensibility(P0)** | Existing Kiwoom KR Cash `submit_order` + resolved status/cancel/fill/reconcile scripted path; KIS Mock2 parity; Dummy NH-like Broker add/config/disable/remove; cash unsupported fee/funding isolation | no new core/strategy code for Dummy Broker; Kiwoom selected route `NOT_IMPLEMENTED` 0; actual NH LIVE claim 0; TOSS DATA_ONLY order blocked; market feed selectable |
| Perp | fee/funding/collateral/partial/close/unsupported modes | algebra and manual expected values match, unknown mode rejected |
| **Crossvenue (필수 로컬 Gate)** | 전략이 상품 동등성/재고/Leg sizing 판단하며 엔진은 단위/권한만 검증, 한 전략 Group→A BUY/B SELL, CONCURRENT 두 호출이 서로의 ACK 전 시작, SEQUENTIAL 순서, A fill/B reject, 양쪽 partial/UNKNOWN, stale 한쪽 feed, private gap, 취소-늦은 체결, restart | **구체적 서로 다른 두 Adapter Mapping/Owner 경로 + fake transport E2E**, group/leg identity·예약·대조·bounded residual·hedge fail-close, 외부 mutation 0; pair 미활성 이유로 SKIP 금지 |
| Strategy isolation | slow/raise/block/queue overflow of one plugin | other Strategy & owner mandatory operations unaffected as specified |
| Cloud/GC | unreadable remote, wrong checksum, reader pin, lease, retention | last unique source delete 0 |
| Operational | Telegram 429/duplicate, Dashboard read pressure, disk full | hot path safety controls responsive, reporting quality visible |
| **Performance** | 2vCPU representative trace, A1 durable and SQLite commit, normal + burst + cloud stall, real Venue Adapter send seam | 동일 환경 histogram/spans와 병목·큐·동기 대기 분석 완료; p50≤2ms/p95≤5ms/p99≤10ms 수치는 참고 기록만. 측정 미실시는 `NOT_RUN`, 수치 초과만으로 실패 금지; 안전/내구/무제한 대기는 별도 FAIL |
| Release | preflight mount/auth/owner/image drift and rollback | no mutation before gate, frozen SHA, backups restorable |

## ARCH-DEPLOY. Single Release · Staged *Safety Validation*

- **ONE INSTALL**: 모든 로컬 변경을 고정된 Git SHA·image digests·config bundle로 빌드해 한 번의 서버 배포 작업으로 설치. 로컬 개발 커밋은 작은 단위. 반복 개별 stage 릴리스 불필요.
- **NOT ONE BLIND SWITCH**: 설치한 서비스 간 transactional atomic swap이 아님. preflight→safe halt old owner→install candidate blocked→read-only/shadow on real feed→same-account reconciliation→user-approved activation. 위험 주문은 user scope마다 독립 gate.
- **14 role sources**: 직전 기록된 배포의 각 컨테이너 image/config/mount/source SHA가 서로 다를 수 있다. 새 build가 운영 전체 배포와 같다고 가정하면 안 된다. release metadata script로 실제 서버 `runtime.yml`와 dashboard Compose 비교.
- **Unmountable/Missing external storage**: volume device marker/path check fail→운영 root disk fallback 쓰기 0. Spool→Parquet→Cloud lease/proof/retention이 충분하지 않으면 원본 삭제 불가.
- **Backups**: WAL consistent DB snapshots + restoration test + immutable rollback images. Code binary rollback에서 DB를 과거 상태로 되돌려 최근 체결을 삭제하면 안 된다. 롤백 여부는 최신 주문/포지션/UNKNOWN 상태가 지원하는 실행 버전과 호환되는지 검사 후 결정.
- **Report**: `release_verification.json`에는 `id/group/PASS|FAIL|BLOCKED|NOT_RUN|EXTERNAL_ONLY/reason/input_timestamp/frozen_sha/evidence_path`, `release_summary.md`에는 안전한 실행 결과와 남은 사용자 선택만. 실패 항목을 묶어 로컬 수정하는 것은 허용하되 P0 위험 발생 후 real mutation 테스트 연속 실행 금지.
- **Operational acceptance**: 실제 체결/시세/오류·장중/주말 관측/기본 리스크·자원 성능이 확보되지 않은 동안 `PRODUCTION_READY=false`. 실서버 API test와 live 주문은 사용자 명시적 승인 없이 실행 금지.

### ARCH-DEPLOY-1. Root Docker + deploy Overlays / Config / Runner (P0)

- 현재 GitHub HEAD 운영 입력은 **루트** `Dockerfile`, `Dockerfile.crypto`, `docker-compose.yml`, `pyproject.toml`, `main.py`, `run_strategy.py`, `scripts/build_release_images.py`와 **deploy** `deploy/crypto-profile.yml`, `dashboard-compose.yml`, `ls-market-profile.yml`, `release1-overlay.yml`, `Dockerfile.dashboard-timefolio`, `Dockerfile.crypto-ledger`, `vibetrading.service`, `vibetrading.cron.example`, `compose.env.example` 등이다. `deploy`만 수정하고 root 파일을 놔두면 Gate FAIL.
- `pyproject.toml`/새 wheel에 포함된 `src/{domain}`와 Docker `COPY`/`PYTHONPATH=/app/src`, `CMD`, Compose `command`, `env_file`, `healthcheck`, `.dockerignore`를 동일 frozen SHA 기준으로 검사. 구형 `Dockerfile.crypto`의 `COPY src/vibetrading /app/src/vibetrading`, Compose의 단일 `.py` bind mount, dashboard `python -m vibetrading.dashboard.*`, Timefolio monitor 명령, `build_release_images.py` source manifest, dashboard-timefolio chromium image 모두 수정 후보.
- 실제 선택 profile은 **NATS-free** 독립 구동. 구형 root Compose 기본 서비스 `nats`, `nats-init`, `depends_on:nats-init`, `NATS_URL`과 구형 profile을 무심코 지우지 말 것: rollback 이미지/compose/volume과 immutable read-only inventory로 따로 보존. new primary profile에서만 NATS 강제 의존 0을 정식 수락. `docker compose -f ... config --profiles`로 모든 실제 조합 검증하되 retired profile을 실서버에 자동 실행하지 않음.
- Timefolio 대회 주문 Runner는 전용 활성 서비스/스케줄이 실제 운용되면 새 `src/timefolio` command/import + env/secrets + 브라우저 설치를 포함해 release manifest에 선언한다. `deploy/dashboard-compose.yml`의 Timefolio **monitor** 컨테이너를 대회 주문 **제출** 컨테이너라고 착각 금지. `submit=false` 기본/explicit approval/arm 2조건 없으면 mutation 0; browser page 상태 UNKNOWN 처리.
- `./src/vibetrading/telegram.py` 등 Docker host bind mount의 old path 0; prefer full immutable code in image over bind-mounted mutable `.py`, 단 설정/데이터/credential volume mount 유지. /data device UUID/owner/lock/source truth/rollback provenance를 모든 역할별 preflight 및 negative tests로 기록.
- `docker compose config`만으로 성공을 인정하지 않는다. `docker build` image import + `python -m runtime.<selected> --help` 또는 실제 유효 CLI smoke + collector/spool/UDS/strategy/owner/Telegram/Timefolio entry tests + old path/static stale scan → PASS. 최종 실서버 기동/운영 검증은 별도 승인.


## ARCH-AI. Codex/OpenCode/FreeLLMAPI 토큰 절약·권한 계약

- **Codex**: PM, 계약 충돌 해결, 위험 검토/최종 Acceptance. *일반 파일 이동·패치·테스트 직접 수행 최소화.*
- **OpenCode**: selected ticket 구현/테스트 담당. 사용자 지정 Worker 우선순위는 GLM → OpenCode direct DeepSeek → FreeLLMAPI Gemini 등이다(`01` 및 `AGENTS.md`의 exact ID 참조). Codex가 실패/부분 변경을 검토하고 전환을 명시·기록한 후 다음 순위를 호출한다. Worker는 단일 검증된 provider/model에 고정하며 자체 자동 전환·무제한 재시도·자기 수락은 금지한다. 카탈로그 등재만으로 실호출·tool calling·테스트 완료를 주장하지 않는다.
- **Deterministic**: git mv, AST/LibCST codemod, import graph, path coverage, Git file hash, test commands, CPU/PSS measurement, JSON schema gating은 Python/tools 사용.
- **Context policy**: 각 구현 Session은 `02`의 한 티켓, `03`의 관련 ARCH ID, 최대한 직접 소스/테스트만 읽음. 사용자 비밀키·운영 파일/생산 계좌 환경 접근 금지. 같은 코드베이스 전체를 매 세션 재독/전체 계획 재작성 금지.
- **Git artifact policy**: 단일 05 Machine Status, 04 Completed Manifest, 3 Core docs. Historical receipts/oracles는 immutable archive; 새 원시 stdout/junit/LLM transcript는 기본 ignored local/CI object. 지나치게 큰 history를 모델 context로 주입 금지.

## ARCH-ACCEPT. 최종 완료 선언 규칙

아래 조건을 **각각 별도로** 기록한다. 명세에서 `COMPLETE` 단어 하나로 합치지 않는다.

1. `REPOSITORY_CONSOLIDATED` — 77 top-level path accounting + 8 subpackage map + root exceptions + actual `git diff`/full package tests, legacy/doc hygiene, **Ponytail Skill의 실제 review·SIM-01 적용 결과·중복 권위 0 또는 사유 있는 예외**.
2. `NATS_FREE_LOCAL_CORE_COMPLETE` — 기존 TX12 로컬 수락과 신규 SHA에서의 data/Paper/restore suite.
3. `ORIGINAL_PLATFORM_REQUIRED_FEATURES_COMPLETE` — **STR-02 strategy action-feedback + EXEC-01 common owner router + VEN-01 dynamic Venue lifecycle + CAP-05 Cross-Venue 코드+로컬 Group E2E 필수 PASS** 및 CAP-00 selection이 확정되고 모든 REQUIRED route & conditional-selected features가 local functionality/code/test PASS. MM P0의 전략 중심 양방향 주문·재고/체결 피드백·취소/늦은 체결 복구 역시 기존 STR-02/EXEC-01/CAP-02/CAP-03/QA-01로 검증하며 별도 MM Engine 신설을 요구하지 않는다. Missing optional product가 구현 안 됐다는 이유만으로 fail은 아니지만 원래 필수인 기능을 임의 OPTIONAL화해 PASS 금지.
4. `FULL_LOCAL_REGRESSION_PASS` — 실제 최종 SHA Linux tests/CLI/build/credential-free suite 실행 완료.
5. `SINGLE_RELEASE_PACKAGE_READY` — frozen digest, deployment preflight/rollback script+simulation, exact server mount config and safety policy prepared.
6. `SERVER_DEPLOYED` — 실제 서버 immutable candidate 설치+old owner 경계 검증, 외부 승인 별도.
7. `OPERATIONAL_VALIDATION_PASS` — 실제 장중·장외/24x7 feed/data/account/order/resources/recovery and required venue authority observations; 리포트·Gate 증거 보유.
8. `LEGACY_RETIRE_COMPLETE` — 명시적 후속 승인과 retained/rollback/data condition satisfied. 7의 완료만으로 자동 삭제 금지.

**변경 원칙**: Hard safety invariant(중복 주문 없음/UNKNOWN retain/원본 무삭제/TOSS DATA_ONLY/KIS 모드·strategy economics)은 새 티켓 상태를 빠르게 채우기 위해 절대 완화하지 않는다. 기본값은 `BLOCKED_OR_UNVERIFIED`, 증거로 PASS를 획득해야 한다.

**주의:** 위 `ARCH-ACTION` 주문 타입의 Python dataclass 스케치는 설계 계약 예시이다. 기존 코드에 존재하지 않는 모든 class/signature를 이미 구현됐다고 보고하지 않는다. 구현자는 02의 `EXEC-01` 수락 시 실제 코드 import와 실테스트에 근거한 형식을 명세에 확정한다.


## ARCH-FEED-VENUE — Venue-Owned Feed Files + Reused Ingestion Contracts (P0, 2026-10-25 19:55 KST)

**경제 로직을 수집기에 추가하지 않고, 실제 거래소별 API 책임을 파일 단위로 식별한다.** 목표 `src/feeds/providers/{binance,bybit,bitget,grvt,arcus,hyperliquid,kis,kiwoom,toss}.py`는 **지원되는 기존 프로토콜만**을 소유한다. Bitget은 현재 수집 코드의 과거 지원 범위를 확인 후 현재 Config에서 `DISABLED`일 수 있으며 자동 활성화 금지. 신규 거래소는 `providers/<new_venue>.py`를 추가하고 선언적 등록(기존 Factory/Config)을 사용하는 것이 원칙이다. Spot/Perp/상품 타입은 provider 파일이 아니라 `(venue,segment,instrument,channel)`로 구분하되, 같은 Venue의 실제 API가 크게 다르면 `<venue>_spot.py`, `<venue>_perp.py` 등의 최소 소유 분리는 예외로 인정한다.

**소유권과 이전 표**

| HEAD source | 정확한 source-level 처리 | 최종 권위 |
|---|---|---|
| `src/vibetrading/providers/crypto_perp.py::CryptoPerpCollector` | Venue별 run/subscribe/transport/signature URL, native message decode, sequence/gap 조건을 `bybit.py`/`bitget.py` 등 실제 지원 Venue별로 이전; 공유 `_instrument`, book normalization은 공통 helper로 단일화 | Venue-specific file only; 기존 다중분기 Collector는 호출 0이면 제거 |
| `src/vibetrading/providers/extended_perp.py::ExtendedPerpCollector` | Arcus/GRVT native subscription, snapshot/delta/gap 규칙을 `arcus.py`/`grvt.py`로 이전 | `arcus.py`, `grvt.py` |
| `src/vibetrading/providers/binance.py::BinanceCollector` | 기존 TradFi Binance 종목 범위·WS 타이밍/시퀀스/시간대·bar 규칙 보존. 일반 Binance Perp와 실제로 같은 의미/transport인 부분만 공통화 | `binance.py` (상품별 동작 명시) |
| `src/vibetrading/providers/hyperliquid.py` | 현 read path, unsupported product 경계 보존 | `hyperliquid.py` |
| `src/vibetrading/providers/kis.py`, `kiwoom.py`, `toss.py` | 데이터 수집 기능 유지. 특히 TOSS bar-end timestamp 및 실제 API 필드·Data-only 유지 | `kis.py`, `kiwoom.py`, `toss.py` |
| Shared book/time/emit funcs | Venue 분기문 없는 안정적 코드만 공통 `base.py`/기존 유틸로 재사용 | 하나의 공유 구현만 |

**FEED-VENUE 불변조건**
1. Provider는 **데이터 전용**이다. `feeds/providers/nh.py`가 없더라도 NH Broker 주문 Adapter가 정상 실행될 수 있다. Feed와 Execution은 `feed_enabled`/`execution_enabled`로 독립 구성한다. `feed_enabled=false,execution_enabled=true` 지원 필수이며, `feed_enabled=true,execution_enabled=false`인 TOSS도 필수 지원한다.
2. Source별 연결/구독/파서/원시 순서/Book 복구 실패 시 **해당 Source만 INVALID**. 동일 마켓 원본 타 Broker 수집은 전략의 config alias로 선택하지만, `KIS 호가 = Kiwoom 호가`를 무조건 동일하다고 가정하거나 양쪽의 native ID/시각을 혼합하지 않는다.
3. Venue별 Provider가 공통 Spool/UDS/MarketState를 2개 이상 만들거나, 전체 Venue snapshot 동시 수신을 대기하거나, raw tick을 pandas DataFrame으로 가공해 전략에 강요하면 FAIL.
4. **TOSS market-data path is REQUIRED_KEEP_DATA_ONLY**. 주문 mutation 경로는 없고 `order_status` 등 역사적 조회를 주문 가능 증거로 사용하지 않는다. 기존 KIS/Kiwoom/TOSS bar labeling과 종가 가용 시점은 Golden으로 보호한다.
5. 모든 현재 활성 `(venue,segment,channel)`에 대해 기존 vs 변경 후 이벤트의 count, `native_seq`, `event_ts`, `receive_ts`, `available_at`, precision, book state digest, gap behavior를 결정적 fixture로 대조. Venue 단위 one-writer/state에 관한 외부 Side Effect 0. Data subscription 성공은 별도 외부 환경 증거다.

## ARCH-OBS-DASH — Live Latency and Per-Strategy Net PnL Projection (P0)

**기존 자산 재사용:** `dashboard/`(프런트엔드 및 API route), 기존 `dashboard/db`, 과거 `src/vibetrading/dashboard/{bridge,projection,telemetry,accounts,timefolio_monitor}.py`를 이동한 `src/operations/dashboard/` 모듈, `execution_ledger.py`, `portfolio_ledger.py`, `trade_reporting.py`를 최초 재사용 후보로 둔다. `dashboard/app/api/pnl/route.ts`, `state/route.ts`, `orders/route.ts`의 현 계약과 프런트엔드 배포 위치를 증거로 확인한다. 별도 Prometheus/Grafana/Kafka/Timescale/Redis/분산 Metrics Pipeline은 금지; 입증된 불가피한 결함 없이는 도입하지 않는다.

**OBS-01 Latency**
- 최소 Span: `provider_receive→normalized`, `ingress_queue`, `group_wait`, `spool_fsync`, `UDS_delivery_ACK`, `market_apply`, `strategy_callback`, `owner_preflight`, `durable_order_commit`, `adapter_send_begin`; 외부 `WAN`, `ACK`, `fill`은 별도. `timestamp_ns`는 프로세스별 monotonic; 프로세스 간 절대 monotonic 차이를 산술하지 않고 trace/cursor ID로 결합하여 **단계별 프로세스 내 측정치**를 보여준다. `T0→T5` 전체 측정은 통일된 clock domain에서 실제 측정 가능할 때만 표시하고, 불가하면 `N/A + segmented` 표시. p50/p95/p99, n, 최근 1/5/15분, Queue oldest age, backlog, drop/reject, durability lag, feed freshness, cgroup RSS/CPU/IO pressure. 미관측과 0ms 구분.
- Hot path emits **bounded, nonblocking** observation to existing `operations` read-side: sampling/aggregate with overflow counter; no mandatory synchronous WebSocket/HTTP/DB/JSON dashboards before order send. `monitor failure` does not cause new order submission, does not block trading; data freshness warning. No raw secret/account credential, user PII or full orders to public telemetry.
- UI server events over `SSE` **or** bounded WebSocket; fixed cadence 1~5s (initial configurable 2s) and server-side aggregation. No tick-per-frame push to each viewer. Reconnect/long-disconnected clients read latest snapshot and bounded historical data; queue control enforced.

**OBS-02 P&L Strategy panels**
- Tabs are keyed by stable `strategy_id` + `account_scope` + mode (PAPER/MOCK/TESTNET/LIVE). Tabs generated from active/retained registry rather than code-defined strategy names. Each tab reports **Net P&L** (realized + unrealized - fee impact + funding, conventions explicit), cumulative equity/P&L graph, intraday/session P&L, strategy positions, working orders, last update/as-of, trade count, state, realized/unrealized/fees/funding/currency fields. Paper and Live never aggregate into the same curve. Net realized already including fee/funding MUST NOT double-subtract fees. Explicit `pnl_method_version` and currency/conversion as-of; if FX missing use N/A not fabricated universal KRW total.
- Strategy-specific PnL requires **event-sourced allocation facts**, not a ratio of pooled account total. If shared-account external fills/funding/cash transfers cannot be deterministically allocated, display `UNALLOCATED` in account tab, strategy curve `INCOMPLETE`, not a false positive net return. Deposits/withdrawals and instrument notional transfers are excluded from trading P&L via cash-flow adjustment. Mark-to-market `price_source`, timestamp, staleness; stale marks freeze/flag unrealized PnL. PnL graph must replay after restart without double count, across duplicate/late fills and correction events, no retroactive rewinding of ledger.
- Dashboard API is **read-only**. No live `OrderOwner` import/mutation, no credentials, no bypass of actual `StrategyRuntime`; telemetry is derived from authoritative persistent execution/portfolio records. Frontend deletion/refresh cannot affect orders. Historical length and downsampling bounded, default 1s/2s current and 1m aggregates for longer periods; configurable retention explicit.

**Required OBS acceptance**: deterministic scripted 2 strategies × 2 venues; fees/funding/partial-fill/late fill; restart/replay; one strategy stopped; account shared unallocated fill; stale mark; dashboard disconnect 1m; verify curves numeric manually with independent calculations and data source hashes; 99th-percentile hot-path impact measured but nonblocking latency targets; dashboard backlog bounded and trading unaffected.

## ARCH-WEB-ACCESS — Authenticated HTTPS Read-only Dashboard (P0)

- User goal: desktop/mobile remote URL with **no SSH tunnel required**. **Public IP alone ≠ safe HTTPS dashboard**. Reuse current `dashboard/` deployment path with one small reverse proxy (prefer already installed ingress; only if absent Caddy/Nginx container with pinned image). Preferred: registered DNS A/AAAA → server Public IP, TLS certificate automated (port 443; port 80 for HTTP challenge only if needed), host allowlist/HSTS/safe headers, authenticated viewer, short sessions and secure cookies, rate limiting at ingress or app. Optional private overlay-network URL if public TLS/domain cannot be approved. No HTTP plaintext with credentials on public port.
- `DASHBOARD_URL`, TLS hostname, backend host/port, permitted origins, auth method, cookie/session key and proxy settings in `deploy/` and `config/`. **Application listen only on 127.0.0.1 or Compose private network**, not `0.0.0.0` with anonymous port exposed. Cloud ACG + host firewall open 443 only to intended audience where feasible; SSH unchanged/private; NATS/UDS/spool/DB/Prometheus/metrics/Telegram Bot admin endpoints MUST NOT be exposed publicly.
- Existing `dashboard/app/api/{accounts,ingest,orders,pnl,state}` must be audited independently. A public viewer token is read-only; `POST/PUT/PATCH/DELETE` that can mutate accounts/settings/orders/ingest keys blocked/isolated. `/api/ingest` if still required is private authenticated service-to-service on internal network; **do not expose under public route**. Per-route authz, CSRF/session, auth failure 401/403; logs redact tokens, keys, positions if configured. Browser Timefolio credentials/browser-control interface must not be public.
- PUBLIC URL must not give execute/order APIs; external mutations 0 during release test. Threat scenarios: unauthenticated GET, stale/expired cookie, bad Origin, brute force, host header mismatch, reverse-proxy misrouting, dashboard/bridge crash, certificate renewal failure, low-memory restart, secret accidentally served. Validate before CUT-01 on loopback with scripted proxy, then production external TLS/auth by explicit approval.

## ARCH-EXEC-DATA-SPLIT — Execution-only Broker Route (P0)

- `DataSourceAlias` and `ExecutionDestination` are **independent**. A strategy using KIS/Kiwoom quote may route an order through `NH(Namu)` if `execution.venues.nh` is explicitly registered, authenticated, account-scoped, and that product/environment is verified; no NH feed implementation needed. Do **not** assume market sessions, ticks, pre/post market eligibility, ETF products or source timestamps of two brokers are identical. Strategy validates input freshness and instrument mapping; execution adapter validates supported exchange/product/order schema, actual tick/lot, limit, buying power; both follow existing `ActionPlan`/Owner.
- Capability config matrix: `feed_enabled`, `execution_enabled`, `venue`, `product/segment`, `account_scope`, `environment`, `symbol_alias`, `source_feed_ref`, `order_destination`, `asof`, risk limits. `ORDER_ONLY` adapter registers `submit/cancel/query/fill/reconcile/positions/balances` but **does not subscribe market data or create an orphan Feed**. `DATA_ONLY` TOSS registers market feed but **never mutation permit**. Full `BOTH` is supported, `NEITHER` rejected/disabled.
- `NH(Namu)` currently **FUTURE_NOT_IMPLEMENTED**, not fake simulated Live. For offline acceptance create `DummyNH` with scripted API facts; live scope requires official API existence/eligibility, credentials and permitted order modes. Do not invent NH endpoints or promise access. `KIS feed → DummyNH order → ACK/fill → strategy`, `Kiwoom feed → DummyNH`, and `TOSS feed → KIS paper` are mandatory local route tests. Reverse association, restarts, stale feeds and unknown outcomes fail closed.

## ARCH-KR-BREADTH — Optional KOSPI200 + KOSDAQ150 (350 stocks) Minute/Daily Collector

**KR350 is an explicit opt-in capability, not an assumption that 350 real-time WebSocket subscriptions or vendor entitlements already exist.** Do not auto-enable on production. Build offline validated bounded polling/schedule code; turn on only after chosen provider supports product/entitlement/call budget. Universe daily point-in-time membership for actual daily collection (not historical current-membership survivorship bias). Resolve duplicate constituents and delistings, KOSPI200 and KOSDAQ150 each 200/150 names; maximum 350 distinct active unless overlap reduces it. Daily stock changes versioned and feed source documented.

- Modes `BAR_1M`, `BAR_5M`, `BAR_30M`, `BAR_1D`; no L2/Trades ×350, no full-depth, no per-bar DataFrame transforms in hot path. **Only 1m and 1D collection is strictly required**; 5m/30m derive offline deterministically if needed from 1m bars (holiday/closing/frequency boundary aware) to minimize vendor calls. User said minute-to-daily, avoid inventing 2-second bars. Separate execution-critical small-universe realtime collector priority from wide-universe low-priority backfill/polling; archive/partition Parquet per date/universe/version; cloud upload async. Enforce `available_at` and end-label, no fabricated ffill bars, stale data metadata.
- Quantify provider eligibility and rate-limit at decision time with official docs; 2026-04-20 KIS published 18 req/s live, 1 req/s mock and 41 WS registrations/session (subject to live changes). 350/60=5.83 single-symbol minute requests/s on average before retry/order/headroom, but batch/paginated endpoint behavior may differ. Dynamic shared token budget reserves headroom for trade/account APIs; REST order priority with bounded backoff; no arbitrary extra appkeys or rate-limit evasion. 350 WS with KIS one appkey is unsupported at stated limit. Alternative vendor/provider must be configured and validated, never assume.
- 350×390 trading minutes=136,500 1m bar rows/day; about 34.4 million/year at 252 sessions (illustrative). `bytes/day=rows×measured_bytes_per_row` and raw/replay/Parquet copies, checkpoint, retention and cloud capacity must be measured. Target no high-rate per-symbol process, no 350 Python workers; use bounded concurrency + limited provider sessions and same spool/UDS pipeline.
- On 2vCPU ~3.8GiB host wide-universe only if effective live memory, CPU throttling, oldest age, exporter/disk quota and API budget pass representative paper workload while MM/ETF hot path unaffected. Staged enable 10→50→150→350. Separate category `EXTERNAL_VALIDATION_PENDING` until actual provider/API and server soak verified. 
