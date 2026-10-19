# 코인 수집 핵심 비효율 후보 — 기존 run 재평가

작성일: 2026-10-01 KST. 이번 작업은 기존 기록과 코드의 읽기 전용 비교다. 운영 코드·설정·DB·서비스를 변경하지 않았고, 추가 부하 시험이나 서버 진단을 실행하지 않았다. 아래 운영 적용 상태는 마지막 확인 기록 기준이며 현재 서버를 다시 조회한 결과가 아니다.

## 결론

최우선 후보는 **① 이벤트별 SQLite event 인덱스 쓰기 증폭, ② maintenance 재개 지연과 작업 간 결합, ③ 원장 정리의 읽기 비용·처리량 부족**이다. 앞서 제시한 100GB 필수 용량은 계산 근거가 없으며, 현재 서버가 세 거래소를 감당할 수 없다는 결론도 입증되지 않았다.

운영과 동등한 자원 조건으로 수행한 30분 시험에서는 같은 20GB 볼륨에서 2,132,012 이벤트를 처리하고, 최종 NATS ACK·SQLite delivery·원격 Parquet 집합이 일치했다. 해당 시험의 cgroup OOM은 0건이었다. 이는 짧은 저장 처리량의 증거이며, 장기 retention 처리량이나 운영 collector 정상화를 증명하지 않는다.

## 1. 핵심 후보: 우선 수정 대상으로 검토

### A. 이벤트마다 큰 랜덤 event B-tree를 갱신하는 구조

- **확인 수준: 운영 원인에 대한 강한 근거.** 보존된 WAL frame의 95.89%가 `idx_delivery_compact_event`에 속했다. 약 426MB 인덱스에 비해 writer SQLite cache는 64MiB였다.
- 기존 원장 규모 사본에서 신규 32,768건을 동일 조건으로 기록했을 때 WAL 비용은 기존 **5,218.27 B/delivery**, 정확한 복구 목록을 포함한 immutable run 후보는 **198.78 B/delivery**였다. 논리 WAL 비용이 약 96.2% 감소했다. 물리 IOPS가 같은 비율로 감소한다고 해석하지 않는다.
- 긴 checkpoint와 SQLite lock 대기는 이 쓰기 증폭의 후속 효과일 수 있다. heartbeat가 같은 lock을 event loop에서 기다리는 결함도 과거에 확인됐다.
- **주식 구조 참조:** 기존 배치 writer의 `파일 확정 → manifest/delivery transaction → ACK` 순서와 일괄 commit을 재사용한다. stock legacy 원장도 event 인덱스가 있으므로 그 테이블을 그대로 복사하면 같은 종류의 비용이 남는다.
- **간결한 수정 방향:** 이미 만들어 검증한 후보의 쓰기 경로를 우선 평가한다. 신규 자료구조를 다시 추가하지 않는다. 정확한 중복 판정·재전달·alias 복구를 보존하면서 신규 이벤트가 큰 랜덤 인덱스를 매번 갱신하는 작업을 줄인다.
- **현재 한계:** 후보 코드·시험이 존재하지만 운영 DB cutover와 collector 정상화는 마지막 기록 기준 미완료다. 인덱스 단순 삭제나 SQLite `synchronous` 완화는 이 후보의 해결책이 아니다.
- 관련 코드: `sqlite_store.py::commit_files/committed_event_ids`, `event_index_runs.py`, `storage_worker.py::_flush`.

### B. maintenance의 양보가 업로드·공간 회수까지 오래 멈추게 하는 구조

- **확인 수준: 실제 부하에서 재현.** `delivery_prune`만 pending 한도로 양보해도 전체 archive cycle이 deferred로 분류돼 300초 쉬었다. I/O가 정상인데도 다음 recovery/archive 처리가 늦어졌다.
- 후속 run에서도 일시적 I/O pressure 후 긴 cooldown 때문에 pressure가 회복된 뒤 업로드가 바로 재개되지 않는 문제가 확인됐다. 보호 공간 도달로 시험이 중단된 사실을 물리적 디스크 만재나 서버 성능 부족으로 해석하면 안 된다.
- 현재 로컬 `runtime.py`에는 pending-only deferral 분리와 resource-only 재확인 코드가 있다. 이 코드가 운영에서 실행된다는 증거는 별도로 필요하다.
- `archive.py`의 한 cycle은 회수·checkpoint·업로드·복구 목록 업로드·backup·orphan·prune·retirement를 순서대로 수행한다. 앞 단계의 오래 걸리는 작업 또는 양보가 뒤 단계 진행을 늦출 수 있다.
- **주식 구조 참조:** 같은 `ArchiveMaintenance`의 검증된 업로드·검증·로컬 회수 함수를 사용하고, 코인도 동일하게 작은 작업 단위로 진행시킨다. 공유 볼륨의 heavy I/O 상호 배제는 유지한다.
- **간결한 수정 방향:** 기존 maintenance loop에서 작업별 진행 여부와 양보 원인을 구분한다. 업로드/회수의 backlog 재시도는 prune-only 양보와 독립적으로 판단하고, 자원 회복은 SQLite 없는 가벼운 probe로 확인한다. 새 global controller나 scheduler 클래스를 추가하지 않는다.
- 관련 코드: `runtime.py::_maintenance_loop`, `archive.py::run_once/_run_once_locked`.

