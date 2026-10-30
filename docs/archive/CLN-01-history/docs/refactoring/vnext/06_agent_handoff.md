# vNext 06 — 다음 Agent 인계

기준일 2026-10-20 KST. 상태 **PLAN_ONLY_COMPLETE / IMPLEMENTATION_NOT_STARTED**. 이번 작성으로 P단계/gate/운영 상태를 승격하지 않는다. 읽기 순서: 이 문서 → [01 현황](01_current_state_delta.md) → 담당 티켓의 [04](04_implementation_plan.md)/[tickets.json](tickets.json) → 관련 [계약](03_contract_delta.md)/[검증](05_validation_and_cutover.md) 절만. 원본과 이미 읽은 파일 전체 재독 금지.

## 1. 확정 요구와 설계 추천의 구분

**기존 사용자 확정:** 최종 NATS 제거, >30분 독립, 신규 broker/DB 서버 없음, SQLite/Parquet/PyArrow/adapter reuse, Policy A, C3/KP/KD·Mock/Mock2·현재 scope·정밀도/수수료/복원 유지, 비활성 전략 활성화 금지, KR 누락 분봉 hotfix 제외.

**이번 추천(후속 구현 기준으로 승인할 안):** A1 durable-first를 첫 후보로 유지; feed별 장애 격리; temporary IPC failure 중 Collector의 기록 지속과 bounded catch-up; narrow full-state checkpoint+delta restore+proof 기반 GC; 기존 ETF journal/permit에 canonical account lease·identity·단조 상태를 보완; Telegram은 동일 owner의 제한된 control lane에 연결. 새로운 서비스는 만들지 않는다.

**보류:** A2/A3 거래 경로는 동일 trace A/B와 데이터 품질/복구·경제 효과 증거 후 별도 결정. generic ExecutionOwner의 offline 제한 해제, derivatives/crypto live 확장, 새 전략/종목, 위험 축소 예외 확대는 이번 계획의 자동 후속이 아니다.

## 2. 코드 재사용 및 남은 blocker

재사용: immutable MarketEventPacket, native sequence/Decimal 모델, HotPathStore/PreparedHistory/StrategyModule, MarketSpool/SpoolReader/UDS frame, EtfJournal·KIS permit/누적 체결, Parquet manifest/hash/readback·reader lease, TelegramState inbox/outbox와 local facts reader, ProcessLease/runtime_paths 검사. `local_snapshot`은 dashboard projection으로만 재사용한다.

핵심 blocker: engine 전역 `_source_error`·전체 flush; publisher transport error→ingress invalid; checkpoint 부재/GC 금지와 prefix scan; actual account-wide owner fencing 미확인; SharedStrategyRunner shadow 전용/NATS source; `/pause /halt /resume /pnl` 미연결; 현재 runtime.yml 의미 drift; 생산 λ/μ·peak·실제 >30m·24h/15m outage·72h 증거 없음.

기존 `contracts/parameters/capabilities`의 미구현/OPEN 설명과 실제 코드를 구분한다. 과거 G03-G12 묶음 NOT_RUN도 후속 scoped 구현을 취소하는 지시가 아니다. [conflict_register](conflict_register.md)를 따라 해석하고 원문을 덮어쓰지 않는다.

## 3. 다음 티켓 1~3개

아래는 **사용자가 구현을 지시한 뒤** dispatch할 후보다. 현재는 모두 `PLANNED_NOT_AUTHORIZED`다. 고정 HEAD `6443eb94133bc0075b0860885829e112adc657c8`, 현재 allowlist source-set `1b0dbadca1398b3350324443b55549d83453bc747776dd9837500ccdec7a903e`, 파일별 원문 before SHA는 source_manifest/tickets JSON에 있다.

| 순서 | 범위·담당 | 완료물과 다음 조건 |
|---|---|---|
| 1 VN00 / root+배포 조사 | 제품 편집0, 최신 Compose 의미·source/image/mount·새 test-copy baseline 조사 | 현재 `b8453deb…` runtime.yml 변경 이유, absent 연구 requirements4를 제외한 credential-free 사본·신규 run. G00 전체 PASS와 구분 |
| 2 VN01 / pipeline 구현자1 + Luna 독립 diff 리뷰 | local_pipeline의 bounded fault reason/stage/cursor/age/lag만, 기존 test_local_pipeline 회귀 | 정확 candidate·회계 불변·계측 비용 확인. 이 결과로 fsync/queue/IPC 원인을 구분 |
| 3 VN02 / engine 구현자1 + 독립 safety review + root | local_engine와 local_snapshot의 feed별 queue/health invalidation | 두 feed interleave oracle, healthy FIFO 보존·dependent 차단·bounded queue. caller 연결 VN03 완료 전 운영 투입 불가 |

Agent에게 전체 코드베이스를 다시 읽게 하지 않는다. packet에는 task ID, source/contract hash, read/edit 경로, 대상 함수와 caller, 앞선 review findings, oracle와 acceptance, 금지 범위만 전달한다. 파일 수정 후 후속 티켓은 그 새 hash로 재기준화한다. 과거 hash를 맞추려고 코드를 되돌리지 않는다.

