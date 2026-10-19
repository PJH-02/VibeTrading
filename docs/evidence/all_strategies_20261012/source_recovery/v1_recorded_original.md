# 잔여 주문흐름·유동성 소진 기반 선택적 Market Making 전략 명세 v1

작성일: 2026-10-03. 입력: `arcus_mm_context(1).md`.

**상태: 구현·검증을 위한 연구 명세. 실제 시장 데이터, 주문 로그, 수익률을 제공받지 않았으므로 알파·승률·수익성을 입증한 결과가 아니다. 아래 수치는 별도 표시가 없어도 검증 전 시작값이며 최적값이 아니다.**

목표는 거래소 내부의 호가·체결로 잔여 공격 주문과 유동성의 흡수력을 추정하고, 그 방향에 유리한 가격에 수동적으로 진입한 뒤 실제 체결 가능한 가격에서 청산하는 것이다. Fee 0는 연구 시나리오이며 특정 거래소의 현재 요율을 확인했다는 뜻이 아니다. 포인트와 리베이트 없이 양의 순거래손익을 먼저 요구한다.

## 1. 전략의 성격과 교정할 전제

이 전략은 **방향성 알파를 가진 선택적 유동성 공급**이다. 항상 양방향으로 호가를 유지하는 중립적 MM과 다르며, 포지션 보유 중에는 방향성 위험이 존재한다. 델타 중립, 모든 종목에서의 수익성, 향후 MM 프로그램의 호가 유지 의무 충족을 자동으로 달성하지 않는다.

| 기존 생각 | 유지할 부분 | 반드시 보완할 부분 |
|---|---|---|
| 좋은 FV가 핵심 | 가격 추정이 주문 최적화보다 우선 | 예측이 맞는 경우와 내 주문이 체결되는 경우는 다를 수 있음 |
| 잔여 매수량을 ask 깊이에 투영 | 주문 소진의 물리적 제약으로 활용 | 그 결과는 조건부 도달 가격이며 기대 mid나 내 청산 체결가가 아님 |
| 지속적 매수는 미완료 대형 주문 | 주문 분할은 합리적인 메커니즘 | 공개 L2만으로 동일 참여자·잔여 모주문 크기를 식별할 수 없음 |
| 좋은 가격이면 재고위험 감소 | 기대 역선택 손실을 줄일 수 있음 | 오예측이 지속되는 순간 같은 방향 재고가 집중될 수 있음 |
| 목표가 도달률이 높아야 함 | 정상 청산률을 중요한 지표로 사용 | 가격 접촉, 내 주문 일부 체결, 전량 청산을 구분해야 함 |
| 5초 지연이면 60초 전략 가능 | 짧은 가격 추격을 피함 | 진입·취소·체결통지·청산 제출 지연을 모두 재현해야 함 |

특히 다음 식을 동일시하지 않는다.

$$
E[m_{t+H}\mid\mathcal F_t]
\ne E[m_{t+H}\mid\mathcal F_t,\text{내 bid 체결}].
$$

상승 예상 시 bid가 체결되는 것은 일시적 매도 충격일 수도, 상승 가설 붕괴의 시작일 수도 있다. 내 주문이 체결된 표본의 손익까지 양수여야 알파다.

참고 연구는 주문 분할과 주문 부호의 지속성, 유동성 상태와 가격 충격, 역선택과 지연의 관계를 뒷받침한다. 다만 이를 Arcus·GRVT의 60초 수익성 증거로 사용할 수는 없다. 마지막의 출처 목록을 참조한다.

## 2. 최종 알파 가설

롱 방향의 중심 가설은 다음 네 조건의 결합이다.

1. **수요 지속:** 매수 공격 주문이 단발성 체결을 넘어 계속 들어올 가능성이 높다.
2. **ask 공급 부족:** 실제 체결로 ask가 소비되는 속도가 재공급 속도를 앞선다.
3. **bid의 매도 흡수:** 진입 쪽 bid에는 일시적인 매도 주문을 견딜 공급이 있다.
4. **청산 물량 여유:** 진입·청산 제출 지연 후에도 내 ask 앞의 물량과 내 청산 수량을 소비할 매수가 남는다.

숏은 buy/sell, bid/ask를 교환한다. 단, 매수·매도 방향의 통계 파라미터는 각각 보정한다. 대칭적인 규칙이 양방향 동일 승률을 뜻하지 않는다.

| ask 상태 | bid 상태 | 롱 판단 |
|---|---|---|
| 매수 체결로 소진, 재공급 부족 | 매도 체결 후 회복 | 주력 후보 |
| 매수 체결에도 잔량 재생 | 안정적 | 매수 흡수 상태, 롱 중단 |
| 소진 | bid도 취소·매도에 붕괴 | 양쪽 유동성 철수, 신규 거래 중단 |
| 취소만으로 비어 있음 | 안정적 | 실제 매수 지속이 확인되기 전까지 후보 제외 |

**체결이 잘 되는 상태 자체를 좋은 신호로 취급하지 않는다.** 빠른 상승에서 미체결이 많은 것은 정상이다. 강한 상승을 쫓아 bid를 계속 높여 체결률을 만드는 방식은 기본 전략에서 제외한다.

## 3. 단위·데이터·시간의 정의

### 3.1 수량 단위

초기 구현은 선형 perpetual 계약으로 제한한다. 주문흐름과 잔량은 동일한 base 수량 또는 동일한 계약 수량으로 변환한다. `contract_multiplier`를 명시하고 수량, 달러 거래대금, 가격을 섞지 않는다. 역선물은 별도 손익·델타 어댑터 검증 뒤 추가한다.

가격은 정수 tick, 수량은 정수 lot 단위로 계산한다. 표시와 외부 전송 시 Decimal로 변환한다. 포지션 비교와 가격 경계에 이진 부동소수점의 근사 등호를 사용하지 않는다.

### 3.2 필수 데이터

| 데이터 | 용도 | 실패 시 행동 |
|---|---|---|
| 양방향 L2 snapshot/delta와 sequence | 가격별 잔량, 소진·회복 | gap 발생 시 신규 주문 차단, 복구 snapshot |
| trade ID, 체결가·수량, aggressor side | 실제 소비 물량 | side 미확인 비율이 높으면 신규 주문 차단 |
| source timestamp, local receive timestamp | 정보 시점·지연 재현 | source clock 불확실성을 지연 여유에 포함 |
| private 주문·체결·포지션 | 실제 재고와 주문 상태 | 불일치 시 HALT 및 REST 대사 |
| tick, lot, multiplier, 최소 주문금액 | 유효 주문과 위험 단위 | 메타데이터 검증 전 거래 금지 |
| maker/taker 수수료, funding 조건 | 순손익 | 확인된 실계정 요율 사용 |
| mark·margin·청산 조건 | perpetual 계정 위험 | 가격 알파와 별개로 위험 모니터링 |

체결 side가 없으면 quote test/tick rule로 추정할 수 있으나 불명확한 체결은 `UNKNOWN`으로 유지한다. mid 체결을 임의로 buy 또는 sell로 배정하지 않는다. 초기 기준은 UNKNOWN 거래대금 10% 초과 시 해당 종목 신규 진입 중단이다.

스냅샷만으로 추가·취소·숨은 주문을 완전히 분리할 수는 없다. 1초마다 L2를 받더라도 1초 사이에 일어난 순서를 복원했다고 가정하지 않는다. 이 경우 순공급 변화와 보수적인 queue 가정만 사용한다.

### 3.3 지연을 분해한다

| 기호 | 정의 |
|---|---|
| `L_md` | 시장 이벤트에서 로컬 수신까지의 정보 지연 |
| `L_new` | 로컬 주문 결정에서 매칭엔진의 주문 활성화까지 |
| `L_cancel` | 취소 결정에서 매칭엔진의 취소 효력까지 |
| `L_private` | 거래소 체결에서 로컬 체결통지까지 |
| `L_compute` | 상태 계산·리스크 승인·직렬화 지연 |
| `B_cancel` | 불리한 변화가 발생한 뒤 주문을 실제로 제거할 때까지의 시간 |

`B_cancel`은 시장정보 수신, 판단 간격, 계산, 취소효력 지연을 포함한다. 종목·상태별 p50/p95/p99를 따로 기록한다. 각 지연의 p95를 더한 값이 합계 지연의 정확한 p95는 아니다. 공동 실측 분포가 없을 때만 보수적 예산으로 사용한다.

첨부의 5초 이상은 하한 가정이다. 현재 실측 p95가 5초라고 간주하지 않는다. 리플레이에서는 최소 5초, 10초, 20초 스트레스와 취소 지연 꼬리를 평가한다.

전략 정보집합은 **receive timestamp까지 실제 도착한 데이터**로 제한한다. 뒤늦게 도착한 체결을 과거 의사결정에 소급 삽입하지 않는다. 거래소 내 실행 순서는 exchange sequence로 재생하되, 의사결정에 보이는 순서는 실제 수신 순서다.

## 4. 시간 설정

| 항목 | 시작값 | 의미 |
|---|---:|---|
| 수신·상태 갱신 | 이벤트마다 | 신호 평가 간격과 별개 |
| 유량 집계 | 1초, 요약 bin 5초 | 차트 봉 신호가 아닌 거래 이벤트 합계 |
| 빠른/중간/배경 유량 EWMA | half-life 10/30/120초 | 단발성·지속성·배경 구분 |
| 신규 알파 판단 | 5초마다 | 매번 주문을 취소한다는 뜻 아님 |
| 방향성 확인 구간 | 30초 | 5초 bin 6개 |
| 기본 예측 horizon | 60초 | 사전 고정한 30/60/120초만 비교 |
| 신규 entry 주문 유효시간 | 활성화 뒤 최대 20초 | 이후 취소 효력까지 노출은 별도 |
| 기본 목표 청산 시간 | 진입 체결 후 30~90초 | 목표이며 보장 아님 |
| hard 최대 보유 | 첫 체결 후 120초 | 주문 재제출·추가체결로 리셋하지 않음 |
| 정상 가격 재호가 최소 간격 | 10초 | 위험 취소·fill 대응은 예외 |
| 가설 붕괴 후 cooldown | 30초 | 새 에피소드로 조건 재확인 |

