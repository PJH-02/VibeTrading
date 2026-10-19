# 코인 선물 수집기 서버 기동 기록 (2026-09-26)

- 서버: `49.50.141.106`, 배포: `/opt/vibetrading/releases/crypto-20260926`, 이미지: `vibetrading-crypto:20260926`.
- 실행 서비스: `vibetrading-crypto-collectors-1`, `vibetrading-crypto-storage-1`, `vibetrading-crypto-maintenance-1`. 기존 수집·저장·텔레그램 서비스는 유지했다. 전략 및 실거래 서비스는 시작하지 않았다.
- 세 거래소의 BTCUSDT·ETHUSDT·SOLUSDT USDT 무기한 선물 WebSocket 호가·체결과 펀딩비·OI를 `CRYPTO_DATA` → 코인 Parquet 경로로 저장한다. 초기 Parquet에서 거래소 × 데이터 종류 12개 조합 모두 세 종목을 확인했다.
- NATS 코인 스트림은 128MiB, 코인 저장 컨테이너 한도는 768MiB다. 서버 데이터 디스크는 20GiB 중 기동 시 약 8GiB가 남아 있었으므로 코인 파일 업로드 대기는 5분, 로컬 보존 설정은 2시간으로 제한했다. 디스크 사용률 60% 이상에서는 검증된 파일을 더 일찍 원격 업로드·정리할 수 있다.
- Ncloud 보관 서비스의 상태 원장 백업을 확인했고, 코인 Parquet 파일 71개의 업로드·원격 검증을 1회 수행했다. Object Storage의 90일 lifecycle 삭제는 별도 설정 여부 확인이 필요하다.
- 초기 약 6분 점검에서 코인 서비스 3개 모두 Docker healthy, 코인 NATS 적체는 27.7MB에서 7.5MB로 감소, 호스트 가용 메모리는 약 0.9GiB였다. 장기간 안정성·피크 용량을 이 결과로 판단하지 않는다.

## 48시간 후 자료

서버에서 다음 파일을 가져와 성능을 검토한다. 1분 간격 지표 기록기는 `vibetrading-crypto-capacity-20260926.service`로 시작했으며 약 48시간 후 종료한다.

`/srv/vibetrading-data/runtime_data/reports/crypto_capacity_20260926.jsonl`

추가로 `docker logs --since 48h`의 `vibetrading-crypto-collectors-1`, `vibetrading-crypto-storage-1`, `vibetrading-crypto-maintenance-1` 로그와 `docker inspect`의 재시작·OOM 상태를 확인한다. Docker 로그는 회전할 수 있으므로 JSONL이 메모리·NATS 적체·디스크 추세의 주 자료다.
