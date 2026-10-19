# TOSS 반복 인증 오류와 코인 수집기 간헐 중단 수정

서버 `49.50.141.106`, 2026-10-07 KST. 조사 범위는 10월 6일과 7일 배포 전까지의 로그다. 클라우드 IP 허용 목록 적용 후 SSH 접속이 복구되어 조사·수정을 진행했다.

TOSS 인증 복구, 코인 발행 통계의 메모리 누적 수정, 정상 WebSocket 종료 분류, Market 파일 병합의 메모리 사용 감소 및 보관 후보 조회 최적화를 배포했다. 저장 자료·NATS 스트림·durable consumer를 purge하지 않았고, BTC/ETH/SOL 외 기존 자료를 삭제하지 않았다. 주문 제출은 하지 않았다.

## TOSS: 캐시된 토큰이 거절되어도 재사용

로그에서 `/api/v1/candles`가 `401 invalid-token`을 반환한 뒤 WebSocket handshake도 401로 반복 거절됐다. 기존 코드는 캐시의 만료 시각만 확인하고, 서버가 토큰을 거절해도 캐시를 무효화하거나 새 토큰을 발급하지 않았다. 결과적으로 재시도할 때도 같은 토큰을 사용했다.

확인한 공백 원장:

- 10월 6일 **20:38:02** 시작, 약 6,802초 뒤 종료한 인증 오류 공백.
- 10월 7일 **01:39:19** 시작한 인증 오류 공백은 이번 배포 후 **03:09:06**에 runtime의 실제 발행·readiness 확인으로 종료됐다. 과거 종료 시각을 임의로 추정하지 않았다.

TOSS는 클라이언트당 유효한 토큰을 하나만 허용하며, 새 토큰을 발급하면 이전 토큰을 즉시 무효화한다. 따라서 로컬 캐시의 만료 시간이 남아 있어도 401이 발생할 수 있다. 어떤 외부 호출이 토큰을 무효화했는지는 이번 서버 로그로 특정하지 못했다. [TOSS 공식 OAuth 명세](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json).

수정 내용:

- REST **GET**에서 401이 발생하면 거절된 캐시를 갱신하고 **한 번만** 재시도한다.
- WebSocket handshake 401도 토큰 갱신 후 한 번 재접속한다.
- 같은 클라이언트의 동시 갱신은 잠금으로 직렬화하고, 이미 더 새 토큰이 캐시에 있으면 재사용한다.
- 새 토큰도 거절되면 오류를 그대로 알린다. 403·서버 오류를 인증 성공으로 처리하지 않는다.
- 주문 POST를 자동 재전송하지 않는다.

배포 후 TOSS가 실제 데이터를 발행했다. 최근 저장 파일 **16개**의 SHA256·행 수를 검증했고, **SPY·QQQ의 `order_book`, `trade`, `bar_1m`**을 확인했다. 발행 health만으로 정상 저장을 판정하지 않았다. [저장 검증 영수증](evidence/toss-storage-verification-20261007.json).

## 코인: 메모리 종료와 외부 연결 종료가 섞여 있었음

배포 전 Docker restart count는 Binance 2, Bybit 5, Bitget 3, Arcus 15였다. 커널 OOM 로그의 container ID를 대조해 이 수집 프로세스들의 메모리 제한에 따른 강제 종료를 확인했다. 현재 cgroup의 OOM 카운터만 읽으면 재시작 이전의 종료를 놓칠 수 있다.

직접적인 코드 결함은 `NatsJetStreamBus._crypto_publish_sizes`였다. 이벤트를 발행할 때마다 `(시각, 크기)`를 추가했지만, 오래된 항목 제거는 발행률 getter를 읽을 때만 실행됐다. 정상 NATS probe 경로에서는 getter를 읽지 않으므로 수백만 개의 항목이 누적되어 256MiB 제한을 초과할 수 있었다. 발행 속도가 빠른 Arcus에서 특히 재시작이 많았던 기록과 일치한다.

