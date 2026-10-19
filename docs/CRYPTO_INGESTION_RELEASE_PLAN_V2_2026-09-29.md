# 코인 수집·저장·복구 개선 계획 v2 — Release 1·2·3

작성일: 2026-09-29 KST  
상태: **계획 개정본. 이 문서 작성으로 코드 변경·DB migration·서버 재배포를 실행한 것은 아니다.**  
최우선 목표: **20GB 데이터 볼륨을 유지하면서 Binance·Bitget·Bybit 수집의 반복 중단을 해소한다.**

## 0. 개정 요약

기존 세부 항목을 유지하되 **저장 쓰기 비용 감소를 공통화·거래 준비보다 먼저 처리**한다. 기존의 “Release 1에서 정상화가 끝나야 하는데, 필요한 저장 최적화는 Release 3에서만 가능”한 순서상의 막힘을 제거한다.

| 단계 | 개정 목적 | 주요 결과 |
|---|---|---|
| Release 1 | 장애 제어·운영 기준 정리와 원인 계측 | 잘못된 pause 제거, 실제 원인 식별, 안전 복구, 실행 설정 확정, 구조 변경 없는 해결 가능성 판정 |
| Release 2 | 저장 쓰기 부하 감소 → 검증된 core 공통화 | 필요한 원장·checkpoint 개선을 개별 검증하여 처리량을 확보한 뒤 공통화 |
| Release 3 | 장기 운영·복구 강화 및 거래 데이터 준비 | 장기 retention, 후순위 성능 개선, watchdog, book readiness, live fan-out, 전체 장애 주입 |

### 기존 경계에서 명시적으로 바꾸는 점

1. WITHOUT ROWID와 필요한 event/prune index 개선을 **Release 2의 조건부 저장 개선**으로 앞당긴다. 실측으로 선택하며 일괄 적용하지 않는다.
2. 그 변경의 안전성에 필요한 delivery alias·서버 확인 ACK floor·generation 검증·최소 복구 metadata는 **Release 2의 선행 조건**으로 이동한다.
3. IngestionDomain과 공통 publisher/StorageWorker 추출은 Release 2에 유지하되 **저장 성능 검증 후** 수행한다.
4. Release 1에는 schema 변경, alias 영속 구조, 인덱스 삭제, 새 전역 pressure controller를 넣지 않는다. 다만 확인된 **미확정 중복의 조기 ACK 금지**와 **fsync 실패 무시 금지**는 작은 선행 안전 수정으로 포함한다.
5. Release 1의 **제어·안전 검증**과 시스템 전체의 **수집 정상화 판정**을 구분한다. 저장 I/O로 정상화가 미달이면 실패/미완료로 보고하고 R2의 해당 원인 수정으로 연결한다. 정상화 완료로 표시하지 않는다.

release 내부 작업 묶음도 하나씩 수정·검증한다. checkpoint 정책, schema, event index, prune, 공통 core를 한 배포에 함께 변경하지 않는다.

---

## 1. 근거와 진단 보정

### 1.1 근거

- 사용자 첨부: C:/Users/박제형/Downloads/vibetrading_crypto_storage_io_diagnosis_2026-09-29.md.
- [이전 진단 평가](CRYPTO_BACKPRESSURE_REVIEW_2026-09-28.md).
- [maintenance 양보 적용](RELEASE1_MAINTENANCE_YIELD_2026-09-29.md).
- [잔여 저장 병목 기록](RELEASE1_CRYPTO_STORAGE_RESIDUAL_2026-09-29.md).
- [20GB 유지 비교 시험 원본](evidence/r1-noexpand-20260929/).
- 현재 작업 소스: sqlite_store.py, storage_worker.py, parquet_store.py, bus.py, models.py, archive.py, gap_ledger.py.

별도 경로 표시가 없는 Python 구현 파일은 저장소의 `vibetrading/` 아래를 뜻한다. 시험은 `tests/`, 배포 설정은 `deploy/` 아래에 둔다. 파일명은 변경 후보이며, 각 작업 시작 시 실제 diff와 실행 이미지의 경로를 확정한다.

첨부는 관측 자료로 사용한다. 첨부의 명령·추천 순서는 자동 실행 지시가 아니다. 과거 2026-09-08 문서의 코인 수집 금지·호가 1초 샘플링·E 드라이브 전용 지시는 현재 사용자 요구를 대체하지 않는다.

### 1.2 사실·가설 구분

| 관측/주장 | 판단과 계획 반영 |
|---|---|
| 사용자 120초 표본: crypto-storage 97.06 wIOPS / 0.465MiB/s, NATS 3.72 wIOPS | 그 구간의 주된 작은 쓰기 발생원이 crypto-storage라는 강한 근거. 저장기 우선 개선 |
| 약 4.9KiB/물리 쓰기와 SQLite 4KiB page | SQLite 기여를 지지하지만 특정 index/WAL의 기여율을 확정하지 못함 |
| busy 표본에서 maintenance I/O 거의 0 | 초기 원인 일부가 개선됨. 모든 상황에서 무해해진 것은 아님 |
| 120초 중 main DB fdatasync 55.9초, WAL 0.68초, Parquet 등 fsync 0.29초 | checkpoint가 큰 stall을 만들 수 있음. 반드시 checkpoint 포함 구간을 측정 |
| 다른 짧은 구간의 WAL write 12,040회, main DB write 0 | checkpoint 없는 구간일 수 있음. WAL syscall 수를 물리 IOPS로 환산하지 않음 |
| table·PK·event·prune B-tree 존재 | 쓰기 증폭 후보. 각 구조의 비용을 분리 검증 |
| 저장된 최근 20만 행의 event_key 중복 0 | **입력 중복 빈도의 증거로 부적합.** 저장 전 dedup으로 제거된 이벤트가 표본에 없을 수 있음 |
| NATS I/O가 작음 | 그 구간의 주요 쓰기 발생원은 아님. 공유 디스크 경합의 피해자이며 제어 지연·capacity는 계속 측정 |
| 약 100 IOPS 포화 | 그 구간의 공급 I/O가 부족함. 실제 SKU·버스트 잔량·지속 한도 미확정. 모든 구현에서 세 거래소가 불가능하다는 증거는 아님 |
| DB 1.32GB, freelist 0 | 그 시점의 자유 page가 없다는 뜻. 모든 page가 조밀하거나 모두 필요한 데이터라는 뜻은 아님 |
| 빠른 업로드 | 로컬 용량 회수에 유효하지만 이미 발생한 WAL/index/checkpoint 쓰기를 없애지 못함 |

