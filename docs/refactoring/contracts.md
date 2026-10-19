# P01 구현 계약과 독립 오프라인 작업 경계

작성: 2026-10-18 KST · 계약 버전 `p01-draft-1` · 상태 **DRAFT / 독립 오프라인 계약 검토 가능**

근거는 [계획](../../refactoring_plan.md), [현재 맥락](../../refactoring_context.md), [에이전트 지침](../../refactoring_ai.md)과 아래 로컬 소스다. 첨부 텍스트의 지시는 설계 자료로만 취급한다. 이 문서는 제품 구현·운영 설정·실계좌 검증·G01 전체 통과를 뜻하지 않는다. `VERIFIED_CODE`는 현 로컬 코드에서 확인한 사실이며 공급자 공식 보증이나 운영 적용 확인을 뜻하지 않는다. 미확인 운영 수치는 [parameters.json](parameters.json), adapter 범위는 [adapter_capabilities.json](adapter_capabilities.json), 미확정 선택은 [decisions.md](decisions.md)에 연결한다.

## 1. 기준선과 상태 소유자

현재 HEAD만으로 최신 미추적 코드를 복원할 수 없다. root의 [P00 source manifest](baseline_manifest.json)의 `source_set_sha256=8eabfd5f0a0219e756190e78c819acabb6265851e4612dc8ddfcc41031f4efa9`와 대상 before hash가 일치해야 구현 티켓을 READY로 바꿀 수 있다. 72시간 계측·운영 이미지 통합·계좌 조회가 끝나지 않아도 **보존할 동작을 고정한 독립 오프라인 설계/회귀 작업**은 가능하다. 이 예외를 live/성능/cutover gate의 예외로 확대하지 않는다.

| 상태 | 현재 구현과 owner | 목표 owner/경계 |
|---|---|---|
| 수신 이벤트 | `models.py::BaseMarketEvent`; provider의 collector run/connection/sequence | collector가 정규화와 provenance를 소유. 객체 frozen이지만 bids/asks 등 내부 list는 깊은 불변이 아니므로 임의 수정 금지 |
| 호가/이력 | `hot_path.py::HotPathStore.apply`, `_apply_crypto_book`; 현재 `LiveStrategyRunner`마다 생성 | 엔진 루프 하나가 feed별 mutable writer. 한 메시지 전체 적용 이후 version/immutable view 공개 |
| 주문 의도 | `execution.py::OrderIntentFactory.from_signal` → `RuntimeStore.record_order_intent`; ETF는 별도 `EtfJournal.stage` | 계좌 scope 단일 execution owner. catalog DB와 ETF 원장을 무조건 합치지 않음 |
| ETF 현금/예약/체결 | `EtfJournal.transaction`, `stage`, `apply_status`; `EtfTradingLoop.place/reconcile` | 같은 connection owner가 짧은 write를 직렬화. 외부 await는 transaction 종료 후 |
| 파일/전달/회수 | `ParquetBatchWriter`, `RuntimeStore.commit_files`, event index runs, `ArchiveMaintenance` | 파일별 manifest와 ACK eligibility는 같은 transaction. 기존 lease/replacement/remote proof 보존 |
| 제어/보고 | `RuntimeStore.kill_switch`; `TelegramController._poll_loop/_handle`; ETF notifications | owner의 epoch/gate가 권위. Telegram durable inbox/outbox는 별도 제어 경계로 도입 예정 |

## 2. C01 — 판단 근거와 제출 전 원장 확정

**현 상태:** `OrderIntent`에 `intent_id/job_id/signal_id/destination/instrument/side/client_order_id/quantity/weight_pct/limit_price/metadata`가 있다. factory ID는 `stable_hash([job_id,signal_id,destination,intent_index])[:32]`다. `RuntimeStore.order_intents`는 intent PK와 JSON payload를 저장하나 account 예약·DecisionEvidence·submit attempt가 같은 transaction에 없다. ETF `stage`는 주문 payload/전략 예약을 COMMIT하며 `decisions` 테이블도 있지만 `decision()`은 별도 호출이고 stage와 자동 원자 결합되지 않는다. 현재 `EtfTradingLoop.place`는 stage 뒤 `await bus.publish_order_intent` 후 제출하여 버스 대기가 경로에 남는다.