수정 후에는 발행할 때마다 오래된 통계를 제거하고, **초별 합계 최대 61개**만 유지한다. 캐시된 NATS 용량을 추정할 때는 가장 오래된 부분 초를 포함해 보수적으로 계산한다. 원본 시장 이벤트·정밀도·발행 순서·ACK 규칙은 바꾸지 않았다. 180,000회 갱신 중 발행률을 한 번도 읽지 않는 테스트로 항목 수의 상한을 검증했다.

별도의 연결 종료도 있었다:

| 기록 | 해석과 처리 |
|---|---|
| GRVT 10/6 14:40, 16:49의 1001 `CloudFlare WebSocket proxy restarting` | 서버 프록시 재시작에 따른 정상 연결 종료. 새 연결과 호가 snapshot이 필요. `ConnectionClosedOK`를 재연결 가능한 일시 종료로 분류하도록 수정 |
| GRVT·Bybit·Arcus `no close frame received` | 원격/네트워크 연결이 close handshake 없이 끊긴 기록. 기존 재연결 경로 유지 |
| Arcus 18:28, 22:29, 22:37, 22:54의 ping timeout | 정해진 시간 내 pong을 확인하지 못한 기록. 서버·네트워크 지연과 로컬 부하가 모두 원인이 될 수 있어, 로그만으로 매 건의 원격 원인을 확정할 수 없음 |

연결이 끊겼다는 사실과 공백 기록은 유지한다. 인증·정책 위반 같은 오류를 정상 종료로 바꾸지 않는다. 외부 서버의 재시작 자체를 없애는 수정은 아니며, 재접속 후 새 호가 기준을 확보하는 동작을 유지한다.

이번 원장 조사에서 마지막 `nats_stream_capacity` 시작 시각은 **10월 5일 23:16:34 KST**였다. 10월 6일 배포 이후 이번 조사 범위에서 새 capacity 공백은 확인되지 않았다. 이번의 반복 프로세스 종료를 이전 용량 오류와 같은 원인으로 판단하지 않았다.

NATS의 별도 `Slow Consumer` 로그에 나온 `172.18.0.10`은 monitor bridge였으며, durable 저장 consumer의 주소는 아니었다. 이 로그만으로 수집 원장이 저장되지 않았다고 판단하지 않았다.

## Market 보관 작업: 메모리 사용과 조회 비용 감소

Market maintenance는 배포 전 restart count **78**이었고 커널 로그에서도 반복 OOM 종료를 확인했다. 기존 파일 병합 코드는 압축된 호가를 4,096행씩 Arrow/Python 객체로 펼쳤다. 압축 파일 크기가 작아도 중첩 호가와 raw payload의 메모리 사용은 클 수 있다.

동일한 중첩 호가 **4,096행**을 이용해 운영 데이터와 격리된 컨테이너에서 검증했다.

| 코드 | 512MiB 제한 테스트 결과 |
|---|---|
| 이전 운영 이미지 | exit 137, `OOMKilled=true` |
| 수정 이미지 | 정상 완료, peak RSS **159.1MiB**, 4,096행과 기대 논리 SHA256 일치 |

읽기와 검증 배치를 배포 설정에서 **64행**으로 줄였고, writer의 dictionary 사용을 해제했다. 목표 입력 크기는 16MiB, 최대 입력은 32MiB로 줄였다. 모든 행·호가·raw payload를 유지하고 병합 전후 논리 체크섬을 대조한다. [빌드 및 비교 테스트 영수증](evidence/collector-recovery-build-20261007.json).

보관 후보 조회에서도 3초 SELECT 제한이 반복되고 있었다. 원격-only 이력을 제외한 업로드 대기·병합 대기 파일의 partial index를 추가했다. 10,000개의 원격 이력이 있는 테스트에서 로컬 후보 조회의 VM 작업량을 검증했고, 업로드 재시도 시각 조건도 유지했다.

