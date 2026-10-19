# 잔여 주문흐름 기반 MM — Bybit 테스트넷 구현·운영 명세

작성일: 2026-10-03, 시간대: Asia/Seoul. 모델: `residual-flow-bybit-v1.0`.
원본: `C:/Users/박제형/Downloads/universal_liquidity_mm_strategy_v1.md`.

이 구현은 원본 연구 명세를 **한 종목의 선택적 방향성 maker 전략**으로 구체화했다.
첫 프로필은 SOLUSDT USDT 선형 무기한 선물이다. 기존 시장 수집기·NATS·저장 서비스와
별도 프로세스에서 실행하며, 계정 인증은 기존 `.env`의
`BYBIT_TESTNET_API_KEY`, `BYBIT_TESTNET_API_SECRET`을 사용한다.
키 값은 로그·문서·상태 파일에 쓰지 않는다.

현재 준비 범위는 전략 코드, 실제 공개·private 연결, 위험 장부, 종료 정리,
관측 기록과 진단용 리플레이다. 실제 주문 실행은 사용자가 기간을 지정해 요청할 때 시작한다.
준비 과정의 shadow 실행은 주문을 제출하지 않는다.

## 확정한 실행 정책

| 항목 | 정책 |
|---|---|
| 거래 환경 | Bybit testnet 고정. mainnet URL·전환 옵션 없음 |
| 최초 종목 | SOLUSDT, `linear`, USDT, 단방향 포지션 모드 |
| 데이터 | `orderbook.50`, `publicTrade`, `tickers`, private `order/execution/position` |
| 가격·수량 | Decimal 변환 후 정수 tick·lot. 선형 계약 multiplier=1 |
| 워밍업 | 120초. 시장 상태는 수신 이벤트마다 반영 |
| 신호 판단 | 5초. 위험·체결 대응은 이벤트마다/최대 약 250ms timer |
| 예측 horizon | 최초 결정부터 60초; 명시적으로 30/60/120초만 설정 가능 |
| 지연 예산 | entry 5초, cancel 5초, private 3초, exit 5초. 실측 p95라는 뜻은 아님 |
| 진입 후보 | 롱 best bid 또는 bid−1tick; 숏 best ask 또는 ask+1tick |
| 진입 | 한 방향, 한 entry 가격, `PostOnly`, 고유 `ulm-` 주문 ID |
| 단위 | 최대 0.1 SOL; 추가 주문도 같은 최대 단위. 위험·참여율 제한으로 더 작아질 수 있음 |
| 재고 | 최대 3단위(0.3 SOL), 최대 명목 90 USDT, 단위 명목 30 USDT |
| 진입 TTL | 거래소 생성 시각으로 환산한 활성화 시점부터 20초; 취소 확정 전 예약 유지 |
| 정상 청산 | 최초 목표에 `reduceOnly + PostOnly`; 목표가 이미 실행 가능하면 `reduceOnly + IOC` |
| 최대 보유 | 가장 오래된 진입 체결부터 120초; 추가 체결로 연장하지 않음 |
| 가설 약화 | 2회 확인 후 entry 취소, 최대 10초 수동 청산 유예 |
| 위험감축 | 미확정 주문 대사·취소 후 가격 제한 IOC, 최대 3회. 성공하면 30초 cooldown |
| 기간 종료/중지 | 신규 노출 중단, 취소·청산 시도, REST 잔여 포지션·주문 확인 |
| 종료 한도 | 정리 단계 최대 60초. 잔여 노출이 있으면 HALT와 보고서에 명시 |

기간은 프로세스의 연결·워밍업 시간을 포함한다. 2분보다 짧으면 워밍업만으로
종료할 수 있다. 지정 기간에 진입 조건이 없으면 주문 0건도 정상 결과다.
정상 손절·가설 무효·보유 만료 후 위험감축에 성공하면 남은 기간 동안 관측과
cooldown을 거쳐 새 에피소드를 평가한다. 계정 손실·통신·대사 오류는 HALT로 종료한다.

## 신호와 가격을 계산하는 방법

1. 최근 완료된 5초 bin 6개에서 같은 방향 거래량 비율 65% 이상,
   우세 bin 4개 이상, 실제 거래 bin 4개 이상, 고유 거래 20개 이상을 요구한다.
   최근 10초 우세 비율도 55% 이상이어야 한다. 에피소드 시작은 최초 실시간 확인 시점이다.
2. buy/sell 유량을 따로 집계한다. 실제 경과시간을 사용한 10/30/120초 반감기 EWMA와
   neutral bin 배경 유량을 쓴다. buy−sell을 ask 깊이에 대입하지 않는다.
