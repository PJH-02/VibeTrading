# v4 독립 FV 전략과 테스트넷 운용

사용자 요청의 v3·v4 중 후보별 재구성이 완료된 v4를 수식 기준으로 적용했다. 첨부 문서는 전략 명세로 사용하며, 계정 접근·실행 지시는 사용자의 채팅 요청을 따른다. 이전 v1 구현은 별도 패키지에 보존되어 있다.

## 전략 파일과 경로

| 후보 | 전략 Python 파일 | 최초 배정 종목 | 가격 단위 입력 x |
|---|---|---|---|
| S1 순유동성 압력 | `strategy/s1_net_liquidity.py` | BTC | spread × price-aware OFI / 평균 BBO 깊이 |
| S2 동일자산 외부 기준가격 | `strategy/s2_external_anchor.py` | ETH | Binance perp mid × exp(고정 log basis) − target mid |
| S3 공격 체결 흐름 | `strategy/s3_aggressive_flow.py` | SOL | spread × 순공격 체결량 / 평균 BBO 깊이 |
| S4 OBI | `strategy/s4_obi.py` | XRP | spread × (bid 잔량−ask 잔량)/(bid 잔량+ask 잔량) |

각 파일의 `Strategy.on_book`, `on_trade`, `signal`, `order_intents`가 전략 경로다. 다른 후보를 진입 승인 조건으로 사용하지 않는다. S1·S3의 활동량 창은 현재 깊이의 nu=1배가 될 때까지 과거로 확장하고 경계 이벤트를 비례 배분한다. S1에 공격 체결량을 재차 더하지 않는다. S4는 현재 잔량만 사용한다. EWMA나 5초 결정 bin을 사용하지 않는다.

`vibetrading/fv_mm`은 두 거래소의 데이터를 정규화하고 공통 주문, 위험 한도, 체결 장부와 종료를 담당한다. 실시간 모델은 입력 이벤트마다 갱신된다. 통계용 1초 표본 저장과 REST 대사 간격은 신호 관측창·진입 지속시간이 아니다.

## 사전 보정

```powershell
python -m pip install -r requirements-fv-mm.txt
python scripts/collect_fv_public.py --duration 600 --output runtime_data/precalibration_NEW
```

이 명령은 공개 데이터만 수집한다. 처음 25%에서 S2의 log basis 중앙값을 추정한다. 나머지 학습 구간에서 H=30초 미래 mid 이동에 대한 절편 없는 단일 기울기를 각 후보·거래소별로 구한다. 학습 종료 시각은 전체 구간의 65%이며 label horizon을 purge한다. 그 뒤 별도 구간에서 방향별 과대예측 오차의 90분위수 u를 추정한다. 평가 중에는 계수·basis·u를 바꾸지 않는다.

신호 또는 label이 부족하거나 x² 합이 0인 모델은 사용하지 않는다. S2의 lambda는 0 < lambda ≤ 1이어야 한다. 잘못된 lambda를 잘라서 범위 안으로 넣지 않는다. 학습 표본 수는 유효한 보정의 최소 자료 요건이며 온라인 거래 횟수 필터가 아니다. 10분 자료는 실행 파이프라인을 점검하는 초기 실험이며 모델의 통계적 수익성 검증으로 취급하지 않는다.

## 테스트넷 실행

```powershell
# 네 전략 × 두 거래소를 동시에 30분 평가
python scripts/run_fv_testnets.py --models runtime_data/precalibration_NEW/models.json --output runtime_data/fv_NEW --duration 1800 --trade-testnet

# 전략별 별도 운용 예시
python scripts/run_fv_testnets.py --models runtime_data/precalibration_NEW/models.json --output runtime_data/fv_S1_NEW --duration 1800 --strategy S1 --venue bybit --trade-testnet
```

`--trade-testnet`을 생략하면 shadow intent만 기록한다. 주문과 인증 주소는 코드에 고정된 테스트넷 주소를 사용한다. `CRYPTO_LIVE_ENABLED`와 전역 live 스위치를 켜는 절차는 필요하지 않다. 각 실행은 새 출력 폴더를 사용하며 기존 장부에 이어 쓰지 않는다. 개별 운용에도 계정 단위 손실·gross 한도를 적용한다.

동일 계정·동일 자산의 반대 전략이 재고를 상쇄하지 않도록 사용자가 승인한 종목 분리를 적용했다. 이 실험에서 전략별 순손익은 서로 다른 종목의 결과이므로 직접적인 전략 우열을 뜻하지 않는다. 같은 종목의 동시 거래 비교에는 전략별 계정 또는 서브계정이 필요하다.

초기 실행 정책은 FV가 예측하는 방향의 신규 주문 한 개와 실제 재고의 reduce-only 감축이다. 양방향 신규 주문은 아직 방향별 체결 성과가 검증되지 않아 사용하지 않는다. cost C는 maker 진입·taker 감축 수수료 예산, 1 tick, 사전 보정한 u의 합이다. quote half-width는 max(spread/2, C)이며 bid/ask는 v4 식을 그대로 tick 반올림한다. Bybit 수수료는 계정에서 조회한다. GRVT는 리베이트를 선반영하지 않고 공개 최대 perpetual taker 0.045%를 감축 예산으로 사용한다. 실제 체결 수수료는 체결 로그에서 계산한다.

