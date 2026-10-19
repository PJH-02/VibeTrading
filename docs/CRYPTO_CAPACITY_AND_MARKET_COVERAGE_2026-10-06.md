# 수집 장애 수정 및 KIS·키움 저장 자료 검증

서버: `49.50.141.106`. 기준일: **2026-10-06, 한국 시각(KST)**. 아래 시간은 별도 표시가 없으면 KST다.

코인 수집의 반복 중단 원인을 수정해 운영 서버에 배포했다. Binance·Bybit·Bitget·Arcus·GRVT가 모두 발행 중이고, Hyperliquid도 발행 중이다. NATS 스트림과 durable consumer를 유지한 채 저장 병목, 보관 작업의 정체, 디스크 배치를 수정했다.

KIS·키움은 **9월 29일 이후에도 실제 저장 자료가 존재한다**. 미종료 `nats_publish_timeout` 하나를 근거로 이후 자료를 모두 제외하는 해석은 실제 저장 결과와 맞지 않는다. 그러나 10월 1일·2일에는 공통 관측 공백이 존재한다. **모든 거래소 틱이 빠짐없이 수집됐다고 입증한 기간은 없다.** 아래 구간은 검증된 저장 자료의 관측 범위다.

## 1. 반복된 `nats_stream_capacity`의 원인과 조치

| 원인 | 확인한 증거 | 배포한 수정 |
|---|---|---|
| 누적 이벤트 대비 중복 검사 필터가 작음 | 32MiB 필터에 약 4,431만 이벤트, 약 50,988개 historical run. 프로파일에서 `committed_event_ids → ExactEventRuns.lookup`의 반복 디렉터리 조회가 저장 CPU를 차지 | 필터 128MiB, 크기 검증 및 조회 계측 추가. Crypto storage 메모리 상한 1,280MiB, SQLite 캐시 64MiB |
| 저장 처리보다 발행 속도가 빨라 작은 NATS 버퍼가 반복 포화 | CRYPTO_DATA 128MiB의 80%에서 보호 정지하고 50% 아래에서 재개하는 반복 | CRYPTO_DATA 512MiB, NATS 전체 파일 저장 한도 2.5GiB. 기존 보호 정지·재개와 저장 후 ACK 규칙 유지 |
| 보관 작업이 삭제 후보 조회에서 막힘 | 이미 원격 보관된 전체 manifest를 재귀적으로 펼치다가 3초 SELECT 제한에 도달. 업로드·복구 원장 정리 단계가 실행되지 않음 | 로컬 존재 파일을 먼저 찾는 partial index와 전방 replacement chain 조회. 이 SELECT만 시간 초과하면 파일 삭제 없이 다음 업로드 단계로 진행. 자원 부족 시 중단 조건 유지 |
| Market/Crypto 보관 작업의 공유 잠금 재시도가 느림 | 잠금을 얻지 못하면 300초 대기하여 작업이 지연 | 잠금 충돌만 약 7–13초 jitter 재시도. 실제 CPU·I/O 부족 시 기존 cooldown 유지 |
| 300GB가 있어도 Market 데이터·원장은 20GB에 남음 | 20GB 약 85%, 여유 2.9GiB로 4GiB reserve 미달. 10월 4일부터 일부 Market 수집이 `disk_reserve` 정지 | Market SQLite·Parquet·exports를 300GB로 옮기고 bind mount 및 systemd 시작 조건에 반영 |

`nats_stream_capacity`는 스트림이 차기 전에 수집을 멈추는 보호 동작이다. 이번에 확인한 주원인은 거래소 API 인증 오류가 아니라 **저장·보관 경로의 처리 지연**이었다. 트래픽 증가가 포화를 앞당길 수 있지만, 이번 수정은 누적 데이터에 따라 느려지는 경로도 다룬다.

중복 판정의 원본 키 확인, run payload/header SHA 검증, 원격 복구 자료 검증, 연속 ACK floor 및 보관 기간 조건을 유지했다. 원격 보관이 검증되지 않은 자료는 정리하지 않는다. 논리 데이터셋이나 NATS 스트림을 purge하지 않았고, SKHYUSDT·SKhynixUSDT 등 기존 자료도 보존했다. 과거 사용자 요청으로 삭제된 코인 세대를 복원하지 않았다.

## 2. 배포 후 측정

