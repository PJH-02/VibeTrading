# MM 전략 후보별 FV 추정 명세 — v4

수정일: 2026-10-04 KST. v3를 후보별로 재구성했다. 후보 번호는 구현·비교 순서이며 검증된 수익성 순위가 아니다. 실제 대상 시장 데이터로 수행한 성과 검증은 아직 없다.

## 1. 네 후보와 핵심 FV 추정 방법

| ID | 독립 전략 후보 | 무엇으로 미래 가격을 추정하는가 | 핵심 FV 식 | 위치 |
|---|---|---|---|---|
| S1 | 순유동성 압력 FV MM | 체결·취소·보충·가격 개선의 순효과가 이후에도 남는 정도 | FV1 = mid + beta1 × spread × OFI/깊이 | 로컬 코어의 첫 구현 |
| S2 | 동일 자산 외부 기준가격 FV MM | 경제적으로 비교 가능한 외부 가격과 target 가격의 차이가 줄어드는 정도 | FV2 = mid + lambda × (조정 reference - mid) | 우선 비교할 별도 전략 |
| S3 | 공격 체결 흐름 FV MM | 시장가 매수·매도 물량의 불균형이 이후에도 이어지는 정도 | FV3 = mid + beta3 × spread × 순공격 체결량/깊이 | 독립 후보 |
| S4 | BBO 잔량 불균형 FV MM | 양쪽 queue 크기 비대칭에 따른 이후 가격변동 | FV4 = mid + beta4 × spread × OBI | 독립 후보 |

**S1·S3·S4는 ‘현재 mid + 예측 이동폭’ 방식이다. S2는 ‘외부 가격 anchor와의 괴리’ 방식이다.** S1을 계산한 뒤 S2·S3·S4로 승인하는 계층 구조가 아니다. 후보마다 자기 신호, 자기 FV, 자기 주문·손익이 있다.

S1이 수익성 1위라는 근거는 아직 없다. 로컬 공통 데이터에서 소비·취소·보충을 하나의 순흐름으로 정리할 수 있어 먼저 구현한다는 뜻이다. S2는 reference를 이용할 수 있는 시장에서 S1과 같은 조건으로 우선 비교한다. S3·S4가 더 좋은 결과를 보이면 해당 후보를 주력으로 선택할 수 있다.

공통 기호:

- m_t: target venue의 mid.
- s_t: target spread.
- Qb_t, Qa_t: 현재 best bid/ask의 외부 표시 잔량.
- D_t = (Qb_t + Qa_t)/2: 양쪽 평균 BBO 깊이.
- t: 신호에 사용한 정보의 기준시각.
- H: 그 시점부터의 예측구간.
- beta1, beta3, beta4: 각 후보에서 별도로 보정할 이동폭 계수.
- lambda: S2에서 별도로 보정할 기준가 괴리 반영률.

여기의 FV는 단기 미래 mid의 근사값이다. S1·S3·S4가 숨은 내재가치를 직접 관측한다는 뜻은 아니다. 세 후보의 관측 신호는 계산할 수 있지만, 그 신호가 몇 틱의 미래 이동에 해당하는지는 별도의 작은 보정과 검증이 필요하다.

네 후보 모두 아래 목표를 근사한다.

$$
\widehat F^{(j)}_t(H)
\approx
E[m_{t+H}\mid \text{후보 }j\text{가 사용하는 시각 }t\text{의 정보}].
$$

전체 이동으로 보정한 FV를 현재 mid 기준으로 계산하고, 실제 주문 도착 후 남은 이동과 체결 손익은 별도로 검증한다. 5초는 고정 signal bin이나 필수 지속시간이 아니다. 실측 end-to-end p95가 3초이면 3초를 적용한다.

## 2. S1 — 순유동성 압력 FV MM

**핵심 FV 추정: 최근 순호가흐름을 깊이로 나누고, 그 압력이 선택한 H에서 만드는 미래 이동폭만 mid에 더한다.**

$$
z^{OFI}_t=\frac{OFI(W_t)}{D_t},
$$

$$
\boxed{\widehat F^{(1)}_t=m_t+\beta_{1,H}s_tz^{OFI}_t.}
$$

### 조건·해석·액션

| 항목 | 명세 |
|---|---|
| 관측 입력 | BBO 가격·잔량 업데이트. 실행 검증에는 체결·private 주문 로그 |
| 해석 | bid 공급 증가·ask 소진/철수는 상승 압력, ask 공급 증가·bid 소진/철수는 하락 압력 |
| 핵심 가설 | 관측한 순압력의 일부가 이어지거나 참여자의 가격 조정이 아직 완료되지 않아 미래 이동이 남음 |
| 추정값 | H 뒤 target mid의 근사값 |
| 액션 | FV와 mid의 가격차가 비용·오차 여유를 넘으면 공통 maker 주문 제출 |
| 시간축 | 깊이 대비 호가 활동량 창으로 관측, 이벤트마다 갱신, H와 실제 지연은 별개 |
| 실패 조건 | 미래 이동이 없음, 도착 전에 이동 완료, fill 표본 손익 음수, 계수 불안정 |

### FV 계산 과정

1. 각 BBO 업데이트에서 bid·ask의 추가/제거·가격 개선을 방향 있는 수량 변화로 집계한다.
2. 순효과를 합쳐 OFI를 계산한다. 같은 체결량을 book 변화와 별도로 또 더하지 않는다.
3. 과거 호가 활동량 합이 현재 평균 깊이의 nu배가 되는 trailing 창 W를 사용한다.
4. OFI/D를 계산한다. 절대 주문 건수나 코인 개수 문턱은 쓰지 않는다.
5. 학습 구간에서 이 신호와 **그 이후의** 가격 이동을 연결해 beta1을 보정한다.
6. 새로운 관측 상태에 위 FV 식을 적용한다.

OFI의 장부 표현은 개념적으로 다음과 같다.

$$
OFI=\text{bid 추가}-\text{bid 제거}
-\text{ask 추가}+\text{ask 제거}.
$$

