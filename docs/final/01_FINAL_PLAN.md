# VibeTrading — 최종 리팩토링 실행 계획 (Consolidated Baseline v2 — 2026-10-25)

> **문서 역할**: PM/Tech Lead가 **왜·무엇을·어떤 순서로·어디까지** 완성할지 결정하는 유일한 실행 계획. 작업 절차와 파일·함수·테스트는 `02_DEVELOPER_TICKETS.md`, 전역 계약·지원 범위·안전 불변조건은 `03_ARCHITECTURE_REQUIREMENTS.md`, 과거 검증 증거는 `04_COMPLETED_MANIFEST.json`, 진행 현황의 단일 쓰기 원천은 `05_TASK_STATUS.json`에 있다. 이 5개 외의 옛 계획서는 **읽기 전용 역사 자료**이며 현재 지시 권위가 없다.
>
> **관측 기준**: GitHub `PJH-02/VibeTrading` `main` HEAD `2c1a82a52a6a2eed0c304f324f6c49e2987edfac` (2026-10-25 13:13 KST 머지). 관측 이후 코드·설정·서버가 변할 수 있으므로 실행 시 HEAD 확인. 기존 로컬 마지막 수락 제품 SHA `001d10d20f1dce58fcb0349983f368af10a2bdc1`; HEAD와 같은 값이라고 주장하지 않는다.
>
> **현재 증거**: VN/TX 51행 중 LOCAL_COMPLETE 38, INCOMPLETE 10, NOT_STARTED 3; R00–R05/O00은 기존 로컬 PASS, O01은 기존 `REWORK_REQUIRED`, R06 미착수. 가장 최근 선택 로컬 회귀 2,480 PASS / live-data 2 deselected. **이 진척은 최소 코어 로컬 계약**에 한정하며 최초 기능 전체·서버 배포·운영 검증을 의미하지 않는다.

## 0. 절대적인 프로젝트 명령

1. **최초 목표 복원**: 기존 운영 코드를 재사용해 NATS 제거, 로컬 spool/UDS/shared state/Parquet/cloud, 개별 전략 파일에서 데이터→시그널/주문 의도(venue/account/side/가격·수량/TIF/maker·taker/park/FOK/대응 정책)를 결정하고 공통 실행 Owner에 전달하여 PAPER·MOCK/TESTNET·승인된 LIVE에서 동일 전략 의미를 실행한다. 기존 C3/KP/KD ETF 경로를 보존한다. 거래소 간 실행 및 Perp 계좌 회계는 처음 의도한 이용 사례에 필요한 만큼 지원 여부를 명시하고 빈 기능을 `PASS`로 치지 않는다.
2. **Repository Consolidation 필수**: 기존 HEAD의 `src/vibetrading/**`를 정식 목표 `src/{core,feeds,market,storage,strategies,execution,portfolio,runtime,operations,timefolio}/**`로 이행한다. `src/vibetrading` 호환 패키지는 최종 릴리스에 남기지 않는 것을 원칙으로 하되 미이전 활성 caller가 있으면 Gate BLOCK하고 삭제를 강행하지 않는다. 최종 *역할별 모듈 배치·권위 단일화·레거시 분리·증거/로그 Git 추적 정리*까지 완성한다. 파일 수 감소만을 성공 지표로 삼지 않는다.
3. **No greenfield + No forced legacy retention**: 기존 활성 ETF C3/KP/KD 및 Timefolio 대회 주문 로직을 보존한다. `fv_mm`/`liquidity_mm`/`fv_pilot`/`mm_state`는 활성 운용 목표가 아니라 기능 재사용·증거가 필요한 과거 묶음이다. 완성된 새 공통 경로에 필요한 코드/테스트만 추출한 후 활성·시험·CLI·배포·롤백 의존이 없으면 최종 Release source/image에서 제거한다. 기존 `etf_trading.py`, `etf_journal.py`, `fv_mm`, `liquidity_mm`, `bybit_execution.py`, `extended_brokers.py`, `perp_execution.py`, `paper_execution.py`, `portfolio_ledger.py`, `storage_backend.py`를 첫 번째 재사용 후보로 둔다. 선택된 함수와 호출자의 **입증된** 공백만 최소 수정한다. 기존 완성 작업을 재구현하지 않는다.
4. **한 개 릴리스**: 로컬의 여러 작은 커밋/테스트로 개발하되 운영 서버에는 최종 고정된 SHA의 **단일 Release Candidate**를 설치·전환한다. 한 번의 설치≠무검사 일괄 활성화; 프리플라이트→Blocked/Shadow→안전 검증→승인된 활성화→Rollback 대비는 필수다. 모든 운영 오류를 최초 한 번에 찾아낸다고 약속하지 않는다.
5. **경계**: 실제 거래 주문/취소/계좌 변조, 원본 운영 DB·NATS·Spool 삭제, 클라우드 원본 회수 및 서버 전환은 사전 명시적 승인 전까지 불허. 코딩 작업에 비밀키·실계좌 식별자·운영 Volume을 전달하지 않는다.

## 1. 최초 목표와 현행 축소 완료의 차이

