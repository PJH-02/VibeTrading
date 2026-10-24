# 단계별 상태와 다음 작업 — 2026-10-21

기존 완료분 선별 커밋은 90847f8이다. **최신 사용자 지시에 따라 현재 변경 전체와 미완성 초안·대시보드 소스를 포함한 Git 스냅샷을 커밋·푸시한다.** 로컬과 서버는 다르며 배포·운영 완료를 뜻하지 않는다. 제품 작업은 VN14B에서 중지했고 다음 티켓은 미착수다.

## 판정 기준

상위 P00~P12는 전체 운영 수락 기준이 남아 있다. 서버 활성 반영을 확인한 완료 항목은 P02 schema-cache 하위 작업이다. P00/P01/P03~P10 및 P02 나머지는 미완성이며 P11 실제 전환·P12 legacy 제거는 시작하지 않았다. 아래는 최신 VN/TX 하위 설계 단계 51개의 로컬 구현 분류다.

**완성 14 / 미완성 23 / 시작하지 않음 14.** 집계 TX 단계는 하위 작업이 진행됐으면 미완성으로 표시한다. 운영 gate는 모든 항목에서 별도 미통과다.

## P00~P12 진행 요약

기존의 **미완성** 표시는 남은 개발과 운영 증거 대기를 합친 상태였다. 아래에서 둘을 구분한다. 구현량이나 완료율을 추정한 표는 아니다. 운영 사실은 확인 당시의 기록이며 현재 서버 재조사를 수행한 결과는 아니다.

| 단계 | 끝낸 작업 | 남은 작업·미완성의 주된 이유 |
|---|---|---|
| P00 기준선 | 소스·역할 조사, fixture/manifest, 격리 회귀 기준선 | **관측 보완:** 현재 서버 image/config/mount·Python tree 고정 완료. 대표 장중/72h 증거는 미완료 |
| P01 계약·상한 | NATS-free 방향, 공통 계약·실패/상한 문서, 다수 코드 검증 | **설계·개발:** 여러 producer 복구 순서 구현 완료. 계좌/전략 복구 권위, 미확인 capability·생산 파라미터 |
| P02 반복 작업 제거 | schema cache 서버 적용 확인, packet/배치 gather 로컬 구현 | **주로 운영 검증:** Ubuntu에서 실제 E2E/CPU·freshness 개선 입증. 과거 Windows 합성 수치는 참고 기록이며 서버 완료 gate가 아님 |
| P03 공유 상태·전략 | HotPathStore·인과적 이력·공유 snapshot·native book 구현/회귀 | **개발:** 공통 전략 lifecycle·장애 격리와 실제 전략 연결. 장중 지속성 확인도 필요 |
| P04 원장·단일 owner | 기존 원장/permit seam, 독립 nonce 저장소, Paper 계좌 초안 | **개발:** legacy 계좌 전체 fencing, canonical owner·intent/fill·UNKNOWN 재조정 통합 |
| P05 수집·실시간 분리 | bounded spool/UDS/pipeline, feed 격리, durable delivery·배치 구현 | **주로 운영 검증:** 실제 30분 초과 독립 실행, 대표 burst/catch-up·전체 자원 예산 |
| P06 replay·warmup | 원자 market checkpoint, native 상태 복원·명시 재개. 다중 producer의 완전한 내구 순서 증명 경로 추가 | **개발:** market journal 수명주기 통합 완료·미기록 suffix 거부. ETF/Paper 계좌·전략·입력 커서의 연속 복구 미완료 |
| P07 저장·보관·GC | direct spool→Parquet/facts, manifest/progress 기본 경로 | **개발:** 저장 lane 격리 완료. retained-base reader와 reader/remote/retention 증명에 따른 GC 미완료 |
| P08 제어·보고 | durable inbox/outbox·로컬 관측, ACK/429/재접속 보완 | **개발:** owner 범위 제어·명령 receipt·Telegram/UDS opt-in 연결 완료. 실제/가정 PnL projection과 운영 활성 증거 미완료 |
| P09 역할·배포·예산 | local runtime/config·mount 계약·후보 source pin | **개발·운영 확인:** 최신 역할별 package와 실제 source/image/mount 일치·합산 CPU/RAM/디스크 예산 |
| P10 통합 검증 | 최신 선별 코드 Linux 전체1419 PASS/2제외, 장애 fixture·합성 A/B | **검증 대기와 선행 개발:** 실제 live-input Paper·계좌·30m/15m/24h/72h·동일 운영 조건 성능 |
| P11 실제 전환 | 절차·rollback 초안 | **실행 미착수:** 선행 gate 충족 후 구체적 package와 별도 전환 권한 필요 |
| P12 legacy 정리 | 정리 방향·보존 조건 | **실행 미착수:** 전환 안정화·backlog/rollback·잔여 호출 증명 후 서비스/코드 제거 |

