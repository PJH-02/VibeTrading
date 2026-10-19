# Crypto MM Strategy Revision Context for Codex

> 목적: 기존 크립토 MM/FV 전략의 실패 원인, EDA 결과, 이번 대화에서 합의한 전략 수정 방향, maker-only 체결 모델, 신규 상태 기반 전략 후보, 향후 EDA/파일럿/백테스트 요구사항을 하나의 SSOT 문서로 통합한다.  
> 사용 대상: Codex를 이용한 코드 수정, 전략 재설계, 체결 시뮬레이터 개선, 연구 파이프라인 구현.  
> 기준 보고서:
> - `REPORT_KO_1st.md`: 네 가지 FV 전략 실패 진단 + 기초 지표 시간축별 실증
> - `REPORT_KO_2nd.md`: 유동성 국면, 범위 평균회귀, 수급 신호 이후 실제 가격 이동
>
> 중요: 아래 내용은 **(A) 기존 보고서에서 실측/확인된 사실**, **(B) 이번 대화에서 새로 제안된 전략 변경안**을 구분한다. 새 변경안은 아직 수익성이 검증된 전략이 아니다.

---

# 0. 최종 방향 요약

기존 접근은 대체로 다음 구조였다.

1. 하나의 신호를 만든다.
2. 해당 신호로 `n초 뒤 정확한 FV` 또는 미래 mid를 회귀식으로 예측한다.
3. `예상 FV - 주문가격 > 비용`일 때 maker 주문을 낸다.
4. 이후 가격이 FV에 도달하거나 조건이 사라지면 청산한다.

기존 실험 결과 이 구조에는 세 가지 문제가 있었다.

- 단기 미시구조 지표는 **방향 정보는 존재하지만 이동폭 예측력은 작고 불안정**했다.
- OFI/체결 등은 이미 발생한 price impact와 미래 residual alpha가 섞여 있었다.
- 실제 파일럿에서는 **0체결**이었고, 전략 alpha보다 주문 수명/취소/지연 모델이 먼저 실패했다.

따라서 다음 구조로 전환한다.

```text
시장 상태 추정
├─ Direction State
│  ├─ L1/L2 OBI
│  ├─ OFI onset / persistence
│  ├─ Trade Flow + Liquidity Supply state transition
│  └─ Cross-Venue dislocation origin
│
├─ Move Magnitude / Jump Risk State
│  ├─ queue depletion
│  ├─ spread widening
│  ├─ next-level gap
│  └─ short-horizon realized activity
│
├─ Volatility / Liquidity State
│  ├─ displayed liquidity / trade rate ratio R
│  ├─ spread
│  ├─ realized volatility
│  └─ event intensity
│
└─ Inventory State
   └─ actual position / fill history / exposure

↓ deterministic policy

(price, size, side, TTL, cancel/replace, one-sided quoting)
```

핵심 목표는:

> **“정확한 n초 뒤 FV 한 점”을 맞추는 것보다, 현재 mid가 어느 방향으로 얼마나 강하게/위험하게 움직일지 추정하고 maker quote를 조절한다.**

최종 정책 구조는 다음을 지향한다.

$$
(Direction_t,\ Magnitude_t,\ Volatility_t,\ Inventory_t)
\rightarrow
(quote\ price,\ size,\ side,\ TTL)
$$

---

# 1. 기존 실험에서 확인된 핵심 사실

## 1.1 원래 네 전략

- S1: 순유동성 OFI 기반 FV
- S2: Binance anchor 기반 FV
- S3: 공격 체결 지속 기반 FV
- S4: BBO OBI 기반 FV

원래 파일럿 경로는 대략:
- S1 = BTC
- S2 = ETH
- S3 = SOL
- S4 = XRP

전략별 종목이 달랐으므로 기존 PnL/성과를 전략 간 직접 순위 비교하면 안 된다.

---

## 1.2 기존 파일럿 운용 결과

### 테스트넷
- Bybit/GRVT 테스트넷
- 약 30분
- Bybit S4 주문 1건
- 최종 체결 0
- 최종 PnL 0
- 테스트넷 가격은 실제 시장 검증 자료로 사용하지 않음

### 실제 공개호가 pilot
- 약 30분
- 총 16개 경로
- 기본 지연 주문 의도 15건
  - Arcus S3: 14건
  - Bitget S1: 1건
- 활성 기록 6건
- 가정 체결 0건
- 2배 latency에서도 체결 0
- 수수료/PnL/종료재고 0

### 원래 FV 평가
- 10분 사전 학습
- 30분 평가
- H = 30초
- 12개 경로 FV 계산 가능
- 4개 경로 배포 차단
- 최대 RMSE 감소 약 2.71%
- 주 검정에서 BH q < 0.05 통과 0개

**중요 해석:**
`PnL = 0`은 전략이 손실을 피했다는 뜻도, 수익성이 없다는 통계적 증거도 아니다.  
체결이 없었기 때문에 **fill-conditioned PnL 자체가 미측정**이다.

---

# 2. 가장 먼저 수정해야 할 체결 모델: maker-only 의미 재정의

## 2.1 사용자의 원래 의도

사용자는 원칙적으로 **모든 정상 주문을 maker로 수행**하려고 한다.

의도한 구조:

### 진입
1. 현재 best bid / best ask에 maker 주문 제출 후 체결 대기
2. spread가 충분히 넓으면 inside spread에 더 공격적인 maker quote 가능
3. 신호가 충분히 강할 때 BBO를 따라 quote 이동
4. 가능한 한 taker fee를 피한다

### 청산
1. 예상 목표가격/회귀가격에 maker 주문 제출 후 대기
2. 필요하면 현재 BBO까지 quote를 당겨 체결확률을 높임
3. 정상 상황에서는 cross하지 않음
4. maker 체결로 재고 해소

---

## 2.2 기존 보고서와의 불일치

기존 보고서 S2 비용 비교에는:

```text
maker 진입 + taker 감축
```

비용 가정이 들어갔다.

따라서 기존 비용모델은 사용자의 maker-only 의도와 일치하지 않는다.

실제 pilot에서는 체결 자체가 없었으므로 **실제 taker 청산이 발생한 것은 아니다**.  
문제는 비용 추정 및 전략 명세가 maker-only와 불일치했다는 점이다.

---

## 2.3 반드시 수정해야 하는 주문 의미

일반적인 CLOB에서:

```text
best bid = 100
best ask = 101
```

일 때

