# P01 결정 기록

작성: 2026-10-18 KST · 계약 `p01-draft-1` · 운영 변경 없음

`ACCEPTED_DESIGN`는 원 계획에 있는 방향을 로컬 구현 계약으로 구체화한 상태다. 구현/테스트/live gate PASS와 구분한다. `OPEN` 항목은 미확정 범위만 차단하며 독립 오프라인 작업은 진행할 수 있다.

## ADR-001 — 기존 SQLite/Parquet와 최근 복구 구현 재사용

상태: **ACCEPTED_DESIGN**

근거: `RuntimeStore`에 manifest/delivery transaction, event runs와 confirmed ACK floor가 있고, `EtfJournal`에 전략별 capital/reservation/cumulative fills/notification/recovery 경로가 있다. `ParquetBatchWriter`는 nested Decimal schema와 tmp/fsync/rename/catalog 순서를, archive는 실제 remote verification/lease/replacement를 이미 보유한다. 이를 이름만 바꾼 새 서비스/catalog로 교체하지 않는다.

결정: P02의 schema/encoding 반복 제거부터 시행한다. 시세 catalog와 주문 원장을 거대한 단일 DB로 합치지 않는다. 계좌 execution owner는 P04, 저장 DB writer/catalog 정비는 P07에서 확정한다. RuntimeStore의 connection lock이 프로세스 간 단일 owner를 증명한다고 보지 않는다. WAL/FULL은 유지한다.

영향/검증: 모델·schema·전략·설정·디스크 layout을 바꾸지 않는 P02 rollback은 해당 diff 복구다. 기존 recovery/ACK/remote proof를 건드리면 독립 저위험 범위에서 이탈하므로 root가 재발행한다.

## ADR-002 — “NATS 30분 중단에도 hot path 독립”의 채택과 transport 분기

상태: **REQUIRED — 2026-10-19 최종 NATS 제거 및 30분 초과 독립 실행 확정. 구현·용량·장애 gate 진행 중**

현재 증거: `NatsJetStreamBus.publish_market_event`는 JetStream `_publish` ACK를 기다린 다음 같은 NATS connection의 `live.*`로 보낸다. `BufferedMarketEventBus`는 bounded queue로 수신 루프 대기를 완화하지만, queue가 차면 backpressure이며 NATS 전체 장애에 독립적이지 않다. Parquet downstream 분리만으로 이 목표를 달성하지 못한다. server snapshot의 CRYPTO_DATA 128MiB와 짧은 일요일 events/s로 30분의 bytes/회복 예산을 계산할 수 없다.

결정 범위는 다음과 같다.

| 선택 | 필요한 구체 근거/작업 | 완료 표시 |
|---|---|---|
| 목표를 필수로 채택 | NATS 외 collector→engine transport, 30분 수용/복구·순서·gap·재접속 계약과 durable 저장 장애 분리. 기존 IPC/transport 재사용과 bounded spool 대안 비교 후 선택 | outage fault/recovery/capacity를 포함한 G-NATS PASS 전에는 미완료 |
| 첫 릴리스는 기존 NATS live/durable 분리 범위 | JetStream ACK 의존 제거 범위와 NATS 자체 장애 한계를 명시하고 affected feed latch/lease/차단 구현 | 30분 독립 목표는 DEFERRED/OPEN으로 계속 노출; 원 목표 완료로 기록 금지 |
| 목표 제외/수정 | 사용자가 보존·중단 허용 수준과 범위를 명시하고 root가 원 계획/ADR/후속 gate를 함께 수정 | 명시 결정 이전에는 제외 처리 금지 |

사용자가 “NATS 중단에도 30분 이상 독립 실행 필수”를 선택했다. P05-S를 필수 작업으로 전환한다. 기존 JSON을 재사용하는 bounded local spool과 Linux UDS data/control 전달을 구현하고, NATS/관측 bridge는 주문·판단 완료 조건에서 제외한다. Core NATS 분리 구현만으로는 이 목표를 완료하지 않는다. MessagePack 전환은 선택하지 않는다. queue/disk sizing·정확 재생·복구 drain·원장/계좌/전환 gate는 계속 필요하다.