**원인 모델:** 원장 갱신의 쓰기 증폭과 checkpoint burst → 공유 디스크 지연 → NATS 응답/발행 지연 또는 ACK 정체 → collector pause. 장애 원장 잠금 오류가 겹치면 복구 루프 자체가 종료될 수 있다.

### 1.3 checkpoint 확대 권고에 추가해야 할 실패 시험

이번 대화에서 실제 비교한 조합:

- SQLite cache 64MiB, wal_autocheckpoint=16384. 4KiB page 기준 명목상 약 64MiB.
- 업로드 대상 대기 60초, 정상 idle cycle 60초, 로컬 보관 목표 1,800초.
- scheduler none → mq-deadline, nr_requests 256 → 32 → 8.

| 구간 | 결과 |
|---|---|
| cache/checkpoint 변경, 기존 queue | util 100%, await 약 734ms, publish failure 재발 |
| queue 32 | publishing 유지 구간이 생겼지만 NATS 제어 요청 약 2.05초 경고 |
| queue 8 | await 약 100–116ms로 감소했지만 ACK 정체 지속 |
| 07:36:06 → 07:37:37 KST | CRYPTO_DATA 22,790건/58.1MB → 42,545건/111.7MB. 세 거래소 nats_stream_capacity pause |

**이 조합은 수집 정상화 실패다.** timeout 감소만으로 성공 처리할 수 없다. 여러 요소가 바뀐 비교이므로 개별 요소의 단독 효과나 개선 배수도 확정하지 않는다.

큰 checkpoint가 I/O를 모으고 commit/ACK를 지연시킬 위험이 드러났다. 따라서 “threshold를 더 크게 하면 해결”은 기본 처방에서 제외한다.