최초 주문 명목 상한은 150 USDT, equity의 1.5%, 표시 깊이의 10% 중 최소값이다. 거래소의 실제 최소 수량·명목 금액을 넘지 못하면 주문하지 않는다. 최소 주문을 만들려고 위험 크기를 올리지 않는다. 계정 gross 상한은 equity의 8%, 손실과 drawdown 중단 기준은 각각 0.2%, margin 상한은 15%다. 재고는 최대 120초 보유하며 가격 근거가 사라진 경우 maker 감축 유예는 15초다. 포지션 가격 손실 한도 35bp 또는 시간 초과 뒤에는 최대 20bp의 가격 여유를 둔 IOC reduce-only 감축을 시도한다. 체결 실패를 청산 성공으로 처리하지 않는다.

주문은 제출 전에 SQLite에 저장한다. partial fill을 exec_id로 중복 제거한다. 취소 ACK 이후에도 terminal 상태를 확인할 때까지 남은 수량을 예약한다. 제출 통신 결과가 불명확하면 주문을 재전송하지 않고 신규 노출을 중지한 뒤 client ID로 대사한다. 주문 목표 가격·잔량·역할이 바뀌었을 때만 취소·교체하며, 현재 FV에 따라 감축가격도 바뀐다. 알려진 자체 resting 주문은 외부 BBO 잔량에서 제외한다. private 공백 시 신규 노출을 제한하고 REST로 대사한다.

## 종료와 결과

평가 폴더에 `STOP` 파일을 만들거나 프로세스에 SIGTERM/Ctrl+C를 보내면 신규 노출을 중단하고 종료 감축을 시작한다. 정상 30분 종료에서도 maker 감축 15초 이후 IOC 감축으로 전환한다. 감축 대사 제한은 120초다. 종료 후 `complete.json`의 complete와 `account_end.json`의 포지션·주문을 확인한다. 불확실한 잔여 주문이 있으면 `attention.json`을 남기며 정상 종료로 보고하지 않는다.

```powershell
python scripts/analyze_fv_run.py runtime_data/fv_NEW --output docs/evidence/fv_NEW
```

보고서는 실제 체결 수량·가격·수수료·funding으로 순손익을 계산한다. 미체결 주문·가격 터치를 수익으로 계산하지 않는다. 완전 왕복, 정상/강제 감축, 관측 MDD, 제출·체결 명목 금액을 포함한다. 30분만으로 연환산 Sharpe·Calmar·VaR/ES를 산출하지 않는다. 체결이 없으면 정상 청산률과 실제 주문 지연도 미측정이다.

가설 검정은 동결 모델과 동일 평가 구간의 미래 mid를 비교한다. H=10·30·60초, 비중첩 쌍 수, 60초 시간 블록 bootstrap 신뢰구간, 다중 비교 BH 보정, mid 유지 예측 대비 RMSE를 기록한다. 시간 블록이 10개 미만이면 추론을 보류한다. 실제 receive→native private 활성 주문 알림 또는 REST 활성 관측의 지연 분위수를 얻은 경우에만 도착 후 잔여 이동을 계산한다. REST 관측에는 조회 간격이 포함된다. 관측이 한 개면 안정적인 p95 분포를 뜻하지 않는다. 활성 관측 시각은 실제 활성화 시각의 대용치이고, 거래소 네트워크 송신 시각은 관측하지 못한다.

## 서버 설정과 원본

2026-10-04 실행은 IP 제한에 맞춰 서버 `49.50.141.106`의 별도 폴더 `/opt/vibetrading/experiments/fv-mm-v4-20261004`에서 수행한다. 최신 테스트넷 키의 원본은 사용자가 갱신한 `/root/vibetrading/.env`다. `scripts/refresh_testnet_env.py SOURCE DESTINATION`은 테스트넷 필드만 복사하고 값을 출력하지 않는다. 실험 `.env` 권한은 600이다. 기존 운영 컨테이너를 수정하거나 재시작하지 않았다.

설정 갱신 뒤 `.venv/bin/python scripts/check_fv_connections.py`로 계정과 native private feed를 읽기 전용 확인할 수 있다. 서버에서 `scripts/launch_fv_run.py`는 별도 프로세스를 실행하고 평가 폴더에 PID와 로그를 남긴다. 자동 반복 재시작·영구 운용·예약 자동화는 설치하지 않는다.

## 2026-10-04 완료 기록

두 거래소 × 네 전략의 최종 30분 평가는 KST 15:37:41~16:07:42에 완료했다. 실제 체결 0건, Bybit S4 미체결 취소 주문 1건, 전략 순손익 모두 0이었다. Bybit S2·GRVT S3/S4는 보정 요건 미충족으로 신규 진입을 막고 관측했다. 종료 포지션·미체결 주문은 모두 0이다. 성과와 가설 해석은 `docs/evidence/fv_mm_v4_20261004/final_30min/REPORT_KO.md`, 앞선 GRVT 체결 점검은 `interrupted_execution_check/REPORT_KO.md`에 별도 보관했다.

참고 API: [Bybit 주문](https://bybit-exchange.github.io/docs/v5/order/create-order), [GRVT 거래 API](https://api-docs.grvt.io/trading_api/), [GRVT 공식 서명 SDK](https://github.com/gravity-technologies/grvt-pysdk/blob/main/src/pysdk/grvt_raw_signing.py), [GRVT 수수료](https://help.grvt.io/en/articles/9614699-how-does-grvt-s-fee-model-work).
