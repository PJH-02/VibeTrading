# 수집 대상 정리와 C3/KP/KD 모의 ETF 운용

현재 CSV 경로·장 마감 갱신 방식은 [티커별 공유 CSV 운영](TICKER_CSV_COLLECTION_2026-10-13.md)을 따른다.
아래 전략별 CSV 묶음 내용은 티커 통합 이전의 기록이다.

2026-10-13 KST, 서버 `49.50.141.106`에 배포했다. 국내 수집기와 저장 서비스,
유지보수 서비스 및 새 `etf-mock` 서비스가 healthy이며, 모의 매매 서비스는
장 종료 후 `waiting_session` 상태다. 이번 작업 중 실제 주문 제출은 0건이다.
장중 실제 호가·iNAV의 지속 수신과 모의 주문 체결은 다음 거래 세션에서 확인해야 한다.

## 적용한 수집 범위

- KIS: KOSPI200·KOSDAQ150 및 기존 B10/USD 선물의 근월·차근월 수집 유지.
  옵션 WebSocket 등록과 발행을 차단하고 옵션이 포함된 과거 universe 캐시 26개를 삭제했다.
  마스터의 `KSQ150`을 설정의 `KOSDAQ150`과 연결하고 두 지수 모두 선물 TR `H0IF`를 사용한다.
  기존 KOSDAQ 매핑의 `H0EU`는 야간 옵션 TR이므로 제거했다.
  [KIS 공식 실시간 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/domestic_futureoption/domestic_futureoption_functions_ws.py)와 대조했다.
  최종 마스터 확인: 네 상품의 근월·차근월 선물 8종, 체결·호가 구독 16개, 옵션 구독 0개.
- Kiwoom: 기존 주식과 ETF 7개 `069500,122630,114800,252670,229200,233740,251340`.
  ETF는 실시간 체결 `0B`, 호가 `0D`, iNAV `0G`와 완료된 native 1분봉 `ka10080`을 수집한다.
  ETF 분류를 `equity`에서 `etf`로 바로잡고 `etf_nav` 이벤트의 NATS·Parquet 저장 경로를 추가했다.
- 일반 Binance/Hyperliquid의 SKHYNIX/SAMSUNG/SKHY 파생상품 수집은 런타임에서 제거했다.
  Bitget/Bybit 수집기는 정지하고 재시작 정책을 `no`, Compose profile을 `retired`로 설정했다.
- 코인 수집은 Binance/Arcus/GRVT의 BTC/ETH/SOL만 유지한다.
  대시보드의 별도 Bitget/Bybit 공개 스트림과 Bybit 계좌 polling도 중지했다.
- 기존 TOSS 미국 ETF 수집은 유지한다.

1분봉의 Kiwoom native 시각은 시작 시각이다. 예를 들어 09:00 라벨을
09:00:00–09:00:59 봉으로 처리하고 09:01 이후에만 완료 봉을 발행한다.
15:30 종가 단일가는 별도의 native auction 관측으로 보존하며 장중 신호 봉에 섞지 않는다.
재접속 때의 과거 봉은 backfill로 구분하고 과거 신호를 주문으로 재생하지 않는다.

[키움 공식 API 사양](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/tree/main/kiwoom/_data)에
따라 0G의 NAV FID 36과 native 괴리율 FID 265/266을 원문과 함께 보존한다.

## 데이터 정리

중단 대상 로컬 파일 391개, **4,334,533,611바이트**를 원격 보관 상태와 체크섬을 대조한 후 회수했다.
등록되지 않은 완료 파일·중단된 writer의 임시 파일 72개도 원래 바이트 그대로
`vibetrading/retired-20261013/cache/` 아래에 업로드하고 원격 전체 SHA256을 검증했다.
이미 원격 검증된 canonical 파일은 기존 검증 영수증, 원격 크기·SHA256 metadata 및 로컬 SHA256을 대조했다.

중단 자료가 포함된 과거 통합 export 4개, **2,221,607,299바이트**는
`vibetrading/retired-20261013/legacy-exports/`로 옮겼다. 모든 export를 원격 전체 SHA256으로 검증하고
로컬 원본을 회수했다. 원격 canonical 과거 데이터는 삭제하지 않았다.
NATS에 남았던 Bitget/Bybit data-gap 기록 **1,299개**도 원격 보관·SHA256 검증 후 정확한 6개 subject만 purge했다.

최종 확인에서 중단 provider의 로컬 market/crypto partition 파일과 과거 export 파일은 0개다.
KIS 로컬 partition 파일도 0개이며, MARKET_DATA/OPERATIONS에 KIS option subject는 없다.
CRYPTO_DATA의 관측 subject는 Binance/Arcus/GRVT × BTC/ETH/SOL × 지원 데이터 유형으로 구성된다.
WorkQueue에서 대기 중인 subject 수는 수신·저장 시점에 따라 달라진다.
데이터 복구에 필요한 원격 manifest·전달 원장과 운용 감사 기록은 유지한다.

