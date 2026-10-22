# VibeTrading 최종 리팩토링 실행 계획 v3

> **문서 유형:** 실행 계획 / PM 기준 문서 (명세·요구사항은 별도 문서)
> **작성:** 2026-10-22 KST  
> **검증 기준:** GitHub `PJH-02/VibeTrading` 기본 브랜치 HEAD `a9a83af43897fdf8badc303aaf3b6ebf2eda87c5` (2026-10-21 20:37 KST 커밋). **이는 원격 저장소의 관측 시점 기준선이며 로컬 Worktree·서버 실행 이미지와 동일하다고 보장하지 않는다.**
> **참조 권위:** `docs/refactoring/vnext/tickets_production.json`, `stage_status.json`, `15_stage_status_and_next_steps.md`, 기존 P00–P12/VN/TX 계약과 과거 검증·배포 영수증. 기준선 변경 시 새 Manifest/Diff를 생성하고 과거 기록을 덮어쓰지 않는다.
> **반드시 병행:** `VibeTrading_Developer_Ticket_Spec_v3.md` 및 `VibeTrading_Architecture_Requirements_v3.md`.

## 1. 사업적 목적과 완료의 정의

프로젝트의 목표는 **수집 데이터의 정밀도·순서·인과성 및 기존 C3/KP/KD 주문 동작을 유지하면서, NATS-free 경량 구조를 기반으로 복수의 주식/ETF·Crypto Perp 전략을 동시에 Paper Trade할 수 있는 범용 실거래 준비 인프라**를 완성하는 것이다. 사용자 전략의 알파·진입 신호·백테스트·실계좌 승격 판단은 본 개발 범위 밖이다. 서버는 검증된 데이터를 외부 연구 환경으로 내려받을 수 있도록 해야 한다.

**이번 작업의 종료 조건:** 서버에 실제 배포하거나 외부 거래소 실계좌를 연결해야만 확인 가능한 항목 **외에는 모든 구현·로컬 통합·패키징·정적 검증·재현 가능한 결함 주입 테스트를 완료한다**. 외부에 의존하는 검증을 이유로 코드 자체의 미구현을 `PASS` 처리하지 않는다. 로컬에서 실제로 재현 가능한 장기 실행 시험은 실행한다. 서버/클라우드 특유의 성능, 실거래 계정 검증, 장중 연속 운용, 실제 Cutover/롤백, 운영 로그 보존/삭제는 별도 External Gate로 남긴다.

### 1.1 거래 및 데이터 범위

- **KIS:** 기존 C3/KP/KD 국내 주식·ETF의 모의/기존 지원 실행. `Mock`/`Mock2`·상품 매핑·경제적 의사결정 및 기존 원장 복원 규칙 보존.
- **Kiwoom:** 기존 수집 기능 유지; 실제 주문 Adapter는 현재 제품별 지원 근거·자격증명 없는 계약시험 가능 범위에서 구현. 지원할 수 없는 상품은 명시적으로 차단.
- **TOSS:** **DATA_ONLY**. 새 주문/취소/체결/잔고 Adapter 개발 금지. Reference Price 및 분봉 수집 활용 가능.
- **Crypto:** Binance, Arcus, GRVT, Bybit, Hyperliquid의 **Perpetual** 계약을 대상으로 한다. 기존 Binance Spot Adapter를 Perp Adapter라고 선언 금지. Linear/inverse, 계약 승수, 마진 모드 및 Position Mode는 실제 지원 범위를 분리한다.
- 여러 전략이 동일 계좌에서 동시에 작동할 수 있으며, GRVT MM + GRVT–Binance Cross-exchange arbitrage 등의 상충 주문·부분체결·UNKNOWN을 지원한다. **실제 계좌 권위는 계좌별 단일 Owner**, 전략별 포지션/PnL은 가상 귀속 장부로 분리한다.
- 전략 승격: **연구 → 계약에 맞춰 전략 구현 → 운영 서버 외부 백테스트 → 실시간 Paper → 사용자 수동 Live 승인**. Testnet/Mock는 전략 승격의 의무 단계가 아니라 주문·취소·응답·지연을 검증하는 도구다.

## 2. 현재 확인된 사실 및 병목

