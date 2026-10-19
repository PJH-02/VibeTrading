# Maker 실행 시뮬레이터 명세

구현: `vibetrading/fv_pilot/simulation.py`. public pilot는 서명키·주문 클라이언트가 없는 별도 경로다. `submit_intent`는 가상 ledger에 의도만 기록한다. 테스트넷 Runner만 명시적인 `--trade-testnet`일 때 외부 주문을 제출한다.

## 인과적 시간 순서

모든 시간은 한 receipt monotonic clock을 사용한다. heap의 유효 시각으로 BOOK/GAP → ARRIVE → TRADE → CANCEL을 처리한다. timer가 뒤늦게 깨어나도 이전 arrival을 이후 호가로 판정하지 않는다. Book의 dictionary는 enqueue 당시 복사하므로 mutable object의 미래 상태를 보지 않는다. 이미 처리한 시각보다 과거 이벤트를 삽입하면 실패한다.

위 tie 순서는 **enqueue된 이벤트**에 적용한다. 스트림에 아직 도착하지 않은 동시 시각 이벤트를 미리 알 수 없으며, 동일 timestamp의 수신 순서가 영향을 줄 수 있다. 이벤트별 receipt ordering을 유지하고 이 한계를 기록한다.

Cancel effective=max(arrival, cancel request+venue cancel latency)다. 제출 직후 취소 의도를 만들었더라도 effective cancel 전까지 주문은 체결 가능하다. submit/cancel latency가 다를 수 있다. ACTIVE 최소 수명은 ACK/arrival 이후부터 잰다. cancel ACK만으로 terminal 처리하거나 예약 수량을 해제하지 않는다.

## Post-only와 queue

maker Buy≥arrival ask 또는 maker Sell≤arrival bid는 POST_ONLY_REJECTED다. bid<price<ask의 inside-spread 개선 주문은 queue ahead=0일 수 있다. 이는 crossing/taker 주문이 아니다. 실제 Bybit PostOnly는 즉시 체결될 주문을 취소한다. [Bybit 공식 주문 문서](https://bybit-exchange.github.io/docs/v5/order/create-order).

도착 시 그 가격의 표시 잔량과 앞선 자기 가상 주문 잔량이 queue ahead다. 관측 depth 밖이면 queue=None이며 빈 queue로 가정하지 않는다. public opposite-side trade가 같은 가격이면 queue를 먼저 소모하고 남는 거래량으로 부분 체결한다. 알려진 queue의 가격을 엄격하게 통과한 trade는 queue가 소진된 근거로 사용하되 체결량을 실제 해당 trade quantity로 제한한다.

동일 가상 계좌의 여러 주문에 public 거래량을 반복 재사용하지 않는다. 부분 체결은 lot으로 내림하고 실제 leaves 및 실제 포지션으로 reduce-only를 제한한다. unknown aggressor, arrival 당시 source보다 과거 trade, stale/invalid book은 maker fill 근거가 아니다. 단순 touch·mid 변화·호가 수량 감소만으로 체결하지 않는다.

`conservative`: 취소로 추정되는 호가 감소에 queue credit을 주지 않는다. `proportional_cancel`: 같은 가격의 관측 감소에서 이미 관측된 trade를 빼고 queue 점유 비율만큼 credit을 준다. 이는 불확실한 낙관 민감도이며 실제 cancellation identity를 안다는 뜻이 아니다. snapshot/reconnect로 기존 queue를 잃으면 취소·재워밍한다.

독립 전략 비교는 별도의 가상 계좌/대안 세계다. 서로 다른 ablation의 체결량을 합쳐 한 실계좌의 가능한 수익이라고 부르지 않는다.

## 지연과 비용

`config/mm_state_latency_scenarios.json`은 Arcus100/Bybit25/Bitget50/GRVT75ms의 **설정 민감도**다. mainnet 또는 최신 testnet 실측값이 아니다. `source_venue` 불일치·다른 venue proxy·provenance 누락을 state pilot에서 거부한다. 측정 profile에는 해당 venue의 실제 send/ACK/cancel 시각 근거를 연결해야 한다. market-data receive, decision, send, exchange ACK가 미측정이면 null로 남긴다.

정상 비용은 actual fill notional×maker fee다. 수수료는 매 fill ledger에 부과한다. maker entry/exit를 taker 왕복 비용으로 평가하지 않는다. emergency IOC는 기본 꺼짐이고 별도 `--emergency-taker`/config opt-in만 허용한다. IOC는 **도착 시** 실제 반대 depth와 제한 가격으로 부분 sweep하며 이미 소비한 displayed volume을 재사용하지 않는다. [GRVT 주문 구조](https://api-docs.grvt.io/trading_api/).

Funding와 USD/USDT 환산 비용은 현재 replay PnL에서 제외하며 보고서에 명시한다. 마지막 재고는 final mid로 평가하고 임의의 shutdown 체결을 만들어 내지 않는다. maker-only shutdown이 완전 청산을 보장하지 않는다.

## Native replay와 품질

원본 SQLite를 `mode=ro`로 열고 L2 원시 book·public trade·Binance quote·feed gap을 시간순 merge한다. GRVT initial sequence baseline, Bitget 첫 delta bridging, Arcus sequence를 확인한다. crossed/invalid book은 gap으로 처리하고 labels/window/queue를 끊는다. duplicate trade ID를 제거한다. quote 단독으로 native sub-second fill/markout을 재구성하지 않는다.

Fill-conditioned markout은 100/250/500ms와 1/2/5/10/30초의 첫 유효 미래 quote로 관측한다. target보다 .5초 넘게 늦거나 gap/끝부분이면 missing label이다. Buy=`1e4*(mid_future/fill_price-1)`, Sell=`1e4*(fill_price/mid_future-1)`. entry maker fee를 뺀 지표는 **entry-fee-adjusted markout**이며 exit fee까지 포함한 왕복 PnL과 같지 않다.

pending markout 최대20,000개, retained rows10,000개, heap은 route별 due 시각만 처리한다. 넘친 label은 CAPACITY_CENSORED로 ledger에 남긴다. 실제 성과 비교에서는 같은 주문의 부분 체결과 overlapping labels를 독립 표본으로 세지 않는다.