연구용 horizon 필터:

$$
H\ge\max(30\text{s},6L_{entry,95},3B_{cancel,95}).
$$

여기서 `L_entry`는 모델의 기준 정보 시점에서 신규 주문 활성화까지의 총 시간이다. 6과 3은 경험 법칙이 아니라 초기 스트레스 비율이다. 이 조건을 통과해도 알파가 보장되지 않는다. 필요한 H가 120초를 초과하면 v1 거래 대상에서 제외하고 별도 장기 horizon 연구로 넘긴다.

horizon을 늘리는 것과 낡은 주문을 오래 방치하는 것은 별개다. 리스크 판단은 이벤트마다 수행하며, 스케줄러가 5초 뒤 실행될 때까지 위험 취소를 미루지 않는다.

## 5. 잔여 공격 주문 추정

### 5.1 관측 가능한 flow episode를 정의한다

5초 bin j에서 매수·매도 공격 체결량을 `B_j`, `S_j`라 한다. 최근 30초의 매수 참여율:

$$
\pi_B=\frac{\sum B_j}{\sum(B_j+S_j)}.
$$

롱 flow episode의 시작값:

- 최근 30초 매수 참여율 65% 이상.
- 6개 bin 중 4개 이상에서 `B_j>S_j`.
- 적어도 4개 bin에 실제 체결 존재.
- 최근 30초에 독립 trade ID 20개 이상. 거래소가 한 주문을 여러 체결로 쪼개어 전송하는 방식도 기록하고 표본 수로 과신하지 않는다.
- 최근 10초 매수 참여율 55% 이상. 현재의 명확한 반전은 긴 창의 매수 우세로 덮지 않는다.

이 조건은 진입 신호가 아니라 **잔여 flow를 추정할 상황의 정의**다. 가격이 이미 올랐다는 조건이나 가격 모멘텀은 넣지 않는다.

종료 규칙:

- 최근 10초 매수 참여율이 55% 미만인 상태가 5초 판단 두 번 연속 지속.
- 또는 최근 5초 반대 매도 참여율 65% 이상이며, 그 물량이 해당 종목의 배경 5초 거래량 중앙값 이상.
- 또는 거래 데이터 품질 붕괴.

age는 이 조건을 **처음 실시간 확인한 시점**부터 잰다. 나중에 발견한 에피소드 시작을 과거로 이동하지 않는다. 종료 검출에 걸리는 지연까지 학습·리플레이에 똑같이 반영한다.

### 5.2 유량의 크기

1초 간격 EWMA는 반감기 h에 대해 다음처럼 갱신한다.

$$
r_t^{(h)}=e^{-\ln(2)\Delta t/h}r_{t-\Delta t}^{(h)}
+(1-e^{-\ln(2)\Delta t/h})\frac{V_t}{\Delta t}.
$$

실제 경과시간 `Delta t`를 사용한다. 한 번에 몰려 도착한 메시지를 정상적인 등간격 이벤트로 간주하지 않는다.

롱의 보수적 현재 유량 시작값:

$$
r_{B,now}=\min(r_B^{(10)},r_B^{(30)}),\qquad
r_{S,now}=\max(r_S^{(10)},r_S^{(30)}).
$$

이는 급격히 늘어난 매수는 천천히 인정하고 늘어난 매도는 빠르게 반영하는 규칙이다. 숏은 반대로 적용한다.

배경 유량 `r_bg`는 과거 30분의 NEUTRAL 상태 5초 bin 유량 중앙값을 방향별로 구한다. 충분한 neutral bin이 없으면 120초 EWMA로 대체하고 신뢰도 경고를 붙인다. 최근 30분의 서로 의존하는 bin들을 독립 표본으로 세지 않는다.

### 5.3 현재 압력의 잔여 수명

미완료 모주문을 직접 관찰한 것처럼 표현하지 않는다. 대신 관측 flow episode의 잔여 지속시간을 추정한다.

age 구간은 0~30, 30~60, 60~120, 120~300초, 300초 이상으로 고정한다. 각 구간에서 다음 5초 내 종료 확률을 hazard `h_k`로 정의한다.

시작 prior:

$$
h_k\sim\operatorname{Beta}(1,1),\qquad
h_k\mid D\sim\operatorname{Beta}(1+d_k,1+n_k-d_k).
$$

`n_k`는 위험에 노출된 5초 구간 수, `d_k`는 그 구간에서 확인된 종료 횟수다. 이산 생존모형의 기본 prior이며, 인접 구간·에피소드 간 독립성을 보장하지 않는다. 불확실성은 에피소드/일자 block resampling으로 추가 평가한다. 표본이 적을 때 posterior를 확신으로 해석하지 않는다.

현재 age a 이후의 생존확률:

$$
S_a(u)=P(T>a+u\mid T>a,\text{현재 상태}).
$$

실제 계산은 5초 구간의 `1-h_k`를 곱한다. 이미 끝난 에피소드와 현재까지 관찰된 at-risk 구간만 사용한다. 수집 종료·통신 장애로 끝난 표본은 자연 종료로 세지 않고 censor 처리한다.

현재 상태는 기본적으로 방향과 age만 사용한다. 추가로 flow가 강해지는지/약해지는지 두 상태를 둘 수 있으나, 별도 out-of-sample 개선이 확인된 뒤에만 켠다. 임의의 많은 feature bucket을 만들지 않는다.

### 5.4 잔여 물량의 계산

미래 유량의 기본 식:

$$
\widehat r_d(u)=r_{d,bg}+
(r_{d,now}-r_{d,bg})S_a(u),\quad d\in\{B,S\}.
$$

압력이 종료되면 총 거래량이 0이 되는 것이 아니라 배경 유량으로 돌아간다. 매수와 매도는 따로 추정한다.

$$
Q_d(u_0,u_1)=\int_{u_0}^{u_1}\widehat r_d(u)\,du.
$$

`u_0`가 중요하다.

- 진입 평가: 신규 주문이 활성화되는 시점 이후의 물량.
- 청산 평가: 진입 체결, 체결통지 수신, exit 주문 활성화 이후의 물량.
- fill 재평가: 이미 관측된 체결량은 다시 잔여 물량에 넣지 않음.

**매수량에서 매도량을 뺀 net volume을 ask 깊이에 대입하지 않는다.** 매수는 ask를, 매도는 bid를 소비하며 시간 순서와 재공급이 다르다. 두 유량이 모두 크면 양쪽 queue를 함께 평가해야 한다.

### 5.5 불확실성을 얻는 방법

첫 버전에서는 파라미터의 평균만으로 주문하지 않는다.

1. 과거의 같은 방향·age 구간에서 현재 모델이 예측한 미래 B/S 물량과 실제 B/S 물량의 잔차를 저장한다.
2. 양쪽 유량·공급 변화가 함께 나타난 연속 구간을 block으로 재표집한다. 변수별로 독립 추출하지 않는다.
3. 매수의 낮은 잔여량, 강한 ask 재공급, 강한 반대 매도를 포함한 스트레스 경로를 계산한다.
4. 데이터가 부족하면 과도한 범용 보정을 하지 않고 해당 종목을 shadow 상태로 둔다.

예를 들어 매수량 25% 분위수와 ask 공급 75% 분위수를 조합할 수는 있지만, 그 조합을 75% 신뢰구간이라고 부를 수는 없다. 이것은 보수적인 스트레스 시나리오다. 실제 확률은 공동 경로와 out-of-sample 체결 결과로 보정한다.

## 6. 유효 유동성: 잔량이 아니라 소진까지 필요한 서비스량

### 6.1 기본 장부 항등식

동일한 절대 가격 p, 정렬된 관측 구간에서:

$$
D_{new}(p)=D_{old}(p)+A(p)-C(p)-M(p).
$$

따라서:

$$
R_{net}(p)=D_{new}(p)-D_{old}(p)+M(p)=A(p)-C(p).
$$

`M`은 해당 호가 면을 소비한 실제 체결량이다. 이 항등식은 관측 순서·가격·단위가 맞을 때만 의미가 있다. 숨은 주문, feed batch, 체결/호가 송신 차이, queue 재생 방식이 있으면 `R_net`을 순유효 공급의 추정치로 해석한다.

`max(R_net,0)`과 `max(-R_net,0)`을 각각 gross addition/cancellation의 정확한 관측치라고 부르지 않는다. 이는 순변화의 양·음 성분이다. 동일 구간의 대량 추가와 대량 취소가 상쇄될 수 있다.

가격 레벨이 top-N 범위 밖으로 나갔다고 cancellation으로 세지 않는다. 거래소가 outgoing level을 0으로 전송하는 feed라면 범위 이탈과 실제 삭제를 분리한다. 같은 rank의 잔량끼리 비교하지 않고 절대 가격을 매칭한다.

### 6.2 흡수와 공급 고갈의 측정

front queue 구간은 현재 best 및 2 tick 이내에서 실제 체결 압력을 받은 가격으로 제한한다. 5초 요약 구간의 공급/소비 비율:

$$
\rho_A=\frac{R^+_{A,front}}{M^B_{front}+\epsilon},\qquad
\rho_B=\frac{R^+_{B,front}}{M^S_{front}+\epsilon}.
$$

`R+`는 짧은 정렬 구간별 양의 순공급 추정치를 누적한 값이다. `epsilon`은 1 lot이며, 실제 소비량이 너무 작으면 ratio를 계산하지 않는다.

- `rho_A`가 높고 ask 가격이 버팀: buy absorption. 유리한 롱 continuation으로 보지 않음.
- `rho_A`가 낮고 실제 buy 체결 후 잔량 감소가 지속: ask 공급 고갈 후보.
- `rho_B`가 높고 sell 체결 후 bid 회복: 진입 쪽 일시적 매도를 흡수할 후보.
- 양쪽 depth가 함께 사라짐: 단순 방향 알파보다 유동성 위험으로 취급.