**목표:** Policy A를 고정한다. owner는 중복·mode·epoch·feed lease/quality·expiry·노출을 검사하고 `intent + DecisionEvidence + reservation + stable client ID + attempt(SEND_AUTHORIZED)`를 **한 주문 원장 transaction**으로 확정한다. transaction 내부 네트워크/파일 압축/Telegram await는 0개다. COMMIT 실패 또는 결과 불명은 adapter 호출 0회다. COMMIT 성공 뒤 gate/epoch/expiry/노출을 재검사하고 직렬화된 `SUBMIT_HANDOFF`를 기록한 범위만 제출한다. 주문 사실 알림은 COMMIT 후 bounded outbox에 넘긴다. 시세 전체 fsync barrier를 추가하지 않는다.

**추가 예정 내부 자료:** `DecisionEvidence`에는 contract/config/spec version, account alias와 mode, engine incarnation/apply_seq, 판단 event IDs, feed input version vector, 관측 시각/clock domain, quality, 전략 입력/결과의 필요한 정확 Decimal 문자열, expiry, reservation 근거를 넣는다. 전체 lookback 무손실 재생을 보장한다고 쓰지 않는다. `EngineCursor`는 `(engine_incarnation, apply_seq)`로 producer seq와 분리한다. 내부 자료를 v2 envelope에 조용히 덧붙이지 않는다(C02).

**실패/복구:** handoff 이후 timeout/cancellation/ACK 유실은 UNKNOWN과 예약 유지. SEND_AUTHORIZED 재시작도 제출 가능성을 대조하고 자동 재전송 금지. intent TTL/계좌 한도/attempt 보존량이 UNVERIFIED면 관련 live 활성화를 차단한다.

**검증 assertion:** COMMIT fault → submit count=0; COMMIT와 handoff 사이 halt/expiry → submit count=0; ACK 유실 후 restart → 재submit count=0 및 UNKNOWN 예약 유지; 동일 intent 반복 → 원장/예약/외부 제출 각 1회 이하. 기존 오라클 `tests/test_etf_mock_trading.py`, `test_dual_mock_and_assumed.py`, `test_publish_and_mock_notifications.py`; 신규 fault matrix는 P04 티켓에서 생성/실행한다. 지금 실행 기록은 NOT_RUN이다.

## 3. C02 — 이벤트/정밀도/schema 호환

| 의미 | 기존 필드 | 추가 확인/설계 |
|---|---|---|
| producer incarnation | `collector_run_id` | 프로세스 시작마다 새 ID인지 provider별 검증 필요 |
| connection epoch | `connection_id`, `receive_sequence`, `record_index` | 재접속 때 새 connection과 seq reset 수명을 provider별 고정. 필터 skip의 연속성 범위 명시 |
| 공급자 정체성/순서 | `native_event_id`, `sequence`; book `first_update_id/final_update_id/previous_update_id/sync_generation` | native sequence가 없는 feed에 임의 의미 부여 금지 |
| 시각 | nullable `source_ts_ns`, `source_timestamp_kind`, `observed_delay_ms`, `receive_ts_ns`, `receive_monotonic_ns`, `wallclock_ts_ns` | monotonic 값의 boot/clock domain은 현재 필드에 없음. engine incarnation과 연결 예정 |
| bar | `Bar1mEvent.open_ts_ns/close_ts_ns/closed=True`, `ingest_mode` | `available_at/revision/provider revision`은 추가 예정. end label과 실제 도착을 혼동 금지 |
| feed 상태 | `DataGapEvent.reason/resync_status` | 별도 quality state/lease 계약 예정; DATA_GAP은 Parquet 지원 8종에 포함되지 않음 |

