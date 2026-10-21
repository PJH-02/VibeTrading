# 단계별 상태와 다음 작업 — 2026-10-21

최신 사용자 지시에 따라 추가 구현을 멈추고 완료분만 선별 커밋한다. **서버 코드는 로컬과 다르며 이번 커밋은 로컬 코드 스냅샷이다. 배포·운영 완료가 아니다.** 이전 커밋 보류 기록보다 최신인 이번 명시적 커밋 지시를 적용한다.

## 판정 기준

상위 P00~P12는 전체 운영 수락 기준이 남아 있다. 서버 활성 반영을 확인한 완료 항목은 P02 schema-cache 하위 작업이다. P00/P01/P03~P10 및 P02 나머지는 미완성이며 P11 실제 전환·P12 legacy 제거는 시작하지 않았다. 아래는 최신 VN/TX 하위 설계 단계 51개의 로컬 구현 분류다.

**완성 7 / 미완성 24 / 시작하지 않음 20.** 집계 TX 단계는 하위 작업이 진행됐으면 미완성으로 표시한다. 운영 gate는 모든 항목에서 별도 미통과다.

## 완료한 로컬 구현

| 단계 | 완료 범위 |
|---|---|
| VN01 | bounded first-fault/age/lag와 소유권 회계 구현·검증 완료 |
| VN02 | feed별 queue/generation/lease 격리와 공통 권위 실패 차단 완료 |
| VN03 | 한국 ETF 입력 의존성/관측 오류 격리 완료. 실제 장중 연속성 증거는 별도 |
| VN04A | opt-in 유한 durable spool writer와 엔진 부재 중 기록 완료 |
| VN04B | C/H·incarnation/digest 확인·state-only replay·명시 readiness 복구 완료 |
| VN05 | count/byte/고정 deadline gather 및 합성 A/B 완료. 운영 성능 gate는 열림 |
| VN06 | native/window/history/dedup codec과 engine-owner 원자 capture 완료 |

## 미완성

| 단계 | 현재 상태 / 남은 작업 |
|---|---|
| VN00 | v9 후보 index만 완료. 현재 서버/image/mount/runtime drift 재고정 필요 |
| VN07 | 완료: callback-free market checkpoint+최대 한 producer suffix 복구·명시 재개·healthy peer lease 보존. 미완료: 여러 suffix의 global order, ETF/Paper owner/strategy 복구 |
| VN13 | 단일 apply/shared snapshot Paper fixture 연결만 구현. lifecycle·강한 격리·실제 지속 운용 미완료 |
| TX00 | product/environment별 매핑 조사. 실제 계좌·margin·capability 확인 미완료 |
| TX01 | 공통 mode/action/product 초안과 Paper 범위. 미완성 TX 구현과 함께 커밋 제외 |
| TX02A | Paper scope owner lease만 구현. 기존 ETF journal/전체 계좌 fencing 미완료 |
| TX02B | Paper virtual/physical 귀속 fixture만 구현 |
| TX02 | 하위 작업이 진행 중인 집계 단계; 별도 완료로 계산하지 않음 |
| TX03A | cash/isolated-linear Paper만 구현. inverse/hedge/cross-currency/실제 margin 미지원 |
| TX03B | Paper fee/rebate/funding 회계. 실제 margin/equity 대조 미완료 |
| TX03 | 하위 작업이 진행 중인 집계 단계; 별도 완료로 계산하지 않음 |
| TX04A | matcher durable state/fill dedup 구현. 전체 입력 C/H 연속 복구 미완료 |
| TX04B | 인과적 matcher fixture. 실제 live-input Paper/continuous input 복구 미완료 |
| TX04 | 하위 작업이 진행 중인 집계 단계; 별도 완료로 계산하지 않음 |
| TX05 | 공유 state Paper runtime fixture. callback hard isolation/전체 lifecycle 미완료 |
| TX06A | Binance Perp request/sign/query 매핑만 구현. transport/private lifecycle/실계좌 미완료 |
| TX06B | Arcus Perp request/sign/query 매핑만 구현. transport/private lifecycle/실계좌 미완료 |
| TX06C | GRVT Perp request/sign/query 매핑만 구현. transport/private lifecycle/실계좌 미완료 |
| TX06D | Bybit Perp request/sign/query 매핑만 구현. transport/private lifecycle/실계좌 미완료 |
| TX06E | Hyperliquid request/sign/query 매핑만 구현. 독립 signer nonce 저장소만 완료·선별 커밋 |
| TX06 | 하위 작업이 진행 중인 집계 단계; 별도 완료로 계산하지 않음 |
| TX08A | Paper shared-account fixture만 구현. 실제 canonical owner/권위 통합 미완료 |
| TX08B | Paper 관측 group facts만 구현. 선행 owner/lifecycle 통합 미완료 |
| TX08 | 하위 작업이 진행 중인 집계 단계; 별도 완료로 계산하지 않음 |

## 시작하지 않은 단계

