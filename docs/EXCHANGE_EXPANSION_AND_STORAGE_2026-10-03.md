# 거래소 확장과 스토리지 재배치 — 2026-10-03

Arcus·GRVT 공개 수집, WebSocket 주문 제출 어댑터, Hyperliquid 복구 수정 및 300GB 볼륨 이전을 운영 서버 `49.50.141.106`에 적용했다. 5개 코인 수집기와 기존 Market 수집기는 정상 발행 중이며 실제 Parquet 읽기로 저장을 확인했다. 실제 주문은 제출하지 않았다.

## 수집과 주문 통합

대상은 BTCUSDT·ETHUSDT·SOLUSDT만이다. 신규 거래소 모두 호가 원본·체결·펀딩·미결제약정을 기존 CRYPTO_DATA → 영속 저장 파이프라인으로 보낸다. 호가는 이벤트 기반으로 수집하고 펀딩·미결제약정은 제공 주기에 맞춰 수집한다. 거래소 자체의 피드 속도·호가 깊이·시간 정보 차이는 보존한다.

| 거래소 | 네이티브 계약 | 실제 표시 통화 | 확인 |
|---|---|---|---|
| Arcus | BTC-USD / ETH-USD / SOL-USD | USD | 4종 데이터 저장·읽기 성공 |
| GRVT | BTC_USDT_Perp / ETH_USDT_Perp / SOL_USDT_Perp | USDT | 4종 데이터 저장·읽기 성공 |

Arcus의 공통 식별자는 BTCUSDT 등으로 맞췄지만 `instrument_currency=USD`와 원본 계약명을 보존한다. USD를 USDT로 환산하거나 같은 가격으로 가정하지 않는다. Arcus 체결 원본에 없는 공격 방향·일부 호가의 없는 거래소 시각을 만들어 넣지 않는다. GRVT 계약명의 대소문자와 나노초 시각을 보존한다.

수집 코드는 `vibetrading/providers/extended_perp.py`, 주문 코드는 `vibetrading/extended_brokers.py`다. CLI·브로커 팩토리·명시적 데이터 정책·헬스체크·배포 프로필·릴리스 빌더에 두 거래소를 연결했다. Arcus 스냅샷 경계 시퀀스와 GRVT 구독 ACK의 시작 시퀀스를 실제 피드에 맞춰 처리하고 중간 누락은 DataGap으로 알린다.

Arcus 주문은 Ed25519 typed canonical payload, GRVT 주문은 EIP-712로 서명한다. 수량·가격 단위를 검증하고 잘못된 틱을 자동 반올림하지 않는다. 연결은 재사용하고 요청별 응답 ID를 확인한다. 전송 후 타임아웃은 결과 불명으로 반환하며 자동 재주문하지 않는다. 클라이언트 주문 ID로 결과를 조회해야 한다.

두 어댑터 모두 `LIVE_ORDER_ENABLED`와 거래소별 `*_ORDER_SUBMIT_ENABLED`가 함께 켜져야 제출할 수 있다. 이번 검증에서는 둘 다 꺼져 있으며 실제 제출은 0회다. 주문의 거래소 접수·체결까지 검증됐다는 의미는 아니다.

## 인증 확인

GRVT IP 허용 목록 반영 후 운영 `/etc/vibetrading/app.env`가 원본 `/root/vibetrading/.env`와 다른 키를 사용한다는 점을 확인했다. 검증된 원본의 GRVT 메인넷·테스트넷 키, 서명키, 주소, 계정 ID를 운영 설정으로 동기화했다. 비밀값은 로그·증빙·이 문서에 기록하지 않았다.

- GRVT 메인넷·테스트넷: 로그인, 서명키와 주소 일치, 주문용 WebSocket 포지션 조회 모두 성공.
- 두 거래소의 BTC·ETH·SOL: 실제 거래소의 계약 메타데이터를 사용한 주문 서명 생성 성공. 서명된 주문은 전송하지 않음.
- Arcus: 사용자 제공 공개 주소를 반영했고 API 키 등록 및 Ed25519 키 쌍 일치를 확인했다. 계정 조회는 HTTP 404다. 계정 활성화·입금 상태 확인이 남아 있다. 공개 수집은 정상이다.

