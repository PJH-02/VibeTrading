# VibeTrading — 남은 작업 개발자 티켓 명세 (Final Execution Tickets)

> **문서 역할**: OpenCode/GLM/DeepSeek 구현자·테스터가 **현재 티켓 한 개만** 읽고 직접 실행할 수 있는 명세. 우선순위와 정책은 `01_FINAL_PLAN.md`; 변경하면 안 되는 계약은 `03_ARCHITECTURE_REQUIREMENTS.md`; 과거 완료의 재실행 금지는 `04_COMPLETED_MANIFEST.json`; 기계적 현재 상태는 `05_TASK_STATUS.json`.
>
> **기준**: GitHub main `2c1a82a52a6a2eed0c304f324f6c49e2987edfac` (2026-10-25 조회). 실행 시작 시 SHA를 다시 확인한다. 여기에 쓰인 후보 `old_path→new_path`는 **실제 이동 완료 사실이 아니라 실행 명세**다. 기존 `VN/TX 38 LOCAL_COMPLETE`, `R00~R05/O00` 로컬 수락 작업은 재개발 금지. 기존 `TX03A/B/03, TX06A-E/06, TX07B/07, TX08B, VN20`, R06, O01에는 신규 단일 소유 티켓과 매핑을 부여한다.

## A. 반드시 적용할 Ticket Contract

### A1. 입력 패킷 — 중복 문서 읽기 금지

```yaml
task_id: MOV-02                 # 반드시 05_TASK_STATUS.json에 존재
base_sha: <git rev-parse HEAD>
current_branch: <non-production-worktree>
role: mechanical|implement|test|audit
executor: opencode|python-codemod|codex-review
pinned_provider_model: <verified-id>  # 작업 중 자동 fallback 금지
scope: LOCAL_NO_SERVER_NO_LIVE
read_paths: [current_source, direct_callers, targeted_tests]
edit_paths: [explicit_allowlist]
forbidden_paths: [.env*, runtime_data/**, cloud-credentials/**, protected-ledger/**]
contract_sections: [ARCH-MOD, ARCH-ACTION, ARCH-EXEC, ARCH-DATA]
existing_authority: [canonical_classes_or_functions]
objective: <single verifiable capability>
acceptance: [machine-checkable assertions]
commands: [scoped_tests, independent_static_check]
expected_base_behavior: <fixture/source_sha>
```

### A2. 반환 패킷

```yaml
task_id: MOV-02
base_sha: ...
result: PASS|FAIL|BLOCKED|PARTIAL
diff_stat: ...
changed_paths: [...]
changed_functions: [...]
scoped_tests: {command: ..., exit: 0, passed: 42, deselected: 0}
static_tests: {command: ..., exit: 0}
new_runtime_calls: 0
external_mutations: 0
unresolved: []
next_read_paths: [...]
```

원시 stdout/JUnit/LLM 응답은 기본적으로 `.local/artifacts/<task>`(Git 비추적)에 저장. 최종 수락은 1~2KB JSON(테스트 합계·명령·환경·SHA·핵심 해시)으로 기록한다. 이전 774개 로그를 무작정 복제하지 않는다.

### A3. 수락/중단 규칙

- Implementer는 PASS를 **제안**할 뿐 `05`의 수락 상태를 쓰지 않는다. Codex/독립 검사기가 테스트 결과와 파일 범위를 확인한 뒤 기록한다.
- Diff가 허용 외 경로/비밀/서버에 접근하면 자동 BLOCK. 에러 로그를 그대로 Codex 대화에 수천 줄 붙여넣지 않는다.
- `MOVE_ONLY`에서는 이동 전후 함수 AST가 Import/Module Path 보정 부분을 제외하고 같다. 위험한 전역 초기화·relative import 변화는 단위/통합 결과로 보충.
- 첫 오류는 좁은 수정. 원인이 틀리면 새 Worker 재호출 전에 실패 범위를 축소. full pytest는 Move Wave 통합 경계 및 최종 release에서 실행.
- 모든 실거래 Mutation/운영 배포/원본 삭제는 거절. Paper 모드에 Broker Mutation Client를 연결하는 코드는 금지.
- `LOCAL_VERIFIED`와 `EXTERNAL_ONLY`는 다른 상태; 직접 코드 구현이 필요한 부분을 External로 둔갑시키지 않는다.

## B. HEAD `src/vibetrading` → 최종 `src/` Direct Domain Move Matrix (77개 루트 파일)

**원본**은 HEAD의 `src/vibetrading/` 바로 아래 `*.py` 77개, **대상**은 `src/` 바로 아래 각 도메인 폴더이다. 표의 OLD/NEW는 각각 이 원본과 최종 `src/` 기준의 상대경로다. `__init__.py`와 임시 호환 Alias는 제품 루트에 잔존시키지 않는다. 현재 패키지에 이미 존재하는 `strategies/`, `providers/`, `fv_mm/`, `fv_pilot/`, `mm_state/`, `liquidity_mm/`, `dashboard/`, `timefolio/`는 별도 패키지 매핑(B2)이다. 실제 이동 시 소스의 `Path(__file__)`, 상대 Import, public CLI, Docker COPY, `importlib` 경로와 패치/monkeypatch identity를 검증해야 한다. **이 표의 도메인 배치는 최초 책임 가설**이며, 파일이 다중 책임이면 `MOV-00`에서 증거를 근거로 소유권을 결정·이동 예외에 등록한다. 근거 없이 단순히 깔끔해 보이는 폴더로 이동하지 않는다.

### B1. 루트 파일 Old→New 매트릭스

| 원본 (`HEAD: src/vibetrading/` 기준) | 목표 (`src/` 기준) | Owner | 분류 |
|---|---|---|---|
| `__init__.py` | *(retire after package migration)* | packaging | `RETIRE_OLD_NAMESPACE` |
| `cli.py` | `runtime/cli.py` | `runtime` | `MOVE_ONLY_CANDIDATE` |
| `dedup.py` | `core/dedup.py` | `core` | `MOVE_ONLY_CANDIDATE` |
| `models.py` | `core/models.py` | `core` | `MOVE_ONLY_CANDIDATE` |
| `trading_contracts.py` | `execution/trading_contracts.py` | `execution` | `MOV00_OWNERSHIP_EXCEPTION` |
| `brokers.py` | `execution/brokers.py` | `execution` | `MOVE_ONLY_CANDIDATE` |
| `bybit_execution.py` | `execution/bybit_execution.py` | `execution` | `MOVE_ONLY_CANDIDATE` |
| `execution.py` | `execution/contracts.py` | `execution` | `MOVE_ONLY_CANDIDATE` |
| `execution_groups.py` | `execution/execution_groups.py` | `execution` | `MOVE_ONLY_CANDIDATE` |
| `execution_ledger.py` | `execution/execution_ledger.py` | `execution` | `MOVE_ONLY_CANDIDATE` |
| `execution_nonce.py` | `execution/execution_nonce.py` | `execution` | `MOVE_ONLY_CANDIDATE` |
| `execution_owner.py` | `execution/execution_owner.py` | `execution` | `MOVE_ONLY_CANDIDATE` |
| `extended_brokers.py` | `execution/extended_brokers.py` | `execution` | `MOVE_ONLY_CANDIDATE` |
| `kis_execution.py` | `execution/kis_execution.py` | `execution` | `MOVE_ONLY_CANDIDATE` |
| `paper_execution.py` | `execution/paper_execution.py` | `execution` | `MOVE_ONLY_CANDIDATE` |
| `perp_execution.py` | `execution/perp_execution.py` | `execution` | `MOVE_ONLY_CANDIDATE` |
| `process_lease.py` | `execution/process_lease.py` | `execution` | `MOVE_ONLY_CANDIDATE` |
| `collection_timing.py` | `feeds/collection_timing.py` | `feeds` | `MOVE_ONLY_CANDIDATE` |
| `etf_csv_sync.py` | `feeds/etf_csv_sync.py` | `feeds` | `MOVE_ONLY_CANDIDATE` |
| `feed_recovery.py` | `feeds/feed_recovery.py` | `feeds` | `MOVE_ONLY_CANDIDATE` |
| `kis_index.py` | `feeds/kis_index.py` | `feeds` | `MOVE_ONLY_CANDIDATE` |
| `local_pipeline.py` | `feeds/local_pipeline.py` | `feeds` | `MOVE_ONLY_CANDIDATE` |
| `universe_config.py` | `feeds/universe_config.py` | `feeds` | `MOVE_ONLY_CANDIDATE` |
| `hot_path.py` | `market/hot_path.py` | `market` | `MOVE_ONLY_CANDIDATE` |
| `local_engine.py` | `market/local_engine.py` | `market` | `MOVE_ONLY_CANDIDATE` |
| `local_snapshot.py` | `market/local_snapshot.py` | `market` | `MOVE_ONLY_CANDIDATE` |
| `market_apply_order.py` | `market/market_apply_order.py` | `market` | `MOVE_ONLY_CANDIDATE` |
| `market_fanout.py` | `market/market_fanout.py` | `market` | `MOVE_ONLY_CANDIDATE` |
| `market_policy.py` | `market/market_policy.py` | `market` | `MOVE_ONLY_CANDIDATE` |
| `market_source_status.py` | `market/market_source_status.py` | `market` | `MOVE_ONLY_CANDIDATE` |
| `health.py` | `operations/health.py` | `operations` | `MOVE_ONLY_CANDIDATE` |
| `local_observation.py` | `operations/local_observation.py` | `operations` | `MOVE_ONLY_CANDIDATE` |
| `logging_utils.py` | `operations/logging_utils.py` | `operations` | `MOVE_ONLY_CANDIDATE` |
| `maintenance_gate.py` | `operations/maintenance_gate.py` | `operations` | `MOVE_ONLY_CANDIDATE` |
| `session_alerts.py` | `operations/session_alerts.py` | `operations` | `MOVE_ONLY_CANDIDATE` |
| `status.py` | `operations/status.py` | `operations` | `MOVE_ONLY_CANDIDATE` |
| `telegram.py` | `operations/telegram.py` | `operations` | `MOVE_ONLY_CANDIDATE` |
| `telegram_state.py` | `operations/telegram_state.py` | `operations` | `MOVE_ONLY_CANDIDATE` |
| `telegram_trade.py` | `operations/telegram_trade.py` | `operations` | `MOVE_ONLY_CANDIDATE` |
| `trade_reporting.py` | `operations/trade_reporting.py` | `operations` | `MOVE_ONLY_CANDIDATE` |
| `etf_journal.py` | `portfolio/etf_journal.py` | `portfolio` | `MOVE_ONLY_CANDIDATE` |
| `etf_recovery_ledger.py` | `portfolio/etf_recovery_ledger.py` | `portfolio` | `MOVE_ONLY_CANDIDATE` |
| `portfolio_ledger.py` | `portfolio/portfolio_ledger.py` | `portfolio` | `MOVE_ONLY_CANDIDATE` |
| `bus.py` | `runtime/bus.py` | `runtime` | `MOVE_ONLY_CANDIDATE` |
| `etf_recovery.py` | `runtime/etf_recovery.py` | `runtime` | `MOVE_ONLY_CANDIDATE` |
| `etf_trading.py` | `runtime/etf_trading.py` | `runtime` | `MOVE_ONLY_CANDIDATE` |
| `local_config.py` | `runtime/local_config.py` | `runtime` | `MOVE_ONLY_CANDIDATE` |
| `local_runtime.py` | `runtime/local_runtime.py` | `runtime` | `MOVE_ONLY_CANDIDATE` |
| `local_transport.py` | `runtime/local_transport.py` | `runtime` | `MOVE_ONLY_CANDIDATE` |
| `real_runner.py` | `runtime/real_runner.py` | `runtime` | `MOVE_ONLY_CANDIDATE` |
| `runtime.py` | `runtime/runtime.py` | `runtime` | `MOVE_ONLY_CANDIDATE` |
| `runtime_checkpoint.py` | `runtime/runtime_checkpoint.py` | `runtime` | `MOVE_ONLY_CANDIDATE` |
| `runtime_control.py` | `runtime/runtime_control.py` | `runtime` | `MOVE_ONLY_CANDIDATE` |
| `runtime_paths.py` | `runtime/runtime_paths.py` | `runtime` | `MOVE_ONLY_CANDIDATE` |
| `settings.py` | `runtime/settings.py` | `runtime` | `MOVE_ONLY_CANDIDATE` |
| `strategy_runner.py` | `runtime/strategy_runner.py` | `runtime` | `MOVE_ONLY_CANDIDATE` |
| `strategy_runtime.py` | `runtime/strategy_runtime.py` | `runtime` | `MOVE_ONLY_CANDIDATE` |
| `archive.py` | `storage/archive.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `checkpoint_journal.py` | `storage/checkpoint_journal.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `compactor.py` | `storage/compactor.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `delivery_recovery.py` | `storage/delivery_recovery.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `etf_csv_history.py` | `storage/etf_csv_history.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `etf_intraday_store.py` | `storage/etf_intraday_store.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `event_index_runs.py` | `storage/event_index_runs.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `gap_ledger.py` | `storage/gap_ledger.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `local_events.py` | `storage/local_events.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `local_storage.py` | `storage/local_storage.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `market_checkpoint.py` | `storage/market_checkpoint.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `market_spool.py` | `storage/market_spool.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `parquet_store.py` | `storage/parquet_store.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `sqlite_store.py` | `storage/sqlite_store.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `storage_backend.py` | `storage/storage_backend.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `storage_guard.py` | `storage/storage_guard.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `storage_worker.py` | `storage/storage_worker.py` | `storage` | `MOVE_ONLY_CANDIDATE` |
| `etf_history.py` | `strategies/kr/etf_history.py` | `strategies` | `MOVE_ONLY_CANDIDATE` |
| `etf_reference.py` | `strategies/kr/etf_reference.py` | `strategies` | `MOVE_ONLY_CANDIDATE` |
| `strategy_contract.py` | `strategies/compat_contract.py` 또는 retire | `strategies` | `REUSE_OR_RETIRE_AFTER_CALLER_AUDIT` |

### B2. 기존 하위 패키지 Old→New 매트릭스

| 이전 | 목표 | 특별 검증·제한 |
|---|---|---|
| `src/vibetrading/providers/**` | `src/feeds/providers/**` | public import, provider registry/dynamic strings, runtime discovery |
| `src/vibetrading/strategies/**` | `src/strategies/**` (root `strategies/` collision remove) | `contract.py`, `kr/`, `legacy/`, `research/`를 자동 등록하지 않음 |
| `src/vibetrading/dashboard/**` | `src/operations/dashboard/**` | 별도 최상위 Node `dashboard/`는 이동 금지, DB cold/read-only |
| `src/vibetrading/timefolio/**` | **`src/timefolio/**` mandatory** | Timefolio 대회 주문 Client/Runner 정식 보존; 브라우저 주문 권한과 NATS-free 경로 검증 |
| `src/vibetrading/fv_mm/**` | 필요 함수 `src/{execution,strategies,market}/**`; 나머지 retire | Bybit/GRVT strategy+venue+runner 혼합; 필요한 함수·테스트 근거만 이관하며 묶음 자체 최종 보존 금지 |
| `src/vibetrading/fv_pilot/**` | 재사용 근거 없으면 retire | research/evaluation 임의 운영 등록 금지 |
| `src/vibetrading/mm_state/**` | 사용중인 pure state만 `src/strategies/crypto/**`에 통합, 나머지 retire | 상태 전략과 runner 호출자 분리 검증; 기존 S1–S4 families 혼동 금지 |
| `src/vibetrading/liquidity_mm/**` | 필요한 execution/private 코드는 canonical 이동, 나머지 retire | 기존 Bybit Testnet entrypoint·Private WS 중 선택 생산 경로 재사용에 필요한 코드만 이관, 역사 Runner 자동 보존 금지 |
| 루트 `services/`, `shared/`, `strategies/`, `strategy/` | source owner로 이동 후 **root active package retire** | `src/strategies`와 root `strategies`의 import precedence를 반드시 해결. 실서버 기존 릴리스 소스는 rollback artifact로 유지하고 새 릴리스 제품 import에서는 동명 권위 0. |

**이전 사유만으로 `runtime/apps`는 최종 생성하지 않는다.** 현재 MM 묶음은 MOVE_ONLY 중 임시 보존 가능하지만 최종 릴리스에는 함수별 역할 이관 및 참조 0 확인 후 제거. `src/timefolio`는 예외가 아닌 필수 독립 대회 주문 패키지다.

### B2a. Flat source Package/Module 충돌 (필수 사전 해결)

- **root package shadow**: root `strategies/`, `services/`, `shared/`, `strategy/`가 새 `src/strategies/`의 로딩을 가로챌 수 있다. 옛 root 패키지의 활성 기능을 소유 도메인으로 이동/병합하고 root에서 다른 패키지가 import되지 않도록 검사한다. 표준 sys.path 순서가 개발·컨테이너·Wheel 모두 다르므로 CWD 별 import test 필수.
- **file vs package**: 기존 `src/vibetrading/execution.py`는 최종 `src/execution/contracts.py`로 이동, `src/vibetrading/runtime.py`는 최종 `src/runtime/runtime.py` 또는 실제 분석 후 한 통합 파일로 이동. `src/execution.py`, `src/runtime.py`가 같은 이름의 `src/execution/`, `src/runtime/`과 병존해서 import precedence를 바꾸지 않게 한다.
- **old namespace**: `vibetrading.cli`, `vibetrading.strategies`, `vibetrading.timefolio` 및 `vibetrading.dashboard`를 외부에 노출하던 caller는 새 import/CLI 로 이행한다. 실제 사용자·외부 script/plugin 의존이 없다는 증거 없는 삭제는 local Gate BLOCK; old `vibetrading` shim 대량 제작은 금지.
- **Timefolio**: 대회 주문 경로는 `src/timefolio/client.py`, `src/timefolio/runner.py`; 모니터는 `src/operations/dashboard/timefolio_monitor.py`로. MM testnet vertical과 별개인 정식 대회 주문 기능이며 브라우저 이미지·권한 검증이 필수.
- **오래된 testnet**: 기존 FV/MM runner의 signer/private/raw WS/pnl 로직은 재사용 근거가 있는 함수만 정식 owner로 이전한다. `runtime/apps` 보존·원형 유지 기본값을 두지 않는다. 연구/테스트 동작 자체는 제품 필수가 아니며 프로덕션 주문 owner를 우회하는 경로로 남아서는 안 된다.

### B3. R07 Codemod 알고리즘 요구