- `Buy @ 100` → maker
- spread가 2tick 이상이면 `Buy @ 100.5` → maker 가능
- `Buy @ 101` → 기존 ask를 즉시 제거하므로 taker
- `PostOnly Buy @ 101` → 일반적으로 reject/cancel/reprice

즉:

> **반대편 best quote를 즉시 체결하면서 maker 수수료를 받는 구조는 일반 CLOB에서는 불가능하다.**

따라서 simulator 및 전략 코드에서 maker/taker 판정을 정확히 구현해야 한다.

---

## 2.4 maker-only와 즉시 stop-loss의 충돌

maker-only를 절대 제약으로 두면:

- 포지션이 손실 방향으로 급격히 움직일 때
- 즉시 반대 BBO를 hit해서 청산할 수 없음
- 따라서 hard stop을 가격 수준만으로 보장할 수 없음

정상 maker-only emergency action:

```text
1. 신규 반대 재고 증가 주문 중단
2. 청산 side quote를 BBO까지 공격적으로 이동
3. 주문 size 증가 가능
4. 반대 side quote 취소
5. 체결 대기
```

따라서 다음 정책을 코드 수준에서 명시해야 한다.

### 정책 후보 A
- 정상 운용 100% maker-only
- emergency taker 없음

### 정책 후보 B
- 정상 운용 maker-only
- hard risk threshold 초과 시에만 emergency taker 허용
- emergency taker는 별도 PnL/빈도로 기록

현재 사용자의 기본 방향은 **정상 주문 maker-only**이며, emergency taker 사용 여부는 명시적 옵션으로 분리해야 한다.

---

# 3. 기존 파일럿의 핵심 실행 실패: 주문 수명 문제

실제 공개호가 pilot의 15개 주문 의도는 모두 가정 주문 도착 전에 cancel 요청이 발생했다.

### 생성 → 취소 시간
- median: 약 3.468ms
- range: 약 0.136ms ~ 151.635ms

### 사용된 주문 도착 latency
- Arcus/Bitget에 GRVT 테스트넷 약 1080ms 값을 대용
- 각 mainnet 실측 latency가 아님

즉 실제 구조는 대략:

```text
t =   0ms : maker order intent 생성
t =   3ms : 아주 작은 FV / quantity 변화로 cancel
t = 1080ms: 원래라면 주문 도착
```

이 구조에서는 주문이 제대로 살아 있을 수 없다.

심지어 동일 가격에서 size가 아주 미세하게 바뀐 것만으로 0.15ms 뒤 cancel된 사례가 있었다.

---

# 4. 체결 시뮬레이터 신규 요구사항

Codex는 전략 수정 전에 **체결 시뮬레이터부터 수정**해야 한다.

## 4.1 핵심 원칙

현재처럼:

```text
매 이벤트마다 FV 재계산
→ 가격/수량 조금 바뀜
→ 기존 주문 즉시 취소
→ 새 주문 재생성
```

하면 안 된다.

대신 주문을 하나의 stateful object로 관리해야 한다.

---

## 4.2 Order state schema

예시:

```python
OrderState = {
    "order_id": str,
    "side": "BUY" | "SELL",
    "price": float,
    "qty": float,
    "remaining_qty": float,

    "created_ts": int,
    "sent_ts": int | None,
    "ack_ts": int | None,
    "active_ts": int | None,
    "cancel_requested_ts": int | None,
    "cancel_effective_ts": int | None,

    "status": (
        "INTENT"
        | "IN_FLIGHT"
        | "ACTIVE"
        | "PARTIAL"
        | "FILLED"
        | "CANCEL_PENDING"
        | "CANCELLED"
        | "REJECTED"
    ),

    "queue_ahead_est": float | None,
    "maker": bool,

    "signal_snapshot": dict,
    "inventory_snapshot": float,
    "reason_open": str,
    "reason_cancel": str | None,
}
```

---

## 4.3 최소 주문 수명

새 quote가 기존 quote와 실질적으로 다르지 않으면 cancel/replace하지 않는다.

예:

```text
price unchanged
AND
qty change < qty_tolerance
AND
state unchanged
AND
risk threshold not breached
```

이면 기존 주문 유지.

추가 파라미터:

```text
min_quote_lifetime_ms
reprice_tick_threshold
resize_pct_threshold
state_transition_required_for_cancel
```

---

## 4.4 cancel 조건

취소는 최소 다음 중 하나일 때만:

1. 가격이 N tick 이상 비경제적으로 stale
2. Direction state가 반대 방향으로 전이
3. Jump-risk state 진입
4. Inventory limit 접근/초과
5. 주문이 예상 adverse-selection 위험 side에 노출
6. max TTL 만료
7. 거래소 book 상태 비정상
8. strategy kill-switch

단순한 소수점 quantity 변화로 cancel 금지.

---

## 4.5 Queue model

maker 전략 검증에서 반드시 필요.

최소 모델:

```text
initial_queue_ahead
- opposite_aggressive_trade_volume
- queue_cancel_ahead_estimate
= remaining_queue_ahead
```

보수적으로:

- 같은 가격 level에서 체결 volume이 누적되어 queue ahead를 소진
- cancel attribution 불확실 시 보수적 fill 모델과 낙관적 fill 모델 둘 다 제공
- partial fill 허용
- quote 이동 시 queue position reset

필요한 출력:

```text
fill_probability
time_to_first_fill
time_to_full_fill
partial_fill_ratio
queue_ahead_at_fill
```

---

## 4.6 Post-only 검증

모든 정상 주문:

```text
assert order_is_non_marketable
```

이어야 한다.

- BUY price >= current best ask → post-only reject/cancel
- SELL price <= current best bid → post-only reject/cancel

거래소별 post-only behavior 차이는 adapter에서 처리.

---

## 4.7 Latency

거래소별 각각 측정/설정.

필요:

```text
market_data_receive_latency
decision_latency
order_send_latency
exchange_ack_latency
cancel_latency
```

하나의 GRVT testnet latency를 Arcus/Bitget mainnet에 공통 적용 금지.

---

## 4.8 Event ordering

동일 처리 loop에서 arrival/cancel 시각이 모두 과거가 된 경우 cancel을 먼저 처리하는 식의 artifact 방지.

모든 이벤트를:

```text
(event_effective_ts, priority)
```

로 정렬한 deterministic event queue에서 처리.

예:

```text
ORDER_ARRIVE
TRADE
BOOK_UPDATE
FILL
CANCEL_REQUEST
CANCEL_EFFECTIVE
```

