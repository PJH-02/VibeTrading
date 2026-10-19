# 현재 서버 지연 측정 — 2026-10-01

## 완료 범위와 차단된 범위

Ncloud `49.50.141.106`에서 2026-10-01 17:03:41–17:04:11 KST, 운영 수집기의 Core NATS live feed를 30초간 읽었다. Binance/Bitget/Bybit 각 BTCUSDT·ETHUSDT·SOLUSDT의 체결과 호가 메시지 총 10,874건을 관측했다. 운영 수집기와 저장기는 수정하거나 재시작하지 않았다. 계측기는 종료했다.

**Bybit testnet 실제 주문·체결 측정은 미완료다.** 기존 컨테이너와 최신 `/etc/vibetrading/app.env`를 읽은 별도 컨테이너 모두 `GET https://api-testnet.bybit.com/v5/position/list`에서 `401 API key is invalid`를 받았다. 최신 파일과 운영 컨테이너의 testnet 키·secret 값은 일치함을 값 노출 없이 확인했다. 계정 안전 확인을 통과하지 못해 주문을 한 건도 제출하지 않았다. 서버 키 갱신 또는 다른 유효한 설정 파일 경로가 필요하다.

## 운영 수집 경로 실측

수집기가 이벤트에 찍는 `receive_monotonic_ns` → durable JetStream publish 승인 → Core NATS live 수신 → 이벤트 파싱과 시험용 `OrderIntent` 모델 생성 완료를 측정했다. 같은 서버의 단조 시계를 사용했다. 별도 계측기의 모델은 외부로 발행하지 않았다. 이는 **시험용 의도 생성이며 실제 전략·위험 검사·주문 라우터의 연산 시간은 포함하지 않는다.** 수집기 수신 시각은 공급자 파서에서 찍으므로 NIC 패킷 도착 시각과도 다르다.

단위 ms. 각 행은 세 종목의 호가·체결을 합친 분포다.

| 거래소 | 표본 | 수집기→live p50 / p95 / p99 | 수집기→시험용 의도 완료 p50 / p95 / p99 | 의도 모델 생성 p50 |
|---|---:|---:|---:|---:|
| Binance | 2,340 | 2.282 / 22.786 / 42.469 | 2.370 / 22.899 / 42.520 | 0.028 |
| Bitget | 2,525 | 2.273 / 16.577 / 34.281 | 2.336 / 16.680 / 35.642 | 0.026 |
| Bybit | 6,009 | 1.438 / 27.056 / 47.374 | 1.516 / 27.097 / 47.424 | 0.028 |

수신→시험용 의도 완료의 최대값은 Binance 81.101ms, Bitget 61.433ms, Bybit 72.432ms였다. 중앙값은 작지만 p99까지 약 35–47ms 늘어나므로 매 이벤트가 1–2ms 안에 전달된다고 해석하면 안 된다. 짧은 관측이며 운영 피크나 장시간 성능 보장은 아니다.

운영 이벤트의 `source_ts_ns`→수집기 wall clock 수신 차이 중앙값은 Binance 18.870ms, Bitget 24.779ms, Bybit 36.021ms다. **각 거래소 시계 오차를 보정하지 않은 이벤트 나이**이며 공급자 timestamp 의미와 호가/체결 주기도 달라 순수 네트워크 지연이나 거래소별 우열로 해석하지 않는다. 거래소별·종목별·이벤트 유형별 분포와 원시 계측값은 evidence JSON에 있다.

## Bybit testnet 공개 호가 구간

동시에 testnet `orderbook.1.SOLUSDT`를 읽었다. 공개 server-time API 7회 중 최소 RTT는 34.371ms, 추정 거래소 시계 offset은 +4ms였다. 거래소/서버 시각을 뺀 값의 불확실성은 최소 ±17.185ms이며 공급자 timestamp 오차는 별도다.

| 구간 | 신규 update 8개 p50 / p95 / p99 |
|---|---:|
| 매칭 엔진 `cts`→시스템 생성 `ts` | 40.500 / 43.300 / 43.860ms |
| 시스템 생성 `ts`→서버 WebSocket 수신, 보정 추정 | 21.458 / 23.784 / 23.855ms |