## 계좌와 전략

사용자 확인에 따라 **beomgu C3·VT30·ETF + jehyeong intraday final KP/KD**를 사용한다.
`config/etf_strategy_provenance.json`에 원본 경로·SHA256·고정 선택을 기록했고,
원본 Noise/VWAP·250일 OLS 함수는 `vibetrading/etf_reference.py`에 그대로 가져왔다.
탐색·파라미터 재선정은 수행하지 않았다.

| 전략 | 원본 규칙 | 매매 차량 |
|---|---|---|
| C3 | 직전 250일 OLS, 최소 200행. 지수 갭 잔차 z≥1 및 ETF 상대 시가 할인≤0. 전일까지 20일 지수 변동성으로 VT30, 노출 상한 2 | 069500/122630 |
| KP | 14일 시각별 Noise, 30분 Noise-band 돌파. M1-P stop, 롱 전용, 하루 진입 1회, 최대 2배 | 069500/122630 |
| KD | 같은 Noise-band 양방향 신호. M1-P stop, 하루 진입 1회, 최대 1배. 전일까지 20일 일중 range 중앙값 × 0.25 안전청산 | 229200/251340 |

현금 잔고 **100,000,000원**을 각각 **33,333,333원**으로 나누고 1원은 미배정 잔액으로 남겼다.
공유 계좌 안에서 전략별 cash·포지션·예약금·주문·체결을 독립적으로 기록한다.
계좌 전체 주문 가능금액과 전략별 예산을 모두 확인하며, 수량은 정수 주식으로 내림한다.
브로커 접수 응답을 체결로 취급하지 않고 주문번호별 누적 실제 체결량·대금의 차분을 한 번씩만 반영한다.
수수료는 편당 0.015%를 보수적으로 원장에 반영하며 실제 증권사 정산과의 대조가 필요하다.

모의 주문은 **명시적으로 `KIS_MOCK_*`만 사용**한다. 사용자가 서버의 주식 모의투자
앱키·시크릿·계좌번호를 갱신했으며 상품코드 01로 인증과 잔고 조회에 성공했다.
기존 `KIS_*` 실계좌 자격증명과 토큰 캐시는 분리되어 있다. 토큰 만료 시각은 KST로 해석한다.
일반 KIS adapter에는 실계좌/모의계좌 **국내 주식·ETF 현금 주문**을 연결했으며,
새 자동매매 루프는 모의 모드로 고정한다. 선물 주문 endpoint를 추가한 것은 아니다.

주문 TR·body·체결 조회·취소는 [KIS 공식 예제](https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/domestic_stock)를 따른다.
POST를 자동 재전송하지 않는다. 접수 결과가 불명확하면 예약금과 상태를 보존하고
새 진입을 막는다. 보유 수량보다 많은 매도와 다른 전략 소유 수량의 매도를 차단한다.
기존 global/account kill switch와 `state/etf-mock/STOP` 파일은 신규 진입을 막고 청산은 허용한다.

## 장중 실행의 시간 차이

원본 C3 백테스트의 ‘확정 시가를 보고 같은 시가 체결’은 실시간으로 재현할 수 없다.
C3는 장 시작 후 첫 실시간 체결부터 native ETF·지수 시가를 조회하고 조건이 맞으면 바로 지정가 주문한다.
다음 1분봉 종가를 가상의 체결가로 사용하지 않는다.
KP/KD는 완료된 30분 봉으로 정상 신호·청산을 판단하며 KD 안전청산은 1분 단위로 확인한다.
봉 완료는 신호의 계산 시점이며, 주문 접수와 실제 체결은 별도로 기록한다.

세 전략의 진입·청산 모두 **시그널 발생 즉시 지정가 → 접수 후 5초 미체결 시 취소 →
취소 확인 후 잔여 수량 시장가**로 처리한다. 최초 지정가는 당시 매수 최우선 매도호가+5원,
매도 최우선 매수호가−5원을 ETF 호가 단위 5원에 맞춘다.
5초는 브로커 접수 응답 시점부터 계산한다. 5초에 취소 절차를 시작하고,
취소 중의 추가 체결을 누적 조회에 반영한 다음 남은 수량만 시장가로 제출한다.
네트워크·KIS 호출 제한·취소 확인 때문에 시장가 접수가 정확히 5.000초에 끝나지는 않는다.
부분체결된 수량을 다시 주문하거나, 취소 확인 없이 지정가와 시장가가 동시에 살아 있게 하지 않는다.
시장가 매수도 증권사의 시장가 주문 예약 단가와 전략별 잔여 예산으로 수량을 제한한다.
취소·대체 상태와 의도 ID를 원장에 보존하여 재시작 시 중복 제출을 막는다.