1. **동결**: clean worktree, base SHA, `git ls-files`, `pyproject.toml`, current package import smoke, captured old name→path→Git blob SHA. Git에 추적되지 않은 자료나 실서버 파일에 접근 금지.
2. **정확한 매핑 생성**: B1+B2를 CSV/JSON으로 컴파일. 현재 `git ls-files src/vibetrading`과 대조, 누락·중복·충돌·대소문자 혼동 0. 새 `__init__.py`는 필요 최소만.
3. **개별 파일 이동**: `git mv old new`; 바이너리/원본 의미 변화 없음. 파일 내용 변경이 동반되면 먼저 move-only 커밋, 이후 참조 보정 커밋으로 구분.
4. **정적 Import Rewrite**: Python AST/LibCST를 사용해 `import`, `from`, 상대 import depth, `__all__`, package data refs 변경. 단순 텍스트 치환만으로 `strategy`, `strategies`, product symbol을 우연히 바꾸지 않는다.
5. **비정적 경로**: `importlib.import_module`, `__import__`, plugin 이름/설정 문자열, `run_strategy.py`, `-m`/console scripts, `sys.modules` aliases, `inspect.getfile`, hashing/provenance, `Path(__file__)`, sourcemap and tests, Docker `COPY`, Compose, shell/systemd/cron 경로, Notebook/Backtest script/monkeypatch를 검색해 정밀 수정.
6. **호환성**: 기존 운영 P02 이미지는 그대로. 로컬 새 패키지에서 legacy public import는 필요한 경우 **단일 canonical module identity** shims(재정의 금지)로 유지. 제거에는 활성 caller 0 증명.
7. **독립 확인**: `compileall`, import-all canonical, CLI `--help`, editable/wheel 설치 후 repo 밖 import, Docker build path, compose config, direct/dynamic caller graph, golden ETF/venues/cloud, full Linux regression. 테스트가 없는 동적 entrypoint는 `UNKNOWN`으로 놓고 삭제하지 않는다.
8. **승인**: Codex에는 전체 diff가 아닌 매핑 요약·AST hash mismatch 목록·실패한 단일 테스트·커밋 SHA만 전달. 통합 후 `03`의 루트 허용 예외를 확인.

## C. 개별 구현 티켓 (NEW; 과거 완료 티켓 재실행하지 말 것)

### GOV-01 — 새 5문서만을 읽는 Authority Cutover

- **선행**: 없음. **담당**: Codex PM + deterministic validation. **수정 범위**: `docs/final/**`의 이번 5파일, root `AGENTS.md`, `README.md`, `docs/status.md` 링크만. 권위 없는 문서를 물리 삭제하지 않음.
- **구현**: 새 문서 5개를 `docs/final/`에 설치, Git SHA 고정; 기존 `stage_status.json` 38개 완료를 새 manifest의 역사 근거로 연결. `AGENTS.md`에서 5개 권위 정의·worker 1티켓 입력·실운영 금지를 20~40줄로 요약; v3/Minimal Reuse 병렬 권위를 종료하되 read-only refs 보존. `05` DAG edge가 0 cycle인지 검사.
- **수락**: 모든 링크/JSON schema/unique task IDs/38 historical-complete IDs 일치; 어떤 기존 `COMPLETE` 코드도 재개발 예약하지 않음; 과거 Artifact 삭제 0; 이전 vs 새 문서 authority 충돌 0.
- **테스트/증거**: JSON parse, link existence, set-diff against historical `stage_status.json`, current SHA. 실패 시 본 티켓에서만 수정.

### OPS-01 — O01의 최소 Task Dispatch/Proof 갭 해소

- **선행**: GOV-01. **담당**: Python deterministic + OpenCode GLM, Codex reviewer. **읽기**: `scripts/dev/status.py`, 기존 O00/O01 receipts, `AGENTS.md`, 05. **쓰기**: `scripts/dev/**`의 해당 스크립트, 필요한 tooling tests. 시스템 설정·Provider credentials 변경 금지.
- **구현**: `task next/show/check/summarize` 등 기존 CLI에 가장 작은 기능만 추가하거나 기존 runner를 재사용; `05`의 deps+state READY, read/edit/forbidden paths, base SHA, provider 실제 모델 ID, diff hash, 테스트 exit code 검증. 모델 비대화형 호출이 실제 안 되면 수동 Packet 출력 모드만. 세션 재시작 시 최신 commit+status만 읽음. 기존 O01 `REWORK_REQUIRED` 이유인 baseline/snapshot 오류를 재현·정정하기 전 PASS 불가.
- **수락**: READY 오판 0, 미승인 경로 수정 검출, 없는 Receipt로 승인 불가, 서브에이전트 무한 루프/자동 재위임 없음, synthetic 두 티켓 재현; `git push`·서버 접속 0.

### MOV-00 — Source Ownership Freeze & Atomic Move Map

- **선행**: GOV-01. **담당**: Codemod script + GLM 분류, Codex path-map 결정. **읽기**: B1/B2, 기존 R00 `repo_inventory.json`, `path_map.json`이 존재하면 재사용, R03/R04 ownership, current `git ls-files`, Dynamic Import 목록, `pyproject`, Docker/Compose. **쓰기**: 이번 5문서의 상태/작업 근거, 작은 `scripts/refactoring/` codemod + 테스트만; 제품 파일 변경 금지.
- **구현**: B1 77행 실제 HEAD 일치 검사, active subpackages 8개 이상 분류, 각 매핑 `move/keep/exception/legacy_deferred`와 `old_path/new_path/source_blob_sha/owner/active_callers/dynamic_refs/runtime_entrypoints/rollback_refs` 필드 포함. 단일 새로운 매핑 파일을 **코드 생성 스크립트 입력 또는 02의 표**에서 도출. 복잡한 런타임/혼합 도메인은 묶음/예외로 기록; 돌발 신규 기능 금지.
- **수락**: 미분류 현재 활성 Python 원본 0; 중복 new_path 0; old_path mismatch 0; 승인 불가능한 unresolved 동적 실행 경로는 예외 처리 후 MOV-01~07에 보류. 기계적 이동 전 source hashes 고정.

**FLAT-SRC 구현 보강 (기존 Map 주석보다 우선)**
1. HEAD `git ls-files src/vibetrading`의 전체 파일(77 root + 8+ subpackages)과 root legacy, root Docker/Compose + `deploy` overlays + scripts + `pyproject`에서 직접/동적 import 참조와 entrypoint를 **모두** inventory. `old_path`는 `src/vibetrading/`, `new_path`는 `src/<domain>/` 실제 full path로 생성한다. `__init__.py` old package retire, `cli.py→src/runtime/cli.py`, Timefolio `→src/timefolio`, 기존 4 MM bundle은 `REUSE_SYMBOL/RETIRED_NO_CALLER/DEFERRED_WITH_BLOCKER` 판정. `runtime/apps` 무조건 이동 금지.
2. 패키지 충돌: `root/strategies` vs `src/strategies`, `src/strategy_contract.py` alias, `src/runtime.py` vs `src/runtime/`, `src/execution.py` vs `src/execution/` 등 명시. Python import `find_spec`/wheel `RECORD`/script CWD, generated `PYTHONPATH` 차이를 matrix화. 동일 이름 파일과 폴더가 동시에 존재하면 사용 전 rename/move atomic wave를 설계한다.
3. Docker 진입점 1행도 빠짐없이 `root Dockerfile`, `.crypto`, `docker-compose.yml`, `deploy/{crypto-profile,dashboard-compose,ls-market-profile,release1-overlay}.yml`, `deploy/Dockerfile*`, `deploy/vibetrading.service`, `scripts/build_release_images.py`, `config/*.yaml`별 `old→new command/path/image/profile`를 작성. 실제 server inventory는 외부/미승인으로 표시한다.
4. **Receipt**: JSON table 각 row `source_sha,old_path,new_path,owning_symbol,active_callers,tests,entrypoint,config_refs,docker_refs,rollback_refs,disposition,reason`. `missing_or_ambiguous>0`이면 MOV-00 BLOCK. 구조를 생각없이 편리한 폴더로 이동 금지.


### MOV-01 — Core Contracts & Public API Compatibility

- **선행**: MOV-00. **담당**: `git mv`+Codemod, GLM tests. **이동**: B1의 `core/` 항목. **읽기**: `models.py`, `trading_contracts.py`, `dedup.py`, 해당 직접 callers/tests. **쓰기**: `src/core/**`, source old paths, 정확한 import callers; release/rollback 경계 경로는 명시 승인만.
- **구현**: Shared Market Event/Instrument/Order ID/Decimal/Wire ABI 보존. `from vibetrading.models import ...`의 canonical update, old alias는 실제 호출자가 필요한 동안 identity shim. `__module__`/pickle path/state contract와 EntryPoint에 끼치는 영향 테스트. 참조 그래프 Cyclic Dependency 0.
- **수락**: static/isolated canonical imports + alias identity + event serialization/Decimal schema equal, ETF/crypto golden equal, package outside repo import PASS. `models` 구조/필드 의미 변경 0.

**FLAT-SRC 이행 절차**: `src/vibetrading/models.py` 등의 공통 타입을 `src/core/`로 이동하고 `import vibetrading.models`→`import core.models` 등을 AST codemod로 수정한다. `pyproject` 이전·루트 root `strategies` import shadow 및 `__module__`/pickle/blob recovery 영향 기록. `src/vibetrading/__init__.py`에 자동 re-export wrapper를 복제하지 않는다. 테스트: `pip wheel . -w /tmp/wheels` 및 wheel 임시 설치 후 repo 밖 `python -c 'from core.models import ...'`, git diff AST Golden, source fixture `importlib.util.find_spec('core').origin` 확인. package relocation 전/후 CALLER identity/serialize schema는 증거로 보존.


### MOV-02 — Feeds & Market Data Domain Package

- **선행**: MOV-01. **담당**: Mechanical codemod + GLM. **이동**: B1 `feeds/`, `market/` 및 B2 `providers/`; `local_engine`/`hot_path`/native book 관련. **읽기**: `local_runtime.py`, existing collectors, `src/vibetrading/providers/**`, tests for native order.
- **데이터 경계 재확인(이 티켓 MOVE_ONLY)**: 이동 전/후 소스별 event/frame order와 `receive/available_at` 동일. Venue A 이벤트에 대해 Venue B timestamp·연결·book snapshot을 기다리는 코드가 **기존 구조에 있다면** `DATA-BARRIER-FINDING`으로 기록하되 MOV-02의 Move Only 커밋 안에서 Data Behavior 수정 금지. `QA-01`/`PERF-01`로 정확한 caller+measure 보냄; 본 티켓에서는 Source별 native sequence/time preservation tests만 재실행. 모호한 중복 normalizer를 발견해도 CLN-02/후속 PERF까지 보류.
- **구현**: Provider registry·WS reconnect·native sequence, normalized frames, Kiwoom/TOSS/KIS timestamp, L2 delta, `available_at`, shared single writer. Import paths/`__file__` based configs, dynamic provider names mapping 업데이트. `market`에서 Broker mutation을 가져오지 않음.
- **수락**: synthetic event ordering/book gap/replay/native precision/ETF 1min/30min/closed bar Golden identical; `python -m` Collector & CLI help; STATIC active retired import 0; Docker/Compose run targets resolve. Perf measured only if baseline available.

**Provider 역할 구분 / 과잉 모듈 제거 수락**
- `src/vibetrading/providers/{binance,crypto_perp,extended_perp,hyperliquid,kis,kiwoom,toss}.py`를 중심으로 `provider×market_segment×channel` Matrix를 작성: `crypto_perp`가 지원하는 실제 venue, `extended_perp` Arcus/GRVT의 native wire, Binance `binance.py`의 별도 TradFi 계약을 검증. 이름만으로 전 거래소/전 상품 지원 선언 불가.
- `feeds/providers`의 venue별 구현을 무조건 파일 하나씩 만들지 않으며 native parser/sequence/gap 차이만 모듈로 유지. venue 이름 분기와 공통 WS/normalizer 중복을 측정해 `CLN-02` 후보로 표시. 전략/Owner 매매 정책 추가 금지.
- 기존 수집 코드 경로 이동 시 `main.py`, `config/crypto_runtime.yaml`, `config/runtime.yaml`, `deploy/crypto-profile.yml`, `scripts`/CLI 등록을 new canonical import로 업데이트; producer/source/bar-end flags와 TOSS historical 수집 Golden 보존. TOSS default active producer=0.
- 테스트 fixture: native Binance Book/Arcus/GRVT sequence, invalid snapshot/delta, cross-source A active B disconnected, per-source receive_ts/available_at, book snapshot restart; `python -m runtime.cli --help` 또는 실제 정의된 새 진입점. 이동 외 비즈니스 처리 수정 0, Data barrier는 QA/PERF 담당.


### MOV-03 — Storage/Checkpoint/Cloud Package

- **선행**: MOV-02. **담당**: Codemod + GLM tests, Codex critical on recovery paths. **이동**: B1 `storage/`; existing `market_spool`, `market_checkpoint`, `local_storage`, `storage_backend`, `parquet_store`, checkpoint/provenance. **읽기**: VN07–09 receipts/Golden, `scripts/refactoring/build_release_manifest.py`, P02 rollback tool (READ ONLY).
- **구현**: `__file__`-relative storage locations와 spool root/config 환경 위치를 명시적으로 mapping; 원본 runtime_data·cloud credential·S3 path/objects 변경 금지. Durable writer/Cursor/Watermark/Parquet/GC semantics 그대로. Import update 외 구현 함수 변경 금지.
- **수락**: spool record checksum/segment recovery/retained prefix/reader bounds, crash/fsync/restart/GC dry-run, cloud fake verify/download, P02 rollback script import/help; source_data deletions 0, persisted schema changed 0.

### MOV-04 — Execution / Portfolio Domain Package

- **선행**: MOV-01, MOV-03. **담당**: Codemod+DeepSeek, Codex critical reviewer. **이동**: B1 `execution/`·`portfolio/`: `execution_owner.py`, `execution_ledger.py`, `execution_nonce.py`, `paper_execution.py`, `perp_execution.py`, `kis_execution.py`, `portfolio_ledger.py`, `etf_journal.py` etc.
- **구현**: 전략 경제/주문 `client_id`·reserve·UNKNOWN·late-fill·cancel state·nonce durability·KIS Mock/Mock2 semantics와 physical/virtual attribution 그대로. Runtime import to canonical path만 교체. 거래소 HTTP/Signer/WS/Real Mutation 재작성 금지. 현재 테스트넷 경로를 Live로 자동 승격 금지.
- **수락**: BEFORE/AFTER old test payload+ledger SQLite outcomes hash equal, fake broker mutation count equal, audit/nonce/ACK-fills parity; `test_dual_mock_and_assumed`, `test_etf_execution_update`, owner/ledger/Paper tests; 실제 Broker API 접근 0.

**Execution canonical source 경계**: 기존 `src/vibetrading/{bybit_execution,kis_execution,perp_execution,extended_brokers,brokers}.py`는 공통 Owner가 참조하는 함수/클래스의 이동 입력일 뿐 영구 파일명 보장 아님. Move-Only 단계에서 임시 `src/execution/`으로 이동 가능하나 즉시 삭제 금지. CAP-01/02/EXEC-01에서 기존 KIS Mock2·Bybit Probe·모든 Perp native signing/order truth를 재사용/통합한 뒤 CLN-02가 별도 `DEDUP_RETIRED` 커밋으로 중복 파일 제거하도록 symbol map과 테스트를 작성. 최종 `src/execution/venues`는 venue/product별 canonical owner 하나, 동일 submit client 복제 0. `src/portfolio`의 원장 SQL schema/owner 상태는 불변.


### MOV-05 — Strategy Ownership + Timefolio Preserve + Legacy Bundle Disposition

- **선행** MOV-02/MOV-04. **담당** deterministic git-move/codemod + DeepSeek; Codex strategy/competition-order audit. **읽기** `src/vibetrading/{strategies,fv_mm,liquidity_mm,fv_pilot,mm_state,timefolio}`, `run_strategy.py`, `scripts/{run_fv_testnets,run_fv_pilot,inspect_timefolio_metrics}.py`, `src/vibetrading/dashboard/timefolio_monitor.py`, Timefolio test suite, dynamic imports, Config/Compose and historical receipts.
- **MOV-05.1 Strategy Move Only**: 기존 strategy loader/strategy files를 `src/strategies` canonical로 이동. root `strategies/` conflict는 MOV-00 map에 따라 영향 호출자를 먼저 옮기고 모든 `import strategies`의 경로가 하나인지 검사. `STR-02` 경제 의사결정 로직 수정 금지. `src/vibetrading/strategies/`/root `strategies` 두 authority로 남기지 않음.
- **MOV-05.2 Timefolio mandatory**: `src/vibetrading/timefolio/client.py`→`src/timefolio/client.py`, `runner.py`→`src/timefolio/runner.py`, `__init__` side-effect-free. `TimefolioStrategyRunner`, `AsyncTimefolioBrowserManager`, order result/weight sizing/duplicate safety/`submit` Arm semantics 보존. `vibetrading.timefolio`/`vibetrading.dashboard.timefolio_monitor` import/entrypoint를 새 `timefolio`/`operations.dashboard` 경로로 치환; NATS-free data path는 MOV-06/QA에서 기능 변경 Gate로 따로. **Timefolio를 `runtime/apps`에 숨기거나 과거 MM 연구와 같이 delete 후보로 분류하면 FAIL**.
- **MOV-05.3 4 legacy bundles evidence**: `fv_mm`, `liquidity_mm`, `fv_pilot`, `mm_state`를 **전부 runtime/apps로 복사하지 않음**. Source function마다 `owner=execution/strategy/market/portfolio`, `reuse_oracle`, `status=REUSE/DELETE/DEFER`, `callers`, `required_tests`, `rollback`를 조사. MOVE_ONLY에서는 비활성 임시 원본을 보존하고, CAP-01/STR-02/EXEC-01/CLN-02 수락 후 최종 Release에서 소스/이미지 중복 제거. testnet/legacy artifacts는 runbook/locked rollback image로 보존 가능, 새 제품 패키지 필수 아님.
- **Acceptance**: (1) KR strategy Golden/loader identity, (2) Timefolio `submit=false` / missing arm / mock browser verified, `tests/test_timefolio.py`, (3) root vs `src/strategies` collision 0, (4) 4-bundle function-level disposition 100%, active legacy runtime auto-registration 0, (5) 실제 브라우저 실주문 0. Timefolio/NATS-free integration까지 미완성일 경우 MOV-06 및 QA로 잔여 blocker 전파하고 전체 릴리스 PASS 금지.


### MOV-06 — Flat Runtime/Operations/Timefolio Entrypoints + Docker Path Wiring

