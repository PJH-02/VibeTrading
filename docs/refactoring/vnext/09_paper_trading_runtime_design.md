# Production 09 — 실시간 Paper 설계와 검증 경계

실제 MarketState/strategy/action/owner/risk/portfolio를 재사용하고 마지막 mutation adapter만 local paper로 바꾼다. Paper의 권위 DB는 명시적 paper environment 경로이며 실제 journal과 공유하지 않는다. 전략별 Paper Engine/DB를 만들지 않고 계좌 owner가 전략 namespace를 관리한다.

## 재사용과 수정

`fv_mm/legacy/paper_simulation.py`는 조사했으나 그대로 운영화하지 않는다. 최신 `fv_pilot/simulation.py`의 schedule/advance/queue_at/_arrive/fill/sweep/_trade와 `tests/test_mm_state_revision.py`의 인과성/depth 시험을 독립 oracle로 사용한다. PilotRunner/Journal/PublicFeeds는 알파·network·live/Paper 혼합·재시작 금지 가정이 있어 공통 runtime에서 import하지 않는다.

새 좁은 `paper_execution.py`는 주문도착/취소효력 시간·book/trade 유효범위·남은 visible liquidity·maker queue를 처리한다. 공통 portfolio writer에 원자 fill 반영을 요청한다. 새 범용 event broker/백테스트 엔진은 만들지 않는다. 경로·심볼은 TX 티켓에서 구현 전 고정한다.

## 체결 계약

- ns receipt time 기준으로 **다음 book을 적용하기 전에** 이전 구간의 도착/취소를 처리한다. backfill/replay가 가상 실시간 주문을 새로 체결시키지 않는다.
- maker는 도착 시 post-only crossing이면 reject. 유효 depth 밖의 queue estimate는 UNKNOWN이며 단순 quote touch로 체결하지 않는다. observed aggressor trade volume을 여러 own order가 중복 소비하지 않는다.
- taker는 도착 시 유효한 visible L2와 protection price까지만 sweep한다. 동일 book generation에서 앞선 simulated taker가 쓴 depth를 다시 사용하지 않는다. 부족한 IOC 잔량은 명시 cancel/expire한다.
- cancel request와 effective 시각 사이 fill은 허용한다. reduce-only는 계좌/strategy의 확정 position과 잔량으로 재검사한다. replace는 이전 잔량·UNKNOWN·cancel-finality를 검증하며 blind new-order로 바꾸지 않는다.
- multiplier·settlement/collateral currency·maker/taker fee/rebate·명시 관측된 funding payment를 회계에 반영한다. 실제 queue position·시장충격·숨은 유동성·exchange matching latency는 관측하지 못한 가정으로 보고한다.

## 내구성·상한

order/attempt/evidence/reserve는 arrival 전에 COMMIT, fill/order 잔량/현금·마진/virtual·physical·fee와 dedup receipt는 한 transaction에 확정한다. transaction 실패는 matcher in-memory 상태에도 반영하지 않는다. restart는 indexed active orders와 마지막 input cursor를 복원하고 적용된 이벤트를 중복 체결하지 않는다. uncertain cursor이면 Paper도 closed/reconcile 상태다.

active orders, events/receipts, latency schedules, book depth, per-strategy actions와 pending fills는 유한 상한을 가진다. 과거 terminal order 전체를 tick마다 scan하지 않는다. 원장/receipt 한도에 도달하면 새 위험을 거절하고 보존/회수는 별도 검증 계약으로 처리한다.

## E2E 판정

Dummy Strategy가 기존 StrategyModule 계약으로 등록되어 시세→snapshot→Signal/Action→owner/reservation→Paper lifecycle→portfolio/PnL·UI까지 연결되는 것을 TX05/TX11이 검증한다. 계좌/venue mutation spy=0은 필수다. 합성 fixture PASS와 실제 live-market Paper 지속 실행은 별도 상태로 기록한다. 현재 readiness는 [12](12_production_acceptance_matrix.md)를 따른다.

## 현재 구현 범위와 열린 차이

`PortfolioLedger`·`PaperExecution`·`PaperStrategyRuntime`이 추가되었다. 현재 경제 지원은 동일 통화 cash 및 명시 isolated linear simulation이다. inverse/hedge/cross-currency·실제 거래소 margin 검증은 미지원이다. 매칭은 post-only GTC maker와 보호 가격이 있는 IOC taker를 지원하며, 일반 taker GTC/FOK/안전한 replace는 명시 거절한다.

matcher state와 fill/회계는 같은 SQLite transaction으로 commit한다. 외부 transaction 안에서 matcher memory를 먼저 공개하지 않는다. native sequence 역행은 거절하고 opaque/누락 sequence에서 사용한 depth를 임의 초기화하지 않는다. gap 이후에는 다른 입증된 generation의 snapshot이 필요하다. 공유 runtime은 engine REPLAY/INVALID/WARMING·backfill 입력에서 matching/claim/주문을 발생시키지 않는다.

연속 durable input cursor·full C/H restart 및 여러 계좌의 원자 복구는 아직 입증하지 않았다. 현재 matcher receipt/주문 상태 복원과 입력 dedup을 전체 복구 완료로 표시하지 않는다. receipt는 run lifetime 유한 한도이며, matcher state 직렬화·commit 비용은 실제 burst/장기 latency gate에서 측정해야 한다. callback 시간 측정은 사후 측정이므로 hard isolation gate는 BLOCKED다. `PaperExecutionGroups`는 immutable 계획·관측을 같은 원장에 기록하지만 child 주문 발행/자동 재제출/hedge 권위는 갖지 않는다.
