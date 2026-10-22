# VibeTrading v3 — 개발자(GLM/DeepSeek)용 구현 명세 및 티켓 수락 기준

> **이 문서는 구현자가 읽는 작업 명세다.** 상위 실행 순서는 `VibeTrading_Refactoring_Final_Plan_v3.md`, 전역 불변조건·데이터 모델·예외 규칙은 `VibeTrading_Architecture_Requirements_v3.md`가 정의한다. 과거 완성된 티켓의 코드/Receipt는 재사용하며 **동일한 기능을 재구현하라는 문서가 아니다.**
>
> **GitHub 관측 기준:** `PJH-02/VibeTrading`, `a9a83af43897fdf8badc303aaf3b6ebf2eda87c5` (2026-10-21). 이 시점의 티켓 상태: VN/TX 총 51개, Complete local 14 / Incomplete 23 / Not started 14. 실제 시작 시 먼저 `git rev-parse HEAD`와 `git status --porcelain`을 확인할 것.
>
> **기본 권위:** 원본 `docs/refactoring/vnext/tickets_production.json`의 기존 ID/depends_on/edit_paths/forbidden_paths와 기존 수락·검증 기록. 아래 **R/O 티켓은 새로운 제안**, `VN/TX` 작업은 과거 티켓의 남은 로컬 수락 내용을 구체화한 **Delta Spec**이다. 충돌 시 조용히 실행하지 말고 `CONTRACT_DECISION`으로 올릴 것.

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

### VN07 — Market Checkpoint + ETF/Paper/Strategy 원자적 복구 잔여

- **소유 파일:** `vibetrading/local_runtime.py::replay_startup`, `vibetrading/local_engine.py::{begin_recovery,apply_replay,handoff}`, `vibetrading/market_checkpoint.py`, `vibetrading/etf_trading.py`의 startup seam, `vibetrading/portfolio_ledger.py`, `vibetrading/paper_execution.py`, `vibetrading/strategy_runtime.py`; 적용 시 R03 path map 사용.
- **현재 상태:** Market checkpoint, producer durable order, journal lifecycle **구현 완료**. ETF/Paper account·전략·pending action의 동일 복구 경계 **미완료**.
- **구현:** `market state cursor / strategy claim cursor / paper order+fill journal version / ETF journal pending handoff`의 복구 순서를 명세화. Startup `RECOVERING`에서 전략 Callback warmup 허용 여부와 **신규 intent 전송 0** 계약. `History.available_at`과 feed generation 보존. Crash 지점마다 recover/reconcile→Ready 판정; replay output은 원장을 재전송하지 않는다.
- **테스트:** Market checkpoint 단독 복원 / 저널보다 앞·뒤인 input / partial write / invalid pending / double replay / newest checkpoint corruption / ETF·Paper 동시 재시작 / broad loss / source-missing fail-closed. 동일 원본에서 recovered booked balances·working orders·strategy claims 동등. 준비 불충분 시 신규 위험주문 0.
- **Critical 리뷰:** Codex가 multi-ledger 원자성의 **실제** 보장 범위를 확인. SQLite 파일 두 개가 있을 때 원자적 Commit이라고 주장하지 않음. 필요한 경우 staged recovery/quarantine 방식 채택.

### VN08 — Retained-prefix aware Spool Reader

- **소유:** `vibetrading/market_spool.py::{SpoolReader,_inventory,scan_spool}` 및 `vibetrading/local_storage.py::{LocalSpoolStorage,_FactLedger}`, 관련 Reader Tests.
- **선행:** VN07. **목적:** 원본 전체를 보존한 v1 Reader와 `base_cursor>0`의 새 Reader를 혼동하지 않음.
- **구현:** checkpoint의 digest/gen/first-retained segment를 재생 시작 증거로 사용. 유실된 gap, chain mismatch, truncated header, CRC miss, fence mismatch 시 비정상 복구를 거부. 여러 reader가 서로 다른 Base를 가질 때 oldest needed range 보존.
- **테스트:** v1 전체 Replay 동일, rotated segments, missing first/last segment, reader pin·crash·bad proof, 즉시 LIVE 거절. GC는 계속 비활성.

### VN09 — GC 증거 기반 회수 및 실패 시 보존

- **소유:** `market_spool.py`, `local_storage.py`, `archive.py`의 실제 증거 함수; 새 별도 GC framework 금지.
- **선행:** VN08, VN16. **구현:** Segment별 `checkpoint_coverage ∧ reader_progress ∧ parquet_commit/provenance ∧ remote_integrity/readback ∧ retention_elapsed ∧ lease_free`를 만족한 경우에만 회수 **후보**. Atomic planner는 `dry_run=True` 기본. S3/객체 저장소가 막히거나 incomplete progress면 보존. Checkpoint rollover/crash와 GC 경쟁 중 delete 의도를 durable record로 분리.
- **테스트:** 오류 주입을 포함한 사본에서 eligible/forbidden segment 목록 일치, **유일본 삭제 0건**, remote hash mismatch 0 삭제, data-root mount 잘못 지정 0 삭제. **운영 원본의 실제 삭제 실행은 별도 승인**.

### VN13 — Local SharedStrategyRunner의 실제 Paper Dispatch

