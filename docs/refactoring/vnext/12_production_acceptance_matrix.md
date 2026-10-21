# Production 12 — 준비 상태와 필수 증거

2026-10-20. 문서/코드/fixture/실제 시세 Paper/실계좌 capability/운영 활성은 별도 상태다. **LIVE_ACTIVE=false**. 과거 v5/v6 PASS는 현재 확장 후보 전체 PASS가 아니다.

| 최종 상태 | 필요한 독립 증거 | 현재 판정 |
|---|---|---|
| MARKET_DATA_READY | 현재 scope·native/book/history/available-at·gap·원본 정밀도 | 기존 코드/증거 재사용, 전체 현재 운영 품질 미확인 |
| NATS_FREE_RUNTIME_READY | 실제 >1800초·Collector/Engine/Storage 분리·checkpoint/restart·용량 | BLOCKED_UNVERIFIED; 가상1801초와 구분 |
| MULTI_STRATEGY_READY | one apply, per-strategy dependency·exception/slow-callback/fairness·bounded action | 로컬 shared snapshot/입력별 gate 구현, hard callback isolation·실제 부하 증거 미완료 |
| PAPER_READY | live-market 데이터의 공통 runtime→risk/reserve→causal match→durable portfolio/PnL→restart, mutation0 | NOT_VERIFIED; fixture-only를 승격하지 않음 |
| MULTI_ACCOUNT_OWNER_READY | canonical scope lease·다른 DBpath 경쟁·계좌별 직렬화·legacy fencing | NOT_VERIFIED |
| PERP_PORTFOLIO_READY | multiplier/currency/mode·virtual/physical·margin/fee/funding·external gap·race | NOT_VERIFIED |
| VENUE_ADAPTER_CODE_COMPLETE | Venue×Product×Environment의 모든 필수 capability와 scripted transport 회귀 | 5개 request mapping 구현; concrete transport/private lifecycle/owner 연결 부족으로 code_complete=false |
| VENUE_SANDBOX_VERIFIED | 승인된 sandbox의 signing/submit/cancel/query/privatefill·rate/reconnect·UNKNOWN 대조 | BLOCKED_UNVERIFIED; 이번 외부 mutation 불허 |
| VENUE_LIVE_CAPABILITY_VERIFIED | 실제 계좌/product/position/margin mode·private events·권한·완전 대조 | BLOCKED_UNVERIFIED |
| OPERATIONS_READY | UI/control·snapshot/backup restore·2vCPU/3.8GiB budgets·latency/capacity/72h·cutover/rollback | BLOCKED_UNVERIFIED |
| LIVE_ACTIVE | 명시적 strategy/config/account/product별 사용자 승인+모든 해당 gate | **false** |

## 통합 시나리오와 판정 기준

| 시나리오 | 독립 fixture 자극 | 필수 assertion |
|---|---|---|
| A KIS C3/KD/KP | 동시 intent·reservation·누적 체결 | 기존 Mock/Mock2/economic golden 동일, account 합산 제한, strategy 귀속·cash/PnL 정확 |
| B GRVT shared account | MM + arb 반대 방향 intent/partial fills | virtual 분리·physical net/hedge-side, 두 번 reserve/중복 fill0, pending worst-case 낙관 상쇄0 |
| C cross-venue partial | GRVT filled + Binance UNKNOWN | 잔여 delta/margin 표시, UNKNOWN reserve 유지·blind retry0, unrelated order 보존 |
| D pressure | crypto burst+KIS3전략+fill/cancel/report/storage stall | queue count/bytes/age/inflight, fairness/TTL, p95/p99/p99.9·CPU/event·RSS/PSS/swap, risk/fill starvation0 |
| E strategy deployment | Dummy Strategy를 기존 계약으로 등록 | 별도 broker/DB/PaperEngine 개발 없이 warmup→action→Paper→portfolio/PnL, mutation spy0 |
| F promotion safety | paper→live 설정 모의, 승인/계좌 gate 누락 | 외부 mutation0, Paper position을 실제 잔고로 복사0, actual reconcile 필수 |

추가 필수: cancel-effective 전 fill, ACK 선/후도착, input/fill/funding replay, mark/fee/collateral unknown, queue-full, native gap, delayed private fill, callback timeout, checkpoint 생성 중 hot-path 영향, Parquet900초/Cloud86400초 중단·회복, representative72h.

기존 C/G hard gate·CPU/event 목표·tail regression·peak3GiB 목표를 완화하지 않는다. 실제 같은 2vCPU/3.8GiB·trace/config/fee/funding metadata로 baseline/candidate를 비교한다. 측정하지 않은 자원/latency/장기 값은 NOT_RUN이다. testnet 성과는 전략 수익성 증거가 아니다.