표시 잔량 제거에는 체결과 취소가 포함된다. 가격이 바뀔 때는 서로 다른 가격의 잔량을 단순 차감하지 않고, 부록의 price-aware BBO 규칙을 사용한다.

같은 ask에서 시장가 매수 100 뒤에 매도 지정가 100이 보충됐다면 두 효과는 대체로 상쇄된다. 보충률·공격 매수 비중·취소 비중 각각의 threshold를 요구하는 대신 순효과 하나를 평가한다.

**주의:** OFI가 이미 발생한 가격충격을 설명한다는 것과 이후 가격을 예측한다는 것은 다르다. Cont·Kukanov·Stoikov의 연구는 전자의 관계를 뒷받침한다.[1] beta1은 그 동시점 충격 계수를 가져오는 값이 아니라, 과거 관측 OFI와 이후 가격 이동으로 별도 보정하는 값이다.

### 계산 예시 — 실측 결과가 아닌 가정

- mid = 100.00, spread = 0.02, tick = 0.01.
- 현재 평균 깊이 D = 1,000.
- 관측창의 순 OFI = +500.
- 별도로 보정됐다고 가정한 beta1 = 5.

$$
z^{OFI}=0.5,\qquad FV_1=100+5\times0.02\times0.5=100.05.
$$

이 계산은 +5틱의 FV다. beta1 = 5가 실제로 맞는지는 아직 확인되지 않았다.

### 이 전략이 제공하지 않는 것

- 미래 잔여 모주문량의 직접 추정.
- 누가 정보성 주문을 냈는지의 식별.
- 과거 가격으로 반드시 복원된다는 anchor.
- 순압력이 0이면 가격이 band 안에 머문다는 결론.

따라서 S1은 주로 미래 방향 이동의 후보다. 별도 가치 근거 없이 과거 mid를 유지하는 range 전략으로 바꾸지 않는다.

## 3. S2 — 동일 자산 외부 기준가격 FV MM

**핵심 FV 추정: Binance 등 비교 가능한 시장의 현재 가격을 target 계약에 맞게 조정한 뒤, target과의 괴리 중 H 안에 반영될 부분을 사용한다.**

$$
A_t=P_t^{ref}\exp(\widehat b),
$$

$$
\boxed{\widehat F^{(2)}_t=m_t+\lambda_H(A_t-m_t).}
$$

### 조건·해석·액션

| 항목 | 명세 |
|---|---|
| 관측 입력 | target BBO, 동일 위험의 reference BBO, 계약·통화·basis 정보 |
| 해석 | reference보다 target이 싸거나 비싸며, 같은 위험을 거래하는 참여자가 차이에 반응할 유인이 있음 |
| 핵심 가설 | 정상 basis를 제거한 가격차가 선택한 H에서 줄어들고, 실제 도착 뒤에도 거래 여지가 남음 |
| 추정값 | reference 방향으로 조정된 H 뒤 target mid |
| 액션 | 가격차가 비용·오차 여유를 넘으면 target venue에서 maker 거래 |
| 시간축 | reference/target의 실제 receive 이벤트마다 갱신, H는 수렴 특성으로 선택 |
| 실패 조건 | reference stale, 계약 차이, basis 구조 변화, 도착 전 수렴, fill 손익 음수 |

### FV 계산 과정

1. 기준 시장을 가격 상관관계 검색이 아니라 동일 기초 위험·상품 구조에 따라 선정한다.
2. reference 가격을 target과 동일한 quote 통화·가격 단위로 맞춘다.
3. 학습 구간의 정상 log 가격비를 중앙값 등으로 보정해 b를 정한다.
4. A = 조정 reference price를 계산한다.
5. 과거 시각 t의 A-m과 이후 target 가격 이동으로 lambda를 보정한다.
6. 새로운 괴리에 lambda를 적용해 FV를 계산한다.

같은 기초자산·계약 구조의 liquid perp를 우선 비교할 수 있다. spot–perp, 다른 담보·통화·funding 구조라면 그 차이를 인정한다. mark/index/oracle을 실제 체결 가능한 가격과 같다고 가정하지 않는다.

b는 다음 평가 구간에서 동결한다. 불리한 가격차가 커질 때마다 현재 target 가격을 따라 움직여 손실을 정상 basis로 지우지 않는다.

수렴 후보의 운용 가정은 0 < lambda <= 1이다. 비제약 추정이 그 범위를 지속적으로 벗어나면 범위 끝으로 잘라 맞춘 결과를 정당화하지 않고, 기준가·수렴 가설을 다시 확인한다.

- lambda = 1: 현재 anchor를 전부 반영.
- lambda = 0.5: 현재 괴리의 절반만 반영.
- lambda가 안정적으로 양수가 아님: 이 수렴 가설을 지지하지 않음.

### 계산 예시 — 실측 결과가 아닌 가정

- target mid = 100.00.
- 상품·basis 조정을 마친 reference A = 100.10.
- 별도로 보정됐다고 가정한 lambda = 0.5.

$$
FV_2=100+0.5(100.10-100)=100.05.
$$

S1과 같은 +5틱 FV지만 근거는 외부 기준가격과의 괴리다. S1·S3·S4가 동시에 상승을 가리킬 필요는 없다.

### 이 전략이 상대적으로 명확한 점

S2에는 현재 target mid와 독립적으로 관측한 가격 기준 A가 있다. A가 유효하고 안정적일 때 target이 그 아래·위로 흔들리면, 같은 FV 주문 규칙으로 아래에서 롱·위에서 숏을 할 수 있다. A가 새 정보로 이동하면 주문도 이동한다.

외부 가격을 읽는 것만으로 양 거래소에서 동시에 체결하거나 헤지하는 것은 아니다. 단일 venue에서 거래하면 방향 위험은 남는다. 차익거래 참여자의 유인도 수렴 시점이나 이익을 보장하지 않는다.[4]

