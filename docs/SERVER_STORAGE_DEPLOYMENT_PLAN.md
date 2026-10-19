# VibeTrading 장기 수집·저장·서버 배포 통합 수정 계획

- 문서 버전: 2026-09-08 / 감사 결과 반영 개정본
- 구현 기준: `E:\VibeTrading`만 사용한다. Desktop 작업본과 혼합 배포하지 않는다.
- 상태: 계획 문서. 아래 코드 수정, 디스크 작업, 서비스 기동, 버킷 변경은 아직 실행하지 않았다.
- 이 문서는 앞선 저장 인프라 계획을 대체한다. 기존 계획의 수집 정책과 사용자 선택인 **90일 Standard → Deep Archive**를 유지하면서 감사에서 발견한 결함을 해결한다.

## 1. 확인된 상태와 완료 목표

### 1.1 확인된 사실

2026-09-08 SSH 및 E 드라이브 코드 감사 기준이며, 배포 직전에 다시 확인한다.

| 항목 | 상태 및 의미 |
|---|---|
| 서버 | `49.50.141.106`, 2vCPU, RAM 약 4GiB, swap 없음 |
| OS | `/dev/vda2`, 약 10GB, 사용률 99%, 여유 약 104MiB |
| 추가 디스크 | `/dev/vdb` 20GB, 파일시스템·UUID·마운트 없음 |
| 실행 상태 | 기존 runtime, Telegram, NATS 모두 중지 |
| 서버 배포본 | `/root/vibetrading`의 이전 코드, 신규 systemd 구성 미설치 |
| 주요 점유 | 프로젝트 전체 약 682MiB, containerd 약 2.6GiB, VS Code 서버 약 748MiB |
| 코드 검증 | 오프라인 테스트 47개 통과·4개 실패. 저장 장애 검증은 부족 |
| 재현한 결함 | 병합 원본 정리 누락, 병합 트랜잭션 사이 종료 시 중복 활성화, flush 전 재전송 UNIQUE 오류, 호가 중복 발행, 로그 마스킹 누락, pause 상태 초기화 |

OS가 가득 찼다고 하루 시세 발생량을 10GB로 간주하지 않는다. 비용과 처리량은 배포 후 실제 수신 건수, Parquet 확정 byte, 버킷 업로드 byte를 분리해서 측정한다. 버킷 IAM·lifecycle·통신 성능은 아직 실검증되지 않았다.

### 1.2 운영 목표와 보장 범위

1. 정상 버킷 연결에서 업로드가 유입을 따라가고 로컬 사용량이 계속 누적되지 않는다.
2. NATS에 영속 수락된 시세는 Parquet·manifest 확정 전에 ACK하지 않는다. 프로세스 재시작과 재전송으로 누락·중복 저장을 만들지 않는다.
3. 공급자에서 아직 받지 못한 데이터, 수집 중지 중의 데이터, NATS가 수락하지 못한 데이터는 무손실을 보장하지 않는다. 중단 구간과 확인 가능한 유실 범위를 기록한다.
4. 정상 연결 시 **로컬 최초 수신부터 데이터 객체·원격 manifest 검증 완료까지 1시간 이내**를 목표로 한다. 병합·재시도는 이 기한을 연장하지 않는다.
5. 단일 VM·단일 디스크 전체 손실에는 아직 원격 확정되지 않은 데이터가 유실될 수 있다. 영구 네트워크 장애에서 무제한 무중단 수집을 약속하지 않는다.
6. 기존 시세 초기화는 허용된 범위에서 수행하되 인증정보·주문·kill switch·외부 설정·기존 버킷 객체는 보존한다.

## 2. 확정 저장 구조와 비용 정책

```text
허용된 공급자·종목
  → 체결 전체 / 호가 복원 후 종목별 1초 최종 상태
  → NATS FILE WorkQueue
  → bounded Arrow buffer
  → 로컬 Parquet + SQLite manifest 원자적 확정
  → 작은 파일 병합
  → Standard 업로드 + 전체 GET SHA-256 검증 + 원격 manifest 확정
  → 검증된 로컬 파일·대체 원본 순환 삭제
  → 객체 생성 후 90일 경과 시 데이터만 Deep Archive
```

### 2.1 디스크와 서버 저장 경로

추가 20GB 디스크는 정상 인식되어 있으므로 유지한다. Object Storage를 NATS·SQLite의 파일시스템으로 마운트하지 않는다. 추가 디스크를 사용할 수 없으면 수집 중지 상태에서 원인을 확인하고, 실제 불량일 때만 교체한다. OS 10GB를 임시 수집 디스크로 사용하는 대체안은 채택하지 않는다.

| 호스트 경로 | 내용 |
|---|---|
| `/srv/vibetrading-data/runtime_data` | 컨테이너 `/data`: Parquet, SQLite, 복구 journal, 캐시 |
| `/srv/vibetrading-data/nats` | JetStream 파일 저장소 |
| `/srv/vibetrading-data/docker` | Docker persistent data-root |
| `/srv/vibetrading-data/containerd` | 실제 Docker가 사용하는 containerd persistent root |
| `/opt/vibetrading/releases/<release-id>` | 사용자 드래그앤드롭 소스, OS 디스크에 현행·직전 릴리스만 유지 |
| `/opt/vibetrading/current` | 활성 릴리스 심볼릭 링크 |
| `/etc/vibetrading` | 서버 전용 설정, secret, 운영 스크립트 |

