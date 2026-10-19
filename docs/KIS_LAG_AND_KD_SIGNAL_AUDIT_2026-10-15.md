# KIS 시세 지연 수정 및 KD 신호·토스 복구 가능성 검증

2026-10-15 KST, main 서버 49.50.141.106. 이전 04:33 디스크 보호 정지와 구분되는 후속 장중 장애다.

## 1. 반복된 KIS 207초 경보의 의미와 원인

연결·발행은 계속되고 있었지만, 도착하는 메시지의 시세 시각이 오래된 상태였다. 10:40경 20초간 NATS Core를 별도 구독하여 확인한 KIS source age는 약 222~238초였다. 이 기간 공급자 시각은 10:36:28~10:36:30 근처에서 느리게 진행됐지만 서버는 10:40 이후였다. source age와 서버 수신 후 발행 지연을 분리해 측정했다. 기존 저장 소비자를 구독/ACK하지 않았다.

따라서 경보의 문제는 완전한 오탐이 아니라 **실제 시세가 밀리는 상태를 “수신 중단”이라고 표현한 점**, 그리고 메시지가 계속 발행되면 기존 무수신 watchdog이 이 지연을 해소하지 못한다는 점이었다.

동일 컨테이너의 KIS·Kiwoom·TOSS는 같은 asyncio 이벤트 루프를 공유한다. 실행 중 수집기의 Python 스택을 10초간 비차단 표본 조사했을 때 메인 스레드 표본 131개 중 109개가 YAML 파서에 있었고, 107개는 Kiwoom 분봉 폴링 경로였다. Kiwoom의 instrument() → configured_instrument() → load_universe_config()가 이벤트마다 universe.yaml 전체를 다시 읽고 파싱했다.

또한 Kiwoom의 매분 REST 응답은 과거 행을 대량으로 반복해서 주는데, 기존 폴러는 이미 처리한 행인지 확인하기 전에 instrument와 분봉 모델을 모두 만들었다. WebSocket 재접속으로 cursor가 초기화되면 오늘 분봉 전체도 다시 만들었다. 반복적인 YAML 파싱과 불필요한 분봉 생성이 공용 루프를 점유했고 KIS 수신 큐가 밀리는 병목으로 관측됐다. 재접속만 해서는 다시 누적될 수 있는 경로였다.

### 수정

- universe.yaml 파싱 결과를 파일 절대 경로·mtime_ns·크기·inode 기준으로 캐시했다. 파일 편집과 원자적 교체는 반영하며, 반환값은 deepcopy하여 호출자의 수정이 캐시를 훼손하지 않도록 했다.
- Kiwoom은 날짜가 다르거나 cursor 이하인 REST 행을 모델 생성 전에 제외한다. 이미 발행한 cursor는 계속 유지하며 신규 행의 정상 검증·발행 순서는 유지한다.
- Telegram은 최근 메시지가 도착하면서 유효한 최신 시세가 갱신되지 않으면 “장중 시세 지연”으로, 메시지 자체가 중단되면 기존 “수신 중단”으로 구분한다. 경보를 끄거나 임계값을 높이지 않았다.

기존 YAML 반복 해석은 로컬 200회 비교에서 약 0.614초, 새 캐시 경로는 약 0.068초였다. 이는 해당 함수 비용 비교이며 전체 시스템 latency 개선율을 의미하지 않는다.

### 배포·검증

release: /srv/vibetrading-data/releases/kis-lag-fix-9ab198562cba.

collectors는 자기 기존 이미지에 universe_config.py와 providers/kiwoom.py만, Telegram은 자기 기존 이미지에 이 두 파일과 telegram.py를 반영했다. 기존 이미지의 다른 의존성을 섞지 않았다. 서버 정식 소스 및 활성 runtime.yml과 호환 market.yml도 갱신했다. 디스크 가드와 systemd는 기존 활성 runtime.yml 경로를 계속 사용한다.

로컬 관련 테스트 62개 통과. 서버에서는 네트워크 격리 컨테이너에서 Telegram 이미지의 관련 테스트 61개와 collectors 이미지의 세션 테스트 1개, collectors 실제 CLI/runtime import 검증이 통과했다. 역할별 이미지에 포함된 runtime 버전이 달라 해당 역할의 코드로 각각 검증했다.

collectors는 10:52:17, Telegram은 10:52:46에 수정본으로 시작됐다. Telegram 종료를 기다리며 새 collectors가 Created 상태에 남아 있어 먼저 시작했다. 주문 루프와 CSV 마감 서비스는 재시작하지 않았고 기존 주문 장부·포지션은 유지했다. 이번 점검을 위한 테스트 주문이나 과거 주문을 제출하지 않았다.

첫 사후 관측은 약 10:52:46~10:53:26, 40초다.