Binance가 어떤 시장에서도 항상 선도한다고 가정하지 않는다. sub-second lead–lag 연구 결과를 실제 3초 지연의 수익성으로 전용하지 않는다.[5]

## 4. S3 — 공격 체결 흐름 FV MM

**핵심 FV 추정: 최근 시장가 매수·매도의 순물량을 깊이로 나누고, 그 수요가 이후에도 이어져 만드는 가격 이동폭을 mid에 더한다.**

$$
z^{Trade}_t=\frac{V^{buy}(W_t)-V^{sell}(W_t)}{D_t},
$$

$$
\boxed{\widehat F^{(3)}_t=m_t+\beta_{3,H}s_tz^{Trade}_t.}
$$

### 조건·해석·액션

| 항목 | 명세 |
|---|---|
| 관측 입력 | aggressor 방향·수량이 있는 체결, 현재 BBO 깊이 |
| 해석 | 실제로 반대 지정가를 소비한 공격 매수·매도 물량의 비대칭 |
| 핵심 가설 | 분할 집행 등의 이유로 같은 방향 수요와 가격 영향의 일부가 이후에도 남음 |
| 추정값 | 그 공격 흐름에 따른 H 뒤 target mid |
| 액션 | FV와 mid의 차이가 비용·오차 여유를 넘으면 공통 maker 주문 제출 |
| 시간축 | 실제 체결량이 현재 깊이의 nu배가 되는 trailing 창, 체결 이벤트마다 갱신 |
| 실패 조건 | 큰 단발 체결, 집행 종료, 공급의 완전한 흡수, 도착 전 반영, fill 손익 음수 |

### FV 계산 과정

1. 공격 매수·매도 물량을 같은 수량 단위로 분류한다.
2. 전체 체결량이 nu × D가 되는 trailing 창을 만든다.
3. 순공격 체결량을 D로 나눈다.
4. 학습 구간에서 이 신호와 이후 가격 이동으로 beta3을 보정한다.
5. 새로운 순체결량에 FV 식을 적용한다.

총량 대비 비중으로도 같은 관측을 읽을 수 있다.

$$
T_t=\frac{V^{buy}-V^{sell}}{V^{buy}+V^{sell}}.
$$

창의 총량이 nu × D이면 zTrade = nu × T다. 매수 물량 비중 75%, 매도 25%이면 T = 0.5다. ‘최근 몇 건 중 몇 건이 매수’라는 개수 규칙이나 EWMA를 쓰지 않는다.

분할 주문은 흐름 지속성의 가능한 메커니즘이다.[2] 하지만 위 신호는 현재 순공격 물량을 관측한 값이다. 미래에도 집행이 남았다는 사실이나 특정 metaorder를 직접 확인한 값은 아니다. **‘공격 체결 지속성’은 이 후보의 예측 가설이며 확인된 사실이 아니다.**

### S1과의 차이

| 같은 가격에서의 관측 | S1 순압력 | S3 공격 체결 압력 |
|---|---|---|
| 시장가 매수 100 + ask 보충 100 | 두 효과가 상쇄 | 매수 우세 100을 기록 |
| 체결 없이 ask 취소 100 | 상승 쪽 순압력 | 공격 체결 신호 없음 |
| 체결 없이 bid 공급 100 | 상승 쪽 순압력 | 공격 체결 신호 없음 |

S3는 보충·취소를 가격 신호에 직접 넣지 않는다. 그 때문에 더 단순한 수요 신호가 될 수 있고, 반대로 흡수되는 수요에 취약할 수 있다. 이 차이는 별도 손익으로 판단한다. ask 보충률 조건을 S3에 바로 추가하면 독립 비교의 의미가 흐려진다.

### 계산 예시 — 실측 결과가 아닌 가정

- mid = 100.00, spread = 0.02, tick = 0.01.
- D = 1,000, nu = 1.
- 관측창의 매수 체결 750, 매도 체결 250.
- 별도로 보정됐다고 가정한 beta3 = 5.

$$
z^{Trade}=0.5,\qquad FV_3=100+5\times0.02\times0.5=100.05.
$$

beta3은 beta1과 별도로 보정한다. 예시에서 같은 숫자를 사용한 것은 계산 비교용이며 같은 계수를 공유한다는 뜻이 아니다. 예측 가능한 거래흐름에 공급이 적응하면 가격 알파는 사라질 수 있다.[3]

## 5. S4 — BBO 잔량 불균형 FV MM

**핵심 FV 추정: 현재 best bid/ask 잔량 비율을 이용해 향후 가격 방향·이동폭을 보정하고 mid에 더한다.**

$$
I_t=\frac{Q^b_t-Q^a_t}{Q^b_t+Q^a_t},
$$

$$
\boxed{\widehat F^{(4)}_t=m_t+\beta_{4,H}s_tI_t.}
$$

### 조건·해석·액션

| 항목 | 명세 |
|---|---|
| 관측 입력 | 현재 BBO 가격·잔량 |
| 해석 | 양쪽 표시 queue 크기의 비대칭 |
| 핵심 가설 | queue 소진·보충 과정이 비대칭을 완전히 상쇄하지 않아 향후 가격 방향에 차이가 생김 |
| 추정값 | 그 상태에 따른 H 뒤 target mid |
| 액션 | FV와 mid의 차이가 비용·오차 여유를 넘으면 공통 maker 주문 제출 |
| 시간축 | 현재 BBO 상태, 이벤트마다 갱신. 지속성은 선택형 비교 |
| 실패 조건 | 도착 전 효과 소멸, 잔량 철수, 오래 유지돼도 가격 이동 없음, fill 손익 음수 |

### FV 계산 과정

1. 현재 외부 BBO 잔량으로 I를 계산한다.
2. 학습 구간에서 I와 이후 가격 이동으로 beta4를 별도 보정한다.
3. 새로운 BBO 상태에 FV 식을 적용한다.

현재 상태만 쓰는 후보이므로 S1처럼 순호가흐름을 누적하거나 S3처럼 공격 체결 물량을 집계할 필요가 없다. bid : ask = 3 : 1이면 I = 0.5다. 30 : 10과 3,000 : 1,000은 같은 신호다.