초기 스크리닝은 롱에서 `rho_A<0.7`인 5초 구간이 최근 3개 중 2개 이상인 경우다. 동시에 유량과 잔량을 직접 사용한 소진시간 계산이 통과해야 한다. `rho<0.7` 자체가 확정적인 알파는 아니다.

bid 흡수는 매도 체결이 관측된 뒤 평가한다. 매도가 없어서 `rho_B`가 정의되지 않는 상태를 강한 지지로 간주하지 않는다. 최근 30초에 매도 충격 뒤 bid가 5~10초 안에 회복하는 사례가 2회 이상 있는지를 후보 필터로 기록한다. 정확한 복원량·시간은 소진 모형과 함께 보정한다.

이를 수치 gate로 바꾸기 위해 entry 후보 b에서 `b-2 tick`까지를 초기 방어 구간으로 둔다. 그 구간의 취소 스트레스 후 잔량과 하위 분위수 재공급을 더한 양이 `B_cancel,95 + L_private,95` 동안의 상위 분위수 sell 물량보다 커야 한다. 더 깊은 방어 구간은 stop·sizing과 함께 별도 검증한다. 방어는 최우선 bid가 절대로 소진되지 않아야 한다는 뜻이 아니다. 내 bid 체결에 필요한 일시적 소진과 그 아래 가격까지 연속으로 무너지는 위험을 구분한다. 실제 fill 후에도 이 구간을 최신 book에서 다시 계산한다.

### 6.3 순차 소진시간

ask 가격 k에서, 해당 queue가 실제로 공격받기 시작한 이후의 소비율을 `r_B`, 공급률을 `r_A`, 신뢰 가능한 취소율을 `c_A`라 하면:

$$
\kappa_{A,k}=r_B+c_{A,k}-r_{A,k}.
$$

모수가 일정한 국소 근사에서:

$$
\Delta\tau_k=\frac{D_{A,k}(\tau_k)}{\kappa_{A,k}},\qquad\kappa_{A,k}>0.
$$

`kappa<=0`이면 그 상태가 유지되는 동안 해당 가격은 소진되지 않는다. 반대로 bid 소진율은:

$$
\kappa_{B,k}=r_S+c_{B,k}-r_{B,k}^{replenish}.
$$

롱의 이상적인 조합은 ask의 빠른 소진과 bid의 긴 생존이다. 그러나 정해진 1회 ratio로 미래를 확정하지 않고 상태가 바뀔 때 재계산한다.

시간가변 모형은 다음 최초 통과시간을 사용한다.

$$
\tau_{k+1}=\inf\left\{u\ge\tau_k:
\int_{\tau_k}^{u}[r_B(v)+c_{A,k}(v)-r_{A,k}(v)]dv
\ge D_{A,k}(\tau_k)\right\}.
$$

깊은 레벨은 아직 best가 아니었을 때와 실제 공격받을 때의 공급률이 다르다. 초기 잔량에 모든 레벨의 `H*refill_rate`를 더하면 재공급을 중복 계산한다. 각 레벨의 도달 시점과 front 노출시간을 분리한다.

기본 보수 모드:

- 수익을 키우는 ask cancellation은 확실한 관측·지속성 검증 없으면 0으로 둔다.
- 위험을 키우는 bid cancellation은 stress에 반영한다.
- ask 재공급은 같은 front 상태의 상위 분위수를 저항으로 반영한다.
- bid 지지는 현재 잔량 전부를 믿지 않고 취소 스트레스 후 잔량을 사용한다.
- 더 좋은 가격에 새 ask가 들어오는 spread 내부 공급도 서비스량에 포함한다.
- 관측된 book 깊이 밖으로 목표가가 나가면 마지막 레벨에서 cap하고, 관측 범위 밖을 유동성 0으로 간주하지 않는다.

### 6.4 두 종류의 exhaustion

용어를 구분한다.

- **수동 공급 고갈:** 매수가 계속되는데 ask가 더 이상 충분히 재생되지 않음. 롱 후보.
- **공격 매수 고갈:** 매수 속도·잔여 수명이 감소. 롱 종료 요인.

많이 올랐거나 한 번 대량 매수가 있었다는 이유만으로 어느 고갈인지 결정하지 않는다.

## 7. 가격 산출의 세 층

| 출력 | 정의 | 주문에서의 사용 |
|---|---|---|
| `P_struct` | 추정한 flow와 공급 시나리오에서 도달 가능한 구조적 가격 | 시장 상태와 잠재 기회 표시 |
| `P_reach` | 약한 flow·강한 공급 등 보수 경로에서 도달 가능한 가격 | 목표가의 상한/하한 제한 |
| `P_exit` | 내 exit 주문이 활성화된 뒤 앞 queue와 내 수량까지 소비될 목표가 | 실제 청산 주문과 entry edge 계산 |

이 중 `P_exit`을 운용상의 tradable FV로 쓴다. 이는 fundamental value나 미래 mid의 불편추정량이 아니다. 매수·매도에 대해 조건부 실행가격을 따로 가진다. 두 출력의 단순 평균으로 공통 mid FV를 만들 필요가 없다.

### 7.1 청산 queue의 제약

롱 진입 체결시각을 `tau_f`라 하면, 거래소에 exit ask를 올릴 수 있는 시점은 최소 다음과 같다.

$$
\tau_{exit,live}=\tau_f+L_{private}+L_{compute}+L_{new,exit}.
$$

exit ask 가격 p의 보수적 필요 물량:

$$
V_{need}(p,q)=V_{better\ asks}(p)+V_{ahead}(p)
+V_{new\ better}(p)+q+V_{reserve}.
$$

모든 항목은 exit 주문 활성화 이후의 경로를 기준으로 계산한다. `V_ahead`는 같은 가격의 앞 queue, `V_new_better`는 이후 더 낮은 ask에 유입되어 매수를 먼저 받아갈 공급이다. 내 뒤에 추가된 같은 가격 주문은 FIFO라면 앞 queue가 아니다. 모를 때 모든 불확실 공급을 앞에 놓는 계산은 보수적 상한으로 표시한다.

기본 필요조건:

$$
Q_B(\tau_{exit,live},T_{deadline})\ge V_{need}(p,q).
$$

이 합계 부등식만으로 충분하지 않다. 체결 시점, 취소, 순서, bid 붕괴, 가격 이탈, flow 종료를 포함한 경로가 유효해야 한다. 특정 가격까지 거래됐더라도 내 ask가 나중에 도착했다면 그 체결은 내 청산에 사용할 수 없다.

### 7.2 공동 경로에서 주문을 평가한다

정확도를 높이는 연구 엔진은 다음을 구현한다.

1. 현재 양쪽 book과 flow episode를 복사한다.
2. 과거 데이터로 보정한 B/S 유량, 생존, front 공급을 동일 시간축에서 진행한다.
3. 초기 주문은 실제 latency 후 삽입하고, entry queue가 소진된 경로에서만 fill을 인정한다.
4. fill 알림 지연과 exit 제출 지연을 거친 뒤 exit queue에 삽입한다.
5. 목표 청산·가설 붕괴·stop·timeout을 먼저 발생한 순서대로 처리한다.
6. 부분 체결, 취소 중 fill, 남은 inventory의 강제 청산까지 전부 계산한다.

큐 이벤트의 조건부 모형은 연구용 합성기다. 합성기 내부 수익을 실시장 수익성의 증거로 삼지 않는다. **초기 실전 허용은 실제 과거 이벤트 리플레이와 소액 실주문 결과의 일치까지 요구한다.**

기본 가격기는 위의 순차 소진식과 고정된 보수 스트레스 경로로 가볍게 운영할 수 있다. 복잡한 온라인 Monte Carlo는 필수가 아니다. 조건부 성공률과 손익의 검증표는 과거 실시간 규칙 그대로의 리플레이에서 얻고, 가격을 직접 회귀하는 core 모델로 대체하지 않는다.

### 7.3 실행 검증 gate

후보 entry 가격 b, exit 가격 a, 수량 q에 대해 다음을 따로 기록한다.

- `P(entry fill before entry TTL)`.
- `P(full maker exit before deadline and invalidation | entry fill)`.
- `E[all-cost net PnL | entry fill]`.
- `E[all-cost net PnL per submitted quote]`.
- 실패 시 taker 청산 손실과 tail loss.

정상 청산 성공률 prior는 `Beta(1,1)`로 명시하고, 독립성이 약하므로 fill 건수가 아니라 에피소드 단위로 집계한다. 확신 구간은 일자/에피소드 block resampling으로 재확인한다. 표본이 적으면 주문하지 않는다.

초기 gate는 다음과 같다.

$$
LB_{95\%}(E[\Pi_{net}\mid fill])>0,
$$

$$
LB_{95\%}(P_{normal\ exit}\mid fill)
\ge\max(0.70,p_{BE}+0.05).
$$

0.70과 여유 0.05는 연구 시작값이다. `p_BE`는 평균 이익 G, 평균 실패 손실 L, 왕복 비용 C를 단순화하면 `(L+C)/(G+L)`이다. 실제 승인에는 평균만 쓰지 않고 전체 stop·지연·미체결 분포의 순손익을 사용한다. 이미 비용을 포함한 G/L로 p_BE를 계산할 때 C를 다시 빼지 않는다.

위 통계는 메커니즘의 실행 가능성을 거르는 검증층이다. 가격 산출 자체는 잔여 flow와 유효 공급의 소진에서 나온다.

검증표는 venue/symbol, 방향, 고정 horizon, entry 가격 offset별 동일 정책의 시간순 replay로 만든다. age 구간별 결과도 확인하되 표본이 부족한 세부 cell은 합리적 사전 pooling이 검증되지 않는 한 거래 적격으로 승인하지 않는다. 현재 주문을 본 뒤 유리한 cell 경계를 새로 만들지 않는다. 테스트 구간에 들어갈 때 가격기·gate·셀 정의·오차 분위수를 함께 동결한다.

