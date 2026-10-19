# 리팩토링 단계 작업과 에이전트 배정

## 현재 상태 — 2026-10-19

**최종 NATS-free 방향 확정. P02 schema cache 서버 적용, 나머지 통합 구현은 비활성 후보.** 아래 '최초 배정 이력' 이후는 10월 18일 최초 배정 당시의 기록이다. 최신 phase/gate는 이 표와 run의 `tasks.json`/`gates.json`을 따른다.

2026-10-19 최신 지시: 누락 분봉 관련 hotfix/자료 수리를 중단한다. 원 계획으로 복귀해 완료 코드를 서버 릴리스에 보존하고, 작은 검증 단위부터 운영에 반영한다. 첫 단위는 P02 schema cache를 기존 storage/crypto-storage 두 이미지 위에 한 파일로 적용하는 것이다. P03~P08 통합 v5는 Windows 1078 PASS/9 SKIP, Linux 1087 PASS이며 서버에는 비활성 후보로 보존한다. 현재 주 작업은 **P09 역할별 릴리스 구성 → P10 운영 증거 → P11 점진 전환**이다. 단순 리뷰는 Luna, 이미 읽은 파일은 변경분만 검토한다.

| 단계 | 실제 산출물·주요 목표 | 현재 범위와 다음 조건 |
|---|---|---|
| P00 | baseline 437 files·schema/encoding oracle·서버 readonly 조사 | baseline PASS, 연속 72시간/전체 role·계좌 증거 미완료. 장기 계측 v1/v2 coverage gap |
| P01 | C01~C07·parameters·capabilities·ADR | ADR-002 최종 NATS 제거 확정. 생산 수용률/용량·adapter 위험 입력 미확정 |
| P02 | Arrow schema/field cache·immutable packet·bounded publisher | schema cache만 실제 storage/crypto-storage 적용, healthy·SHA 확인. packet 변경은 후보, 운영 E2E 개선 미판정 |
| P03 | 공유 HotPathStore·인과적 history·SharedStrategyRunner | replay/shadow 구현·격리 검증. 비활성 generic 전략을 새로 켜지 않음 |
| P04 | account ledger/control/owner·ETF dispatch permit·TTL/예약·체결 버전 | fake adapter/ETF seam 구현·회귀. 실제 계좌 대조/legacy fencing/live capability 미완료 |
| P05 | local spool·Linux UDS·pipeline·local engine·관측 spool | NATS-free 기본 경로 v5 통합 격리 PASS. 운영 전환과 실제 30분 검증은 미완료 |
| P06 | C/H recovery·state-only replay·one-use initial resume barrier | 기존 prefix를 주문으로 재생하지 않음. ETF 별도 복원/bar conflict/startup lease 보완 |
| P07 | SHA/remote verification·reader lease·direct local Parquet/facts | batch manifest→progress 구현. duplicate content/WAL 검토 보완 중. 무증거 spool GC 금지 |
| P08 | Telegram durable inbox/outbox·local facts consumer | ACK 유실/취소된 executor/재접속 보완 완료. 추가 pinned-reader WAL 상한 보완은 v6 관련 Windows46/Linux47 PASS, Luna 리뷰 통과 |
| P09 | mount/device/volume 계약·role source inventory·local runtime | 실제 모듈 소유자 기준 116파일 합성. P02 서버 적용 후 새 preimage로 다음 v6 후보 구성 |
| P10 | 전체 회귀·Linux UDS/장애·가상 1801초·component CPU | v5 전체 Windows1078 PASS/9 SKIP/2 DESELECT, Linux1087 PASS/2 DESELECT. v6 변경 관련 Windows46/Linux47 PASS |
| P11 | cutover/rollback·source/image/mount review package | 실제 image/production budget/계좌·복원·장중 gate 전 전환 차단 |
| P12 | legacy retire·NATS 서비스 제거·인수 | 구형 backlog 보존과 새 경로 안정화 후. 불필요한 신규 spool→NATS bridge는 소스에서 제거 |