P02/P05/P10은 이미 끝낸 로컬 작업이 많다. 반면 P04/P06/P07 등에는 실제로 빠진 핵심 구현이 있다. 대기 중인 운영 검증 때문에 완료 코드를 계속 고치는 방식으로 단계 상태를 바꾸지 않는다.

## 완료한 로컬 구현

| 단계 | 현재 범위 / 남은 작업 |
|---|---|
| VN00 | 2026-10-21 현재14역할 image/config/mount 및13개 Python역할 전체소스 hash 고정·drift 분류 완료. 로컬/서버 동일 또는 배포/장기자원 완료를 뜻하지 않음 |
| VN01 | bounded first-fault/age/lag와 소유권 회계 구현·검증 완료 |
| VN02 | feed별 queue/generation/lease 격리와 공통 권위 실패 차단 완료 |
| VN03 | 한국 ETF 입력 의존성/관측 오류 격리 완료. 실제 장중 연속성 증거는 별도 |
| VN04A | opt-in 유한 durable spool writer와 엔진 부재 중 기록 완료 |
| VN04B | C/H·incarnation/digest 확인·state-only replay·명시 readiness 복구 완료 |
| VN05 | count/byte/고정 deadline gather 및 합성 A/B 완료. 운영 성능 gate는 열림 |
| VN06 | native/window/history/dedup codec과 engine-owner 원자 capture 완료 |
| VN10 | 공유 물리 계좌 authority·DB lease, ETF startup/POST/close fencing 완료. Linux전체1294 PASS/2제외·리뷰미해결P1/P2 0; 실제 legacy stop/cross-host/배포는 별도 gate |
| VN11 | guarded evidence/schema/hash·중복불변충돌·COMMIT rollback 및 기존요청 재사용 완료. Linux관련86 PASS·리뷰P1/P2 0; VN12 후 전체통합 예정 |
| VN12 | 늦은 ACK·native ID 결합·최초 타이머·취소완료/latefill 단조성 완료. Linux관련138 PASS·통합1329 PASS/2제외·리뷰P1/P2 0 |
| VN14A | 범위별 재개·명령 효과/receipt 원자 저장·중복 불변성 완료. Linux 전체1372 PASS/2제외·리뷰미해결P1/P2 0; 외부 명령 연결은 VN14B |
| VN14B | 명시 opt-in UDS owner receipt·numeric allowlist·불변 TTL·재전달/429 및 압박 격리 완료. Linux 전체1419 PASS/2제외·독립 리뷰 P1/P2 0; offline owner만 지원, 운영 활성 미검증 |
| VN16 | 순수 typed source corruption·payload/fingerprint preflight quarantine와 healthy round-robin 완료. 공통writer/ledger/volume/unknownproof는 global유지. Linux관련111 PASS·리뷰P1/P2 0 |

## 미완성

| 단계 | 현재 범위 / 남은 작업 |
|---|---|
| VN07 | 완료: market 다중 producer 내구 순서 증명, 세대별 checkpoint/journal 생성·교체·복원·종료 및 runtime 통합. 미완료: ETF/Paper account/strategy/pending-action 원자 복구와 운영 검증 |
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

| 단계 | 현재 범위 / 남은 작업 |
|---|---|
| VN08 | New reader validates a retained prefix base and v1 data; corrupt/missing segments never become a valid start; GC still disabled. |
| VN09 | Only checkpoint+consumer+manifest/facts+remote/lease/retention proven segments eligible; unique copy deletion count=0 under injected crashes. |
| VN15 | Actual/assumed/recovery separated; missing valuation/currency/cashflow evidence shown unavailable; no reporting IO on submit path. |
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

## 다음 재개 시 순서 (현재 중지)