앞선 계획에서 추가로 바뀐 점은 **Docker와 containerd의 영구 데이터도 20GB 디스크로 옮긴다는 것**이다. Docker 29의 containerd image store는 Docker `data-root`만 변경해도 함께 이동하지 않으므로 실제 사용 데몬과 설정 파일을 확인해 양쪽을 이전한다. [Docker 공식 설명](https://docs.docker.com/engine/storage/containerd/)

Docker·containerd 합계는 공유 레이어를 포함한 실제 디스크 점유 기준 소프트 예산 4GiB로 관리한다. 활성 이미지와 검증된 롤백 이미지 한 개를 보존하고 빌드 캐시·불필요 이미지만 정리한다. 이 예산은 강제 파일 삭제 기준이 아니다.

수집 활성화 전 `필수 고정 점유 + 시세/운영 NATS 한도 + 최소 1GiB 작업 여유`가 실제 파일시스템의 40% 목표를 초과하는지 검사한다. 초과하면 예상 부족 byte를 표시하고 이미지 경량화·불필요 캐시 정리를 먼저 수행한다. 필수 자원만으로 목표를 달성할 수 없으면 수집을 켜지 않고 디스크 크기 또는 목표 수정이 필요한 것으로 보고한다.

### 2.2 버킷과 클래스

- Ncloud Storage의 `https://kr-private.ncloudstorage.com`, 리전 `kr`, AWS Signature V4를 명시한다. 기존 Object Storage endpoint와 `kr-standard`를 혼용하지 않는다.
- 사설 endpoint DNS·TLS·S3 호환 옵션을 서버와 실제 컨테이너 UID에서 검증한다. 인증 실패를 공개 endpoint로 조용히 우회하지 않는다.
- 시세는 Standard에서 객체 생성 후 90일이 지나면 Deep Archive로 전환한다. 공급자 시각이 아닌 객체 생성일 기준임을 status와 운영 문서에 표시한다.
- 데이터, manifest, backup, test prefix를 분리한다.

```text
vibetrading/market-data/<collection-generation>/<partition>/<file-id>.parquet
vibetrading/manifests/<collection-generation>/<file-id>.json
vibetrading/catalog/<collection-generation>/...
vibetrading/backups/<stable-deployment-id>/slots/...
vibetrading/checks/...
```

- lifecycle은 `vibetrading/market-data/` prefix에만 `Transition: Days=90, StorageClass=DEEP_ARCHIVE`를 적용한다. manifest·catalog·backup은 Standard에 유지한다.
- Ncloud Storage lifecycle 필터는 현재 prefix만 지원한다. generation 아래에 data와 manifest를 섞는 기존 key 구조를 바꿔 단일 안전한 규칙으로 데이터를 선택한다. 기존 lifecycle을 먼저 조회하고 관련 규칙만 병합하여 적용하며, unrelated 규칙을 덮어쓰지 않는다. [공식 lifecycle API](https://api.ncloud-docs.com/docs/ncloudstorage-putbucketlifecycleconfiguration)
- 시세·manifest·catalog의 원격 자동 삭제는 금지한다. 버킷 전체 삭제·만료 규칙도 추가하지 않는다. 기존 버전 관리나 Object Lock은 자동 변경하지 않는다.
- Deep Archive는 조회 시 복원 요청이 필요하며 12시간 이상 걸릴 수 있다. 복원 사본은 기본 1일 보관하고 원본 클래스는 유지한다. 복원된 사본의 Standard 비용도 예상 비용에 포함한다. [공식 복원 안내](https://guide.ncloud-docs.com/docs/ncloudstorage-use-object)

### 2.3 중복 백업 비용 제한

원격 시세 무삭제 원칙을 유지하기 위해 기본 구현에서는 lifecycle에 백업 만료 규칙을 추가하지 않는다. 대신 SQLite 백업은 **검증된 순환 슬롯**으로 보관한다: 시간별 48개, 일별 30개, 월별 12개. 슬롯 재사용은 중복 복구 사본에만 적용하며 시세·manifest의 immutable key 정책과 분리한다. 이 정책은 이전 계획의 무기한 중복 backup 누적을 대체한다.

각 슬롯은 A/B 객체와 작은 선택 manifest로 구성한다. 비활성 쪽에 백업을 업로드·GET 검증하고 선택 manifest를 갱신한 다음에만 이전 쪽을 다음 회차에서 재사용한다. 최소 두 개의 서로 다른 검증된 백업이 있을 때만 슬롯 재사용을 허용한다. 최대 180개 압축 DB 객체와 선택 manifest가 유지된다. 슬롯 namespace는 수집 세대와 분리한 고정 deployment ID를 사용하고 재설치 시에도 복원해 재사용한다. 수집 세대는 backup 내부에 기록하며 세대마다 180개 슬롯을 새로 만들지 않는다. 세대별 영구 복구 정보는 시세 manifest/catalog에 보존한다. 기존 버킷에서 버전 관리·Object Lock 때문에 순환 슬롯도 누적되는 경우 이 상한이 성립하지 않으므로 자동 해제하지 않고 운영 사전검사에서 비용 정책 불일치를 알린다.

선택 manifest는 snapshot ID·세대·시각·압축 byte·SHA-256을 기록한다. 복원 reader는 선택 manifest와 객체를 검증하고, 동시 회전으로 불일치하면 선택 정보를 재조회하거나 반대쪽 검증 사본으로 fallback한다. backup 쓰기 권한은 고정 backup namespace에 제한한 별도 자격증명으로 관리한다. DeleteObject 금지만으로 PutObject 덮어쓰기가 금지되는 것은 아니므로 시세 key 충돌 검사는 별도로 유지한다.

비용 예측은 `최근 7일 실제 압축 byte/day × Standard 90일 보관량`과 이후 Deep Archive 증가분, 복원·요청·백업을 분리한다. 이전의 가정치 10GB/일은 예산 기준으로 사용하지 않는다. API 호출 수를 줄이되 64MiB 파일을 기다리느라 원격 보관 기한을 넘기지 않는다.

## 3. 코드 변경: 수집 정책과 연결 수명

### 3.1 모든 진입점에 같은 정책 적용

- 허용 이벤트는 trade와 order_book이며 data_gap은 별도 운영 이벤트로 기록한다. 모든 공급자의 분봉 REST 요청, websocket 구독, 집계기, 모델·subject·저장·실행 경로와 관련 잘못된 테스트를 제거한다. 문서의 제거 설명이나 회귀 테스트의 거절 입력은 남겨도 된다.
- `vibetrading`뿐 아니라 `services/data_feed`, `shared`, `scripts`, cron 예제, CLI, smoke test에서 구형 수집기가 실행되지 않도록 검색·정리한다. 사용하지 않는 구형 시세 실행 코드는 삭제하고 필요한 공통 인터페이스는 새 정책만 호출하도록 수정한다.
- Binance 시세 collector와 top-N 자동 발견은 provider 생성 전에 명시적으로 거절한다. 데이터 수신 후 버리는 방식으로 구현하지 않는다. 주문·브로커 기능이 쓰는 타입은 수집 코드와 분리해 보존한다.
- 국내 ETF는 Kiwoom만, Toss는 명시적으로 허용된 미국 ETF만 구독한다. `@universe`, 직접 CLI 종목, provider.run, 공통 발행에서 모두 검증한다.
- KIS 자동 옵션 범위는 기초자산별 두 만기의 ATM ±1, 콜·풋으로 고정한다. 실제 master에서 유효한 행사가만 선택한다. 수동 지정도 현재 허용 집합의 부분집합만 허용한다. 옵션이 아닌 현재 국내 선물 범위는 유지한다.
- 현행 runtime의 `auto:all`이 해외 NQ까지 활성화하는 것은 아니다. 이번 배포에서 수집 범위를 확대하지 않으며, 사용하지 않는 NQ 설정은 inactive임을 명시한다.
- ATM 이동과 만기 교체 시 새 허용 집합을 만들고 shard 전체에 일관되게 적용한다. 구독 해제·등록 응답을 확인하고 실제 활성 목록을 status로 제공한다.
- Hyperliquid는 확인된 증권·주식·지수 파생상품 allowlist가 없으면 provider 자체를 비활성화한다. 서버 외부 설정은 검증 후보로만 읽고 문자열 패턴으로 자동 승인하지 않는다.
- Hyperliquid의 `security_kind`, 실제 `asset_class`, underlying을 collector→event→Parquet→manifest까지 그대로 전달한다. 모든 허용 상품을 TOKENIZED_EQUITY로 바꾸는 코드를 제거하고 Parquet에 누락된 `instrument_security_kind`를 추가한다.

### 3.2 호가 샘플러와 공급자 작업 관리

- 상태 key는 provider·venue·전체 instrument identity·연결 세대를 포함한다. 옵션 만기·행사가가 다른 상품을 같은 key로 묶지 않는다.
- 공급자별 snapshot/delta 규칙에 따라 먼저 호가장을 복원하고 receive timestamp 기준 1초 구간의 마지막 유효 상태를 선택한다. 원본 시각, 수신 시각, 샘플링 구간 끝, 원본 depth와 full-depth 의미를 보존한다.
- delta sequence 검증은 공급자 규격대로 처리한다. 단순히 모든 공급자에 `sequence + 1`을 적용하지 않는다. 연결 단절·역전·누락·재동기화 실패 상태의 delta를 저장하지 않는다.
- book 상태 전환과 pending 선택은 단일 소유 task 또는 lock으로 직렬화한다. 발행 중 항목을 inflight로 유지하고 성공 후 제거한다. 실패하면 동일한 sample ID로 재시도하고 메모리 상한을 넘으면 collector를 중단한다.
- sample ID는 provider·상품·sampling bucket을 기준으로 결정하고, 같은 bucket 재발행은 멱등 처리한다. bucket 최종화 이후 오래된 업데이트를 새 snapshot으로 만들지 않는다. 체결은 시간 구간으로 dedup하지 않으며 공급자 고유 trade ID가 없을 때 동일 시각의 서로 다른 체결을 합치지 않는다.
- sampler·collector·KIS shard를 구조화된 task 그룹으로 관리한다. 하나가 실패하면 같은 시도의 나머지 task를 취소하고 websocket 종료를 모두 기다린 후 재시작한다. 영속적인 pause 상태는 task 재생성과 분리한다.
- 정상 종료 시 collector 입력 중단→완료 구간 sample 확정→storage flush/ACK 순서로 종료한다. 미완료 구간·실패한 시세의 처리는 gap으로 표시하고 재연결 전후 데이터를 한 호가장으로 섞지 않는다.

## 4. 코드 변경: 영속화·복구·중복 제거

### 4.1 NATS 초기화와 자원

- 버전과 image digest를 검증된 조합으로 고정한다. 서버 설정은 파일에 `jetstream { store_dir, max_file_store, max_memory_store, sync_interval }`로 작성하고 `nats-server -t -c`로 검사한다. 지원되지 않는 `--max_file_store` CLI 인자는 제거한다.
- 초기 서버 파일 한도는 1.25GiB, 시세 stream 1GiB, 운영 stream 64MiB로 두어 stream 합계보다 서버 한도가 크도록 한다. FILE 스트림이어도 메모리 캐시가 있으므로 NATS RSS도 감시한다.
- disk sync 정책은 `sync_interval: always`를 기본으로 성능 검증한다. 처리량이 부족해도 자동으로 fsync 보장을 약화시키지 않고 수집을 제한한다. [NATS 설정 파서](https://raw.githubusercontent.com/nats-io/nats-server/v2.10.29/server/opts.go)
- 시세는 FILE·WorkQueue·DiscardNew, 시세 전체를 담당하는 storage durable consumer 하나로 고정한다. 자동 MaxAge·MaxMsgs 폐기로 미저장 시세를 지우지 않는다. 운영 stream은 별도 bounded retention을 사용한다.
- stream과 consumer 생성·변경은 `nats-init` 하나에서만 수행한다. `scripts/init_nats.py`는 이 명령을 호출하는 호환 wrapper로 바꾸고 구형 MARKET/CANDLES 설정은 제거한다.
- consumer 사용 코드는 존재·정책 검증만 한다. NotFound만 생성 대상으로 구분하며 timeout·권한 오류를 리소스 부재로 오인하지 않는다. 초기화 실패 상태에서 다른 worker가 임의 consumer를 만들지 않는다.
- MaxAckPending은 초기 65,536, ACK wait 20분, fetch batch 최대 256으로 설정한다. 80% pending에서 조기 flush하며 Arrow/RSS 한도가 항상 우선한다. 15분은 최대 대기시간이지 보장되는 최소 flush 간격이 아니다.
- NATS 80%를 수집 중단, 50% 미만을 재개 조건으로 사용한다. publish timeout과 DiscardNew는 수락 여부 불명/거절로 구분하며 같은 message ID로 재시도한다. 수락되지 않은 과거 시세까지 보존됐다고 보고하지 않는다.

### 4.2 ACK와 파일 확정

- 메시지 동일성은 `(stream generation, stream name, stream sequence)`로 보존한다. generation은 초기화 시 영속 생성하고 stream metadata·로컬 catalog와 대조한다. generation 조회 실패 시 빈 문자열로 계속하지 않는다.
- row와 파일 footer·원격 manifest에 generation과 sequence를 기록한다. 복구에 필요한 정확한 sequence coverage는 구간 집합 또는 bitmap으로 기록하며 단순 min/max로 대체하지 않는다.
- 미확정 버퍼의 ID set과 영속 committed ledger를 함께 확인해 flush 전 재전송도 다시 append하지 않는다. ACK reply 정보만 별도 보관하고 원본 JSON payload 전체를 두 번 유지하지 않는다.
- 확정 순서는 journal 준비→Parquet 쓰기→파일 fsync→rename→부모 directory fsync→manifest와 delivery 단일 SQLite 트랜잭션→ACK이다. fsync 실패를 무시하지 않는다.
- 진행 ACK는 별도 주기 task에서 30초마다 전송한다. flush가 오래 걸려도 진행 통지가 멈추지 않는다. 완료 ACK는 서버 확인을 받고, ACK 실패는 저장 rollback이나 재적재 사유로 삼지 않는다.
- 부분 flush 오류 시 이미 commit된 파티션과 미확정 파티션을 구분한다. 손상된 worker·버퍼를 그대로 반복 재사용하지 않고 journal/ledger 기반으로 재구성한다. SIGTERM 처리와 Docker 종료 유예를 연결한다.

### 4.3 시작 시 복구

- storage·compactor·maintenance가 공유하는 복구 프로토콜과 단일 소유 lease를 사용한다. 다른 작업의 살아 있는 `.tmp`를 시작 스캔이 지우지 않는다.
- job journal과 footer를 검사해 완성된 미등록 파일은 검증 후 등록하고 NATS delivery를 복원한다. 불완전 파일만 안전하게 정리하거나 quarantine한다.
- manifest 확정 후 ACK되지 않은 batch는 재전송 시 ACK만 수행한다. 단순 `.orphan` rename으로 파일을 무한 누적시키지 않는다.
- 파일이 없는데 local_present만 참인 상태, unlink 직후 중단, 원격 업로드 완료 후 DB 기록 전 중단도 멱등 복구한다.
- quarantine도 byte를 계측한다. 자동 삭제하지 않고, 재수신/원격 사본과 동일성이 증명되는 파일만 정리한다. 복구 불가능한 손상은 수집 중지와 정확한 파일·sequence 범위 경고로 처리한다.

### 4.4 delivery ledger와 catalog의 장기 증가 억제

- delivery를 단순 TTL로 삭제하지 않는다. stream generation·consumer incarnation별 **서버 확인 ACK watermark + 그보다 큰 sparse committed 집합**으로 유지한다.
- 전체 시세 subject를 소비하는 단일 consumer라는 전제 아래, NATS의 확인된 ACK floor와 로컬 영속 상태를 대조한다. SQLite 한 트랜잭션에서 watermark를 전진시키고 그 이하 개별 ledger 행을 제거한다.
- 재전송 sequence가 watermark 이하이거나 sparse 집합에 있으면 append 없이 ACK한다. consumer 재생성·stream 초기화·NATS snapshot 복원 시 세대 검증을 거쳐 자동 watermark 재사용을 금지한다.
- 최근 catalog와 로컬/미업로드/실행 중 파일은 주 SQLite에 유지한다. 원격 검증된 90일 이전 파일 목록은 월별 Standard catalog shard로 내보내고 검증한 뒤 주 DB에서는 월별 shard pointer로 축약한다. 조회·복구는 shard를 참조한다.
- WAL checkpoint, DB·WAL·ledger 행 수를 계측한다. 작은 증분 vacuum으로 반환 가능한 페이지를 회수하고 디스크 부족 중 전체 VACUUM 복사를 하지 않는다.

## 5. 코드 변경: 메모리·병합·업로드·조회

### 5.1 Arrow 버퍼와 메모리 상한

| 항목 | 초기값 |
|---|---:|
| flush 최대 대기 | 900초 |
| 파티션 Arrow byte | 64MiB |
| 전체 Arrow byte | 128MiB |
| storage RSS 조기 flush / 수신 정지 | 650MiB / 800MiB |
| storage RSS 수신 재개 | 600MiB 미만 |
| 최종 병합 목표 크기 | 압축 파일 약 64MiB |
| 회당 병합 입력 | 압축 파일 합계 최대 128MiB |
| 병합 작업 Arrow 메모리 | 최대 64MiB, batch 적응 축소 |
| 조회 캐시 | 디스크 최대 512MiB |

- 실제 Arrow RecordBatch를 보관하고 byte 합계를 증가분으로 계산한다. 매 검사마다 전체 Python 목록을 Table로 재변환하지 않는다. 전처리 Python batch도 행 수·byte 상한을 둔다.
- 한 조건에 도달하면 해당 파티션 또는 가장 큰 버퍼부터 flush한다. 전체 파티션 일괄 flush는 종료·급박한 RSS 압박에 한정한다. 신규 메시지가 없어도 타이머가 동작한다.
- 프로세스 RSS뿐 아니라 cgroup memory.current, Arrow allocator, pending ACK 메모리를 계측한다. Python 메모리 반환 지연 시 fetch를 계속하지 않는다.
- collector 768MiB, storage 1GiB, maintenance 512MiB, NATS 512MiB, Telegram 256MiB를 초기 상한으로 둔다. 상한 합계만으로 OOM 없음이라 판단하지 않고 host 여유·페이지 캐시까지 48시간 측정한다.

### 5.2 병합과 원본 정리

- 5분마다 후보를 확인하되 확정 후 5분 이상, 아직 원격 업로드되지 않은 작은 active 파일만 선택한다. 반복 병합으로 큰 파일을 계속 다시 쓰지 않는다.
- 한 파티션에서 목표 크기에 도달했거나 oldest input 30분 이상이면 작은 묶음도 처리한다. 혼자 남은 파일은 병합 없이 업로드 기한에 따라 전송한다.
- source 파일을 정렬된 ID 순으로 lease 획득하고 fencing token을 발급한다. write 작업은 한번에 하나, lease는 30초 heartbeat·5분 만료이며 갱신 실패 후 commit하지 않는다.
- row count와 모든 schema 필드의 정규화된 내용 해시를 입력 순서대로 스트리밍 비교한다. 전체 행 목록, 전체 시각 정렬, event ID만의 해시를 사용하지 않는다.
- 병합본 fsync·검증 후 **출력 manifest 등록 + source superseded 전환 + lineage 기록을 단일 트랜잭션**으로 처리한다. 입력들의 최초 수신 시각과 업로드 deadline을 출력에 상속한다.
- 독자는 snapshot manifest와 reader lease/pin을 얻고 파일을 사용한다. 삭제는 신규 reader 획득과 경합하지 않는 트랜잭션으로 결정한다. pin/lease는 fencing과 heartbeat로 보호한다.
- superseded 원본은 대체 파일의 로컬 commit 및 원격 데이터·manifest 검증을 확인하고 reader가 해제되면 즉시 정리한다. 원본 자체가 remote_verified일 필요는 없다. active 파일은 정상 6시간 보관 또는 압박 정책을 적용한다.
- 대체 파일이 다시 병합된 경우 lineage를 따라 최종 검증 대체본까지 확인한다. 중간 파일을 업로드하지 않았다는 이유로 조상 원본을 영구 보존하지 않는다.
- remote sidecar의 lineage와 월별 catalog에서도 대체 관계를 보존한다. 원격 복구 시 원본·병합본을 동시에 active로 만들지 않는다. 업로드된 파일은 다시 병합하지 않는다.

### 5.3 업로더와 원격 확정

- maintenance는 upload/compact/delete를 한 작업씩 처리하되 **한 파일 처리 후 5분 대기하는 제한을 제거**한다. backlog가 있으면 즉시 다음 작업을 수행하고 모두 비었을 때만 최대 10초 대기한다.
- 정상 파일은 확정 후 5분부터 업로드 후보가 될 수 있다. deadline이 10분 이내인 파일은 병합보다 우선하며 최초 수신 후 1시간 내 원격 확정을 목표로 한다.
- 지연·오류는 파일별 retry 상태로 관리한다. exponential backoff와 jitter를 적용하고 다른 파일 및 검증된 로컬 삭제는 계속 진행한다. 403 등 설정 오류는 공통 경고를 내고 과도한 API 반복을 제한한다.
- SDK client를 worker 수명 동안 재사용하고 multipart worker 병렬성을 1로 제한한다. 전송 중에도 lease와 제어 heartbeat를 갱신한다. 중단 multipart는 기록해 재시도 또는 해당 upload ID만 abort한다.
- Backend의 put은 전송만, verify는 HEAD size + 전체 GET SHA-256 한 번만 담당한다. 호출자·backend의 중복 GET 검증을 제거한다.
- key는 collection generation과 file ID로 고정한다. 재시도 시 기존 객체가 일치하면 재업로드하지 않고 재검증한다. 다른 내용이 있으면 덮어쓰지 않고 충돌로 중단한다.
- collection generation은 파일 생성 시 manifest에 고정한다. 업로드 당시의 전역 generation 값으로 과거 파일 key를 다시 계산하지 않는다.
- 데이터 검증→sidecar 업로드·검증→SQLite remote_verified 기록 순서로 처리한다. sidecar에는 schema version, 정확한 상품 정보, row count, byte, hash, 시간 범위, generation, delivery coverage, lineage를 기록한다.
- 원격 검증 전 로컬 원본을 삭제하지 않는다. remote_verified 후에도 query pin·lease와 대체 관계 검증이 삭제 조건이다.

### 5.4 백업과 읽기·복원

- SQLite backup API를 매시간 실행하고 압축 사본을 전송한다. 원본 DB 파일·WAL을 파일 복사로 따로 백업하지 않는다. 슬롯 회전은 2.3 정책을 따른다.
- 백업 생성 시 이미 축약된 ledger와 catalog를 사용한다. 백업을 위해 세대별 전체 tick ledger를 다시 복원하지 않는다. backup scratch는 한 작업분만 두며 완료·실패를 기록하고 안전하게 회수한다.
- history는 local_present=False를 건너뛰지 않는다. 월별 catalog 포함 manifest 탐색→원격 다운로드→hash 검증→임시파일 fsync/rename→bounded cache 편입을 수행한다.
- 캐시 512MiB에는 내려받는 `.part`와 예약 byte도 포함한다. 같은 파일 요청은 합치고 reader가 pin한 파일은 퇴출하지 않는다. 공간이 없으면 명시적으로 대기/실패하며 제한을 넘겨 다운로드하지 않는다.
- 전체 데이터를 Python 목록으로 모은 뒤 sort하지 않고 시간 범위·partition·instrument key로 후보를 제한하고 bounded top-K/merge 방식으로 limit을 적용한다. 전체 옵션 identity를 보존한다.
- Deep Archive 객체는 기본 조회에서 `RESTORE_REQUIRED`로 반환한다. 사용자가 restore 명령을 실행하면 요청 ID와 상태를 기록하고 주기적으로 확인한다. pending을 빈 데이터로 반환하지 않는다.
- restore의 크기·예상 대상 수를 먼저 출력하고 지정된 대상만 1일 복원한다. 비용이 발생하는 대량 자동 복원은 수집 worker가 수행하지 않는다.
- DB 분실 시 검증된 backup을 새 경로로 복원한 뒤 Standard manifest/catalog를 페이지 단위로 조회해 이후 데이터를 재구성한다. generation·lineage·hash·sequence coverage를 검증하고, 완료 전 collector는 중지한다.
- backup의 local_only 파일이 실제 로컬에도 원격에도 없으면 `lost_local_only`로 기록한다. 이를 포함하는 옛 ACK watermark를 그대로 복구 완료 증거로 신뢰하지 않는다. 원격 coverage와 보존된 NATS를 대조해 복구하고, 새 설치는 검증 후 새 stream generation으로 시작한다.

## 6. 용량 제어·상태·보안

### 6.1 공통 제어 상태

maintenance의 경량 controller가 10초마다 디스크·NATS·worker heartbeat를 판단한다. 실제 upload/compact는 별도 작업 thread에서 직렬 실행해 제어 루프를 막지 않는다. SQLite 또는 원자적 상태 파일에 reason, state, last transition, stable-since를 보존한다.

| 상태/조건 | 동작 |
|---|---|
| NORMAL | 업로드·병합, 검증 active 파일 최대 6시간 유지 |
| 데이터 디스크 60% 이상 | CLEANING 진입, 검증 파일 정리. 40% 이하까지 상태 유지 |
| 75% 이상 | 병합과 대형 다운로드·새 backup scratch 중단, 확정 파일 직접 업로드 |
| 80% 이상 또는 free ≤4GiB | PAUSED, collector 구독 종료. storage drain·upload·delete·controller 계속 |
| NATS ≥80% 또는 상태 조회 실패 | PAUSED. 이미 수락된 시세는 storage가 처리 |
| resume | 데이터 <50%, free ≥6GiB, NATS <50%, storage/controller 정상, 조건 60초 연속 충족 |
| OS 60%/75%/80% | 경고 / 비필수 쓰기 제한 / collector·빌드 차단. 로그·SSH용 공간 확보 |

- 재시작 직후 PAUSED 또는 UNKNOWN으로 시작하고 이전 pause 이유를 유지한다. 재연결 자체가 재개 조건이 되지 않는다. 수집 enable 파일이 없으면 조건이 좋아도 자동 수집하지 않는다.
- controller heartbeat가 30초 넘게 오래됐거나 실제 mount 확인이 실패하면 collector는 fail-closed한다. NATS가 불통이어도 업로드·정리·직접 Telegram 경고는 실행 가능해야 한다.
- collector 진입 시에도 빠른 공통 상태 확인을 수행한다. 낮은 threshold 테스트는 같은 설정 경로를 쓰며 하드코딩된 80/50/4/6을 없앤다.
- 부팅 preflight는 올바른 mount·rw·권한만 검사한다. free 6GiB 미만이라는 이유로 maintenance 전체를 막지 않는다. 실제 ENOSPC에서 복구 journal조차 쓰지 못하면 collector를 계속 중지하고 기존 검증 데이터의 최소 쓰기 정리 경로를 사용한다.
- 이를 위해 추가 디스크에 실제 할당된 256MiB emergency reserve 파일을 미리 만든다. 사용량·고정 예산에 포함하고, ENOSPC 접근 시 이 파일만 해제해 DB/journal 정리에 사용한다. 40% 이하 회복 후 재생성하며 sparse 파일을 예약 공간으로 간주하지 않는다.

### 6.2 status·알림·health

- 공급자·event별 received/accepted/persisted byte·건수, blocked reason, 현재 구독 목록·세대, 초당 trade/book 수를 구분한다.
- data/OS 실제 byte·used ratio, Docker/containerd·NATS·DB/WAL·active/superseded/tmp/cache별 byte를 제공한다. logical manifest 합계와 실제 물리 사용량을 혼동하지 않는다.
- Arrow/cgroup/RSS, pending ACK, oldest uncommitted age, NATS backlog·ack floor, oldest upload deadline, verified/deletable/pinned byte, 최종 성공·실패·재시도 시각을 제공한다.
- 실제 변화율로 고갈 시간을 계산하되 표본 부족·증가율 ≤0은 unknown/not-growing으로 표시한다. 1시간 원격 확정 목표 위반과 backup freshness를 별도로 경고한다.
- worker health는 단순 파일 존재가 아닌 실제 poll·flush·upload·controller 진행 시각이다. 거래 없는 세션은 정상 idle heartbeat로 판정하고, 진행이 멈추면 프로세스가 비정상 종료해 Docker가 재시작하도록 한다.
- Telegram의 누락된 delete_consumer 호출은 제거한다. 구 durable 정리는 nats-init/migration 책임으로 옮긴다. Telegram polling·session·알림 경로는 NATS 초기 연결 실패에 종속되지 않게 한다.
- 알림은 상태 변화·복구·지속 장애 요약으로 rate limit한다. NATS 장애 중 storage 경고는 직접 Telegram API 경로를 쓴다.

### 6.3 비밀정보와 접근 제어

- collector, storage, maintenance, Telegram의 env를 역할별로 분리한다. collector·Telegram에 archive 비밀키를 전달하지 않고 maintenance에 브로커 주문 인증정보를 전달하지 않는다.
- 공통 비밀 없는 storage/nats 정책 env는 init·storage·controller·status에 동일하게 전달한다. Docker Compose 변수 치환용 env와 컨테이너 env_file을 혼동하지 않는다.
- root logger만이 아니라 출력 handler에 redaction을 설치한다. traceback·HTTP URL·구조화 필드에도 같은 마스킹을 적용하고 합성 token·key로 검증한다.
- host secret 디렉터리는 root 소유 0750, 각 파일 root:전용 secret-read GID 0640으로 두고 필요한 컨테이너 UID10001에만 supplemental GID를 부여한다. Compose bind secret의 실제 UID/GID·read access를 해당 UID로 검증한다.
- secret file 읽기 실패, 비어 있는 키, 잘못된 필수 archive 설정은 조용히 disabled 처리하지 않고 명확한 설정 실패로 보고한다. read-only 복구 모드와 수집 가능 모드를 구분한다.
- 업로더 권한은 지정 prefix의 list/put/get 및 필요한 multipart 정리로 제한한다. 시세 DeleteObject, bucket/lifecycle 변경 권한은 주지 않는다. restore와 운영 lifecycle 설정은 별도 운영 권한으로 처리한다.
- NATS 4222는 host publish하지 않고 monitoring 8222는 localhost만 노출한다. 앱별 인증·subject 권한을 부여해 storage 외의 시세 durable 생성과 임의 stream 변경을 막는다.
- SSH는 기존 연결과 두 번째 키 인증 세션을 확인한 후 password/keyboard-interactive 로그인을 비활성화하고 root는 키 인증만 허용하는 `prohibit-password`로 제한한다. `sshd -t` 후 reload하며 사용자가 요청하지 않은 계정·키 삭제는 하지 않는다.
- Ncloud ACG의 허용 source IP를 유지한다. rpcbind 등의 불필요한 listener는 사용 의존성을 확인한 후 중지한다. 기존 `.env`의 키는 실제 노출 증거 없이 일괄 교체하지 않는다.

## 7. 설정·인터페이스 정리

계획에서 사용한 이름과 코드가 읽는 이름을 통일한다. 알 수 없는 storage/archive 설정과 충돌한 구·신 설정을 `config-check --strict`에서 실패시킨다.

| 설정/인터페이스 | 결정 |
|---|---|
| `ARCHIVE_UPLOAD_AFTER_SECONDS` | 300, 로컬 확정 후 후보가 되는 최소 나이 |
| `ARCHIVE_UPLOAD_MAX_AGE_SECONDS` | 신규 3600, 최초 receive부터 원격 검증까지 최대 목표 |
| `ARCHIVE_LOCAL_RETENTION_SECONDS` | 21600. 앞선 `LOCAL_VERIFIED_RETENTION_SECONDS`는 사용하지 않음 |
| `ARCHIVE_STANDARD_DAYS` | 90. 실제 lifecycle read-back과 비교하는 운영 설정; 선언만으로 bucket을 변경하지 않음 |
| `ARCHIVE_CACHE_MAX_BYTES` | 536870912 |
| `ARCHIVE_SQLITE_BACKUP_INTERVAL_SECONDS` | 3600 |
| KIS 범위 | `config/universe.yaml`의 `strikes_each_side: 1`이 단일 진실. 앞선 미구현 `KIS_OPTION_STRIKE_RADIUS` env는 제거 |
| Hyperliquid 범위 | typed `universe.yaml` allowlist 단일 진실. 문자열 env 목록을 새로 병행하지 않음 |
| `status --json` | 세대·정합성·저장·비용·health·구독 목록을 구조화해서 반환 |
| `archive-check` | check prefix에 작은 객체 PUT→HEAD→GET/hash 검증. 수집용 키에 삭제 권한이 없으면 생성 key와 cleanup 필요를 반환하고 운영 계정으로 그 key만 정리 |
| `archive-restore` | 대상 file ID·기간·byte 출력, 복원 요청/조회, pending/ready/failure 상태 |
| `catalog-recover` | 새 destination에만 복구, dry-run과 검증 보고 제공. 운영 DB 자동 덮어쓰기 금지 |
| `collection pause/resume` | 공유 제어 상태와 enable gate 변경. resume 요청도 용량·health 조건을 우회하지 않음 |
| `release-verify` | 파일 hash, strict config, schema compatibility, mount, UID permissions, image digest 검증 |

CLI exit code는 성공 0, 잘못된 설정/정책 거절 2, 외부 장애/불완전 검증 1로 통일한다. restore pending은 구조화 상태와 함께 반환해 빈 조회 성공으로 오해하지 않게 한다.

## 8. 서버 작업과 VS Code 배포 순서

### 8.1 로컬 구현·릴리스 준비

1. E 드라이브의 기존 변경을 보존하고 현재 파일 hash·Git 기준·미추적 파일 목록을 기록한다. Desktop의 다른 구현을 자동 병합하지 않는다.
2. 순서대로 정책/기동 → 영속화/병합 정합성 → 업로드/정리/ledger → 제어/복구/관측 → 서버 스크립트를 구현한다. 각 단계의 실패 주입 테스트를 완료한다.
3. Python 3.11 배포 환경에서 의존성을 고정한다. runtime과 dev/선택적 execution 의존성을 나누고 사용하지 않는 browser·DB·crypto collector 도구를 공통 수집 이미지에 넣지 않는다.
4. 모든 앱 역할은 한 번 만든 `vibetrading:<release-id>` 이미지를 사용한다. 서비스별 같은 소스를 따로 빌드하지 않는다. base image와 NATS도 digest를 기록한다.
5. release ID는 UTC timestamp와 소스 manifest SHA-256 앞부분으로 만든다. manifest는 tracked/untracked 배포 파일 모두 포함하며 secret은 포함하지 않는다.

### 8.2 디스크·엔진 이전

1. 수집 중지와 기존 cron/자동 시작을 확인한다. 설정·주문 상태·kill switch·SQLite는 일관된 백업으로 보존하고 새 디스크에 검증 사본을 확보하기 전에 삭제하지 않는다.
2. `lsblk`, `blkid`, `wipefs -n`, Ncloud 콘솔의 disk ID/용량을 대조한다. 실제 빈 추가 장치임이 확인된 경우에만 ext4로 포맷한다. 기존 파일시스템이 보이면 포맷하지 않는다.
3. UUID로 `/srv/vibetrading-data`를 fstab에 등록한다. `nofail`로 OS/SSH 부팅은 가능하게 하고, 애플리케이션 엔진은 해당 mount에 의존하게 한다.
4. 앱 디렉터리는 UID/GID10001 기준으로 만들고 엔진 저장 경로는 기존 엔진 소유권을 보존한다. 디스크 UUID와 installation ID를 담은 sentinel을 생성한다.
5. Docker·containerd를 모두 정지한 상태에서 각각 실제 persistent root를 새 경로로 이전·검증하고 daemon 설정을 수정한다. 엔진 데이터는 실행 중 복사하지 않는다.
6. `docker.service`와 실제 `containerd.service` 양쪽에 데이터 mount의 Requires/After/BindsTo 및 UUID·rw preflight를 추가한다. 마운트 실패 시 Docker 자동 재시작도 데이터를 OS 경로에 쓰지 못해야 한다.
7. 엔진 재기동 후 image/container/volume 목록과 실제 저장 위치를 검증한다. 검증된 이전 데이터만 정확한 경로로 정리한다. `/var/lib` 전체 삭제나 무차별 prune은 금지한다.
8. 앱 bind mount는 `create_host_path: false`를 사용한다. entrypoint에서도 sentinel과 bind mount 상태를 확인한다. free-space gate는 collector에만 둔다.

### 8.3 사용자 드래그앤드롭

1. 사용자가 VS Code Remote-SSH로 `/opt/vibetrading/releases/<release-id>.uploading`을 연다.
2. E 드라이브의 배포 파일을 해당 디렉터리에 드래그앤드롭한다. 코드 업로드에 tar/zip 압축·해제를 사용하지 않는다.
3. `.git`, `.env`, `.venv`, runtime_data, cache, 로컬 로그·실행 결과를 제외한다. `config`는 예제/기본 정책으로 포함하되 서버 검증 allowlist와 비밀값은 `/etc/vibetrading`에서 관리한다.
4. `release-verify`로 누락·추가·hash 불일치를 확인하고 실행 스크립트의 LF·chmod를 검사한다. `.uploading` 디렉터리는 실행하지 않는다.
5. 업로드 완료 후 최종 디렉터리로 rename한다. Compose project name은 항상 `vibetrading`으로 고정하고 현재 디렉터리 이름으로 새 project가 생기지 않게 한다.
6. 모든 Compose 호출은 동일한 명시 경로, project name, `/etc/vibetrading/compose.env`를 사용한다. systemd의 WorkingDirectory는 `/opt/vibetrading/current`로 맞춘다.

### 8.4 초기화·기동·검증

1. 기존 시세 reset은 대상 Parquet/tmp, 시세 stream, 관련 manifest/delivery를 dry-run 목록으로 확정한다. 주문 테이블·설정·외부 디렉터리·기존 원격 객체는 제외한다.
2. 기존 구버전 cron과 old stack 자동 재시작을 비활성화한다. 구형 NATS stream reset은 신규 init과 별도 migration 단계에서 수행하고 stream generation을 새로 만든다.
3. 공통 이미지 하나를 메모리·디스크 예산 내에서 빌드한다. 빌드 중 collector는 중지하고 build cache를 완료 후 정리한다.
4. strict config/UID secret 검사와 migration을 먼저 수행한다. current symlink는 검증된 release에만 원자적으로 전환한다.
5. NATS 시작→health 확인→nats-init 성공→storage recovery 순으로 진행한다. init은 healthy NATS, storage는 init의 `service_completed_successfully` 조건을 사용한다.
6. maintenance와 Telegram은 NATS 장애 중에도 기동 가능하게 한다. collector는 init/storage/controller 정상 및 enable gate를 모두 요구한다.
7. archive-check, lifecycle read-back, backup/restore, remote query와 낮춘 threshold 시험을 수행한다. test 객체는 별도 prefix·운영 권한으로 정리하며 시세 원격 삭제권을 주지 않는다.
8. 조건 통과 후 collector enable gate를 생성하고 60초 안정 조건을 만족할 때 수집한다. 실거래·전략 제출은 비활성 상태를 유지한다.

### 8.5 배포 중지·롤백

- 정지는 collector gate 해제→collector/shard 종료→storage flush·확인 ACK→maintenance 작업 완료 또는 안전 중단→NATS 종료 순서다. SIGTERM handler와 유예 시간(storage 120초, maintenance 180초)을 적용한다.
- 최초 신구 전환은 기존 runtime으로 자동 롤백하지 않는다. 구버전은 새 WorkQueue·manifest schema를 이해하지 못하므로 실패 시 **collector 중지 + 새 recovery 서비스 유지**가 기본 fallback이다.
- 이후 롤백은 동일 schema를 지원한다고 검증된 직전 release/image 한 개로만 한다. source symlink뿐 아니라 image digest·설정 hash도 되돌린다.
- 오래된 SQLite backup으로 되돌려 최신 주문·시세 commit을 잃게 하지 않는다. catalog 복구는 별도 경로에서 이후 remote manifest와 journal을 반영하고 검증한 뒤 전환한다.
- 마이그레이션 실패·버킷 인증 실패·disk target 불가·원격 검증 실패는 수집 비활성 상태로 남는다. 원격 객체를 되돌리기 위해 삭제하지 않는다.

## 9. 필수 검증과 감사 지적 추적

| ID | 해결할 문제 | 완료를 증명할 시험 |
|---|---|---|
| F01 | NATS 잘못된 옵션·Telegram missing method | 고정 이미지 config test, empty disk Compose cold start, Telegram 연결/명령 smoke |
| F02 | superseded 원본 영구 잔류 | 병합본 원격 검증 후 원본 실제 삭제; reader pin 동안 보존; 사용량 감소 확인 |
| F03 | 5분당 1개·1시간 이후 시작·업로드 실패가 삭제 차단 | 수천 작은 파일 backlog drain, deadline 상속, 403 중 검증 파일 정리 |
| F04 | 병합 두 트랜잭션·lease 없음 | 출력 rename/commit 전후 kill, active logical rows 불변, 중복 worker fencing |
| F05 | 미확정 재전송 UNIQUE 실패·ACK 진행 중단 | flush 전 duplicate, ACK timeout, 부분 flush 실패, 20분 초과 작업 중 progress ACK |
| F06 | Python 전체 목록·중복 검증 목록 OOM | 고압축·깊은 호가 입력에서 RSS/cgroup peak 측정, 작은 Arrow batch로 처리 |
| F07 | MaxAckPending이 작은 파일 양산 | 서로 다른 유입률·partition 수에서 pending/flush 이유/파일 크기 측정, uploader 추종 |
| F08 | pause 재생성·40% 정리 목표 미달 | 80% pause 후 재연결·재부팅해도 50%/6GiB/60초 전 재개 없음, CLEANING40%까지 유지 |
| F09 | 호가 중복·pending 유실·depth 훼손 | 경계 동시성, publish 실패, sampler 예외, sequence gap/reconnect, no-update 구간 시험 |
| F10 | KIS shard 잔존 | 한 shard 강제 종료 후 전체 연결 수가 원래 상한 복귀, ATM 변경·만기 교체 검증 |
| F11 | 원격 조회·backup/restore 미구현·SQLite 무한 증가 | local 제거 후 history, archive pending/restore, DB 분실 복원, ACK floor 이후 ledger 축약 |
| F12 | 수동 CLI·legacy policy 우회·HL 오분류·분봉 잔재 | 모든 진입점에서 일반 코인/Toss 국내/분봉 거절, 실제 구독/NATS/Parquet 분류 확인 |
| F13 | root filter 마스킹 실패·비밀권한·공개 포트 | child logger/traceback 합성 secret, UID10001 read test, port/SSH 키 세션 검사 |
| P01 | mount 우회·부팅 복구 gate | 디스크 없는 reboot 시 OS/SSH만 정상, disk low reboot 시 maintenance 작동 |
| P02 | containerd OS 잔류·빌드 peak·중복 images | 각 persistent root 검증, 실제 추가 disk 사용량 계측, 단일 image build |
| P03 | 릴리스 경로·project/env 이름 충돌 | 불완전 drop 거절, hash 검증, 동일 project 재배포, 서버 설정 보존 |
| P04 | lifecycle prefix·Deep Archive 조회·backup 비용 | data만 90일 transition, manifest Standard 유지, backup 슬롯 재사용 무결성 |
| P05 | status/health 부족·NATS 의존 경고 | idle 정상, worker deadlock 실패, NATS off 중 직접 경고·정리 계속 |
| P06 | 중복 GET·legacy cron/init·의존성 변동 | 파일당 verify GET1회, retired 실행 거절, dependency lock 재현 빌드 |

강제 종료 시험은 flush 전, 파일 fsync 후, rename 후, manifest commit 후, ACK 전, upload 후 sidecar 전, remote_verified 전, unlink 후 DB 전에서 각각 수행한다. 프로세스 종료 테스트와 VM/디스크 전체 손실의 보장 범위를 구분한다.

48시간 검증에는 국내·미국 거래 세션을 포함한다. 다음을 모두 통과해야 운영 완료로 표시한다.

- 일반 코인·분봉·Toss 국내 ETF 저장 0건, 원본 체결과 저장 체결 대조, 종목별 초당 호가 최대 1개.
- OOM 0회, 비정상 websocket/shard 증가 없음, host 메모리 여유와 disk target 회복 확인.
- 정상 연결 시 모든 대상 데이터의 원격 검증 지연 ≤1시간, backlog 장기 증가 없음.
- 2배 실측 평균 유입의 시험 backlog를 줄일 수 있는 처리량 확인. 평균 처리량만 맞고 피크 후 회복하지 못하면 미완료.
- SQLite/delivery/superseded/tmp/cache 증가가 정책 범위 안에 있고, 종료·재시작 후 행 수와 hash 불변.
- 최신 백업·원격 manifest만으로 새 로컬 경로에서 조회 가능한 catalog 복원.
- 서비스 상태 변화·복구 알림이 전달되고 비밀값이 로그에 출력되지 않음.

## 10. 실행 순서와 산출물

1. **정책·기동 수정:** NATS/Telegram, legacy 경로, 전체 수집 정책, 고정 의존성.
2. **정합성 수정:** pending dedup, durable commit/journal, 단일 병합 트랜잭션, lease/pin, sampler/task lifetime.
3. **장기 용량 수정:** Arrow bounded 처리, 빠른 backlog drain, superseded 정리, ledger/catalog 축약, backup/cache.
4. **운영 제어 수정:** 공유 pause/resume, 별도 disk recovery, metrics/health/직접 알림, secrets/마스킹.
5. **통합 검증:** F01–F13/P01–P06 회귀·실패 주입·고정 이미지 빌드.
6. **서버 배포:** 디스크/엔진 이전, 사용자 코드 드래그앤드롭, 설정·시세 reset, staged 기동.
7. **48시간 확인:** 실제 수집 byte와 비용 예측, 복구 시험 보고, 릴리스·설정·이미지 hash 고정.

구현 완료 시 이 문서의 각 ID에 테스트 결과와 미완료 이유를 연결하고, 배포 release manifest·migration/reset dry-run 목록·서버 before/after disk report·48시간 운영 결과를 남긴다. 테스트 미통과나 외부 검증 미완료 항목을 단순히 코드가 존재한다는 이유로 완료 처리하지 않는다.