- **소유:** `strategy_runner.py`, `strategy_contract.py`, `local_runtime.py`, 신규 `strategy_runtime.py`와의 seam, `tests/refactoring/test_strategy_runtime.py`·`test_strategy_dispatch.py`.
- **선행:** VN03, TX01, TX04B 및 TX05 통합 직전 계약 확인. **주의:** 과거 VN13과 TX05의 **상호 연동을 중복 Worker로 만들지 않음**.
- **구현:** 실제 Market Engine에서 `apply()` 1회 후 immutable snapshot을 다수 전략에 전달. Paper Action만 계좌 Owner로 라우팅. `LIVE_MARKET_DATA` vs `execution=PAPER` 직교; `SHADOW/REPLAY`은 신규 주문 0. 전략별 dependency/warmup/quality/risk gate, 예외 경계, action byte/count/TTL 상한. Python callback을 실제로 preempt 못한다는 현재 계약을 그대로 둔 채 'hard isolated'라고 표시 금지: 프로세스 격리 또는 별도의 Worker Pool을 **측정한 비용과 안전 계약으로** 결정.
- **테스트:** 동일 시장 상태 공유, 임의 느린 전략/무한 loop 격리 반례, ETF 비관련 feed 장애, Replay→LIVE Handoff 뒤 Paper 중복 0, NATS-free path에서 NATS client construct 0.

### VN15 — 실제·가정·복구 상태가 구분된 PnL/Control Projection

- **소유:** `telegram_trade.py`, `telegram.py`, `vibetrading/dashboard/bridge.py`, Owner/Portfolio read-only snapshot.
- **선행:** VN14B; TX09 통합 중 중복 처리 금지.
- **구현:** `actual`, `assumed/PAPER`, `recovery/unreconciled`를 같은 수치로 표현하지 않음. `PnL = realized/unrealized/fees/funding`과 계산 기준통화·기준가격·Timestamp·Data freshness 포함. 값 근거가 불명확하면 `UNAVAILABLE`. `status/feeds/orders/positions/pnl`는 cold read model에서 조회하고 Order Hot Path에서 DB read/API 호출 금지. `/pause,/halt,/resume`은 오직 권위 Owner로 전달.
- **테스트:** missing FX, stale mark, incomplete fill, Telegram outage, restart 중 stale snapshot. 과거 `/trade`, Mock2 보고 회귀.

### VN17 — 역할별 로컬 구성 및 자원 예산 Gate

- **소유:** `local_config.py`, `scripts/refactoring/build_release_manifest.py`, `deploy/local-runtime.yml` (존재 확인 후 ADD), `tests/refactoring/test_runtime_mounts.py`.
- **선행:** VN09, VN13, VN15, VN16. **구현:** NATS-free `collector/engine/storage/telegram/dashboard` 실제 역할별 이미지·Cmd·Env·Mount/Host 경계와 2vCPU/3.8GiB RAM, 메모리 예약, 동일 물리 데이터 디스크, Spool/Parquet/Checkpoint/DB peak 예산. Docker 경로는 `src` 이동된 release image 기준 검증, 운영 server config를 자동 수정하지 않음.
- **테스트:** docker compose config, role imports, volume device alias, impossible budget, credential render, CPU threads (`ARROW_NUM_THREADS`, multiprocessing), stale source SHA. 각 역할의 CPU/RSS/PSS는 합산 피크로 예산 평가.

### VN18 — 로컬 통합·성능·장애 수락

- **소유:** `scripts/refactoring/benchmark_vnext.py`(기존 파일 존재 여부 검증), 관련 runtime/replay 테스트, `docs/refactoring/runs/<new-run>/` 간결 영수증.
- **선행:** VN17, VN05. **구현:** 고정 Trace·동일 계정/전략 수·동일 하드웨어·동일 커밋의 A1 vs 후보 경로를 측정. `receive→apply→decision→paper_admit`, p50/95/99/99.9, CPU/event, RSS/PSS peak, swap, Byte Queue Age, storage lag, disk growth, error/gap 카운터.
- **테스트:** 로컬 NATS-free >30분, 저장 격리(Parquet 15분 중단), Cloud Mock 24h **가상 경과는 실제 wall-clock과 구분**, 프로세스 kill/restart, 누적 매칭. 실제 서버의 72h soak·실제 장중/Cloud/API 장애는 외부 Gate로 남김.

### VN19 — 출시/롤백 준비 패키지(로컬까지만)

- **소유:** `docs/refactoring/runs/<new-run>/release_manifest.json`, `cutover_runbook.md`, `rollback_runbook.md` 및 소스/이미지 hash 스크립트.
- **선행:** VN18. **구현:** 정확 SHA, 역할별 이미지 Digest, 데이터 볼륨 마운트·SQL Schema/Epoch, 기존 P02 활성 역할, `src` 패키징 CLI 변경, cutover/rollback 단계별 명령과 검사 예상 결과, 증거 부족 시 중단 조건. 명령은 승인 전 실행 금지.
- **수락:** Release Image 내부 import/entrypoints 및 Docker Compose 구성 성공, 기존 `deploy_p02_schema.py` 회귀. 최신 원장·UNKNOWN 예약·원본 백로그 복구를 위한 안전 절차; 원장 과거 이미지 덮어쓰기 없음.

### VN20 — NATS 과거 코드 삭제는 **서버 안정화 이후**

- **지금 할 일:** `bus.py`, `runtime.py`, `market_fanout.py`, 구 Docker Compose/NATS config, `requirements.txt`의 참조 지도·정식 신규 코드의 무참조 증명·삭제 Candidate List.
- **지금 하지 않을 일:** NATS 서비스/백로그/레거시 복구 코드/현재 P02 배포 복귀 경로 삭제, 운영 원본 데이터 GC, 복구 책임의 무리한 제거. `P11 실제 운영 전환+안정화+롤백 만료` 전 Gate Closed.
- **수락:** `VN20_PREPARED`, `VN20_APPLIED=false`로 표기. 문서 완료 ≠ 실제 제거 완료.

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
