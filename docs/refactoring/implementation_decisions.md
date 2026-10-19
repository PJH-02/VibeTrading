# 구현 단계의 추가 결정과 P03 인계

작성: 2026-10-18 KST · P03 최소 계약 `p03-minimum-offline-1`

이 문서는 READY 티켓에 묶인 기존 `contracts.md`, `parameters.json`, `adapter_capabilities.json`, `decisions.md`를 수정하지 않고 후속 구현 선택을 기록한다. root가 해당 source/contract/test receipt를 고정한 뒤 최종 READY·통합·gate를 판단한다. 설계 agent는 제품 파일, 원 workspace import, pytest, 운영 접근을 실행하지 않았다.

## ID-001 — P02 packet의 실제 구현 선택

상태: **현 코드 확인 / root 통합 검증 판정 대기**

root가 구현한 `bus.py::prepare_market_event`는 **첫 await 이전 동기 encoding + scalar snapshot**을 선택했다. `MarketEventPacket`은 frozen/slots 자료이며 durable `payload: bytes`, 필요한 stripped `live_payload: bytes`, subject와 `FrozenMarketSnapshot`을 갖는다. snapshot에는 ID/type/provider/venue/time/sequence/fast와 `InstrumentId`만 있다. `InstrumentId`는 frozen이며 정의된 필드가 문자열·enum·Decimal·nullable scalar이므로 mutable array/metadata 참조가 없다. bids/asks/metadata는 packet에 참조로 보관하지 않고 이미 소유한 bytes로만 남는다.

초기 [P02 설계 티켓](p02_encoding_ticket.json)의 full `model_copy(deep=True)`와 전체 재귀 snapshot 제안은 이 구현 선택으로 **대체**한다. full deepcopy를 encoding 앞에 추가해 같은 이벤트의 전체 순회를 늘리지 않는다. 같은 이벤트 루프의 caller가 enqueue await 이후 원래 bids/asks를 바꿔도 이미 만든 wire bytes와 scalar snapshot은 변하지 않는다는 범위다. 다른 OS thread가 동기 encoding 도중 같은 모델을 동시에 변경해도 안전하다고 주장하지 않는다.

기존 serializer와 raw crypto book live payload 제거 경로를 유지한다. public fake/custom override와 `__getattr__` wrapper는 concrete class의 명시 capability로만 packet 경로를 선택하므로 interception을 우회하지 않는다. readiness는 packet 길이를 재사용하되 성공·filter 의미를 바꾸지 않는다. root가 보고한 targeted 결과는 **62 passed**이며 이 설계 agent가 실행한 결과는 아니다. root가 최종 diff와 독립 리뷰·전체 회귀 receipt로 통합 판정을 남긴다.

## ID-002 — P03 최소 작업과 파일 소유권

상태: **구현용 설계 초안 / root READY 분해 필요**

[p03_market_ticket.json](p03_market_ticket.json)은 다음 순서의 구체 범위를 지정한다.

| 티켓 | 제품 수정 범위 | 확인할 동작 |
|---|---|---|
| P03-A-VERSIONED-STATE | `hot_path.py` | 기존 native book 적용 보존, apply/version/quality, immutable snapshot, 공유 preload·closed bar tail |
| P03-B-SHARED-DISPATCH | `strategy_contract.py`, `strategy_runner.py` | 기존 module/on_event 계약 유지, 관심 전략 dispatch, 한 subscription/reader/apply, quiet timer 수신 유지 |
| P03-C-RUNTIME-WIRING | `runtime.py` | 전략 job들을 한 shared supervisor에 연결하는 명시 shadow 경로와 안전한 종료 |
| P03-COMPAT-EXAMPLE | root 별도 티켓의 `strategies/example_signal.py` | 존재하는 Bar1mEvent API에 맞춘 호환 수정. 경제적 비교·action·가격·lookback 동결 |

P02 root가 `bus.py/runtime.py`의 편집을 끝내고 integrated hash를 제공하기 전 P03은 두 파일을 편집하지 않는다. P03의 runtime before hash는 P02 이전 값으로 복사하지 않고 최종 integrated source에서 다시 고정한다. 최초 source manifest는 최근 미추적 구현을 포함한 기준선이며 Git HEAD만 checkout해 시작하지 않는다.

새 일반 feature service/spool/DB/컨테이너는 이 최소 범위에 필요하지 않다. `models.py`와 provider wire envelope도 변경하지 않는다. 향후 clock domain·provider revision 필드를 추가할 때 extra=forbid인 v2 reader 호환과 migration을 별도 확정한다.

## ID-003 — 공유 writer와 불변 판단 snapshot

상태: **설계 고정 후보**

