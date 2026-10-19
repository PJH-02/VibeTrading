# Release 1 보완 — maintenance I/O 양보 누락

## 범위와 원인

2026-09-29 운영 서버 `49.50.141.106`의 maintenance I/O 양보 누락만 보완했다.
기존 구현은 `_reclaim_verified()`를 pressure 검사 전과 검사 결과에 관계없이 호출했고,
마지막 `delivery_ledger_stats()`에서 delivery 전체 인덱스를 읽는 `COUNT(*)`를 실행했다.
주식 DB에서 정리 대상 조회의 manifest 전체 스캔과 임시 정렬도 확인됐다.

maintenance끼리의 mutex만으로는 NATS/저장기와 공유하는 `/dev/vdb`의 경합을 막지 못한다.
이는 실제 I/O 병목의 기여 요인이다. 볼륨의 상품별 IOPS 한도나 최적화 후 필요한 IOPS는 이 시험으로 확정하지 않는다.

## 변경 파일과 동작

| 파일 | 변경 이유 / 동작 |
|---|---|
| `vibetrading/archive.py` | DB를 지연 생성한다. pressure 판정은 DB를 열지 않는다. busy이면 전체 maintenance를 연기한다. 정리·checkpoint·업로드 대상 조회·개별 업로드·backup·orphan·prune 사이에 재판정한다. 주기적 원장 `COUNT(*)`를 제거한다. 새 업로드가 없으면 두 번째 정리 조회를 생략한다. |
| `vibetrading/sqlite_store.py` | maintenance SELECT 전용 progress handler를 추가한다. 0.25초 간격으로 pressure를 재확인하고 최대 3초 읽기 예산을 적용한다. 압력이 상승하면 SELECT를 중단하고 handler를 해제한다. DB schema와 저장/ACK 경로는 변경하지 않는다. |
| `vibetrading/runtime.py` | maintenance의 DB 생성·사용·종료를 worker thread 안에서 수행한다. defer된 cycle은 빠른 적체 재시도 대신 기존 5분 대기 경로를 사용한다. compaction job 사이에도 SQLite 없는 pressure 검사를 수행한다. |
| `tests/test_maintenance_yield.py` | Market/Crypto와 force 양쪽에서 DB 미접속 양보, 중간 압력 상승, 통계 스캔 제거, 읽기 중단 및 이후 정상 commit을 검증한다. |
| `tests/test_storage_pipeline.py` | 기존 파일 삭제/원격 보관 안전성 시험에 quiet pressure fixture를 명시한다. |
| `scripts/verify_maintenance_yield_e2e.py` | 독립 NATS에서 실제 파일/원장 확정 후 ACK와 양쪽 domain의 수집 결과를 검증한다. |
| `scripts/measure_maintenance_io.py` | SQLite 조회 없이 cgroup I/O, diskstats, NATS monitoring, heartbeat를 짧게 수집한다. |

읽기 중단은 SQLite VM callback 시점에 적용된다. 이미 kernel에 제출된 디스크 읽기를 즉시 취소하는 장치는 아니다.
삭제를 시작한 chunk의 lease/manifest bookkeeping은 완료 후 양보한다.
파일 하나의 원격 업로드와 이미 시작한 DB 쓰기 transaction을 임의로 중단하지 않는다.

## 회귀 및 실제 NATS 시험

- 관련 단위/회귀: **41 passed**.
- 전체 로컬 회귀: **90 passed, 2 deselected**. 제외된 2개는 기존 외부 실데이터 시험이다.
- Python compile 검사 통과.
- 운영 NATS와 분리된 내부 Docker network 및 임시 NATS를 사용했다. 운영 키·데이터 볼륨을 연결하지 않았다.
- Market 200건, Crypto 200건: Parquet의 event ID와 입력이 일치하고 중복 행이 없었다.
- 각 ACK 직전 파일 존재 및 같은 stream/generation/sequence의 manifest/delivery 확정을 검사했다.
- 양쪽 consumer 모두 `num_pending=0`, `num_ack_pending=0`이었다.
- 압력 상태에서 maintenance는 DB를 열지 않고 양보했고 저장/ACK는 완료됐다.
- 임시 시험 container/network는 제거했다.

이는 400건 회귀 시험의 결과이며 운영 전체 이력의 무손실 증명이나 피크 2배 부하 시험을 뜻하지 않는다.

## 배포

- 디렉터리: `/opt/vibetrading/releases/r1-maintenance-yield-20260929`
- build revision: `r1-253f3e1445d38a935d22`
- build config hash: `6b9c1ad6e9797048c90977f4ef60848ef173eb6e06eed6d5d8c28abf9a88f4d7`
- app image: `vibetrading-app:r1-253f3e1445d38a935d22`
- app ID: `sha256:241ae2ea20503c0a6d398f286063d404633062b1bdae0fe0cb049a88505e8ac9`
- crypto image: `vibetrading-crypto:r1-2392fc1e53e9a750c54e`
- crypto ID: `sha256:a335bfd9664eb299d08e1d4859f18395927d71186099dbc5629b743093345caa`
- 대상: **maintenance, crypto-maintenance 두 서비스만**.
- 시작 시각: **2026-09-29 01:59:41 KST**.
- 두 container의 image ID, image label, runtime provenance와 실제 3개 Python 파일 SHA-256 일치 확인.
- 수집기·저장기·NATS·Telegram은 기존 이미지로 유지했다. 두 maintenance만 새 manifest로 검증한다.

초기 중지/빌드 시도는 승인 검토 사용량 한도로 실행되지 않았고, 사용자 재개 요청 후 수행했다.

## 운영 측정