**01:03:25–01:08:25, 300.218초 동안 16개 표본**을 수집했다.

- 5개 코인 수집기 모두 계속 `publishing`, Crypto storage `ready=true`.
- 새 NATS 이벤트 **277,846개**, 평균 **925.5개/초**.
- CRYPTO_DATA 최대 **55,171,367바이트**, 새 한도의 **10.3%**. 해당 구간 말미 undelivered 0, redelivery 0.
- 이벤트 lookup 41,400회 동안 전체 run 디렉터리 조회 7회. 조회 비율 약 **0.017%**.
- 프로파일의 `committed_event_ids` 표본이 이전 679개에서 이후 5개로 감소했다. 저장 프로세스 CPU 관측은 기존 한 코어 약 96%에서 약 33–47%로 낮아졌다. 이는 서버 전체 CPU 부하의 감소를 보장하는 수치가 아니다.

최종 보관 코드 배포 후에도 수집·저장이 진행됐다. **01:42–01:43대 표본**에서 Crypto 보관 작업은 각각 약 **13.0–13.9초**에 완료되었고, 각 주기에 다음 결과를 확인했다.

- 파일 **32개 업로드**.
- 원격 복구 자료와 ACK floor가 검증된 compact delivery 기록 **65,536개 정리**.
- 이벤트 run retirement 단계까지 실행. 이 표본에서는 자격 조건을 충족한 retirement가 0개여서 원본을 유지했다. backlog 전부가 해소됐다는 뜻은 아니다.
- Market 보관 작업도 파일 **32개 업로드**, 약 **14.5초**에 완료.

약 01:41의 전체 스냅샷에서 관련 컨테이너는 healthy, 배포 후 restart count 0, OOM kill 0이었다. Crypto storage는 약 211만 건 수신 및 약 211만 건 ACK를 기록했고, 당시 buffered 3,411건은 정상 배치 저장 대기였다. NATS의 pending ACK와 아직 소비되지 않은 메시지는 구분해야 한다.

스트림·consumer 생성 시각은 변경 전후 동일하다. CRYPTO_DATA는 `2026-09-26T10:23:10.911121827Z`, crypto consumer는 `2026-09-26T10:23:10.918695863Z`이다. 재생성·purge·임의 ACK reset을 하지 않았다.

검증: **로컬 관련 테스트 104개, 실제 배포 Linux 이미지 테스트 101개 통과**. 필터 포화, 정확한 중복 판정, 대규모 원격-only manifest 조회, replacement chain/sidecar/lease/age 조건, 시간 초과와 자원 부족의 구분, 잠금 재시도를 포함한다.

측정 근거: [5분 검증](evidence/capacity-validation-20261006.json), [최종 보관 검증](evidence/maintenance-validation-final-20261006.json), [보관 진행 원장](evidence/maintenance-progress-final-20261006.json), [최종 전체 스냅샷](evidence/incident-final-20261006.json), [프로파일](evidence/profile-after-20261006.json).

## 3. 디스크 운영 배치

| 할당 | 운영 용도 | 최종 관측 사용률/여유 |
|---|---|---|
| 기존 10GB `/dev/vda2` | OS 및 기본 시스템 | 약 83%, 여유 1.6GiB |
| 기존 20GB `/dev/vdb` | Docker 및 NATS | 약 **23%, 여유 15GiB** |
| 신규 300GB `/dev/vdc` | Market·Crypto 상태 원장, Parquet, exports, 4GiB swap, 마이그레이션 보존 사본 | 약 **21%, 여유 233GiB** |

NATS와 데이터 파일 I/O를 다른 볼륨으로 분리했다. 기존 프로그램 경로는 bind mount로 유지하고, 재부팅 때 mount가 준비된 뒤 서비스가 시작되도록 구성했다.

| 기존 경로 | 실제 300GB 경로 | 검증한 파일/바이트 |
|---|---|---|
| `/srv/vibetrading-data/runtime_data/state` | `/srv/vibetrading-capacity/market-state` | 27개 / 6,355,895,936B |
| `/srv/vibetrading-data/runtime_data/market_data` | `/srv/vibetrading-capacity/market-data` | 116,667개 / 3,391,667,819B |
| `/srv/vibetrading-data/runtime_data/exports` | `/srv/vibetrading-capacity/market-exports` | 6개 / 2,221,607,502B |