| 목적 / 계약 | 현재 증거 또는 기존 구현 | 신규 완료 판정이 필요한 부분 |
|---|---|---|
| KIS·Kiwoom·TOSS 및 Crypto 실시간 수집 (TOSS 주문 DATA_ONLY) | 기존 Collector, `providers/`, 로컬 Data Path, TOSS DATA_ONLY 보존 | 활성 Producer별 **독립 수신→최소 정규화→A1 내구 기록→상태 적용→전략 통지**. 다른 Venue 이벤트·전역 watermark·Parquet/Cloud flush를 기다리지 않음. Native sequence/시간/원시필드 보존, TOSS 시세는 선택된 정식 수집 경로로 활성화·유지하고 주문만 금지 |
| NATS-free 시장 데이터 | `local_pipeline.py`, `market_spool.py`, `local_transport.py`, `local_engine.py`, `local_runtime.py`와 VN 로컬 PASS | 대표 운영 입력·스토리지·Parquet/Cloud 진입점의 실환경 정합성, 서버 NATS 비의존 실증 |
| **전략 파일이 의사결정 전체 소유** | R04 Loader + VN13/TX05 Paper Plugin; R05 C3/KP/KD는 helper만 | **C3/KP/KD 및 새로운 crypto/주식 전략**에서 데이터→주문 Action(`venue/account/side/qty/price/order_style/TIF/park/FOK`)→결과 콜백→전략 결정에 의한 다음 Action까지 파일 단위 소유. PAPER와 대상 실계좌 실행 연결도 필수 검증 |
| 기존 ETF C3/KP/KD·Mock/Mock2 | 기존 `EtfTradingLoop`/저널과 Golden 테스트 | 새로운 데이터 경로에서 의미·체결·계좌·지연·복구 동등성, 실서버 실행 역할별 검증 |
| 전략별 Paper | `strategy_runtime.py`, `paper_execution.py`, `portfolio_ledger.py`, TX04/TX05/TX08 PASS | 실제 여러 플러그인·동시 계좌·restart stress·격리 한계, live mutation 0 증명 |
| **확장 가능한 Crypto Venue + 국내 증권사 Broker** | 기존 Binance Spot/5 Perp 경계, KIS Cash Mock, `brokers.py::KiwoomBrokerAdapter` 주문 제출(조회 `NOT_IMPLEMENTED`), Bybit/GRVT 실행 코드 | **신규 Crypto Venue 또는 국내 증권사 Broker를 별도 Adapter 모듈+선언적 Config로 추가**, 공통 Router/전략 코드 변경 없이 주문·취소·조회·Fill·계좌/Reconcile 연결. **기존 Kiwoom 국내주식 주문 경로는 실제 코드 기반 Local scripted E2E로 검증하고 부족한 취소·조회·체결 경로를 보강**. NH 등 미구현 Broker의 새 API는 향후 개별 Adapter 개발 후에만 지원 선언. TOSS 시세 수집은 필수 보존, 주문 DATA_ONLY, 실거래 활성화는 외부 승인 |
| Perp 포지션/마진/펀딩 | `perp_execution.py`, 기존 런타임·Paper journal 일부 | 실제 선택 포지션 모드, 부호·수수료·funding·예약, 계좌 reconciliation, unsupported 모드 fail-closed |
| 동일 계좌 복수 전략 | TX02/TX08 *Paper 협조 계좌* 수락 | 실거래 Owner·Subaccount·이중 프로세스 소유권은 별도 증명; Paper PASS는 Live PASS 아님 |
| **Cross-Venue Arb/MM 실행 (필수 코드 기능)** | 기존 `execution_groups.py`는 PAPER 관측만 담당; TX08B 실행 미완성 | **CAP-05는 거래소 쌍 선택과 무관하게 필수 구현·로컬 수락**. 전략 신호 1개 → Venue A BUY / Venue B SELL의 두 독립 주문, 동시·순차 제출, Leg별 ACK/체결·부분 체결·UNKNOWN·취소 및 재시작 복구. **상품의 경제적 동등성·Leg 크기·재고 활용·잔여 노출에 대한 매매 대응/헤지 Action은 전략만 결정**. 공통 엔진은 실제 체결·계좌·예약·정량적 안전 한도 및 상태 사실을 제공한다. 특정 실계좌 Pair만 opt-in; 외부 승인 전 실주문 0. 두 거래소의 원자적 체결은 불가능 |
| 데이터 내구성·복구·GC | VN07–VN09 *로컬 검증·GC dry-run* 완료 | 서버 Checkpoint/retained prefix/cloud readback 및 용량/성능, 실제 삭제는 후속 별도 승인 |
| 운영 제어·보고 및 Timefolio 대회 주문 | VN14/VN15/TX09 완료, `timefolio/client.py`, `timefolio/runner.py`, `dashboard/timefolio_monitor.py` 존재 | Telegram/대시보드 복구와 Timefolio 대회 주문 제출(`submit=false` 기본, 승인/계좌별 arm 필요)·조회·모니터링·NATS-free 전환을 명시적 기능으로 보존 |
| **Python Hot Path 효율성 관측** | 2 vCPU/약 3.8 GiB, 기존 latency 일반 목표·로컬 Paper 중심 | **시세 Python provider callback 수신(T0) → 전략 Action 생성 → 권위 Owner의 adapter.submit 호출 시작(T5)**까지 실측. 대표 부하 T0→T5 p50 ≤2ms/p95 ≤5ms/p99 ≤10ms는 **최적화 참고 목표(비차단)**이며 초과 자체는 티켓·릴리스 FAIL/BLOCKED 사유가 아니다. 수집→내구 기록→전략 전달→주문 제출·관리 루프에 불필요한 동기 대기·중복 변환·무제한 큐가 없는지를 계측/분석한다. 필수 영속 커밋은 보존하고 WAN/거래소 ACK·체결 지연은 분리 보고한다 |
| **Flat src + 역할별 코드 구조** | HEAD `src/vibetrading/`에 루트 77개 및 패키지 다수, root-level 구 `strategies/` 등 동명 패키지 | `src/` 바로 아래 `core/ feeds/ market/ storage/ strategies/ execution/ portfolio/ runtime/ operations/ timefolio/`; wheel/install/CLI/Compose/Timefolio 입증, `vibetrading` 런타임 패키지 잔존 0, 중복 권위 정리 및 Ponytail 적용 |
| 문서/아티팩트 경량화 | HEAD 당시 추적 파일 1,714개; docs 993개, runs 774개 | 원시 반복 실행 로그를 Git 기본 추적에서 제외하고, 핵심 Receipt·Golden·장애 분석/감사를 보존 |

**Scope 결정 규칙**: **Cross-Venue 실행 CAP-05는 선택 전략의 존재 여부와 관계없이 REQUIRED_CORE_CODE이며 `NOT_APPLICABLE_APPROVED` 또는 조건부 티켓으로 대체할 수 없다.** 어떤 거래소 쌍을 실제 계좌에서 켤지는 독립된 운영 설정/승인 사항이다. 지원 범위가 역사 문서에 보인다는 것만으로 모든 거래소·모든 상품·모든 환경을 새로 구현하라는 뜻은 아니다. 하지만 사용자 목표에 해당하는 **선택된 경로**가 코드/로컬 테스트상 미완성인데 단순히 `OPTIONAL`로 내려서 프로젝트 완료라고 주장해서도 안 된다. `03`의 Capability Matrix에 `REQUIRED / CONDITIONAL_IF_SELECTED / UNSUPPORTED / EXTERNAL_ONLY`와 현 지원 상태를 분리한다. 기본 다섯 Perp 코드 지원 및 crossvenue 이용 사례는 사용자 명시적 제외 승인 없이 임의 삭제할 수 없다. 미확정 상품·계좌 선택은 `DECISION_REQUIRED`로 남겨 전체 플랫폼 완료 판정을 금지하되 이미 완료된 최소 코어는 보존한다.

### 1.1 이번 보강의 비확장적 범위 고정 (Data Independence + Broker Extensibility)