### C. 원장을 충분히 줄이지 못하고 삭제 불가능한 과거 기록을 반복 읽는 구조

- **확인 수준: 기존 원장·설정·run에서 확인.** 분석 당시 delivery 9,240,414행 중 4,760,997행이 24시간보다 오래됐다. 이는 삭제 가능 행 수가 아니다. ACK 세대와 원격 복구 증명이 없으면 보존해야 한다.
- 과거 설정은 prune이 비활성이었고, 8,192건/30초의 이상적 정리 상한은 약 273건/초였다. 실제 정리 비용과 양보까지 포함하면 낮아진다. 당시 2배 최다 30분 입력 추정치 약 266건/초와 차이가 작았다. 현재 로컬 설정의 65,536건은 별도 후보이며 실제 처리량 통과를 뜻하지 않는다.
- 이전 run에서 미확정 과거 generation의 큰 recovery payload를 반복 읽는 비용이 확인됐다. 로컬 소스에는 index-only 후보 검색과 bounded negative cache가 추가됐지만, 운영 적용과 지속 처리량은 미완료다.
- **주식 구조 참조:** manifest로 업로드 상태를 찾고 검증된 파일을 회수하는 기존 흐름을 따른다. 코인 전용 recovery 검증은 삭제 직전에 필요한 후보에 한정한다. 주식도 장기 원장 정리가 항상 정상이라는 가정은 하지 않는다.
- **간결한 수정 방향:** 삭제 후보는 작은 metadata/index로 선별하고, 불가능한 후보의 큰 payload를 매번 읽지 않는다. 안전 조건이 충족된 행의 실제 삭제율과 신규 확정률을 비교한다. DB 파일 크기와 live 행 수·freelist를 구분한다. 매 cycle VACUUM이나 COUNT 전체 스캔은 피한다.
- 관련 코드: `delivery_recovery.py::prune_verified/retire_runs`, `archive.py::_archive_crypto_recovery`, `sqlite_store.py::prune_delivery_history`.

## 2. 보조 후보: 핵심 세 후보보다 뒤에 검토

### D. 작은 Parquet 파일과 중복 파일 작업

- **확인된 현상:** 30분 시험 원격 원본은 1,720파일/578,739,439B로 평균 약 336KB/file이다. 압박·건수·시간 flush가 16MiB 목표 이전에 발동할 수 있다. 각 파일에 fsync, 전체 hash 읽기, manifest, 업로드와 sidecar 작업이 붙는다.
- **원인 기여는 미확정:** 작은 파일이 있다는 사실만으로 주 병목이라고 단정하지 않는다. 이전 syscall 관측에서는 Parquet fsync보다 SQLite 본체 fdatasync 대기가 훨씬 컸다.
- **주식 구조 참조:** 기존 partition 배치 writer를 그대로 사용한다. 현재 partition key는 event type/date/provider/venue이며 종목별 파일이 아니다. 3종목 × 3거래소 때문에 9개 별도 파일을 매번 쓰는 구조로 해석하면 안 된다.
- **간결한 수정 방향:** flush 원인별 파일 수·rows/file·bytes/file을 확인하고, 지배적인 조기 flush 조건만 조정한다. 드문 funding/OI의 최대 대기 시간과 ACK floor 진행은 보존한다. 새 파일 형식이나 compaction 시스템은 먼저 추가하지 않는다.
- 관련 코드: `storage_worker.py::run`, `parquet_store.py::_flush/_write_staged`.

### E. 이벤트의 반복 변환·직렬화 및 수집에 붙은 추가 발행

- **코드상 후보:** publisher byte 계측용 JSON, durable publish용 JSON, 호가 live copy용 JSON, Parquet logical hash용 JSON 변환이 존재한다. `_normalize()`는 이벤트마다 Arrow schema를 구성한다. 호가는 원문 `raw_payload`와 정규화된 bids/asks를 함께 보존한다.
- **원인 기여는 미측정:** CPU·메모리가 지배적인 병목이라는 기존 증거는 부족하다. 원문 보존은 사용자 요구이므로 제거하지 않는다. 전량 delta를 초단위 snapshot이나 샘플로 축소하지 않는다.
- **주식 구조 참조:** 공통 publisher/writer를 사용하고 반복 생성되는 불변 schema·직렬화 작업만 재사용 후보로 삼는다. 코인 live fan-out은 현재 목적에 필요하지 않은 비용인지 별도 판단하되 이번에 수정하지 않는다.
- 관련 코드: `bus.py::BufferedMarketEventBus/publish_market_event`, `parquet_store.py::_schema/_normalize/add`, `providers/crypto_perp.py`.

