# VibeTrading 디렉터리·배포 파일 전체 검토표 — 2026-10-15

삭제하지 않은 검토 목록이다. 판정의 의미와 필수 보존 파일은 [삭제·보존 안내](CLEANUP_DELETE_KEEP_2026-10-15.md)를 먼저 읽는다.

## 로컬 디렉터리

루트: C:\Users\박제형\Desktop\VibeTrading. 조사 260,927개 파일, .git 내부 제외. 크기는 논리 파일 길이 합계이며 링크/hardlink 중복을 포함할 수 있다. 상위·하위 행을 합산하지 않는다.

| 경로 | 크기 | 파일 수 | 판정 | 근거 |
|---|---:|---:|---|---|
| [.analysis-venv](<C:/Users/박제형/Desktop/VibeTrading/.analysis-venv>) | 279.03 MiB | 8,761 | 재생성 가능 | 분석 종료 후 requirements로 재설치 가능 |
| [.analysis-venv/Lib](<C:/Users/박제형/Desktop/VibeTrading/.analysis-venv/Lib>) | 277.57 MiB | 8,744 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [.analysis-venv/Scripts](<C:/Users/박제형/Desktop/VibeTrading/.analysis-venv/Scripts>) | 1.46 MiB | 15 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [.analysis-venv/share](<C:/Users/박제형/Desktop/VibeTrading/.analysis-venv/share>) | 5.5 KiB | 1 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [.codex_remote](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote>) | 5.04 GiB | 37 | 완료 작업 후보 | 과거 배포/성능 실험. 아래 DB 폴더는 운영 원본이 아님; 결과 JSON과 생성 코드 보존 |
| [.codex_remote/__pycache__](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/__pycache__>) | 17.0 KiB | 1 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [.codex_remote/r2-event-run-cost](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-event-run-cost>) | 4.32 GiB | 5 | 완료 작업 후보 | benchmark-only 복제 DB 3개; 결과 JSON 및 원본 입력 보존 후 정리 |
| [.codex_remote/r2-synthetic-20260929](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-synthetic-20260929>) | 730.65 MiB | 6 | 완료 작업 후보 | 합성 SQLite DB 3개; 거래 원장 아님. 결과와 생성 코드 보존 |
| [.matplotlib-cache](<C:/Users/박제형/Desktop/VibeTrading/.matplotlib-cache>) | 185.5 KiB | 1 | 재생성 가능 | 폰트 캐시 |
| [.pytest_cache](<C:/Users/박제형/Desktop/VibeTrading/.pytest_cache>) | 61.9 KiB | 5 | 재생성 가능 | pytest 종료 후 정리 |
| [.pytest_cache/v](<C:/Users/박제형/Desktop/VibeTrading/.pytest_cache/v>) | 61.4 KiB | 2 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [.server_patch](<C:/Users/박제형/Desktop/VibeTrading/.server_patch>) | 23.92 MiB | 172 | 완료 작업 후보 | 과거 배포 tar/복사본. 정식 소스·rollback·인증 비교 증적 한 부 보존 후 정리. ETF 데이터 tar 제외 |
| [.server_patch/__pycache__](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/__pycache__>) | 29.8 KiB | 6 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [.server_patch/api-docs](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/api-docs>) | 3.39 MiB | 20 | 재생성 가능 | 공급자 문서 다운로드; 참조 URL과 필요한 문서 보존 |
| [.server_patch/config](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/config>) | 2.1 KiB | 2 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [.server_patch/crypto-backpressure-20260928](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/crypto-backpressure-20260928>) | 333.7 KiB | 23 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [.server_patch/profiler](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/profiler>) | 2.80 MiB | 1 | 완료 작업 후보 | 완료한 프로파일링 산출물; 최종 수치/재현 코드 보존 |
| [.server_patch/vibetrading](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/vibetrading>) | 268.1 KiB | 24 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [__pycache__](<C:/Users/박제형/Desktop/VibeTrading/__pycache__>) | 0.3 KiB | 1 | 재생성 가능 | 하위 __pycache__/와 *.pyc에도 동일 적용 |
| [backtest](<C:/Users/박제형/Desktop/VibeTrading/backtest>) | 82.1 KiB | 19 | 조건부·구형/연구 | 기존 run_strategy 백테스트 엔진; 사용 목적 확인 |
| [backtest/__pycache__](<C:/Users/박제형/Desktop/VibeTrading/backtest/__pycache__>) | 45.3 KiB | 7 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [backtest/engines](<C:/Users/박제형/Desktop/VibeTrading/backtest/engines>) | 5.1 KiB | 7 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [config](<C:/Users/박제형/Desktop/VibeTrading/config>) | 13.4 KiB | 9 | 보존 | 운영 수집·전략 설정 |
| [dashboard](<C:/Users/박제형/Desktop/VibeTrading/dashboard>) | 3.08 GiB | 218,980 | 보존 | 사이트 원본과 설치물 혼재; 하위 재생성 항목만 분리 |
| [dashboard/.git](<C:/Users/박제형/Desktop/VibeTrading/dashboard/.git>) | 705.1 KiB | 190 | 보존 | 별도 dashboard Git 이력 |
| [dashboard/.next](<C:/Users/박제형/Desktop/VibeTrading/dashboard/.next>) | 1.2 KiB | 1 | 재생성 가능 | 빌드 캐시 |
| [dashboard/.openai](<C:/Users/박제형/Desktop/VibeTrading/dashboard/.openai>) | 0.1 KiB | 1 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [dashboard/.sites-runtime](<C:/Users/박제형/Desktop/VibeTrading/dashboard/.sites-runtime>) | 6.09 MiB | 214 | 조건부 보존 | 사이트 작업/연결 상태. 단순 캐시로 일괄 삭제하지 않음 |
| [dashboard/.vinext](<C:/Users/박제형/Desktop/VibeTrading/dashboard/.vinext>) | 0.2 KiB | 1 | 재생성 가능 | 빌드 캐시 |
| [dashboard/.wrangler](<C:/Users/박제형/Desktop/VibeTrading/dashboard/.wrangler>) | 133.0 KiB | 13 | 조건부 보존 | 로컬 DB/state 포함 가능; export 후 판단 |
| [dashboard/app](<C:/Users/박제형/Desktop/VibeTrading/dashboard/app>) | 54.8 KiB | 9 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [dashboard/build](<C:/Users/박제형/Desktop/VibeTrading/dashboard/build>) | 16.9 KiB | 5 | 보존 | 빌드용 소스·설정; 이름만 보고 캐시로 삭제하지 않음 |
| [dashboard/components](<C:/Users/박제형/Desktop/VibeTrading/dashboard/components>) | 209.9 KiB | 62 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [dashboard/db](<C:/Users/박제형/Desktop/VibeTrading/dashboard/db>) | 1.5 KiB | 2 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [dashboard/dist](<C:/Users/박제형/Desktop/VibeTrading/dashboard/dist>) | 2.03 MiB | 71 | 재생성 가능 | 로컬 미리보기 종료 후 재빌드 가능 |
| [dashboard/drizzle](<C:/Users/박제형/Desktop/VibeTrading/dashboard/drizzle>) | 5.4 KiB | 3 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [dashboard/examples](<C:/Users/박제형/Desktop/VibeTrading/dashboard/examples>) | 2.0 KiB | 2 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [dashboard/hooks](<C:/Users/박제형/Desktop/VibeTrading/dashboard/hooks>) | 0.6 KiB | 1 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [dashboard/lib](<C:/Users/박제형/Desktop/VibeTrading/dashboard/lib>) | 10.5 KiB | 7 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [dashboard/node_modules](<C:/Users/박제형/Desktop/VibeTrading/dashboard/node_modules>) | 3.07 GiB | 218,360 | 재생성 가능 | 개발/빌드 종료 후 lockfile로 재설치. 링크/hardlink로 실제 확보 공간은 다름 |
| [dashboard/outputs](<C:/Users/박제형/Desktop/VibeTrading/dashboard/outputs>) | 1.71 MiB | 3 | 완료 작업 후보 | 최종 결과/배포 증적 한 부 보존 후 |
| [dashboard/public](<C:/Users/박제형/Desktop/VibeTrading/dashboard/public>) | 2.5 KiB | 4 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [dashboard/scripts](<C:/Users/박제형/Desktop/VibeTrading/dashboard/scripts>) | 55.0 KiB | 13 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [dashboard/vendor](<C:/Users/박제형/Desktop/VibeTrading/dashboard/vendor>) | 16.7 KiB | 2 | 보존 | 빌드/실행 공급 코드 |
| [deploy](<C:/Users/박제형/Desktop/VibeTrading/deploy>) | 18.0 KiB | 13 | 보존 | compose/systemd/NATS 배포 원본; env는 민감정보 가능 |
| [docs](<C:/Users/박제형/Desktop/VibeTrading/docs>) | 659.70 MiB | 662 | 보존 | 운영/연구 보고서 및 고유 증적 |
| [docs/evidence](<C:/Users/박제형/Desktop/VibeTrading/docs/evidence>) | 658.81 MiB | 599 | 조건부 보존 | 고유 원본/재현 근거. 원격 사본 검증 전 일괄 삭제 불가 |
| [docs/mm_state](<C:/Users/박제형/Desktop/VibeTrading/docs/mm_state>) | 28.0 KiB | 6 | 보존 | 연구 기록 |
| [output](<C:/Users/박제형/Desktop/VibeTrading/output>) | 1.84 MiB | 1 | 보존 | 최종 보고서 PDF |
| [output/pdf](<C:/Users/박제형/Desktop/VibeTrading/output/pdf>) | 1.84 MiB | 1 | 보존 | 최종 crypto_strategy_complete_report_20261012.pdf |
| [runtime_data](<C:/Users/박제형/Desktop/VibeTrading/runtime_data>) | 6.78 GiB | 28,843 | 보존 | 시세·CSV·주문/연구 원장 혼재; 전체 삭제 불가 |
| [runtime_data/capacity_replay_20260926](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/capacity_replay_20260926>) | 216.3 KiB | 5 | 완료 작업 후보 | 과거 성능 시험. 증적 보존 후 운영 데이터와 분리해 정리 |
| [runtime_data/etf_reference_csv](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/etf_reference_csv>) | 112.14 MiB | 11 | 보존 | 사용자 정리 티커별 원본 CSV 10개와 manifest |
| [runtime_data/fv_mm](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/fv_mm>) | 184.37 MiB | 51 | 조건부·연구 | 테스트넷/가상매매 원장·연구 입력 |
| [runtime_data/fv_pilot](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/fv_pilot>) | 1.70 GiB | 43 | 조건부·연구 | run.sqlite·server_evidence·압축본. 결과 원장 한 부 검증 후 중복만 |
| [runtime_data/fv_regime_20261005](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/fv_regime_20261005>) | 1.04 MiB | 6 | 조건부·연구 | 연구 입력/산출물; 원격 보존·동일성 미검증 |
| [runtime_data/fv_research_20261004](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/fv_research_20261004>) | 112.88 MiB | 10 | 조건부·연구 | 원시 catalogue·audit·파생 parquet; 다른 사본과 해시 미대조 |
| [runtime_data/fv_research_native](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/fv_research_native>) | 4.55 GiB | 28,675 | 조건부·연구 | 원시/파생 연구 입력과 압축본. 해제본과 중복 검증 전 보존 |
| [runtime_data/fv_research_production_arcus](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/fv_research_production_arcus>) | 17.10 MiB | 3 | 조건부·연구 | 연구 입력/산출물; 원격 보존·동일성 미검증 |
| [runtime_data/fv_research_production_bitget](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/fv_research_production_bitget>) | 55.28 MiB | 3 | 조건부·연구 | 연구 입력/산출물; 원격 보존·동일성 미검증 |
| [runtime_data/fv_research_production_bybit](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/fv_research_production_bybit>) | 55.10 MiB | 3 | 조건부·연구 | 연구 입력/산출물; 원격 보존·동일성 미검증 |
| [runtime_data/fv_research_production_grvt](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/fv_research_production_grvt>) | 9.78 MiB | 3 | 조건부·연구 | 연구 입력/산출물; 원격 보존·동일성 미검증 |
| [runtime_data/liquidity_mm](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/liquidity_mm>) | 425.7 KiB | 23 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [runtime_data/state](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/state>) | 200.2 KiB | 4 | 보존 | 로컬 토큰/DB/주문 원장. WAL/SHM/lock 실행 중 삭제 불가 |
| [scripts](<C:/Users/박제형/Desktop/VibeTrading/scripts>) | 1.46 MiB | 257 | 파일별 판단 | Python 파일별 목록 참조 |
| [scripts/__pycache__](<C:/Users/박제형/Desktop/VibeTrading/scripts/__pycache__>) | 573.1 KiB | 74 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [services](<C:/Users/박제형/Desktop/VibeTrading/services>) | 295.5 KiB | 40 | 조건부·구형/연구 | 구형 실행 경로. shared/backtest 의존성과 함께 정리 필요 |
| [services/__pycache__](<C:/Users/박제형/Desktop/VibeTrading/services/__pycache__>) | 5.5 KiB | 1 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [services/data_feed](<C:/Users/박제형/Desktop/VibeTrading/services/data_feed>) | 90.7 KiB | 9 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [services/execution](<C:/Users/박제형/Desktop/VibeTrading/services/execution>) | 102.7 KiB | 13 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [services/monitoring](<C:/Users/박제형/Desktop/VibeTrading/services/monitoring>) | 15.0 KiB | 3 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [services/risk_engine](<C:/Users/박제형/Desktop/VibeTrading/services/risk_engine>) | 25.4 KiB | 4 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [services/signal_gen](<C:/Users/박제형/Desktop/VibeTrading/services/signal_gen>) | 51.8 KiB | 9 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [shared](<C:/Users/박제형/Desktop/VibeTrading/shared>) | 163.4 KiB | 20 | 조건부·구형/연구 | 구형 공통 DB/모델/메시징. 호출부와 함께 판단 |
| [shared/__pycache__](<C:/Users/박제형/Desktop/VibeTrading/shared/__pycache__>) | 106.9 KiB | 13 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [strategies](<C:/Users/박제형/Desktop/VibeTrading/strategies>) | 23.4 KiB | 8 | 조건부·구형/연구 | turtle/example 및 동적 로더 대상 |
| [strategies/__pycache__](<C:/Users/박제형/Desktop/VibeTrading/strategies/__pycache__>) | 13.6 KiB | 5 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [strategy](<C:/Users/박제형/Desktop/VibeTrading/strategy>) | 24.9 KiB | 8 | 조건부·연구 | 코인 S1~S4 연구 전략. ETF와 별개인 고유 소스 |
| [strategy/__pycache__](<C:/Users/박제형/Desktop/VibeTrading/strategy/__pycache__>) | 12.4 KiB | 4 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [tests](<C:/Users/박제형/Desktop/VibeTrading/tests>) | 1.14 MiB | 152 | 보존 | 회귀 테스트 |
| [tests/__pycache__](<C:/Users/박제형/Desktop/VibeTrading/tests/__pycache__>) | 836.0 KiB | 88 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [tmp](<C:/Users/박제형/Desktop/VibeTrading/tmp>) | 205.20 MiB | 2,700 | 완료 작업 후보 | 현재 조사 중간 메타데이터 포함; 실행 중인 작업 확인 후 하위별 정리 |
| [tmp/kiwoom-official](<C:/Users/박제형/Desktop/VibeTrading/tmp/kiwoom-official>) | 12.68 MiB | 450 | 재생성 가능 | 공식 예제 checkout; URL/commit 보존 |
| [tmp/pdfs](<C:/Users/박제형/Desktop/VibeTrading/tmp/pdfs>) | 189.27 MiB | 1,761 | 재생성 가능 | 렌더 중간물. output/pdf 최종본과 원본 문서 보존 |
| [tmp/pytest-collector-incident](<C:/Users/박제형/Desktop/VibeTrading/tmp/pytest-collector-incident>) | 417.0 KiB | 195 | 재생성 가능 | 테스트 임시 DB/출력. 테스트 종료 후 |
| [tmp/pytest-crypto-latency](<C:/Users/박제형/Desktop/VibeTrading/tmp/pytest-crypto-latency>) | 1.0 KiB | 2 | 재생성 가능 | 테스트 임시 DB/출력. 테스트 종료 후 |
| [tmp/pytest-disk-guard](<C:/Users/박제형/Desktop/VibeTrading/tmp/pytest-disk-guard>) | 0.5 KiB | 6 | 재생성 가능 | 테스트 임시 DB/출력. 테스트 종료 후 |
| [tmp/pytest-incident-final](<C:/Users/박제형/Desktop/VibeTrading/tmp/pytest-incident-final>) | 13.2 KiB | 62 | 재생성 가능 | 테스트 임시 DB/출력. 테스트 종료 후 |
| [tmp/pytest-trade-exclusions](<C:/Users/박제형/Desktop/VibeTrading/tmp/pytest-trade-exclusions>) | 372.1 KiB | 20 | 재생성 가능 | 테스트 임시 DB/출력. 테스트 종료 후 |
| [tmp/pytest-trade-reporting](<C:/Users/박제형/Desktop/VibeTrading/tmp/pytest-trade-reporting>) | 466.2 KiB | 29 | 재생성 가능 | 테스트 임시 DB/출력. 테스트 종료 후 |
| [tmp/pytest-trade-reporting-approved](<C:/Users/박제형/Desktop/VibeTrading/tmp/pytest-trade-reporting-approved>) | 1.12 MiB | 166 | 재생성 가능 | 테스트 임시 DB/출력. 테스트 종료 후 |
| [vibetrading](<C:/Users/박제형/Desktop/VibeTrading/vibetrading>) | 2.12 MiB | 220 | 보존 | 현재 운영 모듈 원본 |
| [vibetrading/__pycache__](<C:/Users/박제형/Desktop/VibeTrading/vibetrading/__pycache__>) | 553.8 KiB | 48 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [vibetrading/dashboard](<C:/Users/박제형/Desktop/VibeTrading/vibetrading/dashboard>) | 56.3 KiB | 12 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [vibetrading/fv_mm](<C:/Users/박제형/Desktop/VibeTrading/vibetrading/fv_mm>) | 156.9 KiB | 30 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [vibetrading/fv_pilot](<C:/Users/박제형/Desktop/VibeTrading/vibetrading/fv_pilot>) | 98.4 KiB | 10 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [vibetrading/liquidity_mm](<C:/Users/박제형/Desktop/VibeTrading/vibetrading/liquidity_mm>) | 200.1 KiB | 22 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [vibetrading/mm_state](<C:/Users/박제형/Desktop/VibeTrading/vibetrading/mm_state>) | 67.4 KiB | 20 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [vibetrading/providers](<C:/Users/박제형/Desktop/VibeTrading/vibetrading/providers>) | 404.4 KiB | 28 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |
| [vibetrading/timefolio](<C:/Users/박제형/Desktop/VibeTrading/vibetrading/timefolio>) | 54.1 KiB | 6 | 상위 판정 적용 | 상위 디렉터리 정책을 따름; 소스와 캐시 구별 |