- **DATA-01 — No Cross-Source Barrier(P0)**: `provider/WS callback`이 받은 한 Venue의 정상 Market Event는 **그 Venue의 generation/sequence 및 A1 durable/market apply 경계만** 거쳐 전략에 공개한다. 동작하지 않는 다른 Venue, 느린 Feed, 모든 거래소의 공통 Candle 종료, 글로벌 `asyncio.gather(all_sources)`/join/watermark를 기다리는 수집·정규화/공유 상태 갱신은 금지. 전략이 Cross-Venue 최신성/동등성을 판단할 때 사용하는 다중 Source Snapshot은 전략의 판단이며 Producer를 정지시키지 않는다. 다른 Source의 데이터가 오래됐을 때 해당 전략의 위험 주문은 차단될 수 있지만 정상 Source 수집은 지속돼야 한다.
- **DATA-02 — A1 내구성 유지(P0)**: 최소 필드 정규화는 `(source, market_segment, instrument, channel, generation, native_sequence)`별 필요한 검증/변환만 수행. `APPENDED ≠ DURABLE ≠ ENGINE_APPLIED`. 기존 local spool의 bounded `group_sync_ms`는 **시간 기반 durable batch**이며 거래소 전체 수신을 모으는 장벽이 아니다. 배치 대기/순차 `RECEIVED/APPLIED` ACK/공유 lock의 지연은 `PERF-01`에서 먼저 재현 계측하고, **실증된 불필요한 대기만** 기존 코드 안에서 최소 수정한다. A1 durable-before-strategy, native L2 연속성, silent drop 0, crash recovery 보존은 변경 금지.
  - **숨은 Ready Gate 주의**: 기존 `LocalMarketEngine.event_source(required_producers=None)`에는 all-feed barrier, `LocalMarketEngine._initial_proofs_ready`에는 all-feed initial-recovery proof가 있다. 정규화/수집 병목과 startup/account recovery safety를 분리해서 검사한다. Live streaming에서 A-only 전략은 B에 의존하지 않도록 연결하되, 계좌/체크포인트 복구 seal을 무증거로 제거하는 shortcut은 절대 금지. 안전한 A-only resume 불가능하면 전체 완료를 선언하지 않고 구체적 `BLOCKED_RECOVERY_DEPENDENCY`를 보고한다.
- **VEN-STOCK-01 — 동일 Extension Seam(P0)**: `execution/venues/`의 Adapter 등록·Capabilities·enable/disable/remove·계좌 Owner 계약을 Crypto Perp만이 아니라 **선택된 Crypto Spot 및 국내주식 Broker(KIS, Kiwoom, 향후 NH)**에도 적용한다. `KIS` 기존 ETF Mock/Mock2 경로 보존, `Kiwoom` 기존 `brokers.py` 제출 코드를 재사용해 부족한 필수 주문 상태 경로만 보강한다. 향후 **NH 신설은 기존 Core·Strategy에 패치 없이 별도 Adapter+Config만** 허용하지만, **NH 실 API 코드는 본 릴리스에서 만들거나 Live 지원으로 선언하지 않는다**. NH는 credential-free Dummy Broker Plugin으로 확장 구조만 시험한다.
- **TOSS**: **시세 수집을 계속 사용하므로 정식 Data Provider로 유지하고 설정된 종목에 대해 운영 수집 가능해야 한다.** 주문 API는 사용하지 않으며 `DATA_ONLY`로 Mutation을 차단한다. TOSS 원본·Historical/Golden·Rollback 증거는 보존하고 NATS 교체를 이유로 수집기를 제거하지 않는다.
- **작업 소유**: 기존 31개 Ticket/DAG를 유지한다. 데이터 변경은 `MOV-02`에서 **기계적 이동만** 수행하고 `QA-01`에서 독립성/인과성 통합 테스트, `PERF-01`에서 지연 실측·원인 보정. Venue/Broker는 `CAP-00` 대상/미지원 확정 → `VEN-01` Config Plugin → `CAP-01/02` 선택 코드 연결 → `EXEC-01` Owner Router → `QA-01` E2E 순서로 검증한다. 새 Frame/동기화 Worker/Adapter 기반 프레임워크를 세우지 않는다.

## 2. 개발 원칙과 우선순위

### 2.0 이번 버전에서 수정한 실행·전략 책임의 단일 원칙

**Strategy owns all trading decisions; Execution owns safe mechanics and factual truth.** 전략 파일이 매매 시그널만 내고 실거래 주문 유형은 Runtime이 추측해서 채우는 설계는 금지한다. 파일은 `NEW/CANCEL/REPLACE`, `BUY/SELL`, `venue/account/symbol`, 주문 수량·지정가, `MAKER_POST_ONLY`/`TAKER`/`FOK`/`PARK_GTC`, deadline, Cross-Venue Leg 구성, 실패·부분 체결 대응 방침까지 **명시적 ActionPlan**으로 출력한다. 실패 후 새 주문을 낼지는 `on_order_event` 등 전략 파일의 결정이며, 실행 엔진은 이를 임의 재시도하지 않는다. 다만 **위험 한도·잔고 예약·실계좌 권한·중복 실행 차단·UNKNOWN 대조·API 검증은 전략이 무력화할 수 없는 공통 엔진 책임**이다. 교차 거래소의 두 Leg 정책도 전략 파일이 고르되 그룹 영속성·전송·상태 추적은 실행 엔진이 처리한다. **Cross-Venue 상품 동등성·스프레드/수익성·자본/재고 이용·헤지 대상/수량 결정은 전략 책임**이다. **MM의 FV/스프레드/양방향 호가/수량·유지·교체·취소·헤지·재고 목표도 전적으로 전략 책임**이다. 공통 엔진은 fill/order/account/position/available balance/working orders의 검증된 사실을 전략에 반환할 뿐 호가·재고 목표나 대체 주문을 생성하지 않는다. 전략 Config의 `instrument alias → (venue, market_segment, symbol, account)`를 명시적으로 해석해 Spot과 Perp처럼 동일 심볼인 상품을 안전하게 구별하며 전략별 거래소 분기문이나 새 Instrument 서비스는 만들지 않는다.

현 운영 알파는 C3/KP/KD만 활성 대상이다. `fv_mm`, `liquidity_mm`, `fv_pilot` 등의 과거 연구/테스트 전략은 **자동 등록/실운영 불허**. 단, 유일하게 사용 중인 거래소 실행 함수나 중요한 Golden이 남아 있다면 기능을 신규 공통 엔진으로 재사용한 후 증거를 확보할 때까지 코드를 보존한다.

