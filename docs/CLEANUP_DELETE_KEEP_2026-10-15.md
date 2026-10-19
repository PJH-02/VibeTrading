# VibeTrading 삭제·보존 안내 — 2026-10-15

**삭제한 파일은 없다.** 아래는 사용자가 직접 정리하기 위한 검토 목록이다. 삭제 명령이나 자동 삭제 스크립트도 만들지 않았다.

10/15 장중 후속 수정: /srv/vibetrading-data/releases/kis-lag-fix-9ab198562cba/는 현재 KIS 시세 지연 수정 배포본으로 보존한다. 새 정식 테스트 tests/test_universe_hot_path.py도 보존 대상이다. 아래 전체 표는 앞선 메타데이터 조사 시점의 목록이며, 이전 collector-recovery 배포본은 rollback 용도로 남아 있다.

- [디렉터리·배포 파일별 전체 표](CLEANUP_DIRECTORY_INDEX_2026-10-15.md): 로컬 디렉터리 100개, 서버 디렉터리 157개, 배포/원본 CSV 파일 45개.
- [Python 파일 534개 개별 표](CLEANUP_PYTHON_FILES_2026-10-15.md): 정식 운영 소스, 연구 소스, 완료 작업 복사본을 구분.
- [기계 판독 목록](evidence/collector_recovery_20261015/cleanup-review.json): 경로·크기·판정·근거.
- [디스크 원인 및 ETF 누락 분석](ROOT_CAUSE_AND_ETF_GAP_AUDIT_2026-10-15.md).

## 판정 읽는 법

| 판정 | 의미 |
|---|---|
| 재생성 가능 | 실행 중인 작업이 끝나고 원본·최종본이 남아 있으면 지울 수 있는 캐시/설치물/임시파일 |
| 완료 작업 후보 | 단발 배포·측정·계좌 테스트가 끝났고, 고유 증적과 rollback 한 부가 보존돼 있으면 정리할 후보 |
| 조건부·연구 / 조건부·백업 | 고유 데이터일 수 있다. 연구 중단 여부, 원격 보존, 파일 내용 일치를 확인하기 전 보존 |
| 보존 | 운영 의존성·원본·원장·설정·최종 산출물·중요 증적 |

“상시 운영에서 호출하지 않음”만으로 고유 연구 코드를 불필요하다고 판정하지 않았다. 현재 대부분의 정식 소스가 Git 미추적 상태라서 “지워도 Git으로 복구 가능”하다는 전제를 둘 수도 없다.

## 먼저 정리 검토할 항목

로컬 경로 기준은 C:\Users\박제형\Desktop\VibeTrading이다. 아래 크기는 로컬에서 파일 길이를 합친 값이다. pnpm 링크/hardlink, 계속 쓰이는 파일 때문에 실제 삭제 후 확보량과 다를 수 있다. 상위·하위 항목은 중복 합산하지 않는다.