## 8. 최소 edge와 주문가격

롱의 제안 entry b와 목표 a에 대해:

$$
g=a-b.
$$

진입 필터:

$$
g\ge C_{fees}+C_{funding}+B_{latency}+B_{model}+E_{profit}.
$$

- `C_fees`: 실요율의 진입·정상 청산 비용. emergency taker 비용은 별도 실패 경로에도 포함.
- `C_funding`: 실제 보유 구간이 funding 정산을 걸치는 경우의 비용. 부호와 정산 규칙 반영.
- `B_latency`: 취소 불가능 구간의 불리한 가격 이동 경험 분위수로 만든 여유.
- `B_model`: 과거에 과대평가한 청산가격 오차의 보수적 여유.
- `E_profit`: 최소 1 tick에서 시작하는 비용 초과 여유.

buffer는 주문 승인용 보수 장치다. 실제 순손익을 보고할 때 가상의 buffer를 비용으로 또 빼지 않는다. 반대로 stress 경로에 이미 들어간 같은 손실을 여러 이름으로 중복 차감해 수익성 수치를 만들지 않는다.

처음에는 고정 2bp나 3bp를 모든 종목에 적용하지 않는다. 1 tick의 bp 크기, 지연 중 변동, 청산 깊이, 실제 수수료가 다르다.

### 8.1 entry 가격

기본 후보는 롱에서 `best bid`, `best bid-1 tick` 두 개, 숏에서 `best ask`, `best ask+1 tick` 두 개다. 각 후보는 별도 체결 조건부 결과가 필요하다.

스프레드 내부로 1 tick 개선하는 후보는 초기 비활성화한다. 활성화하려면 post-only의 거절 처리와 해당 후보의 fill 품질을 별도 검증한다. 무조건 queue를 앞으로 당기는 기능은 없다.

가장 깊은 가격을 무조건 선택하지 않는다. 할인은 커져도 반전 때만 체결되는 주문이 될 수 있다. 통과한 후보 중 보수적인 시간당 순기대수익이 큰 후보를 선택한다. 비슷하면 기존 queue를 유지하는 후보를 우선한다.

### 8.2 exit 가격

관측된 tick 가격들 중 `P_reach`를 넘지 않으며, 내 물량까지 서비스되는 가장 높은 ask를 롱의 기본 후보로 잡는다. 숏은 가장 낮은 bid다. 이후 정상 전량 청산률과 손익 gate를 통과해야 한다.

목표를 한 tick 앞당기는 것이 체결확률을 충분히 개선하면 그 후보를 선택할 수 있다. 이는 FV보다 무조건 한 tick 싸게 파는 규칙이 아니다. 개선 전후의 순기대수익과 청산시간을 비교한다.

신호가 강해져도 기존 이익실현 목표를 계속 멀리 올리지 않는다. v1은 진입 때 정한 정상 목표를 기본적으로 유지한다. 위험 때문에 목표를 낮출 수 있으나 원래 목표의 도달·체결 여부는 원래 값으로 별도 평가한다.

## 9. 진입 규칙 전체

롱은 다음 조건을 모두 통과해야 한다.

| 순서 | 조건 | 실패 시 |
|---|---|---|
| 1 | book/trades/private feed, 수량 단위, 계정 대사 정상 | HALT 또는 관측 전용 |
| 2 | 검증된 지연 분포 범위 내, source 정보가 유효 | 신규 주문 취소 |
| 3 | 매수 flow episode 활성, age·표본 적격 | NO_QUOTE |
| 4 | 실제 buy 소비가 ask 공급을 초과, 보수 소진 경로 유효 | NO_QUOTE |
| 5 | bid가 반대 sell을 흡수, 양쪽 동시 유동성 철수 아님 | NO_QUOTE |
| 6 | entry와 exit 지연 후에도 정상 목표가의 청산 물량 여유 존재 | NO_QUOTE |
| 7 | 최소 edge, 체결 조건부 순손익·정상 청산률 gate 통과 | NO_QUOTE |
| 8 | 포지션+전량 미체결 주문 시나리오가 한도 내 | 수량 축소 또는 NO_QUOTE |
| 9 | 청산 깊이·funding·mark·동시 종목 위험 정상 | NO_QUOTE |
| 10 | 승인한 후보 가격·수량으로 post-only 전송 | 거절 후 상태 재평가 |

`NO_QUOTE`는 실패가 아니라 정상적인 엔진 출력이다. 거래량을 만들기 위해 어느 조건을 임의로 제거하지 않는다.

종목 적격성의 추가 조건:

- 평균 잔량보다 실제 체결과 회복이 관측되는 시장을 우선한다.
- 60초에 체결이 거의 없어서 잔여 flow·queue를 추정할 수 없으면 스프레드가 넓어도 제외한다.
- 예측 목표가까지의 book이 관측되지 않으면 제외한다.
- 동일 자산의 주요 시장과 괴리가 지속되고 정상적인 basis인지 판단하기 어렵다면 신규 진입을 멈춘다. 외부 가격은 core 알파를 대신하지 않는 sanity check다.
- 기초시장 폐장으로 가격발견이 약해진 RWA를 24/7이라는 이유로 강제 거래하지 않는다. v1은 연속적인 관측과 가격발견이 가능한 crypto perpetual에서 시작한다.

## 10. 주문 생애주기

### 10.1 신규 주문

- `post_only=true`, 유효 tick·lot, 고유 `client_order_id`.
- 주문 의도에 `ENTRY` 또는 `EXIT`를 부여한다.
- 호가 변경마다 새로운 `decision_id`와 사용한 모델 버전을 저장한다.
- 주문 HTTP 응답 성공과 실제 book 활성화를 구분한다.
- timeout은 미전송으로 간주하지 않는다. 같은 의도를 새 ID로 재전송하기 전에 상태를 조회한다.
- 가능한 거래소에서는 native expiry/dead-man 기능을 사용한다. 지원 여부와 expiry가 적용되는 정확한 의미를 어댑터에서 검증한다.

서버 측 expiry가 없으면 local TTL 후 취소를 요청하지만, 효력까지의 시간에도 주문은 살아 있다. native expiry를 쓰더라도 시계 오차·최소 만기·정산 의미를 확인한다.

기본 정상 청산 deadline은 최초 후보의 `decision_time+H`다. 해당 주문의 미체결 시간도 알파의 수명에서 소비된다. 같은 에피소드에서 주문을 고쳤다고 deadline을 무한히 연장하지 않는다. 최대 보유 120초는 이 정상 목표 deadline보다 바깥의 별도 위험 한도다.

### 10.2 유지·재호가

5초마다 새 FV를 계산해도 매번 취소·재제출하지 않는다.

- 현재 주문가격의 보수 edge가 유지되고 queue 대기가 유리하면 유지.
- 정상적인 개선은 목표 변화가 2 tick 이상이거나 현재 edge의 25% 이상 바뀌었을 때 검토.
- 정상 재호가는 최소 10초 간격. 실제 rate limit과 취소효력 시간을 포함해 조정.
- 알파 무효·재고 한도·데이터 이상에서는 간격 제한 없이 즉시 취소 요청.
- 취소 확인 전에 replacement를 내는 경우에는 old와 new가 모두 체결되는 경우까지 승인해야 함.
- 가격·수량 수정이 queue priority를 유지하는지는 거래소 명세를 확인. 모르면 priority 상실로 처리.

기존 queue를 지키는 가치가 잘못된 가격을 유지하는 근거가 될 수는 없다.

### 10.3 부분 체결

100 중 30이 체결되면:

1. 실제 포지션에 30 반영.
2. 원주문의 잔여 70과 모든 pending 주문을 유지한 채 위험 계산.
3. fill 시점과 fill 수신시점 기록.
4. 알파·bid 흡수·잔여 청산량을 재평가.
5. 체결된 30만큼의 exit 의도를 만든다. 최소 주문 수량 미만이면 다음 fill과 묶되 이미 가진 노출로 계산한다.
6. 남은 70을 유지할지 취소할지 독립적으로 결정.

fill 즉시 재평가는 로컬에서 통지를 받은 즉시라는 뜻이다. 실제 거래소 체결과 동시에 판단한 것으로 리플레이하지 않는다.

### 10.4 주문 상태

`INTENT -> PENDING_NEW -> LIVE/PARTIAL -> PENDING_CANCEL -> CANCELLED/FILLED`.

거절은 `REJECTED`, 전송 결과 불명은 `UNKNOWN`으로 간다. `PENDING_NEW`, `PENDING_CANCEL`, `UNKNOWN`을 전부 아직 체결 가능한 주문으로 위험 장부에 남긴다. cancel ACK 뒤 늦게 도착한 fill도 실제 exchange fill ID로 중복 제거하여 반영한다.

단순 타이머만으로 `UNKNOWN`을 삭제하지 않는다. 조회·대사로 최종 상태를 확인하기 전까지 신규 노출을 제한한다.

## 11. 간단한 재고관리

복잡한 inventory penalty를 FV 식에 넣지 않는다. 예측 가격과 재고 위험을 분리한다. 재고가 늘면 **허용 수량과 신규 진입 여부**를 조절한다.

### 11.1 단위 수량과 최대 3단위

예시 단위 `q0=100`, 한도 `I_max=300`을 사용하면 다음처럼 운용할 수 있다. 숫자 100은 설명용 수량이며 특정 계정에 대한 권장 주문금액이 아니다.

| 실제 롱 재고 | 롱 진입 주문 최대 | 정상 청산 주문 | 추가 조건 |
|---:|---:|---:|---|
| 0 | 100 | 없음 | 롱 알파 통과 |
| 100 | 최대 100 | 총 미체결 exit 최대 100 | fill 후 알파·손실예산 재통과 |
| 200 | 최대 100 | 총 미체결 exit 최대 200 | 첫 lot의 deadline·위험 정상 |
| 300 | 0 | 총 미체결 exit 최대 300 | 청산 전용 |
| 가설 붕괴 | 0 | 현재 재고 감소 목적 | 모든 entry 취소 |

