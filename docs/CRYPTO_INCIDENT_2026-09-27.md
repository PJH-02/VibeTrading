# 코인 선물 수집 중단 및 복구 (2026-09-27)

## 영향

- Binance·Bitget·Bybit BTCUSDT, ETHUSDT, SOLUSDT USDT 무기한 선물 수집이 2026-09-26 20:40 KST 무렵부터 멈췄다.
- `collection_gaps.sqlite3` 기준 `storage_or_nats_backpressure` 구간은 Bitget·Bybit 2026-09-26 20:40:39부터 2026-09-27 10:47:04 KST, Binance 20:40:50부터 10:47:03 KST다. 중단된 약 14시간 6분의 WebSocket 원본은 재수집되지 않았다.
- 2026-09-27 10:47 KST에 세 수집기를 재시작했다. Binance SOLUSDT 초기 호가 스냅샷 연결 실패로 인한 약 2.5초의 추가 누락도 원장에 기록됐다. 이후 자동 재연결했다.

## 원인

1. 코인 저장 worker가 cgroup `memory.current`에 포함된 회수 가능한 파일 캐시를 작업 메모리로 간주했다. Parquet flush 후에도 90% 이상으로 판정해 반복 실패했고, NATS `CRYPTO_DATA`가 128MiB 한도의 80%인 약 108MB까지 쌓였다.
2. 공통 `.env`의 `STORAGE_RESUME_USED_RATIO=0.5`가 코인 수집기의 재개 조건에도 적용됐다. 당시 디스크 사용률이 약 60%여서 NATS 적체가 비워진 뒤에도 수집을 재개하지 못했다.
3. 코인 보관 worker는 약 1,000개 파일을 한 번에 압축하며 512MiB 컨테이너 한도를 넘어 OOM 종료됐다. 재시작 한도를 모두 사용하여 업로드도 중단됐다.
4. 기존 Telegram `/status`는 코인 저장·보관 상태를 반영하지 않아, 코인 수집이 멈췄어도 전체 서버를 정상으로 표시했다.

## 조치

- 저장 worker의 cgroup 사용량에서 `inactive_file`을 제외하여 회수 가능한 캐시를 압박으로 오인하지 않도록 했다. 별도 heartbeat를 추가해 유휴 시에도 건강 상태를 기록한다.
- 코인 수집기에만 디스크 재개 기준 65%를 적용했다. 실거래 서비스는 기동하지 않았다.
- 코인 보관 worker는 업로드를 재개하고 압축 작업을 임시 중지했다 (`COMPACTOR_JOBS_PER_CYCLE=0`). 압축을 다시 켜기 전에는 작업당 입력 파일 수·메모리 상한을 별도로 낮춰야 한다.
- Telegram `/status`에 코인 저장·보관 상태를 추가하고, 세 거래소 중 하나라도 세 종목을 수집하지 않으면 전체 상태를 `주의`로 표시한다.
- 코인 3개 서비스를 `vibetrading-crypto:20260927-fix` 이미지로 재생성했다. 기존 국내·미국 시장 서비스는 변경하지 않았다.

## 단기 검증

- 2026-09-27 10:54 KST에 코인 수집·저장·보관 컨테이너 3개 모두 Docker `healthy`, OOM 없음, 재시작 횟수 0.
- Binance·Bitget·Bybit 각 3개 종목의 최근 이벤트가 `/status`에 표시됐다.
- 저장 worker 누적 수신 121,153건, ACK 116,969건, flush 10회, Parquet 114개 파일. NATS 코인 스트림 약 11.8MB로 128MiB 한도 아래였다. 호스트 가용 메모리 약 1.61GiB, 데이터 디스크 여유 약 7.75GiB.
- 보관 worker가 재기동 후 512개 파일을 원격 검증했다. 당시 미업로드 활성 파일 11,382개는 순차 처리 중이며 삭제하지 않았다.
- 관련 테스트 20개 통과. 장기간 안정성은 아직 확인하지 않았다. 기존 48시간 지표 기록기는 계속 실행되며, 추후 해당 JSONL과 Docker 로그로 평가한다.

## 배포 위치

- 서버 이미지: `vibetrading-crypto:20260927-fix`
- 코드: `/opt/vibetrading/releases/crypto-20260927-fix`
- Compose 설정: `/opt/vibetrading/current/deploy/crypto-profile.yml` (이전 설정은 `.20260926.bak.yml`)
- 지표: `/srv/vibetrading-data/runtime_data/reports/crypto_capacity_20260926.jsonl`
