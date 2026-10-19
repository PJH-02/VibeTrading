# 로컬 파일 삭제 후보 목록 — 2026-10-19

기준 폴더: `C:/Users/박제형/Desktop/VibeTrading`. **아래 목록 작성 이후 사용자 지시로 A 항목과 재생성 bytecode를 삭제했다. B 항목·재설치가 필요한 환경은 유지했고, 커밋·서버 변경은 하지 않았다.** 아래 경로는 이 폴더를 기준으로 한다. 이름이 비슷한 서버 경로에 적용하지 않는다.

과거 실험은 [EXPERIMENT_HISTORY.md](EXPERIMENT_HISTORY.md) 한 파일로 날짜별 요약했다. 원래의 모든 오류 로그·테스트 출력·raw 데이터·옛 리포트를 다시 보관하지 않는다. ETF 누락 분봉 수집/수리 자료와 후보는 폐기 대상이다.

현재 재개 위치는 P09 역할별 배포 후보/자원·용량 검증이다. P02 schema cache만 서버 두 저장 서비스에 활성 적용됐고, 나머지 NATS-free v6는 비활성 후보다.

## 1. 삭제량과 적용 순서

A는 제품·정규 회귀 테스트 소스를 지우지 않는 산출물 목록이다. B의 옛 도구는 아래 명시한 수집 목록/import 참조를 정리한 뒤 같은 커밋에서 삭제한다. A에서도 문서 링크와 과거 결과 경로는 새 단일 요약 또는 현재 gate로 교체한다.

| 구분 | 후보 파일 수 | 파일 크기 합 |
|---|---:|---:|
| A1 종료된 연구·백테스트·pilot 데이터와 결과 | 28,816 | 6.671 GiB |
| A2 과거 원격 SQLite 비용 시험·재생 사본 | 36 | 5.039 GiB |
| A3 기존 배포 패치·소스 백업·문서 캐시 | 152 | 23.72 MiB |
| A4 중복 소스·pytest 임시 데이터·다운로드·종료 hotfix | 28,780 | 626.00 MiB |
| A5 과거 evidence·리포트·오류·체크·raw 결과 | 696 | 661.99 MiB |
| A6 과거 리팩토링 실행 결과·중간 메타데이터 | 1,058 | 30.05 MiB |
| A7 완료된 리뷰·옛 티켓·중복 현황 문서 | 26 | 0.61 MiB |
| A8 날짜별 과거 MD·집계표·옛 AI 지침 | 48 | 0.94 MiB |
| A9 생성된 빌드·그림·일반 캐시 | 96 | 4.64 MiB |
| B 참조 정리 후 삭제할 과거 작업 전용 도구 | 16 | 0.15 MiB |

중복 없는 합계: **59,724개, 13.026 GiB**. 일반 파일의 논리 크기 합이며 실제 회수 공간과 같다고 보장하지 않는다. 아래 선택 캐시는 합계에 넣지 않았다.

## 2. 삭제에서 반드시 제외할 것

- 현재 제품/재사용 도구: `vibetrading/`, `services/`, `shared/`, `strategy/`, `strategies/`, `backtest/`, `main.py`, `run_strategy.py`, `tests/`, `config/`, `deploy/`, Docker/requirements/패키지 설정. B에 적은 과거 도구 외의 `scripts/`도 유지한다.
- `tests/`는 임시 결과가 아니라 회귀 시험이다. 특히 schema·wire의 두 golden oracle와 `scripts/refactoring/encoding_probe.py`를 삭제하면 테스트가 깨진다.
- 핵심 설명: `README.md`, `refactoring_plan.md`, `refactoring_context.md`, `refactoring_ai.md`, 새 `docs/EXPERIMENT_HISTORY.md`와 이 목록.
- 현재 스펙: `docs/trading_spec.md`, `observability_interfaces.md`, `DATA_FEED_API_GUIDE.md`, `WEB_DASHBOARD.md`, `CRYPTO_MARKET_IMPLEMENTATION.md`, `LS_MARKET_DATA_IMPLEMENTATION_2026-09-27.md`, `ETF_MOCK_COLLECTION_SCOPE_2026-10-13.md`, `DUAL_MOCK_AND_PERMISSION_PNL_2026-10-14.md`, `TRADE_TEST_EXCLUSION_2026-10-14.md`, `TICKER_CSV_COLLECTION_2026-10-13.md`. 날짜가 있어도 현재 provider/계좌·보고/CSV 계약을 설명하는 이 파일들은 옛 오류 리포트와 구분한다. `docs/mm_state/`와 FV/MM 실행 가이드도 유지한다.
- 현재 로컬 입력·계좌 상태: `runtime_data/etf_reference_csv/`, `runtime_data/state/`, `runtime_data/liquidity_mm/`의 DB/위험 원장/lock/status. 이번 목록의 과거 report/setup/log 파일만 예외로 삭제한다. 이 경로들은 옛 오류 로그가 아니라 실제 코드가 읽는 데이터/상태다.
- `.git/`, `.env`, 인증 키·토큰은 삭제하지 않는다. `.env`·토큰·실행 DB는 웹 GPT용 커밋에도 포함하지 않는다.

### 리팩토링에서 남길 최소 기준 자료

중간 실행 사본 수십 개를 남기는 대신 다음 두 기준 소스만 보존한다.

1. `tmp/refactoring/20261018-stage-assignment/baseline/`: `build_server_candidate.py`가 고정 기준으로 직접 읽는 수정 전 소스 한 벌. 같은 내용의 `baseline_source.tar.gz`와 다른 중간 사본은 삭제한다.
2. `tmp/refactoring/server-source-20261019/active-owner-after-p02/`: 실제 서버 모듈 소유자를 합치고 P02 적용 파일을 반영한 소스 한 벌. 로컬 코드와 서버 preimage의 혼동을 막는 P09 입력이다.

위 두 폴더는 숨은 실험 결과가 아니라 다음 단계의 실제 비교 입력이다. 현행 제품 코드와 같은 v1~v6 시험 사본·배포 후보 사본은 추가 보존하지 않는다.

`docs/refactoring/`에서 유지할 파일:
```text
docs/refactoring/adapter_capabilities.json
docs/refactoring/baseline_manifest.json
docs/refactoring/contracts.md
docs/refactoring/cutover.md
docs/refactoring/decisions.md
docs/refactoring/execution_board.md
docs/refactoring/fixture_manifest.json
docs/refactoring/implementation_decisions.md
docs/refactoring/parameters.json
docs/refactoring/rollback.md
```