| 지표 | 결과 |
|---|---:|
| 실제 KIS 메시지 | 2,265개: 호가 1,804, 체결 461 |
| KIS source age 중앙값 | 0.487초 |
| KIS source age 95백분위 | 0.963초 |
| KIS source age 최대 | 1.229초 |
| KIS 서버 수신 → NATS 관측 중앙값 | 0.0034초 |
| 공용 수집기 CPU 단회 표본 | 수정 전 68.87%, 수정 후 6.23% |

KIS 공급자 시각은 초 단위이므로 source age를 밀리초 정밀도의 거래소 네트워크 지연으로 해석하면 안 된다. 위 수치는 약 4분의 지연이 사라졌는지 확인하는 관측값이다. 장시간 무장애를 보장하는 통계는 아니다. 추가 분봉 폴링 주기 관측은 증적의 post-fix-second-poll-cycle.json을 따른다.

후속 10:54:28~10:55:18 관측에서도 KIS 5,269개가 수신됐으며 중앙값 0.544초, 95백분위 1.038초, 최대 1.261초였다. publisher 누적 10,597건 중 실패 0·pending 0이었다. 10:56:58 최종 확인에서 collectors healthy, KIS/Kiwoom/TOSS 모두 publishing이고 배포 후 collectors/Telegram 오류 및 수집기 경보 전달 로그는 없었다. [최종 확인](evidence/kis_kd_followup_20261015/final-verification.json).

## 2. 오늘 KD 진입이 원래 전략과 같은가

실제 장부의 KD 진입 결정은 **10:30 종료 분봉**에 대한 것이다. 결정 저장 시각은 약 10:30:14, 첫 주문 생성은 약 10:30:17이었다. 251340 매수는 기존 mock2 라우팅으로 처리됐다.

오늘 데이터만으로 계산한 것은 아니다. 서버 warmup은 네 ETF의 과거 15거래일 분봉과 일봉을 로드했고 과거 CSV 최종 일자는 10/14였다. 신호 종목 069500·229200의 오늘 09:01~10:30 종료 분봉 90개는 모두 저장돼 있었다. 122630의 초기 두 분봉 누락은 실행 차량 데이터의 누락이며, KD 신호 계산은 229200을 사용한다.

현재 feature 함수 make_features와 차량 비중 함수 weights를 Future_Trading 원본 backtest_intraday_momentum.py와 AST로 비교했으며 동일했다. 기존 OHLCV를 원래 함수에 넣어 다시 계산했다.

| 종료 시각 | KP: 초기 무포지션 평가 | KD: 초기 무포지션 평가 |
|---|---|---|
| 09:30 | 진입 없음 | 숏 진입 조건 충족, 노출 1배 |
| 10:00 | 진입 없음: 숏 조건이나 KP는 롱 전용 | 숏 조건 충족 |
| 10:30 | 진입 없음: 숏 조건이나 KP는 롱 전용 | 숏 조건 충족 |

각 행은 초기 무포지션을 가정한 독립 조건 평가다. KD가 세 번 진입한다는 뜻이 아니다. 원래 규칙에는 일일 진입 횟수와 보유·청산·안전 손절 상태가 있다.

**10:30 시점의 KD 숏 조건 자체는 재현된다. 그러나 정상 운영 시 최초 진입 기회는 09:30이었으므로, 현재 실제 모의계좌 거래 경로가 원래 전략의 하루 전체 경로와 같다고 할 수 없다.** 과거 분봉을 뒤늦게 채우면 현재 지표 입력은 복원되지만 09:30의 진입·그 이후 포지션 상태까지 소급해서 실제 계좌에 적용되지는 않는다.

정확한 원래 결정 시점의 feature snapshot은 기존 장부가 보존하지 않았다. 이번 비교는 현재 저장된 분봉 revision을 사용한 재계산이다.

## 3. 토스 분봉으로 보충·재현할 수 있는가

**가능하다.** 서버에서 현재 유효한 Toss 캐시 토큰을 읽어 네 ETF에 각각 1회, 총 4회의 GET /api/v1/candles 요청으로 직접 확인했다. count=200, interval=1m, adjusted=false, 완료 분봉 기준 before를 사용했다. 토큰 발급·갱신·공동 캐시 수정이나 NATS 발행, 계좌/주문 API 호출은 하지 않았다.

네 ETF 모두 한 페이지 200개를 반환했고, 당시 오늘 09:01부터 108개의 분봉이 있었다. **122630의 09:01·09:02 종료 분봉도 토스에는 존재했다.** 따라서 해당 두 슬롯을 0거래량으로 만들어 넣을 이유가 없다.

| 비교 | 구간 | 결과 |
|---|---|---|
| 069500: 저장 Kiwoom vs 조회 Toss | 09:01~10:30, 90개 | OHLCV 전 항목 일치 |
| 229200: 저장 Kiwoom vs 조회 Toss | 09:01~10:30, 90개 | OHLCV 전 항목 일치 |
| KP·KD features 및 초기 무포지션 결정 | 09:30·10:00·10:30 | 양쪽 데이터로 같은 계산값·방향 |