Gould·Bonart는 queue imbalance와 다음 mid 변화 방향의 관계를 연구했다.[7] 그 결과가 대상 crypto의 H초·실측 지연 후 알파나 +5틱 이동을 보장하지는 않는다.

### 계산 예시 — 실측 결과가 아닌 가정

- mid = 100.00, spread = 0.02, tick = 0.01.
- Qb = 1,500, Qa = 500, I = 0.5.
- 별도로 보정됐다고 가정한 beta4 = 5.

$$
FV_4=100+5\times0.02\times0.5=100.05.
$$

beta4 = 0.5로 두면 weighted mid다.

$$
F^{weighted}=m+\frac{s}{2}I.
$$

weighted mid는 bid와 ask 사이에 있다. 위 예에서는 100.005, 즉 mid 대비 +0.5틱이다. 몇 틱의 미래 FV를 얻으려면 같은 OBI라도 더 긴 horizon의 가격 이동을 따로 보정해야 한다. 원하는 +5틱을 만들기 위해 계수를 임의로 키우지 않는다.

### 선택형 지속성 버전

OBI가 같은 방향 문턱을 5초·30초 유지하는 버전은 S4 내부의 비교 실험이다. 네 후보 공통의 필수 조건이 아니다.

- 문턱은 잔량 비율로 정의. 예를 들어 I >= 0.5는 bid : ask >= 3 : 1.
- 확인이 끝난 시점부터 이후 움직임을 평가.
- 확인 이전의 가격 이동을 성과에 포함하지 않음.
- 오래 유지될수록 남은 알파가 강하다고 가정하지 않음.
- 효과가 없으면 지속시간·문턱 제거.

‘비정보성 거래자가 부족해 잔량이 유지됐다’는 것은 가능한 설명 중 하나다. OBI만으로 정보성 여부·잔량 보충·거래 부진의 원인을 특정하지 않는다.

## 6. 네 후보가 공유하는 주문 규칙

각 후보는 자기 FV 또는 ‘추정 불가’를 출력한다. 주문·재고 엔진은 동일하게 적용한다. 방향성·횡보·충격을 별도 진입 모듈로 나눌 필요는 없다. S2의 독립 anchor는 가격차 복원에 이용할 수 있지만 S1·S3·S4의 중립 신호만으로 과거 가격의 band를 선언하지 않는다.

| FV와 시장의 관계 | 같은 규칙의 결과 |
|---|---|
| FV가 현재 시장보다 위 | 시장 근처 매수, FV 위 매도 |
| FV가 현재 시장보다 아래 | FV 아래 매수, 시장 근처 매도 |
| 유효한 FV가 안정적이고 시장이 그 아래·위로 이동 | 아래에서 롱, 위에서 숏 |
| 국소 가격 충격 이후 유효한 FV와 괴리 발생 | FV 방향으로 복원 거래 |
| FV 자체가 새 정보로 이동 | 주문가격도 이동. 과거 밴드 유지 안 함 |
| FV 추정 불가 또는 edge 부족 | 신규 quote 없음 |

여기서 ‘안정적인 FV’는 과거 mid가 평평했다는 이유만으로 선언하지 않는다. 특히 로컬 압력이 0이라는 사실만으로 과거 가격이 미래에도 유효한 anchor가 되지는 않는다. **단순한 FV 모델과 가격이 특정 band를 벗어나지 않는다는 예측은 다른 문제다.**

현재 mid ±1%를 장래 가격의 보장된 경계로 지정하지 않는다. 검증된 FV 아래·위에서 거래할 수는 있지만, 별도 근거 없이 ±1% containment를 주장하지 않는다.

### 6.1 경제적 진입 기준 하나

단위 가격 기준의 비용·오차 여유를 C_t라고 한다.

$$
C_t=c_t+u_t,
$$

여기서 c는 예상 왕복 비용과 실패 청산 비용의 보수적인 반영이고, u는 과거 검증 구간에서 관측한 불리한 FV 오차의 여유다. tick·bp·spread 비율로 표현하며 고정 코인 수량 조건을 쓰지 않는다.

$$
\boxed{|\widehat F_t-m_t|>C_t}
$$

이면 신규 quote 후보를 만든다. 이 가격 우위 조건이 알파 진입의 공통 기준이다. ‘OBI도 강하고, flow도 강하고, refill도 낮아야 한다’는 추가 확인은 없다.

u는 정규분포 표준편차 배수를 당연하게 가정하지 않는다. 과거 불리한 오차의 경험적 분위수를 사용할 수 있으며, 그 분위수와 평가 구간을 사전에 고정한다. 이는 손실 보장이 아니며 실제 fill 표본에서 다시 평가한다. 데이터가 부족하면 낮은 u를 임의로 지정해 거래를 허용하지 않는다.

모델이 유효하지 않거나 가격차가 C 이하이면 신규 노출 주문을 취소한다. 기존 재고를 줄이는 주문은 별도로 유지·조정한다.

### 6.2 주문가격

quote의 반폭은 다음처럼 단순하게 둔다.

$$
h_t=\max(s_t/2,C_t).
$$

$$
p_b=\min\left(b_t,\operatorname{floor}_{tick}(\widehat F_t-h_t)\right),
$$

$$
p_a=\max\left(a_t,\operatorname{ceil}_{tick}(\widehat F_t+h_t)\right).
$$

각 방향의 실제 실행 성과가 검증된 경우 q/q 양방향 제출을 허용한다. 한쪽만 검증되면 그쪽 신규 주문과 기존 재고 감축 주문만 둔다. 양방향 검증은 오프라인 정책 선택이며 온라인 지표를 더 붙이는 일이 아니다.

예를 들어 bid = 99.99, ask = 100.01, tick = 0.01, FV = 100.05, h = 0.01이라는 **가정**에서는 매수 99.99, 매도 100.06이다. 매도는 현재 best ask보다 5틱 위다. 이 FV가 확인된 실측 결과라는 뜻은 아니다.

