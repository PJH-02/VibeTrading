# VibeTrading v3 — 개발자(GLM/DeepSeek)용 구현 명세 및 티켓 수락 기준

> **이 문서는 구현자가 읽는 작업 명세다.** 상위 실행 순서는 `VibeTrading_Refactoring_Final_Plan_v3.md`, 전역 불변조건·데이터 모델·예외 규칙은 `VibeTrading_Architecture_Requirements_v3.md`가 정의한다. 과거 완성된 티켓의 코드/Receipt는 재사용하며 **동일한 기능을 재구현하라는 문서가 아니다.**
>
> **GitHub 관측 기준:** `PJH-02/VibeTrading`, `a9a83af43897fdf8badc303aaf3b6ebf2eda87c5` (2026-10-21). 이 시점의 티켓 상태: VN/TX 총 51개, Complete local 14 / Incomplete 23 / Not started 14. 실제 시작 시 먼저 `git rev-parse HEAD`와 `git status --porcelain`을 확인할 것.
>
> **기본 권위:** 원본 `docs/refactoring/vnext/tickets_production.json`의 기존 ID/depends_on/edit_paths/forbidden_paths와 기존 수락·검증 기록. 아래 **R/O 티켓은 새로운 제안**, `VN/TX` 작업은 과거 티켓의 남은 로컬 수락 내용을 구체화한 **Delta Spec**이다. 충돌 시 조용히 실행하지 말고 `CONTRACT_DECISION`으로 올릴 것.
>
> **2026-10-23 binding scope correction (문서 기준 SHA `4977ab509490173e312183f32d47f862d94ee0bf`; 위 2026-10-21 관측선은 역사 기록):** [tech_lead_scope_decisions.json](./reuse_audit/tech_lead_scope_decisions.json)·[revised_dag.json](./reuse_audit/revised_dag.json)·[VibeTrading_Minimal_Reuse_Plan_20261023.md](./VibeTrading_Minimal_Reuse_Plan_20261023.md)·[execution_reuse_matrix.md](./reuse_audit/execution_reuse_matrix.md)가 이 명세의 광범위한 connector/Platform 문구를 대체한다. 현재 동작하는 ETF/venue/cloud/strategy 경로를 보존하고, **새 HTTP/sign/private wrapper·all-five venue rebuild·universal real margin/lifecycle·cross-venue atomic group·새 cloud uploader는 필수 작업이 아니다.** 기본값은 KEEP이며, 입증된 선택 함수 gap에 대해서만 Tech Lead의 bounded gap ticket 후 제품 변경이 가능하다. 이 문서는 제품 코드를 변경하지 않는다.

## A. 구현 표준 계약

### A1. Task Packet (에이전트에 전달할 정확한 최소 입력)

```yaml
task_id: TX04B                 # 실제 READY ID로 치환
base_sha: <git rev-parse HEAD>
model_profile: pinned-glm-or-deepseek
role: implementer              # implementer | tester | reviewer | mechanical
scope: LOCAL_ONLY
read_paths: [current_target, direct_callers, relevant_test]
edit_paths: [explicit_allowlist]
forbidden_paths: [.env*, '**/secrets/**', 'runtime_data/**', 'ops-ledger/**']
existing_symbols: [real_function_or_class]
contract_ids: [C01, C02, C03, C04, C05, C06, C07, C08, C09]
prerequisites: [named_tickets_or_gates]
objective: one_verifiable_behavior
implementation_steps: [bounded_steps]
acceptance: [deterministic_results]
test_commands: [explicit_commands]
review_severity: low|medium|critical
stop_conditions: [scope_drift, safety_failure, missing_capability]
```

**출력:** `diff` 또는 브랜치 커밋 + 수정 심볼, 실패한/통과한 테스트, 계측치와 실행 환경, 미확정 사항. 제품의 루트 설계 파일·과거 Test Receipt·원장·운영 서버에 대한 무단 변경 금지. 역할이 `reviewer`이면 제품 파일 쓰기 금지.

### A2. 티켓 수락 함수

- `ACCEPT = pristine_base ∧ diff_allowlist ∧ contract_valid ∧ scoped_tests_pass ∧ static_checks_pass ∧ (critical⇒independent_review) ∧ no_real_mutation`.
- 테스트 결과가 없으면 `NOT_RUN`, 모델이 작성한 설명만 있으면 `UNVERIFIED`, 어댑터가 공식 API와 맞지만 실계좌가 없으면 `CODE_COMPLETE_EXTERNAL_VALIDATION_PENDING`으로 기록한다.
- 이동된 파일은 R03의 `path_map.json`으로 과거 티켓 경로를 변환한다. **변환 결과가 2개 이상이거나 동적 import로 불명확하면 코딩을 중단한다.**
- 모든 수정 코드에 대한 정적/단위 검사는 로컬에서 완료해야 한다. `pytest -q <scoped tests>` 후 통합 Gate에서 전체 테스트. 합성 Trace 성능은 실제 거래소/서버 성능으로 과장 금지.
- 실패 시험은 단독으로 고치고 2회 이상 같은 에러를 수정 없이 반복하지 않는다. 생성물·로그 대량 출력은 파일로 보존하고 요약만 모델에 전달한다.

### A3. 역할·모델 배정 (기본)

| 실행 역할 | 기본 실행기/모델 후보 | 입력 폭 | 산출물/Reviewer |
|---|---|---|---|
| `mechanical` | Python AST, pathlib, git mv, Ruff/pytest | 파일 메타데이터 | deterministic tests, Codex는 실패만 |
| `structure` | OpenCode+GLM | 1~3 파일+caller | diff+import/packaging proof, Codex 요약 승인 |
| `feature` | OpenCode+GLM 또는 DeepSeek | 2~5 파일+관련 테스트 | 기능/반례/Fixture, 교차 모델 리뷰 |
| `state_machine` | OpenCode+DeepSeek 또는 GLM (도구 호출 검증한 모델) | 코드+계약+독립 Oracle | Crash/Fault/Idempotency, **Codex Critical Reviewer** |
| `test` | OpenCode+다른 모델 | 변경 diff+expected contract | 구현자 테스트와 독립 실패 사례; 승인 권한 없음 |
| `PM / release gate` | Codex | task summaries, diff, receipts, current SHA | 승인/차단, next READY |

모델 이름을 명세에 고정하지 않는다. `model_profile`은 `/v1/models`에서 확인한 실제 ID와 provider로 기록한다. 자동 fallback 시 모델·제공자·동작이 바뀌면 Acceptance 실행은 처음부터 재검증한다. Code Runner(도구 호출) 오류를 생성 모델 품질 문제와 분리한다.