신규 `STR-02`, `EXEC-01`, `VEN-01`, `SIM-01`은 **필수**다. `STR-02`는 파일 내부 Order Action·피드백, `EXEC-01`은 모드별 공통 주문 라우팅·안전 실행, `VEN-01`은 Perp Venue 추가·차단·제거, `SIM-01`은 Ponytail Skill 기반 실제 Verbose 제거를 완수한다. `CAP-05`는 Cross-Venue 필수이며 이 새로운 계약 위에서 실제 양방향 흐름을 수락한다.


- **Single owner**: 각 이벤트/호가/계좌/주문/전략 상태에는 정해진 authoritative writer; alias는 호환 계층일 뿐 추가 authority 아님.
- **Preserve behavior first**: 구조 이동은 0 비즈니스 의미 변화, 기존 Native Precision/Sequence·C3/KP/KD 의사결정·KIS 모드/잔고·거래소 주문 semantics 변화 없음.
- **KISS/YAGNI/DRY**: 새 Frame/Store/Adapter/Planner/DB/Worker 도입은 현재 실행 경로의 명시적 미충족 조건과 기대 이득이 있어야만 허용.
- **Fail closed**: UNKNOWN/late-fill/cancel ACK/계좌 reconcile 미완료/invalid market/replay 시 신규 위험 주문 거절; Intent+Evidence+Reservation은 실제 mutation 이전 durable commit.
- **Resource bounded**: queue count+bytes+age, snapshot/history, per-strategy callback, API in-flight, spool backlog; 2 vCPU 호스트에 무제한 background 작업 없음.
- **Mechanical moves separated**: 파일 이동·Import·패키징과 비즈니스 로직 변경은 별도 커밋·수락. 하나의 **최종 배포** 안에 여러 개발 커밋 포함 가능.
- **Reduced LLM context**: 완료 작업 재분석 금지, 현재 티켓/직접 호출자/테스트만 읽기, git/AST/codemod와 테스트는 결정론적 도구; OpenCode/GLM·DeepSeek 구현, Codex는 Tech Lead·고위험 승인.

## 3. 권위와 상태 데이터 모델

| 문서 | 유일한 책임 | 작성·읽기 규칙 |
|---|---|---|
| `01_FINAL_PLAN.md` | Scope, phase sequence, priorities, exit gates, release policy | 변화 시 PM만 수정 |
| `02_DEVELOPER_TICKETS.md` | 미완료·미착수 **신규** 작업별 파일·함수·작업 순서·테스트·수락 | 현재 티켓만 모델에 제공; 완료 티켓 상세 역사 복사 금지 |
| `03_ARCHITECTURE_REQUIREMENTS.md` | 모든 티켓이 지켜야 할 모듈 경계·자료 계약·불변조건·Capability와 안전 규칙 | 구현자가 관련 절만 읽기; spec gate 무단 축소 금지 |
| `04_COMPLETED_MANIFEST.json` | 기존 로컬 수락 작업의 동결 목록·SHA/Receipt 참조 | append/실수정은 수락 절차에서만; 과거 완료의 증거 값 덮어쓰기 금지 |
| `05_TASK_STATUS.json` | **현재 신규 티켓** 상태·DAG·다음 READY·blocker·시험 receipt 참조 | 단일 machine status; 완료 증거 생성 후 Tech Lead 업데이트 |

기존 `docs/refactoring/vnext/stage_status.json`, `tickets_production.json`, `reuse_audit/revised_dag.json` 및 v3, Minimal Reuse는 **legacy snapshots**, 새 작업 실행을 지휘하지 않는다. 이 5개가 실사용으로 승인되기 전 기존 기록 삭제 불가. 이전 문서의 Acceptance Receipt·Golden·runbook는 역사 증거로만 참조.

### 3.1 `DONE`의 기준

- `CODE_COMPLETE`: 함수/IO 인터페이스 구현·타입/정적 검사 PASS.
- `LOCAL_VERIFIED`: 결정적 unit/integration/fault/Golden/packaging PASS; 실행 명령·exit code·SHA 존재.
- `OFFLINE_RELEASE_READY`: 새 아키텍처·명시 REQUIRED capability·전원 실행 역할 정합, **성능 계측 재현성·구조적 비차단성** 입증 (수 ms 수치 충족은 비차단 목표), image/config/mount preflight 계획 준비.
- `SERVER_CANDIDATE_INSTALLED`: 사용자 승인된 서버에 immutable artifact 배치; 기존 owner/원본 보호.
- `OPERATIONAL_VERIFIED`: 실제 장중/24x7 환경의 feed·재연결·사용량·복구·권한·Owner/계좌 대조 PASS.
- `LEGACY_RETIRE_AUTHORIZED`: 관측 안정화·rollback 정책 충족·사용자 별도 승인 이후만.

`LOCAL_VERIFIED`가 `OPERATIONAL_VERIFIED`를 뜻하지 않으며, *일부* venue만 검증된 상태는 전체 플랫폼 완료가 아니다.

## 4. 실행 Wave — 새 티켓만

| Wave | 티켓 | 목표 | 통과 Gate |
|---|---|---|---|
| **W0 권위 전환·기준선** | GOV-01, OPS-01 | 이 5개에 Authority 고정, 기존 38개·R/O 완료 동결, OpenCode/tooling 경계 | HEAD/checksum/status 단일성, 최소 task packet, 비밀/원격 금지 |
| **W1 구조 이전** | MOV-00, MOV-01, MOV-02, MOV-03, MOV-04, MOV-05, MOV-06, MOV-07 | HEAD의 77개 root modules + active packages를 **`src/` 바로 아래** 역할별 배치로 이전, `vibetrading` 네임스페이스/동명 root legacy 제거 계획, Timefolio 및 Docker/Compose 경로 업데이트 | 미분류 경로/동명 권위 0, 설치/CLI/모든 서비스 smoke, semantic move-only AST 동일, P02 rollback 보존 |
| **W2 범위 고정·문서 보존** | CAP-00, CLN-01 | 선택 지원 범위를 기존 소스 근거와 대조하고, 역사 증거·원시 로그의 보존/정리 경계 확정 | 실제 삭제 후보별 static+dynamic+deploy+rollback+test 참조 0, 완전 회귀 |
| **W3 최초 기능 Gap** | STR-01, **STR-02, VEN-01, EXEC-01**, CAP-01, CAP-02, CAP-03, CAP-04, **CAP-05 (무조건 필수)**, CAP-06, **FEED-01, OBS-01** | 기존 경로의 지원 범위를 확정하고 부족한 실행·Private/Reconcile·Perp 회계·계좌 공유·전략 격리를 최소 코드로 보완. **전략 신호→두 Venue의 반대 주문 제출·Group 지속·실패 복구는 선택 페어 없이 구현** | `03`의 REQUIRED 표 + CAP-05 두 가지 제출 모드·부분 체결·실패·재시작을 **실제 두 개의 서로 다른 Venue Adapter 코드 경로**(자격증명 없는 scripted transport)에 연결한 E2E `LOCAL_VERIFIED`. 특정 실계좌 페어 활성화는 별도 승인 |
| **W4 정리·간소화·문서** | CLN-02, **SIM-01**, CLN-03, **WEB-01** | 선택된 정식 경로 구현 후 중복·Verbose 코드를 실제 제거하고 문서/Caller/롤백 의존 보존 | 변경된 code diff + Ponytail 실제 리뷰 + regression + clean tracked artifacts |
| **W5 품질·통합** | QA-01, PERF-01, **KR-DATA-01 (조건부)** | 전체 Linux 검증·실제 OS 조립·fault, 2vCPU 자원/상태/latency 프로파일링 | 안전 결함 P0/P1 0, golden diff 0; 성능 계측과 구조적 비차단성 확인. p50/p95/p99 참고 목표 미달만으로 FAIL 금지 |
| **W6 단일 릴리스 준비** | REL-01, DEP-01 | 단일 SHA 이미지·env/mount/owner/rollback 검증 스크립트; 서버 통합 테스트 계획 | frozen artifact, immutable manifests, 한 번 설치+순차 검증 가능 |
| **W7 별도 승인 서버 작업** | CUT-01, RET-01 | 서버 단일 전환·운영 검증, 이후 구 NATS 제거/데이터 회수 | 사용자 명시 승인, 단계별 안전 Gate, rollback, 원본 데이터 보호 |