추가 주문은 가격 하락이나 단순 fill 자체가 아니라 **다시 확인한 긍정적 edge**가 허용한다. q0는 커지지 않는다. 손실을 회수하려고 다음 단위 수량을 늘리지 않는다.

초기 버전은 1개 entry 가격만 사용한다. 2단계 ladder는 이후에 같은 총 risk budget 안에서 `0.5 q0 + 0.5 q0`로 나누는 형태로 추가한다. 가격을 한 단계 더 늘린다고 총 주문수량을 늘리지 않는다.

### 11.2 미체결 주문까지 재고로 예약한다

실제 재고 I, 아직 체결 가능한 buy 잔량 합 `B_open`, sell 잔량 합 `S_open`에 대해:

$$
I_{worst,long}=I+B_{open},\qquad
I_{worst,short}=I-S_{open}.
$$

$$
I_{worst,long}\le I_{max},\qquad
I_{worst,short}\ge-I_{max}.
$$

buy와 sell을 서로 상쇄하지 않는다. 한쪽만 모두 체결될 수 있다. exchange가 강제하는 reduce-only 주문은 정확한 계정 semantics로 최악 노출을 계산할 수 있지만, 그렇지 않으면 위의 보수식으로 제한한다.

계정 private 상태가 뒤늦게 들어오는 경우 마지막 확정 포지션과 모든 미확정 체결 가능성을 함께 추적한다. pending 취소를 끝난 주문으로 빼버리지 않는다.

### 11.3 사용자가 제시한 100/100 방식의 적용

`bid 100 + ask 100 -> bid fill -> ask 100 추가 + bid 100 재제출`은 다음 두 점을 수정하면 실행층으로 사용할 수 있다.

첫째, 강한 롱 상태에서 기존 ask는 잠재적으로 불리한 숏 진입이다. 평탄한 상태에서 양방향 가격이 각각 양의 edge를 가질 때만 100/100을 허용한다. v1의 기본은 롱 신호에 bid, 숏 신호에 ask 한쪽이다.

둘째, 매수 100 체결 뒤 기존 매도 100이 살아 있는데 매도 100을 또 추가하면 총 매도 200이다. 실제 재고가 100뿐이면 반대 숏으로 넘어갈 수 있다. 따라서 **청산 수량 총합을 실제 재고에 맞추고 기존 ask를 갱신·대체**한다. 매도 주문 추가와 재고 감소는 같은 일이 아니다.

권장 루프:

`유효 롱 -> bid 100 -> fill 100 -> reduce-only ask 총 100 + 조건부 bid 100 -> I=300이면 buy 중단`.

exit는 exchange가 지원하면 reduce-only를 사용한다. 지원하지 않는 경우에는 미확정 exit를 포함한 총 매도 가능량이 확정 롱 재고를 넘지 않는 직렬화된 제어가 필요하다. 이 대체 방식의 race를 검증하지 못하면 실거래를 켜지 않는다.

### 11.4 양방향 quote를 허용하는 정확한 조건

선택 확장에서는 bid가 체결된 표본과 ask가 체결된 표본이 각각 양의 기대손익이어야 한다. 공통 FV 하나가 mid 근처라는 이유로 두 방향을 승인하지 않는다.

양방향 가격구간의 개념적 필요조건:

$$
b+C_B+E_B\le FV_{long}^{conservative},
$$

$$
a-C_S-E_S\ge FV_{short}^{conservative}.
$$

재고가 생기면 반대 주문의 의도를 EXIT로 바꾸고 수량을 제한한다. atomic한 변경이 안 되면 취소 대기까지 포함한 노출을 계산한다. v1에서는 이 복잡성을 피하려고 평탄한 상태의 양방향 quote를 기본 비활성화한다.

## 12. sizing과 portfolio 한도

계정 equity를 E, 단위 진입의 스트레스 손실률을 `ell`, 그 단위의 손실예산을 R이라 하면:

$$
N_{unit}\le R/\ell.
$$

초기 제안:

$$
N_{unit}=\min\left(
\frac{N_{symbol,max}}3,
\frac{0.0005E}{\ell_{stress}},
0.01V_{opp,60s},
0.05D_{liquidation},
N_{exit\ capacity}
\right).
$$

- `N_symbol,max=0.5E`: 종목별 최대 명목노출의 연구 시작값.
- `V_opp,60s`: 내 정상 청산을 받아줄 방향의 최근 60초 실제 공격 주문 명목금액. 롱은 buy, 숏은 sell.
- `D_liquidation`: 스트레스 가격 범위 내 즉시 재고를 줄일 수 있는 반대편의 신뢰 가능한 누적 잔량.
- `N_exit_capacity`: 목표 ask/bid에서 내 앞 queue를 제하고 남은 보수적 서비스 물량으로 정한 수량.
- `ell_stress`: 진입 시 정한 stop 거리, 취소 불가 구간 이동, taker 청산 수수료·impact를 포함한 손실률.

q0는 이 금액을 현재 계약가치로 나눈 뒤 lot 단위 내림한다. 최소 주문금액 미달이면 거래하지 않는다. 주문을 넣은 뒤 가격이 움직였다고 기존 예약수량을 늘리지 않는다.

초기 portfolio 한도:

| 한도 | 시작값 |
|---|---:|
| 한 종목 최대 단위 수 | 3 |
| 전체 gross 명목노출 | E 이하 |
| 같은 방향 crypto 위험 묶음 | 0.75E 이하 |
| 단위 진입 stress 손실예산 | equity 0.05% |
| 일중 손실 중단 | 당일 시작 equity 대비 1% |
| 고점 대비 중단 drawdown | 5% |

이는 연구·소액 도입용 제한이다. 사용자의 실제 운용자금과 변동성 분포가 없으므로 수량의 최적값은 계산하지 않는다. 여러 alt 롱을 서로 다른 종목이라는 이유로 분산 포트폴리오로 취급하지 않는다. 동일 자산을 여러 거래소에서 거래하면 총 델타를 합산한다.

필요 증거금, 유지증거금과 mark 변화에 대한 여유를 추가 제한한다. cross margin의 다른 포지션과 공유하는 위험도 포함한다. 청산가격을 손절가격으로 사용하지 않는다.

## 13. 청산·가설 무효·손절

### 13.1 정상 청산

- 체결 후 잔여 흐름과 앞 queue를 갱신한다.
- 목표가가 여전히 유효하면 기존에 승인한 목표에 reduce-only post-only 주문.
- 실제 exit 주문 활성화 이후의 buy/sell 물량만 청산 서비스에 계산.
- 목표에 mid 또는 체결가가 닿았다는 이유로 체결 완료 처리하지 않음.
- 부분 exit 후 재고와 남은 exit 수량 재대사.

### 13.2 soft invalidation

롱에서 다음 상태가 두 차례 5초 평가에 걸쳐 지속되면 정상 가설이 약해진 것으로 본다.

- 잔여 매수량 하락으로 앞 queue+내 물량을 더 이상 서비스하지 못함.
- ask 공급이 회복되어 `kappa_A<=0`.
- bid 흡수력이 약해지고 반대 sell 소비율이 상승.
- 현재 목표의 정상 청산률 gate 이탈.

처리: 모든 신규 롱 entry 취소, exit 목표를 현재 가능한 가격으로 조정하고 최대 10초의 수동 청산 유예. 유예 중 더 악화되면 즉시 hard 경로로 넘어간다. 이 10초에 실제 취소·신규 제출 지연을 추가해 백테스트한다.

### 13.3 hard invalidation

- 강한 반대 flow와 bid의 실제 소진이 함께 발생.
- 사전 설정한 hard loss·계정 손실 한도 도달.
- margin 또는 mark 기반 청산 위험 증가.
- 거래소 주문 상태 불일치, sequence gap, 심각한 stale market data.
- 첫 진입 후 120초 maximum holding time 도달.

처리: 신규 노출을 차단하고 pending entry를 취소한다. 정상적인 연결과 신뢰 가능한 가격이 있으면 reduce-only marketable IOC로 잔여 포지션을 줄인다. 슬리피지 제한을 둔 IOC는 전량 체결이 보장되지 않는다. 남은 수량을 재대사하고 허용된 가격 한도 안에서 재시도한다. 무한 재시도는 금지하며 3회 실패 또는 주문 결과 UNKNOWN이면 HALT와 운영자 알림으로 전환한다.

데이터/주문 연결이 완전히 끊겼을 때 즉시 시장가 청산이 가능하다고 가정하지 않는다. 거래소 native 보호주문이 있으면 사전 검증한 방식으로 사용하고, 없으면 잔여 노출을 명시적으로 표시한다. kill-switch는 새 노출을 멈추는 기능이지 원격 포지션을 반드시 0으로 만드는 보증이 아니다.

### 13.4 평균단가 대신 현재 선택지를 비교

가설의 유지 여부를 `FV - 평균매수가`만으로 판단하지 않는다. 평균단가는 손익 기록과 hard budget에 필요하지만 이미 지출한 가격이다.

롱의 보유 판단은 개념적으로:

$$
G_{hold}=E[P_{future\ liquidation}\mid\mathcal F_t,I]
-P_{liquidation\ now}-C_{incremental}-R_{incremental}.
$$

즉 지금 줄이는 것보다 더 보유할 추가 가치가 있는지를 판단한다. `G_hold<=0`이면 손익이 플러스여도 나가고, 원가가 높다는 이유로 목표를 올려 기다리지 않는다. 모델이 불확실할 때는 위험감축을 우선한다.

### 13.5 hard price stop

FV 무효화와 별도로 원격 취소 지연·급변에 대비한 hard stop을 둔다. 시작 distance는 과거 적격 에피소드 전체의 H 구간 불리한 이동 99% 분위수, 취소 불가능 시간의 99% 이동, 최소 2 tick 중 큰 값에 청산 여유를 더해 정한다. 이 거리로 sizing을 줄인다.