순서를 명시적으로 관리.

---

# 5. S1 수정: OFI 단독 FV → 단기 Direction / Quote Skew 신호

## 5.1 기존 S1

기본 아이디어:

```text
price-aware BBO OFI
→ depth normalization
→ coefficient beta
→ 미래 FV 조정
```

형태.

즉:

$$
FV_t = Mid_t + \beta \cdot OFI_t
$$

---

## 5.2 기존 문제

보고서상:

- OFI 계산식 자체가 뒤집힌 단순 버그는 아님
- 짧은 학습기간에서 계수 불안정
- venue별/기간별 beta 부호가 변함
- OFI는 **동시 price impact 설명력은 강하지만 미래 residual prediction은 약함**

예:

### W = 5초, pilot
- 이미 발생한 가격변화 H=0 설명 RMSE 개선: 약 20.10%
- 미래 H=1초: 약 +0.53%
- 미래 H=5초: 약 +0.56%
- H=30초: 약 -0.50%
- H=180초: 약 -10.87%

### production W = 5초
OFI RMSE 개선 중앙값:
- H=1: 0.43%
- H=5: 0.94%
- H=10: 0.84%
- H=30: 0.45%
- H=60: 0.16%
- H=120: 0.16%
- H=180: 0.18%

Direction BA:
- H=1: 71.29%
- H=5: 65.55%
- H=10: 61.76%
- H=30: 55.74%
- 이후 50% 근처로 감소

---

## 5.3 새로운 S1 해석

OFI는 정확한 장기 FV보다:

```text
최근 order flow shock가
아직 residual direction 정보를 남기고 있는가?
```

를 보는 것이 더 적합.

따라서 S1 역할을:

```text
FV predictor
→ short-horizon directional pressure / quote skew signal
```

로 변경.

---

## 5.4 신규 후보 feature

```text
OFI level
OFI onset
OFI persistence
OFI acceleration
OFI sign flip
OFI / average depth
OFI relative to current spread
```

특히 단순 fixed 30s target보다:

```text
next price move
1s / 2s / 5s / 10s
```

또는 fill-conditioned markout target을 우선.

---

# 6. S4 수정: BBO OBI → 핵심 Direction State 후보

## 6.1 기존 S4

L1 OBI:

$$
OBI_t =
\frac{Q_{bid}-Q_{ask}}
{Q_{bid}+Q_{ask}}
$$

기존에는 OBI × spread 등으로 FV 조정.

---

## 6.2 EDA 핵심 결과

### production, W=5초

L1 OBI RMSE 개선:
- H=1: 0.03%
- H=5: 0.68%
- H=10: 0.95%
- H=30: 0.91%
- H=60: 0.67%

L1 OBI direction BA:
- H=1: 73.32%
- H=5: 69.04%
- H=10: 67.76%
- H=30: 61.45%
- H=60: 58.50%

L2 OBI는 대체로 L1보다 비슷하거나 약함.

### pilot 기간 제외 production
L1 OBI high-low 25초 평균 이동차:
- 중앙값 약 +0.568bp
- 12개 venue×asset 경로 모두 평균 차이 양수

### signal strength
H=25초에서:
- weak L1 OBI aligned mean: 약 0.027bp
- strong L1 OBI aligned mean: 약 0.351bp
- strong - weak: 약 +0.324bp

### onset
L1 OBI onset 후:
- H=5 mean: 약 +0.210bp
- H=25 mean: 약 +0.430bp
- H=60 mean: 약 +0.511bp
- H=180 mean: 약 +0.556bp

하지만:
- H=25 median: 약 +0.083bp
- H=5 median: 0bp

즉:

> 평균 방향성 정보는 존재하지만 많은 개별 이벤트는 flat 또는 반대 방향이며 이동폭이 작다.

---

## 6.3 신규 역할

OBI를:

```text
정확한 FV 예측 변수
→ 가장 중요한 short-horizon Direction State
```

로 사용.

예:

```text
D_OBI =
StrongSell
Sell
Neutral
Buy
StrongBuy
```

threshold는 고정 수치보다 train-period quantile로 동결.

---

## 6.4 L1 vs L2

현 데이터에서는 L1이 충분히 강하고 L2가 명확히 우월하지 않음.

우선순위:

1. L1 OBI
2. L2 OBI는 보조
3. deeper book은 jump-risk / next-level liquidity 용도로 별도 사용

---

# 7. S2 수정: Binance anchor OLS → Cross-Venue Dislocation State

## 7.1 기존 S2 문제

기존 구조:

$$
FV^{local}_t
=
P^{local}_t
+
\lambda(P^{Binance}_t-P^{local}_t)
$$

문제:

기존 regression target의 \(\lambda\)를
“현재 gap 중 미래에 수렴하는 비율”로 해석했지만 실제 target에는:

1. gap convergence
2. Binance 자체 미래 이동

이 함께 섞여 있었다.

항등적으로:

$$
\Delta P_{local}
=
\Delta P_{Binance}
+
(Gap_t-Gap_{t+H})
$$

따라서 기존 lambda는 순수 convergence coefficient가 아니었다.

기존 예:
- Arcus total lambda -0.291
  - convergence component +0.512
  - Binance move component -0.803
- Bybit +1.112
  - convergence +0.750
  - Binance move +0.362
- Bitget +1.744
  - convergence +0.977
  - Binance move +0.767
- GRVT +0.142
  - convergence +0.081
  - Binance move +0.061

따라서 기존 `lambda outside (0,1] → 전략 차단`은 논리적으로 부적절.

---

## 7.2 기존 추가 문제: gap < 비용

pilot의 보정 gap 중앙값:
- 약 0.30 ~ 0.85bp

기존 보고서 비용가정:
- maker entry + taker unwind 기준 약 2.25 / 7.5 / 8 / 4.5bp

따라서 기존 비용가정에서는 중앙 gap 자체가 거래비용보다 작았다.

단, 앞으로는 maker-only 비용모델로 재평가해야 하므로 기존 cost hurdle을 그대로 사용하지 않는다.

---

## 7.3 신규 S2 핵심 아이디어

OLS 회귀를 버리고:

> **Binance와 local 간 장기 baseline spread를 추정한 뒤, spread가 평소보다 확대될 때 local의 재수렴 또는 catch-up을 거래한다.**

정의:

$$
s_t =
10^4\left(\log P^L_t-\log P^B_t\right)
$$