| 현재 상태 | 근거·결함 | 처리 | 우선순위 |
|---|---|---|---|
| 51개 VN/TX 단계 | `stage_status.json`: 로컬 완료 14, 부분/미완료 23, 미착수 14. 별도 최신 전체 Linux 검증 1532 PASS/2 제외 기록 | 완료된 구현 재개발 금지, 최근 HEAD에서 결과 재검증 | P0 |
| 제품 코드의 세대 혼재 | `vibetrading/`, `services/`, `shared/`, `strategy/`, `strategies/`, `fv_mm/legacy/` | 활성 진입점·동적 import·테스트 소비자 분류 후 단일 권위로 통합 | P0 |
| 문서의 복수 권위 | 루트 구형 README·계획, 과거 P/VN/TX 문서·수백 기록 | 현재 README/AGENTS/architecture/status는 짧게, 상세 근거는 보존·archive | P0 |
| 패키지 경로 복잡성 | 최상위 `vibetrading`와 루트 `services` 동시 존재 | `src/vibetrading` 표준 패키징; import/Compose/rollback 경로 동등성 | P0 |
| C3/KP/KD의 전략/실행 혼재 | `vibetrading/etf_trading.py`와 `etf_journal.py`에 실제 전략 존재 | 순수 신호 계산을 안정된 테스트와 함께 분리; 경제적 계산 동일 | P1 |
| 핵심 기능 WIP | `paper_execution.py`, `portfolio_ledger.py`, `strategy_runtime.py`, `perp_execution.py`, `execution_groups.py` | 추가 추상화 대신 E2E 연결과 계약을 완성 | P0 |
| 복구/저장 미완성 | VN07 account/strategy 연속 복구, VN08 retained-base, VN09 proof GC 남음 | checkpoint+reader+GC 증명 완성. 실제 운영 데이터 삭제는 제외 | P0 |
| Runtime/실거래 실행 갭 | Paper fixture 중심, Generic Owner offline-only, Perp venue adapter request mapping 중심 | 공통 권위·데이터 계좌 상태·Perp 주문/취소/체결 조회 정합성 구현; Live 권한 기본 DISARMED | P0 |
| 다중 전략 자원 압박 | 2vCPU/약 3.8GiB; 이전 서버 표본에서 swap·CPU PSI 상승 | bounded queue, O(affected orders), 부하/피크·실측. 비용 큰 신규 서비스 금지 | P0 |
| 토큰 소모 과다 | 전체 문서 반복 재독, 별도 agent 상태·중복 테스트/receipt, 폭넓은 WIP | Codex는 명세/검토/통합 승인, OpenCode+FreeLLMAPI는 구현, deterministic scripts는 기계적 수정 | P0 |

**핵심 이슈:** 코드의 개수보다 **현재 권위가 여러 경로에 흩어진 점, 개발 범위가 기능별로 수직 통합되지 않은 점, 소스·문서·테스트 증거가 여러 버전으로 존재하는 점**이 작업을 어렵게 만든다.

## 3. 선택한 개발 도구·작업 권한

### 3.1 선택: OpenCode + FreeLLMAPI **둘 다 사용**

- **OpenCode:** 저장소 읽기·도구 호출·파일 수정·단위 테스트·비대화형 실행이 가능한 코딩 Agent(Worker 실행기).
- **FreeLLMAPI:** 로컬에 띄우는 다중 무료 API **Gateway/Router**. API 자체가 코드를 직접 수정하지는 않는다. OpenCode를 `http://127.0.0.1:3001/v1`로 연결한다.
- **GLM / DeepSeek:** 런타임에서 실제 `/v1/models`에 나타나고 **tool calls/스트리밍/문맥 길이/Rate Limit** 시험에 합격한 모델만 업무 배치. 특정 모델의 무료 제공은 영구 보장되지 않는다. Model ID를 추측하지 않는다.
- **Codex:** 비싼 구현자 대신 **Tech Lead/PM/Independent Critical Reviewer**. 계약·범위 고정, 작업 패킷 발행, 위험한 diff 검토, 막힌 티켓 기술 결정, 최종 릴리스 Gate 승인에만 모델을 사용한다. 일상 파일 이동·테스트 실행·로그 요약은 수행하지 않는다.
- **Python/pytest/git:** 파일 이동, AST/Import Graph, 문서 링크 검사, SHA·DAG·권한 검사, 테스트 실행, 보고서 추출 등에 우선 사용한다.