모든 파일을 원본→활성 경로→보존 사본으로 SHA256 대조했다. 보존 사본은 `/srv/vibetrading-capacity/migration-retained-20261006/`에 있다. 전체 영수증은 `/srv/vibetrading-capacity/incident-20261006/market-migration/complete.json`, SHA256은 `19eec9de8834ea274a24fe6535106ef39d972a86a88c8c263f2ef79bb360be96`이다. [영수증 요약](evidence/market-migration-summary-20261006.json).

마이그레이션에는 서비스 정지가 필요했다. 실제 정지 시각은 [정지 기록](evidence/maintenance-stops-20261006.json)에 남겼다. 당시 거래소에서 발생한 모든 신규 이벤트를 받았다고 보장하지 않는다. 복사 후 감사 도구가 만든 SQLite WAL/SHM의 UID 문제로 Market writer가 일시 실패했다. 해당 보조 파일을 DB 소유자 UID로 생성하도록 고쳤고, Market writer를 재시작해 NATS에 남은 미ACK 메시지를 저장·ACK했다. 원본 자료 체크섬은 일치했으며 임의 ACK나 메시지 삭제는 없었다.

CPU/RAM은 확장하지 않았다. 2코어 CPU와 현재 RAM이 피크 처리량의 제약으로 남는다. 지금 KIS·키움은 `market_closed`여서 국내 장중 데이터와 코인이 동시에 최대 유량으로 들어오는 상황은 이번 새벽 검증에 포함되지 않았다. 저장 공간 확충만으로 CPU 처리량이 무한히 늘지는 않는다.

## 4. KIS·키움 원장과 텔레그램이 달라 보인 이유

원본 공백:

- KIS, gap ID 456: **2026-09-29 08:45:31.475750**, `nats_publish_timeout`, 종료 시각 NULL.
- 키움, gap ID 460: **2026-09-29 08:50:00.222254**, `nats_publish_timeout`, 종료 시각 NULL.

과거 Market 이미지 `c8689…`의 `confirm_recovered()`는 KIS·키움에도 **CRYPTO_DATA** readiness를 확인했다. Market 기대 한도와 Crypto 한도가 달라 이 검사가 실패하면, 실제 발행이 재개되어도 공백 종료 callback이 실행되지 않았다. 올바른 provider별 MARKET_DATA/CRYPTO_DATA 선택은 10월 3일 코드에 반영되어 있고 이번 배포에서도 유지했다. [과거 이미지 코드 증거](evidence/old-market-readiness-20261006.json).

실제 복구 시각을 소급해 확정할 수 있는 operation 기록이 부족하다. **원본 gap 시작·종료 시각을 임의 수정하지 않았다.** 현재 장 종료 상태에서 과거 공백을 '지금 복구'로 닫는 것도 하지 않았다.

텔레그램의 healthy·수집 중·장 종료 표시는 그 시점의 운영 상태다. 그날의 모든 종목·모든 틱이 저장됐다는 증명이 아니다. 반대로 미종료 gap도 그 이후 저장 자료가 하나도 없다는 증명이 아니다. 실제 Parquet와 시각별 관측을 함께 확인해야 한다.

## 5. 실제 저장 자료에서 확인한 기간

활성 KIS·키움 Parquet **43,758개**의 SHA256·크기·행 수를 확인했고 실패는 **0개**였다. `order_book`·`trade` 합계 **4,539,240행**, **456개 종목/이벤트/일 조합**이다. 오래된 `participant_flow`는 검증된 KOSPI200 선물 외국인 수급으로 인정할 수 없어 아래 핵심 자료에서 제외했다.

아래 표는 대표 종목 **KIS:A01612 선물 호가**와 키움 ETF **069500·229200 호가**에서 확인한 공통 구간이다. '연속 관측'은 **각 5분 구간에 호가 관측이 하나 이상 존재**한다는 의미다. 틱 사이의 유실, 예상 구독 전체의 완전성, 모든 옵션의 완전성은 입증하지 않는다.