`SCHEMA_VERSION=2`, Pydantic `extra='forbid'`다. v2 reader는 미지 필드가 들어오면 거절한다. 따라서 신규 envelope 필드는 version/reader 배포 순서·구버전 거절 경로·reader migration fixture를 명시한 별도 P01/P03 티켓이 필요하다. **P02는 모델/SCHEMA_VERSION/schema field/type/order/nullable 변경 0개**, 저장·live 기존 payload bytes를 유지한다.

가격/수량/strike는 `Decimal`, JSON 정확 문자열, Arrow `decimal128(38,18)`를 보존한다. 정수화/tick-lot 변환과 시장 데이터 scale을 합치지 않는다. 38자리/소수 18자리 초과는 조용한 rounding 없이 기존 Arrow 실패를 유지하고, 명시 quarantine은 별도 계약 변경으로 다룬다. orderbook `raw_payload`, bids/asks 순서·null·order_count와 GRVT case-sensitive raw_symbol을 보존한다. 모든 trade wire 원문이 저장된다고 주장하지 않는다.

**검증 assertion:** event ID·native cursor·Decimal 문자열 round-trip 동일; 공급자 미제공 source time은 null; nested 배열/zero delete/null order_count 동일; schema equality와 field order 동일; unsupported parquet event_type은 같은 ValueError; overflow/unrepresentable Decimal에서 manifest/ACK가 생기지 않음. 기존 `tests/test_models.py`, `test_crypto_perp_pipeline.py::test_book_original_frame_reaches_parquet`, `test_durable_pipeline_guards.py::test_raw_book_frame_is_forwarded_without_sampling_or_truncation`를 재사용한다.

## 4. C03 — 공유 상태와 인과적 dispatch

현재 `HotPathStore`의 `_trades/_books/_index_quotes`는 instrument별 bounded deque다(기본 2048/512/2048). `_crypto_books`는 Decimal key의 bid/ask map과 sync/final ID를 보유하며 기존 sequence/gap/crossed/stale 검사를 한다. book당 최대 1000 price levels 유지, top 50 정렬/재구성, `latest_book` crypto 1초 freshness는 **현 코드 사실**이며 새 목표값이 아니다. `StrategyDataView.history`는 Parquet reader를 동기 호출할 수 있다. frozen view wrapper만으로 deep immutable/비차단이 완성되지 않는다.

목표는 feed 상태 1회 적용 → version 증가 → 전략별 immutable view와 공통 feature 재사용이다. on_event에서 DB/파일/HTTP/압축 작업을 호출하지 않는다. top-K cache key는 `(book_version,K)`, feature key는 `(의존 feed versions,parameter/config version,window/clock version)`다. timer에서 window 만료를 갱신하고 worker는 입력 version을 반환한다. 현재 version과 불일치한 결과는 신규 주문으로 이어지지 않는다. 시간 길이 보장이 안 되는 ring은 HISTORY_INCOMPLETE다.

ETF `EtfTradingLoop.event/restore_today`, `etf_reference.py`의 기준·실행 ETF/09:01 end label/closed·도착 시각/오늘 복구를 보존한다. runtime `strategies: []`가 ETF 비활성을 뜻하지 않는다. replay/backfill은 과거 실주문을 재생하지 않는다.

**검증 assertion:** 같은 trace의 경제적 signal 출력은 golden과 일치; snapshot+delta 전체 적용 이전 callback 0회; stale worker 출력 주문 0회; quiet timer에서도 window 만료; gap/reconnect/incarnation 변화 후 신규 주문 0회 until validation. 기존 `tests/test_binance_snapshot_bridge.py`, `test_etf_execution_update.py`, `test_etf_outage_recovery.py`, `test_publish_and_mock_notifications.py::test_restart_restores_today_opening_minutes_without_replaying_signals`를 연결한다. 공유 state/feature는 P03, live fanout은 P05, replay/live 복구는 P06이며 P02에 포함하지 않는다.