**사용자 지시에 따라 VN14B까지 완료한 뒤 중지했다. 현재 제품 WIP는 없고 VN15는 미착수다. 재개 지시 없이 다음 티켓을 시작하지 않는다.**

1. **VN15** — Read-only actual/assumed/recovery reporting; unavailable valuation/currency/cashflow explicit. 사용자 재개 지시 전 착수 금지; reporting IO on submit path forbidden.
2. **TX01/TX02/TX04 → VN07/VN08** — Common owner/account/strategy recovery before retained-base reader; GC disabled. Prototype and recovery gates remain open.
3. **VN09/VN17/VN18/VN19/VN20 + TX05-TX12** — GC proof, combined role budgets, actual operational evidence and reviewable cutover. No automatic deletion/deployment/real activation; 30m/24h/72h/account evidence not replaced by fixtures.

## 토큰 사용과 단계 미완료의 원인 분석

### 확인한 수치와 해석 범위

- 현재 대화의 세션 로그 `token_count` 누적값(2026-10-21 09:07 KST 기록): 입력 약 **168.86M**, 그중 캐시 입력 **164.70M(97.53%)**, 입력에서 캐시를 뺀 값 **4.16M**, 출력 **0.633M**. 중복 제거한 usage 갱신901개에서 `last_token_usage.input_tokens` 중앙값은 **181,596**, 마지막 값은 **255,299**였다. 이는 반복 전달을 포함한 누적 카운터이며 고유 문서 분량이 아니다. 하위 agent별 합계·과금액·한도 소진율·원인별 비용 비율은 이 기록만으로 계산하지 않았다.
- 해당 production run 폴더에는 결과 receipt **47개(성공32/실패15)**, source manifest **19개**가 있다. 전체 회귀는 플랫폼별 합산 **8회**, 중복 포함 testcase 실행10,022개, 실행시간 합계2,309초다. 병렬 실행 시간이 겹치므로 전체 경과시간이나 토큰량으로 환산하지 않는다.
- v10/v11은 관련 테스트가 통과한 뒤에도 커서 되감기·초기 상태 출처·callback 권위 문제가 발견돼 수정됐다. [최종 독립 리뷰](../runs/20261020-production/vn07-review.json)에 닫은 결함을 기록했다. 이런 수정은 필요했으며, 발견 시점을 앞당기는 것이 절감 대상이다.

### 왜 토큰은 많이 쓰고 상위 단계는 미완성인가

| 원인 | 이번 작업에서 확인한 사실 | 줄일 방법 |
|---|---|---|
| 완료 정의가 너무 큰 단위 | P 단계에 구현·로컬 회귀·실계좌/실부하·배포 조건이 함께 있음. 미완성 한 단어로 구현 진척이 가려짐 | `구현 상태 / 로컬 검증 / 운영 gate`를 따로 보고. 운영 증거 대기는 별도 항목으로 유지 |
| 범위 확대와 진행 중 작업 누적 | 기존 리팩터링에 공통 runtime·원장·Paper·5개 Perp·group까지 더해짐. 제품 초안6개/테스트5개가 완료 커밋에서 제외됨 | 한 기능을 검증·커밋 가능한 단위로 닫은 뒤 다음 기능 착수. 선행 owner/lifecycle이 열렸으면 venue별 초안을 더 늘리지 않음 |
| 핵심 복구 계약을 구현 후 보완 | 단일 C/H에서 여러 producer 순서·원자 capture·callback 출처까지 뒤늦게 검토. v10/v11 PASS 후 재수정 | P01에서 권위·커서·실패 경계를 먼저 짧게 고정하고 공격 사례를 묶어 리뷰. 기능을 늘리기 전에 반례부터 확인 |
| 긴 문맥과 여러 상태 문서 | 입력 중앙값18.2만 토큰, 과거 execution board와 최신 VN/TX 상태가 함께 존재. 재개 시 서로 대조하는 비용 발생 | 시작점은 이 문서와 해당 티켓 하나. 필요한 심볼/diff/receipt만 읽고 과거 보드는 변경 이유가 있을 때만 확인 |
| 후보·검증·커밋 경계를 늦게 확정 | 여러 후보를 만든 뒤 최종 커밋에서 미완성11파일을 제외해 별도 전체 회귀가 필요했음 | 시작 전에 포함/제외 파일을 고정. 개발용 격리 사본에서 좁게 검증하고 리뷰 종료 후 최종 후보 한 번 동결 |
| 전체 테스트·증거 출력의 반복 | source manifest19개, 전체 회귀8회. 매번 테스트가 모델 토큰을 많이 쓰는 것은 아니지만 실행 조정·출력·인계가 누적됨 | 변경 관련 시험부터 실행. 전체 회귀는 통합/커밋 후보에서 수행하고 필요할 때만 반복. 긴 hash 목록·raw 로그 대신 요약값만 도구 결과로 반환 |