성공한 거래만으로 stop 거리를 추정하지 않는다. 손실 꼬리와 jump가 표본에 없을 수 있으므로 depth 축소·지연 2배의 별도 stress도 계산한다. 실제 손실은 gap과 청산 유동성 때문에 이 stop을 넘을 수 있다.

## 14. 숫자 예시

다음은 산술 검증용 가상 예시이며 실제 데이터에서 얻은 승률·유량이 아니다.

현재 `bid=99.99`, `ask=100.01`, tick 0.01. 기본 horizon은 최초 결정 시각부터 60초다. 현재 buy 유량 300 units/s, 배경 100 units/s, 잔여 지속성은 설명을 위해 `S(u)=exp(-u/40)`이라고 둔다.

$$
Q_B(u,60)=100(60-u)+8000(e^{-u/40}-e^{-60/40}).
$$

| 사용할 수 있는 시작 시각 | 남은 buy 물량 |
|---:|---:|
| 결정 시각 0초 | 12,214.96 |
| entry 활성화 5초 | 10,774.93 |
| fill·통지·exit 제출 후 20초 | 7,067.20 |

entry가 12초에 100 units 체결되고, private 알림 3초와 exit 제출 5초를 더해 exit가 20초에 활성화됐다고 가정한다. 미래의 20초를 미리 안다는 뜻이 아니다. 진입 전에는 가능한 fill 시각 경로별로 평가하고, 실제 fill 후에는 수신된 정보로 재평가한다.

exit 활성화 시점에 전망한 ask 서비스량을 다음처럼 가정한다. 각 가격 물량은 그 시나리오의 앞 잔량과 예상 재공급을 포함한다.

| ask 가격 | 해당 가격 유효 서비스량 | 해당 가격까지 누적 | 내 100 + 불확실성 500 포함 |
|---:|---:|---:|---:|
| 100.01 | 1,200 | 1,200 | 1,800 |
| 100.02 | 1,400 | 2,600 | 3,200 |
| 100.03 | 1,800 | 4,400 | 5,000 |
| 100.04 | 1,600 | 6,000 | 6,600 |
| 100.05 | 2,200 | 8,200 | 8,800 |
| 100.06 | 1,800 | 10,000 | 10,600 |

잔여 7,067은 100.04의 6,600을 넘지만 100.05의 8,800에는 미달한다. 이 시나리오에서 내 전량 exit 후보는 **100.04**다. 처음의 12,215를 계속 사용하면 이미 지나간 매수량을 다시 사용해 과도한 목표를 낸다.

99.99 매수 -> 100.04 매도는 약 5.0005bp gross edge다. 단, 지연·모형·비용 buffer 합계가 5bp보다 크거나 체결 조건부 gate가 통과하지 못하면 이 예시에서도 주문하지 않는다.

승률과 손익을 분리하는 추가 예시:

- 정상 성공 80%, 성공 이익 5bp, 실패 손실 8bp, 별도 평균 비용 0.4bp라면 순기대값은 `0.8*5-0.2*8-0.4=2.0bp`.
- 손익분기 성공률은 `(8+0.4)/(5+8)=64.62%`.
- 같은 80% 승률이어도 실패 손실 25bp면 순기대값은 `4-5-0.4=-1.4bp`.

높은 청산 승률은 필요할 수 있지만 충분하지 않다.

## 15. 상태 머신

| 현재 상태 | 전이 조건 | 다음 상태 | 행동 |
|---|---|---|---|
| BOOT/SYNC | market/private snapshot·메타데이터 대사 완료 | WATCH | 워밍업 |
| WATCH | 데이터·표본 적격, flow 후보 | ARMED_LONG/SHORT | 가격·서비스량 계산 |
| ARMED | 모든 진입 gate 통과 | QUOTING_LONG/SHORT | 한 방향 entry 제출 |
| ARMED | 조건 해제 | WATCH | 주문 없음 |
| QUOTING | 부분/전량 fill | INVENTORY_LONG/SHORT | 재평가, exit 생성 |
| QUOTING | TTL/알파 해제 | DRAIN | entry 취소, 확정 상태 확인 |
| INVENTORY | edge 지속, 예약재고 여유 | INVENTORY | 조건부 다음 단위 entry |
| INVENTORY | 정상 exit로 I=0, pending 없음 | WATCH | 손익·라벨 확정 |
| INVENTORY | soft invalidation | DRAIN | 추가 entry 중단, 수동 청산 유예 |
| ANY | hard 위험, 단 연결 정상 | REDUCE | 취소·위험감축 |
| ANY | 연결/계정 불명·일중 손실·DD 초과 | HALT | 신규 노출 차단, 대사·알림 |
| DRAIN/REDUCE | I=0, 모든 주문 최종 확인 | COOLDOWN | 30초 및 새 episode 확인 |
| HALT | 원인 복구·계정 대사·재개 조건 충족 | WATCH | 자동 또는 명시적 운영 재개 정책 |

연속 손실 횟수만으로 통계적 실패를 단정하지 않는다. 다만 일중/누적 손실 한도는 독립적인 운영 한도이므로 모델이 낙관적이어도 우선한다. 손실 한도 HALT는 운영자 검토 전 자동 재개하지 않는 것을 초기 정책으로 둔다.

반대 방향 신호가 생겼다고 바로 포지션을 뒤집지 않는다. 기존 재고와 pending 주문을 정리한 뒤 cooldown을 거쳐 반대 방향 신규 진입을 평가한다.

## 16. 리플레이와 백테스트

### 16.1 검증 순서를 분리한다

1. **flow:** 앞으로의 B/S 물량, 지속시간을 제대로 추정하는가.
2. **liquidity:** 어느 가격이 언제 소진되고 언제 흡수가 생기는지 설명하는가.
3. **price:** `P_struct`, `P_reach`의 과대·과소 예측과 도달시간이 보정되는가.
4. **execution:** 내 주문의 체결 시각, 전량 청산률, cancel 중 fill이 재현되는가.
5. **economics:** 수수료·funding·실패 청산·지연 포함 순손익과 tail risk가 적격인가.

가격 예측만 실패한 것인지, 가격은 맞지만 maker 체결이 독성인지, 청산 queue가 과소평가됐는지 구분한다. 한 단계가 실패했는데 다른 단계의 복잡도를 올려 손익을 맞추지 않는다.

### 16.2 자료 진단

실제 데이터가 없으므로 이 문서에는 검정 결과를 쓰지 않는다. 모델 보정 전 다음 검사를 시행한다.

| 진단 | 대상 | 활용 |
|---|---|---|
| ADF + KPSS | 유량, 정규화 소비/공급, 모델 잔차, 수익률 | 국소 안정성·구조 변화 확인 |
| ACF/PACF + Ljung-Box | trade sign, 유량·공급 잔차, strategy returns | 의존구조·block 길이 결정 |
| QQ + Anderson-Darling, 보조 Shapiro | 유량 오차·markout·손익 | 비정규성·꼬리·비대칭 확인 |
| 구간별 분위수·생존 곡선 | symbol/venue/age/방향 | 한 상태의 추정치를 모든 시장에 강제하지 않음 |

원시 가격의 비정상성이 발견됐다는 이유만으로 단기 flow 모델을 폐기하지 않는다. 반대로 ADF 통과를 인과성 증거로 사용하지 않는다. 큰 표본에서는 사소한 비정규성도 유의할 수 있으므로 p-value보다 분포와 경제적 오차를 함께 본다. 기본은 비정규·의존 자료이며 block bootstrap과 경험 분위수를 사용한다.

### 16.3 L2 queue fill 가정

보수적 FIFO 리플레이:

- 주문 활성화 시 동일 가격의 보이는 잔량을 전부 내 앞 queue로 둔다.
- 앞 queue의 취소 여부를 모르면 queue 이득을 주지 않는다.
- 실제 반대 aggressor trade가 내 가격에 도착해 앞 물량을 소진한 뒤 남은 수량만 내 fill로 할당한다.
- 단순 quote touch, best 변화, mid 통과만으로 fill을 주지 않는다.
- 숨은/iceberg 유동성 가능성은 앞 queue 추가량과 sensitivity로 반영한다.
- 여러 내 주문이 같은 trade 물량을 중복 사용하지 않도록 exchange의 가격·시간 우선순위로 할당한다.
- exit는 실제 제출·활성화 이후부터 fill 계산한다.
- 부분 체결 및 cancel 요청 후 실제 취소 전 fill을 포함한다.

FIFO가 아닌 matching 방식의 venue에 FIFO 결과를 전용하지 않는다. L2만으로 queue가 정확히 식별된다고 주장하지 않는다. 앞 취소 0%인 보수 모델, 일부 취소를 앞에 배분하는 중간 모델, 실주문 로그의 세 결과를 함께 비교한다.

내 주문이 시장에 없었던 historical path를 재생하면 내 유동성 추가가 가격과 체결에 주는 피드백이 생략된다. 이 때문에 초기 참여율을 낮추고 실제 주문으로 확인한다. 단순 과거 경로에 큰 q를 얹어 capacity를 선형 확대하지 않는다.

### 16.4 수신시간·지연·비용

- source와 receive 시계를 분리하고, 당시 도착하지 않은 데이터를 사용하지 않음.
- 신규·취소·체결 알림·조회 지연을 각각 측정 분포로 재생.
- 시장 급변과 latency가 함께 커질 수 있으므로 지연을 시간 구간과 무관하게 무작위 섞지 않음.
- post-only rejection, API limit, websocket gap, order timeout 반영.
- maker/taker 실제 요율, funding settlement, lot 반올림, contract multiplier 반영.
- 테스트 끝의 열린 포지션은 낙관적 mid가 아니라 실행 가능한 liquidation 가정으로 평가.
- 계정 equity에는 실현·미실현·비용·funding을 모두 포함.

### 16.5 학습·검증 분리

초기 시간 순서:

- train 14일: flow survival·supply·오차 분포 보정.
- calibration 7일: 소수의 사전 고정 gate와 후보 가격 정책 선택.
- test 7일: 정책·모수를 동결한 out-of-sample 평가.
- 7일씩 이동하는 walk-forward, 최소 4개 test 구간을 먼저 확보.

첫 4개 구간을 얻는 데 기본적으로 49일의 연속 자료가 필요하다. 거래가 너무 적으면 기간을 늘리며 일수를 충족했다는 이유로 표본이 충분하다고 하지 않는다. 여러 주문이 같은 flow episode를 공유하면 하나의 cluster로 다룬다.

각 경계에서 label horizon이 겹치는 표본을 purge한다. 최소 격리시간은 `entry 대기 + 최대 보유 + 취소·청산·알림 최종확정 지연` 이상으로 정한다. 기본 180초를 출발점으로 사용하되 스트레스 지연을 넣어 더 길어지면 반드시 늘린다. 라벨이 아직 확정되지 않은 주문을 학습에 사용하지 않는다.

오늘의 symbol 목록을 과거 모든 날에 적용하지 않는다. 당시 상장·폐지·데이터 수신 가능 종목을 유지하고, 성과가 나쁜 폐지 종목도 포함한다. universe 선택 기준은 test 전에 고정한다.

### 16.6 통계 추론과 다중 탐색

- 독립 trade bootstrap 대신 episode/day block bootstrap.
- 여러 종목을 합산할 때 같은 시간대의 공동 충격을 보존.
- flow 미시 block 길이는 ACF·잔차 의존으로 정하고 민감도를 확인.
- 일별 순손익의 confidence interval은 일자 또는 여러 날 block으로 산출.
- 30/60/120초 horizon, 가격 후보, gate 변경 횟수를 실험 장부에 기록.
- 수십 개 조합 중 최고 Sharpe 한 개만 보고하지 않음.
- 최종 정책 선택에 사용하지 않은 마지막 holdout과 신규 venue/symbol에서 다시 확인.

Bayesian prior는 추정 안정성을 위한 것이며 데이터 독립성·모형 정확성을 만들어 주지 않는다. pooling을 하더라도 거래소, tick 크기, matching 방식이 다른 모수의 동일성을 강제하지 않는다.

### 16.7 필수 ablation

| 비교 | 확인할 질문 |
|---|---|
| 단순 고정 spread MM | 복잡한 가격기가 실제로 가치를 더하는가 |
| OBI 또는 microprice 기준의 단순 모델 | 제안 메커니즘이 단순 baseline을 넘는가 |
| flow만 사용 | 유효 공급 추정이 필수인가 |
| flow + static depth | refill과 cancellation 고려가 개선하는가 |
| flow + effective depth | bid 흡수 필터가 entry 역선택을 줄이는가 |
| exit queue 무시/반영 비교 | 목표가 접촉과 실제 실현 edge의 차이는 얼마인가 |
| 0초/5초/10초/20초 지연 | 0초에서만 성립하는 알파인가 |
| same total size의 1레벨/2레벨 | ladder가 용량을 늘리는가, 나쁜 fill만 늘리는가 |

baseline은 제안된 core를 바꾸기 위한 것이 아니라 불필요한 복잡도를 판별하기 위한 대조군이다.

## 17. 반드시 보고할 결과

### 17.1 가격·체결 지표

- 미래 B/S 물량 예측 bias와 분위수 coverage.
- flow 잔여 수명 calibration.
- ask/bid 소진시간 오차, absorption 오분류율.
- 최초 목표 가격 접촉률.
- entry fill 뒤 최초 목표에 대한 정상 **전량** maker exit 비율.
- 부분 exit 비율, taker exit 비율, timeout 비율.
- quote당 entry fill률, fill까지 시간, 재고 청산까지 시간.
- `MO_5s, MO_15s, MO_30s, MO_60s, MO_120s`.
- 미체결 후보 전체와 체결 후보의 이후 가격분포 차이.

매수 fill의 markout은 `(m_{fill+h}-p_fill)`, 매도는 부호를 뒤집는다. mid markout은 체결 품질 지표이지 실현수익이 아니다. 목표를 나중에 수정해 원래 목표의 실패를 성공으로 바꾸지 않는다.

### 17.2 경제적 지표

| 지표 | 정의/주의 |
|---|---|
| 순손익 | 실현·미실현·fees·funding·실패 청산 포함 |
| 승률/평균 이익/평균 손실 | 동시에 보고; episode와 왕복 거래 단위 병기 |
| Profit factor | 모든 실패 청산 포함 |
| 일별 Sharpe | 일별 순 equity return, 24/7 기준 연환산 sqrt(365), CI 병기 |
| MaxDD | 시간순 equity 고점 대비 저점; open inventory 포함 |
| VaR 99%, ES 97.5% | 1분·5분·일 단위 경험/블록 분포; 표본 수 명시 |
| Gross/net exposure | 모든 종목·거래소 및 pending worst-case 포함 |
| Turnover | `(abs buy notional + abs sell notional)/평균 equity` |
| Maker 비율·volume | 전체 정상·실패 경로와 함께 |
| capacity | q를 늘렸을 때 앞 queue·impact·청산시간 변화 |

Sharpe는 수천 개의 체결을 독립 거래로 연환산하지 않는다. 너무 짧은 데이터에서 일별 VaR 99%를 안정적으로 추정할 수 없다. 그런 경우 계산값과 추정 한계를 표시하고 별도 jump·depth stress를 병기한다.

진입 edge 분해를 보고할 때:

$$
s(p_{exit}-p_{entry})
=s(m_{entry}-p_{entry})
+s(m_{exit}-m_{entry})
+s(p_{exit}-m_{exit}),
$$

`s=+1`은 롱, `s=-1`은 숏이다. entry 할인, 보유 중 이동, exit 가격 개선을 나누되 mid의 선택 시각을 고정하고 실제 비용을 별도로 차감한다.

현재는 데이터 미제공이므로 위 지표 모두 **미측정**이다. 문서의 80% 성공률·2bp 기대값은 가상 예시일 뿐 성과 추정치가 아니다.

## 18. 단계별 승인 기준

| 단계 | 해야 할 일 | 다음 단계 조건 |
|---|---|---|
| 0. 수집 | 정상 market/private 이벤트 저장, 지연·단위 검증 | book 재구성과 계정 대사가 신뢰 가능 |
| 1. shadow | 실제 주문 없이 flow·가격·가상 queue 평가 | flow·supply·목표가 calibration 가능 |
| 2. replay | 실제 latency와 보수 queue, walk-forward | 비용 후 기대손익 하한 양수, tail 한도 통과 |
| 3. 소액 live 검증 | 1~2개 종목, 1단위, 초소형 참여율 | 실제 fill/취소/청산이 replay 범위 안 |
| 4. 제한 확대 | 3단위·종목 확대 | 순손익·재고·queue 오차 안정 |
| 5. 수수료 확대 실험 | 3bp 시나리오와 capacity 검증 | 비용 상회하는 별도 적격 subset |

2단계의 최소 자료량은 방향별 유효 episode 200개와 실제/보수 replay roundtrip 300개를 연구 출발 기준으로 둔다. 이것은 신뢰구간을 자동 보장하는 표본 수가 아니다. 의존성이 강하거나 tail loss가 큰 경우 더 많은 기간이 필요하다. 실제 gate는 순손익 하한과 out-of-sample 안정성이다.

다음 결과면 가격기나 execution을 재설계한다.

- 예측 표본에서는 상승하지만 maker fill 표본의 markout·순손익은 음수.
- 가격 접촉률은 높은데 목표 ask 전량 청산률이 낮음.
- 기대이익 대부분이 포인트·리베이트 또는 특정 한 종목의 급등에 의존.
- cancel latency를 넣으면 수익성이 소멸.
- out-of-sample 실패를 더 많은 feature와 예외로 반복해서 덮어야 함.

손익이 Fee 0에서만 성립하면 그것도 해당 비용 환경에 한정된 결과다. 다음 세 수수료 시나리오를 구분한다.

1. maker 0 + 실제 taker 요율.
2. maker/taker 모두 0인 이상적 기준.
3. 3bp 편도면 정상 maker 왕복 6bp, 3bp 왕복이면 총 3bp. 두 의미를 명시적으로 나눠 평가.

Fee 0 수익성이 3bp 수익성을 자동으로 시사하지 않는다. points는 현금성 거래손익과 분리하고 알파 승인 gate에 넣지 않는다.

## 19. 종목 회전과 capacity

raw `abs(FV-mid)`가 큰 순서로 고르지 않는다. 넓은 spread·큰 tick·빈 book은 큰 가격차를 만들지만 청산이 어려울 수 있다.

후보 우선순위는 다음 개념을 사용한다.

$$
Score_j=\frac{P(fill_j)\,LB(E[\Pi_{net,j}\mid fill])}
{N_{reserved,j}\,\max(E[T_{cycle,j}],10\text{s})}.
$$

분자는 주문 1건의 보수적 기대손익, 분모는 예약자금과 점유시간이다. 표본이 적어 하한이 음수이면 제외한다. 수량 선택과 portfolio 제약을 통과한 후보만 비교한다.

기존 재고는 새 종목의 score가 높다는 이유만으로 억지 청산하지 않는다. 재고는 기존 가설·위험·deadline 규칙으로 관리하고 새로 사용할 수 있는 capacity만 재배치한다.

volume 확대 순서:

1. 동일 규칙이 유효한 종목·venue를 늘림.
2. 단위 수량을 실제 청산 capacity 안에서 점진적으로 늘림.
3. 필요할 때 같은 총수량을 2개 가격으로 분할.
4. 그 다음에만 제한적인 양방향 quote 모드 연구.

일반화 대상은 **메커니즘과 상태 전이**다. 유량 크기·잔여수명·tick·공급 회복속도·latency까지 같은 숫자로 고정하는 것이 아니다.

## 20. 구현 구조

초기에는 단일 프로세스의 event-driven 모듈 구조로 충분하다. latency 5초라는 이유로 서비스 간 통신과 동기 DB I/O를 hot path에 추가하지 않는다. 확장 시 같은 스키마와 이벤트로 서비스 분리가 가능하도록 만든다.

