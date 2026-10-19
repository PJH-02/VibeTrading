# 코인 선물 수집 병목 조치 (2026-09-28 KST)

## 영향과 원인

- Binance·Bitget·Bybit의 BTCUSDT, ETHUSDT, SOLUSDT 수집이 2026-09-27 17:38경부터 2026-09-28 00:42경까지 중단됐다. `collection_gaps.sqlite3`에 기록된 구간이다. 해당 WebSocket 원본은 소급 수집되지 않는다.
- 세 수집기가 공유하는 디스크는 한때 80% 중지 기준을 넘었다. 여유가 돌아온 뒤에도 재개 기준이 사용률 65% 미만이어서, 당시 약 68%인 상태에서 중지 래치가 풀리지 않았다. NATS 코인 스트림은 비어 있었고 저장 consumer 적체도 없었다.
- 보관 DB에는 원격 검증 후 로컬에 남은 Parquet 파일 약 39,070개(1.2GB)가 있었다. 삭제 조회의 재귀 관계에 인덱스가 없어 CPU를 장시간 사용했고, 한 차례 512개 업로드가 끝나야 삭제를 시작했다. 삭제도 파일마다 여러 SQLite 트랜잭션을 만들었다.
- 저장 worker는 clean `active_file` 캐시를 메모리 압박으로 계산해 flush와 재시작을 반복했다. `memory.events`에서 OOM은 없었고 프로세스 RSS는 한도보다 낮았다.
- 약 1GB의 SQLite 원장 백업은 업로드 뒤 원격 본문을 두 번 다운로드하여 해시 검증했다. 00:58 백업 직후 세 수집기의 발행이 멎었고, 01:01:58 NATS timeout으로 재연결했다. 당시 watchdog은 180초 뒤에야 감지하여 약 3분의 무발행 구간을 누락 원장에 제대로 반영하지 못했다.

## 변경

- 코인 디스크 재개 기준을 75% 미만과 여유 6GiB 이상으로 변경했다. 80% 중지와 60초 안정화 조건은 유지한다.
- 보관 DB에 `superseded_by` 인덱스를 만들고, 삭제 조회가 이 인덱스를 사용하도록 지정했다. 실서버에서 삭제 조회 64건이 약 1초에 끝나는 것을 확인했다.
- 원격 검증을 마친 파일을 업로드 전에 우선 회수하고 업로드 중에도 나누어 회수한다. 적체가 있으면 보관 주기를 30초로 단축한다. 파일 회수는 128개씩 lease와 완료 상태를 묶음 트랜잭션으로 기록한다.
- SQLite WAL이 256MiB 이상이면 보관 worker가 비차단 체크포인트를 시도한다. 실제로 수동 `TRUNCATE` 체크포인트 후 WAL 0바이트와 디스크 여유 회복을 확인했다.
- 저장 worker의 cgroup 압박 계산에서 clean 파일 캐시를 제외하고 dirty/writeback/shared 메모리는 계속 포함한다. RSS 상한과 강제 flush는 유지한다.
- 원격 업로드의 중복 전체 검증을 제거했다. 각 `put_file`/`put_bytes`는 내부에서 본문 SHA-256을 검증한다. 대용량 업로드는 단일 전송으로 수행하고 보관 프로세스는 낮은 CPU·I/O 우선순위로 실행한다.
- 코인 watchdog의 무발행 감지 시간을 180초에서 60초로 줄이고, 감지 시 마지막 정상 발행 시각부터 누락 구간을 기록한다.

## 단기 검증과 범위

- 관련 테스트 32개 통과. 서버에서 3개 거래소의 WebSocket 발행, 3개 종목의 갱신, 코인 저장 ACK, NATS 적체 감소, 보관 worker의 업로드·로컬 회수를 확인했다.
- 01:11 KST 기준 디스크 사용률 64%, 여유 6.8GiB. 최근 보관 cycle은 512개 업로드와 4,224개 파일 회수(약 81MB)를 완료했다. NATS와 세 코인 서비스는 정상 동작 중이다.
- 새 백업 경로는 다음 정기 백업 시 검증된다. 요청에 따라 48시간 상시 관찰은 진행하지 않았다. 기존 용량 JSONL 및 Docker 로그로 이후 평가한다.

## 배포