3. 방향·age 5개 구간의 Beta(1,1) 이산 hazard로 잔여 생존을 계산한다.
   현재 확인 가능한 at-risk/종료만 반영하고 연결·데이터 장애는 censor 처리한다.
   neutral 표본이 12개 미만이면 배경 유량은 120초 EWMA를 쓴다.
4. 절대 가격별 `D_new − D_old + 실제 소비량`으로 순공급을 추정한다.
   front는 best에서 2tick 이내이며 top-N 범위 이탈은 공급 취소로 세지 않는다.
   양의 순공급은 gross addition의 정확한 관측치가 아니다.
5. 공격 방향의 front 공급/소비 비율이 0.7 미만인 bin이 최근 3개 중 2개 이상이어야 한다.
   방어 면에서는 실제 반대 체결 뒤 10초 내 회복이 최근 30초에 2회 이상 있어야 한다.
   방어 2tick 잔량에 50% haircut을 적용하고 취소+체결통지 지연 동안의
   스트레스 반대 유량을 견딜 수 있어야 한다.
6. 각 레벨이 공격받는 시간만큼 순차적으로 재공급을 반영한다.
   수익에 유리한 취소는 0으로 둔다. 스트레스에서는 공격 유량 0.75배,
   재공급 1.5배, 불확실 물량 10%를 사용한다. 추가로 더 좋은 가격의 재공급
   한 흐름을 보수적으로 차감한다. 이 수치는 경험적 신뢰구간이 아닌 실험 시작값이다.
7. `P_struct`는 기본 경로, `P_reach`는 보수 경로의 관측 깊이 내 도달 가격이다.
   `P_exit`은 앞 호가, 같은 가격 잔량, 내 청산 수량, 불확실 여유까지
   소진 가능한 가장 유리한 관측 가격이다. 관측 깊이 밖은 외삽하지 않는다.
8. 진입 전에는 TTL 끝에 체결되는 경우를 기준으로 entry+TTL+private+exit,
   총 33초 뒤부터 남은 horizon의 물량만 청산에 쓴다. 실제 체결 후에는
   현재 수신 정보와 기존 deadline으로 다시 계산한다. 지나간 물량은 재사용하지 않는다.
9. 실제 maker 왕복 수수료 + latency 2bp + model 2bp + 최소 1tick을 초과하는
   gross edge를 요구한다. buffer를 실제 손익 비용으로 다시 차감하지 않는다.
10. 통과한 후보 중 보수 edge×수량이 큰 후보를 선택한다. 현재 entry의 목표
    서비스량·방어 조건도 다시 확인한다. 자동 가격 추격과 빈번한 재호가는 없다.

이는 구현 가능한 보수적 근사다. 시장의 잔여 모주문 크기를 식별하거나 정확한
개별 queue 위치를 관찰한다고 주장하지 않는다. 가격이 목표에 닿는 것과
내 주문이 전량 체결되는 것은 private 실행 기록으로 구분한다.

## 수량과 계정 위험

단위 수량은 설정된 q0, 재고 여유, 명목 한도 및 아래 금액 한도를 모두 적용한 뒤 lot 내림한다.

```
min(30 USDT, 종목 명목 한도/3, equity×0.5/3,
    equity×0.0005 / 스트레스 손실률,
    청산을 받아줄 최근 60초 실제 거래대금×0.01,
    stop 범위의 즉시 청산 depth×0.05)
```

스트레스 손실률에는 stop 35bp, IOC slippage 20bp, 지연 여유 2bp,
실제 maker/taker 수수료가 포함된다. 최소 주문 수량·금액 미달이면 NO_QUOTE다.
첫 종목만 실행하므로 종목별 0.5E 한도는 원본의 전체 gross E, 방향군 0.75E보다 엄격하다.

`I + 미확정 buy`와 `I − 미확정 sell`을 각각 계산한다. 서로 상쇄하지 않는다.
`PENDING_NEW`, `PENDING_CANCEL`, `UNKNOWN`은 대사 완료 전 체결 가능 노출이다.
terminal order 메시지가 execution보다 먼저 오면 아직 반영되지 않은 체결량을 예약한다.
execution ID로 중복 체결을 제거한다. 청산 주문 합계는 확정 재고에서 기존
미확정 청산 예약을 뺀 수량 이내다.

일중 equity 손실 1%, 계정 고점 대비 5% drawdown, 세션 손익 −5 USDT,
증거금 사용률 25% 초과를 중단 조건으로 쓴다. 일중 시작 equity·고점·계정 HALT는
계정별 SQLite 위험 장부에 보존하며 프로세스를 다시 켜도 리셋하지 않는다.
손실 한도 HALT 이후 자동 재개는 없고 운영자 검토가 필요하다.

