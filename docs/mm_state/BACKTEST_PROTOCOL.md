# 재현·실행 절차

Python 코드가 전략별 하나의 `strategy/*.py` 파일에 있고 `StateStrategy`의 on_book/on_trade/signal/order_intents 인터페이스로 독립 운용 가능하다. `Coordinator`는 공유 feature engine 위에 선택 모듈을 올린다. 기존 testnet 전용 private Runner와 주문 API가 없는 mainnet public PilotRunner를 구분한다.

## 로컬 검증

```powershell
python -m pytest tests/test_mm_state_revision.py tests/test_fv_mm.py tests/test_fv_pilot.py tests/test_fv_analysis.py tests/test_fv_pilot_analysis.py tests/test_fv_prediction_diagnosis.py -q
python scripts/benchmark_mm_state.py --events 16000 --routes 16 --journal
python scripts/replay_mm_state_revision.py --output runtime_data/mm_state_replay_NEW
```

매 run은 새로운 output directory를 사용한다. 기존 ledger overwrite를 거부한다. 연구 dependencies는 `requirements-mm-state-research.txt`; statistical fitting은 로컬/offline에서 수행한다. 기존 raw SQLite는 read-only다.

replay는16전략 경로+동일 Bybit BTC 7개 ablation을 conservative1x, uncertain proportional-cancel1x, conservative4x latency의3케이스로 비교한다. 총69개의 **독립 가상 계좌**이며 한 실계좌의 합산 성과가 아니다. 모델 threshold는 training에서만 fit한다.

출력: `models.json`, `same_asset_models.json`, `run.sqlite`, `summary.json`, `markouts.csv`. gross/fees/net MTM, realized/unrealized, 잔여 재고, max drawdown, fills/intents/post-only rejection/부분 체결·취소 무체결 비율/first fill 대기와 markout missing 사유를 검토한다. actual fill 수량, 거래소 minimum/tick/lot, residual inventory를 숨기지 않는다.

## 새 public pilot 준비

```powershell
python scripts/collect_fv_mainnet.py --duration 600 --output runtime_data/mm_state_mainnet_train_NEW
python scripts/run_fv_pilot.py --models runtime_data/mm_state_mainnet_train_NEW/models.json --latency config/mm_state_latency_scenarios.json --duration 1800 --output runtime_data/mm_state_pilot_NEW
```

위 latency 파일은 configured sensitivity다. 실제 지연을 적용하려면 **각 venue 자체의** 검증된 profile로 교체한다. 같은 GRVT 측정값을 Arcus/Bitget에 복제한 이전 profile은 새 state pilot 입력으로 허용하지 않는다. frozen historical replay 모델은 fresh pilot용이 아니며 30분 expiry gate가 차단한다.

현재 mainnet pilot는 모든16 public route를 구독한다. 종목별 계좌 격리는 가상 계좌에서 수행된다. 실제 서버에서 실행 전 `RISK_CONTROLS.md`의 CPU·memory·feed freshness 기준을 확인해야 한다. 이번 작업에서는 public pilot를 새로30분 운영하거나 생산 collector를 재시작하지 않았다.

## 테스트넷 준비

```powershell
python scripts/collect_fv_public.py --duration 600 --output runtime_data/mm_state_testnet_train_NEW
python scripts/run_fv_testnets.py --models runtime_data/mm_state_testnet_train_NEW/models.json --duration 1800 --venue bybit --strategy S4 --output runtime_data/mm_state_shadow_NEW
```

기본은 shadow이며 외부 주문을 제출하지 않는다. 주문 제출 운용을 지시받은 다음 동일 명령에 `--trade-testnet`을 명시하여 실행한다. 환경은 testnet으로 고정한다. Bybit/GRVT 양쪽과4전략 운용은 `--venue all`, `--strategy` 생략이며, S1BTC/S2ETH/S3SOL/S4XRP로 종목을 분리한다. 전략별 동일 종목의 실제 계좌 비교는 독립 subaccount가 필요하다.

public testnet calibration은 인증 없는 instrument endpoint로 tick을 먼저 가져오고, GRVT public trade selector에 `@50`을 사용한다. 이 수정은 전략 전용 feed에 적용되며 생산 crypto collector를 교체하지 않는다. [GRVT public trade selector 공식 명세](https://api-docs.grvt.io/schemas/ws_trade_feed_selector_v1/).

## 서버 검증

`scripts/assess_mm_state_server.py`는 기존 SSH 설정을 이용한 read-only /proc/docker/NATS 진단이다. `scripts/verify_mm_state_on_server.py`는 source-only checksum directory에 복사하고 기존 Python image에서 **network none / CPU.25 / memory256MiB / source read-only**로 테스트·benchmark를 실행한다. `.env`, 자격 증명, 생산 volume, 전략 process service를 붙이지 않는다.

서버에 복사된 review source는 생산 경로 `/root/vibetrading`에 자동 반영되는 배포가 아니다. 기존 collector/NATS/storage service는 유지된다. 같은 단일 shared 계좌의 실제 orders를 검증 benchmark로 생성하지 않는다.