실제 Toss 응답에는 tradingValue가 없었다. 두 공급자 모두 해당 항목을 비워 원래 함수의 30m HLC3 VWAP proxy 경로를 사용했다. 따라서 이번 비교에서 확인된 차이를 “토스는 실제 VWAP, 키움은 근사 VWAP”으로 설명할 수 없다. 다른 날짜·티커까지 데이터 일치를 입증한 검사는 아니다.

기존 Future_Trading/jehyeong/intraday/code/collect_intraday_data.py에는 Toss 페이지 조회·정규화·체크포인트 코드가 있다. 다만 현재 이 파일의 기본 UNIVERSE는 114800·252670·233740이고 최대 과거 수집 모드다. 그대로 상시 실행하면 필요한 네 ETF와 범위가 다르다. collect_intraday_data_v2.py는 현재 KOSPI200 지수 일봉 전용이라 ETF 분봉 보충 코드가 아니다.

서버에서 병행 운영하려면 기존 코드의 조회/정규화 부분을 사용하되 다음 범위로 제한하는 방식이 적합하다.

1. 별도 worker가 069500·122630·229200·251340 중 누락된 종목과 오늘 필요한 시각만 조회한다. 종료 조건·페이지 상한·간격·CPU/메모리 상한을 둔다. 실시간 수집기의 공용 이벤트 루프 안에서 최대 과거 수집기를 돌리지 않는다.
2. staging에서 KST/bar_end·완료 시각·중복/충돌·OHLCV·원본 해시를 검사한다. 현재 입력과 다르면 원본 두 개와 판정을 남긴다.
3. 과거 신호·가상 거래 재현은 기존 실시간 주문 장부와 분리된 replay 장부에서 시간순으로 진행한다. 미래 행을 보지 않고, 일일 진입 횟수·보유 상태·청산·수수료까지 순차 적용해야 한다.
4. 과거 분봉을 실시간 전략 입력에 채울 때는 명시적인 backfill로 전달한다. 과거 신호를 새 realtime 진입으로 해석하게 하면 안 된다. 마감 CSV는 단일 writer·manifest 무결성을 유지해 병합한다.

이번 요청은 가능 여부에 대한 질문이므로 bounded 조회와 신호 비교까지만 수행했다. 상시 보충 worker를 새로 활성화하거나 조회 데이터를 운영 CSV/주문 장부에 합치지는 않았다.

### “모의 주문·체결” 재현의 범위

- **과거 신호·주문 의도 생성:** 필요한 데이터가 확보되면 별도 replay에서 가능하다.
- **과거 가상 체결·손익:** 가격/체결 규칙을 명시하면 계산할 수 있다. 기존 계좌 권한 실패의 가정 체결 규칙과, 수집 중단 시간의 별도 연구 시나리오는 원인을 구분해 기록해야 한다.
- **당시 best quote로 지정가, 5초 후 시장가 전환의 정확한 재현:** 해당 시각의 호가·체결 또는 주문 응답 증거가 필요하다. 1분 OHLCV는 5초 안의 가격 순서·호가·호가 잔량을 제공하지 않으므로 정확히 복원할 수 없다.
- **지난 시각에 KIS 모의계좌가 실제 체결한 기록 생성:** API로 소급 제출할 수 없다. 지금 제출하면 지금의 주문·체결이 된다. 기존 실제 모의 거래 내역과 재현 결과는 각각의 장부에 기록해야 한다.
- **C3:** ETF 분봉만으로 충분하지 않다. 전일까지의 KOSPI200·S&P500 일봉, 당일 지수 시가와 ETF 시가가 필요하다. 과거 일봉은 현재 로드돼 있지만 이번에는 C3의 당일 지수 시가 재조회와 개장 시나리오 체결 재현까지 실행하지 않았다.

## 증적

[배포 소스·해시](evidence/kis_kd_followup_20261015/source.json), [서버 격리 테스트](evidence/kis_kd_followup_20261015/build.json), [배포 결과](evidence/kis_kd_followup_20261015/activation.json), [배포 후 서비스 상태](evidence/kis_kd_followup_20261015/verification.json).

[수정 전 KIS 수신](evidence/kis_kd_followup_20261015/kis-passive-observation.json), [프로파일 집계](evidence/kis_kd_followup_20261015/profile-summary.json), [수정 후 첫 관측](evidence/kis_kd_followup_20261015/post-fix-observation.json), [후속 조회 주기 관측](evidence/kis_kd_followup_20261015/post-fix-second-poll-cycle.json).

[KD 실제 결정·warmup](evidence/kis_kd_followup_20261015/kd-decision-inputs.json), [Toss 제한 조회 원본](evidence/kis_kd_followup_20261015/toss-bounded-candle-probe.json), [Toss 분봉 범위](evidence/kis_kd_followup_20261015/toss-coverage.json), [두 공급자 신호 재계산](evidence/kis_kd_followup_20261015/signal-input-replay.json), [원본 전략 함수 비교](evidence/kis_kd_followup_20261015/strategy-source-comparison.json).