실행 폴더에서 유지할 파일 — 아래 목록 외에는 A6 삭제 대상:
```text
docs/refactoring/runs/20261018-stage-assignment/encoding_oracle.json
docs/refactoring/runs/20261018-stage-assignment/gates.json
docs/refactoring/runs/20261018-stage-assignment/handoff.md
docs/refactoring/runs/20261018-stage-assignment/integrated-linux-v5/linux_result.json
docs/refactoring/runs/20261018-stage-assignment/integrated-windows-v5/result.json
docs/refactoring/runs/20261018-stage-assignment/local-facts-wal-linux-v6/linux_result.json
docs/refactoring/runs/20261018-stage-assignment/local-facts-wal-windows-v6/result.json
docs/refactoring/runs/20261018-stage-assignment/offline_guard.py
docs/refactoring/runs/20261018-stage-assignment/p02-schema-v1/result.json
docs/refactoring/runs/20261018-stage-assignment/p02-server-health.jsonl
docs/refactoring/runs/20261018-stage-assignment/p02-server-package-manifest.json
docs/refactoring/runs/20261018-stage-assignment/p02-server-package.tar.gz
docs/refactoring/runs/20261018-stage-assignment/p02-server-preflight.json
docs/refactoring/runs/20261018-stage-assignment/p02-server-resume-receipt.json
docs/refactoring/runs/20261018-stage-assignment/p02-server-stage-receipt.json
docs/refactoring/runs/20261018-stage-assignment/prepare_candidate.py
docs/refactoring/runs/20261018-stage-assignment/run_linux_tests.py
docs/refactoring/runs/20261018-stage-assignment/run_offline_tests.py
docs/refactoring/runs/20261018-stage-assignment/schema_oracle.json
docs/refactoring/runs/20261018-stage-assignment/server-active-owner-after-p02.json
docs/refactoring/runs/20261018-stage-assignment/server-active-owner-candidate-v6.json
docs/refactoring/runs/20261018-stage-assignment/server-progress-v6-receipt.json
docs/refactoring/runs/20261018-stage-assignment/tasks.json
```

`docs/evidence/`에서 유지할 파일 — 이 네 개 외에는 A5 삭제 대상:
```text
docs/evidence/refactoring_20261018/server_readonly_snapshot.json
docs/evidence/refactoring_20261019/crypto_domestic_policy_equivalence.json
docs/evidence/refactoring_20261019/server_source_inventory.json
docs/evidence/refactoring_20261019/server_source_variants.json
```

5개의 최신 test JSON은 현재 gate 도구가 읽는 성공 결과·tested source hash다. 모든 과거 성공/실패 로그를 보관하는 것이 아니다. `schema_oracle.json`·`encoding_oracle.json`은 결과 리포트가 아니라 앞으로도 실행하는 테스트 입력이다. P02 package/manifest/receipt는 현재 활성 릴리스의 rollback 스크립트가 사용하는 자료다.

## 3. A 삭제 목록

### A1. 종료된 연구·백테스트·pilot 데이터와 결과

| 삭제 경로/그룹 | 파일 수 | 크기 |
|---|---:|---:|
| `runtime_data/bybit_capture_20260926.jsonl` | 1 | 0.46 MiB |
| `runtime_data/bybit_latency_probe_code.zip` | 1 | 0.13 MiB |
| `runtime_data/capacity_replay_20260926` | 5 | 0.21 MiB |
| `runtime_data/fv_mm` | 51 | 184.37 MiB |
| `runtime_data/fv_pilot` | 43 | 1.698 GiB |
| `runtime_data/fv_regime_20261005` | 6 | 1.04 MiB |
| `runtime_data/fv_research_20261004` | 10 | 112.88 MiB |
| `runtime_data/fv_research_native` | 28,675 | 4.547 GiB |
| `runtime_data/fv_research_production_arcus` | 3 | 17.10 MiB |
| `runtime_data/fv_research_production_bitget` | 3 | 55.28 MiB |
| `runtime_data/fv_research_production_bybit` | 3 | 55.10 MiB |
| `runtime_data/fv_research_production_grvt` | 3 | 9.78 MiB |
| `runtime_data/liquidity_mm` | 11 | 0.01 MiB |
| `runtime_data/liquidity_regime_blog.html` | 1 | 0.07 MiB |

### A2. 과거 원격 SQLite 비용 시험·재생 사본

| 삭제 경로/그룹 | 파일 수 | 크기 |
|---|---:|---:|
| `.codex_remote/` | 36 | 5.039 GiB |

### A3. 기존 배포 패치·소스 백업·문서 캐시

| 삭제 경로/그룹 | 파일 수 | 크기 |
|---|---:|---:|
| `.server_patch/` | 152 | 23.72 MiB |

### A4. 중복 소스·pytest 임시 데이터·다운로드·종료 hotfix

**위에서 보존한 baseline과 active-owner-after-p02 폴더는 제외한다.** `rf-*`, `pytest-*`, `pdfs`, API 문서 다운로드, 종료 hotfix 및 중복 v1~v6 소스는 삭제한다.