- **선행** MOV-02/03/04/05. **실행** Codemod + GLM, Codex deploy safety review. **읽기** HEAD `src/vibetrading/{local_runtime,local_config,settings,cli,runtime,real_runner,telegram,health,dashboard/**,timefolio/**}`, root `main.py/run_strategy.py`, `pyproject.toml`, `Dockerfile*`, `docker-compose.yml`, `deploy/*.yml`, `deploy/Dockerfile*`, `deploy/vibetrading.service`, `deploy/vibetrading.cron.example`, `scripts/build_release_images.py` & selected smoke tests.
- **MOV-06.1** `src/runtime/{cli,local_runtime,local_transport,real_runner,strategy_runtime,settings,...}.py`, `src/operations/{health,telegram,dashboard/**,...}.py`를 canonical로 이동; old `vibetrading.*` CLI/Plugin/NATS dependency 새 활성 경로 0. `runtime/`은 process composition root, old MM app 저장소 금지.
- **MOV-06.2** distribution `pyproject.toml`: `vibetrading=src/vibetrading` 제거, `where=["src"]`, 패키지 목록/namespace/entrypoints 관리. root `strategies/services/shared` import collision과 `src/runtime.py`/`src/execution.py` 파일·패키지 충돌을 MOV-00 mapping으로 해결. wheel smoke를 **repo 외 cwd**에서 실행. `importlib.find_spec` + `.__file__` 경로 단일성 증명. 비표준 sys.path monkeypatch로 덮지 않음.
- **MOV-06.3** root `Dockerfile` `Dockerfile.crypto` source COPY to `/app/src`, CMD/entry, user 10001; root Compose, `deploy/crypto-profile.yml`, `dashboard-compose.yml`, `ls-market-profile.yml`, `release1-overlay.yml`의 모든 source path, `python -m` module, healthcheck, `env_file`, Config path, role, NATS dependency, bind mounts/volumes를 static map에 따라 업데이트. Timefolio browser image (`deploy/Dockerfile.dashboard-timefolio`)와 Monitor entrypoint를 분리 검증. `scripts/build_release_images.py` 및 `scripts/refactoring/build_release_manifest.py`에는 source hashes/new paths를 포함. root Dockerfiles와 deploy overlay 둘 다 다뤄야 한다.
- **MOV-06.4** `src/timefolio/runner.py` 현재 `NatsJetStreamBus` 수신을 NATS-free Runtime/MarketState Adapter로 교체하되 이미 있는 Browser login/order client는 그대로 재사용, 전략 데이터/결정·대회 주문 순서/권한 불변. 운영 Timefolio 주문 service가 원래 존재하지 않았다면 자동 실주문 컨테이너를 켜지 않고 `config`/explicit separate opt-in profile로 선언. Default `submit=false`, live submit enabled + account arm 보존.
- **MOV-06.5** Compose old files는 historical rollback snapshot으로 보존하고 최종 NATS-free *primary* compose/config는 명시적으로 분리. Old `nats-init` 강제 dependency 0을 정식 primary에서 증명. Fake no-network start로 subprocess roles: collectors/market/storage/strategy/execution/operations/timefolio monitor/order runner/maintenance startup or justified disabled list. `config/runtime.yaml`, `config/crypto_runtime.yaml`, selected env, Docker mounts/healthchecks/StopGrace 모두 resolve.
- **수락**: `docker compose config` 각 프로파일 PASS, Docker build→import `core/feeds/.../timefolio` PASS, root & outside cwd wheel import, Timefolio browser `submit=false` test, Telegram/health/monitor roles valid, old `/app/src/vibetrading`/`vibetrading.*` active command/mount 0, secrets in logs 0, live mutation 0. 변경이 광범위하므로 import path diff와 functional runtime diff 다른 commits로 나눌 것.


### MOV-07 — Flat src/ + Full Entrypoint & Image Gate

- **선행** MOV-01~06. **담당** deterministic path checker, Codex release reviewer. **검사** immutable B1+ B2 original→target, `git ls-files src/vibetrading`, `git ls-files src`, root `strategies` conflicts, `pyproject` wheels, Docker profiles/volume mounts, service commands/healthchecks, cron/systemd, archived rollback manifests, Timefolio client/runner/monitor.
- **Gate-A**: `src/vibetrading/**` canonical runtime files 0, `import vibetrading.*` source/active config/CLI/Docker entry 0; any remaining stale text only in explicitly marked historical docs/rollback. `src/core,feeds,market,storage,strategies,execution,portfolio,runtime,operations,timefolio` all importable, no root `strategies` shadow, no `src` namespace imports. `python -m compileall -q src`; `pip wheel .` + editable/wheel from independent CWD.
- **Gate-B**: no duplicate live mutation owner/signer for same venue/product/environment, no mandatory `runtime/apps/{fv_mm,liquidity_mm,fv_pilot,mm_state}`, Timefolio competition submit/monitor evidence present, C3/KP/KD & Bybit/KIS Golden exact unless intentional path-only hash mapping.
- **Gate-C**: every active root Dockerfile and relevant `deploy/*.yml` overlay source/config/image/entry/volume/healthcheck updated, no bind mounted old `.py` paths; all selected compose profile combinations parsed & image smoke pass; NATS-free primary has no nats-init dependency, old NATS rollback artifact unchanged.
- **정밀 검증**: compare old/new symbolic mapping SHA, imports under `python -c` outside repo, `python -m runtime.cli --help` if available, executable container command, `docker compose config -q`, no-network startup, rollback drill dry-run; full Linux regression. **Source file path-only text grep 0만으로 PASS 금지**. 실패 시 해당 MOV 소유자에게 돌려 단일 차이 수정, 이 티켓 자체는 증거/상태만 기록.


### CLN-01 — Documentation, Receipts & Logs Git Hygiene

- **선행**: GOV-01, MOV-07. **담당**: Python inventory + GLM; Codex retention 승인. **읽기**: `docs/refactoring/runs/**`, old v3/Minimal Reuse, `docs/archive/README.md`, tests/readme links, `.gitignore`. **쓰기**: docs index, `.gitignore`, git evidence manifest, 권한 승인된 특정 obsolete docs; 운영 데이터 삭제 금지.
- **구현**: 과거 acceptance receipts/Golden/test oracle/provenance/runbook/rollback-critical source는 보존 목록 고정. 시도별 raw console/JUnit/AI response 중 중복·비권위 파일을 분류. 자체 업로드·외부 스토리지의 **복구 가능 경로+SHA가 있는** 파일만 tracked set에서 제거; 필요하면 git history에만 보존하되 현재 수락 증거가 다시 읽혀야 하는 경우에는 삭제 불가. 앞으로 raw logs 기본 `.local/artifacts`/CI artifact, Git에는 compact immutable acceptance summary만. `docs/status.md`는 자동 생성 또는 05로 단방향 연결.
- **수락**: Git tracked docs count와 제거·보존 건수 전/후 보고(목표는 측정, 임의 수치 강제 아님), 모든 04 manifest receipt 경로/해시 유효, 필수 링크 404 0, **`docs/final/`의 정식 5개 외 이전 계획 Markdown·중복 backup·반복 테스트/JUnit/AI response를 활성 개발 문서/기본 Git 추적 대상에서 제거하거나 근거 있는 별도 보존 경로로 이동**, 제거 불가능한 파일은 이유·Caller·예외 만료 기준 명시, 새 세션이 구형 계획을 참조하는 기본 경로 0. 구 계획서 본문을 신규 5개에 통째로 복제하지 않는다. `.gitignore`만 수정 후 완료 선언 금지.

### CLN-02 — Legacy Wrappers, Duplicate Authorities and Source Simplification

- **선행**: MOV-07, CAP-00. **담당**: GLM 조사·Codemod, Codex high-risk review. **읽기**: `services/`, `shared/`, root `strategy/` & `strategies/`, old `vibetrading.strategy_contract`, `fv_mm/legacy`, `brokers.py`, `perp_execution.py`, tests/deploy/dynamic refs. **쓰기**: proof-confirmed obsolete source/callers only.
- **구현**: 각 파일 `KEEP/ACTIVE_CANONICAL/ALIAS_REQUIRED/RETIRED_NO_CALLER/UNKNOWN`과 static refs/dynamic imports/entrypoints/tests/Docker/rollback refs를 증명. 중복 venue HTTP client/WS stream은 함수별 SAME product/env/signer인지 확인해야만 통합. 실행 중 또는 롤백이 필요한 NATS/legacy scripts, testnet tools, 연구 strategy는 삭제 금지. 실질 중복 제거 후 verbose/과도한 abstraction 제거는 **필수 `SIM-01`**에 함수·검증 케이스·위험 등급과 함께 넘긴다. 단순 보류로 CLN-02 완료 금지; 주문 사실/리스크/복구 gate 삭제 금지.
- **수락**: 삭제 파일별 증거 5종, retained canonical math/test exact, full regression/packaging, delete count + preserved reason report. 코드 수 감소 미입증 시 무리하게 0으로 만들지 않음.

**필수 삭제/보존 Gate (예전 보존 가정보다 우선)**
1. `fv_mm`, `liquidity_mm`, `fv_pilot`, `mm_state`에 대해 `HEAD path:function → selected canonical src/<domain>/module:function` 매트릭스 작성. 경제전략 S1-S4가 현재 활성 설정·실제 submit target에 없으면 전략 코드 생산 패키지에 잔존시킬 명분 없음. 필요한 signer/private parser/native sequence 등의 **검증된 기능만** 옮기고 기존 Bundle 자체는 `RETIRED` 가능한지 검토. 이전 testnet runner 로그는 파일 시스템/rollback artifact로 역사 보존할 수 있으나 현재 Docker 이미지의 라이브러리 의존성으로 만들지 않음.
2. `bybit_execution.py`: 현재 BybitOrderClient/BybitExecutionProbe/Audit의 실제 사용 여부, `kis_execution.py`: KisCashBroker real/mock/TR IDs, `perp_execution.py`: Binance/Bybit/GRVT/Arcus/HL adapter classes, `extended_brokers.py`: signed WS, `brokers.py`: Kiwoom/KIS 등 **서로 중첩되는 mutation authority**를 식별. `execution/venues` canonical 위치가 확보되고 다른 client/entrypoint에서 더 이상 사용하지 않으면 별도 모듈은 제거, 기능이 특이한 경우 정확한 함수를 canonical에 통합. 아무 이유 없이 old file을 매핑해서 최종 남기는 건 FAIL.
3. 문서·test fixture·entry script·Dockerfile/COPY/Compose/cron/systemd/Timefolio/page selectors의 active caller가 0인지 자동/수동 증거 확보. Root `strategies/`와 `src/strategies` 충돌 해결, old NATS wrappers는 새 실행 루프에서 사용 0. ROLLBACK/previous run 증거는 리포 밖 immutable artifact로 보존, 실제 원본 운영 기록 삭제 금지.
4. `KEEP`는 최소 1개 능동 caller 및 재사용 불가 근거/보존 기간/owner 제시. `DELETE`는 smoke/golden/economic semantics/private fill/account/Timefolio negative tests PASS. `DEFER`는 해결 전 `G-LEAN-CODE` 미충족으로 기록하되 보존 필요 근거를 설명할 것.


### SIM-01 — REQUIRED Ponytail Skill Review + Proven Verbose Code Reduction

- **선행**: CLN-02, STR-02, EXEC-01, VEN-01, CAP-05 (핵심 구현 후 가장 큰 복잡성을 대상으로 선택). **책임**: Ponytail Skill 실제 로드 상태의 OpenCode/Codex 리뷰; 구조적으로 안전한 deterministic codemod + scoped worker 구현, 위험 승인 Codex. **실행 범위**: 변경된 `strategies/contract`, `execution`/`portfolio`/`runtime`의 직접 Owner, 기존 venue 중복 HTTP/private wiring, `services`/`shared` legacy, 과도한 order wrapper, 반복 데이터 변환, 실패한 옛 전략의 불필요 runner. 해당 `old→new path`는 MOV-00 매핑 참조.
- **Ponytail 전제**: 현재 모델 컨테이너에서 설치 확인되지 않았으므로, 사용자 개발 환경에서 **공식 `DietrichGebert/ponytail` Skill의 설치·내용·권한을 직접 검토하고 실제 로드 증거**를 남긴 뒤 수행한다. 권고된 `ponytail-review` 모드/명령이 실제 설치 버전에서 지원될 때 사용한다. 출처 `https://github.com/DietrichGebert/ponytail`. 미설치·로드 실패 시 `BLOCKED_SKILL_NOT_AVAILABLE`; 다른 리뷰를 Ponytail 실행 결과로 위장 금지. 설치 과정에서 운영 비밀/서버 접근 금지.
- **구현**: 실제 변경 Diff·직접 caller·표준 라이브러리 대체 가능성을 검사하고 `REMOVE/INLINE/REUSE_STDLIB/KEEP_WITH_REASON/DEFER_WITH_DEPENDENCY`의 판단을 파일:함수 단위로 남긴다. 최소 1개 이상의 실제 적용 가능한 후보가 있으면 안전한 별도 `SIMPLIFY` 커밋으로 반영하며, 리뷰 결과 모두 KEEP이라면 근거·지표·해당 owner 범위 확인을 기록한다. 코드를 억지로 줄이기 위한 얕은 압축·안전 검사 삭제 금지. 경제 규칙·fee/funding·영속성·주문 동작의 모의수치 보존.
- **수락**: (1) Ponytail 실제 로드/버전/모드 및 검토한 함수 목록, (2) 구체적 findings(`path:symbol:before→after:risk:tests`), (3) 승인한 후보의 **실제 LOC/branch/wrapper/duplicate authority 변화** 또는 전부 KEEP의 구체적 증거, (4) 전체 회귀+주문/복구/파일/서버 경계 검증, (5) 사용자 목적에 비춰 고비용·과잉 동작 없음. *검토 문서만 만들고 코드 정리 없이 이유 없이 PASS 금지.*

### CLN-03 — README / AGENTS / Architectural Consistency

- **선행**: CLN-01, CLN-02, SIM-01, MOV-07. **담당**: GLM docs, Codex final. **쓰기**: `README.md`, `AGENTS.md`, `docs/architecture.md`, `docs/status.md`의 current links. **구현**: deployed legacy NATS vs local NATS-free candidate vs user-approved new server state를 명시적으로 분리. 5개 권위 문서만 정식, 이전 계획은 역사 링크만. 실제 설치/CLI/Release/strategy plugin/Data path를 사용자 관점으로 짧게 설명.
- **수락**: stale NATS production claims 없음, file paths resolve, 실제 `05` status와 documentation counters 동일, R07 topology matches import graph, 문서 생성만으로 제품 Gate PASS를 쓰지 않음.

### CAP-00 — Original Goal Capability Contract and Gap Freeze

- **선행**: GOV-01, MOV-07 (source path mapping 완료). **담당**: Codex Tech Lead, OpenCode small static analysis; **제품 변경 금지**. **읽기**: 현재 03 matrix, 이미 존재하는 TX00 `reuse_audit` 47 files/712 functions, `perp_execution.py`, `fv_mm`, `liquidity_mm`, KIS ETF, Paper, selected callers.
- **구현**: 사용자 최초 목표에 실제 필요한 `venue/product/environment/order_mode/strategy/account` 목록을 `03`의 **Required Capability Matrix**에 명시. 기존 코드 `KEEP/REUSE/ADAPT/MISSING/EXTERNAL_ONLY` 식별. model/testnet/local/fake와 Live를 혼동하지 않음. 이미 근거가 있는 함수는 재감사하지 않고 **변경된 대상의 직접 caller 1-hop**만 확인. 지원 결정이 없는 venue/product는 `DECISION_REQUIRED`, 완성했다고 선언 금지. 추가 영구 레지스트리 프레임워크 생성 금지.
- **국내 Broker 신규 범위 P0**: (a) KIS Mock/Mock2 기존 경로 보존 (b) **Kiwoom 국내주식/ETF 주문을 선택된 Required Local Route로 분류**, `brokers.py::KiwoomBrokerAdapter.submit_order`와 `order_status=NOT_IMPLEMENTED`를 확인해 `cancel/query/open/fills/account/reconcile` API 계약·출처·누락 경로를 함수 단위 표에 기록. Kiwoom REST vs Windows COM은 기능·OS가 달라 별도 줄로. 공개 문서나 기존 코드로 검증 불가능한 URL/TR ID는 만들지 말고 `BLOCKED_API_SPEC_REQUIRED` 기록 (c) NH/나무는 **미구현 future extension fixture**로 분류하며 코드/Live 요구를 추가하지 않음 (d) TOSS는 **신규 및 기존 Data 수집에서 정상 지원**하고 주문만 DATA_ONLY; Golden/시각 규칙 유지. 신규 CEX venue도 미래 extensibility만 지원, 가짜 Live code claim 금지.
- **수락**: 모든 REQUIRED 행마다 owner/source/accepted test/external gate 존재, blanket all-five connector rebuild 0, spot≠perp, paper≠live, KIS mock≠live. 함수 수준 gap이 없는 경우 새 코드 작성 0.

### STR-01 — C3/KP/KD Real File Plugin Completeness (R05의 한계 봉합)

- **선행**: MOV-05, CAP-00. **담당**: OpenCode DeepSeek + Codex economic review. **읽기**: `strategies/kr/{c3,kp,kd,_intraday}.py`, old/new `etf_trading.py`, `etf_history.py`, `strategy_contract.py`/`strategies/contract.py`, `strategy_runtime.py`, ETF Golden, config.
- **구현**: 현재 R05 `c3.py`는 `open_decision` 단일 함수, `kp.py`/`kd.py`는 `decide` wrapper이며 **파일 기반 독립 전략** 완료가 아님을 인정. 기존 전략 계산·history·schedule/Market Dependencies를 모듈 단위 `STRATEGY_ID`, `SUBSCRIPTIONS`, `WARMUP`, `create_strategy`, `on_event`/주어진 contract로 구성할 수 있는지 판별·구현. 복수 전략을 한 파일에 강제하지 않음. 기존 ETF KIS Mock/Mock2 등 실행 경로도 **같은 전략 파일이 낸 Action**을 소비해야 하며, `EtfTradingLoop`가 경제적 신호/Order Type 정책을 중복해서 다시 결정하면 미완료. 기존 ETF 주문 제출 API와 리스크 Owner는 그대로 재사용한다. 별도 Paper에서도 명시 등록, 자동 실거래 전환 금지. 전략 캔들/과거 Close/Open Decision/Foreign data/KP/KD timestamp의 인과성·기존 Golden 충족. 전략의 주문형식·계좌선택·실패 후 후속 행동은 파일에서 결정하고, 새 전략 파일 추가/삭제/중지는 중앙 execution core 코드 수정 없이 가능해야 함.
- **수락**: C3/KP/KD 각각 단독 explicit load, illegal startup/warmup reject, identical decision/sizing/side/price/feed cutoff/Mock2 semantics, Paper execution mutation 0, no duplicated order owner; `strategies/kr` 독립 모듈 삭제 시 다른 전략과 runtime import 정상. 필요한 Feed/History가 없으면 명확한 `UNSUPPORTED`로 표시하고 대상 범위 미완료.

### STR-02 — REQUIRED Full Strategy Order Action + Execution Feedback Loop

