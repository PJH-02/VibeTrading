# 코인 저장 병목 최소 수정 — 2026-10-01

## 수정 범위

- 기존에 구현·검증한 immutable batch event index와 exact recovery를 운영에 적용한다. 이번에 별도 인덱스 구조를 설계하지 않았다.
- 기존 maintenance loop의 pending-only 양보 분리와 자원 회복 재확인 코드를 함께 적용한다. 새 scheduler/controller를 만들지 않았다.
- `delivery_recovery.py::prepare_document`에 작은 negative cache를 추가했다. 원격 canonical이 아직 미확정인 recovery block은 재조회 때 큰 payload를 다시 읽지 않는다. DB identity별 최대 64 block, block당 최대 1,024 manifest reference만 보관한다. 조건이 회복되면 실제 payload와 checksum을 다시 확인한다. cache는 업로드·ACK·삭제 권한을 부여하지 않는다.
- 새 회귀 2건: 연결을 재개해도 미확정 payload를 다시 읽지 않음; 원격 canonical 회복 후 손상된 payload를 업로드하지 않음.
- `run_r2_maintenance_regressions.py`에 release 경로 환경 변수만 추가해 기존 실제 NATS 시험을 재사용했다.

주식의 공통 batch writer/maintenance를 재사용하며, stream·consumer·SQLite·Parquet·process 분리를 유지한다. 원본 snapshot/delta 전량 보존, commit 전 ACK 금지, generation/remote 검증 조건, 24시간 delivery 정책은 유지한다. 실거래 기능을 추가하지 않았다.

## 검증

- 관련 회귀: 39 passed.
- 전체 Market/Crypto 회귀: **207 passed, 1 skipped, 2 deselected**. 제외 2건은 기존 live_data 시험이다.
- 새 이미지의 실제 격리 NATS: Market 원본 180건, Crypto 원본 180건/정확 delivery 216건, 재전달·재시작·복구·정리 후 replay 차단/복구 확인. pending/ack_pending=0.
- 실제 stream 재생성 시험: 새로운 generation의 floor가 옛 generation 삭제 권한이 되지 않음; 미확정 옛 20건 보존 후 독립 증명이 생긴 경우만 정리.
- 앞선 동등 자원 30분 시험의 2,132,012건 저장·ACK·원격 집합 대조 증거를 재사용한다. 새 cache/scheduler의 장기 부하 통과 증거로 확대 해석하지 않는다.

## 배포 이미지와 공간 확보

- release: `/opt/vibetrading/releases/crypto-minimal-fix-20261001`
- revision: `r2-17cb00452ee1e8ed6d99`
- image: `sha256:d2714b445da301e705786c7f24414313a9c8ebcdb606aec1b4b4b74cb7a7d8d6`
- config hash: `feaeeed381d8afc4d51fee22474be039d4d9b394076455441b9b14b839467314`
- 검증된 pristine 후보: 1,755,222,016 bytes, SHA-256 `02d3f0f96535ad5eef8bc3f4dc7000f538dd7ef2a813e45eff50b9d19098f016`, 기존 delivery 9,240,414건·manifest 72,665건 보존. 합성 trial DB를 운영 DB로 사용하지 않았다.
- 중단된 synthetic trial catalogue 사본 2,196,836,352 bytes만 정리했다. 운영 원본은 이때 유지했고 trial Parquet·NATS·audit 증거도 유지했다. 별도 trial NATS는 데이터 보존 상태로 종료했다.
- 기존 원장의 승인된 원격 전체 checksum 검증 receipt와 현재 object HEAD를 재확인했다. 전체 백업·다운로드 검증을 반복하지 않았다.
- 새 원장 전송·checksum 확인 후 기존 운영 DB/WAL을 rollback 디렉터리로 옮겨 보존하고 새 원장을 설치했다. cutover 직전 NATS의 미확정 200건은 유지했다.

## 운영 결과