| 삭제 경로/그룹 | 파일 수 | 크기 |
|---|---:|---:|
| `tmp/benchmark_grvt_local_prepare.py` | 1 | 0.00 MiB |
| `tmp/build_cleanup_documents.py` | 1 | 0.02 MiB |
| `tmp/cleanup-local-inventory.json` | 1 | 46.51 MiB |
| `tmp/cleanup_inventory_20261019.json` | 1 | 7.28 MiB |
| `tmp/domestic-recovery-observation.json` | 1 | 0.00 MiB |
| `tmp/kis_vts_public_bundle.js` | 1 | 0.89 MiB |
| `tmp/kiwoom-official` | 421 | 11.55 MiB |
| `tmp/observe_domestic_recovery.py` | 1 | 0.00 MiB |
| `tmp/pdfs` | 1,210 | 181.49 MiB |
| `tmp/probe_arcus_compare.py` | 1 | 0.00 MiB |
| `tmp/probe_arcus_raw_time.py` | 1 | 0.00 MiB |
| `tmp/probe_arcus_timestamp.py` | 1 | 0.00 MiB |
| `tmp/probe_order_shape.py` | 1 | 0.00 MiB |
| `tmp/pytest-collector-incident` | 195 | 0.41 MiB |
| `tmp/pytest-crypto-latency` | 2 | 0.00 MiB |
| `tmp/pytest-disk-guard` | 6 | 0.00 MiB |
| `tmp/pytest-etf-recovery-capital` | 371 | 3.39 MiB |
| `tmp/pytest-etf-recovery-final1` | 370 | 3.28 MiB |
| `tmp/pytest-etf-recovery-fixed` | 349 | 2.82 MiB |
| `tmp/pytest-incident-final` | 62 | 0.01 MiB |
| `tmp/pytest-kis-kd-followup` | 338 | 1.85 MiB |
| `tmp/pytest-kis-kd-utf8` | 344 | 2.00 MiB |
| `tmp/pytest-recovery-sort` | 7 | 0.79 MiB |
| `tmp/pytest-trade-exclusions` | 20 | 0.36 MiB |
| `tmp/pytest-trade-reporting` | 29 | 0.46 MiB |
| `tmp/pytest-trade-reporting-approved` | 166 | 1.12 MiB |
| `tmp/pytest-universe-hot-path` | 143 | 0.04 MiB |
| `tmp/refactoring/20261018-stage-assignment/execution-risk-red` | 472 | 3.23 MiB |
| `tmp/refactoring/20261018-stage-assignment/integrated-v1` | 471 | 3.29 MiB |
| `tmp/refactoring/20261018-stage-assignment/integrated-v2` | 471 | 3.30 MiB |
| `tmp/refactoring/20261018-stage-assignment/linux-foundation-v1` | 472 | 3.39 MiB |
| `tmp/refactoring/20261018-stage-assignment/linux-foundation-v2` | 473 | 3.40 MiB |
| `tmp/refactoring/20261018-stage-assignment/local-runtime-v1` | 480 | 3.49 MiB |
| `tmp/refactoring/20261018-stage-assignment/local-runtime-v2` | 480 | 3.49 MiB |
| `tmp/refactoring/20261018-stage-assignment/local-runtime-v3` | 481 | 3.51 MiB |
| `tmp/refactoring/20261018-stage-assignment/local-runtime-v4` | 490 | 3.68 MiB |
| `tmp/refactoring/20261018-stage-assignment/local-runtime-v5` | 491 | 3.73 MiB |
| `tmp/refactoring/20261018-stage-assignment/local-runtime-v6` | 498 | 3.68 MiB |
| `tmp/refactoring/20261018-stage-assignment/p02-packet-v1` | 448 | 3.09 MiB |
| `tmp/refactoring/20261018-stage-assignment/p02-packet-v2` | 448 | 3.03 MiB |
| `tmp/refactoring/20261018-stage-assignment/p02-schema-v1` | 444 | 3.02 MiB |
| `tmp/refactoring/20261018-stage-assignment/p08-v1` | 452 | 3.05 MiB |
| `tmp/refactoring/20261018-stage-assignment/shared-fanout-v1` | 471 | 3.21 MiB |
| `tmp/refactoring/20261018-stage-assignment/storage-control-mount-v1` | 457 | 3.11 MiB |
| `tmp/refactoring/20261018-stage-assignment/storage-recovery-v2` | 466 | 3.18 MiB |
| `tmp/refactoring/20261018-stage-assignment/storage-recovery-v3` | 465 | 3.18 MiB |
| `tmp/refactoring/20261018-stage-assignment/transport-agent-v1` | 477 | 3.40 MiB |
| `tmp/refactoring/collect_server_source_20261019.py` | 1 | 0.01 MiB |
| `tmp/refactoring/server-candidates-20261019/active-owner-v1` | 490 | 3.73 MiB |
| `tmp/refactoring/server-candidates-20261019/active-owner-v6` | 497 | 3.67 MiB |
| `tmp/refactoring/server-candidates-20261019/etf-v1` | 489 | 3.70 MiB |
| `tmp/refactoring/server-hotfix-20261019/active-owner-v2` | 446 | 2.94 MiB |
| `tmp/refactoring/server-hotfix-20261019/active-owner-v3` | 447 | 2.95 MiB |
| `tmp/refactoring/server-hotfix-20261019/etf-history-v1` | 446 | 2.94 MiB |
| `tmp/refactoring/server-source-20261019/active-owner-base` | 116 | 1.15 MiB |
| `tmp/refactoring/server-source-20261019/vibetrading-collectors-1` | 113 | 1.11 MiB |
| `tmp/refactoring/server-source-20261019/vibetrading-crypto-collector-arcus-1` | 111 | 1.10 MiB |
| `tmp/refactoring/server-source-20261019/vibetrading-crypto-collector-binance-1` | 111 | 1.10 MiB |
| `tmp/refactoring/server-source-20261019/vibetrading-crypto-collector-grvt-1` | 111 | 1.10 MiB |
| `tmp/refactoring/server-source-20261019/vibetrading-crypto-maintenance-1` | 111 | 1.10 MiB |
| `tmp/refactoring/server-source-20261019/vibetrading-crypto-storage-1` | 111 | 1.10 MiB |
| `tmp/refactoring/server-source-20261019/vibetrading-etf-csv-sync-1` | 112 | 1.10 MiB |
| `tmp/refactoring/server-source-20261019/vibetrading-etf-mock-1` | 115 | 1.14 MiB |
| `tmp/refactoring/server-source-20261019/vibetrading-maintenance-1` | 111 | 1.10 MiB |
| `tmp/refactoring/server-source-20261019/vibetrading-monitor-bridge-1` | 111 | 1.10 MiB |
| `tmp/refactoring/server-source-20261019/vibetrading-monitor-timefolio-1` | 111 | 1.10 MiB |
| `tmp/refactoring/server-source-20261019/vibetrading-storage-1` | 111 | 1.10 MiB |
| `tmp/refactoring/server-source-20261019/vibetrading-telegram-1` | 112 | 1.11 MiB |
| `tmp/refresh_kiwoom_incident.py` | 1 | 0.00 MiB |
| `tmp/rf-15dd6e` | 414 | 6.95 MiB |
| `tmp/rf-345af5` | 87 | 0.97 MiB |
| `tmp/rf-446003` | 1,275 | 31.00 MiB |
| `tmp/rf-7d4ebc` | 1,082 | 25.38 MiB |
| `tmp/rf-9437ac` | 6 | 0.16 MiB |
| `tmp/rf-968c15` | 38 | 0.99 MiB |
| `tmp/rf-a4b357` | 169 | 1.43 MiB |
| `tmp/rf-a815d6` | 1,749 | 40.68 MiB |
| `tmp/rf-d3c9a3` | 1,904 | 43.79 MiB |
| `tmp/rf-d9c6e5` | 34 | 1.41 MiB |
| `tmp/rf-db42c8` | 1,274 | 30.98 MiB |
| `tmp/rf-e4057b` | 1,082 | 25.38 MiB |
| `tmp/rf-e6f022` | 1,082 | 25.38 MiB |
| `tmp/rf-ed08ef` | 438 | 12.82 MiB |
| `tmp/rf-ef219a` | 311 | 12.53 MiB |

### A5. 과거 evidence·리포트·오류·체크·raw 결과

아래 그룹은 2절의 네 개 예외 파일만 남기고 삭제한다. `native_repair_inputs/`, `archive_repair_inputs/`, 한국장 결손 수집/검증 스크립트·JSON·MD도 삭제하며 재개용으로 남기지 않는다.