빈 디렉터리는 집계에서 빠질 수 있다. .git은 보존한다. 비어 있는 운영 데이터 경로를 굳이 삭제할 필요는 없다.

## 배포 산출물·원본 CSV 파일별 목록

| 경로 | 크기 | 판정 |
|---|---:|---|
| [.codex_remote/crypto-minimal-fix-20261001.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/crypto-minimal-fix-20261001.tar.gz>) | 164.2 KiB | 완료 작업 후보 |
| [.codex_remote/r2-archive-scheduler-20261001.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-archive-scheduler-20261001.tar.gz>) | 162.6 KiB | 완료 작업 후보 |
| [.codex_remote/r2-crypto-prune-20261001.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-crypto-prune-20261001.tar.gz>) | 162.6 KiB | 완료 작업 후보 |
| [.codex_remote/r2-crypto-source-20260930-gates.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-crypto-source-20260930-gates.tar.gz>) | 162.2 KiB | 완료 작업 후보 |
| [.codex_remote/r2-crypto-source-20260930.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-crypto-source-20260930.tar.gz>) | 162.2 KiB | 완료 작업 후보 |
| [.codex_remote/r2-load-pressure-interim.jsonl](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-load-pressure-interim.jsonl>) | 862.1 KiB | 완료 작업 후보 |
| [.codex_remote/r2-maintenance-capacity-20261001.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-maintenance-capacity-20261001.tar.gz>) | 163.7 KiB | 완료 작업 후보 |
| [.codex_remote/r2-maintenance-recovery-20261001.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-maintenance-recovery-20261001.tar.gz>) | 163.7 KiB | 완료 작업 후보 |
| [.codex_remote/r2-maintenance-service-20261001.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-maintenance-service-20261001.tar.gz>) | 163.7 KiB | 완료 작업 후보 |
| [.codex_remote/r2-maintenance-service-final-20261001.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-maintenance-service-final-20261001.tar.gz>) | 163.8 KiB | 완료 작업 후보 |
| [.codex_remote/r2-observed-receive-histogram.json](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-observed-receive-histogram.json>) | 213.0 KiB | 완료 작업 후보 |
| [.codex_remote/r2-prune-index-scan-20261001.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-prune-index-scan-20261001.tar.gz>) | 163.4 KiB | 완료 작업 후보 |
| [.codex_remote/r2-prune-proof-index-20261001.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-prune-proof-index-20261001.tar.gz>) | 163.6 KiB | 완료 작업 후보 |
| [.codex_remote/r2-prune-read-cache-20261001.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-prune-read-cache-20261001.tar.gz>) | 163.1 KiB | 완료 작업 후보 |
| [.codex_remote/r2-recovery-test-source.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-recovery-test-source.tar.gz>) | 225.1 KiB | 완료 작업 후보 |
| [.codex_remote/r2-replay-files.json](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-replay-files.json>) | 743.2 KiB | 완료 작업 후보 |
| [.codex_remote/r2-retirement-test-source.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-retirement-test-source.tar.gz>) | 231.6 KiB | 완료 작업 후보 |
| [.codex_remote/r2-test-source.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.codex_remote/r2-test-source.tar.gz>) | 216.7 KiB | 완료 작업 후보 |
| [.server_patch/arcus_llms.txt](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/arcus_llms.txt>) | 23.9 KiB | 완료 작업 후보 |
| [.server_patch/capacity-20261006.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/capacity-20261006.tar.gz>) | 27.1 KiB | 완료 작업 후보 |
| [.server_patch/collector-recovery-20261007.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/collector-recovery-20261007.tar.gz>) | 60.8 KiB | 완료 작업 후보 |
| [.server_patch/collector-recovery-20261015.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/collector-recovery-20261015.tar.gz>) | 113.5 KiB | 완료 작업 후보 |
| [.server_patch/crypto-ledger-20260928.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/crypto-ledger-20260928.tar.gz>) | 50.0 KiB | 완료 작업 후보 |
| [.server_patch/crypto_deploy_20260926.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/crypto_deploy_20260926.tar.gz>) | 117.6 KiB | 완료 작업 후보 |
| [.server_patch/crypto_deploy_20260927_fix.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/crypto_deploy_20260927_fix.tar.gz>) | 117.8 KiB | 완료 작업 후보 |
| [.server_patch/docker-compose.yml](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/docker-compose.yml>) | 3.0 KiB | 완료 작업 후보 |
| [.server_patch/etf-reference-csv-20261013.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/etf-reference-csv-20261013.tar.gz>) | 15.16 MiB | 보존·중복검증 전 |
| [.server_patch/etf-scope-20261013.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/etf-scope-20261013.tar.gz>) | 123.5 KiB | 완료 작업 후보 |
| [.server_patch/expansion-20261003.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/expansion-20261003.tar.gz>) | 251.1 KiB | 완료 작업 후보 |
| [.server_patch/incident_1714_capacity.jsonl](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/incident_1714_capacity.jsonl>) | 322.1 KiB | 완료 작업 후보 |
| [.server_patch/maintenance-capacity-20261006.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/maintenance-capacity-20261006.tar.gz>) | 50.8 KiB | 완료 작업 후보 |
| [.server_patch/nats-frozen.json](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/nats-frozen.json>) | 12.2 KiB | 완료 작업 후보 |
| [.server_patch/nats-restored.json](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/nats-restored.json>) | 11.8 KiB | 완료 작업 후보 |
| [.server_patch/telegram-recovery-20261014.tar.gz](<C:/Users/박제형/Desktop/VibeTrading/.server_patch/telegram-recovery-20261014.tar.gz>) | 386.1 KiB | 완료 작업 후보 |
| [runtime_data/etf_reference_csv/manifest.json](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/etf_reference_csv/manifest.json>) | 3.4 KiB | 보존 |
| [runtime_data/etf_reference_csv/raw/069500_daily.csv](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/etf_reference_csv/raw/069500_daily.csv>) | 386.5 KiB | 보존 |
| [runtime_data/etf_reference_csv/raw/069500_minute.csv](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/etf_reference_csv/raw/069500_minute.csv>) | 27.68 MiB | 보존 |
| [runtime_data/etf_reference_csv/raw/122630_daily.csv](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/etf_reference_csv/raw/122630_daily.csv>) | 274.9 KiB | 보존 |
| [runtime_data/etf_reference_csv/raw/122630_minute.csv](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/etf_reference_csv/raw/122630_minute.csv>) | 28.22 MiB | 보존 |
| [runtime_data/etf_reference_csv/raw/229200_daily.csv](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/etf_reference_csv/raw/229200_daily.csv>) | 178.4 KiB | 보존 |
| [runtime_data/etf_reference_csv/raw/229200_minute.csv](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/etf_reference_csv/raw/229200_minute.csv>) | 27.89 MiB | 보존 |
| [runtime_data/etf_reference_csv/raw/251340_daily.csv](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/etf_reference_csv/raw/251340_daily.csv>) | 158.3 KiB | 보존 |
| [runtime_data/etf_reference_csv/raw/251340_minute.csv](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/etf_reference_csv/raw/251340_minute.csv>) | 26.62 MiB | 보존 |
| [runtime_data/etf_reference_csv/raw/KOSPI200_daily.csv](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/etf_reference_csv/raw/KOSPI200_daily.csv>) | 135.8 KiB | 보존 |
| [runtime_data/etf_reference_csv/raw/SP500_daily.csv](<C:/Users/박제형/Desktop/VibeTrading/runtime_data/etf_reference_csv/raw/SP500_daily.csv>) | 627.1 KiB | 보존 |

