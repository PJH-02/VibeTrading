# 코인 수집·Bybit testnet·시장 데이터 적용 현황

## 현재 활성화 범위

- `config/crypto_runtime.yaml`은 Binance, Bitget, Bybit의 BTCUSDT·ETHUSDT·SOLUSDT USDT 무기한 선물 공개 피드만 지정한다. 호가 snapshot/delta는 전체 레벨과 공급자 원문 프레임을 함께 보존한다. 전략용 복사본에는 원문을 제외한다. 체결은 WebSocket 이벤트로 수집한다. 펀딩비·OI는 거래소 관측 시각과 native 단위를 보존하고 WebSocket ticker의 반복값은 최대 1Hz로 관측한다. Binance OI만 공식 공개 REST를 5초 간격으로 조회한다.
- `CRYPTO_DATA` JetStream WorkQueue와 `crypto-parquet-storage` durable을 기존 `MARKET_DATA`에서 분리했다. JetStream 수락 뒤 전략용 `live.crypto.>` Core NATS 복사본을 보내며, Parquet commit은 독립적으로 진행한다. 저장이 뒤처지면 collector를 중지하고 `state/collection_gaps.sqlite3`에 누락 구간을 남긴다.
- 코인 서비스는 Compose `crypto` 프로필에 있으며 기본 기동에는 포함되지 않는다. 코인 수집·저장·유지보수는 별도 프로세스와 `/data/crypto_state`, `/data/crypto_market_data` 경로를 쓴다. 코인 프로필은 Ncloud archive 설정이 없으면 시작하지 않는다. 실거래는 `CRYPTO_LIVE_ENABLED` 기본 거짓이며 testnet probe 명령에서도 실거래 제출이 불가능하다.
- 전략 실행은 `crypto-strategies` 역할과 Compose 프로필로 수집기에서 분리했다. `config/crypto_runtime.yaml`의 전략 목록이 비어 있어 기본 기동되지 않으며, 구성된 경우에도 신호만 발행한다. 자동 실거래 주문 연결은 maker/taker testnet 체결 검증과 운영 용량 게이트 뒤에 진행한다.
- 기존 KRX 주간 세션에는 XKRX 거래소 휴장일을, 미국 주식 세션에는 XNYS 정규장 및 조기 마감·서머타임을 적용했다. 미국 선물 세션은 CMES 달력과 17:00~18:00 뉴욕 시간 유지보수 휴장을 따른다. 해당 데이터 피드의 무료 권한 미확인 상태는 계속 비활성이다.
- 기존 KIS 국내주식 투자자 수치를 선물 외인으로 저장하던 `kis-flow` 작업을 제거했다. KIS 유료 CME 실시간 경로도 실행 단계에서 차단했다. `python main.py market-source-status`로 미확인 데이터 항목의 비활성 상태를 확인한다.

## 무료 API 및 출처 게이트