| 시각(KST) | 조건 | 디스크 가동률 | 평균 쓰기 대기 | 비고 |
|---|---|---:|---:|---|
| 01:16:51 | 적용 전 10초 표본 | 100.01% | 983.54ms | diskstats read 약 50/s, write 약 52/s |
| 01:56:34 | maintenance 중지 후 10초 표본 | 76.69% | 13.87ms | 기존 저장 적체 처리 중 |
| 01:59:52 | 새 maintenance 시작 직후 | 0.14% | 0.25ms | 수집기 재개 대기 포함 |
| 02:01:01 | 초기 복구 중 20초 표본 | 0.14% | 0.35ms | CRYPTO_DATA 적체 0, 그러나 최근 발행도 멈춘 구간이어서 정상 처리량 증거로 사용하지 않음 |
| 02:02:39 | 실제 재발행/저장 구간 약 30초 | 78.78% | 413.80ms | maintenance 읽기 0, crypto-storage cgroup write 약 153/s |
| 02:03:10 | 수집 정체 및 ACK 처리 구간 약 30초 | 38.58% | 739.00ms | maintenance 읽기 0, CRYPTO_DATA 마지막 발행 시각 정체 |
| 02:05:14 | 다음 maintenance cycle 포함 약 30초 | 25.67% | 59.90ms | 주식 maintenance는 pressure 상승 시 orphan 전 양보, crypto maintenance는 시작 연기 |

적용 전후에는 시간 차와 유입량 차이가 있다. 위 표만으로 동일 부하 성능 개선 배수나 처리 용량을 확정하지 않는다.
`diskstats`의 완료 I/O 수와 cgroup별 I/O 수는 집계 위치가 달라 합계가 정확히 일치하지 않을 수 있다.

## 판정과 남은 문제

**maintenance 양보 누락 수정은 배포·검증했지만, 전체 수집 정상화 기준은 통과하지 못했다.**

1. busy인 동안 양쪽 maintenance의 읽기 I/O가 0으로 내려갔다. heartbeat의 소량 쓰기만 남았다.
2. 다음 cycle에서는 주식 maintenance가 quiet일 때 시작한 뒤 I/O PSI full avg10이 5.08로 상승하자 `orphan_cleanup` 전에 양보했다. 원장 통계는 `measured=false`로 기록됐다. 코인 maintenance는 pending 2,558 / PSI 5.55 조건에서 DB 작업을 시작하지 않았다.
3. 그럼에도 maintenance 읽기가 0인 구간에 crypto-storage의 쓰기 부하와 수백 ms의 디스크 대기가 발생했다. NATS 로그에는 **02:02:24 KST STREAM.INFO 7.48~8.10초 지연**이 새로 남았다. `nats_publish_timeout`과 pause가 재발했다.
4. 데이터가 다시 들어오는 구간에서 CRYPTO_DATA 마지막 sequence는 6,794,044에서 6,819,100으로 증가했고, storage ACK 누적은 316,798에서 335,758까지 증가했다. 이는 실제 재개/저장을 보여주지만 지속 정상 발행의 증거는 아니다.
5. 마지막 기록 **02:05:34 KST**: maintenance 두 서비스는 healthy, Binance paused(`nats_publish_timeout`), Bitget/Bybit recovering. 전체 수집기를 정상으로 보고하지 않는다.
6. 이번 짧은 관찰에서 새 원격 업로드 완료는 확인하지 못했다. pressure 때문에 archive가 계속 연기되면 로컬 데이터가 쌓이므로 장기 운영 안정성도 아직 통과로 판정할 수 없다.

다음 병목은 **crypto-storage의 flush/SQLite 쓰기 집중과 공유 볼륨 응답 지연**이다.
현재 측정만으로 해당 쓰기 중 WAL, checkpoint, Parquet 각각의 기여도 또는 볼륨의 실제 보장 IOPS를 분리할 수는 없다.
따라서 서버 스펙 부족으로 단정하거나 임의의 최소 IOPS 수치를 제시하지 않는다.
후속 수정 전에는 flush 단계별 시간·WAL 증가·블록 쓰기량을 같은 시간축으로 측정하고,
볼륨의 실제 종류/기본 성능/버스트 조건을 대조해야 한다.

이번에는 수집기·저장기 재시작, DB migration, 원장 삭제, NATS 설정 변경, 볼륨 증설을 수행하지 않았다.
48시간 관찰도 수행하지 않았다. Release 2/3의 schema, Bloom filter, ACK floor, trading 기능은 수정하지 않았다.

## 적용 재현 및 롤백

현재 배포는 maintenance만 새 릴리스이며 다른 서비스는 이전 릴리스이다. 전체 서비스를 새 manifest로 일괄 검증하거나 재생성하지 않는다.

```bash
cd /opt/vibetrading/releases/r1-maintenance-yield-20260929
docker compose --env-file /etc/vibetrading/compose.env --env-file deploy/release.env \
  -f /root/vibetrading/docker-compose.yml \
  -f /root/vibetrading/deploy/crypto-profile.yml \
  -f deploy/release1-overlay.yml --profile crypto \
  up -d --no-deps --no-build maintenance crypto-maintenance
```

기존 이미지와 `/opt/vibetrading/releases/r1-a8cf71c54194134d4bb1`를 보존했다.
롤백이 필요하면 위 명령의 작업 디렉터리를 기존 릴리스로 바꾸어 두 서비스만 재생성한다.
단, 이전 버전은 확인된 maintenance 읽기 I/O 우회 문제가 있으므로 이번 관찰에서 롤백하지 않았다.

측정 원본과 독립 NATS 결과는 `docs/evidence/r1-maintenance-20260929/`에 보관한다.