## 5. C04 — 계좌 owner·halt·체결 대조

scope는 `(venue,account alias,subaccount/margin_scope,environment)`로 해석하고 mock/mock2/real을 충돌시키지 않는다. 전략 장부 C3/KP/KD와 실제 공통 계좌 노출/예약을 구분한다. 현재 `BrokerAdapter` protocol에는 cancel이 없고 KIS `order_status`의 인자는 client ID가 아니라 **broker order_number**다. generic protocol의 이름을 KIS 재조회 보증으로 읽으면 안 된다. 실제 지원은 capability 파일을 따른다.

`RuntimeStore`는 `_lock + transaction(BEGIN IMMEDIATE/COMMIT)`과 WAL/FULL을 사용하고 connection별 직렬화한다. 여러 프로세스의 같은 DB writer가 한 owner가 됐다는 뜻은 아니다. `EtfJournal`은 autocommit connection과 `BEGIN IMMEDIATE` context를 사용한다. 후속 작업은 한 DB 파일 writer ownership을 고정하고 기존 WAL/FULL을 확인한다. synchronous 확인은 실제 writer connection에서 한다.

halt linearization은 owner가 gate를 닫는 시점이다. gate close → scope epoch 증가/영속 COMMIT → ACCEPTED를 반환하고, handoff 이후 주문의 query/cancel/fill 처리는 지속한다. COMPLETED는 미해결 주문 0건이며 포지션 0 보장이 아니다. global/account/strategy epoch 재검사, DB 실패 시 closed 유지, stale resume 거절. 내부 epoch는 외부 중복 프로세스를 막는 fencing 증거가 아니므로 cutover 때 기존 owner stop/restart 차단이 별도 필요하다.

제출/외부 주문/취소/대조 상태축을 분리하고 ACK 전 fill·cancel 도중/후 fill을 허용한다. KIS는 `EtfJournal.apply_status`의 `tot_ccld_qty/tot_ccld_amt` 누적차와 `(account_scope,day,order_number)` 보고 key를 재사용한다. 현재 코드의 수량/금액 역행·초과 시 RuntimeError, 동일 누적값 재입력 시 delta=0, cash/positions/reserve/notifications 같은 transaction을 보존한다. 임의 timestamp fill ID를 만들지 않는다. 공급자별 fill ID 유일성·correction 정책은 UNVERIFIED다. UNKNOWN/미체결 worst exposure를 계좌 한도에 포함한다. 검증된 위험 축소 정책 없이는 halt 중 신규 제출을 허용하지 않는다.

**검증 assertion:** repeated cumulative fill로 position/cash/fee 중복 0; cancel-fill race에서 확정 잔량만 1회 replacement; 취소 ACK만으로 예약 해제 금지; account/mode 별 cursor/노출 분리; UNKNOWN 재시작은 query로 이어지고 blind retry 없음. 기존 `tests/test_etf_execution_update.py::test_cancel_fill_race_replaces_only_confirmed_remaining_once`, `test_publish_and_mock_notifications.py::test_acceptance_and_incremental_fills_have_separate_durable_notifications`, `test_dual_mock_and_assumed.py`, `test_exchange_expansion.py`를 사용한다. 신규 owner/gate assertions는 후속 티켓 대상이다.

## 6. C05 — 상한과 장애 전파

현재 buffer는 4 shards, 총 요청 max_items=128에서 shard별 `max(1,max_items//shards)` queue를 만든다. `_queued_bytes`는 worker 처리 완료 전까지의 예약량을 포함한다. live subscribe는 256 messages/8MiB다. 이것을 검증된 peak 메모리/최대 지연 보장으로 쓰지 않는다. raw event object, Python overhead, encoded bytes, live view, in-flight/executor 작업도 예산에 포함해야 한다.

