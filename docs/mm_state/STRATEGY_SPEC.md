# 상태 기반 MM 명세 — 2026-10-07

수정 대상은 첨부 피드백의 상태 기반 후보와 기존 주문 처리다. 예측력이나 수익성이 입증된 전략이라는 뜻은 아니다. 기존 사용자의 파일 구조 요구를 유지하여 **각 전략은 `strategy/`의 Python 파일 하나**로 운용한다. 공통 계산과 주문 정책은 `vibetrading/mm_state/`를 공유하며, 통합 운용과 ablation은 `Coordinator`를 사용한다.

## 전략과 출력

| 전략 파일 | 역할 | 진입 조건과 제한 |
|---|---|---|
| `strategy/s1_net_liquidity.py` | 가격 변화까지 반영한 OFI로 방향·onset·지속·가속을 관측 | 학습 구간의 절대 OFI q90으로 정규화. 최소 강도 아래는 중립. 체결 후 maker 청산 |
| `strategy/s4_obi.py` | L1 OBI를 주 방향 상태로 사용 | 학습 절대 OBI q90 기준. L2 top10 OBI는 보조 진단이며 자동 가중하지 않음 |
| `strategy/s3_aggressive_flow.py` | 압력→흡수→매수/매도 공격자 또는 공급 소진 | 실제 공격 방향과 episode 이후 체결량·동일 가격 호가 순변화가 필요. 순 체결 부호만으로 진입하지 않음 |
| `strategy/s2_external_anchor.py` | Binance와 로컬 가격의 basis residual 및 발생 원인 | reference freshness·source skew·학습 stationarity·baseline drift·비용 gate를 모두 확인 |

기존 수량 clock/FV 회귀식은 `vibetrading/fv_mm/legacy/`에 보존했다. 기본 모드는 `state`; `--legacy-fv`는 이전 **시그널 공식**을 사용한다. 이전 체결 모델까지 재현하려면 보관된 `legacy/paper_simulation.py`가 필요하다. 기존 보고서와 원시 데이터는 수정하지 않는다.

새 `Signal`은 `direction ∈ {-1,0,+1}`, `strength ∈ [0,1]`, `state`, `ready`, `metadata`를 반환하며 `fv=None`이다. 단일 정확한 가격 추정치를 새로 만들어 내지 않는다. `magnitude_state`는 관측된 RV로 분류한 FLAT/SMALL/ACTIVE이며 **미래 가격 이동 규모의 학습 모델은 아직 아니다**. 별도의 Jump Risk gate가 주문을 제한한다.

## 공통 feature

receipt-time 기반 100ms bucket을 최대 W≤60초 유지한다. 기본 W=5초는 실행 예시이며 본질적인 최적 시간축을 주장하지 않는다. OFI는 가격이 바뀌는 경우까지 포함하는 best bid/ask event 식으로 계산하고 `asinh(ΣOFI / 시간가중 평균 L1 depth)`로 정규화한다. L1 OBI는 `(bid_qty-ask_qty)/(bid_qty+ask_qty)`다.

R은 `∫(bid*bid_qty + ask*ask_qty)dt / Σ(trade_price*trade_qty)`이며 단위는 **초**다. 동일 호가 이벤트를 중복 합산하지 않는다. 거래가 없으면 R=None이고, 고유동성·저변동 상태로 취급하지 않는다. `trade_rate`는 체결 **수량/초**, `book_rate`는 호가 메시지/초다. RV는 창 내 native mid 변화의 제곱합 제곱근(bp)이므로 기존 1초 표본 RV와 수치가 동일하지 않다.

L2 refill/depletion은 연속 두 메시지에 **공통으로 있는 가격**의 수량 순증감이다. 새 가격 편입/기존 가격 이탈을 gross 공급/취소로 부르지 않는다. Public L2로 새 주문·취소·체결을 완전히 분해할 수 없다. 가장 오래된 부분 bucket을 제외하여 최대 100ms의 경계 자료를 누락할 수 있다. gap·clock 역행·3초 이상 stale 호가는 window를 초기화하고 재워밍한다.

## S2 세부