| 단계 | 현재 상태 / 남은 작업 |
|---|---|
| VN08 | New reader validates a retained prefix base and v1 data; corrupt/missing segments never become a valid start; GC still disabled. |
| VN09 | Only checkpoint+consumer+manifest/facts+remote/lease/retention proven segments eligible; unique copy deletion count=0 under injected crashes. |
| VN10 | Cooperating duplicate owners across alternate DB paths rejected; Mock and Mock2 remain separate; lease failure leaves account closed. |
| VN11 | Guarded stage requires evidence; duplicate immutable intent/evidence collision rejected; commit failure cannot submit; existing cumulative fill reservations unchanged. |
| VN12 | ACK after partial/full fill never regresses terminal state/accounting; cancel/fill cumulative effects applied once; no unsupported broker absence inference. |
| VN14A | Duplicate command effect once; scoped resume cannot reopen broader halt; pause/halt/reduce-only semantics approved before editing. |
| VN14B | Numeric user/chat allowlists and TTL; owner durable receipt before accepted; lost replies/429 cause no repeated effect; status/trade preserved. |
| VN15 | Actual/assumed/recovery separated; missing valuation/currency/cashflow evidence shown unavailable; no reporting IO on submit path. |
| VN16 | Source corruption isolated and observable; healthy round-robin lanes progress; common ledger/volume error still global and bounded. |
| VN17 | Per-role exact digest/mount/config contract with finite combined CPU/RAM/physical-volume budgets, no secret render and no activation. |
| VN18 | Execute validation matrix with raw sample counts and source hashes; 30m wall clock/24h cloud/15m Parquet/72h representative coverage never substituted by fixtures. |
| VN19 | Reviewable per-role cutover/rollback package with exact commands/digests/accounts/current-ledger compatibility; separate execution authorization. |
| VN20 | After actual cutover stabilization, archived legacy backlog and rollback proof, pin remaining callers and split exact retire paths before dispatch; no automatic deletion. |
| TX07A | Canonical acceptance/fill/cancel/funding/account facts and UNKNOWN/reconciliation contract |
| TX07B | Per-venue paginated query/private gap/timeout/cancel reconciliation with UNKNOWN reservations; no absence inference |
| TX07 | Order lifecycle/reconciliation acceptance aggregate |
| TX09 | Single actual/Paper PnL-position projection, owner-scoped control and promotion permissions |
| TX10 | Off-server backtest export manifest/metadata and strategy registration/promotion gate; no server research |
| TX11 | Scenarios A-F, exact candidate regression, live-market Paper mutation0, 2vCPU resources and >30m/72h/outage evidence |
| TX12 | Separate readiness states and reviewable local release/cutover/promotion package; no server deployment or LIVE_ACTIVE |

## 커밋 경계

시장 파이프라인·feed/ETF 의존성 격리·transport/C-H recovery·배치 gather·native checkpoint codec/owner capture와 닫힌 market-only 복구 하위 기능, 독립 signer nonce 저장소 및 해당 테스트를 포함한다. VN07/TX06E 전체를 완료라고 표시하지 않는다.

Paper/portfolio/venue/group 초안 6개 제품 모듈과 5개 테스트, dashboard, tmp, 데이터/백업은 제외한다. 정확 제외 목록은 [stage_status.json](stage_status.json)을 따른다. 커밋 대상만으로 별도 credential-free 후보를 만들어 회귀한 뒤 stage한다. 전체 작업 트리 v12 PASS를 선별 커밋의 PASS로 재사용하지 않는다.

전체 작업 트리 v12 회귀: Windows1312 PASS/14 SKIP/2 DESELECTED, Linux1326 PASS/2 DESELECTED. 선별 후보 `completed-market-v1`(539 files, source-set `d4a4f02777d56c2f30b53d52db76021da7aa1fbe8adc4d03a179a2436863625b`)는 Windows **1199 PASS/14 SKIP/2 DESELECTED**, Linux **1213 PASS/2 DESELECTED**다. [선별 후보 검증](../runs/20261020-production/completed-market-v1-validation.json). 이 문서를 포함한 Git commit이 선별 경계이며 실제 SHA는 `git log -1`로 확인한다. `.gitattributes`로 증거 파일의 원본 바이트/해시가 checkout에서 바뀌지 않게 한다.

## 다음 실행 순서

1. **VN07** — 미완료 복구 경계: 여러 producer suffix의 실제 global apply order 증명/저장 계약, ETF/Paper account/strategy/pending-action 상태와 시장 checkpoint의 원자 커서 관계 확정. 순서를 입증하지 못하면 현행 fail-closed 유지. 완료 시장 전용 경로를 약화하지 않음.
2. **VN08** — retained-base cursor/first-segment reader format과 v1 호환 검증. GC 비활성. VN07 원자 capture 및 해당 복구 계약에 맞는 base 증명부터 확정; 원본 prefix 삭제 금지.
3. **VN00 → VN10/VN11/VN12 + TX01/TX02/TX03/TX07A** — 현재 서버 기준선 재확인 후 canonical owner/intent/FILL·ACK 회귀/공통 계약·회계·lifecycle 통합. 실계좌 mutation/배포 없이 로컬 구현; 미확인 capability는 계속 차단.
4. **VN16 → VN09 / TX04B → TX05 → TX06A-E → TX07B → TX08** — 저장 lane 격리 후 GC 증명, 입력 연속 복구·전략 dispatch·실제 connector lifecycle와 재조정·shared/group 통합. sole-copy 삭제·UNKNOWN 무조건 retry·자동 위험 활성 금지.
5. **VN14A/B/VN15/VN17/VN18/VN19/VN20 + TX09-TX12** — 제어/보고/역할 패키지/실제 장기·부하 검증/전환/최종 legacy 정리. 30m/24h/72h와 실제 계좌·production 성능은 fixture로 대체하지 않음; 별도 전환 권한 필요.

**바로 다음 착수점:** VN07의 아직 없는 multi-producer global-order 계약과 ETF/Paper 복구 상태 경계부터 설계·시험을 확정한다. 이후 VN08 reader 포맷을 구현한다. VN08은 아직 제품 코드를 수정하지 않았다. 단순 diff/경계 검토는 Luna, 복구·계좌 권위는 독립 safety reviewer로 분리한다.
