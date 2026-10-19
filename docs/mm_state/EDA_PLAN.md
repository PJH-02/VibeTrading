# 새 검증 계획과 이번 구현 검증의 경계

이번 역사 재생은 이전에 이미 본 2026-10-04 training10분/evaluation30분 자료다. 새 전략의 구현·실행 가능성 확인이며 독립적인 confirmatory test가 아니다. 이미 탐색한 데이터에서 최고 성과를 골라 운영하지 않는다.

## 데이터 기준

native L2·public aggressor trade·Binance same-asset quote가 모두 있어야 해당 분석을 수행한다. gap·crossed quote·source clock skew·stale·duplicate를 별도 집계한다. 없는 공격 방향을 mid/tick rule로 임의 보완하지 않는다. 결측 block을 넘어 future price·episode·queue를 연결하지 않는다. public L2의 순변화를 gross order flow로 바꾸지 않는다.

State engine 입력 창1/5/10/30/60초 및 미래100/250/500ms,1/2/5/10/30초를 사전 동결한 작은 실험군으로 평가한다. sub-second 값은 native 이벤트가 있는 경로에서만 관측한다. 이전 초 단위 보고서의 0~180초 가격 반응은 기존 evidence에 보존되어 있으며 새 runtime이 그 전 결과를 교체하지 않는다.

## 모듈별 질문

| 모듈 | 관측할 것 | 반증·제한 |
|---|---|---|
| OBI/OFI | 독립 onset 이후 부호별 mid 경로, maker fill-conditioned markout, 강도별 단조성 | 같은 방향의 지속 표본·overlap을 독립 사례로 세지 않음. 방향 반응과 수수료 후 수익을 분리 |
| S3 | pressure→absorption→소진 episode별 진행·refill·공격 압력 약화·maker 체결 | episode가 적으면 검정 불가. hidden liquidity/gross supply의 증거로 과장 금지 |
| S2 | basis Q-Q/꼬리/MAD, ADF+KPSS, ACF/PACF/Ljung-Box, half-life; origin별 local/ref 움직임·exit 성공률 | 기준가격 쪽만 돌아와도 residual은 줄 수 있다. unhedged local PnL 별도 필요 |
| R/RV | high/low R의 조건부 future RV, past RV/trade intensity 통제 후 추가 가치 | high R이 band center로 복귀한다는 가설은 별도다. grid 운영으로 자동 전환하지 않음 |
| Inventory | long/short별 불리한 체결, 체류 시간, 감축 완료율 | spread 확대만으로 재고가 줄었다고 부르지 않음 |

S2 통계는 현재 offline calibration에 구현됐다. 관측 시계열의 가장 긴 연속 학습 block만 검정한다. half-life는 **표본 수** 단위이며 불규칙 1Hz 표본의 정확한 초 환산이 아니다. Q-Q 그림과 origin별 충분한 episode를 가진 새로운 장기 표본 검증은 후속 연구다. hot path에서 SciPy/statsmodels·전수 재회귀를 실행하지 않는다.

## 추론

Chronological train/validation/test와 label horizon purge·embargo를 사용한다. `purged_walk_forward`가 index 분할을 제공하고 미래 label이 training 끝을 넘지 않는 테스트가 있다. 이번 replay는 별도의10분 training→30분 evaluation **단일 분할**이며 multi-fold 검정을 끝냈다고 주장하지 않는다.

유효 sample size는 onset/episode/order 수준으로 산정한다. 경로별 충분한 independent block이 있으면 block bootstrap CI, 탐색 다중 검정의 FDR, train-only 강도 bin의 test 반응을 함께 보고한다. 이 짧은 구현 replay의 fill 이벤트 수로 t-test·Sharpe 우월성 검정을 만들지 않는다. 특히 0fill은 alpha 부재의 검정이 아니다.

신규 data에서 미리 동결할 것: fee schedule, latency 측정 근거, window/horizon, onset definition, episode reset/censor 규칙, inventory/TTL/quote tick, maximum adverse markout, train/validation/test 날짜. validation 수정 후 최종 test를 한 번만 사용한다.