baseline:

$$
b_t =
\operatorname{robust\ baseline}(s_{t-T:t})
$$

dislocation:

$$
e_t=s_t-b_t
$$

예:
- \(e_t>0\): local이 Binance 대비 평소보다 비쌈
- \(e_t<0\): local이 평소보다 쌈

---

## 7.4 baseline은 고정 mean이 아님

크립토 거래소 간 spread는 다음 때문에 drift 가능:
- USD vs USDT
- funding
- contract specification
- venue-specific demand
- liquidity regime
- latency
- persistent basis

따라서 baseline 후보:
- rolling median
- robust EWMA
- rolling quantile center
- state-space slow mean

우선 단순하고 해석 가능한 방법을 선호.

---

## 7.5 baseline 검증

필수 EDA:

### Stationarity
- rolling ADF
- rolling KPSS

둘을 함께 사용:
- ADF만 통과했다고 stationarity 선언 금지
- KPSS와 함께 regime drift 확인

### Dependence
- ACF/PACF
- Ljung-Box
- spread half-life

### Distribution
- QQ
- Anderson-Darling
- 필요 시 Shapiro는 참고
- 정규성 가정 금지
- median/MAD/quantile 기반 threshold 우선

---

# 8. S2의 가장 중요한 수정: dislocation origin 분해

단순:

```text
spread 확대 → 무조건 local mean reversion
```

은 위험.

왜냐하면 unhedged local position이기 때문.

예:

```text
Binance = 100
Local   = 101
Local short entry
```

이후:

```text
Binance = 102
Local   = 102
```

이면 spread는 정상화됐지만 local short는 손실.

즉:

$$
spread\ convergence
\not\Rightarrow
unhedged\ PnL > 0
$$

---

## 8.1 반드시 구분할 두 사건

### Case A: Binance impulse, local lag

예:

```text
Binance +20bp
Local    +3bp
```

→ local이 baseline 대비 싸짐

해석:
- Binance가 price discovery
- local이 아직 따라가지 않음
- **local catch-up long 후보**

---

### Case B: Local impulse, Binance stable

예:

```text
Binance +1bp
Local   +20bp
```

해석:
- local-specific dislocation
- local 가격이 평소 spread보다 과도하게 위
- **local mean-reversion short 후보**

---

## 8.2 추가 상태

```text
Case C: 둘 다 비슷하게 큰 방향 이동
→ common market shock
→ trade skip / low conviction

Case D: price는 거의 안 움직이는데 spread만 이상
→ stale quote / timestamp / latency / market-data issue 가능
→ trade skip
```

---

## 8.3 신규 S2 signal schema

```python
CrossVenueState = {
    "spread_bp": float,
    "baseline_bp": float,
    "residual_bp": float,

    "binance_return_bp": float,
    "local_return_bp": float,

    "origin": (
        "BINANCE_LEADS"
        | "LOCAL_IDIOSYNCRATIC"
        | "COMMON_SHOCK"
        | "STALE_OR_UNKNOWN"
    ),

    "direction": -1 | 0 | +1,
    "strength": float,

    "baseline_stationary": bool,
    "expected_convergence_bp": float | None,
}
```

---

# 9. S2 진입/청산 후보

## 9.1 Entry

기본:

$$
|e_t|>\theta_{entry}
$$

그리고:

$$
|e_t|-C_{RT}>M
$$

여기서:
- \(C_{RT}\): maker-only 예상 왕복 비용
- \(M\): model uncertainty + adverse selection buffer

추가:
- origin이 명확해야 함
- baseline stationarity 조건 통과
- latency/stale data 이상 없음
- local book liquidity 충분

---

## 9.2 Partial Exit

사용자가 제안한 구조:

### TP1
spread residual이 sufficiently 축소되어:

```text
이미 확보된 convergence ≳ round-trip cost
```

가 되면 50% 청산.

예:

$$
|e_t|\le\theta_1
$$

### TP2
나머지 50%는:

$$
|e_t|\le\theta_2
$$

또는 baseline 도달 시 최종 청산.

---

## 9.3 단순 residual exit의 문제

unhedged local-only position이므로 exit은:

```text
residual
+
actual PnL
+
Binance momentum
+
time
```

을 같이 봐야 한다.

즉:

```python
exit_state = f(
    residual_spread,
    unrealized_pnl,
    binance_short_term_direction,
    local_direction,
    holding_time,
    current_cost_to_exit,
)
```

spread만 정상화됐지만 position PnL이 음수인 사례를 방지.

---

# 10. S3 수정: Trade Imbalance 단독 → Flow × Liquidity Supply State Machine

## 10.1 기존 S3

Trade imbalance:

$$
TI=
\frac{V_{buy}-V_{sell}}
{V_{buy}+V_{sell}}
$$

기존 해석:
- TI > 0 → buy
- TI < 0 → sell

---

## 10.2 기존 문제

단독 aggressive flow는:

```text
수요 측만 보고
반대쪽 LP / 공급 반응을 보지 않음
```

예:

```text
Aggressive buy 100
Ask refill 100
Price unchanged
```

이면 buy flow가 강해도 가격이 오르지 않는다.

기존 EDA에서 trade imbalance는 OBI/OFI보다 약했다.

production W=5초:
- H=5 RMSE improvement 약 +0.11%
- H=10 +0.14%
- H=30 +0.05%
- H=60 -0.02%

Direction BA:
- H=5 약 59.3%
- H=10 약 56.9%
- H=30 약 53.3%

또한 사전 계수와 평가 기울기 부호가 venue별로 뒤집히는 사례 존재.

---

# 11. 신규 S3: State transition 기반 모델

핵심:

> **체결 수요 + 반대쪽 호가 공급 반응을 같이 본다.**

단순 동시 interaction이 아니라 **event sequence / state transition**이 핵심.

---

## 11.1 Buy-side 예시

### State 0 — Neutral

```text
TI ≈ 0
No persistent aggressive flow
```

---

### State 1 — Buy Pressure + Absorption

조건 후보:
- TI > positive threshold
- aggressive buy 지속
- best ask가 거의 안 올라감
- 상당한 buy volume이 ask에서 체결
- ask quantity가 계속 replenish
- ask price resilience 큼

해석:

> 매수세가 공격하고 있지만 sell-side LP가 충분히 받아내는 상태

이 상태에서는 즉시 long하지 않는다.

---

### State 1-1 — Absorption Wins / Buy Exhaustion

State 1 이후:

- TI 감소
- buy aggressor intensity 감소
- ask refill 지속
- ask price 상승 실패
- bid-side OBI/OFI도 약화 가능

해석:

> 매수자가 많은 체결을 만들었지만 가격을 못 올렸고 결국 힘이 약해짐

액션 후보:
- short
- 기존 long inventory reduction
- buy quote cancel / widen
- ask quote aggressive maker placement

---

### State 1-2 — Seller Exhaustion / Breakout

State 1 이후:

- TI 양수 유지 또는 증가
- ask queue 감소
- refill ratio 감소
- best ask가 한 tick 이상 상승
- next ask level도 빠르게 소진
- spread 확대 가능

해석:

> LP sell-side supply가 aggressive buy를 더 이상 흡수하지 못함

액션 후보:
- long
- sell quote cancel/widen
- bid quote aggressive maker placement

---

## 11.2 Sell-side는 완전 대칭

State 2:
- aggressive sell + bid absorption

State 2-1:
- sell exhaustion → upward reversal

State 2-2:
- buyer liquidity exhaustion → downside continuation

---

# 12. S3 event-driven feature

fixed 5초가 전략 본질이 되어서는 안 된다.

buy-pressure onset \(t_0\) 이후 누적:

$$
V_B(t)
=
\sum_{u=t_0}^{t} AggressiveBuyVolume_u
$$

$$
R_A(t)
=
\sum_{u=t_0}^{t} ObservedAskRefill_u
$$

$$
D_A(t)
=
AskDepletion_t
$$

$$
J_A(t)
=
AskPriceTicksMoved_t
$$

흡수:

```text
V_B large
R_A large
J_A ≈ 0
```

소진/돌파:

```text
V_B large
R_A / V_B falling
D_A increasing
J_A > 0
```

---

# 13. 기존 Replenishment 실험을 새 S3의 반증으로 보면 안 되는 이유

기존 EDA에서:
- net visible additions
- depletion
- replenishment
- trade × replenishment

성과는 대체로 약했다.

하지만 기존 실험은 주로:

```text
동일 시점의 정적 interaction
```

이고 새 아이디어는:

```text
State 1
→ State 1-1
or
→ State 1-2
```

라는 **transition**이다.

따라서 기존 약한 interaction 결과만으로 새 S3를 기각하면 안 된다.

---

# 14. Replenishment 데이터 한계

현재 공개 L2에서는:
- order ID 없음
- 실제 신규 주문
- 취소
- 체결 후 refill
- hidden liquidity

를 완전히 분해할 수 없다.

현재 측정 가능한 것은 대체로:

```text
top-N 공통 가격 level에서
visible net quantity 변화
```

수준.

따라서 true gross refill이 아니라 proxy.

Codex는 변수명도:
- `observed_net_refill`
- `visible_refill_proxy`

처럼 명확히 표기.

실제 `gross_refill` 또는 `cancel_volume`으로 이름 붙이지 말 것.

---

# 15. OBI + sudden jump + spread widening을 이용한 MM

실제 크립토 특성:
- OBI extreme
- 한쪽 queue 급격히 소진
- 다음 price level이 멀음
- spread가 갑자기 확대
- price jump 발생

가능.

이것을 단순 directional alpha뿐 아니라 **quote / inventory control**에 사용.

---

# 16. 중요한 수정: spread widening 자체는 재고 처리가 아니다

양쪽 quote를 동시에 멀리하면:
- 추가 fill을 줄일 뿐
- 기존 inventory가 자동으로 감소하지 않음

재고 처리를 위해서는 **asymmetric quote skew** 필요.

---

## 16.1 Long inventory

현재:

$$
I_t>0
$$

이면:

### Bid
- price farther
- size smaller
- 필요 시 cancel

### Ask
- price closer
- size larger
- BBO join or inside-spread maker

목표:
- 추가 long fill 억제
- sell fill 확률 증가

---

## 16.2 Short inventory

반대:
- ask farther / smaller
- bid closer / larger

---

# 17. Jump Risk State 추가

Volatility state를 단순 Calm/High 두 개로 두지 말고 최소:

```text
NORMAL
THIN_BOOK
JUMP_RISK
```

후보.

### JUMP_RISK 입력
- extreme L1 OBI
- same-direction OFI surge
- opposite queue depletion
- refill failure
- spread widening
- next-level gap large
- trade intensity spike

### JUMP_RISK action
- vulnerable side quote cancel
- 양쪽 기본 quote width 확대
- order size 축소
- inventory max 축소
- inventory가 있으면 unwind side만 maker quote
- 신규 재고 확대 side quote 중단

---

# 18. 신규 통합 구조: Direction × Magnitude × Volatility × Inventory

## 18.1 Direction State

예:

```text
StrongSell
Sell
Neutral
Buy
StrongBuy
```

입력:
- L1 OBI
- L2 OBI auxiliary
- OFI onset/persistence
- S3 absorption/exhaustion transition
- S2 cross-venue dislocation state

---

## 18.2 Move Magnitude State

예:

```text
Flat
Small
Medium
Jump
```

목표:

정확한 가격 숫자보다:

> 향후 짧은 구간에서 움직임이 사실상 없을지, 작을지, 의미있을지, 점프할지를 분류

입력 후보:
- |OBI|
- |OFI|
- queue depletion
- next-level gap
- spread widening
- short-horizon event intensity
- R
- recent realized volatility

---

## 18.3 Volatility / Liquidity State

예:

```text
Calm
Normal
Stressed
```

입력:
- R = displayed liquidity / trade rate
- current spread
- short realized volatility
- book depletion
- quote/trade event rate

---

## 18.4 Inventory State

예:

```text
ShortHeavy
Short
Flat
Long
LongHeavy
```

혹은 continuous normalized inventory:

$$
I_t =
\frac{position_t}{max\_inventory}
$$

---

# 19. R: 유동성 비율의 역할

정의 개념:

$$
R(W)
=
\frac{AverageDisplayedLiquidity}
{TradeRate}
$$

직관:
- R 큼 → 현재 체결 속도 대비 book 두꺼움
- R 작음 → book 얇거나 trade pressure 큼

---

## 19.1 EDA 결과

production high-R / low-R 미래 RV 비율:

### W=5
- H=5: 약 0.375
- H=25: 약 0.524
- H=60: 약 0.605
- H=180: 약 0.670

### W=60
- H=5: 약 0.271
- H=25: 약 0.377
- H=60: 약 0.474
- H=180: 약 0.543