- **선행**: STR-01, MOV-05, CAP-00. **담당**: OpenCode/DeepSeek 소규모 구현, Codex 계약 리뷰. **읽기(이동 전 기준)**: `src/vibetrading/strategies/contract.py::load_strategy`, `strategy_runtime.py::PaperStrategyRuntime.register_configured/_dispatch/_execute`, `models.py`의 `Signal`, `trading_contracts.py`/`execution_ledger.py`의 `OrderIntent`, `execution_groups.py::ExecutionGroupSpec`, `etf_trading.py`, ETF Golden. **쓰기**: 단일 plugin contract, bounded Strategy Action 변환/피드백 callback, 직속 테스트; 전략별 경제 의사결정 if/else를 공통 Core에 추가 금지.
- **설계 구현**: `03`의 `ARCH-ACTION`을 기존 계약에 **호환되도록** 반영. 신규 `strategies/crypto/example_order_policy.py` 등 샘플은 **명시 테스트 fixture**, 실운영 자동 등록 금지. 각 전략 파일이 데이터/OrderEvent 입력에서 `StrategyActionPlan`에 해당하는 NEW/CANCEL/REPLACE, venue/account/instrument, BUY/SELL, maker-postonly/taker, GTC-park/IOC/FOK, 가격/수량, reduce-only, deadline, 실패/부분 체결 시 후속 Action을 **직접 산출**하도록 한다. FOK와 PARK/POST_ONLY의 실제 Venue capability 불일치 시 엔진 사전 거절. 기존 `on_event` API는 보존하며 `on_order_event` callback 이름/인수는 현재 loader와 migration plan에 맞춰 한 번 확정한다.
- **동작**: `OrderEvent(ACCEPTED,REJECTED,PARTIAL_FILL,FILLED,CANCEL_PENDING,CANCELLED,UNKNOWN)`가 strategy ID/Order key/cursor에 의해 **원래 전략 파일로만** 귀환. strategy 는 명시 거절 시 가격 수정·수량 축소·새 FOK 등 새 Action을 선택하고, 엔진은 이 결정을 대행하지 않는다. UNKNOWN·latefill은 account reconciliation 전 위험 재주문 차단; 정상 cancel/partial/new의 순서·복구 영속 claim 일치. TradingAction이 필요한 전략만 API를 사용하고 신호 중심 구 전략은 호환 유지.
- **MM P0 피드백 계약(기존 Action/Callback 확장만)**: 양방향 Maker 주문의 유지·재호가 시점/가격/수량, 목표 재고·헤지 규칙·상쇄 주문 여부는 **오직 전략 파일**이 결정한다. 엔진은 `OrderEvent`와 읽기 전용 `execution_context`(해당 전략의 open/working orders·잔여 수량·부분/전체 체결·취소 경합·수수료·계좌/상품별 포지션·가용 잔고/예약·factual strategy attribution 및 각 값의 event cursor/timestamp/quality)를 전달한다. 누락/불확실한 재고/잔고는 `UNKNOWN/UNAVAILABLE`로 식별하고 추정값을 확정값처럼 주지 않는다. 새 전용 MM policy/coordinator/state 엔진 작성 금지.
- **테스트**: 하나의 신규 Crypto sample file 변경만으로 PAPER A: Maker `PARK_GTC`→취소/가격변경→부분 체결, PAPER B: taker FOK→명시 reject→전략 callback 대체 Action, 기존 KIS Mock/Mock2: C3/KP/KD old input=old side/price/qty, strategy callback duplicate/raise/time gap/backpressure·restart. Core 로직에는 strategy ID별 FOK/park 하드코딩 0.
- **MM P0 추가 테스트(동일 샘플 fixture/기존 테스트에 흡수)**: 전략이 Bid/Ask Maker를 동시에 발행→Bid partial fill→전략 귀속 fill/working-order/계좌 balance/position callback 확인→전략만 Ask 재가격·Bid 취소/교체를 선택→cancel ACK 뒤 late fill을 정확히 한 번 반영→중단/재시작 후 전략이 복원된 체결·미체결 사실을 보고 다음 Action을 결정. Engine이 스스로 quote/hedge/재고 목표를 정하면 FAIL.
- **수락**: 테스트/Signal 의도에서 다음 Action까지 원 전략 파일의 함수 trace가 존재, 실체결 결과 동일 전략에 라우팅, 중복 Action/unknown blind retry 0, 기존 완료 Paper 2480 기준 Golden 보존(신규 HEAD에서 재실행), 수동 승인 없이 실계좌 주문 0. 샘플 전략 하나만 작동하고 기존 국내 ETF 경로를 연결하지 못했다면 `PARTIAL`.

### VEN-01 — REQUIRED Crypto Venue + Domestic Broker Extensible Adapter Registration / Disable / Removal

- **선행**: MOV-04, CAP-00. **실행자**: GLM/DeepSeek 좁은 변경, Codex/독립 Reviewer Owner/capability 승인. **권위**: `03::ARCH-VENUE-EXT(VEN-PROT-01~04)`, `ARCH-00A`, `ARCH-EXEC`, `ARCH-ACTION`. **레거시 상태 재구현 금지**: `execution_owner.py`는 현 HEAD에서 Local fake-only, `brokers.py`는 Binance Spot/Kiwoom/KIS/HL/TOSS factory 및 `extended_brokers`의 GRVT/Arcus 레거시 호출자, `perp_execution.py`는 선택된 five-perp primitive, `bybit_execution.py`는 probe; 이 중 *입증된 canonical*을 선택하여 감싼다.
- **읽기 allowlist(이동 전 경로; MOV-04 이후 MOV map으로 변환)**: `src/vibetrading/{brokers.py,extended_brokers.py,perp_execution.py,kis_execution.py,bybit_execution.py,execution_owner.py,execution.py,models.py,trading_contracts.py,local_config.py}`, `src/vibetrading/fv_mm/venues.py`, `src/vibetrading/liquidity_mm/exchange.py`, `src/vibetrading/strategies/contract.py`, `config/` 관련 선택 파일, 정확한 직접 호출자/테스트만. **쓰기 allowlist**: canonical `src/execution/{venues/**,brokers.py,perp_execution.py,extended_brokers.py}` 중 실제 필요한 등록/shim, existing `runtime` config/loader 등록 지점 한 곳, 관련 기존 테스트와 fixture. 다른 Client/Signer 신규 구현 금지; 안전 Owner 함수 변경은 `EXEC-01`로 이관.

**순차 구현 단계 (각 단계는 독립 Diff/Receipt)**

1. `VEN-01.1 Inventory`: `broker_for_name`, `make_perp_adapter`/`capability_for` 실제 존재 여부, `KisCashBroker`, `KiwoomBrokerAdapter`, `BinanceBrokerAdapter`, `ArcusBrokerAdapter`, `GrvtBrokerAdapter`, 현재 Bybit·Hyperliquid 호출 경로를 **source SHA + 함수명**으로 단일 표 작성. 현재 MOV 이동 완료 전 존재하지 않는 `execution/venues/` 경로는 target path임을 명시. 기존 Factory가 Venue별 `if/elif/mapping`을 소유하는지와 데이터 수집 Registry와 충돌하는지 확인. **정적 Factory의 등록 목록을 늘릴 수 있지만 Strategy/Router에 분기는 추가하지 않는다**.
2. `VEN-01.2 Contract bridge`: 신규 거대한 `AbstractExchange` 대신 현 Adapter Interface/Protocol에 필요한 `submit/cancel/query_order/list_open_orders/get_fills/account_state/positions/reconcile/instrument_spec/private_or_poll` callable + capability 사실만 정렬. Common Order/Event 타입 유지. Product별 unsupported는 request before-send에 `CAPABILITY_UNSUPPORTED`; native decimals/lot/min_notional/market session/holiday/order type/client ID, 계좌/환경 scope 검증. Perp funding/margin을 국내주식 Cash Adapter에 의무화 금지.
3. `VEN-01.3 Declarative config`: 기존 strategy config에 다음 최소 alias를 허용(새 Registry 서버 생성 금지). `strategy_id -> instrument alias -> (venue, market_segment, symbol, account_scope[, environment inherited])`. 예: `btc_spot=(binance,spot,BTCUSDT,acct_s)`, `btc_perp=(binance,usdm_perp,BTCUSDT,acct_p)`, `kr_etf=(kiwoom,krx_cash,069500,acct_k)`. 오타/중복/ambiguous tuple/reused account incompatible product → **Mutation 0**. `venue` 추가/삭제 후 Strategy 파일 내용과 Common Router import/diff 0.
4. `VEN-01.4 New adapter lifecycle`: Test fixture에서 `execution/venues/dummy_crypto.py`와 `dummy_stock_broker.py`를 각각 분리 등록, 둘 다 **Adapter Contract Harness(Owner permit/Action과 동등한 synthetic stub)**에서 `submit/query/cancel/fill` facts 생성. 실제 Strategy→Common Owner→Adapter 귀환은 후속 `EXEC-01`/`QA-01` 소유이므로 본 티켓에서 완성이라고 주장하지 않는다. Broker fixture에는 KR cash fill/quantity/fees, unsupported funding, no fake Margin; Crypto fixture에는 USDT perp 최소 capability. LIVE는 **두 fixture 모두 blocked**. 코드의 실제 Route entrypoint/Loader 등록이 import scanner를 통한 plugin auto-exec가 되지 않도록 explicit config only.
5. `VEN-01.5 Disable/Remove`: `ENABLED → QUARANTINED/DISABLED → REMOVED`의 기존 config/state 활용. DISABLED에서는 신규 위험 NEW/REPLACE 0, 원래 소유한 미체결/UNKNOWN에 대한 `query/cancel/read/reconcile` 가능; 한 venue disable 후 다른 Venue owner는 영향을 받지 않아야 함(shared account risk fault는 예외). `REMOVED`는 open/UNKNOWN/unreconciled/account/strategy alias/rollback 참조 중 하나라도 있으면 **거절**하고 정확 blocker 출력.
6. `VEN-01.6 Actual Broker bridge scope`: 기존 Kiwoom Class의 order `submit_order` 경로와 `order_status NOT_IMPLEMENTED`/필요 Cancel/GetFills가 missing인지 CAP-00 감사만 기록한다. **실제 API completion은 `CAP-01/02`가 소유**하므로 여기서 Endpoint/Signer 추측 구현 금지. KIS Mock/Mock2 원형 보호. TOSS 수집 등록/시세 입력 유지, 신규 주문 권한 0, DATA_ONLY 유지. NH/나무는 fixture로 구조만 검증하고 실 API/지원 선언하지 않는다.

**필수 결정적 테스트(새 이름은 구현 가능한 기존 테스트로 병합 가능)**

| Test ID | 준비/행위 | PASS assertion |
|---|---|---|
| `VEN-CRYPTO-ADD` | Dummy sixth Perp adapter file+config만 Add | 기존 Router/Strategy/Owner/core diff 0, registry import 및 **contract-harness** submit/ACK/fill normalized once (실제 Runtime 연결은 EXEC-01) |
| `VEN-STOCK-ADD` | Dummy NH-like KR Stock Broker file+config만 Add | Stock Buy/Sell contract-harness submit/cancel/query/fill normalized event, funding/margin unsupported, CORE diff 0; 전략 callback E2E는 EXEC-01 |
| `VEN-ALIAS-SPOT-PERP` | 동일 BTCUSDT text로 binance spot/perp alias 등록 | 서로 다른 `(market_segment,product,account_scope)`와 API capability 사용; ambiguity rejected before send |
| `VEN-MODE-REJECT` | KRX cash에 perp-specific reduce_only/funding 요구 또는 미지원 POST_ONLY/FOK | before-send fail closed, mutation counter 0, 의도 보존 |
| `VEN-DISABLED-READ` | 미체결 상태에서 A disabled; B enabled | A new risk 0, A query/cancel/reconcile 유지, B new risk 정상, late fill once |
| `VEN-REMOVE-BLOCK` | outstanding/UNKNOWN 또는 strategy reference or rollback 참조 | remove reject, active owner 유지; 사전 증거 모두 0인 dummy only removed |
| `VEN-TOSS-DATAONLY` | 신규 config가 TOSS 주문 라우팅 요청 | mutation 0, explicit DATA_ONLY error, old read-only fixture preserved |

**수락/미완료 판정**: 플러그인 2종(Crypto+Stock) 모두 Contract Harness 기반 위 검증 PASS, core/strategy 분기 증가 0, 기존 five-perp/KIS Golden 보존, live mutation 0. Dummy Broker만 성공하고 Kiwoom 실제 REST 코드 통합이 남았다면 `VEN-01`은 registry PASS 가능하지만 **플랫폼 G-TRADE는 CAP-01/02/EXEC-01에서 BLOCK**; NH 실 API가 없는 것은 `FUTURE_UNSUPPORTED`이지 이 릴리스 미완료가 아니다. Diff/경로·Capability Matrix·실제 Source SHA·정확한 테스트 exit/passed/failed/skip를 compact receipt로 반환.

**VEN-01.6 — Lean Canonical Adapter Source & Feed/Order Independence (P0)**
- 현재 `execution/bybit_execution.py`, `execution/kis_execution.py`, `execution/perp_execution.py`, `execution/extended_brokers.py`, `execution/brokers.py`의 클래스/함수별 caller·Live Testnet API ownership inventory는 MOV-04가 제공한다. VEN-01은 각 지원 venue/product/env에 정확히 하나의 canonical `submit/cancel/query/private` owner가 등록됨을 검증한다. 파일명 보존/빈 delegate wrapper 증식을 요구하지 않는다. 실제 중복 코드 제거는 CAP-01/02+CLN-02 이후 증명하고 다른 병렬 티켓에서 무단 삭제 금지.
- `ADD_EXECUTION`과 `ADD_FEED` Capability는 분리. 신규 Venue의 Adapter+Config만 있으면 주문 경로가 *미구현 Feed를 자동 만든다*고 주장 금지. 별도 수집기가 있는 경우에만 source readiness를 연결. 신규 Venue 예제는 단일 Execution Adapter 경로와 별도 `feeds/providers` fixture 또는 existing reference feed를 정확히 선언.
- **Acceptance IDs** `VEN-CANONICAL-01`: Bybit/KIS/Perp/Extended 기존 canonical owners 중 mutable signer 중복 0 (기존 변경 전 중복은 CLN-02 remediation로 기록), `VEN-COLLECTOR-INDEPENDENT-02`: fake order Venue만 추가한 뒤 Feed 없음→selected dependent strategy disabled, 다른 feeds/strategies unaffected; `VEN-CASH-03`: stock Broker config plugin but no `perp.funding` fake methods; `VEN-TIMEFOLIO-04`: Timefolio tournament client bypasses generic API registry unless explicit registered and verified, browser order remains guarded. 모든 테스트 `transport=fake`, mutation external=0.


### EXEC-01 — REQUIRED Common Strategy→Account Owner→Crypto Venue/Domestic Broker/Paper Execution Router

- **선행**: STR-02, VEN-01, CAP-01, CAP-02, CAP-03. **실행**: DeepSeek implementation + Codex HIGH-RISK owner audit. **권위**: ARCH-ACTION/ARCH-VENUE-EXT/ARCH-EXEC/ARCH-CROSS. **기존 `ExecutionOwner`가 local fake-only임을 변경 전 README/Docstring + source SHA로 증명**; mock E2E가 Live Owner 검증이 되는 것은 아님.
- **읽기**: 이동 후 canonical `execution/{execution_owner,execution_ledger,brokers,kis_execution,perp_execution,extended_brokers,execution_groups,paper_execution}.py`, `runtime/{strategy_runtime,real_runner,local_runtime,etf_trading}.py`, `portfolio/portfolio_ledger.py`, `strategies/contract.py`, selected ETF Golden, Kiwoom direct callers. **쓰기**: 이미 정한 router seam/Owner select + Adapter invocation + event feedback 직접 파일만; Capabilities/CLI/Strategy policy 재작성 금지. CAP-01/02가 소유한 실제 Kiwoom cancel/query/ledger adapter bug는 해당 티켓에서 구현하고, 이 티켓은 *하나의* Route/Owner에 연결.

**단일 Routing Contract (P0, 타입/함수 기존 코드 맞춰 최소 보정)**

```text
StrategyFile.on_event/on_order_event → Explicit ActionPlan
  → Resolve strategy_config.instrument_alias
  → key=(venue_id,market_segment,product,environment,account_scope)
  → capability+owner+product+account+readiness preflight
  → durable intent/client_id/reservation/attempt commit
  → selected adapter.submit/cancel/query (one authoritative owner)
  → normalize ACK/FILL/CANCEL/REJECTED/UNKNOWN
  → Execution Ledger + Portfolio facts → original strategy on_order_event
```

- **절대 경제 정책 미침범**: venue/pair, symbol/market segment, side, limit price, qty, TIF, maker/taker, park, cancel/replace, hedge/repricing, inventory target/equivalence는 Strategy만 설정. Engine은 실질량·precision/available_balance/capability/risk 확인에 실패하면 `REJECTED/UNSUPPORTED` 사실을 반환하며 전략 대신 가격/수량 조정·재시도·헤지 주문 생성 금지. 제품별 native API mapping/validation은 Adapter; 공통 Risk/Permit과 durable owner는 계좌별 단일 권위.
- **Env guard**: PAPER는 외부 mutation client 인스턴스 생성 0. KIS Mock/Mock2는 각 TR/API 모드 보호, Kiwoom 국내주식은 **선택된 KR cash route**로 Owner→현재 REST Adapter method(추후 CAP-01/02 완성)→ACK/order lookup/partial fill/reconcile 왕복. TESTNET은 해당 Venue의 독립 env auth; LIVE는 사용자 승인·장중 private/account reconciliation/active owner 검증 이전에 mutation 0.
- **Order identity**: `strategy_id, account_scope, venue_id, market_segment, client_order_id, attempt_id`는 영속·중복 방지, cancel/replace의 original owner/race/late fill 사실 유지. 여러 전략이 동일 Broker Cash Account를 공유할 때 Portfolio reservation 원장과 계좌 Owner는 단일·전략별 cancel 권한 분리. `DISABLED`은 신규 risk 차단하지만 query/cancel/reconcile 계속. `UNKNOWN` 송신 여부 불확실 시 blind resend 0. 지연된 private fill도 reconcile/source sequence 기준 1회 반영.
- **No core broker-specific branching**: 신규 NH-like Dummy Broker Plugin 추가 시 Strategy/Router/Owner의 source hash 유지. 실제 NH API 연결은 별도 미래 구현이며 이번 티켓에서 pretend implementation 금지. Kiwoom REST/legacy COM 구분; TOSS는 selected order route에서 차단.

**실행 작업 절차**

