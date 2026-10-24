# TX07A → TX08A → TX08 로컬 수락 — 2026-10-24

요청한 다음3개만 수행했다. OpenCode `opencode/deepseek-v4.1-flash-free`가 구현/테스트했고 Codex가 명세·실패 반례·소스 검토·독립 실행·수락을 맡았다. FreeLLMAPI 설정/모델 route는 보존했다. 무료 OpenCode direct route를 사용했으며 upstream 가중치 실체는 UNKNOWN이다. repair delegation은 TX07A0/TX08A0/TX08 1회다. TX08 초기97 PASS는 직접 runtime 재입력 때문에 공동 경로 증거로 수락하지 않았고, 실제 spool/engine에 동일 native 증거를 재전달하도록 보완했다. [초기 검토](../tx08/initial-review/decision.json).

| 티켓 | 완료 범위 | 독립 scoped PASS |
|---|---|---|
| TX07A | 기존 fact/identity/terminal/UNKNOWN 계약 문서와 실제 ledger·ETF·offline owner 테스트, product 변경0 | 79 |
| TX08A | 계좌 한도 충돌 방지: 기존 register 한 메서드만 수정 | 133 |
| TX08 | 실제 두 파일 전략의 동일계좌 Paper·부분체결·체크포인트 복구·취소 소유권 공동 gate, product 변경0 | 97 |

고정 source `04c9f576925c4ad4c8b559501e09055e52201b83`: **2348 PASS / 2 live-data 제외**. [전체 회귀](full-regression.json), [입력 해시/명령](full-source-inputs.json), [소스 범위](source-review.json), [메타데이터 범위](final-scope-status-validation.json), [배치 수락](acceptance.json)에 실제 종료코드와 증거가 있다. scoped와 전체 검사는 중복 범위가 있으므로 합산하지 않는다.

TX08A 수정 전 owner 반례는112 PASS/2 FAIL(exit1): 두 전략이 동일 계좌의 notional/capital 한도를 다르게 선언해도 등록됐다. 수정 후 두 반례와 Decimal 동등값 허용 반례를 포함해133 PASS(exit0)다. worker 최종 실행130 PASS에 reviewer3개를 더한 수다. account 한도는 모든 등록 상태와 겹치는 계좌에서 일치해야 하며 DB claim/plugin 생성 전 검증한다. 전략별 한도/funding buffer와 실제 다른 계좌의 한도는 독립이다. 기존 direct ledger API나 native-live 계좌 규칙을 재작성하지 않았다.

TX07A field 문서의 strategy 바인딩 한 곳은 Codex가 소스와 대조해 수정했다: Paper의 명시 strategy 인수, ETF job_id, offline-owner metadata fallback의 권위를 구분한다. 제품 코드는 변경하지 않았다. worker의 초기 실패/collect-only는 성공 증거로 세지 않으며 마지막 실제 테스트 실행/독립 실행만 수락 근거로 기록했다.

TX08 수동 경제 oracle: seed100000, multiplier10, 양방향2주문 reserve408 → 각1 부분체결 cash100018/physical0/virtual+1,-1/reserve204/margin200 → 실제 cut3 checkpoint 복구 → 동일 native 증거를 실제 transport4로 재전달(경제/콜백/apply_seq 불변) → alpha 취소 reserve103.02 유지 → transport5에서 beta 잔여1 체결 cash100016.99/physical-1=virtual+1,-2/reserve0/margin301. 최종fill3, 주문2, 파일 callback각1, fee alpha.99/beta2.02와 명시 netting adjustment20을 확인한다. 중복 입력/취소가 재수수료·재해제·재주문을 만들지 않는다. 기존 companion 테스트가 stale admission/owner fence/reservation/restart도 검증한다.

C3/KP/KD·Mock/Mock2·TOSS DATA_ONLY·기존 거래소/cloud·Golden을 보존했다. 신뢰된 협조 Python/PAPER 계약이며 process isolation, max-min fairness, 실계좌·native venue·cross-venue 원자성을 보장하지 않는다. TX07B/TX08B 및 네 번째 티켓은 착수하지 않았다.

현재 **30 완료 / 10 미완료 / 11 미착수**. 분류는 로컬 계약 상태다.

- **완료 (30):** VN00, VN01, VN02, VN03, VN04A, VN04B, VN05, VN06, VN07, VN08, VN09, VN10, VN11, VN12, VN13, VN14A, VN14B, VN16, TX00, TX01, TX02A, TX02B, TX02, TX04A, TX04B, TX04, TX05, TX07A, TX08A, TX08.
- **미완료 (10):** TX03A, TX03B, TX03, TX06A, TX06B, TX06C, TX06D, TX06E, TX06, TX08B.
- **미착수 (11):** VN15, VN17, VN18, VN19, VN20, TX07B, TX07, TX09, TX10, TX11, TX12.

기존 완료27개와 비선택48개 task 객체·모델 profile·permissions·tooling·DAG 간선을 보존했다. 로컬 candidate commit/적용만 수행하며 원격 push/배포/실주문은 수행하지 않는다. 기존 사용자 dirty174건은 별도 적용 receipt로 보존을 검증한다.