## 서버 디렉터리

main 서버의 Linux 절대 경로다. du 할당량이며 bind mount와 부모/자식 중복을 합산하지 않는다.

| 서버 절대 경로 | 할당량 | 판정 | 근거 |
|---|---:|---|---|
| /opt/oracle-a1-catcher | 533.83 MiB | 완료 작업 후보 | main에서 disabled/inactive, 열린 FD 없음. micro 서버와 혼동하지 않음 |
| /opt/oracle-a1-catcher/.venv | 533.79 MiB | 완료 작업 후보 | main에서 disabled/inactive, 열린 FD 없음. micro 서버와 혼동하지 않음 |
| /opt/oracle-a1-catcher/.venv/bin | 52.0 KiB | 완료 작업 후보 | main에서 disabled/inactive, 열린 FD 없음. micro 서버와 혼동하지 않음 |
| /opt/oracle-a1-catcher/.venv/include | 8.0 KiB | 완료 작업 후보 | main에서 disabled/inactive, 열린 FD 없음. micro 서버와 혼동하지 않음 |
| /opt/oracle-a1-catcher/.venv/lib | 533.72 MiB | 완료 작업 후보 | main에서 disabled/inactive, 열린 FD 없음. micro 서버와 혼동하지 않음 |
| /opt/vibetrading | 360.0 KiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /opt/vibetrading/dashboard | 4.0 KiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /opt/vibetrading/incidents | 24.0 KiB | 보존·증적 | 장애/복구 근거; 대용량 복사본만 별도 검증 |
| /opt/vibetrading/incidents/collectors-20261015 | 20.0 KiB | 보존·증적 | 장애/복구 근거; 대용량 복사본만 별도 검증 |
| /opt/vibetrading/latency-audits | 136.0 KiB | 완료 작업 후보 | 고유 events/result와 복구 설정을 다른 위치에 검증 보존 후 정리 |
| /opt/vibetrading/latency-audits/crypto-e2e-1791988489784645020 | 40.0 KiB | 완료 작업 후보 | 고유 events/result와 복구 설정을 다른 위치에 검증 보존 후 정리 |
| /opt/vibetrading/latency-audits/crypto-e2e-1791988631395500772 | 44.0 KiB | 완료 작업 후보 | 고유 events/result와 복구 설정을 다른 위치에 검증 보존 후 정리 |
| /opt/vibetrading/latency-audits/crypto-e2e-1791988738275858741 | 48.0 KiB | 완료 작업 후보 | 고유 events/result와 복구 설정을 다른 위치에 검증 보존 후 정리 |
| /opt/vibetrading/releases | 192.0 KiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /opt/vibetrading/releases/capacity-20261006 | 40.0 KiB | 보존·호환 설정 | 현재 mirror/이전 참조 확인 전 삭제 불가 |
| /opt/vibetrading/releases/telegram-recovery-20261014 | 148.0 KiB | 보존·활성 설정 | runtime.yml/dashboard.yml이 실제 systemd/가드에서 사용됨 |
| /root/vibetrading | 1.98 MiB | 보존 | 서버 정식 소스·재빌드·config mount 원본 |
| /root/vibetrading-mm-state-review-20261007 | 10.04 MiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/084ae2bf7e402373 | 1.64 MiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/084ae2bf7e402373/scripts | 72.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/084ae2bf7e402373/strategy | 28.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/084ae2bf7e402373/tests | 48.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/084ae2bf7e402373/vibetrading | 1.21 MiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/0d2dc9fedf1a2e35 | 1.66 MiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/0d2dc9fedf1a2e35/scripts | 72.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/0d2dc9fedf1a2e35/strategy | 28.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/0d2dc9fedf1a2e35/tests | 52.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/0d2dc9fedf1a2e35/vibetrading | 1.23 MiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/5f2a10fd19f7728d | 1.65 MiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/5f2a10fd19f7728d/scripts | 72.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/5f2a10fd19f7728d/strategy | 28.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/5f2a10fd19f7728d/tests | 52.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/5f2a10fd19f7728d/vibetrading | 1.22 MiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/646c9f6a48f4a3bf | 1.64 MiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/646c9f6a48f4a3bf/scripts | 72.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/646c9f6a48f4a3bf/strategy | 28.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/646c9f6a48f4a3bf/tests | 48.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/646c9f6a48f4a3bf/vibetrading | 1.21 MiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/692487af63298f4a | 1.79 MiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/692487af63298f4a/config | 12.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/692487af63298f4a/docs | 48.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/692487af63298f4a/scripts | 104.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/692487af63298f4a/strategy | 28.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/692487af63298f4a/tests | 52.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/692487af63298f4a/vibetrading | 1.23 MiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/94f7f797e0a212c5 | 1.65 MiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/94f7f797e0a212c5/scripts | 72.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/94f7f797e0a212c5/strategy | 28.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/94f7f797e0a212c5/tests | 52.0 KiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading-mm-state-review-20261007/94f7f797e0a212c5/vibetrading | 1.22 MiB | 완료 작업 후보 | 과거 검토 checkout. 로컬 정식 소스·고유 결과 보존 후 |
| /root/vibetrading/config | 44.0 KiB | 보존 | 서버 정식 소스·재빌드·config mount 원본 |
| /root/vibetrading/deploy | 36.0 KiB | 보존 | 서버 정식 소스·재빌드·config mount 원본 |
| /root/vibetrading/strategies | 20.0 KiB | 보존 | 서버 정식 소스·재빌드·config mount 원본 |
| /root/vibetrading/strategy | 28.0 KiB | 보존 | 서버 정식 소스·재빌드·config mount 원본 |
| /root/vibetrading/tests | 452.0 KiB | 보존 | 서버 정식 소스·재빌드·config mount 원본 |
| /root/vibetrading/vibetrading | 1.37 MiB | 보존 | 서버 정식 소스·재빌드·config mount 원본 |
| /root/vibetrading/vibetrading/dashboard | 52.0 KiB | 보존 | 서버 정식 소스·재빌드·config mount 원본 |
| /root/vibetrading/vibetrading/fv_mm | 124.0 KiB | 보존 | 서버 정식 소스·재빌드·config mount 원본 |
| /root/vibetrading/vibetrading/fv_pilot | 64.0 KiB | 보존 | 서버 정식 소스·재빌드·config mount 원본 |
| /root/vibetrading/vibetrading/liquidity_mm | 140.0 KiB | 보존 | 서버 정식 소스·재빌드·config mount 원본 |
| /root/vibetrading/vibetrading/mm_state | 60.0 KiB | 보존 | 서버 정식 소스·재빌드·config mount 원본 |
| /root/vibetrading/vibetrading/providers | 256.0 KiB | 보존 | 서버 정식 소스·재빌드·config mount 원본 |
| /root/vibetrading/vibetrading/timefolio | 40.0 KiB | 보존 | 서버 정식 소스·재빌드·config mount 원본 |
| /srv/vibetrading-capacity | 65.53 GiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /srv/vibetrading-capacity/build-wheels-20261003 | 14.20 MiB | 재생성 가능 | 연구 의존성/빌드 캐시; requirements와 offline 필요성 확인 |
| /srv/vibetrading-capacity/crypto-market-data | 205.85 MiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/crypto-market-data/event_type=funding_rate | 2.41 MiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/crypto-market-data/event_type=open_interest | 2.38 MiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/crypto-market-data/event_type=order_book | 193.36 MiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/crypto-market-data/event_type=trade | 7.70 MiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/crypto-state | 7.34 GiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/crypto-state/backups | 4.0 KiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/crypto-state/health | 12.0 KiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/crypto-state/pre-minimal-fix-20261001 | 4.0 KiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/experiments | 1.62 GiB | 조건부·연구 | 연구 원본/파생본/실험 원장. 원격 보존 후 중복만 |
| /srv/vibetrading-capacity/experiments/fv-pilot-20261004 | 1.62 GiB | 조건부·연구 | 연구 원본/파생본/실험 원장. 원격 보존 후 중복만 |
| /srv/vibetrading-capacity/fv-research-20261004 | 25.13 GiB | 조건부·연구 | 연구 원본/파생본/실험 원장. 원격 보존 후 중복만 |
| /srv/vibetrading-capacity/fv-research-20261004/arcus_native | 569.18 MiB | 조건부·연구 | 연구 원본/파생본/실험 원장. 원격 보존 후 중복만 |
| /srv/vibetrading-capacity/fv-research-20261004/binance_native | 3.54 GiB | 조건부·연구 | 연구 원본/파생본/실험 원장. 원격 보존 후 중복만 |
| /srv/vibetrading-capacity/fv-research-20261004/binance_normalized | 386.36 MiB | 조건부·연구 | 연구 원본/파생본/실험 원장. 원격 보존 후 중복만 |
| /srv/vibetrading-capacity/fv-research-20261004/bitget_native | 3.16 GiB | 조건부·연구 | 연구 원본/파생본/실험 원장. 원격 보존 후 중복만 |
| /srv/vibetrading-capacity/fv-research-20261004/bybit_native | 2.19 GiB | 조건부·연구 | 연구 원본/파생본/실험 원장. 원격 보존 후 중복만 |
| /srv/vibetrading-capacity/fv-research-20261004/deps | 56.48 MiB | 재생성 가능 | 연구 의존성/빌드 캐시; requirements와 offline 필요성 확인 |
| /srv/vibetrading-capacity/fv-research-20261004/duckdb-temp | 10.50 GiB | 재생성 가능·사용 확인 | DuckDB spill. 열린 FD 0건 스냅샷 확인; 재실행 작업 없음도 확인 필요 |
| /srv/vibetrading-capacity/fv-research-20261004/grvt_native | 133.36 MiB | 조건부·연구 | 연구 원본/파생본/실험 원장. 원격 보존 후 중복만 |
| /srv/vibetrading-capacity/fv-research-20261004/slim | 2.35 GiB | 조건부·연구 | 연구 원본/파생본/실험 원장. 원격 보존 후 중복만 |
| /srv/vibetrading-capacity/fv-research-20261004/temp | 4.0 KiB | 조건부·연구 | 연구 원본/파생본/실험 원장. 원격 보존 후 중복만 |
| /srv/vibetrading-capacity/incident-20261006 | 130.96 MiB | 보존·증적 | 장애/복구 근거; 대용량 복사본만 별도 검증 |
| /srv/vibetrading-capacity/incident-20261006/market-audit | 23.52 MiB | 보존·증적 | 장애/복구 근거; 대용량 복사본만 별도 검증 |
| /srv/vibetrading-capacity/incident-20261006/market-historical-audit | 932.0 KiB | 보존·증적 | 장애/복구 근거; 대용량 복사본만 별도 검증 |
| /srv/vibetrading-capacity/incident-20261006/market-migration | 85.25 MiB | 보존·증적 | 장애/복구 근거; 대용량 복사본만 별도 검증 |
| /srv/vibetrading-capacity/incident-20261007 | 1.50 MiB | 보존·증적 | 장애/복구 근거; 대용량 복사본만 별도 검증 |
| /srv/vibetrading-capacity/incident-20261007/compactor-new | 220.0 KiB | 보존·증적 | 장애/복구 근거; 대용량 복사본만 별도 검증 |
| /srv/vibetrading-capacity/incident-20261007/compactor-old | 148.0 KiB | 보존·증적 | 장애/복구 근거; 대용량 복사본만 별도 검증 |
| /srv/vibetrading-capacity/lost+found | 16.0 KiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /srv/vibetrading-capacity/market-data | 5.00 GiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/market-data/event_type=bar_1m | 44.71 MiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/market-data/event_type=etf_nav | 596.38 MiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/market-data/event_type=funding_rate | 436.0 KiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/market-data/event_type=open_interest | 6.45 MiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/market-data/event_type=order_book | 2.46 GiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/market-data/event_type=participant_flow | 88.0 KiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/market-data/event_type=trade | 1.90 GiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/market-exports | 4.0 KiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /srv/vibetrading-capacity/market-state | 8.88 GiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/market-state/backups | 4.0 KiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/market-state/etf-csv-close-probe-1791897983608037096 | 324.0 KiB | 완료 작업 후보 | 격리된 마감 갱신 시험 sink; 결과 보존 후 |
| /srv/vibetrading-capacity/market-state/etf-csv-sync | 5.75 MiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/market-state/etf-mock | 252.44 MiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/market-state/health | 96.0 KiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/market-state/universe_cache | 20.0 KiB | 보존·운영 데이터 | 활성 DB/원장/시세 또는 bind mount 실체. WAL/SHM/토큰/lock 포함 |
| /srv/vibetrading-capacity/migration-retained-20261003 | 1.84 GiB | 조건부·백업 | 이동 전 보관본. 고유 행/원격 receipt/내용 동일성 검증 전 보존 |
| /srv/vibetrading-capacity/migration-retained-20261003/crypto_market_data.before-300gb-20261003 | 24.36 MiB | 조건부·백업 | 이동 전 보관본. 고유 행/원격 receipt/내용 동일성 검증 전 보존 |
| /srv/vibetrading-capacity/migration-retained-20261003/vibetrading-crypto-state.before-300gb-20261003 | 1.82 GiB | 조건부·백업 | 이동 전 보관본. 고유 행/원격 receipt/내용 동일성 검증 전 보존 |
| /srv/vibetrading-capacity/migration-retained-20261006 | 11.37 GiB | 조건부·백업 | 이동 전 보관본. 고유 행/원격 receipt/내용 동일성 검증 전 보존 |
| /srv/vibetrading-capacity/migration-retained-20261006/market-data | 3.39 GiB | 조건부·백업 | 이동 전 보관본. 고유 행/원격 receipt/내용 동일성 검증 전 보존 |
| /srv/vibetrading-capacity/migration-retained-20261006/market-exports | 2.07 GiB | 조건부·백업 | 이동 전 보관본. 고유 행/원격 receipt/내용 동일성 검증 전 보존 |
| /srv/vibetrading-capacity/migration-retained-20261006/market-state | 5.92 GiB | 조건부·백업 | 이동 전 보관본. 고유 행/원격 receipt/내용 동일성 검증 전 보존 |
| /srv/vibetrading-data/host-tools | 2.59 GiB | 보존 | VS Code 설치 및 기존 symlink 목적지 |
| /srv/vibetrading-data/host-tools/vscode-servers | 2.59 GiB | 보존 | VS Code 설치 및 기존 symlink 목적지 |
| /srv/vibetrading-data/host-tools/vscode-servers/.locks | 4.0 KiB | 보존 | VS Code 설치 및 기존 symlink 목적지 |
| /srv/vibetrading-data/host-tools/vscode-servers/Stable-07f806f999227108933c2e30515b26eecc1fda74 | 639.65 MiB | 조건부·구버전 | 현재 접속 버전 확인 후 미사용 버전만 정리; 이미 데이터 디스크로 이전 |
| /srv/vibetrading-data/host-tools/vscode-servers/Stable-16507d91be0dc1ce4f94c04d4d0430210b8685d9 | 670.32 MiB | 조건부·구버전 | 현재 접속 버전 확인 후 미사용 버전만 정리; 이미 데이터 디스크로 이전 |
| /srv/vibetrading-data/host-tools/vscode-servers/Stable-2a59476c9bfcb90b3ddc372c36762471b7dfad1c | 670.34 MiB | 조건부·구버전 | 현재 접속 버전 확인 후 미사용 버전만 정리; 이미 데이터 디스크로 이전 |
| /srv/vibetrading-data/host-tools/vscode-servers/Stable-7baac39ec3ca969dab74ac12c097f6a486e46593 | 670.29 MiB | 조건부·구버전 | 현재 접속 버전 확인 후 미사용 버전만 정리; 이미 데이터 디스크로 이전 |
| /srv/vibetrading-data/releases | 868.0 KiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /srv/vibetrading-data/releases/collector-recovery-20261015 | 864.0 KiB | 보존·현 복구본 | 이번 복구 build/이전 소스/주문 장부 snapshot; 현 rollback |
| /srv/vibetrading-data/releases/collector-recovery-20261015/source-before | 104.0 KiB | 보존·현 복구본 | 이번 복구 build/이전 소스/주문 장부 snapshot; 현 rollback |
| /srv/vibetrading-data/releases/collector-recovery-20261015/src | 116.0 KiB | 보존·현 복구본 | 이번 복구 build/이전 소스/주문 장부 snapshot; 현 rollback |
| /srv/vibetrading-data/releases/collector-recovery-20261015/tests | 468.0 KiB | 완료 작업 후보 | 완료한 테스트 복사본. 로그·현 rollback 보존 |
| /srv/vibetrading-data/runtime_data | 134.97 MiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /srv/vibetrading-data/runtime_data/crypto-delete-audit-20261002 | 108.82 MiB | 보존·삭제 증적 | 퇴역 데이터 원격 보관·삭제 감사 근거 |
| /srv/vibetrading-data/runtime_data/crypto_state | 24.0 KiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /srv/vibetrading-data/runtime_data/crypto_state/backups | 4.0 KiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /srv/vibetrading-data/runtime_data/crypto_state/health | 12.0 KiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /srv/vibetrading-data/runtime_data/crypto_state/pre-minimal-fix-20261001 | 4.0 KiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /srv/vibetrading-data/runtime_data/dashboard-monitor | 20.0 KiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /srv/vibetrading-data/runtime_data/liquidity_mm | 4.0 KiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /srv/vibetrading-data/runtime_data/logs | 4.0 KiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /srv/vibetrading-data/runtime_data/recovery-receipts | 2.10 MiB | 보존·증적 | 장애/복구 근거; 대용량 복사본만 별도 검증 |
| /srv/vibetrading-data/runtime_data/replay | 4.0 KiB | 보존·하위별 검토 | 여러 역할 혼재. 하위 행·활성 mount 확인 |
| /srv/vibetrading-data/runtime_data/reports | 23.81 MiB | 조건부·증적 | 최종 보고서/receipt 보존 후 중복 raw만 |
| /srv/vibetrading-data/runtime_data/reports/collector-recovery | 984.0 KiB | 조건부·증적 | 최종 보고서/receipt 보존 후 중복 raw만 |
| /srv/vibetrading-data/runtime_data/reports/market-coverage | 1.82 MiB | 조건부·증적 | 최종 보고서/receipt 보존 후 중복 raw만 |
| /srv/vibetrading-data/runtime_data/reports/market-coverage-20260925 | 64.0 KiB | 조건부·증적 | 최종 보고서/receipt 보존 후 중복 raw만 |
| /srv/vibetrading-data/runtime_data/scope-migration-20261013 | 160.0 KiB | 조건부·증적 | 최종 보고서/receipt 보존 후 중복 raw만 |
| /srv/vibetrading-data/testnet_latency_audits | 32.57 MiB | 완료 작업 후보 | 고유 events/result와 복구 설정을 다른 위치에 검증 보존 후 정리 |
| /srv/vibetrading-data/testnet_latency_audits/20261001 | 6.16 MiB | 완료 작업 후보 | 고유 events/result와 복구 설정을 다른 위치에 검증 보존 후 정리 |
| /srv/vibetrading-data/testnet_latency_audits/crypto-e2e-1791988489784645020 | 908.0 KiB | 완료 작업 후보 | 고유 events/result와 복구 설정을 다른 위치에 검증 보존 후 정리 |
| /srv/vibetrading-data/testnet_latency_audits/crypto-e2e-1791988489784645020/data | 12.0 KiB | 완료 작업 후보 | 고유 events/result와 복구 설정을 다른 위치에 검증 보존 후 정리 |
| /srv/vibetrading-data/testnet_latency_audits/crypto-e2e-1791988489784645020/state | 4.0 KiB | 완료 작업 후보 | 고유 events/result와 복구 설정을 다른 위치에 검증 보존 후 정리 |
| /srv/vibetrading-data/testnet_latency_audits/crypto-e2e-1791988631395500772 | 568.0 KiB | 완료 작업 후보 | 고유 events/result와 복구 설정을 다른 위치에 검증 보존 후 정리 |
| /srv/vibetrading-data/testnet_latency_audits/crypto-e2e-1791988631395500772/data | 12.0 KiB | 완료 작업 후보 | 고유 events/result와 복구 설정을 다른 위치에 검증 보존 후 정리 |
| /srv/vibetrading-data/testnet_latency_audits/crypto-e2e-1791988631395500772/state | 4.0 KiB | 완료 작업 후보 | 고유 events/result와 복구 설정을 다른 위치에 검증 보존 후 정리 |
| /srv/vibetrading-data/testnet_latency_audits/crypto-e2e-1791988738275858741 | 24.89 MiB | 완료 작업 후보 | 고유 events/result와 복구 설정을 다른 위치에 검증 보존 후 정리 |
| /srv/vibetrading-data/testnet_latency_audits/crypto-e2e-1791988738275858741/data | 12.0 KiB | 완료 작업 후보 | 고유 events/result와 복구 설정을 다른 위치에 검증 보존 후 정리 |
| /srv/vibetrading-data/testnet_latency_audits/crypto-e2e-1791988738275858741/state | 4.0 KiB | 완료 작업 후보 | 고유 events/result와 복구 설정을 다른 위치에 검증 보존 후 정리 |

[Python 파일별 목록](CLEANUP_PYTHON_FILES_2026-10-15.md) · [기계 판독 목록](evidence/collector_recovery_20261015/cleanup-review.json)