| 데이터 | 처리 | 근거 |
|---|---|---|
| Binance USDT 선물 | 공개 WebSocket과 공개 OI REST | [Binance WS 라우팅](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice), [호가 동기화](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly) |
| Bitget USDT 선물 | 공개 v3 WS `books`, `publicTrade`, `ticker` | [공개 접속 주소](https://www.bitget.com/docs/uta/quick-start), [호가 순서](https://www.bitget.com/docs/uta/websocket/public/Order-Book-Channel), [체결](https://www.bitget.com/docs/uta/websocket/public/New-Trades-Channel) |
| Bybit USDT 선물 | 공개 linear WS `orderbook.50`, `publicTrade`, `tickers` | [접속 주소](https://bybit-exchange.github.io/docs/v5/ws/connect), [호가](https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook) |
| CME ES/NQ | **비활성**. 무료 자동 수집 API 미확인 | [KIS 예제: 유료 시세 신청 필요](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/overseas_futureoption/overseas_futureoption_functions_ws.py), [CME 지연 호가](https://www.cmegroup.com/market-data/browse-data/delayed-quotes.html) |
| 코스피200 선물 외인 장후 확정 | **비활성**. KRX 공식 공개 API의 해당 필드 미확인 | [KRX 공개 API 서비스 목록](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd) |
| 코스피200 선물 장중 외인·야간선물, Nasdaq Composite·S&P 500 현물 | **비활성**. 선물 전용 무료 경로·계정 권한·정식 심볼 미확인 | 각 공급자 공식 명세 검증 필요 |

공개 거래소 피드는 별도 시세 요금 신청 경로를 사용하지 않는다. 거래소 약관과 네트워크 차단 여부는 운영 계정에서 다시 확인해야 한다. CME 화면의 무료 지연 호가는 자동 수집 API 권한으로 간주하지 않는다. 유료 경로를 환경 변수만으로 우회 활성화하는 스위치는 없다.

## Bybit testnet 체결 검증

Ncloud 서버의 공개 호가 발생→애플리케이션 수신→의사결정→주문→실제 체결 단발성 계측은 [BYBIT_TESTNET_LATENCY_2026-09-26.md](BYBIT_TESTNET_LATENCY_2026-09-26.md)에 기록했다. Best ask 매수는 taker `IOC`이며, maker `PostOnly` 매수는 best bid를 쓴다. 거래소의 실제 네트워크 송신 시각은 공개 피드에서 확인할 수 없다.

`BYBIT_TESTNET_API_KEY`, `BYBIT_TESTNET_API_SECRET`가 유효한 서버에서 아래 명령을 **한 건씩** 실행한다. `--max-notional`은 주문당 USDT 한도이며, 실제 종목의 최소 주문수량과 가격 단위는 제출 전 API에서 검사한다. 기존 포지션이나 이전 미확정 주문이 있으면 제출을 중단한다.

```bash
python main.py bybit-testnet-probe --symbol SOLUSDT --side Buy --mode taker --qty 0.1 --max-notional 25 --timeout-sec 30
python main.py bybit-testnet-probe --symbol SOLUSDT --side Buy --mode maker --qty 0.1 --max-notional 25 --timeout-sec 120
python main.py bybit-testnet-probe --symbol SOLUSDT --side Buy --mode maker --qty 0.1 --max-notional 25 --timeout-sec 180 --market-trigger
python main.py bybit-testnet-probe --symbol SOLUSDT --side Sell --mode taker --qty 0.1 --max-notional 25 --timeout-sec 30 --reduce-only
python -m scripts.bybit_testnet_status
python main.py bybit-kill-switch on
```

Maker 성공은 `PostOnly` 주문의 private order `New` 관측 후 private execution의 `isMaker=true` 실제 체결이 필요하다. Taker 성공은 `IOC`의 private execution `isMaker=false` 실제 체결이 필요하다. ACK는 접수로만 기록한다. 결정·전송·ACK·장부 게시·첫 체결·최종 체결은 로컬 단조 시계로 원장에 기록하고, 성공 체결의 단계별 p50/p95/p99 및 실패 상태별 건수를 보고한다. 장부에 올라간 maker 주문이 정해진 시간 안에 체결되지 않으면 취소를 제출하고 성공 표본에서 제외한다. 주문 원장은 `state/bybit_testnet/orders.sqlite3`, 실거래 어댑터 원장은 `state/bybit_live/orders.sqlite3`로 분리한다. [Bybit 주문 ACK 의미](https://bybit-exchange.github.io/docs/v5/order/create-order), [private execution `isMaker`](https://bybit-exchange.github.io/docs/v5/websocket/private/execution).

2026-09-26 수정된 testnet 키로 SOLUSDT 0.1계약 실제 체결을 확인했다. 첫 taker `IOC` 매수는 `isMaker=false`, 결정→ACK 1,485ms, 결정→첫/최종 체결 1,656ms였다. Maker `PostOnly` 매수는 private order `New`를 결정 후 719ms에 관측하고, `isMaker=true` 체결을 56,031ms에 관측했다. 두 매수 포지션은 각각 `reduceOnly` taker 매도로 청산했다. 테스트 종료 시 거래소 조회에서 SOLUSDT 잔여 포지션·미체결 주문이 모두 0건이었다.

감사 원장의 성공 표본은 maker 1건, taker 3건(검증 매수 1건과 포지션 청산 2건)이다. Maker 결정→최종 체결 p50/p95/p99는 모두 56,031ms(`n=1`), taker는 각각 1,172/1,607.6/1,646.32ms(`n=3`)다. 표본 수가 적어 운영 지연 분포나 실거래 보장치로 해석하지 않는다. 주문 ACK와 실제 체결은 별도 단계로 기록한다. Bybit가 한 차례 인증 시각 오류 `10002`를 반환해 공개 서버 시각으로 REST·private WS 인증 시각을 보정하고 요청 유효 시간을 10초로 조정했다. [Bybit 인증 시간창](https://bybit-exchange.github.io/docs/v5/guide), [서버 시각 API](https://bybit-exchange.github.io/docs/v5/market/time).

## 저장 용량과 운영 게이트

기존 저장 기본값은 2,048건 강제 flush이고, Python 객체·Arrow·ACK 핸들의 실제 RSS/cgroup 상한을 보장하지 않는다. 코인 worker는 16,384건·64MiB·60초 및 RSS/cgroup 압박을 조건으로 flush한다. 코인 원본 로컬 목표 보존은 24시간, Ncloud 원격 목표는 90일이다. 원격 90일 삭제 정책은 현재 코드에서 자동 적용되지 않으며 Ncloud Object Storage lifecycle 설정과 비용 확인이 필요하다.

공개 피드 5초 smoke 점검은 세 거래소 모두 성공했지만 운영 피크와 Parquet 처리량 검증을 대체하지 않는다. `crypto` 프로필의 추가 컨테이너 메모리 한도는 768MiB(collector)+1GiB(storage)+512MiB(maintenance)로, 기존 컨테이너까지 합치면 과거 문서의 4GiB 서버 범위를 넘을 수 있다. 실제 서버의 cgroup/RSS, 디스크, NATS 적체, 원장 증가, 압축량과 업로드 속도를 아래 명령으로 수집한다.

2026-09-26 호가 원문 필드 추가 후 각각 5초 공개 피드 계측에서 JSON 이벤트 바이트는 Bybit 약 0.57MB, Bitget 약 1.28MB, Binance 약 1.16MB였다. 합계 약 0.60MB/s, 단순 24시간 환산 약 52GB의 **압축 전 이벤트 데이터**다. 짧은 표본이며 운영 피크가 아니므로 서버 용량 확정에 사용할 수 없다. 앞선 Bybit 3초 캡처를 240건/s로 5초 재생한 별도 Parquet writer 테스트는 1,197건, 최대 RSS 97.8MB, 최대 flush 93ms였으나 원문 필드 추가 전 결과이며 NATS·업로드·3개 거래소를 포함하지 않는다.

```bash
python -m scripts.smoke_crypto_feeds --venue binance --duration-sec 5
python -m scripts.smoke_crypto_feeds --venue bitget --duration-sec 5
python -m scripts.smoke_crypto_feeds --venue bybit --duration-sec 5
python -m scripts.smoke_crypto_feeds --venue bybit --duration-sec 300 --capture-path runtime_data/bybit_sample.jsonl
python -m scripts.replay_storage_capacity --input runtime_data/bybit_sample.jsonl --observed-events-per-sec <실측_피크_초당_건수> --multiplier 2 --duration-sec 600 --output-root runtime_data/replay_check
python -m scripts.measure_pipeline_capacity --duration-sec 172800 --interval-sec 60 > capacity.jsonl
docker compose --env-file deploy/compose.env --profile crypto config --quiet
```

피크 유입의 2배를 재생한 결과와 48시간 관찰에서 OOM, 기록 없는 폐기, 지속 증가하는 NATS 적체가 없어야 한다. 원격 업로드 처리량이 실제 Parquet 생성량보다 커야 하며 24시간 로컬 보존 중 디스크 여유가 유지돼야 한다. 이 수치가 확인되기 전에는 `docker compose --profile crypto up`을 운영 승인 단계로 취급한다. 한국·미국 시장의 비활성 항목은 각각 공식 무료 API 명세와 권한을 검증한 뒤 별도 collector로 추가한다.