모든 queue에 byte/record/max frame/oldest age/in-flight/overflow 정책이 필요하다. 저장 수용 실패는 affected feed storage GAP latch와 신규 위험 차단, live overflow는 INVALID와 resync다. delta/trade silent drop/coalesce 금지. 제어 지연 시 feed health lease가 주문을 차단한다. 저장/engine 사이의 장애 전파 지연을 측정하며 즉시 동시 차단을 약속하지 않는다.

현재 `NatsJetStreamBus.publish_market_event`는 `await _publish`(JetStream ACK) **뒤** Core NATS live 발행한다. Parquet는 downstream 분리지만 JetStream 대기와 NATS 자체 장애에서 hot path가 독립적이지 않다. “NATS 30분 중단 독립”의 필수 여부와 transport/spool 선택은 **ADR-002 OPEN**이며 임의 PASS/목표 제외 처리하지 않는다. P02 encoding은 이 순서를 유지한다.

용량은 `bytes_per_second × outage_seconds + in_flight + temporary + reserve`를 같은 물리 볼륨 기준으로 계산한다. Parquet 중단 900초/회복 1800초, cloud 중단 86400초/회복 86400초에 필요한 이론 μ/λ는 1.5와 2.0이다. 계획 후보 1.8와 2.4는 측정 실적이 아니다. 현재 실측 자료의 CRYPTO_DATA 128MiB로 요구 중단시간을 보장할 수 없다. 지속 유입/검증 완료 처리율이 없으면 용량 gate는 UNVERIFIED다.

**검증 assertion:** 정상·enqueue cancel·worker cancel·publish failure·drain timeout 각각 accounting이 음수/누수/무한 task 없이 닫힘; accepted는 소유권이 worker에 넘어간 실제 항목으로 셈; 같은 instrument 순서 유지; failed/unsent는 관측 가능. 기존 `test_durable_pipeline_guards.py::test_buffered_publisher_preserves_per_symbol_order`와 `test_buffered_publisher_reports_unsent_events_after_failure`; 신규 취소/한도 fault는 P02-ENCODING에 추가한다.

## 7. C06 — replay/파일/ACK/remote/회수

정확 회귀 입력은 초기 state/config/spec/market events/timer/control/fills/external replies/seed/engine 적용 순서다. 실제 복구는 `WARMING → VALIDATING → LIVE`, 장애는 `INVALID → CATCHING_UP 또는 WARMING`으로 전환한다. C와 handoff H를 고정해 `(C,H]` REPLAY 후 H 이후 bounded ring을 dedupe로 연결한다. cursor의 incarnation/연속성/clock/window가 증명되지 않으면 resync한다. 현재 producer cursor만으로 과거 교차 feed 적용 순서를 복원했다고 하지 않는다.

현재 `ParquetBatchWriter._write_staged/_flush`의 `tmp close → file fsync → os.replace → directory fsync → RuntimeStore.commit_files(manifest+deliveries) → last_committed_ids` 순서를 유지한다. Windows `_fsync_dir`는 no-op이므로 Linux production directory-fsync 검증은 별도 gate다. 현재 `reconcile_startup`은 tmp 제거와 미등록 final의 `.orphan` 격리를 수행한다. final을 검증해 재등록하는 목표는 아직 구현된 계약으로 주장하지 않는다.

normalized nested schema, `_canon/logical_hash_rows`, incremental logical_hasher, `file_sha256`, exact event runs, alias delivery, `ConfirmedAckFloor`의 stream/consumer incarnation/policy 확인, replacement chain/lease를 재사용한다. ACK 호출 성공을 confirmed floor로 대체하지 않는다. 원격 검증은 기존 backend의 실제 내용 SHA/readback을 유지하고 ETag/설정 metadata/길이만으로 대체하지 않는다. 원본 회수는 동등 대체본/reader pin/retention, 마지막 local 회수는 remote proof까지 만족해야 한다.