즉 high R에서 미래 변동성이 낮은 관계는 꽤 명확.

---

## 19.2 R의 한계

R이 낮다고 방향 정확도가 좋아지지 않았다.

예:
L1 OBI BA

### high-R
- H=5: 약 74.4%
- H=25: 약 65.1%
- H=60: 약 60.2%

### low-R
- H=5: 약 60.0%
- H=25: 약 55.3%
- H=60: 약 53.4%

따라서:

```text
R low
→ 큰 변동성 가능
≠
방향을 더 잘 안다
```

결론:

> R은 alpha 방향 신호보다 **risk / volatility regime variable**로 사용.

---

# 20. Range/Grid 평균회귀 후보의 현재 판정

기존 후보:

```text
최근 15/60분 range
→ center를 FV
→ center에서 멀어지면 mean reversion/grid
```

---

## 20.1 EDA 결과

15분 range outer-half 진입 후 30분 내:

전체:
- center first 약 32.5%
- range exit first 약 67.2%

high-R:
- center first 약 31.8%
- exit first 약 67.6%

60분 band도 비슷하게 exit이 더 많음.

비중첩 episode에서는 high-R:
- center first 약 24%
- exit first 약 71%

즉:

> high R = 조용한 시장
> 는 일부 맞지만
> high R = range center로 평균회귀
> 는 지지되지 않음

---

## 20.2 결론

현재 형태의 naive grid:

```text
R high
→ recent range center를 FV
→ grid
```

는 보류/폐기.

Range stability와 mean reversion은 별개다.

향후 grid를 다시 연구하려면:
- center stability
- band stability
- actual two-way oscillation
- fill probability
- inventory accumulation
- range-break MTM

을 별도 검증.

---

# 21. 정확한 FV 대신 Quote Skew 정책

reference price는 당장 current mid를 사용 가능.

예:

$$
p^{bid}_t
=
m_t-h(V_t)+\alpha D_t-\kappa I_t
$$

$$
p^{ask}_t
=
m_t+h(V_t)+\alpha D_t-\kappa I_t
$$

여기서:
- \(m_t\): current mid / micro-reference
- \(h(V_t)\): volatility/liquidity 기반 half-spread
- \(D_t\): directional score
- \(I_t\): inventory

해석:

### D > 0
- quote center upward shift
- bid 더 공격적
- ask 더 높은 가격으로 이동

### D < 0
- 반대

### I > 0 (long)
- quote center downward shift
- bid 덜 공격적
- ask 더 공격적
- sell fill 유도

이 구조는 정확한 미래 FV 한 점보다:
- adverse-selection control
- maker fill quality
- inventory control

에 적합.

---

# 22. 전략 모듈 재편안

기존 S1~S4를 완전히 독립 전략으로 경쟁시키기보다 다음 모듈로 통합.

## D1. Book Pressure
핵심:
- L1 OBI
- OFI onset
- OFI persistence

역할:
- short-horizon direction

---

## D2. Flow Absorption / Exhaustion
핵심:
- Trade imbalance
- aggressive flow persistence
- opposite-side refill
- queue depletion
- price response failure/success

역할:
- continuation vs reversal state transition

---

## D3. Cross-Venue Dislocation
핵심:
- Binance/local spread residual
- Binance lead
- local idiosyncratic shock
- baseline stability

역할:
- catch-up / mean reversion directional state

---

## V1. Liquidity / Jump Regime
핵심:
- R
- spread
- RV
- queue depletion
- next-level gap
- event intensity

역할:
- quote width
- size
- inventory cap
- TTL
- risk-off

---

## Inventory Controller
역할:
- quote skew
- one-sided quote
- size asymmetry
- stop-new-risk
- maker unwind

---

# 23. EDA 목표를 바꿔야 함

기존 핵심 target:

$$
P_{t+H}-P_t
$$

만으로는 MM 성과를 직접 평가하지 못함.

앞으로는 반드시:

> **fill-conditioned markout**

을 핵심 target으로 사용.

---

## 23.1 Buy maker markout

$$
MO_h^{buy}
=
10^4
\left(
\frac{Mid_{t+h}}{P_{fill}}-1
\right)
$$

---

## 23.2 Sell maker markout

$$
MO_h^{sell}
=
10^4
\left(
\frac{P_{fill}}{Mid_{t+h}}-1
\right)
$$

---

## 23.3 핵심 질문

기존:

```text
OBI +0.8이면 25초 뒤 가격이 오르는가?
```

보다:

```text
OBI +0.8 상태에서
내 BUY maker order가 실제 fill되었을 때
1/2/5/10/30초 markout이 양수인가?
```

를 검증해야 한다.

이것이 실제 MM alpha에 더 직접적.

---

# 24. 신규 EDA 요구사항

## 24.1 공통 데이터 정합성

- snapshot 정상화
- sequence continuity
- crossed/locked book 제거
- source timestamp / receive timestamp 구분
- latency anomaly 제거
- trade side unknown 구분
- 정상 구간만 사용
- 미래 label 확보 안 되는 tail 제거
- 보간으로 결측 채우지 않음

---

## 24.2 Stationarity / distribution / dependence

time-series 특성에 따라 반드시:

### Stationarity
- ADF
- KPSS

### Dependence
- ACF
- PACF
- Ljung-Box

### Distribution
- QQ
- Anderson-Darling
- 필요 시 Shapiro
- non-normal 기본 가정

---

## 24.3 Train / Validation / Test

필수:
- chronological split
- purged split
- embargo
- walk-forward

금지:
- random shuffle
- future threshold leakage
- 전체기간 quantile로 threshold 설정
- 결과 본 뒤 동일 데이터에서 재최적화

---

## 24.4 OBI/OFI EDA

각 지표별:
- signed quantile
- absolute strength quantile
- onset event
- persistence duration
- sign flip
- next price move
- future return
- fill-conditioned markout

H 후보:
- 100ms/250ms/500ms 가능하면 native event level
- 1s
- 2s
- 5s
- 10s
- 30s

현재 1초 aggregation만으로 10~500ms alpha를 놓칠 수 있음.

---

## 24.5 S3 transition EDA

각 State 1 episode에 대해:

```text
onset_ts
initial_TI
peak_TI
aggressive_volume
ask_refill_proxy
ask_depletion
ask_price_ticks_moved
time_to_transition
transition = 1-1 or 1-2
```

측정.

Target:
- transition probability
- next-price direction
- MFE
- MAE
- markout
- fill probability

