# VibeTrading

이벤트 드리븐 알고리즘 트레이딩 시스템 리포지토리입니다. 연구·백테스트·모의 운용이 중심이며,
실서버 배포 상태는 아래 "배포 이력과 로컬 후보"에서 확인하세요. 이 README는 2026-10 R02
문서 티켓에서 대체되었고, 검증되지 않은 실행·배포·실거래 지시를 담지 않습니다.

## 소스 구조와 로컬 설치

정식 패키지는 `src/vibetrading/`에 있습니다. Python 3.12 이상과 기존 `requirements.txt` 의존성이
준비된 개발 환경에서 editable 설치를 한 뒤 기존 진입점을 사용합니다.

```bash
python3 -m pip install --no-deps -e .
python3 main.py --help
```

R03에서 패키지 루트를 이동했고, R04에서 전략 로딩을 `vibetrading.strategies.load_strategy`로
통합했습니다. 과거 캔들 전략과 서로 다른 S1~S4 구현은 `strategies/legacy/` 및
`strategies/research/state/`로 분리하고 기존 import는 호환 어댑터로 보존합니다.
R05에서 선택된 C3/KP/KD 순수 판단 계산을 `strategies/kr/`로 분리했습니다.
데이터 준비·계좌·주문 처리는 기존 ETF Runtime에 유지하며, 다른 도메인은 해당 VN/TX 티켓 범위입니다.

## 현재 리팩토링 권위

실행 계획과 범위는 [최종 계획](docs/final/01_FINAL_PLAN.md), 구현·테스트는
[개발 티켓](docs/final/02_DEVELOPER_TICKETS.md), 안전 계약은
[아키텍처 요구사항](docs/final/03_ARCHITECTURE_REQUIREMENTS.md)을 따릅니다.
과거 완료 사실은 [불변 manifest](docs/final/04_COMPLETED_MANIFEST.json), 현재 상태와
의존성은 [작업 상태](docs/final/05_TASK_STATUS.json)가 소유합니다.
[상태 안내](docs/status.md)는 이 다섯 문서로 연결하는 색인입니다.

기존 v3/Minimal Reuse 및 VN/TX/R/O 문서·상태는 읽기 전용 역사 자료입니다.
`python3 scripts/dev/status.py --root . --json`은 OPS-01 전환 전까지 구 v3 투영이므로
신규 티켓 READY 판단에 사용하지 않습니다. 기존 수락 코드를 재개발하지 않습니다.
최종 목표는 역할별 Flat `src/`이며 현재 패키지 이동 완료를 뜻하지 않습니다.

## 배포 이력과 로컬 후보

| 항목 | 현재 판정 |
| --- | --- |
| 마지막 문서화된 배포 | P02 범위 릴리스. NATS JetStream 마이크로서비스 구성이 그대로 유지된 상태로 출시됨 |
| 로컬 NATS-free 후보 | 부분 구현, 미배포. spool·engine·recovery·checkpoint·storage·order ledger 소유권 지도는 [아키텍처 지도](docs/architecture.md) 참고 |
| PostgreSQL / QuestDB 서술 | 구버전 README의 범용 DB 구성과 데이터 흐름은 역사적 설계 기술입니다. 현재 상용 운용 실측 증거는 없습니다 |
| v3 5거래소 capability | 완성 아님. [10 Venue/Product/Environment capability](docs/refactoring/vnext/10_venue_capability_completion.md)는 계획·계약 경계만 다룹니다 |
| Paper / Live 준비 | 입증되지 않음. [12 준비 매치 행렬](docs/refactoring/vnext/12_production_acceptance_matrix.md), [13 전략 승격 생애주기](docs/refactoring/vnext/13_strategy_deployment_lifecycle.md), [현시점 간극](docs/refactoring/vnext/07_production_readiness_gap.md) 참고 |

- 출시 경계 기록은 [운영 전환 패키지](docs/refactoring/cutover.md)와
  [production_source_manifest.json](docs/refactoring/vnext/production_source_manifest.json)을 참고하세요.
- 아키텍처 소유권 자체는 [docs/architecture.md](docs/architecture.md)로 분리했습니다.
- 과거 충돌 기록은 [conflict_register.md](docs/refactoring/vnext/conflict_register.md)에 보존되며, 현재 충돌 처리는 최종 5문서를 따릅니다.

## 현행 전략·모의 운용 (변경 없음)

- C3/KP/KD 전략 기반 KIS 모의계좌 ETF 자동매매는
  [수집 대상 정리와 C3/KP/KD 모의 ETF 운용](docs/ETF_MOCK_COLLECTION_SCOPE_2026-10-13.md)을 따릅니다.
- KIS Mock / Mock2 라우팅은 변경 없음. ETF별 모의계좌 선택과 권한 거절 가정 손익은
  [ETF별 모의계좌 선택 문서](docs/DUAL_MOCK_AND_PERMISSION_PNL_2026-10-14.md)를 참고하세요.
- 토스(Toss)는 데이터 전용(DATA_ONLY)이며 주문 경로가 아닙니다.
- 티커별 공유 CSV 입력과 장 마감 갱신 규칙은
  [공유 CSV 운영](docs/TICKER_CSV_COLLECTION_2026-10-13.md)을 따릅니다.