1. `EXEC-01.1`: 존재하는 `PaperStrategyRuntime._dispatch/_execute`, `EtfTradingLoop`, `ExecutionOwner.dispatch`, `broker_for_name`/selected Perp factory의 현재 Call Graph+tuple Authority를 기록. 원래 Owner를 대체하는 second ledger/router 생성 금지. 해당 Strategy callback의 확정 이름/signature는 STR-02 계약 그대로.
2. `EXEC-01.2`: Strategy Alias 해석→registered Venue/Broker capabilities→account Owner 결정·selection 오류 검사. Spot/Perp 동일 심볼 false sharing 0, KIS/Kiwoom KR cash route 충돌 0.
3. `EXEC-01.3`: 기존 공통 Risk/Reservation/Mutation Permit/ClientID durable-before-send 규칙을 재사용해 KIS Mock2, Kiwoom scripted class, two Crypto concrete selected venues, Paper matcher로 분기 없는 Owner dispatch를 연결. Router의 mode switch는 허용하되 거래소별 영업 정책이나 API payload를 Router에서 만들지 않는다.
4. `EXEC-01.4`: OrderEvent ↔ (Strategy, Account, Venue, Product) 결정적 귀환, fee/balance/positions/working status quality/age 포함 read-only context 제공. Strategy가 후속 Action 생성하지 않으면 엔진 신규 주문 0. Partial/Cancel Pending/Unknown/late fill/restore exactly-once Ledger 업데이트.
5. `EXEC-01.5`: Broker disable/remove 및 owner process double-lease/NULL credentials/Env drift 테스트, 기존 ETF Golden/crypto sample regression. 에러 시 단일 함수 단위 수정. No Greenfield refactoring.

**필수 acceptance tests**

| Test ID | Injection | Expected |
|---|---|---|
| `EXEC-PAPER-REAL-GUARD` | same strategy Action in PAPER | Paper matcher only, network mutation 0, order/fill event routed once |
| `EXEC-KIS-MOCK2-PARITY` | C3/KP/KD chosen sample config vs old Golden | side/qty/price/clock/account unchanged, mock adapter invoked and feedback to owning file |
| `EXEC-KIWOOM-KR-CASH` | original Kiwoom adapter via credential-free scripted transport: NEW→query/partial→CANCEL→late FILL→reconcile | actual canonical Kiwoom class methods used, all facts once, required methods not `NOT_IMPLEMENTED`, live 0 |
| `EXEC-NEW-DUMMY-STOCK` | Dummy NH-like Broker added via module+config | source hash changes in core/router/strategies 0, NEW/CANCEL/query/fill delivered, unsupported modes blocked |
| `EXEC-TWO-CRYPTO-SCOPE` | same symbol spot vs perp and two selected live-code Adapter paths with fake credentials/transport | correct segments/accounts/meta/fees, incorrect account or mode Mutation 0 |
| `EXEC-UNKNOWN-LATEFILL` | Timeout after transmit + cancel ACK precedes real fill + restart | no blind resubmit, one position/fee credit, same strategy attributed, required blocking until reconcile |
| `EXEC-MULTI-STRATEGY-OWNER` | two Strategies, same account and overlapping orders | one owner lease; Strategy A cannot cancel B's owned order; reservation not overallocated |
| `EXEC-VENUE-DISABLE` | Venue disabled mid outstanding order | new risk 0; outstanding read/cancel/reconcile valid; unrelated Venue continues |

**PASS**: STR-02 sample Action/Feedback, Paper/KIS Mock2/Kiwoom scripted KR cash/Crypto existing Adapter and explicit dummy Broker plug-through 모두 증명; exact receipt of existing class symbols/actual trace per scenario, mutable Production API 0; external live unknowns remain `EXTERNAL_ONLY`, **기능 구현 공백은 External-only 처리 불가**. CAP-05 two-leg는 이 Owner seam을 재사용하고 자체 Coordinator로 새 Router 만들지 않는다.

**EXEC-01.6 — 중복 Runner/Owner 전환 및 Timefolio 실행 소유권**
- 기존 KIS `KisCashBroker`와 `brokers.KisBrokerAdapter`의 owner 중복, Bybit `BybitOrderClient` vs `perp_execution.BybitPerpAdapter` vs `fv_mm.venues.Bybit`, Arcus/GRVT `extended_brokers` vs `perp_execution`, Binance Spot `brokers.BinanceBrokerAdapter` vs USD-M `/fapi`를 비교. 제품/환경별 하나의 canonical 실행 경로를 선택하고 `execution_owner`의 단일 submit permit을 거쳐야 함. Bybit latency Probe는 관측이 필요한 경우 운영용 Adapter의 instrumentation으로 이전하되 별도 live submit 백도어 허용 금지.
- Timefolio는 웹 페이지 기반 대회 주문 Runner. `src/timefolio/runner.py`에서 대회 주문 제출은 독립 권한/arm/safety gate가 소유한다. 이를 `Common VenueAdapter`로 위장하지 않되, 새 `src/runtime` 데이터 입력/NATS-free/보고 통합 및 기존 별도 승인 조건이 유지돼야 한다. generic Broker Router가 Timefolio에서 암묵적으로 Live submit하도록 연결 금지.
- **Acceptance** `EXEC-SINGLE-MUTATION-AUTHORITY`: 5 Crypto Perp+KIS+Kiwoom 로컬 scripted route에서 same account/venue/environment 단일 submit caller/one permit; `EXEC-REMOVE-PROBE`: old Bybit probe extra submit path cannot place orders, old names changed/retired without missing tests; `EXEC-TIMEFOLIO-FENCE`: missing submit flag 또는 account arm이면 browser mutation 0, Timefolio monitor separate from runner; `EXEC-FLAT-PACKAGE`: wheel outside repository import & Docker image path smoke PASS.


### CAP-01 — Selected Venue Execution Adapter *Reuse* Integration

- **선행**: CAP-00, MOV-04. **담당**: GLM/DeepSeek 구현, Codex critical reviewer. **읽기**: `fv_mm/venues.py` + `private.py`, `liquidity_mm/exchange.py`, `bybit_execution.py`, `services/execution/crypto_binance.py`, `extended_brokers.py`, `perp_execution.py` existing classes and only targeted callers. **쓰기**: 해당 canonical path, 좁은 route/binding, tests; duplicated full connectors 금지.
- **구현**: ARCH-00B의 다섯 Perp code 지원 목표를 기본 REQUIRED로 유지한다. 명시적 사용자 승인이 없으면 지원 대상 전체를 선택 사항으로 다운그레이드하지 않는다. 선택된 venue×product×env 행별 canonical submit/cancel/status/fills/account+symbol/product metadata/TIF/reduce_only/precision/permission bridge. Binance Spot vs USD-M Perp, Bybit testnet vs live, GRVT subaccount, Arcus signing/WS, HL SDK nonce vs read-only stub를 구분. `perp_execution.py`의 injected Transport가 없는 code mapping은 `CODE_MAPPING_ONLY`라서 실제 selected runner가 필요하면 작은 검증된 Transport adapter를 연결하되 기존 mutable clients를 우선 사용. Timeout 이후 `OutcomeUnknown`→query; 위험한 자동 재전송 금지. GRVT client ID 추정 결함 등은 reproducer 없으면 손대지 않음.
- **수락**: 각 REQUIRED 행의 fake signed request/outcome fixture, reject/ACK lost/cancel late fill, mode/secret-free, live gating, owner permit, zero duplicate client. 불필요한 5개 client 신규 생성 0. 실제 venue 인증은 EXTERNAL_ONLY로 별도 기록.

**CAP-01 추가 구현 서브스텝 — 국내주식 Kiwoom 필수 Local Route**

1. MOV-04 이후 `execution/brokers.py::KiwoomBrokerAdapter`(변경 전 `src/vibetrading/brokers.py`)의 `connect/account_state/submit_order/order_status/_post_api/_access_token`을 감사해 **기존 Native REST Client를 재사용**한다. 키움 선택 상품의 취소/정정/미체결 조회/실제 체결 조회/잔고/계좌 식별·모의 vs 실전 API ID와 응답 schema는 현재 공식 문서 또는 검증된 기존 코드에서 확인한 경우만 작성한다. **새 Endpoint/TR ID 추정 금지**. Windows COM 경로는 별도 selected mode일 때만 취급.
2. 필요한 `cancel_order`, `query_order`, `open_orders`, `fills` 호출을 기존 Kiwoom Adapter에 최소 함수로 연결하고 `OrderEvent` 공통 factual mapping을 보존한다. Broker-specific mapping은 이 파일, 계좌 영속/상태확정은 `CAP-02`/Owner. Kiwoom `order_status`가 `NOT_IMPLEMENTED`를 반환하는 한 **Required Kiwoom local route 미완료**. 부족한 API 증거는 `BLOCKED_API_SPEC_REQUIRED` 기록하고 금지된 추정 구현으로 성공 위장 금지.
3. KIS Mock2·기존 ETF Economic Golden 재실행. `KiwoomBrokerAdapter`의 fake HTTP 응답 케이스를 `accepted/rejected/partial/cancel pending/cancel late fill/timeout-unknown/account query`로 구성하되 실제 계좌 mutation 0. 별도 NH actual API 파일 생성 금지; NH Dummy는 VEN-01/QA-01 담당.
4. 수락 Receipt: 원본 class reuse proof, API 계약 출처/샘플 응답 hash, 테스트 시나리오별 expected→actual status/fill/fee/quantity/account id, mutation count 0, KIS original Golden checksum unchanged.

### CAP-02 — Private Order/Fills + Reconnect/Reconcile

- **선행**: CAP-01. **담당**: DeepSeek + independent Codex review. **읽기**: existing `fv_mm/private.py`, `Runner.reconcile`, `liquidity_mm/runner.py::reconcile`, `execution_owner`, `perp_execution.normalize_private_event`. **쓰기**: 필요한 selected venue bridge + fake tests.
- **구현**: private auth/subscription/session ready, out-of-order late fill, duplicate fill ID, ACK-before/after-fill, `cancel ACK != terminal`, gap→halt affected account, REST order/history/positions/funding rewind with pagination proof, final reconciled resume. 재연결 시 새 주문 전 account truth 확보. 선택된 venue가 유효한 Private stream을 제공하지 않으면 documented polling fallback + bounded staleness.
- **MM 필수 사실 전달**: 신규 정책 없이 `strategy_order_key/client_order_id`와 거래소 체결 ID를 연결하고 partial fill→position/balance/order projection의 인과적 순서·최신성·중복 제거를 보장한다. 원래 전략이 최신 계좌/주문 상태를 못 보거나 미확정 정보를 확정으로 받으면 P0 FAIL.
- **수락**: scripted disconnect, duplicate, missing-page, partial history, unknown order, stop/restart tests; gate 닫힌 동안 새 위험 주문 0, old query-only capability preserved, fee/funding once.

**CAP-02 추가 구현 서브스텝 — Kiwoom Cash 주문·체결 Reconciling**

- 기존 `CAP-02`의 Crypto private WS/REST recovery scope를 유지하면서 **Kiwoom Cash는 선택된 지원 mode에 맞는 bounded REST polling**으로 동일 `OrderEvent`/Ledger reconciliation 수락. `order_number`/`client_id` 매핑, 동일 order의 조회↔체결 데이터 overlap dedup, `CANCEL_PENDING` 후 late fill, partial fill·미체결 수량, 조회 시간창/페이지 끝 누락 `INCOMPLETE_HISTORY`, 응답 timeout `UNKNOWN` 구분. REST rate limit/backoff는 bounded; 새로운 전략 주문은 불확실성 해소 이전 0.
- 환경별 Broker가 authoritative fill history를 노출하지 않거나 실제 계좌 주문 조회가 불가능하면 로컬 `UNSUPPORTED/BLOCKED_API_SPEC_REQUIRED`로 기록; **모든 주문 조회를 `FILLED`로 간주하거나 종가·현재 잔고 차이로 fill을 추정하지 말 것**. 이 티켓은 전략의 매매 정책 및 대체 주문을 생성하지 않는다.
- Credential-free fixture: NEW Ack → partial 2/5 → cancel acknowledged → late fill 1/5 → remaining cancellation confirmed; restart 시 cumulative fill=3/5 exactly once. Timed out status → UNKNOWN; no new risk until successful query/reconcile. 기존 KIS Mock2/Perp reconciliation 케이스 회귀 유지.

**사용자 확정 수수료 정책 (2026-10-28, 기존 보고액 의존 후속 과제보다 우선)**

- Kiwoom Cash 계좌의 수수료율은 사용자가 계좌별 config에 나중에 명시한다. 설정한 요율을 주문 전 자금 예약, PAPER 체결 비용, 전략별 손익 귀속의 비용 기준으로 사용한다. 임의 실제 요율·기본0·자동 요율 조회는 추가하지 않는다.
- 증권사 수수료 보고액 수신·구독·추가 조회·보고액 대조/정정 작업은 수행하지 않는다. 수수료 필드 미수신 자체를 신규 위험 차단 사유로 삼지 않는다. 주문·체결·잔고·누락 복구 안전 검증은 유지한다.
- Config 비용은 `ACCOUNT_CONFIG` 근거와 정확한 계좌/상품/환경·요율을 주문의 불변 ProductSpec에 묶는다. 미설정·잘못된 설정은 명확한 config 오류이며 증권사 데이터 부족으로 표현하지 않는다. 기존 미가격화 영속 체결을 소급 변경하거나 기존 receipt를 다시 부과하지 않는다.
- 구현/검증 패킷: `docs/tickets/CAP-02-account-fee-config.md`. 현재 Kiwoom 수수료 보고액 연결을 요구했던 CAP02 후속 설명은 이 결정으로 대체한다. CAP03 전체 계좌·마진 작업은 별도 티켓으로 남는다.

### CAP-03 — Perp Cashflow, Product Constraints, Same-account Live Ownership

- **선행**: CAP-00, CAP-01, CAP-02. **담당**: DeepSeek/GLM implement + Codex critical review. **읽기**: existing `portfolio_ledger.py`, `perp_execution.py` metadata, `execution_owner.py`, `execution_groups.py`, `etf_journal.py`. **쓰기**: 선택된 제품 범위만, existing ledger/owner methods.
- **구현**: selected linear perp contractSize/tick/lot/fee/funding/collateral/reservation/position/margin model; gross worst-case open and UNKNOWN; physical account per `(venue,account,subaccount,margin_scope,env)` single owner; strategy virtual attribution·partial fill/close·funding allocation. 상용 통합 Margin 모델, inverse, hedge/cross/portfolio mode는 **지원한다고 선택된 때에만** 정확한 math/API 근거로 추가. 실제 net/cross vs isolated 차이 무시 금지. `TX03A/B/03` 역사 미완성은 이 티켓의 선택 스코프에 합쳐서 닫는다.
- **수락**: 계산 가능한 selected Paper synthetic fixture 수동 expected equity/available/IM/MM/fee/funding/PnL, account-scope concurrent order competition, restart+latest facts+UNKNOWN, unsupported mode rejects. Real venue current margin state 신뢰성은 operational external, 로컬 math gate 생략 불가.

### CAP-04 — Multi-strategy Process Isolation, Fairness & Latency

- **선행**: STR-01, CAP-03, MOV-06. **담당**: performance+runtime worker; Codex contract review. **읽기**: `strategy_runtime.py`, `strategy_runner.py`, `local_engine.py`, `hot_path.py`, selected plugin, `runtime_control.py`. **쓰기**: 필요 최소 worker safety / queues / tests.
- **구현**: 공유 Event 1회 적용, per-plugin bounded inputs + runtime CPU/timeout/effects; blocking infinite strategy가 나머지 Market Apply와 mandatory cancel/reconcile을 막으면 **Production-grade isolation fail**. Cooperative-only라면 명시된 신뢰 플러그인 범위에서만 지원하며 필수 untrusted multi-strategy 목표를 완료했다고 하지 않는다. Process-isolated bounded pool은 실제 2vCPU benchmark로 통과할 때만 도입. Fair scheduling, strategy pause/resume fencing, queue overflow strategy-specific invalidation.
- **수락**: slow/throw/blocked strategy scenarios, 다른 strategy active while one halted, feed quality dependencies preserved, interprocess duplication 0, tail-latency+RSS measure; unverified hard preemption falsely claimed 0.

### CAP-05 — REQUIRED Cross-Venue Execution Group (Strategy ActionPlan → A 매수/B 매도)