여러 전략이 `LiveStrategyRunner`마다 상태를 복원하는 구조를 한 engine의 `HotPathStore` writer와 하나의 `ParquetHistoryReader`로 묶는다. 입력 decode/dedupe/book apply를 한 번 수행하고 **메시지 전체 적용 → version/quality 확정 → immutable snapshot → 관심 callback들** 사이에는 await를 넣지 않는다. callback 결과를 모두 받은 뒤 transport/shadow sink/ACK를 처리한다.

내부 `(engine_incarnation,apply_seq)`는 producer cursor와 분리한다. version vector와 quality가 판단 snapshot에 포함된다. gap·crossed/empty·history conflict는 affected feed와 의존 전략을 차단한다. 이전 book이 legacy deque에 남아 있어도 INVALID snapshot은 사용할 수 없다. 무이벤트 timer에서도 freshness/window 만료를 반영한다.

`StrategyDataView`는 writer와 디스크 reader를 직접 노출하지 않는 snapshot facade로 연결한다. frozen `OrderBookEvent` 내부 subtype의 tuple bid/ask와 frozen metadata처럼 기존 BaseMarketEvent/OrderBookEvent/Bar1mEvent의 `isinstance` 및 읽는 필드를 유지하는 내부 표현을 사용한다. 외부 `models.py`나 wire schema를 바꾸지 않는다. 원 모델 scalar의 공유와 한 번 만든 immutable 값의 재사용을 우선하며 전략마다 전체 book을 deepcopy/정렬하지 않는다. `history()`의 기존 list[dict] 결과만 immutable backing에서 필요할 때 분리 생성해 호출자의 변경이 다른 판단을 오염시키지 않게 한다.

현재 1000 levels/side, top50 projection, max_books=512, native bridge와 sync_generation 규칙은 그대로 비교한다. max_books=1이나 일반 seq+1을 이 작업에 섞지 않는다.

## ID-004 — 선적재 history와 live closed bars의 범위

상태: **오프라인 계약 고정 / production history·bar transport 미검증**

최소 warmup 문법은 빈 dict 또는 `bars: 정수`다. 동일 feed의 요청은 합쳐 최대 필요량을 shared reader에서 한 번 읽는다. callback의 history API는 준비한 메모리만 읽으며 미선언/미준비 자료에 새 Parquet·SQLite·원격 조회를 시작하지 않는다. 부족하면 HISTORY_INCOMPLETE로 차단한다. N개 bar를 N초의 연속 window로 설명하지 않는다.

closed bar key는 provider/instrument/interval/open/close다. 같은 key와 같은 OHLCV는 중복이며, 충돌하는 값에 provider revision 증거가 없으면 REVISION_UNVERIFIED로 차단한다. close label과 실제 수신·가용 시각을 분리해 늦게 도착한 bar가 더 이른 replay 판단에 보이지 않게 한다. v2의 receive timestamp/clock 근거가 부족하면 causal readiness를 UNVERIFIED로 남긴다. `Bar1mEvent.closed=True`, open<close와 Decimal 값을 유지한다.

현재 `subscribe_live`는 `live.crypto.*`만 허용하고 일반 bar/ETF의 새 공급 경로가 완성된 것은 아니다. injected offline live-bar 시험은 가능하지만 이 결과를 production bar/ETF fanout 완료로 보고하지 않는다. 그 연결은 P05와 별도 ETF 입력 adapter 작업에 남는다. ETF의 09:01 end-label, 기준 ETF와 실행 ETF, 최근 복구/원장/제외 규칙은 읽고 회귀할 대상이다.

## ID-005 — replay/shadow와 live 출력 gate

상태: **설계 고정 후보 / live 활성화 미허용**

새 shared route는 먼저 replay/shadow로 연결한다. WARMING/VALIDATING/REPLAY/INVALID의 출력은 외부 주문이나 production signal subject에 발행하지 않는다. 역사 입력은 상태와 전략 내부 warmup에 사용할 수 있으나 역사 출력으로 live dedup를 소모하지 않는다. SHADOW의 결과에는 외부 sidecar로 판단 cursor/version/quality를 결합하며 `Signal` payload·ID 규칙을 임의 변경하지 않는다.

관심 구독은 exact instrument key 또는 검증된 venue/std_symbol/event_type 제약으로 컴파일한다. 알 수 없는 구독/warmup 표현은 추측해 wildcard/READY로 통과시키지 않는다. callback 하나의 오류는 그 job을 fault/blocked로 남기고 공유 state를 다시 적용하지 않는다. production 최대 전략 5개는 유지하고 10개 시험은 fixture로 engine을 직접 구성한 성능 비교다.

P04 owner와 P05 fanout 및 미확인 production clock/lease/메모리/history 값이 해결되기 전 LIVE 출력 경로를 열지 않는다. 이 최소 티켓 성공은 전체 G03 완료가 아니다. 모델/clock/revision 호환, 실제 전략 inventory, ETF 공유 입력/golden과 관련 window·용량 확인이 남는다.

## ID-006 — quiet 수신과 heartbeat의 구체 결함 수정