| 로컬 경로 | 크기 | 삭제/보존 판단 |
|---|---:|---|
| .codex_remote/r2-event-run-cost/recovery-benchmark-only.db | 1,761,280,000 B | 완료 작업 후보. 성능 비교용 복제 DB |
| .codex_remote/r2-event-run-cost/btree-benchmark-only.db | 1,441,701,888 B | 완료 작업 후보. 성능 비교용 복제 DB |
| .codex_remote/r2-event-run-cost/runs-benchmark-only.db | 1,436,606,464 B | 완료 작업 후보. 성능 비교용 복제 DB |
| .codex_remote/r2-synthetic-20260929/rowid.sqlite3 | 296,845,312 B | 완료 작업 후보. 합성 DB |
| .codex_remote/r2-synthetic-20260929/without_rowid.sqlite3 | 267,341,824 B | 완료 작업 후보. 합성 DB |
| .codex_remote/r2-synthetic-20260929/no_event_index_ablation.sqlite3 | 201,949,184 B | 완료 작업 후보. 합성 DB |
| 위 두 폴더의 btree.json / runs.json / rowid.json / without_rowid.json / no_event_index_ablation.json | 작음 | 최종 성능 수치 증적으로 보존 권장 |
| dashboard/node_modules/ | 합계 약 3.29GB | 재생성 가능. 개발/빌드 종료 후 lockfile로 재설치 |
| .analysis-venv/ | 약 292.6MB | 재생성 가능. 분석 종료 후 requirements로 재설치 |
| tmp/pdfs/ | 약 198.5MB | 재생성 가능. 최종 PDF·원본 문서를 보존하고 렌더 중간물 정리 |
| dashboard/dist/, .next/, .vinext/ | dist 약 2.13MB | 재생성 가능. 로컬 미리보기 종료 후 |
| .pytest_cache/, .matplotlib-cache/, 모든 __pycache__/ 및 *.pyc, .DS_Store | 작음 | 재생성 가능한 캐시·OS 메타데이터 |
| tmp/pytest-*/ | 합계 수 MB | 테스트 종료 후 재생성 가능 |
| tmp/kiwoom-official/ | 약 13.3MB | 공식 예제 checkout. 참조 URL/commit 보존 후 재다운로드 가능 |
| tmp/cleanup-local-inventory.json | 48,771,607 B | 이번 조사의 메타데이터 중간물. 최종 목록 검토 후 재생성 가능한 후보 |
| .server_patch/의 과거 배포 tar·복사본·프로파일 결과 | 합계 약 25.1MB | 완료 작업 후보. 현 소스·rollback·인증 변경 비교 증적 한 부 보존 후 |
| .codex_remote/의 나머지 과거 배포 tar·단발 코드 | 위 DB 제외 수 MB | 완료 작업 후보. 현재 작업/원본·증적과 구분 후 |
| scripts/의 과거 단발 배포·측정·계좌 테스트 파일 | 작음 | 파일별 표에서 후보 지정. 연구/반복 운영 도구와 구분 |

복제 DB는 scripts/compare_event_run_cost.py, 합성 DB는 scripts/compare_ledger_layouts.py의 생성 방식과 이름을 대조했다. 운영 주문 원장으로 취급하지 않는다. 다만 결과를 다시 재현할 필요가 있다면 생성 코드·입력 원본·결과 JSON은 남겨야 한다.

서버에서는 다음 항목이 상대적으로 크다. 모두 main 서버의 Linux 경로이며 micro 서버와 구분한다.

| 서버 경로 | 실제 할당량 | 삭제/보존 판단 |
|---|---:|---|
| /srv/vibetrading-capacity/fv-research-20261004/duckdb-temp/ | 11,274,768,384 B | 재생성 가능한 DuckDB spill. 조사 시 열린 FD 0건. 연구 재실행 등 사용 여부 확인 후 후보 |
| /srv/vibetrading-capacity/migration-retained-20261006/ | 12,212,895,744 B | 조건부 백업. 과거 DB·시세·수출 묶음의 고유 행과 원격 receipt를 검증하기 전 보존 |
| /srv/vibetrading-capacity/migration-retained-20261003/ | 1,979,166,720 B | 조건부 백업. 이전 crypto DB·시세 내용 대조 전 보존 |
| /srv/vibetrading-capacity/fv-research-20261004/의 native/slim/압축본 | 전체 연구 폴더 약 26.99GB, spill 포함 | 조건부 연구 데이터. 축약본이 원본의 완전한 대체라고 가정하지 않음 |
| /opt/oracle-a1-catcher/ | 559,759,360 B | 이 main 서버의 service는 disabled/inactive, 열린 FD 없음. 잘못 남은 설치물의 정리 후보 |
| /root/vibetrading-mm-state-review-20261007/ | 10,526,720 B | 과거 검토 checkout 복사본. 정식 소스·고유 결과 보존 후 후보 |
| /srv/vibetrading-data/testnet_latency_audits/ | 34,152,448 B | 완료 측정. 고유 events/result 한 부를 해시 검증 보존한 뒤 후보 |
| /opt/vibetrading/latency-audits/ | 139,264 B | 완료 측정·원상복구 설정. 증적 보존 후 후보 |
| /srv/vibetrading-data/host-tools/vscode-servers/Stable-각버전 | 버전별 약 640~670MiB | 현재 접속에서 쓰지 않는 구버전만 후보. 목적지 전체와 기존 symlink는 보존 |