## B. 신규 Repo/Tooling 티켓 — 반드시 별도 Commits

### R00 — 보호 기준선 및 권위 지도 생성

- **담당:** Python deterministic inventory + GLM(결과 분류); **선행:** 없음.
- **읽기:** `README.md`, `main.py`, `run_strategy.py`, `docker-compose.yml`, Dockerfile 2개, `deploy/`, `config/`, `vibetrading/cli.py`, `vibetrading/local_runtime.py`, `vibetrading/runtime.py`, `vibetrading/etf_trading.py`, `services/`, `shared/`, `strategy/`, `strategies/`, 핵심 테스트/스크립트 참조의 경로·import 구문; 최신 stage/tickets 증거.
- **쓰기:** `docs/architecture.md`의 *초안* 또는 구조화된 `docs/refactoring/vnext/repo_inventory.json`; 제품 코드 수정 **0건**.
- **구현:** `git ls-files`/AST import scanner/`rg`/Compose CLI·Mount 경로 Scanner로 `active_runtime/active_deploy/dynamic_import/tests/research/legacy/rollback` 분류. `C3/KP/KD` 실제 호출 경로와 `Mock/Mock2` 계좌 분리 증거 생성. 실행 중 서버의 상태를 원격 Git로 대체하지 않는다.
- **수락:** 누락된 Entry Point 0(정적 검사 범위), 동적 import 불명확 목록 존재, `source_sha` 고정, active P02 rollback script 보존, 자동 삭제 목록 없음. 중요 경로가 불명확하면 `UNKNOWN`으로 남김.

### R01 — 단일 작업 상태·계약 색인

- **담당:** GLM + deterministic generator; **선행:** R00.
- **읽기:** `stage_status.json`, `tickets_production.json`, `15_stage_status_and_next_steps.md`, `14_agent_execution_handoff.md`, 계약 Delta; 전체 과거 Receipt 재독 금지.
- **쓰기:** 최소 `AGENTS.md`와 `docs/status.md`; 필요 시 `scripts/dev/status.py` 또는 기존 도구 확장.
- **구현:** 지금 로컬 DONE/INCOMPLETE/EXTERNAL 검증 구분, 다음 READY 계산, 마지막 완료 티켓/HEAD 표시. 1KB 단위 Status Overview; 원본 JSON은 권위 유지. 중복된 다른 상태 원천을 만들지 않는다.
- **수락:** 상태 합계/Next READY가 원본과 일치, 실서버 P02만 scoped 적용되었다는 표기를 보존, READ.ME/DOCS가 실제 배포를 주장하지 않음, `AGENTS.md` 기본 읽기 예산·금지 경로·사용 가능한 CLI 명시.

### R02 — 현행 문서 정리·비활성 파일 분류

- **담당:** GLM; **선행:** R00, R01.
- **읽기:** 루트 README·P/VN/TX 문서 제목·링크 인덱스, `docs/`, `scripts/`, `docs/refactoring/runs` 파일의 참조·Git 추적 경로. **본문 전체 일괄 열람 금지.**
- **쓰기:** `README.md`, `docs/architecture.md`, `docs/status.md`, `docs/archive/` 인덱스(선택), 유효한 runbooks. 과거 evidence/rollback/data 경로 수정 금지.
- **구현:** 공식 문서는 현재 **deployed NATS / candidate NATS-free**를 분리. 오래된 PostgreSQL/QuestDB 상용 운용 주장 폐기. `docs/archive` 이동은 외부 참조가 없는 문서부터 단위별로. 과거 Source Receipt/Golden Oracle은 보존; 사용 안 하는 일회성 스크립트도 *우선 quarantine 목록*만 만든다.
- **수락:** 주요 Markdown 링크 및 CLI 예제 검사, 루트 문서에서 서로 다른 아키텍처를 현재 동작으로 동시 기술하지 않음, 보존 목록과 이동 목록이 추적 가능.

### R03 — `src/vibetrading/` 패키지 루트의 기계적 이동

- **담당:** scripts+GLM, Codex 승인; **선행:** R00–R02.
- **읽기:** `Dockerfile`, `Dockerfile.crypto`, `docker-compose.yml`, `main.py`, `run_strategy.py`, `requirements.txt`, `pytest.ini`, 프로젝트 import, Compose의 `./vibetrading/telegram.py` 단일 파일 Mount, `scripts/refactoring/deploy_p02_schema.py` 및 릴리스 빌더.
- **쓰기:** `src/vibetrading/**` 및 **실제 경로를 사용하는** packaging/entrypoints/deploy/tests 한정. 데이터/전략 계산/위험 식·모델 변경 **금지**.
- **구현:** `git mv vibetrading src/vibetrading`, `pyproject.toml` 또는 기존 빌드 계약의 `package-dir=src`에 준하는 최소 변경. 기존 `import vibetrading` 유지. 동적 import와 옛 모듈 실행법은 현재 설치된 정식 경로로 호환. Docker 내부 `WORKDIR/COPY/PYTHONPATH/entrypoint`, 기존 Compose 파일 Mount, 위급 롤백 도구 수정은 실제 실행 호환성 검증이 가능한 범위에서만.
- **수락:** 이전·이후 패키지 import/CLI/pytest/`docker compose config`/Docker image import가 동일. 0 semantic diff(보존된 모듈 AST 비교), C3/KP/KD Golden·P02 배포 도구 빌드/도움말 검증, 전체 Linux tests. **이동이 기존 운영 롤백 스크립트의 소스 경로를 깨면 중단**.

### R04 — 활성/레거시 소유권 통합

- **담당:** GLM/DeepSeek; **선행:** R03.
- **읽기:** R00 ownership map, 현재 전략 loader, `services/signal_gen/**`, `shared/**`, `strategy/**`, `strategies/**`, `fv_mm/legacy/**`, `src/vibetrading/strategy_contract.py`의 현재 위치.
- **쓰기:** `src/vibetrading/strategies/**`, 해당 import caller/테스트. 경로의 Rename과 로직 변경은 **별도 커밋**.
- **구현:** 전략은 한 개의 정식 등록/로딩 경로를 갖도록 통합하되 실제 서로 다른 S1~S4 경제적 논리를 이름만 보고 합치지 않는다. Legacy 실행 경로/백테스트 의존은 독립 용도로 Archive 또는 API Adapter로 보존. 제거 전 `rg`, AST, 동적 import, tests, deploy usages 검사.
- **수락:** 신규 전략의 로더 1개, 중복된 Production Runtime authority 0, retired 경로에 활성 import 0, C3/KP/KD 결과 불변, 전/후 테스트 명시.