상태: **현 코드 확인 / P03-B에 포함**

현재 `wait_for(anext(messages), timeout)`은 timeout 때 async generator read를 취소하고 unsubscribe/finally를 실행할 수 있다. quiet timer 뒤에도 같은 pending `anext` task를 유지하는 `asyncio.wait` 또는 shield 방식으로 바꾼다. 동시에 pending read는 1개다. 실제 수신 후에만 다음 read를 만들고 shutdown 때 cancel/await 후 generator/bus를 한 번 닫는다. fixture는 두 번 이상의 quiet interval 후 이벤트가 도착하는 시나리오를 포함한다.

현재 heartbeat의 `getattr(self.strategy_module, 'STRATEGY_ID', self.strategy_module.__name__)`는 default 표현식이 먼저 평가되어 dataclass `StrategyModule`에 없는 `__name__` 접근으로 실패할 수 있다. 이미 존재하는 `strategy_id`를 사용하고 필요한 fallback은 `.module.__name__`에서 별도로 평가한다. 전략 ID를 새로 만들거나 알고리즘을 변경하지 않는다.

## ID-007 — 예제 전략 호환 수정은 분리

상태: **root 별도 compatibility 티켓 예정**

`strategies/example_signal.py`는 현재 없는 `models.Bar1m`을 import하고 `correction/end_ts_ns`와 없는 view `closed_bars()`를 사용한다. 원 상태가 성공한 baseline이라는 주장을 하지 않는다. API 호환은 `Bar1mEvent`, 실제 close timestamp, upstream revision/quality gate 및 새 closed_bars에 연결하는 명시 수정으로 검토한다. 원 breakout 비교 연산·ENTER_LONG/EXIT_LONG·reference price·4개 조회·30개 warmup과 경제적 판단을 고정한다.

P03-A/B의 기준선은 기존 HotPathStore/provider fixture와 정상 계약을 만족하는 deterministic synthetic module이다. 예제 호환은 원 조건을 보존한 별도 golden으로 검증하고 ETF `intraday_decision`·`etf_reference.py` 계산식과 섞지 않는다.

## ID-008 — P03 구현의 명시적 변경과 한계

상태: **root 구현 지시로 작성 / 격리 테스트·독립 리뷰 대기**

root가 P03 A/B 및 예제 compatibility 구현을 이 agent에 배정했다. 현 before hash와 READY 승인 근거, 편집 범위는 p03_market_ticket.json의 implementation_authorization에 고정했다. runtime.py/bus.py는 root가 소유하고 agent가 수정하지 않았다. P06 handoff core는 포함하지 않았다.

`LiveStrategyRunner`의 생성자와 run의 단일 job 반환 형태는 유지하지만 **production signal publish를 하지 않는 shadow adapter**로 변경했다. 생성한 signal ID tail과 immutable JSON decision diagnostic은 상한 안에 남는다. runtime의 공유 opt-in이 false인 legacy adapter 경로도 이 제한을 따른다. 이는 초기 replay/shadow 전환을 위한 의도적인 expected-diff이며 기존 외부 signal 송신과 같다고 주장하지 않는다. 실제 외부 출력은 별도 실행 gate를 통과한 경로에서 도입한다.

동일 event ID의 다른 normalized 내용은 bounded SHA-256 fingerprint로 감지하여 affected feed의 EVENT_ID_CONFLICT를 latch하고 book projection을 제거한다. freeze/fingerprint 검증은 dedup 등록 전에 끝낸다. fingerprint 보존 horizon은 기존 bounded dedup 크기이며 장기 전체 기록의 유일성 보증이 아니다. 충돌 latch는 새 engine의 명시 재구성 전까지 자동 snapshot으로 해제하지 않는다. 원시 frame의 exact bytes 비교와 달리 이 비교는 모든 기존 모델 필드의 결정적 normalized 내용 비교이며 동일 내용을 가진 JSON 공백 차이는 구분하지 않는다.

일반 native gap 뒤 새 snapshot은 book quality를 회복할 수 있다. `warmup_bars=0`이고 별도 이력/window 의존을 선언하지 않은 job은 새 snapshot만으로 shadow readiness를 회복할 수 있다. bar/window를 요구하는 job은 history의 HISTORY_INCOMPLETE latch 때문에 계속 차단한다. 선언하지 않은 history()를 callback에서 즉석 조회하지 않는다.

known_instruments가 없는 runtime 호출은 startup preload 요청이 없다. 이때 처음 본 feed의 bar를 메모리에 쌓는 live-window warmup만 가능하고 첫 이벤트 뒤 cold reader를 호출하지 않는다. 현재 generic bar transport도 없으므로 실제 운영의 30-bar warmup 완료나 ETF 연결 완료로 보고하지 않는다. 이력 가용 조건은 receive<=as_of와 close<=as_of를 모두 요구한다.