| 날짜 | 대표 종목 공통 관측 또는 공백 | 판정 |
|---|---|---|
| 9월 9·10·11·14일 | 각 날짜 **09:00–15:30** | 해당 호가의 5분 관측 공백 없음 |
| 9월 8일 | 늦은 오후부터 자료 존재 | 일중 전체 수집으로 사용할 수 없음 |
| 9월 15일 | 대표 종목에서 최장 약 305분 공백 | 전체 장 정상 수집 판정 불가 |
| 9월 16일 | 오전 초반 짧은 구간만 존재 | 전체 장 정상 수집 판정 불가 |
| 9월 21일 | 약 12:04–12:38의 부분 자료 | 전체 장 정상 수집 판정 불가 |
| 9월 29일 | 약 **15:10–15:30** 공통 관측. KIS:A01612 최초 15:09:39, ETF 최초 약 15:09:38 | 오후 부분 자료 사용 검토 가능. 오전 자료를 있다고 추정할 수 없음 |
| 9월 30일 | 약 **09:34–15:30** 공통 연속 관측. KIS 최초 09:33:55, ETF 최초 09:33:53 | 이 범위에서 5분 관측 공백 없음. 시작 전 자료는 인정 불가 |
| 10월 1일 | 공통 빈 구간 **14:25–15:30** | 앞부분 자료는 존재하지만 전체 장 정상 수집 판정 불가 |
| 10월 2일 | 공통 빈 구간 **10:40–12:10**, **13:15–13:40**, **14:40–14:55** | 해당 공백을 제외해 사용 검토 필요 |

빈 구간의 경계는 5분 집계 경계다. 실제 정지·복구 시각과 초 단위로 동일하다는 뜻은 아니다. KIS:A01612는 여러 날 15:45까지 자료가 있으나 ETF와의 공통 범위를 위 표에서는 15:30까지 표시했다.

옵션은 종목별 구독 기간·유동성·만기가 다르고 trade가 없는 것이 곧 장애는 아니다. 위 대표 호가 구간을 모든 옵션·모든 체결에 적용하지 않는다. **종목·이벤트 종류별 첫/마지막 관측, 빈 5분 구간, 관측 interval은 CSV를 확인한다.**

- [종목·이벤트·일별 관측](MARKET_OBSERVATION_DAYS_2026-10-06.csv)
- [종목별 관측 구간](MARKET_OBSERVED_INTERVALS_2026-10-06.csv)
- [미종료 원장과 저장 관측의 해석/체크섬](MARKET_GAP_RECONCILIATION_2026-10-06.json)

## 6. 다른 프로젝트가 읽을 서버 보고서

다음 경로에 동일한 보고서를 배치했다.

`/srv/vibetrading-data/runtime_data/reports/market-coverage/20261006/`

원본 미종료 gap만으로 9월 29일 이후 전부를 삭제·배제하기보다, 이 디렉터리의 `MARKET_GAP_RECONCILIATION_2026-10-06.json`과 종목별 관측 CSV를 함께 읽어야 한다. `all_ticks_lossless_periods_proven`은 빈 배열이고, 관측 interval의 `all_ticks_verified`는 false다. 연구에서 모든 틱의 무손실을 요구한다면 이번 감사만으로 그 조건을 충족했다고 간주해서는 안 된다.

## 7. 운영 배포 및 복구 참고

- Crypto storage·수집기 및 Market 수집기: `/opt/vibetrading/releases/capacity-20261006/`, 이미지 `sha256:fcfe1a36a1efce6e7bbe843f4dab59df48e230bbda545b3ad9d94bd77758b026`.
- Crypto maintenance·Market storage·Market maintenance: `/opt/vibetrading/releases/capacity-maintenance-final-20261006/`, revision `maintenance-7fa74634081791d369a7`, 이미지 `sha256:3f0a8ade56fbfba6fbf5b9304b54d7d45b35dbdf7319f2acd1f37fdf6eeec2fe`.
- systemd `vibetrading.service`는 위 설정을 사용한다. fstab bind 및 mount 확인 조건도 반영했다.
- [최종 maintenance 릴리스 영수증](evidence/maintenance-release-final-20261006.json), [capacity 릴리스 영수증](evidence/capacity-release-20261006.json).

코드 rollback이 필요해도 현재 데이터 경로·NATS 스트림·consumer ACK 상태를 유지해야 한다. 20GB 이전 배치로 돌아가거나 작은 128MiB 한도를 무조건 복원하면 이전 압박이 재발할 수 있다. cold 보존 사본은 현재 원장보다 오래된 스냅샷이므로 실행 중 DB 위에 덮어쓰지 않는다. 복구가 필요하면 writer를 정지하고 현 상태를 별도로 보존한 뒤 영수증을 검증해 진행한다.