펀딩 시각 전후 최대 보유+180초 범위에서는 신규 진입을 막는다.
entry에 MarkPrice 기준 원격 stopLoss를 함께 요청한다. stop이 갭·통신·유동성
위험을 모두 제거하지는 않으므로 원격 잔여 상태를 REST로 확인한다.

계정과 선택 종목은 시작 시 포지션·주문이 없어야 한다. 다른 USDT 선형 종목의
포지션/주문이 실행 중 생겨도 중단한다. 기존 수집 서비스는 같은 계정을 주문에
사용하지 않아야 한다. 단방향 모드가 아니면 자동 변경하지 않고 preflight에서 차단한다.

## 초기 실험과 검증된 운용의 구분

기본 `evidence_mode: testnet_pilot`은 **검증 전 테스트넷 실험**이다.
구조·유량·방어·비용·수량·데이터 gate를 모두 사용하지만, 아직 없는
체결 조건부 손익·승률의 통계 gate는 승인 근거로 주장하지 않는다.
승인 결정마다 `EXPERIMENTAL_TESTNET_UNVALIDATED`를 기록한다.
mainnet 실행 경로는 제공하지 않는다.

`calibrated` 모드는 모델 버전, symbol, horizon, 설정 SHA-256,
out-of-sample 구분, 방향·age별 동결된 survival 모델 및 보정 당시 요율을 요구한다.
현재 요율이 보정 요율보다 높으면 차단한다. cell은 `Buy/Sell:offset:age_bucket`이다.
각 cell은 최소 100개 에피소드, block bootstrap,
`net_pnl_lower_95 > 0`,
`normal_exit_lower_95 >= max(0.70, break_even_probability+0.05)`를 요구한다.
파일이 없거나 적격 cell이 없으면 진입하지 않는다.

이 작업에서 원본의 14일 train + 7일 calibration + 7일 test와
4개 walk-forward test 구간을 만들지는 않았다. 실제 과거 자료로 그 검증을
수행하기 전에는 알파·승률·수익성을 입증했다고 해석하면 안 된다.
배경·공급 측정은 실행 시점 관측값이고 calibrated hazard는 실행 중 학습하지 않는다.

## 준비·실행·중지

프로젝트 폴더에서 기존 Python으로 실행한다. 현재 환경에는 필요한 패키지가 있다.
새 환경에서는 `python -m pip install -r requirements-liquidity-mm.txt`로 설치한다.

```powershell
# 읽기 전용 인증·수수료·계정·private WS 확인
python -m vibetrading.liquidity_mm check

# 주문 없는 관측 실행
python -m vibetrading.liquidity_mm run --mode shadow --duration 10m

# 별도 운용 지시를 받은 뒤, 30분 테스트넷 주문 실행
python -m vibetrading.liquidity_mm start --mode testnet --duration 30m

# 2시간을 터미널에서 실행하는 경우
python -m vibetrading.liquidity_mm run --mode testnet --duration 2h

# 실행 상태와 마지막 결과
python -m vibetrading.liquidity_mm status

# 실행 프로세스에 중지 요청. 주문 취소·위험감축·대사 후 종료
python -m vibetrading.liquidity_mm stop
```

`start`는 Windows에서 숨겨진 별도 프로세스를 실행하므로 채팅 응답 이후에도
지정 기간 동안 동작한다. 인증·계정 상태는 시작할 때 다시 확인한다.
동일 API 계정의 중복 전략 프로세스는 OS 파일 잠금으로 차단한다.
기간을 생략한 무기한 운용은 허용하지 않는다. 범위는 1초~7일이다.
새 운용 지시 없이 재시작하거나 부팅 시 자동으로 운용하지 않는다.
PC가 켜져 있고 네트워크와 Python 프로세스가 유지되어야 한다.

사용자가 “테스트넷에서 30분 운용해”라고 지시하면 `start --duration 30m --mode testnet`으로
즉시 시작할 수 있다. 기간이 빠진 경우에만 기간을 확인한다.
수익성 통계 부재 때문에 진입을 강제하지 않고 설정된 테스트넷 실험 정책대로 실행한다.

## 기록과 진단

기본 경로 `runtime_data/liquidity_mm/`:

- `preflight.json`: 키 값 없는 최신 준비 확인.
- `status.json`: PID, 상태, 재고, 최악 예약, 주문·체결·거절 사유, 마지막 결정.
- `<run-id>.sqlite3`: 수신순 공개·private 이벤트, 의사결정, 주문 의도, 실행 ID, 대사, 메타데이터.
- `<run-id>-report.json`: 종료 결과, 실제 수수료 차감 실현손익, equity 변화, 잔여 노출 확인.
- `<account-hash>.risk.sqlite3`: 재시작을 견디는 일중 손실·고점·HALT.
- `launch-<time>.log`: 백그라운드 실행 stdout/stderr.

