# 티커별 공유 CSV와 장 마감 갱신

2026-10-13 KST. 사용자가 다시 정리한 `runtime_data/etf_reference_csv/raw/`의 10개 파일,
117,579,184바이트를 원래 바이트와 SHA256 그대로 서버에 설치했다.
국내 ETF·KOSPI200은 2026-10-13, SP500은 마지막 완료 세션인 2026-10-12까지다.
이 문서는 이전 전략별 CSV 묶음의 경로·갱신 방식을 대체한다.

서버 기준 디렉터리: `/srv/vibetrading-data/runtime_data/state/etf-mock/history/raw/`.

| 대상 | 공유 파일 | 이후 갱신 provider |
|---|---|---|
| KODEX 200 | 069500_daily.csv / 069500_minute.csv | Kiwoom |
| KODEX 레버리지 | 122630_daily.csv / 122630_minute.csv | Kiwoom |
| KODEX 코스닥150 | 229200_daily.csv / 229200_minute.csv | Kiwoom |
| KODEX 코스닥150선물인버스 | 251340_daily.csv / 251340_minute.csv | Kiwoom |
| 코스피200 지수 | KOSPI200_daily.csv | Kiwoom ka20006, inds_cd 201 |
| S&P500 지수 | SP500_daily.csv | Yahoo ^GSPC |

제공한 지수는 파일명·가격 수준에 따라 KOSPI200으로 연결했다. KOSPI 종합지수와는 별개다.
C3·KP는 동일한 069500·122630 파일을 읽으며 C3 전용 ETF 파일을 두지 않는다.
KD는 229200·251340 파일을 읽는다. C3의 OLS/VT30, KP 롱 전용 최대 2배,
KD 양방향 최대 1배의 신호·매매 차량 규칙은 유지한다.

## 장중과 장 마감의 흐름

장 시작 준비는 공유 CSV에서 일봉과 최근 15거래일의 1분봉을 읽는다.
이 과정은 과거 NATS 재생이나 Kiwoom 과거 전체 조회를 수행하지 않는다.
당일 신호는 메모리에 쌓이는 당일 완료 분봉과 신선한 체결·호가를 이용한다.
일봉의 당일 종가는 과거 Noise·변동성 계산에 들어가지 않는다.
C3는 당일 native ETF·지수 시가를 확인한 즉시 주문한다.
모든 전략 주문은 지정가로 시작하고 접수 후 5초 미체결이면 취소 확인 후 잔여 수량을 시장가로 제출한다.

새 `etf-csv-sync` 서비스는 모의매매 프로세스와 독립적으로 실행된다.
장중 `live.md.bar_1m.kiwoom.etf.>`에서 네 ETF의 완료 봉을 받고 날짜·티커별 JSONL staging에 기록한다.
CSV에는 장 마감까지 쓰지 않는다. 국내 세션 마감 후 5분부터 staging과 native REST 분봉을 결합해
재접속·재시작 누락분을 복구하고 새 거래일의 분봉과 확정 일봉을 CSV에 추가한다.
통상 KST 15:35 이후다. KOSPI200 지수 일봉도 같은 마감 조건으로 추가한다.

