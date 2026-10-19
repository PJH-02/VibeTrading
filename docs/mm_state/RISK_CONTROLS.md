# 위험·컴퓨팅 제한

## 주문·계좌

Normal entry/exit는 maker, emergency taker는 기본 false다. maker-only stop은 **감축 주문과 신규 위험 차단**이며 stop 가격의 즉시 체결을 보장하지 않는다. hard price stop이 필요하면 taker emergency opt-in과 그 비용·슬리피지가 별도 필요하다. 미청산 position 및 unknown order는 operator 확인이 필요하며 완료로 표시하지 않는다.

기본 virtual equity10,000, entry notional≤150, L1 depth 참여≤10%, account gross≤8%, account loss/DD≤20(0.2%), holding≤120초, position adverse move0.35%다. testnet은 실제 account equity·available margin, loss/DD0.2%, maximum margin rate15%를 확인한다. 수량은 minimum/lot/tick을 만족하고, 잔량 최소 수량 미달로 maker 청산이 안 되면 dust를 보고한다.

Feed age>3초, sequence gap/crossed/invalid book, reference age>1.5초 또는 source skew>500ms는 신규 거래·관련 labels를 차단한다. stale book으로 emergency sweep하지 않는다. 이후 window 재워밍·새 quote가 필요하다.

실시간 source→local wall delay>3초 또는1초 넘는 미래 시각은 stale/clock gap이다. 테스트넷·public pilot 모두 최신 호가 source보다2초 이상 과거인 initial trade snapshot을 신규 flow에 넣지 않는다. 장중 source timestamp가 없는 경로에서는 receipt freshness만 확인할 수 있으므로 clock/transport latency의 검증은 제한된다.

`RiskGuard`는 venue별 latched 회로다. 60초 cancellation>180, 최소40 sends 이후 reject ratio≥50%, send→ACK>5초, 관측된1초 entry-fee-adjusted maker markout 최소20개 평균<-5bp면 신규 진입 중단. threshold는 동결한 운영 초기값이며 최적 수익 parameter가 아니다. partially-filled records는 회로의 진단이며 유의성 표본이 아니다. actual testnet create timeout은 UNKNOWN 상태로 보존하고 reconciliation 전 예약을 해제하거나 재제출하지 않는다.

감축은 halt 상태에서도 허용한다. 계좌 손실·jump·방향 반전·closing은 entry 주문을 취소하고 재고 maker unwind를 우선한다. 자동으로 halt를 해제하지 않는다. key/auth material·signature/session body를 보고서·benchmark에 기록하지 않는다.

## 자원 상한

Feature는100ms×W≤60초 bucket, trade ID50k(mainnet adapter100k), public book side4,096level hard cap, WS max_queue8이다. 과도한 native depth는 **잘라서 정상 데이터로 쓰지 않고 실패·재연결**하며 bounded snapshot feed가 필요함을 알린다. mainnet adapter의 기본 frame 최대 크기도 유지한다. 모르는 queue를 빈 queue로 만들지 않는다.

paper terminal orders는5,000개 초과 시4,000개까지 회수하며 live pending은 보존한다. Markout pending20k/retained rows10k; cap 시 missing 사유를 남긴다. 정책 계산은 route별 최대10Hz, 주문 제출 최소.25초 간격이다. median은1Hz, ADF/KPSS와 회귀는 offline only다. SQLite commit은 batching하며 파일럿 raw frame journaling의 disk 비용은 benchmark의 model 비용과 따로 본다.

## 현재 서버의 적합성

2vCPU·약3.82GiB 서버에는 ingestion/NATS/storage/다른 수집기가 함께 있다. 단순16-route engine 자체의 메모리는 가벼우나 CPU 여유와 native burst·journaling이 병목이다. 상세 측정값은 `docs/evidence/mm_state_revision_20261007/REPORT_KO.md`와 server JSON을 기준으로 한다.

격리 .25CPU benchmark의 events/s는 전체 생산 pipeline capacity가 아니다. 전체 실시간 event rate가 이를 넘으면 backlog와 receipt latency가 커지고 stale gate를 밟는다. .25CPU의 높은 p99는 cgroup scheduling을 포함한다. public queue를 크게 늘려 이를 숨기지 않는다.

현재 권장: heavy EDA/replay/fit은 로컬, 생산 collector와 별도 network-none review는 가능, 실제 신규 운용은 적은 venue/전략의 shadow부터 native p95/p99 lag·CPU·RSS·NATS pending을 관측. 16조합 신규 websocket 수집+raw journaling을 한꺼번에 붙일 안전 여유는 이번 synthetic benchmark만으로 승인하지 않는다. source staged 상태에서 자동 service/cron/주문 제출을 켜지 않았다.

운용 확대 기준: representative burst에서 backlog가 증가하지 않고 feed source→receive p99가 stale 기준 안이며, 전체 CPU·memory 여유 및 production NATS consumer 지연을 확보해야 한다. 기존 collector 자료 공유 또는 CPU 증설이 필요할 수 있다. RAM만 증설하면 CPU 문제가 해결된다고 주장하지 않는다.