- **선행 추가 규칙**: `STR-02`, `VEN-01`, `EXEC-01`가 만든 전략별 ActionPlan·OrderEvent·공통 Owner seam을 실제 그룹 구현이 사용해야 한다. 신규 Group 레그의 매매 의도/실패 대응 정책을 Coordinator 내부에 하드코딩하지 않는다. **전략 파일**이 Group Side, FOK/PARK, 순차·동시, 후속 헤지·취소 결정을 소유하고 Group Owner는 안전 검증·전송·대조·영속성만 소유한다. 실제 두 Venue 잔고·원장/예약은 atomic distributed commit을 약속하지 않으며 crash-cut 중복 제출 0을 증명한다.
- **필수성**: `required_for_offline_release=true`, 조건 없는 `REQUIRED_CORE_CODE`. **CAP-00의 전략 선택 여부 또는 실계좌 거래소 페어 활성화 여부로 구현·테스트를 건너뛰면 FAIL.** 특정 A/B 페어의 TESTNET/LIVE 계좌 활성화, 실제 API 인증 및 실주문은 별도 opt-in/외부 승인이다. `NOT_APPLICABLE_APPROVED`는 이 티켓의 종료 상태로 허용하지 않는다.
- **선행**: CAP-01(선택한 Venue의 실제 Adapter/Owner seam), CAP-02(주문/체결/재대조), CAP-03(계좌·예약·잔여 위험). **담당**: OpenCode/DeepSeek 개발 + Codex 고위험 수락. **기존 소유권 후보**: `src/vibetrading/execution_groups.py::{GroupLeg,ExecutionGroupSpec,PaperExecutionGroups}`는 **PAPER 관측 전용**이므로 실주문 Coordinator로 오인하거나 기존 관측 동작을 파괴하지 않는다. `execution_owner.py::ExecutionOwner`는 현재 **local fake-adapter owner**이므로 실계좌 권위로 선언하지 않는다. 기존 `fv_mm`, `liquidity_mm`, `perp_execution`, `execution_ledger`, `portfolio_ledger`, `strategy_runtime`, 필요시 `real_runner`를 활용한다. 아래 경로명은 MOV 이전 기준이며 이후에는 MOV-00의 매핑을 사용한다.
- **쓰기**: 선택된 공통 Group 계약·Orchestrator, 기존 소유 Owner/Adapter 연결 seam, 영속 Fact/Journaling 및 관련 테스트만. 별도 거래소 HTTP/Signer/PrivateWS를 중복 생성하지 않는다. `PaperExecutionGroups`의 기존 read-only observation 계약은 보존한다.
- **입력 계약**: import 가능한 **독립 Strategy File Plugin**이 한 의사결정에서 두 Leg를 가진 `CrossVenueIntent`(이름은 구현 시 확정) 또는 기존 Intent의 명시적 Group 확장을 발행한다. 공통 원소는 `group_id`, `strategy_id`, `config_hash`, `decision_time/available_at`, `deadline`, `mode`, `base_asset`, `legs[]`이며, 각 Leg에는 `leg_id`, `venue`, `product/segment`, `account_scope`, `symbol`, **side(BUY/SELL)**, `quantity`, `limit_price`, `TIF`, `reduce_only`, `client_order_id`, 가격·수량 규격 및 위험 설정 참조가 있어야 한다. A BUY / B SELL이 기본 수락 시나리오이며 반대 방향도 가능해야 한다. 두 거래소의 상이한 계약 승수·단위·기준자산을 검증하고 단순 수량 동일성을 헷지 동등성으로 오인하지 않는다.
- **경제적 판단 권한**: 두 Instrument가 경제적으로 동등한지, Cross-Venue 스프레드/체결 가능성, 보유 재고 사용, Leg 수량 비율, hedge instrument/size/trigger는 **전략이 판단**한다. Group/Adapter는 각 Leg의 실제 상품 메타데이터·체결·계좌 위험 한도만 사실대로 검증·노출한다. 서로 다른 계약 승수를 엔진이 임의로 재조정하거나 `base_asset`이 같다는 이유로 헤지를 자동 가정하지 않는다.
- **준비·영속 계약**: 각 Leg의 계좌 Owner 및 주문 가능 상태, 두 Venue의 freshness/quality, 각각의 예약금·Leg별 최악 위험 노출 및 설정된 계좌/그룹 한도·명시된 추정 비용 상한을 **첫 전송 전에 확인**한다. 스프레드 수익성/펀딩 기대/슬리피지 예측 및 Leg의 경제적 동등성은 전략이 계산하며 엔진은 이를 새로 추정하지 않는다. 같은 Group의 Leg 정의·client ID·위험 예약·attempt 및 decision evidence를 영속 Commit한다. 어떤 Leg도 사전검사 실패 시 임의 발송하지 않는다. 원자적인 **서로 다른 거래소 체결**은 보장하지 않는다.
- **실제 실행**: `submit_mode=CONCURRENT`일 때 하나의 Group Dispatch에서 두 개의 **서로 다른 Venue Owner/Adapter**에 BUY/SELL 전송을 `asyncio` 등의 bounded concurrency로 시작한다(첫 ACK를 기다린 뒤 다른 Leg를 제출하는 방식이 아님). `submit_mode=SEQUENTIAL`도 지원하되 설정 없을 때 거래 전략에 유리하다고 임의 추정하지 않는다. Leg별 send/ack/last_exchange_state/fill/fee/funding/cancel/UNKNOWN를 별도로 유지하며 Group 결과는 두 Leg의 사실을 합성한 **비원자적** 상태다. API timeout/ACK loss는 `UNKNOWN`으로 취급하고 조회·대조 전 무조건 재제출하지 않는다.
- **부분 체결·위험**: A만 체결되고 B가 거부/지연/부분체결/연결해제되면 **Leg별 실제 체결 수량·계약 단위·원장 노출과 잔여 예약을 기록·검증해 전략에 전달**한다. 경제적 delta-neutral 여부와 hedge 수량은 전략이 계산한다. 사전에 승인된 `WAIT/HALT_AND_RECONCILE/CANCEL_REMAINDER/HEDGE_BOUNDED/REDUCE_ONLY_FLATTEN` 등 정책은 전략이 **구체적 후속 OrderAction(상품·계좌·수량·가격/한도)을 제출하는 방식**으로 적용한다. 엔진은 이 Action의 위험 허용 여부만 검증하며 시장·수량·타이밍을 결정하지 않는다. 무제한 hedge loop, stale 가격 기준 시장가, 다른 전략 소유 주문 취소, `cancel ACK == final` 가정 금지. 위험·시간·API budget 초과 시 신규 위험 발송을 차단한다. Hedge/flatten도 기존 권위 Owner와 체결 대조를 통과해야 하며, 정책 미설정 시 자동으로 임의 수량을 보충하지 않는다.
- **Restart/Recovery**: 영속 Group·Leg별 client ID·submit attempt·부분 체결·reservation·최신 실주문 상태를 읽고 각 Venue의 주문·체결을 조회해 **동일 Group의 후속 결정**으로 복귀한다. 불확실한 Leg/비연속 이벤트/원장 불일치 시 Group `BLOCKED/RECONCILING`; 이전 Group을 새 시그널로 중복 실행하지 않는다. Group/Leg idempotency와 per-account single owner를 유지한다.
- **테스트용 코드 연결**: 테스트 전용 가짜 Venue 이름 두 개만으로 PASS 처리하지 않는다. **적어도 서로 다른 두 개의 구체적 지원 대상 Venue Adapter의 실제 Mapping/Owner 호출 경로**를 자격증명 없는 scripted transport/fake response로 연결하는 local integration을 포함한다(예: Bybit와 GRVT, 실제 가용 범위에 맞춰 CAP-01 결과 사용). 실계좌 API 호출 0, 실제 API 검증은 EXTERNAL_ONLY. 이 단계의 Code Complete는 *임의의 지원되는 두 Venue를 선택할 수 있는 인터페이스*를 뜻하지 모든 실제 페어의 실계좌 인증 완료를 뜻하지 않는다.
- **필수 수락 시나리오**: (0) 전략이 서로 다른 계약승수·단위에 대한 Leg 수량/경제적 매칭을 직접 결정하고, 엔진은 자동 1:1 매칭·헤지 수량 추정을 하지 않음, (1) A BUY/B SELL 정상 두 Leg, (2) `CONCURRENT` 두 제출이 **서로의 ACK를 기다리지 않고 시작**됨을 Barrier fixture로 확인, (3) `SEQUENTIAL` 순서 준수, (4) A full fill/B reject, (5) A partial/B partial 불균형, (6) A submit UNKNOWN/B 정상, (7) 양쪽 UNKNOWN, (8) B Private WS gap/REST pagination 누락, (9) cancel ACK와 late fill 경쟁, (10) 중복 신호·재시도·그룹 재시작, (11) hedge 제약 위반/한도 소진/timeout, (12) stale 한쪽 feed 또는 계좌 owner 없음 → 양쪽 신규 발송 0, (13) 다른 전략 주문 미침범, (14) 동일 전략의 여러 Group 격리, (15) 주문·체결·수수료·funding·잔여노출 보고 일치. **모든 필수 수락은 credential-free 로컬 코드 테스트**로 입증하고, 실제 A/B 계좌·환경 활성화는 하지 않는다.
- **완료 판정**: 구현+local E2E+fault/restart PASS 및 기존 MM/KIS/Paper Golden 보존 시 `ACCEPTED_LOCAL`. `OBSERVATION_ONLY`, `CONTRACT_ONLY`, `NOT_APPLICABLE_APPROVED`, `PENDING_PAIR_SELECTION`은 완료 아님. 특정 실제 페어를 활성화하지 않았다는 이유만으로 CAP-05의 코드 완료를 막지는 않되, 그 페어의 `PRODUCTION_VERIFIED`를 주장할 수 없다.

### CAP-06 — Selected Live/Paper Strategy Routing, Account Promotion Gate

- **선행**: STR-01, STR-02, VEN-01, EXEC-01, CAP-01~04의 필요한 REQUIRED 및 **무조건 필수 CAP-05**. **담당**: GLM config + Codex risk review. **읽기**: `local_runtime.py`, `real_runner.py`, `execution_owner.py`, `strategy_runtime.py`, `local_config.py`, current YAML. **쓰기**: selected config/owner glue; default live order env settings 변경 금지.
- **구현**: 같은 Python Strategy Plugin을 구성 파일로 PAPER/TESTNET/승인된 LIVE mode에 안전하게 dispatch; `PAPER`에는 외부 real client 자체 생성하지 않음. 프로모션 시 Paper position을 실제 잔고에 복사하지 않고 실제 broker account+working orders reconciliation 필수. 계좌 동시 owner 없고 subaccount/venue/mode 별 fence. Config 오타·미지원 모드·데이터 stale은 fail closed.
- **수락**: paper/testnet/live fixture 분리, bad env token rejection, unauthorized live mutation 0, startup with unknown balance halted, name-only strategy registration without central if/elif, C3/KP/KD legacy protected.

### QA-01 — Full Product, Multi-Source Independence, Broker Plugin & Reliability Gate

- **선행**: 기존 DAG 그대로 (`MOV-07/CLN-02/SIM-01/STR-01/02/VEN-01/EXEC-01/CAP-00~06`). **담당**: Deterministic Linux CI + 독립 승인자. **권위**: ARCH-DATA-4 + VEN-PROT-01~04 및 기존 P0 안전/Release 전부. **쓰기**: 기존 `tests/**`의 관련 Scope, fixture, compact receipt만. 새 서비스/Strategy 알파 구현 금지.
- **기본 계약(변경 없음)**: R04/R05 Golden, VN/TX 2,480 baseline historical only, C3/KP/KD/KIS Mock2 exact parity, full pytest/CLI/wheel/Compose, UDS→Spool→State→Strategy→Paper/selected Owner, live mutation 0, UNKNOWN+late fill+crash cut+cloud/GC safety. 기존 MM 전략 소유 Bid/Ask→partial→inventory facts→전략 리프라이스만, CAP-05 양 Crypto Adapter 두 leg/local E2E를 별도로 필수 통과.

**추가 P0 QA 배터리 — 기존 테스트 파일에 병합 우선**

| Case ID | 단위 구성(credential-free) | PASS/FAIL 판정 및 구체 증거 |
|---|---|---|
| `QA-DATA-01` | A/B 서로 다른 두 existing Provider 또는 native-equivalent stub; B 미연결 0 event·A 100 event | A `DURABLE`/`ENGINE_APPLIED`/strategy receive count 100, ordered per A cursor, B delay가 A admission/apply blocking 조건 아님; all-venues join 존재 시 FAIL |
| `QA-DATA-02` | A 연속 100, B disconnect/gap/stale/late reconnect | A source state monotonic; B-dependent Arb Action blocked while A-only active, new B generation resets appropriate, no synthetic ffill |
| `QA-DATA-03` | Binance `BTCUSDT spot` + `BTCUSDT USDM Perp`, out-of-order interleaving | schema IDs/scope/tick/lot/cursor/availability 분리, strategy sees both snapshots without hard join |
| `QA-DATA-04` | Parquet/Cloud/Telegram stuck 60s (fake clock/controlled scheduling) | A1 local durability+market apply가 달성된 이벤트는 Cloud ACK 기다리지 않고 Strategy 전달; queue remains bounded; no silent drop |
| `QA-DATA-05` | low-rate first event group_sync + burst, fsync EIO, restart | `ACCEPTED_RAM/APPENDED/DURABLE/APPLIED` 증거 분리, pre-DURABLE strategy 0, replay cursor exactly once |
| `QA-DATA-06` | Startup/restore B proof delayed vs A proof ready + both strategy A-only/B-required, `event_source(required_producers=None)` vs `()` fixture | 수집·Spool에 cross-source barrier 없음; A-only route는 완전한 A restoration+Owner/account proofs 이후만 활성. B-required route는 B full readiness 전 risk Action 0. all-producer checkpoint gate가 막는다면 안전한 근거 없는 우회가 아닌 `BLOCKED_RECOVERY_DEPENDENCY` 및 정확한 결함 범위 기록 |
| `QA-BROKER-01` | Dummy Crypto Venue + Dummy NH-like Domestic Broker 각각 adapter file+config Add | Router/Strategy/Core unchanged hashes, both exact Owner Action→Ack/Fill/Cancel events, unsupported product rejected |
| `QA-BROKER-02` | 기존 `KiwoomBrokerAdapter` selected KR cash, mock REST fixture | actual class method called; `submit/cancel/query/open/fill/reconcile` facts complete; required status `NOT_IMPLEMENTED` 0, fees/partial/late fill correct; no external API |
| `QA-BROKER-03` | KIS MOCK/MOCK2 + C3/KP/KD against old Golden | price/size/side/account/TR semantic unchanged; no new duplicate owner |
| `QA-BROKER-04` | disable A with outstanding/UNKNOWN; B active; remove attempt A | A no new risk but existing reads/cancel/reconcile work; B order normal; removal blocked until dependencies 0 |
| `QA-BROKER-05` | unsupported KRX Cash perp options; TOSS selected Order Action; NH dummy Live request | before-send `UNSUPPORTED/DATA_ONLY/BLOCKED` respective, mutation counter 0 |
| `QA-END2END-01` | MM bid/ask on concrete Broker/Perp and CrossVenue two Perp scripted paths | Strategy owns each follow-up; OrderEvent and available balances returned; restart/crash/fill idempotency; CAP-05 separate group durability |

**통합 수락 절차**: (1) verify frozen candidate SHA + host/test command; (2) exact focused tests before full Linux suite; (3) audit `02` scope and `03` relevant contracts + uncovered features; (4) parse structured results, require `skip/deselected` on REQUIRED local tests = 0; (5) functional safety finding P0/P1=0; (6) store compact `qa_acceptance.json` with `scenario_id, exact source_SHA, command, exit_code, assertions, fixture hash, actual adapter symbol, external_call_count, exception, resolved path`; (7) report missing external proof separately.

**PASS**: 모든 REQUIRED 로컬 경로(기존 five Perp + CrossVenue + selected domestic Broker/KIS + both Dummy adapter plugin families + data no-cross-source barrier) executable and verified. Kiwoom required method gap을 dummy만으로 숨기거나 TOSS/NH Live 지원으로 가정하면 FAIL. Reference latency threshold miss alone is not FAIL; DATA functional waiting for B or Cloud at hot delivery gate is FAIL. 2vCPU profiling belongs PERF-01; 이후 PERF에서 code diff가 발생하면 QA scoped+full 다시 PASS 후 REL-01.

**QA-01 보강 — Flat Import / Legacy Absence / Timefolio / Docker Matrix**

| ID | 확인 | PASS 기준 |
|---|---|---|
| `QA-FLAT-IMPORT` | `/tmp` wheel, fresh editable, repo-root/non-root cwd에서 core/feeds/market/storage/strategies/execution/portfolio/runtime/operations/timefolio import | exact expected module file, `vibetrading.*` 실행 import 0, `strategies` root collision 0 |
| `QA-SOURCE-SINGLE-OWNER` | 한 Venue+Product+Env에 중첩 Bybit/KIS/perp/extended client 참조 찾기 | production order transport/signer canonical owner 1, legacy probe에서 주문 직접 submit 0 |
| `QA-LEGACY-LEAN` | 4 MM old bundle + old separate execution files active importer/runner/deploy image scan | 필요 함수 owner 이관, 불필요 old Bundle/legacy client 미포함, retention/rollback receipts preserved |
| `QA-TIMEFOLIO-CONTEST` | fake browser: account missing, submit=false, submit armed, ACK ambiguous, duplicate click, dashboard monitor without order rights | 미승인 mutation 0, once-only safe intent, UNKNOWN reconcile/stop, correct order and competition weight unchanged |
| `QA-DOCKER-BUILD` | root Dockerfile*, deploy/Dockerfile*, build_release_images, compose root + 4 overlays combinations; no network dry-run startup | image new package imports, required roles/health commands exist, old bind paths 0, manifest frozen SHA, source/config hashes match |
| `QA-COMPOSE-NATS-FREE` | new selected compose effective config inspect `depends_on`, environment, mounts, secrets | primary services do not depend on NATS/nats-init; previous NATS compose preserved as disconnected rollback profile |
| `QA-REPO-ENTRYPOINTS` | `main.py`, `run_strategy.py`, script imports, cron/systemd and dashboard/timefolio module entrypoints | no unresolved `vibetrading.*`, import semantics/CLI options preserved |

이 매트릭스 외 KIS/ETF/C3/KP/KD/Native L2/Paper/Live-fence/Group fault/restart test는 원래 QA-01 요구 그대로 수행. 테스트 코드에 `sys.path`를 임의 삽입해 module shadow 문제를 감추면 Gate FAIL.


### PERF-01 — REQUIRED Multi-Source Hot Path Profiling / 2vCPU / Storage & ACK Bottleneck Validation

- **선행**: QA-01. **실행자**: deterministic Python trace profiler + GLM/DeepSeek *필요한 경우 좁은 최적화*, Codex safe-change 승인. **읽기**: MOV-02/03 accepted canonical `feeds/local_pipeline.py`(old `local_pipeline.py`), `storage/market_spool.py`(old `market_spool.py`), `runtime/local_transport.py`, `market/local_engine.py`, `market/hot_path.py`, `runtime/strategy_runtime.py`, `execution/execution_owner.py`, `portfolio/portfolio_ledger.py`, `storage/{storage_worker,parquet_store}.py`, scripted venue adapters, QA scenario fixture. **쓰기**: benchmark script/trace assertions, 직접 병목이 입증된 existing function 및 해당 테스트만. 이동 경로가 MOV map에 다르면 **실제 canonical 경로로 resolve 후 변경**. 새 Data Fabric/NATS/WS Aggregator/Worker/언어 도입 금지.

**PERF 단계별 절차 / 계측 이벤트(모든 span에는 source_id, product_id, account_scope 여부, trace_id, sample_count, host/cgroup spec, stage, elapsed_ns)**

1. `PERF-01.1 Instrument`: 동일 프로세스 `monotonic_ns` span 또는 메시지 경계별 독립 span. `T0` Provider callback receive, `T1` normalize+available, `T_DQ` ingress queue admitted/dequeued, `T_FS` group-sync start/end + fsync, `T_DUR` producer durable receipt, `T_UDS` send/RECEIVED/APPLIED wait, `T2` MarketState apply & strategy available, `T3` ActionPlan, `T4` Owner/reservation durable commit, `T5` real existing Venue Adapter transport send invocation. T6 ACK/T7 Fill은 separate, T0 이전 WAN은 제외. **Cross-process monotonic timestamp 차감 금지**; process-local span + causal ID만 연결. `group_sync_ms`, batch size/bytes, queue depth/age, ACK in-flight, spool backlog, SQLite WAL fsync, cloud wait 각각 보고.
2. `PERF-01.2 Baseline`: 현재 구현을 수정하지 않고 A-only low-rate first event 100회, A+silent B, A+B interleaved, burst/mixed KR+crypto, B disconnect/reconnect, duplicate/gap, Cloud 60s stall, disk slow/stall injected workload 실행. Trace/hash/frozen SHA, N completed/omitted/failed, p50/p95/p99/p99.9, CI if feasible, CPU RSS cgroup throttling, persisted/strategy-delivered event count, A1 loss indicator 기록. Signal 없는 Event는 `(T0→strategy_evaluated)` 별도 보고해 조건부 latency selection bias 피함.
3. `PERF-01.3 Diagnose` (우선 검증해야 하는 4개 지점): (i) `SpoolingMarketPublisher.accept_payload` → bounded queue `put_nowait`가 타 Source를 기다리는지, (ii) `_writer`의 `group_sync_ms`는 **flush batch-bound** 대기이며 all-venue waiting이 아닌지 / first event 추가 대기 비용, (iii) `_deliver_live`의 현재 **per-record `RECEIVED→APPLIED` ACK 직렬화**가 2vCPU에서 sustained backlog/head-of-line을 야기하는지, (iv) `LocalMarketEngine.on_frame`의 one-writer/book update/replay와 `StorageWorker` Parquet/Cloud 작업이 Strategy callback/Owner permit path를 간접적으로 블록하는지. 정확한 call graph/lock scope/queue/max age와 측정된 stage path를 compact finding 표에 남긴다.
   - **반드시 실제 코드로 재현할 Hidden Readiness Gate**: `LocalMarketEngine.event_source(required_producers=None)`는 all-producer default, `local_runtime.py`의 ETF 실행부는 `required_producers=()`를 명시한다. `_initial_proofs_ready()`는 모든 등록 Feed의 initial proof를 요구한다. 해당 3개 경로 및 `runtime_checkpoint.restore`/`complete_checkpoint_recovery`/`_queue_admissible(producer)`를 확인해 (a) 정상 운영 중 B silence/gap이 A-only strategy를 막는지, (b) cold restart 시 B 복구 지연이 A-only strategy readiness를 불필요하게 막는지 **별도** 보고하라. 단일 계좌·checkpoint root consistency 때문에 필요한 복구 fence는 유지하되, 보호 증거가 있는 A-only 전략에서 per-strategy subscriptions/readiness를 사용하도록 선택적으로 배선한다. `_initial_proofs_ready`에 무조건 `all`을 `any`로 치환하는 것은 금지; 복구 seal 및 Owner readiness 증명 없이 unblock하면 FAIL.