제가 개선해야 할 부분은 **넓은 범위의 초안을 병행한 점, 복구 경계를 늦게 확정한 점, 큰 상위 단계를 계속 미완성으로만 보고한 점**이다. 다만 실제 미구현과 실운영 증거 부족은 그대로 남겨야 한다. 표시만 바꾸거나 안전 리뷰·필수 시험을 생략해 완료 처리하지 않는다.

### 다음 작업부터 적용할 절감 규칙

1. **한 번에 제품 기능 한 개.** 목표·포함/제외 파일·완료 assertion·필수 시험을 먼저 정한다. 독립 리뷰와 Luna 단순 보조는 병행하되 별도 제품 기능을 추가하지 않는다.
2. **한 번 읽고 변경분만 읽는다.** 인계는 목표/수정 심볼/판정/receipt/미해결/다음 시작점의 짧은 기록으로 제한한다. 하위 agent는 전체 대화 복사 대신 필요한 맥락만 전달한다. 새 대화로 재개할 때도 commit `90847f8`과 이 상태표·해당 티켓부터 시작한다.
3. **계약 리뷰를 앞당긴다.** 복구·계좌·GC는 독립 reviewer가 권위 경계와 반례를 먼저 묶어 검토한다. Luna는 단순 diff·경계 테스트·증거 대조를 맡는다. 모델을 싸게 바꾸는 것만으로 설계 재작업을 해결하려 하지 않는다.
4. **Ubuntu 기준으로 좁게 시작해 필요한 범위만 넓힌다.** 제품 검증은 WSL Ubuntu의 Linux 파일시스템(`/tmp` 등)에 둔 credential-free 사본에서 clean env/guard로 수행한다. `/mnt/c`의 NTFS를 Linux 파일시스템 내구성·성능 증거로 사용하지 않는다. Windows는 편집·Git 호스트이며, 사용자가 Windows 지원을 요구한 경우 외에는 Windows Python 회귀를 실행하지 않는다. 원본 .env 레포에서 제품 import/pytest 금지, 개발용 사본 하나, 시험 중 수정 금지를 유지한다. 리뷰 수정 후 후보를 고정하고 관련 Linux 시험부터 실행한다. 전체 회귀는 통합 후보에서 필요할 때 수행하며 통과 후 이유 없이 반복하지 않는다. WSL 결과도 실제 서버의 파일시스템·자원·장기 운용 검증을 대신하지 않는다.
5. **대기 항목을 코딩으로 반복 처리하지 않는다.** 실제 30m/24h/72h, 서버 identity, 계좌 capability는 필요한 관측/권한/입력을 명시하고 관련 단계만 대기시킨다. 같은 기능을 재설계하거나 과거 근거를 다시 전부 수집하지 않는다.
6. **닫힌 기능은 즉시 경계를 보존한다.** 완료 시 diff·시험·리뷰·commit/보류 이유를 남긴다. 사용자가 승인한 범위에서만 커밋하고 미완성 초안과 섞지 않는다. 원인별 토큰 절감률이나 완료율은 실측 없이 약속하지 않는다.

**현재 티켓 운용:** VN14B 로컬 구현·검증 완료 후 사용자 요청으로 중지. 다음 티켓 미착수, 제품 WIP 없음. 최신 경계는 tickets_production.json.execution_control과 이 문서의 다음 재개 시 순서를 따른다.

## VN07 순서 증명 하위 작업 — 2026-10-21