**검증 assertion:** fsync/rename/COMMIT fault에서 manifest/ACK eligibility의 선행 공개 0; duplicate delivery는 canonical commit 후 ACK 가능; confirmed floor provenance 미확인 시 prune 0; remote 손상·reader pin·replacement 누락 시 삭제 0; REPLAY/WARMING/INVALID 신규 주문 0. 기존 `tests/test_parquet_store.py`, `test_durable_pipeline_guards.py`, `test_confirmed_ack_receipts.py`, `test_crypto_replay_integrity.py`, `test_crypto_delivery_recovery.py`, `test_crypto_committed_aliases.py`, `test_archive_reclaim_scaling.py`를 재사용한다.

## 8. C07 — 명령 수신/알림/보고

현재 `TelegramController._handle`은 sender allowlist의 status/trade/help/start 조회만 처리한다. `_offset`은 메모리이며 `_handle`/reply 성공 뒤 증가한다. `_poll_loop`의 네트워크 재시도와 ETF `notifications`의 durable send bookkeeping을 재사용할 수 있으나 durable command inbox/TTL/chat allowlist/단일 poller lease/bounded outbox/429 retry_after가 이미 완성됐다고 하면 안 된다.

신규 변경 명령은 `(bot alias,update_id)` unique inbox transaction에서 접수 또는 거절하고 결과를 재사용한다. batch의 연속 앞부분이 durable 처리되기 전 높은 offset을 발행하지 않는다. message.date 기반 TTL, numeric user/chat allowlist, halt 이후 stale resume 차단, bot당 단일 poller ownership을 적용한다. TTL 60초는 미확정 후보이며 production 값/정책 없으면 변경 명령 비활성이다. Telegram sender는 bounded outbox에서 429 retry_after/backoff를 처리한다. 송신 응답 유실로 알림 중복 가능하더라도 주문 중복은 허용하지 않는다.

`/trade`는 실체결/가정체결/recovery book/test exclusion의 구분과 valuation as-of/통화/fee/funding/외부 입출금/실계좌 대조 상태를 보존한다. Telegram 장애에도 로컬 owner halt와 계좌 snapshot 조회가 가능해야 한다.

**검증 assertion:** 같은 update 재수신 명령 effect 1회; batch 중간 transaction 실패 시 뒤 update offset 선행 없음; stale/unauthorized resume 제출 0회; 429 outbox item 유지; reply 실패 시 durable 명령 재실행 없음; `/trade` golden 보존. 기존 `tests/test_telegram_commands.py`, `test_telegram_status.py`, `test_trade_exclusions.py`, `test_publish_and_mock_notifications.py`를 재사용한다. 현재 새 control 계약 테스트/구현은 NOT_RUN/NOT_IMPLEMENTED다.

## 9. P02 독립 티켓 최소 계약

### P02-SCHEMA-CACHE

