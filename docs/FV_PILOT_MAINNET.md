# 실거래 호가 기반 FV 모의 거래

네 전략 파일은 기존 `strategy/s1_net_liquidity.py`, `s2_external_anchor.py`, `s3_aggressive_flow.py`, `s4_obi.py`를 그대로 사용한다. S1=BTC, S2=ETH, S3=SOL, S4=XRP를 Arcus·Bybit·Bitget·GRVT에서 동시에 평가한다. 각 경로의 모의 재고와 의도는 분리하며, 거래소별 가상 자본 10,000과 기존 위험 한도를 적용한다. Arcus는 USD, 나머지는 USDT이며 손익을 환산해 합산하지 않는다.

`vibetrading/fv_pilot`에는 공개 데이터 연결, 의도 연산과 가정 체결만 있다. `.env`·서명키·계정·거래 주문 클라이언트를 로드하지 않는다. HTTP POST는 공개 GRVT 상품 정의 조회뿐이며 주문·취소·계정 API를 호출하지 않는다. 실제 매칭 엔진이나 계좌에서 이 주문이 체결됐다는 의미가 아니다.

## 가격 데이터와 보정

Bybit USDT linear·Bitget USDT futures·GRVT USDT perpetual·Arcus USD perpetual의 mainnet 호가 및 공개 거래를 사용한다. S2 외부 기준은 Binance USDT perpetual BBO mid다. Arcus USD와 Binance USDT의 수준 차이는 사전 log basis에 포함되며 동결한다. USD/USDT 환율을 별도로 측정해 변환한 기준가격은 아니다.

S1·S3의 활동량 창, S4의 순간 OBI, S2의 lambda 범위와 quote 식은 v4 구현을 따른다. 실거래 데이터의 사전 구간에서 각 거래소·전략별 계수를 재보정하고 평가 중 동결한다. 30초 예측 label을 시간 순서로 분리·purge하고, 이후 구간에서 과대예측 오차의 90분위수를 보정한다. 표본이나 활동량이 부족하거나 S2 lambda가 (0,1] 밖이면 신규 진입을 막고 원시 입력을 기록한다.

Bitget에서 실제로 받은 `books50` 프레임은 `action=snapshot`, `pseq=0`의 연속 전체 갱신이었다. 호가 맵은 전체 교체하되 증가하는 sequence의 연속 프레임 사이에서는 S1/S3 활동량 창을 유지한다. 재연결·sequence 역행·검출된 delta 공백에서는 모델 이력을 초기화한다. 이 의미를 고친 뒤 첫 사전 구간의 Bitget S1/S3는 동일한 원본 BBO·거래 수신 시각으로 재계산했다. 평가 구간 데이터를 계수에 사용하지 않았고 원본 DB를 변경하지 않았다. `mainnet_models_verified_20261004.audit.json`에 원본 해시와 전후 결과가 있다.

Arcus trade의 native `side`를 사용하며 방향이 없는 거래는 S3와 maker 가정 체결에서 제외한다. 공개 BBO 프레임에 source timestamp가 없는 경우 0으로 기록하고 임의의 거래소 시각을 만들지 않는다. 최초 GRVT 거래 snapshot 등 현재 호가보다 10초 이상 오래된 거래는 모델 입력에서 제외한다. source 순서가 가정 주문 활성 시점보다 이른 거래도 체결 근거로 사용하지 않는다.

## 지연과 체결 규칙

`config/fv_pilot_latency.json`은 기존 testnet 원장에서 생성했다. Bybit 수신→활성 관측 325.0ms(1개), 취소 요청→terminal 관측 196.1ms(1개), GRVT 수신→활성 관측의 표본 95분위수 1,080.5ms(3개)를 사용한다. 활성 관측은 private 알림 또는 REST 첫 관측 대용 시각이며 실제 매칭 엔진 도착 시각을 측정한 것은 아니다.

Arcus·Bitget에 실측 주문 지연 자료가 없어 두 거래소의 큰 값을 대용했다. GRVT의 유효 취소 지연 표본도 없어 주문 지연으로 대용했다. mainnet p95 보장치로 해석하지 않는다. 같은 실시간 입력을 독립적인 기본·2배 지연 모형으로 비교한다. 공개 시장 데이터는 이미 서버에 도착한 값이므로 수신 뒤 주문 경로 지연을 한 번 추가한다.

1. 주문 의도를 만들면 즉시 장부에 저장하고 수신 시각 기준 지연 이후에 가정 활성화한다. 취소 의도 역시 지연 후 효력이 생기며 그 전까지 잔량과 노출을 유지한다.
2. 활성 시점의 관측 호가와 가격이 교차한 maker 의도는 PostOnly 거절로 처리한다. 단순 호가 터치를 체결로 계산하지 않는다.
3. Maker queue는 활성 시점 표시 잔량 전부를 앞에 둔다. 같은 가격의 실제 반대 방향 거래량으로 queue를 소진한 뒤 잔량만 부분 체결한다. 표시 잔량 감소·취소에 의한 queue 개선은 가정하지 않는다.
4. 관측된 가격을 실제 거래가 엄격하게 지나가면 가격 우선순위를 적용하되 해당 거래량까지만 가정 체결한다. 관측 깊이 밖을 빈 queue로 보지 않는다.
5. IOC 감축은 지연 후 관측 L2 호가를 보호 가격까지 소진한다. 부족하면 부분 체결·미체결로 처리하며 같은 book 버전의 이미 소진한 수량을 재사용하지 않는다.
6. 모의 주문은 실제 호가에 포함되지 않으므로 표시 잔량에서 자체 가정 수량을 빼지 않는다. 모의 체결은 public 거래 수신 시 재고에 반영하며 private 체결 인지 지연은 별도 모델링하지 않는다. 숨은 유동성·실제 주문의 시장 영향·실제 queue 위치는 관측할 수 없다.