첫 snapshot의 `ts`→수신 추정은 709.047ms였다. 첫 snapshot과 동일 `u`의 반복 snapshot 3개는 신규 update 분포에서 제외했다. Level 1은 호가 변화가 없으면 동일 `u` snapshot을 다시 보내므로 오래된 `cts`를 신규 처리 지연으로 집계하지 않는다. `cts`는 매칭 엔진 생성, `ts`는 시스템 생성 시각이다. 실제 네트워크 송신 시각은 아니다. [Bybit 공식 호가 명세](https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook).

## 이전 측정과 비교

`BYBIT_TESTNET_LATENCY_2026-09-26.md`의 직접 testnet WS→best ask Buy Limit IOC 단일 실제 체결 표본:

- 서버 수신→첫/최종 체결 알림: **287.699ms**.
- 의사결정→API 호출: 3.166ms; API 호출→HTTP 헤더 송신: 3.951ms.
- 헤더 송신→ACK: 113.247ms.
- 호가 `cts`→거래소 체결 `execTime`: 856ms.

이번에는 실제 주문이 없으므로 **전체 주문·체결 지연이 줄었다고 판정할 수 없다.** 기존 측정은 운영 collector→NATS를 거치지 않았으며 단일 표본이다. 당시 `ts`→수신 673.672ms와 이번 신규 update 중앙값 21.458ms도 snapshot/신규 update 표본 조건이 달라 개선율로 비교하지 않는다. 이번 첫 snapshot 709.047ms는 오히려 초기 snapshot 나이가 여전히 커질 수 있음을 보여준다. 초기 snapshot을 곧바로 트리거로 사용하는 예전 계측의 비교 한계가 있다.

## 키 복구 후 실제 주문 측정 조건

1. 현재 testnet 포지션/미체결 주문 확인, 기존 사용자 주문은 건드리지 않는다.
2. Private order/execution 구독과 REST 연결을 준비하고 clock offset/RTT를 기록한다.
3. SOLUSDT 소량 매수: **Limit IOC 가격은 그 시점 testnet best ask 정확히**, Market은 실제 `orderType=Market`. Bid나 PostOnly로 바꾸지 않는다. 가격 이동에 따른 IOC 미체결·부분 체결·취소는 따로 보고한다.
4. 직접 testnet 호가 기점 경로는 이전 결과와 비교하고, 운영 collector→NATS 기점 경로는 별도 구분한다. 후자의 주문 가격도 testnet 호가에서 얻으며 운영 거래소 가격을 testnet 주문 가격으로 사용하지 않는다.
5. 수신, 의도 생성, API 호출, HTTP 송신 trace, ACK, 첫/최종 private 체결 수신은 로컬 단조 시계로 기록한다. 거래소 `execTime`과 주문 생성 `createdTime`은 따로 기록한다.
6. **거래소 네트워크 ingress 시각은 API에 없으므로 주문 생성 시각을 도착 시각으로 대체하지 않는다.** ACK 왕복에는 왕복 네트워크와 거래소 처리 시간이 섞인다. 도착→매칭의 순수 구간은 직접 분리 측정할 수 없다. [주문 접수 명세](https://bybit-exchange.github.io/docs/v5/order/create-order), [체결 시각·체결 유형 명세](https://bybit-exchange.github.io/docs/v5/websocket/private/execution), [주문 상태 명세](https://bybit-exchange.github.io/docs/v5/websocket/private/order).
7. 반복 성공 표본과 실패를 구분하고 측정으로 만든 포지션만 reduceOnly로 정리한다. 최종 잔여 주문/포지션을 확인한다.

## 파일

- 계측기: `scripts/measure_crypto_latency.py` (운영 코드 변경 없음, 주문 제출 없음, Python compile 통과).
- 로컬 원시 증거: `docs/evidence/crypto-ingestion-latency-20261001.json`.
- 서버 원시 증거: `/srv/vibetrading-data/testnet_latency_audits/20261001/ingestion-latency.json`.