### R05 — C3/KP/KD 전략·ETF Runtime 분리

- **담당:** DeepSeek 또는 GLM; Codex 위험 리뷰; **선행:** R03, R04, 기존 VN10~12 완료.
- **읽기:** ETF `EtfTradingLoop`의 `intraday_decision`, C3 open decision, KP/KD 관련 함수, `EtfJournal`, 보조 `Kiwoom/KIS/TOSS` 데이터 로더, C3/KP/KD 기존 Golden tests.
- **쓰기:** `src/vibetrading/strategies/kr/{c3,kp,kd}.py` (ADD, 권고 위치), 기존 ETF loop에서 **선택된 순수 계산 함수의 호출만 변경**, tests.
- **구현:** Feed clock/start/end/available_at, Position/Order/Intent 의미 불변. 단지 신호/결정 계산을 순수 함수로 추출; 동일 Account Routing/Exit rule/계좌 상태 유지. KIS 모의 계좌를 Generic Perp와 혼용하지 않는다.
- **수락:** 기존 Fixture에서 구조 이동 전후 `decision/qty/limit_price/side/timestamp/account_mode` 완전 동일, 실제 주문 API 호출 0, `etf_trading` 모듈 진입점 호환.

### R06 — 안전한 레거시·중복 도구·문서 격리

- **담당:** GLM, Python caller validator; Codex 삭제 후보 승인; **선행:** R04~R05와 관련 도메인 TX/VN 완료.
- **읽기:** R00 ownership, `git grep`, dynamic import, Docker COPY, Pytest, rollback map.
- **쓰기:** 승인된 obsolete 경로만; 제품 활성 Path/Golden/기존 운영 rollback/과거 레코드 **삭제 금지**.
- **구현:** DROP 대신 분리 저장소/`docs/archive`/git-history-only로 단계화. History가 있다고 안전하게 지울 수 있다는 전제 금지: 실제 P02 사용 중인 rollback 도구는 반드시 유지. Replaced wrappers는 시험·Migration 이전에는 남김.
- **수락:** `active_imports=0`, `dynamic_loaders=0`, `docker_references=0`, `rollback_dependencies=0`, `oracles_lost=0`, full regression PASS, 순수 이동·삭제 통계/증거.

### O00 — FreeLLMAPI/OpenCode 로컬 실행기 구성