S&P500은 XNYS 캘린더의 실제 마감 후 10분부터 완료된 OHLCV만 추가한다.
휴일·서머타임·조기 폐장을 적용한다. Yahoo 응답의 진행 중인 당일 봉은 제외한다.
정규 거래와 조기 폐장은 [NYSE 공식 거래시간](https://www.nyse.com/trade/hours-calendars)과 대조했다.
2026-10-13 미국 세션은 KST 2026-10-14 05:00 마감이며 첫 갱신 대상 시각은 05:10이다.
공급자에게 확정 봉이 아직 없으면 기존 CSV를 보존하고 다음 시도에서 다시 조회한다.

기존 CSV 행은 보존하고 종료일 이후 날짜만 추가한다. 누락 봉은 임의로 채우지 않는다.
Kiwoom 시작 시각은 완료 시각으로 변환하며 종가 단일가 15:30 관측은 15:30에 보존한다.
종가 단일가는 장중 신호의 30분 봉에 들어가지 않는다.
원본의 숫자 티커 69500을 읽을 때 069500으로 정규화하고 `9:01` 형식의 시각도 처리한다.

갱신은 전용 파일 lock·임시 파일·fsync·원자적 rename을 사용한다.
CSV와 manifest의 교체 사이에 종료돼도 transaction 영수증에서 복구하며 같은 날짜를 중복 추가하지 않는다.
`manifest.json`의 현재 크기·행 수·종료일·SHA256도 갱신한다.
이전 전략별 11개 묶음은 서버의 `history-before-ticker-*`에 복구용으로 보관했으며 활성 경로에서 참조하지 않는다.
이번 변경은 전략 입력과 CSV 갱신에 한정한다. 기존 NATS/Parquet 원시 데이터 저장 경로도 함께 작동한다.

## 현재 설정된 수집 범위

| 수집기 | 대상 | 데이터 | 저장 경로 |
|---|---|---|---|
| Kiwoom | 069500,122630,229200,251340 | 체결·호가·iNAV·1분봉, 마감 일봉 | 기존 NATS/Parquet + 네 티커 CSV |
| Kiwoom | 114800,252670,233740 | 체결·호가·iNAV·1분봉 | 기존 NATS/Parquet |
| Kiwoom/TOSS | 005930,000660 | 체결·호가 | 기존 NATS/Parquet |
| Kiwoom | KOSPI200 지수 | 마감 일봉 | KOSPI200_daily.csv |
| Yahoo | S&P500 지수 ^GSPC | 미국 세션 마감 일봉 | SP500_daily.csv |
| KIS | KOSPI200,KOSDAQ150,B10,USD 각각 근월·차근월 | 선물 체결·호가 | 기존 NATS/Parquet |
| TOSS | SPY,QQQ | 체결·호가·1분봉 | 기존 NATS/Parquet |
| Binance/Arcus/GRVT | 각각 BTC,ETH,SOL | 체결·호가·funding·open interest | 기존 crypto NATS/Parquet |

KIS 옵션, Bitget/Bybit 및 Binance/Hyperliquid의 SKHYNIX/SAMSUNG/SKHY 수집은 중단 상태다.
위 표는 현재 활성 설정이다. 장중 연속 수신과 모의 체결은 다음 거래 세션에서 확인해야 한다.

## 검증과 운영

- revision: `etf-scope-5a70b17a7b2478a4`
- image: `sha256:8227ac19c1d0aa9844797004eb1a05818d1539af8e49b2c767a0d3b14175626a`
- 배포 이미지 Linux 테스트 89개 통과. Windows 88개 통과, 디렉터리 fsync 1개 skip.
- 티커 경로 공유, 과거 REST 없는 warmup, 미국 미완료 봉 제외, 조기 폐장,
  원본 보존·중복 방지·CSV/manifest 교체 중 종료 복구를 검증했다.
- 실제 서버 warmup에서 네 ETF 각각 15거래일·5,850개 과거 1분봉을 읽고 C3 계산에 성공했다.
- 마감 동기화 점검은 국내 완료일 10-13, 미국 완료일 10-12로 판정했다.
  모든 파일이 이미 최신이라 기존 CSV에 추가한 행은 0개다.
- 별도 검증 디렉터리에서 실제 Kiwoom 마감 데이터를 조회해 네 ETF 분봉·일봉과 지수 일봉,
  총 9개 CSV를 추가하는 전체 경로를 실행했다. 재실행 시 추가 행은 0개였고,
  사용자 제공 canonical 파일 10개의 SHA256이 그대로임을 확인했다.
- 수집·저장·모의매매·CSV 갱신 서비스가 healthy이며 실제 계좌 주문 제출은 0건이다.

```sh
systemctl status vibetrading-etf-mock.service vibetrading-etf-csv-sync.service
docker exec vibetrading-etf-csv-sync-1 python main.py health-check --component etf-csv-sync --max-age-seconds 180
cat /srv/vibetrading-data/runtime_data/state/etf-csv-sync/last-sync.json
```

거래 원장은 기존 `state/etf-mock/journal.sqlite3`, CSV staging과 동기화 영수증은
`state/etf-csv-sync/`, heartbeat는 `state/health/etf-csv-sync.json`이다.
기존 STOP 파일·kill switch는 신규 진입을 막으며 CSV 수집·마감 갱신은 계속한다.
증거는 [영수증 디렉터리](evidence/etf_scope_20261013/),
[티커 CSV 목록](evidence/etf_scope_20261013/csv-manifest.json),
[마감 동기화 점검](evidence/etf_scope_20261013/csv-sync-check.json),
[실제 마감 API 저장 검증](evidence/etf_scope_20261013/native-close-probe.json)에 보관한다.
