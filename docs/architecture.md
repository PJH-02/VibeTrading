# VibeTrading 아키텍처 소유권 지도 (bounded)

2026-10-23 현재 범위는 [최소 재사용 계획](refactoring/vnext/VibeTrading_Minimal_Reuse_Plan_20261023.md)을 따릅니다. 기존 ETF/FV/liquidity runtime과 각 원장이 자신의 주문·계좌 동작을 계속 소유합니다. `ExecutionOwner`/`PortfolioLedger`의 Paper 구현은 재사용 대상이며 기존 실행 경로 전체를 대체하는 범용 권위가 아닙니다. 이번 단계에서 코드를 이동·삭제하거나 주문 코드를 새로 작성하지 않았습니다.

이 문서는 R02 문서 티켓에서 만든 소유권 지도입니다. 폭넓은 아키텍처 감사나 구현 완료 선언이
아니라, "무엇이 어느 트랙의 소유인지"를 문서 경계 기준으로만 확정합니다. 파일 수준 실현 목록,
다이내믹 임포트 인벤토리, 실서버 실측 결과는 이 문서가 제공하지 않습니다.

근거는 [v3 local execution status](status.md)와 세 개의 v3 권위 문서
([아키텍처·불변조건 명세](refactoring/vnext/VibeTrading_Architecture_Requirements_v3.md),
[개발자 구현 명세·수락 기준](refactoring/vnext/VibeTrading_Developer_Ticket_Spec_v3.md),
[최종 실행 계획](refactoring/vnext/VibeTrading_Refactoring_Final_Plan_v3.md))이며,
과거 구성 서술은 교체 전 루트 README의 기술을 따릅니다.

## 트랙 1 — 마지막 문서화된 배포: NATS 유지 P02 범위 릴리스

마지막으로 문서화된 배포는 P02 범위 릴리스이며, NATS JetStream 마이크로서비스 구성이
유지된 상태로 출시되었습니다. 교체 전 README가 기술한 구성은 다음과 같습니다
(역사적 설계 기술이며 현재 동작 실측이 아님).

| 구성요소 | 문서화된 역할 |
| --- | --- |
| Data Feed | 거래소 WebSocket/API 수집, NATS로 캔들 발행 |
| Signal Generation | NATS 캔들 구독 → 전략 실행 → 시그널 발행 |
| Order Manager | 시그널 구독 → 주문 생성·체결, 브로커 호출 |
| Risk Manager | 체결 구독 → P&L, 리스크 감시, Kill Switch |
| PostgreSQL / QuestDB | 트랜잭션 상태 / 시계열 저장 설계(상용 운용 실측 증거 없음) |

관련 제출물·설정 경로(주: 파일은 존재하지만 현재 서버 동작은 미검증):

- `docker-compose.yml`, `Dockerfile`, `Dockerfile.crypto`
- `deploy/nats.conf`, `deploy/crypto-profile.yml`, `deploy/ls-market-profile.yml`,
  `deploy/dashboard-compose.yml`, `deploy/release1-overlay.yml`
- `deploy/Dockerfile.crypto-ledger`, `deploy/Dockerfile.dashboard-timefolio`
- `deploy/vibetrading.service`, `deploy/vibetrading.cron.example`,
  `deploy/compose.env.example`, `deploy/maintenance.env.example`,
  `deploy/preflight-data-disk.sh`

출시 경계 기록은 [운영 전환 패키지](refactoring/cutover.md)와
[production_source_manifest.json](refactoring/vnext/production_source_manifest.json)이,
롤백 경계는 [복귀 절차와 보존 경계](refactoring/rollback.md)와 활성 스크립트
[scripts/refactoring/deploy_p02_schema.py](../scripts/refactoring/deploy_p02_schema.py)가
소유합니다. 롤백 스크립트는 외부 VN20 절차 전까지 이동·삭제 금지입니다.

## 트랙 2 — 로컬 NATS-free 후보 (부분 구현, 미배포)

로컬 후보는 NATS-free 설계로 진행 중이며 부분 구현 상태입니다. 소유권 분할은 v3 계약 문서를 따릅니다.