4. `PERF-01.4 Minimal change decision`: `WAIT_ALL_VENUES` 같은 진짜 글로벌 장벽/Cloud ACK 동기 의존이 **입증**되면 `DATA-NB-01/04` 기능 위반이므로 기존 caller의 조건만 제거/분리하고 focused test. `group_sync_ms`·ACK 순서·A1 durable path는 **자체 안전 기능**이므로 목표 수치 초과만으로 제거하지 않는다. 병목이 통계적으로 의미 있으면 기존 설정의 bounded batch/flush limit 변경 또는 동일 cursor/ordered apply를 유지하는 최소 파이프라인 변경만 검토. ACK 파이프라이닝 도입 시 `client_id/producer_id/cursor` 단위 receipt 대응, 재전송/중복·순서·crash proof를 추가하고 독립 Codex risk signoff; 정당한 증거가 없으면 기존 로직 유지·측정값 기록.
5. `PERF-01.5 Re-run & guard`: 기능 변경 시 `QA-DATA-01~05`, spool/crash/fill/Owner tests, full Linux QA rerun + before/after CPU/latency/queue histograms 비교. 모든 변경에서 A1 durability, MarketState freshness, native book sequence, duplicate Order suppression, kill/fail closed를 유지. 단순 2/5/10ms 목표 미달은 PASS/FAIL 트리거 금지.

**재현 시나리오/수락표**

| ID | Input | Mandatory evidence / interpretation |
|---|---|---|
| `PERF-A-ONLY-B-SILENT` | A→100 events, B silent for 5m in virtual time | A accepted/applied/delivered counts, no global barrier; source B stale dependent strategies blocked; lock/queue spans |
| `PERF-GROUPSYNC-FIRST` | 1 event/s then 1000event burst | distinct group wait vs fsync vs strategy availability, tune recommendation; never label this all-venue wait without evidence |
| `PERF-ACK-SEQ` | 100 events + variable scripted UDS ACK delay | serialize overhead measured; no silent loss; proposed optimization only with exact cursor-order and failover proof |
| `PERF-CLOUD-STALLED` | Parquet/upload stalled 60s or scripted timeout | Local durable→market strategy path makes progress; bounded pressure measured; archive recover later |
| `PERF-ETF-CRYPTO-2VCPU` | KR ETF + two Crypto venues + 2 strategies + group action + storage concurrency | all spans/CPU/rss/queue backlog; no miscomputed cross-process p99; full source/candidate compare |
| `PERF-DURABILITY-FAULT` | fsync ENOSPC/restart/corrupt tail | pre-durable Strategy events 0; precise source/gap invalidation; latest ledger not rewound |

**PASS**: reproducible local trace SHA+config+host/cgroup limits, required spans and counts, source-independent proof, bounded queue limits, measured group-fsync/per-record ACK overhead, QoS structural finding decisions, before/after data if code changed, re-run QA proof. **p50≤2ms/p95≤5ms/p99≤10ms는 비차단 최적화 reference**; 초과만으로 QA/PERF/REL 실패 판정 금지. 단, 의도되지 않은 다른 Venue 대기/Cloud 동기 장벽이나 안전/무손실/Owner 일관성 위반은 P0 bug로 FAIL. Linux external network/server claims are CUT-01 only.

### REL-01 — Immutable Offline Release Candidate and Artifact Hygiene

- **선행**: QA-01, PERF-01, CLN-03, SIM-01. **담당**: deterministic scripts + Codex release review. **읽기**: `scripts/refactoring/build_release_manifest.py`, `collect_release_metadata.py`, `deploy/`, package builders, `pyproject.toml`, rollbacks. **쓰기**: manifest, release assembly scripts/tests.
- **구현**: freeze SHA, branch clean, source hash, image digests, lock dependencies, versioned `config/`, account/data mount matrices, per-role entrypoints, secrets scan, proven replay/protected originals, rollback binaries, exact ledger DB version, docs state. 미입증 capability는 `DEPLOY_BLOCKED`로 표시. Candidate에 old NATS rollback files 포함하되 신규 primary data path에는 NATS 필수 dependency 없음.
- **수락**: source/digest mismatch detects, changed file after freeze rejects, reproducible offline build smoke, complete read-only preflight list, sensitive config 0.

**REL-01 이미지 조립 작업 / 정확한 write paths**
1. root `Dockerfile`, `Dockerfile.crypto`, `docker-compose.yml`, `pyproject.toml`, `.dockerignore`; `deploy/{crypto-profile,dashboard-compose,ls-market-profile,release1-overlay}.yml`, `deploy/Dockerfile.dashboard-timefolio`, `deploy/Dockerfile.crypto-ledger`, `scripts/build_release_images.py`, `scripts/refactoring/{build_release_manifest,collect_release_metadata}.py` 및 `config/{runtime,crypto_runtime,ls_market_runtime}.yaml`를 명시적 direct owner scope로 관리. 실제 활성 파일 확인 없이 이전 이름을 무작정 교체 금지.
2. `Dockerfile.crypto`의 옛 `COPY src/vibetrading /app/src/vibetrading`, `pyproject`의 `vibetrading=src/vibetrading`, root `docker-compose`와 crypto overlay의 Python bind-mount, dashboard/timefolio의 `python -m vibetrading.dashboard.*`를 역할별 새 경로로 교체. python distribution wheel은 `src/`에서 직접 각 package를 설치하고 `PYTHONPATH=/app/src` 유지 여부를 불변성/보안/빌드 결과로 결정.
3. Docker profile 목록/현 사용 조합/역할→컨테이너→이미지→소스 SHA→CMD→env/secrets→volume mount→healthcheck→restart/stop grace를 JSON 리포트로 작성. 구 NATS compose는 저장·복구용 immutably 보존; 새 NATS-free primary는 nats-init /NATS_URL 의존 없음. Timefolio **주문 Runner**(대회 주문)와 **dashboard monitor**(읽기 전용)를 별도 역할로 검사하고 Chromium image/dependencies/browsers path 포함.
4. `scripts/build_release_images.py` manifest에 new files/source tree/import graph/config hashes를 해시; docker build `--pull=false` or pinned base in sandbox, metadata reproducibility; bad source mapping/old volume path injected→must fail preflight.
5. **PASS**: 100% activated roles `container command executable`, `docker compose ... config -q` used combinations, image smoke `python -c 'import core,feeds,market,storage,strategies,execution,portfolio,runtime,operations,timefolio'`, isolated CWD, NATS-free role health, Timefolio blocked submit; no actual server connection, no live order.


### DEP-01 — One-Install Integrated Preflight / Deployment Verification

- **선행**: REL-01. **담당**: Ops tooling + Codex critical. **쓰기**: `deploy/` 및 scripts/refactoring 선택 경로, runbook/fixtures; **실서버 명령 실행 금지**. **구현**: `plan/preflight/install-blocked/verify/activate/rollback` 모드 분리. 미승인 단계는 dry-run. 운영 14 role image/config/mount inventory와 filesystem mount identity, old/new account owner, orders/UNKNOWN/positions, WAL-safe backup, spool/Cloud proof, disk headroom, NATS lock, health/status/Telegram cold, Paper+data on host CPU, kill switch, fault drills를 **한 번의 배포 세션**에서 수집. 위험 P0 후 독립 read-only 검사만. `release_verification.json` 각 gate `PASS/FAIL/NOT_RUN/EXTERNAL_ONLY`, timestamp, source SHA, input lineage, evidence.
- **수락**: **샌드박스 호스트**에서 failure injection preflight before any mutation, forbidden activation 0, immutable release config, old owner fencing, one button rollback only after latest account truth; 실제 서버 deploy는 CUT-01 명시 승인 후.

**DEP-01 환경 통합 및 강제 오류주입**
- `PRE-IMAGE-MISMATCH`: image digest/source SHA/pyproject wheel RECORD와 runtime/source mount source hashes 다르면 초기 단계 BLOCK.
- `PRE-STALE-CMD`: `docker-compose.yml`/selected overlay/container health command에 `vibetrading.*`, `src/vibetrading`, 제거된 `fv_mm`/`liquidity_mm` 실행 모듈 참조 있으면 BLOCK. 과거 rollback-only Compose는 명시 exception.
- `PRE-ROOT-STRATEGIES-SHADOW`: Image/CWD import `strategies.__file__`가 새 `src/strategies` 아닌 옛 root를 가리키면 BLOCK.
- `PRE-TIMEFOLIO`: 브라우저 의존/계정 arm/secret 미확보시 Timefolio 주문 role `BLOCKED`이고 모니터 role은 정상 읽기 가능; 실주문 0.
- `PRE-EMPTY-MOUNT`: 새 spool/ledger/config volume identity가 잘못되면 OS 루트로 fallback 쓰기 0.
- `PRE-NATS-ONLY`: new primary profile에 nats-init depends-on 주입시 BLOCK, rollback file 계속 읽기 전용 남김.
- **작업 출력**: generated manifest, `docker compose config` outputs, per-container probe logs, `preflight` JSON and `rollback` instructions. 실제 서버 Docker Pull/Up/Down/Volume delete는 사용자가 승인한 CUT-01에서만 실행.


### CUT-01 — EXTERNAL: 사용자 승인 단일 서버 전환·운영 검증

- **선행**: DEP-01, user approval, documented rollback image and account backup, production preflight. **현재 상태**: EXTERNAL_NOT_AUTHORIZED. **구현 시**: 단일 설치·blocked/shadow, 일치하는 계좌/미체결 상태 대조, user-approved scope activation, feed/data/Spool/Cloud/strategy/order CPU/latency 측정. 서로 독립인 여러 계좌에 대해 exact owner scope. live credential access/real orders user opt-in.
- **수락**: 운영 계좌 소유권·주문 재전송/중복 없음, dynamic feed/price/funding correct, valid recovery, 24x7/장중 representative metrics, rollback-tested; 장시간 검증을 즉시 통과 처리 금지.

### RET-01 — EXTERNAL: VN20 NATS/Legacy Retirement After Cutover

- **선행**: CUT-01 OPERATIONAL_VERIFIED, historical recovery/retention/rollback authority ended, separate user approval. **현재 상태**: EXTERNAL_NOT_AUTHORIZED. **구현**: active Docker/Compose/NATS/legacy Python/wrappers 제거는 실제 production caller 0, rollback 0, unique data 0, retained prefix/remote readback proof를 갖춘 *경로별* 작업; remote backlog/DB/storage는 별도 retention policy. 단순 Git 파일 제거와 운영 NATS/원본 데이터 삭제를 혼동하지 않음.
- **수락**: no active NATS production dependency, complete live recovery, no irrecoverable source deletion, exact backups and final release regression.

## D. 과거 미완료/미착수 13개의 유실 방지 매핑

> **현재 운용 대상은 C3/KP/KD이며 과거 fv_mm/liquidity_mm은 실행 코드를 재사용할 수 있지만 경제 전략 자동 등록은 금지.** 신규 crypto/주식 전략은 STR-02→EXEC-01→CAP-06·CAP-05의 왕복 Action/OrderEvent E2E에 성공해야 한다. 단일 신규 Venue의 추가·차단·삭제는 VEN-01로 강제하고 Verbose 정리는 SIM-01의 Ponytail 실제 검토·별도 Diff로 수락한다.

| 과거 ID | 2026-10-25 상태 | 새 소유 티켓 | 의도적으로 포기하지 않을 계약 |
|---|---|---|---|
| TX03A | INCOMPLETE | CAP-03 | Selected Perp product metadata/mode validity |
| TX03B | INCOMPLETE | CAP-03 | Fee/Funding/Margin/Cashflow |
| TX03 | INCOMPLETE | CAP-03 | 위 제품 범위 aggregate, unsupported reject |
| TX06A | INCOMPLETE | CAP-01 | Binance Spot vs USD-M Perp |
| TX06B | INCOMPLETE | CAP-01 | Arcus WebSocket/signing/order status |
| TX06C | INCOMPLETE | CAP-01, CAP-02 | GRVT existing private/fills/reconcile |
| TX06D | INCOMPLETE | CAP-01, CAP-02 | Bybit existing testnet & selected live |
| TX06E | INCOMPLETE | CAP-01 | Hyperliquid SDK nonce and order |
| TX06 | INCOMPLETE | CAP-00, CAP-01 | 선택 지원 범위의 end-to-end code complete |
| TX07B | NOT_STARTED | CAP-02 | REST/private gap & account reconciliation |
| TX07 | NOT_STARTED | CAP-02 | canonical lifecycle integration |
| TX08B | INCOMPLETE | CAP-05 REQUIRED | Group에서 두 Venue BUY/SELL의 실제 실행·동시/순차 제출·부분 체결·미확정·잔여 Hedge·복구 로컬 수락; 실제 페어 활성화만 opt-in |
| VN20 | NOT_STARTED | RET-01 | post-cutover only, original data retention |

**보조 미완료**: R06 → CLN-01/02/03, O01→OPS-01, R07 역할별 내부 이동→MOV-00..07 (신규). R05에서 C3/KP/KD helper 분리 완료를 인정하되 독립 파일 Plugin 미완성→STR-01. 기존 38개 VN/TX 및 R00–R05/O00 완료의 수락 근거는 `04`가 전적으로 소유한다.

## E. 공통 테스트와 증거 규칙

- Core: `python -m compileall -q src` 및 `python -m pytest -q -p no:cacheprovider` (환경별 플러그인은 실제 기존 pytest.ini 따른다), exact targeted tests first.
- CLI packaging: editable/wheel import outside repository, `python -m runtime.cli --help` (실제 callable CLI로 smoke), `python run_strategy.py --help`, selected crypto app CLI help, Docker build & `docker compose config` **실제 실행 가능한 환경에서만 PASS**.
- Functional: C3/KP/KD Golden, R04 strategy module/alias identity, old ETF account Mock/Mock2, KIS TOSS DATA_ONLY, Native L2, VN checkpoint/recovery, TX Paper/owner/portfolio, venue fake ACK/cancel/fill/funding, cloud archive/verify/download.
- Added REQUIRED: `DATA-NB-01~04` Source-independent Data Receipt path; `VEN-PROT-01~04` separate Dummy Crypto/Domestic Broker registration and actual Kiwoom scripted submit/cancel/query/fill/reconcile; canonical Existing Owner; NH Live remains unsupported. Test source SHA, return counts, expected state, fixture hash and actual Adapter class name in task receipt. **Move-only MOV-02에서 비즈니스 수정 금지; 발견한 병목은 QA/PERF 소유**.
- Critical: invalid/UNKNOWN→no new submit, late/duplicate fills one ledger credit, crash before/after commit, account double owner, replay→real mutation 0, missing storage volume fails closed, secret leakage 0.
- Return on failure: base/current SHA, first failure traceback limited, impacted paths, diff stat, altered stage state (unapproved 0), rollback point. Never re-run a failing whole suite blindly without a changed variable.
- Product receipts are meaningful only if based on exact candidate SHA. Historical 2,480 PASS is *baseline evidence*, not permission to mark changed code verified.


## F. 최종 명세 충돌 제거 우선순위 (FLAT-SRC 2026-10-25)

1. 이 문서 B1/B2의 HEAD old path는 실제 원본, **goal new path는 `src/` 도메인 루트**. 이전 티켓에 남은 `src/vibetrading/execution/*` 등의 target 표현은 **구형 MOVE_ONLY 중간 경로**이며 최종 권위가 아니다. MOV-00 path-map과 본 F절이 우선한다.
2. `python -m vibetrading.cli`, `vibetrading.strategies.load_strategy` 등 과거 테스트 명령은 새 `python -m runtime.cli`, `strategies.load_strategy`(실제 exported 함수 존재 검증)로 교체하며 테스트 문자열 변경을 source equivalence 위반으로 판정하지 않는다. 단, 사용자 요청한 직접 운영 기능/경제 Golden 동등성은 유지한다.
3. `runtime/apps/{fv_mm,liquidity_mm,fv_pilot,mm_state,timefolio}`를 최종 보존 대상으로 해석하지 말 것. **Timefolio만 `src/timefolio` 정식 보존**, 4 연구 Bundle은 재사용 함수 이관+무참조 증거 확인 시 최종 이미지/source에서 retire.
4. 기존 `bybit_execution.py`, `kis_execution.py` 파일명의 영구 보존보다 **검증된 native API/Mock & Audit 동작 보존 + 단일 mutation authority**가 우선이다. 파일 이름을 지운다고 API 지원을 제거하거나 API 함수를 중복 재구현하지 않는다.
5. ROOT `Dockerfile*`, ROOT `docker-compose.yml`는 deploy 밖에 있으며 **최종 릴리스 필수 입력**이다. deploy 폴더만 고치는 티켓은 불완전. 활성 Compose profile/source/entry/secret/mount/health/Timefolio Browser 역할 전체 업데이트 PASS가 필요하다.
6. 기존 경제 전략/Perp/ETF/Timefolio 1회 주문·재시작/Unknown/Limit/disabled source 보호는 여전히 Gate. 제거한 기존 연구 코드 자체를 무조건 regression PASS 대상으로 강제하지 않고 새 canonical로 이관한 기능/Golden을 테스트한다.


## G. 신규 사용자 요구 — 고립된 소규모 개발 티켓 (4개, 2026-10-25 19:55 KST)

**이 절의 티켓은 기존 31개를 재구현하지 않는다.** 구현자가 현재 SHA에서 대상 코드의 직접 호출자/테스트만 읽고 실패한 계약에 대한 최소 변경을 한다. 각 티켓은 A1 입력패킷/A2 반환패킷, `edit_paths` allowlist, 독립 검증자 수락 필수. 이 절의 모든 P0/P1 테스트 ID는 입증 가능한 수치/수량/상태를 assertion으로 생성해야 한다. 신규 런타임·DB·스크래퍼·Cache Service 금지. 보호 파일/비밀/Live 계좌/서버 원격 명령 접근 금지. `ARCH-FEED-VENUE/ARCH-OBS-DASH/ARCH-WEB-ACCESS/ARCH-EXEC-DATA-SPLIT/ARCH-KR-BREADTH`가 우선 권위다.

