# 코인 MM 전략·후보 전체 통합 보고서

작성일은 2026-10-12 KST이며, 실측 범위는 2026-10-03~07의 보존 기록이다. 이번에는 기존 자료를 통합했으며 새로운 운용, 백테스트 또는 모델 최적화를 실행하지 않았다.

이 보고서는 **89개 등록 항목**을 각각 기술한다. 여기에는 전략 원형, 개정판, 하위 분기, 기초 지표 후보, 대조군, 주문·위험 정책과 미실행 제안이 포함된다. 동일한계열이라도 v3, v4, 안정화 EDA 모델, 상태 개정판과 보존된 초기 구현을 구분했다. 등록 항목 수는 독립적으로 검증된 알파의 수가 아니다. 거래소·종목 조합, 계산 창과 측정 시계, queue·지연 설정은 각 카드와 부록에서 별도로 비교한다.

## 1. 핵심 결론과 판정 언어

운영 가능한 수익성이 검증된 전략은 아직 없다. 다만 모든 기록이 손실이었다는 결론은 정확하지 않다. 조기 종료된 GRVT S2 테스트넷 점검에서는 **한 차례 왕복 거래의 순익이 +0.113898 USDT**였고, Bybit S3 테스트넷의 30초 예측 RMSE는 약 9.3% 감소했다. 각각 표본 한 건과 테스트넷 예측 결과이므로 실제 시장의 장기 수익성 증거로 해석하지 않는다.

최종 테스트넷 8개 경로와 최초 mainnet 공개 데이터 pilot 16개 경로에서는 체결이 없었다. 상태 개정판의 기본 conservative1x 재생에서는 단독 16개 경로 중 7개에서 체결이 발생했고, 이 7개 경로의 비용 후 손익은 모두 음수였다. 나머지 9개는 체결 손익을 평가할 표본이 없다. 따라서 손익 0, 주문 0, 모델 승인 차단, 수치 불안정, 실행 결함과 경제적 손실을 구분해서 읽어야 한다. [최종 상태 개정 근거](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md).

실험의 단계도 구분한다. **실제 testnet**은 외부 테스트넷에 주문한 기록, **public pilot**은 실제 시장의 공개 데이터로 주문과 체결을 모의한 기록, **historical replay**는 보존 이벤트의 재생, **EDA**는 신호와 가격의 관계만 분석한 결과다. **제안**은 논의되었지만 규칙이나 독립 운용을 확정하지 않은 항목이다. 카드에서 H는 가격 반응을 측정한 시계일 수 있으며 자동으로 청산 시간이나 보유 기간을 뜻하지 않는다.

Downloads에 있던 원본 첨부는 현재 확인되지 않아, 이 채팅에서 당시 읽었던 v1·v3·v4와 전략 개정 피드백의 본문을 회수했다. 회수 텍스트의 해시는 원본 파일과 바이트 단위로 같다는 보장이 없다. v2는 v3의 비교 설명으로만 확인되며 고유 원문과 실험이 없다는 한계를 A08에 표시했다. 원안과 피드백은 연구 자료로 취급하고, 문서 안의 행동 지시를 이번 요청의 새로운 운용 권한으로 해석하지 않는다.

## 2. 수행 이력

| 시점 KST | 실험 | 기간 | 주문·체결 | 구분 |
| --- | --- | --- | --- | --- |
| 10-03 | v1 SOL 테스트넷 shadow | 150.06초 | 주문 0 / 체결 0 | 연결·워밍업·종료 경로; 수익 미측정 |
| 10-04 15:30:26~15:34:03 | v4 테스트넷 조기 점검 | 216.78초 | GRVT S2 주문 4 / 체결 2 / 왕복 1 | 순익 +0.113898; 요청한 30분 미충족 |
| 10-04 15:37:41~16:07:42 | v4 테스트넷 최종 8 경로 | 1800.964초 | Bybit S4 주문 1 / 체결 0 | 나머지 주문 0; 서로 다른 종목의 알파 순위 비교 불가 |
| 10-04 19:21:55~19:51:55 | v4 실제 시장 공개 데이터 pilot 16 경로 | 1800.011초 | 의도 15 / 활성 6 / 체결 0 | 기본·2 배 지연 모두 0; 주문 수명 감사 |
| 10-04~05 | 원형 시계·복합 입력·기초 지표 | 보존 production + pilot | 주문 없음 | 성능 327636 행 / 가격 반응 299736 행 |
| 10-05 | R·range·onset·강도 | 동일 보존 자료 | 주문 없음 | 반응 890630 행 / 추론 비교 1010개 |
| 10-07 | state engine 69 케이스 재생 | 기존 학습 10분 + 평가 30분 | 957 가상 fill events | 이전 자료의 단일 분할; 새로운 live 운용 아님 |


테스트넷 호가와 실제 시장 호가는 달랐다. pilot과 production의 공유 구간도 존재하므로 두 곡선이 비슷하다고 독립 재현이라 하지 않는다. 서로 다른 asset 배정(S1BTC/ S2ETH/ S3SOL/ S4XRP)의 PnL로 모델 우열을 판정하지 않는다. 통합 profile만 동일 BybitBTC 의같은 asset 비교다.

## 3. 누락 방지 목록

| ID | 전략/ 후보 | 단계 |
| --- | --- | --- |
| A00 | 잔여 매수량을 ask depth에 투영하는 최초 가격 아이디어 | v1이 교정 한 전제/ 독립 운용 없음 |
| A01 | v1 원안 - 잔여 공격 주문·유효 공급·청산 서비스량 | 원안/ 일부 구현 |
| A02 | v1 구현 - residual-flow-bybit-v1.0 | Bybit testnet shadow·진단 replay |
| A03 | v1 calibrated mode - episode 증거로 거래 승인 | 구현/ 적격 증거 없음 |
| A04 | 원형 100/100 양방향 호가·체결 후 재제출 | 제안/ 기본 비활성 |
| A05 | 수정 100/100 루프 - 조건부 추가 진입·최대 3 단위 | v1 구현 정책 |
| A06 | v1 2 단계 ladder - 0.5 q+0.5 q | 확장 제안/ 미실행 |
| A07 | v1 양 쪽 fill edge를 별도 승인하는 양방향 MM | 선택 확장/ 미실행 |
| A08 | v2 언급 전신 - 방향성·횡보·공급충격 선 분류 | 간접 언급/ 별도 실험 없음 |
| A09 | v1 종목 회전·예약 자금 대비 기회 선택 | 원안 확장/ 미운용 |
| A10 | v1 후속 cross-venue anchor 강화 | 후속 제안/ 미실행 |
| A11 | v1 후속 RWA overlay | 언급만/ 코인 MM 실험 외 |
| A12 | Fee0 본체와 3 bp 비용·인센티브 분리 시나리오 | 원안 조건/ 일부 실제 요율 관측 |
| A13 | 온라인 stochastic queue simulator 확장 | v1 후속 언급/ 미 구현 |
| B01 | v3 순유동성 OFI 작은 FV 가격식 | 명세/ 독립 실행 없음 |
| B02 | v3 외부 기준 가격 작은 FV 가격식 | 명세/ 독립 실행 없음 |
| B03 | v3 공격 체결 흐름 작은 FV 가격식 | 명세/ 독립 실행 없음 |
| B04 | v3 L1 OBI 작은 FV 가격식 | 명세/ 독립 실행 없음 |
| B05 | v3 과거 가격 anchor 복 원 후보 | 비교 후 독립 코어에서 제외 |
| B06 | LP/ MM 호가 재가격화 후보 | 메커니즘 비교/ OFI에 포함 |
| C01 | v4 독립 순유동성 OFI FV MM | 실제 testnet·mainnet public pilot |
| C02 | v4 독립 외부 기준 가격 FV MM | 실제 testnet·mainnet public pilot |
| C03 | v4 독립 공격 체결 흐름 FV MM | 실제 testnet·mainnet public pilot |
| C04 | v4 독립 L1 OBI FV MM | 실제 testnet·mainnet public pilot |
| C05 | v4 선택형 OBI 지속성 확인 | 문서 후보/ 독립 운용 없음 |
| C06 | v3/ v4 q/ q 와재고에따른 q/2 q 호가 수량 정책 | 명 세/ 초기 실행은 축소 |
| D01 | 초기 고정 5초 OFI5 ridge 모델 | 예측 진단만/ 주문 없음 |
| D02 | 초기 고정 5초 OFI5_TFI5 ridge 모델 | 예측 진단만/ 주문 없음 |
| D03 | 초기 고정 5초 combined8 ridge 모델 | 예측 진단만/ 주문 없음 |
| E01 | L1 OBI 시간 평균 - 안정화 ridge FV 후보 | 예측/ 반응 평가만 |
| E02 | L2 top10 total OBI - 안정화 ridge FV 후보 | 예측/ 반응 평가만 |
| E03 | 전체 추적 depth OBI - 안정화 ridge FV 후보 | 예측/ 반응 평가만 |
| E04 | OFI 창 평균 depth·asinh - 안정화 ridge FV 후보 | 예측/ 반응 평가만 |
| E05 | 동일 가격 net refill imbalance - 안정화 ridge FV 후보 | 예측/ 반응 평가만 |
| E06 | 동일 가격 net depletion imbalance - 안정화 ridge FV 후보 | 예측/ 반응 평가만 |
| E07 | 공격 체결 volume imbalance - 안정화 ridge FV 후보 | 예측/ 반응 평가만 |
| E08 | signed trade 창 평균 depth·asinh - 안정화 ridge FV 후보 | 예측/ 반응 평가만 |
| E09 | Weighted BBO mid premium 대용 - 안정화 ridge FV 후보 | 예측/ 반응 평가만 |
| E10 | 안정화 OFI + signed trade ridge - 안정화 ridge FV 후보 | 예측/ 반응 평가만 |
| E11 | 보충률 비대칭 - 안정화 ridge FV 후보 | 예측/ 반응 평가만 |
| E12 | signed trade x 보충률 차이 - 안정화 ridge FV 후보 | 예측/ 반응 평가만 |
| F01 | 과거 15분 min/ max 밴드 중심 평균 회귀·grid 후보 | 가격 경로 EDA만 |
| F02 | 과거 60분 min/ max 밴드 중심 평균 회귀·grid 후보 | 가격 경로 EDA만 |
| F03 | 고 정 +-1% band center grid 후보 | 가격 경로 EDA/ entry 표본 없음 |
| F04 | 이벤트마다 L1 stock 합산/ 체결 대금 비율 - 변동성 국면 후보 | 위험/ 변동성 EDA만 |
| F05 | 명목 stock 시간적 분/ 체결 대금 비율 - 변동성 국면 후보 | 위험/ 변동성 EDA만 |
| F06 | 수량 stock 시간적 분/ 체결수량 비율 - 변동성 국면 후보 | 위험/ 변동성 EDA만 |
| F07 | high-R grid / low-R 방향성 자동 전환 | 복합 가설/ 부분 EDA |
| F08 | 공격 체결 x 소비 측 weak refill | 원시 방향·강도 평가 |
| O01 | l1_obi - 독립 onset 비교 후보 | 가격 경로 EDA만 |
| O02 | l2_obi - 독립 onset 비교 후보 | 가격 경로 EDA만 |
| O03 | ofi - 독립 onset 비교 후보 | 가격 경로 EDA만 |
| O04 | replenishment - 독립 onset 비교 후보 | 가격 경로 EDA만 |
| O05 | trade_imbalance - 독립 onset 비교 후보 | 가격 경로 EDA만 |
| O06 | signed_trade_flow - 독립 onset 비교 후보 | 가격 경로 EDA만 |
| O07 | aggressive_weak_refill - 독립 onset 비교 후보 | 가격 경로 EDA만 |
| O08 | 신호 절댓값 강도·signed decile 후보 | 반응 EDA만 |
| G01 | 상태 개정 S1 순유동성 OFI | 역사 native replay/ 새 live 운용 없음 |
| G02 | 상태 개정 S2 외부 기준 가격 | 역사 native replay/ 새 live 운용 없음 |
| G03 | 상태 개정 S3 공격 체결 흐름 | 역사 native replay/ 새 live 운용 없음 |
| G04 | 상태 개정 S4 L1 OBI | 역사 native replay/ 새 live 운용 없음 |
| G05 | S2 Binance-leads 로컬 catch-up 분기 | 구현/ entry 표본 없음 |
| G06 | S2 local-idiosyncratic 로컬 되돌림 분기 | 구현/ entry 표본 없음 |
| G07 | S3 흡수 후 공급 소진 breakout 분기 | 구현/ 전체 S3 replay에 포함 |
| G08 | S3 흡수 후 공격자 소진 reversal 분기 | 구현/ 전체 S3 replay에 포함 |
| G09 | 극단 OBI·OFI·levelgap/ spread 확대 Jump Risk MM | 구현/ 통합 비교에 포함 |
| G10 | Direction x Magnitude x Volatility x Inventory 통합 구조 | 구현된 부분과 미학습 부분 구분 |
| G11 | 가격 변화 유무·활동 확률 - 후속 학습 후보 | 논의/ 고유 학습·운용 없음 |
| G12 | 다음 가격 변화 방향 분류 - 후속 학습 후보 | 논의/ 고유 학습·운용 없음 |
| G13 | 다음 가격 변화까지의 시간 - 후속 학습 후보 | 논의/ 고유 학습·운용 없음 |
| G14 | 방향 조건부 이동 규모 모델 - 후속 학습 후보 | 논의/ 고유 학습·운용 없음 |
| H01 | 통합 static - 가격 신호 없는 BBO 기본 MM | 동일 자산 3 조 건 native replay |
| H02 | 통합 obi - L1 OBI 통합 기준 | 동일 자산 3 조 건 native replay |
| H03 | 통합 book - OBI + OFI book pressure | 동일 자산 3 조 건 native replay |
| H04 | 통합 flow - book + absorption/ exhaustion | 동일 자산 3 조 건 native replay |
| H05 | 통합 cross - flow + cross-venue origin | 동일 자산 3 조 건 native replay |
| H06 | 통합 volatility - cross + R/ RV/ levelgap 위험 | 동일 자산 3 조 건 native replay |
| H07 | 통합 inventory - volatility + inventory skew | 동일 자산 3 조 건 native replay |
| I01 | 상태 S2 초기 구현 - 비연속 표본 결합·드리프트 제한 전 | 보 존 소스/ 최종 G02에서 보완 |
| I02 | 상태 S3 초기 구현 - 체결 유효성 확인 전 episode 누적 | 보 존 소스/ 최종 G03에서 보완 |
| I03 | 통합 static 초기 구현 - 상시 재고 감축 분기 전 | 중간 v2 재생/ 최종 H01에서 보완 |
| J01 | S2 robust EWMA baseline 대안 | 제안/ 고유 구현·학습 없음 |
| J02 | S2 rolling quantile center baseline 대안 | 제안/ 고유 구현·학습 없음 |
| J03 | S2 state-space slow mean baseline 대안 | 제안/ 고유 구현·학습 없음 |
| J04 | 피드백 정책 A - 비상 taker 없는 maker-only 감축 | 제안 및 최종 기본 정책 |
| J05 | 피드백 정책 B - 정상 maker·hard risk emergency taker | 명시 옵션/ 이번 성과에는 비활성 |
| J06 | 피드백 일반형 - Direction·Volatility·Inventory quote skew | 제안/ 최종 tick 정책의 선행 식 |
| Z01 | Turtle breakout 프로젝트 검증용 프로토타입 | 코드 확인/ 성과 근거 없음 |
| Z02 | Example signal - 직전 캔들 고가/ 저가 돌파 | hot-path 예제/ 성과 근거 없음 |


각 카드는 논리·신호·진입·청산·EDA/ 실측·해설·한계·출처를 같은 틀로 갖는다. 실제 룰이 없으면‘미동결/ 미실행’으로 표시하며 가상의 threshold 나 PnL을 만들지 않는다. EDA의 예측 값을 그 당시 주문에 썼다고 소급하지 않는다.

## 4. 공통 측정·실행 정의

FV는 진짜 잠재 내재 가치 관측이 아니고 미래 mid의 proxy 다. predictionRMSE와 tradePnL을 분리한다. 1 bp=.01%, RMSE 감소 %는 수익률이 아니다. 방향 BA는 비영상 승/ 하락 표본의 recall 평균이며 무변화 제외 분모와 체결 승률을 혼동하지 않는다. high-low 반응은 두 조건부 집단 평균 차이, 평균 alignedmove 와개별 event 경로 중앙값도 다르다.

원형 FV train은 별도 10분: 처음 25%basis,65%까지 계수 fit·Hpurge, 그 뒤 adverseerrorq90calibration. primaryH30, 보조 10/60. 이후 diagnosis는 같은 계수를 100 ms~300초에 적용하되 t+H 까지마지막 BBO를 사용해 기존 firstfuture1초표본과 목표 정의가 다르다.

기초 indicator 연구는 nativeL2복원 후 1초 집계, W1/5/10/30/60, H 매 초 1~180. production 시간 순 60/40·label180초 purge·window60초 embargo, pilot은 별도 10 min/30 min 원장. 모든 past60/ future180이 정상인 공통 지지 구간. crossed/ sequencegap/ 결 측 경로를 보 간하지 않고 제외한다. book stock·OBI는 시간 가중, flow는 합계. 원형 activity clock은 다른 계산 창이다.

회귀는 train1/99%clip·standardization·ridge10·H별 계수 동결. inference 는보조 H1/2/5/10/30/60/120/180, block=max(60,2 max(W, H)),1000 bootstrap, BH family. block<10은 보류. 2070 유의 탐색 셀은 인접 W/ H·같은 자료의 상관 때문에 2070 독립 성공 전략이 아니다.

Public L2의 같은 가격 net 변화로 grosssubmit/ cancel/ hiddenliquidity를 식별하지 못한다. 공통 top10가격만 계산한 refill은 완전 소진·새 level을 놓친다. OFI 에실제 trade 량을 다시 더 하면 중복 소모일 수 있어 결합은 별도 계수를 사용한다. microprice 대용은 weightedmid이며 fullstate-transitionmicroprice가 아니다.

원형 cost는 maker entry+taker reduction+tick+adverseerror 이고새 normalstate는 maker entry+maker exit 다. 비용 gate·폭·life 도 변했으므로 원형/ 개정 차이를 alpha 한 가지 효과로 귀속하지 않는다. 실 체결 fees는 ledger에만 실제 비용으로 반영하고 errorbuffer를 손익에서 다시 차감하지 않는다.

## 5. 전략·개정·후보별 상세

### A00 - 잔여 매수량을 ask depth에 투영하는 최초 가격 아이디어

단계: **v1이 교정 한 전제/ 독립 운용 없음** / 분류: 초기 flow

**전략 논리:** 공개 체결에서 남은 공격 buy를 추정하고 반대 ask 누적 depth를 소진한 가격을 FV로 사용하는 초기 생각.

**신호·계산:** 잔여 buy/ sell, 표시 depth, flow 지속성. 원문은 arcus_mm_context(1). md를 입력으로 언급하지만 그 파일 자체는 이 대화에서 확보하지 않았다.

**진입·주문:** 도달 예상 가격보다 유리한 maker entry를 하려는 후보이며 별도 동결 문턱은 미확인.

**청산·재고:** 도달 가격의 반 대 호가에 exit 하는 생각. v1은 가격 touch와 내 queue 전량 체결이 다름을 교정했다.

**EDA·실측 결과:** 고유 실제 매매·EDA·PnL 없음. v1 원안 표의 교정 전 생각을 별도로 등록한다.

**가능한 해설:** 물량의 조건부 도달 가격, 미래 기대 mid, 내 실행 가능한 청산 가격은 다르다. 원형 직 투영을 그대로 수익 모델로 승인하지 않았다.

**한계·판정:** 실제 모주문 잔여량·공급 재유입·hidden queue는 public feed로 직접 알 수 없다.

근거: [V1](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v1_recorded_original.md)

### A01 - v1 원안 - 잔여 공격 주문·유효 공급·청산 서비스량

단계: **원안/ 일부 구현** / 분류: 초기 flow

**전략 논리:** 같은 방향의 아직 남은 공격 주문이 반대 호가의 순 재공급보다 크고 내 정상 exit queue까지 소모할 수 있을 때만 참여한다. 가격 도달과 내 전량 청산을 별도로 예측한다.

**신호·계산:** B/ S 유 량, neutral 배경, episode age별 생존, 가격별 순 공급, 소진율, 반대편 흡수·회복. P_struct/ P_reach/ P_exit의 세 가격을 구분.

**진입·주문:** 30초 지속 episode 및 소비 측 공급 고갈·방어 회복, 보수적 exit 서비스량·경제적 edge·수량·위험을 모두 통과한 한 방향 maker 후보.

**청산·재고:** 승인한 목표의 reduce-only maker; soft invalidation은 entry 취소와 짧은 감축 유예, hard 위험/120초 보유 만료는 제한 IOC 및 대사.

**EDA·실측 결과:** 원안은 실제 성과를 제공하지 않은 연구 명세. 구현 준비와 짧은 shadow를 수행했고 원안의 49일 walk-forward·정상 청산률 신뢰구간은 확보하지 않았다.

**가능한 해설:** 경제적 목적은 내 물량을 청산할 수 있는 잔여 흐름이며, 이미 본 총 체결량을 미래 서비스량으로 재사용하면 과대평가된다. 여러 조건의 AND는 기회 부족과 원인 식별 어려움을 만들 수 있다.

**한계·판정:** 모 주문 직접 식별 불가, public L2 순 공급은 gross 공급이 아님, survival·공급·fill 모델의 공동 오차. v4·상태 모델과 다른 모델이다.

근거: [V1](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v1_recorded_original.md), [L01](C:/Users/박제형/Desktop/VibeTrading/docs/LIQUIDITY_MM_BYBIT_TESTNET.md), [C02](C:/Users/박제형/Desktop/VibeTrading/vibetrading/liquidity_mm/strategy.py)

### A02 - v1 구현 - residual-flow-bybit-v1.0

단계: **Bybit testnet shadow·진단 replay** / 분류: 초기 flow

**전략 논리:** v1을 SOLUSDT의 선택적 한 방향 maker로 구체화. EWMA 유량과 이 산 hazard로 남은 물량을 projected depth에 통과시킨다.

**신호·계산:** 완료 5초 bin6개: 우 세 volume>=65%, 우세 bin>=4, 거래 bin>=4, trade ID>=20; 최근 10초 우세>=55%. EWMA half-life10/30/120초, Beta(1,1) age5구간 survival.

**진입·주문:** 120초 warmup·5초 판단. bid 또는 bid-1tick / ask 또는 ask+1 tick. 최근 3개 bin 중 2개에서 front 공급/ 소비<.7; 반대 체결 후 10초 회복 2회; 방어 depth50% haircut. flow×.75, supply×1.5, uncertainty10%. 33초 지연 이후 exit service와 maker 왕복 +2 bp latency+2 bp model+1 tick gate.

**청산·재고:** 정상 target PostOnly; target이 이미 marketable 이면 reduce-only IOC. 가장 오래된 fill부터 120초, soft2회/10초 유예, hard IOC 최대 3회·30초 cooldown. 최대 3 단위 0.3 SOL/90 USDT.

**EDA·실측 결과:** 150.06초 shadow: 주문/ 체결 0, warmup6·stale_mark3·stale_book19·no_episode2. 5/10/20초 diagnostic replay 모두 후보 0. 관련 70 tests 통과, 전체 288 pass/2 실패는 기존 배포 기대값 불일치.

**가능한 해설:** 기록·연결·예약·정리 경로는 확인했지만 수익 평가 표본이 없다. strict gate와 짧은 warmup 이후 관측이 원인 후보이며 alpha 부재 판정은 불가.

**한계·판정:** entry/ cancel/ exit5초·private3초는 보수 설정이지 실측 p95가 아니다. equity 변화는 다른 계정 요인일 수 있어 자체 trading PnL로 사용하지 않는다.

근거: [L01](C:/Users/박제형/Desktop/VibeTrading/docs/LIQUIDITY_MM_BYBIT_TESTNET.md), [L02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/liquidity-mm-setup-20261003.json), [C02](C:/Users/박제형/Desktop/VibeTrading/vibetrading/liquidity_mm/strategy.py)

### A03 - v1 calibrated mode - episode 증거로 거래 승인

단계: **구현/ 적격 증거 없음** / 분류: 초기 flow

**전략 논리:** 같은 가격 규칙을 쓰되 독립 episode의 비용 후 손익과 정상 exit 확률 하한이 양수인 cell만 승인한다.

**신호·계산:** direction× entry offset× age bucket cell, frozen survival·fee·config hash, out-of-sample 선언과 block bootstrap.

**진입·주문:** cell별 episode>=100, net_pnl lower95>0, normal_exit lower95>=max(.70, break-even+.05); 적격 evidence file과 fee/ config 정합성 필요.

**청산·재고:** A02와 동일한 예약·maker/ 위험 IOC exit이며 calibrated hazard는 실행 중 재학습하지 않음.

**EDA·실측 결과:** 적격 calibrated 파일과 14일 train+7일 calibration+7일 test/4 fold 자료를 만들지 않았다. 승인된 calibrated 운용 성과 없음.

**가능한 해설:** gate는 좋은 원칙이지만 필요 표본을 확보하지 않은 상태에서 거래가 차단된 것을 전략 성공·실패로 세지 않는다.

**한계·판정:** 짧은 replay로 evidence file을 만들어 수익성 승인을 받을 수 없다.

근거: [V1](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v1_recorded_original.md), [L01](C:/Users/박제형/Desktop/VibeTrading/docs/LIQUIDITY_MM_BYBIT_TESTNET.md), [L02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/liquidity-mm-setup-20261003.json)

### A04 - 원형 100/100 양방향 호가·체결 후 재제출

단계: **제안/ 기본 비활성** / 분류: 재고·주문 후보

**전략 논리:** bid q와 ask q로 spread 왕복을 반복하고 bid fill 뒤 ask를 늘리고 bid를 다시 넣는 단순 MM 루프.

**신호·계산:** BBO 및 fill, q/ q 주문, 별도 검증된 방향별 fill edge가 원래 루프에는 없다.

**진입·주문:** 평탄 상태 bid100+ask100은 설명용 수량이며 실제 구현의 권장 수량이 아니다. 한쪽 fill 뒤 동일 단위 재제출을 가정.

**청산·재고:** 기존 ask100에 ask100을 추가하면 재고 100에 sell200이 되어 숏으로 넘어갈 수 있다. reduce-only와 전체 미확정 exit 예약이 필수.

**EDA·실측 결과:** 해당 순진한 루프의 별도 실제 운용/ 왕복 PnL은 확인되지 않는다. v1원문이 위험을 지적해 기본 비활성화했다.

**가능한 해설:** 양방향 거래의 경제적 우위와 재고 회수는 자동 보장되지 않는다. 기존 ask가 exit로 바뀌는 의미와 새로운 short entry를 분리해야한다.

**한계·판정:** quote touch를 fill로 세거나 buy/ sell 잔량을 상쇄하면 손익과 위험이 왜곡된다.

근거: [V1](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v1_recorded_original.md)

### A05 - 수정 100/100 루프 - 조건부 추가 진입·최대 3 단위

단계: **v1 구현 정책** / 분류: 재고·주문 후보