주문은 post-only다. 계산 시점에 passive였더라도 도착 시 marketable이면 거절·재가격되는 실제 venue 동작을 반영한다. 기존 목표 가격·수량이 같으면 유지해 queue 우선순위를 보존한다.

### 6.3 체결·재호가·청산

- 이벤트마다 상태·FV 갱신. 실제 제출은 tick 반올림 후 목표 가격·수량이 달라질 때.
- fill이면 재고와 남은 주문을 먼저 갱신하고 현재 FV로 재평가.
- 롱 정상 감축은 유효한 ask, 숏 정상 감축은 유효한 bid에 제출.
- FV가 바뀌면 감축가격도 갱신. 손실 포지션의 최초 FV를 영구 목표로 고정하지 않음.
- 가격 근거가 사라지면 신규 노출 중단. 설정된 감축시간·손실 예산 안에서 maker 감축하고, 넘으면 marketable reduce-only로 감축.
- 취소 요청 중 fill을 허용하고, cancel ACK 전 기존 잔량을 없애지 않음.

목표가격 도달, 지정가 터치, 실제 청산 체결을 구분한다. 정상 청산률은 실제 전량 감축을 기준으로 측정한다.

## 7. 지연·예측구간·보유기간을 분리한다

| 시계 | 의미 | 결정 방법 |
|---|---|---|
| 관측창 W | 현재 압력을 요약할 과거 자료 | 깊이 대비 수량 비율 |
| 신호 갱신 | 언제 새 입력으로 FV를 계산하는가 | 유효 시장 이벤트 |
| 전송 지연 L | 정보 기준시각부터 주문이 활성화될 때까지 | 실제 경로의 end-to-end 분포 |
| 예측구간 H | 어떤 미래 가격을 예측하는가 | 신호 반응과 실행 성과로 선택 |
| 재호가 | 언제 기존 주문의 의도를 바꾸는가 | 가격·수량 변경 필요 |
| 보유 제한 T_cap | 자금과 위험을 얼마나 오래 점유할 수 있는가 | 사전 위험 예산 |

H = 30초는 최초 비교 실험의 한 기준으로 사용할 수 있다. 10·30·60초 반응을 소수의 사전 지정 진단점으로 확인하되 최종 H는 calibration에서 선택하고 test에서 동결한다. 30초를 모든 종목의 본질적인 알파 수명으로 가정하지 않는다.

실측 p95가 3초이면 3초가 주요 실행 시나리오다. 취소·private 알림 지연과 p99 꼬리도 함께 재생한다. 동일 환경에서 무지연·실측 지연의 차이로 알파가 어디에서 사라지는지 진단한다.

신호 정보의 기준시각을 t라 할 때:

$$
\Delta^{total}_{t,H}=m_{t+H}-m_t,
$$

$$
\Delta^{remaining}_{t,L,H}=m_{t+H}-m_{t+L},\qquad H>L.
$$

FV를 전체 이동으로 보정했다면 현재 m을 기준으로 해석한다. remaining 이동은 주문 도착 후 남는 알파의 진단이다. 미래 m(t+L)을 이미 알았던 것처럼 실시간 가격식에 넣지 않는다. ‘3초 뒤에도 신호가 살아 있던 사례’만 사후 선택하지 않는다.

source/receive/decision/live 시각을 별도로 저장한다. receive-time replay에 수신 지연을 중복 가산하지 않는다. ACK를 live 대용으로 썼다면 한계를 기록한다. 주문이 H 이후에야 유효해진 경우 해당 예측을 제시간에 이용했다고 세지 않는다.

## 8. 재고는 수량과 한도로 관리한다

선형 계약에서 equity E, multiplier M, 깊이 D에 대해 기본 수량의 예시는 다음과 같다.

$$
q=\operatorname{lotfloor}\left[
\min\left(\frac{\rho_EE}{mM},\rho_DD,q_{\mathrm{risk\ headroom}}\right)
\right].
$$

rho_E는 주문당 명목 노출의 equity 비중, rho_D는 시장 깊이에 대한 참여 비중이다. 감축 가능한 실제 유동성과 stress 손실 예산으로 상한을 둔다. 큰 표시 주문 하나의 전량을 신뢰 가능한 청산 깊이로 간주하지 않는다. inverse 계약은 별도 변환이 필요하다.

확정 signed 재고를 J라고 하면, 한도 적용 전 목표 잔량은:

$$
q_b=q+\max(-J,0),\qquad q_a=q+\max(J,0).
$$

| 재고 | 신규 노출이 허용될 때 매수/매도 목표 |
|---|---|
| 0 | q / q |
| +q | q / 2q |
| -q | 2q / q |

따라서 매수 100 체결 뒤 매수 100·매도 200은 가능하다. 매도 200 중 100은 기존 롱 감축, 나머지 100은 새 숏 가능성이므로 노출 한도를 적용한다. 신규 알파가 없으면 그 추가 100은 두지 않고 재고 감축분만 둔다.

최악 노출은 상쇄하지 않는다.

$$
J_{\max}=J+\sum q_{buy,leaves},\qquad
J_{\min}=J-\sum q_{sell,leaves}.
$$

pending new/cancel/unknown도 포함한다. 계정 전체·종목·동일 기초자산의 방향 위험을 합산한다. FV 자체를 재고에 맞춰 왜곡하는 최적 inventory-skew 모형은 초기 코어에 넣지 않는다.

상태와 동작:

| 상태 | 신규 노출 | 감축 |
|---|---|---|
| READY, edge 있음 | 크기·노출 한도 안에서 허용 | 현재 FV에 따라 |
| READY, edge 없음 | 취소/중단 | 기존 재고만 감축 |
| REDUCE_ONLY | 중단 | maker 우선, 위험시간 초과 시 marketable |
| HALTED/UNKNOWN | 중단 | 계정 대사 후 가능한 취소·감축 수행 |