- 코인 수집: `vibetrading-crypto:20260928-backpressure7`
- 코인 저장: `vibetrading-crypto:20260928-backpressure5`
- 코인 보관: `vibetrading-crypto:20260928-backpressure6`
- 국내·미국 수집 이미지는 변경하지 않았다.
- 서버의 기존 소스 트리는 배포 중인 코인 이미지보다 오래됐다. 이번 패치의 재빌드 입력은 `/root/vibetrading/.server_patch/crypto-backpressure-20260928/overlay`에 보존했고, Compose는 위 이미지 태그를 명시적으로 사용한다. 향후 전체 소스 배포를 할 때 이 패치를 통합해야 한다.

## 압축 delivery 원장과 분리 복구 (2차 조치)

- 코인 원본을 SQLite에 중복 저장하지 않는다. NATS JetStream을 단기 버퍼로, Parquet를 원본 저장소로 유지한다.
- `parquet_delivery_compact`는 반복 문자열 대신 stream 정수 ID와 SHA-256 BLOB event key를 저장한다. Parquet manifest와 delivery 확정은 같은 SQLite 트랜잭션에서 수행한다.
- 같은 NATS stream 세대의 ACK 바닥값 이하이고, 원격 Parquet 검증이 끝났으며, 기록 후 24시간이 지난 delivery 행만 낮은 I/O 구간에 최대 2,000건씩 삭제한다. 주문·체결 감사 원장은 이 정책의 대상이 아니다.
- 정상 최초 delivery는 메모리 상태로 판별하고, NATS 재전달 또는 재시작 경계에서만 SQLite 중복 조회를 배치로 수행한다.
- Parquet flush는 크기와 최대 대기 시간을 함께 사용한다. 압박 시 모든 파티션을 동시에 flush하지 않고 큰 파티션부터 확정하며, 파일과 SQLite 확정 전에 NATS ACK를 보내지 않는다.
- Binance, Bitget, Bybit 수집기는 별도 프로세스로 실행한다. NATS/디스크 장애 시 전체 코인 수집을 멈추고, 60초 동안 발행 정상과 적체 감소를 확인한 뒤 거래소별로 복구한다.
- publisher 종료에는 시간 제한을 두며 실패 시 미전송 건수와 바이트를 `collection_gaps.sqlite3`에 기록한다. Telegram 복구 알림도 안정화 확인 후 보낸다.

### 2026-09-28 실서버 이전 결과

- 수집기와 코인 저장기를 중단한 상태에서 NATS `CRYPTO_DATA` stream의 ACK 바닥값 `5,241,849`를 캡처했다. 미처리 28,499건은 NATS에 유지했다.
- 기존 delivery 5,242,624건을 압축 원장으로 이전했다. 전체 SQLite 무결성 검사를 통과한 뒤 원자적으로 교체했다.
- SQLite 파일은 1,763,291,136바이트에서 800,878,592바이트로 줄었다.
- 기존 DB는 `/srv/vibetrading-data/runtime_data/crypto_state/vibetrading.sqlite3.precompact-20260928T021539Z`로 보존했다.
- 마지막 등록 원격 백업은 `vibetrading/crypto/generations/2aa51333e40a4b4891448842abf9ade4/state/vibetrading-1790555080246970213-e8cf5ba20c134fd3b2fb9207b06b6a0b.sqlite3`, SHA-256 `4ee5af4023aa7e96235b6b4ed296d060f4ed7036e74937425c7eb522f69863a4`다.
- 소스 전체 테스트는 68개 통과, 2개 선택 제외다.

### 재기동 중 확인한 추가 병목과 수정