큰 연구·마이그레이션 보관본은 300GB 데이터 디스크에 있다. 이번에 루트 여유를 급감시킨 파일은 새 VS Code 서버 버전 약 703MB였다. 로컬의 큰 벤치마크 DB나 데이터 디스크의 측정 산출물과는 별개다.

## 정리하더라도 남겨야 할 로컬 항목

| 경로 | 이유 |
|---|---|
| .env, .env.example, .git/ | 자격증명, 설정 문서, Git 이력. 민감값을 공개 목록에 포함하지 않음 |
| README.md, requirements*.txt, Dockerfile*, docker-compose.yml, pytest.ini, .dockerignore, .gitignore | 재설치·빌드·검증 입력 |
| main.py, vibetrading/**, tests/**, config/**, deploy/** | 운영 코드·설정·회귀 검증. 중지한 거래소 모듈도 공용 CLI가 import할 수 있음 |
| scripts/os_disk_guard.py | 현재 가드의 정식 소스 |
| runtime_data/etf_reference_csv/raw/069500_daily.csv 및 069500_minute.csv | C3/KP가 공유하는 사용자 원본 |
| 같은 위치의 122630_daily.csv 및 122630_minute.csv | C3/KP 레버리지 실행 차량 원본 |
| 같은 위치의 229200_daily.csv 및 229200_minute.csv | KD 신호·롱 실행 원본 |
| 같은 위치의 251340_daily.csv 및 251340_minute.csv | KD 인버스 실행 원본 |
| 같은 위치의 KOSPI200_daily.csv, SP500_daily.csv, 상위 manifest.json | 지수 입력 및 파일 무결성 |
| .server_patch/etf-reference-csv-20261013.tar.gz | 위 CSV와의 내용 동일성을 아직 대조하지 않은 압축 묶음. 중복 검증 전 보존 |
| runtime_data/state/** | 로컬 시세 DB·토큰·Bybit 테스트 주문 원장. 과거 실험이어도 이름만 보고 삭제하지 않음 |
| runtime_data/fv_pilot/server_evidence/**/run.sqlite | 연구 원장. 임시 캐시와 구분 |
| runtime_data/fv_research_native/*_slim.tar.gz 및 해제본 | 중복처럼 보여도 전체 내용 해시를 아직 대조하지 않음 |
| output/pdf/crypto_strategy_complete_report_20261012.pdf | 최종 공유 산출물. tmp 렌더 파일과 구분 |
| docs/** | 운영 원인·연구 보고서 및 고유 증적. 큰 raw 증적은 아래 조건으로만 별도 정리 |
| dashboard/.git/, .sites-runtime/, .wrangler/, build/, vendor/ 및 소스·lockfile | Git/연결/로컬 DB 상태 또는 빌드 소스. node_modules와 구분 |
| backtest/, services/, shared/, strategy/, strategies/, run_strategy.py | 기존 별도 전략/백테스트 소스. 연구를 중단하고 호출부까지 정리할 때 조건부 삭제 검토 |

docs/evidence/fv_research_20261004 약 393.8MB, fv_regime_20261005 약 244.5MB, mm_state_revision_20261007 약 19.2MB는 큰 증적 묶음이다. 최종 보고서만으로 원본 연구를 모두 재현할 수 있는지 확인되지 않았으므로 일괄 삭제 대상에 넣지 않았다. 원격 사본과 해시·내용을 검증한 후 중복 raw만 분리할 수 있다.

## 서버 필수 보존 항목

| 경로 | 이유 |
|---|---|
| /etc/vibetrading/app.env, compose.env, secrets/* 및 /root/vibetrading/.env | 실제 운영 자격증명·설정 |
| /root/vibetrading/config/ | 활성 컨테이너의 /app/config:ro bind mount 원본 |
| /root/vibetrading/vibetrading/ 및 빌드 입력 | 현재 정식 서버 소스·재배포 입력 |
| /opt/vibetrading/releases/telegram-recovery-20261014/runtime.yml | 현재 systemd와 디스크 가드 재개가 참조. 날짜만 보고 구버전으로 삭제 불가 |
| 같은 디렉터리 dashboard.yml | 활성 dashboard bridge/timefolio 설정 |
| /opt/vibetrading/releases/capacity-20261006/market.yml | 호환 mirror. 전체 참조 재확인 전 보존 |
| /usr/local/lib/vibetrading/os_disk_guard.py, os-disk-guard.sh 및 관련 systemd unit/timer | 실제 호스트 가드 실행 경로 |
| /srv/vibetrading-data/releases/collector-recovery-20261015/ | 현 장애 복구 build·이전 소스·runtime.before.json·journal-before.sqlite3. 지금은 rollback 보존 |
| /srv/vibetrading-capacity/market-state/vibetrading.sqlite3 및 -wal/-shm | 활성 시장 저장 catalogue·delivery 원장. 본체 약 9.25GB는 백업 파일이 아님 |
| /srv/vibetrading-capacity/crypto-state/vibetrading.sqlite3 및 -wal/-shm | 활성 코인 저장 원장. 본체 약 7.60GB와 WAL 모두 보존 |
| market-state/collection_gaps.sqlite3 | 수집 누락 추적 |
| market-state/kiwoom_token_real.json 및 .lock, kis_access_token*.json, toss_access_token.json | 인증 캐시·프로세스 간 잠금. 0바이트 lock도 실행 중 삭제하면 잠금이 분리될 수 있음 |
| market-state/etf-mock/ 아래 history CSV·manifest·주문 장부·재구성 기록 | 전략 과거 입력·실제/가정 체결·순손익 기록 |
| market-state/etf-csv-sync/sessions/*.jsonl | 장중 분봉·마감 입력. 반복 행이 있어도 백업 파일로 간주하지 않음 |
| /srv/vibetrading-data/docker/ | Docker가 관리하는 이미지/레이어. 내부 파일을 수동 삭제하지 않음 |
| /srv/vibetrading-data/runtime_data/dashboard-monitor/ | 활성 monitor mount |
| /srv/vibetrading-data/host-tools/vscode-servers 및 /root/.vscode-server/cli/servers symlink | 이동한 VS Code 서버와 원래 경로 |
| cloud object, manifest, upload receipt, 삭제/복구 audit | 원격 보관·복구를 입증하는 근거. 로컬과 원격을 동시에 제거하면 안 됨 |

runtime_data/state는 /dev/vdc의 market-state bind mount이며 /srv/vibetrading-capacity/market-state와 같은 데이터다. runtime_data/market_data와 market-data, /srv/vibetrading-crypto-state와 crypto-state도 같은 실체의 별칭이다. 경로가 둘이라고 백업이 두 부 있는 것이 아니다.

## 이번 목록의 한계

2026-10-15 최초 신호 복구 추가: `/srv/vibetrading-data/releases/etf-recovery-07a74502d725/`는 현재 활성 이미지 빌드·복구 계획·검증·롤백 자료이므로 보존한다. `market-state/etf-recovery/2026-10-15/`와 ETF 장부의 recovery_runs / reconstructed_orders / strategy_order_exclusions도 운영 기록이므로 보존한다. 미사용 staging `/srv/vibetrading-data/releases/etf-recovery-7f2404a306ea/`는 활성 이미지·실행 프로세스·독자 증적 참조가 없음을 재확인한 뒤 조건부 정리할 수 있다. 이번 작업에서 삭제하지 않았다.

운영 데이터의 전체 해시 대조나 cloud 보존 검증을 추가 실행하지 않았다. 파일 이름·날짜·미사용 FD 스냅샷만으로 데이터 삭제를 확정하지 않았다. PostgreSQL/NATS/Docker 등의 내부 파일이나 SQLite WAL을 청소 파일로 분류하지 않았다. 실제 계좌 테스트의 보고서 제외도 원본 주문 장부 파일을 삭제하는 것과는 별개다.

현 소스·활성 compose·운영 DB를 유지하면서 코드베이스를 가볍게 하려면 먼저 캐시, 합성/복제 벤치마크 DB, 완료 작업의 복사본부터 정리하고, 연구 데이터와 구형 엔진은 별도 보존 여부를 정하는 순서가 적절하다. 이 문서는 삭제 계획의 근거이며 실제 삭제는 사용자에게 맡긴다.
