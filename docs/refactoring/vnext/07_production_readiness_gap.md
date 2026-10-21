# Production 07 — 목표 확장과 현재 간극

2026-10-20. 이번 요청은 **로컬 구현·격리 시험 허용**, 서버 배포·외부 mutation·기존 데이터 삭제·NATS 서비스 제거 불허다. 기존 01~06·tickets·manifest·원본 P/G 이력은 보존한다.

## 변경된 목표

NATS-free 공유 시장 상태 위에서 여러 전략이 같은 실제 시세를 읽고 공통 owner/risk/portfolio를 거쳐 **로컬 Paper**를 실행한다. 전략 수익성·알파/FV 연구, 파라미터 탐색·백테스트 서버 구축은 제외한다. live 승격은 strategy/config/계좌/product/environment별 별도 명시적 승인이다.

| 영역 | 현재 확인한 구현 | 생산 목표와 부족분 |
|---|---|---|
| 시장/저장 | 기존 Collector, spool/UDS, HotPathStore, Parquet/cloud | VN02~09 isolation/reconnect/checkpoint/GC와 용량 실증 필요 |
| 전략 | StrategyModule/SharedStrategyRunner의 Signal 호환·shadow | 등록/lifecycle, action/cancel/replace, 공통 Paper dispatch·bounded fairness 필요 |
| 실행 | offline cash/spot owner, ETF Mock/Mock2 journal/permit | 계좌 scope 독점·다중 전략 귀속·Perp 위험/portfolio, 실제 adapter 계약 필요 |
| Paper | legacy matcher와 최신 fv_pilot matcher | 최신 causal/depth 알고리즘 재사용. 물리 분리된 durable 계좌·재시작·입력/fill dedup·fee/funding/multiplier 부족 |
| Venue | Binance Spot; Arcus/GRVT 부분 broker; 별도 Bybit/GRVT testnet 도구 | 5 Perp venue × product × environment × capability별 독립 판정 |
| UI | status/trade, durable inbox/outbox·local facts | 전략/계좌 Paper·실제 PnL, owner 제어, 승격 권한 분리 필요 |
| 검증 | 고정 v5 전체/v6 부분 fixture | 현재 candidate 전체 회귀·실제 시세 Paper·2vCPU/3.8GiB·장기 outage 미검증 |

## 기존 계획과 충돌하는 새 요구사항

| ID | 과거 경계 | 이번 명시적 변경 | 보존 조건 |
|---|---|---|---|
| PR-C01 | VN13은 shadow only | Production Strategy Runtime와 Paper dispatch까지 확대 | 기존 generic inactive 전략 자동 기동0; on_event Signal 호환 |
| PR-C02 | generic owner는 offline cash/spot | 검증된 별도 product policy로 cash와 Perp 분리 지원 | 기존 ETF/legacy cash 경로 성급한 교체0; live 기본 차단 |
| PR-C03 | TOSS broker capability audit 존재 | **TOSS DATA ONLY**, 신규 Paper/Live account 등록·routing 거절 | 데이터 collector/history/reference price는 유지; legacy 무차별 삭제0 |
| PR-C04 | Bybit/Hyperliquid는 retired feed | Perp 실행 인프라의 최종 지원 대상 | 현재 Collector scope 자동 확대0; connector 구현과 수집 활성 분리 |
| PR-C05 | A2/A3는 후순위 | VN01 다음 조기 latency Gate | A1 첫 후보 유지. freshness/TTL 위반 원인이 spool이면 별도 A2 비교 |
| PR-C06 | PnL은 기존 ETF 중심 | 전략 virtual/account physical, Paper/actual, fees/funding/margin | 관측 불명·외부 차이를 임의 전략에 할당0 |

현재 local HEAD는 여전히 `6443eb9`다. 작업 전 source set `1b0dbadca…`를 보존하며 변경 후 새 production manifest를 작성한다. 과거 SHA를 맞추는 reset/clean/stash/덮어쓰기는 금지한다. 10월20일 서버 표본은 과거 01 문서의 관측이며 이번 구현의 성능·활성 증거가 아니다.

현재 실제 구현/시험 결과는 [14](14_agent_execution_handoff.md), 전체 상태는 [12](12_production_acceptance_matrix.md)에 누적한다. 문서 작성만으로 PAPER_READY/Connector Complete를 선언하지 않는다.