`MOV`와 `CAP`은 충돌 없는 파일에서 병렬화 가능하나, 주로 **W1→W3**를 권장. 릴리스는 W0~W6를 하나의 후보로 묶어 한 번 배포한다. 특정 필수 CAP이 범위 미정이면 전체 완료를 주장하지 않고 사용자가 해당 feature를 명확히 제외한다고 승인받을 때까지 block한다. 문서만으로 필수 feature를 삭제하지 않는다.

## 5. 최종 src/ Flat Domain Layout — 개발 대상 배치 확정

**목표는 `src/vibetrading/` 없이, `src/` 바로 아래 역할별 패키지를 두는 것**이다. 현 HEAD의 경로는 실행 중 데이터/프로젝트 경로가 아니라 **마이그레이션 입력**이다. `pyproject.toml`의 과거 `vibetrading = "src/vibetrading"` 고정, `import vibetrading.*`, `python -m vibetrading.*`, 이미지/볼륨 바인드, root `strategies/` 충돌을 **동일 리팩토링 릴리스 안에서 명시 수정**한다. 이름 충돌 때문에 불가능하다는 결론을 사전에 내리지 말고 import precedence/wheel/package discovery를 증명한다.

```text
src/
  core/              # 모델·계약
  feeds/providers/   # 수집 전용, 실제 venue 프로토콜 코드 중복 제거
  market/            # 한 writer의 book/trade state 및 fanout
  storage/           # spool/Parquet/cloud/checkpoint
  strategies/        # KR/crypto strategy files, loader; old root strategies/ 제거·이전 검증
  execution/         # 공통 owner/router/groups/ledger, Venue/Broker adapter
    venues/          # 기존 adapter canonical owners + 신규 module seam
  portfolio/         # 잔고·포지션·거래내역
  runtime/           # 조립/실행역할/복구/CLI; 연구 MM bundle 보존소가 아님
  operations/        # 상태·Telegram·dashboard Python
  timefolio/         # Timefolio 대회 웹 주문 client/runner; 별도 활성 경로
```

- **중복/레거시 제거 조건**: `bybit_execution.py`는 기존 별도 Bybit 지연측정/주문 Probe; `kis_execution.py`는 KIS 국내현금주식 주문 소스다. 구현을 새로운 `execution/venues` canonical adapter에 흡수할 때, 동일 실행·감사·Mock 특성이 증명되면 옛 독립 모듈은 최종 구조에서 **제거**한다. `perp_execution.py`의 다섯 Venue 구현도 각각 파일을 복제하지 말고 현 코드를 분리/재사용하며 owner 중복 0으로 만든다. `brokers.py`, `extended_brokers.py`, `fv_mm/venues.py`도 동일 원칙.
- **Feed 구조**: 기존 `providers/crypto_perp.py`는 복수 Perp의 공유 collector, `extended_perp.py`는 Arcus/GRVT, `providers/binance.py`는 별도 Binance 상품/feed를 포함한다. Venue별 별도 파일 수를 균등하게 만들지 않는다. 프로토콜 소유·필드 차이·서브스크립션·순서 복구를 근거로 필요한 모듈만 남긴다. 새 Venue는 수집과 주문이 각각 필요한 인터페이스에 연결될 때만 사용 가능하다.
- **Timefolio는 유지**: `src/timefolio/{client,runner}.py`를 별도 대회 주문 경로로 유지. 브라우저 기반 주문 실행이라는 사실을 감추고 일반 거래소의 REST Adapter라고 위장하지 않는다. 현재 NATS 기반 runner 이벤트 공급 경로는 NATS-free candidate로 변경하고 `submit=false` 기본·계좌 승인·정확한 주문 반환 상태를 증명한다.
- **이전 구 폴더**: 기존 `fv_mm`, `liquidity_mm`, `fv_pilot`, `mm_state`를 이유 없이 `runtime/apps`에 옮기지 않는다. 필요한 API/상태 함수·Golden만 정확히 이관하고 `CLN-02`에서 참조 0을 증명한 경우 최종 코드/이미지에서 제거. 실행 필요성이 남은 파일만 담당 도메인에 소유시키며 예외는 owner/제거 시점까지 기록한다.
- **Python 실행 계약**: `src`는 소스 루트일 뿐 `src`라는 Python 패키지가 아니다. `from core.models`, `from execution...`처럼 명시적 top-level 패키지를 사용한다. `pyproject.toml`은 `where=["src"]` 중심으로 다시 작성하고 wheel/editable/off-repo/독립 cwd import/CLI를 검증한다. `root/strategies`, `root/services`, `root/shared` 등 동명 레거시는 canonical 패키지로 옮기거나 active dependency 소멸 증명 후 제거/rollback 별도 보관한다. 동명 두 권위를 남긴 채 성공 선언 금지.
- **실서버 배포**: 루트 `Dockerfile`, `Dockerfile.crypto`, `docker-compose.yml`이 실제 운영 입력이고 `deploy/` 아래에는 Compose overlay·dashboard/Timefolio 이미지·scripts/systemd/runbook이 있다. **두 위치 모두** 수정·수락 대상. 모든 이미지의 새 `src/`/entrypoint/`PYTHONPATH`/config/mount/healthcheck/env/secrets 연결을 `MOV-06/07→REL-01→DEP-01`에서 증명한다. 구 NATS Compose는 전환 전 rollback 자료로 보존하되 최종 NATS-free primary profile에 강제 의존시키지 않는다.