**전략 논리:** q0를 늘리지 않고 매 fill 뒤 남은 긍정적 edge와 청산 capacity를 재확인하여 최대 3 단위까지 쌓는다.

**신호·계산:** 실제 재고, 아직 체결 가능한 entry/ exit leaves, 최초 episode deadline, 보수 exit 서비스량.

**진입·주문:** long q fill 뒤 reduce-only ask 총 q와 조건부 bid q; I=3 q에서 buy 중단. 가격 하락이나 손실 회복만으로 추가하지 않음.

**청산·재고:** exit 합계<=실제 재고-기존 미확정 exit; 최초 fill의 보유 deadline을 추가 fill로 연장하지 않음. 반전 전에 감축·cooldown.

**EDA·실측 결과:** A02의 shadow·0 후보 replay만 있어 추가 진입/ 다 단 재고의 성과는 미측정.

**가능한 해설:** 원형 A04의 과 청산과 martingale 위험을 줄인 실행 개정이며 새로운 alpha를 만든 것은 아니다.

**한계·판정:** 실제 fill·취소 race의 장기 성과와 capacity는 별도 검증 필요.

근거: [V1](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v1_recorded_original.md), [L01](C:/Users/박제형/Desktop/VibeTrading/docs/LIQUIDITY_MM_BYBIT_TESTNET.md), [C02](C:/Users/박제형/Desktop/VibeTrading/vibetrading/liquidity_mm/strategy.py)

### A06 - v1 2 단계 ladder - 0.5 q+0.5 q

단계: **확장 제안/ 미실행** / 분류: 재고·주문 후보

**전략 논리:** 한 entry 가격을 두 수준으로 분할해 가격/ 체결 기회를 비교하되 총 위험 예산을 유지한다.

**신호·계산:** 같은 flow alpha, 두 가격의 queue·체결·잔여 exit capacity.

**진입·주문:** 전체 q0 안에서 0.5 q0+0.5 q0. 최소 lot·동시 예약·같은 public volume 배분 필요. 세부 가격 간격은 미동결.

**청산·재고:** 실제 체결된 합계 만 reduce-only로 감축하며 두 entry의 미확정 잔량까지 노출 예약.

**EDA·실측 결과:** 원안의 후속 확장으로만 제시; 실제 grid/ ladder 운용 및 PnL 없음.

**가능한 해설:** 두 가격은 총 위험을 늘리지 않는 실행 변형이다. 거래 기회 증가와 우위 증가는 같은 말이 아니다.

**한계·판정:** 가격 간격·예약 race·queue 경쟁의 실측 없음.

근거: [V1](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v1_recorded_original.md)

### A07 - v1 양쪽 fill edge를 별도 승인하는 양방향 MM

단계: **선택 확장/ 미실행** / 분류: 재고·주문 후보

**전략 논리:** 공통 FV가 mid 부근이라는 이유 대신 bid-fill·ask-fill 각각의 비용 후 이익을 요구한다.

**신호·계산:** 방향별 보수 FV, 실제 buy/ sell fill-conditioned PnL·error buffer.

**진입·주문:** b+cost+error<=FV_long와 a-cost-error>=FV_short를 각각 충족하고 양쪽 체결 증거가 있을 때 q/ q.

**청산·재고:** 재고 후 반대 주문을 EXIT로 전환하고 수량 제한. atomic 변경 불가 시 cancelpending까지 직렬 제어.

**EDA·실측 결과:** 별도 양방향 적격 증거/ 실제 운용 성과 없음. 초기 v1과 v4는 한 쪽 entry만 사용.

**가능한 해설:** 대칭 가격만으로 양쪽 alpha를 승인하는 오류를 피하려는 제안이다.

**한계·판정:** 공통 static ablation과 같은 정책이 아니므로 static PnL을 성과로 대입하지 않음.

근거: [V1](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v1_recorded_original.md), [V3](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v3_recorded_original.md), [V04](C:/Users/박제형/Desktop/VibeTrading/docs/FV_MM_V4_TESTNETS.md)

### A08 - v2 언급 전신 - 방향성·횡보·공급충격 선 분류

단계: **간접 언급/ 별도 실험 없음** / 분류: 문서 전신

**전략 논리:** v3에서 제거한다고 명시한, 국면을 먼저 나누고 각 모듈의 quote를 선택하는 이전 구조.

**신호·계산:** v3의 비교 설명에서 매수 압력·공급부족·반대흐름·호가복귀 및 방향/ 횡보/ 충격 분류를 확인.

**진입·주문:** v2 원문과 고유 문턱은 제공/ 회수되지 않아 특정 규칙을 재구성하지 않음.

**청산·재고:** v2 고유 청산 규칙은 미확인. v1·v3 규칙을 임의 전용하지 않음.

**EDA·실측 결과:** 독립 코드·운용 원장·성과 없음. v3가 대체한 설계로 등록한다.

**가능한 해설:** 출처가 부족한 전신도 목록에서 누락하지 않되 실제 시도한 실행 전략이라고 과장하지 않는다.

**한계·판정:** 단일 v2 원안의 전체 전략 목록은 확인 불가이며 이 한계를 명시한다.

근거: [V3](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v3_recorded_original.md)

### A09 - v1 종목 회전·예약 자금 대비 기회 선택

단계: **원안 확장/ 미운용** / 분류: capacity·선택 정책

**전략 논리:** raw abs(FV-mid)가 큰 종목 대신 보수적 filled 기대 손익을 예약 자금과 cycle 시간으로 나눈 기회를 고른다.

**신호·계산:** Score=P(fill)*lowerBound(E[netPnL|fill])/(reservedNotional*max(E[cycle],10 s)); portfolio 및 sizing을 먼저 통과.

**진입·주문:** 하한이 양수인 후보에 새 capacity를 배치. 넓은 spread·큰 tick·빈 book을 큰 alpha로 착각하지 않음.

**청산·재고:** 기존 재고는 다른 종목 score가 높다는 이유로 강제 청산하지 않고 원래 가설·deadline·위험으로 관리.

**EDA·실측 결과:** 구현은 첫 SOL 단일 시장이었다. 다 종목 rotation/ capacity의 실측 성과는 없다.

**가능한 해설:** 효율적인 후보 선택은 기존 거래 alpha와 별개다. fill 확률·손익 하한·점유 시간이 미측정이면 score 도 인증되지 않는다.

**한계·판정:** 여러 alt의 같은 방향 위험을 독립 분산으로 보지 않으며 symbol 확대를 용량 검증으로 대신하지 않는다.

근거: [V1](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v1_recorded_original.md), [L01](C:/Users/박제형/Desktop/VibeTrading/docs/LIQUIDITY_MM_BYBIT_TESTNET.md)

### A10 - v1 후속 cross-venue anchor 강화

단계: **후속 제안/ 미실행** / 분류: 초기 flow 확장

**전략 논리:** 잔여-flow 모델에 외부 동일 자산 기준 가격을 더해 가격 근거를 강화하려는 후속 확장.

**신호·계산:** v1 flow/ supply와 동등 계약의 reference/ basis. 결합 가중치·gate는 동결하지 않았다.

**진입·주문:** 별도 성과가 확인된 뒤에만 확장하며 v1 진입의 필수 AND로 추가하지 않음.

**청산·재고:** 기존 v1 실제 exit capacity/ queue를 유지한다는 제안; 고유 청산 성과 없음.

**EDA·실측 결과:** v3/ v4의 독립 S2 및 새 state S2는 후속 구현이지만 이 v1 overlay를 실제 운용한 기록은 아니다.

**가능한 해설:** 외부 anchor를 읽는 것과 동시에 양 거래소를 헤지하는 것은 다르다.

**한계·판정:** 독립 S2 수치를 결합 overlay의 성과로 복제하지 않는다.

근거: [V1](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v1_recorded_original.md), [V3](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v3_recorded_original.md), [V4](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v4_recorded_original.md)

### A11 - v1 후속 RWA overlay

단계: **언급만/ 코인 MM 실험 외** / 분류: 원문 기타 확장

**전략 논리:** 원문이 후속 대상으로 제시한 RWA 가격·기준 시장 특성을 적용하는 overlay.

**신호·계산:** RWA 현물/ 기준 시장·거래 시간·stale oracle 등은 필요하지만 이 대화의 독립 자료·구현은 없다.

**진입·주문:** 고유 진입 규칙·threshold 미확인. crypto perpetual 규칙을 자동 전용하지 않음.

**청산·재고:** 고유 청산·hedge·휴 장 정책 미확인.

**EDA·실측 결과:** 원문 확장 항목만 확인; 수행된 RWA EDA·주문·PnL 없음.

**가능한 해설:** 문서에서 언급된 확장도 누락하지 않고 코인 실험과 범위를 분리해 등록한다.

**한계·판정:** 이 항목을 시도된 crypto alpha 나 성공/ 실패 건수로 세지 않는다.

근거: [V1](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v1_recorded_original.md), [V3](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v3_recorded_original.md)

### A12 - Fee0 본체와 3 bp 비용·인센티브 분리 시나리오

단계: **원안 조건/ 일부 실제 요율 관측** / 분류: 비용·환경 변형

**전략 논리:** points/ rebate 없이 자체 trading edge를 먼저 요구하고 비용이 있는 환경의 순익을 따로 평가한다.

**신호·계산:** 동일 alpha·maker/ taker 실제 fee·funding·points/ rebate 별도 장부. Fee0은 당시 연구 시나리오이며 모든 venue 현재 fee의 주장 아님.

**진입·주문:** 원래 flow gate에 비용을 반영한다. points를 받기 위해 음의 기대 거래를 승인한 구현은 없다.

**청산·재고:** 실제 종료/ 청산 cost와 인센티브 수입을 따로 계산하는 제안.

**EDA·실측 결과:** 원형·개정의 venue별 fees는 기록됐다. 별도 동결 3 bp subset 또는 인센티브 포함 수익성 시험은 수행되지 않았다.

**가능한 해설:** Arcus의 개정 기본 S1은 기록 maker fee0에서도 gross 손실이었다. 모든 손실을 수수료만으로 설명할 수 없다.

**한계·판정:** 이것은 비용 조건 변형이고 새로운 directional alpha가 아니다. 미래 프로그램의 유지 의무·points 수익도 미확인.

근거: [V1](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v1_recorded_original.md), [V3](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v3_recorded_original.md), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### A13 - 온라인 stochastic queue simulator 확장

단계: **v1 후속 언급/ 미 구현** / 분류: 체결 모델 변형

**전략 논리:** queue state에 따른 주문/ 취소/ 체결 확률을 확률적으로 재생해 실제 fill 불확실성을 표현하는 후속 실행 모델.

**신호·계산:** 가격·시간 우선순위, queue intensity·취소 위치·hidden liquidity. 원문은 해당 확장을 추후 성과 확인 뒤로 미뤘다.

**진입·주문:** 기존 alpha의 entry intent를 받아 실행하는 후보이며 독립 진입 alpha 없음.

**청산·재고:** 기존 실제 재고/ exit를 연결해야하나 고유 확률 모수·live 검증 없음.

**EDA·실측 결과:** 실제로 수행한 것은 FIFO/ 보수 queue 및 비례 cancel credit 민감 도다. 그것을 stochastic simulator 완성으로 부르지 않는다.

**가능한 해설:** queue 관측의 확률 모델과 deterministic execution replay를 구분한다.

**한계·판정:** 개별 order ID 없는 L2에서 정확한 앞 취소 위치를 알 수 없다.

근거: [V1](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v1_recorded_original.md), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md)

### B01 - v3 순유동성 OFI 작은 FV 가격 식

단계: **명세/ 독립 실행 없음** / 분류: v3 문서

**전략 논리:** 순 호가 압력의 일부가 미래에도 남는다.

**신호·계산:** price-aware u/ v; OFI=sum(u-v), activity=sum(|u|+|v|)가 nu*D에 도달하는 trailing 창; FV=m+beta*spread*OFI/ D.

**진입·주문:** FV 유효·|FV-mid|>C. h=max(spread/2, C); bid=min(bestbid, floor_tick(FV-h)), ask=max(bestask, ceil_tick(FV+h)). 단독 구현에서는 예측 방향 entry 한쪽만; lot/ depth/ equity/ 예약 한도 적용. v3는 후보를 AND/ 투표로 결합하지 않으며 가격 공급기와 공통 엔진을 분리한다.

**청산·재고:** long은 현재 FV의 ask, short은 bid의 reduce-only. 모델 무효·손실·120초·shutdown 유예 이후 marketable risk 감축. maker 중심이지만 IOC 가능; 원형 비용 예산은 maker entry+taker reduction+tick+학습 adverse error q90. 원안에서는 양쪽 fill edge가 검증된 경우 q/ q 및재고 J에 따른 q_b=q+max(-J,0), q_a=q+max(J,0)도 허용.

**EDA·실측 결과:** v3만을 별도 계수로 운용한 결과는 없다. 구현 수식 기준은 후보별 재구성된 v4를 선택했다. v4결과는 후손 증거로만 참조.

**가능한 해설:** v1의 잔여 미래 물량·다중 gate를 작은 한 변수 가격 식으로 줄인 개정. 그 단순화가 성과 개선을 입증한 것은 아니다.

**한계·판정:** v3와 v4가 비슷해도 문서 버전·정책 차이를 보존한다. 별도 PnL을 복제하지 않는다.

근거: [V3](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v3_recorded_original.md), [V4](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v4_recorded_original.md), [V04](C:/Users/박제형/Desktop/VibeTrading/docs/FV_MM_V4_TESTNETS.md)

### B02 - v3 외부 기준 가격 작은 FV 가격 식

단계: **명세/ 독립 실행 없음** / 분류: v3 문서

**전략 논리:** 정상 basis를 제거한 같은 자산 가격 차가 축소된다.

**신호·계산:** anchor=Binance perp mid*exp(frozen median log(local/ ref)); gap=anchor-m; FV=m+lambda*gap,0<lambda<=1.

**진입·주문:** FV 유효·|FV-mid|>C. h=max(spread/2, C); bid=min(bestbid, floor_tick(FV-h)), ask=max(bestask, ceil_tick(FV+h)). 단독 구현에서는 예측 방향 entry 한쪽만; lot/ depth/ equity/ 예약 한도 적용. v3는 후보를 AND/ 투표로 결합하지 않으며 가격 공급기와 공통 엔진을 분리한다.

**청산·재고:** long은 현재 FV의 ask, short은 bid의 reduce-only. 모델 무효·손실·120초·shutdown 유예 이후 marketable risk 감축. maker 중심이지만 IOC 가능; 원형 비용 예산은 maker entry+taker reduction+tick+학습 adverse error q90. 원안에서는 양쪽 fill edge가 검증된 경우 q/ q 및재고 J에 따른 q_b=q+max(-J,0), q_a=q+max(J,0)도 허용.

**EDA·실측 결과:** v3만을 별도 계수로 운용한 결과는 없다. 구현 수식 기준은 후보별 재구성된 v4를 선택했다. v4결과는 후손 증거로만 참조.

**가능한 해설:** v1의 잔여 미래 물량·다중 gate를 작은 한 변수 가격 식으로 줄인 개정. 그 단순화가 성과 개선을 입증한 것은 아니다.

**한계·판정:** v3와 v4가 비슷해도 문서 버전·정책 차이를 보존한다. 별도 PnL을 복제하지 않는다.

근거: [V3](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v3_recorded_original.md), [V4](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v4_recorded_original.md), [V04](C:/Users/박제형/Desktop/VibeTrading/docs/FV_MM_V4_TESTNETS.md)

### B03 - v3 공격 체결 흐름 작은 FV 가격 식

단계: **명세/ 독립 실행 없음** / 분류: v3 문서

**전략 논리:** 공격 buy/ sell의 순 물량과 분할 집행이 이후 가격 이동을 남긴다.

**신호·계산:** 실제 aggressor buy-sell; 총 체결량 nu*D의 trailing 창. FV=m+beta*spread*(buy-sell)/ D. unknown 방향 제외.

**진입·주문:** FV 유효·|FV-mid|>C. h=max(spread/2, C); bid=min(bestbid, floor_tick(FV-h)), ask=max(bestask, ceil_tick(FV+h)). 단독 구현에서는 예측 방향 entry 한쪽만; lot/ depth/ equity/ 예약 한도 적용. v3는 후보를 AND/ 투표로 결합하지 않으며 가격 공급기와 공통 엔진을 분리한다.

**청산·재고:** long은 현재 FV의 ask, short은 bid의 reduce-only. 모델 무효·손실·120초·shutdown 유예 이후 marketable risk 감축. maker 중심이지만 IOC 가능; 원형 비용 예산은 maker entry+taker reduction+tick+학습 adverse error q90. 원안에서는 양쪽 fill edge가 검증된 경우 q/ q 및재고 J에 따른 q_b=q+max(-J,0), q_a=q+max(J,0)도 허용.

**EDA·실측 결과:** v3만을 별도 계수로 운용한 결과는 없다. 구현 수식 기준은 후보별 재구성된 v4를 선택했다. v4결과는 후손 증거로만 참조.

**가능한 해설:** v1의 잔여 미래 물량·다중 gate를 작은 한 변수 가격 식으로 줄인 개정. 그 단순화가 성과 개선을 입증한 것은 아니다.

**한계·판정:** v3와 v4가 비슷해도 문서 버전·정책 차이를 보존한다. 별도 PnL을 복제하지 않는다.

근거: [V3](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v3_recorded_original.md), [V4](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v4_recorded_original.md), [V04](C:/Users/박제형/Desktop/VibeTrading/docs/FV_MM_V4_TESTNETS.md)

### B04 - v3 L1 OBI 작은 FV 가격 식

단계: **명세/ 독립 실행 없음** / 분류: v3 문서

**전략 논리:** best queue 비대칭이 다음 소진/ 가격 방향에 정보를 갖는다.

**신호·계산:** OBI=(Qb-Qa)/(Qb+Qa); FV=m+beta*spread*OBI. 기본은 현재 L1 잔량.

**진입·주문:** FV 유효·|FV-mid|>C. h=max(spread/2, C); bid=min(bestbid, floor_tick(FV-h)), ask=max(bestask, ceil_tick(FV+h)). 단독 구현에서는 예측 방향 entry 한쪽만; lot/ depth/ equity/ 예약 한도 적용. v3는 후보를 AND/ 투표로 결합하지 않으며 가격 공급기와 공통 엔진을 분리한다.

**청산·재고:** long은 현재 FV의 ask, short은 bid의 reduce-only. 모델 무효·손실·120초·shutdown 유예 이후 marketable risk 감축. maker 중심이지만 IOC 가능; 원형 비용 예산은 maker entry+taker reduction+tick+학습 adverse error q90. 원안에서는 양쪽 fill edge가 검증된 경우 q/ q 및재고 J에 따른 q_b=q+max(-J,0), q_a=q+max(J,0)도 허용.

**EDA·실측 결과:** v3만을 별도 계수로 운용한 결과는 없다. 구현 수식 기준은 후보별 재구성된 v4를 선택했다. v4결과는 후손 증거로만 참조.

**가능한 해설:** v1의 잔여 미래 물량·다중 gate를 작은 한 변수 가격 식으로 줄인 개정. 그 단순화가 성과 개선을 입증한 것은 아니다.

**한계·판정:** v3와 v4가 비슷해도 문서 버전·정책 차이를 보존한다. 별도 PnL을 복제하지 않는다.

근거: [V3](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v3_recorded_original.md), [V4](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v4_recorded_original.md), [V04](C:/Users/박제형/Desktop/VibeTrading/docs/FV_MM_V4_TESTNETS.md)

### B05 - v3 과거 가격 anchor 복 원 후보

단계: **비교 후 독립 코어에서 제외** / 분류: v3 문서

**전략 논리:** 일시적 압력이 끝나면 과거 가격으로 되돌아온다는 평균 회귀 가정.

**신호·계산:** 과거 anchor와 현재 mid 차이. anchor의 현재 유효성을 독립적으로 확인해야함.

**진입·주문:** 유효한 과거 가치 기준과 비용을 넘는 괴리가 있을 때의 후보이며 구체 lookback/ threshold는 당시 미동결.

**청산·재고:** anchor 회복 또는 근거 무효/ 손실·보유 한도; 구체 주문 정책은 미동결.

**EDA·실측 결과:** v3는 과거 가격의 FV 정당성이 부족해 독립 코어에서 제외. 후속 grid 가격 경로 연구와 별개로 실제 체결 실험 없음.

**가능한 해설:** 평평한 과거 mid 나 OFI0은 앞으로 같은 가격대가 유지된다는 증거가 아니다.

**한계·판정:** 후속 15/60분 band 검사 결과를 v3실제 매매 성과로 대체하지 않음.

근거: [V3](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v3_recorded_original.md), [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md)

### B06 - LP/ MM 호가 재가격화 후보

단계: **메커니즘 비교/ OFI에 포함** / 분류: v3 문서

**전략 논리:** 공급자의 추가·철수·price improvement가 새로운 예약 가격을 나타낼 수 있다.

**신호·계산:** bid/ ask 순 공급·가격 개선·철수. v3는 이를 price-aware OFI에 이미 포함한다고 명시.

**진입·주문:** 별도 alpha 점수로 다시 더 하지 않는다. 독립 매매 문턱 미정.

**청산·재고:** 독립 exit 정책 없음; OFI 후손과 공통 엔진에서만 사용.

**EDA·실측 결과:** 독립 재가격화 전략의 실측 PnL 없음.

**가능한 해설:** 관측된 재가격화가 현재 가격에 이미 반영됐는지를 구분해야한다. OFI와 중복 계수로 집계하면 입력의 독립성이 깨진다.

**한계·판정:** 공급자 identity·inventory·의도를 public L2로 직접 식별하지 못함.

근거: [V3](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v3_recorded_original.md)

### C01 - v4 독립 순유동성 OFI FV MM

단계: **실제 testnet·mainnet public pilot** / 분류: v4 실행

**전략 논리:** 순 호가 압력의 일부가 미래에도 남는다.

**신호·계산:** price-aware u/ v; OFI=sum(u-v), activity=sum(|u|+|v|)가 nu*D에 도달하는 trailing 창; FV=m+beta*spread*OFI/ D.

**진입·주문:** FV 유효·|FV-mid|>C. h=max(spread/2, C); bid=min(bestbid, floor_tick(FV-h)), ask=max(bestask, ceil_tick(FV+h)). 단독 구현에서는 예측 방향 entry 한쪽만; lot/ depth/ equity/ 예약 한도 적용.

**청산·재고:** long은 현재 FV의 ask, short은 bid의 reduce-only. 모델 무효·손실·120초·shutdown 유예 이후 marketable risk 감축. maker 중심이지만 IOC 가능; 원형 비용 예산은 maker entry+taker reduction+tick+학습 adverse error q90.

**EDA·실측 결과:** testnet 최종 30분 전체 8 경로 fill0, BybitS4order1. mainnet 기본 16 경로 의도 15/ fill0. Bybit의 사전 beta=-1.934와 평가 raw slope=+22.291은 부호가 달랐다. nu=1 창에서 |OFI/ D|<=1이므로 x가 spread 내로 제한된다. 동시 가격 충격을 미래 30초 지속으로 옮긴 가정과 짧은 계수 보정의 안정성이 약했다.

**가능한 해설:** 독립 공식 검 산 27,456 표본 불일치 0은 명세 일치 확인이다. 체결 0·계수 차단·비용 미달·수량 0·실행 수명 결함을 가설 전면 실패와 분리한다.

**한계·판정:** 서로 다른 종목 배정이며 testnet 가격은 production과 다름. 10분 train/30분 eval, 이미 본 자료의 탐색 비교; OOS 장기 수익 증거 없음.

| Venue | 종목 | 계수 | FV valid | 의도 | Fill | 30 s RMSE 감소 % | q |
| --- | --- | --- | --- | --- | --- | --- | --- |
| arcus | BTC | 1.1744 | True | 0 | 0 | -0.015 | 0.9520 |
| bybit | BTC | -1.9339 | True | 0 | 0 | -0.076 | 0.2163 |
| bitget | BTC | 28.7465 | True | 1 | 0 | 0.664 | 0.0654 |
| grvt | BTC | -22.6463 | True | 0 | 0 | -1.153 | 0.8516 |


근거: [V4](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v4_recorded_original.md), [V04](C:/Users/박제형/Desktop/VibeTrading/docs/FV_MM_V4_TESTNETS.md), [T01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_mm_v4_20261004/interrupted_execution_check/results.json), [T02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_mm_v4_20261004/final_30min/results.json), [P01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/final_30min/results.json), [P02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/final_30min/order_lifecycle_audit.json), [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/original_formula_audit.json), [C01](C:/Users/박제형/Desktop/VibeTrading/vibetrading/fv_mm/legacy/s1_net_liquidity.py)

### C02 - v4 독립 외부 기준 가격 FV MM

단계: **실제 testnet·mainnet public pilot** / 분류: v4 실행

**전략 논리:** 정상 basis를 제거한 같은 자산 가격 차가 축소된다.

**신호·계산:** anchor=Binance perp mid*exp(frozen median log(local/ ref)); gap=anchor-m; FV=m+lambda*gap,0<lambda<=1.

**진입·주문:** FV 유효·|FV-mid|>C. h=max(spread/2, C); bid=min(bestbid, floor_tick(FV-h)), ask=max(bestask, ceil_tick(FV+h)). 단독 구현에서는 예측 방향 entry 한쪽만; lot/ depth/ equity/ 예약 한도 적용.

**청산·재고:** long은 현재 FV의 ask, short은 bid의 reduce-only. 모델 무효·손실·120초·shutdown 유예 이후 marketable risk 감축. maker 중심이지만 IOC 가능; 원형 비용 예산은 maker entry+taker reduction+tick+학습 adverse error q90.

**EDA·실측 결과:** testnet 최종 30분 전체 8 경로 fill0, BybitS4order1. mainnet 기본 16 경로 의도 15/ fill0. Arcus/ Bybit/ Bitget의 lambda=-.291/1.112/1.744로 차단. 사후 분해에서 gap 수렴 성분은 +.512/ +.750/ +.977이나 anchor 이동성 분이-.803/ +.362/ +.767이었다. 전체 target 이동 계수를 순수 수렴율로 해석한 목표 불일치다. Binance 누락 0건으로 수집 중단은 원인이 아니다. 조기 종료 GRVT S2 216.78초 점검에서 4 orders/2 fills/1 왕복, fee.066502, net+.113898 USDT가 있었다. 최종 30 분과 분리하며 한 왕복의 alpha 인증이 아니다.

**가능한 해설:** 독립 공식 검 산 27,456 표본 불일치 0은 명세 일치 확인이다. 체결 0·계수 차단·비용 미달·수량 0·실행 수명 결함을 가설 전면 실패와 분리한다.

**한계·판정:** 서로 다른 종목 배정이며 testnet 가격은 production과 다름. 10분 train/30분 eval, 이미 본 자료의 탐색 비교; OOS 장기 수익 증거 없음.

| Venue | 종목 | 계수 | FV valid | 의도 | Fill | 30 s RMSE 감소 % | q |
| --- | --- | --- | --- | --- | --- | --- | --- |
| arcus | ETH | -0.2912 | False | 0 | 0 | 미측정 | 0.6743 |
| bybit | ETH | 1.1115 | False | 0 | 0 | 미측정 | 0.9520 |
| bitget | ETH | 1.7440 | False | 0 | 0 | 미측정 | 0.8741 |
| grvt | ETH | 0.1421 | True | 0 | 0 | 1.602 | 0.0506 |