raw journal 512MiB 초과 또는 남은 디스크 512MiB 미만이면 신규 노출을 중단하고
정리한다. 임의로 기존 시장 자료를 삭제하지 않는다.

```powershell
python -m pytest tests/test_liquidity_mm.py -q
python -m vibetrading.liquidity_mm replay runtime_data/liquidity_mm/<run-id>.sqlite3 --latency 5
python -m vibetrading.liquidity_mm replay runtime_data/liquidity_mm/<run-id>.sqlite3 --latency 10
python -m vibetrading.liquidity_mm replay runtime_data/liquidity_mm/<run-id>.sqlite3 --latency 20
```

리플레이는 실제 수신순으로 core 신호를 다시 계산하고 신규·취소·통지 지연을 적용한다.
동일 가격의 실제 반대 공격 체결만 FIFO queue를 소진한다. quote touch,
취소에 따른 queue 이득, 주문 활성화 전 체결, 동일 거래량 중복 사용은 인정하지 않는다.
종료 재고는 haircut 적용 실제 book VWAP와 adverse slippage·taker 비용으로 평가한다.

현재 리플레이는 진단용이다. 완전한 source/receive 이중 타임라인의 거래소 내
순서, native stop·soft invalidation의 모든 운영 race, 실제 펀딩 정산,
일자별 bootstrap·walk-forward 자동 보정까지 구현한 수익성 인증기가 아니다.
리플레이 출력의 `statistically_validated`는 false다. 이 결과만으로
calibrated 증거 파일을 만들어 승인을 받지 않는다.

## 이번 준비 작업의 검증 결과

2026-10-03 테스트넷의 읽기 전용 조회와 private WS 인증을 확인했다.
maker 요율은 0.0002, taker 요율은 0.00055였으며 포지션과 미체결 주문은 0건이었다.
150초 shadow 관측, 백그라운드 시작, 조기 중지, 지정 기간 자동 종료를 확인했다.
준비 작업에서 실제 테스트넷 주문은 제출하지 않았다.

전략·Bybit 연결 계층·설정·로그 관련 회귀 검사는 70개 통과했다.
전체 검사는 288개 통과, 2개 실패, 1개 skip, 2개 deselect였다.
실패는 기존 crypto 배포 테스트의 volume 기대값과 보존시간 기대값이다:

- `test_crypto_release_does_not_recreate_market_or_mount_old_source`: 현재 별도 crypto_state mount를 기대값이 반영하지 않음.
- `test_crypto_release_activates_all_safe_retention_gates_together`: 현재 43,200초 설정에 대해 테스트가 172,800초를 기대함.

이 작업은 해당 배포 파일을 수정하지 않았다.
5/10/20초 지연의 관측 로그 리플레이 모두 주문 후보가 0건이었다.
이는 기록·재생 경로 확인이며 수익성 검증 결과가 아니다.
검증 기록은 `docs/evidence/liquidity-mm-setup-20261003.json`에 저장했다.

## Bybit에서 확인한 의미

- [연결·테스트넷 주소·인증](https://bybit-exchange.github.io/docs/v5/ws/connect):
  테스트넷 공개/개인 WS와 주기적인 ping을 사용한다.
- [L2 snapshot/delta](https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook):
  snapshot 교체, 0 수량 삭제, update ID와 cross sequence를 사용한다.
  한 종목의 cross sequence는 연속 번호라고 가정하지 않는다. 역행·snapshot 미확인 delta·연결 손실은 차단한다.
  TCP 내 정상 전달은 순서대로 처리하지만 feed 내부의 모든 누락을 단독 seq로 증명할 수는 없다.
- [공개 체결](https://bybit-exchange.github.io/docs/v5/websocket/public/trade):
  고유 trade ID, taker side, source 체결 시각을 사용한다.
- [주문 생성](https://bybit-exchange.github.io/docs/v5/order/create-order):
  PostOnly, reduceOnly, 고유 orderLinkId 및 ACK 이후 private 확인을 적용한다.
- [private 실행](https://bybit-exchange.github.io/docs/v5/websocket/private/execution):
  execId, execTime, execQty, execFee, isMaker로 장부를 확정한다.
- [계정 요율](https://bybit-exchange.github.io/docs/v5/account/fee-rate):
  fee=0 가정을 하지 않고 계정별 maker/taker 요율을 조회한다.

일반 API 계정의 DCP 지원을 검증하지 않았으므로 cancel-on-disconnect가
설정돼 있다고 표시하지 않는다. 로컬 TTL, 원격 stop 요청, 별도 중지 명령과
REST 대사를 사용한다. 프로세스 강제 종료/PC 절전 시 로컬 TTL은 동작하지 않는다.