C3의 종가 단일가 청산은 15:20:05부터 최근 유효 호가로 지정가를 접수하고 동일한 5초 전환 규칙을 적용한다.
KP/KD 장 종료 청산은 15:19:55부터 접수하여 종가 단일가 이전 연속매매 구간에서 시도한다.
KD 안전청산 가격은 진입 접수 시점의 signal ETF 실제 호가 중간값에 고정한다.
지연·미체결·부분체결은 원장에 그대로 남고, 다음 장 미청산 포지션 복구 경로가 있다.
따라서 원본 백테스트와 동일한 체결가·성과를 보장하는 구현은 아니다.

## 과거 CSV와 실시간 데이터의 결합

**과거 CSV는 NATS에 재발행하지 않고 별도 읽기 전용 기준 데이터로 둔다.**
현재 MARKET_DATA는 실시간 수집과 저장을 연결하는 WorkQueue다. 과거 자료를 여기에 넣으면
실시간 저장·관측 경로를 불필요하게 지나고, 과거 봉의 수신 시각을 새 시각으로 오해할 수 있다.
전략 warmup 단계에서 원본 CSV와 원본보다 새로운 native 조회 데이터를 결합하며,
장중 신호는 기존 실시간 NATS의 별도 `live.md.*.kiwoom.etf.>` 구독으로 계산한다.

Future_Trading에서 아래 **11개 원본 CSV, 128,634,895바이트**를 그대로 가져왔다.
서버 위치는 `/srv/vibetrading-data/runtime_data/state/etf-mock/history/`다.
각 파일은 읽기 전용이며, `manifest.json`에 원본 경로·기간·행 수·SHA256을 기록하고 로딩할 때 검증한다.

| 용도 | 원본 상대 경로 | 원본 마지막 날짜 |
|---|---|---|
| KP 일봉 | jehyeong/intraday/data/069500_daily.csv | 2026-10-07 |
| KD 일봉 | jehyeong/intraday/data/229200_daily.csv | 2026-10-07 |
| KP 1분봉 | jehyeong/intraday/data/minute/069500_TOSS_1m.csv | 2026-10-07 |
| KD 1분봉 | jehyeong/intraday/data/minute/229200_TOSS_1m.csv | 2026-10-07 |
| KP 레버리지 일봉 | jehyeong/intraday/data/122630_daily.csv | 2026-10-07 |
| KP 레버리지 1분봉 | jehyeong/intraday/data/minute/122630_TOSS_1m.csv | 2026-10-07 |
| KD 인버스 일봉 | jehyeong/intraday/data/251340_daily.csv | 2026-10-07 |
| KD 인버스 1분봉 | jehyeong/intraday/data/minute/251340_TOSS_1m.csv | 2026-10-07 |
| C3 ETF 일봉 | beomgu/data/etf/069500.csv | 2026-09-29 |
| C3 지수 일봉 | beomgu/data/etf/KPI200.csv | 2026-09-29 |
| C3 SPX 일봉 | beomgu/data/spx_daily.csv | 2026-09-22; 마지막 유효 종가는 09-21 |

원본의 가격과 완료 봉 라벨을 유지하며, 중복 날짜는 원본을 우선한다.
일봉은 원본 종료일 이후 Kiwoom 데이터를 덧붙이고, SPX는 이후 완료된 Yahoo 일봉으로 갱신한다.
1분봉은 계산에 필요한 최근 15거래일만 chunk 단위로 읽고 하루에 하나의 provider만 사용한다.
이날 검증에서 KP 일봉 5,918행·KD 일봉 2,701행, 각 원본 1분봉 5,070행을 실제 사용했다.
C3는 원본 ETF 5,913행·지수 3,000행·유효 SPX 9,247행을 사용했으며 최신 입력으로 계산에 성공했다.
당일 종가는 과거 변동성·Noise 계산에 들어가지 않고, 과거 NATS 자료로 신규 주문을 재생하지 않는다.

KP의 Noise·VWAP·변동성 신호는 069500, KD는 229200에서 계산한다.
KP는 롱 전용이며 노출 L≤1일 때 069500, 1<L≤2일 때 069500 비중 2−L와 122630 비중 L−1이다.
KD는 최대 1배로 롱 229200, 숏 방향은 251340 인버스 ETF를 현금 매수한다.
각 실행 ETF의 과거 가격과 장중 1분봉을 별도로 유지하고, 신규 진입은 해당 차량의 과거 데이터가 있어야 허용한다.
주문 감사 기록의 `vehicle_history`에는 그 ETF의 전일 종가·직전 과거 1분봉 가격·provider를 기록한다.
이 과거 가격은 주문 체결가로 사용하지 않는다. 장중 `vehicle_bars`에는 네 ETF 각각의 완료 봉 수와 최근 종가를 기록한다.