근거: [V4](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v4_recorded_original.md), [V04](C:/Users/박제형/Desktop/VibeTrading/docs/FV_MM_V4_TESTNETS.md), [T01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_mm_v4_20261004/interrupted_execution_check/results.json), [T02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_mm_v4_20261004/final_30min/results.json), [P01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/final_30min/results.json), [P02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/final_30min/order_lifecycle_audit.json), [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/original_formula_audit.json), [C10](C:/Users/박제형/Desktop/VibeTrading/vibetrading/fv_mm/legacy/s2_external_anchor.py)

### C03 - v4 독립 공격 체결 흐름 FV MM

단계: **실제 testnet·mainnet public pilot** / 분류: v4 실행

**전략 논리:** 공격 buy/ sell의 순 물량과 분할 집행이 이후 가격 이동을 남긴다.

**신호·계산:** 실제 aggressor buy-sell; 총 체결량 nu*D의 trailing 창. FV=m+beta*spread*(buy-sell)/ D. unknown 방향 제외.

**진입·주문:** FV 유효·|FV-mid|>C. h=max(spread/2, C); bid=min(bestbid, floor_tick(FV-h)), ask=max(bestask, ceil_tick(FV+h)). 단독 구현에서는 예측 방향 entry 한쪽만; lot/ depth/ equity/ 예약 한도 적용.

**청산·재고:** long은 현재 FV의 ask, short은 bid의 reduce-only. 모델 무효·손실·120초·shutdown 유예 이후 marketable risk 감축. maker 중심이지만 IOC 가능; 원형 비용 예산은 maker entry+taker reduction+tick+학습 adverse error q90.

**EDA·실측 결과:** testnet 최종 30분 전체 8 경로 fill0, BybitS4order1. mainnet 기본 16 경로 의도 15/ fill0. Bybit mainnet30s RMSE 감소 2.71%, q=.0506. Arcus beta=-4.013 →평가 slope+2.797, Bitget+2.449 →-3.129로 부호 이동. episode 미래지속과반대공급흡수를 trade 량만으로 구분하지 못했다.

**가능한 해설:** 독립 공식 검 산 27,456 표본 불일치 0은 명세 일치 확인이다. 체결 0·계수 차단·비용 미달·수량 0·실행 수명 결함을 가설 전면 실패와 분리한다.

**한계·판정:** 서로 다른 종목 배정이며 testnet 가격은 production과 다름. 10분 train/30분 eval, 이미 본 자료의 탐색 비교; OOS 장기 수익 증거 없음.

| Venue | 종목 | 계수 | FV valid | 의도 | Fill | 30 s RMSE 감소 % | q |
| --- | --- | --- | --- | --- | --- | --- | --- |
| arcus | SOL | -4.0129 | True | 14 | 0 | -2.048 | 0.6276 |
| bybit | SOL | 1.3963 | True | 0 | 0 | 2.705 | 0.0506 |
| bitget | SOL | 2.4489 | True | 0 | 0 | -0.704 | 0.6276 |
| grvt | SOL | 미측정 | False | 0 | 0 | 미측정 | 미측정 |


근거: [V4](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v4_recorded_original.md), [V04](C:/Users/박제형/Desktop/VibeTrading/docs/FV_MM_V4_TESTNETS.md), [T01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_mm_v4_20261004/interrupted_execution_check/results.json), [T02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_mm_v4_20261004/final_30min/results.json), [P01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/final_30min/results.json), [P02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/final_30min/order_lifecycle_audit.json), [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/original_formula_audit.json), [C11](C:/Users/박제형/Desktop/VibeTrading/vibetrading/fv_mm/legacy/s3_aggressive_flow.py)

### C04 - v4 독립 L1 OBI FV MM

단계: **실제 testnet·mainnet public pilot** / 분류: v4 실행

**전략 논리:** best queue 비대칭이 다음 소진/ 가격 방향에 정보를 갖는다.

**신호·계산:** OBI=(Qb-Qa)/(Qb+Qa); FV=m+beta*spread*OBI. 기본은 현재 L1 잔량.

**진입·주문:** FV 유효·|FV-mid|>C. h=max(spread/2, C); bid=min(bestbid, floor_tick(FV-h)), ask=max(bestask, ceil_tick(FV+h)). 단독 구현에서는 예측 방향 entry 한쪽만; lot/ depth/ equity/ 예약 한도 적용.

**청산·재고:** long은 현재 FV의 ask, short은 bid의 reduce-only. 모델 무효·손실·120초·shutdown 유예 이후 marketable risk 감축. maker 중심이지만 IOC 가능; 원형 비용 예산은 maker entry+taker reduction+tick+학습 adverse error q90.

**EDA·실측 결과:** testnet 최종 30분 전체 8 경로 fill0, BybitS4order1. mainnet 기본 16 경로 의도 15/ fill0. Bybit testnet 사전 beta=-1.374와 평가 30 s slope+1.386의 부호 반대. mainnetBybit30s RMSE+1.52%, q=.0506. fixed 계수의 추가 as-of 시계 검사에서 5 s+4.12%, 비영 BA65.7%지만 forecast 중앙 폭.187 bp로 비용보다 작았다.

**가능한 해설:** 독립 공식 검 산 27,456 표본 불일치 0은 명세 일치 확인이다. 체결 0·계수 차단·비용 미달·수량 0·실행 수명 결함을 가설 전면 실패와 분리한다.

**한계·판정:** 서로 다른 종목 배정이며 testnet 가격은 production과 다름. 10분 train/30분 eval, 이미 본 자료의 탐색 비교; OOS 장기 수익 증거 없음.

| Venue | 종목 | 계수 | FV valid | 의도 | Fill | 30 s RMSE 감소 % | q |
| --- | --- | --- | --- | --- | --- | --- | --- |
| arcus | XRP | -0.0990 | True | 0 | 0 | 0.013 | 0.9510 |
| bybit | XRP | 0.6358 | True | 0 | 0 | 1.520 | 0.0506 |
| bitget | XRP | 2.5611 | True | 0 | 0 | -0.719 | 0.4373 |
| grvt | XRP | 0.2447 | True | 0 | 0 | 0.534 | 0.5676 |


근거: [V4](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v4_recorded_original.md), [V04](C:/Users/박제형/Desktop/VibeTrading/docs/FV_MM_V4_TESTNETS.md), [T01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_mm_v4_20261004/interrupted_execution_check/results.json), [T02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_mm_v4_20261004/final_30min/results.json), [P01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/final_30min/results.json), [P02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/final_30min/order_lifecycle_audit.json), [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/original_formula_audit.json), [C12](C:/Users/박제형/Desktop/VibeTrading/vibetrading/fv_mm/legacy/s4_obi.py)

### C05 - v4 선택형 OBI 지속성 확인

단계: **문서 후보/ 독립 운용 없음** / 분류: v4 확장

**전략 논리:** 순간 잔량이 잠깐 나타났다 사라지는 것을 줄이기 위해 OBI가 유지된 뒤의 남은 움직임을 본다.

**신호·계산:** 현재 OBI 및 선택 5초/30초 지속; 기본 S4는 순간 OBI로 독립 실험.

**진입·주문:** 지속 확인 시각부터 예측·주문을 시작해야하며 확인 전의 가격 이동을 future 성과에 넣지 않음. 별도 문턱 미동결.

**청산·재고:** FV 공통 감축·risk limits를 적용하는 제안; 별도 정책 성과 없음.

**EDA·실측 결과:** 고정 W의 시간 평균 OBI·onset 연구는 있지만, 엄격한 지속 dwell 조건의 독립 체결 실험은 없다.

**가능한 해설:** 지속 확인은 잡음과 기회를 함께 줄이며 정보가 이미 소진될 수 있다.

**한계·판정:** 시간 평균·강도 onset과 dwell 확인은 다른 변수다.

근거: [V3](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v3_recorded_original.md), [V4](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v4_recorded_original.md), [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md)

### C06 - v3/ v4 q/ q 와재고에따른 q/2 q 호가 수량 정책

단계: **명세/ 초기 실행은 축소** / 분류: 재고·주문 후보

**전략 논리:** 재고를가격 FV 왜곡 대신 수량 비대칭으로 다루되 양방향 신규 alpha가 각각 필요하다.

**신호·계산:** 확정 signed 재고 J, 기본 q, 모든 pendingnew/ cancel/ unknown leaves.

**진입·주문:** q_b=q+max(-J,0), q_a=q+max(J,0). +q 재고에서 q/2 q는 매도 2 q 중 q 가새 short 가능성이므로 headroom 과별도 alpha 필요.

**청산·재고:** 신규 alpha 없으면 기존 재고 감축 q 만; reduce-only 와새반대 entry를 분리한다.

**EDA·실측 결과:** 원형 실행은 한 쪽 OPEN 및실제재고 REDUCE로 제한했다. q/2 q 동시 양방향 신규의 별도 성과는 없다.

**가능한 해설:** 수량 규칙이 수익 신호를 대체하지 않으며 매수/ 매도 예약을 상쇄하면 위험을 누락한다.

**한계·판정:** A05의 총 exit<=재고 정책과 동일하지 않다. q/2 q는 새 반대 노출 가능성을 명시적으로 포함한다.

근거: [V3](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v3_recorded_original.md), [V4](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v4_recorded_original.md), [V04](C:/Users/박제형/Desktop/VibeTrading/docs/FV_MM_V4_TESTNETS.md)

### D01 - 초기 고정 5초 OFI5 ridge 모델

단계: **예측 진단만/ 주문 없음** / 분류: 초기 복합 EDA

**전략 논리:** 같은 BTC에서 정보 결합이 원형 단일 입력보다 미래 가격을 더 잘 설명하는지 평가.

**신호·계산:** 5초 OFI/ 현재 BBO 깊이; 별도 10분 train 표준화와 ridge10, 평가에서 재학습하지 않음.

**진입·주문:** 회귀 예측 부호로 방향을 해석했으나 실제 entry·fee·inventory gate를 동결해 운용하지 않았다.

**청산·재고:** H=1/5/30/60초는 관측 label이며 청산 규칙이 아니다. 별도 exit 성과 없음.

**EDA·실측 결과:** 4 venues*3 groups*4 horizons=48 비교. 초기 표현은 instantdepth 로정규화하고 clip 없는 곱셈을 써 얕은 book에서 폭증했다.

**가능한 해설:** combined8의 Bybit/ Bitget5s RMSE 감소는 약-2463%/-10192%. 추가 입력의 경제적 무효만으로 귀속할 수 없고 표현 외삽의 수치 불안정을 확인했다.

**한계·판정:** 후속 asinh·창 평균 depth·trainclip 모델과 같은 모델이 아니다. 모든 음수 결과를 보존한다.

| Venue | Train | Test | 5 s RMSE 감소 % | 예측중앙폭 bp | 최대 eval 표준화 |
| --- | --- | --- | --- | --- | --- |
| arcus | 592 | 1786 | 0.170 | 0.0325 | 8.7 |
| bybit | 592 | 1786 | 0.313 | 0.0447 | 75.4 |
| bitget | 592 | 1790 | -13.717 | 0.0434 | 170.7 |
| grvt | 133 | 701 | -127.664 | 0.1320 | 191.3 |


근거: [D01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/REPORT_KO.md), [D02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/combination_metrics.csv)

### D02 - 초기 고정 5초 OFI5_TFI5 ridge 모델

단계: **예측 진단만/ 주문 없음** / 분류: 초기 복합 EDA

**전략 논리:** 같은 BTC에서 정보 결합이 원형 단일 입력보다 미래 가격을 더 잘 설명하는지 평가.

**신호·계산:** 5초 OFI/ 현재 깊이와 signed trade/ 현재 깊이의 별도 두 계수; 별도 10분 train 표준화와 ridge10, 평가에서 재학습하지 않음.

**진입·주문:** 회귀 예측 부호로 방향을 해석했으나 실제 entry·fee·inventory gate를 동결해 운용하지 않았다.

**청산·재고:** H=1/5/30/60초는 관측 label이며 청산 규칙이 아니다. 별도 exit 성과 없음.

**EDA·실측 결과:** 4 venues*3 groups*4 horizons=48 비교. 초기 표현은 instantdepth 로정규화하고 clip 없는 곱셈을 써 얕은 book에서 폭증했다.

**가능한 해설:** combined8의 Bybit/ Bitget5s RMSE 감소는 약-2463%/-10192%. 추가 입력의 경제적 무효만으로 귀속할 수 없고 표현 외삽의 수치 불안정을 확인했다.

**한계·판정:** 후속 asinh·창 평균 depth·trainclip 모델과 같은 모델이 아니다. 모든 음수 결과를 보존한다.

| Venue | Train | Test | 5 s RMSE 감소 % | 예측중앙폭 bp | 최대 eval 표준화 |
| --- | --- | --- | --- | --- | --- |
| arcus | 592 | 1786 | 0.210 | 0.0324 | 11.9 |
| bybit | 592 | 1786 | -0.624 | 0.0518 | 153.3 |
| bitget | 592 | 1790 | -35.423 | 0.0443 | 526.6 |
| grvt | 133 | 701 | -127.664 | 0.1320 | 191.3 |


근거: [D01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/REPORT_KO.md), [D02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/combination_metrics.csv)

### D03 - 초기 고정 5초 combined8 ridge 모델

단계: **예측 진단만/ 주문 없음** / 분류: 초기 복합 EDA

**전략 논리:** 같은 BTC에서 정보 결합이 원형 단일 입력보다 미래 가격을 더 잘 설명하는지 평가.

**신호·계산:** OFI, TFI, OBI, 동일 BBO 순 증가/ 감소, trade intensity, OFI*TFI, TFI*OBI; 별도 10분 train 표준화와 ridge10, 평가에서 재학습하지 않음.

**진입·주문:** 회귀 예측 부호로 방향을 해석했으나 실제 entry·fee·inventory gate를 동결해 운용하지 않았다.

**청산·재고:** H=1/5/30/60초는 관측 label이며 청산 규칙이 아니다. 별도 exit 성과 없음.

**EDA·실측 결과:** 4 venues*3 groups*4 horizons=48 비교. 초기 표현은 instantdepth 로정규화하고 clip 없는 곱셈을 써 얕은 book에서 폭증했다.

**가능한 해설:** combined8의 Bybit/ Bitget5s RMSE 감소는 약-2463%/-10192%. 추가 입력의 경제적 무효만으로 귀속할 수 없고 표현 외삽의 수치 불안정을 확인했다.

**한계·판정:** 후속 asinh·창 평균 depth·trainclip 모델과 같은 모델이 아니다. 모든 음수 결과를 보존한다.

| Venue | Train | Test | 5 s RMSE 감소 % | 예측중앙폭 bp | 최대 eval 표준화 |
| --- | --- | --- | --- | --- | --- |
| arcus | 592 | 1786 | 1.827 | 0.0049 | 50.1 |
| bybit | 592 | 1786 | -2463.114 | 0.1292 | 6124.1 |
| bitget | 592 | 1790 | -10191.555 | 0.0729 | 21940.0 |
| grvt | 133 | 701 | -25.623 | 0.1814 | 191.3 |


근거: [D01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/REPORT_KO.md), [D02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/combination_metrics.csv)

### E01 - L1 OBI 시간 평균 - 안정화 ridge FV 후보

단계: **예측/ 반응 평가만** / 분류: 기초 지표 EDA

**전략 논리:** 잔량 비대칭 이후 가격 방향과 규모 정보.

**신호·계산:** TWMean((Qb-Qa)/(Qb+Qa)); W=1/5/10/30/60초, train1/99%clip·표준화·ridge10·절편/ H별 계수 동결.

**진입·주문:** 실제 신규 진입 가격·수량·수수료 gate를 동결해 운용하지 않았다. 학습된 예측 bp의 부호/ 크기를 측정한 후보.

**청산·재고:** H=1~180초는 미래 mid label이고 실제 보유 시간·exittrigger가 아니다. 청산 및 cost 후 P&L 미측정.

**EDA·실측 결과:** 모든 W와 매초 H에 대한 표면 평가, 아래 W5의 경로 동일 가중 중앙값. pilot_test H0 RMSE 감소 중앙값 2.334%; production H0 RMSE 감소 중앙값-4.909%

**가능한 해설:** 가격 규모 오차 개선과 조건부 방향 정보를 분리한다. 채택 가능한 순이익은 이 표에서 계산할 수 없다. 양/ 음 결과와 drift 기준을 같이 보존.

**한계·판정:** 작은 이동·무변화가 많고 지속 상태 표본은 독립 신호가 아니다. 정상 과거 60초/ 미래 180초 전체 경로만 사용하며 Arcusproduction 방향 UNKNOWN·GRVTfreshtrade 부족은 미측정 처리.

| 자료 | H 초 | 경로 | RMSE 감소 % | drift 대비 % | 비영 BA% | 예측 폭 bp |
| --- | --- | --- | --- | --- | --- | --- |
| production | 1 | 15 | 0.033 | 0.034 | 73.32 | 0.0415 |
| production | 5 | 15 | 0.684 | 0.682 | 69.04 | 0.1128 |
| production | 10 | 15 | 0.951 | 0.950 | 67.76 | 0.1501 |
| production | 30 | 15 | 0.912 | 0.933 | 61.45 | 0.1802 |
| production | 60 | 15 | 0.673 | 0.721 | 58.50 | 0.2305 |
| production | 120 | 15 | 0.452 | 0.582 | 55.78 | 0.2198 |
| production | 180 | 15 | 0.212 | 0.278 | 53.71 | 0.2610 |
| pilot_test | 1 | 16 | 0.040 | 0.078 | 62.17 | 0.0161 |
| pilot_test | 5 | 16 | -0.316 | -0.233 | 53.85 | 0.0606 |
| pilot_test | 10 | 16 | -0.711 | -0.550 | 50.50 | 0.1131 |
| pilot_test | 30 | 16 | -2.178 | -0.406 | 50.00 | 0.4413 |
| pilot_test | 60 | 16 | -3.903 | -0.128 | 50.00 | 0.8231 |
| pilot_test | 120 | 16 | -3.405 | -0.124 | 50.00 | 1.5929 |
| pilot_test | 180 | 16 | -12.850 | -0.406 | 50.00 | 2.6453 |


근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/indicator_metrics.csv)

### E02 - L2 top10 total OBI - 안정화 ridge FV 후보

단계: **예측/ 반응 평가만** / 분류: 기초 지표 EDA

**전략 논리:** 여러 관측 queue의 대칭성.

**신호·계산:** TWMean((sumTop10Bid-sumTop10Ask)/(합계)); W=1/5/10/30/60초, train1/99%clip·표준화·ridge10·절편/ H별 계수 동결.

**진입·주문:** 실제 신규 진입 가격·수량·수수료 gate를 동결해 운용하지 않았다. 학습된 예측 bp의 부호/ 크기를 측정한 후보.

**청산·재고:** H=1~180초는 미래 mid label이고 실제 보유 시간·exittrigger가 아니다. 청산 및 cost 후 P&L 미측정.

**EDA·실측 결과:** 모든 W와 매초 H에 대한 표면 평가, 아래 W5의 경로 동일 가중 중앙값. pilot_test H0 RMSE 감소 중앙값 1.484%; production H0 RMSE 감소 중앙값 0.034%

**가능한 해설:** 가격 규모 오차 개선과 조건부 방향 정보를 분리한다. 채택 가능한 순이익은 이 표에서 계산할 수 없다. 양/ 음 결과와 drift 기준을 같이 보존.

**한계·판정:** 깊은 호가가 실제로 가격 결정이나 exit까지 유효한지는 별도. 정상 과거 60초/ 미래 180초 전체 경로만 사용하며 Arcusproduction 방향 UNKNOWN·GRVTfreshtrade 부족은 미측정 처리.

| 자료 | H 초 | 경로 | RMSE 감소 % | drift 대비 % | 비영 BA% | 예측 폭 bp |
| --- | --- | --- | --- | --- | --- | --- |
| production | 1 | 15 | 0.010 | 0.017 | 67.83 | 0.0166 |
| production | 5 | 15 | 0.278 | 0.280 | 66.44 | 0.0650 |
| production | 10 | 15 | 0.705 | 0.721 | 64.83 | 0.0809 |
| production | 30 | 15 | 0.782 | 0.741 | 60.56 | 0.1410 |
| production | 60 | 15 | 0.641 | 0.629 | 58.65 | 0.1729 |
| production | 120 | 15 | 0.420 | 0.497 | 55.21 | 0.1702 |
| production | 180 | 15 | 0.374 | 0.373 | 53.02 | 0.2421 |
| pilot_test | 1 | 16 | -0.026 | 0.061 | 55.28 | 0.0154 |
| pilot_test | 5 | 16 | -0.674 | -0.521 | 50.77 | 0.0756 |
| pilot_test | 10 | 16 | -1.066 | -0.711 | 49.96 | 0.1376 |
| pilot_test | 30 | 16 | -0.697 | -0.421 | 50.00 | 0.4624 |
| pilot_test | 60 | 16 | -1.392 | -0.681 | 50.00 | 0.7963 |
| pilot_test | 120 | 16 | -4.065 | -0.673 | 50.00 | 1.7420 |
| pilot_test | 180 | 16 | -12.263 | -0.966 | 50.00 | 3.4637 |


근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/indicator_metrics.csv)

### E03 - 전체 추적 depth OBI - 안정화 ridge FV 후보

단계: **예측/ 반응 평가만** / 분류: 기초 지표 EDA

**전략 논리:** 넓은 공급 분포가 미래 방향 정보를 추가하는지.

**신호·계산:** 실제로 유지한 모든 관측 가격 단계 총 수량의 TW imbalance; W=1/5/10/30/60초, train1/99%clip·표준화·ridge10·절편/ H별 계수 동결.

**진입·주문:** 실제 신규 진입 가격·수량·수수료 gate를 동결해 운용하지 않았다. 학습된 예측 bp의 부호/ 크기를 측정한 후보.

**청산·재고:** H=1~180초는 미래 mid label이고 실제 보유 시간·exittrigger가 아니다. 청산 및 cost 후 P&L 미측정.

**EDA·실측 결과:** 모든 W와 매초 H에 대한 표면 평가, 아래 W5의 경로 동일 가중 중앙값. pilot_test H0 RMSE 감소 중앙값-0.943%; production H0 RMSE 감소 중앙값-0.104%

**가능한 해설:** 가격 규모 오차 개선과 조건부 방향 정보를 분리한다. 채택 가능한 순이익은 이 표에서 계산할 수 없다. 양/ 음 결과와 drift 기준을 같이 보존.

**한계·판정:** snapshot 깊이·레벨 절단 차이와 먼 가격의 무관한 큰 주문에 민감. 정상 과거 60초/ 미래 180초 전체 경로만 사용하며 Arcusproduction 방향 UNKNOWN·GRVTfreshtrade 부족은 미측정 처리.

| 자료 | H 초 | 경로 | RMSE 감소 % | drift 대비 % | 비영 BA% | 예측 폭 bp |
| --- | --- | --- | --- | --- | --- | --- |
| production | 1 | 15 | -0.008 | -0.003 | 51.14 | 0.0074 |
| production | 5 | 15 | -0.000 | 0.002 | 51.75 | 0.0071 |
| production | 10 | 15 | 0.003 | 0.002 | 50.89 | 0.0114 |
| production | 30 | 15 | 0.011 | 0.010 | 50.92 | 0.0397 |
| production | 60 | 15 | -0.010 | 0.008 | 50.00 | 0.0773 |
| production | 120 | 15 | 0.014 | 0.022 | 50.61 | 0.1318 |
| production | 180 | 15 | 0.037 | 0.006 | 50.00 | 0.1723 |
| pilot_test | 1 | 16 | -0.074 | -0.031 | 50.00 | 0.0191 |
| pilot_test | 5 | 16 | -0.558 | -0.351 | 50.00 | 0.0938 |
| pilot_test | 10 | 16 | -1.012 | -0.833 | 50.37 | 0.1904 |
| pilot_test | 30 | 16 | -3.408 | -0.764 | 50.00 | 0.5073 |
| pilot_test | 60 | 16 | -0.234 | -0.280 | 50.00 | 0.6674 |
| pilot_test | 120 | 16 | -1.950 | 0.738 | 50.00 | 1.1851 |
| pilot_test | 180 | 16 | -7.073 | 1.029 | 50.00 | 2.1172 |


근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/indicator_metrics.csv)

### E04 - OFI 창 평균 depth·asinh - 안정화 ridge FV 후보

단계: **예측/ 반응 평가만** / 분류: 기초 지표 EDA

**전략 논리:** 순 공급 변화의 동시 충격과 미래 지속을 분리.

**신호·계산:** asinh(sum(priceAwareOFI)/ TWMeanL1depth); W=1/5/10/30/60초, train1/99%clip·표준화·ridge10·절편/ H별 계수 동결.

**진입·주문:** 실제 신규 진입 가격·수량·수수료 gate를 동결해 운용하지 않았다. 학습된 예측 bp의 부호/ 크기를 측정한 후보.

**청산·재고:** H=1~180초는 미래 mid label이고 실제 보유 시간·exittrigger가 아니다. 청산 및 cost 후 P&L 미측정.

**EDA·실측 결과:** 모든 W와 매초 H에 대한 표면 평가, 아래 W5의 경로 동일 가중 중앙값. pilot_test H0 RMSE 감소 중앙값 20.103%; production H0 RMSE 감소 중앙값 15.587%

**가능한 해설:** 가격 규모 오차 개선과 조건부 방향 정보를 분리한다. 채택 가능한 순이익은 이 표에서 계산할 수 없다. 양/ 음 결과와 drift 기준을 같이 보존.

**한계·판정:** H0충격 설명은 미래 수익 예측이 아니다. 정상 과거 60초/ 미래 180초 전체 경로만 사용하며 Arcusproduction 방향 UNKNOWN·GRVTfreshtrade 부족은 미측정 처리.

| 자료 | H 초 | 경로 | RMSE 감소 % | drift 대비 % | 비영 BA% | 예측 폭 bp |
| --- | --- | --- | --- | --- | --- | --- |
| production | 1 | 15 | 0.428 | 0.441 | 71.29 | 0.0078 |
| production | 5 | 15 | 0.944 | 0.948 | 65.55 | 0.0179 |
| production | 10 | 15 | 0.845 | 0.855 | 61.76 | 0.0224 |
| production | 30 | 15 | 0.446 | 0.490 | 55.74 | 0.0345 |
| production | 60 | 15 | 0.158 | 0.222 | 53.63 | 0.0613 |
| production | 120 | 15 | 0.163 | 0.137 | 52.37 | 0.0984 |
| production | 180 | 15 | 0.179 | 0.117 | 51.70 | 0.1671 |
| pilot_test | 1 | 16 | 0.525 | 0.598 | 67.42 | 0.0257 |
| pilot_test | 5 | 16 | 0.563 | 0.783 | 61.48 | 0.1063 |
| pilot_test | 10 | 16 | -0.285 | 0.127 | 52.28 | 0.0767 |
| pilot_test | 30 | 16 | -0.501 | -0.164 | 50.00 | 0.3101 |
| pilot_test | 60 | 16 | -1.444 | -0.145 | 50.00 | 0.6196 |
| pilot_test | 120 | 16 | -3.799 | -0.218 | 50.00 | 1.5658 |
| pilot_test | 180 | 16 | -10.866 | -0.068 | 50.00 | 3.0012 |