| 삭제 경로/그룹 | 파일 수 | 크기 |
|---|---:|---:|
| `docs/evidence/activation.json` | 1 | 0.00 MiB |
| `docs/evidence/all_strategies_20261012` | 17 | 1.58 MiB |
| `docs/evidence/arcus-live-smoke-20261003.json` | 1 | 0.31 MiB |
| `docs/evidence/auth-verification.json` | 1 | 0.00 MiB |
| `docs/evidence/binance-bridge-server-tests-20261003.txt` | 1 | 0.00 MiB |
| `docs/evidence/binance-deployment-20261003.json` | 1 | 0.00 MiB |
| `docs/evidence/binance-headroom-20261003.json` | 1 | 0.00 MiB |
| `docs/evidence/binance-paused-20261003.json` | 1 | 0.03 MiB |
| `docs/evidence/binance-postfix-20261003.json` | 1 | 0.04 MiB |
| `docs/evidence/binance-verified-20261003.json` | 1 | 0.01 MiB |
| `docs/evidence/capacity-20261003.json` | 1 | 0.16 MiB |
| `docs/evidence/capacity-derived-20261003.json` | 1 | 0.04 MiB |
| `docs/evidence/capacity-metadata-20261003.json` | 1 | 0.00 MiB |
| `docs/evidence/capacity-observation-early-20261006.json` | 1 | 0.28 MiB |
| `docs/evidence/capacity-observation-final-20261006.json` | 1 | 0.28 MiB |
| `docs/evidence/capacity-release-20261006.json` | 1 | 0.00 MiB |
| `docs/evidence/capacity-validation-20261006.json` | 1 | 0.56 MiB |
| `docs/evidence/collector-diagnosis-20261007.json` | 1 | 0.36 MiB |
| `docs/evidence/collector-recovery-activation-20261007.json` | 1 | 0.00 MiB |
| `docs/evidence/collector-recovery-build-20261007.json` | 1 | 0.00 MiB |
| `docs/evidence/collector-recovery-completion-20261007.json` | 1 | 0.02 MiB |
| `docs/evidence/collector-source-baseline-20261007.json` | 1 | 0.00 MiB |
| `docs/evidence/collector-validation-20261007.json` | 1 | 0.52 MiB |
| `docs/evidence/collector-validation-derived-20261007.json` | 1 | 0.00 MiB |
| `docs/evidence/collector-validation-summary-20261007.json` | 1 | 0.00 MiB |
| `docs/evidence/collector_alerts_20261014.json` | 1 | 0.00 MiB |
| `docs/evidence/collector_recovery_20261015` | 9 | 0.34 MiB |
| `docs/evidence/completion-verification-20261006.json` | 1 | 0.03 MiB |
| `docs/evidence/crypto-capacity-diagnosis-20261001-2059.md` | 1 | 0.00 MiB |
| `docs/evidence/crypto-deletion-pc-20261002.json` | 1 | 0.00 MiB |
| `docs/evidence/crypto-deletion-server-20261002.json` | 1 | 0.01 MiB |
| `docs/evidence/crypto-ingestion-latency-20261001.json` | 1 | 6.15 MiB |
| `docs/evidence/crypto-minimal-coverage-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/crypto-minimal-cutover-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/crypto-minimal-io30-20261001.json` | 1 | 0.01 MiB |
| `docs/evidence/crypto-minimal-nats-regression-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/crypto-minimal-provenance-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/crypto-minimal-rollback-reclaim-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/crypto-minimal-status-20261001.json` | 1 | 0.01 MiB |
| `docs/evidence/crypto-resumption-20261002.json` | 1 | 0.00 MiB |
| `docs/evidence/crypto-storage-split-before-20261002.json` | 1 | 0.02 MiB |
| `docs/evidence/crypto-storage-split-coverage-20261002.json` | 1 | 0.00 MiB |
| `docs/evidence/crypto-storage-split-cutover-20261002.json` | 1 | 0.08 MiB |
| `docs/evidence/crypto-storage-split-deployment-audit-20261002.json` | 1 | 0.00 MiB |
| `docs/evidence/crypto-storage-split-final-20261002.json` | 1 | 0.04 MiB |
| `docs/evidence/crypto-storage-split-first-20261002.json` | 1 | 0.03 MiB |
| `docs/evidence/crypto-storage-split-market-cache-20261002.json` | 1 | 0.00 MiB |
| `docs/evidence/crypto-storage-split-old-layout-20261002.json` | 1 | 0.03 MiB |
| `docs/evidence/crypto-storage-split-post-cache-first-20261002.json` | 1 | 0.03 MiB |
| `docs/evidence/crypto-storage-split-post-cache-second-20261002.json` | 1 | 0.04 MiB |
| `docs/evidence/crypto-storage-split-second-20261002.json` | 1 | 0.03 MiB |
| `docs/evidence/crypto-storage-split-summary-20261002.json` | 1 | 0.00 MiB |
| `docs/evidence/crypto-storage-split-telegram-20261002.json` | 1 | 0.00 MiB |
| `docs/evidence/crypto-storage-split-with-monitor-20261002.json` | 1 | 0.03 MiB |
| `docs/evidence/crypto_e2e_20261014` | 15 | 4.24 MiB |
| `docs/evidence/dual_mock_20261014.json` | 1 | 0.00 MiB |
| `docs/evidence/etf_recovery_20261015` | 16 | 0.75 MiB |
| `docs/evidence/etf_scope_20261013` | 15 | 0.03 MiB |
| `docs/evidence/expansion-auth-probe-20261003.json` | 1 | 0.00 MiB |
| `docs/evidence/expansion-inventory-20261003.json` | 1 | 0.03 MiB |
| `docs/evidence/expansion-operational-verification-20261003.json` | 1 | 0.00 MiB |
| `docs/evidence/expansion-production-final-observation-20261003.json` | 1 | 0.25 MiB |
| `docs/evidence/expansion-production-observation-20261003.json` | 1 | 0.24 MiB |
| `docs/evidence/expansion-release-20261003.json` | 1 | 0.00 MiB |
| `docs/evidence/expansion-storage-verification-20261003.json` | 1 | 0.02 MiB |
| `docs/evidence/fv_mm_v4_20261004` | 11 | 0.81 MiB |
| `docs/evidence/fv_pilot_mainnet_20261004` | 28 | 2.37 MiB |
| `docs/evidence/fv_regime_20261005` | 209 | 233.17 MiB |
| `docs/evidence/fv_research_20261004` | 72 | 375.51 MiB |
| `docs/evidence/grvt-env-sync.json` | 1 | 0.00 MiB |
| `docs/evidence/grvt-live-smoke-20261003.json` | 1 | 0.36 MiB |
| `docs/evidence/hummingbot_structure_review_20261014` | 12 | 0.53 MiB |
| `docs/evidence/incident-after-20261006.json` | 1 | 0.47 MiB |
| `docs/evidence/incident-before-20261006.json` | 1 | 0.47 MiB |
| `docs/evidence/incident-final-20261006.json` | 1 | 0.48 MiB |
| `docs/evidence/incident-metadata-20261006.json` | 1 | 0.01 MiB |
| `docs/evidence/kis_kd_followup_20261015` | 16 | 0.36 MiB |
| `docs/evidence/kiwoom_recovery_20261014.json` | 1 | 0.00 MiB |
| `docs/evidence/liquidity-mm-setup-20261003.json` | 1 | 0.01 MiB |
| `docs/evidence/maintenance-progress-final-20261006.json` | 1 | 0.00 MiB |
| `docs/evidence/maintenance-release-final-20261006.json` | 1 | 0.00 MiB |
| `docs/evidence/maintenance-stops-20261006.json` | 1 | 0.00 MiB |
| `docs/evidence/maintenance-validation-final-20261006.json` | 1 | 0.43 MiB |
| `docs/evidence/market-audit-result-20261006.json` | 1 | 0.00 MiB |
| `docs/evidence/market-historical-audit-result-20261006.json` | 1 | 0.00 MiB |
| `docs/evidence/market-historical-instrument-days-20261006.csv` | 1 | 0.03 MiB |
| `docs/evidence/market-historical-observed-book-intervals-20261006.csv` | 1 | 0.10 MiB |
| `docs/evidence/market-instrument-days-20261006.csv` | 1 | 0.02 MiB |
| `docs/evidence/market-migration-complete-20261006.json.gz` | 1 | 7.74 MiB |
| `docs/evidence/market-migration-summary-20261006.json` | 1 | 0.00 MiB |
| `docs/evidence/market-observed-book-intervals-20261006.csv` | 1 | 0.07 MiB |
| `docs/evidence/market-operations-20261006.json` | 1 | 0.02 MiB |
| `docs/evidence/mm_state_revision_20261007` | 45 | 18.34 MiB |
| `docs/evidence/monitor-failure-256m.json` | 1 | 0.00 MiB |
| `docs/evidence/nats-matched.conf` | 1 | 0.00 MiB |
| `docs/evidence/old-market-readiness-20261006.json` | 1 | 0.00 MiB |
| `docs/evidence/prepared-256m.json` | 1 | 0.00 MiB |
| `docs/evidence/profile-after-20261006.json` | 1 | 0.00 MiB |
| `docs/evidence/r1-crypto-storage-20260929` | 5 | 0.06 MiB |
| `docs/evidence/r1-maintenance-20260929` | 17 | 0.19 MiB |
| `docs/evidence/r1-noexpand-20260929` | 17 | 0.20 MiB |
| `docs/evidence/r2-crypto-image-manifest-20260930.json` | 1 | 0.01 MiB |
| `docs/evidence/r2-crypto-prune-image-manifest-20261001.json` | 1 | 0.01 MiB |
| `docs/evidence/r2-current-local-snapshot-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-current-remote-backup-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-current-snapshot-reuse-preparation-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-event-runs-migration-20260929.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-event-runs-nats-e2e-20260930.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-event-runs-optimized-lookup-20260930.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-load-integrity-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-load-pressure-20261001.jsonl` | 1 | 1.49 MiB |
| `docs/evidence/r2-load-summary-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-local-dbstat-20260929.json` | 1 | 0.01 MiB |
| `docs/evidence/r2-local-integrity-20260929.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-observed-burst-replay-trace-20260930.json` | 1 | 0.01 MiB |
| `docs/evidence/r2-observed-replay-trace-20260930.json` | 1 | 0.01 MiB |
| `docs/evidence/r2-precompact-archive-20260930.txt` | 1 | 0.00 MiB |
| `docs/evidence/r2-precompact-reclaim-20260930.txt` | 1 | 0.00 MiB |
| `docs/evidence/r2-production-scale-btree-cost-20260930.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-production-scale-recovery-cost-20260930.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-production-scale-runs-cost-20260930.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-prune-generation-nats-e2e-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-prune-service-budget-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-readonly-20260929.json` | 1 | 0.01 MiB |
| `docs/evidence/r2-recovery-coverage-migration-20260930.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-recovery-nats-e2e-20260930.txt` | 1 | 0.00 MiB |
| `docs/evidence/r2-replay-terminal-integrity-preparation-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-retired-trial-cache-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-retired-trial-image-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-retirement-nats-e2e-20260930.txt` | 1 | 0.00 MiB |
| `docs/evidence/r2-synthetic-index-ablation-20260929.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-synthetic-rowid-20260929.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-synthetic-without-rowid-20260929.json` | 1 | 0.00 MiB |
| `docs/evidence/r2-trial-256m-kernel-oom.txt` | 1 | 0.00 MiB |
| `docs/evidence/r2-wal-attribution-20260929.json` | 1 | 0.00 MiB |
| `docs/evidence/refactoring_20261018/catalog_readonly.json` | 1 | 0.01 MiB |
| `docs/evidence/refactoring_20261018/collect_catalog_readonly.py` | 1 | 0.00 MiB |
| `docs/evidence/refactoring_20261018/collect_readonly.py` | 1 | 0.01 MiB |
| `docs/evidence/refactoring_20261018/document_validation.json` | 1 | 0.01 MiB |
| `docs/evidence/refactoring_20261018/local_source_inventory.json` | 1 | 0.09 MiB |
| `docs/evidence/refactoring_20261019/archive_repair_inputs` | 35 | 0.64 MiB |
| `docs/evidence/refactoring_20261019/collect_archive_repair_inputs.py` | 1 | 0.01 MiB |
| `docs/evidence/refactoring_20261019/collect_archive_scope_check.py` | 1 | 0.00 MiB |
| `docs/evidence/refactoring_20261019/collect_korean_continuity.py` | 1 | 0.01 MiB |
| `docs/evidence/refactoring_20261019/collect_korean_gap_detail.py` | 1 | 0.01 MiB |
| `docs/evidence/refactoring_20261019/collect_korean_repair_sources.py` | 1 | 0.01 MiB |
| `docs/evidence/refactoring_20261019/collect_native_repair_inputs.py` | 1 | 0.01 MiB |
| `docs/evidence/refactoring_20261019/korean_hotfix_deploy_identity.json` | 1 | 0.00 MiB |
| `docs/evidence/refactoring_20261019/korean_strategy_continuity.json` | 1 | 0.04 MiB |
| `docs/evidence/refactoring_20261019/korean_strategy_continuity_audit.md` | 1 | 0.01 MiB |
| `docs/evidence/refactoring_20261019/korean_strategy_gap_detail.json` | 1 | 0.06 MiB |
| `docs/evidence/refactoring_20261019/korean_strategy_repair_sources.json` | 1 | 0.05 MiB |
| `docs/evidence/refactoring_20261019/native_repair_inputs` | 6 | 0.72 MiB |
| `docs/evidence/refactoring_20261019/server_source_diff_summary.json` | 1 | 0.10 MiB |
| `docs/evidence/refactoring_20261019/server_source_preservation.md` | 1 | 0.01 MiB |
| `docs/evidence/refactoring_20261019/validate_archive_repair_inputs.py` | 1 | 0.00 MiB |
| `docs/evidence/refactoring_20261019/validate_native_repair_inputs.py` | 1 | 0.00 MiB |
| `docs/evidence/retained-trees.json` | 1 | 0.03 MiB |
| `docs/evidence/retired_alerts_20261014` | 2 | 0.00 MiB |
| `docs/evidence/storage-after-20261006.prof` | 1 | 0.05 MiB |
| `docs/evidence/storage-before-20261006.prof` | 1 | 0.02 MiB |
| `docs/evidence/storage-sizing-20261001.json` | 1 | 0.00 MiB |
| `docs/evidence/telegram_recovery_20261014.json` | 1 | 0.01 MiB |
| `docs/evidence/tests-passed.json` | 1 | 0.00 MiB |
| `docs/evidence/toss-runtime-20261007.log` | 1 | 0.05 MiB |
| `docs/evidence/toss-storage-verification-20261007.json` | 1 | 0.01 MiB |
| `docs/evidence/verified-trees.json` | 1 | 0.03 MiB |