현재 역할: root는 runtime/config·테스트·최종 판정, `execution_review_2`는 독립 리뷰 후 snapshot/dashboard 구현, `control_review_2`는 ETF/관측 구현 뒤 저장 독립 리뷰·수정, `storage_fanout_review_2`는 spool/storage 구현 뒤 facts/Telegram 독립 리뷰·수정이다. 역할 변경을 구분하고 한 파일에 한 편집자를 둔다. 운영 주문·외부 메시지·배포·기존 DB 변경은 실행하지 않았다.

## 최초 배정 이력 (2026-10-18)

run: `20261018-stage-assignment` · 2026-10-18 KST

이 보드는 [단계 계획](../../refactoring_plan.md), [현재 맥락](../../refactoring_context.md), [역할 지침](../../refactoring_ai.md)을 실제 작업 큐로 연결한다. 세부 티켓은 [tasks.json](runs/20261018-stage-assignment/tasks.json), 단계 판정은 [gates.json](runs/20261018-stage-assignment/gates.json), 재개 지점은 [handoff.md](runs/20261018-stage-assignment/handoff.md)에 있다.

## 이번에 배정한 에이전트

| 실행 주체 | 배정 역할 | 완료한 작업 | 소유 산출물 |
|---|---|---|---|
| root | 오케스트레이션·기준선·테스트·통합 판정 | 작업 큐, source snapshot, 격리 baseline, fixture/oracle, gate 통합 | baseline/fixture manifest, run 기록과 이 보드 |
| `p00_code_audit` | P00 현황·코드 대응 조사 | 실제 심볼 72개·호출 관계 76개, E03 149개 source hash 대조 | [code_map.json](code_map.json), [current_state_audit.md](current_state_audit.md) |
| `p01_contract_design` | P01 계약·작업 분해 설계 | C01~C07, 코드 기본값·미확인 운영값, adapter capability, ADR 8개 | [contracts.md](contracts.md), [parameters.json](parameters.json), [adapter_capabilities.json](adapter_capabilities.json), [decisions.md](decisions.md) |
| `p00_independent_review` | 독립 리뷰·검증 조건 점검 | source snapshot, 테스트 격리, P02 위험·계약 검토 | [p00_review.md](p00_review.md) |

모델은 실행 환경의 inherited model을 사용했다. 특정 Sol/Astra/Luna로 전환했다고 주장하지 않는다. 모델 ID는 도구에서 제공되지 않아 NOT_EXPOSED로 기록한다. 동시에 root+서브 에이전트 3개를 넘기지 않았고, 한 파일에 한 편집자를 배정했다. 현재 세 서브 에이전트의 산출물을 통합했으며 제품 코드 편집 작업은 아직 dispatch하지 않았다.

## 단계별 작업 지정

| 단계 | 지정 작업 | 담당 역할 | 현재 상태·진입 조건 |
|---|---|---|---|
| P00 | 기준선·운영 차이·source/fixture·테스트 | root + 현황 조사 + 독립 리뷰 | 로컬 준비 완료, **전체는 PARTIAL**. 최신 운영·72시간 계측 등 남음 |
| P01 | C01~C07 binding·상한·adapter·ADR | 계약 설계 + root 검수 | 초안 통합, **P02 schema 범위만 검토 완료**. 전체 G01 PARTIAL |
| P02 | 먼저 Arrow schema/field metadata 재사용, 다음 encoded packet | 구현자 1명 + 독립 리뷰 + root | **P02-SCHEMA-CACHE READY**, encoding DRAFT_OFFLINE. 둘을 한 티켓으로 합치지 않음 |
| P03 | 공유 HotPathStore·feature·history·전략 입력 adapter | 시장 상태 구현자 | G01/G02 이후. 전략 경제적 출력 oracle 보존 |
| P04 | 계좌 owner·원장 예약·체결·halt/reconciliation | 실행 영역 구현자 + 독립 리뷰 | G01/G03 evidence 이후. DB별 writer 범위에 Telegram sender 포함 |
| P05 | live/storage fanout·bounded queue·실패 전파 | transport 구현자 | G03/G04 이후. spool/UDS는 ADR 필요 시만 |
| P06 | C/H replay 경계·warmup·no-submit | 복구 구현자 | G05 이후 |
| P07 | Parquet·catalog·원격 검증·GC | 저장 구현자 | G05/G06 이후. 기존 exact run/ACK/lease 재사용 |
| P08 | Telegram inbox/outbox·local control·PnL | 제어·보고 구현자 | G04 이후 독립 개발 가능, 통합은 저장 계약과 대조 |
| P09 | 역할별 배포·mount·관측·전체 자원 예산 | root + 배포 검토 | G04~G08 이후 |
| P10 | 동일 trace A/B·장애·shadow·장기 사용량 | 독립 검증자 + root | G02~G09 이후. Windows PASS와 운영 성능을 구분 |
| P11 | 구체적 cutover/rollback 패키지·계좌 전환 | 권한을 가진 root/운영 역할 | G10과 해당 운영 전환 범위 필요 |
| P12 | 검증된 legacy retire·인수 | root + 정리 검토 | G11 안정화·보존 조건·삭제 범위 확인 뒤 |