근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/indicator_metrics.csv)

### E05 - 동일 가격 net refill imbalance - 안정화 ridge FV 후보

단계: **예측/ 반응 평가만** / 분류: 기초 지표 EDA

**전략 논리:** 매수 측 재공급/ 매도측 재공급의 방향 정보.

**신호·계산:** (bidNetIncrease-askNetIncrease)/(increase 합계); W=1/5/10/30/60초, train1/99%clip·표준화·ridge10·절편/ H별 계수 동결.

**진입·주문:** 실제 신규 진입 가격·수량·수수료 gate를 동결해 운용하지 않았다. 학습된 예측 bp의 부호/ 크기를 측정한 후보.

**청산·재고:** H=1~180초는 미래 mid label이고 실제 보유 시간·exittrigger가 아니다. 청산 및 cost 후 P&L 미측정.

**EDA·실측 결과:** 모든 W와 매초 H에 대한 표면 평가, 아래 W5의 경로 동일 가중 중앙값. pilot_test H0 RMSE 감소 중앙값 0.085%; production H0 RMSE 감소 중앙값-1.437%

**가능한 해설:** 가격 규모 오차 개선과 조건부 방향 정보를 분리한다. 채택 가능한 순이익은 이 표에서 계산할 수 없다. 양/ 음 결과와 drift 기준을 같이 보존.

**한계·판정:** 공통 top10가격만 계산하므로 완전 소진·신규 level 제외; grossrefill 아님. 정상 과거 60초/ 미래 180초 전체 경로만 사용하며 Arcusproduction 방향 UNKNOWN·GRVTfreshtrade 부족은 미측정 처리.

| 자료 | H 초 | 경로 | RMSE 감소 % | drift 대비 % | 비영 BA% | 예측 폭 bp |
| --- | --- | --- | --- | --- | --- | --- |
| production | 1 | 15 | -0.189 | -0.180 | 60.42 | 0.0151 |
| production | 5 | 15 | -0.010 | 0.006 | 54.93 | 0.0268 |
| production | 10 | 15 | 0.049 | 0.045 | 54.48 | 0.0437 |
| production | 30 | 15 | 0.080 | 0.078 | 52.59 | 0.0916 |
| production | 60 | 15 | 0.065 | 0.078 | 51.73 | 0.1165 |
| production | 120 | 15 | 0.060 | 0.053 | 51.14 | 0.1510 |
| production | 180 | 15 | 0.029 | 0.044 | 50.67 | 0.1918 |
| pilot_test | 1 | 16 | -0.037 | -0.013 | 50.00 | 0.0120 |
| pilot_test | 5 | 16 | -0.505 | -0.175 | 50.00 | 0.0731 |
| pilot_test | 10 | 16 | -0.335 | -0.052 | 50.00 | 0.1257 |
| pilot_test | 30 | 16 | -0.046 | -0.039 | 50.00 | 0.2489 |
| pilot_test | 60 | 16 | 0.028 | 0.121 | 50.00 | 0.5294 |
| pilot_test | 120 | 16 | -2.655 | 0.200 | 50.00 | 1.3827 |
| pilot_test | 180 | 16 | -9.131 | 0.198 | 50.00 | 2.8005 |


근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/indicator_metrics.csv)

### E06 - 동일 가격 net depletion imbalance - 안정화 ridge FV 후보

단계: **예측/ 반응 평가만** / 분류: 기초 지표 EDA

**전략 논리:** 한쪽 표시 queue 소진압력.

**신호·계산:** (askNetDecrease-bidNetDecrease)/(decrease 합계); W=1/5/10/30/60초, train1/99%clip·표준화·ridge10·절편/ H별 계수 동결.

**진입·주문:** 실제 신규 진입 가격·수량·수수료 gate를 동결해 운용하지 않았다. 학습된 예측 bp의 부호/ 크기를 측정한 후보.

**청산·재고:** H=1~180초는 미래 mid label이고 실제 보유 시간·exittrigger가 아니다. 청산 및 cost 후 P&L 미측정.

**EDA·실측 결과:** 모든 W와 매초 H에 대한 표면 평가, 아래 W5의 경로 동일 가중 중앙값. pilot_test H0 RMSE 감소 중앙값 0.845%; production H0 RMSE 감소 중앙값-4.038%

**가능한 해설:** 가격 규모 오차 개선과 조건부 방향 정보를 분리한다. 채택 가능한 순이익은 이 표에서 계산할 수 없다. 양/ 음 결과와 drift 기준을 같이 보존.

**한계·판정:** 소멸한 level 전체와 숨은 물량 제외; 취소와 체결 분해 불가. 정상 과거 60초/ 미래 180초 전체 경로만 사용하며 Arcusproduction 방향 UNKNOWN·GRVTfreshtrade 부족은 미측정 처리.

| 자료 | H 초 | 경로 | RMSE 감소 % | drift 대비 % | 비영 BA% | 예측 폭 bp |
| --- | --- | --- | --- | --- | --- | --- |
| production | 1 | 15 | 0.008 | 0.013 | 58.65 | 0.0095 |
| production | 5 | 15 | 0.045 | 0.043 | 56.67 | 0.0218 |
| production | 10 | 15 | 0.034 | 0.037 | 54.48 | 0.0250 |
| production | 30 | 15 | 0.042 | 0.037 | 52.35 | 0.0559 |
| production | 60 | 15 | 0.032 | 0.034 | 51.93 | 0.0671 |
| production | 120 | 15 | 0.012 | 0.001 | 50.85 | 0.1100 |
| production | 180 | 15 | 0.033 | 0.028 | 50.00 | 0.1807 |
| pilot_test | 1 | 16 | 0.002 | -0.012 | 52.52 | 0.0131 |
| pilot_test | 5 | 16 | -0.008 | -0.007 | 54.08 | 0.0531 |
| pilot_test | 10 | 16 | -0.150 | -0.116 | 50.58 | 0.1046 |
| pilot_test | 30 | 16 | 0.204 | 0.076 | 50.00 | 0.2746 |
| pilot_test | 60 | 16 | -0.614 | -0.078 | 50.00 | 0.5916 |
| pilot_test | 120 | 16 | -3.771 | 0.143 | 50.00 | 1.5710 |
| pilot_test | 180 | 16 | -8.240 | 0.033 | 50.00 | 2.6557 |


근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/indicator_metrics.csv)

### E07 - 공격 체결 volume imbalance - 안정화 ridge FV 후보

단계: **예측/ 반응 평가만** / 분류: 기초 지표 EDA

**전략 논리:** 같은 방향 수요의 정보.

**신호·계산:** (buyVolume-sellVolume)/(buyVolume+sellVolume); W=1/5/10/30/60초, train1/99%clip·표준화·ridge10·절편/ H별 계수 동결.

**진입·주문:** 실제 신규 진입 가격·수량·수수료 gate를 동결해 운용하지 않았다. 학습된 예측 bp의 부호/ 크기를 측정한 후보.

**청산·재고:** H=1~180초는 미래 mid label이고 실제 보유 시간·exittrigger가 아니다. 청산 및 cost 후 P&L 미측정.

**EDA·실측 결과:** 모든 W와 매초 H에 대한 표면 평가, 아래 W5의 경로 동일 가중 중앙값. pilot_test H0 RMSE 감소 중앙값 5.573%; production H0 RMSE 감소 중앙값-19.504%

**가능한 해설:** 가격 규모 오차 개선과 조건부 방향 정보를 분리한다. 채택 가능한 순이익은 이 표에서 계산할 수 없다. 양/ 음 결과와 drift 기준을 같이 보존.

**한계·판정:** 거래없음 0, unknown 방향 있는 창 제외; 공급 흡수 없음. 정상 과거 60초/ 미래 180초 전체 경로만 사용하며 Arcusproduction 방향 UNKNOWN·GRVTfreshtrade 부족은 미측정 처리.

| 자료 | H 초 | 경로 | RMSE 감소 % | drift 대비 % | 비영 BA% | 예측 폭 bp |
| --- | --- | --- | --- | --- | --- | --- |
| production | 1 | 9 | -0.035 | -0.033 | 63.90 | 0.0320 |
| production | 5 | 9 | 0.113 | 0.112 | 59.28 | 0.0641 |
| production | 10 | 9 | 0.137 | 0.138 | 56.93 | 0.0680 |
| production | 30 | 9 | 0.051 | 0.071 | 53.34 | 0.0558 |
| production | 60 | 9 | -0.017 | 0.035 | 50.95 | 0.0520 |
| production | 120 | 9 | -0.055 | 0.023 | 50.00 | 0.0984 |
| production | 180 | 9 | -0.080 | -0.005 | 50.00 | 0.1543 |
| pilot_test | 1 | 12 | -0.104 | -0.031 | 51.60 | 0.0130 |
| pilot_test | 5 | 12 | -0.138 | -0.088 | 50.98 | 0.0567 |
| pilot_test | 10 | 12 | -0.318 | -0.058 | 50.00 | 0.0623 |
| pilot_test | 30 | 12 | -0.947 | -0.225 | 50.00 | 0.3402 |
| pilot_test | 60 | 12 | -0.301 | -0.118 | 50.00 | 0.5340 |
| pilot_test | 120 | 12 | -3.206 | -0.033 | 50.00 | 1.4637 |
| pilot_test | 180 | 12 | -10.830 | -0.086 | 50.00 | 2.9807 |


근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/indicator_metrics.csv)

### E08 - signed trade 창 평균 depth·asinh - 안정화 ridge FV 후보

단계: **예측/ 반응 평가만** / 분류: 기초 지표 EDA

**전략 논리:** trade 강도를 깊이에 비례한 척도로 비교.

**신호·계산:** asinh((buy-sell)/ TWMeanL1depth); W=1/5/10/30/60초, train1/99%clip·표준화·ridge10·절편/ H별 계수 동결.

**진입·주문:** 실제 신규 진입 가격·수량·수수료 gate를 동결해 운용하지 않았다. 학습된 예측 bp의 부호/ 크기를 측정한 후보.

**청산·재고:** H=1~180초는 미래 mid label이고 실제 보유 시간·exittrigger가 아니다. 청산 및 cost 후 P&L 미측정.

**EDA·실측 결과:** 모든 W와 매초 H에 대한 표면 평가, 아래 W5의 경로 동일 가중 중앙값. pilot_test H0 RMSE 감소 중앙값 6.304%; production H0 RMSE 감소 중앙값 16.299%

**가능한 해설:** 가격 규모 오차 개선과 조건부 방향 정보를 분리한다. 채택 가능한 순이익은 이 표에서 계산할 수 없다. 양/ 음 결과와 drift 기준을 같이 보존.

**한계·판정:** 원형 S3 volume clock과 다름; 여기서는 fixedtimeW. 정상 과거 60초/ 미래 180초 전체 경로만 사용하며 Arcusproduction 방향 UNKNOWN·GRVTfreshtrade 부족은 미측정 처리.

| 자료 | H 초 | 경로 | RMSE 감소 % | drift 대비 % | 비영 BA% | 예측 폭 bp |
| --- | --- | --- | --- | --- | --- | --- |
| production | 1 | 9 | 0.108 | 0.109 | 60.14 | 0.0027 |
| production | 5 | 9 | 0.040 | 0.052 | 56.96 | 0.0033 |
| production | 10 | 9 | 0.011 | 0.024 | 50.78 | 0.0072 |
| production | 30 | 9 | -0.011 | -0.003 | 50.04 | 0.0168 |
| production | 60 | 9 | -0.019 | -0.007 | 49.87 | 0.0340 |
| production | 120 | 9 | -0.074 | 0.000 | 49.99 | 0.0804 |
| production | 180 | 9 | -0.068 | -0.000 | 50.00 | 0.1534 |
| pilot_test | 1 | 12 | -0.018 | -0.004 | 50.54 | 0.0115 |
| pilot_test | 5 | 12 | -0.497 | -0.205 | 50.03 | 0.0370 |
| pilot_test | 10 | 12 | -0.930 | -0.152 | 49.85 | 0.0689 |
| pilot_test | 30 | 12 | -1.752 | -0.905 | 49.62 | 0.2047 |
| pilot_test | 60 | 12 | -0.699 | -0.621 | 50.00 | 0.5383 |
| pilot_test | 120 | 12 | -2.943 | -0.219 | 50.00 | 1.4924 |
| pilot_test | 180 | 12 | -12.008 | -0.048 | 50.00 | 2.8935 |


근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/indicator_metrics.csv)

### E09 - Weighted BBO mid premium 대용 - 안정화 ridge FV 후보

단계: **예측/ 반응 평가만** / 분류: 기초 지표 EDA

**전략 논리:** queue 가중 가격의 mid 대비 premium.

**신호·계산:** weightedMid=(ask*bidQty+bid*askQty)/(qty 합계); TWMean(1 e4*(weightedMid/ mid-1)); W=1/5/10/30/60초, train1/99%clip·표준화·ridge10·절편/ H별 계수 동결.

**진입·주문:** 실제 신규 진입 가격·수량·수수료 gate를 동결해 운용하지 않았다. 학습된 예측 bp의 부호/ 크기를 측정한 후보.

**청산·재고:** H=1~180초는 미래 mid label이고 실제 보유 시간·exittrigger가 아니다. 청산 및 cost 후 P&L 미측정.

**EDA·실측 결과:** 모든 W와 매초 H에 대한 표면 평가, 아래 W5의 경로 동일 가중 중앙값. pilot_test H0 RMSE 감소 중앙값 2.782%; production H0 RMSE 감소 중앙값-5.451%

**가능한 해설:** 가격 규모 오차 개선과 조건부 방향 정보를 분리한다. 채택 가능한 순이익은 이 표에서 계산할 수 없다. 양/ 음 결과와 drift 기준을 같이 보존.

**한계·판정:** fullStoikovmicroprice의 상태 전이 모델 아님; OBI*halfspread와 수학적으로 연결. 정상 과거 60초/ 미래 180초 전체 경로만 사용하며 Arcusproduction 방향 UNKNOWN·GRVTfreshtrade 부족은 미측정 처리.

| 자료 | H 초 | 경로 | RMSE 감소 % | drift 대비 % | 비영 BA% | 예측 폭 bp |
| --- | --- | --- | --- | --- | --- | --- |
| production | 1 | 15 | 0.046 | 0.047 | 73.32 | 0.0413 |
| production | 5 | 15 | 0.675 | 0.673 | 68.96 | 0.1110 |
| production | 10 | 15 | 0.953 | 0.952 | 67.85 | 0.1398 |
| production | 30 | 15 | 0.910 | 0.930 | 61.47 | 0.1773 |
| production | 60 | 15 | 0.682 | 0.730 | 58.46 | 0.2305 |
| production | 120 | 15 | 0.462 | 0.579 | 55.39 | 0.2204 |
| production | 180 | 15 | 0.207 | 0.280 | 53.73 | 0.2631 |
| pilot_test | 1 | 16 | 0.064 | 0.099 | 62.17 | 0.0175 |
| pilot_test | 5 | 16 | -0.313 | 0.119 | 57.76 | 0.0694 |
| pilot_test | 10 | 16 | -0.367 | 0.111 | 51.62 | 0.1083 |
| pilot_test | 30 | 16 | -2.164 | -0.394 | 50.00 | 0.3949 |
| pilot_test | 60 | 16 | -3.918 | -0.095 | 50.00 | 0.8231 |
| pilot_test | 120 | 16 | -3.370 | -0.100 | 50.00 | 1.5940 |
| pilot_test | 180 | 16 | -12.715 | -0.391 | 50.00 | 2.6322 |


근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/indicator_metrics.csv)

### E10 - 안정화 OFI + signed trade ridge - 안정화 ridge FV 후보

단계: **예측/ 반응 평가만** / 분류: 기초 지표 EDA

**전략 논리:** 호가와 체결 정보의 추가 가치.

**신호·계산:** OFI와 signedtrade를 별도 두 회귀 계수로 fit; OFI에 tradevolume 직접 더 하지 않음; W=1/5/10/30/60초, train1/99%clip·표준화·ridge10·절편/ H별 계수 동결.

**진입·주문:** 실제 신규 진입 가격·수량·수수료 gate를 동결해 운용하지 않았다. 학습된 예측 bp의 부호/ 크기를 측정한 후보.

**청산·재고:** H=1~180초는 미래 mid label이고 실제 보유 시간·exittrigger가 아니다. 청산 및 cost 후 P&L 미측정.

**EDA·실측 결과:** 모든 W와 매초 H에 대한 표면 평가, 아래 W5의 경로 동일 가중 중앙값. pilot_test H0 RMSE 감소 중앙값 23.603%; production H0 RMSE 감소 중앙값 15.831%

**가능한 해설:** 가격 규모 오차 개선과 조건부 방향 정보를 분리한다. 채택 가능한 순이익은 이 표에서 계산할 수 없다. 양/ 음 결과와 drift 기준을 같이 보존.

**한계·판정:** 두 입력 변동이 모두 필요. 상수 trade가 OFI만 재현한 결과는 결합 성과에서 제외. 정상 과거 60초/ 미래 180초 전체 경로만 사용하며 Arcusproduction 방향 UNKNOWN·GRVTfreshtrade 부족은 미측정 처리.

| 자료 | H 초 | 경로 | RMSE 감소 % | drift 대비 % | 비영 BA% | 예측 폭 bp |
| --- | --- | --- | --- | --- | --- | --- |
| production | 1 | 9 | 0.384 | 0.393 | 71.23 | 0.0167 |
| production | 5 | 9 | 0.903 | 0.907 | 65.55 | 0.0269 |
| production | 10 | 9 | 0.728 | 0.726 | 61.54 | 0.0305 |
| production | 30 | 9 | 0.331 | 0.327 | 55.72 | 0.0384 |
| production | 60 | 9 | 0.163 | 0.211 | 52.76 | 0.0570 |
| production | 120 | 9 | 0.058 | 0.126 | 51.32 | 0.0985 |
| production | 180 | 9 | 0.015 | 0.088 | 50.89 | 0.1536 |
| pilot_test | 1 | 12 | 0.441 | 0.433 | 64.06 | 0.0305 |
| pilot_test | 5 | 12 | 0.130 | 0.505 | 60.70 | 0.1263 |
| pilot_test | 10 | 12 | -0.705 | -0.070 | 52.61 | 0.1094 |
| pilot_test | 30 | 12 | -1.666 | -1.037 | 49.49 | 0.3446 |
| pilot_test | 60 | 12 | -1.613 | -0.848 | 50.06 | 0.6301 |
| pilot_test | 120 | 12 | -5.484 | -0.471 | 50.00 | 1.4824 |
| pilot_test | 180 | 12 | -12.805 | -0.207 | 50.00 | 2.6147 |


근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/indicator_metrics.csv)

### E11 - 보충률 비대칭 - 안정화 ridge FV 후보

단계: **예측/ 반응 평가만** / 분류: 기초 지표 EDA

**전략 논리:** 방향별 재공급 대소진의 비율.

**신호·계산:** bidAdd/(bidAdd+bidRemove)-askAdd/(askAdd+askRemove); W=1/5/10/30/60초, train1/99%clip·표준화·ridge10·절편/ H별 계수 동결.

**진입·주문:** 실제 신규 진입 가격·수량·수수료 gate를 동결해 운용하지 않았다. 학습된 예측 bp의 부호/ 크기를 측정한 후보.

**청산·재고:** H=1~180초는 미래 mid label이고 실제 보유 시간·exittrigger가 아니다. 청산 및 cost 후 P&L 미측정.

**EDA·실측 결과:** 모든 W와 매초 H에 대한 표면 평가, 아래 W5의 경로 동일 가중 중앙값. pilot_test H0 RMSE 감소 중앙값 1.838%; production H0 RMSE 감소 중앙값-4.512%

**가능한 해설:** 가격 규모 오차 개선과 조건부 방향 정보를 분리한다. 채택 가능한 순이익은 이 표에서 계산할 수 없다. 양/ 음 결과와 drift 기준을 같이 보존.

**한계·판정:** 공급/ 취소 grossrate 아님; 활동 없는 분모는 0 처리한 proxy. 정상 과거 60초/ 미래 180초 전체 경로만 사용하며 Arcusproduction 방향 UNKNOWN·GRVTfreshtrade 부족은 미측정 처리.

| 자료 | H 초 | 경로 | RMSE 감소 % | drift 대비 % | 비영 BA% | 예측 폭 bp |
| --- | --- | --- | --- | --- | --- | --- |
| production | 1 | 15 | -0.094 | -0.070 | 55.32 | 0.0102 |
| production | 5 | 15 | 0.004 | 0.002 | 53.52 | 0.0224 |
| production | 10 | 15 | 0.005 | 0.001 | 51.89 | 0.0355 |
| production | 30 | 15 | 0.018 | -0.001 | 51.67 | 0.0537 |
| production | 60 | 15 | 0.023 | 0.010 | 50.71 | 0.0674 |
| production | 120 | 15 | 0.057 | 0.009 | 50.21 | 0.1348 |
| production | 180 | 15 | 0.019 | 0.007 | 50.00 | 0.1812 |
| pilot_test | 1 | 16 | 0.162 | 0.155 | 60.58 | 0.0137 |
| pilot_test | 5 | 16 | 0.114 | 0.113 | 54.73 | 0.0555 |
| pilot_test | 10 | 16 | 0.008 | 0.304 | 53.70 | 0.1052 |
| pilot_test | 30 | 16 | -0.390 | -0.037 | 50.36 | 0.3334 |
| pilot_test | 60 | 16 | -0.333 | 0.157 | 50.00 | 0.5417 |
| pilot_test | 120 | 16 | -3.330 | -0.045 | 50.00 | 1.5627 |
| pilot_test | 180 | 16 | -10.489 | -0.029 | 50.00 | 2.9848 |


근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/indicator_metrics.csv)

### E12 - signed trade x 보충률 차이 - 안정화 ridge FV 후보

단계: **예측/ 반응 평가만** / 분류: 기초 지표 EDA

**전략 논리:** trade 방향과 공급 흡수의 상호 작용.

**신호·계산:** signedFlow*(askRefillRate-bidRefillRate); W=1/5/10/30/60초, train1/99%clip·표준화·ridge10·절편/ H별 계수 동결.

**진입·주문:** 실제 신규 진입 가격·수량·수수료 gate를 동결해 운용하지 않았다. 학습된 예측 bp의 부호/ 크기를 측정한 후보.

**청산·재고:** H=1~180초는 미래 mid label이고 실제 보유 시간·exittrigger가 아니다. 청산 및 cost 후 P&L 미측정.

**EDA·실측 결과:** 모든 W와 매초 H에 대한 표면 평가, 아래 W5의 경로 동일 가중 중앙값. pilot_test H0 RMSE 감소 중앙값-1.142%; production H0 RMSE 감소 중앙값-0.145%

**가능한 해설:** 가격 규모 오차 개선과 조건부 방향 정보를 분리한다. 채택 가능한 순이익은 이 표에서 계산할 수 없다. 양/ 음 결과와 drift 기준을 같이 보존.

**한계·판정:** 부호가 방향을 반전할 수 있는 곱셈이다. 이후 aggressiveWeakRefill의 방향 보존 곱과 다름. 정상 과거 60초/ 미래 180초 전체 경로만 사용하며 Arcusproduction 방향 UNKNOWN·GRVTfreshtrade 부족은 미측정 처리.

| 자료 | H 초 | 경로 | RMSE 감소 % | drift 대비 % | 비영 BA% | 예측 폭 bp |
| --- | --- | --- | --- | --- | --- | --- |
| production | 1 | 9 | -0.028 | -0.014 | 50.00 | 0.0027 |
| production | 5 | 9 | -0.015 | -0.006 | 49.64 | 0.0027 |
| production | 10 | 9 | -0.023 | -0.015 | 50.04 | 0.0061 |
| production | 30 | 9 | -0.014 | 0.001 | 49.91 | 0.0170 |
| production | 60 | 9 | -0.031 | 0.003 | 49.98 | 0.0406 |
| production | 120 | 9 | -0.120 | -0.000 | 50.00 | 0.1012 |
| production | 180 | 9 | -0.079 | 0.006 | 49.93 | 0.1587 |
| pilot_test | 1 | 12 | -0.058 | -0.068 | 49.75 | 0.0091 |
| pilot_test | 5 | 12 | -0.276 | -0.324 | 49.72 | 0.0249 |
| pilot_test | 10 | 12 | -0.186 | -0.095 | 50.27 | 0.0484 |
| pilot_test | 30 | 12 | -0.158 | -0.109 | 49.92 | 0.1885 |
| pilot_test | 60 | 12 | -0.274 | -0.086 | 50.00 | 0.4432 |
| pilot_test | 120 | 12 | -2.782 | -0.021 | 50.00 | 1.5227 |
| pilot_test | 180 | 12 | -10.779 | 0.000 | 50.00 | 2.9614 |


근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [F02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/indicator_metrics.csv)

### F01 - 과거 15분 min/ max 밴드 중심 평균 회귀·grid 후보

단계: **가격경로 EDA 만** / 분류: range/ grid

**전략 논리:** 과거 상 하단 안에서 가격이 center 로회귀하며 grid 왕복을 만든다는 가정.

**신호·계산:** t 직전 lookback의 min/ max, center=(low+high)/2, halfwidth, z=(mid-center)/ halfwidth. 신호 이후 band 고정.

**진입·주문:** |z|<1, outerhalf |z|>=.5에서 center 쪽 방향을 평가. 실제 grid 간격·orderTTL·수량은 미동결.

**청산·재고:** center first hit와 band exit의 선후를 진단; 동일 1초도 달은 모호 표본. 실제 gridexit/ fillPnL 없음.

**EDA·실측 결과:** 15분 band/ highR/ 미래 30분에서 center-first 경로 중앙 31.8%, exit-first67.6%; center 이동/ 기존 halfwidth1.147. 비중첩 highR은 center24.0%, exit71.0%, 경로 median n8.

**가능한 해설:** 과거범위중간점은미래 economicFV 라고 자동 인정할 수 없다. band 이탈·center 이동·순익 cost를 따로 평가해야한다.

**한계·판정:** 검사는 center 가설의 firstpassage 이고실제 gridbacktest가 아니다. overlappedanchors·소수 독립 episode·국면별 band 폭 선택 차이.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md)

### F02 - 과거 60분 min/ max 밴드 중심 평균 회귀·grid 후보

단계: **가격경로 EDA 만** / 분류: range/ grid

**전략 논리:** 과거 상 하단 안에서 가격이 center 로회귀하며 grid 왕복을 만든다는 가정.

**신호·계산:** t 직전 lookback의 min/ max, center=(low+high)/2, halfwidth, z=(mid-center)/ halfwidth. 신호 이후 band 고정.

**진입·주문:** |z|<1, outerhalf |z|>=.5에서 center 쪽 방향을 평가. 실제 grid 간격·orderTTL·수량은 미동결.

**청산·재고:** center first hit와 band exit의 선후를 진단; 동일 1초도 달은 모호 표본. 실제 gridexit/ fillPnL 없음.

**EDA·실측 결과:** 60분 band/ highR/ 미래 30분 center-first32.3%, exit-first59.0%, neither6.9%; center 이동/ halfwidth.400. 높은 R 에서도 center 회귀를 보장하지 않았다.