### A6. 과거 리팩토링 실행 결과·중간 메타데이터

각 그룹에서 **2절의 유지 파일은 제외**한다. `stdout.txt`, `junit.xml`, guard receipts, matplotlib 캐시, 중간 source JSON, 실패 txt, 종료된 measurement 디렉터리는 삭제한다. 장기 측정 미완료 사실은 단일 요약에 남겼다.

| 삭제 경로/그룹 | 파일 수 | 크기 |
|---|---:|---:|
| `docs/refactoring/runs/20261018-stage-assignment/baseline-full` | 846 | 23.69 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/baseline-short` | 6 | 0.22 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/baseline_source.tar.gz` | 1 | 0.80 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/benchmark_encoding.py` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/benchmark_schema.py` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/encoding_benchmark_guard.jsonl` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/encoding_oracle_guard.jsonl` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/evidence_corrections.json` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/execution-risk-red` | 4 | 0.06 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/execution-risk-red-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/guard_probe.json` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/guard_probe_receipts.jsonl` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/handoff-initial-20261018.md` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/implementation_preflight` | 2 | 0.18 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/integrated-full-v1` | 5 | 0.29 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/integrated-full-v2` | 5 | 0.29 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/integrated-full-v4` | 5 | 0.35 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/integrated-linux-v5/guard_receipts.jsonl` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/integrated-linux-v5/junit.xml` | 1 | 0.15 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/integrated-linux-v5/launcher.json` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/integrated-linux-v5/launcher_stdout.txt` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/integrated-linux-v5/stdout.txt` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/integrated-v1-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/integrated-v2-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/integrated-windows-v5/guard_receipts.jsonl` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/integrated-windows-v5/junit.xml` | 1 | 0.15 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/integrated-windows-v5/matplotlib/fontlist-v390.json` | 1 | 0.14 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/integrated-windows-v5/stdout.txt` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/linux-foundation-targeted-v2` | 5 | 0.02 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/linux-foundation-targeted-v3` | 5 | 0.02 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/linux-foundation-targeted-v4` | 6 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/linux-foundation-v1-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/linux-foundation-v2-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-facts-wal-linux-v6/guard_receipts.jsonl` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-facts-wal-linux-v6/junit.xml` | 1 | 0.01 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-facts-wal-linux-v6/launcher.json` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-facts-wal-linux-v6/launcher_stdout.txt` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-facts-wal-linux-v6/stdout.txt` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-facts-wal-windows-v6/guard_receipts.jsonl` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-facts-wal-windows-v6/junit.xml` | 1 | 0.01 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-facts-wal-windows-v6/stdout.txt` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-runtime-linux-v1` | 6 | 0.02 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-runtime-linux-v2` | 6 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-runtime-linux-v3` | 6 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-runtime-linux-v4` | 6 | 0.06 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-runtime-v1-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-runtime-v2-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-runtime-v3-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-runtime-v4-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-runtime-v5-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/local-runtime-v6-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/measurement-72h-v1` | 7 | 0.43 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/measurement-72h-v2` | 7 | 0.25 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/measurement-preflight` | 3 | 0.01 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/p02-packet-full-v1` | 5 | 0.27 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/p02-packet-targeted-v1` | 4 | 0.06 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/p02-packet-targeted-v2` | 4 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/p02-packet-v1-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/p02-packet-v2-encoding-benchmark.json` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/p02-packet-v2-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/p02-schema-v1-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/p02-schema-v1/guard_receipts.jsonl` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/p02-schema-v1/junit.xml` | 1 | 0.08 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/p02-schema-v1/matplotlib/fontlist-v390.json` | 1 | 0.14 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/p02-schema-v1/stdout.txt` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/p02-server-apply-error.txt` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/p02-server-resume-error.txt` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/p08-targeted-v1` | 4 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/p08-v1-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/progress-v6.tar.gz` | 1 | 0.48 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/run.json` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/schema_benchmark.json` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/schema_benchmark_guard.jsonl` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/schema_oracle_guard.jsonl` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/server-active-owner-base.json` | 1 | 0.04 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/server-active-owner-candidate-v1.json` | 1 | 0.03 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/server-candidate-source-equivalence.json` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/server-etf-candidate-v1.json` | 1 | 0.03 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/server-history-hotfix-active-owner-basis-v2.json` | 1 | 0.02 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/server-history-hotfix-active-owner-basis-v3.json` | 1 | 0.02 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/server-history-hotfix-agent-receipt.json` | 1 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/server-history-hotfix-basis.json` | 1 | 0.01 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/server-history-hotfix-independent-review.md` | 1 | 0.01 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/server-history-hotfix-linux-v1` | 6 | 0.02 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/server-history-hotfix-linux-v3` | 6 | 0.03 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/server-history-hotfix-windows-v1` | 3 | 0.00 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/server-history-hotfix-windows-v2` | 4 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/server-history-hotfix-windows-v3` | 4 | 0.06 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/shared-fanout-targeted-v1` | 4 | 0.07 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/shared-fanout-v1-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/storage-control-mount-targeted-v1` | 4 | 0.07 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/storage-control-mount-v1-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/storage-recovery-targeted-v2` | 4 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/storage-recovery-targeted-v3` | 4 | 0.07 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/storage-recovery-v2-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/storage-recovery-v3-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/transport-agent-tests-v1` | 4 | 0.06 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/transport-agent-v1-source.json` | 1 | 0.05 MiB |
| `docs/refactoring/runs/20261018-stage-assignment/validation.json` | 1 | 0.00 MiB |

