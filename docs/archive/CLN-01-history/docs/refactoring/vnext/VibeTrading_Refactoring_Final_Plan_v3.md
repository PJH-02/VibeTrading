# VibeTrading 최종 리팩토링 실행 계획 v3

> **문서 유형:** 실행 계획 / PM 기준 문서 (명세·요구사항은 별도 문서)
> **작성:** 2026-10-22 KST  
> **검증 기준:** GitHub `PJH-02/VibeTrading` 기본 브랜치 HEAD `a9a83af43897fdf8badc303aaf3b6ebf2eda87c5` (2026-10-21 20:37 KST 커밋). **이는 원격 저장소의 관측 시점 기준선이며 로컬 Worktree·서버 실행 이미지와 동일하다고 보장하지 않는다.**
> **참조 권위:** `docs/refactoring/vnext/tickets_production.json`, `stage_status.json`, `15_stage_status_and_next_steps.md`, 기존 P00–P12/VN/TX 계약과 과거 검증·배포 영수증. 기준선 변경 시 새 Manifest/Diff를 생성하고 과거 기록을 덮어쓰지 않는다.
> **반드시 병행:** `VibeTrading_Developer_Ticket_Spec_v3.md` 및 `VibeTrading_Architecture_Requirements_v3.md`.

> **2026-10-23 binding scope correction (사용자 최신 교정, 이 문서의 우선 권위):** 문서 기준 SHA는 `4977ab509490173e312183f32d47f862d94ee0bf`이며 위 2026-10-21 관측선은 역사 기록으로만 보존한다. 이 프로젝트는 **reuse-first 최소 변경**으로 전환한다. [tech_lead_scope_decisions.json](./reuse_audit/tech_lead_scope_decisions.json)·[revised_dag.json](./reuse_audit/revised_dag.json)·[VibeTrading_Minimal_Reuse_Plan_20261023.md](./VibeTrading_Minimal_Reuse_Plan_20261023.md)·[execution_reuse_matrix.md](./reuse_audit/execution_reuse_matrix.md)가 이 문서의 광범위한 "5 Venue 범용 플랫폼 완성" 문구를 대체한다. 현재 동작하는 ETF/venue 주문·취소·체결·포지션/reconcile과 Ncloud archive/upload/download를 보존하고, 신규 exchange/client/signer/Private Stream은 기본 금지한다. **이번 단계 제품 코드 변경 0, 코드 삭제 0.** TX06 all-five connector rebuild, TX03 extra margin 일반화, TX08B cross-venue atomic group, 새 cloud uploader는 필수 core가 아니다. 정적 감사는 live 검증이 아니며 Codex만 결정·수락한다.

## 1. 사업적 목적과 완료의 정의 (2026-10-23 교정)

이 프로젝트의 현재 목표는 **기존에 동작하는 ETF/venue/cloud/strategy 경로를 보존하면서, NATS-free 데이터 전달·저장·복구와 별도 Python 전략 FILE module을 기존 strategy Contract·shared data·Signals·기존 Paper matcher/ledger/runtime에 연결하고, 입증된 동일계좌 전략 안전성 gap만 완성하는 것**이다. 사용자 전략 알파·백테스트·실계좌 승격 판단은 범위 밖이다.

**이번 작업의 종료 조건:** 선택된 local gap(데이터/복구, 파일 plugin, Paper, 동일계좌 safety, projection/E2E)에 대해 기존 Golden과 최소 기존 runtime fixture로 증거를 확보한다. 서버/실계좌/장중/운영 검증은 별도 External Gate다. 외부 의존을 이유로 코드 미구현을 PASS 처리하지 않는다. **어떤 작업도 이 계획 문서만으로 시작되지 않는다.**

### 1.1 거래·데이터 범위 (reuse-first)

