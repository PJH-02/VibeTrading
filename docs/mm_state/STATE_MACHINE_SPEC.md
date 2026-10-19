# 상태와 전이 명세

## 주문

`Intent → IN_FLIGHT → ACTIVE → PARTIAL → FILLED`가 정상 경로다. 취소 의도는 IN_FLIGHT/ACTIVE/PARTIAL에서 CANCEL_PENDING으로 전이한다. CANCEL_PENDING에서도 effective cancel 전 fill이 가능하다. 이후 CANCELLED 또는 FILLED로 terminal 처리한다. crossing/stale arrival은 각각 POST_ONLY_REJECTED/REJECTED_STALE다.

PaperOrder는 intent/born, sent_ts, arrival, ack_ts, active_ts, cancel_requested_ts, cancel_at, first_fill_ts, leaves, queue, reason_cancel, signal_snapshot, inventory_snapshot을 보관한다. SQLite orders/lifecycle/fills/markouts에서 실행 결과를 추적한다. live Working은 intent·active timestamp·확인된 leaves·signal snapshot을 보관하고 exchange terminal confirmation 또는 REST reconciliation으로 예약을 해제한다. 불명확한 제출은 UNKNOWN+venue halt이며 blind retry하지 않는다.

## 취소

기본 ACTIVE 최소 수명1.5초, 최대 TTL15초, reprice2ticks, resize20%다. 작은 가격·수량 차이로 매 tick cancel하지 않는다. pre-ACK minor change는 취소 근거가 아니다. 명확한 방향 반전, 위험 상태, entry/reduce 역할 전환은 최소 수명을 우회한다. 주문이 부분 체결되어 재고가 생기면 남은 entry 노출을 취소하고 실제 재고의 감축을 우선한다.

## 전략

S1: NEUTRAL ↔ OFI_ONSET → OFI_PERSISTENT. onset/persistence/acceleration은 관측 통계이며 시간 수평선의 확정 가격 예측이 아니다.

S4: STRONG_SELL / SELL / NEUTRAL / BUY / STRONG_BUY. train-only threshold로 strength를 정규화한다. 동일 imbalance가 오래 지속된 표본은 독립 onset이 아니다.

S2: BINANCE_LEADS / LOCAL_IDIOSYNCRATIC / COMMON_SHOCK / STALE_OR_UNKNOWN. stale reference·source skew·stationarity 불충족·baseline drift면 score0. TP1·TP2·time/risk 감축은 position context와 실제 로컬 이익을 통해 결정한다.

S3: NEUTRAL → PRESSURE → ABSORPTION → SUPPLY_EXHAUSTED 또는 AGGRESSOR_EXHAUSTED. 흡수 없이 breakout 상태를 만들지 않는다. episode 30초 초과·unknown direction·feed reset은 NEUTRAL/UNKNOWN으로 초기화한다. 실제 trade 이후 수량을 episode에 추가하고 rolling window 누적량을 반복 합산하지 않는다.

## 공통 상태

Volatility/liquidity: INVALID / NORMAL / THIN_BOOK / JUMP_RISK. no-trade는 inactive/unknown이지 자동 calm이 아니다. jump는 극단 OBI·OFI와 level gap 또는 spread 확대의 결합으로 분류한다.

Inventory: flat / long / short, actual filled inventory만 사용한다. 후보 주문+in-flight leaves는 별도 최악 노출로 예약한다. 감축은 reduce-only이며 방향 신호 반전이 기존 포지션을 즉시 반대 포지션으로 바꾸는 이유가 아니다.

live `order_state`/`policy_cancel`, paper `pilot_lifecycle`, `quote_policy_audit`, `state_samples`, replay `state_counts`에서 이유·시간·상태를 기록한다. 청산이 안 된 재고, missing markout, unknown 주문 상태를 0으로 보정하지 않는다.