연속 실행: **VN07-J1 완료** — 비동기 순서 journal·정확한 flush barrier·공통 실패 차단. Linux150 PASS. [검증](../runs/20261020-production/vn07-j1-linux-final/linux_result.json). **VN07-J2 완료** — 세대별 checkpoint/journal·원자 포인터 교체·총 저장 상한·늦은 cleanup까지 root lock 보존. Linux76 PASS. [검증](../runs/20261020-production/vn07-j2-linux-reviewfix/linux_result.json). **VN07-J3 로컬 완료** — 게시된 세대만 복원하고 새 세대를 준비한 뒤 listen하며 주기 작업 오류와 종료를 runtime이 처리한다. 최종 고정 후보 Linux 전체 **1263 PASS/2 DESELECTED**. 최초 전체 회귀는600초 제한으로 종료됐고 동일 코드 재검증이 통과했다. 세 티켓 독립 리뷰 미해결 P1/P2 0. 상세 hash·receipt는 VN07 `lifecycle_tickets`에 기록했다. [최종 검증](../runs/20261020-production/vn07-lifecycle-validation.json)을 보존했다. **VN00 현재 기준선 고정 완료 후 VN10-A 실행 소유권**으로 진행한다.

기존 `MarketSpool`의 single-writer lock·프레임 checksum·record/byte 상한을 재사용한다. `capture_checkpoint(order_spool=...)`가 정확한 checkpoint SHA를 빈 journal에 결합하고, 새로 적용한 cursor마다 digest를 기록한다. 재전송은 기록하지 않지만 event-ID 중복과 raw-only 입력의 새 cursor는 빠뜨리지 않는다. 새 서비스·DB·의존성과 이벤트별 fsync는 추가하지 않는다.

`ApplyOrder.read(reader,max_bytes=...)`만 불변 증명 객체를 생성한다. `replay_checkpoint_startup(...,apply_order=proof)`와 엔진이 C/H 전체 범위·순서를 확인하고, 실제 payload digest를 대조하며 상태만 복원한다. 자동 LIVE 승격은 없고 기존 readiness 검증이 필요하다. journal 기록 실패는 공통 권위를 닫는다.

**현재 경계:** 명시 opt-in runtime이 생성·flush/교체·복원·close를 관리한다. 기본 config/service는 활성화하지 않는다. journal 하나는 checkpoint 하나에만 결합한다. 재시작 후 새 기록에는 새 checkpoint와 빈 journal이 필요하다. producer H가 journal보다 앞서거나 순서 기록이 불완전하면 복구를 거부한다. 운영 지속 실행·성능·ETF/Paper 복구를 완료했다는 의미가 아니다. 정확한 코드 hash·시험 결과·리뷰는 VN07의 `order_subtask`에 기록한다.

**검증:** 최종 v2 관련 회귀 Windows235 PASS/5 SKIP, Linux240 PASS. 직전 v1의 전체 회귀 Windows1214 PASS/14 SKIP, Linux1228 PASS는 마지막 직렬화 실패 차단 보완 전의 결과이며 v2 전체 회귀로 표시하지 않는다. 독립 safety 리뷰의 미해결 P1/P2는 0이다. 초기 Windows 수집기 시험의 파일 교체 권한 오류는 이전 커밋 대조와 이후 시험에서 재현되지 않았지만 원인을 확정하지 않았다. [hash·시험·리뷰·한계](../runs/20261020-production/vn07-order-validation.json)를 함께 보존했다. 상위 VN07은 미완성, 이번 하위 기능만 로컬 완료이며 현재 수정은 미커밋이다.

## 작업 기록 — VN10 (2026-10-21)

VN00의 현재14역할 image/config/mount와13개 Python역할 전체 소스 목록·hash를 읽기 전용으로 고정했다. 관측한 서버 파일은 과거 서버 hash와 같지만 로컬과 다르며 배포 동등성은 열려 있다. [현재 기준선](../runs/20261020-vnext/vn00-package/current-identity-full-20261021.json). VN10-A에서 명시 공유 authority root·물리 계좌 단위의 보수적 lease로 다른 DB 경로의 중복 owner를 막는다. 이후 VN10-B에서 기존 ETF Mock/Mock2의 초기화·dispatch·종료 수명주기에 연결한다. 서버 고유 동작은 diff로 확인하며 실제 서버/계좌를 변경하지 않는다.

VN10-A 완료: canonical physical-parent 계좌 공유 lease와 DB lease를 함께 유지한다. Linux35 PASS, 단순 diff 리뷰 미해결 P1/P2 0. [시험](../runs/20261020-production/vn10-a-linux-focus2/linux_result.json). FIFO가 fdopen에서 먼저 거부되던 경로는 regular-file 검사 후 fdopen으로 바꿔 descriptor도 회수한다. VN10-B의 ETF 수명주기 통합을 시작한다.