## 3. '주식 구조 카피' 요구를 적용할 기준

요구는 주식의 정상 동작하는 수신·bounded publisher·배치 저장·업로드·회수 생명주기를 코인에 적용하는 것으로 이해한다. 추상 클래스만 공통화하는 것으로 완료 처리하지 않는다.

현재 main source의 Market/Crypto는 이미 `BufferedMarketEventBus`, `StorageWorker`, `ParquetBatchWriter`, `ArchiveMaintenance`를 공유한다. 따라서 다음 수정은 공통 core를 다시 만드는 것보다 **실제 운영 Market 버전과 코인 버전의 차이 및 코인 전용 원장/설정/maintenance 분기를 줄이는 것**이 우선이다. `.server_patch` 파일은 과거 사본이므로 현재 운영 Market의 기준 버전으로 자동 채택하지 않는다.

그대로 복사하면 안 되는 과거 주식 코드도 확인됐다. `.server_patch/vibetrading/storage_worker.py`는 delivery/event SQLite 조회를 메시지마다 수행하고, 아직 파일 확정 전인 pending event 중복에도 즉시 ACK할 수 있다. 코드가 짧다는 이유로 이 동작을 코인에 복제하지 않는다. 정확한 중복 처리와 commit 전 ACK 금지는 유지한다.

공통 처리 코드는 하나로 유지하고 stream/consumer/SQLite/Parquet 경로와 프로세스는 분리한다. 코인 전용 차이는 거래소 parsing·데이터 단위·자원 설정 및 필요한 복구 증명으로 한정하는 방향으로 평가한다.

## 4. 몇 시간의 run이 해결로 이어지지 않은 이유

1. R1의 maintenance 양보와 flush transaction 묶기는 일부 비용을 줄였지만 큰 event 인덱스 갱신을 제거하지 못했다.
2. R2 후보는 인덱스 쓰기를 크게 줄였으나 정리·복구 업로드·회수의 처리량과 재개 지연이 다음 병목이었다. ACK 완료와 장기 원장 안정화를 같은 성공으로 볼 수 없다.
3. 여러 개선본이 로컬/격리/staging에만 존재하고 운영 cutover는 끝나지 않았다. 시험 통과 수가 운영 해결 증거를 대신하지 않는다.
4. 첫 시험의 NATS 256MiB 제한 OOM 등 운영과 다른 시험 조건을 바로잡는 시간이 들었다. 이후 동등 자원 30분 시험은 OOM 없이 완료됐으므로 첫 시험을 서버 스펙 부족 근거로 쓰지 않는다.

## 5. 후보를 선택할 때의 최소 기준

- A/B/C의 기존 구현·증거를 먼저 사용한다. 추가 구조 설계와 반복 전체 백업/전체 원장 스캔을 시작하지 않는다.
- 수정은 후보별 최소 변경으로 묶고 실제 실행 이미지/설정의 적용 상태를 기록한다.
- 신규 확정률, 안전한 원장 정리율, 원격 업로드율, 로컬 증가율을 함께 본다. 입력을 멈춘 상태의 낮은 I/O나 healthy heartbeat만으로 정상화를 선언하지 않는다.
- 48시간 관찰은 이번 대화에서 수행하지 않는다. 지금은 후보 평가 단계이며 운영 수정과 추가 시험은 실행하지 않았다.

## 주요 근거

- `docs/CRYPTO_IO_STRUCTURAL_REASSESSMENT_2026-09-29.md`: 운영 WAL 분해, 후보 비교, run별 변경/미완료 기록.
- `docs/RELEASE1_MAINTENANCE_YIELD_2026-09-29.md`: maintenance 읽기가 0이어도 storage 쓰기와 pause가 남은 관측.
- `docs/RELEASE1_CRYPTO_STORAGE_RESIDUAL_2026-09-29.md`: 배치 commit 후 잔여 병목, 과거 IOPS 추론 정정.
- `docs/evidence/r2-production-scale-{btree,recovery}-cost-20260930.json`: 동일 규모 논리 WAL 비용 비교.
- `docs/evidence/r2-prune-service-budget-20261001.json`: 당시 원장 정리율 상한과 입력 비교.
- `docs/evidence/r2-load-{summary,integrity}-20261001.json`: 동등 자원 30분 처리·종료 집합 대조와 전체 gate 미완료.