---

## 24.6 S2 EDA

필수:

```text
spread residual distribution
baseline stability
spread half-life
origin classification
conditional convergence
actual local-only PnL
```

각 origin별:

### BINANCE_LEADS
- residual convergence rate
- local direction accuracy
- local maker fill markout

### LOCAL_IDIOSYNCRATIC
- local reversal probability
- residual convergence
- local maker fill markout

### COMMON_SHOCK
- trade하지 않는 것이 맞는지 검증

---

## 24.7 R / volatility EDA

R은 directional alpha가 아니라:

```text
future RV
future spread
jump probability
fill probability
adverse-selection markout
```

과 연결.

---

# 25. 성과평가: 방향 적중률만 보면 안 됨

필수 보고:

### Prediction
- balanced accuracy
- Brier score / calibration if probabilistic
- confusion matrix
- move magnitude conditional stats

### Execution
- fill rate
- partial fill rate
- time to fill
- cancel-before-fill
- post-only reject rate
- queue loss due to reprice

### PnL
- gross PnL
- maker rebate/fee
- realized PnL
- unrealized PnL
- markout-adjusted PnL
- inventory carry

### Risk
- max drawdown
- Sharpe
- Sortino optional
- VaR / CVaR
- max inventory
- inventory duration
- tail markout
- jump loss

### Efficiency
- turnover
- fills/hour
- PnL per fill
- PnL per notional
- cancellation rate

---

# 26. 거래 비용 정의

기존 `maker entry + taker unwind` 비용을 기본값으로 사용하지 말 것.

maker-only 정상 정책에서는:

```text
entry maker fee/rebate
exit maker fee/rebate
```

기준.

그러나 비용에는 fee만 넣으면 안 됨.

effective cost:

$$
C_{eff}
=
fees
+
queue\ opportunity\ cost
+
adverse\ selection
+
latency\ slippage
+
inventory\ risk
$$

특히 maker MM에서는 fee보다:
- fill-conditioned adverse selection
- missed fills
- queue loss

가 핵심.

---

# 27. 전략별 현재 판정

| 전략/모듈 | 기존 결과 | 수정 방향 | 현재 우선순위 |
|---|---|---|---|
| S1 OFI | 단기 방향정보 존재, 장기 FV 불안정 | short-horizon direction / quote skew | 높음 |
| S4 L1 OBI | 가장 일관된 direction 정보 | 핵심 Direction State | 매우 높음 |
| L2 OBI | 정보 있음, L1 대비 명확한 우월성 없음 | 보조 + depth/jump 정보 | 중상 |
| S2 Binance anchor | 회귀 target 해석 오류 | baseline residual + origin classification | 높음 |
| S3 Trade imbalance | 단독 성능 약함 | flow × liquidity supply transition | 높음 |
| Replenishment | 기존 proxy 단독 약함 | S3 state transition 보조 | 중간 |
| R ratio | 미래 변동성 state에 유의미 | risk/volatility state | 높음 |
| Naive range/grid | center reversion 근거 약함 | 현재 보류 | 낮음 |
| Exact FV regression | 이동폭 예측 약함 | direction/magnitude/state로 전환 | 낮춤 |

---

# 28. 구현 우선순위

## Phase 1 — Execution Simulator Fix

먼저:
1. maker-only post-only semantics
2. event-ordering
3. order lifecycle
4. minimum quote lifetime
5. cancel/replace tolerance
6. queue model
7. partial fills
8. venue-specific latency
9. inventory tracking
10. markout logging

전략 연구보다 우선.

---

## Phase 2 — Core Feature Engine

구현:
- L1 OBI
- L2 OBI
- OFI
- aggressive buy/sell volume
- TI
- visible refill/depletion proxy
- R
- spread
- next-level gap
- short RV
- cross-venue residual

모든 feature는:
- causal
- timestamp-safe
- no future information
- deterministic

---

## Phase 3 — State Engines

### DirectionStateEngine
입력:
- OBI
- OFI
- D2 state
- D3 state

출력:
```text
direction
confidence
reason_codes
```

### VolatilityStateEngine
입력:
- R
- RV
- spread
- depletion
- next-level gap

출력:
```text
vol_regime
jump_risk
recommended_halfspread
```

### InventoryController
입력:
- inventory
- direction
- vol regime
- active orders

출력:
```text
bid skew
ask skew
bid size
ask size
one-sided quoting
```

---

## Phase 4 — Policy Engine

입력:

```python
PolicyInput(
    direction_state,
    move_magnitude_state,
    volatility_state,
    jump_risk_state,
    inventory_state,
    current_book,
    active_orders,
)
```

출력:

```python
QuoteDecision(
    bid_price,
    bid_qty,
    ask_price,
    ask_qty,
    bid_enabled,
    ask_enabled,
    ttl_ms,
    cancel_order_ids,
    reason_codes,
)
```

---

## Phase 5 — Historical Event Replay

평가:
- raw event replay
- no lookahead
- realistic latency
- queue fill simulation
- maker-only enforcement
- partial fill
- inventory-aware quote

---

## Phase 6 — Shadow / Paper Pilot

실제 mainnet public data + simulated orders.

먼저:
- 실제 주문 제출 없이
- live signal
- live queue estimate
- hypothetical post-only fills

검증.

그 후 소액 pilot.

---

# 29. State transition은 deterministic하게 기록

reason code 예:

```text
DIRECTION_OBI_STRONG_BUY
DIRECTION_OFI_ONSET_BUY
FLOW_ABSORPTION_BUY
FLOW_BUY_EXHAUSTED
FLOW_ASK_EXHAUSTED
CROSS_BINANCE_LEADS_UP
CROSS_LOCAL_OVERPRICED
VOL_HIGH_R_CALM
VOL_LOW_R_STRESSED
JUMP_ASK_DEPLETION
INVENTORY_LONG_HEAVY
```

모든 quote decision은:
- 어떤 state 때문에 나왔는지
- 어떤 state transition으로 cancel됐는지

audit 가능해야 함.

---

# 30. Kill-switch

필수:

```text
max_inventory
max_inventory_duration
max_drawdown
max_adverse_markout
max_cancel_rate
max_reject_rate
max_feed_staleness
max_sequence_gap
max_latency
```

위반 시:
- 신규 risk quote 중단
- maker unwind mode
- 필요 시 optional emergency taker mode

---

# 31. Codex가 하지 말아야 할 것