- 상태: **READY_CANDIDATE_OFFLINE**. root가 source/fixture hash와 관련 baseline 결과를 채우기 전 READY 아님. G00 장기 계측은 live 활성화 blocker이고 이 보존 작업의 설계 blocker가 아니다.
- edit: `vibetrading/parquet_store.py`; add: `tests/refactoring/test_schema_cache_equivalence.py`. 소유 editor 1명. model/settings/bus/runtime/archive/sqlite/전략/운영 데이터 수정 금지.
- before SHA256: `72665b0d28433ec8d70845a4da9fd4ce20215b7ff7f68915b11a94a01ae9c6c2`.
- target existing: `_schema`, `_normalize`, `ParquetBatchWriter._materialize`와 schema를 쓰는 `_write_staged`. 변경은 schema와 field-name immutable tuple/frozenset 재사용에 제한한다. cache는 지원하는 8종 `trade/order_book/bar_1m/etf_nav/funding_rate/open_interest/participant_flow/index_quote`만 key로 수용하고 최댓값 8이다. 미지 type은 cache 삽입 없이 기존 ValueError. 성공한 schema만 cache하며 PyArrow import 지연/기존 missing dependency 오류를 보존한다.
- state/transaction/await: schema/field cache는 process-local immutable metadata. row/buffer/Decimal/bid/ask 객체는 공유하지 않는다. 파일 commit/ACK 코드 이동·await 추가 0개. 독립 thread의 동시 첫 lookup은 중복 immutable 생성까지 허용하되 잘못된 type의 값 공유/교차 contamination은 금지한다.
- oracle: 기존 소스 `_schema/_normalize`의 preimage를 source hash로 고정한 오프라인 baseline 결과와 비교한다. candidate 자신의 cache builder를 기대값 생성에 사용하지 않는다. 8종 fixture에 Decimal 최대 정밀도/scale, null/metadata, empty/multi-level book, sequence int/string, native provenance를 포함한다.
- assertion: schema `.equals(check_metadata=True)`/names/field nullability/order 동일, normalized row/`logical_hash_rows`/decoded Arrow rows 동일, manifest delivery coverage 동일, 미지원 type/scale 초과 실패 동일. cache key 수 ≤8; 호출자에게 mutable names container를 노출하지 않음. Parquet binary bytes 동일은 compression/runtime metadata 때문에 필수 oracle로 삼지 않고 logical content/schema가 기준이다.
- 명령(미실행): `python -m pytest -q tests/test_parquet_store.py tests/test_models.py tests/test_crypto_perp_pipeline.py tests/test_durable_pipeline_guards.py tests/refactoring/test_schema_cache_equivalence.py`.
- 성능: 같은 trace/hash·환경으로 baseline/candidate A/B ≥3회, CPU/event와 호출 수 보고. 최소 개선율은 미확정이며 성능 PASS를 임의 선언하지 않는다. 기능 동등성·bounded cache가 코드 통합 gate; 장중 성능은 P10이다.
- rollback: 이 diff만 복구. schema/file migration 없음; 기존 파일 그대로 읽힘.

### P02-ENCODING

