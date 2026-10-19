# Bybit testnet 호가 기점 주문 지연 측정 (2026-09-26)

## 범위와 가격 원칙

Ncloud 서버 `49.50.141.106`에서 단발성 Docker 계측기를 실행했다. Bybit testnet `orderbook.1.SOLUSDT` WebSocket 수신을 기점으로 같은 testnet 계정에 0.1 SOLUSDT 계약 주문을 제출했다. 매수 maker는 **best bid `PostOnly`**, 매수 taker는 **best ask `IOC`**다. Best ask의 `PostOnly` 매수는 이미 매도 유동성을 건드릴 가격이므로 [Bybit 주문 명세](https://bybit-exchange.github.io/docs/v5/order/create-order)에 따라 즉시 체결 가능한 상황에서는 취소된다. 따라서 이를 maker 체결 지연으로 집계하지 않는다.

## 단계별 실측

| 구간 | Taker IOC 매수 | Maker PostOnly 매수 | 측정 방식 |
|---|---:|---:|---|
| 매칭 엔진 호가 생성 `cts` → 시스템 이벤트 생성 `ts` | 44ms | 45ms | Bybit의 두 시각 |
| `ts` → 서버 애플리케이션 WebSocket 수신 | 약 673.672ms | 약 637.693ms | 서버 시각 보정 추정치. 거래소 내부 발송 처리와 네트워크 전송을 합친 구간 |
| 서버 수신 → 의사결정 | 0.037ms | 0.037ms | 서버 `perf_counter_ns` |
| 의사결정 → 주문 API 호출 | 3.166ms | 3.873ms | 서버 `perf_counter_ns`; 감사 원장 기록 포함 |
| API 호출 → HTTP 헤더 송신 | 3.951ms | 3.982ms | `aiohttp` 헤더 송신 trace |
| HTTP 헤더 송신 → 주문 ACK | 113.247ms | 41.796ms | ACK는 접수이며 체결 아님 |
| ACK → private order `New` 장부 게시 | 해당 없음 | 165.616ms | private order 스트림 |
| 장부 게시 → 첫 체결 알림 | 해당 없음 | 4,271.795ms | 시장 상대 주문을 기다린 시간 포함 |
| 서버 호가 수신 → 첫/최종 체결 알림 | 287.699ms | 4,487.099ms | 서버 단조 시계 |
| 거래소 호가 생성 `cts` → 거래소 실제 체결 `execTime` | 856ms | 5,010ms | Bybit 거래소 시각끼리 비교 |
| 거래소 실제 체결 → 서버 private 알림 수신 | 약 149.379ms | 약 159.800ms | 서버 시각 보정 추정치 |

Maker의 **주문 경로 지연**은 의사결정→장부 게시 215.267ms다. 그 뒤 실제 체결까지 4,271.795ms는 장부 대기와 상대 주문 도착에 따른 값이므로 주문 제출 지연과 분리한다. Maker private execution의 `isMaker=true`, taker의 `isMaker=false`를 확인했다. [호가 `cts`·`ts` 정의](https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook), [체결 `execTime`·`isMaker` 정의](https://bybit-exchange.github.io/docs/v5/websocket/private/execution).

두 주문의 서버 시각 보정값은 taker `+2ms` (최단 시각 조회 왕복 39.921ms), maker `+4ms` (36.339ms)였다. 거래소 시각과 서버 시각을 직접 뺀 추정치의 불확실성은 각각 **최소 ±19.961ms, ±18.170ms**이며 공급자 내부 타임스탬프 오차는 별도다. Bybit가 실제 네트워크로 **송신한 시각은 제공되지 않아** `cts→실제 송신`과 `실제 송신→서버 수신`을 독립적으로 측정할 수 없다. `ts`는 시스템이 데이터를 생성한 시각이며 송신 시각이 아니다.

각 매수 포지션은 `reduceOnly` taker 매도로 청산했다. 최종 Bybit 조회에서 SOLUSDT 잔여 포지션과 미체결 주문은 모두 0건이었다. 서버의 별도 감사 원장은 `/srv/vibetrading-data/testnet_latency_audits/bybit_20260926_MerHtR.sqlite3`에 보존했으며 SHA-256은 `8b69117351aaf7503661a46eb1cf46585baddb16af168f4e4e51e85a1639fd1e`이다.

이는 **서버에서 직접 공개 호가 WebSocket을 받아 주문한 단발성 testnet 표본**이다. Binance·Bitget·Bybit 수집기→NATS→전략의 운영 경로 지연, 반복 표본의 p95/p99, 실거래 지연 보장치는 아직 측정하지 않았다. 장기간 서버 관찰은 실행하지 않았다.