| 모듈 | 책임 |
|---|---|
| VenueAdapter | 시장·주문·계정 메시지, matching·단위·API capability |
| BookReconstructor | snapshot+delta, sequence, 가격 레벨과 자체 주문 제외 |
| FlowEstimator | 양방향 유량, episode age, survival, Q_rem |
| SupplyEstimator | 순공급·흡수·front 상태, 소진시간 |
| PricingEngine | P_struct, P_reach, executable target, 오차 여유 |
| RiskGate | 수량, pending worst case, portfolio·margin·손실 한도 |
| OrderManager | idempotent 의도, submit/cancel/partial fill/reconcile |
| EventJournal | 원본 이벤트와 구조화된 의사결정·모델 버전 |
| ReplayAdapter | 동일한 전략 엔진에 과거 이벤트·가상 주문 피드 제공 |

live와 replay는 같은 전략 상태전이와 pricing/risk 코드를 사용한다. 바뀌는 부분은 시계·데이터·주문 실행 어댑터다. 실시간 매매 경로에 LLM 호출은 넣지 않는다.

### 20.1 상태 스키마

| 객체 | 필수 필드 |
|---|---|
| `InstrumentState` | venue, symbol, tick_size, lot_size, multiplier, fee_version, matching_type |
| `BookState` | source_ts, receive_ts, sequence, bids, asks, depth_boundary, is_valid |
| `FlowState` | r_buy_10/30/120, r_sell_10/30/120, episode_id, side, age, survival_version |
| `SupplyState` | per_price_net_supply, front_exposure_time, rho_bid/ask, depth_uncertainty |
| `Forecast` | info_cutoff, horizon_deadline, Q_buy/sell bands, P_struct/reach/exit, rejection_reason |
| `OrderIntent` | client_id, decision_id, role, side, price_ticks, qty_lots, post_only, reduce_only, expiry |
| `OrderLedger` | exchange_id, intent_id, status, original_qty, filled_qty, leaves_qty, send/ack/cancel times |
| `Inventory` | net_qty, average_price, oldest_fill_time, lots, exit_reservations, worst_long/short |
| `RiskState` | equity, daily_start_equity, high_watermark, gross/net, group_exposure, margin_headroom, halt_reason |
| `DecisionRecord` | model/config version, inputs digest, forecasts, approval/rejection, predicted edge, all timestamps |

자체 미체결 주문을 public depth에서 구분해 supply 추정에 자기 호가를 외부 유동성처럼 넣지 않는다. 내 정상 passive fill의 상대방 공격 체결은 실제 거래지만, 참여율이 커지면 시장에 대한 내 영향이 커지므로 별도 표시한다.

### 20.2 처리 순서

모든 이벤트에 대해:

1. event ID로 중복 제거, sequence·timestamp·단위 검증.
2. public book 또는 private order ledger 갱신.
3. fill이면 실제 재고·pending 예약·원가·가장 오래된 lot deadline 갱신.
4. 즉시 위험 gate 평가. 위험 취소를 신호 평가 주기보다 우선 처리.
5. 정상 이벤트는 유량·공급 누적값에 반영.
6. 예정된 판단 시점 또는 fill 시점이면 잔여 유량·소진시간·실행가격 재계산.
7. 현재 주문을 유지/취소/축소/새로 제출할지 결정.
8. 승인 후에도 송신 직전 state version이 바뀌었으면 재검증.
9. 의사결정과 모델 버전을 journal에 기록.

재시작할 때 로컬 journal만 믿고 재주문하지 않는다. open orders, 최근 fills, positions를 거래소에서 대사한 뒤 WATCH로 진입한다.

### 20.3 성능·캐시

- 1초/5초 ring buffer로 flow 집계. 매 tick 전체 DataFrame 재계산 금지.
- book prefix depth·front 통계를 증분 갱신.
- survival/supply 모수는 시간순으로 완료된 데이터만으로 매일 보정해 versioned snapshot으로 교체.
- fee/tick/contract 정보는 버전 캐시. instrument update·계정 fee 변경 시 즉시 invalidation.
- source gap·reconnect 시 book와 관련 forecast cache 전부 무효화.
- private fill·cancel·rejection 시 주문·재고 관련 forecast 무효화.
- 명령은 idempotent ID, 종목별 mutation 직렬화, 공통 risk 예약은 원자적 갱신.

프로파일링에는 시장수신→상태반영, flow/supply 계산, risk, submit queue, API RTT, private fill 지연을 각각 넣는다. local compute p99 20ms 이하는 시작 목표이며 측정값이 아니다. 초과하면 호가를 낡은 상태로 계속 계산하는 대신 처리량·종목 수를 제한한다.

### 20.4 venue capability 표

| capability | 필요한 이유 | 확인 안 됐을 때 |
|---|---|---|
| post-only의 maker 보장/거절 의미 | entry taker 전환 방지 | live 비활성 |
| reduce-only의 포지션·주문 합계 규칙 | 과잉 청산·반대 포지션 방지 | 보수 직렬화 검증 또는 live 비활성 |
| FIFO/pro-rata 등 matching | queue 모델 | 해당 어댑터 연구 중단 |
| snapshot/delta·depth boundary | book 재구성 | 순공급 단순화·confidence 축소 |
| private fill/order sequence | 확정 재고·지연 | 대사 완료 전 신규 중단 |
| native expiry/cancel-on-disconnect | 연결 장애 노출 제한 | local watchdog의 한계와 별도 보호 설계 |
| 주문 수정의 priority 효과 | 재호가 가치 | priority 상실로 보수 처리 |
| fee/funding/mark/liquidation | 실제 경제성 | 확인 전 자금 투입 금지 |

이번 조회에서 GRVT 공식 문서의 post-only/reduce-only, book snapshot/delta, taker buyer 표시는 확인했다. Arcus의 공식 API capability·현재 요율은 이 작업에서 확인하지 못했으므로 GRVT와 같다고 가정하지 않는다. GRVT에 cancel-on-disconnect endpoint가 존재하지만 동작 범위·최소 countdown·환경별 지원은 어댑터 검증 사항이다. 이 문서는 특정 계정에서 주문을 실행하거나 설정을 변경하지 않는다.

## 21. v1에서 구현할 범위

핵심 구현은 다음으로 고정한다.

- 유량의 지속성/종료와 양방향 유효 공급으로 가격을 계산.
- 60초 기본 horizon, 5초 이상 latency 반영.
- one-sided maker entry, 정상 maker exit, 위험 시 taker 감축.
- 종목당 1개 entry 가격, 최대 3단위 inventory.
- fill 이후 재가격 산출 및 exit 서비스량 재검증.
- pending 주문 전량체결을 고려한 수량 제한.
- 데이터·계정 상태 오류 및 손실 한도 kill-switch.
- 실제 이벤트 replay, temporal split, 체결 조건부 economics 검증.

후속 확장은 별도 성과가 확인될 때만 켠다: 두 번째 entry 가격, 중립 상태 양방향 MM, cross-venue 가격 anchor 강화, RWA overlay, 온라인 stochastic queue simulator, 3bp 비용 subset. 이를 v1의 실패를 가리는 필수 기능으로 만들지 않는다.

전략을 반증하는 가장 직접적인 결과는 **정상적인 수급 예측에도 내 maker fill 이후의 순손익이 일관되게 음수인 경우**다. 이때는 재고관리 최적화보다 entry 상태·가격·latency 적격성을 먼저 수정한다.

## 22. 근거와 적용 범위

아래는 메커니즘과 API 의미를 확인한 1차 자료다. 이 문서의 threshold·horizon·sizing·승률 목표·생존 prior는 별도로 제안한 연구 설계이며 아래 논문에서 입증한 매매 파라미터가 아니다.

1. Lillo, Mike, Farmer, *A theory for long-memory in supply and demand*. 주문 분할이 주문 부호의 장기 의존성을 만들 수 있다는 메커니즘. 공개 체결로 실제 모주문 잔량을 식별한다는 증거는 아님. <https://arxiv.org/abs/cond-mat/0412708>
2. Farmer, Gerig, Lillo, Mike, *Market efficiency and the long-memory of supply and demand*. 지속적 주문흐름에 유동성이 적응하면 단순한 방향 지속성이 가격 알파로 남지 않을 수 있다는 근거. <https://arxiv.org/abs/physics/0602015>
3. Cont, Kukanov, Stoikov, *The Price Impact of Order Book Events*. 최우선 호가의 주문흐름과 depth가 단기 가격충격에 연결됨을 분석. 순수 체결량 하나보다 공급·수요 이벤트를 함께 볼 이유. <https://arxiv.org/abs/1011.6402>
4. Huang, Lehalle, Rosenbaum, *Simulating and analyzing order book data: The queue-reactive model*. book 상태에 따른 이벤트 강도를 이용한 큐 모형. 여기의 60초 잔여 flow 엔진 전체를 직접 증명하는 논문은 아님. <https://arxiv.org/abs/1312.0563>
5. Lehalle, Mounjid, *Limit Order Strategic Placement with Adverse Selection Risk and the Role of Latency*. limit fill의 역선택과 주문 교체 지연이 정보 우위를 훼손할 수 있음을 다룸. <https://arxiv.org/abs/1610.00261>
6. GRVT, *Api create order request*. post-only/reduce-only 및 주문 의미. <https://api-docs.grvt.io/schemas/api_create_order_request/>
7. GRVT, *Market Data Websocket*. snapshot/delta, depth boundary, trade aggressor 필드. <https://api-docs.grvt.io/market_data_streams/>
8. GRVT, *Trading API*. 주문·취소·cancel-on-disconnect 등 기능의 공식 정의. <https://api-docs.grvt.io/trading_api/>

문헌의 관측 시장·시간척도와 현재 대상 venue가 다르므로, 메커니즘에서 실전 수익성으로의 확장은 반드시 위의 검증 절차를 거친다.