# Production 08 — 공통 실행·포트폴리오 계약

기존 C01~C09 유지 + 다음 **C10~C17 추가**. 이 계약은 선택된 local 구현의 기준이며, **2026-10-23 문서 단계는 제품 코드를 변경하지 않는다.** 실제 계좌 mutation/승격 권한은 포함하지 않는다.

> **2026-10-23 binding scope correction:** [tech_lead_scope_decisions.json](./reuse_audit/tech_lead_scope_decisions.json)·[revised_dag.json](./reuse_audit/revised_dag.json)·[VibeTrading_Minimal_Reuse_Plan_20261023.md](./VibeTrading_Minimal_Reuse_Plan_20261023.md)가 이 계약의 적용 범위를 정한다. 현재 동작하는 ETF/venue/Paper/cloud 경로를 보존하고, 새 connector/universal real margin/lifecycle/cross-venue atomic group/새 uploader를 요구하지 않는다. 계약은 **선택된 기존 mode/route**에 적용하며, 아래 C16은 DEFERRED, C13/C17의 broad product/promotion 완성은 NON-BINDING이다. 안전 불변조건(cancel ACK≠terminal fill, UNKNOWN reservation 유지, TOSS DATA_ONLY, monotonic fill identity, per-mode one writer)은 그대로 유지한다.

| ID | 계약 | 불변조건·검증 |
|---|---|---|
| C10 Strategy/Mode | 기존 on_event→list[Signal] 유지. 주문·취소·정정은 bounded action ID와 strategy/config/version/account/mode에 연결 | strategy 활성≠live permission. PAPER에서 venue client 생성·외부 mutation0. TOSS account 거절 |
| C11 Account authority | venue/account/subaccount/environment/product-margin domain별 한 owner. 같은 계좌가 다른 DB path를 써도 같은 protected lease key | 협조 lease는 타 host/legacy를 fence하지 못함. 실제 cutover stop/restart 차단 별도 |
| C12 Attribution | fill은 account/strategy/intent/order/fill/instrument/side/position-side/group·fee currency에 귀속 | fill fingerprint 충돌 거절. virtual 합계와 physical 차이를 external/imported/inflight/funding/correction/gap으로 구분 |
| C13 Product economics (selected mode) | 선택된 **기존** cash/linear Paper product의 reservation/margin만 재사용. inverse/cross/hedge/portfolio margin은 명시 수요까지 DEFER | 미확인 margin/equity를0으로 쓰지 않음. 통화 FX 없는 합산 금지. 실제 venue margin 공식을 일반 leverage로 대체0. universal real margin engine 요구0 |
| C14 Lifecycle | new/cancel/replace/partial/full/reject/expire/UNKNOWN 별도 사실. Client ID는 namespace·mapping 고정 | timeout/부분조회는 미제출 증거 아님; cancel ACK만으로 reserve 해제0; fill-first/late fill 단조 반영 |
| C15 Local Paper | receipt/order-arrival/cancel-effective 순서, depth·trade-volume 소비, 보수적 maker queue | touch-only maker fill0, 미래book 사용0, 유동성 중복사용0, restart replay duplicate fill0 |
| C16 Cross-venue group (DEFERRED) | 기존 observational child/group fact만 보존. cross-exchange atomic execution은 core 아님 | 분산 원자성 주장0, UNKNOWN leg 재제출0, 승인되지 않은 자동 hedge/unwind0. TX08B 선행·완료 요구0 |
| C17 Lifecycle/export (selected) | 기존 strategy/version/config/manifest 등록과 Paper→사용자 승인 경계를 재사용; plugin metadata linkage만 보완 | 운영 백테스트/탐색/학습0. Paper 자산/포지션을 live로 복사0. universal lifecycle engine 요구0 |

## 정책 경계

초기 공통 Paper 회계는 명시적으로 제공된 **검증 가능한 상품 metadata**로 동작한다. linear/inverse/cash의 계약 단위·손익 계산은 구분하고, live margin은 venue 권위 account/margin snapshot과 대조한다. Paper의 고정 initial/maintenance rate·fee·latency 모델은 **simulation assumption**이며 venue 공식 margin 보증이 아니다. 규격/통화/position mode/가격·수량 정밀도가 없으면 주문을 거절한다.

동일 account의 복수 전략 예약은 owner의 하나의 transaction에서 직렬화한다. 반대 주문의 미체결 위험은 낙관적 netting하지 않는다. confirmed physical net과 strategy virtual long/short를 분리한다. hedge mode는 position_side별로 관리하며 unsupported mode는 명시 거절한다.

Cash/fee/funding/realized PnL 변화와 fill receipt/주문잔량/reservation/virtual·physical position은 **같은 권위 원장 transaction**에서 반영한다. 주문 사실을 담은 기존 SQLite WAL/FULL을 우선 사용한다. Paper DB 경로·scope는 실계좌 journal과 물리적으로도 분리하고 scope mismatch는 open 실패다.

순수 계산은 Decimal, 유한 값·tick/lot·notional 검사, mutation 전에 intent/evidence/예약/attempt/permit COMMIT를 유지한다. negative maker rebate와 fee currency는 비용 부호를 잃지 않게 명시한다. mark/funding 미관측은 UNVERIFIED이며0으로 보정하지 않는다.

## Action 및 event 경계

기존 `OrderIntent`/`Signal` wire v2를 변경하지 않는다. 새 공통 action은 NEW/CANCEL/REPLACE, 대상 원장 order key, explicit environment, expiry, post-only/reduce-only/TIF, group ID를 내부 계약으로 전달한다. 취소·정정도 해당 strategy의 working-order namespace만 접근한다. claim만으로 risk-reducing을 신뢰하지 않는다.

canonical execution event는 실제·Paper 모두 동일한 의미의 accepted/rejected/fill/cancel/funding/account snapshot을 제공한다. 시세 이벤트와 체결 이벤트의 durable/ACK·cursor 의미를 합치지 않는다. private stream gap은 계좌 risk 차단/재대조 사유이며 시세 Collector 종료 사유가 아니다.

신규 queue는 count/bytes/age/inflight 상한과 fairness를 가진다. fill·risk·reconcile 처리는 낮은 우선순위 PnL 보고와 분리한다. sync Python callback의 사후 runtime 측정은 hard preemption이 아니므로, 악성/무한 callback 격리가 입증되지 않은 runtime을 전체 Production-ready라고 표시하지 않는다.