- **KIS:** 기존 C3/KP/KD ETF `mock`/`mock2` 실행과 원장 복원 규칙을 보존한다. legacy `BrokerStub` 부재를 ETF venue 미지원으로 해석하지 않는다.
- **Kiwoom:** Windows COM execution legacy와 REST data-only를 분리해 보존한다. 새 주문 wrapper를 만들지 않는다.
- **TOSS:** **DATA_ONLY**. 새 주문/취소/체결/잔고 Adapter 개발 금지.
- **Crypto:** 기존 Binance spot, Arcus/GRVT/Bybit/Hyperliquid 기존 경로와 test-visible perp mapping/signing composition을 보존한다. **all-five connector rebuild는 필수 core가 아니다.** Spot ≠ perp, mock ≠ testnet ≠ live.
- **동일계좌:** 기존 account Owner/ledger를 재사용하고, 새 multi-strategy 연결에서 입증된 reservation/attribution/cancel ownership/restart safety gap만 다룬다. Cross-venue atomic group은 명시 수요까지 deferred.
- **전략:** 별도 importable Python file + 기존 `STRATEGY_ID/SUBSCRIPTIONS/WARMUP/create_strategy/on_event` contract. C3/KP/KD 수학 함수는 **calculator**이며 plugin 완료가 아니다. 기존 전략 economics·Golden을 보존한다.

## 2. 현재 확인된 사실 및 병목

| 현재 상태 | 근거·결함 | 처리 | 우선순위 |
|---|---|---|---|
| 51개 VN/TX 단계 | `stage_status.json`: 로컬 완료 38(TX00 정적 감사 포함), 부분/미완료 10, 미착수 3. VN17/VN18/VN19/TX11/TX12 수락 전체 2480 PASS/2 제외; 과거 검증 기록 보존 | 완료된 구현 재개발 금지, 최근 HEAD에서 결과 재검증 | P0 |
| 제품 코드의 세대 혼재 | `vibetrading/`, `services/`, `shared/`, `strategy/`, `strategies/`, `fv_mm/legacy/` | 활성 진입점·동적 import·테스트 소비자 분류 후 단일 권위로 통합 | P0 |
| 문서의 복수 권위 | 루트 구형 README·계획, 과거 P/VN/TX 문서·수백 기록 | 현재 README/AGENTS/architecture/status는 짧게, 상세 근거는 보존·archive | P0 |
| 패키지 경로 복잡성 | 최상위 `vibetrading`와 루트 `services` 동시 존재 | `src/vibetrading` 표준 패키징; import/Compose/rollback 경로 동등성 | P0 |
| C3/KP/KD의 전략/실행 혼재 | `vibetrading/etf_trading.py`와 `etf_journal.py`에 실제 전략 존재 | 순수 신호 계산을 안정된 테스트와 함께 분리; 경제적 계산 동일 | P1 |
| 재사용 가능한 핵심 기능 | `paper_execution.py`, `portfolio_ledger.py`, `strategy_runtime.py`는 실제 matcher/ledger/runtime; `perp_execution.py` mapping/signing은 prototype; `execution_groups.py`는 observational | 새 엔진 대신 기존 기능 REUSE/ADAPT; cross-venue group은 deferred | P0 |
| 복구/저장 미완성 | VN07 복구·VN08 reader·VN09 proof-only GC 후보 계획 로컬 완료; 실제 삭제/운영 연결 별도 gate | 기존 recovery/reader/archive 재사용; 실제 운영 데이터 삭제 제외 | P0 |
| Runtime/실거래 실행 | 기존 ETF/Bybit/GRVT/KIS/cloud 경로 동작; Generic Owner는 offline-only; 정규화 Perp는 injected transport | 기존 경로 보존; 새 connector/Private Stream/universal live engine 금지. Live 권한 DISARMED | P0 |
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
| **G4 복구/상태** | VN07·VN08·VN09(로컬 계약 완료; GC dry-run) | DeepSeek/GLM; Codex Critical review | 기존 market recovery/journal/reader/archive 재사용; strategy/Paper checkpoint continuity, Reader prefix, GC dry-run. 실제 삭제 0 |
| **G5 파일 plugin + 기존 Paper** | TX00 정적 감사·TX01/TX02·TX04·VN13/TX05·TX07A/TX08A/TX08 로컬 완료 | GLM/DeepSeek; Codex 계약 결정 | 실제 시장 입력→기존 Runtime→기존 Paper matcher/ledger→restart; 전략별 file module, 동일계좌 safety. TX03 extra margin은 선택 |
| **G6 선택 기존 venue 감사** | TX06A–E/TX06, TX07A/B 중 선택된 route만 | GLM/DeepSeek; Codex review | **신규 connector build 없음.** 기존 testnet/prototype 경로의 함수·caller·environment를 감사하고, 입증된 선택 gap만 별도 티켓. all-five gate 아님 |
| **G7 Projection/E2E** | VN15/TX09, TX10, VN17, VN18, TX11 | GLM/DeepSeek + pytest | actual/assumed/PAPER/recovery projection, 기존 cloud export, local wiring, narrow NATS-free E2E. cross-venue partial fill은 제외 |
| **G8 최소 local acceptance** | TX12, VN19 | 스크립트 + Codex 최종 감사 | 정확 HEAD + scoped receipts + 기존 package/rollback. 범용 Trading Platform 완성 badge 없음, 실제 서버 배포 없음 |
| **G9 실서버 이후** | P11 전환, 관측/안정화, VN20 레거시 서비스/원본 삭제 | **이번 범위 제외** | 승인 없이는 실행하지 않음. 롤백 경계와 NATS 백로그 보존, 삭제 0 |