**가능한 해설:** 과거범위중간점은미래 economicFV 라고 자동 인정할 수 없다. band 이탈·center 이동·순익 cost를 따로 평가해야한다.

**한계·판정:** 검사는 center 가설의 firstpassage 이고실제 gridbacktest가 아니다. overlappedanchors·소수 독립 episode·국면별 band 폭 선택 차이.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md)

### F03 - 고정 +-1% band center grid 후보

단계: **가격 경로 EDA/ entry 표본 없음** / 분류: range/ grid

**전략 논리:** center에서 상하 100 bp 안에 장래 FV가 있다는 가정으로 되돌림 왕복을 노린다.

**신호·계산:** 과거 center, 고정 halfwidth100bp, z 및 밴드 이탈.

**진입·주문:** 검사의 outerhalf 조건은 center에서 50 bp 이상 내부 이탈이다. 실제 gridentry 문턱은 미동결.

**청산·재고:** center 복귀·band 이탈·riskexit 가후보; 실제 주문 청산 규칙과 손익 없음.

**EDA·실측 결과:** 이번 정상 자료에서 |z|>=.5의 진입 표본 0. 넓은 범위 안에 머무름만 관측했고 큰 되돌림을 검증하지 못했다.

**가능한 해설:** entry 표본 0은 grid 효과의 부재가 아니라 가정한 검사 조건에서 검증 기회가 없었다는 뜻.

**한계·판정:** 다른 간격 grid의 실패로 확대하지 않음; 범위 밖 손실·inventory·fees 모델 미실행.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md)

### F04 - 이벤트마다 L1 stock 합산/ 체결 대금 비율 - 변동성 국면 후보

단계: **위험/ 변동성 EDA 만** / 분류: R 국면

**전략 논리:** 공급 stock 대비소비 flow가 많으면 미래 변동성이 낮고 작으면 높다는 가설. R은 방향 부호가 없다.

**신호·계산:** sum_quote(b*Qb+a*Qa)/ sum_trade(price*qty); W1/5/10/30/60; train25/75%threshold 동결, 거래 0은 inactive로 분리. 주문 stock을 여러 번 세어 업데이트 빈도에 영향 받는 대조 변형.

**진입·주문:** R 단독 entry 없음. 저 변동시 크기/ 폭 또는 다른 directionprovider를 조절하는 후보다.

**청산·재고:** R 단독 exit 없음. 실제 inventory 감축과 독립적인 risk/ state 변수.

**EDA·실측 결과:** production 수량 R: W5→H5 high/ lowRV .375, W60→H60 .474. pastRV·flow에 R을 추가한 RV MAE 개선 W5 약.9~1.7%, W60거의 0 또는 음수. 이벤트 stock 합산의 모든 native tick 복제는 아니다.

**가능한 해설:** 관측 비율과 미래 RV의 관계는 있지만 이미 조용한 시장이 지속되는 효과와 activity를 통제하면 추가 가치가 작다.

**한계·판정:** 개인 분석 원 글은 S&P500 tick의 일별/20일 MA 라 이번 초 단위 crypto 검증과 다름. GRVT는 ratio 학습 freshtrade 부족.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md)

### F05 - 명목 stock 시간적 분/ 체결 대금 비율 - 변동성 국면 후보

단계: **위험/ 변동성 EDA 만** / 분류: R 국면

**전략 논리:** 공급 stock 대비소비 flow가 많으면 미래 변동성이 낮고 작으면 높다는 가설. R은 방향 부호가 없다.

**신호·계산:** integral(b*Qb+a*Qa)dt / sum_trade(price*qty); W1/5/10/30/60; train25/75%threshold 동결, 거래 0은 inactive로 분리. pilot에서 실제 체결 가격을 보존한 주 비율이며 단위 초.

**진입·주문:** R 단독 entry 없음. 저 변동시 크기/ 폭 또는 다른 directionprovider를 조절하는 후보다.

**청산·재고:** R 단독 exit 없음. 실제 inventory 감축과 독립적인 risk/ state 변수.

**EDA·실측 결과:** 정확한 pilotR: W5→H5 high/ lowRV .768, W60→H60 .864. 명목/ 수량 R 순위 상관 거의 1, 상대 차이 W5중앙.0011%, W60 .0039%.

**가능한 해설:** 관측 비율과 미래 RV의 관계는 있지만 이미 조용한 시장이 지속되는 효과와 activity를 통제하면 추가 가치가 작다.

**한계·판정:** 개인 분석 원 글은 S&P500 tick의 일별/20일 MA 라 이번 초 단위 crypto 검증과 다름. GRVT는 ratio 학습 freshtrade 부족.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md)

### F06 - 수량 stock 시간적 분/ 체결수량 비율 - 변동성 국면 후보

단계: **위험/ 변동성 EDA 만** / 분류: R 국면

**전략 논리:** 공급 stock 대비소비 flow가 많으면 미래 변동성이 낮고 작으면 높다는 가설. R은 방향 부호가 없다.

**신호·계산:** integral(Qb+Qa)dt / sum_trade(qty); W1/5/10/30/60; train25/75%threshold 동결, 거래 0은 inactive로 분리. production 집계에 가격 *수량 곱이 없어 사용한 proxy; 정확한 명목비율 아님.

**진입·주문:** R 단독 entry 없음. 저 변동시 크기/ 폭 또는 다른 directionprovider를 조절하는 후보다.

**청산·재고:** R 단독 exit 없음. 실제 inventory 감축과 독립적인 risk/ state 변수.

**EDA·실측 결과:** production 수량 R: W5→H5 high/ lowRV .375, W60→H60 .474. pastRV·flow에 R을 추가한 RV MAE 개선 W5 약.9~1.7%, W60거의 0 또는 음수. 이벤트 stock 합산의 모든 native tick 복제는 아니다.

**가능한 해설:** 관측 비율과 미래 RV의 관계는 있지만 이미 조용한 시장이 지속되는 효과와 activity를 통제하면 추가 가치가 작다.

**한계·판정:** 개인 분석 원 글은 S&P500 tick의 일별/20일 MA 라 이번 초 단위 crypto 검증과 다름. GRVT는 ratio 학습 freshtrade 부족.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md)

### F07 - high-R grid / low-R 방향성 자동 전환

단계: **복합 가설/ 부분 EDA** / 분류: R·grid 복합

**전략 논리:** highR에서는 range 회귀, lowR 에서는 OFI·공격수요·약한 refill의 방향을 따르는 두 갈래 전환.

**신호·계산:** R frozenquantiles, center/ band, OBI/ OFI/ TFI/ refill 강도와 부호.

**진입·주문:** high/ low 와각 alpha를 같이 확인한다는 제안. lowR 만으로 매수/ 매도를 결정하지 않으며 실제 통합 entryrule은 미동결.

**청산·재고:** gridrange 이탈/ center 변화, 방향 반전, holding·inventorystop을 필요로 하지만 통합 exitPnL 실험 없음.

**EDA·실측 결과:** L1 W5/ H25 방향 정렬 평균 high .187 bp/ low .134 bp, 비영 BA65.09%/55.29%; OFI high .133/ low .154 bp이나 lowH60 -.001 bp. 낮은 R에서 방향 정확도 우위는 일관되지 않음.

**가능한 해설:** 큰 변동성과 방향을 아는 것은 별개다. Rhigh를 grid 승인으로, Rlow를 directionedge로 즉시 치환할 수 없다.

**한계·판정:** regime별 강도·band 폭·독립 episode 차이가 있다. 실제 전환기 체결·cost 후 profit 미 실측.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md), [R02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/regime_direction.csv)

### F08 - 공격 체결 x 소비 측 weak refill

단계: **원시 방향·강도 평가** / 분류: flow·refill EDA

**전략 논리:** 같은 방향 공격 흐름이 강하되 소비되는 반대 호가의 보충이 약하면 가격 진행이 남는다는 가정.

**신호·계산:** signedFlow*(1-consumedSideRefillRate); buy 일때 ask, sell 일때 bid. 관련 가격 측 활동이 없으면 NaN 제외.

**진입·주문:** 원시 부호를 방향으로, train 분위수를 강도로 평가. 구체 실제 entry quote/ fee gate 미동결.

**청산·재고:** H1~180 반응 label만 계산; 실제 exit 정책 없음. 새 S3처럼 흡수 →소진의 episode 전이가 아니다.

**EDA·실측 결과:** production H25강한-약한 차이 +.061 bp; highR mean+.044 bp/ BA54.43%, lowR mean+.025 bp/ BA51.00%. lowR H60 mean-.179bp.

**가능한 해설:** 곱이 0 아닌 경우 부호는 trade 와같아서단순 direction 개선보다 강도·onset 선택의 추가 가치를 검정해야한다.

**한계·판정:** E12의(signedFlow*(askRate-bidRate))와 부호·표본 정의가 다름; netrefill로 grossabsorption을 식별하지 못함.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md), [R02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/regime_direction.csv)

### O01 - l1_obi - 독립 onset 비교 후보

단계: **가격 경로 EDA 만** / 분류: onset 변형

**전략 논리:** E01의 지속 상태 모든 anchor 대신 신호가 새롭게 극단에 진입한 시점의 이후 반응을 평가.

**신호·계산:** W5, train10/90%outside로 처음 cross; buy/ sell 합쳐 180초 cooldown. 모든 과거 60/ 미래 180초 정상 경로 필요.

**진입·주문:** onset의 부호와 정보 cutoff를 신호 시점으로 삼되 실제 makerentry·volume·feesgate는 동결하지 않음.

**청산·재고:** future1~180초 경로·favorable/ adverse 관측만. H는 청산 조건이 아니다.

**EDA·실측 결과:** production H25: 경로 median n=365.0, alignedmean=0.430 bp, alignedmedian=0.083 bp, p_flat=31.8%. pilot 비중첩 onset 대체로 경로당 7~9개.

**가능한 해설:** 평균 반응이 양수 여도 중앙 0·반대 이동·미체결이 가능하다. 같은 지속 상태를 수 천 독립 신호로 세는 오류를 줄인다.

**한계·판정:** onset은 원형 FV·새 state onsets와 문턱/ clock 이 다르며 실제 체결성과는 미측정.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md), [R03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/onset_curves.csv)

### O02 - l2_obi - 독립 onset 비교 후보

단계: **가격 경로 EDA 만** / 분류: onset 변형

**전략 논리:** E02의 지속 상태 모든 anchor 대신 신호가 새롭게 극단에 진입한 시점의 이후 반응을 평가.

**신호·계산:** W5, train10/90%outside로 처음 cross; buy/ sell 합쳐 180초 cooldown. 모든 과거 60/ 미래 180초 정상 경로 필요.

**진입·주문:** onset의 부호와 정보 cutoff를 신호 시점으로 삼되 실제 makerentry·volume·feesgate는 동결하지 않음.

**청산·재고:** future1~180초 경로·favorable/ adverse 관측만. H는 청산 조건이 아니다.

**EDA·실측 결과:** production H25: 경로 median n=255.0, alignedmean=0.385 bp, alignedmedian=0.053 bp, p_flat=27.8%. pilot 비중첩 onset 대체로 경로당 7~9개.

**가능한 해설:** 평균 반응이 양수 여도 중앙 0·반대 이동·미체결이 가능하다. 같은 지속 상태를 수 천 독립 신호로 세는 오류를 줄인다.

**한계·판정:** onset은 원형 FV·새 state onsets와 문턱/ clock 이 다르며 실제 체결성과는 미측정.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md), [R03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/onset_curves.csv)

### O03 - ofi - 독립 onset 비교 후보

단계: **가격 경로 EDA 만** / 분류: onset 변형

**전략 논리:** E04의 지속 상태 모든 anchor 대신 신호가 새롭게 극단에 진입한 시점의 이후 반응을 평가.

**신호·계산:** W5, train10/90%outside로 처음 cross; buy/ sell 합쳐 180초 cooldown. 모든 과거 60/ 미래 180초 정상 경로 필요.

**진입·주문:** onset의 부호와 정보 cutoff를 신호 시점으로 삼되 실제 makerentry·volume·feesgate는 동결하지 않음.

**청산·재고:** future1~180초 경로·favorable/ adverse 관측만. H는 청산 조건이 아니다.

**EDA·실측 결과:** production H25: 경로 median n=256.0, alignedmean=0.445 bp, alignedmedian=0.142 bp, p_flat=23.4%. pilot 비중첩 onset 대체로 경로당 7~9개.

**가능한 해설:** 평균 반응이 양수 여도 중앙 0·반대 이동·미체결이 가능하다. 같은 지속 상태를 수 천 독립 신호로 세는 오류를 줄인다.

**한계·판정:** onset은 원형 FV·새 state onsets와 문턱/ clock 이 다르며 실제 체결성과는 미측정.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md), [R03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/onset_curves.csv)

### O04 - replenishment - 독립 onset 비교 후보

단계: **가격 경로 EDA 만** / 분류: onset 변형

**전략 논리:** E05의 지속 상태 모든 anchor 대신 신호가 새롭게 극단에 진입한 시점의 이후 반응을 평가.

**신호·계산:** W5, train10/90%outside로 처음 cross; buy/ sell 합쳐 180초 cooldown. 모든 과거 60/ 미래 180초 정상 경로 필요.

**진입·주문:** onset의 부호와 정보 cutoff를 신호 시점으로 삼되 실제 makerentry·volume·feesgate는 동결하지 않음.

**청산·재고:** future1~180초 경로·favorable/ adverse 관측만. H는 청산 조건이 아니다.

**EDA·실측 결과:** production H25: 경로 median n=467.0, alignedmean=0.025 bp, alignedmedian=0.000 bp, p_flat=38.3%. pilot 비중첩 onset 대체로 경로당 7~9개.

**가능한 해설:** 평균 반응이 양수 여도 중앙 0·반대 이동·미체결이 가능하다. 같은 지속 상태를 수 천 독립 신호로 세는 오류를 줄인다.

**한계·판정:** onset은 원형 FV·새 state onsets와 문턱/ clock 이 다르며 실제 체결성과는 미측정.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md), [R03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/onset_curves.csv)

### O05 - trade_imbalance - 독립 onset 비교 후보

단계: **가격 경로 EDA 만** / 분류: onset 변형

**전략 논리:** E07의 지속 상태 모든 anchor 대신 신호가 새롭게 극단에 진입한 시점의 이후 반응을 평가.

**신호·계산:** W5, train10/90%outside로 처음 cross; buy/ sell 합쳐 180초 cooldown. 모든 과거 60/ 미래 180초 정상 경로 필요.

**진입·주문:** onset의 부호와 정보 cutoff를 신호 시점으로 삼되 실제 makerentry·volume·feesgate는 동결하지 않음.

**청산·재고:** future1~180초 경로·favorable/ adverse 관측만. H는 청산 조건이 아니다.

**EDA·실측 결과:** production H25: 경로 median n=468.0, alignedmean=0.127 bp, alignedmedian=0.000 bp, p_flat=37.0%. pilot 비중첩 onset 대체로 경로당 7~9개.

**가능한 해설:** 평균 반응이 양수 여도 중앙 0·반대 이동·미체결이 가능하다. 같은 지속 상태를 수 천 독립 신호로 세는 오류를 줄인다.

**한계·판정:** onset은 원형 FV·새 state onsets와 문턱/ clock 이 다르며 실제 체결성과는 미측정.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md), [R03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/onset_curves.csv)

### O06 - signed_trade_flow - 독립 onset 비교 후보

단계: **가격 경로 EDA 만** / 분류: onset 변형

**전략 논리:** E08의 지속 상태 모든 anchor 대신 신호가 새롭게 극단에 진입한 시점의 이후 반응을 평가.

**신호·계산:** W5, train10/90%outside로 처음 cross; buy/ sell 합쳐 180초 cooldown. 모든 과거 60/ 미래 180초 정상 경로 필요.

**진입·주문:** onset의 부호와 정보 cutoff를 신호 시점으로 삼되 실제 makerentry·volume·feesgate는 동결하지 않음.

**청산·재고:** future1~180초 경로·favorable/ adverse 관측만. H는 청산 조건이 아니다.

**EDA·실측 결과:** production H25: 경로 median n=345.0, alignedmean=0.252 bp, alignedmedian=0.000 bp, p_flat=26.0%. pilot 비중첩 onset 대체로 경로당 7~9개.

**가능한 해설:** 평균 반응이 양수 여도 중앙 0·반대 이동·미체결이 가능하다. 같은 지속 상태를 수 천 독립 신호로 세는 오류를 줄인다.

**한계·판정:** onset은 원형 FV·새 state onsets와 문턱/ clock 이 다르며 실제 체결성과는 미측정.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md), [R03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/onset_curves.csv)

### O07 - aggressive_weak_refill - 독립 onset 비교 후보

단계: **가격 경로 EDA 만** / 분류: onset 변형

**전략 논리:** F08의 지속 상태 모든 anchor 대신 신호가 새롭게 극단에 진입한 시점의 이후 반응을 평가.

**신호·계산:** W5, train10/90%outside로 처음 cross; buy/ sell 합쳐 180초 cooldown. 모든 과거 60/ 미래 180초 정상 경로 필요.

**진입·주문:** onset의 부호와 정보 cutoff를 신호 시점으로 삼되 실제 makerentry·volume·feesgate는 동결하지 않음.

**청산·재고:** future1~180초 경로·favorable/ adverse 관측만. H는 청산 조건이 아니다.

**EDA·실측 결과:** production H25: 경로 median n=351.0, alignedmean=0.234 bp, alignedmedian=0.000 bp, p_flat=26.8%. pilot 비중첩 onset 대체로 경로당 7~9개.

**가능한 해설:** 평균 반응이 양수 여도 중앙 0·반대 이동·미체결이 가능하다. 같은 지속 상태를 수 천 독립 신호로 세는 오류를 줄인다.

**한계·판정:** onset은 원형 FV·새 state onsets와 문턱/ clock 이 다르며 실제 체결성과는 미측정.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md), [R03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/onset_curves.csv)

### O08 - 신호 절댓값 강도·signed decile 후보

단계: **반응 EDA 만** / 분류: 강도 변형

**전략 논리:** 강한 신호가 약한 신호보다 큰 directional move를 만드는지 평가하고 작은 신호를 필터할 가능성을 검토.

**신호·계산:** 7 지표 W5의 train signed deciles 와절댓값하위/ 상위 20%; 평가에서 bin 재학습 없음.

**진입·주문:** 강도 filter 후보이나 actualentry gate 미동결. 부호 정렬 후 동일 표본 기준 비교.

**청산·재고:** 각초 반응 분포 평가만; exit·fee 후 손익 없음.

**EDA·실측 결과:** H25strong-weak: L1 .324, L2 .288, OFI .253, refill .044, signedtrade .003, tradeimbalance .115, weakrefill .061 bp.

**가능한 해설:** 강도는 일부 지표의 규모 정보를 보였지만 표본 선택·무변화·cost·fill 후 markout을 같이 보아야한다.

**한계·판정:** Discrete 지표는빈 bin이 있다. 강한 표본을 사후 고른 수익 선택으로 사용하지 않음.

근거: [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md)

### G01 - 상태 개정 S1 순유동성 OFI

단계: **역사 native replay/ 새 live 운용 없음** / 분류: 2026-10-07 state

**전략 논리:** OFI를 점 FV 대신 방향·onset·지속 상태로 읽는다.

**신호·계산:** asinh(OFI/ TWdepth), train absq90로 score를[-1,1]clip; abs<.4 threshold 중립. 비 0 sign 전환 onset, 지속 시간, OFI 차분 가속기록.

**진입·주문:** defaultW5의 ready state, score 방향 한쪽 maker. h=max(spread/2, tick/2), center=mid+tick*score-2tick*normalizedInventory; 반대 BBO와 cross 하지 않게 tickround. thinbook 폭 2 배·크기.5, jump 신규 차단. cap150USDT/ depth10%, flat에서만 신규, model.valid 필요.

**청산·재고:** 재고 발생 후 reduce-onlymaker를 우선. longask/ shortbid를 BBO 근처로 감축; maxhold120초·adverse.35%·DD20·stale/ grace/ closing은 riskmakerunwind. emergencytaker 기본 false 이며즉시 hardstop·완전 shutdown을 보장하지 않음.

**EDA·실측 결과:** 10분 학습 →30분 native 평가, 세 queue/ latency 조건. 아래 C1단독 경로 실측과 부록 전체 3 조건. S2모두 0 의도; trade 한 단독 7 경로 전부 비용 후손 실. 기존 계수 FV를 제거했지만 미래 방향 확률·이동 규모는 학습하지 않음.

**가능한 해설:** 코드 수정·체결 발생은 실행 진전이 고수익성 확인은 아니다. 이미 본 기간의 단일 fold이며 normalmaker 폭을 비용만큼 벌리던 구형 정책도 함께 변경돼 alpha 수정 효과를 단독 식별하지 못한다.

**한계·판정:** 새미관측날짜·실측 latency·계정 specificfee·funding·USD/ USDT 미포함. Magnitude state는 관측 RV이며 미래 이동 모델이 아님.

| Venue | 자료 valid | 의도 | Fill | Gross | Fees | Net | 잔여 재고 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| arcus | True | 340 | 19 | -0.00729 | 0.00000 | -0.00729 | 0.00000000 |
| bybit | True | 107 | 6 | -0.02840 | 0.10244 | -0.13084 | 0.00000000 |
| bitget | True | 353 | 56 | -0.18797 | 0.34468 | -0.53265 | 0.00000000 |
| grvt | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |


근거: [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json), [M03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/run.sqlite), [M05](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/STRATEGY_SPEC.md), [C03](C:/Users/박제형/Desktop/VibeTrading/strategy/s1_net_liquidity.py)

### G02 - 상태 개정 S2 외부 기준 가격

단계: **역사 native replay/ 새 live 운용 없음** / 분류: 2026-10-07 state

**전략 논리:** basis 괴리와발생 origin을 분리해 로컬 leg 이익이 가능한 dislocation을 평가.

**신호·계산:** spreadbp=1 e4log(local/ Binance); residual=spread-priorrollingmedian; 최근 1초 ref/ local 이동으로 origin. ADF<.05·KPSS>.05·연속 200, refage1.5s·sourceskew500ms, drift<=max(5 bp,6 MAD).

**진입·주문:** defaultW5의 ready state, score 방향 한쪽 maker. h=max(spread/2, tick/2), center=mid+tick*score-2tick*normalizedInventory; 반대 BBO와 cross 하지 않게 tickround. thinbook 폭 2 배·크기.5, jump 신규 차단. cap150USDT/ depth10%, flat에서만 신규, model.valid 필요. absresidual>max(trainq90, maker 왕복 +1 bp)·origin 방향 정합도 필요.

**청산·재고:** residual 수축과 실제 local PnL이 각각 비용을 초과할 때 최초 filledqty 절반 TP1; 절반 이하에서 반복 안 함. residual<=.5 bp이고 이익 조건 충족시 TP2. risk/ time은 profitgate 우 회 maker. 재고 발생 후 reduce-onlymaker를 우선. longask/ shortbid를 BBO 근처로 감축; maxhold120초·adverse.35%·DD20·stale/ grace/ closing은 riskmakerunwind. emergencytaker 기본 false 이며즉시 hardstop·완전 shutdown을 보장하지 않음.

**EDA·실측 결과:** 10분 학습 →30분 native 평가, 세 queue/ latency 조건. 아래 C1단독 경로 실측과 부록 전체 3 조건. S2모두 0 의도; trade 한 단독 7 경로 전부 비용 후손 실. spread 수축과미헤지 localPnL을 분리; origin 휴리스틱의 인과적 식별 없음.

**가능한 해설:** 코드 수정·체결 발생은 실행 진전이 고수익성 확인은 아니다. 이미 본 기간의 단일 fold이며 normalmaker 폭을 비용만큼 벌리던 구형 정책도 함께 변경돼 alpha 수정 효과를 단독 식별하지 못한다.

**한계·판정:** 새미관측날짜·실측 latency·계정 specificfee·funding·USD/ USDT 미포함. Magnitude state는 관측 RV이며 미래 이동 모델이 아님.

| Venue | 자료 valid | 의도 | Fill | Gross | Fees | Net | 잔여 재고 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| arcus | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| bybit | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| bitget | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| grvt | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |


근거: [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json), [M03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/run.sqlite), [M05](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/STRATEGY_SPEC.md), [C04](C:/Users/박제형/Desktop/VibeTrading/strategy/s2_external_anchor.py)

### G03 - 상태 개정 S3 공격 체결 흐름

단계: **역사 native replay/ 새 live 운용 없음** / 분류: 2026-10-07 state

**전략 논리:** 공격 flow 이후 흡수와 공급/ 공격자 소진의 순서를 확인한다.

**신호·계산:** knownTI>=max(.3, trainq90), 창 flow>=L1depth→PRESSURE; episode 후 flow>=초기 consumedDepth, refill/ flow>=.5, move<1 tick→ABSORPTION. episode30초.

**진입·주문:** defaultW5의 ready state, score 방향 한쪽 maker. h=max(spread/2, tick/2), center=mid+tick*score-2tick*normalizedInventory; 반대 BBO와 cross 하지 않게 tickround. thinbook 폭 2 배·크기.5, jump 신규 차단. cap150USDT/ depth10%, flat에서만 신규, model.valid 필요.

**청산·재고:** 재고 발생 후 reduce-onlymaker를 우선. longask/ shortbid를 BBO 근처로 감축; maxhold120초·adverse.35%·DD20·stale/ grace/ closing은 riskmakerunwind. emergencytaker 기본 false 이며즉시 hardstop·완전 shutdown을 보장하지 않음.

**EDA·실측 결과:** 10분 학습 →30분 native 평가, 세 queue/ latency 조건. 아래 C1단독 경로 실측과 부록 전체 3 조건. S2모두 0 의도; trade 한 단독 7 경로 전부 비용 후손 실. 체결 부호 단독에서 사건 전이로 변경했으나 netrefill로 gross 공급을 식별하지 않음.

**가능한 해설:** 코드 수정·체결 발생은 실행 진전이 고수익성 확인은 아니다. 이미 본 기간의 단일 fold이며 normalmaker 폭을 비용만큼 벌리던 구형 정책도 함께 변경돼 alpha 수정 효과를 단독 식별하지 못한다.

**한계·판정:** 새미관측날짜·실측 latency·계정 specificfee·funding·USD/ USDT 미포함. Magnitude state는 관측 RV이며 미래 이동 모델이 아님.

| Venue | 자료 valid | 의도 | Fill | Gross | Fees | Net | 잔여 재고 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| arcus | True | 13 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| bybit | True | 32 | 21 | -0.09000 | 0.21842 | -0.30842 | 0.00000000 |
| bitget | True | 114 | 24 | 0.01540 | 0.26191 | -0.24651 | 0.00000000 |
| grvt | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |


근거: [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json), [M03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/run.sqlite), [M05](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/STRATEGY_SPEC.md), [C05](C:/Users/박제형/Desktop/VibeTrading/strategy/s3_aggressive_flow.py)

### G04 - 상태 개정 S4 L1 OBI

단계: **역사 native replay/ 새 live 운용 없음** / 분류: 2026-10-07 state

**전략 논리:** L1 OBI를 주 방향 상태로 사용하고 L2는 보조 진단으로 둔다.

**신호·계산:** trainL1 absq90 score, score.9에서 STRONG,.4 threshold 미만 neutral. L2top10OBI·levelgap·RV를 공통 engine에 보관.