일중 손실·누적 drawdown·margin·데이터 무효·계정 불일치에 대한 kill-switch를 둔다. 값은 자본 예산으로 정하고 backtest 이익 최대화에 맞춰 튜닝하지 않는다. 통신 장애 때 취소와 청산이 즉시 성공한다고 가정하지 않는다.

## 9. FV 적중보다 체결 후 손익이 중요하다

$$
E[m_{t+H}-m_t\mid signal]>0
$$

이어도 다음 값은 음수일 수 있다.

$$
E[m_{t+H}-p_{\mathrm{fill}}\mid signal,\ buy\ fill].
$$

가격이 올라간 사례에서는 내 bid가 안 채워지고, 큰 매도가 밀려온 사례에서만 채워질 수 있기 때문이다. Binance Bitcoin perp의 실주문 연구도 maker fill 가능성과 이후 수익의 불리한 관계를 보여준다.[6] 이를 없애기 위해 지표를 더 붙인다는 결론은 아니다. **작은 가격식 자체를 실제 체결 조건에서 검증해야 한다는 의미다.**

첫 연구는 단순한 비교로 충분하다.

1. 같은 시각·시장·위험 크기의 mid 기준 MM — 알파 없는 실행 대조군.
2. S1 순유동성 압력 FV.
3. S2 동일 자산 외부 기준가격 FV — reference 이용 가능 시장.
4. S3 공격 체결 흐름 FV.
5. S4 BBO 잔량 불균형 FV.

동시에 결합하지 않는다. 각 정책의 H·계수·주문폭·재고 규칙을 calibration 뒤 고정한다. entry-only, ask-first, bid-first, 부분 체결, 미체결, cancel 중 체결, 강제 청산을 포함한다.

최소 측정 항목:

- 전체 기회당·제출 주문당·체결 주문당 순손익.
- 도착 후 남는 가격 이동과 지연별 감소.
- fill 이후 방향별 markout. 실제 왕복 손익과 구분.
- 실제 정상 청산률, 보유기간, 실패 때 평균·최악 손실.
- 순손익·Calmar·MaxDD·일별 Sharpe·VaR/ES.
- maker 명목 거래량, turnover, gross/net 및 pending 최악 노출.
- 수수료·funding 포함 본체 손익과 incentive 손익의 분리.

승률이 높아도 드문 큰 손실로 Calmar와 순손익이 나쁘면 탈락이다. 거래를 거의 하지 않아 MaxDD가 작다는 이유로 선택하지 않는다. 달력시간 수익, 유의미한 체결량, 용량도 함께 평가한다.

## 10. 보정·통계 검증은 오프라인으로 제한한다

### 가정부터 진단한다

실제 자료 확보 후, 학습 이전에 다음을 확인한다. 현재는 모든 수치가 미측정이다.

- 정상성: 정규화 신호·가격변화·basis 잔차의 ADF와 KPSS. 가격 수준 자체의 비정상성을 억지로 없애고 과거 가격 복원을 정당화하지 않음.
- 의존성: ACF/PACF와 Ljung–Box로 관측·예측오차·손익의 의존 구조 진단.
- 분포: QQ plot과 AD/Shapiro를 보조적으로 사용. 원시 의존 표본에 독립 정규성 검정의 p-value를 그대로 적용하지 않음.
- 비정규·의존 자료를 기본 가정으로 하고, day/episode block bootstrap으로 불확실성을 평가.
- 구조 변화로 서로 다른 기간의 관계가 달라지면 하나의 보편 계수를 강제하지 않음.

이 검정은 실시간 진입 필터가 아니다. 자료의 성질과 보정의 신뢰도를 확인하는 연구 절차다. 검정 통과만으로 경제적 인과관계가 입증되지는 않는다.

### 작은 보정만 한다

우선 신호의 소수 구간별 미래 평균 이동과 경험적 오차를 확인한다. 충분한 데이터에서 크기 관계가 대략 일정하면 기울기 하나로 압축한다.

예측 표적은 공통으로 y_t = m(t+H) - m(t)다. 각 후보의 가격 단위 입력은 다음과 같다.

| 후보 | 계수 보정에 쓰는 x |
|---|---|
| S1 | spread × OFI/깊이 |
| S2 | 조정 reference - mid |
| S3 | spread × 순공격 체결량/깊이 |
| S4 | spread × OBI |

S1·S3·S4의 계수는 서로 독립적으로 구한다.

$
\widehat\beta_{j,H}=\frac{\sum_t x_{j,t}y_t}{\sum_t x_{j,t}^2},
\qquad j\in\{1,3,4\}.
$

S2는 자기 x에 대해 같은 방식으로 lambda를 확인하고, 수렴 가설의 운용 범위를 별도로 평가한다. 분모가 0이면 보정하지 않는다. 각 계수는 현재부터 H까지의 전체 미래 이동을 위한 값이며, m(t+L)을 기준으로 한 잔여 이동 계수와 섞지 않는다. 선형식은 작은 보정 방식일 뿐 정규분포나 인과 식별을 주장하지 않는다. 단순 계수가 불안정한데 고차항·새 지표를 계속 더해 구제하지 않는다.

자료가 희소하면 소수 구간의 비모수 추정과 넓은 불확실성을 우선한다. 정확한 수치를 만들어 내기보다 추정 불가로 남긴다. test 결과를 보고 유리한 구간 경계를 계속 추가하지 않는다.

시간순 train/calibration/test와 walk-forward를 사용한다. 겹친 미래 label·주문·재고 episode는 purge하고 경계를 가로지르는 누설을 막는다. 신규 상장·폐지·수집 중단 자산을 당시 universe대로 포함한다. reference도 당시 실제 receive 시각에 볼 수 있었던 자료만 사용한다.

최초 검증의 데이터가 없으므로 alpha, Sharpe, Calmar, VaR 수치를 임의로 넣지 않는다. 작은 계수 하나를 추정하는 통계와 feature 조합으로 수익 패턴을 찾는 탐색을 구분한다.