**DAG 세부:** `reuse_audit/revised_dag.json`이 현재 canonical DAG다. 기존 `tickets_production.json`의 ID/read/edit/forbidden path·완료 evidence는 보존하되, 기존 완료 14개와 추가 로컬 완료 VN07·VN08·VN09을 재개발하지 않는다. 위 Wave는 우선순위/분업 분류이며 선행조건을 무시하지 않는다. TX05는 VN13, TX09는 VN15, TX08 aggregate는 TX08A만 의존하고, TX11 core는 all-five/cross-venue group을 선행으로 갖지 않는다.

### 5.1 작업 중 반드시 적용할 2가지 수정

- **활성 NATS 코드 제거:** VN20 전체는 `server cutover → 안정화 → rollback 필요 해소` 이후로 보류. 로컬 후보는 NATS-free entrypoint와 독립적으로 빌드/시험하되, 기존 운영 NATS 복구 경로는 삭제하지 않는다. 따라서 "최종 구조에서 NATS 제거"와 "현재 서버의 기존 NATS 활성"을 문서에서 구분한다.
- **외부 환경 전용 Gate 판정:** 공식 API/Fixture/Contract test는 로컬에서 완료할 수 있지만, 실제 권한/마진/체결/장중 시세/프로덕션 72h/실제 이미지–볼륨–계좌 대조는 로컬로 증명할 수 없다. 외부 Gate를 이유로 개발 티켓을 `PASS` 처리하지 않는다.

## 6. 모델 호출/토큰 절약 정책

1. 한 작업은 `task_id/base_sha/read_paths/edit_paths/forbidden_paths/contract/acceptance/test_command`가 포함된 **2~5KB 수준 Task Packet**으로 전달한다. 복잡한 원장 티켓은 해당 계약 부분만 추가한다.
2. 결정론적 인덱스·AST/Import Graph·경로 이동·해시·테스트 실행은 LLM 없이 수행한다. 대량 JSON/테스트 로그를 모델 컨텍스트에 다시 출력하지 않는다.
3. GLM은 R0/R1/R2, 일반 기능/테스트/문서 정리 담당, DeepSeek는 인과적 상태기계·기존 원장/Paper/복구 gap 담당 **후보**로 둔다. Perp Adapter 신규 구현은 기본 업무가 아니다. 실제 성능이 반대이면 제한된 시험으로 역할을 바꾼다. 모델 이름만으로 품질 보증하지 않는다.
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