후속 역할은 선행 조건에 따라 슬롯을 재사용해 배정한다. 위 미래 역할을 이미 실행 중인 별도 에이전트로 표시하지 않는다. 모든 단계의 실제 파일·함수·검증 목표는 원 계획과 티켓에 연결한다.

## 다음 구현 티켓

**P02-SCHEMA-CACHE**를 첫 구현 작업으로 지정한다. 제품 수정 파일은 `vibetrading/parquet_store.py` 하나, 새 테스트는 `tests/refactoring/test_schema_cache_equivalence.py`다. 기본 대상 함수는 `_schema`, `_normalize`, `ParquetBatchWriter._materialize`다. `_write_staged` 등 다른 호출부는 필요 근거를 리뷰한 뒤 범위를 확장한다.

완료 기준은 8종 schema의 field/type/order, Decimal/null/nested array/raw payload, normalized 값·canonical logical hash·unsupported 오류 동등성이다. cache 상한을 고정하고 실제 component CPU/event를 비교한다. 원장·ACK·wire format·전략·계좌 설정은 티켓 범위에서 바꾸지 않는다. source/contract/fixture hash, 합쳐 확정한 기존 회귀 명령은 tasks.json에 있다.

P02-ENCODE-PACKET은 기존 queue.put 취소 시 예약 byte/accepted 회계 누수와 내부 가변 객체·wrapper 우회 위험을 별도로 검토한 뒤 READY로 전환한다. 현재 구현됐거나 gate를 통과했다고 표시하지 않는다.

## 검증 결과와 남은 맥락

소스 사본 baseline 결과는 **584 passed, 1 skipped, 2 deselected**다. [known_failures.md](known_failures.md)에 하네스 오류의 해결·skip·미실행 운영 조건을 기록했다. 7개 기존 합성 fixture 그룹과 8종 schema의 수정 전 oracle를 [fixture_manifest.json](fixture_manifest.json)에 고정했다. 실제 운영 wire trace를 수집했다고 표현하지 않는다.

12:06 KST 서버 실측은 역사적 관측으로 유지한다. 이 run의 로컬 baseline으로 최신 계좌 소유권·writer PRAGMA·장중 사용량을 대신 검증하지 않는다. NATS 30분 독립은 ADR-002 OPEN이며 운영 전환/데이터 삭제는 이번 작업 큐 구성 결과에 포함되지 않는다.

## 구현 진행 갱신 (2026-10-18T21:31:08.526825+09:00)

- 사용자 완전 수행 요청에 따라 로컬 코드 구현·격리 테스트를 진행 중이다.
- P02 schema 캐시: 구현·root 독립 리뷰·전체 608 PASS. 구성 요소 CPU/event 비교 증거 저장.
- P02 packet/버퍼: 16종 기준 전송 transcript 일치. 관련 62 PASS, 전체 623 PASS. 독립 리뷰가 발견한 class-level 계측 wrapper 우회를 수정하고 추가 16 PASS. 최신 전체 회귀는 이후 통합에서 재실행한다.
- P03: 공유 상태 구현 티켓 설계 중. P04: 주문 안전성 감사 후 구현 티켓 구체화 중. P07: 기존 저장 경로 재사용 및 확인된 hash/cache 무결성 결함 수정 중.
- 서브 에이전트 사용량 제한이 해제되어 세 역할을 재개했다. 운영 배포·실계좌 주문·Telegram 발송은 실행하지 않았다.