모형은 표시 queue와 공개 거래량을 사용하는 가정이다. 실제 매칭 엔진의 순위·체결 가능성을 확정한 결과가 아니다.

## 비용과 위험

Bybit는 공개 VIP0 maker/taker 0.02%/0.055%, Bitget은 공개 상품 요율, Arcus는 공개 Base tier, GRVT는 maker 0%·공개 최대 perpetual taker 0.045%를 적용한다. VIP 할인·리베이트를 선반영하지 않는다. 비용 C는 maker 진입·taker 감축 수수료, 1 tick, 사전 오차 여유의 합이다. 비용 기준을 넘지 못하면 후보 bid/ask만 기록하고 가정 주문을 활성화하지 않는다.

최초 주문 단위는 가상 equity 1.5%·150 명목 금액·평균 BBO 깊이 10%의 최소값을 거래소 수량 단위로 내림한다. 거래소별 gross 8%, 손실·drawdown 0.2%, 재고 보유 120초, 포지션 가격 손실 35bp 한도를 사용한다. 종료에서는 신규 노출을 막고 maker 감축 15초 후 보호 가격의 IOC 감축으로 전환한다. 종료 대사는 최대 120초이며 미청산 가정 재고는 last mid 평가로 남긴다.

손익은 **funding 제외 가정 체결 손익**이다. 현재 funding rate를 실제 정산액으로 간주하지 않는다. 체결이 없으면 funding 노출도 0이고, 체결이 있으면 미측정 funding을 분리한다. 연환산 Sharpe·Calmar는 산출하지 않는다.

## 실행과 원본

```powershell
python -m pip install -r requirements-fv-pilot.txt
python scripts/collect_fv_mainnet.py --duration 600 --output runtime_data/pilot_training_NEW
python scripts/build_fv_pilot_latency.py --source-root runtime_data/fv_mm/server_checks/runtime_data --output config/fv_pilot_latency.json
python scripts/run_fv_pilot.py --duration 1800 --models runtime_data/pilot_training_NEW/models.json --latency config/fv_pilot_latency.json --output runtime_data/pilot_NEW
python scripts/analyze_fv_pilot.py runtime_data/pilot_NEW --output docs/evidence/pilot_NEW
python scripts/plot_fv_report.py runtime_data/pilot_NEW --output docs/evidence/pilot_NEW
```

`pilot_decisions`는 후보 가격과 차단 사유가 바뀐 시점을, `pilot_orders`는 경제적으로 허용된 모의 주문 의도를, `pilot_fills`는 가정 체결을 기록한다. `public_frames`·`public_trades`·`quotes`·`samples`로 근거를 남긴다. `STOP` 파일 또는 SIGTERM은 신규 의도를 막고 종료 모형을 진행한다.

미래 가격 검증은 H=30초의 주 결과와 H=10·60초 보조 결과를 기록한다. 60초 블록 bootstrap과 BH 보정에 더해, H=30을 120초 블록으로 재검사한 CI를 민감도 자료로 남긴다. 시간이 겹치는 사건을 독립 표본으로 보고 p값을 계산하지 않는다. 시간 블록이 10개 미만이면 추론을 보류한다.

2026-10-04 실행 서버는 `49.50.141.106`의 `/opt/vibetrading/experiments/fv-pilot-20261004`다. 기존 운영 컨테이너와 testnet 실험을 수정하지 않고 별도 디렉터리·프로세스에서 실행했다. 주문 키를 이 폴더로 복사하지 않았다.

2026-10-04 19:21:55–19:51:55 KST의 16개 조합 동시 평가를 완료했다. 기본 지연에서 Arcus S3 14건·Bitget S1 1건의 의도, 가정 체결 0건, 종료 재고 0이었다. 2배 지연에서도 체결은 없었다. 상세 성과·모델 차단 사유·가설 검정·원본 무결성은 [최종 보고서](evidence/fv_pilot_mainnet_20261004/final_30min/REPORT_KO.md)에 있다.

이후 같은 원본의 100ms~300초 예측 시계, 동일 BTC의 OFI·체결·OBI·표시 잔량 변화 조합, S2 계수 분해와 Binance 기준가격·운영 수집기 상태를 읽기 전용으로 검토했다. [후속 예측력 진단](evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/REPORT_KO.md)에 수치와 그림이 있다. 미래 목표는 t+H까지 수신한 마지막 BBO로 정의해 기존 1초 표본 이후 목표와 구분했고, 대안 계수는 원래 별도 사전 구간에만 맞췄다. 평가 기간을 보고 수행한 탐색적 진단이며 원래 운용 성과를 대체하지 않는다.

공식 데이터/API 근거: [Bybit 호가](https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook), [Bybit 수수료](https://www.bybit.com/en/help-center/article/Futures-Contracts-Fees-Explained), [Bitget 호가](https://www.bitget.com/docs/uta/websocket/public/Order-Book-Channel), [Bitget 거래](https://www.bitget.com/docs/uta/websocket/public/New-Trades-Channel), [Bitget 상품 설정](https://www.bitget.com/legacy-docs/uta/public/Instruments), [GRVT 공개 스트림](https://api-docs.grvt.io/market_data_streams/), [GRVT 수수료](https://help.grvt.io/en/articles/9614699-how-does-grvt-s-fee-model-work), [Arcus 상품 설정](https://api.arcus.xyz/v1/markets), [Arcus 수수료 등급](https://api.arcus.xyz/v1/feetiers).