이후 사용자는 당초 구상이 NATS 없는 더 가볍고 단순한 구조였음을 재확인했다. 최종 선택은 **local spool → UDS engine / direct Parquet worker**다. 위 표의 NATS 유지 선택지는 과거 검토 이력이며 최종 목표 후보가 아니다. NATS bridge는 migration compatibility로만 격리한다. Telegram/dashboard/status·legacy backlog·archive proof까지 전환하지 않은 상태를 전체 제거 완료로 부르지 않는다. 신규 local delivery의 확인은 기존 Parquet manifest/COMMIT를 재사용하되 구형 NATS ACK floor를 local proof로 위조하지 않는다.

초기 로컬 통합은 producer writer thread의 group flush 이후 durable 입력을 engine에 넘기는 보수적 경로로 검증한다. collector callback은 유한 queue 수용만 하며 per-order fsync를 호출하지 않는다. 이 stream 전달 지연과 기존 Policy A의 빠른 APPENDED 전달은 별도 성능 비교 대상이다. 생산 경로를 선택했다고 성능/용량 입증을 대신하지 않는다.

필요 자료: feed별 encoded ingress peak/지속률·공통 clock/순서·가용 메모리·물리 볼륨별 여유·임시본·검증 완료 drain rate. outage T와 λ/μ에 대해 `μ>λ`, `recovery=λT/(μ−λ)`와 retention/GAP 정책을 검증한다. 새 transport의 효과를 데이터 누락·임의 drop·저장 보존 약화로 만들지 않는다.

## ADR-003 — 시세 비동기 보관과 주문의 Policy A

상태: **ACCEPTED_DESIGN / 실행 원장 구현 미완료**

결정: intent/DecisionEvidence/reservation/client ID/attempt를 제출 전 단일 주문 원장 transaction에 확정한다. 매 주문의 전체 시세 fsync barrier(Policy B)는 병행하지 않는다. DecisionEvidence는 판단 입력 근거를 남기며 전체 lookback의 crash 무손실 재현 보증이 아니다.

부족한 현 구현: generic factory는 order_intents만 기록한다. ETF stage는 기존 예약/주문을 확정하지만 decision evidence/submit attempt/gate epoch가 한 transaction으로 통합되지 않았고, stage 뒤 order bus await가 남는다. KIS broker order number 유실 시 client ID로 재조회할 수 있는 코드도 없다. 따라서 현재 원장 존재만으로 C01/C04 전체를 PASS시키지 않는다.

검증/활성화: commit/handoff/halt/expiry/ACK-lost crash matrix와 adapter 대조가 P04 gate다. 저위험 P02에서 이 상태 machine을 건드리지 않는다.

## ADR-004 — 파라미터 출처와 live 차단

상태: **ACCEPTED_DESIGN / validation 구현 미완료**

결정: [parameters.json](parameters.json)의 code default, historical snapshot, target, required production 값, offline fixture를 분리한다. 필수 production null/UNVERIFIED를 code default/fixture로 채워 live gate를 통과시키지 않는다. Telegram TTL 60초와 성능 여유 1.8λ/2.4λ는 후보이며 실측/승인값이 아니다.

영향: 관련 production 값이 없는 공유 state(P03)/주문(P04)/fanout(P05)/저장(P07)/Telegram(P08)/자원 예산(P09)/성능(P10)의 해당 활성화만 차단한다. 기존 설정값을 변경하지 않는 P02는 고정 오프라인 oracle로 검증한다. “현재 64MiB publisher”를 전체 memory 상한이나 15분 수용 증명으로 사용하지 않는다.

## ADR-005 — v2 schema 호환과 P02 무형식변경 경계

상태: **ACCEPTED_DESIGN**

근거: `models.py`는 SCHEMA_VERSION=2, extra=forbid다. 새 boot/apply/version/revision 필드를 reader 업그레이드 없이 publish하면 기존 reader가 거절한다. Arrow는 decimal128(38,18) nested bid/ask schema를 사용한다.

결정: P02는 schema/field order/nullable/type/serializer/precision/wire bytes를 유지하고 process-local immutable metadata만 재사용한다. event_type cache는 현재 지원 8종으로 bounded다. 신규 event envelope/DecisionEvidence/internal cursor/schema migration은 별도 티켓에서 reader-first 호환과 rollback fixture를 확정한다. scale 초과를 rounding으로 숨기지 않는다.

