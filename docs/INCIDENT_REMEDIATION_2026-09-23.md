# 2026-09-23 수집 중단 및 오류 해결안

## 확인된 상태 (2026-09-23 15:01~15:04 KST, 운영 서버 읽기 전용 점검)

- 수집 중단 예외 `CollectionPaused`는 적어도 9월 22일 09:00 KST부터 반복됐다. 9월 23일 14:40:04 KST에는 Toss의 `universe_manifest` `INSERT OR IGNORE`가 `database is locked`로 실패했고, 약 0.12초 뒤 Binance의 283초 무발행 감시 오류가 기록됐다.
- 데이터 디스크는 20 GiB 중 약 62% 사용, 여유 공간 약 7.1 GiB다. NATS JetStream 전체 저장량은 약 127 KiB로 현재 포화 상태가 아니다. 저장 작업은 헬스 파일상 대기 0건, ACK 1,399,154건이다.
- 수집 압박 래치는 디스크 사용률 80% 이상, 여유 공간 4 GiB 이하, 또는 NATS 스트림 사용률 80% 이상에서 켜진다. 재개에는 **디스크 사용률 50% 미만**, 여유 공간 6 GiB 이상, NATS 50% 미만이 60초 지속돼야 한다. 현재 디스크 62% 때문에 NATS가 비어 있어도 자동 재개할 수 없다.
- Docker 빌드 캐시 4.264 GB가 회수 가능으로 표시된다. SQLite 상태 디렉터리는 약 3.5 GB이며, 그중 과거 백업 임시 파일 약 1.2 GB와 진행 중인 백업 파일 약 0.9 GB가 있다. 운영 데이터와 백업 파일은 원격 검증 전 삭제하지 않는다.
- `collectors` 컨테이너는 `healthy`이지만 Toss 헬스 파일은 `paused`다. 현재 헬스 검사는 `state=running`과 하트비트 시각만 확인하여 실제 발행 중단을 정상으로 표시한다.

처음 압박 래치를 켠 원인(디스크, NATS 실제 포화, 또는 NATS 상태 조회 실패)은 당시 원인별 수치가 기록되지 않아 확정하지 못했다. 유지보수의 SQLite 백업이 관찰됐지만 그 작업이 쓰기 잠금을 보유했다는 증거도 아직 없다.

## 1. 즉시 복구 (코드 배포 전)

1. 수집기·저장기 컨테이너 상태와 NATS 대기량을 다시 기록한다. 실행 중인 Docker 빌드가 없음을 확인한다.
2. 사용하지 않는 Docker 빌드 캐시를 `docker builder prune --force`로 회수한다. `docker system df`에서 이 캐시 4.264 GB는 전량 reclaimable이고 빌드 작업은 관찰되지 않았다. 실행 중인 이미지와 컨테이너는 유지된다. 예상 디스크 사용률은 약 40~45%다.
3. `df -h /srv/vibetrading-data`에서 사용률 50% 미만 및 여유 6 GiB 이상을 확인한다. NATS 사용률 50% 미만이 60초 유지되면 래치가 자동 해제되는지 확인한다. 공간 확보 전에 수집기 재시작으로 래치를 우회하지 않는다.
4. Binance·Hyperliquid·Toss 헬스 파일의 `phase`가 `publishing`으로 바뀌고 `last_event_ts_ns`와 `published_count`가 증가하는지 확인한다. 저장기 ACK가 증가하고 대기량이 누적되지 않아야 한다. 30분 이상 재발을 감시한다.
5. 과거 SQLite 백업 임시 파일은 원격 백업의 최신성·무결성을 검증하고 현재 백업이 끝난 다음에만 정리 여부를 결정한다. SQLite 본체, WAL, Parquet, NATS 파일은 수동 삭제하지 않는다.

## 2. 코드 수정

### 압박 래치 및 관측

- 디스크 압박과 NATS 압박을 별도 원인으로 기록하고 별도 재개 조건으로 해제한다. 디스크는 80%/4 GiB에서 중단하고 65% 미만/6 GiB 이상이 60초 지속되면 재개하도록 제안한다. NATS는 80%에서 중단하고 50% 미만이 60초 지속되면 재개한다. 어느 쪽이든 현재 안전 조건은 충족해야 한다.
- 코드에 하드코딩된 수치를 `StorageGuardSettings`로 통합한다. 상태 변화마다 디스크 사용률·여유 바이트, NATS 바이트/상한, 상태 조회 성공 여부, 래치 원인과 경과 시간을 기록한다. 조회 실패는 실제 포화와 구분한다.
- `CollectionPaused`를 의도된 중단으로 처리해 KIS 참가자 흐름의 반복 ERROR 스택과 불필요한 재연결을 줄인다.

### SQLite와 이벤트 루프 분리