## 4. 권장 역할과 검토 순서

| 역할 | 목표/지침 | 산출과 금지 |
|---|---|---|
| root / PM·통합 | source·계약·DAG·file owner·증거 유효성 관리. 한 번에 구현1개 기본 | 새 run receipt·현재 상태. 자체 리뷰를 독립 리뷰로 기록하지 않음 |
| 아키텍처/계약 검토 | 담당 C delta의 실패/복원·scope만 검토 | 대안·불변조건·blocker. 제품 편집/운영 gate 개방 금지 |
| 구현자 | 작은 edit_paths만; 기존 helper와 caller 사용; KISS/DRY/YAGNI | diff+관련 fault/golden 시험. 임의 새 framework/환경/계좌 변경 금지 |
| Luna 단순 리뷰 | diff·이미 제공된 caller·실패 시나리오 검토 | 구체 trigger/영향/검증 누락. 원본 전체 재독·직접 수정 금지 |
| 고위험 독립 검증 | 원장/lease/GC/checkpoint/cursor 상태기계 및 별도 oracle 검토 | 정확 test command/exit/hash/한계. fake broker 결과를 실제 API 보증으로 승격 금지 |
| 최종 통합 | root가 scoped test→독립 review→현재 candidate 통합 결과 판정 | REVIEW/PASS_SCOPED/NOT_RUN/MISSING_EVIDENCE 구분. production은 별도 |

논리 역할과 실제 모델/OS 프로세스를 구분한다. sub-agent를 썼다는 이유로 새 서버 프로세스를 만들지 않는다. 슬롯을 채우기 위한 병렬 작업 금지. 같은 파일의 서로 다른 함수도 한 editor 소유다. 구현→리뷰→수정→관련 검증을 순차 진행하고 새로운 실패/변경이 없으면 전체 시험을 반복하지 않는다.

## 5. 운영 전환 전에 사용자 결정이 필요한 사항

1. **추천 증분안/C03~C09 채택과 로컬 구현 범위.** 이번 PLAN-ONLY는 코드 수정 권한이 아니다. A1 기본은 추천이며 A2/A3는 아직 미채택이다.
2. **명령 의미:** pause/halt의 scope, `/halt all`의 current account set, 기존 ETF reduce-only 예외, scoped resume, TTL·숫자 allowlist. halt는 자동 flatten이 아니다. 정책은 원장/owner 근거와 결합해야 한다.
3. **PnL/Return 정의:** 기준 시각·통화·opening equity·입출금/fee/funding, actual/assumed/복구 구분. 근거 없는 return 계산은 unavailable 표시를 기본으로 한다.
4. **실제 전환 범위·시간과 신규 real 권한 여부:** VN19의 검증된 패키지를 본 뒤 결정. 기존 Mock/Mock2 보존과 신규 실계좌/crypto 활성화를 분리한다. 새 실거래가 필요하지 않으면 비활성 유지.

NATS 제거 여부와 KR hotfix 보류는 이미 확정되어 다시 묻지 않는다. 실제 λ/μ/lease/RTO/resource 수치는 운영 정책 선택을 핑계로 임의 숫자를 채우지 말고 측정한다. 미확정 항목은 해당 티켓/활성화만 차단하며 독립 offline 작업을 모두 멈추지 않는다.

## 6. 절대 금지

- 이번 작업의 권한을 제품 수정/서버 게시/배포/주문/Telegram 발송/데이터 삭제로 확대하지 않는다.
- 원본 `.env`가 있는 repo에서 제품 import/pytest 금지. credential-free copy+clean env+guard를 사용한다.
- reset/clean/stash, 오래된 source로 사용자 코드 덮기, live DB를 과거 backup으로 복구, NATS stream reset, 유일본/원격 객체 삭제 금지.
- 수집 종목·전략·Mock/Mock2·수수료·정밀도·history lookback 변경, 누락 bar 합성/숨김, KR repair hotfix 재개 금지.
- UI snapshot을 recovery checkpoint로 쓰거나 APPLIED/healthy/가상1801초/단일 표본으로 운영 완료를 선언하지 않는다.
- 기존 실행 기록·fixture/oracle/PASS 의미를 수정하지 않는다. 후속 상태는 **새 run**에 기록하고 승인 뒤 기준 문서에 연결한다.

최종 서버 전환 준비는 [05의 VN-G0~4](05_validation_and_cutover.md)에 따른다. 그 전까지 결과 상태는 구현·scoped offline·패키지 준비로만 표시한다.

## 7. 이번 산출물 검증

새 문서 7개(요청 문서6+충돌 등록부), JSON 4개(source manifest·서버 읽기 전용 identity·source comparison·23개 tickets)를 작성했다. 로컬 링크, JSON 파싱, 23개 티켓의 DAG, 현재 존재한다고 지정한 심볼 68개, manifest 531개 hash를 확인했다. 분석 전 추적 파일 1116개는 변경0, HEAD 변경0이다. 독립 Luna 문서 리뷰에서 발견한 VN16→VN09 도식 누락을 반영했다. 제품 pytest/benchmark/장기 시험은 이번에 실행하지 않았다.