VN10-B 구현·리뷰 완료, 전체 회귀 대기: Mock/Mock2의 실제 CANO를 같은 mock 환경에서 구분하고, 공유 잠금을 확보한 뒤 저널/계좌를 초기화한다. 모든 KIS POST는 한 번 결합한 소유권 검사기를 token 전과 POST 직전에 요구한다. 외부 close는 활성 runner·warmup·보고 종료와 bus/DB close 뒤 잠금을 푼다. 관련157 PASS와 최종 종료8 PASS는 서로 다른 범위로 보존하며 합산하지 않는다. 실제 운영에는 모든 협조 프로세스에 동일한 보호 볼륨의 `EXECUTION_AUTHORITY_ROOT`가 필요하다. 서버 설정·서비스는 수정하지 않았다.

VN10 완료: 최종 고정 후보544파일의 Linux 전체 **1294 PASS/2 DESELECTED**, 독립 단순 diff 리뷰 미해결P1/P2 0. [최종 검증](../runs/20261020-production/vn10-owner-validation.json). 실제 shared-root 배치/legacy 정지는 운영 gate로 남긴다. **다음 WIP VN11 시작:** guarded 주문 evidence 검증·불변 intent/evidence 충돌 거부·COMMIT 실패 rollback을 구현하고 기존 fill/reserve는 보존한다.

VN11 완료: guarded stage의 intent-bound evidence·16KiB schema·중복 payload/day/evidence 충돌 거부와 COMMIT rollback을 구현했다. 같은 사용자 요청과 시장가 잔량 재호출은 저장된 요청을 확인한 뒤 quote/time 재생성 없이 반환하며 변경된 요청은 거부한다. Linux관련 **86 PASS**, 독립 단순 리뷰 미해결P1/P2 0. [검증](../runs/20261020-production/vn11-stage-validation.json). VN12의 늦은 ACK·취소/체결 단조성으로 이어가며 이후 두 티켓의 전체 통합을 한 번 수행한다.

VN12 완료: 늦은 ACK가 terminal 상태·최초 accepted_ns·native ID를 되돌리지 않으며 확인된 취소 뒤 늦은 체결은 금액/수량만 단조 반영한다. Linux관련138 PASS, VN11/VN12 통합 **1329 PASS/2 DESELECTED**, 리뷰미해결P1/P2 0. [검증](../runs/20261020-production/vn12-ack-validation.json). **다음 WIP VN16 시작:** source preflight 오류만 bounded quarantine하고 공통 writer/DB/볼륨·미확인 checkpoint/attestation proof는 전체 fail-closed로 유지한다.

VN16 완료: pure source corruption·전처리 오류만 격리하며 정상 lane은 순환 진행한다. 공통 writer/DB/볼륨·기존 checkpoint/unknown attestation 오류는 전체 차단한다. Linux관련 **111 PASS**, 리뷰P1/P2 0. [검증](../runs/20261020-production/vn16-lane-validation.json). 다음 **VN14A scoped resume**를 시작한다. C04의 상위 halt 보존과 기존 검증된 ETF reduce-only 범위만 다루며 halt를 flatten/cancel로 확대하지 않는다.

VN14A-1: selected-scope resume와 상위 halt/다른 전략 보존, 명시 parent-first 초기화 구현. Linux관련66 PASS, 최종 diff 리뷰 대기. VN14A-2는 기존 SQLite 원장 안에서 bounded 명령 receipt와 gate 효과를 같은 transaction으로 확정하는 계약을 검토한다. 외부 Telegram 연결/정책 확장은 포함하지 않는다.

## VN14A 완료와 VN14B 착수 — 2026-10-21

범위별 parent-first 재개와 단일 트랜잭션 명령 효과/receipt를 구현했다. 만료·후속 재개/중단·재시작 뒤 exact retry는 과거 receipt만 반환하고, 같은 ID의 다른 본문은 거절한다. COMMIT 실패는 효과와 receipt를 rollback하고 위험을 닫는다. pause/halt는 취소·청산을 실행하지 않는다. Linux 관련88 PASS, 최종 전체 **1372 PASS/2 DESELECTED**, 독립 리뷰 미해결P1/P2 0. [고정 후보·검증](../runs/20261020-production/vn14a-command-validation.json). VN14B는 동일 명령을 기존 control UDS/Telegram inbox·outbox에 연결하는 명시 opt-in 범위로 착수했다. 현재 변경 미커밋, 서버 반영 없음.