검증: baseline preimage로 schema/normalized/decoded logical content/hash oracle를 생성한다. baseline/candidate 자신을 같은 함수로 호출해 기대값을 만든 테스트는 동등성 증거가 아니다. binary Parquet file hash 동일을 요구하는 대신 logical/schema/delivery coverage를 비교한다.

## ADR-006 — adapter 기능 존재와 복구 보증 구분

상태: **ACCEPTED_DESIGN / adapter별 공급자 계약 OPEN**

결정: [adapter_capabilities.json](adapter_capabilities.json)의 VERIFIED_CODE는 local code fact다. 외부 ID uniqueness/retention, query completeness, pagination, fill correction, nonce 재사용은 별도 UNVERIFIED로 둔다. public client ID를 보낸다는 사실만으로 안전한 retry를 추론하지 않는다.

확인된 차이: generic BrokerAdapter에는 cancel이 없다. KIS query는 broker order_number이며 stable local client ID를 전송하지 않는다. Binance spot query는 symbol context를 받지 않고, Toss/Arcus는 제한된 history page만 검색한다. GRVT는 client ID 64비트 매핑/32비트 nonce이며 durable collision guard가 없다. Hyperliquid generic submit은 거절한다. Timefolio UI의 position-change 관측은 고유 fill 원장 증거가 아니다. Bybit probe/liquidity testnet은 별도 실행 경로이며 generic adapter 누락을 보완하는 코드 재사용 후보지만 실제 활성과 provider semantics는 미확인이다.

영향: 해당 경로 live owner 전환은 capability blocker가 해소될 때까지 차단한다. P02에서 API/주문 기능/계좌/테스트넷 arm을 추가하거나 활성화하지 않는다.

## ADR-007 — G00 장기 계측과 독립 보존 작업의 관계

상태: **ACCEPTED_DESIGN / root별 티켓 READY 판정 필요**

결정: G00 전체를 통과하지 못해도 관련 source hash와 deterministic fixture/oracle를 고정한 독립 오프라인 계약 설계와 P02 보존 작업은 가능하다. source manifest/기존 실패/fixture hash가 비어 있는 채 구현을 READY로 배정하지 않는다. 72시간·국내/미국/코인 burst 계측·actual writer PRAGMA·운영 이미지 대조·실계좌 대조를 “오프라인 테스트 성공”으로 대체하지 않는다.

우선 READY 검토 후보는 P02-SCHEMA-CACHE(Parquet 단일 제품 파일)다. P02-ENCODING(bus/runtime 단일 편집자)은 encoding/취소 accounting 경계의 별도 계약 리뷰 후 배정한다. 두 티켓의 edit path는 다르지만 현재 encoding을 자동 READY로 올리지 않는다. 전체 G01, G-NATS, live/cutover/성능은 NOT_RUN/UNVERIFIED로 유지한다.

## ADR-008 — P02 encoding 최적화와 accounting 오류 수정

상태: **ACCEPTED_DESIGN / 구현·회귀 미실행**

현재 `BufferedMarketEventBus.publish_market_event`는 bytes와 accepted를 먼저 예약한 뒤 `await queue.put`한다. 그 대기 중 취소되면 예약을 worker에 넘기지 못하고 누수될 수 있다. 반복 encoding 제거로 bytes를 carrier에 유지할 때 이 취소 경계의 소유권을 명시한다.

결정: event + durable bytes + length를 묶어 기존 public event 메서드와 호환되는 내부 경로로 전달한다. 큐 put 이전 rollback과 put 이후 worker 단일 release를 구분하고, 오류/close에서 unsent를 숨기지 않는다. runtime readiness/policy wrapper를 `__getattr__`의 내부 메서드 forwarding으로 우회하지 않는다. 저위험 범위에는 wire/정책/ACK 순서 보존과 이 accounting 오류 수정만 포함한다. timestamp/gap/filter/readiness 의미 변경, 새로운 live transport, retry 정책 변경은 별도 티켓이다.

검증: 고정 fake transport transcript byte 동등성과 cancel/error/close boundary fault matrix. 구현 자체의 encode 횟수 검사만으로 wire/정밀도/순서 보존 검증을 대체하지 않는다.