### A7. 완료된 리뷰·옛 티켓·중복 현황 문서

| 삭제 경로/그룹 | 파일 수 | 크기 |
|---|---:|---:|
| `docs/refactoring/code_map.json` | 1 | 0.37 MiB |
| `docs/refactoring/control_recovery_review.md` | 1 | 0.01 MiB |
| `docs/refactoring/current_state_audit.md` | 1 | 0.03 MiB |
| `docs/refactoring/known_failures.md` | 1 | 0.00 MiB |
| `docs/refactoring/local_pipeline_review.md` | 1 | 0.01 MiB |
| `docs/refactoring/local_storage_review.md` | 1 | 0.00 MiB |
| `docs/refactoring/p00_review.md` | 1 | 0.03 MiB |
| `docs/refactoring/p02_encoding_ticket.json` | 1 | 0.02 MiB |
| `docs/refactoring/p02_packet_review.md` | 1 | 0.01 MiB |
| `docs/refactoring/p02_schema_review.md` | 1 | 0.00 MiB |
| `docs/refactoring/p03_market_ticket.json` | 1 | 0.02 MiB |
| `docs/refactoring/p04_code_review.md` | 1 | 0.01 MiB |
| `docs/refactoring/p04_etf_ticket.json` | 1 | 0.00 MiB |
| `docs/refactoring/p04_implementation_ticket.json` | 1 | 0.01 MiB |
| `docs/refactoring/p04_safety_review.md` | 1 | 0.02 MiB |
| `docs/refactoring/p05_fanout_ticket.json` | 1 | 0.00 MiB |
| `docs/refactoring/p05_local_engine_ticket.json` | 1 | 0.00 MiB |
| `docs/refactoring/p05_spool_bridge_ticket.json` | 1 | 0.00 MiB |
| `docs/refactoring/p05_spool_ticket.json` | 1 | 0.01 MiB |
| `docs/refactoring/p05_transport_ticket.json` | 1 | 0.00 MiB |
| `docs/refactoring/p06_recovery_ticket.json` | 1 | 0.00 MiB |
| `docs/refactoring/p07_storage_review.md` | 1 | 0.01 MiB |
| `docs/refactoring/p08_implementation_ticket.json` | 1 | 0.00 MiB |
| `docs/refactoring/server_history_hotfix_candidate_20261019.md` | 1 | 0.01 MiB |
| `docs/refactoring/server_korean_signal_review_20261019.md` | 1 | 0.01 MiB |
| `docs/refactoring/storage_fanout_code_review.md` | 1 | 0.01 MiB |