`basis_bp=1e4*log(local_mid/binance_mid)`, `residual=basis-과거 rolling median`. baseline은 1Hz 표본 최대 1,800개이고 중앙값을 최대 초당 한 번 갱신한다. 120개 전에는 frozen training median을 사용한다. 현재 관측을 baseline에 넣기 **전** residual을 계산한다.

최근 1초의 로컬·Binance 이동을 비교해 BINANCE_LEADS, LOCAL_IDIOSYNCRATIC, COMMON_SHOCK, STALE_OR_UNKNOWN을 구분한다. 기본 impulse 1bp, 로컬 이동이 reference 이동의 절반 미만이면 Binance lead다. 이는 원인에 대한 **휴리스틱 분류**이며 인과성이 입증된 것은 아니다. 동일 계약의 USD/USDT·venue basis와 funding 차이는 남아 있다.

진입은 abs(residual)>max(train q90, maker 왕복 비용+1bp uncertainty), origin 방향 정합, ADF p<.05/KPSS p>.05/200개 이상 학습 표본일 때 허용한다. 시계열 검정은 reference 유효·ready 표본의 가장 긴 연속 학습 block(표본 간격≤2.5초)을 사용한다. 불연속 block을 연결하지 않는다. reference age≤1.5초, source timestamp 차이≤500ms; baseline drift>max(5bp,6*MAD)이면 신규 진입 중단.

TP1은 residual 수축과 **로컬 포지션 PnL**이 비용을 각각 충족할 때 최초 체결 수량의 절반을 maker로 청산한다. 절반 이하 잔량에서 TP1을 반복하지 않는다. TP2는 residual≤0.5bp 및 같은 이익 조건을 사용한다. 보유 시간·손실·운영 위험은 profit gate를 우회하여 maker 감축한다. spread convergence 자체는 미헤지 로컬 수익과 같지 않다.

## S3 세부

알려진 체결 방향, |TI|≥학습 q90(최소 .3), 창의 체결량≥현재 L1 평균 depth에서 pressure episode를 연다. **episode 시작 이후** 공격 체결량과 소비되는 쪽의 순 refill/depletion을 누적한다. 초기 소비 측 depth 이상 체결되고 refill/flow≥.5, 가격 진행<1tick이면 absorption이다.

흡수 이후 refill/flow<.3·같은 방향 TI 유지·1tick 이상 가격 진행·가시적 소진이 함께 있으면 공급 소진으로 같은 방향을 선택한다. 체결 압력이 episode 최고치의 절반 미만으로 약해지고 refill이 있으나 가격 진행이 없으면 공격자 소진으로 반대 방향을 선택한다. 매도 episode는 대칭이다. episode 최대 30초, unknown 공격 방향이면 중립/초기화한다. 완전한 gross 공급과 hidden liquidity를 알 수 없으므로 전이 판정은 proxy다.

## 주문 정책과 통합

정상 half-width는 현재 half-spread와 half-tick 중 큰 값에서 시작한다. 방향 score로 최대 1tick center skew, inventory 정규화로 최대 2tick 반대 skew를 준다. THIN_BOOK에서 폭×2·크기×.5; JUMP_RISK는 신규 노출을 차단한다. R은 방향을 만들지 않는다. 중립은 단독 방향 전략의 신규 주문을 만들지 않는다.

재고가 있으면 감축 주문을 우선한다. long은 maker ask, short는 maker bid이며 `reduce_only=True`. normal entry와 normal exit 모두 maker다. `cost`는 ledger/S2 경제성 gate에 쓰며, 모든 타이트한 spread에 비용만큼 바깥 주문을 강제하여 0체결을 숨기는 방식은 사용하지 않는다. S1·S3·S4의 quote 자체가 수수료를 넘는 edge라는 보장은 없다.

통합 가중치는 S4 .45/S1 .30/S3 .15/S2 .10, 선택 모듈의 가중치 합으로 정규화하며 |score|<.2는 중립이다. 이는 최적화한 계수가 아니라 동결한 연구 기준값이다. `static → obi → book → flow → cross → volatility → inventory` 7개 ablation을 **동일 Bybit BTC**에서 비교한다. 단독 16개 경로의 서로 다른 종목 성과를 전략 순위로 해석하지 않는다.
