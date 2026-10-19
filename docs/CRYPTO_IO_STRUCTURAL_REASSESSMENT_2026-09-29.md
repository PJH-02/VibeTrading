# 코인 저장 I/O 재검정 — 2026-09-29

## 판정

Release 1은 일부 안전·회귀 시험과 배포가 완료됐지만 R1-S 전체 및 지속 수집 Gate P를 통과하지 못했다. Release 2도 미완료다. **현재 구현의 I/O 포화를 20GB 볼륨 증설 필요성으로 곧바로 연결한 판단은 근거가 부족했다.** 이번에는 운영 원장의 실제 B-tree와 WAL 비용을 분해했다.

핵심 개선 대상은 `idx_delivery_compact_event`다. 보존된 운영 WAL의 **95.89%**가 이 인덱스 page frame이다. WITHOUT ROWID 후보는 합성 비교에서 공간만 줄이고 WAL 쓰기는 줄이지 못했다. R2-B1을 우선 배포하지 않고, R2-B2의 정확한 중복 조회를 보존하는 immutable/batched event index 후보를 비교해야 한다. 인덱스 단순 삭제는 허용하지 않는다.

## 확인 방법과 운영 영향

- 운영 checkpoint PID 13462는 재접속 시 사라졌다. WAL은 1,239,555,592 bytes 그대로였다. 성공 반환·truncate 완료는 확인되지 않았다.
- crypto-storage는 `exit=137`, `OOMKilled=false`였다. 앞선 문서의 ‘clean stop’ 표현은 부정확하다. Docker stop grace 이후 강제 종료와 부합하지만 exit code만으로 종료 주체를 확정하지 않는다.
- 재접속 순간 I/O PSI full avg10 약 1%, vdb util 1.1–2%, 가용 메모리 약 2.3GiB였다. 코인 저장·수집이 중단된 idle 표본이며, 지속 수집 능력의 증거로 쓰지 않는다.
- 새 read-only 진단에서 열린 crypto DB handle이 없고 NATS CRYPTO_DATA messages/pending/ack_pending이 모두 0임을 확인했다. stream ACK floor는 10,956,663이었다. 기존 Market consumer는 진행 중이었다.
- 사용자 승인 후 정지된 crypto DB와 WAL만 로컬 `.codex_remote/r2-baseline/`로 속도 제한 복사했다. 서버에 DB 사본을 만들지 않았다. 복사 전후 원본 크기·mtime은 동일했고 로컬 SQLite quick_check는 `ok`였다. 이는 분석용 사본 검증이며 원격 복구 백업 승인 gate와 동일하지 않다.
- 운영 서버에서는 full dbstat/VACUUM/checkpoint를 다시 실행하지 않았다. index 전체 집계와 WAL attribution은 로컬 사본에서 수행했다. 초반 서버의 제한된 schema/root-page 조회도 87.8초가 걸렸으므로 진단 자체의 I/O 영향을 정상 부하와 혼동하면 안 된다.
- Docker/부모 cgroup에서 해당 서비스의 명시적 io.max 제한은 발견하지 못했다. 클라우드 상품의 실제 지속 IOPS/QoS는 이번 자료로 확정할 수 없다. 100% util 또는 완료 IOPS만으로 한도를 산정하지 않는다.

## 실제 DB 구성

DB는 1,436,606,464 bytes, delivery 9,240,414행, 서로 다른 event key도 9,240,414개다. manifest는 72,665행이다. freelist는 0이다. **중복 0은 exact dedup 제거 근거가 아니다.**

| 객체 | bytes | 역할 |
|---|---:|---|
| parquet_delivery_compact | 551,796,736 | delivery 본체 |
| idx_delivery_compact_event | 426,266,624 | 랜덤 SHA-256 event key 조회 |
| idx_delivery_compact_prune | 225,800,192 | 보존 기간 순서 조회 |
| composite PK autoindex | 138,895,360 | stream/sequence 중복 조회 |
| parquet_manifest | 70,021,120 | 파일 목록 본체 |

delivery 본체와 세 인덱스가 DB의 **93.47%**다. 운영 writer의 cache 64MiB는 event 인덱스 단독 크기의 약 16%다. 매 이벤트의 무작위 hash 조회·삽입이 큰 B-tree page 집합을 건드리는 구조다.

분석 시점 24시간보다 오래된 delivery는 4,760,997행이다. 이는 삭제 가능 행 수가 아니다. ACK 세대·출처, 원격 파일/metadata 검증, alias coverage가 별도로 필요하다. prune이 안전성 때문에 비활성인 상태에서 보존 기간만 설정해도 원장은 줄지 않는다.

manifest 집계상 로컬에 남은 local_only 파일은 3,421개/744,035,778 bytes, remote_verified 로컬 파일은 179개/32,148,291 bytes다. 빠른 Parquet 업로드는 공간을 줄일 수 있지만 event B-tree의 쓰기 증폭을 제거하지는 못한다.

## 운영 WAL 비용 분해

