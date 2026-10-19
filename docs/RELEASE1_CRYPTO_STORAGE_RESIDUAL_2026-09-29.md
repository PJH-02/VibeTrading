# Release 1 잔여 코인 저장 병목 — 2026-09-29

## 확인한 원인

운영 수집 중 `crypto-storage`가 `/dev/vdb`의 쓰기 한도를 포화시켰다. 30초 표본에서 블록 장치 쓰기는 약 102 IOPS, 가동률 99.87%, 평균 쓰기 대기 844.64ms였고, I/O PSI `full avg10`은 77.74%였다. 같은 구간 crypto-storage cgroup은 초당 약 108회의 쓰기를 냈으며, NATS `STREAM.INFO` 지연 경고도 발생했다.

서버 식별값은 `c2-g3a;HIGH_CPU`, 2 vCPU / 4GB RAM이고 `/dev/vdb`는 `/srv/vibetrading-data`에 마운트된 20GB 추가 볼륨이다. g3에서 FB1/FB2의 최소 용량은 100GB이므로 이 20GB 볼륨은 용량상 CB1/CB2다. Ncloud 공식 사양상 CB1/CB2는 10–30GB에서 기본 100 IOPS를 제공한다. 따라서 약 100 IOPS에서 포화된 관측값은 실제 볼륨의 기본 한도와 일치한다. 서버 인스턴스 성능 표에는 2 vCPU / 4GB High CPU g3의 IOPS QoS가 5,250으로 제시되어 있어, 서버 CPU/IO QoS가 아니라 볼륨의 기본 IOPS가 직접 한계다. [Ncloud g3 서버·스토리지 사양](https://guide.ncloud-docs.com/docs/server-spec-vpc)

CPU와 메모리는 주된 한계로 보이지 않았다. 포화 표본 전후 `crypto-storage` cgroup 메모리는 약 409–421MiB / 768MiB였고, CPU idle은 60% 이상인 순간 표본이 있었다. SQLite DB는 약 1.01GB, WAL은 약 33.6MB였다. 따라서 현 단계에서는 서버 메모리 부족보다 데이터 볼륨의 작은 쓰기 I/O 한도가 직접 병목이라는 근거가 더 강하다.

## 이번 변경

| 파일 | 변경 |
|---|---|
| `vibetrading/sqlite_store.py` | 한 flush에서 생성한 여러 Parquet manifest와 delivery 행을 기존 schema 안에서 하나의 SQLite `FULL` transaction으로 확정한다. |
| `vibetrading/parquet_store.py` | flush에 포함된 Parquet 파일들을 모두 fsync·rename한 뒤 디렉터리 fsync를 하고, 원장 transaction 한 번으로 등록한다. manifest/delivery 확정 전 ACK하지 않는다. |
| `vibetrading/archive.py` | 원격 업로드·검증·로컬 공간 회수는 storage heartbeat와 I/O pressure만으로 판정한다. pending 제한은 원장 prune/compaction에만 적용한다. pressure가 다시 높아지면 현재 업로드 단위 뒤에서 양보한다. |
| `vibetrading/storage_worker.py` | crypto 저장기의 상시 RSS가 설정된 400MiB를 이미 넘는 문제를 반영했다. cgroup 한도를 읽을 수 있을 때 crypto RSS flush/pause 경계는 각각 최소 75%/90%로 올리고, 실제 cgroup working set 압력은 별도 75%/90% guard가 처리한다. cgroup 정보를 못 읽을 때는 기존 RSS 기준을 사용한다. Market 경로에는 적용하지 않는다. |
| `tests/test_storage_pipeline.py` 등 | flush 단위 원자성, pending이 많을 때 archive 허용, crypto cgroup RSS 경계 테스트를 보강했다. |

기존 저장 순서 `NATS → Parquet durable write → manifest/delivery transaction → NATS ACK`와 SQLite schema를 유지했다. ACK floor, delivery alias, Bloom filter, live trading fan-out은 변경하지 않았다.

## 검증과 배포

- Storage/maintenance 회귀: **39 passed**.
- 전체 로컬 회귀: **95 passed, 2 deselected**.
- 새 이미지에서 격리 NATS E2E: Market 200건, Crypto 200건이 모두 Parquet에 저장됐다. 400건 각각 ACK 직전 원장 확정을 확인했고, 각 durable consumer의 대기 건수는 0이었다.
- 새 crypto 이미지: build revision `r1-05f511e42bd1a6b267c6`, config hash `6b9c1ad6e9797048c90977f4ef60848ef173eb6e06eed6d5d8c28abf9a88f4d7`, image ID `sha256:49c026fe70b8dd61be962a0d145b7fd16a0c096e6c84474a3238ae98bb363809`.
- 운영에는 `crypto-storage`만 새 이미지로 교체했다. 저장기 재시작 횟수 0, 새 이미지로 healthy였다. Binance·Bitget·Bybit collector 프로세스는 재시작하지 않았다. 재기동 직후 Bitget/Bybit가 잠깐 pause됐지만 후속 로그에서 세 거래소 모두 pressure recovery를 기록했고 컨테이너 health는 healthy였다.
- 새 maintenance는 pending이 prune 한도를 넘은 상태에서도 원격 파일 142개를 업로드하고 원격 검증된 로컬 파일 32개(약 4.5MB)를 회수했다. 이후 I/O pressure가 오르면 업로드 중간에서 양보했다. pending 수 때문에 archive 전체가 굶던 현상은 제거됐다.

## 운영 결과와 판정

RSS 기준 변경 직후 30초 표본에서는 crypto-storage 쓰기가 약 1.2 IOPS였지만 입력/flush 주기가 초기 구간이어서 성능 개선 증거로 보지 않는다. 실데이터 유입이 쌓인 후에는 한 표본에서 다시 장치 가동률 99.87%, 쓰기 대기 844.64ms, I/O PSI full avg10 77.74%, crypto-storage 쓰기 약 108 IOPS가 관측됐다. 해당 부하는 pending이 8,192건까지 증가한 시점이었다. 따라서 cgroup/RSS 조기 flush와 flush transaction 중복은 줄였지만 **실시간 처리량에서 볼륨 포화와 `storage_or_nats_backpressure` 재발 가능성은 아직 해결되지 않았다.**

NATS 지연 경고는 새 storage 배포 직후 포화 표본에서 발생했으며, 이후 약 2분간은 추가 경고가 없었다. 이 짧은 표본만으로 반복 오류 제거를 판정하지 않는다. 48시간 관찰은 하지 않았다.

마지막 짧은 상태 확인에서는 저장기 `messages=61,093`, `acked=55,771`, 메모리 pending 5,322건, 버퍼 16.2MiB, RSS 약 406MiB / cgroup working set 약 394MiB였다. maintenance는 낮은 압력 구간에서 파일 2개를 업로드한 뒤 I/O PSI가 5% 기준을 넘어 다음 작업을 미뤘다. Binance/Bitget은 `recovering`, Bybit은 `publishing`이었고 세 컨테이너 health는 healthy, 마지막 2분 NATS 지연 경고는 0건이었다. 이 상태는 수집·ACK의 장기 안정성을 보장하지 않는다.

### 후속 정정 및 사용자 방향 (2026-09-29)

사용자는 20GB 볼륨 확장 대신 기존 자원에서 개선하도록 요청했다. 볼륨 확장과 snapshot 생성은 실행하지 않았다.

앞선 70GB = 220 IOPS 계산은 오류다. 공식 상세식은 max(100, 용량GB × 3)이므로 70GB는 210 IOPS다. 또한 포화 상태에서 완료된 102 IOPS를 입력 부하의 피크로 간주하여 2배 용량을 산정할 수 없다. 콘솔의 실제 볼륨 SKU·버스트 잔량은 확인하지 못했으므로, 위의 볼륨 기본 한도는 관측과 부합하는 가설이며 확정된 원인으로 단정하지 않는다.

후속 확인에서는 로컬 코인 Parquet가 약 48MiB, 데이터 볼륨 여유가 약 7.6GiB였다. 120초 syscall 표본에서 SQLite 본체 fdatasync 대기 55.9초, WAL fdatasync 0.68초, Parquet 등의 fsync 0.29초였다. checkpoint 작은 쓰기를 줄이는 연결 설정과 장애 원장 잠금 수정을 먼저 비교한다. 상세 결과는 후속 no-expansion 보고서에 기록한다.

운영 측정 원본과 이미지 manifest는 `docs/evidence/r1-crypto-storage-20260929/`에 보관했다. API 키·`.env` 값은 수집하거나 보고서에 넣지 않았다.