최초 7개 묶음에는 122630·251340이 누락돼 있었다. 2026-10-13 추가 배포에서 두 차량의 일봉·1분봉을
보완하고, warmup과 장중 봉 처리를 네 ETF 전체로 확장했다. 원본 122630 일봉 4,091행과
251340 일봉 2,489행, 두 차량의 1분봉 각 368,040행을 가져왔다.
실제 warmup에서 두 차량 각각 원본 1분봉 5,070행과 이후 native 데이터를 사용하고 최근 15거래일을 확보했다.
장 종료 후 조회한 네 ETF의 당일 봉도 각각 구분했고, 기초 ETF의 30분 신호 구간은 KP/KD 각각 12개가 usable이었다.
native 조회의 누락 봉은 원래대로 남기며 다른 ETF 가격으로 대체하거나 임의로 채우지 않는다.

## 운영과 검증

- revision: `etf-scope-f02370e89232e152`
- 배포 이미지: `sha256:40606599e603e59cde4c78500e2ca0752569fb1c4328f6834714c39f3f454ddf`
- Linux 배포 이미지에서 **82개 테스트 통과**. 로컬은 81개 통과, Windows 디렉터리 fsync 테스트 1개 skip.
  5초 전·취소 미확인 시 시장가 금지, 취소 중 추가 부분체결, 매수·매도 잔여 수량의 1회 대체,
  원본 CSV 가격·시각 유지 및 SHA256 불일치 차단, C3의 1분봉 대기 없는 시가 신호를 검증했다.
  KOSDAQ 마스터 별칭과 선물 전용 TR 선택의 회귀 테스트도 포함한다.
  KP의 기본·혼합·2배 배분, KD의 롱·인버스 매수, 차량별 과거 가격 구분,
  차량 데이터 누락 시 차단 및 차량 봉이 기초 ETF 신호를 바꾸지 않는 것도 검증했다.
- 실제 Kiwoom native 시가 조회, 원본 CSV와 최신 입력의 warmup, C3 계산,
  KIS 모의계좌의 시장가 예약 단가 조회가 통과했다.
- 모의계좌 인증·잔고·주문 가능금액 조회, 전략별 3분할과 서비스 healthy를 확인했다.
  이번 작업에서는 장중 모의 주문 접수·체결을 수행하지 않았다.
- `vibetrading-etf-mock.service`를 enable했고 Docker `etf-mock`의 재시작 정책은 `unless-stopped`다.
  매 거래일 08:40 이후 warmup을 갱신하고, 신선한 당일 데이터와 전략 조건이 맞을 때 주문한다.
  휴장일에는 당일 ETF 봉이 없어 신규 주문을 만들지 않는다.
- 국내 수집기의 5일간 중단 원인은 OS 디스크 guard였다. 종료된 연구 cache 107개,
  1,738,344,756바이트를 300GB 디스크로 복사·전체 SHA256 대조한 뒤 기존 경로를 symlink로 보존했다.
  OS 여유 공간은 약 896MB → 2.5GB로 회복했다. guard 기준은 유지하고 복구 경로를 새 market.yml에 고정했다.

운용 명령(서버):

```sh
systemctl status vibetrading-etf-mock.service
docker logs --tail 100 vibetrading-etf-mock-1
docker exec vibetrading-etf-mock-1 python main.py health-check --component etf-mock --max-age-seconds 120
```

원장은 `/srv/vibetrading-data/runtime_data/state/etf-mock/journal.sqlite3`,
health는 `/srv/vibetrading-data/runtime_data/state/health/etf-mock.json`에 있다.
신규 진입 일시 중지는 `state/etf-mock/STOP` 파일을 생성한다.
서비스 정지는 `systemctl stop vibetrading-etf-mock.service`이며 이것만으로 이미 보유한 포지션이 자동 청산되지는 않는다.

증거는 [운영 영수증 디렉터리](evidence/etf_scope_20261013/),
[실제 API 입력 검증](evidence/etf_scope_20261013/input-probe.json),
[원본 CSV 검증 목록](evidence/etf_scope_20261013/csv-manifest.json),
[계좌 사전검증](evidence/etf_scope_20261013/preflight.json),
[최종 데이터 범위](evidence/etf_scope_20261013/final-scope.json),
[배포 서비스 상태](evidence/etf_scope_20261013/runtime-verification.json)에 보관했다.