인증 방식은 [GRVT 공식 문서](https://api-docs.grvt.io/auth/), Arcus 프로토콜은 [공식 WebSocket 문서](https://docs.arcus.xyz/api-reference/websocket)를 기준으로 구현했다.

## 장애 원인과 수정

Hyperliquid의 2026-10-03 18:10 KST 로그에서 REST HTTP 502가 정상 WebSocket까지 종료시키는 경로를 확인했다. 일시적 REST 429·5xx·연결·타임아웃은 다음 제한된 폴링 주기에 재시도하고 WebSocket 수집은 유지한다. 인증·정책·영속 저장 오류는 계속 상위로 전달한다. 정상적인 WebSocket 종료도 재연결되도록 태스크 완료 조건을 수정했다.

복구 확인 코드가 Market 수집기의 상태를 CRYPTO_DATA로 검사해 계속 recovering으로 남기는 문제도 수정했다. Hyperliquid와 기존 Binance Market 수집기는 실제 MARKET_DATA로 검사한다.

과거 코인 수집기에는 대형 WebSocket 큐로 컨테이너 메모리 제한을 넘을 수 있는 설정이 있었다. Binance의 512개 큐와 Bybit·Bitget의 128개 큐를 포함해 수신 큐를 8개, 프레임 최대 크기를 2MiB로 제한했다. 발행 버퍼는 64MiB에서 16MiB로 줄이고 역압을 유지했다. 이번 수정 후 관측에서는 OOM·컨테이너 재시작이 없다. 이전 VM 전체 정지의 단일 원인을 확정한 것은 아니다.

## 볼륨 배치

| 할당 | 역할 | 확인된 사용량 / 여유 |
|---|---|---|
| 기존 10GB 루트 | OS·SSH·서비스 정의·릴리스 파일 | 5.6GiB / 3.7GiB, 61% |
| 기존 20GB | NATS·Docker·기존 Market 데이터/상태 | 약 14GiB / 5.3GiB, 72% |
| 신규 300GB | 코인 Parquet·코인 SQLite/WAL·이전 원본 보관·4GiB 비상 swap | 약 7.8GiB / 284GiB, 3% |

신규 볼륨은 `/dev/vdc`, ext4, noatime, 예약 공간 1%이며 `/srv/vibetrading-capacity`에 UUID로 영구 마운트했다. 기존 경로를 유지하는 bind mount로 코인 상태와 Parquet을 신규 볼륨에 연결했다. 마운트가 없으면 서비스가 루트 디스크에 쓰지 않도록 systemd 마운트 의존성과 조건을 추가했다. fstab 검증 오류는 0개다. 실제 재부팅 검증은 수행하지 않았다.

서비스를 중지한 상태에서 원본과 복사본의 모든 파일 SHA-256 및 SQLite 무결성을 확인했다. 원본 보관본도 300GB로 검증 이동해 루트 약 1.9GiB를 확보했다. 보관본은 `/srv/vibetrading-capacity/migration-retained-20261003`에 있다. 논리적 수집 데이터를 삭제하지 않았다. 삭제했던 과거 코인 세대를 복원하지 않았다.

NATS는 20GB, 코인 데이터/상태는 300GB 각각의 여유 공간을 별도로 검사한다. 큰 볼륨에 공간이 있어도 작은 NATS 볼륨이 부족하면 일시 중지한다. 데이터 4GiB, 상태 1GiB, NATS 2GiB 예약 및 재개 여유를 적용했다. WAL FULL 동기화와 기존 영속성 정책은 유지했다.

수집기는 각각 256MiB, 코인 저장기는 1GiB, 코인 유지보수는 384MiB로 제한했다. SQLite 캐시는 저장기 64MiB·유지보수 32MiB로 줄였다. Arrow/OMP 스레드는 1개로 설정했다. 비상 swap은 4GiB, swappiness는 10이다. swap은 RAM 확장이나 지속적인 처리 용량의 대체가 아니다.

SKHYUSDT·SKhynixUSDT 등 미지정 데이터는 삭제하지 않았다. 기존 Market 설정을 유지했고, 최신 Binance/Hyperliquid Parquet에서 SKHY·SKHYNIX·SAMSUNG 종목의 지속 저장을 읽기로 확인했다.

## 검증과 운영 부하

관련 로컬 테스트 53개와 실제 Linux 운영 이미지 테스트 50개가 통과했다. 새 테스트 18개는 서명 검증, 잘못된 단위 거절, 전송 결과 불명 시 재시도 금지, 실제 구독 시퀀스, 가격 통화 보존, 일시적 REST 장애 격리, 스트림별 복구 확인 및 분리된 디스크 가드를 검증한다.

23:28 KST의 40초 관측에서 코인 5개 거래소 합계 약 346건/초, Hyperliquid 약 8.5건/초였다. RAM 가용량은 926MiB, swap 사용은 29MiB/4095MiB였다. 메모리 PSI avg10은 0이었다. 디스크 busy는 루트 0.1%, 기존 20GB 16.8%, 신규 300GB 0.7%였다. 기존 20GB의 평균 IOPS는 약 514, 신규 300GB는 24였다. 서비스의 순간 작업에 따라 달라지는 측정값이다.

CRYPTO_DATA는 128MiB 한도 중 약 9.8MiB, 소비자의 미전달 대기 `num_pending=0`, 미확인 처리 중 4165개, 재전달 0개였다. 저장기의 flush/ACK가 진행되며 현재 수집 속도를 처리하고 있다. 5개 코인 수집기·Market 수집기·저장기·유지보수·NATS가 healthy이고 컨테이너 OOM·자동 재시작은 0회다.

23:32 KST 후속 40초 관측에서도 모두 healthy였다. 코인 수집량은 약 548건/초로 늘었지만 저장 ACK는 194,216건까지 진행했고 저장 버퍼 대기는 3,460건이었다. 디스크 busy는 루트 0.1%·20GB 4.5%·300GB 0.7%, RAM 가용량 859MiB, swap 32MiB였다. 23:35 KST SSH 재확인과 증빙 다운로드도 성공했다. 후속 관측은 `expansion-production-final-observation-20261003.json`에 보관했다.

현재 주요 자원 부담은 CPU 처리와 기존 Market/NATS가 공유하는 20GB의 I/O다. 당시 CPU PSI some avg60은 약 21%였다. 300GB를 붙였다는 이유만으로 피크 거래량·국내 시장 개장·장기간 데이터 증가까지 해결됐다고 판단하지 않는다. 이번 결과는 짧은 운영 관측이며 장기 용량 보장은 아니다.

## 릴리스와 증빙

운영 이미지: `sha256:3724b7738064ee488a2fd1a683c369845fa5f2db4f619ef091179135d15805d3`.

운영 릴리스: `/opt/vibetrading/releases/exchange-expansion-20261003`, revision `expansion-846bc0031a7521c9e092`. 이전 이미지·환경·서비스 설정과 데이터 보관본은 롤백용으로 유지했다. source manifest와 실행 이미지의 파일 해시를 대조했다. 운영 서비스 경로는 systemd에 반영했다.

증빙은 `docs/evidence`의 `expansion-release-20261003.json`, `tests-passed.json`, `auth-verification.json`, `expansion-storage-verification-20261003.json`, `expansion-operational-verification-20261003.json`, `expansion-production-observation-20261003.json`, `verified-trees.json`, `retained-trees.json`이다. API 키·서명키·세션 쿠키는 포함하지 않는다.