운영 저장기는 2026-10-01 15:48:50 KST에 새 이미지로 기동했다. index 9,240,414건 복구 후 ready 상태에서 기존 200건을 수신했다. 전부 같은 NATS generation에서 ACK됐으며, 정확한 delivery 200건과 해당 canonical 파일의 전체 checksum을 확인했다. 검증된 원격 원본 backup과 로컬 분석 원본을 유지하면서 교체 전 서버 DB/WAL 사본을 회수해 약 2.68GB를 추가 확보했다.

기동 순서: Binance 15:51:18 → Bitget 15:52:29 → Bybit 15:53:49 KST. 16:01:41 KST 관측에서 세 collector 모두 publishing이고 각 거래소의 BTCUSDT/ETHUSDT/SOLUSDT 수신 시각이 갱신됐다. 최근 확정 Parquet 48개에서도 **9개 거래소·종목 조합 모두 trade/order_book/funding_rate/open_interest**를 확인했다.

| 항목 | 관측값 |
|---|---:|
| 코인 저장기 신규 처리/ACK 누적 | 113,494 / 107,147 (health 시각 기준) |
| NATS 잔여 메시지 | 3,416 (더 늦은 monitoring 시각 기준) |
| 코인 저장기 RSS / cgroup working set | 약 465MB / 422MB |
| host MemAvailable | 약 1.36GiB |
| 데이터 볼륨 여유 | 약 7.48GB |
| 코인 저장기 data volume write IOPS | 3.46 (30.06초 표본) |
| 전체 data volume read/write IOPS | 33.94 / 93.22 (동일 표본) |
| 전체 data volume 가동률 / 쓰기 대기 | 5.27% / 0.95ms |
| I/O PSI full avg10 | 0.74% (16:01:41 시각) |

health/monitoring 시각이 다르므로 두 pending 숫자의 차이를 데이터 유실로 해석하지 않는다. 일부 partition의 확정 순서 때문에 ACK floor 앞뒤에 ACK된 hole이 존재하며, num_deleted는 미기록 폐기 수가 아니다.

새 crypto 5개 container가 모두 healthy, restart=0이다. 재개 이후 container log에서 Traceback/nats_publish_timeout/storage_or_nats_backpressure가 0건이고, kernel cgroup oom_kill도 0건이다. image ID/build revision/config hash가 release와 일치하고 `/app` source bind mount가 없음을 확인했다. 기존 Market storage/maintenance/NATS PID는 유지됐으며 Market ACK도 계속 증가했다. 기존 Market 수집기의 Toss 인증 문제 해결은 이번 범위에 포함되지 않는다.

첫 maintenance cycle에서는 과거 local-only 파일 296개를 업로드한 뒤 I/O pressure 상승으로 양보했고, 이후 자원 회복 재확인을 통해 archive 작업이 재개됐다. 전체 과거 backlog 처리와 장기 원장 정리 수지의 완료 증거는 아직 없다. 현재 active archive heartbeat만으로 원장 정리 완료를 선언하지 않는다.

현재 수정·운영 재개와 단기 정상 수집은 확인됐지만, **Release 2 전체 부하/retention gate 완료를 선언하지 않는다.** 위 30초 표본은 최소 필요 IOPS나 24시간 처리량의 보장치도 아니다.

48시간 장기 관찰은 수행하지 않는다. 단기 수집 확인으로 장기 안정성이나 모든 오류의 영구 제거를 선언하지 않는다.

## 증거

- `docs/evidence/crypto-minimal-nats-regression-20261001.json`
- `docs/evidence/crypto-minimal-cutover-20261001.json`, `crypto-minimal-rollback-reclaim-20261001.json`
- `docs/evidence/crypto-minimal-status-20261001.json`, `crypto-minimal-io30-20261001.json`
- `docs/evidence/crypto-minimal-coverage-20261001.json`, `crypto-minimal-provenance-20261001.json`
- 기존 원격 backup: `docs/evidence/r2-current-remote-backup-20261001.json`
- 기존 처리량/무결성: `docs/evidence/r2-load-summary-20261001.json`, `r2-load-integrity-20261001.json`
- 서버 release의 `cutover.json`, `trial-catalogue-reclaim.json`, health/NATS 관측 파일.