FreeLLMAPI GitHub README는 OpenCode 연결과 `setup-opencode`, `/v1/chat/completions`를 문서화한다. 동시에 무료 한도 총량은 마케팅상 합계일 뿐 사용자 실사용 보장이 아니며 프로젝트는 개인 실험용이라고 명시한다. **개발용 PC에서만 실행하고 실거래 운영 VM/핫패스에 설치하지 않는다.** [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi), [OpenCode CLI](https://opencode.ai/docs/cli/), [Provider 설정](https://opencode.ai/docs/providers/).

### 3.2 작업 실행 권한과 모델별 RACI

| 산출물/단계 | Codex | OpenCode(GLM/DeepSeek) | Python·pytest/Git | 사용자 |
|---|---|---|---|---|
| 목표/불변조건/티켓 우선순위 | **A/R** | C | I | A(최종 범위) |
| 자동 경로 조사, `git mv`, import codemod | 승인 기준 | C | **R** | I |
| 리포지토리/README/문서 통합 | 검토 | **R** | 정합성 체크 | I |
| 일반 함수·Adapter 구현 | 티켓 명세·Critical review | **R** | 빌드/시험 | I |
| 주문·마진·복구 불변조건 | **A/Reviewer** | 상세 구현·보조 테스트 | Fault/Property tests | 안전 범위 승인 |
| diff·테스트·매니페스트 생성 | 실패 결과만 검토 | 정리 | **R** | I |
| 합격 판정 및 Merge | **A** | 증거 제출 | Gate 판정 | 릴리스 승인 |
| 실서버/실계좌 | 불허 | 불허 | 불허 | 별도 명시 승인 필요 |

R=수행, A=책임·최종 결정, C=협의, I=공유. Codex는 자체 개념의 Subagent 안에서 외부 무료 모델을 반드시 자동 호출할 수 있는 것이 **아니다**. **로컬 `opencode run` 또는 작업 패킷 전달**을 호출할 수 있는 작은 CLI/스크립트를 통해 위임한다. 외부 도구 미설치/한도 소진 시 Codex가 몰래 대량 구현하는 Fallback을 금지한다.

## 4. 완성 후 디렉터리 구조

```text
VibeTrading/
├─ src/vibetrading/
│  ├─ core/          # 이벤트·상품·ID·계약·clock
│  ├─ feeds/         # WebSocket·REST market data, TOSS(data only)
│  ├─ market/        # 원본 native L2, shared book/state/history
│  ├─ storage/       # spool, reader, checkpoint, Parquet, archive
│  ├─ strategies/    # C3/KP/KD·crypto plugins·registry
│  ├─ execution/     # owner, order lifecycle, paper, perp adapters
│  ├─ portfolio/     # strategy virtual/physical accounts, margin, funding
│  ├─ runtime/       # local process wiring, roles, readiness
│  └─ operations/    # Telegram, dashboard projections, maintenance
├─ config/           # 비밀을 포함하지 않는 버전 관리 설정
├─ docs/             # architecture, status, contracts, ADR, runbooks, archive
├─ tests/            # 기존 Golden + 모듈별·통합·fault
├─ scripts/          # 운영/개발용 통합 CLI; 보존 근거 없는 일회성 코드 제외
├─ deploy/           # Compose/Docker/rollback 호환
├─ dashboard/        # 독립 Node/TS 프로젝트 유지
├─ pyproject.toml
├─ AGENTS.md
└─ README.md
```

**실행 설계가 아니라 Repository 구조의 목표다.** 초기 `src/vibetrading` 위치 이동에서는 내부 파일과 함수의 의미를 바꾸지 않는다. 각 도메인의 분리/중복 제거는 그 기능의 변경을 이미 수행하는 티켓 안에서 개별 커밋으로 처리한다. C3/KP/KD 이관은 동일 입력→동일 intent/수량·가격·주문 가드 증명 후에만 진행한다. 모든 활성 패키지의 내재적 `vibetrading` import 경로를 최종 정식 경로로 갱신한다. **신규 alias wrapper를 영구적으로 남기지 않는다.**

## 5. 단계별 실행 계획 및 Gate

| Wave / Gate | 순서 및 포함 티켓 | 수행자 | 완료 산출물·통과 기준 |
|---|---|---|---|
| **G0 기준선** | R00·R01; 현 HEAD/status/Active Runtime/동적 import/14역할 Mount/Source comparison/데이터 분리 | 스크립트 + GLM, Codex 승인 | 단일 Root-of-Truth와 원본 Protected Path 목록. 파일 이동/제품 수정 없음 |
| **G1 에이전트 배선** | O00·O01: FreeLLMAPI/OpenCode 설치, pinned-model smoke, 안전 워크트리, task packet/검사기 | 사용자 1회 설정 + GLM | secret-free sandbox, 도구 호출 PASS, 토큰/지연 상한, 무허가 파일 쓰기 차단 |
| **G2 문서/패키징** | R02·R03: README/status/AGENTS, `src/vibetrading` 기계적 이동, build/CLI/Compose 테스트 | GLM+codemod | 이동 전후 Golden·전체 pytest·Docker build 설정 동등, rollback 스크립트 보존 |
| **G3 경계 통합** | R04·R05: legacy/caller 분류, 기존 전략 분리와 중복 해소, module ownership | GLM/DeepSeek; 고위험 Codex 리뷰 | 명백한 무참조 코드만 Archive; C3/KP/KD economics 불변. **기능 변경과 이동은 별도 Commit** |
| **G4 복구/상태** | VN07(잔여), VN08, VN09, VN15 | DeepSeek/GLM; Codex Critical review | account/strategy/pending-action 연속 복구·Reader prefix·GC dry-run, PnL quality; crash/rollback proof |
| **G5 Paper E2E** | TX00→TX01→TX02(A/B)→TX03(A/B)→TX04(A/B)→TX07A→VN13/TX05 및 TX08(A/B) | GLM/DeepSeek; Codex 계약 결정 | live-input fixture를 이용한 매매 Runtime→Risk→Paper Fill→Durable Portfolio→Restart, 다중 전략·동일 계좌 isolation |
| **G6 Venue Perp E2E** | TX06A–E, TX07B, TX07; 지원 Venue×Product×Mode 명확화 | GLM/DeepSeek; Codex Critical review | 주문/취소/조회/체결/펀딩·재접속·UNKNOWN 정규화 코드는 완성. 실 API credential 실행은 별도 External Gate |
| **G7 연계/확장** | TX09, TX10, TX11의 로컬 항목, VN17, VN18의 로컬 항목 | GLM/DeepSeek + pytest | 데이터 Export 계약, 텔레그램, cross venue partial fill, burst·성능·복구 로컬 검증 |
| **G8 Release Candidate** | TX12, VN19의 패키지 생성·전체 회귀·정적/라이선스/비밀 검사·문서 정리 | 스크립트 + Codex 최종 감사 | 정확 HEAD + test/gate receipts + Docker package + `EXTERNAL_ONLY_GATES.md` 보완표. 실제 서버 배포 없음 |
| **G9 실서버 이후** | P11 전환, 관측/안정화, VN20 레거시 서비스/원본 삭제 | **이번 범위 제외** | 승인 없이는 실행하지 않음. 롤백 경계와 NATS 백로그 보존 |

**DAG 세부:** 기존 `tickets_production.json`의 depends_on 및 read/edit/forbidden paths가 기본 권위다. 위 Wave는 **우선순위/분업 분류**이지 선행조건을 무시하는 직렬 스크립트가 아니다. 이동 이후 경로는 R03이 생성한 매핑에서 동적으로 변환한다. 이미 완료된 VN00–06, VN10–12, VN14A/B, VN16은 무의미하게 다시 구현하지 않는다.

### 5.1 작업 중 반드시 적용할 2가지 수정

- **활성 NATS 코드 제거:** VN20 전체는 `server cutover → 안정화 → rollback 필요 해소` 이후로 보류. 로컬 후보는 NATS-free entrypoint와 독립적으로 빌드/시험하되, 기존 운영 NATS 복구 경로는 삭제하지 않는다. 따라서 "최종 구조에서 NATS 제거"와 "현재 서버의 기존 NATS 활성"을 문서에서 구분한다.
- **외부 환경 전용 Gate 판정:** 공식 API/Fixture/Contract test는 로컬에서 완료할 수 있지만, 실제 권한/마진/체결/장중 시세/프로덕션 72h/실제 이미지–볼륨–계좌 대조는 로컬로 증명할 수 없다. 외부 Gate를 이유로 개발 티켓을 `PASS` 처리하지 않는다.

## 6. 모델 호출/토큰 절약 정책

1. 한 작업은 `task_id/base_sha/read_paths/edit_paths/forbidden_paths/contract/acceptance/test_command`가 포함된 **2~5KB 수준 Task Packet**으로 전달한다. 복잡한 원장 티켓은 해당 계약 부분만 추가한다.
2. 결정론적 인덱스·AST/Import Graph·경로 이동·해시·테스트 실행은 LLM 없이 수행한다. 대량 JSON/테스트 로그를 모델 컨텍스트에 다시 출력하지 않는다.
3. GLM은 R0/R1/R2, 일반 기능/테스트/문서 정리 담당, DeepSeek는 인과적 상태기계·주문 생명주기·Perp Adapter 구현 담당 **후보**로 둔다. 실제 성능이 반대이면 제한된 시험으로 역할을 바꾼다. 모델 이름만으로 품질 보증하지 않는다.
4. 고위험 티켓은 저비용 모델 구현 + 다른 모델의 반례 검토 + Codex 최종 **diff 중심** 승인. 일반 티켓은 `pytest`/Lint/Contract checks가 기본이며 Codex는 예외·Gate만 검토한다.
5. 총 사용량은 `request_count, input_tokens, output_tokens, retries, fallback_provider, model_id, effective_cost, time, test_failures`를 로컬에 집계한다. FreeLLMAPI의 모델 자동 교체는 **수정 티켓에서 기본 비활성 또는 모델 변경 기록 필수**, 품질 확인되지 않은 provider fallback은 자동 수정 금지.
6. `OPEN_CODE_WRITE_SCOPE`: 작업 전용 Worktree·allowlisted paths; `.env`, DB, 운영 자격증명, 키, 서버 SSH·Compose 운영 엔드포인트 접근 불허. OpenCode 외부 모델에 비공개 소스가 전송됨을 이해하고, 사용자가 provider 약관·데이터 보존/학습 정책을 검토한 공급자만 활성화한다.
7. 한 티켓당 성공 조건/실패 반복·재시도 상한을 명시한다. 사용량 한도 소진 시 **작업 중단 → 상태 파일 작성**; Codex가 조용히 코딩을 이어받지 않는다.
8. `release`/full regression은 파일마다 반복하지 않고 의미 있는 경계에서만 수행한다. 오류 수정 중 관련 테스트는 반복 허용하며 위험 상태기계 독립 리뷰는 생략하지 않는다.

## 7. 승인·예외 처리

- **명시적 사용자 승인 불요(현재 범위):** 별도 로컬 브랜치/Worktree의 코드 이동·구현·테스트, 비밀 없는 신규 문서·Config, Diff와 Build 결과 생성.
- **별도 승인 필요:** 실계좌 Mutation, 운영 서버 배포, NATS 원본/운영 데이터/DB 제거, 계정 Activation, 자격증명 외부 전송, 임시 서버 접속, 서버 내 코드 변경.
- **블로커 등급:** `LOCAL_FIXABLE`(구현해서 해결), `CONTRACT_DECISION`(Codex 결정), `EXTERNAL_ONLY`(검증 절차만 작성), `SAFETY_BLOCKER`(전파 차단/Fail-closed). 미지원 Perp 상품을 무리하게 지원 대상으로 표기하지 않는다.

## 8. 작업 완료 보고 형식

각 티켓은 `HEAD / task_id / changed_symbols / exact tests / independent review / outstanding-risk / next-ticket`만 1,000토큰 이내로 보고한다. Git SHA와 테스트 영수증을 남긴다. Final Report는 세 가지 상태를 독립적으로 보고한다.

- `LOCAL_IMPLEMENTATION_COMPLETE`: 코드·배포 구성이 목표 동작을 구현한다.
- `LOCAL_VERIFICATION_COMPLETE`: 정확한 SHA에 대한 회귀·Fault·성능·패키징 증거를 확보했다.
- `EXTERNAL_OPERATIONAL_VALIDATION_PENDING`: 서버/거래소/실계좌/장중·실서비스 시간 관측이 필요한 항목만 남았다.

**첫 작업:** R00/G0에서 현재 HEAD와 Worktree, Repo 진입점, 활성/레거시 호출 관계를 **자동 추출**하고, 기존 티켓에 REPO/O 계열 증분 티켓만 붙인다. 제품 코드를 먼저 대량 수정하지 않는다. 이 세 문서를 추가로 전체 재작성하지 말고 구현 기준으로 삼는다.