## VN14B 완료·사용자 요청으로 중지 — 2026-10-21

기존 control UDS에 명시 opt-in 명령/결과를 연결했다. 숫자 user/chat allowlist와 원래 message.date 기반 TTL·명령 본문을 고정 저장하고, owner의 내구 receipt가 커밋된 뒤 기존 outbox가 응답을 소유한다. 응답 유실·429·재시작은 효과를 반복하지 않는다. market 세션/partial DATA 용량이 가득 차도 별도 유한 control 용량으로 HALT를 전달하고, 미완성 COMMAND의 용량도 반환한다. fresh resume는 명시 full epoch와 cached readiness를 같은 트랜잭션에서 검증한다. epoch 원본 변경과 NaN/무한대/bool/float/범위 밖 readiness 시각 우회를 닫았다.

최종 고정 후보548파일, Linux 전체 **1419 PASS/2 DESELECTED**, 마지막 owner 관련102 PASS, 독립 diff/안전 리뷰 미해결P1/P2 0. [소스·시험·리뷰·제한](../runs/20261020-production/vn14b-control-validation.json). 이전 전체1411/1413 PASS는 마지막 안전 보완 전 결과이며 최종 후보 증거로 재사용하지 않는다. 최종 전체 회귀는 독립 안전 리뷰의 수정을 닫은 뒤 동결해야 한다는 절감 규칙을 다시 확인했다.

기본 handler는 None이며 제어 명령은 명시 주입한 offline owner에서만 지원한다. pause/halt는 자동 취소·청산을 하지 않는다. 기존 status/trade 회귀를 보존했고 서버 반영·외부 메시지·실계좌 활성·커밋·push는 하지 않았다. **현재 작업 중지, VN15 미착수.**

## 2026-10-21 임시 파일 정리·전체 변경 커밋

사용자가 불필요 파일 삭제와 전체 변경 커밋·푸시를 명시했다. 완료분만 선별하던 이전 Git 경계와 달리 미완성 Paper/전략/venue/group 초안도 작업 스냅샷에 포함하며 단계 완료·운영 활성 판정은 올리지 않는다. 대시보드 소스도 주 저장소에 포함하고 기존 중첩 Git 이력은 로컬 .git/local-repositories/dashboard.git에 보존한다.

삭제 대상은 과거 credential-free 소스 사본·pytest scratch·중간 focus/retry/Windows 결과와 캐시·사용 종료된 P02 배포/진행기록 스크립트다. 계획된 삭제 30772파일, 422.5MiB. 재사용 테스트, 최신 단계별 source/validation/receipt와 현재 서버 기준선은 유지한다. 원본 운영 데이터·환경·자격 증명은 정리 대상이 아니다. build_server_candidate는 남겨둔 과거 tmp 대신 명시 --baseline-source 입력을 받는다.

과거 지연 측정은 아래 날짜별 요약으로 보존하고 raw 사본은 정리한다. Windows 행은 과거 결과이며 이번 제품 검증은 Ubuntu에서 수행한다.

| 날짜 | 측정 | p99 ms 전→후 | CPU ns/event 전→후 | 범위 |
|---|---|---|---|---|
| 2026-10-21 | windows/burst8 | 48.092 → 11.818 | 461155 → 556098 | 합성 A/B, 운영 성능 아님 |
| 2026-10-21 | windows/sparse1 | 46.050 → 47.597 | 3255208 → 3797743 | 합성 A/B, 운영 성능 아님 |
| 2026-10-21 | linux/burst8 | 57.349 → 28.200 | 149709 → 123868 | 합성 A/B, 운영 성능 아님 |
| 2026-10-21 | linux/sparse1 | 47.517 → 46.671 | 993038 → 1008143 | 합성 A/B, 운영 성능 아님 |

[정리 범위·집계·실행 결과 요약](../runs/20261020-production/cleanup_summary.json)에 삭제 목록과 재실행 전 결과를 기록한다. 임시 source 경로는 실행 당시 위치이며 재사용 사본이 아니다. 재검증은 prepare_source.py와 보존한 manifest/Git 소스로 새 사본을 생성한다.

