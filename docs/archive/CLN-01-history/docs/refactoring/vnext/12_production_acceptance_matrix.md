# Production 12 — 준비 상태와 필수 증거

2026-10-20 (2026-10-23 binding scope correction). 문서/코드/fixture/실제 시세 Paper/실계좌 capability/운영 활성은 별도 상태다. **LIVE_ACTIVE=false**. 과거 v5/v6 PASS는 현재 후보 전체 PASS가 아니다.

> **2026-10-23 적용 범위:** [tech_lead_scope_decisions.json](./reuse_audit/tech_lead_scope_decisions.json)·[revised_dag.json](./reuse_audit/revised_dag.json)·[VibeTrading_Minimal_Reuse_Plan_20261023.md](./VibeTrading_Minimal_Reuse_Plan_20261023.md)가 우선한다. 필수 core는 NATS-free data/storage/recovery + strategy-file→기존 Paper + same-account safety + projection/minimal E2E + 보존된 legacy Golden이다. **all-five venue rebuild, extra margin, cross-venue group, universal real margin/lifecycle, 새 cloud uploader는 필수 gate가 아니다.** same-account safety는 **새 multi-strategy 연결에만** 요구하며 기존 ETF runtime을 재작성하지 않는다. 아래 VENUE/ARBITRAGE row는 선택/reference이며, 안전 불변조건(cancel ACK≠terminal fill, UNKNOWN reservation 유지, TOSS DATA_ONLY, monotonic fill identity, per-mode one writer)은 유지한다.

| 최종 상태 | 필요한 독립 증거 | 현재 판정 |
|---|---|---|
| MARKET_DATA_READY | 현재 scope·native/book/history/available-at·gap·원본 정밀도 | 기존 코드/증거 재사용, 전체 현재 운영 품질 미확인 |
| NATS_FREE_RUNTIME_READY / DATA_PATH (core) | 선택 local data/storage/recovery, checkpoint/restart, retained-prefix | 기존 코드 재사용; 장기/용량 증거는 local gate |
| MULTI_STRATEGY_READY (selected) | one apply, 전략별 dependency/exception/fairness, bounded action | 기존 shared snapshot 재사용; hard callback isolation 주장은 측정 전 금지 |
| PAPER_READY (core) | local data→strategy file→기존 risk/reserve→causal match→기존 ledger/PnL→restart, mutation0 | NOT_VERIFIED; fixture-only를 승격하지 않음 |
| MULTI_ACCOUNT_OWNER_READY / SAME_ACCOUNT_SAFETY_READY (core) | 새 multi-strategy 연결의 single writer, reservation/attribution/cancel/restart | NOT_VERIFIED; 기존 ETF runtime 재작성 아님 |
| PERP_PORTFOLIO_READY (selected) | 선택된 기존 product의 virtual/physical·fee/funding·external gap | NOT_VERIFIED; universal margin 요구0 |
| VENUE_ADAPTER_CODE_COMPLETE (optional/reference) | 선택된 기존 route의 함수/fixture equivalence | all-five gate 아님; 5 adapter code_complete=false는 historical evidence |
| VENUE_SANDBOX_VERIFIED (external/optional) | 승인된 sandbox의 signing/submit/cancel/query/privatefill 대조 | BLOCKED_UNVERIFIED; 이번 외부 mutation 불허 |
| VENUE_LIVE_CAPABILITY_VERIFIED (external/optional) | 실제 계좌/product/mode/private events 완전 대조 | BLOCKED_UNVERIFIED |
| OPERATIONS_READY (selected) | 기존 UI/control·restore·resource·cutover/rollback | BLOCKED_UNVERIFIED; broad platform badge 없음 |
| LIVE_ACTIVE | 명시적 strategy/config/account/product별 사용자 승인+해당 gate | **false** |

## 통합 시나리오와 판정 기준

| 시나리오 | 독립 fixture 자극 | 필수 assertion |
|---|---|---|
| A KIS C3/KD/KP | 동시 intent·reservation·누적 체결 | 기존 Mock/Mock2/economic golden 동일, account 합산 제한, strategy 귀속·cash/PnL 정확 |
| B same-account strategies (core) | 선택된 기존 Paper에서 두 전략 반대 intent/partial fills | virtual 분리·physical net, 두 번 reserve/중복 fill0, pending worst-case 낙관 상쇄0, cancel ownership |
| C cross-venue partial (DEFERRED) | GRVT filled + Binance UNKNOWN (기존 prototype reference only) | core 수락 조건 아님; 기존 observational fact 보존, UNKNOWN reserve 유지·blind retry0 |
| D selected local pressure (core) | local data burst + strategy files + fill/cancel/report/storage stall | queue count/bytes/age/inflight, fairness/TTL, selected p95/p99, risk/fill starvation0 |
| E strategy deployment (core) | 별도 strategy file을 기존 contract로 등록 | 새 broker/DB/PaperEngine 없이 warmup→Signal→기존 Paper→portfolio/PnL, mutation spy0 |
| F promotion safety (core) | paper→live 설정 모의, 승인/계좌 gate 누락 | 외부 mutation0, Paper position을 실제 잔고로 복사0, actual reconcile 필수 |

선택 local scope 필수: cancel-effective 전 fill, ACK 선/후도착, input/fill/funding replay, mark/fee/collateral unknown, queue-full, native gap, delayed private fill, callback timeout, checkpoint 생성 중 hot-path 영향, Parquet900초/Cloud86400초 중단·회복. **representative72h·장기 운영은 External Gate**이며 이번 core PASS 조건이 아니다.

선택 scope에 해당하는 기존 C/G hard gate·CPU/event 목표·tail regression·peak3GiB 목표를 완화하지 않는다. 실제 같은 2vCPU/3.8GiB·trace/config/fee/funding metadata로 baseline/candidate를 비교한다. 측정하지 않은 자원/latency/장기 값은 NOT_RUN이다. testnet 성과는 전략 수익성 증거가 아니다.