### 위험 지표의 정의

계정 equity에는 실현·미실현 손익, 수수료·funding을 포함한다.

$$
MaxDD=\max_t\left(1-\frac{E_t}{\max_{u\le t}E_u}\right),\qquad
Calmar=\frac{CAGR}{MaxDD}.
$$

손실 표본 L에 대해 VaR_99 = 99% 경험적 손실 분위수, ES는 그 꼬리의 평균 손실로 보고한다. 꼬리 표본 부족을 표시하고 정규 VaR로 대체해 확신을 높이지 않는다. pending 주문이 모두 불리한 방향으로 체결되는 stress 노출도 함께 평가한다.

Sharpe는 24/7 일별 equity 수익률 기준으로 연환산 기준을 명시한다. 짧은 표본의 CAGR·Calmar·Sharpe를 장기 성과로 해석하지 않는다. turnover는 기간 체결 명목액 / 평균 equity로 표시해 거래량 확대가 위험 확대인지 확인한다.

## 11. 결정 가능한 최소 구현 명세

가격 계산과 주문 엔진은 분리하되 동일 엔진을 live/replay에서 사용한다. 마이크로서비스로 나눌 수 있는 인터페이스를 두고, 초기 저사양 서버에서는 불필요한 프로세스 간 왕복을 만들지 않는다.

| 상태 묶음 | 저장 항목 |
|---|---|
| instrument | venue, symbol, tick, lot, multiplier, fee, matching 규칙 |
| market | source/receive time, sequence, BBO/depth, validity, 외부 잔량 |
| alpha | candidate_id(S1/S2/S3/S4), 후보별 압력/체결 deque·reference/basis·OBI 상태, 독립 계수·config 버전 |
| prediction | 정보 cutoff, FV, H, 비용·오차 여유, 최초 목표와 만료시각 |
| orders | client ID, side, price, leaves, OPEN/REDUCE, pending/live/cancel/unknown 상태 |
| risk | 확정 재고, pending 최악 노출, equity, 손실·margin·시간 한도 |
| audit | 당시 입력 버전, FV, 허용/취소 이유, fill·실제 청산·손익 |

결정 순서는 고정한다.

1. sequence·시각·단위를 확인하고 book/private 주문 상태를 갱신.
2. 데이터·계정·위험 위반이면 신규 노출 차단 후 취소·대사·감축.
3. 선택된 단일 pricing provider 갱신. 후보들을 실시간 투표로 결합하지 않음.
4. FV와 비용·오차 여유 계산. 추정 불가/edge 부족이면 신규 중단.
5. tick에 맞춘 목표 bid/ask 및 equity·깊이 비중 수량 계산.
6. pending 포함 최악 노출로 신규 수량 제한.
7. 기존 주문과 차이가 있는 부분만 제출·취소·amend.
8. fill을 수신하면 동일 절차 재실행. 같은 fill을 중복 보충하지 않음.
9. 모델 만료 후 살아 있는 주문과 취소 지연 손실도 결과에 포함.

증분 합계·deque를 이용하고 매 이벤트마다 전체 DataFrame을 재생성하지 않는다. 시장/계정 수신은 비동기로 처리하되 결정 순서를 event ID와 cutoff로 재현 가능하게 남긴다. sequence gap, reconnect, 계약 규격 변경, 계수 버전 변경 시 관련 캐시를 명시적으로 무효화한다.

프로파일링은 source→receive, receive→decision, decision→send, send→live, cancel/private 알림을 나눠 p50/p95/p99로 기록한다. 각 구간 p95를 더한 값을 전체 p95라고 부르지 않는다. 목표는 작은 가격식의 연산시간과 외부 경로 지연을 구분하는 것이다.

## 12. 구현·비교·선택 순서

| 단계 | 결정 |
|---|---|
| 첫 로컬 구현 | S1의 net book flow와 FV를 구현 |
| 우선 비교 | reference를 이용할 수 있는 시장에서 S2를 별도 구현·평가 |
| 같은 평가에서 비교 | S3·S4도 자기 FV로 독립 손익을 기록 |
| 후보 채택 | 실측 지연 후 순손익·Calmar·실패 손실·거래량으로 선택 |
| 운영 | 다음 구간의 시장별 후보·계수·H를 고정하고 공통 주문 엔진에 연결 |

S1이 S2의 승인 조건도 아니고, S3·S4가 S1의 보조 필터도 아니다. S1+S2+S3+S4를 합산하거나 네 지표가 모두 찬성해야 거래하는 결합은 초기 연구에서 하지 않는다.

비교할 때는 같은 시장·기간·지연·비용·자본 위험을 사용한다. S2가 가능한 시장과 다른 후보가 가능한 시장의 교집합에서 직접 비교하고, 각 후보의 전체 적격 universe 성과는 별도로 표시한다. 유리한 시장만 남긴 결과를 모델 자체의 우수성으로 보지 않는다.

번호 순서와 수익성 순위를 구분한다. S2·S3·S4가 더 좋은 결과를 보이면 그 후보를 주력으로 선택할 수 있다. 같은 결과면 입력·파라미터·숨은 가정이 적은 후보를 우선한다.

채택 기준:

- 실제 주문 도착 후 가격 예측력이 남음.
- 실제 maker fill·청산·실패 비용을 넣은 순손익이 양수.
- MaxDD·Calmar·tail risk와 거래량·자금 점유가 허용 범위.
- 넓은 주변 비율·다른 기간·다른 시장에서 경제적 결론이 유지.

가격 예측은 되지만 fill 손익이 음수면 해당 주문 정책을 중단한다. 지연 후 예측 자체가 없으면 해당 후보를 중단한다. 네 후보 모두 실패하면 AND 조건을 계속 추가하지 않고 시장·기준가·실행 경로의 제약을 다시 검토한다.

## 13. S1 부록 — BBO 순흐름의 구현 정의