### 2026-10-21 정리·전체 스냅샷 검증 완료

불필요 30809파일은 Windows 휴지통으로 이동했다. 마지막 검증 사본 555파일은 사용자 지시에 따라 trashbin/worktree-snapshot-20261021로 옮겼으며 /trashbin/과 /tmp/를 Git에서 제외한다. 총 31364파일을 작업 소스에서 분리했다. 영구 삭제는 사용자가 수행한다. 원본 운영 데이터는 변경하지 않았다.

미완성 초안11파일까지 포함한 Python 전체 **1532 PASS/2 DESELECTED**, Ubuntu /tmp·clean env·offline guard로 검증했다. [전체 스냅샷 검증](../runs/20261020-production/worktree-snapshot-validation.json). 이전 완료 기능만의1419 PASS와 범위를 구분한다. 단계 집계14완성/23미완성/14미착수는 유지하며 초안이나 실운영 gate를 완료로 승격하지 않는다. 기존 대시보드 소스를 주 저장소에 포함했고 별도 frontend build는 재실행하지 않았다.

이 문서가 포함된 Git commit이 전체 스냅샷 경계이며 실제 SHA/원격 반영은 git log와 origin/main에서 확인한다. 커밋·푸시 후 사용자 지정 .freebuff/repo-analysis-review.md를 읽으며 새로운 제품 티켓은 시작하지 않는다.

### 2026-10-21 외부 리뷰 확인·필수 rollback 도구 복원

전체 스냅샷 adaf3bf를 origin/main에 반영한 뒤 사용자 지정 .freebuff/repo-analysis-review.md를 읽었다. 보고서의 P02 rollback 보존 지적을 기존 CLEANUP_DELETE_LIST_20261019.md 및 활성 P02 상태 문서와 대조했다. deploy_p02_schema.py를 사용 종료 도구로 분류한 것은 잘못이므로 90847f8의 원본 바이트 그대로 복원해 보존한다. 실제 rollback/배포는 실행하지 않았다. 기존 패키지 p02-server-package.tar.gz도 남아 있다.

정리 수치는 최초 계획30772 + raw 중복37 = 휴지통 이동30809다. 그중 필수 도구1개를 Git에서 복원했으므로 현재 부재 파일은30808개, 마지막 검증 사본555개는 trashbin에 있다. 계획/실행/복원 차이를 cleanup_summary.json에 명시했다.

미착수 리뷰 후속은 ingest의 읽기 전 UTF-8 byte 상한, Paper strategy pause/halt의 자동 취소 의미, dashboard 계정 수 제한의 원자성, 미사용 UI catalog 축소다. 보고서는 Python1532 PASS가 dashboard 검증이나 운영 승인이 아니라고 명시하며, checkpoint frame-budget 지적은 철회했다. 이 후속 제품 변경은 착수하지 않았다.

## 2026-10-24 현재 — TX02B/TX02/TX04A 배치 수락

이전 날짜의 상태는 역사 기록이다. 현재 canonical 집계는 **23 완료 / 16 미완료 / 12 미착수**다. 이번 요청의 연속3개만 로컬 완료했고 전체2266 PASS/외부2건 제외를 확인했다. TX04B와 나머지 티켓은 추가 구현하지 않았다. [전체 티켓 분류·수락 증거](../runs/20261024-v3/batch-tx02b-tx04a/README.md).

## 2026-10-24 후속 현재 — TX04B/TX04/VN13 수락

최신 canonical 상태는 **26 완료 / 13 미완료 / 12 미착수**, 전체2322 PASS/외부2건 제외다. 이번 연속3개만 개발했고 TX05는 착수하지 않았다. [전체 완료/미완료/미착수·증거](../runs/20261024-v3/batch-tx04b-vn13/README.md).

## 2026-10-24 현재 — TX05 로컬 수락

최신 canonical 집계는 **27 완료 / 12 미완료 / 12 미착수**다. 기존 파일 전략/Paper 경제 checkpoint E2E 검증을 마감했고 전체2325 PASS/외부2건 제외다. 이번에는 TX05 하나만 진행했다. [전체 분류와 증거](../runs/20261024-v3/tx05/README.md).
