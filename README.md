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

## 현재 상태 (v3 리팩토링 기준)

2026-10-23 범위 수정: 기존 거래소 실행·주문·계좌·클라우드 전송을 보존하면서 NATS-free 데이터/저장/복구와 전략 파일별 Signal·기존 Paper·동일 계좌 안전성만 보완합니다. [최소 재사용 계획](docs/refactoring/vnext/VibeTrading_Minimal_Reuse_Plan_20261023.md)이 기존 범용 Trading Platform 확대 요구보다 우선합니다. 이 단계는 제품 변경 없이 조사·계획만 수행했습니다.

상태의 유일한 요약은 [v3 local execution status](docs/status.md)입니다. 유지보수용 계층 결과는
아래 명령으로 다시 만듭니다.

```bash
python3 scripts/dev/status.py --root . --json
```

- 스냅샷 기준: 2026-10-23, 소스 99f25957(이 티켓 base). 아래 수치는 스냅샷 값입니다.
  변하는 READY/현재 카운트는 [tickets_production.json](docs/refactoring/vnext/tickets_production.json),
  [stage_status.json](docs/refactoring/vnext/stage_status.json)과
  파생 요약 [v3 local execution status](docs/status.md)를 따르며, 이 README는 티켓 상태를 복제하지 않습니다.
- R00 저장소 인벤토리는 이미 존재하며 수락되었습니다: [repo_inventory.json](docs/refactoring/vnext/repo_inventory.json) —
  파싱된 Python 파일 504개, 다이내믹 임포트 지점 24개. 미해결 지점의 의미는 **UNKNOWN**입니다
  (인벤토리나 소스 파일 지도가 없다는 뜻이 아닙니다). 문서·상태 색인은 R01의 권위 산출물인
  [R01 권위 상태 색인](docs/tickets/R01-authority-status.md)을 참고하세요.
- 신규 제품 기준선(격리 리눅스): **1531 passed / 1 failed / 2 deselected**. 전체 회귀 PASS가 아닙니다.
  알려진 실패는 체크포인트 이후 아직 열리지 않은 스풀 세그먼트를 교체해도 복구가 진행되는 결함이며,
  감사로 확인되었습니다. 준비 완료로 읽지 마세요.
- VN/TX 라인 진행 요약은 [아키텍처 지도](docs/architecture.md)의 스냅샷을 참고하고,
  세부 계획은 [11 통합 VN/TX 실행 계획](docs/refactoring/vnext/11_integrated_vn_tx_plan.md)을 따릅니다.
- VN20 실서버 전환·삭제는 외부 실행입니다. 이 리포 내부 티켓·에이전트가 수행 대상이 아닙니다.
- 현재 서버에는 접촉 기록이 없고, 실서버 현재 상태는 UNKNOWN입니다.

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
- 문서·코드·증거 충돌은 [conflict_register.md](docs/refactoring/vnext/conflict_register.md)에 등록됩니다.

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

## v3 리팩토링 진입점

세 개의 v3 권위 문서는 이번 티켓에서 건드리지 않으며(untouched), 계약·수락 기준의 원천입니다.

- [v3 아키텍처·불변조건 명세](docs/refactoring/vnext/VibeTrading_Architecture_Requirements_v3.md)
- [v3 개발자 구현 명세·티켓 수락 기준](docs/refactoring/vnext/VibeTrading_Developer_Ticket_Spec_v3.md)
- [v3 최종 리팩토링 실행 계획](docs/refactoring/vnext/VibeTrading_Refactoring_Final_Plan_v3.md)

머신이 읽는 권위 상태(canonical JSON):

- [tickets_production.json](docs/refactoring/vnext/tickets_production.json)
- [stage_status.json](docs/refactoring/vnext/stage_status.json)

단계 문서: [01 현재 상태·기준선 차이](docs/refactoring/vnext/01_current_state_delta.md),
[02 아키텍처 검토](docs/refactoring/vnext/02_architecture_review.md),
[15 단계별 상태와 다음 작업](docs/refactoring/vnext/15_stage_status_and_next_steps.md).

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