- `@universe`의 정적 종목 목록은 YAML에서 읽고, 수집기 재연결마다 `universe_manifest`에 쓰지 않는다. 감사용 목록 보존이 필요하면 변경된 해시만 별도 저장 작업에서 기록한다.
- 나머지 동기 SQLite 접근도 `asyncio.to_thread` 또는 전용 저장 작업으로 이동한다. 특히 수집기 생성과 유지보수 생성자에서 SQLite 연결이 이벤트 루프를 막지 않도록 한다. `RuntimeStore`의 120초 busy timeout과 `time.sleep` 재시도는 네트워크 수집 루프에서 실행되면 안 된다.
- SQLite 백업 작업의 시작/종료/소요 시간과 임시 파일 정리를 기록한다. 중단된 백업 파일은 원격 검증 상태와 실행 중인 백업 여부를 확인한 뒤 별도 청소 절차로 처리한다.

### 헬스 및 경고

- `collectors` 헬스는 프로세스 생존뿐 아니라 시장이 열려 있는 각 수집기의 `phase`, 최근 발행 시각, 압박 중단 여부를 반영한다. `paused`는 정상으로 표시하지 않는다.
- 이벤트 루프 지연과 실제 Binance 피드 무발행을 구분해 감시 경고에 넣는다. 같은 압박 사고에서 파생된 제공자별 경고는 하나의 원인 알림으로 묶고 복구 알림을 낸다.

## 3. 검증 및 배포 순서

1. SQLite에 다른 프로세스가 쓰기 잠금을 잡는 테스트에서 Toss 재연결이 이벤트 루프를 멈추지 않는지 확인한다. 동일 종목 목록 재연결 시 DB 쓰기가 없는지도 확인한다.
2. 디스크 80%→62%, NATS 80%→소량, NATS 상태 조회 실패/복구의 상태 전이를 시험한다. `paused`일 때 헬스 검사가 실패해야 한다.
3. 저장기 ACK와 원격 백업 무결성을 확인한 뒤, 수집기 이미지를 배포한다. 한 번에 전체 수집량을 무제한 재개하지 않고 NATS 사용률과 저장기 지연을 감시한다.
4. 배포 후 최소 30분 동안 `CollectionPaused`, SQLite 잠금, Binance 무발행, NATS 사용률, 디스크 사용률, 원격 업로드 지연을 확인한다. 재발 시 수집기 중단 상태를 유지하고 새 원인별 수치로 판별한다.

## 근거 코드

- `vibetrading/market_policy.py`: 중단/재개 래치와 하드코딩된 임계값.
- `vibetrading/cli.py`, `vibetrading/providers/universe.py`, `vibetrading/sqlite_store.py`: Toss 종목 목록 기록과 동기식 SQLite 잠금 대기.
- `vibetrading/runtime.py`: 모든 수집기가 공유하는 asyncio 루프와 Binance 무발행 감시.
- `vibetrading/health.py`: 생존 하트비트 중심의 헬스 판정.
- `vibetrading/archive.py`: SQLite 백업, 업로드 및 임시 파일 처리 순서.

## 4. 적용 결과 (2026-09-25 KST)

- 정적 `@universe` 종목 조회는 SQLite를 열거나 쓰지 않도록 변경했다. 따라서 Toss 재연결이 `universe_manifest` 쓰기 잠금에 대기하지 않는다.
- 디스크 압박 재개 기준은 사용률 50% 미만에서 65% 미만으로 조정했고, `paused` 수집기는 헬스 검사에서 실패로 표시한다.
- 유지보수의 `delivery_history` 정리는 기본 비활성화했다. 이 삭제는 SQLite 단일 writer 잠금을 오래 보유할 수 있으므로, 필요하면 `ARCHIVE_DELIVERY_HISTORY_PRUNE_ENABLED=true`를 설정한 별도 비업무 시간 작업에서만 수행한다.
- 수집기를 계속 중지시킨 직접 원인은 `vibetrading-os-disk-guard.timer`였다. 이 가드는 루트 파일시스템(`/`) 여유가 1 GiB 이하일 때 수집기를 중지한다. 데이터 디스크는 정상이어도 루트 디스크는 95% 사용, 0.54 GiB 여유였다.
- 이전 VS Code Server 4개, 참조 없는 이전 이미지, 로그와 APT 캐시를 정리해 루트 디스크를 60% 사용, 3.8 GiB 여유로 회복했다. 가드는 2 GiB 재개 기준을 충족해 수집기를 자동 재개했고 이후 `action=none`을 유지했다.
- 새 이미지에서 회귀 테스트 4건과 Python 컴파일 검증을 통과했다. 재개 후 수집기는 healthy 상태에서 Toss, Hyperliquid, Binance 이벤트를 발행했고, 저장 ACK는 331,800에서 333,849로 증가했다. 이 관찰 구간에는 `database is locked`, `CollectionPaused`, Binance 무발행 오류가 없었다.