**진입·주문:** defaultW5의 ready state, score 방향 한쪽 maker. h=max(spread/2, tick/2), center=mid+tick*score-2tick*normalizedInventory; 반대 BBO와 cross 하지 않게 tickround. thinbook 폭 2 배·크기.5, jump 신규 차단. cap150USDT/ depth10%, flat에서만 신규, model.valid 필요.

**청산·재고:** 재고 발생 후 reduce-onlymaker를 우선. longask/ shortbid를 BBO 근처로 감축; maxhold120초·adverse.35%·DD20·stale/ grace/ closing은 riskmakerunwind. emergencytaker 기본 false 이며즉시 hardstop·완전 shutdown을 보장하지 않음.

**EDA·실측 결과:** 10분 학습 →30분 native 평가, 세 queue/ latency 조건. 아래 C1단독 경로 실측과 부록 전체 3 조건. S2모두 0 의도; trade 한 단독 7 경로 전부 비용 후손 실. 원시 방향 정보와 수수료를 초과하는 maker 체결 edge를 분리.

**가능한 해설:** 코드 수정·체결 발생은 실행 진전이 고수익성 확인은 아니다. 이미 본 기간의 단일 fold이며 normalmaker 폭을 비용만큼 벌리던 구형 정책도 함께 변경돼 alpha 수정 효과를 단독 식별하지 못한다.

**한계·판정:** 새미관측날짜·실측 latency·계정 specificfee·funding·USD/ USDT 미포함. Magnitude state는 관측 RV이며 미래 이동 모델이 아님.

| Venue | 자료 valid | 의도 | Fill | Gross | Fees | Net | 잔여 재고 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| arcus | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| bybit | True | 339 | 42 | 0.00177 | 0.23470 | -0.23293 | 0.00000000 |
| bitget | True | 443 | 13 | -0.04390 | 0.12122 | -0.16512 | 0.00000000 |
| grvt | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |


근거: [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json), [M03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/run.sqlite), [M05](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/STRATEGY_SPEC.md), [C06](C:/Users/박제형/Desktop/VibeTrading/strategy/s4_obi.py)

### G05 - S2 Binance-leads 로컬 catch-up 분기

단계: **구현/ entry 표본 없음** / 분류: state 하위 후보

**전략 논리:** reference가 먼저 움직이고 local 이 따라오지 못한 괴리가 local 방향 이동으로 메워지는 경우.

**신호·계산:** |ref1sreturn|>=1 bp, |localreturn|<.5 |refreturn|; residualsign과 reference 방향 정합. stationarity/ freshness/ cost 공통 gate.

**진입·주문:** local 저평가 residual<0·ref 상승이면 localBuymaker; 대칭 short. 같은 assetref 관측만으로 hedge 포지션을 만들지 않음.

**청산·재고:** G02의 localprofit 조건 TP1/ TP2및 time/ riskmaker 감축.

**EDA·실측 결과:** 최종 4 venueS2가 모두 0 의도/0 체결. origin 상태 관측과 actualentry를 구분; 분기별 왕복 PnL 없음.

**가능한 해설:** Binance가 모든 시간 항상 lead 한다는 가정은 하지 않으며 timestamp 정렬과 가격 반응 완료 여부가 중요.

**한계·판정:** commonshock·basis 변화·얕은 흐름의 noise가 origin 분류를 오염할 수 있음.

근거: [C04](C:/Users/박제형/Desktop/VibeTrading/strategy/s2_external_anchor.py), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### G06 - S2 local-idiosyncratic 로컬 되돌림 분기

단계: **구현/ entry 표본 없음** / 분류: state 하위 후보

**전략 논리:** local 이 독자적으로 움직였으나 reference는 안 움직였고 괴리가 local 되돌림으로 해소될 가능성을 본다.

**신호·계산:** |local1sreturn|>=1 bp, |refreturn|<.5 |localreturn|; residual과 localimpulse 반대 방향 entry.

**진입·주문:** local 급등·양 residual 이면 makerSell, local 급락·음 residual 이면 makerBuy; G02공통 stationarity/ cost/ clockgate.

**청산·재고:** residual 수 축만으로 profit 으로 부르지 않고 actuallocalleg 이익을 같이 요구.

**EDA·실측 결과:** 분기별 actualtrade/ 독립 episodeexit 성과 없음. 원형 S2학습 계수 분해는 기계적 수렴 분기의 수익 증거가 아님.

**가능한 해설:** reference 가뒤늦게 local을 따라 오면 residual 이 축소돼 도내 로컬 포지션은 수익을 얻지 못할 수 있다.

**한계·판정:** local의 새 정보와 일시적 압력을 실시간으로 완전히 구별하지 못함.

근거: [C04](C:/Users/박제형/Desktop/VibeTrading/strategy/s2_external_anchor.py), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json), [D01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/REPORT_KO.md)

### G07 - S3 흡수 후 공급 소진 breakout 분기

단계: **구현/ 전체 S3 replay에 포함** / 분류: state 하위 후보

**전략 논리:** 흡수가 유지되다가 소비되는 측의 재공급이 약해지고 가격이 전진하면 같은 방향 수요의 돌파를 노린다.

**신호·계산:** ABSORPTION 이후 동 방향 TI 유지, 누적 refill/ episodeflow<.3, 가격>=1 tick 진행, 가시적 depletion>0.

**진입·주문:** buyepisode의 ask 공급 소진이면 Buy, sell episode의 bid 공급 소진이면 Sellmaker. pressure 만으로 전이하지 않음.

**청산·재고:** 재고 발생 후 reduce-onlymaker를 우선. longask/ shortbid를 BBO 근처로 감축; maxhold120초·adverse.35%·DD20·stale/ grace/ closing은 riskmakerunwind. emergencytaker 기본 false 이며즉시 hardstop·완전 shutdown을 보장하지 않음.

**EDA·실측 결과:** G03전체상태기계 PnL에 포함되지만 해당 분기만 고립한 충분한 episode/ PnL 표는 없다.

**가능한 해설:** 단순 TI와 동일하지 않은 이벤트 가설. 공급의 완전 gross 관측이 없어 전이를 proxy로 명시한다.

**한계·판정:** 누적 refillratio·창 peakflow·반영 tick의 관측 delay와 level 범위 절단에 민감.

근거: [C05](C:/Users/박제형/Desktop/VibeTrading/strategy/s3_aggressive_flow.py), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### G08 - S3 흡수 후 공격자 소진 reversal 분기

단계: **구현/ 전체 S3 replay에 포함** / 분류: state 하위 후보

**전략 논리:** 압력이 공급에 흡수된 후 공격 flow가 약해지고 가격이 진행하지 않으면 반대 방향을 노린다.

**신호·계산:** ABSORPTION에서 currentflow<episodepeak*.5, visible_refill>0, 가격 진행<1 tick.

**진입·주문:** buy 공격자 소진이면 Sell, sell 공격자 소진이면 Buymaker. unknown이나 warmup은 episode 초기화.

**청산·재고:** 재고 발생 후 reduce-onlymaker를 우선. longask/ shortbid를 BBO 근처로 감축; maxhold120초·adverse.35%·DD20·stale/ grace/ closing은 riskmakerunwind. emergencytaker 기본 false 이며즉시 hardstop·완전 shutdown을 보장하지 않음.

**EDA·실측 결과:** G03결과에 포함. 별도 reversal 분기의새날짜 독립 검증과 최종 왕복 수익은 없음.

**가능한 해설:** 공격 trade의 원래 부호와 반대 entry 이므로 G07의 돌파와 동일 전략으로 합쳐 해석하지 않는다.

**한계·판정:** peak의 fixedW 측정과 episode 누적 측정이 다르며 full 흡수와우연한 flow 감소를 완전히 구분하지 못함.

근거: [C05](C:/Users/박제형/Desktop/VibeTrading/strategy/s3_aggressive_flow.py), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### G09 - 극단 OBI·OFI·levelgap/ spread 확대 Jump Risk MM

단계: **구현/ 통합 비교에 포함** / 분류: 위험 정책 후보

**전략 논리:** 한쪽 queue가 급격히 취약해지고 spread 나 levelgap 이커지면유해한 fill과 inventorytail을 억제한다.

**신호·계산:** |OBI|>.85, |OFI|>1.5 및 nextlevelgap>=3 ticks 또는 spread>train90%. R 낮음·RV 높음은 THIN_BOOK.

**진입·주문:** JUMP_RISK는 신규 노출 없음; THIN_BOOK은 폭 2 배·크기.5. 단독 directionalpha가 아님.

**청산·재고:** 재고 있으면 반대 side의 reduce-onlymaker를 유지/ 가깝게 조정. 단순 폭 확대가 실제 감축은 아니다.

**EDA·실측 결과:** volatility/ inventory ablation과 단독 state에 포함. jump 만추가 한 독립 counterfactualP&L 없음.

**가능한 해설:** risk 설명 변수의 조건부 타당성과 수익 예측을 분리한다. maker-onlyriskstop은 즉시 체결 보장 없음.

**한계·판정:** jump 문턱은 초기 동결 규칙이며 미래 jump 확률 calibration은 미완료.

근거: [M05](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/STRATEGY_SPEC.md), [M07](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/RISK_CONTROLS.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### G10 - Direction x Magnitude x Volatility x Inventory 통합 구조

단계: **구현된 부분과 미학습 부분 구분** / 분류: 통합 설계

**전략 논리:** 각축의 역할을 방향·폭/ 크기·위험·감축으로 분리하고 점 FV 대신 주문 의도를 산출한다.

**신호·계산:** S4/ S1/ S3/ S2 direction score, observedRV의 FLAT/ SMALL/ ACTIVE, R/ spread/ gap risk, actualfilledinventory.

**진입·주문:** 선택 모듈 score 가중 평균, S4.45/ S1.30/ S3.15/ S2.10; |합성 |<.2 neutral. 미래규모예측을배워 tickedge에 대입한 구조는 아직 아님.

**청산·재고:** inventory skew 최대 2 ticks와 reduce-onlymaker. closing·손실·holding 위험은 override.

**EDA·실측 결과:** H01~H07증분 비교로 구현 상태를 평가. 현재 forecast 확률/ 규모 calibration·multi-fold 검증 미완료.

**가능한 해설:** 수익 모델이 완성됐다는 뜻이 아니며 공통 엔진과 모듈 ablation을 실행 가능하게한 architecture 다.

**한계·판정:** 관측된 RV 상태를 futuremove 모델로 포장하지 않음. 모든 축의 실제 필요성은 새기 간의 동일 asset 비교 필요.

근거: [M05](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/STRATEGY_SPEC.md), [C07](C:/Users/박제형/Desktop/VibeTrading/vibetrading/mm_state/coordinator.py), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### G11 - 가격 변화 유무·활동 확률 - 후속 학습 후보

단계: **논의/ 고유 학습·운용 없음** / 분류: 후속 목표 분해

**전략 논리:** 작은 신호와 무변화가 많은 상황을 먼저 분리한다.

**신호·계산:** P(mid_change by H) 또는 무변화 확률; OBI/ OFI·flow·depth·volatility state는 입력 후보이며 최종 특징·계수·loss·threshold는 미동결.

**진입·주문:** 확률·규모·시간을 quote 정책에 반영하는 제안이나 독립 주문 규칙은 아직 없음.

**청산·재고:** 고유 exit 정책과 왕복 PnL 없음. future horizon과 holdings를 자동 동일시하지 않음.

**EDA·실측 결과:** 가격 변화/ 부호/ 규모와 현재 RV·무변화 빈도는 관측했지만 이 고유 모델을 fit 하여 운용한 기록은 없다.

**가능한 해설:** 상태 기반 G10이 이러한 학습 모델까지 완료한 것은 아니다. 목적 변수를 나눠야한다는 연구 제안을 별도 등록한다.

**한계·판정:** 기존 indicator regression 또는 observation RV 결과를 이 모델의 성과로 대체하지 않는다.

근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [D01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/REPORT_KO.md), [M05](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/STRATEGY_SPEC.md)

### G12 - 다음 가격 변화 방향 분류 - 후속 학습 후보

단계: **논의/ 고유 학습·운용 없음** / 분류: 후속 목표 분해

**전략 논리:** fixed30초 전체 이동 량 대신 다음 변화 방향을 예측한다.

**신호·계산:** 다음 mid가 up/ down 일 조건부 확률; OBI/ OFI·flow·depth·volatility state는 입력 후보이며 최종 특징·계수·loss·threshold는 미동결.

**진입·주문:** 확률·규모·시간을 quote 정책에 반영하는 제안이나 독립 주문 규칙은 아직 없음.

**청산·재고:** 고유 exit 정책과 왕복 PnL 없음. future horizon과 holdings를 자동 동일시하지 않음.

**EDA·실측 결과:** 가격 변화/ 부호/ 규모와 현재 RV·무변화 빈도는 관측했지만 이 고유 모델을 fit 하여 운용한 기록은 없다.

**가능한 해설:** 상태 기반 G10이 이러한 학습 모델까지 완료한 것은 아니다. 목적 변수를 나눠야한다는 연구 제안을 별도 등록한다.

**한계·판정:** 기존 indicator regression 또는 observation RV 결과를 이 모델의 성과로 대체하지 않는다.

근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [D01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/REPORT_KO.md), [M05](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/STRATEGY_SPEC.md)

### G13 - 다음 가격 변화까지의 시간 - 후속 학습 후보

단계: **논의/ 고유 학습·운용 없음** / 분류: 후속 목표 분해

**전략 논리:** 정보가 얼마나 오래 남고 제출 지연 뒤 이용 가능한지 다룬다.

**신호·계산:** time-to-next-change 또는 price event hazard; OBI/ OFI·flow·depth·volatility state는 입력 후보이며 최종 특징·계수·loss·threshold는 미동결.

**진입·주문:** 확률·규모·시간을 quote 정책에 반영하는 제안이나 독립 주문 규칙은 아직 없음.

**청산·재고:** 고유 exit 정책과 왕복 PnL 없음. future horizon과 holdings를 자동 동일시하지 않음.

**EDA·실측 결과:** 가격 변화/ 부호/ 규모와 현재 RV·무변화 빈도는 관측했지만 이 고유 모델을 fit 하여 운용한 기록은 없다.

**가능한 해설:** 상태 기반 G10이 이러한 학습 모델까지 완료한 것은 아니다. 목적 변수를 나눠야한다는 연구 제안을 별도 등록한다.

**한계·판정:** 기존 indicator regression 또는 observation RV 결과를 이 모델의 성과로 대체하지 않는다.

근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [D01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/REPORT_KO.md), [M05](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/STRATEGY_SPEC.md)

### G14 - 방향 조건부 이동 규모 모델 - 후속 학습 후보

단계: **논의/ 고유 학습·운용 없음** / 분류: 후속 목표 분해

**전략 논리:** 방향을 알 때 충분한 tick/ bp가 비용 뒤 남는지를 별도 추정한다.

**신호·계산:** E[abs move|direction, state, H] 및 경험적 quantile; OBI/ OFI·flow·depth·volatility state는 입력 후보이며 최종 특징·계수·loss·threshold는 미동결.

**진입·주문:** 확률·규모·시간을 quote 정책에 반영하는 제안이나 독립 주문 규칙은 아직 없음.

**청산·재고:** 고유 exit 정책과 왕복 PnL 없음. future horizon과 holdings를 자동 동일시하지 않음.

**EDA·실측 결과:** 가격 변화/ 부호/ 규모와 현재 RV·무변화 빈도는 관측했지만 이 고유 모델을 fit 하여 운용한 기록은 없다.

**가능한 해설:** 상태 기반 G10이 이러한 학습 모델까지 완료한 것은 아니다. 목적 변수를 나눠야한다는 연구 제안을 별도 등록한다.

**한계·판정:** 기존 indicator regression 또는 observation RV 결과를 이 모델의 성과로 대체하지 않는다.

근거: [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md), [D01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/REPORT_KO.md), [M05](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/STRATEGY_SPEC.md)

### H01 - 통합 static - 가격 신호 없는 BBO 기본 MM

단계: **동일 자산 3 조 건 native replay** / 분류: 통합 ablation

**전략 논리:** 동일 Bybit BTC 에서각추가 모듈의 가치를 비교한다. 기존 서로 다른 asset 의단독 S1~S4성과를 순위로 비교하지 않는다.

**신호·계산:** BBO, alpha score 없음.

**진입·주문:** flat 시 BBO buy/ sell 두 side, 재고/ holding/ DD 한 도 공통. 무제한 양방향 inventoryMM은 아님.

**청산·재고:** 재고가 생기면 반대 BBO의 STATIC_MAKER_UNWIND; forcehold/ risk 감축 공통.

**EDA·실측 결과:** 아래동일 BybitBTC의 3 가상 시나리오. fill0을 DD0우수 성과로 선택하지 않음. inventory는 flat-onlyentry 때문에 주로 exit 정책을 바꾼다.

**가능한 해설:** 모듈을 추가할수록 항상 좋아진 증거는 없다. profile은 별도 가상 대안이며 69 케이스 합산 profit은 실 계좌 수익이 아니다.

**한계·판정:** 기본 가중치·neutral 문턱·정상 quote 폭도 작용해 각 모듈의 효과를 완전히 인과 식별하지 못한다. 일부 profile0의도는 기본 spreadgate와 tick/ minimum의 상호 작용일 수 있다.

| 설정 | 의도 | Fill | Gross | Fees | Net | MaxDD | EndBTC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C1 | 353 | 103 | -0.70665 | 1.75782 | -2.46447 | 2.46447 | -0.001000 |
| O1 | 376 | 125 | -0.74535 | 2.13325 | -2.87860 | 2.87860 | -0.001000 |
| C4 | 350 | 101 | -0.71085 | 1.72368 | -2.43453 | 2.43453 | -0.001000 |


근거: [C07](C:/Users/박제형/Desktop/VibeTrading/vibetrading/mm_state/coordinator.py), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### H02 - 통합 obi - L1 OBI 통합 기준

단계: **동일 자산 3 조 건 native replay** / 분류: 통합 ablation

**전략 논리:** 동일 Bybit BTC 에서각추가 모듈의 가치를 비교한다. 기존 서로 다른 asset 의단독 S1~S4성과를 순위로 비교하지 않는다.

**신호·계산:** 선택 모듈 =S4; 선택 가중치 합으로 정규화·|score|<.2 neutral. R/ RV/ gap/ OFI 기반 risk를 제거하는 ablation 이나 spread 기본 위험과 hardlimits는 유지.

**진입·주문:** defaultW5의 ready state, score 방향 한쪽 maker. h=max(spread/2, tick/2), center=mid+tick*score-2tick*normalizedInventory; 반대 BBO와 cross 하지 않게 tickround. thinbook 폭 2 배·크기.5, jump 신규 차단. cap150USDT/ depth10%, flat에서만 신규, model.valid 필요. 비 volatilityprofile은 riskfeature를 고정해 비교.

**청산·재고:** 재고 발생 후 reduce-onlymaker를 우선. longask/ shortbid를 BBO 근처로 감축; maxhold120초·adverse.35%·DD20·stale/ grace/ closing은 riskmakerunwind. emergencytaker 기본 false 이며즉시 hardstop·완전 shutdown을 보장하지 않음. directional center는 유지 하나 inventory center/ 감축 비대칭은 제거.

**EDA·실측 결과:** 아래동일 BybitBTC의 3 가상 시나리오. fill0을 DD0우수 성과로 선택하지 않음. inventory는 flat-onlyentry 때문에 주로 exit 정책을 바꾼다.

**가능한 해설:** 모듈을 추가할수록 항상 좋아진 증거는 없다. profile은 별도 가상 대안이며 69 케이스 합산 profit은 실 계좌 수익이 아니다.

**한계·판정:** 기본 가중치·neutral 문턱·정상 quote 폭도 작용해 각 모듈의 효과를 완전히 인과 식별하지 못한다. 일부 profile0의도는 기본 spreadgate와 tick/ minimum의 상호 작용일 수 있다.

| 설정 | 의도 | Fill | Gross | Fees | Net | MaxDD | EndBTC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C1 | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.000000 |
| O1 | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.000000 |
| C4 | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.000000 |


근거: [C07](C:/Users/박제형/Desktop/VibeTrading/vibetrading/mm_state/coordinator.py), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### H03 - 통합 book - OBI + OFI book pressure

단계: **동일 자산 3 조 건 native replay** / 분류: 통합 ablation

**전략 논리:** 동일 Bybit BTC 에서각추가 모듈의 가치를 비교한다. 기존 서로 다른 asset 의단독 S1~S4성과를 순위로 비교하지 않는다.

**신호·계산:** 선택 모듈 =S4, S1; 선택 가중치 합으로 정규화·|score|<.2 neutral. R/ RV/ gap/ OFI 기반 risk를 제거하는 ablation 이나 spread 기본 위험과 hardlimits는 유지.

**진입·주문:** defaultW5의 ready state, score 방향 한쪽 maker. h=max(spread/2, tick/2), center=mid+tick*score-2tick*normalizedInventory; 반대 BBO와 cross 하지 않게 tickround. thinbook 폭 2 배·크기.5, jump 신규 차단. cap150USDT/ depth10%, flat에서만 신규, model.valid 필요. 비 volatilityprofile은 riskfeature를 고정해 비교.

**청산·재고:** 재고 발생 후 reduce-onlymaker를 우선. longask/ shortbid를 BBO 근처로 감축; maxhold120초·adverse.35%·DD20·stale/ grace/ closing은 riskmakerunwind. emergencytaker 기본 false 이며즉시 hardstop·완전 shutdown을 보장하지 않음. directional center는 유지 하나 inventory center/ 감축 비대칭은 제거.

**EDA·실측 결과:** 아래동일 BybitBTC의 3 가상 시나리오. fill0을 DD0우수 성과로 선택하지 않음. inventory는 flat-onlyentry 때문에 주로 exit 정책을 바꾼다.

**가능한 해설:** 모듈을 추가할수록 항상 좋아진 증거는 없다. profile은 별도 가상 대안이며 69 케이스 합산 profit은 실 계좌 수익이 아니다.

**한계·판정:** 기본 가중치·neutral 문턱·정상 quote 폭도 작용해 각 모듈의 효과를 완전히 인과 식별하지 못한다. 일부 profile0의도는 기본 spreadgate와 tick/ minimum의 상호 작용일 수 있다.

| 설정 | 의도 | Fill | Gross | Fees | Net | MaxDD | EndBTC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C1 | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.000000 |
| O1 | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.000000 |
| C4 | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.000000 |


근거: [C07](C:/Users/박제형/Desktop/VibeTrading/vibetrading/mm_state/coordinator.py), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### H04 - 통합 flow - book + absorption/ exhaustion

단계: **동일 자산 3 조 건 native replay** / 분류: 통합 ablation

**전략 논리:** 동일 Bybit BTC 에서각추가 모듈의 가치를 비교한다. 기존 서로 다른 asset 의단독 S1~S4성과를 순위로 비교하지 않는다.

**신호·계산:** 선택 모듈 =S4, S1, S3; 선택 가중치 합으로 정규화·|score|<.2 neutral. R/ RV/ gap/ OFI 기반 risk를 제거하는 ablation 이나 spread 기본 위험과 hardlimits는 유지.

**진입·주문:** defaultW5의 ready state, score 방향 한쪽 maker. h=max(spread/2, tick/2), center=mid+tick*score-2tick*normalizedInventory; 반대 BBO와 cross 하지 않게 tickround. thinbook 폭 2 배·크기.5, jump 신규 차단. cap150USDT/ depth10%, flat에서만 신규, model.valid 필요. 비 volatilityprofile은 riskfeature를 고정해 비교.

**청산·재고:** 재고 발생 후 reduce-onlymaker를 우선. longask/ shortbid를 BBO 근처로 감축; maxhold120초·adverse.35%·DD20·stale/ grace/ closing은 riskmakerunwind. emergencytaker 기본 false 이며즉시 hardstop·완전 shutdown을 보장하지 않음. directional center는 유지 하나 inventory center/ 감축 비대칭은 제거.

**EDA·실측 결과:** 아래동일 BybitBTC의 3 가상 시나리오. fill0을 DD0우수 성과로 선택하지 않음. inventory는 flat-onlyentry 때문에 주로 exit 정책을 바꾼다.

**가능한 해설:** 모듈을 추가할수록 항상 좋아진 증거는 없다. profile은 별도 가상 대안이며 69 케이스 합산 profit은 실 계좌 수익이 아니다.

**한계·판정:** 기본 가중치·neutral 문턱·정상 quote 폭도 작용해 각 모듈의 효과를 완전히 인과 식별하지 못한다. 일부 profile0의도는 기본 spreadgate와 tick/ minimum의 상호 작용일 수 있다.

| 설정 | 의도 | Fill | Gross | Fees | Net | MaxDD | EndBTC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C1 | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.000000 |
| O1 | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.000000 |
| C4 | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.000000 |


근거: [C07](C:/Users/박제형/Desktop/VibeTrading/vibetrading/mm_state/coordinator.py), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### H05 - 통합 cross - flow + cross-venue origin

단계: **동일 자산 3 조 건 native replay** / 분류: 통합 ablation

**전략 논리:** 동일 Bybit BTC 에서각추가 모듈의 가치를 비교한다. 기존 서로 다른 asset 의단독 S1~S4성과를 순위로 비교하지 않는다.

**신호·계산:** 선택 모듈 =S4, S1, S3, S2; 선택 가중치 합으로 정규화·|score|<.2 neutral. R/ RV/ gap/ OFI 기반 risk를 제거하는 ablation 이나 spread 기본 위험과 hardlimits는 유지.

**진입·주문:** defaultW5의 ready state, score 방향 한쪽 maker. h=max(spread/2, tick/2), center=mid+tick*score-2tick*normalizedInventory; 반대 BBO와 cross 하지 않게 tickround. thinbook 폭 2 배·크기.5, jump 신규 차단. cap150USDT/ depth10%, flat에서만 신규, model.valid 필요. 비 volatilityprofile은 riskfeature를 고정해 비교.

**청산·재고:** 재고 발생 후 reduce-onlymaker를 우선. longask/ shortbid를 BBO 근처로 감축; maxhold120초·adverse.35%·DD20·stale/ grace/ closing은 riskmakerunwind. emergencytaker 기본 false 이며즉시 hardstop·완전 shutdown을 보장하지 않음. directional center는 유지 하나 inventory center/ 감축 비대칭은 제거.

**EDA·실측 결과:** 아래동일 BybitBTC의 3 가상 시나리오. fill0을 DD0우수 성과로 선택하지 않음. inventory는 flat-onlyentry 때문에 주로 exit 정책을 바꾼다.

**가능한 해설:** 모듈을 추가할수록 항상 좋아진 증거는 없다. profile은 별도 가상 대안이며 69 케이스 합산 profit은 실 계좌 수익이 아니다.

**한계·판정:** 기본 가중치·neutral 문턱·정상 quote 폭도 작용해 각 모듈의 효과를 완전히 인과 식별하지 못한다. 일부 profile0의도는 기본 spreadgate와 tick/ minimum의 상호 작용일 수 있다.

| 설정 | 의도 | Fill | Gross | Fees | Net | MaxDD | EndBTC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C1 | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.000000 |
| O1 | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.000000 |
| C4 | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.000000 |


근거: [C07](C:/Users/박제형/Desktop/VibeTrading/vibetrading/mm_state/coordinator.py), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### H06 - 통합 volatility - cross + R/ RV/ levelgap 위험

단계: **동일 자산 3 조 건 native replay** / 분류: 통합 ablation

**전략 논리:** 동일 Bybit BTC 에서각추가 모듈의 가치를 비교한다. 기존 서로 다른 asset 의단독 S1~S4성과를 순위로 비교하지 않는다.

**신호·계산:** 선택 모듈 =S4, S1, S3, S2; 선택 가중치 합으로 정규화·|score|<.2 neutral.

**진입·주문:** defaultW5의 ready state, score 방향 한쪽 maker. h=max(spread/2, tick/2), center=mid+tick*score-2tick*normalizedInventory; 반대 BBO와 cross 하지 않게 tickround. thinbook 폭 2 배·크기.5, jump 신규 차단. cap150USDT/ depth10%, flat에서만 신규, model.valid 필요. 비 volatilityprofile은 riskfeature를 고정해 비교.

**청산·재고:** 재고 발생 후 reduce-onlymaker를 우선. longask/ shortbid를 BBO 근처로 감축; maxhold120초·adverse.35%·DD20·stale/ grace/ closing은 riskmakerunwind. emergencytaker 기본 false 이며즉시 hardstop·완전 shutdown을 보장하지 않음. directional center는 유지 하나 inventory center/ 감축 비대칭은 제거.

**EDA·실측 결과:** 아래동일 BybitBTC의 3 가상 시나리오. fill0을 DD0우수 성과로 선택하지 않음. inventory는 flat-onlyentry 때문에 주로 exit 정책을 바꾼다.

**가능한 해설:** 모듈을 추가할수록 항상 좋아진 증거는 없다. profile은 별도 가상 대안이며 69 케이스 합산 profit은 실 계좌 수익이 아니다.

**한계·판정:** 기본 가중치·neutral 문턱·정상 quote 폭도 작용해 각 모듈의 효과를 완전히 인과 식별하지 못한다. 일부 profile0의도는 기본 spreadgate와 tick/ minimum의 상호 작용일 수 있다.

| 설정 | 의도 | Fill | Gross | Fees | Net | MaxDD | EndBTC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C1 | 168 | 10 | -0.05580 | 0.17067 | -0.22647 | 0.22647 | 0.000000 |
| O1 | 168 | 10 | -0.05580 | 0.17067 | -0.22647 | 0.22647 | 0.000000 |
| C4 | 167 | 10 | -0.05580 | 0.17067 | -0.22647 | 0.22647 | 0.000000 |


근거: [C07](C:/Users/박제형/Desktop/VibeTrading/vibetrading/mm_state/coordinator.py), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### H07 - 통합 inventory - volatility + inventory skew

단계: **동일 자산 3 조 건 native replay** / 분류: 통합 ablation

**전략 논리:** 동일 Bybit BTC 에서각추가 모듈의 가치를 비교한다. 기존 서로 다른 asset 의단독 S1~S4성과를 순위로 비교하지 않는다.

**신호·계산:** 선택 모듈 =S4, S1, S3, S2; 선택 가중치 합으로 정규화·|score|<.2 neutral.

**진입·주문:** defaultW5의 ready state, score 방향 한쪽 maker. h=max(spread/2, tick/2), center=mid+tick*score-2tick*normalizedInventory; 반대 BBO와 cross 하지 않게 tickround. thinbook 폭 2 배·크기.5, jump 신규 차단. cap150USDT/ depth10%, flat에서만 신규, model.valid 필요. 비 volatilityprofile은 riskfeature를 고정해 비교.

**청산·재고:** 재고 발생 후 reduce-onlymaker를 우선. longask/ shortbid를 BBO 근처로 감축; maxhold120초·adverse.35%·DD20·stale/ grace/ closing은 riskmakerunwind. emergencytaker 기본 false 이며즉시 hardstop·완전 shutdown을 보장하지 않음. inventory center/ 감축 비대칭을 적용.

**EDA·실측 결과:** 아래동일 BybitBTC의 3 가상 시나리오. fill0을 DD0우수 성과로 선택하지 않음. inventory는 flat-onlyentry 때문에 주로 exit 정책을 바꾼다.

**가능한 해설:** 모듈을 추가할수록 항상 좋아진 증거는 없다. profile은 별도 가상 대안이며 69 케이스 합산 profit은 실 계좌 수익이 아니다.

**한계·판정:** 기본 가중치·neutral 문턱·정상 quote 폭도 작용해 각 모듈의 효과를 완전히 인과 식별하지 못한다. 일부 profile0의도는 기본 spreadgate와 tick/ minimum의 상호 작용일 수 있다.

| 설정 | 의도 | Fill | Gross | Fees | Net | MaxDD | EndBTC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C1 | 168 | 10 | -0.05620 | 0.17067 | -0.22687 | 0.22687 | 0.000000 |
| O1 | 168 | 10 | -0.05620 | 0.17067 | -0.22687 | 0.22687 | 0.000000 |
| C4 | 167 | 10 | -0.05620 | 0.17067 | -0.22687 | 0.22687 | 0.000000 |


근거: [C07](C:/Users/박제형/Desktop/VibeTrading/vibetrading/mm_state/coordinator.py), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### I01 - 상태 S2 초기 구현 - 비연속 표본 결합·드리프트 제한 전

단계: **보 존 소스/ 최종 G02에서 보완** / 분류: 개정 중간판

**전략 논리:** 같은 basis/ origin 논리를 사용했지만, 정상 기준 가격이 유효한 모든 학습 표본을 한 시계열로 이어 붙이고 실시간 baseline의 이동을 별도로 제한하지 않았다.

**신호·계산:** log basis, causal rolling median, ADF/ KPSS, origin, cost gate. 초기 calibration은 reference가 있는 records를 연속성 확인 없이 모았고, score 생성에는 stable_baseline 조건이 없었다.

**진입·주문:** ready·stationary·abs residual>entry와 origin 방향 정합성만 통과하면 한쪽 maker 후보. G02는 가장 긴 연속 학습 구간과 baseline drift<=max(5 bp,6 MAD)를 추가했다.

**청산·재고:** basis 수 축과 실제 로컬 이익을 요구하는 TP1/ TP2 및 maker 위험 감축은 같은 계열이었다.

**EDA·실측 결과:** 초기·최종 소스의 해당 차이를 tar 스냅 샷에서 직접 확인했다. 중간 v2의 S2 4개 단독 경로는 모두 체결 0이었다. 초기 소스와 각 중간 재생의 정확한 전체 runtime 결합은 최종 sealed 만큼 동결되지 않아 별도 수익성 결과로 승인하지 않는다.

**가능한 해설:** 공백을 건너뛰어 정상성 검정을 하면 시간 의존 구조와 표본 간격이 왜곡될 수 있고, 천천히 이동하는 기준을 수렴 대상이라고 오인할 수 있다. 최종 보완은 이 계산 위험을 줄였지만 수익성 향상의 증거는 아니다.

**한계·판정:** 이 항목은 C02의 OLS anchor 와도 G02의 최종 state 구현과도 구분한다. 조기 testnet의 +0.113898 USDT는 이 초기 state 모델의 성과가 아니다.

근거: [I01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/server_review/084ae2bf7e402373.tar.gz), [I03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/server_final/692487af63298f4a.tar.gz), [I04](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_v2/summary.json), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md)