배포 후 운영 Market 보관 작업이 파일 32개를 업로드했고, 실제 파일 병합도 다음과 같이 완료했다:

- Binance 1분봉: 파일 **544개 → 병합 파일 1개**, **3,981행**, 03:11:27 KST 완료.
- TOSS 1분봉: 파일 **149개 → 병합 파일 1개**, **2,721행**, 03:12:33 KST 완료.

로컬 파일 회수는 기존 원격 검증·보관 정책을 따른 캐시 관리다. 원격 검증 없는 논리 데이터셋 삭제 또는 원격 객체 삭제를 수행하지 않았다.

## 배포와 검증 범위

관련 테스트 **로컬 95개 및 실제 Linux 배포 이미지 95개 통과**. 토큰 갱신의 직렬화·재시도 상한·POST 비재전송·WebSocket 401/403 분리, 통계 메모리 상한, 정상/정책 위반 close 분리, 원장·ACK·호가 처리와 보관 조회를 포함한다.

- revision: `collector-recovery-16d0cce7638345bb`
- 이미지: `sha256:279ad22189ef35848f19f980143e9597debea5410500ad9aae5cf8ae4be4d83e`
- 릴리스: `/opt/vibetrading/releases/collector-recovery-20261007/`
- 변경한 서비스: Market collectors, 코인 거래소 collector 5개, Market maintenance.
- Crypto storage·Crypto maintenance·Market storage·NATS는 재시작하지 않았다.
- 기존 systemd에서 사용하는 compose 설정의 이미지 pin을 갱신해 재부팅 시에도 수정본을 사용한다. 이전 설정은 새 릴리스 디렉터리에 `.before`로 보존했다.

[배포 영수증](evidence/collector-recovery-activation-20261007.json), [배포 전 진단](evidence/collector-diagnosis-20261007.json), [배포 전 소스 SHA](evidence/collector-source-baseline-20261007.json).

**03:09:44–03:15:45 KST, 360.88초·13개 표본**에서 다음을 확인했다.

- 첫 표본의 Arcus 초기 readiness 이후 TOSS·5개 코인 수집기 모두 `publishing`. 마지막 표본의 관련 컨테이너 모두 healthy.
- 새 공백 기록 **0개**, 배포한 프로세스 재시작 **0회**, OOM kill **0회**.
- CRYPTO_DATA 새 이벤트 **271,587개**, 최대 점유 **14,900,969바이트 / 512MiB = 약 2.8%**.
- 마지막 Crypto durable consumer의 undelivered **5개**, pending ACK **5,162개**, redelivery **0개**. 이는 해당 순간의 정상 배치 버퍼를 포함하며, 모든 pending ACK가 0이었다는 주장은 하지 않는다.
- 코인 수집기별 cgroup 메모리 표본은 **39.5–75.5MiB**. 단기 메시지 처리 peak를 포함한다.
- Market maintenance의 cgroup 메모리는 파일 캐시를 포함해 상한에 가까운 표본도 있었다. 격리 비교의 RSS 159.1MiB를 운영 전체 프로세스 사용량으로 해석하지 않는다. 실제 운영 병합·업로드와 재시작/OOM 여부를 함께 확인했다.

[검증 표본](evidence/collector-validation-20261007.json), [공백 원장 결과](evidence/collector-validation-summary-20261007.json), [수치 요약](evidence/collector-validation-derived-20261007.json).

배포 중 수집기 재연결에는 짧은 수집 중단이 발생한다. 기존에 받지 못한 실시간 호가·체결을 이번 수정으로 모두 복원했다고 주장하지 않는다. 커널이 프로세스를 종료한 구간은 정상 종료 callback이 실행되지 않으므로 gap 원장만으로 모든 공백을 파악할 수 없다. CPU 2코어·RAM 약 4GB 제약은 그대로이며, 새벽 검증을 국내 장중 최대 부하 검증으로 확대 해석하지 않는다.
