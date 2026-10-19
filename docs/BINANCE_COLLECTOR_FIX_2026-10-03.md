# Binance 수집기 복구 — 2026-10-03

2026-10-03 09:51 KST에 수정 이미지를 운영 서버에 배포했다. 10:04 KST 검증 완료 시 Binance는 `healthy` / `publishing`이고, BTCUSDT·ETHUSDT·SOLUSDT의 새 데이터가 실제 원격 Parquet 파일에 저장된 것을 확인했다.

## 확인된 원인

09:29 KST Binance의 중단 상태는 `paused`, 사유는 `state_disk_reserve`였다. 10GB 시스템/상태 디스크 여유가 약 1.11GiB로, 재개 기준인 1.25GiB보다 낮아 안전 정지가 유지됐다. 코인 SQLite 본체는 이전 조치 이후 1,953,497,088바이트로 동일했다. 시스템 프로그램, 저널과 패키지 캐시가 추가 공간을 사용했다. 당시 NATS 코인 스트림은 약 8.5%이고 미전달 메시지는 0이므로, 이번 중단의 직접 원인은 NATS 용량 초과가 아니다.

디스크 공간 확보 후 기존 이미지가 자동으로 수집을 재개했지만 09:41 KST에 `Binance SOLUSDT snapshot bridge failure`가 다시 기록됐다. 코드에서도 별개의 경계값 결함을 재현했다. Binance의 최초 호가 연결은 `U <= lastUpdateId <= u`이고, `u == lastUpdateId`인 프레임도 연결을 성립시킨다. 기존 코드는 이 프레임을 버려 다음 정상 프레임을 연결 실패로 판단할 수 있었다. 이후 `pu` 검사도 최초 프레임 여부 대신 현재 번호가 스냅샷 번호인지에 따라 생략됐다. [Binance 공식 호가 동기화 규칙](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly)

운영 오류 로그에는 해당 프레임의 U/u 값이 없으므로, 관측된 SOL 오류가 정확히 이 경계값에서 발생했는지까지 단정하지 않는다. 로그와 일치하는 실패 경로의 코드 결함은 회귀 시험으로 확인하고 수정했다.

## 적용한 조치

- 사용하지 않는 이전 NATS 복사본 3개와 이전 전환 백업을 압축 보관했다. 모든 파일의 내용 해시·크기·소유자·권한을 대조하고 원본이 변경되지 않았음을 확인한 뒤 이전 디렉터리에 복원 위치를 기록했다. 압축본과 영수증은 `/srv/vibetrading-data/admin-retained-20261003/`에 보존했다.
- 종료된 시스템 저널도 검증한 압축본으로 보존하고, journald 영구 로그 상한을 96MiB로 설정했다. 재생성 가능한 APT 인덱스 캐시 약 107MiB를 정리하고 해당 캐시 파일의 재생성을 제한했다. 설치된 프로그램은 유지했다.
- 시스템 디스크 여유는 약 1.11GiB에서 1.85GiB로 증가했다. 20GB 데이터 디스크 여유는 검증 시 약 4.67GiB다. 기존 디스크 안전 기준은 유지했다.
- `vibetrading/providers/crypto_perp.py`에서 최초 연결의 경계값을 인정하고, 이후 프레임의 연속성을 검사한다. 이미 스냅샷에 포함된 프레임과 중복 프레임은 원문을 보존하되 호가에 중복 적용하지 않는다. 실제 순서 단절은 계속 감지하여 재동기화한다.
- 기존 운영 이미지를 기반으로 해당 코드 파일 하나만 교체했다. Binance 컨테이너만 새 이미지로 재생성했고, 다른 9개 컨테이너는 유지했다. systemd가 사용하는 Compose에도 Binance 이미지와 빌드 정보를 반영했다.

배포 이미지: `sha256:218f2784bfec94e57e97bf3ecb2ee9bdb72b02f59d5e05a02fafc65f1530010c`

빌드 버전: `binance-bridge-21db868bd804ec4d`

실행 중인 수집 데이터, SKHYUSDT/SKhynixUSDT 등 다른 종목 데이터와 기존 다운로드용 내보내기 파일은 삭제하지 않았다. 기존 Market DB inode와 보호 대상 내보내기 파일의 inode·크기·수정 시각도 배포 전후 동일했다. 과거 삭제한 코인 세대를 복원하지 않았다.

## 검증

새 회귀 시험 5개 중 기존 코드에서 3개가 실패했고, 수정 후 모두 통과했다. 관련 파이프라인·상태 디스크 보호 시험을 포함한 15개가 로컬과 실제 서버 후보 이미지에서 각각 통과했다. 서버 시험 결과는 `15 passed in 33.20s`다.

배포 완료 13.12분 후 검증 결과:

| 항목 | 결과 |
|---|---|
| Binance | healthy, publishing; 발행 33,168건 |
| 새 컨테이너 재시작 / OOM | 0 / 없음 |
| 배포 후 로그 검사 | 오류 0, 경고 0, traceback 0 |
| 발행 실패 | 0 |
| 종목 상태 | BTCUSDT·ETHUSDT·SOLUSDT 모두 최근 데이터 수신 |
| 코인 저장기 | ready; 커밋과 ACK 진행 |
| 코인 NATS 스트림 | 약 8.42%; 미전달 0 |
| 원격 데이터 | 새 Parquet 8개 다운로드, SHA-256·크기 모두 일치 |
| 데이터 종류 | 세 종목 각각 거래·호가·펀딩·미결제약정 확인, 총 12개 조합 누락 없음 |

검증에 사용한 임시 다운로드 파일은 검사 후 제거했다. 데이터 파일은 공간 확보 정책에 따라 검증된 원격 보관소로 빠르게 이동하므로, 로컬 파일 유무만으로 수집 성공을 판단하지 않았다.

20GB 디스크의 I/O 가동률은 여전히 높은 상태다. 이번 Binance 정지는 시스템 디스크 여유 공간을 확보해 해소했고, 코인 SQLite의 10GB 디스크 분리와 원격 보관 처리는 계속 정상 동작한다. 이번 관측으로 장기 무중단을 보장하지는 않는다.

## 증거

- [중단 당시 상태](evidence/binance-paused-20261003.json)
- [공간 확보 및 압축 무결성 확인](evidence/binance-headroom-20261003.json)
- [배포 및 보호 데이터 확인](evidence/binance-deployment-20261003.json)
- [서버 회귀 시험](evidence/binance-bridge-server-tests-20261003.txt)
- [최종 상태와 원격 데이터 검증](evidence/binance-verified-20261003.json)

서버의 수정 릴리스와 배포 기록은 `/opt/vibetrading/releases/binance-bridge-fix-20261003/`에 있다. 서비스 시작 설정은 `/opt/vibetrading/releases/crypto-relief-final-20261002/deploy/crypto-r2.yml`을 계속 사용한다.