- 상태: **DRAFT_OFFLINE / 별도 계약 리뷰와 단독 파일 소유권 필요**. P02-SCHEMA-CACHE와 edit path는 다르지만 encoding/취소 accounting 변경 범위를 root가 별도 검토한 뒤 배정한다. 현재 우선 READY 검토 범위는 P02-SCHEMA-CACHE다.
- edit: `vibetrading/bus.py`, `vibetrading/runtime.py`; add: `tests/refactoring/test_encoding_equivalence.py`. P03/P05 및 다른 agent가 두 제품 파일을 동시 수정하면 안 된다.
- before SHA256: bus `dea0b7bf1f494d6ce0fcff18ba17654c2c48f6e89dce57ab3e9153f50ff20476`; runtime `69a4e401016cdfb4be8a2ef6dad2142a2601c10a569a01faefdf8c7a19d28832`.
- target existing: `dumps`, `BufferedMarketEventBus.publish_market_event/_worker/close`, `NatsJetStreamBus.publish_market_event`, `_CollectorReadinessBus.publish_market_event`. `EventBus.publish_market_event(event)`은 기존 fake/custom callers까지 호환을 유지한다. 신규 encoded carrier/내부 optional method는 **내부 전용 추가 예정**이며 명시 capability로 선택한다. `hasattr`가 wrapper `__getattr__`를 통해 readiness/policy interception을 건너뛰게 만들지 않는다. capability 없는 bus는 기존 event 메서드로 fallback한다.
- carrier 계약: frozen event reference + durable immutable bytes + `byte_length=len(durable bytes)`; carrier 내부와 event identity 일치 검증은 내부 생성 경로가 보장한다. 1회의 최상위 호출마다 기존 `dumps(event)`와 byte-for-byte 같은 durable bytes를 최대 1회 만든다. deepcopy/재-normalize로 source/event_id를 바꾸지 않는다. 같은 event의 후속 별도 publish는 별도 호출로 센다. raw_payload 제거 crypto live view는 별도 JSON bytes로 최대 1회; raw 없는/기타 live view는 durable bytes 재사용. JSON serializer/encoding/field order/null 처리 변경 0개.
- policy: 현 provider/product/allowed_supplement/timestamp/fast=False filter, subject와 `Nats-Msg-Id`, `_publish`의 시세 identified 3회 재시도·order 1회, JetStream ACK 뒤 live 순서를 그대로 보존한다. invalid timestamp가 생성하는 DataGap은 **새 event**로 1회 직렬화한다. 이미 filter-out된 event를 신규 publish하지 않는다. readiness count/bytes의 현 성공·실패·filtered 호출 의미를 먼저 golden으로 고정하며 수정 범위 밖의 의미 개선을 섞지 않는다.
- ownership/accounting: byte 예약 전 실패는 accepted=0. 예약 후 queue.put 취소/실패는 reserved bytes와 accepted를 정확히 rollback한다. queue.put 성공 후 worker가 소유하면 caller 취소가 같은 reservation을 다시 차감하지 않는다. cancellation/worker failure/close timeout에 남은 accepted를 누락 없이 pending/failed/unsent로 관측하고 worker 수는 shards 이하다. 오류가 있는 다른 shard가 queue.put 대기자를 영원히 묶지 않도록 종료/실패 전파 경로를 검증한다. max_bytes는 encoded payload의 accepted/in-flight 범위이며 event object/Python overhead를 포함한 process RSS 상한으로 설명하지 않는다.
- oracle: baseline의 fake JS/NC에 기록된 `(subject,payload bytes,headers,ordering)` transcript. crypto raw book/live stripped, fast=False, Kiwoom ETF, supplement/backfill, data_gap, disallowed provider/product, stale/future timestamps, retry/failure/취소를 고정 clock으로 비교한다. readiness 통계는 성공한 underlying publish 후에만 갱신되는 현재 계약과 일치한다. 기본 public 메서드를 override한 fake/custom wrapper를 우회하지 않는 호환 fixture를 포함한다.
- assertion: baseline transcript 동일; 1 durable encode(+필요시 1 stripped live encode); retry는 같은 bytes/ID; per-symbol FIFO; bytes invariant는 reserve/ownership transfer/release 사이 어느 await에서도 음수·누수 없음; public event-only fake 경로 유지; failed publish readiness 증가 0. 이후 NATS 독립/제어 lease 계약까지 PASS한 것으로 확대하지 않는다.
- 명령(미실행): `python -m pytest -q tests/test_durable_pipeline_guards.py tests/test_publish_and_mock_notifications.py tests/test_crypto_perp_pipeline.py tests/test_collection_limits.py tests/refactoring/test_encoding_equivalence.py`.
- rollback: bus/runtime/tests 한 티켓 diff를 함께 복구. wire/schema/설정/migration 변경 없음. 제한된 예외 경로의 예약 누수 복구는 의도한 bug fix로 별도 expected-diff에 기록한다.

## 10. gate 인계

독립 READY 판정은 root가 해당 티켓의 source manifest, before hash, 오프라인 fixture/oracle hash, 파일 소유권, baseline 테스트의 기존 실패와 candidate 실패 구분을 고정한 뒤 한다. 이 문서 작성에서는 테스트/benchmark/실계좌 조회를 실행하지 않았다. G01 전체, G-NATS, P04 주문 owner, P10 성능, P11/P12 cutover는 **NOT_RUN 또는 UNVERIFIED**다. 미확인 production parameter/capability가 필요한 티켓은 해당 범위만 차단한다. schema/encoding 보존 작업 때문에 실거래 설정을 바꾸지 않는다.