- 실매매 제외 등 운용 예외(예: 069500)는
  [테스트 실매매 제외 문서](docs/TRADE_TEST_EXCLUSION_2026-10-14.md)를 따릅니다.
- 웹 운용 대시보드: [개인 대시보드](https://vibetrading-monitor.mwm3870.chatgpt.site).
  조회 서비스 운영 방법은 [웹 대시보드 문서](docs/WEB_DASHBOARD.md)에 있습니다.

## 연구·백테스트·과거 운용 문서 (역사적/조건부 참고)

- 잔여 주문흐름 기반 선택적 MM의 Bybit 테스트넷 전략은
  [구현·운영 명세](docs/LIQUIDITY_MM_BYBIT_TESTNET.md)에 있습니다. 초기 프로필은 검증 전
  테스트넷 실험이며 자동 운용은 활성화하지 않았습니다. 시작·실행 명령은 원문 문서만 참고하고,
  이 README에는 복사하지 않습니다.
- 2026-10-07 S1–S4 상태 기반 개정: [전략 명세](docs/mm_state/STRATEGY_SPEC.md)와
  [재현·실행 절차](docs/mm_state/BACKTEST_PROTOCOL.md). 기본 정상 진입·청산은 maker이며
  새 state 모델을 전제로 합니다. 서버에는 격리 검토 소스만 복사되었고 자동 운용은 활성화하지 않았습니다.
- 구성 요소 명세: [실행 시뮬레이터](docs/mm_state/EXECUTION_SIM_SPEC.md),
  [상태 머신](docs/mm_state/STATE_MACHINE_SPEC.md), [위험·컴퓨팅 제한](docs/mm_state/RISK_CONTROLS.md).
- 실험·백테스트·지연 측정 요약은 [실험 이력](docs/EXPERIMENT_HISTORY.md),
  시장 데이터 내보내기 분석은 [내보내기 분석](docs/MARKET_DATA_EXPORT_ANALYSIS.md).
- 암호화폐 수집·Bybit 테스트넷 적용 현황은
  [코인 시장 문서](docs/CRYPTO_MARKET_IMPLEMENTATION.md), LS증권 선물 수집 준비는
  [LS 수집 준비 문서](docs/LS_MARKET_DATA_IMPLEMENTATION_2026-09-27.md).
- FV(v4 독립 전략)는 [테스트넷 운용 이력](docs/FV_MM_V4_TESTNETS.md)과
  [실거래 호가 기반 모의 거래 이력](docs/FV_PILOT_MAINNET.md)으로 이력만 남깁니다.
- 시장 데이터 어댑터 통합 가이드는 [데이터 피드 가이드](docs/DATA_FEED_API_GUIDE.md).

## 과거 v3 근거 (읽기 전용)

아래 문서는 과거 수락·복구 증거이며 현재 계약이나 상태 권위가 아닙니다.

- [v3 아키텍처 이력](docs/refactoring/vnext/VibeTrading_Architecture_Requirements_v3.md)
- [v3 개발 티켓 이력](docs/refactoring/vnext/VibeTrading_Developer_Ticket_Spec_v3.md)
- [v3 실행 계획 이력](docs/refactoring/vnext/VibeTrading_Refactoring_Final_Plan_v3.md)
- [과거 VN/TX 및 R/O 상태](docs/refactoring/vnext/tickets_production.json)
- [과거 단계 분류](docs/refactoring/vnext/stage_status.json)
- [R00 소스 인벤토리](docs/refactoring/vnext/repo_inventory.json)

복귀 경계는 [복귀 절차 문서](docs/refactoring/rollback.md)와 활성 P02 롤백 스크립트
[scripts/refactoring/deploy_p02_schema.py](scripts/refactoring/deploy_p02_schema.py)가 유지됩니다.
이 스크립트는 외부 VN20 절차 전까지 이동·삭제 대상이 아닙니다.

## 저장·보존

- 보존·격리 인덱스(이 티켓에서 파일 이동·삭제는 없었음): [docs/archive/README.md](docs/archive/README.md)
- 수락 전인 로컬 삭제 후보 목록: [2026-10-19 삭제 후보 목록](docs/CLEANUP_DELETE_LIST_20261019.md)

## 확인 가능한 명령

이 README는 실행 승인이나 소프트웨어 권한 부여 문서가 아닙니다. 아래는 문서 구조 파악에 쓰는
도움말·상태 확인 명령 예시뿐이며, start/deploy/live 계열은 각 원문 문서의 승인 조건과 절차를
따르고 이 README에 복제하지 않습니다.

```bash
python3 scripts/dev/status.py --root . --json
python3 -m vibetrading.cli --help
python scripts/run_backtest.py --help
python -m vibetrading.liquidity_mm --help
```

CLI 모듈은 [src/vibetrading/cli.py](src/vibetrading/cli.py)입니다. 백테스트·MM 실행 절차는
각각 [재현·실행 절차](docs/mm_state/BACKTEST_PROTOCOL.md)와
[MM 구현·운영 명세](docs/LIQUIDITY_MM_BYBIT_TESTNET.md) 문서의 조건부 절차를 따릅니다.