**최종 레포는 가독성만으로 성공하지 않는다.** import 충돌, 실행 역할 누락, Timefolio 주문 경로 탈락, 이미지에 옛 코드/없는 경로를 담는 구성은 모두 Gate FAIL. 과거 프로그램과 byte-exact AST를 보존해야 하는 `MOVE_ONLY`와 코드 통합/삭제를 수행하는 `DEDUPE`는 반드시 서로 다른 diff와 테스트로 수락한다.

### 5.1 구조 이동은 3개의 서로 다른 승인 단위

1. `MOVE_ONLY`: `git mv` + import/entrypoint/path 참조만; 각 구현 함수 AST equivalent, Golden identical.
2. `DEDUPE`: 단일 canonical owner 선택 및 old alias 제거; 실제 import/reference/monkeypatch identity 테스트.
3. `SIMPLIFY`: 중복 데이터·과잉 추상화·verbose code 감소; 별도 기능 회귀 + benchmark; 가독성 향상만으로 안전 불변조건 제거 금지.

이 세 가지를 한 커밋에 섞지 않는다. 동시에 2개 이상의 Git worktree에서 동일 파일을 수정하지 않는다. 이전 진행 중 로그는 모델에 모두 전달하지 않고 실패 요약·git diff --stat·테스트 결과만 전달한다.

## 6. 성능 평가 — 토큰 사용과 서버 성능을 혼동하지 않음

- 리포 파일 수 감소는 **모델의 자동 입력 토큰 감소가 아니다**. 모델이 읽는 `AGENTS`/권위 5개/현재 티켓과 검색 Scope를 축소하는 것이 실제 입력 절약 요소.
- 문서 3개를 매 티켓마다 전부 읽히지 않는다. 시작 시 `01` 한 번, 각 티켓에는 `02`의 해당 블록 + `03`의 해당 계약 번호 + 직접 소스/테스트 파일만 제공.
- Local regression은 변경 파일 대상 먼저, 배치 경계 전체; 기존 2,480 PASS는 **과거 SHA의 기준**이며 신규 HEAD의 결과가 아니다.
- 파일 이동만으로 CPU/latency가 개선되지 않는다. 자원 최적화는 실제 `PERF-01`의 비교 측정과 원래 P02/P05 목표(예: 동일 trace CPU/event -20% 목표, tail +10% 변화는 원인 분석용 참고 지표(미달만으로 FAIL 금지))를 별도 판정한다. 무근거 성능 약속 금지.
- FreeLLMAPI/OpenCode 모델 한도는 fail-fast/strict-pinned; 불명확한 자동 provider fallback 금지. Codex의 긴 직접 구현 fallback 금지. 토큰·시간 예산을 증거 로그로 남기되 산출물 경로는 core Git에 무차별 저장하지 않는다.
- **Worker 우선순위 (사용자 지정 2026-10-26)**: 1순위 `freellmapi/glm-4.5-flash` → 2순위 `opencode/deepseek-v4.1-flash-free` → 3순위 FreeLLMAPI Gemini 등(초기 고정 후보 `freellmapi/gemini-2.5-flash`, 카탈로그/실호출 별도 검증). Codex가 실패·부분 변경을 검토하고 전환을 알린 뒤 다음 순위를 사용한다. Worker 자체의 무기록 모델 변경은 금지하며 새 유료 경로·계정 변경은 승인 대상이다.

## 7. 단일 릴리스와 검증

**개발 로컬 커밋은 분할, 운영 설치는 한 번**. `DEP-01`은 Read-only → Blocked/Shadow → user-approved limited real state transition을 **한번의 설치 이벤트 안에서** 실행하는 스크립트/검증 명세. Docker Compose 프로세스 교체는 원자적 다중 서비스 트랜잭션이 아니다.

1. 로컬: 최종 Git SHA freeze; image digest·dependency lock; full regression; representative trace·fault; stale README/기존 경로 reference zero; Volume mount contract와 restore 테스트.
2. 서버 preflight(비파괴): 운영 14 역할별 image/config/mount/source 차이; disk inode/backlog, owner lease + 외부 거래소 미체결·UNKNOWN 확인, WAL/DB snapshot consistent backup, checksum/readback; CLI/compose config.
3. 안전 정지: old order owner + auto-restart 비활성화 후 신규 risk permit 0, 주문·포지션 reconcile, 구 NATS 및 원본 데이터를 보존.
4. candidate 설치: image digest 고정, shadow/blocked 시작, NATS-free feed·spool·UDS·Parquet/cloud, market quality, Paper, archive recovery, Telegram cold report/kill 확인.
5. gate 충족 시 사용자가 승인한 특정 account/venue만 LIVE 허용(지원 불가면 block 유지). 독립 subprocess hard isolation이 없는 cooperative 전략은 운영 정책을 명시.
6. 오류: 모든 안전한 검사는 한 차례 통합 리포트로 수집하되, 보안·원장·소유권 P0 발견 시 위험 검사 즉시 중지. 새 버전 중단, 최신 원장/주문 상태 대조 뒤 구버전 재기동 여부 판정. 과거 checkpoint로 원장 rewind 금지.
7. 관측: 하루/장중·장외·weekend/24x7에 걸친 실제 기간은 서버 운영 데이터가 필요. 한 번의 배포로 전체 장기 조건이 즉시 증명되지는 않는다.

**외부 Gate가 필요한 항목**은 `NOT_RUN / BLOCKED / EXTERNAL_ONLY`로 기록. 실계좌 API 인증이 불가한 것은 외부 검증 보류지만 해당 API transport·mapping·로컬 fake fixture 구현 누락은 `EXTERNAL_ONLY`로 숨길 수 없다.

## 8. 최종 Release Acceptance Matrix