### A8. 날짜별 과거 MD·집계표·옛 AI 지침

| 삭제 경로/그룹 | 파일 수 | 크기 |
|---|---:|---:|
| `docs/BINANCE_COLLECTOR_FIX_2026-10-03.md` | 1 | 0.01 MiB |
| `docs/BYBIT_TESTNET_LATENCY_2026-09-26.md` | 1 | 0.00 MiB |
| `docs/CLEANUP_DELETE_KEEP_2026-10-15.md` | 1 | 0.01 MiB |
| `docs/CLEANUP_DIRECTORY_INDEX_2026-10-15.md` | 1 | 0.05 MiB |
| `docs/CLEANUP_PYTHON_FILES_2026-10-15.md` | 1 | 0.14 MiB |
| `docs/COLLECTOR_RECOVERY_AND_MISSING_ALERTS_2026-10-15.md` | 1 | 0.01 MiB |
| `docs/COLLECTOR_TIMEOUT_AND_MOCK_ALERTS_2026-10-14.md` | 1 | 0.01 MiB |
| `docs/CRYPTO_BACKPRESSURE_2026-09-28.md` | 1 | 0.01 MiB |
| `docs/CRYPTO_BACKPRESSURE_REVIEW_2026-09-28.md` | 1 | 0.02 MiB |
| `docs/CRYPTO_CAPACITY_AND_MARKET_COVERAGE_2026-10-06.md` | 1 | 0.01 MiB |
| `docs/CRYPTO_CORE_INEFFICIENCY_CANDIDATES_2026-10-01.md` | 1 | 0.01 MiB |
| `docs/CRYPTO_DELETION_2026-10-02.md` | 1 | 0.00 MiB |
| `docs/CRYPTO_DEPLOYMENT_2026-09-26.md` | 1 | 0.00 MiB |
| `docs/CRYPTO_E2E_LATENCY_2026-10-14.md` | 1 | 0.01 MiB |
| `docs/CRYPTO_E2E_LATENCY_REASSESSMENT_2026-10-15.md` | 1 | 0.01 MiB |
| `docs/CRYPTO_EXCHANGE_EXPANSION_CAPACITY_2026-10-03.md` | 1 | 0.01 MiB |
| `docs/CRYPTO_INCIDENT_2026-09-27.md` | 1 | 0.00 MiB |
| `docs/CRYPTO_INGESTION_RELEASE_PLAN_V2_2026-09-29.md` | 1 | 0.04 MiB |
| `docs/CRYPTO_IO_STRUCTURAL_REASSESSMENT_2026-09-29.md` | 1 | 0.05 MiB |
| `docs/CRYPTO_LATENCY_2026-10-01.md` | 1 | 0.01 MiB |
| `docs/CRYPTO_MINIMAL_FIX_2026-10-01.md` | 1 | 0.01 MiB |
| `docs/CRYPTO_STORAGE_SPLIT_FIX_2026-10-02.md` | 1 | 0.01 MiB |
| `docs/ETF_FIRST_SIGNAL_RECOVERY_2026-10-15.md` | 1 | 0.00 MiB |
| `docs/EXCHANGE_EXPANSION_AND_STORAGE_2026-10-03.md` | 1 | 0.01 MiB |
| `docs/HUMMINGBOT_STRUCTURE_REVIEW_2026-10-14.md` | 1 | 0.03 MiB |
| `docs/INCIDENT_REMEDIATION_2026-09-23.md` | 1 | 0.01 MiB |
| `docs/KIS_LAG_AND_KD_SIGNAL_AUDIT_2026-10-15.md` | 1 | 0.01 MiB |
| `docs/KIWOOM_RECONNECT_AND_KIS_REJECTION_2026-10-14.md` | 1 | 0.01 MiB |
| `docs/MARKET_COVERAGE_AUDIT_2026-09-25.md` | 1 | 0.00 MiB |
| `docs/MARKET_COVERAGE_BY_SYMBOL_2026-09-25.csv` | 1 | 0.01 MiB |
| `docs/MARKET_COVERAGE_GAPS_2026-09-25.csv` | 1 | 0.01 MiB |
| `docs/MARKET_COVERAGE_PROVIDER_OUTAGES_2026-09-25.csv` | 1 | 0.00 MiB |
| `docs/MARKET_COVERAGE_SUMMARY_2026-09-25.json` | 1 | 0.02 MiB |
| `docs/MARKET_DATA_QA_2026-09-14.md` | 1 | 0.00 MiB |
| `docs/MARKET_GAP_RECONCILIATION_2026-10-06.json` | 1 | 0.00 MiB |
| `docs/MARKET_OBSERVATION_DAYS_2026-10-06.csv` | 1 | 0.05 MiB |
| `docs/MARKET_OBSERVED_INTERVALS_2026-10-06.csv` | 1 | 0.23 MiB |
| `docs/REFACTORING_SUMMARY.md` | 1 | 0.01 MiB |
| `docs/RELEASE1_CRYPTO_STORAGE_RESIDUAL_2026-09-29.md` | 1 | 0.01 MiB |
| `docs/RELEASE1_MAINTENANCE_YIELD_2026-09-29.md` | 1 | 0.01 MiB |
| `docs/RETIRED_COLLECTOR_ALERT_FIX_2026-10-14.md` | 1 | 0.00 MiB |
| `docs/ROOT_CAUSE_AND_ETF_GAP_AUDIT_2026-10-15.md` | 1 | 0.01 MiB |
| `docs/SKILLS.md` | 1 | 0.02 MiB |
| `docs/SSH_CONNECTION_DIAGNOSIS_2026-10-03.md` | 1 | 0.01 MiB |
| `docs/TELEGRAM_COMMANDS_AND_SERVER_RECOVERY_2026-10-14.md` | 1 | 0.01 MiB |
| `docs/TOSS_AND_CRYPTO_RECOVERY_2026-10-07.md` | 1 | 0.01 MiB |
| `docs/TOSS_DAYMARKET_2026-09-28.md` | 1 | 0.00 MiB |
| `docs/claude.md` | 1 | 0.00 MiB |

