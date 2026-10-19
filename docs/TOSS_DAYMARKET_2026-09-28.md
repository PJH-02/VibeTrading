# Toss SPY·QQQ 한국 장중 수집 점검 (2026-09-28)

## 확인된 데이터

- 2026-09-22 12:00~16:00 KST의 Ncloud 보관 Parquet 표본에 `TOSS:US:SPY` WebSocket 체결·호가가 있었다. `sequence`는 `trade:us:SPY`, `orderbook:us:SPY`였고, 체결 원본 시각과 서버 수신 시각 차이 중앙값은 표본 시간대별 약 0.39~1.12초였다. 일부 호가는 재연결 직후 REST 스냅샷이었다.
- 기존 토스 키로 `GET /api/v1/stocks?symbols=QQQ`가 활성 미국 ETF를 반환했다. `GET /api/v1/candles?symbol={SPY|QQQ}&interval=1m&before=2026-09-22T12:00:00+09:00`은 양쪽 모두 2026-09-22 10~12시 KST의 거래량 있는 봉을 반환했다. 16시 커서 조회도 두 종목의 장중 봉을 반환했다.
- 토스 WebSocket은 `trade:us:SPY`, `trade:us:QQQ`, `orderbook:us:SPY`, `orderbook:us:QQQ` 네 구독을 모두 승인했다. 승인 시점은 9월 28일 00시 KST로 데이마켓 밖이어서 QQQ의 실제 장중 WebSocket 프레임은 아직 관측하지 못했다.
- 공식 명세는 미국 체결 푸시 범위에 데이마켓을 포함하지만 시세 프레임은 `LOSSY`라고 명시한다. 따라서 체결 프레임 합산으로 거래량을 재구성하지 않고 토스 1분봉의 거래량을 사용한다. [체결 채널](https://developers.tossinvest.com/docs/trade), [시세·캔들 API](https://developers.tossinvest.com/docs/market-data).

## 비용·한도

- 기존 계정의 공식 API 키로 별도 신청 없이 두 종목 REST와 WebSocket 구독이 성공했다. 토스 공식 API 소개 페이지는 시세 조회를 기본 기능으로 설명하고 수수료 FAQ에는 기존 매매 수수료 적용을 안내한다. 별도의 API·시세 이용료는 공개 페이지에서 확인되지 않았다. **이 사실만으로 미래까지 100% 무료라고 보장할 수는 없다.** 토스는 기능·호출 한도·정책 변경 가능성을 명시한다. [공식 API 안내](https://corp.tossinvest.com/ko/open-api), [공식 API 명세·한도](https://developers.tossinvest.com/docs).
- 추가 유료 데이터 계약, ES/NQ 상품, 거래 주문은 사용하지 않는다. 추가되는 REST 호출은 SPY·QQQ 각 1회/분이며 공식 `MARKET_DATA_CHART` 초당 20회 한도보다 낮다. WebSocket 주제는 두 종목 × 체결·호가 = 4건으로 연결당 100건 한도보다 낮다.
- 추가 Parquet와 Ncloud 원격 보관량은 발생하지만 Ncloud 비용은 기존 사용자 허용 범위다.

## 적용

- `config/universe.yaml`에 `US:QQQ`를 추가하고 SPY의 잘못된 Nasdaq 대용 표시를 S&P 500 대용으로 수정했다.
- 토스 수집기가 완료된 1분봉만 분당 조회한다. 원본 봉 종료 시각과 실제 수신 시각을 기록하고, 24시간이 지난 봉은 새 수집으로 재전송하지 않는다. 안정적인 이벤트 ID로 재연결 중복을 제거한다.
- 시장 정책과 NATS 발행 정책 양쪽에서 토스 SPY·QQQ의 1분봉만 허용한다.
- 서버의 수집기 전용 이미지 `vibetrading-toss-daymarket:20260928`로 일반 수집기만 재시작했다. 코인·저장 컨테이너는 재시작하지 않았다. 미국 일요일 밤을 월요일 정규장으로 잘못 판정하던 서버 세션 조건도 수정했다.
- 운영 서버 디스크 가용량이 3.0GiB까지 줄어 있었으므로, 더 최신의 Ncloud SQLite 백업 객체와 해시 메타데이터를 확인한 뒤 원격 확인 목록에 없는 오래된 로컬 임시 백업 두 개(합계 3.52GB)를 정리했다. 가용량은 6.3GiB로 회복했다.

## 남은 실시간 확인

다음 미국 데이마켓이 열리면 `TOSS:US:QQQ` 체결·호가의 원본 시각, 서버 수신 시각, 1분봉 저장 건수와 종목별 누락을 확인한다. 이 확인 전까지 QQQ 장중 WebSocket 실수신은 검증 완료로 표시하지 않는다.