자동 checkpoint는 PASSIVE이다. PASSIVE는 lock 대기를 피하는 성질이지 I/O 작업량·실행 시간 제한이 아니다. WAL 파일 크기만으로 미반영 frame 수도 알 수 없다. [SQLite WAL](https://www.sqlite.org/wal.html), [checkpoint 명세](https://www.sqlite.org/pragma.html#pragma_wal_checkpoint).

### 1.4 다음 구현자가 먼저 확인할 배포 상태

아래는 마지막 직접 확인 상태이다. 첨부 작성 이후 변경 가능성이 있으므로 다음 접속 때 재확인한다.

- 배포 확인 image: vibetrading-crypto:r1-6ff17203f98c479e45c2.
- image ID: sha256:b03ac88409220f7bf2596ccff2dc6c93751c1a9e7dca3c7a97c22d82257aaee6.
- 이 이미지로 기동한 코인 서비스 5개는 16,384-page 시험 설정을 사용했다.
- threshold를 1,000으로 되돌린 파일은 로컬/준비 디렉터리에 반영했다.
- 후속 image vibetrading-crypto:r1-b1c2559dfa691970e506의 빌드 완료는 확인했다.
- **후속 image의 운영 적용은 확인하지 못했다.** 자동 승인 심사가 사용량 한도로 실패해 후속 SSH가 차단됐다.
- 파일의 1,000과 실행 중 설정을 혼동하지 않는다. 현재 실제 값은 다음 접속 때 확인한다.
- mq-deadline / nr_requests=8은 마지막 확인된 일시적 host 설정이다. 영구 udev 규칙은 적용하지 않았다.
- 마지막 직접 관측한 collector 상태는 capacity pause였다. 이를 문서 작성 시점의 실시간 상태로 단정하지 않는다.
- 102개 오프라인 회귀와 격리 NATS 400건 시험은 통과했으나 운영 처리량은 실패했다.

running / staged / rollback을 별도로 기록한다. 원격 확인 없이 로컬 파일만 수정하고 서버 복구 완료로 보고하지 않는다.

---

## 2. 모든 단계의 불변 조건

### 수집·비용·자원

- 세 거래소의 BTCUSDT·ETHUSDT·SOLUSDT USDT 무기한 선물 수집 유지.
- 체결·호가는 원본 WebSocket snapshot/delta 보존. 샘플링·depth 축소·종목 누락으로 I/O 수치를 낮추지 않는다.
- 펀딩비·OI 수집과 관측값·단위·거래소 시각 구분도 유지한다.
- Market/국내·미국 수집의 종류·심볼·세션·무료 경로 제한·지연 표시 유지.
- 20GB 데이터 볼륨 유지. 여유 2.3GB의 root disk로 NATS를 옮기거나 버킷을 SQLite/NATS 파일시스템처럼 mount하지 않는다.
- 새 외부 유료 서비스 미도입. 기존 Ncloud의 요청·보관·다운로드 비용은 사용량과 함께 기록.
- 실제 거래 기본 비활성. 수집 정상화의 완료 조건에 trading system 완성을 넣지 않는다.

### 저장·ACK·삭제

~~~text
NATS 수락/배달
→ 파일 쓰기·fsync·rename·부모 directory fsync
→ manifest와 delivery/alias의 동일 SQLite transaction 확정
→ ACK 전송
→ 별도 서버 확인 ACK 상태 갱신
→ 원격 데이터와 복구 metadata 검증
→ 모든 보존 조건 충족 항목만 정리
~~~

- WAL과 synchronous=FULL 유지. 파일/directory fsync 실패를 성공 처리하지 않는다.
- FULL commit은 WAL의 내구성 확정이다. 매 ACK마다 main DB checkpoint 완료를 추가로 요구하지 않는다. WAL sync 전 ACK도 금지한다.
- 같은 delivery 재전달과 다른 sequence의 동일 event를 구분한다. 원본이 메모리에 있다는 이유로 duplicate ACK를 보내지 않는다.
- 로컬 ACK 송신 성공과 서버 확인 floor 구분. generation·durable/consumer incarnation이 불명확하면 prune 금지.
- delivery 정리: 기록 후 24시간 이상 + 원본/metadata 원격 검증 + 같은 stream 세대의 신뢰할 수 있는 ACK 확인.
- 주문·체결 감사 원장은 이 TTL에서 제외한다.
- NATS 배수는 저장·ACK로 수행한다. purge·consumer 삭제/재생성·조기 ACK로 비우지 않는다.

무손실 검증은 시험 입력 또는 이미 수락된 delivery 집합을 기준으로 한다. 수집 중지 동안의 거래소 미수신 이벤트까지 보존했다고 주장하지 않는다. 중단·미전송·수락 여부 불명 구간을 별도 기록한다.

---

## 3. 원인과 대응 연결

| ID | 원인/잠재 원인 | 대응 | 위치 |
|---|---|---|---|
| C1 | probe 실패를 usage 100%로 오인 | fresh/cached/unavailable, 제한된 LKG | R1-A |
| C2 | 최초 원인과 재개 차단 조건 혼합 | primary/blocking 분리, pause latch | R1-A |
| C3 | 고정 disk 재개 조건 불일치 | 보호 공간 R + margin H + 안정 시간 | R1-A |
| C4 | maintenance scan/backup/checkpoint 경합 | 공통 lock·단계별 양보·daily backup | R1-B, R2-D |
| C5 | 장애 원장 read→write 경합·연결 누수 | BEGIN IMMEDIATE·close·제한된 기다림 | R1-A |
| C6 | delivery table/PK/event/prune 쓰기 증폭 | 동일 입력으로 구조별 비용 비교 | R2-A/B |
| C7 | checkpoint가 commit/ACK 장기 정지 | 단계별 시간 계측·필요 시 책임 분리·WAL 상한 | R1-B, R2-C |
| C8 | 모든 신규 event exact lookup | batching·필요 시 Bloom negative 경로 | R2-D, R3-A |
| C9 | 저장 후 표본으로 입력 중복률 추정 | dedup 이전 입력/후보/적중/alias 계수 | R1-B |
| C10 | prune이 유입보다 느리거나 계속 defer | confirmed floor·증분 수지·제한된 adaptive prune | R2-A/C, R3-A |
| C11 | migration 시 implicit rowid 참조 파손 | stable manifest identity 또는 명시적 remap | R2-B |
| C12 | WAL/reader/checkpoint 장애로 공간·복구시간 증가 | backlog/age·단일 소유권·soft/hard budget | R2-C |
| C13 | 업로드 가속을 IOPS 해결로 오인 | 용량 목표와 쓰기 목표 분리 | R1-B, R2-C |
| C14 | paused 구간의 낮은 I/O를 개선으로 오인 | 입력·ACK·gap과 같은 시간축 판정 | 전 단계 |
| C15 | 실행 image/config/host 설정 불일치 | digest·revision·effective config 기록 | 전 단계 |

---

## 4. 공통 계측과 시험

### 4.1 필수 metric

프로세스 시작 ID·monotonic 시각·UTC/KST를 연결하고 10–30초 간격으로 기록한다. 계측 자체가 전체 원장 scan을 일으키면 안 된다.

| 구간 | 계측 |
|---|---|
| Exchange → collector | provider/symbol/type별 수신 건수·byte, 마지막 수신, reconnect/gap |
| Publisher → NATS | accepted/pending/success/failed/unknown, queue byte, publish p50/p95/p99 |
| NATS | stream byte/한도/세대, pending/ack_pending, confirmed floor, probe 상태·시간·연속 실패 |
| Storage | fetch/조회/정규화/Arrow/파일 쓰기·sync/manifest transaction/ACK 단계 시간, flush 이유·rows·files·bytes |
| SQLite | 실제 writer 연결 PRAGMA, transaction 수/rows, query 수, WAL 기록량·checkpoint 진행·시간·busy·reader age, DB/WAL/SHM |
| OS/cgroup | device IOPS/byte/await/queue, I/O PSI, RSS·working set·dirty/writeback, CPU·OOM |
| Archive | eligible/uploaded/verified/reclaimed, 가장 오래된 미업로드 age, 속도·실패 |
| Retention | insert-prune 차이, eligible/ineligible backlog 추정, defer 이유·기간, 예상 disk 고갈 시점 |

주의:

1. 새 sqlite3 연결에서 읽은 cache/autocheckpoint는 실제 writer 연결 값이 아니다. writer 생성 직후 설정 적용과 readback을 health/provenance에 노출한다.
2. WAL stat 크기는 재사용 공간을 포함한다. 실제 미반영 frame과 분리한다. PASSIVE checkpoint를 읽기 전용 metric처럼 주기 호출하지 않는다. NOOP 지원도 실행 SQLite 버전을 확인한다.
3. WAL syscall에는 작은 frame header 기록도 있다. syscall 수·frame 수·물리 IOPS를 별개로 보고한다.
4. strace는 fd 매핑과 unfinished/resumed를 함께 처리한다. payload·키 출력 금지. 계측 overhead도 기록한다.
5. 입력 중복률은 dedup 전 측정한다. models.py의 event identity가 native ID인지 connection/receive sequence 기반인지 event별로 구분한다. reconnect 간 logical identity를 일괄 가정하지 않는다.

### 4.2 비교 방법

1. 운영과 유사한 크기·분포의 **일관된 DB 복사본**을 사용한다. 빈 DB 시험으로 1GB 이상 원장 성능을 결론 내리지 않는다.
2. 같은 원본 feed와 timestamp로 trade/book/funding/OI, 거래소·종목 비율, event_id 분포, reconnect burst를 재생한다.
3. 정상 유입과 관측 피크 2배를 분리한다. pause 때문에 낮아진 유입을 피크로 쓰지 않는다.
4. 후보 하나당 같은 입력·초기 DB·cache·장치 조건. warm/cold 구분.
5. 실제 NATS FILE stream과 ACK 경로 포함. 임시 network/subject/consumer/파일을 사용하고 운영 키·subject에 접근하지 않는다.
6. 기능 → 1배 → 피크 2배 30분 순서. 종료 시 미반영 WAL·미확정 ACK·archive backlog를 포함해 같은 확정 상태까지의 비용을 비교한다.
7. burst credit이 남은 결과만으로 지속 처리량을 합격시키지 않는다. 실제 기본 한도를 확인해 격리 환경에 반영한다. 미확인 시 100 IOPS 등의 보수적 시험 가정과 한계를 명시한다.
8. 운영 VM에서 시험이 수집을 방해하면 명시적인 중단 구간으로 기록한다. 운영 durable consumer에 시험 입력을 섞지 않는다.

### 4.3 수집 정상화 Gate P

- 정상 입력에서 반복 pause/recover·publish timeout·무기록 폐기·OOM·잘못된 ACK 0.
- 장애 주입 시에는 기대 reason으로 pause/gap을 남기고, 조건 회복과 안정 시간 후 resume.
- 서버 확인 ACK가 유입을 따라가며 pending/WAL/archive backlog가 지속 증가하지 않음.
- 전체 volume에 I/O 여유 확보. 지속 한도 B가 확인되면 NATS/Market/Docker/maintenance를 포함해 20% 이상의 여유 목표. **crypto 70–80 IOPS는 B≈100인 경우의 잠정 예산**이다.
- util 80%나 단일 await만으로 판정하지 않음. queue/PSI/publish latency/ACK stall 동시 확인. 현재 발행 timeout 5초 대비 충분한 p99 여유와 반복 수초 지연 제거.
- 여러 checkpoint를 포함. checkpoint 전 또는 paused인 낮은 I/O 구간은 성능 증거 제외.
- 입력 identity·delivery·canonical row·alias·manifest·confirmed ACK 집합 대조. 건수만 비교하지 않음.
- 실제 새 파일의 upload/verify/reclaim 확인. 계속 defer된 상태를 local 30분 보관 성공으로 표시하지 않음.
- Market format/symbol/timestamp/session/정책 회귀. 비개장 시간의 idle만으로 정상 판정하지 않음.

48시간 관찰은 대화를 유지하며 수행하지 않는다. 이후 사용자가 제공하는 로그로 별도 장기 합격을 판정한다. metric과 로그 순환 보존을 준비하되 이 문서만으로 새 자동화 작업을 설치하지 않는다.

---

## 5. Release 1 — Incident Control & Verified Baseline

### R1-A. 실행 상태와 장애 제어

변경 후보: market_policy.py, storage_guard.py, runtime.py, bus.py, health.py, gap_ledger.py, maintenance_gate.py, archive.py, release compose/검증 스크립트. 이미 배포된 것은 재작성하지 않고 차이를 감사한다.

1. image/source/env hash·mount·실제 SQLite 연결 설정·host queue를 수집해 running/staged/rollback 표 작성.
2. 16,384-page 실패 시험과 1,000-page 준비 이미지 상태 확인. 기준값 복원은 비교 기준 회복이며 성능 해결로 보고하지 않음.
3. probe fresh/cached/unavailable. 성공 시각·generation·byte/한도와 cache 유효기간·실패 횟수 관리.
4. nats_probe_unavailable / nats_stream_capacity / nats_publish_timeout / disk_reserve / storage_stalled / publisher_worker_failed 등 실제 원인 분류.
5. primary_reason은 incident 동안 보존. blocking_reasons는 현재 재개 차단 조건으로 갱신.
6. R은 미확정/NATS·WAL·flush·고정 여유 예산, H는 resume margin. 중복 계산 금지. migration/build 임시 공간은 별도 추가.
7. paused에서는 WS 반복 생성 금지. 자원 회복 확인 후 안정 시간 경과 시 연결.
8. connecting/retrying/recovering과 publishing/healthy 구분. 복구 알림은 정상 발행 지속과 적체 안정/감소를 함께 확인.
9. gap ledger의 read→write 경합과 연결 수명 수정 검증. 잠금 timeout에도 원래 미전송 계수·원인·시각 보존. 무한 retry/예외 삼키기 금지.
10. bounded buffer 종료·NATS drain/close timeout 유지. 실패와 수락 여부 불명 계수 분리.
11. queue tuning은 latency 완화 후보. ACK 부족 해결로 인정하지 않으며 처리량 합격 전 영구 host 규칙으로 고정하지 않음.

### R1-B. maintenance 회귀 방지·계측·제한적 설정 비교

- 양 domain heavy I/O 공통 lock과 resource isolation 유지.
- pressure 검사 전에 DB를 열지 않음. scan/upload/reclaim/backup/prune/checkpoint 단계별 양보.
- 전체 DB backup 기본 하루 1회. DB/WAL·임시 사본·R/H 공간 검사. disk pressure로 backup이 반복 실행되지 않게 함.
- read progress handler는 SELECT 예산이다. write/checkpoint를 안전하게 취소하는 장치로 오인하지 않음.
- heartbeat의 전체 COUNT 제거. 계수나 제한된 offline 진단 사용.
- cache는 cgroup/Arrow/Python/ACK handle/dirty peak 예산 안에서만 비교. 캐시 확대로 작은 파일 flush가 늘어나는 회귀도 확인.
- 큰 autocheckpoint는 기본 처방 제외. 필요가 입증된 후보 하나만 격리 비교하고 최대 commit stall과 stream backlog까지 검증.
- 실제 연결의 synchronous/cache_size/wal_autocheckpoint/page size와 checkpoint 결과를 health에 기록.
- upload 대상 대기 60초·정상 idle 60초·local target 30분은 용량 완화 후보. pressure 양보와 원격 전체 checksum/metadata 확인 후 삭제 조건 유지. 즉시 업로드/삭제 보장으로 설명하지 않음.

### R1-C. 작은 안전 결함 선행 차단

기존 R1 경계에서 의도적으로 앞당긴 최소 수정이다. 새 alias schema/orphan format은 도입하지 않는다.

1. storage_worker.py의 pending event_id 중복 경로에서 원본 flush 전 ACK 가능성을 차단한다. 기존 schema에서 보류/재전달로 안전하게 처리하고 보류 handle/byte 상한을 둔다.
2. 운영 Linux/ext4의 directory fsync 실패를 무시하지 않는다. 실패한 파일의 manifest/ACK 확정을 막는다. Windows 테스트의 미지원 처리와 운영 실패를 구분한다.
3. prune floor가 로컬 ACK 추정으로만 전진할 수 있으면 신뢰 입증 전 보수적으로 제한한다. 제한에 따른 DB growth를 기록하고 R2-A로 연결한다.

### R1 시험과 gate

- 단발 probe timeout/cache 유효: pause 없음.
- cache 만료/반복 control 장애: 명시적 pause.
- 실제 capacity/disk reserve: 별도 reason, 회복 후 자동 resume.
- 세 거래소 동시 gap 기록, 기존 writer lock, close/publisher 실패, 재시작 시험.
- pending duplicate 후 원본 commit 전 kill: 조기 ACK 없음.
- fsync/manifest 오류: ACK 없음.
- Market/Crypto 회귀, 실제 NATS E2E, 동일 시간축 I/O·입력·ACK·checkpoint 보고.

**Gate R1-S:** 제어·안전 시험 통과, 실행 코드 일치, 잔여 원인 특정. 통과 전 다음 단계 코드 변경을 섞지 않는다.

**Gate P:** 전체 처리량 기준은 별도 판정. R1에서 P 실패 시 “R1 제어 검증 통과 / 수집 정상화 미달”로 기록한다. R1-S 통과와 저장 병목 근거가 있을 때 R2 격리 개선으로 진행한다. P를 통과해야 P의 원인을 수정할 수 있다는 순환 조건을 만들지 않는다.

---

## 6. Release 2 — Storage Write Stabilization & Common Ingestion

순서: **R2-A 안전 기반 → R2-B 구조 후보 → 필요 시 R2-C checkpoint/retention → Gate P → R2-D 공통화.** 각 묶음은 별도 diff·단위·회귀·NATS·부하 결과를 제출한다.

### R2-A. 원장 변경의 안전 기반

#### A1. delivery와 event 상태 분리

~~~text
D = (stream identity, generation, sequence)
E = 검증된 event identity

새 D / 새 E       → canonical row + delivery 동일 manifest transaction
동일 D 재전달    → 확정 확인 또는 pending handle 교체
새 D / pending E → ACK 보류, canonical 확정과 함께 alias 확정
새 D / 확정 E    → alias durable 확정 후 ACK
~~~

- canonical Parquet row는 한 번 저장. alias는 확정 row/file과 복구 가능한 관계를 가짐.
- alias transaction 전후 crash, 원격 전용 canonical, 재시작 pending alias, 동일 D 반복을 시험.
- 현재 event_id 의미 유지. native ID와 connection/receive ID를 혼동하지 않음.
- hash와 실제 중복 의미는 별개. 모호한 identity는 exact 경로에서 검증.

#### A2. 서버 확인 floor와 generation

- ack_sent / ack_server_confirmed / prune_eligible 구분.
- generation 조회 실패를 빈 문자열이나 stream 이름으로 대체 금지.
- stream/consumer 조회 사이 재생성을 감지하도록 세대 전후 대조. durable/consumer incarnation·filter·delivery/ACK 정책 검증.
- legacy ack_floor가 local/server 값을 혼합했다면 신뢰하지 않음. confirmed provenance 분리 또는 미확정 migration. 높은 legacy 값에 무조건 MAX 적용 금지.
- floor 이하라도 canonical/alias의 durable·remote·24시간 조건을 동시에 확인.
- generation 변경·consumer 재생성·순번 reset·ACK 유실·역순·hole을 주입. 미확정이면 floor 미전진.

#### A3. migration/prune용 최소 복구 metadata

- file identity·schema version·stream generation·정확한 delivery coverage·alias 관계를 복구할 수 있어야 함.
- min/max sequence로 중간 hole을 채우지 않음.
- 필요 시 footer/sidecar version 추가와 구형 reader 호환 시험. 구형 파일의 불명확한 범위를 추정으로 승인하지 않음.
- fsync→rename→directory fsync→manifest/delivery 경계 crash 일관성을 먼저 검증. orphan 발견만으로 ACK 금지.

### R2-B. B-tree 쓰기 감소 — 후보별 채택

대상: sqlite_store.py, migration/검증 스크립트, storage 파이프라인 시험. schema version을 명시한다.

#### B0. offline 비용 분해

- 일관된 DB 복사본의 table/index page 크기·밀도, insert/lookup/prune query plan 확인.
- 현행, WITHOUT ROWID, event index 후보, prune index 후보를 각각 비교.
- index를 삭제한 clone은 원인 진단 실험일 뿐 운영 적용 승인이 아님.
- physical I/O/1,000 delivery, WAL bytes/delivery, 전체 checkpoint 포함 시간, DB 증가, lookup latency, peak memory 비교.

#### B1. WITHOUT ROWID 우선 후보

- delivery table만 (stream_id, stream_sequence) PK의 WITHOUT ROWID로 비교.
- 1차 후보는 event/prune index와 exact dedup 유지. table/PK 구조만 비교 변수로 사용.
- delivery.rowid 기반 prune/조회/migration을 composite key로 변경. lastrowid·NULL PK·변경 계수·재시작 시험.
- **delivery rowid와 manifest rowid는 별개.** 현재 file_rowid는 manifest의 implicit rowid를 참조한다.
- manifest를 보존하는 table-level migration 또는 명시적 stable manifest ID/remap을 선택·검증한다. DB 재구축 후 동일 file_id 연결을 입증하지 않고 교체하지 않는다.
- 보조 index의 PK 포함 비용과 random event index 쓰기는 남는다. “B-tree 하나 감소 = IOPS 25% 감소” 금지.
- 이득이 작으면 기각. schema 복잡도만 늘리는 변경은 배포하지 않는다. [SQLite WITHOUT ROWID](https://www.sqlite.org/withoutrowid.html), [rowid 주의사항](https://www.sqlite.org/rowidtable.html).

#### B2. persistent event-key index — 필요 시에만

- B1 뒤 event index가 주요 비용으로 남는 경우 진행.
- 저장된 행의 duplicate 0을 exact 보호 제거 근거로 쓰지 않음.
- bounded exact recent index, 시간/세대별 exact 구조, immutable recovery metadata를 통한 정확 조회 등을 후보 비교.
- recent window 밖 재전달/backfill/replay 계약 명시. 오래된 이벤트는 exact cold lookup 또는 별도 replay 절차로 처리. 신규로 조용히 간주하지 않음.
- 24시간 delivery 보존과 event dedup 기간을 동일 개념으로 취급하지 않음. 통계만으로 TTL 축소 금지.
- replacement의 crash/restart/remote restore 통과 후에만 기존 index 제거. 완전한 exact 경로를 제공할 수 없으면 유지.

#### B3. prune index — confirmed floor 이후

- sequence range만으로 age를 추정하지 않음. 24시간 조건을 증명할 file/batch age 또는 검증된 range metadata 필요.
- generation·정확 coverage·canonical/alias·remote 검증의 교집합만 삭제.
- 작은 범위 DELETE와 부수 index 비용까지 비교. 큰 DELETE/VACUUM으로 몰아서 처리하지 않음.
- index 제거 대신 추가되는 metadata 쓰기 비용까지 포함.

### R2-C. checkpoint 책임과 retention 진행

#### C1. checkpoint 책임 분리

단순 threshold 확대가 아니라 **durable WAL commit과 main DB 정리가 한 호출에서 묶여 수집을 막는지** 먼저 입증한다.

- FULL sync writer와 checkpoint 수행 주체를 구분하고 DB별 checkpoint 단일 소유권 확보.
- maintenance의 짧은 metadata commit이 큰 WAL checkpoint를 대신 수행하는 경로까지 모든 연결 감사.
- 필요 시 별도 연결/작업에서 PASSIVE 수행. 한 호출의 시간/I/O 상한은 보장되지 않음을 설계에 반영.
- wait_for(to_thread(checkpoint)) timeout은 thread 취소가 아님. 기존 작업이 살아 있으면 추가 checkpoint 시작 금지.
- busy_timeout은 fsync 대기를 제한하지 않음. TRUNCATE는 정상 live 경로에서 제외하고 배수된 유지보수 구간의 공간 회수 필요 시 사용.
- autocheckpoint=0은 기본값으로 도입하지 않음. owner 감시·실패 모드·WAL soft/hard budget·backlog/reader age·복구가 검증된 경우에만 후보 평가.
- owner 장애/reader starvation/hard limit이면 안전 입력 중단. WAL 수동 삭제·불명확한 강제 checkpoint·commit 전 ACK 금지.
- 다른 프로세스로 I/O를 옮겨 storage cgroup 수치만 낮아지면 개선이 아님. volume 전체 I/O와 end-to-end ACK/복구 비용으로 비교.

#### C2. 원장 증가 수지와 archive

- 원장 증가 = 신규 delivery/alias − 안전 prune. 증분 계수로 유지.
- 예를 들어 매 60초 또는 300초에 8,192행을 한 번 삭제할 수 있다면 단순 환산은 각각 약 136.5행/초, 27.3행/초다. 이는 해당 실행 간격의 예시이며 전체 구현의 처리량 상한이 아니다. 실제 cycle은 busy 재시도·작업 시간·defer 조건에 따라 달라지므로 완료된 삭제량/경과시간을 측정한다.
- 유입이 실제 안전 prune 처리량을 계속 넘으면 24시간이 지나도 원장은 증가한다. 유지 가능한 수지를 합격 조건으로 둔다.
- eligible backlog에 따라 rows/time/byte 예산을 조절하되 writer backlog와 I/O pressure 우선.
- 현실 입력 중 prune/upload가 진행되는지 확인. 항상 양보하여 실행되지 않으면 완료 아님.
- quiet 구간을 확보할 수 없으면 Gate P 실패. retention 몰래 단축 또는 unsafe prune으로 20GB를 맞추지 않음.
- old DB/export/image 정리는 복구 필요성·원격 검증·롤백을 확인한 항목만 후보 목록화.

### R2-D. 검증된 구현에서 공통 core 추출

진입 조건: 선택한 저장 개선의 Gate P 통과와 canonical/alias/recovery 결과 확정. 새 성능 알고리즘을 동시에 도입하지 않는다.

- IngestionDomain: domain별 subject/stream/durable/state/parquet/health/config identity.
- PressureSnapshot: 시각·신뢰 상태·NATS generation/사용량·disk R/H·storage/publisher pressure.
- PressureDecision: pause/resume·primary/blocking·회복 안정 시간.
- 공통 probe/pressure, bounded publisher의 accept/publish/failure/close 의미.
- 공통 fetch→commit→ACK StorageWorker.
- 공통 health/readiness·gap 인터페이스, maintenance gating/disk budget, config 해석/validation.
- 주식에서 검증된 부분을 추출하되 작은 이벤트량·샘플링·세션 가정을 코인 raw stream에 복사하지 않음.
- .server_patch 및 서버 bind patch의 production 의존성 제거. 적용 patch를 main source로 통합한 뒤 계약 비교.
- domain 하나씩 연결하고 golden replay·NATS·운영 확인. 양쪽 adapter 동시 전면 교체 금지.

격리 유지:

~~~text
MARKET_DATA != CRYPTO_DATA
durable consumer / SQLite / Parquet 경로 분리
storage process / 거래소 collector process 분리
메모리·queue 한도 / 장애 상태 분리
~~~

새 global pressure controller는 필수 항목이 아니다. 공통 판단 함수와 domain별 상태로 해결되는 문제에 별도 control plane을 추가하지 않는다.

### R2 migration·배포·롤백

1. running release와 schema/identity 기록. live SQLite의 단순 파일 cp를 일관된 backup으로 간주하지 않음.
2. 필요한 원본·metadata·DB backup의 원격 checksum과 복원 가능성 검증.
3. 새 DB/index·WAL·검증/rollback 사본·Docker build 증가분을 포함한 peak 공간 dry-run. free ≥ 4GB만으로 합격하지 않음.
4. 부족하면 검증된 비활성 사본/캐시 정리를 먼저 검토. 자동 증설·root 이전·미확정 삭제 금지.
5. 수집 중단/gap 기록. 정상 commit/ACK로 NATS 배수. stream/consumer 삭제 금지.
6. storage/maintenance writer 정지, 일관된 cutover, 선택한 변경 하나만 migration.
7. integrity·참조·세대/순번·event 집합·alias/manifest coverage 대조. row count만으로 합격하지 않음.
8. schema version 검사로 구형 코드의 잘못된 수정을 차단. 저장기 단독 기동→검증→거래소별 collector→maintenance 순서.
9. 첫 수신·durable ACK·원격 검증 확인. rollback image/DB/명령/시험 결과 보관.
10. **새 DB에서 ACK된 데이터가 생긴 뒤 old DB를 덮어쓰는 rollback 금지.** 새 schema 호환 이전 binary, 검증된 reverse migration, 또는 cutover 이후 Parquet/metadata 병합 복구 중 준비된 절차만 사용. ACK된 메시지는 NATS에 없을 수 있음.

**Gate R2:** 각 후보의 안전/회귀/NATS/부하, migration/rollback, Gate P, 공통화 전후 결과 동등성 통과. 실패 후보는 같은 baseline으로 되돌리고 다음 후보를 비교한다.

---

## 7. Release 3 — Long-Term Safety, Performance & Trading Readiness

R1·R2 수집/저장 검증 후 진행한다. R2 완료 기능을 다시 재설계하지 않는다.

### R3-A. 후순위 성능·장기 보존

- **Bloom:** exact lookup이 실제 병목으로 남을 때만. negative만 조회 생략, positive exact 확인. 초기화/재시작 coverage 미완성·새 writer·schema/generation 변경 시 exact fallback. commit-key 반영 순서와 race 검증. persistent index 쓰기 자체는 줄이지 못함.
- **Incremental counters:** R2 최소 수지 확장. 같은 transaction 갱신, crash 재조정은 offline/제한된 작업. heartbeat COUNT 재도입 금지.
- **Adaptive prune:** 안전 predicate 유지하면서 작업 예산 조정. throughput·eligible age·I/O·장기 growth 검증.
- **Parquet/Arrow/flush:** 고빈도 크기, 저빈도 최대 age. memory pressure에서는 큰 파티션부터. 작은 파일·transaction 재증가 회귀 검사.
- **Hash:** 현재 read I/O 기여가 작아 후순위. streaming hash는 seek/재기록을 고려하여 최종 파일의 정확한 SHA를 보장할 때 채택. archive 때까지 hash를 미뤄 local manifest 검증 능력을 잃지 않음.
- **Retention/remote archive:** 기존 원격 보존 정책과 주문 감사 유지. local 30분 실현 여부, DB/WAL/backup/image 포함 전체 사용량, 원격 복원 시간·비용 확인.

### R3-B. 복구·운영 강화

- R2 최소 metadata 외 전체 orphan/restart recovery. 구형 schema/세대·원격 전용 파일·sidecar 일부 유실 포함.
- 파일/fsync/rename/manifest/ACK/upload/verify/reclaim 각각 전후 kill.
- hung watchdog은 heartbeat와 실제 진행·진행 중 작업을 구분. 긴 정상 checkpoint를 죽이는 재시작 폭주 금지.
- 종료 제한, NATS 불능, ENOSPC, readonly filesystem, 원격 장애, clock jump, memory pressure 동시 시험.
- 원격 목록·metadata·DB backup으로 새 환경 복원. backup 이후 ACK 범위 보존.
- 이후 제공되는 48시간 로그로 gap·수집률·DB growth·WAL age·prune/archive backlog·memory plateau 판정. 대화 유지 방식 관찰 제외.

### R3-C. 거래 판단용 데이터 준비

- 거래소별 book reconnect/resync/desync. sequence 규칙 획일화 금지.
- raw 수집은 보존하되 불완전/stale book은 주문 판단에서 제외.
- durable publish, live fan-out, book readiness 독립 결과. live subscriber 장애가 durable 수집을 중단시키지 않게 함.
- provider 연결·book 동기화·timestamp 품질·freshness·queue lag로 trading readiness 판정.
- 실거래 기본 비활성. Bybit testnet 등 거래 검증은 별도 실행 범위·키·주문 감사 원장으로 유지.

**Gate R3:** 전체 fault injection, Market/Crypto 회귀, 실제 NATS 부하, 원격 복원, 장기 로그 판정. readiness 통과는 실거래 자동 활성화가 아니다.

---

## 8. 기존 세부 항목 재배치 추적표

| 기존 항목 | 새 위치 |
|---|---|
| probe 실패의 usage 100% 취급 제거 | R1-A |
| fresh/cached/unavailable·LKG | R1-A |
| reason 분해·primary/blocking | R1-A |
| disk R/H·paused WS 재접속 억제 | R1-A |
| connecting/paused/retrying/publishing 의미 | R1-A |
| maintenance 동시 실행 방지·I/O 양보 | R1-B → R2-D |
| daily backup·공간 검사 | R1-B, R2 migration, R3-B restore |
| immutable digest/revision/config hash | R1-A 및 모든 배포 |
| pressure/NATS/disk/storage/publisher metric | 공통 계측, R1-B |
| IngestionDomain·PressureSnapshot·PressureDecision | R2-D |
| 공통 probe/pressure·bounded publisher | R2-D |
| 공통 fetch→commit→ACK | R2-D |
| health/readiness·gap 인터페이스 | R2-D, 거래 readiness R3-C |
| maintenance/disk budget/config validation 통합 | R2-D |
| .server_patch 의존성 제거 | R2-D |
| stream/consumer/SQLite/Parquet/process 격리 | 전 단계 |
| 미확정 duplicate·delivery alias | 조기 ACK 금지만 R1-C, 영속 관계 R2-A |
| server-confirmed ACK floor/generation | R1-C 보수적 제한 → R2-A |
| fsync/rename/directory/manifest crash consistency | R1-C 실패 처리, R2-A 필수분, R3-B 전체 |
| orphan/restart metadata | R2-A prerequisite, R3-B 전체 |
| Bloom filter | R3-A |
| incremental counters | R2-C 수지, R3-A 확장 |
| adaptive prune | R2-C 안전 진행, R3-A 확장 |
| WAL/checkpoint/flush | R1-B 진단·제한 비교, R2-C 병목 해결, R3-A 후속 |
| WITHOUT ROWID·event/prune index 후보 | 새 진단 반영, R2-B |
| long retention/remote archive | R2-C 수지, R3-A/B 장기 |
| book reconnect/resync/desync | R3-C |
| durable publish/live fan-out 분리 | R3-C |
| trading readiness/freshness | R3-C |
| hung watchdog | R3-B |
| E2E fault injection·장시간 부하 | 단계별 필수 subset, 전체 R3-B |

---

## 9. 구현자 제출 양식과 진행 규칙

각 작업 묶음마다 다음 순서를 지킨다.

1. 현재 코드·실행본·invariant 확인: 기존 패치·미완료 배포·위험 명시.
2. 변경 파일/이유: 원인에 대응하는 최소 diff와 영향 domain.
3. 해당 묶음만 수정: 미래 단계 기능 선행 구현 금지.
4. 단위 시험: 오류 경로·안전 계약 우선.
5. Market/Crypto 회귀: 형식·정책·수집 결과 비교.
6. 실제 NATS E2E 및 필요한 부하 시험: 입력·확정·ACK·원격 집합과 자원.
7. 결과/미완료 보고: 같은 부하 여부·표본 길이·checkpoint/maintenance 포함 여부·실패 후보 기록.
8. 해당 gate 통과 후 다음 묶음: R1-S / P / R2 / R3를 통과·실패·미확인으로 별도 표시.

산출물:

~~~text
release manifest + effective connection/config snapshot
changed-files/reasons + rollback procedure
unit/regression/NATS/fault-injection results
input trace fingerprint + DB baseline fingerprint
time-aligned I/O / ingress / confirmed ACK / archive metrics
migration exact-set/reference verification
unresolved issues + next permitted work package
~~~

### 다음 실행의 첫 작업

**R1-A running/staged/rollback 대조부터 시작한다.** 실패 시험 잔여 설정·적체·보호 중단을 확인하고 기준 상태를 만든다. R1-S를 마무리하면서 원장 비용의 offline 비교 자료를 준비한다.

event-key 인덱스 삭제, 큰 autocheckpoint 적용, NATS 한도 확대, volume 증설을 첫 조치로 실행하지 않는다.

현재 결론은 **20GB에서 코드 개선 여지가 남아 있고 우선 대상은 SQLite 저장 메타데이터의 쓰기 비용**이라는 것이다. 이 계획은 그 가능성을 단계별로 검증하며, 모든 피크에서 영구 무중단을 사전에 보장하지 않는다.

---

## Release 1 실행 기록 — 2026-09-29

### 적용

- R1 코드와 설정을 immutable image로 빌드하고 운영에 적용했다. 운영 대상 8개 서비스의 image ID, build revision, config hash가 manifest와 일치함을 `verify_release_runtime.py`로 확인했다.
- 운영 이미지: revision `r1-8299908cbf057b73ebf6`; app image `sha256:c8689b8f46e9e79cf8c43c4460f5465dc724095b0c5223d2554eedb682f011f2`; crypto image `sha256:adec98135b87f1fdbc33c2f7009da4ad9170a34e2235d105a71b4358bdef0639`; config hash `e1d4f8a5943ebfd48da5fc425ffe4998a938a22bd34956f9b7812053bb9a78a2`.
- 적용 전 컨테이너/image/status 목록을 서버의 `/opt/vibetrading/releases/r1-release1-control-20260929/deploy/rollback-inventory-20260929.txt`에 저장했다. 기존 image tag는 유지했다. 데이터 볼륨은 20GB 그대로다.
- SQLite writer의 실제 PRAGMA는 WAL, synchronous=FULL(2), cache_size=-2000, wal_autocheckpoint_pages=1000, page_size=4096으로 확인했다. 두 maintenance 서비스의 backup interval은 86400초이며 crypto delivery prune은 비활성이다.

### 검증 결과

- 로컬 Release 1 집중 테스트: 63 passed, 1 skipped. 전체 로컬 회귀: 105 passed, 1 skipped, 2 deselected. `compileall`과 `git diff --check` 통과.
- 격리된 실제 NATS E2E에서 Market/Crypto 각 200건을 저장하고 manifest 확정 후 ACK되는 것을 확인했다. pending/ack_pending은 0이었다. Linux directory fsync의 EIO 주입은 오류가 전파되는 것을 확인했다.
- 배포 직후 CRYPTO_DATA가 113.7MB였고 storage consumer pending 36,992, ack_pending 6,912로 용량 압박이 확인되어 세 crypto collector는 `nats_stream_capacity`로 안전 정지했다. 약 7분 뒤 stream 사용량은 94.4MB, pending 33,920, ack_pending 3,200까지 감소했다. 이는 저장기가 적체를 따라잡는 진행 증거지만, 아직 consumer 전체 적체가 해소되거나 collector 자동 재개가 확인된 것은 아니다. 현재 관측 시점에는 3개 collector가 `nats_resume_hysteresis`와 `recovery_stability_window` blocker로 paused 상태다.
- 그 시점 storage health: buffer 0, ACK 1,909건, 실패 0, 마지막 ACK 2ms, RSS 약 180MB, cgroup 사용량 약 122MB/1GiB. 20GB volume의 여유는 약 5.7GB, root filesystem 여유는 2.3GB였다.
- Release runtime provenance 검증은 8개 서비스 모두 통과했다. NATS service healthy, storage/maintenance 서비스 healthy다. 국내 collector container는 unhealthy이며 KIS flow provider가 “KOSPI200 선물 외인 수급으로 검증되지 않음” 사유로 retrying 상태다. 이 provider의 데이터 적격성은 R1 배포로 바뀌지 않았으며 market 전체 health gate를 통과한 것으로 처리하지 않는다.

### Gate 판정 및 다음 단계

- **코드·배포 일치:** 통과.
- **R1 안전성 부분집합:** 단위/회귀, 격리 NATS ACK, fsync 실패 전파, 운영 중 실제 stream-capacity pause와 적체 감소 확인.
- **R1-S 전체:** 미통과/미확정. 운영에서 자동 resume, disk pause/resume, 반복 control-plane 장애의 실제 NATS 시험, Market/Crypto 전체 health 회복은 아직 입증되지 않았다.
- **Throughput Gate P:** 미통과. backlog가 감소했지만 collector가 재개되지 않았고, 관측은 단기 배포 점검뿐이다. 사용자의 지시에 따라 48시간 관찰은 수행하지 않았다.
- Release 2/3은 시작하지 않았다. R1 이외의 schema migration·index/prune 재설계 등은 적용하지 않았다. 이 기록은 지속 운영이나 반복 장애 완전 제거를 보증하지 않는다.

## Release 2 진행 기록 — 2026-09-29

> 후속 재검정: [구조·WAL 비용 분석](CRYPTO_IO_STRUCTURAL_REASSESSMENT_2026-09-29.md)을 우선 참조한다. checkpoint PID는 이후 종료됐으나 truncate 성공은 확인되지 않았다. 실제 원장/WAL의 로컬 분석에서 event hash index가 WAL frames의 95.89%를 차지했다. B1 WITHOUT ROWID 합성 후보는 WAL을 줄이지 못해 기각하고, B2의 exact 조회를 유지하는 immutable/batched event index 후보 검증으로 진행한다. 볼륨 증설을 선행 조건으로 삼지 않는다. 아래는 당시 관측 이력이며 현재 상태와 구분한다.

### R2-C1 안전 점검 시도

- Crypto NATS stream과 storage worker가 비어 있고 pending/ack_pending이 0인 시점에 `crypto-storage`와 `crypto-maintenance`만 정지한 뒤, SQLite의 오프라인 `wal_checkpoint(TRUNCATE)`를 실행했다. Crypto collector는 이미 pause 상태였고 강제 재개하지 않았다.
- 원격 checkpoint 프로세스는 16분 이상 Linux uninterruptible I/O 대기(`D`, `folio_wait_bit_common`)에 머물렀다. `/dev/vdb`는 100% utilization, host I/O wait는 한 구간에서 93.97%, write await 약 167ms였다. 프로세스 I/O 카운터는 최소 4분 이상 정체했고 main DB/WAL 크기 역시 변하지 않았다. 사용 가능 공간은 약 4.3GiB였다.
- SQLite main DB는 약 1.44GB, WAL 약 1.24GB 상태다. Checkpoint가 성공 응답을 반환하거나 WAL truncation을 확인하기 전에는 해당 DB를 다시 여는 storage writer를 기동하지 않았다. 현재 서비스 중단은 crypto-storage/maintenance에 한정하고 NATS 및 Market 서비스는 유지한다.
- 무진행 상태가 이어져 checkpoint PID에 `SIGTERM`을 보냈으나, 커널의 uninterruptible block-device 대기(`D`, `blk_mq_get_tag`)로 종료 신호도 아직 처리되지 않았다. 프로세스 I/O 카운터는 계속 동일하며 crypto-storage/maintenance는 정지, 세 crypto collector는 unhealthy/paused다. 이 장애 상태는 해소된 것으로 간주하지 않는다.

### 판정

- **Release 2 미완료.** C1 checkpoint조차 volume I/O에서 진행하지 않아 성공·회복 여부를 판정할 수 없다. DB schema/index/prune 변경, DB 복사·재구축, R2-D commonization은 수행하지 않았다.
- 현 증거는 메모리보다 20GB volume의 I/O 처리량·지연이 즉시 병목임을 지지한다. checkpoint 자체가 대기 중이므로 SQLite 원장 구조 개선이 병목을 얼마나 줄일지는 아직 측정되지 않았다.
- 다음 안전 단계는 checkpoint 프로세스가 커널 I/O 대기에서 풀려 종료될 때까지 기다린 뒤 WAL/DB 상태와 free space를 검증하는 것이다. 프로세스가 살아 있는 동안 writer를 재기동하거나 schema migration을 겹치지 않는다. 이후에는 crypto storage를 먼저 기동하고 NATS/ACK 상태를 확인한 다음, disk hysteresis와 fresh capacity probe가 허용하는 경우에만 collector가 자동 재개하도록 둔다.

