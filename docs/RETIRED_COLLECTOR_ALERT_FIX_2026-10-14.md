# 중단한 거래소의 오류 알림 수정

2026-10-14 01:04 KST 확인. Bitget·Bybit 수집기는 2026-10-13 17:15 KST에
정상 종료했으며 Docker 상태는 `exited`, 재시작 정책은 `no`다.
감사 당시 새 Bitget/Bybit 수집 프로세스는 없었다.

수집 중단 뒤 알림 서비스의 범위 변경을 누락했다. 기존 Telegram 컨테이너에는
`CRYPTO_COLLECTOR_PROVIDERS`가 없었고, 오래된 health 코드는 그룹 파일의
Binance·Bitget·Bybit 구성을 그대로 읽었다. 중단한 두 worker의 약 8시간 전
heartbeat가 오래됐다는 이유로 응답 없음 알림을 생성했다.
Telegram 오류 알림·상태 표시는 두 중단 거래소가 코드에 고정된 상태였다.

현재 health·알림·상태 표시는 활성 provider인 Binance·Arcus·GRVT만 사용한다.
중단 provider의 lifecycle 오류 이벤트는 ACK 후 알림 없이 처리하며,
직접 오류·복구 알림 경로도 비활성 provider를 제외한다.
레거시 그룹 파일과 중단 worker의 stale heartbeat는 활성 그룹 판정에 들어가지 않는다.

서버의 Telegram bind source `health.py`, `telegram.py`를 백업 후 변경했다.
root Compose profile에도 활성 provider 3개를 명시했고, 기존 bot 이미지·자격증명·mount를
보존한 채 Telegram 컨테이너만 재생성했다. 수집기·저장기·ETF 서비스는 재시작하지 않았다.
Telegram 재생성용 private Compose에는 기존 환경이 포함되므로 mode 0600으로 보관한다.
자격증명과 private Compose는 이 로컬 증거 디렉터리에 복사하지 않는다.

검증 결과:

- 중단된 두 수집기의 종료 시각과 restart=no가 그대로 유지됨.
- 활성 Binance·Arcus·GRVT의 heartbeat가 정상이며 각각 BTC/ETH/SOL을 수신 중.
- Telegram status가 `서버 정상`, 서비스 3/3 정상, 활성 세 거래소만 표시함.
- 로컬 알림·세션 테스트 9개 통과. 서버에서는 변경한 알림 회귀 테스트 6개 통과.
- 테스트 fixture는 컨테이너 환경 변수와 실제 bot 자격증명이 섞이지 않도록 임시 디렉터리·가짜 토큰으로 고정함.

서버 영수증은 `/opt/vibetrading/releases/retired-alerts-20261014/receipt.json`,
검증 출력은 같은 디렉터리의 `verification.txt`에 있다.
원본 Telegram 이미지의 세션 캘린더 구현은 이번 중단 거래소 알림 수정 범위에 포함하지 않았다.