- 고유량 상태에서 큰 파티션 flush가 계속 실행되면 전역 flush 시각이 갱신돼 저유량 funding/OI 파티션의 최대 대기시간 flush가 밀릴 수 있었다. 크기 압박과 독립적으로 aged 파티션을 최대 5초 간격으로 검사한다.
- Arrow allocator가 해제한 메모리를 RSS에 유지하면 400MiB soft threshold가 매 NATS 배치마다 작은 Parquet 파일을 만들었다. RSS 기준 flush는 실제 미확정 버퍼가 8MiB 이상일 때만 동작하도록 히스테리시스를 추가했다. 적용 전 약 3분 동안 190회 flush·232개 파일이 생성됐지만, 적용 직후 같은 세 거래소 부하에서 38초 동안 1회 flush·2개 파일로 줄었다.
- 연속 ACK 상태를 메모리 집합만으로 계산하면 재시작 때 out-of-order ACK 정보가 사라져 SQLite ACK floor가 고정됐다. 각 파일 확정 후 JetStream durable consumer의 실제 ACK floor를 조회해 같은 stream generation의 `delivery_stream`에 기록한다. 실서버에서 SQLite floor가 5,241,849에서 NATS의 5,303,608로 갱신되는 것을 확인했다.
- 오프라인 이전 파일은 원본 DB의 UID, GID, mode를 유지한다. 최초 실서버 이전에서 확인된 root 소유권 문제는 원본 UID/GID 10001:10001로 복구했다.
- 새 코인 이미지는 최신 CLI가 참조하는 `providers/ls_market.py`도 포함한다. 이 모듈은 entitlement가 확인되지 않은 LS 수집을 활성화하지 않는다.

### 적용 후 확인

- Binance, Bitget, Bybit를 별도 컨테이너로 순차 기동했다. 세 거래소 모두 BTCUSDT, ETHUSDT, SOLUSDT의 최신 이벤트를 발행하며 `phase=publishing`이다.
- 초기 NATS 적체 28,499건을 전부 Parquet로 확정했고 consumer pending과 ack pending이 0이 되는 것을 확인한 뒤 수집기를 기동했다.
- maintenance는 한 cycle에서 244개 파일을 원격 검증 업로드하고 동일한 244개·18,262,730바이트를 로컬에서 회수했다. 저장 적체가 높은 동안 delivery prune은 `storage_or_io_busy`로 보류됐다.
- Telegram compact `/status`는 서버 메모리·디스크, 코인 저장·보관, 거래소별 실제 세 종목만 표시한다. 분리된 heartbeat를 합성하도록 `health.py`를 Telegram 컨테이너에 읽기 전용 mount했다.
- 실측 합산 유입률 초기값은 266.21건/초, 608,216.95바이트/초다. 48시간 capacity logger는 `/srv/vibetrading-data/runtime_data/reports/crypto_capacity_20260928.jsonl`에 WAL, PSI, vdb, NATS, 컨테이너와 분리 수집기 지표를 60초마다 기록한다.
- 24시간 만료 삭제가 유입률을 따라가도록 한 cycle을 8,192행으로 제한하고, batch가 가득 차면 maintenance가 5분 대신 30초 뒤에 다시 실행된다. 삭제는 storage pending 2,048건 이하와 I/O full avg10 5% 이하 조건을 모두 충족해야 시작한다.
- 2배 writer replay는 실측 266.21건/초의 2배인 532.42건/초로 시도했다. 약 3분대에 I/O full avg10 64.38%, 데이터 볼륨 사용률 80%, 여유 3.8GiB로 안전 기준을 위반해 즉시 중지했다. replay 종료 후 68%·6.1GiB로 회복됐고 운영 NATS 적체는 당시 1,829건이었다. 현재 20GB 볼륨은 2배 검증을 통과하지 못했으므로 측정한 IOPS에 여유를 둔 볼륨 증설 후 동일 시험을 반복해야 한다.
- 최종 전체 테스트는 70개 통과, 2개 선택 제외다.

### 2026-09-28 재조사에 따른 해석 정정

- 위 2배 writer replay 중단은 코인 자체의 지속 처리 한계나 볼륨 증설 필요성을 확정하지 못한다. 16:26:26에 일반 시장 maintenance의 전체 SQLite 백업이 시작됐으며, 디스크 급감과 해당 컨테이너의 I/O 급증이 겹쳤다. 일반 시장 maintenance에는 시간당 백업이 남아 있었다.
- replay는 NATS 발행·consumer·delivery 원장을 포함한 전체 경로 시험이 아니다. 당시 결과는 안전 기준 위반으로 중단된 미완료 시험으로 해석한다.
- 신규 delivery-key 조회는 줄었지만 신규 event_id의 SQLite 중복 조회는 남아 있다. 하루 1회 백업 설정도 코인 역할에만 적용됐다.
- 최신 평가, 확인된 사실과 미확정 원인, 수정된 개선 순서는 [재검토 문서](CRYPTO_BACKPRESSURE_REVIEW_2026-09-28.md)를 따른다. 이번 재조사에서는 코드와 서버 설정을 변경하지 않았다.