### I02 - 상태 S3 초기 구현 - 체결 유효성 확인 전 episode 누적

단계: **보 존 소스/ 최종 G03에서 보완** / 분류: 개정 중간판

**전략 논리:** PRESSURE→ABSORPTION→공급 또는 공격자 소진의 가설은 같지만, episode 누적 flow를 공통 feature의 유효성 검증보다 먼저 늘렸다.

**신호·계산:** 초기 on_trade는 episode 방향과 side만 일치하면 quantity를 self.flow에 더 한 뒤 공통 on_trade를 호출했다. 최종은 finite·양수 수량/ 가격, 직전 book의 신선도, cursor 이전 timestamp 여부부터 확인한다.

**진입·주문:** 흡수·breakout/ reversal 상태 및 공통 maker 정책을 사용한다. 나중에 제외되는 오래된 trade가 누적 flow에 들어가면 전 이 문턱을 잘못 통과할 수 있었다.

**청산·재고:** 같은 maker 감축·holding·손실제한. 실제로 체결된 재고와 신호 episode를 별도로 관리한다.

**EDA·실측 결과:** 스냅샷 간 코드 차이는 확인됐다. 최종 재생은 오래된 trade 417개를 제외했다. 그 수가 모두 초기 S3 episode를 바꾸었다고 검증한 것은 아니며, 해당 오류만 분리한 손익 반사실 실험은 없다.

**가능한 해설:** 가설의 틀과 입력 집계 순서의 구현 오류를 구분해야한다. 잘못 누적한 신호 상태를 바로잡았다는 사실이 새 episode의 예측력을 보장하지 않는다.

**한계·판정:** 중간 원장의 부분 체결을 최종 G03 손익에 합산하지 않는다. 초기 S1/ S4 전략 파일은 스냅샷 간 동일해 별도 새 알파로 중복 등록하지 않았다.

근거: [I01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/server_review/084ae2bf7e402373.tar.gz), [I03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/server_final/692487af63298f4a.tar.gz), [I04](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_v2/summary.json), [I06](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_final/run.sqlite), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md)

### I03 - 통합 static 초기 구현 - 상시 재고 감축 분기 전

단계: **중간 v2 재생/ 최종 H01에서 보완** / 분류: 개정 중간판

**전략 논리:** 신호 없는 flat BBO 양방향 기준 군이었지만, 재고가 생겼을 때 평상시 reduce-only 주문을 반환하는 분기가 빠져 있었다.

**신호·계산:** BBO·unit·lot·max inventory. 초기 함수는 qty<=0 또는 new_exposure=False 이면 바로 빈 의도를 반환했다. 공통 flat-only 제한 때문에 재고 보유 중 감축 의도가 사라질 수 있었다.

**진입·주문:** 평탄할 때 bid/ ask를 제출하고 공통 위험 한도를 사용했다. 최초 스냅샷의 별도 calibration 인자 중복도 통합 생성자 단계에서 수정되었다; 이것은 새 진입 가설이 아니다.

**청산·재고:** 보유 중 평상 시 STATIC_MAKER_UNWIND가 없고 공통 강제 holding/ risk 경로에 의존한 초기 구현. 최종 H01은 확정 재고를 lot 단위로 반대 BBO에 reduce-only로 돌려 준다.

**EDA·실측 결과:** 중간 v2 static C1은 의도 61건·fill event 23개·net -0.541994 USDT 였다. 최종 H01의 353건·103개·-2.464474와 별도 기록이다. 아래는 중간 v2의 3개 조건이며 초기 미완료 원장과 합산하지 않는다.

**가능한 해설:** 거래 수와 보유 시간이 달라져 손실 절댓값만으로 개정 우열을 판단하면 안 된다. 평상시 감축 누락을 고친 목적은 기준 군의 실행 의미를 다른 정책과 맞추는 것이다.

**한계·판정:** v2와 최종은 동일 자료를 반복 재생한 개발 이력이다. 독립 OOS 표본이나 새 30분 live 실험이 아니며, 가중치·queue 등 전체 변경 효과와 분리된 통계적 개선 검정도 없다.

| 조건 | 의도 | Fill events | Net USDT | End BTC |
| --- | --- | --- | --- | --- |
| C1 | 61 | 23 | -0.541994 | -0.001000 |
| O1 | 61 | 23 | -0.553595 | -0.001000 |
| C4 | 61 | 23 | -0.541994 | -0.001000 |


근거: [I01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/server_review/084ae2bf7e402373.tar.gz), [I02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/server_review/646c9f6a48f4a3bf.tar.gz), [I03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/server_final/692487af63298f4a.tar.gz), [I04](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_v2/summary.json), [I05](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay/run.sqlite), [I06](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_final/run.sqlite), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### J01 - S2 robust EWMA baseline 대안

단계: **제안/ 고유 구현·학습 없음** / 분류: 피드백 계산 대안

**전략 논리:** basis의 중심을 느린 지수 가중 평균으로 갱신하되 이상치의 영향을 제한한다.

**신호·계산:** robust EWMA; half-life, 이상치 처리와 초기화는 피드백에서 동결되지 않았다.

**진입·주문:** 추정 중심을 뺀 residual과 Binance/ local origin·freshness·비용으로 거래 후보를 만드는 대안. 실제 entry threshold 나 독립 주문 규칙은 확정하지 않았다.

**청산·재고:** 로컬 이익과 residual 수 축을 함께 요구한다는 설계 방향만 있으며 고유 exit·PnL은 없다.

**EDA·실측 결과:** 피드백의 baseline 후보 목록에서 확인했다. 실제 채택된 것은 G02의 causal rolling median이며 이 대안들을 fit·재생·운용한 기록은 없다.

**가능한 해설:** 중심 추정의 유연성과 진짜 기회의 보존 사이에 trade-off가 있다. 실제 dislocation을 baseline 이 빠르게 따라가면 alpha를 지우고, 너무 느리면 지속적인 basis drift를 가짜 괴리로 만들 수 있다.

**한계·판정:** rolling median의 EDA 나 실제 S2 결과를 이 대안의 성과로 대입하지 않는다. 문서에 모델 이름이 있다는 사실은 유효한 수익 모델이라는 뜻이 아니다.

근거: [FEEDBACK](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/feedback_recorded_original.md), [M05](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/STRATEGY_SPEC.md), [C04](C:/Users/박제형/Desktop/VibeTrading/strategy/s2_external_anchor.py)

### J02 - S2 rolling quantile center baseline 대안

단계: **제안/ 고유 구현·학습 없음** / 분류: 피드백 계산 대안

**전략 논리:** 분포가 비대칭일 때 하나의 고정 평균 대신 분위수로 중심을 정의한다.

**신호·계산:** rolling quantile center; 사용할 분위 수와 조합·lookback은 미동결. q50만 사용하면 구현된 rolling median과 수학적으로 같다.

**진입·주문:** 추정 중심을 뺀 residual과 Binance/ local origin·freshness·비용으로 거래 후보를 만드는 대안. 실제 entry threshold 나 독립 주문 규칙은 확정하지 않았다.

**청산·재고:** 로컬 이익과 residual 수 축을 함께 요구한다는 설계 방향만 있으며 고유 exit·PnL은 없다.

**EDA·실측 결과:** 피드백의 baseline 후보 목록에서 확인했다. 실제 채택된 것은 G02의 causal rolling median이며 이 대안들을 fit·재생·운용한 기록은 없다.

**가능한 해설:** 중심 추정의 유연성과 진짜 기회의 보존 사이에 trade-off가 있다. 실제 dislocation을 baseline 이 빠르게 따라가면 alpha를 지우고, 너무 느리면 지속적인 basis drift를 가짜 괴리로 만들 수 있다.

**한계·판정:** rolling median의 EDA 나 실제 S2 결과를 이 대안의 성과로 대입하지 않는다. 문서에 모델 이름이 있다는 사실은 유효한 수익 모델이라는 뜻이 아니다.

근거: [FEEDBACK](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/feedback_recorded_original.md), [M05](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/STRATEGY_SPEC.md), [C04](C:/Users/박제형/Desktop/VibeTrading/strategy/s2_external_anchor.py)

### J03 - S2 state-space slow mean baseline 대안

단계: **제안/ 고유 구현·학습 없음** / 분류: 피드백 계산 대안

**전략 논리:** 천천히 변하는 공통 basis와 빠른 잔차를 잠재 상태로 분리한다.

**신호·계산:** state-space slow mean; 관측/ 상태 잡음·전이 식·filter 종류와 파라미터는 미동결.

**진입·주문:** 추정 중심을 뺀 residual과 Binance/ local origin·freshness·비용으로 거래 후보를 만드는 대안. 실제 entry threshold 나 독립 주문 규칙은 확정하지 않았다.

**청산·재고:** 로컬 이익과 residual 수 축을 함께 요구한다는 설계 방향만 있으며 고유 exit·PnL은 없다.

**EDA·실측 결과:** 피드백의 baseline 후보 목록에서 확인했다. 실제 채택된 것은 G02의 causal rolling median이며 이 대안들을 fit·재생·운용한 기록은 없다.

**가능한 해설:** 중심 추정의 유연성과 진짜 기회의 보존 사이에 trade-off가 있다. 실제 dislocation을 baseline 이 빠르게 따라가면 alpha를 지우고, 너무 느리면 지속적인 basis drift를 가짜 괴리로 만들 수 있다.

**한계·판정:** rolling median의 EDA 나 실제 S2 결과를 이 대안의 성과로 대입하지 않는다. 문서에 모델 이름이 있다는 사실은 유효한 수익 모델이라는 뜻이 아니다.

근거: [FEEDBACK](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/feedback_recorded_original.md), [M05](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/STRATEGY_SPEC.md), [C04](C:/Users/박제형/Desktop/VibeTrading/strategy/s2_external_anchor.py)

### J04 - 피드백 정책 A - 비상 taker 없는 maker-only 감축

단계: **제안 및 최종 기본 정책** / 분류: 청산·위험 정책

**전략 논리:** 정상·비상 상황 모두 resting maker 로만 줄이고 taker 수수료와 즉시 crossing을 피한다.

**신호·계산:** 재고·holding·손실·stale 상태와 opposite BBO. 반대 best를 즉시 치는 주문에 maker fee를 부여하지 않는다.

**진입·주문:** G/ H의 entry alpha를 사용하며 위험 상태에서는 새 노출과 재고 확대 side를 차단한다.

**청산·재고:** 재고 감축 side만 BBO로 당기고 reduce-only를 유지한다. emergency_taker=False 이므로 위험 문턱을 넘어도 즉시 전량 청산을 보장하지 않는다.

**EDA·실측 결과:** 최종 state 기본 재생과 공통 정책에 포함됐다. 다른 alpha와 독립적인 정책 A 대 정책 B의 체결·손익 실험은 없다.

**가능한 해설:** maker-only는 비용·주문 방식의 선택이며 손절의 시간 보장이 아니다. exit 부문의 불리한 markout과 최종 미청산 재고를 함께 확인해야한다.

**한계·판정:** 최종 shutdown MTM을 실제 청산으로 부르지 않는다. A02의 정상 maker·hard IOC 정책과 구분한다.

근거: [FEEDBACK](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/feedback_recorded_original.md), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md), [M07](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/RISK_CONTROLS.md), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### J05 - 피드백 정책 B - 정상 maker·hard risk emergency taker

단계: **명시 옵션/ 이번 성과에는 비활성** / 분류: 청산·위험 정책

**전략 논리:** 정상 운용은 maker로 하되 극단위험에서 비용을 지불하고 노출을 즉시 줄이는 별도 정책.

**신호·계산:** hard risk threshold, inventory·fresh book·minimum·taker fee와 emergency 횟수.

**진입·주문:** 기존 entry 가설을 사용하며 비상 감축을 새로운 directional entry로 혼동하지 않는다.

**청산·재고:** 명시적으로 emergency 옵션을 켰을 때만 시장성 reduce-only 감축. emergency 비용·빈도·PnL을 정상 maker와 별도 기록해야한다.

**EDA·실측 결과:** 피드백과 코드 옵션은 확인됐지만 최종 state 재생에서는 false 였다. 따라서 이번 G/ H 손익 을 정책 B의 검증 결과라고 부르지 않는다.

**가능한 해설:** tail inventory 위험을 줄일 수 있는 반면 spread·슬리피지·taker 비용이 추가된다. 정책 A와의 같은 조건 비교가 없으므로 우열을 단정할 수 없다.

**한계·판정:** v1/ v4의 위험 IOC 기록을 새 state 정책 B의 실험으로 재분류하지 않는다. 원형과 계정별 비용·위험 문턱도 다르다.

근거: [FEEDBACK](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/feedback_recorded_original.md), [M07](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/RISK_CONTROLS.md), [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md)

### J06 - 피드백 일반형 - Direction·Volatility·Inventory quote skew

단계: **제안/ 최종 tick 정책의 선행 식** / 분류: 피드백 통합 가격 정책

**전략 논리:** 정확한 미래 FV 한 점 대신 현재 mid를 기준으로 방향과 재고에 따라 quote 중심을 옮기고 위험에 따라 폭을 정한다.

**신호·계산:** bid=m-h(V)+alpha*D-kappa*I, ask=m+h(V)+alpha*D-kappa*I. D=방향 score, V=유동성/ 변동성, I=확정 재고.

**진입·주문:** 양의 D는 중심을 올리고 음의 D는 내린다. 실제 tick round·PostOnly·양방향 승인·lot 정책은 별도 필요하며 alpha/ kappa의 학습 값은 피드백에서 확정하지 않았다.

**청산·재고:** long 재고는 중심을 낮춰 ask를 더 적극적으로 하고 bid 확대를 억제한다; short는 대칭. 폭 확대만으로 재고가 줄어든다고 해석하지 않는다.

**EDA·실측 결과:** 일반형 자체의 독립 운용이나 PnL은 없다. G10/ H07이 이 방향을 tick 기반 skew와 flat-only 신규 진입·maker 감축으로 제한 구현했다.

**가능한 해설:** 제안된 연속형 양방향 가격 식과 실제로 보유 중 감축만 하는 최종 구현은 같지 않다. 따라서 H07 결과를 이 일반형의 완전한 검증으로 확대하지 않는다.

**한계·판정:** D·V·I의 역할 분리는 논리적 설계이며 계수의 경제적 유효성, fill-conditioned edge와 최적 quote 폭은 미검증이다.

근거: [FEEDBACK](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/feedback_recorded_original.md), [M05](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/STRATEGY_SPEC.md), [C07](C:/Users/박제형/Desktop/VibeTrading/vibetrading/mm_state/coordinator.py), [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json)

### Z01 - Turtle breakout 프로젝트 검증용 프로토타입

단계: **코드 확인/ 성과 근거 없음** / 분류: 프로젝트 기타

**전략 논리:** 직전고가돌파 long·저가이탈 exit의 추세 추종. MM과 독립된 프로젝트 시스템 검증용 전략도 누락 방지 등록.

**신호·계산:** 문서는 20일 고가/10일 저가라 표현하나 설정 interval=1 m, BTCUSDT paper. deque high20/ low10에현재 bar를 넣고[:-1]로 비교.

**진입·주문:** 실제 코드는 현재 bar 포함 20 칸에서 현재를 빼 직전 19 bars 고가와 close를 비교; in_position=False 일때 ENTER_LONG.

**청산·재고:** 직전 9 bars 저가 이탈 EXIT_LONG. 실제 fill 전략 연계 없이 signal 발생 시 in_position을 변경한다.

**EDA·실측 결과:** 이대화의코인 MM testnet/ pilot/ EDA에 포함된 체결·PnL 자료 없음. 독립 생산 운용을 확인하지 못함.

**가능한 해설:** 20일/10일 명칭·1분 interval·실제 19/9lookback이 서로 달라 규칙 명확화가 필요하다. 테스트 전략 존재와 유효한 trading 성과는 별개다.

**한계·판정:** 이 보고서는 코드를 고치거나 새 backtest를 실행하지 않았다. 미래 침범 방지를 위해 currentbar 제외는 맞지만 deque 크기로 lookback 하나가 줄어든다.

근거: [C08](C:/Users/박제형/Desktop/VibeTrading/strategies/turtle_breakout.py)

### Z02 - Example signal - 직전 캔들 고가/ 저가 돌파

단계: **hot-path 예제/ 성과 근거 없음** / 분류: 프로젝트 기타

**전략 논리:** 완료한 새 1분 bar가 직전 bar 범위를 벗어나면 long 진입/ exit signal을 만든다.

**신호·계산:** BinanceBTCUSDT bar_1m, 구독 warmup30bars, 전략 내 closedbars 최소 4, closed·noncorrection 조건.

**진입·주문:** close>prev.high 면 ENTER_LONG, signal 유효시각현재 +60초.

**청산·재고:** close<prev.low 면 EXIT_LONG. 자체 filledposition 검증·risk·quoteprice 설계는 예제에 없고 실행층의 역할.

**EDA·실측 결과:** 해당 예제의 실제 운용 PnL/ 예측성과 기록 없음. MM 연구 표의 성공/ 실패에 합산하지 않는다.

**가능한 해설:** 전략 인터페이스·warmup·eventid·validity 연결을 검증하는 기본 예제다.

**한계·판정:** 60초 signal validity는 목표 보유 기간이나 수익률 근거가 아니다.

근거: [C09](C:/Users/박제형/Desktop/VibeTrading/strategies/example_signal.py)

## 6. 모든 실행 경로의 비교 부록

### 6.1 최종 실제 테스트넷 8 경로 및 조기 점검

금액 USDT. 조기 점검은 30분 목표를 충족하지 않았다. 실제 주문/ 체결 기록과 가상 replay를 합산하지 않는다.

| 실험 | Route | 주문 | Fill | 왕복 | Fees | Net | MDD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 조기 점검 | bybit: S1 | 0 | 0 | 0 | 0.000000 | 0.000000 | 0.000000 |
| 조기 점검 | bybit: S2 | 0 | 0 | 0 | 0.000000 | 0.000000 | 0.000000 |
| 조기 점검 | bybit: S3 | 0 | 0 | 0 | 0.000000 | 0.000000 | 0.000000 |
| 조기 점검 | bybit: S4 | 0 | 0 | 0 | 0.000000 | 0.000000 | 0.000000 |
| 조기 점검 | grvt: S1 | 0 | 0 | 0 | 0.000000 | 0.000000 | 0.000000 |
| 조기 점검 | grvt: S2 | 4 | 2 | 1 | 0.066502 | 0.113898 | 0.096899 |
| 조기 점검 | grvt: S3 | 0 | 0 | 0 | 0.000000 | 0.000000 | 0.000000 |
| 조기 점검 | grvt: S4 | 0 | 0 | 0 | 0.000000 | 0.000000 | 0.000000 |
| 최종 30분 | bybit: S1 | 0 | 0 | 0 | 0.000000 | 0.000000 | 0.000000 |
| 최종 30분 | bybit: S2 | 0 | 0 | 0 | 0.000000 | 0.000000 | 0.000000 |
| 최종 30분 | bybit: S3 | 0 | 0 | 0 | 0.000000 | 0.000000 | 0.000000 |
| 최종 30분 | bybit: S4 | 1 | 0 | 0 | 0.000000 | 0.000000 | 0.000000 |
| 최종 30분 | grvt: S1 | 0 | 0 | 0 | 0.000000 | 0.000000 | 0.000000 |
| 최종 30분 | grvt: S2 | 0 | 0 | 0 | 0.000000 | 0.000000 | 0.000000 |
| 최종 30분 | grvt: S3 | 0 | 0 | 0 | 0.000000 | 0.000000 | 0.000000 |
| 최종 30분 | grvt: S4 | 0 | 0 | 0 | 0.000000 | 0.000000 | 0.000000 |

### 6.2 원형 mainnet16경로와 2 배 지연