현재 DB의 page→객체 mapping과 보존 WAL의 유효 salt frame을 대조했다. 4KiB page, 300,863 frames, commit marker 437개다. SQLite 공식 WAL format에 따라 파일의 물리 크기와 checkpoint 상태를 구분했다. `mxFrame=300863`, `nBackfill=0`, `nBackfillAttempted=300863`이었다. 일부 main DB page가 이미 쓰였더라도 완료된 checkpoint로 승인할 수 없다는 뜻이며, 서로 다른 미반영 page 300,863개라는 뜻은 아니다. [SQLite WAL format](https://www.sqlite.org/walformat.html)

| 객체 | WAL frames | WAL bytes |
|---|---:|---:|
| event hash index | 288,483 | 1,188,549,960 |
| delivery 본체 | 3,748 | 15,441,760 |
| composite PK | 2,422 | 9,978,640 |
| prune index | 2,295 | 9,455,400 |
| manifest 본체 | 353 | 1,454,360 |
| 기타 | 3,562 | 14,675,440 |

event 인덱스의 서로 다른 page는 96,040개다. 같은 page가 반복 갱신되며 약 1.19GB의 WAL을 만들었다. frame 수는 syscall 수·물리 IOPS와 다르다. 이 WAL 구간에는 이전 진단/중단의 영향도 섞일 수 있으므로 정상 유입률이나 시간당 증가율로 환산하지 않는다.

## 구조 후보 비교

로컬 합성 데이터 seed 2,032,768행에 동일한 새 32,768행을 8,192행씩 추가했다. 측정 엔진 SQLite 3.53.1, cache 64MiB, FULL sync, autocheckpoint 1,000 pages를 공통 사용했다. 먼저 만들어 둔 rowid seed는 재사용했고 초기 작성 엔진은 3.40.1이었다. 이는 구조 비용 분해 시험이며 운영 DB와 동일한 물리 I/O 성능 시험은 아니다.

| 후보 | 최종 DB bytes | 추가 WAL frames | WAL bytes/delivery |
|---|---:|---:|---:|
| 현행 rowid + 3 indices | 296,845,312 | 33,826 | 4,253.0 |
| WITHOUT ROWID + event/prune | 267,341,824 | 34,192 | 4,299.0 |
| event index만 뺀 진단 대조군 | 201,949,184 | 845 | 106.2 |

- B1: 공간 약 9.9% 감소, WAL 약 1.1% 증가. I/O 해결 후보로 기각한다. 보조 인덱스에 PK가 포함되는 비용은 여전히 남는다. [SQLite WITHOUT ROWID](https://www.sqlite.org/withoutrowid.html)
- event 인덱스 대조군: WAL 약 97.5% 감소. **정확 조회 대체 구조 없이 운영 배포하지 않는다.**
- 두 결과와 실제 WAL 분해가 같은 원인을 지지한다. B2 대체 구조를 구현·검증하기 전 ‘현재 서버가 감당 불가’라고 결론 내릴 수 없다.

## 함께 확인한 코드 결함과 현재 수정

1. `StorageWorker` heartbeat가 event loop에서 `connection_pragmas()`의 SQLite lock을 기다렸다. 긴 commit/checkpoint 동안 NATS 처리까지 멈출 수 있다. 전체 health read/write를 한 개의 `to_thread` 작업으로 옮겨 대기 작업이 무한 생성되지 않도록 했다.
2. 이미 확정된 event가 새 NATS sequence로 도착하면 alias delivery를 기록하지 않고 ACK했다. Crypto compact ledger에서는 canonical manifest 조회와 alias 삽입을 하나의 transaction으로 확정한 다음 ACK하도록 했다. 원본 Parquet row는 추가하지 않는다.
3. maintenance가 upload 후보 조회 전에 `wal_checkpoint(TRUNCATE)`를 실행한다. 긴 checkpoint가 upload/reclaim 진행까지 막을 수 있다. 현재 이 부분은 진단만 완료했으며 별도 checkpoint 책임 설계와 함께 수정해야 한다.

변경 파일: `vibetrading/storage_worker.py`, `vibetrading/sqlite_store.py`, 신규 heartbeat contention/committed alias 테스트, 합성 비교 스크립트, `.gitignore`의 분석용 사본 제외 규칙. **운영 배포는 아직 하지 않았다.**

현재 변경 후 로컬 회귀: **109 passed, 1 skipped, 2 deselected** (`not live_data`). SQLite lock 중 event loop 진행, alias commit 후 ACK, alias transaction 실패 시 ACK 금지, 재시작 후 alias 유지가 포함된다. 실제 NATS E2E·부하·migration 시험 통과를 대신하지 않는다.

## 다음 실행 범위

1. R2-A의 confirmed floor/generation 및 exact recovery coverage를 마무리하고, 기존 원장의 미확정 출처를 보수적으로 유지한다.
2. R2-B2에서 매 이벤트마다 큰 hash B-tree를 갱신하는 대신 flush 단위 immutable sorted event index를 저장하는 후보를 비교한다. event→canonical file과 delivery→file 관계, pending/committed alias를 정확히 복구할 수 있어야 한다.
3. 빠른 음성 판정 필터가 필요해도 그것은 정확 조회의 보조 캐시다. cold/replay 경로를 삭제하거나 cache miss를 신규 event로 승인하지 않는다. 필터 복구·검증·메모리 상한까지 후보 gate에 포함한다. 기존 R3 filter 항목을 앞당길 필요가 생기면 R2-B2의 필수 범위만 계획에 명시하고 별도로 시험한다.
4. 현행 운영 규모 사본에서 새 WAL bytes/delivery·checkpoint·lookup·메모리·복구를 비교한다. 실패 후보는 배포하지 않는다.
5. 백업 검증과 공간 예산을 충족한 뒤 crypto만 migration/배포한다. Market image/config/process와 stream/consumer는 유지한다. 실제 NATS, 동일 입력, 피크 2배 30분, remote archive/ACK 집합 검증 후 collector 정상화를 판정한다.

현재 세 crypto collector는 pause 상태이고 crypto-storage/maintenance는 정지 상태다. Gate P 및 전체 목표는 미달이며 작업은 계속된다. 48시간 대화 유지 관찰과 볼륨 증설은 실행하지 않았다.

## 증거 파일

- `docs/evidence/r2-readonly-20260929.json`: 원격 상태·WAL header·schema·cgroup·NATS.
- `docs/evidence/r2-local-dbstat-20260929.json`: 로컬 실제 DB 구조 집계.
- `docs/evidence/r2-local-integrity-20260929.json`: quick_check·행 수·manifest 집계.
- `docs/evidence/r2-wal-attribution-20260929.json`: 운영 WAL page attribution.
- `docs/evidence/r2-synthetic-{rowid,without-rowid,index-ablation}-20260929.json`: 합성 후보 결과.

## 2026-09-30 후속 검증 — B2 후보, 운영 미배포

### 실제 규모 사본의 결과

사용자가 승인한 운영 DB+WAL 로컬 사본으로 진행했다. 원본과 분리한 새 migration 대상에
9,240,414 delivery와 72,665 manifest의 rowid/참조/모든 필드 fingerprint를 보존했다.
141개의 immutable sorted run 생성, quick_check=ok, offline checkpoint 완료를 확인했다.
준비 파일은 `.codex_remote/r2-candidate/vibetrading.sqlite3`이며 합성 입력을 넣지 않았다.
benchmark 파일은 다른 경로에 있고 `benchmark_only=true`로 표시했다. 운영 교체에 사용하면 안 된다.

동일한 원장 크기에서 신규 32,768건을 8,192건씩 추가한 결과:

| 항목 | 기존 event B-tree | immutable exact run 후보 |
|---|---:|---:|
| WAL bytes/delivery | 5,218.27 | 161.57 |
| WAL frame | 41,503 | 1,285 |
| 8,192건 commit | 2.719~3.203초 | 0.453~0.891초 |

WAL 비용은 **96.9% 감소**했다. FULL sync, cache 64MiB, autocheckpoint 1,000을 유지한 비교다.
이는 로컬 SQLite 3.53.1의 논리 WAL 비용이며 운영 볼륨의 물리 IOPS 감소율은 아니다.

최초 후보의 조회/시작은 너무 느려 채택하지 않았다. 필터 계산을 NumPy로 벡터화하고
global negative filter를 고정 32MiB로 바꾼 뒤, pristine 후보에서 **시작 검증 82.5초**,
**신규 8,192건 조회 0.078~0.093초**를 확인했다. 필터 비트의 scalar/vector 완전 일치
테스트를 포함했다. 벡터화 후 commit 속도를 다시 측정한 값으로 위 commit 수치를 표시하지 않는다.

### 안전 장치와 변경 파일

- `event_index_runs.py`: checksum 검증된 정렬 key/canonical manifest 참조, 정확한 binary search.
  Bloom은 음성 판정 보조이며 양성일 때 반드시 exact 경로를 확인한다. 동일 SQLite transaction에 저장한다.
- `sqlite_store.py`: 명시적 format gate, 무검증 기존 DB 자동전환 거부, alias commit 후 ACK,
  기존 ACK floor와 분리한 서버 확인 receipt. consumer 재생성 시 옛 floor를 상속하지 않는다.
- `bus.py`: stream/consumer 생성 identity를 전후 대조하고 durable/filter/ACK/delivery 정책을 검증한다.
- `storage_worker.py`: 긴 SQLite lock과 시작 필터 복구가 event loop/heartbeat를 막지 않도록 분리.
  시작 상태는 initializing이며 복구 완료 후 ready가 된다.
- `market_policy.py`: `CRYPTO_REQUIRE_STORAGE_READY=true`일 때만 crypto storage ready/fresh gate.
  Market 판단에는 적용하지 않는다.
- `archive.py`: 새 crypto index mode의 live TRUNCATE를 제외한다. 기존 FULL/auto-checkpoint는 유지한다.
  이는 checkpoint 단일 owner 설계의 완료를 뜻하지 않는다. maintenance metadata connection도
  auto-checkpoint를 실행할 수 있으므로 연결별 소유권 감사는 남아 있다.
- `prepare_crypto_event_index.py`: 원본을 바꾸지 않는 새 DB 준비, rowid 보존, exact 참조/내용 검증.
  VACUUM으로 manifest implicit rowid를 바꾸지 않는다.
- `compare_event_run_cost.py`, `verify_event_runs_nats_e2e.py` 및 전용 회귀 테스트 추가.

### 수행한 회귀 및 실제 NATS 시험

로컬 전체 시험은 135 passed / 1 failed / 1 skipped / 2 deselected였다. 실패 1건은 새 테스트가
PRAGMA 보고 필드명을 잘못 사용한 것이며 수정 후 해당 테스트 1 passed를 확인했다.
백업 스케줄 테스트는 호스트 PC 여유공간에 따라 결과가 달라지지 않도록 디스크 조건을 고정했다.
운영 공간 보호 기준은 낮추지 않았다.

서버에서는 운영 mount/credential을 넣지 않은 internal Docker network + tmpfs NATS로 시험했다.
Market 원본 180건, Crypto 원본 180건/정확한 delivery 216건(중복 alias 36건)을 확인했다.
각 ACK 전에 delivery 확정을 검사했고, ACK 한 건을 의도적으로 유실시킨 뒤 재전달을 확인했다.
저장기 재시작 후 새 sequence alias도 중복 원본 없이 처리했다. pending/ack_pending=0,
Crypto server-confirmed floor=216이었다. 임시 컨테이너와 network는 정리했다.

### 미완료 항목과 운영 상태

**운영 배포 및 수집 정상화 Gate P는 미완료**다. 작은 NATS 기능 시험은 피크 2배 30분 시험을 대체하지 않는다.
아직 immutable run 삭제를 금지하여 원본 coverage를 보존하고 있다. 이 상태의 run은 장기적으로
계속 증가하므로 20GB 장기 운영 완료로 보고하지 않는다. delivery prune도 활성화하지 않았다.
정확한 remote recovery coverage, canonical/alias의 안전 삭제와 cold replay 계약, remote restore,
성장/삭제 수지 및 migration 공간 검증을 마무리해야 한다.

01:30 KST 전후 최종 확인에서 crypto-storage/maintenance는 정지, 세 crypto collector는 paused/unhealthy다.
운영 DB/WAL의 bytes/mtime는 사본 생성 기준과 같았다. 기존 Market storage/maintenance는 healthy이며
Market collector의 기존 unhealthy 상태는 유지됐다. 이 작업에서 Market image/config/process는 바꾸지 않았다.
20GB 데이터 볼륨 여유는 약 4.49GB이며, 비활성 precompact 사본 1.763GB가 있다.
그 사본은 아직 원격 검증/정리하지 않았으며 현재 DB/WAL도 삭제하지 않았다.

추가 증거:

- `evidence/r2-event-runs-migration-20260929.json`
- `evidence/r2-production-scale-btree-cost-20260930.json`
- `evidence/r2-production-scale-runs-cost-20260930.json`
- `evidence/r2-event-runs-optimized-lookup-20260930.json`
- `evidence/r2-event-runs-nats-e2e-20260930.json`

## 2026-09-30 후속: exact recovery, 안전한 원장 정리, 공간 회수

앞 절의 'immutable run 삭제 금지/복구 coverage 미구현'은 당시 상태다. 아래 코드와 시험이
추가됐지만 **운영 이미지는 아직 교체하지 않았다.**

### 동일 transaction의 정확한 복구 목록

`delivery_recovery.py`를 추가했다. 새로운 canonical/alias delivery를 manifest/index와
같은 SQLite transaction에서 압축된 정확 목록으로 저장한다. 목록은
`(stream_id, sequence, event_key, canonical_manifest_rowid, persisted_ts_ns)`의 집합이며
순번 범위로 중간 hole을 채우지 않는다. alias도 별도 복구 블록과 exact run을 갖는다.

ArchiveMaintenance는 canonical Parquet/sidecar 또는 검증된 supersession chain이
준비됐을 때 stream generation, manifest 매핑, 정확 delivery 목록을 한 원격 객체로 저장한다.
`put_bytes()`의 전체 SHA 검증 후에만 receipt를 확정한다. 원격 전용 목록만으로 새 catalogue를
복원하는 경로를 구현했다. 복원은 로컬 Parquet 존재나 NATS ACK를 추정하지 않는다.

삭제는 원격 receipt, 같은 stream generation의 server-confirmed ACK floor, 24시간 조건을
모두 요구한다. exact PK+event+file+timestamp를 일치시켜 소량씩 삭제하며 cursor를 같은
transaction에 기록한다. 실패 시 삭제와 cursor가 함께 rollback된다. 확인되지 않은 앞 블록이
독립적인 뒤 블록의 업로드/정리를 영구적으로 막지 않도록 제한된 cursor 순회를 사용한다.

### 이벤트 인덱스의 장기 증가 방지

delivery가 모두 안전하게 정리된 run이라도 바로 삭제하지 않는다. 해당 canonical 파일의
최대 이벤트 시각이 live 수신 허용 창 밖이고, 원격 exact recovery가 존재할 때만 삭제한다.
현재 live 허용 창 기본값은 기존 publisher와 같은 172,800초다. 파일 timestamp가 불명확하면 보존한다.
delivery 24시간 보존과 이벤트 dedup 창을 같은 조건으로 취급하지 않는다.

운영 활성화 시 `CRYPTO_EVENT_RUN_RETENTION_ENABLED=true`와
`CRYPTO_ENFORCE_LIVE_EVENT_WINDOW=true`가 함께 필요하다. 이 계약은 DB에 고정되며,
이후 창을 넓히거나 검사를 끄려면 먼저 offline exact index 복구가 필요하다.
retired cutoff보다 오래된 미확정 입력은 `crypto_historical_replay_requires_index_restore`로
정지하고 NATS에 남긴다. 정확한 원격 목록을 복원하면 실제로 존재했던 이벤트/alias는 다시
검증할 수 있다. native ID의 원본 이벤트 시각을 조작한 입력을 동일 이벤트로 판정하는 기능은 아니다.

필터는 삭제 이후 잠시 superset으로 남아 안전한 false positive만 허용한다. 제거량이 커지면
별도 read-only snapshot에서 고정 32MiB 필터를 재구축하고, 교체 직전에 추가된 run도 반영한다.
재구축 중 추가 메모리는 필터 32MiB와 제한된 SQLite/page buffer다. 실패 시 기존 필터를 유지한다.
새 데이터 누락이나 ACK 권한의 근거로 background cache를 사용하지 않는다.

### 실제 규모 사본 및 시험 결과

- 이미 준비한 사본에 coverage만 추가: **9,240,414 delivery / 141 block**, exact run과
  전부 대조, 완료 checkpoint `[0,0,0]`, 후보 DB **1,755,222,016 bytes**.
  baseline 원본 및 기존 manifest/delivery는 수정하지 않았다.
- 복구 목록 비용을 포함한 새 32,768건: **WAL 198.78 bytes/delivery**, 기존 5,218.27 대비
  **96.2% 감소**. cache 64MiB/FULL/autocheckpoint 1,000 유지. 조회 8,192건당 0.016~0.031초,
  commit 0.062~0.094초, 시작 필터 검증 6.312초였다. 로컬 캐시/부하 영향을 받는 시간이므로
  앞 절의 82.5초나 운영 서버 지연 보장치와 혼동하지 않는다.
- 복구 경로 추가 직후 전체 회귀: 145 passed, 1 skipped, 2 deselected.
- 이후 retirement/과거 입력 차단/background filter/강제 process exit 경계 전용 시험도 통과.
- 마지막 전체 회귀: **152 passed, 1 skipped, 2 deselected** (18.71초).
- 실제 격리 NATS: 원본 180건/216 delivery 원격 backend 모사 객체로 복원·정확 삭제.
  인덱스 정리 후 과거 이벤트를 새 sequence로 발행하면 ACK 없이 보존됨을 확인했다.
  exact recovery 복원 뒤 재전달이 처리돼 pending/ack_pending=0이고 canonical은 계속 180건이다.
  archive backend는 이 E2E에서 로컬 모사이며 Ncloud 전체 복원 시험의 대체가 아니다.

### 검증된 비활성 사본 공간 회수

자동 승인 검토가 특정 DB 파일의 원격 반출 승인을 요구하여 중단했고, 사용자에게
`vibetrading.sqlite3.precompact-20260928T021539Z`의 기존 Ncloud 버킷 백업·검증을 명시적으로 승인받았다.
8MiB/s 제한, idle I/O 우선순위, PSI full avg10>5에서 양보하는 일회성 보존 작업을 수행했다.
1,763,291,136 bytes를 업로드하고 원격 전체 내용을 읽어 SHA-256을 확인했다.

- SHA: `ce37e720151176a095c8b473851b1d90209dd6e599fcf8fffedd9d98ab73131c`
- key: `vibetrading/crypto/recovery/pre-r2-20260930/vibetrading.sqlite3.precompact-20260928T021539Z-ce37e720151176a095c8b473851b1d90209dd6e599fcf8fffedd9d98ab73131c.bytes`
- 원격 receipt와 현재 object identity를 확인하고 local recovery pointer를 fsync한 뒤
  **이 비활성 로컬 사본 한 개만** 정리했다. 현재 DB/WAL, Parquet, 주문 감사 원장은 그대로다.
- recovery pointer: `/data/recovery-receipts/vibetrading.sqlite3.precompact-20260928T021539Z.json`
- 볼륨 여유: 회수 직후 6,065,532,928 bytes. 최신 확인 6,056,755,200 bytes.
- 이것은 이전 파일의 정확한 bytes 보존이며 현재 DB+WAL의 일관된 원격 backup 완료를 뜻하지 않는다.

공간 회복 이후 기존 collector가 새 저장기보다 먼저 재개되는 것을 막으려고 세 crypto collector를
정상 종료했다. 모두 exit=0/OOM=false다. 종료 직후 NATS에는 **200건/772,189 bytes**가 있었고
기존 ACK floor는 **10,956,663**, 신규 마지막 sequence는 **10,956,863**이었다.
200건은 삭제하거나 ACK하지 않았다. 준비 사본은 그 전의 ACK된 원장을 반영하므로 다음 cutover는
이 추가 200건을 보존·처리하는 순서까지 포함해야 한다. NATS가 비었다고 가정하면 안 된다.

### 남은 gate

1. 새 코드의 완전한 source/config/image provenance 고정, crypto만 배포.
2. 현재 DB/WAL의 일관된 remote backup 및 rollback/cutover 증명. 이미 검증된 로컬 baseline은 보존.
3. 후보·WAL·동시 Market 임시 파일까지 포함한 공간 예산. 비활성 사본 회수만으로 합격이라고 하지 않음.
4. 관측 피크 2배/30분 실제 NATS·archive·prune/retire 동시 부하, 전체 volume I/O와 Market 영향 측정.
5. 신규 200건 보존과 새 수신→manifest→ACK→원격 검증, 거래소별 순차 재개 및 publishing 확인.

관련 증거: `r2-recovery-coverage-migration-20260930.json`,
`r2-production-scale-recovery-cost-20260930.json`, `r2-recovery-nats-e2e-20260930.txt`,
`r2-retirement-nats-e2e-20260930.txt`, `r2-precompact-archive-20260930.txt`,
`r2-precompact-reclaim-20260930.txt` (모두 `docs/evidence/`).

## 2026-09-30 후속: 배포 이미지 고정과 실제 부하 입력 준비

이 절 역시 **운영 DB 교체·collector 재개 완료를 의미하지 않는다.**

### Crypto 전용 빌드 및 설정

`scripts/build_crypto_release.py`는 runtime source/config만 whitelist로 묶는다.
`.env`, DB, 키, `.server_patch`, 테스트 데이터는 포함하지 않는다. 기존 crypto 이미지 ID를
고정한 뒤 network-disabled build를 수행하고 base layer ancestry와 이미지 내부 파일 SHA를
검사한다. 기존 Market 서비스를 포함하지 않는 별도 Compose를 생성하며, 기존 NATS network는
external로 참조한다. runtime source/config bind mount를 제거해 과거 설정 덮어쓰기를 방지한다.

- 최종 staging 경로: `/opt/vibetrading/releases/r2-crypto-gates-20260930`
- revision: `r2-5c2db280daec9a9cf163`
- image ID: `sha256:6b38ed494ff0c3047b0ce8b20d83871ad71bb0a51ebc5a2762c5b23cc5197f77`
- config hash: `3083f832f888648376db27eda7d3a42a931fa492e460f8687444bf09d3e8b652`
- 새 배포 경계 테스트 3건 통과, 실제 서버 Compose `config --quiet` 통과.
- `docs/evidence/r2-crypto-image-manifest-20260930.json`에 전체 runtime 파일 SHA 보존.

설정 검토 중 delivery prune enable 외에
`ARCHIVE_DELIVERY_PRUNE_ACK_FLOOR_VERIFIED=true`가 필요한 것을 확인해 보완했다.
이 값은 검증 경로의 실행 허용일 뿐 ACK 증명이 아니다. immutable run 경로는 별도로
generation별 server-confirmed receipt, 원격 exact recovery, 원격 canonical, 24시간을
동일 삭제 transaction 안에서 검증한다. 이 설정이 빠진 첫 staging 이미지
`r2-0fcc089621c72d7b4955`는 배포하지 않는다.

### 관측 피크와 재생 자료

운영 DB를 조회하지 않고 로컬 검증 사본의 active/local-present manifest 3,600개를 선택했다.
서버에서는 원본 Parquet 경로를 read-only로 마운트하고, CPU 0.35 / memory 256MiB / idle I/O,
PSI 양보 및 파일 간 pacing을 적용해 receive timestamp column을 읽었다.

- 3,600파일 모두 존재. 2,794,289건, receive timestamp 누락 0.
- Binance 492,071 / Bitget 883,413 / Bybit 1,418,805건.
- 관측 구간 Unix seconds: 1,790,631,060 ~ 1,790,666,786.
- 최대 1초 **4,358건**, 최대 연속 60초 **31,764건**(평균 529.4건/초).
- 가장 바쁜 60초의 원본 31,764건, **104,578,807 bytes**를 서버 임시 경로에 보존.
- SHA: `898baf345ec20b21aa2160a4b0fcbfdfed6fe6788c52c07b66c1b92e85165ff0`.
- 3거래소×3종목의 trade/order_book이 모두 포함되고 funding/OI 원본도 포함한다.
- 보고서: `docs/evidence/r2-observed-replay-trace-20260930.json`.
- 입력: `/opt/vibetrading/r2-cutover-20260930/backup/crypto-replay.jsonl`.

이는 **저장까지 도달한 가용 구간**의 관측 피크다. 거래소 전체 이론 피크나 중단 중 유실된
수신량을 대표한다고 주장하지 않는다. 선택한 60초의 내부 최대 1초가 전체 관측 최대와 같은지는
아직 검사하지 않았으므로, 이후 재생에서는 이를 확인하고 필요하면 최대 burst 구간도 추가한다.
단순 고정 평균 입력만으로 통과 판정을 하지 않는다. 2배 재생은 구간 내 시간 간격을 반영하고,
30분 누적 쓰기·archive·prune 수지도 확인해야 한다. 현재 prune limit 8,192/cycle,
busy cooldown 30초의 이상적 상한은 약 273건/초이므로 관측 피크를 계속 유지하는 부하에서의
장기 정리 능력은 별도 측정이 필요하다. 이 값만으로 정상 수집 중 일평균 수지를 추정하지 않는다.

### 현재 백업 승인 경계

`scripts/backup_crypto_cutover.py`에 현재 DB+WAL을 read-only로 읽어 root volume에 일관된
독립 SQLite snapshot을 만들고, 8MiB/s·PSI 양보·512MiB scratch reserve를 적용하여 기존
Ncloud crypto archive에 업로드/전체 SHA 검증하는 일회성 절차를 준비했다.
원본 DB checkpoint·삭제·변경은 하지 않는다. 스크립트만 서버에 전달했다.

**실행은 자동 승인 검토에서 거부되어 시작되지 않았다.** 검토 사유는 앞선 사용자의 원격
반출 승인이 특정 비활성 precompact 파일에만 해당하며 현재 DB payload는 별도라는 것이다.
현재 DB 사본(예상 약 1.44GB, 수집·거래 기록 포함)의 기존 버킷 백업·검증 허용을 사용자에게
요청했고 응답 대기 중이다. 다른 경로로 우회하지 않았다.

이번 작업에서 Market 컨테이너/설정/이미지, 현재 crypto DB/WAL, 운영 NATS의 대기 데이터는
변경하지 않았다. Crypto 3 collector는 계속 중지 상태이며 운영 교체·실측 부하 gate·재개는 남아 있다.

### 재생 입력 보완 — 순간 최대 부하 누락 방지

후속 입력 검사에서 앞서 선택한 최고 밀도 60초의 최대 1초는 **1,856건**으로 드러났다.
전체 관측 최대 4,358건/초 구간은 그 60초에 포함되지 않았다. 따라서 기존 입력만으로
순간 peak gate를 통과시키지 않고, Unix second `1790664687`의 4,358건을 추가했다.
기존 histogram이 저장되지 않아 이 구간 위치 확인에 필요한 timestamp column을 다시 읽었으며,
이번에는 전체 histogram도 별도 보존해 이후 위치 탐색 시 같은 I/O를 반복하지 않게 했다.

- 최종 입력: `/opt/vibetrading/r2-cutover-20260930/backup/crypto-replay-burst.jsonl`
- 60초 최고 밀도 구간 + 별도 1초 최대 burst를 이어 붙인 **61초 / 36,122건**.
- **112,267,982 bytes**, SHA `0c76fa600945a2ae198ae9c5978250afabe12c9031a7c6b57a553772b23e87ce`.
- 검사 결과: 최종 입력의 최대 1초 4,358건, 전체 관측 최대와 일치.
- 세 거래소×세 종목의 trade/book 18개 조합 모두 포함.
- 보고서: `evidence/r2-observed-burst-replay-trace-20260930.json`.

`scripts/replay_crypto_nats.py`를 추가했다. 원본 수신 간격을 압축해 2배 속도로 재생하고,
cycle별 새 event ID/현시점 timestamp로 기존 dedup 때문에 부하가 줄어드는 것을 방지한다.
동시 발행 32개/대기 256개, 5초 publish timeout, 5초 이상 scheduling lag는 시험 실패다.
`nats://r2-load-nats:4222` 전용 endpoint와 새 stream만 허용한다. 재생 입력은 160MiB 한도를 두고,
모든 원본 행을 Python 객체로 동시에 펼치지 않는다. 실제 부하 시험은 아직 실행하지 않았으며,
서버에서는 `--inspect-only`의 SHA·종목·burst 포함 검증만 수행했다.

**현재 DB 백업 승인 응답은 여전히 대기 중이다.** 백업 실행, 현재 DB 삭제/교체, collector 재개는
진행하지 않았다. 읽기 전용 trace 준비 컨테이너와 검사 컨테이너는 정상 종료·자동 제거됐다.

## 2026-10-01 후속: 세대별 정리 지연 수정 및 서버 내부 백업 준비

### 실제 수신량과 정리 한도

새 Parquet/DB scan 없이 보존된 초당 수신 histogram으로 장시간 창을 계산했다.
가장 바쁜 30분은 239,639건(133.133건/초), 1시간은 426,614건(118.504건/초),
2시간은 794,788건(110.387건/초)이었다. 30분 관측의 두 배는 266.266건/초로,
8,192건/30초의 이상적 prune 상한 273.067건/초와 차이가 작다.
이 상한은 작업 시간·I/O 양보·원격 미검증을 모두 무시한 값이므로 충분한 정리 성능의 증명이 아니다.
또한 histogram은 중단 중 미수신량을 포함하지 않아 완전 정상 수집의 평균을 보장하지 않는다.
자료: `evidence/r2-prune-service-budget-20261001.json`.

### 앞쪽 미확정 세대가 안전한 뒤쪽 블록을 막는 문제

기존 `prune_verified()`는 매번 가장 앞쪽 eligible block 16개만 조회했다.
이 블록들이 과거 stream generation의 미확인 ACK 때문에 보존돼야 하면,
현재 세대의 독립적인 안전 블록도 검토하지 못하고 원장이 계속 늘어날 수 있었다.
새 회귀를 먼저 실행해 두 번째 호출에서도 안전한 새 세대 1건이 삭제되지 않는 실패를 재현했다.

`delivery_recovery.py`에 runtime metadata의 조회 위치를 추가했다.
미확정 블록은 행을 그대로 두고 조회를 진행하며, 끝에 도달하면 다시 앞쪽을 검토한다.
부분 삭제 중인 블록은 다음 호출에서 계속 처리한다. 조회 위치와 exact 삭제/블록 cursor는
같은 transaction에서 확정하며, 조회 위치는 삭제 권한으로 쓰지 않는다.
ACK generation, remote exact recovery, canonical verification, 보존 기간 검사는 유지한다.

- 관련 단위/복구/retirement 시험 **18건 통과**.
- 실제 격리 NATS에서 old generation 20개 delivery/block을 ACK한 뒤 local receipt 유실을 모사하고
  stream을 실제 삭제·재생성했다. 새 generation 1건을 저장/ACK했다.
- old proof가 없을 때 old 20건은 보존하고, 두 번의 제한 조회 결과는 `[0,1]`이었다.
- 별도로 보존한 원래 server-confirmed receipt를 다시 제공하자 old 20건도 순회 후 정리됐다.
- canonical Parquet 1건/event index 유지, NATS pending/ack_pending=0.
- backend는 local-test, 24시간 cutoff만 시험에서 단축했다. 원격 Ncloud 성능 시험의 대체가 아니다.
- 자료: `evidence/r2-prune-generation-nats-e2e-20261001.json`.

전체 회귀는 **156 passed, 1 failed, 1 skipped, 2 deselected**였다.
실패는 옵션 선택 시험의 고정 `2026-09` 계약이 10월 전환으로 만료된 날짜 의존 fixture였다.
기존 함수의 `today` 인자로 시험 기준일만 고정한 후 해당 파일 4건이 통과했다.
Market 운영 코드는 변경하지 않았다. 새 runtime mutation의 검증 때문에 회귀/NATS를 실행했으며,
이전에 완료한 동일 상태의 시험을 이유 없이 반복한 것은 아니다.

최신 staging: `/opt/vibetrading/releases/r2-crypto-prune-20261001`.
revision `r2-0b22cbb7d01da90241b1`, image
`sha256:5dce97a767197d4ffd9cae0e8024aeca4ba5574369a287942f4f2a8ed3406e8c`.
config hash는 이전과 같은 `3083f832f888648376db27eda7d3a42a931fa492e460f8687444bf09d3e8b652`이다.
실제 이미지 내부 source hash/base ancestry와 Compose 해석을 검증했다.
`evidence/r2-crypto-prune-image-manifest-20261001.json`에 보존했다.
운영 서비스에 이 이미지를 적용한 것은 아니다.

### 20GB 증설 없이 시험 캐시 공간 회수

현재 root free가 약 1.51GB로 내려가 root scratch snapshot+512MiB reserve를 충족하지 못했다.
컨테이너 참조가 0인 과거 `vibetrading-toss-daymarket:20260928` 시험 이미지 한 개의 ID를 확인하고
그 태그만 정리했다. 강제 삭제, 운영 이미지, 컨테이너, 볼륨 정리는 하지 않았다.
태그 제거만으로는 빌드 캐시 참조 때문에 공간이 회수되지 않았다.

캐시 의존성을 조사해 해당 시험의 private/reclaimable subtree 9개를 골랐고,
현재 이미지와 공유되거나 사용 중인 기록이 없음을 검사했다.
[Docker의 정확 ID 정규식 필터](https://docs.docker.com/reference/cli/docker/buildx/prune/)로
예상 ID 집합과 실제 선택이 일치하는지 확인한 후 그 집합만 정리했다.
BuildKit 정리량은 **977.8MB**였다. Docker root가 `/srv/vibetrading-data/docker`여서
공간은 root가 아니라 **데이터 볼륨에** 회수됐다.
데이터 volume free는 5,863,677,952 → 6,836,547,584 bytes였다(동시 Market 쓰기와 GC 시차 포함).
기록: `evidence/r2-retired-trial-image-20261001.json`, `evidence/r2-retired-trial-cache-20261001.json`.
그 receipt의 `free_bytes_*`는 root 측정이므로 실제 data 회수량과 혼동하지 않는다.

### 반출 없이 가능한 준비 단계

로컬 Docker 실행 환경도 확인했지만 앱 로그 폴더 권한 문제로 준비되지 않았다.
이번에 만든 Docker start 명령과 그 자식 프로세스만 정리했고 로컬 부하 시험은 시작하지 않았다.

현재 DB의 Ncloud 반출 승인은 아직 없으므로 `backup_crypto_cutover.py`에 `--local-only`를 추가했다.
이 모드는 archive 설정/클라이언트를 초기화하지 않고 동일 서버 snapshot/검증 후 종료한다.
실행 컨테이너는 **network none, 키/환경 파일 mount 없음, source DB/WAL read-only**다.
snapshot 경로는 `/srv/vibetrading-data/r2-cutover-local-backup/current-crypto-pre-r2.sqlite3`.
4GiB scratch reserve, 8MiB/s, PSI 양보, idle I/O, 384MiB/0.5CPU 제한을 적용했다.
진행 도중 기존 shared maintenance lock도 같은 lock inode로 잡아 남은 무거운 단계와 중첩을 방지했다.
현재 파일 복사는 끝나고 quick_check가 진행 중이다. 최종 SHA/fsync/receipt 전에는 완료로 판정하지 않는다.
실행명 `r2-local-current-backup`, 공통 잠금은 이 container의 종료까지 기다린 뒤 해제한다.

마지막 운영 확인: 원래 crypto NATS generation과 대기 **200건/772,189 bytes**,
ACK floor **10,956,663**, last sequence **10,956,863** 그대로다.
3 collector/storage/crypto-maintenance는 여전히 중지이며 Market storage/maintenance는 healthy,
Market collector는 기존 unhealthy 상태다. 현재 원본 DB를 교체하거나 대기 메시지를 ACK하지 않았다.
원격 backup/검증, production-size 2배·30분 gate, crypto 실제 재개는 여전히 남아 있다.

### 현재 서버 내부 snapshot 완료

위의 진행 중 상태 이후 `r2-local-current-backup`은 exit=0으로 완료/자동 제거됐다.
검사 중 새로 시작한 검증이나 중복 backup은 없으며 같은 실행의 완료까지 기다렸다.
SQLite quick_check 결과 `ok`, 파일 fsync/SHA 계산/local receipt 디렉터리 fsync를 완료했다.
공통 maintenance lock을 보유한 `docker wait`도 0으로 종료되어 잠금이 해제됐다.

- 실제 파일: `/srv/vibetrading-data/r2-cutover-local-backup/current-crypto-pre-r2.sqlite3`
- 크기: **1,436,606,464 bytes**
- SHA-256: **33b552ff03bcc3caa72671f1995c7ed7ff699d48e7fc2e8bf69142db3ca4e84e**
- elapsed: 1,255.0초. 이는 rate 제한/PSI 양보/legacy snapshot 검증을 포함한 작업 시간이며
  운영 storage 처리량 측정이나 immutable run 후보의 30분 gate가 아니다.
- source DB: 1,436,606,464 bytes / mtime_ns 1,790,675,030,665,676,603 / inode 1,048,590.
- source WAL: 1,239,555,592 bytes / mtime_ns 1,790,667,614,686,035,422 / inode 1,048,635.
- source stat identity는 백업 전후 동일. 원본 교체/삭제 없음.
- `remote_full_sha256_verified=false`. **서버 내부 백업이며 Ncloud에 업로드한 것이 아니다.**
- receipt: `evidence/r2-current-local-snapshot-20261001.json`.

현재 DB 사본의 Ncloud 반출은 기존 자동 승인 검토의 차단 사유가 아직 해소되지 않았다.
그 승인을 기다리는 동안에도 안전하게 가능한 서버 내부 snapshot까지 준비했다.
원격 검증 전 cutover/원본 정리/collector 재개는 진행하지 않는다.

### 완료된 snapshot 재사용 준비 및 승인 범위 확인

이번 사용자 승인에 명시된 파일은 비활성
`vibetrading.sqlite3.precompact-20260928T021539Z`다. 해당 파일의 기존 Ncloud 백업·전체 SHA 검증은
이미 완료되어 재실행하지 않았다. 현재 DB snapshot의 원격 반출 승인과 혼동하지 않는다.
자동 승인 검토가 현재 DB의 반출을 별도 승인 대상으로 차단한 상태이며,
준비된 `current-crypto-pre-r2.sqlite3`의 업로드·전체 원격 SHA 검증에 대한 승인을 요청했다.

`scripts/backup_crypto_cutover.py`에 `--archive-existing`을 추가했다. 승인 이후 실행할 때
기존 local receipt의 완료 상태·원본 identity·snapshot 경로/크기/SHA를 확인하고, snapshot을 다시
hash하여 receipt와 대조한다. SQLite backup/quick_check는 반복하지 않는다. 원본 또는 snapshot이
변경됐거나 완료 receipt가 불일치하면 archive 초기화 전에 중단한다. 업로드 이후에는 원격 객체의
전체 길이/SHA와 원본·snapshot identity를 다시 확인하고 성공 receipt를 남긴다.
이미 원격 성공 receipt가 있으면 중복 작업을 시작하지 않으며, `--local-only`와 동시 지정할 수 없다.

- 변경 파일: `scripts/backup_crypto_cutover.py`, `tests/test_crypto_cutover_backup.py`.
- 관련 시험 **13 passed**: 잘못된 receipt/원본 identity, 사본 손상, 원격 checksum 불일치,
  SQLite 작업 재실행 방지, 성공 receipt, mode 충돌 검증. 원격 객체는 시험용 메모리 client이며
  실제 Ncloud 연결·승인·운영 성능 gate를 대신하지 않는다.
- 운영 runtime/config/image 변경 없음. 해당 script는 운영 이미지 runtime bundle에 포함되지 않으므로
  기존 staging image revision을 바꾸지 않는다.
- SSH 읽기 전용 확인: crypto 3 collector/crypto storage/crypto maintenance 중지 상태 유지.
  Market storage/maintenance healthy, 기존 Market collector unhealthy 상태 유지.
- data free **5,327,306,752 bytes**, root free **1,507,102,720 bytes**.
  원본 DB/WAL과 기존 snapshot의 크기·mtime은 앞선 기록과 동일하다.

새 pristine candidate 1,755,222,016 bytes를 지금 data volume에 추가하면 잔여
3,572,084,736 bytes로 4GiB reserve를 충족하지 못한다. 부족분은 722,882,560 bytes다.
원격 전체 검증이 끝난 동일 snapshot 1,436,606,464 bytes의 중복 로컬 공간을 안전하게 회수한 경우에만
candidate 추가 후 계산상 5,008,691,200 bytes가 남는다. 이는 정적인 공간 계산이며 부하 시험의
Parquet/JetStream/WAL 증가를 보장하지 않는다. 아직 snapshot 회수, DB 교체, 30분 부하 gate,
collector 재개는 하지 않았다. 승인 후 원격 검증과 실제 작업 peak budget을 확인해 진행한다.

### 부하 시험 종료 후 전체 경로 대조 도구 준비

기존 `replay_crypto_nats.py`는 발행기의 입력 성공만 증명하므로 단독으로 storage/ACK/archive 통과를
판정할 수 없다. `scripts/verify_crypto_replay_integrity.py`를 추가해 이 빠진 종료 검증을 준비했다.
운영 DB를 수정하는 도구가 아니며 `r2-load-gate` 하위 시험 root, 격리 NATS
`nats://r2-load-nats:4222`, 별도 Ncloud qualification prefix만 허용한다.
실행 시 app root가 PYTHONPATH에 있어야 하며 replay/verification scripts를 함께 제공해야 한다.

검증 범위:

- capture의 수신 offset·2배 속도·시험 시간으로 예정 event 집합을 재구성한다. 마지막 일부 cycle도
  정확히 판정하며, 수백만 event 문자열 set 대신 bitmap/정수 배열을 사용한다.
- 실제 server-confirmed ACK floor, stream/consumer generation, 최종 last sequence와
  pending/ack_pending/잔여 메시지를 확인한다.
- 새 active canonical 파일의 원격 manifest 필드와 Parquet 전체 bytes/SHA를 대조하고,
  원격 Parquet의 event ID/NATS sequence가 예정 집합을 누락·중복 없이 구성하는지 검사한다.
- 실제 SQLite delivery 행의 event hash와 canonical manifest 참조를 확인한다.
  compaction 시 supersession chain을 따라 동일 active canonical 참조임을 검증한다.
- SQLite는 query_only/ro이며 schema migration, ACK, prune, checkpoint를 하지 않는다.
  시험 writer를 먼저 정지해야 하며, DB/WAL stat identity 및 NATS 세대의 검사 중 변경은 실패다.
- 원격 파일은 한 번에 한 개만 내려받아 확인하고 시험 root의 scratch를 정리한다.
  CLI의 보호 공간은 최소 4GiB이며 동일 파일 검증 완료 receipt가 있으면 재실행하지 않는다.

`tests/test_crypto_replay_integrity.py`의 **18건 통과**: 부분 cycle·발행 순서 변동, 예상 밖 이벤트,
누락·중복, 잘못된 event hash/manifest 참조, compaction 참조, 원격 손상/sidecar 불일치,
미검증 업로드, stream 세대 불일치, 공간 부족. 이 시험은 작은 로컬 fixture를 사용했으며
실제 NATS/Ncloud/production-size 30분 gate를 실행한 것은 아니다.

검증 도구도 종료 시 데이터 일치만으로 전체 부하 통과를 선언하지 않는다.
결과의 `whole_load_qualification_passed`는 false이며, 시험 중 적체 추세·RSS/OOM·disk pressure,
prune/retirement 서비스 처리량, Market 영향, crash 시 commit-before-ACK 순서는 별도 증거가 필요하다.
기존 pressure 측정 도구/회귀·장애 주입 시험과 함께 판정한다. 원본 payload의 모든 필드가
수집 입력과 동일하다는 의미도 아니다. 이번 도구는 event/delivery 정합성과 원격 저장 파일 무결성을 대조한다.

현재 DB snapshot의 Ncloud 반출 승인은 여전히 없다. 반복 백업·기존 검사·수집 재개·운영 이미지
적용을 하지 않았으며, 반출 승인 후 백업 전체 검증 → peak 공간 예산 → 격리 30분 gate →
DB cutover/대기 200건 처리 → 세 collector 순차 재개가 남아 있다.

### 2026-10-01 후속 승인·백업 완료와 실제 서버 시험

위의 승인 대기 기록은 과거 상태다. 사용자가 **현재 DB 사본도 Ncloud 백업 검증 허용**을
명시한 후 기존 snapshot을 재사용해 현재 사용 중인 crypto archive 버킷에 업로드했고,
원격 객체 전체 다운로드 SHA-256/길이 검증을 완료했다. SQLite backup/quick_check는 반복하지 않았다.

- 크기 1,436,606,464 bytes, SHA-256
  `33b552ff03bcc3caa72671f1995c7ed7ff699d48e7fc2e8bf69142db3ca4e84e`.
- 원격 키: `vibetrading/crypto/recovery/pre-r2-20260930/current-crypto-33b552ff03bcc3caa72671f1995c7ed7ff699d48e7fc2e8bf69142db3ca4e84e.sqlite3`.
- receipt: [현재 DB 원격 백업 증거](evidence/r2-current-remote-backup-20261001.json).
- 업로드와 전체 검증은 성공했으나 마지막 로컬 receipt 기록이 UID 권한으로 실패했다.
  원격 완료 receipt와 HEAD만 조회해 로컬 receipt를 복구했다. 업로드/전체 hash 재실행 없음.
- 원본 DB/WAL stat identity는 백업 전후 동일하다. 원본 DB/WAL 삭제·교체 없음.

20GB 볼륨을 확장하지 않고 아래 중복 공간만 회수했다.

1. 고정된 과거 export 두 개의 tar.gz 전체 SHA와 압축 내부의 모든 파일을 추출본과 대조했다.
   누락·변경·추가 파일·symlink·경로 이탈이면 삭제하지 않는다. 검증 receipt를 fsync한 뒤
   추출 중복본 1,344,467,454 logical bytes만 제거했다. tar.gz와 checksum은 보존한다.
2. 현재 DB의 원격 전체 검증 receipt와 원본 identity를 확인하고 복구 pointer를 fsync한 뒤
   중복 로컬 snapshot 1,436,606,464 bytes만 제거했다. 원본 DB/WAL과 원격 백업은 보존한다.
3. 위 정리 후 free 7,632,392,192 bytes에서 pristine trial 후보 1,755,222,016 bytes를
   추가했다. trial 전송 전체 SHA는 기존 검증 후보와 일치했다.

새 script: `recover_current_backup_receipt.py`, `reclaim_export_duplicates.py`,
`reclaim_current_local_snapshot.py`, `setup_crypto_load_trial.py`, `watch_crypto_load_trial.py`.
export 회수 관련 4개 단위 시험 통과. 이는 운영 storage runtime 변경이 아니다.

시험 root는 `/srv/vibetrading-data/r2-load-gate/fullscale-20261001`이며 운영 DB/Parquet/NATS와
분리한다. shared maintenance lock만 동일 inode로 공유한다. 자격 증명은 시험 archive에만
제공하고 기존 버킷의 `vibetrading/crypto/qualification/fullscale-20261001` prefix를 사용한다.
시험 DB에는 기존 9,240,414 exact event entries가 있으며, 역사적 Parquet가 시험 mount에
없음을 local_present=0으로 기록한다. 원격 검증·ACK 상태를 위조하지 않는다.
시험 DB는 synthetic 이벤트가 섞이므로 **운영 cutover에 사용하지 않는다**.

첫 시험은 약 85초에 중단됐다. 커널은 시험 NATS 컨테이너의 256MiB memcg OOM을 확인했다.
Docker OOMKilled=false였으므로 그 필드만으로 OOM 여부를 판정할 수 없다.
운영 NATS는 512MiB이고 CPU 제한이 없으므로 이 시험은 운영과 동등한 자원 조건을 충족하지
못했다. 이를 서버 전체의 메모리 부족 또는 30분 처리량 gate 실패로 일반화하지 않는다.
첫 입력/원장/압력 로그와 정지된 컨테이너는 보존했다.

두 번째 시험에서는 운영의 실제 JetStream max_memory=134,217,728,
max_storage=1,342,177,280, sync_interval=1초, NATS 512MiB, storage 768MiB,
maintenance 512MiB와 CPU 무제한을 적용했다. `docker update --cpus 0`이 기존 NanoCpus를
해제하지 않은 점을 inspect에서 발견해 시험 worker만 재생성했고 실제 제한=0을 확인했다.
첫 시험 synthetic 데이터는 보존하며 두 번째 시험의 baseline manifest rowid는 72737이다.

두 번째 2배·30분 시험은 현재 진행 중이다. 처음 2분에서 저장기는 running/ready이고,
관측 RSS 약 451~512MB, host MemAvailable 약 1.33~1.41GB, free 약 5.59~5.64GB다.
이 단기 관측을 gate 통과로 표시하지 않는다. 입력 종료 후 실제 ACK/원격 집합 대조와
적체·I/O·prune 서비스·Market 영향 판정이 남아 있다. 운영 crypto collector는 아직 중지 상태다.

### 동등 자원 조건의 30분 재생·종료 대조 완료

입력과 storage/ACK/archive 경로의 검증을 완료했다. 시험 이미지는 기존 staging
`5dce97…`, revision `r2-0b22cbb7d01da90241b1`이며 이후의 scheduler 수정본과 구분한다.

- **2,132,012건** 발행, publish 오류 0. 실제 NATS ACK floor=2,132,012,
  pending=ack_pending=잔여 messages=0으로 drain됐다.
- 원격 canonical **1,720개 / 578,739,439 bytes**의 전체 SHA와 sidecar 필드 대조 완료.
  원격 Parquet event/sequence와 SQLite exact delivery 2,132,012건이 누락·중복 없이 일치한다.
- 시험 후반 backlog 기울기 **-0.954 events/sec**. 전체 관측 입력 1,180.451/sec,
  NATS 제거 1,175.955/sec의 차이는 마지막 관측 시 미확정 8,120건이며 이후 전량 ACK됐다.
  최대 잔여 messages=11,498. 지속 증가 적체로 판정하지 않는다.
- storage 최대 RSS **602,701,824 bytes**, 최대 nonreclaimable working set **566,128,640 bytes**.
  raw cgroup 사용량 최대 805,253,120 bytes는 clean file cache를 포함한다.
  모든 시험 컨테이너의 cgroup oom_kill=0, NATS 최대 raw cgroup=329,641,984 bytes.
- host MemAvailable 최소 **1,148,907,520 bytes**, data free 최소 **4,924,727,296 bytes**,
  IO PSI full avg10 최대 9.13. 4GiB 보호 공간 유지. Market 운영 PID 변경 없음.
  기존 Market collector의 unhealthy(Toss 인증) 상태는 별도 미해결 상태이며 정상으로 위장하지 않는다.
- 종료 audit 첫 실행은 첫 파일 조회 전 host-owned trial root의 scratch 생성 권한에서 실패했다.
  격리 audit launcher만 UID 0으로 실행하도록 수정했다. DB는 URI mode=ro/query_only 유지,
  운영 mount 없음. 완료한 파일 검증을 반복한 것이 아니다.
- 증거: [압력 로그](evidence/r2-load-pressure-20261001.jsonl),
  [측정 요약](evidence/r2-load-summary-20261001.json),
  [원격 exact 대조](evidence/r2-load-integrity-20261001.json),
  [첫 부적합 시험의 kernel OOM](evidence/r2-trial-256m-kernel-oom.txt).

**전체 P gate와 Release 2 완료를 선언하지 않는다.** 장기 원장 정리/복구 블록 archive 서비스의
지속 처리량, 새 scheduler 수정본의 실제 NATS 회귀, 운영 DB cutover/세 collector 재개가 남아 있다.
48시간 관찰은 수행하지 않았다.

### 실제 부하에서 발견한 후속 병목과 최소 scheduler 수정

원격 업로드를 마친 cycle에서 `delivery_prune`이
`storage_pending_above_prune_limit`으로 양보하면 기존 `_maintenance_loop`가 전체 archive를
deferred로 취급해 300초 쉬었다. IO PSI가 정상이어도 복구 블록의 batch backlog를 다시
처리하지 않아, 원격 Parquet 처리량과 exact recovery 처리량이 분리될 수 있다.
따라서 단순 ACK/Parquet 완료를 원장 retention 정상화와 동일시할 수 없다.

- `vibetrading/runtime.py`: prune/retirement의 pending-only deferral을 실제 I/O 또는 health
  deferral과 구분한다. completed archive/recovery backlog는 30초 간격으로 처리한다.
  실제 I/O/health deferral은 300초 cooldown 유지.
- `vibetrading/archive.py`: bounded recovery scan의 forward progress를 기록한다.
  미확인 앞 블록을 지나가는 full scan도 후속 블록을 계속 검토하되, wrap만으로 busy를
  선언하지 않는다. 삭제 권한·ACK·24시간 조건·remote 검증 순서는 변경하지 않는다.
- 기존 loop에서 재현 시험 2건 실패/2건 통과를 먼저 확인한 후 수정했다.
  scheduler/recovery/I/O 양보 시험 **31건 통과**(미검증 forward scan과 wrap 포함).
- 전체 회귀 첫 실행 **195 passed / 1 failed / 1 skipped / 2 deselected**.
  실패는 storage-status 시험이 실제 PC의 가용 RAM(<0.5GiB)에 의존한 fixture였다.
  해당 시험의 RAM 입력만 고정한 뒤 Telegram 파일 4건 통과. 제품의 경고 기준은 변경하지 않았다.
- 새 immutable staging `/opt/vibetrading/releases/r2-archive-scheduler-20261001`:
  image `sha256:8476f1d7224219e83eeaef405c09eb28ab679f9a3f64146969b3291671cb1132`,
  revision `r2-eaf3be9592651cc14d48`, config hash `3083f832…`.
  base ancestry/runtime source SHA 검증 완료. 운영 이미지 교체 없음.

새 수정본의 archive/recovery/retention 서비스 검증은 다음 작업이며,
기존 30분 시험의 결과를 새 수정본 전체 gate 통과로 재사용하지 않는다.

### 후속 probe 준비 중 SSH 접근 중단

`probe_crypto_prune_capacity.py`를 준비했다. 완료된 disposable trial의 실제 NATS generation/
ACK floor/remote exact 감사 receipt가 일치하는 경우에만, 시험 보존 시간 1초로 8,192행씩
최대 16회/약 60초의 원장 정리를 측정한다. timestamp·ACK·원격 검증 flag를 위조하지 않는다.
운영 24시간 정책을 수정하지 않으며 동시 부하/운영 gate를 통과했다고 선언하지 않는다.

이 script의 전송 응답 중 도구 host가 재시작됐고, 이후 SSH 연결은 기본 재시도 및
ConnectTimeout=10/ConnectionAttempts=1 양쪽에서 TCP 시간 초과됐다.
**probe 실행 명령은 전송하지 않았다.** 전송 완료 여부는 접속 회복 후 remote SHA와 대조해야 한다.
로컬 script SHA-256: `2a2b3d9459204a8b501785de5c3cf470cfaa9339fa888f1465b74c0c3bc26e97`.

마지막 확인 상태:

- 현재 운영 DB/WAL 보존, 운영 crypto 이미지 교체/DB cutover/collector 재개 없음.
- 30분 trial producer 정상 완료, trial storage/maintenance exit=0으로 정지.
- 원격 terminal exact 감사 정상 완료, receipt는 로컬 문서에 보관했다.
- 새 archive scheduler 이미지는 staging 빌드/파일 대조만 완료했고 적용하지 않았다.
- 추가 scanner/wrap 검증을 포함한 관련 **31개 시험 통과**.
- 콘솔 브라우저 도구는 초기화 시 `failed to write kernel assets`로 실패해 서버 상태를
  대신 확인하지 못했다. SSH timeout을 서버 메모리/볼륨 부족의 증거로 취급하지 않는다.

접속 회복 후 첫 작업은 원본/컨테이너/전송 SHA의 읽기 전용 대조다. 이후 제한된 prune
probe → archive/recovery/retention 처리량 보강 검증 → 운영 cutover/200건 보존 처리 →
세 collector 순차 재개 순서를 유지한다. 현재 서버 상태는 접속 단절 이후 미확인이다.