### A9. 생성된 빌드·그림·일반 캐시

| 삭제 경로/그룹 | 파일 수 | 크기 |
|---|---:|---:|
| `.DS_Store` | 1 | 0.01 MiB |
| `.matplotlib-cache/fontlist-v3.11.0.json` | 1 | 0.18 MiB |
| `.pytest_cache/.gitignore` | 1 | 0.00 MiB |
| `.pytest_cache/CACHEDIR.TAG` | 1 | 0.00 MiB |
| `.pytest_cache/README.md` | 1 | 0.00 MiB |
| `.pytest_cache/v` | 2 | 0.06 MiB |
| `dashboard/.vinext` | 1 | 0.00 MiB |
| `dashboard/.wrangler` | 13 | 0.13 MiB |
| `dashboard/dist` | 71 | 2.03 MiB |
| `dashboard/outputs` | 3 | 1.71 MiB |
| `dashboard/tsconfig.tsbuildinfo` | 1 | 0.53 MiB |

## 4. B 참조 정리 후 삭제할 도구

다음은 현재 제품·회귀 시험에서 직접 호출하지 않는 과거 결과 생성/종료 작업 도구다. 다만 P00의 넓은 `baseline_manifest.json` 목록 때문에 `prepare_candidate.py`가 일부 옛 스크립트까지 복사하도록 되어 있다. 삭제와 같은 커밋에서 비필수 삭제 경로를 테스트 소스 선택에서 제외해야 한다. **고정 baseline manifest의 과거 해시를 현재 파일의 해시로 덮어쓰지는 않는다.** 현재 비교 입력과 실제 회귀 fixture는 그대로 유지한다.

```text
scripts/activate_etf_first_signal_recovery.py
scripts/build_all_strategy_report.py
scripts/complete_fv_research_partitions.py
scripts/complete_mm_state_server_review.py
scripts/merge_fv_research_results.py
scripts/plot_fv_diagnosis.py
scripts/plot_fv_report.py
scripts/polish_strategy_report.py
scripts/probe_missing_etf_trades.py
scripts/refactoring/collect_korean_hotfix_metadata.py
scripts/refactoring/publish_progress.py
scripts/refactoring/rebase_after_p02.py
scripts/render_strategy_report_pdf.py
scripts/seal_fv_indicator_report.py
scripts/validate_all_strategy_report.py
scripts/verify_etf_first_signal_recovery.py
```

- `collect_korean_hotfix_metadata.py`의 `REMOTE`는 현재 `collect_release_metadata.py`가 import한다. 이 범용 metadata 코드를 현재 수집기에 흡수/공통화한 뒤 옛 파일을 삭제한다. ETF 수리 작업을 보존하라는 의미가 아니다.
- `rebase_after_p02.py`·`publish_progress.py`는 완료한 특정 경로를 다시 생성하는 일회용 도구다. 다음 실행은 `build_server_candidate.py`와 새 격리 candidate label을 사용한다.
- `deploy_p02_schema.py`는 현재 활성 P02의 rollback 때문에 유지한다. 이미 끝난 prepare에 묶인 v5/p02-schema 시험 폴더 참조는 정리하되 rollback을 제거하지 않는다.
- `update_execution_state.py`, 현재 세 문서·보드·tasks/gates의 폐기된 KR 작업/과거 증거 링크를 정리한다. 종료된 ETF 수집 태스크를 자동으로 다시 추가하지 않게 한다.
- `scripts/measure_crypto_e2e.py`, `analyze_fv_run.py`, `analyze_fv_pilot.py`, `diagnose_fv_prediction.py`, `prepare_crypto_event_index.py`, `migrate_crypto_delivery_ledger.py`, `reclaim_export_duplicates.py` 등은 정규 테스트가 import하므로 삭제하지 않는다. 과거 결과를 지우는 것과 재사용 분석/회귀 코드를 지우는 것은 구분한다.

## 5. 선택적으로 지울 재생성 캐시

- 작업 폴더의 모든 `__pycache__/`, `*.pyc`, `.DS_Store`.
- `dashboard/node_modules/`: lockfile로 재설치 가능하다. `dashboard/package.json`·lockfile·소스·`.sites-runtime/`은 유지한다.
- `.analysis-venv/`: 과거 분석 환경을 재사용하지 않을 때 삭제 가능하다. requirements 파일은 유지하며, 이후 분석을 실행하려면 환경 설치가 필요하다.
- 이 선택 캐시와 `.env`/런타임 데이터는 Git 커밋 대상과 별개다. 이미 ignore되는 로컬 파일을 지워야만 웹에서 코드를 읽을 수 있는 것은 아니다.

## 6. 커밋에 남길 상태

현재 제품 소스·정규 회귀 테스트·설정/배포 파일·세 리팩토링 문서·현재 contracts/gates/handoff·이 단일 실험 요약을 중심으로 커밋한다. 기준 소스 두 벌과 운영 CSV/DB는 비교·운영 입력으로 따로 유지하고 무심코 `git add .`로 올리지 않는다. 목록 작성 뒤 승인된 A 항목을 삭제했으며 실제 결과는 7절에 기록했다. 로컬과 서버가 달라 추가 커밋은 하지 않았다.

이 목록은 실제 존재한 파일을 조사한 결과다. 삭제를 수행하기 전 새로 생긴 현재 작업 파일까지 같은 이름 패턴만으로 삭제 대상으로 확대하지 않는다.

## 7. 실제 삭제 결과

- 실제 삭제: 29,621파일, 6.170GiB. 빈 디렉터리 14,276개도 정리했다.
- 기존 목록에서 이미 없던 파일은 제외했다. A 기본 정리에서 보호한 코드·시험·설정·도구 481파일의 해시는 삭제 전후 동일했다. 이후 종료 hotfix의 미사용 배포 metadata JSON 1개를 추가로 삭제했고, 이를 제외한 필수 480파일도 해시 동일성을 재확인했다.
- 삭제 실패 0개, 사전 점검 뒤 변경되어 건너뛴 파일 0개. B 항목과 두 기준 소스는 유지했다.
- 실행 중 서버 소스를 별도로 비교했으며 로컬과 달라 커밋하지 않았다. 비교 범위와 현재 상태는 `refactoring_context.md` 첫 문단을 따른다.

추가로 `scripts/refactoring/korean_history_hotfix_deployment_manifest.json`을 삭제했다. 제품·시험·스크립트의 호출 참조가 없는 종료 작업 메타데이터였다. B 도구 16개는 그대로 유지했다.