| 설정 | Route | 의도 | Active | Fill | Net exfund | FV valid |
| --- | --- | --- | --- | --- | --- | --- |
| base | arcus:S1 | 0 | 0 | 0 | 0.000000 | True |
| base | arcus:S2 | 0 | 0 | 0 | 0.000000 | False |
| base | arcus:S3 | 14 | 5 | 0 | 0.000000 | True |
| base | arcus:S4 | 0 | 0 | 0 | 0.000000 | True |
| base | bybit:S1 | 0 | 0 | 0 | 0.000000 | True |
| base | bybit:S2 | 0 | 0 | 0 | 0.000000 | False |
| base | bybit:S3 | 0 | 0 | 0 | 0.000000 | True |
| base | bybit:S4 | 0 | 0 | 0 | 0.000000 | True |
| base | bitget:S1 | 1 | 1 | 0 | 0.000000 | True |
| base | bitget:S2 | 0 | 0 | 0 | 0.000000 | False |
| base | bitget:S3 | 0 | 0 | 0 | 0.000000 | True |
| base | bitget:S4 | 0 | 0 | 0 | 0.000000 | True |
| base | grvt:S1 | 0 | 0 | 0 | 0.000000 | True |
| base | grvt:S2 | 0 | 0 | 0 | 0.000000 | True |
| base | grvt:S3 | 0 | 0 | 0 | 0.000000 | False |
| base | grvt:S4 | 0 | 0 | 0 | 0.000000 | True |
| stress2x | arcus:S1 | 0 | 0 | 0 | 0.000000 | True |
| stress2x | arcus:S2 | 0 | 0 | 0 | 0.000000 | False |
| stress2x | arcus:S3 | 14 | 7 | 0 | 0.000000 | True |
| stress2x | arcus:S4 | 0 | 0 | 0 | 0.000000 | True |
| stress2x | bybit:S1 | 0 | 0 | 0 | 0.000000 | True |
| stress2x | bybit:S2 | 0 | 0 | 0 | 0.000000 | False |
| stress2x | bybit:S3 | 0 | 0 | 0 | 0.000000 | True |
| stress2x | bybit:S4 | 0 | 0 | 0 | 0.000000 | True |
| stress2x | bitget:S1 | 1 | 1 | 0 | 0.000000 | True |
| stress2x | bitget:S2 | 0 | 0 | 0 | 0.000000 | False |
| stress2x | bitget:S3 | 0 | 0 | 0 | 0.000000 | True |
| stress2x | bitget:S4 | 0 | 0 | 0 | 0.000000 | True |
| stress2x | grvt:S1 | 0 | 0 | 0 | 0.000000 | True |
| stress2x | grvt:S2 | 0 | 0 | 0 | 0.000000 | True |
| stress2x | grvt:S3 | 0 | 0 | 0 | 0.000000 | False |
| stress2x | grvt:S4 | 0 | 0 | 0 | 0.000000 | True |


15 의도 전부 도착 전 cancelrequest, 생성 →취소 median3.468ms, range.136~151.635 ms. proxy 도착 1080.451 ms 였다. 초미세 수량 1.236674 →1.236695 SOL 만으로.150 ms 후 cancel 된 사례가 있다. 같은 timer 주기에취소를 도착보다 먼저 처리하던 모형 한계도 함께 작용했다. 가설 P&L 검정과 실행 실패를 구분해야한다.

### 6.3 상태 개정 단독 48 케이스 + 동일 자산 21 케이스

C1=보수 queue+설정 기본 latency; O1=불확실 proportional cancel credit+기본 latency; C4=보수 queue+설정 4 배 latency. 기본 Arcus100/ Bybit25/ Bitget50/ GRVT75ms는 **미 실측 configured sensitivity**이며 mainnet/ testnet 실측이라고 부르지 않는다. ArcusUSD 와나머지 USDT는 환산 합산하지 않는다. End 재고는 finalmid MTM 이며가짜 shutdownfill을 만들지 않았다. Funding·USD/ USDT 변환 cost 제외.

| Case | 자료 valid | 의도 | Fill | Gross | Fees | Net | End qty |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C1.arcus.S1 | True | 340 | 19 | -0.00729 | 0.00000 | -0.00729 | 0.00000000 |
| C1.arcus.S2 | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C1.arcus.S3 | True | 13 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C1.arcus.S4 | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C1.bybit.S1 | True | 107 | 6 | -0.02840 | 0.10244 | -0.13084 | 0.00000000 |
| C1.bybit.S2 | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C1.bybit.S3 | True | 32 | 21 | -0.09000 | 0.21842 | -0.30842 | 0.00000000 |
| C1.bybit.S4 | True | 339 | 42 | 0.00177 | 0.23470 | -0.23293 | 0.00000000 |
| C1.bitget.S1 | True | 353 | 56 | -0.18797 | 0.34468 | -0.53265 | 0.00000000 |
| C1.bitget.S2 | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C1.bitget.S3 | True | 114 | 24 | 0.01540 | 0.26191 | -0.24651 | 0.00000000 |
| C1.bitget.S4 | True | 443 | 13 | -0.04390 | 0.12122 | -0.16512 | 0.00000000 |
| C1.grvt.S1 | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C1.grvt.S2 | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C1.grvt.S3 | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C1.grvt.S4 | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C1.ablation.static | True | 353 | 103 | -0.70665 | 1.75782 | -2.46447 | -0.00100000 |
| C1.ablation.obi | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C1.ablation.book | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C1.ablation.flow | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C1.ablation.cross | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C1.ablation.volatility | True | 168 | 10 | -0.05580 | 0.17067 | -0.22647 | 0.00000000 |
| C1.ablation.inventory | True | 168 | 10 | -0.05620 | 0.17067 | -0.22687 | 0.00000000 |
| O1.arcus.S1 | True | 313 | 36 | -0.04626 | 0.00000 | -0.04626 | 0.00000000 |
| O1.arcus.S2 | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| O1.arcus.S3 | True | 13 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| O1.arcus.S4 | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| O1.bybit.S1 | True | 107 | 6 | -0.02840 | 0.10244 | -0.13084 | 0.00000000 |
| O1.bybit.S2 | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| O1.bybit.S3 | True | 36 | 26 | -0.07800 | 0.30579 | -0.38379 | 0.00000000 |
| O1.bybit.S4 | True | 365 | 23 | -0.02539 | 0.26468 | -0.29007 | 0.00000000 |
| O1.bitget.S1 | True | 368 | 52 | -0.15713 | 0.37198 | -0.52911 | 0.00000000 |
| O1.bitget.S2 | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| O1.bitget.S3 | True | 115 | 31 | 0.01770 | 0.29101 | -0.27331 | 0.00000000 |
| O1.bitget.S4 | True | 449 | 14 | 0.03050 | 0.15841 | -0.12791 | 0.00000000 |
| O1.grvt.S1 | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| O1.grvt.S2 | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| O1.grvt.S3 | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| O1.grvt.S4 | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| O1.ablation.static | True | 376 | 125 | -0.74535 | 2.13325 | -2.87860 | -0.00100000 |
| O1.ablation.obi | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| O1.ablation.book | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| O1.ablation.flow | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| O1.ablation.cross | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| O1.ablation.volatility | True | 168 | 10 | -0.05580 | 0.17067 | -0.22647 | 0.00000000 |
| O1.ablation.inventory | True | 168 | 10 | -0.05620 | 0.17067 | -0.22687 | 0.00000000 |
| C4.arcus.S1 | True | 305 | 13 | -0.00606 | 0.00000 | -0.00606 | 0.00000000 |
| C4.arcus.S2 | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C4.arcus.S3 | True | 12 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C4.arcus.S4 | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C4.bybit.S1 | True | 106 | 8 | -0.02850 | 0.13654 | -0.16504 | 0.00000000 |
| C4.bybit.S2 | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C4.bybit.S3 | True | 35 | 24 | -0.09400 | 0.21842 | -0.31242 | 0.00000000 |
| C4.bybit.S4 | True | 338 | 34 | 0.00661 | 0.23470 | -0.22809 | 0.00000000 |
| C4.bitget.S1 | True | 336 | 82 | -0.23546 | 0.43002 | -0.66548 | 0.00000000 |
| C4.bitget.S2 | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C4.bitget.S3 | True | 114 | 20 | 0.01730 | 0.23282 | -0.21552 | 0.00000000 |
| C4.bitget.S4 | True | 408 | 18 | -0.05630 | 0.12122 | -0.17752 | 0.00000000 |
| C4.grvt.S1 | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C4.grvt.S2 | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C4.grvt.S3 | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C4.grvt.S4 | False | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C4.ablation.static | True | 350 | 101 | -0.71085 | 1.72368 | -2.43453 | -0.00100000 |
| C4.ablation.obi | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C4.ablation.book | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C4.ablation.flow | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C4.ablation.cross | True | 0 | 0 | 0.00000 | 0.00000 | 0.00000 | 0.00000000 |
| C4.ablation.volatility | True | 167 | 10 | -0.05580 | 0.17067 | -0.22647 | 0.00000000 |
| C4.ablation.inventory | True | 167 | 10 | -0.05620 | 0.17067 | -0.22687 | 0.00000000 |


GRVT BTC의 가격 85258~85398 USDT에서 lot.001/ minnotional100/ cap150은 함께 충족 불가:.001 BTC≈85 USDT로 최소 미달,.002 BTC≈171로 상한 초과. risk 축소 량 0 판정 2125회/ minimum 불충 족 174/ jump177이었다. ArcusS4는 readytrain189<200, GRVTS4도 readytrain154<200. 거래 자체가 막힌 경로는 queue 나 latency 실패로 설명하지 않는다.

### 6.4 Fill-conditioned 진입·청산 markout

100/250/500ms,1/2/5/10/30초의첫유효미래 quote, target 늦음>.5초·gap·tail은 missing. Buy=1 e4(mid_future/ fillprice-1), Sell=1 e4(fillprice/ mid_future-1). 각 fill 수량 가중 평균; 부분 fill은 독립 order 표본이 아니다. 진입과 exit를 분리한다.

| Case | 역할 | N1s | Gross1bp | Fee 후 1 bp | Fee 후 5 bp | Fee 후 30 bp |
| --- | --- | --- | --- | --- | --- | --- |
| C1.ablation.inventory | ENTRY | 5 | -0.325 | -2.325 | -2.498 | -3.632 |
| C1.ablation.inventory | EXIT | 5 | -0.505 | -2.505 | -2.508 | -2.283 |
| C1.ablation.static | ENTRY | 52 | -0.546 | -2.546 | -2.739 | -2.814 |
| C1.ablation.static | EXIT | 51 | -0.722 | -2.722 | -2.864 | -2.930 |
| C1.ablation.volatility | ENTRY | 5 | -0.325 | -2.325 | -2.498 | -3.632 |
| C1.ablation.volatility | EXIT | 5 | -0.496 | -2.496 | -2.498 | -2.274 |
| C1.arcus.S1 | ENTRY | 10 | -0.243 | -0.243 | -0.161 | -0.056 |
| C1.arcus.S1 | EXIT | 9 | -0.858 | -0.858 | -0.970 | -1.189 |
| C1.bitget.S1 | ENTRY | 31 | -0.591 | -2.591 | -2.647 | -2.722 |
| C1.bitget.S1 | EXIT | 25 | -0.823 | -2.823 | -2.912 | -3.208 |
| C1.bitget.S3 | ENTRY | 9 | -0.472 | -2.472 | -1.611 | 0.314 |
| C1.bitget.S3 | EXIT | 15 | -1.889 | -3.889 | -4.174 | -4.659 |
| C1.bitget.S4 | ENTRY | 7 | -2.989 | -4.989 | -5.042 | -3.949 |
| C1.bitget.S4 | EXIT | 6 | -0.993 | -2.993 | -2.716 | -3.692 |
| C1.bybit.S1 | ENTRY | 3 | -0.279 | -2.279 | -2.568 | -3.775 |
| C1.bybit.S1 | EXIT | 3 | -0.307 | -2.307 | -2.310 | -1.518 |
| C1.bybit.S3 | ENTRY | 10 | -0.027 | -2.027 | -1.807 | -2.851 |
| C1.bybit.S3 | EXIT | 11 | -1.035 | -3.035 | -2.211 | -2.465 |
| C1.bybit.S4 | ENTRY | 12 | -0.638 | -2.638 | -2.787 | -2.034 |
| C1.bybit.S4 | EXIT | 30 | -0.992 | -2.992 | -2.970 | -3.152 |


BitgetS3진입 30초 gross+2.314 bp/ entryfee 후 +.314 bp는 9 partial 포함 record 뿐이고 maker 왕복 4 bp보다 작다. 실제 exit 포함 NetMTM은-.246506 USDT. 하나의 H에서 양의 반응이 있다는 사실을 수익 모델 성공과 동일시하지 않는다.

개발 중간 판도 별도로 확인했다. 첫 `replay`에는 완성된 summary가 없고 fill 원장도 0개였다. `replay_v2`의 69개 결과에는 fill event 697개가 있었으며, 보존된 `replay_final` 원장의 612개 기록은 최종 동결 결과로 승인하지 않는다. 최종 `replay_sealed`의 957개와 서로 합산하지 않는다. I01~I03은 이 개발 이력을 별도 전략 개정 카드로 보존한다.

중간 v2와 최종 sealed의 case별 결과 필드를 직접 대조하면, 차이가 있는 것은 static 3 조 건의 거래·손익 과 Arcus S2 3 조건의 `enabled=True→False`다. 다른 case의 보존된 주문·체결·손익 필드는 동일하다. Arcus S2는 양쪽 모두 주문과 체결이 0이었으므로 승인 필드 변경을 이익 개선으로 부르지 않는다. static의 중간 3 조건 손익은 I03, 최종 3 조 건 손익은 H01에 표시한다. 이 대조는 같은 자료의 개발 버전 비교이며 독립 실험의 추가 표본이 아니다.

### 6.5시간축·국면의 전체 실측 범위

각 E 카드의 W5표외에 W1/5/10/30/60의 모든 H1~180 표면은 F02자료에 보존되어 있다. 인접 327636 행을 별개의 전략으로 세지 않는다. 아래 선택된 분석 결과는 기존 보고서에서 정한 규칙의 요약이며 사후 최고 수익의 채택이 아니다.

| 검사 | 관측 | 경제적 해석 |
| --- | --- | --- |
| OFI 동시 W5/ H0 | pilot RMSE+20.10%; production+15.59% | 이미 발생한 충격 설명, 미래 예측 아님 |
| OFI 미래 W5/ H5 | pilot+.56%; production+.94% | 동시 관계가 미래에도 같은 크기로 남지 않음 |
| productionL1W5/ H30 | RMSE+.91%; 비영 BA61.45% | 작은 가격 방향 정보; fillednetprofit 별도 |
| 원형 계수 BybitS4 H5 | RMSE+4.12%, nonzeroBA65.7%, 예측 폭 median.187bp | 원형 maker+taker7.5bp보다 작음 |
| L1같은 12 routes H25 | pilot high-low.539bp, production excludingpilot.568bp | 관계 있음; 공유 OBIcorr.999699·mid 차이 0으로 독립 재현과 구별 |
| L1 onset H25 | alignedmean.430bp, median.083bp | 평균과 개별 확률·실제 체결 profit을 구별 |
| 정규화 OFI+trade W5/ H5 | productionpaired improvement+.007%p, pilot-.439%p | 동일 경로 paired 비교; 여러 지표 조합 자동 개선 없음 |
| 과거 RV·flow에 R 추가 | productionW5 MAE 미소 개선, W60거의 0/ 음수 | 국면 상관과 추가 미래 예측정보를 분리 |


![ 기초지표 W/H - production; RMSE 는거래수익률아님 ](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/figures/production_window_horizon.png)


![ 기초지표 W/H - pilot; 별도 train/eval 이며같은자료탐색 ](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/figures/pilot_test_window_horizon.png)


![ L1 조건부가격반응과 pilot 중복기간제외 ](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/figures/l1_actual_price_responses.png)


![ 7지표 onset 후실제방향정렬이동;filledprofit 아님 ](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/figures/onset_actual_moves.png)


![ BybitBTC C1 진입 maker markout,entryfee 차감;왕복 PnL 아님 ](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/figures/ablation_markouts.png)

## 7. 실패 원인·가능한 해설의 종합

| 층 | 확인한 것 | 아직 말할 수 없는 것 |
|---|---|---|
| 수식 구현 | 원형 27456 표본 계산 일치; price-awareOFI·volumeclock·단독 signal 식보존 | 명세일치가 시장 가설이나 순이익을 증명하지 않음 |
| 모델/ target | S2전체 localmove 를순수 gap 수렴율로 해석한 불일치, 단기 계수 부호 이동, signal spread 스케일 제한 | 모든 OFI/ OBI/ 공급·체결 메커니즘의 전면 부재 |
| 특징 안정성 | instantdepth 곱셈 combined8폭증, 후속 TWdepth/ asinh/ trainclip로 표현 변경 | 안정화된 표현이 알파를 반드시 만듦 |
| 데이터 | Binance 실험 기준가 누락 0, ArcusproductionsideUNKNOWN, GRVTfreshtrade 부족, 일시 crossedbook | 과거누락 side 복구, netrefill에서 gross 공급·숨은 queue 의식별 |
| 실행 | pre-arrivalcancel·미세수량재취소·timeordering 한계, 개정 후 가상 fill 발생 | source/ receive/ arrival 실제 clock·실측 venue latency로 확정된 mainnetfill 정확도 |
| 경제성 | gross 손실 경로와 작은 양 gross 보다큰 fees; 기본 statetrade 경로모두 net 손실 | no-trade 경로의 순 기대값·미래 모든 regime 에서영구실패 |
| 연구 설계 | 초기 train10분/ eval30분, 후속 EDA 이미 본 자료, overlappinglabel 과다 중 비교 | 새 날짜 독립 확증·장기 Sharpe/ Calmar·capacity |

시장 수급 정보가 있어도 이미 현재 가격에 반영됐거나 makerfill이 불리한 표본을 선택할 수 있다. 이는 공통 메커니즘 해설이며 각 전략 손실을 단일 원인으로 인과 귀속한 검정은 아니다. 우리의 직접 근거는 진입/ exit별 markout·gross/ fee/ net·차단 사유다.

OFI 문헌은 미국 주식의 짧은 같은 구간 가격 충격과 depth의 관계를 보였다. 이 관계를 미래 30초 cryptoPnL로 자동 전용하지 않는다. [Cont·Kukanov·Stoikov](https://arxiv.org/abs/1011.6402). Queue imbalance 문헌의 목표는 다음 mid 변화 방향이며 fixed30초이동 크기와 다르다. [Gould·Bonart](https://arxiv.org/abs/1512.03492). 다층 OFI의 동시 가격 설명 향상도 이번 미래 수익의 검증은 아니다. [Multi-Level OFI](https://arxiv.org/abs/1907.06230).

개인의 유동성 비율 분석은 S&P500 tick의 일별/20일 MA와 RV 관계였다. 이번 crypto 초 단위·짧은 관측과 목표는 다르며 논문으로 인용하지 않는다. [퀀트대디원글](https://blog.naver.com/quantdaddy/223200751708); 당시 확인 내용은 R01에 보존했다.

## 8. 서버·환경 적합성과 현재 운용 상태

2026-10-07검토 서버는 2 vCPU/ RAM3.82GiB/ available 약 1.04 GiB, 당시 load4.74/4.51/4.14였다. 이는 10-12현재 재측정 값이 아니다. production 수집기·NATS·storage와 공유하므로 추가 16 조합에 CPU 여유가 확보됐다고 하지 않는다.

최종 network-none/ read-onlysource/.25 CPU/256 MiB 검증에서 92 tests,16000 events/16 routesbenchmark, wall25.850s·CPU6.460s·2476.7 events/ CPU-s·618.97 wall-events/ s, peakRSS49.22MiB, p50.338ms/ p9953.91ms 였다. synthetic50level L2·JSON·4 모듈·ledger·1216 syntheticfill/ 최대 pendingmarkout2784를 포함했다. 전체 WS/ nativedepth/ NATS/ storage/ rawjournal의 end-to-end 실측은 아니다.

활동 모델은 boundedbucket/ deque·10 Hz 정책·1 Hzmedian·offline 통계로 비용을 줄였다. CPU 부하·queuebacklog 가미래 alpha를 먹을 수 있고 최소 lot/ minnotional은 해당 venue/ 예산의 실행 가능성 문제다. 아직 strategycapacity·실측 p95/ p99/ 새 날짜 PnL을 확보하지 않았다. 당시 서버에는 review source 만복사했고 productionservice 나새주문 운용을 활성화하지 않았다. 현재 상태를 이 보고서 작성으로 변경하지 않는다.

## 9. 출처·재현·완전성 감사

아래는 이번 본문이 사용한 원안·코드·실측 출처다. 기존 잘못된 옛 파일명 REPORT_KO.md 링크 대신 현재 존재하는 1 st/2 nd 파일명을 사용한다. source_recovery와 catalogue.json은 본문의 등록 근거이며 원문 명령의 다른 파일·키·계정 인증 자료는 복사하지 않는다.

| ID | 자료 | 위치 |
| --- | --- | --- |
| L01 | v1 구현·운영 명세 | [L01](C:/Users/박제형/Desktop/VibeTrading/docs/LIQUIDITY_MM_BYBIT_TESTNET.md) |
| L02 | v1 setup·shadow·지연 replay 근거 | [L02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/liquidity-mm-setup-20261003.json) |
| V04 | v4 공통 실행 명세 | [V04](C:/Users/박제형/Desktop/VibeTrading/docs/FV_MM_V4_TESTNETS.md) |
| T01 | 조기 종료된 실제 테스트넷 점검 | [T01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_mm_v4_20261004/interrupted_execution_check/results.json) |
| T02 | 최종 30분 실제 테스트넷 결과 | [T02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_mm_v4_20261004/final_30min/results.json) |
| P01 | 최초 16 경로 public pilot 결과 | [P01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/final_30min/results.json) |
| P02 | 원형 pilot 주문 수명 감사 | [P02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/final_30min/order_lifecycle_audit.json) |
| D01 | 시계·복합 입력·S2 계수 분해 진단 | [D01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/REPORT_KO.md) |
| D02 | 동일 BTC 초기 3 모델·4 시계 비교 | [D02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/combination_metrics.csv) |
| F01 | 기초 지표 연구 1차 보고서 | [F01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/REPORT_KO_1st.md) |
| F02 | 기초 지표 전체 성능 | [F02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/indicator_metrics.csv) |
| F03 | 원형 공식 독립 검산 | [F03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_research_20261004/original_formula_audit.json) |
| R01 | 국면·grid·실제 가격 경로 2차 보고서 | [R01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md) |
| R02 | 국면별 방향 반응 전체 수치 | [R02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/regime_direction.csv) |
| R03 | onset 가격 반응 전체 수치 | [R03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/fv_regime_20261005/onset_curves.csv) |
| M01 | 상태 기반 개정·성과·서버 검토 | [M01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/REPORT_KO.md) |
| M02 | 최종 개정 69 케이스 재생 결과 | [M02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json) |
| M03 | 최종 개정 maker fill·markout 원장 | [M03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_sealed/run.sqlite) |
| M04 | 주문 의도 0건의 원인 진단 | [M04](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/no_intent_diagnosis.json) |
| M05 | 상태 전략 및 공통 feature 명세 | [M05](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/STRATEGY_SPEC.md) |
| M06 | 서버 격리 검증 | [M06](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/server_final/server_verification.json) |
| M07 | 운영 위험·수신 지연 제한 | [M07](C:/Users/박제형/Desktop/VibeTrading/docs/mm_state/RISK_CONTROLS.md) |
| C01 | 원형 OFI 전략 코드 | [C01](C:/Users/박제형/Desktop/VibeTrading/vibetrading/fv_mm/legacy/s1_net_liquidity.py) |
| C02 | v1 flow 모델 코드 | [C02](C:/Users/박제형/Desktop/VibeTrading/vibetrading/liquidity_mm/strategy.py) |
| C03 | 상태 기반 OFI 코드 | [C03](C:/Users/박제형/Desktop/VibeTrading/strategy/s1_net_liquidity.py) |
| C04 | 상태 기반 basis 코드 | [C04](C:/Users/박제형/Desktop/VibeTrading/strategy/s2_external_anchor.py) |
| C05 | 상태 기반 flow 코드 | [C05](C:/Users/박제형/Desktop/VibeTrading/strategy/s3_aggressive_flow.py) |
| C06 | 상태 기반 OBI 코드 | [C06](C:/Users/박제형/Desktop/VibeTrading/strategy/s4_obi.py) |
| C07 | 7개 통합 비교 정책 | [C07](C:/Users/박제형/Desktop/VibeTrading/vibetrading/mm_state/coordinator.py) |
| C08 | Turtle 프로토타입 | [C08](C:/Users/박제형/Desktop/VibeTrading/strategies/turtle_breakout.py) |
| C09 | 단일 캔들 돌파 예제 | [C09](C:/Users/박제형/Desktop/VibeTrading/strategies/example_signal.py) |
| C10 | 원형 anchor 코드 | [C10](C:/Users/박제형/Desktop/VibeTrading/vibetrading/fv_mm/legacy/s2_external_anchor.py) |
| C11 | 원형 공격 체결 코드 | [C11](C:/Users/박제형/Desktop/VibeTrading/vibetrading/fv_mm/legacy/s3_aggressive_flow.py) |
| C12 | 원형 OBI 코드 | [C12](C:/Users/박제형/Desktop/VibeTrading/vibetrading/fv_mm/legacy/s4_obi.py) |
| I01 | 상태 개정 초기 소스 스냅샷 | [I01](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/server_review/084ae2bf7e402373.tar.gz) |
| I02 | 통합 생성자 수정 전 소스 스냅샷 | [I02](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/server_review/646c9f6a48f4a3bf.tar.gz) |
| I03 | 최종 상태 개정 소스 스냅샷 | [I03](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/server_final/692487af63298f4a.tar.gz) |
| I04 | 중간 v2 재생 결과 - 최종 결과와 구분 | [I04](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_v2/summary.json) |
| I05 | 첫 재생 원장 - 완성된 성과 없음 | [I05](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay/run.sqlite) |
| I06 | 중간 final 재생 원장 - 최종 sealed와 구분 | [I06](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/mm_state_revision_20261007/replay_final/run.sqlite) |
| V1 | 대화 기록에서 회수한 v1 원문 | [V1](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v1_recorded_original.md) |
| V3 | 대화 기록에서 회수한 v3 원문 | [V3](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v3_recorded_original.md) |
| V4 | 대화 기록에서 회수한 v4 원문 | [V4](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/v4_recorded_original.md) |
| FEEDBACK | 대화 기록에서 회수한 feedback 원문 | [FEEDBACK](C:/Users/박제형/Desktop/VibeTrading/docs/evidence/all_strategies_20261012/source_recovery/feedback_recorded_original.md) |


카드 입력 8 필드 누락·ID 중복·출처 없는 카드·12 indicator/3 초기 combination/4 legacy/4 state/7 profile/7 onset의 coverage를 자동 검사한다. appendix는 8 실제 testnet 경로(2 실험),16 원형 pilot 경로(2 지연),48 state 단독 +21 통합 경로를 모두 포함한다. 지표별 최대 H·강도·각 asset 최고 값을 하나씩 독립 수익 전략으로 count 하지 않는다.

개선 전후 인접 셀이나 중간 replay_v2/ replay_final 결과를 최종 replay_sealed와 합치지 않았다. 본문 PDF와 Markdown은 같은 단일 보고서의 두 표현이고 catalogue.json·integrity.json은 확인용 부속 증거다. 새로운 가설 검정·주문 제출·운용 재개를 수행하지 않았다.