- **담당:** 사용자 제공 API 계정 + GLM 단위 구성; **선행:** R00, 전용 개발 PC 준비.
- **읽기:** 공식 [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi), [OpenCode providers](https://opencode.ai/docs/providers/), [OpenCode CLI](https://opencode.ai/docs/cli/).
- **쓰기:** 개발 PC 로컬 설정; **프로젝트의 `.env`/운영 키에는 작성하지 않음**.
- **구현:** FreeLLMAPI `127.0.0.1:3001` 개인용 서비스, provider key는 암호화 저장; OpenCode `setup-opencode --dry-run` 후 생성·수정된 설정 검토. 선택된 GLM/DeepSeek의 pinned ID·실제 upstream 및 tool-call round trip을 각각 시험. 타 모델로 silent fallback 금지. 모델이 스스로 git push·운영 서버 접근 불가하도록 permission 설정.
- **수락:** OpenCode TUI와 `opencode run --model <provider>/<model>` 작업 1회, READ/WRITE/pytest 호출 성공, 의도한 실패 호출 실패 처리, API key 로그·Git 유출 0, provider/model/limits 증거, 모델별 소비 기록. 증명이 없으면 `MODEL_UNSUPPORTED`.

### O01 — 작은 Ticket Runner 및 결과 수락기

- **담당:** GLM + deterministic Python; **선행:** R01, O00.
- **읽기:** `tickets_production.json`, `stage_status.json`, R03 path map 및 정확 git status.
- **쓰기:** `scripts/dev/agent_task.py` 권고 (실제 구조에 맞게 위치 선택) 및 소수 테스트; 또 다른 상태 DB 생성 금지.
- **CLI 제안:** `list-ready`, `packet <ID>`, `check-scope <ID>`, `check-tests <ID>`, `record <ID>`, `handoff`. `packet`은 JSON→2~5KB MD/YAML 추출. `check-scope`는 실제 Git Diff를 allowlist와 비교. `record`는 승인된 Receipt만 남기며 Legacy 상태를 덮어쓰지 않는다.
- **수락:** 거짓 선행 Gate 거절, 의도하지 않은 파일 수정 검출, 비밀 경로 자동 차단, TTL·중단상태·중복 처리, 다른 모델의 Patch 재현, synthetic task 2건에서 Codex 전체 repo 재독 필요 0회.

## C. VN 기존 티켓의 잔여 개발 명세 — DONE은 수정 금지

### C0. 재개 시 구현 금지(이미 선별 로컬 수락 완료)

| 기존 ID | 검증·재사용할 현재 기능 | 다시 열어야 하는 경우 |
|---|---|---|
| VN00 | Source/image/config/mount baseline (scope 한정) | 현재 HEAD·Deploy 상태 차이가 실제로 발견됨 |
| VN01 | bounded first-fault/lag 계측 | 성능/큐 관측 오류가 테스트로 발견됨 |
| VN02·VN03 | feed별 오류 격리 및 ETF dependency gate | Producer/Instrument 별 실패 전파 반례 발견 |
| VN04A·VN04B | durable spool + UDS C/H/verified replay | 복구 원장·연속성 반례 또는 R03 Import 실패 |
| VN05 | bounded group sync gather 및 synthetic timing | 실제 local benchmark에서 부정적 회귀 확인 |
| VN06 | Native Book/Window/History codec/checkpoint | 최신 R03 빌드 또는 Snapshot 반례 실패 |
| VN10·VN11·VN12 | canonical ETF lease, evidence COMMIT, fill/ACK 단조성 | 계좌 범위 충돌·주문 안전성 반례 실패 |
| VN14A·VN14B | scoped durable halt/resume 및 opt-in Telegram/UDS | 소유권/명령 중복·인증·TTL 반례 실패 |
| VN16 | Storage source-specific quarantine 및 healthy lane | 공통 Volume/DB 오류 범위의 잘못된 분류 |

DONE의 상태표가 서버 운영 수락을 의미하지는 않는다. **구현이 끝났다는 사실과 신규 릴리스 후보에서 그 기능을 재검증해야 한다는 사실을 분리**한다.

### VN07 — Market Checkpoint + ETF/Paper/Strategy 단계적 복구 (로컬 완료)

- **소유 파일:** `vibetrading/local_runtime.py::replay_startup`, `vibetrading/local_engine.py::{begin_recovery,apply_replay,handoff}`, `vibetrading/market_checkpoint.py`, `vibetrading/etf_trading.py`의 startup seam, `vibetrading/portfolio_ledger.py`, `vibetrading/paper_execution.py`, `vibetrading/strategy_runtime.py`; 적용 시 R03 path map 사용.
- **현재 상태:** Market checkpoint, producer durable order, journal lifecycle **구현 완료**. VN07B에서 ETF/Paper·전략 상태의 동일 경계 검증과 단계적 복구를 **로컬 완료**했다. 정확히 일치하는 cut만 복구하고, 앞·뒤 저널/미확인 suffix/불명확 handoff는 전체 격리한다. 독립 SQLite 간 atomic commit이나 연속 Paper suffix 처리는 주장하지 않는다. [수락 기록](../runs/20261023-v3/vn07b/acceptance.json).
- **구현(REUSE 우선):** 기존 market checkpoint/journal, `PaperExecution`/`PortfolioLedger`, ETF journal 위에서 `strategy claim cursor / paper order+fill journal version / ETF journal pending handoff`의 복구 순서만 연결한다. Universal account-state replacement는 금지. Startup `RECOVERING`에서 전략 Callback warmup 허용 여부와 **신규 intent 전송 0** 계약. `History.available_at`과 feed generation 보존. Crash 지점마다 recover/reconcile→Ready 판정; replay output은 원장을 재전송하지 않는다.
- **테스트:** Market checkpoint 단독 복원 / 저널보다 앞·뒤인 input / partial write / invalid pending / double replay / newest checkpoint corruption / ETF·Paper 동시 재시작 / broad loss / source-missing fail-closed. 동일 원본에서 recovered booked balances·working orders·strategy claims 동등. 준비 불충분 시 신규 위험주문 0.
- **Critical 리뷰:** Codex가 multi-ledger 원자성의 **실제** 보장 범위를 확인. SQLite 파일 두 개가 있을 때 원자적 Commit이라고 주장하지 않음. 필요한 경우 staged recovery/quarantine 방식 채택.

- **최근 완료 하위 작업 VN07A:** 기존 Paper `strategy_event` 처리 기록으로 재시작한 전략의 상태 복원 미확인 시 신규 콜백·위험주문을 차단한다. 정상 동일 실행/REPLAY warmup과 명시적 pause/halt 취소는 보존한다. [수락 기록](../runs/20261023-v3/vn07a/acceptance.json). VN07A 당시 열려 있던 전략 내부 상태 checkpoint·검증 복원·시장/Paper 경계는 후속 VN07B에서 로컬 완료했다. VN07B 수락 당시 VN08은 미착수였으며, 후속 VN08 로컬 수락은 아래 기록을 따른다.

- **VN07 전체 검증:** 제품 1,850 + tooling 169 = **2,019 PASS**, live-data 2건 제외. 기존 Golden/거래소 주문·cloud 동작 보존. 복수 실제 Paper 원장과 ETF/Paper 동시 복구, 상태 hook/준비 검사 중 저널 변조, 최신 파일 손상 및 출력 경로 충돌 반례 포함. Production gate는 BLOCKED이며 별도 서버 배포/wiring은 VN17 범위다.

### VN08 — Retained-prefix aware Spool Reader (로컬 완료)

- **소유:** `vibetrading/market_spool.py::{SpoolReader,_inventory,scan_spool}` 및 `vibetrading/local_storage.py::{LocalSpoolStorage,_FactLedger}`, 관련 Reader Tests.
- **선행:** VN07. **목적:** 원본 전체를 보존한 v1 Reader와 `base_cursor>0`의 새 Reader를 혼동하지 않음.
- **구현:** checkpoint의 digest/gen/first-retained segment를 재생 시작 증거로 사용. 유실된 gap, chain mismatch, truncated header, CRC miss, fence mismatch 시 비정상 복구를 거부. 여러 reader가 서로 다른 Base를 가질 때 oldest needed range 보존.
- **테스트:** v1 전체 Replay 동일, rotated segments, missing first/last segment, reader pin·crash·bad proof, 즉시 LIVE 거절. GC는 계속 비활성.

- **로컬 수락:** 명시적으로 pin한 hash/generation·first-retained segment 증거와 실제 저장소 progress/manifest/fact 검증을 연결했다. 단조 durable watermark, record/segment/absolute cursor 한도와 consumer별 base 검증을 포함한다. 기존 v1 writer/frame/checkpoint AST 불변. 전체 **2,055 PASS**, live-data 2건 제외. [수락 기록](../runs/20261023-v3/vn08/acceptance.json). GC·생산 데이터 삭제·자동 cutover/LIVE 승격은 없었다. VN08 수락 당시 VN09는 미착수였으며 후속 dry-run 수락은 아래 기록을 따른다.

### VN09 — GC 증거 기반 후보 계획 및 실패 시 보존 (로컬 완료, dry-run)

- **소유:** `market_spool.py`, `local_storage.py`, `archive.py`의 실제 증거 함수; 새 별도 GC framework 금지.
- **선행:** VN08, VN16. **구현:** Segment별 `checkpoint_coverage ∧ reader_progress ∧ parquet_commit/provenance ∧ remote_integrity/readback ∧ retention_elapsed ∧ lease_free`를 만족한 경우에만 회수 **후보**. Atomic planner는 `dry_run=True` 기본. S3/객체 저장소가 막히거나 incomplete progress면 보존. Checkpoint rollover/crash와 GC 경쟁 중 delete 의도를 durable record로 분리.
- **테스트:** 오류 주입을 포함한 사본에서 eligible/forbidden segment 목록 일치, **유일본 삭제 0건**, remote hash mismatch 0 삭제, data-root mount 잘못 지정 0 삭제. **운영 원본의 실제 삭제 실행은 별도 승인**.

- **로컬 수락:** 기존 checkpoint/reader/storage/manifest·compaction·원격 Parquet/sidecar/원본 spool readback 증거의 교집합으로 후보를 계산하고, 경로 경합에도 기존 파일을 덮어쓰지 않는 비실행 제안서를 남긴다. 전체 **2,119 PASS**, live-data 2건 제외. 기존 함수 110개 AST·Golden·uploader 유지. [수락 기록](../runs/20261023-v3/vn09/acceptance.json). 원본 prefix·원격 spool 사본·owner 확인 reader/lease 입력이 없으면 전체 보존한다. 실제 GC/삭제·자동 운영 연결은 포함하지 않으며 production gate는 BLOCKED다.

### VN13 — 전략 FILE plugin → 기존 Paper (TX05와 한 workstream)

- **소유:** 신규 `src/vibetrading/strategies/<strategy-id>.py` 또는 `strategies/kr/{c3,kp,kd}.py`, 기존 `strategies/contract.py` seam, `strategy_runtime.py`, `local_runtime.py`의 최소 wiring, 관련 tests.
- **재사용:** `STRATEGY_ID/SUBSCRIPTIONS/WARMUP/create_strategy/on_event` 기존 contract, `PaperStrategyRuntime.register/process_event/_dispatch`, `PaperExecution`/`PortfolioLedger`. C3/KP/KD 수학 함수는 **재사용 calculator**이며 file plugin 완료로 간주하지 않는다.
- **최소 구현:** 전략별 importable Python file, explicit config/subscriptions/warmup, shared data가 만든 Signal을 기존 Paper로 전달. 신규 runtime에 hardcoded strategy-ID branching 금지. registry/config 등록은 명시적으로만. `SHADOW/REPLAY` 신규 주문 0, PAPER 외부 mutation 0.
- **보존 gate:** 기존 전략 economics와 Golden decision/qty/price/account_mode 불변. ETF runtime을 새 Paper 원장으로 이관하지 않는다.
- **테스트:** 기존 Golden 먼저, 가장 작은 기존 runtime fixture, file discovery/registration, shared data→Signal→Paper, restart continuity. 새 fault test는 local data/Paper gap에만.

### VN15 — actual/assumed/Paper/recovery Projection (TX09와 한 workstream)

- **소유:** `telegram_trade.py`, `telegram.py`, `vibetrading/dashboard/bridge.py`, 기존 Owner/Portfolio read-only snapshot.
- **재사용:** `EtfTradingLoop.account_report :708`, `report_daily :698`, `EtfJournal.notify :290`, 기존 Telegram command/ACL 경로. 새 dashboard platform을 만들지 않는다.
- **최소 구현:** `actual`, `assumed/PAPER`, `recovery/unreconciled` label 분리. `PnL=realized/unrealized/fees/funding`과 통화·기준가격·timestamp·freshness; 근거 불명은 `UNAVAILABLE`. cold read only, submit hot path reporting I/O 0. `/pause,/halt,/resume`은 기존 권위 Owner로 전달.
- **테스트:** missing FX/stale mark/incomplete fill/Telegram outage/restart stale snapshot. 기존 `/trade`, Mock2 보고 회귀.

### VN17 — 기존 runtime의 최소 local wiring

- **소유:** `local_config.py`, `scripts/refactoring/build_release_manifest.py`, 기존 runtime mount tests. 새 topology가 필요하면 별도 Tech Lead ticket.
- **재사용:** 기존 collector/engine/storage/runtime/Telegram 경로와 현재 배포 구성을 우선한다. 역할별 프로세스/자원 상한은 실제 local 병목에만 추가한다.
- **최소 구현:** NATS-free selected data path와 strategy-file/Paper/projection을 기존 runtime에 연결. 운영 server config 자동 수정 금지, universal execution deployment topology 금지.
- **테스트:** 기존 role import/compose config 유지, bounded queue/resource, stale source SHA. 합산 CPU/RSS/PSS는 기존 profile로 평가.

### VN18 — 기존 Golden + 선택 local fault gate

- **소유:** 기존 test runner, smallest existing runtime mocks/transport fixtures, `docs/refactoring/runs/<new-run>/` 요약.
- **재사용:** 기존 Golden, `PaperExecution`/`PortfolioLedger`/`strategy_runtime` fixtures, data recovery tests. 새 venue reconnect 구현 금지.
- **최소 테스트:** **unchanged Golden first**; selected data/storage recovery, strategy file/Paper restart/dedup, same-account reservation/cancel/restart fault. 새 테스트는 누락 local gap에만. live operation은 외부 gate이며 자동 재실행하지 않는다.
- **수락:** 기존 runtime behavior equivalence, mutation 0, unknown reservation 유지. 과거 HEAD 1961 pass는 과거 사실이며 이 문서 단계의 새 PASS가 아니다.

### VN19 — 기존 packaging/export/rollback 재사용

- **소유:** 기존 release manifest, `cutover_runbook.md`, `rollback_runbook.md`, hash scripts.
- **재사용:** 기존 packaging/export/rollback, `deploy_p02_schema.py`, Ncloud archive 경로. 새 uploader/backtest platform 없음.
- **최소 구현:** 정확 SHA, 기존 image/volume/schema 호환, selected local data-path 전환 절차와 중단 조건. 명령은 승인 전 실행 금지.
- **수락:** 기존 image import/entrypoint와 Compose config, rollback 보존, 원장 과거 이미지 덮어쓰기 0.

### VN20 — 외부 proof 이후 legacy 정리 (삭제 금지)

- **현재:** 참조 지도·삭제 candidate list만. NATS/legacy 복구 경로·P02 rollback·운영 데이터는 보존한다.
- **Gate:** `P11 cutover → 안정화 → rollback 필요 해소`와 active import/dynamic/deploy/rollback/Golden 무영향 증명 이후 **별도 승인**.
- **표기:** `VN20_PREPARED`, `VN20_APPLIED=false`; 이번 단계 소스 삭제 0. 문서 완료 ≠ 실제 제거.

## D. TX 기존/신규 Trading Infrastructure 세부 명세

> TX 상위 집계 단계(예: TX02, TX03, TX04, TX06–08)는 하위 티켓이 구현되었다고 `PASS`가 되지 않는다. **실제 같은 계좌·전략·Replay E2E**에서 수락해야 한다. 아래 테스트 파일은 기존 티켓에서 제안된 경로를 우선 사용하고, 없는 파일은 ADD로 명시한다. 기존 제품 코드에 없는 심볼은 `(ADD)`로 구분한다.

### TX00 — Reuse Audit (기존 경로 인벤토리)

- **소유:** `docs/refactoring/vnext/reuse_audit/**`의 static evidence와 `execution_reuse_matrix.md`; 제품 코드 수정 0.
- **선행:** VN00. **현재:** 로컬 정적 재사용 감사 완료(제품/실거래 지원 완료 의미 아님).
- **구현:** `(venue, product, environment, account domain, position/margin mode)`별 **기존** 함수와 caller를 기록하고 `TOSS=DATA_ONLY`를 적용한다. `SUPPORTED_CODE/FIXTURE/SANDBOX_REQUIRED/LIVE_REQUIRED/UNSUPPORTED/UNKNOWN`은 honest label이며 빈 generic wrapper만으로 미지원을 선언하지 않는다. 새 connector/registry 구현 없음.
- **수락:** 모든 발견 path와 `Class.method/path:line` 링크, UNKNOWN/N/A 명시, secret 없음, raw audit와 machine evidence의 한계 표기. 601-function/245-call evidence를 수기/live 검증으로 과장하지 않는다.
- **현재 수락:** HEAD `fa2478e5` 기준 47파일·712정의·510입력·459정적 참조를 대조했다. 역사692정의 중679동일/13변경, 복구20추가/삭제0. [현재 감사](./reuse_audit/tx00_current/closure.md)·[수락](../runs/20261023-v3/tx00-current/acceptance.json). 기존 TOSS36건과 독립 경계14건 PASS. TX01 제품 변경은 이 감사에 포함하지 않는다.

### TX01 — 기존 Signal/OrderIntent·strategy contract 재사용

- **소유:** `trading_contracts.py`, `strategy_contract.py`, `execution_owner.py`의 현재 seam; 기존 ledger tests.
- **선행:** TX00. **현재:** 기존 Signal/OrderIntent/Paper 계약 로컬 완료(2026-10-24).
- **구현(ADAPT):** 기존 immutable `OrderIntent`/`Signal`과 `ExecutionOwner`/`PaperExecution` 계약을 재사용한다. 필요한 경우에만 local Paper/strategy identity bridge를 최소 추가한다. **universal replacement action API나 새 order engine을 만들지 않는다.** 금액/수량 Decimal 원값, 동일 ID 다른 payload 거절, Spot≠Perp, TOSS mutation 거절을 유지한다.
- **수락:** 기존 Signal fixture 호환, Paper 외부 mutation 0, invalid tick/lot/mode reject, 기존 ledger checksum 불변.
- **현재 수락:** 새 API 없이 기존 `_validate`의 NEW 사전 검사17줄을 보완했다. 전체 InstrumentId/기준 feed/ID/생성시각을 검증하고 Kiwoom→KIS ETF Paper·wire·Decimal·기존 matcher/ledger를 보존했다. 독립287건, 전체2170건 PASS(외부 live-data2건 제외). [수락](../runs/20261024-v3/tx01/acceptance.json). TX02A 이후와 전략 plugin 완성을 뜻하지 않는다.

### TX02A — 기존 owner/lease 재사용, 단일 writer 증명

- **소유:** `portfolio_ledger.py`, `etf_journal.py`, `process_lease.py`, `execution_owner.py`의 기존 seam.
- **선행:** TX01, VN10. **현재:** PAPER_SCOPED_LEGACY_FENCING_OPEN.
- **구현(ADAPT):** ETF `_acquire_ownership`/`EtfJournal`과 `PortfolioLedger` lease를 재사용한다. 선택된 mode에서 하나의 physical account writer만 허용하고, 다른 DB path/중복 process/stale epoch를 거절한다. ETF `mock`/`mock2` 구분 보존, 새 account engine 금지.
- **수락:** 같은 계좌 2 owner 동시 허가 0, owner 장애/restart fence, ledger mismatch 거절, 다른 계좌에 불필요한 global lock 0.

### TX02B — 기존 journal/ledger의 attribution gap만

- **소유:** `PortfolioLedger.apply_fill/_strategy_credit`, `EtfJournal.apply_status/logical_book`.
- **선행:** TX02A. **현재:** PAPER_SCOPED.
- **구현(REUSE):** ETF는 `EtfJournal`, 신규 PAPER는 `PortfolioLedger`를 권위로 유지한다. Fill→physical+virtual+fee+reservation의 기존 transaction을 재사용하고, 입증된 same-account attribution/reservation/restart gap만 보완한다. external fill 임의 strategy 귀속 0.
- **수락:** 두 전략 반대 포지션 physical/virtual equation, pending worst-case 미상쇄, restart 후 attribution 동일.

### TX02 — 기존 owner/books 공동 gate

- **선행:** TX02A/B. **구현 중복 금지:** 소유권/장부를 Portfolio/ETF에 다시 만들지 않는다. **수락:** 선택 mode의 reserve/partial-fill/cancel/unknown/restart에서 기존 원장 재계산 일치, `sum(released)≤sum(original)`.

### TX03A — 상품 일반화는 DEFER, 기존 Paper 범위 재사용

- **소유:** `trading_contracts.py`, `portfolio_ledger.py`의 기존 ProductSpec/CAPABILITIES.
- **선행:** TX01. **현재:** CASH + isolated linear PAPER만 확인.
- **구현:** 기존 isolated-linear/cash 범위를 재사용한다. Inverse/cross/hedge/portfolio margin과 추가 상품은 **명시적 전략 수요 전까지 DEFER**하며, 미지원은 `UNSUPPORTED`로 정확히 차단한다. 새 universal margin engine/fake cross formula 금지.
- **수락:** 기존 fixture 불변, invalid qty/collateral/currency reject, unsupported fail-closed.

### TX03B — 선택된 Paper 회계 gap만

- **소유:** `PortfolioLedger.apply_fill/_margin/_capital/apply_funding`, `EtfJournal.apply_status`.
- **선행:** TX02B, TX03A. **현재:** PAPER_CASHFLOW_ACTUAL_MARGIN_OPEN.
- **구현(REUSE):** 기존 fee/realized/funding identity와 reservation/position 분리를 재사용한다. 선택된 PAPER 상품의 누락 회계만 보완하고, 실제 live margin/FX/mark 역산은 `UNAVAILABLE`로 남긴다. Universal live margin ledger rewrite 금지.
- **수락:** fee currency/funding 중복·역순/mark stale/external cash movement, 기존 sum invariant. Fake 0 또는 `FULL_READY` 금지.

### TX03 — 선택 상품 gate (필수 core 아님)

- **선행:** TX03A/B. **수락:** 명시 선택된 기존 Venue×Product×Mode에서만 기존 ledger 일관성, profile 누락 시 위험 주문 0. broad perpetual platform 완성은 요구하지 않는다.

### TX04A — 기존 Paper durability 재사용

- **소유:** `paper_execution.py::{_transition,submit,cancel,_fill}`, `portfolio_ledger.py::{transaction,save_paper_state}`.
- **선행:** TX02B. **현재:** LOCAL_IMPLEMENTED_TEST_PASS_PRODUCTION_OPEN.
- **구현(REUSE):** 기존 atomic transaction과 `_aligned` restart check를 재사용하고, 입증된 restart/dedup gap만 보완한다. `intent_id+fill_serial+source_event` identity, crash-after-write/before-visible, conservative fill semantics 유지.
- **수락:** submit/cancel/fill 중 exception/disk full/kill, restart 후 balance/position/open orders 일치, fill 1회, 외부 mutation 0.

### TX04B — NATS-free 연속 입력을 기존 matcher에 연결

- **소유:** `paper_execution.py::{advance,_arrive,_sweep,on_book,on_trade}`, `market` snapshot seam.
- **선행:** TX04A, VN03. **현재:** continuous input/recovery open.
- **구현(ADAPT):** 기존 matcher에 continuous local data/replay input을 연결한다. causal timestamps(`received/decision/submitted/arrives/cancel_effective/market_event`)와 gap/stale/epoch reset 시 신규 risk admission 차단, maker queue uncertainty, depth 기반 taker를 유지한다. 새 exchange simulator/client 금지.
- **수락:** post-only cross reject, cancel/fill race, gap/duplicate/order inversion, deterministic replay, 외부 API 0.

### TX04 — 기존 Paper E2E gate

- **선행:** TX04A/B. **수락:** local event→기존 matcher→single durable fill→기존 ledger/PnL→restart continuity; uncertainty 표시, 외부 mutation 0.

### TX05 — VN13과 동일 workstream

- **소유:** `PaperStrategyRuntime.register/process_event/_dispatch`, `strategies/contract.py`, 기존 local runtime wiring.
- **선행:** VN13. **현재:** PAPER_FIXTURE_PRODUCTION_BLOCKED.
- **구현:** 별도 Worker/Runtime을 만들지 않고 VN13의 전략 file module을 기존 `PaperStrategyRuntime`에 등록한다. shared state 1회 apply, duplicate signal/intent 0, dependency/warmup/exception/TTL/bounded queue, PAPER 외부 mutation 0. Hard isolation을 주장하려면 측정된 process/pool 계약이 필요하며, 없으면 cooperative limitation을 명시한다.
- **수락:** 기존 Golden, strategy file register→Signal→Paper→PnL, slow/raise/stale snapshot, ETF 무관 feed 장애, `SharedStrategyRunner` 중복 live 권위 0.

### TX06A — Binance: 기존 connector 보존, 신규 build 금지

- **소유/근거:** `services/execution/crypto_binance.py`(spot), `perp_execution.py::BinancePerpAdapter`(test-visible mapping), legacy `brokers.py`. **선행:** TX00.
- **범위:** 기존 spot과 perp mapping/environment를 **감사·비교**한다. **새 HTTP/signer/private wrapper를 만들지 않는다.** Binance spot을 perp로 선언하지 않는다. 선택된 local bridge가 실제로 필요하면 function-level caller·환경 증거와 Tech Lead gap ticket이 먼저다.
- **수락:** 기존 함수/transport 보존, spot≠perp, UNKNOWN/UNSUPPORTED honest label, 무허가 mutation 0. Live 검증은 external.

### TX06B — Arcus: 기존 WS/signer 보존

- **소유/근거:** `extended_brokers.py::ArcusBrokerAdapter`, `perp_execution.py::ArcusPerpAdapter`/signer composition. **선행:** TX00.
- **범위:** 기존 WebSocket RPC, Ed25519 signer/nonce, order/status/account 경로를 보존하고 testnet/live 차이를 기록한다. 새 generic transport/nonce client를 만들지 않는다. 누락 capability는 함수·caller 증거 없이는 개발하지 않는다.
- **수락:** 기존 서명/경로 불변, unsupported/UNKNOWN fail-closed, mutation 0. Live external.

### TX06C — GRVT: 기존 fv_mm/private/reconcile 보존

- **소유/근거:** `fv_mm/venues.Grvt`, `fv_mm/private.grvt_private`, `extended_brokers.GrvtBrokerAdapter`, `perp_execution.GrvtSigningComposition`. **선행:** TX00.
- **범위:** 기존 testnet `*_USDT_Perp`, EIP-712/signing, private `v1.order/v1.fill`, reconcile을 재사용한다. **incomplete generic adapter를 working exchange 연결 부재로 해석하지 않는다.** `native_client_id` bit-width/hash 결함은 **UNVERIFIED**이므로 이 ticket에서 수정하지 않는다.
- **수락:** 기존 경로 보존, 중복 framework 0, client-ID defect evidence 없으면 변경 0.

### TX06D — Bybit: testnet 경로 보존

- **소유/근거:** `liquidity_mm/exchange.TestnetClient`, `fv_mm/venues.Bybit`, `bybit_execution.BybitOrderClient/Probe`, `perp_execution.BybitPerpAdapter`. **선행:** TX00.
- **범위:** 기존 testnet-only 실행/조회/정규화를 재사용하고, probe를 production live authority로 승격하지 않는다. 새 duplicate order/private client·새 wrapper 금지. `run`의 live block과 testnet gate를 보존한다.
- **수락:** duplicate execution dedup, cancel ACK≠terminal, unknown reservation, live mutation 0. Live external.

### TX06E — Hyperliquid: SDK/broker 보존

- **소유/근거:** `brokers.HyperliquidBrokerAdapter`(read/stub), `perp_execution.HyperliquidPerpAdapter`/SDK signing composition. **선행:** TX00.
- **범위:** 기존 read path와 SDK signer composition을 보존하고, generic transport 미구현을 자동 개발 사유로 삼지 않는다. Native action/nonce/subaccount/vault는 기존 composition 범위에서만 감사한다.
- **수락:** broker placeholder와 perp composition 분리, existing signer 불변, mutation 0.

### TX06 — 선택 venue audit gate (필수 core 아님)

- **선행:** TX00. **수락:** 선택된 기존 route 하나에 대해 함수/caller/environment가 matrix와 일치하고, UNKNOWN/N/A가 honest하다. **all-five rebuild gate는 제거**하며 완료 adapter prototype evidence를 지우지 않는다.

### TX07A — 기존 fact 위 최소 projection

- **소유:** `trading_contracts.py`, `execution_owner.py`, 기존 `execution_ledger.py`, `tests/refactoring/test_tx07a.py`(존재 시 재사용).
- **선행:** TX01. **구현(ADAPT):** 기존 `Runner.order_event/execution_event`, `fv_mm.on_order/on_fill`, `EtfJournal.apply_status`, `PaperExecution._fill`의 identity/terminal 규칙 위에 필요한 canonical 이름만 projection한다. 두 번째 universal lifecycle state machine 금지. ACK-after-fill이 terminal을 역전하지 않고 cumulative fill이 단조다.
- **수락:** 기존 ledger checksum, late/duplicate fill, ACK/fill 역순, UNKNOWN positive match, 기존 Golden 불변.

### TX07B — 기존 reconciliation 재사용

- **소유:** `liquidity_mm.Runner.reconcile`, `fv_mm.Runner.reconcile`, `EtfTradingLoop.reconcile`, `PortfolioLedger.reconcile_positions`, `perp_execution._Adapter.reconcile`.
- **선행:** TX00, TX07A. **구현:** 선택된 경로의 입증된 cursor/seq/window/absence gap만 보완한다. **모든 venue에 새 Private Stream을 강제하지 않는다.** 일부 조회 실패는 미제출 증거가 아니며 ambiguous reservation을 유지한다.
- **수락:** existing reconcile 동작 보존, false absence 0, unknown 유지, 선택 path 외 변경 0.

### TX07 — 선택 execution equivalence gate (필수 core 아님)

- **선행:** TX07A/B. **수락:** 선택된 기존 runtime이 order/cancel/UNKNOWN/fee/funding을 중복 없이 projection하고, blocked 상황에서 unsupervised retry가 0. 새 universal lifecycle engine을 요구하지 않는다.

### TX08A — 동일계좌 전략 safety (우선)

- **소유:** `portfolio_ledger.py::{admit_order,_gross_notional,_reserved}`, `strategy_runtime.py` risk binding, `etf_journal.py` 기존 book.
- **선행:** TX02, TX05, TX07A. **현재:** PAPER_SHARED_ACCOUNT_FIXTURE_IMPLEMENTATION.
- **구현(REUSE/ADAPT):** 기존 account-wide serialized reservation, strategy budget, worst-case exposure를 재사용한다. 두 전략 반대 포지션이 net zero여도 pending/cancel-pending/UNKNOWN을 낙관 상쇄하지 않는다. **새 multi-strategy 연결에서 입증된** attribution/reservation/cancel ownership/restart gap만 보완한다. 기존 ETF runtime 재작성·새 universal risk engine 금지.
- **수락:** 2+ same-account strategies, reservation race/crash/restart, physical=virtual+external equation, cancel ownership, one physical writer, strategy fairness. Paper first, live rewrite 없음.

### TX08B — Cross-venue group: DEFER

- **소유:** 기존 `execution_groups.py`, `PortfolioLedger.record_group_fact/group_facts`.
- **선행:** TX08A, TX07A. **현재:** PAPER_OBSERVATIONAL_COORDINATOR_IMPLEMENTED.
- **범위:** 기존 observational group facts를 보존한다. **cross-exchange atomic execution, 자동 hedge/unwind, 새 group state machine은 명시 전략 수요 전까지 deferred**이며 core multi-strategy acceptance가 아니다. 분산 원자성 주장 0.
- **수락:** 기존 group fact 동작 보존, account Owner 권위 미대체, 추가 구현 0이면 0으로 보고.

### TX08 — Same-account gate only

- **선행:** TX08A. **수락:** selected same-account attribution/reservation/cancel/restart evidence, margin over-reservation 0, stale risky admission 0. TX08B/cross-venue는 선행·수락 조건이 아니다.

### TX09 — VN15와 동일 Projection/Control workstream

- **소유:** `telegram.py`, `telegram_trade.py`, `dashboard/bridge.py`, `local_observation.py`, 기존 `account_report`/`notify`.
- **선행:** VN15. **구현:** VN15의 actual/assumed/PAPER/recovery label을 `/status,/feeds,/strategies,/orders,/positions,/pnl`에 투영한다. 기존 `/trade`와 Mock2 보고를 보존하고, `/pause,/halt,/resume`은 기존 권위 Owner로 전달한다. 새 dashboard platform·submit-path reporting I/O 금지.
- **수락:** numeric ACL/TTL/dedup, Telegram 장애에서 account mutation 1회 이하, cold read가 hot path를 막지 않음, unknown fail-visible.

### TX10 — 기존 Ncloud/Export 보존 + plugin metadata 연결

- **소유:** 기존 `storage_backend.archive_backend`/`NcloudStorageBackend`, `ParquetHistoryReader`, 기존 registry seam.
- **선행:** TX01. **구현(REUSE):** working Ncloud put/verify/download와 기존 export/manifest를 **변경 없이 보존**한다. 누락된 data-manifest→strategy file/Paper registration linkage만 최소 추가한다. 새 uploader/backtest platform/SDK client 금지.
- **수락:** 기존 upload/download/checksum 회귀, timestamp/available-at/ffill invariant, `PAPER→LIVE` config toggle만으로 real API 0.

### TX11 — 좁은 NATS-free core E2E

- **소유:** 기존 local runtime/Paper/strategy seam, test-only fixtures, 기존 benchmark driver.
- **선행:** TX05, TX08A, TX10, VN15, VN18. **구현:** `data→strategy file→existing Paper→same-account attribution→report/restart` 한 줄과 보존된 legacy Golden을 검증한다. **five-venue/arbitrage platform scenario는 제거**한다. 새 state machine/engine 없음.
- **수락:** deterministic local E2E, restart continuity, mutation 0, 기존 runtime behavior 동등. 2vCPU/3.8GiB 측정은 기존 profile로 수행하되 새 제품 요구가 아니다.

### TX12 — 최소 local acceptance

- **소유:** 기존 release candidate/runner gate, `docs/refactoring/vnext` status, secret-free runbook/manifest.
- **선행:** TX11, VN19. **구현:** 선택된 local scope(data/storage/recovery/plugins/Paper/same-account)와 unchanged legacy runtime을 matrix에 연결한다. 실제 서버/실계좌 항목만 External Gate로 남긴다.
- **수락:** 선택 scope 미구현 0, scoped test PASS(또는 NOT_RUN 명시), Paper 외부 mutation 0, source/build hash 고정, `server_deployed=false`, `live_active=false`. **generic Trading Platform 완성 badge 없음.** 소스 삭제 0/제품 변경 0은 owner 검증 전 self-acceptance가 아니다.

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

**종결 조건:** `R00–R06/O00–O01` + 기존 VN/TX 중 **선택된 필수 core 18개**와 TX11/TX12 aggregate가 새 Candidate SHA에서 증명되고, TOSS DATA_ONLY / 보존된 기존 ETF·venue 경로 / strategy-file→기존 Paper / same-account safety / NATS-free local data·recovery / 기존 cloud 경로가 기존 Golden과 최소 fixture로 입증되어야 한다. **all-five venue rebuild, TX03 extra margin, TX08B cross-venue, 새 uploader/Private Stream은 종결 조건이 아니다.** Legacy 원본 서비스 삭제(VN20), 실계좌/서버 운영 Gate는 별도 승인 사후 작업이며 이번 단계 소스 삭제 0이다.