### FEED-01 — REQUIRED Venue-Specific Collectors, Shared Normalization, TOSS Data Retained

- **Wave/Prerequisites:** W3; `MOV-07`, `CAP-00`. **Role:** Implement/test; human/Codex scoped review. **Old sources:** `src/vibetrading/providers/{crypto_perp,extended_perp,binance,hyperliquid,kis,kiwoom,toss,base}.py`, `src/vibetrading/local_pipeline.py`, `src/vibetrading/local_engine.py` (move 완료 후 `src/feeds/...` etc), config crypto + ETF + source registry, provider tests. **Allowed writes:** after MOV-07 `src/feeds/providers/**`, `src/feeds` existing provider registration, scoped provider tests, `config/{crypto_runtime,runtime,universe}.yaml` if needed. No `execution/`, `portfolio/`, `dashboard/` edits. `src/vibetrading` is OLD PATH ONLY.
- **FEED-01.0 baseline:** Before edit, `ripgrep CryptoPerpCollector|ExtendedPerpCollector|BinanceCollector|Toss` within repository, enumerate actual Venue/market/channel matrix, `run`, websocket URLs/subscription, response parsing, `_book/_trade/_ticker`, `_gap`, shared `_instrument`, caller, data Golden tests. In particular `CryptoPerpCollector` supports bybit/bitget and selected others; `ExtendedPerpCollector` implements Arcus/GRVT; `BinanceCollector` implements Binance-specific TradFi subset. Do not assume the names imply fully interchangeable products. Freeze before/after fixture JSON digest & class ownership table.
- **FEED-01.1 extract files:** `src/feeds/providers/{bybit,bitget,arcus,grvt,binance,hyperliquid,kis,kiwoom,toss}.py` as actual supported venue files; if venue unsupported in selected config, retain disabled source but do not invent implementation. E.g. bybit `run/subscribe/decode/orderbook-bridge`, GRVT `subscribe stream`, `first_sequence_number` boundary; Arcus L2 native sequence and GRVT distinct. Move stable shared `_instrument`, decimal level parser and emit into existing `base.py`/one shared module without framework. The final active `crypto_perp.py`/`extended_perp.py` multiplexer must have **zero live callers** and be removed after exact regression; if shared nontrivial helper remains, move only helper, not monolithic venue switch; avoid duplicating code. Do not remove TOSS.
- **FEED-01.2 independent feed plumbing:** All provider implementations expose their actual existing `run(bus,..)`/factory interface until callsites migrated; config selects `(venue,segment,channel,product)`; Provider emits existing `BaseMarketEvent` with correct native metadata. Use one shared bounded spool and state authority, no private second bus. TOSS `DATA_ONLY` with valid collection, bar-end convention; `KIS/Kiwoom` matching bar convention tests. The venue registration method must not call execution adapter constructors or require account credentials for public data.
- **FEED-01.3 functionality/latency tests:** fixture native messages for Binance, Bybit, Bitget (only if currently configured), GRVT, Arcus, Hyperliquid, KIS, Kiwoom, TOSS. Assert native sequence/generation, bridge/gap, timestamp/available-at, Decimal, book digest/closed-bar semantics. A 100 events while B disconnected applies A=100; no global barrier or broadcast await; secret-free. Head of line test `slow arcus` while `binance normal`: any global blocking must be traced to actual common durability/ack safety, not invented synchronization. Source-by-source old/new canonical event hashes equal after allowed import-path only conversion.
- **FEED-01.4 retire:** Check no import, CLI, Compose, config, tests, runtime refs to retired `crypto_perp.py`/`extended_perp.py`. Keep only active helpers that have owner. Set status PASS only if enabled providers still start in fake/test fixtures and local backfill, no unsupported venue auto-enabled.
- **Receipt:** `provider_ownership_matrix.json`, `fixture_equivalence.json`, changed paths/functions, 100-event A/B independent case, TOSS data-only positive test, no external mutations, `pytest` scoped/full impact tests, one failing case sample. **FAIL:** duplicated native Venue parse ownership, removed TOSS, fake public API URL, per-venue hard-coded execution requirement, lost BarStart/End semantics, global multi-venue wait.

### OBS-01 — REQUIRED Nonblocking Runtime Latency + Strategy-specific P&L Tabs

- **Wave/Prerequisites:** W3; `MOV-07`, `STR-02`, `CAP-03`, `FEED-01`. **Role:** Implement/test. **Existing assets:** `dashboard/app/{page.tsx,api/pnl/route.ts,api/state/route.ts,api/orders/route.ts}`, `dashboard/db/{index,schema}.ts`, `src/operations/dashboard/{bridge,projection,telemetry}.py` post-move, `src/{execution,portfolio,runtime,market,feeds}`, `trade_reporting.py`. **Allowed edits:** existing dashboard read-side/API/frontend, `src/operations/dashboard/**`, **small read-only projection hooks** in existing `src/execution/`, `src/portfolio/`, `src/runtime/`, `src/feeds` only with named functions; `tests/test_dashboard*`, `tests/test_latency*`, related fixture/scripts. No alternative frontend rewrite/Prometheus/Grafana/Timescale/Redis.
- **OBS-01.0 baseline:** inventory actual dashboard hosting, route methods, current accounts ingest auth, PnL schema and source of truth; current `telemetry.heartbeat` publishes strategy row with `realized_pnl=None`, `unrealized_pnl=None`. Search exact telemetry and ledger event method owners. Capture current `dashboard` schema/API response before modifications; user-facing tab added to existing page. Require (a) data model one account/multiple strategies, (b) PAPER vs LIVE splits, (c) fees/funding data owner; **do not construct PnL from market price × number of orders**.
- **OBS-01.1 latency writer:** in current stage span points, sampled monotonic per-process histograms. Use bounded counters, errors, stale, drops and process identifiers; source_id/strategy_id/account scope/mode and event/decision trace reference. Export 1/5/15min windows `p50,p95,p99,n,source_latest_age_ms,queue_depth,oldest_queue_age,fsync_group_wait_ms,UDS_ACK_ms,owner_commit_ms,adapter_send_started_ms`. In-app 2s refresh default bounded 1–5s; avoid hot-loop JSON serialization or dashboard client writes. If `T0→T5` crosses clocks display local segmented spans and missing end-to-end mark; never subtract cross-process monotonic clocks. Never call order owner from dashboard code.
- **OBS-01.2 PnL projection:** stable `(strategy_id,account_scope,mode,currency)` key. Source: existing ledger `executed fill`, fee, realized position cashflows, funding, mark time; deterministic cash-flow adjustments (deposits/withdrawals not profit). Produce `realized_pnl`, `unrealized_pnl`, `fees`, `funding`, `net_pnl` **with explicit schema contract whether fee already included**, `equity_curve[(time,net,currency,asof,status)]`, `position_count`, `working_order_count`, `trades`, `stale` and `attribution_status`. Shared-account unmatched venue fill → `UNALLOCATED` account bucket, strategy show `INCOMPLETE`, NEVER fabricate strategy net figure. FX absent means mark N/A, not 0. Max samples configurable with server-side downsample; e.g. last 24h ≤1440 1m aggregate points per strategy, 1-5s live snapshot memory bounded. Use existing persist/checkpoint to survive restart; never write into execution ledger as GUI cache.
- **OBS-01.3 UI:** top-level system health/latency view, separate tab for every active strategy; each tab shows `net/realized/unrealized/fee/funding`, rolling equity/PnL chart, orders/positions, strategy running/blocked/stale, time zone/asof and refresh status. Tab ordering deterministic, hidden/inactive strategies visibly stopped, user-config limit on concurrent rendered graphs. Existing Timefolio competition orders/report remains but Timefolio browser secrets never projected.
- **OBS-01.4 tests:** scripted synthetic prices/fills/funding plus manual independent expected calculations; 2 strategies share account and one unallocated fee; partial fill+late fill+cancel ACK; KIS Mock/ETF vs Bybit crypto; Paper vs Live, restart replay idempotent; missing mark and missing FX -> N/A; dashboard polling failure/reconnect, 1min 3 viewers, slow UI 60s; strategy order progression unaffected; error counters increment. Test UI via frontend unit/browser smoke and Python projection tests; `GET` only viewer auth; no Post order control. Measure CPU/RSS before/after with same input. No real account mutation.
- **Receipt:** `latency_schema.json`, `pnl_golden.csv/json`, end-to-end frontend screenshot optional but build command required, `dashboard build` status, source data/algorithm provenance, replay/fault test IDs and pass counts. **FAIL:** fee double-count, fake PnL, synchronous hot-path telemetry wait, unbounded sampling, Timefolio credential leak, trading blocked due GUI disconnect.

### WEB-01 — REQUIRED Secure Public URL for Read-only Dashboard

- **Wave/Prerequisites:** W4; `OBS-01`, `MOV-06`. **Role:** Implement/security review. **Actual targets:** `dashboard/app/api/*`, existing dashboard deployment, `deploy/dashboard-compose.yml`, root `docker-compose.yml`, optional one reverse-proxy `deploy/Caddyfile` (create only if current ingress absent), `deploy/compose.env.example` (redacted templates only), DNS/TLS runbook, smoke scripts. **No web-admin live broker calls.**
- **WEB-01.0 threat baseline:** determine current dashboard placement/hosting and existing auth (`dashboard/app/chatgpt-auth.ts`, route handlers, reverse proxy if any). Inventory routes and methods, especially `accounts`, `ingest`, `orders`, `state`, `pnl`: `public read routes` vs `private service mutation/admin`. Treat URL, TLS credentials and external request logs as secrets. Check public IPv4 and cloud ACG externally only at CUT-01; no run without authorization.
- **WEB-01.1 minimal ingress:** same user-facing dashboard retained; choose existing trusted ingress if present, otherwise pinned Caddy/Nginx image with config: DNS name -> TLS:443 -> authenticated read-only web app private network port, no published app backend. Public IP only HTTP with bearer credentials is not allowed. Prefer no additional heavy dashboard process; existing Next/Cloudflare runtime compatibility measured. TLS termination and renewal need durable cert volume, small RAM/cgroup bound, health/readiness log. `:80` only challenge/redirect; only `443` application port exposed. Add explicit `read_only`/`no-new-privileges`/scoped secrets and safe proxy headers, rate-limiting/session expiry; credentials hashed in config/secret file only.
- **WEB-01.2 API isolation:** for all public-facing `/api/*`, explicit auth/authorization. Public `GET state,pnl,orders` return redacted and scoped data only; `POST /api/ingest`, account key registration, credential/secret admin, orders mutation never public. If ingest must run: private host/interface and service auth; no proxy route to it. Timefolio browser/credentials 100% private. XSS/CSRF, session cookies `Secure/HttpOnly/SameSite`, no raw JWT/query tokens; no CORS `*` for authenticated data. Viewer is **read-only** in application API; do not rely on frontend hiding buttons.
- **WEB-01.3 test matrix:** HTTP80→HTTPS443 redirect (or challenge); valid remote URL from simulated external client, invalid/no token 401/403, OPTIONS/POST blocked, stale session 401, 3 concurrent clients 2s SSE/snapshot, websocket upgrade if used, timeout/reconnect, proxy restart, disconnected dashboard process, TLS renewal simulation, dashboard container cannot reach credential mounts/Execution mutation endpoints, host port scan shows no NATS/UDS/DB/collector ports. Pure local integration simulates DNS/TLS with self-signed test CA; actual trusted cert/DNS/ACG external verification stays `EXTERNAL_ONLY`.
- **WEB-01.4 Docker/Release:** root and deploy Compose overlays config parse; `REL-01` must include proxy image digest, secret and certificate mount identity and no old import paths; `DEP-01` must block missing DNS/auth/TLS and public open backend. Public dashboard can be disabled while trading continues; stop frontend not trading. `CUT-01` alone enables external firewall/ports on real server after permission.
- **Receipt:** route matrix HTTP-method auth, example read-only proxy config with placeholders only (no sensitive values), `docker compose config` scoped, 401/403/mutation0 evidence, attempted security break tests, SSL status `LOCAL_TEST_ONLY` vs `PRODUCTION_VERIFIED`. **FAIL:** public unauthenticated exposure, order mutation via dashboard, plaintext secret, public `accounts/ingest` admin, built-in auth bypass, frontend as hard trade dependency.

### KR-DATA-01 — CONDITIONAL Stock Minute/Daily Breadth to KOSPI200+KOSDAQ150 (max 350)

- **Wave/Prerequisites:** W5; `FEED-01`, `MOV-07`, `CAP-00`. **Required?** `false` for base platform release; when user selects actual `KR350` deployment, must pass local code/fixture and separate external provider/soak acceptance before claiming production. **Existing assets:** `src/feeds/providers/{kis,kiwoom,toss}.py`, `src/feeds/{universe_config,collection_timing,local_pipeline}.py`, storage Parquet/cache and `config/{universe,runtime}.yaml`, historical collector script/tests. **No new persistent daemon if current collector supports a separate low-priority schedule.** Writes: exact existing providers, config, focused unit/benchmark tests, optional tiny scheduling helper ONLY if current runtime lacks one.
- **KR-DATA-01.0 provider feasibility:** review official current selected provider live API entitlement, endpoint type, available bar conventions, duration/row windows, rate and 350 symbol subscription viability. Persist evidence URL/date and max safely achievable rate, separate `REST/BATCH/WS` based on supported APIs. NO invention of Kiwoom/TOSS/KIS limits. KIS documented 2026-04-20 18 req/s live, 1 req/s mock, 41 WS registrations/session; reverify date. If cannot feasibly deliver 350×1m in target interval mark `EXTERNAL_PROVIDER_LIMIT_BLOCKED` and leave feature disabled.
- **KR-DATA-01.1 universe:** daily current KOSPI200/KOSDAQ150 membership sources; 200+150 stock identifiers snapshot version/fetch timestamp; verify unique count 1..350; no current-constituent retrospective backtest assumption. Symbol/venue exchange market cap: precise stock listing and session schedule; invalid/delist flags; idempotent daily manifest. User requested only 1m..1D; no L2/Trades scale-up.
- **KR-DATA-01.2 scheduling:** existing market bars per-source independent ingest to A1 spool, low-priority bucket, separate resource limits, max inflight API requests, max age threshold; hard reservation for KIS execution/account API rate, reconnect jitter, bounded retry and token budget. Actual order execution cannot wait for 350-symbol round-robin; global flow blockage P0. End-time label and availability as-of. Missing bars flagged rather than forward filled. 5m/30m optionally derived from 1m at archive/analytics layer, not in hot path; configured no duplicate pulls.
- **KR-DATA-01.3 capacity fixture:** synthetic `350×390=136500` bars/day and 1D 350/day, morning bursts & staggered requests, 10/50/150/350 staged profiles; measure CPU p50/p95 max, RAM RSS/cgroup peak, spool queue oldest, rate-limit utilization, local 1d spool bytes, Parquet bytes/bar, cloud retention, public dashboard/2 strategies latency while collector runs. Require all live-capable hot path data counts, Account Owner no starvation. If headroom below declared limit or API quota lacks budget, stop at last verified cohort and feature `BLOCKED_SELECTION`, not platform-wide success claim.
- **KR-DATA-01.4 test fault:** provider 429/timeout/blocked timestamp, rate limiter 0 tokens, websocket 41 cap, partial daily membership update, conflicting duplicates, two markets holiday differences, download disrupted mid-day, restart checkpoint idempotency, disk near-full, downtime/backfill, 350-constituent changes. Existing KR ETF signals/Timefolio unaffected. Do not enable in production without host soak and market data license evidence.
- **Receipt:** `kr350_feasibility.json`, source/API documents, selected provider, `bars_per_day`, `bytes/bar` empirical, per-stage peak RSS/CPU, `call_rate_budget`, real eligibility pending if none, manifest hashes and explicit `activation=OFF` by default. Offline implementation can be `ACCEPTED_LOCAL` while external activation remains pending.

## H. Mandatory Existing-ticket Addenda — New 4 Tickets Cannot Be Skipped

- **MOV-00/02**: freeze exact old `CryptoPerpCollector` and `ExtendedPerpCollector` venue/segment/channel matrix and TOSS file location. `MOV-02` remains purely mechanical; FEED-01 alone changes Venue-specific native parsing/ownership. No import rewrite mixed with semantic changes.
- **CAP-00**: list data/execute capability independently for each venue: `DATA_ONLY`, `ORDER_ONLY`, `BOTH`, `NONE`; TOSS DATA_ONLY, DummyNH ORDER_ONLY; KIS/Kiwoom BOTH when selected route proof exists. Explicitly identify current external Namu support as unverified.
- **VEN-01/EXEC-01**: support broker `feed_enabled=false`, `execution_enabled=true`, no creation of fake data producer; dummy NH only credential-free scripted route. `KIS public feed → DummyNH submit` and `Kiwoom public feed → DummyNH submit` with distinct exact source/target instrument IDs, risk gate and order feedback; Strategy can use third-party source. TOSS data remains, TOSS order mutation impossible. No forced feed module for every execution venue.
- **STR-02/PORTFOLIO/CAP-03**: expose canonical strategy-owned order event and account position facts, idempotent strategy-id attribution, fee/funding semantics for `OBS-01`; unallocated live shared-account exposures visible but not invented attribution.
- **QA-01**: must execute `FEED-01` fully, `OBS-01` PnL/latency, `WEB-01` public read-only security local fixtures; conditional KR350 tests if selected. UI failure/order placement isolation, 100 event A/B split, correct native parser across 5 crypto + three KR; no real mutations.
- **PERF-01**: include 2 viewers/2s refresh, 5-venue market + MM/Arb + Timefolio idle browser, telemetry ingest and sampled histograms; dashboard disconnect 60s; 2vCPU/3.8GiB CPU, RSS/cgroup OOM, disk, FD/WS, Queue/max-age and order path p99 distribution. Same SHA/config baseline. Distinguish `measured` from `estimated`; overload blocks new feature activation, not nonblocking 2/5/10ms reference overrun.
- **REL-01/DEP-01**: add dashboard reverse proxy/TLS/auth/ingest exclusion, UI asset+source SHA and image pin, `dashboards public ingress` role or existing safe proxy. Docker/Compose root+deploy mount/host port/ACG runbook. No default production exposure, gate activation CUT-01. Timefolio secrets inaccessible from public viewer. `KR350` collector disabled by default.
- **CUT-01**: external HTTPS/DNS/renewal/ACG and host long soak (24/72h only if user authorizes); web authentication read-only and external provider rate budgets need explicit live evidence. `WEB-01` locally accepted doesn't imply real remote URL reachable.