| 컴포넌트 용어(설계 분할) | 설계·계약 소유 |
| --- | --- |
| spool | [03 C 계약 증분](refactoring/vnext/03_contract_delta.md), [v3 아키텍처 명세](refactoring/vnext/VibeTrading_Architecture_Requirements_v3.md) |
| engine | [08 공통 실행·포트폴리오 계약](refactoring/vnext/08_execution_and_portfolio_contracts.md) |
| recovery / checkpoint | v3 아키텍처 명세의 불변조건 항목 |
| storage | [v3 아키텍처 명세](refactoring/vnext/VibeTrading_Architecture_Requirements_v3.md), [v3 최종 실행 계획](refactoring/vnext/VibeTrading_Refactoring_Final_Plan_v3.md) |
| order ledger | [08 공통 실행·포트폴리오 계약](refactoring/vnext/08_execution_and_portfolio_contracts.md) |

- 구현 진행 계획은 [04 증분 실행 계획](refactoring/vnext/04_implementation_plan.md)과
  [11 통합 VN/TX 실행 계획](refactoring/vnext/11_integrated_vn_tx_plan.md)을 따릅니다.

### 실제 소스 경로별 소유 지도 (R00 인벤토리 기반)

R00 저장소 인벤토리는 이미 존재하고 수락되었습니다: [repo_inventory.json](refactoring/vnext/repo_inventory.json) —
파싱된 Python 파일 **504개**, 다이내믹 임포트 지점 **24개**. 미해결 지점의 의미는 **UNKNOWN**입니다
(인벤토리나 소스 지도가 없다는 뜻이 아님). 인증된 후보 소스 지도에 따른 실제 경로→소유 주체는
다음과 같습니다.

| 소유 그룹 | 실제 소스 경로 |
| --- | --- |
| collector | `src/vibetrading/local_pipeline.py`, `src/vibetrading/local_runtime.py`의 `run_collector` |
| spool | `src/vibetrading/market_spool.py` |
| transport | `src/vibetrading/local_transport.py` |
| engine | `src/vibetrading/local_engine.py`, `src/vibetrading/hot_path.py` |
| recovery_checkpoint | `src/vibetrading/feed_recovery.py`, `src/vibetrading/market_checkpoint.py`, `src/vibetrading/local_runtime.py`의 `replay_checkpoint_startup` |
| storage | `src/vibetrading/local_storage.py`, `src/vibetrading/local_events.py`, `src/vibetrading/local_snapshot.py` |
| account_order_ledger | `src/vibetrading/portfolio_ledger.py`, `src/vibetrading/execution_owner.py`, `src/vibetrading/paper_execution.py` (**부분 구현**; 입증된 시장 데이터 전용(market-only) 복구와 별개 트랙입니다) |
| etf | `src/vibetrading/etf_trading.py`, `src/vibetrading/etf_journal.py` |
| modern strategy contract | `src/vibetrading/strategies/contract.py` (`vibetrading.strategies` public API) |
| research / historical strategy | `src/vibetrading/strategies/research/state/`, `src/vibetrading/strategies/legacy/fair_value/`, `src/vibetrading/strategies/legacy/candle/` |
| historical API adapters | `strategy/`, `strategies/`, `services/signal_gen/strategy_loader.py`, `src/vibetrading/strategy_contract.py`, `src/vibetrading/fv_mm/legacy/s*.py` |

R04에서 현대 이벤트 전략의 로더는 하나로 통합했습니다. 레거시 캔들 서비스·백테스트는
별도 계약을 유지하며 새 이벤트 전략으로 자동 승격하지 않습니다. 동일 이름의 상태 기반
S1~S4와 과거 fair-value S1~S4는 서로 다른 계산이며 구현을 합치지 않았습니다.

경계 표기: market-only 복구는 입증되었으나 계정·전략 복구(account_order_ledger 등)는 **불완전**하며,
여기서 전체 Paper/Live 준비를 추론할 수 없습니다. research 그룹 내 소유자들은 서로 다른 트랙이고
자동 교체 가능하지 않습니다. 위 표 미존재 경로의 소유는 이 문서가 주장하지 않습니다.

- 다이내믹 임포트 24개 지점 중 아직 미해결인 지점의 의미는 **UNKNOWN**이며, UNKNOWN 지점이
  겹치는 격리·아카이브 판단은 불가합니다 ([보존·격리 인덱스](archive/README.md)의 per-path 증거 조건 참고).

## 트랙 3 — 연구·백테스트 소유권