1. 기존 30초 FV regression을 단순 튜닝하는 데 집중하지 말 것.
2. 기존 lambda를 0~1 범위로 clip해서 S2를 억지로 살리지 말 것.
3. OBI/OFI 좋은 EDA를 곧바로 “수익성 확인”으로 해석하지 말 것.
4. 0 PnL pilot을 전략 실패로 해석하지 말 것.
5. 0 fill을 전략 alpha 부재로 해석하지 말 것.
6. fixed 5초 window를 전략 본질로 강제하지 말 것.
7. maker order를 opposite best price에 내고 즉시 fill되는데 maker로 처리하지 말 것.
8. 단순 spread widening을 inventory liquidation으로 해석하지 말 것.
9. trade imbalance 단독 sign으로 방향 거래하지 말 것.
10. R low를 방향성 alpha로 해석하지 말 것.
11. recent range center를 자동 FV로 두지 말 것.
12. public L2 refill proxy를 true order-level gross refill/cancel로 부르지 말 것.
13. random CV 금지.
14. future quantile/threshold leakage 금지.
15. 모든 전략을 동일 데이터에서 과도하게 grid-search한 뒤 최고값만 채택하지 말 것.

---

# 32. 최소 신규 실험 세트

## Experiment A — OBI/OFI Maker Markout

질문:

```text
strong OBI / OFI 상태에서
실제 maker fill이 발생한 경우
향후 markout이 개선되는가?
```

출력:
- fill rate
- markout 1/2/5/10/30s
- adverse selection
- side별
- asset별
- venue별

---

## Experiment B — Flow Absorption Transition

질문:

```text
Aggressive buy가 발생했을 때
ask absorption 후
1-1 exhaustion과 1-2 breakout을 구분할 수 있는가?
```

출력:
- transition confusion matrix
- transition probability
- post-transition return
- maker fill markout
- MFE/MAE

---

## Experiment C — Cross-Venue Origin

질문:

```text
spread dislocation 중
Binance-led와 local-idiosyncratic을 분리하면
local-only directional edge가 생기는가?
```

출력:
- residual convergence
- actual local PnL
- markout
- holding time
- false dislocation rate

---

## Experiment D — Volatility State

질문:

```text
R / spread / depletion을 이용해
quote width와 inventory cap을 바꾸면
adverse selection과 MDD가 감소하는가?
```

비교:
- static quote
- volatility-aware quote

---

## Experiment E — Unified Policy

비교:
1. static symmetric MM
2. OBI-only skew
3. OBI+OFI direction
4. + flow state
5. + cross-venue state
6. + volatility state
7. + inventory controller

단계별 incremental contribution 측정.

---

# 33. 연구 검증 원칙

## 33.1 미리 규칙 동결

EDA에서 후보를 찾은 뒤:
- threshold
- horizon
- transition rule
- quote policy

를 고정.

새 기간에서 검증.

---

## 33.2 multiple testing

탐색적 BH q는 참고만.

인접 W/H는 독립 전략 개수로 세지 말 것.

---

## 33.3 sparse data

데이터 부족 시:
- Bayesian posterior
- bootstrap
- non-parametric quantile

우선.

OLS 하나의 coefficient를 과신하지 말 것.

---

## 33.4 regime shift

rolling coefficient stability:
- sign stability
- magnitude stability
- rank stability

확인.

---

# 34. 최종 전략 철학

이 프로젝트의 핵심은:

> **“시장가를 정확히 예측하는 것”이 아니라, maker로 체결될 때 유리한 side에만 유동성을 공급하고, 불리한 side에서는 quote를 회수/후퇴시키며, 재고를 비대칭 quote로 관리하는 것.**

따라서 alpha의 최종 평가는:

```text
Prediction accuracy
```

가 아니라:

```text
Fill-conditioned markout
+
maker fee/rebate
+
inventory PnL
-
adverse selection
-
queue opportunity cost
```

로 해야 한다.

---

# 35. Codex에 요청할 최종 구현 목표

Codex는 기존 코드를 다음 목표로 수정/재구성하라.

## 필수 작업

1. maker-only execution semantics 교정
2. event-driven order lifecycle simulator 구현
3. queue-aware fill model 구현
4. partial fill 구현
5. min quote lifetime / cancel tolerance 구현
6. venue-specific latency 구성
7. fill-conditioned markout recorder 구현
8. S1을 OFI directional module로 재작성
9. S4를 OBI core directional module로 재작성
10. S2를 baseline residual + dislocation-origin module로 재작성
11. S3를 flow × liquidity supply transition state machine으로 재작성
12. R 기반 volatility/liquidity regime engine 구현
13. inventory-aware asymmetric quote engine 구현
14. jump-risk state 구현
15. deterministic reason-code/audit logging
16. purged walk-forward EDA/backtest pipeline
17. 전략별/모듈별 incremental ablation report 생성

---

# 36. 최종 산출물 요구

Codex 결과는 최소 다음을 포함해야 한다.

```text
src/
  features/
  states/
  strategies/
  execution/
  simulation/
  risk/
  analytics/

tests/
  unit/
  replay/
  leakage/
  execution/

reports/
  eda/
  markout/
  execution/
  strategy_ablation/
```

필수 문서:

```text
STRATEGY_SPEC.md
EXECUTION_SIM_SPEC.md
STATE_MACHINE_SPEC.md
EDA_PLAN.md
BACKTEST_PROTOCOL.md
RISK_CONTROLS.md
```

---

# 37. 최종 우선순위

현재 우선순위는 다음과 같다.

### Priority 1
- 체결 시뮬레이터 교정
- L1 OBI
- OFI
- fill-conditioned markout

### Priority 2
- S3 absorption/exhaustion transition
- R volatility state
- inventory-aware quote skew

### Priority 3
- S2 cross-venue dislocation origin
- jump-risk state
- L2/deeper book auxiliary features

### 보류
- naive fixed-range grid
- exact long-horizon FV regression
- trade imbalance sign-only strategy

---

# 38. 한 문장 결론

현재까지 가장 강한 증거는:

> **L1 OBI와 단기 OFI에 방향 정보가 존재하고, R은 미래 변동성 상태를 구분하며, 단독 trade imbalance와 naive range-center mean reversion은 약하다. 따라서 다음 버전은 정확한 미래 FV 회귀가 아니라 `Direction × Magnitude × Volatility × Inventory` 상태 추정과 maker-only queue-aware execution을 중심으로 재설계해야 한다.**