| Gate | PASS 조건 | 실패/미충족 조치 |
|---|---|---|
| `G-SCOPE` | 최초 요구의 선택된 기능 목록/지원 범위가 `03`와 CAP-00에서 양쪽 일치 | 목표 임의 축소 금지; 승인 없는 Optional 전환 금지 |
| `G-STRUCT` | `src/vibetrading` 최종 active import/module/entrypoint/image 참조 0, `src/` domain imports/wheel/CLI, root `strategies` 충돌 제거, Timefolio 보존, canonical adapter 단일 권위, 각 파일 이동·퇴역 증거 | MOV/CLN 재작업 |
| `G-SEMANTICS` | C3/KP/KD 경제·KIS mode·기존 Venue order/fill 및 cloud path 회귀 기준과 동일 | 신규 risk 활성화 금지 |
| `G-DATA` | NATS 없는 수집→Spool A1 durable→UDS→MarketState→전략 경로, **다른 Venue 정지 중 활성 Producer 수집·정규화·공개 지속**, Source별 sequence/gap/available_at, bounded backlog, Parquet/Cloud stall 독립, checkpoint/GC/replay PASS | 타 Venue 수신까지 기다리는 전역 barrier 또는 A1 이전 전략 공개/무단 drop이면 BLOCK |
| `G-STRATEGY` | 전략 파일 실제 `create_strategy`/on_event/**on_order_event** + Order Action 전체 결정(venue/side/TIF/maker/FOK/park) + 모드별 PAPER/MOCK·TESTNET/LIVE-gated E2E, C3/KP/KD 판단 단일 소유 및 격리/readiness | 해당 플러그인 비활성/필수 경로 BLOCK |
| `G-TRADE` | **Crypto + 국내주식 Broker 공통 Plugin Lifecycle**: Dummy Crypto/Dummy Stock Broker 각각 module+config만으로 등록, existing KIS Mock2 및 Kiwoom 국내주식 **실제 Adapter 코드 경로**를 scripted transport로 submit/cancel/query/fill/reconcile·Owner feedback 검증. config disable 중 기존 주문 대조 유지; 계좌/포지션 안전 경계·5 Perp 필수 보존. NH 신규 실제 API·Live 검증은 아직 주장 금지 | 신규 Broker를 쓰기 위해 Router/Strategy 분기 수정이 필요하거나 Kiwoom 선택 주문 경로가 `NOT_IMPLEMENTED`로 남으면 필수 범위 BLOCK |
| `G-PORTFOLIO` | same-account reservation/partial fill/UNKNOWN/fee/funding/segregation **및 CAP-05 Cross-Venue 그룹의 필수 로컬 실행·복구 PASS** (페어 실계좌 활성화는 별도) | account risk owner BLOCK |
| `G-CAPACITY` | 2vCPU 대표 입력·동시 저장/전략·두 Venue Group에서 **T0→T5** 분포·CPU/RSS/queue/disk·내구 commit 비용을 재현 가능하게 측정하고, 불필요한 동기 I/O·중복 처리·무제한 대기를 분석한다. p50≤2ms/p95≤5ms/p99≤10ms는 참고 목표로 보고 | 측정 미실행/증거 부재는 `NOT_RUN`; 구조적 비차단성·기존 안전/내구 불변조건 위반은 FAIL. 참고 지연시간 목표 초과만으로 FAIL/BLOCK 금지 |
| `G-RELEASE` | Linux full PASS, 루트 Dockerfiles/Compose + 모든 사용 deploy overlays + dashboard/Timefolio + systemd/cron/config/health/volume/secret 경로의 새 소스와 일치, 각 image SHA·digest 고정, NATS-free selected profile, rollback proof, P0/P1=0 | 배포 금지 |
| `G-SERVER` | 명시 승인·preflight·단일 owner·read-only/blocked/shadow·적용 이후 운영 관측 PASS | 비활성 유지 또는 안전 복귀 |
| `G-RETIRE` | 보존/롤백 필요기간 종료 + dependency/원격 backup + 사용자 별도 승인 | NATS 및 유일본 유지 |

## 9. 완료 후 사용자에게 보고할 내용

최종 PM 보고는 `(a) 정확 SHA, (b) 완료/미완료 티켓 수, (c) 지원 범위별 verified/blocked, (d) old→new path diff, (e) 삭제·아카이브/보존 바이트·개수, (f) full test + fault & performance, (g) 외부 환경에서만 확인할 Gate, (h) 단일 릴리스·rollback 검증`에 한정한다. 새 계획 세트를 추가 생성하지 말고 이 다섯 파일을 갱신한다.


## 10. 신규 V2 핵심 Gate: 의사결정 파일과 공통 엔진의 독립성

- 파일 하나만 추가해 새로운 crypto/국내주식 전략을 등록하고, 계좌/venue 구성만으로 PAPER 및 해당 지원 환경에서 실제 주문 어댑터 호출까지 이어지는 시나리오가 필수다. 주문 가격·TIF·maker/taker·FOK/park를 중앙 엔진이 전략 이름으로 추측하지 않도록 검사한다.
- 실제 시세→(내구 경계 완료 후 bounded 실시간 전달; Parquet/Cloud는 병렬)→전략 이벤트/주문 Action→pre-mutation durable account commit→adapter network-send start를 **같은 시계의 trace ID**로 계측한다. `T0_provider_callback→T5_adapter_send_start`와 `T1_strategy_available→T5_adapter_send_start`의 p50/p95/p99를 계측·보고하며 외부 network RTT/ACK/fill과 분리한다.
- Order Reject/Partial Fill/Cancel/UNKNOWN이 원래 전략에 결정적인 Event로 귀환하고, 전략 파일 자체가 후속 주문 의도를 계산한다. 불확실한 주문은 재대조 전 무조건 blind retry 금지.
- MM 필수 P0 사례는 **기존 STR-02/EXEC-01/CAP-02/CAP-03/CAP-05/QA-01**에서 검증한다: 전략 발행 양방향 Maker 주문→취소/교체와 늦은 Fill→정확한 체결·미체결·계좌/전략 귀속 재고 상태 전달→전략이 다음 Action 생성. 공통 엔진의 quote/repricing/inventory-target/hedge-policy 구현 0; 신규 MM 티켓/서비스 0.
- Ponytail Skill 미설치·미로드 상태에서는 SIM-01의 리뷰 완료를 주장하지 않는다. 임의 계획서 한 줄로 verbose 정리가 끝났다고 하지 않는다.
- 이전 연구 전략 파일을 실운영으로 재활성화하지 않는다. 신규 Crypto Venue **또는 국내주식 Broker** 추가/disable/delete가 전략 의사결정 코드를 바꾸지 않아야 한다. TOSS 주문은 계속 DATA_ONLY로 차단하고 TOSS 시세 수집 경로는 유지한다.


## 11. 이번 결정의 마이그레이션 Acceptance (User 2026-10-25 18:23 KST)

- `G-FLAT-SRC`: current Python import roots `{core,feeds,market,storage,strategies,execution,portfolio,runtime,operations,timefolio}`. Source tree에서 `src/vibetrading` active import/relative `PYTHONPATH`/image mount를 실행시키는 경우 PASS 금지. `pip wheel`, editable, Docker no-repo-cwd, CLI/pytest, pickle/state migration을 실행·기록.
- `G-LEAN-CODE`: 독립 `bybit_execution.py` 및 `kis_execution.py` **독립 authority 필요성**을 실제 caller로 반증하지 못하면 canonical Venue adapter 통합 후 제거한다. `fv_mm/liquidity_mm/fv_pilot/mm_state`는 연구 소스 보존 요청이 없는 한 최종 운영 이미지의 필수 디렉터리가 아니다. 런타임·롤백·테스트 의존 0이면 삭제한다.
- `G-TIMEFOLIO`: `src/timefolio/client.py`, `runner.py`의 대회 주문 제출 기능·모니터링, 계좌별 승인 Gate 및 NATS-free event input을 보존한다. 제출하지 않는 기본 설정과 승인 없는 실주문 0을 검증한다.
- `G-DOCKER`: root `Dockerfile*`, `docker-compose.yml`, 사용중인 `deploy/*compose*.yml`·profile·Dockerfile·systemd 및 `scripts/build_release_images.py`를 모두 묶어 이미지·Config·Runner·Volume·Secret·health 경로 검사. 운영 서버 실제 변경은 승인 이전 금지.


## 12. User Scope 2026-10-25 19:55 — Venue Feeds, Read-only Strategy Dashboard, External Web, Order-only Broker, KR350 Option

**Authority / no new framework:** maintain original 31 tickets and their accepted states; append exactly four bounded tickets (`FEED-01`, `OBS-01`, `WEB-01` required local; `KR-DATA-01` conditional). Existing Data A1 durable, single Account Owner, Strategy-owned economic decisions, one immutable release, `src/` flat domain roots, Timefolio submission, no mandatory per-Venue feed/execution symmetry. Nothing can automatically enable real orders or expose public server.

**Final topology (representative, not synthetic source files):**

```text
src/
  feeds/providers/{binance,bybit,bitget,grvt,arcus,hyperliquid,kis,kiwoom,toss}.py
  feeds/{local_pipeline,feed_recovery,...}.py
  market/                 # one-writer source-independent state
  storage/                # spool + checkpoint + Parquet/cloud async
  strategies/{kr,crypto,contract.py}
  execution/{execution_owner,execution_groups,execution_ledger,paper_execution,...}.py
    venues/               # crypto/broker submit/query/cancel/normalize/reconcile; NH future
  portfolio/              # authoritative per-account and strategy facts
  runtime/                # composition, execution/strategy lifecycle, state restore
  operations/dashboard/   # existing Python bridge/projections, latency/PnL read-side
  operations/             # health/Telegram/report
  timefolio/              # contest order client/runner
 dashboard/               # existing UI /api/pnl/state/orders, strategy tabs
 deploy/                  # existing overlays/Reverse proxy TLS auth config as needed
 config/                  # independently enabled feed/order venue aliases
 Dockerfile*; docker-compose.yml; pyproject.toml
```

**Lifecycle:** each provider `receive → source-native minimal normalization → bounded enqueue → A1 durable spool → UDS → shared MarketState → per-strategy dependency-filtered snapshot → strategy ActionPlan → guarded Owner → selected execution-only or both Venue Adapter → ACK/fill/account facts → portfolio + strategy feedback`. Parallel read-only `observability projection → dashboard UI`, `spool → Parquet/cloud` (no cloud/UI ACK dependency on strategy). Venue A never waits for Venue B, except explicit data dependencies of the consuming strategy and provable recovery/account safety.

**Public dashboard:** reuse current `dashboard/` + `operations/dashboard` Bridge. Dashboard system view: source+stage p50/p95/p99, counts, queue/durable backlog, freshness and RSS/CPU; one tab per registered strategy with realized/unrealized/net P&L (fees/funding marked once), cumulative P&L plot, positions/orders/last-as-of. Program operations must continue if read-only dashboard fails. HTTPS/domain/TLS + authentication, API method isolation, external ACG only after authorized CUT-01. No credential/payment/order mutation endpoints publicly exposed; public IP is routing, not an authentication method.

**Venue feed separation:** `providers/crypto_perp.py`, `extended_perp.py` are historical multiplexer input, not mandatory final modules. FEED-01 extracts venue-specific parsing/subscription/gaps to files and **deletes old multiplexer when callers zero**, retaining shared helper at one location. TOSS remains DATA_ONLY collector. Provider and Execution Adapter registration are independent; e.g. `KIS/키움 data → future NH execution` without `nh.py` feed. `NH` real support never claimed until external API verified.

**KR breadth scope:** Optional `KR-DATA-01`, KOSPI200 (200) + KOSDAQ150 (150) minute/daily bars, ≤350 unique symbols, no mass L2/Trades, 10→50→150→350 staged; API quotas/cost and provider entitlement proven before enabling. 350×390=136,500 minute rows/trading day, ≈34.4m/year (252 sessions). API and market data rights are the primary unknowns, not row count alone. Initial goal host 2 vCPU/3.8GiB; no claim 24/7 full service is guaranteed before actual representative workload/soak. Need reserved RSS/headroom, CPU, SQLite/WAL/fsync, spool/Parquet/cloud, display frontend memory and Chromium for Timefolio.

| Gate | Required acceptance | Planned ticket |
|---|---|---|
| `G-FEED-MODULES` | currently selected crypto venue native parsing separated, TOSS collector preserved, source barriers 0 and source data Golden | FEED-01, QA-01 |
| `G-OBSERVABILITY` | real per-stage latency trace + strategy-owned authoritative net PnL curves and read-only dashboard tabs, restart/duplicate handling | OBS-01, QA-01, PERF-01 |
| `G-DASHBOARD-REMOTE` | authenticated HTTPS read-only UI with private ingest and blocked mutation paths; external DNS/ACG verified only with approval | WEB-01, REL-01, DEP-01, CUT-01 |
| `G-ORDER-ONLY` | feed-only TOSS, order-only DummyNH, both KIS/Kiwoom, independent source vs destination scope no illegal side effects | VEN-01, EXEC-01, QA-01 |
| `G-KR350-CANDIDATE` | local 350 bar simulator, proven provider API budget, staged 10/50/150/350 resource benchmark; enabled only if selected+external evidence | KR-DATA-01, PERF-01, CUT-01 |

**Core order execution and UI are not synonyms:** a Python Strategy can still submit ActionPlan if dashboard is offline. The dashboard cannot generate broker Orders. Historical completed-work manifest remains immutable. Code+UI+Docker release is not Production Verified without explicit approval and environmental evidence.