| 항목 | 소유 문서 |
| --- | --- |
| MM 상태 기반 전략 명세 | [전략 명세](mm_state/STRATEGY_SPEC.md), [상태 머신](mm_state/STATE_MACHINE_SPEC.md), [실행 시뮬레이터](mm_state/EXECUTION_SIM_SPEC.md), [위험·컴퓨팅 제한](mm_state/RISK_CONTROLS.md) |
| 백테스트 재현 절차 | [재현·실행 절차](mm_state/BACKTEST_PROTOCOL.md) |
| 실험 기록 | [실험 이력](EXPERIMENT_HISTORY.md), [내보내기 분석](MARKET_DATA_EXPORT_ANALYSIS.md) |
| 시장 데이터 통합 | [데이터 피드 가이드](DATA_FEED_API_GUIDE.md), [코인 시장 현황](CRYPTO_MARKET_IMPLEMENTATION.md) |

연구·백테스트 트랙은 서버 운용 트랙이 아닙니다. 실행은 안전 도움말 확인 외에
원문 문서의 조건부 절차를 따릅니다(예: `python scripts/run_backtest.py --help`).

## 알려지지 않음 (UNKNOWN, 이 문서가 판정하지 않는 것)

- 현재 서버 실상: 접촉 기록 없음, 상태 UNKNOWN. 서버 상태 색인은 R01의 권위 상태 색인이
  소유하며, 이 문서는 현재 동작을 판정하지 않습니다.
- PostgreSQL / QuestDB 현재 상용 운용 여부: UNKNOWN.
- 다이내믹 임포트 24개 지점 중 미해결 지점의 의미: UNKNOWN
  (인벤토리 자체는 [repo_inventory.json](refactoring/vnext/repo_inventory.json)에 있습니다).
- Paper / Live 준비: 입증 없음. 게이트는
  [12 준비 매치 행렬](refactoring/vnext/12_production_acceptance_matrix.md),
  [13 승격 생애주기](refactoring/vnext/13_strategy_deployment_lifecycle.md),
  [현시점 간극](refactoring/vnext/07_production_readiness_gap.md)이 소유합니다
  ([09 Paper 런타임 설계](refactoring/vnext/09_paper_trading_runtime_design.md)도 설계 경계만 다룸).
- 5거래소 venue/product/environment capability: 완성 아님
  ([10 문서](refactoring/vnext/10_venue_capability_completion.md)).
- 전체 회귀 PASS: 없음. 기준선 스냅샷(2026-10, 소스 99f25957)은 1531 passed / 1 failed /
  2 deselected이며, 알려진 실패는 체크포인트 이후 아직 열리지 않은 스풀 세그먼트를
  치환하는 권한 부재 간극으로 감사로 확인되었습니다
  ([status.md](status.md) 링크의 접수 경로 참고).

## 용량·성능 경계

2 vCPU / 3.8GiB는 **목표 명세**입니다. 실측 서버 통과 기록이 아니며, 이 문서는 측정 결과로
표시하지 않습니다. 용량·검증·서버 전환 게이트의 판정 절차는
[05 검증·용량·서버 전환 Gate](refactoring/vnext/05_validation_and_cutover.md)를 따릅니다.
측정 대상 판정 절차는 나중 O/VN 티켓에서 별도 제공되어야 합니다.

## 문서·도구 소유권

- 권위 상태(JSON): [tickets_production.json](refactoring/vnext/tickets_production.json),
  [stage_status.json](refactoring/vnext/stage_status.json).
- 계층 도구: `python3 scripts/dev/status.py --root . --json`.
- P01~P02 계약·결정 기록: [계약](refactoring/contracts.md),
  [결정 기록](refactoring/decisions.md), [구현 결정](refactoring/implementation_decisions.md),
  [작업 보드](refactoring/execution_board.md).
- 아키텍처 검토 원천: [02 아키텍처 검토](refactoring/vnext/02_architecture_review.md),
  [01 현재 상태·기준선 차이](refactoring/vnext/01_current_state_delta.md),
  [충돌 등록부](refactoring/vnext/conflict_register.md).

## 명령 경계

이 문서에 나열하는 것은 안전 확인(도움말·상태)용 명령 예시뿐이며, 이 문서가 실행 권한을
부여하는 것은 아닙니다.

```bash
python3 scripts/dev/status.py --root . --json
python3 -m vibetrading.cli --help
```

start·deploy·live 계열 명령은 이 문서 어디에도 없으며, 전환은 외부 VN20이고
[운영 전환 패키지](refactoring/cutover.md)의 별도 절차와 승인을 필요로 합니다.