이 부록의 e와 활동량 A는 S1 신호를 정확히 계산하기 위한 정의다. FV 추정식에 새로운 진입 조건을 추가하는 것이 아니다. S2의 anchor A와 혼동하지 않도록 구현 필드명은 각각 book_activity와 reference_anchor로 구별한다.

현재 이벤트 n의 best bid/ask 가격을 b_n, a_n, 잔량을 Qb_n, Qa_n이라고 한다.

$$
u_n=
1_{\{b_n\ge b_{n-1}\}}Q^b_n
-
1_{\{b_n\le b_{n-1}\}}Q^b_{n-1},
$$

$$
v_n=
1_{\{a_n\le a_{n-1}\}}Q^a_n
-
1_{\{a_n\ge a_{n-1}\}}Q^a_{n-1}.
$$

$$
e_n=u_n-v_n,\qquad A^{book}_n=|u_n|+|v_n|.
$$

e는 순방향 압력이다. 가격이 그대로이면 잔량 증감을, 가격이 바뀌면 이전·새 best queue를 구별해 집계한다. Abook은 관측한 양쪽 변화의 크기다.

과거로 거슬러 합이 nu × 현재 D가 되는 trailing 창을 만든다.

$$
\sum_{n\in W_t}A^{book}_n=\nu D_t,\qquad
z^{OFI}_t=\frac{\sum_{n\in W_t}e_n}{D_t}.
$$

- nu = 1을 첫 연구 기준으로 사용하고 0.5·2에서 경제적 결론의 민감도를 확인.
- 경계 이벤트는 계산상 부분 가중 가능. 여러 독립 신호로 분할하지 않음.
- D = 0, book 무효, 이력 부족이면 추정 불가.
- 창의 실제 경과시간을 기록. 데이터 단절을 지속성 증거로 인정하지 않음.
- L2 snapshot만 있으면 관측 사이의 gross 이벤트를 정확히 복원했다고 주장하지 않음.
- 자기 주문을 식별할 수 있으면 외부 잔량으로 계산.
- 과거·실거래에서 같은 feed 해상도와 집계 정의를 적용.

같은 비율이 모든 시장에서 같은 알파 수명을 보장하지 않는다. nu는 경제적 관측량 비율이고 H와 L은 별도의 시계다.

## 참고자료

[1] Cont, Kukanov, Stoikov, *The Price Impact of Order Book Events*. 순호가흐름과 동시점 가격충격의 관계. 미래 예측 증거와 구별. https://arxiv.org/abs/1011.6402

[2] Lillo, Mike, Farmer, *A Theory for Long-Memory in Supply and Demand*. 분할 주문과 주문 부호 지속성의 메커니즘. https://arxiv.org/abs/cond-mat/0412708

[3] Taranto, Bormetti, Lillo, *The Adaptive Nature of Liquidity Taking in Limit Order Books*. 흐름 지속성이 가격 예측력과 같지 않은 이유를 연구. https://arxiv.org/abs/1403.0842

[4] Makarov, Schoar, *Trading and Arbitrage in Cryptocurrency Markets*. 거래소 가격 괴리와 차익거래 제약. https://dspace.mit.edu/entities/publication/7f91bfb5-ba77-4d0e-9c79-ec75e104e6cc

[5] Albers et al., *Fragmentation, Price Formation, and Cross-Impact in Bitcoin Markets*. 시장 간 정보 전달 및 실행을 연구하나 주요 예측 구간은 500ms. https://arxiv.org/abs/2108.09750

[6] Albers et al., *To Make, or to Take, That Is the Question: Impact of LOB Mechanics on Natural Trading Strategies*. 실제 maker 주문의 체결 선택과 이후 손익. https://arxiv.org/abs/2502.18625

[7] Gould, Bonart, *Queue Imbalance as a One-Tick-Ahead Price Predictor in a Limit Order Book*. BBO queue imbalance와 다음 mid 변화 방향의 연구. https://arxiv.org/abs/1512.03492

문헌은 메커니즘과 연구상의 구분을 뒷받침한다. 제안한 volume clock, 미래 가격식, quote 폭·재고 규칙이 현재 대상 거래소에서 수익성이 있다는 검증을 대신하지 않는다.

?? config/liquidity_mm_testnet.yaml
?? strategies/example_signal.py
?? vibetrading/liquidity_mm/
rg: strategy: 지정된 파일을 찾을 수 없습니다. (os error 2)
rg: requirements*: 파일 이름, 디렉터리 이름 또는 볼륨 레이블 구문이 잘못되었습니다. (os error 123)
.env.example:34:BYBIT_TESTNET_API_KEY=
.env.example:35:BYBIT_TESTNET_API_SECRET=
.env.example:36:BYBIT_TESTNET_REST_URL=
.env.example:37:BYBIT_TESTNET_PRIVATE_WS_URL=
.env.example:51:#BINANCE
.env.example:52:BINANCE_API_KEY=
.env.example:53:BINANCE_API_SECRET=
.env.example:54:BINANCE_TESTNET_API_KEY=
.env.example:55:BINANCE_TESTNET_API_SECRET=
.env.example:61:#GRVT
.env.example:62:GRVT_API_KEY=
.env.example:63:GRVT_API_SECRET=
.env.example:64:GRVT_ACCOUNT_ID=
.env.example:65:GRVT_ADDRESS=
.env.example:66:GRVT_TESTNET_API_KEY=
.env.example:67:GRVT_TESTNET_API_SECRET=
.env.example:68:GRVT_TESTNET_ACCOUNT_ID=
.env.example:69:GRVT_TESTNET_ADDRESS=
.env.example:71:GRVT_USE_TESTNET=true
.env.example:72:GRVT_ORDER_SUBMIT_ENABLED=false
.env.example:138:DASHBOARD_CRYPTO_VENUES=binance,bitget,bybit,grvt,arcus
docs/LIQUIDITY_MM_BYBIT_TESTNET.md:9:`BYBIT_TESTNET_API_KEY`, `BYBIT_TESTNET_API_SECRET`을 사용한다.
config\crypto_runtime.yaml:12:  - provider: grvt-perp