TX00 수락 당시 HEAD의 기존 함수·호출자·환경 정적 감사를 마감했다. 제품 주문 코드 변경0, 기존 cloud/전략/Golden 보존. TX01 및 다른 단계는 당시 TX00 수락에서 착수하지 않았다. [수락 기록](../runs/20261023-v3/tx00-current/acceptance.json).

2026-10-24 TX01 로컬 계약 수락: 기존 Signal/OrderIntent/Paper 경로에 전체 종목·기준 feed·causal 생성시각 검사만 추가했다. 전체2170 PASS/외부2건 제외. 기존 거래소/cloud/전략 동작·Golden 보존; 다른 티켓은 미착수. [수락 기록](../runs/20261024-v3/tx01/acceptance.json).

2026-10-24 TX02A 수락: 기존 Paper 물리 계좌 lease와 checked-API/transaction/cleanup fence 로컬 완료. 동일 보호 root/협조 프로세스 전제이며 기존 ETF·주문·cloud·경제 동작은 보존했다. 전체2204 PASS/외부2건 제외. TX02B 및 다른 티켓은 미착수. [수락](../runs/20261024-v3/tx02a/acceptance.json).

2026-10-24 연속3개 수락: **TX02B → TX02 공동 gate → TX04A**. 기존 내부 attribution 보호와 내구 matcher 정합성만 보완했고 TX02는 제품 변경 없는 통합 검증이다. 전체2266 PASS/외부2건 제외. **23 완료 / 16 미완료 / 12 미착수**이며 TX04B는 이번 범위에서 착수하지 않았다. [배치 기록](../runs/20261024-v3/batch-tx02b-tx04a/README.md).

2026-10-24 다음3개 수락: **TX04B → TX04 공동 gate → VN13**. 기존 watchdog/mapper/ledger/file loader/register를 재사용했다. 전체2322 PASS/외부2건 제외, **26 완료 / 13 미완료 / 12 미착수**. TX05는 이번에 추가 개발하지 않았다. [배치 기록](../runs/20261024-v3/batch-tx04b-vn13/README.md).

2026-10-24 TX05 수락: 기존 파일 전략·Paper·체크포인트 구현을 재사용한 경제 E2E 검증을 완료했다. 제품 코드 변경0, 전체2325 PASS/외부2건 제외. **27 완료 / 12 미완료 / 12 미착수**. 다른 티켓은 착수하지 않았다. [수락](../runs/20261024-v3/tx05/acceptance.json).

2026-10-24 다음3개 수락: **TX07A → TX08A → TX08**. 기존 fact/원장/전략 파일/Paper 복구를 재사용하고 계좌 한도 충돌만 기존 등록 메서드에서 보완했다. 전체 2348 PASS/외부2건 제외. **30 완료 / 10 미완료 / 11 미착수**. 네 번째 티켓은 진행하지 않았다. [배치 기록](../runs/20261024-v3/batch-tx07a-tx08/README.md).

2026-10-25 VN15/TX09/TX10 로컬 수락: 기존 Paper readonly 조회·Telegram/owner/outbox·export를 재사용했다. 전체 2438 PASS/외부2건 제외, **33 완료 / 10 미완료 / 8 미착수**. VN17 및 네 번째 티켓은 진행하지 않았다. [배치 기록](../runs/20261025-v3/batch-vn15-tx10/README.md).

2026-10-25 VN17/VN18/VN19/TX11/TX12 로컬 수락. 전체 2480 PASS/외부2건 제외, **38 완료 / 10 미완료 / 3 미착수**. 최소 local core 근거를 마감했으며 기존 거래소/전략/cloud를 보존했다. VN20_APPLIED=false, 운영/image/account/72h capacity 검증은 외부 gate다. [배치 기록](../runs/20261025-v3/core5/batch-vn17-tx12/README.md).
