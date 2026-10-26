# Production 10 — Venue/Product/Environment capability

상태: **TX00 조사 및 TX06 하위 구현**. CODE_COMPLETE/SANDBOX_VERIFIED/LIVE_CAPABILITY_VERIFIED를 별도로 기록한다. 현재 Spot·testnet probe·API 문서의 존재만으로 Perp connector READY를 선언하지 않는다. TOSS는 DATA_ONLY, 신규 Paper/Sandbox/Live account·execution route 금지다.

| Venue | 현재 재사용 소스 | 구현 단위와 부족분 | 환경 검증 |
|---|---|---|---|
| KIS stock/ETF | kis_execution.KisCashBroker, EtfJournal/EtfTradingLoop | 기존 Mock/Mock2 보존. client ID 로컬/ODNO lookup·UNKNOWN 수동대조 경계 유지 | 기존 회귀/운영 모드 보존; 이번 신규 mutation0 |
| Kiwoom | 데이터 collector, legacy broker | 실제 주문/취소/체결·계좌 capability 조사. 시장 데이터 수집을 실행 READY로 승격0 | BLOCKED_UNVERIFIED |
| Binance Perp | providers.crypto_perp, fv_mm venue adapter | 기존 brokers BinanceBrokerAdapter는 **Spot /api/v3**. USDⓈ-M/COIN-M 구분한 별도 Perp capability와 private/account/funding 필요 | sandbox/live 미검증 |
| Arcus Perp | extended_brokers의 Ed25519/WS, extended_perp 데이터 | 기존 signing/ct/goodTilTime/client ID 유지; cancel/private fills/account/funding·완전 조회 보완 | sandbox/live 미검증 |
| GRVT Perp | extended_brokers, fv_mm/venues·private | SDK/signature/nonce/clientID, account/positions·cancel/fill/funding와 paging 계약 재사용·검증 | 기존 testnet-pinned 도구≠live 준비 |
| Bybit Perp | bybit_execution, fv_mm/venues·private, liquidity_mm/exchange | linear/inverse·category/positionIdx·margin domain 분리, account/funding/private reconnect 검증 | testnet 응답은 live 의미 보증 아님 |
| Hyperliquid Perp | 데이터 provider 및 기존 generic 제한 adapter | 실제 signed exchange submit/cancel·cloid/status/fills/funding/account lifecycle 추가 필요 | sandbox/live 미검증 |

기본 상품은 임의 활성화하지 않는다. 각 metadata record는 native/internal symbol, linear/inverse, quote/settlement/collateral, multiplier/tick/lot/min-notional, position/margin modes, initial/maintenance/fee/funding 출처·version을 갖는다. 미확인 항목은 null/UNVERIFIED이고 의존하는 신규 위험을 차단한다.

공식 API 근거와 구체 completeness 목록은 TX00 산출을 여기에 연결한다. 응답 parsing·signature known-vector·client ID collision/expiry·paginated reconciliation·cancel/fill race는 offline scripted transport로 시험한다. 문서에 있는 기능도 실제 계좌에서 검증하지 않았으면 환경 gate는 BLOCKED_UNVERIFIED다.

현재 공식 확인 경로: [Binance USDⓈ-M](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info), [Bybit V5](https://bybit-exchange.github.io/docs/v5/intro), [GRVT](https://api-docs.grvt.io/), [Hyperliquid](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api), [Arcus perpetual WS](https://docs.arcus.xyz/guides/websocket-trading). 공식 기능/환경·단위를 해당 하위 티켓에 고정하고 바뀌면 재기준화한다.

## 현재 추가된 공통 경계

`vibetrading/perp_execution.py`의 `PerpScope`는 product/environment/account/settlement를 명시하며 Spot·COIN-M·inverse를 지원으로 오인하지 않는다. 다섯 adapter 모두 `request_mapping_complete=true`, **`code_complete=false`, `sandbox_verified=false`, `live_verified=false`, `owner_integrated=false`**다. native private frame normalizer와 실제 private connection/auth/subscribe/reconnect는 구별한다. concrete transport·private lifecycle·perpetual owner/reconciliation 연결이 완료 전이므로 factory가 주입된 transport로 구성된다는 사실을 venue 완료로 보고하지 않는다.

| 새 클래스 | 구현된 제한 범위 | 계속 차단되는 부분 |
|---|---|---|
| BinancePerpAdapter | USDⓈ-M 요청·HMAC·submit/cancel/query·account/fill/funding 매핑 | COIN-M/inverse, 실제 account mode·transport/private/owner |
| ArcusPerpAdapter | 기존 Ed25519 composition·WS submit/cancel·case-preserved client ID·조회/체결/펀딩 매핑 | USD quote/USDG settlement의 검증 없는 동등 취급·FX, concrete private session |
| GrvtPerpAdapter | 기존 signed-order composition·명시 chain/subaccount·uint32 nonce/uint64 client ID·paging | 실제 signer/session/account 권위·owner/stream 연결 |
| BybitPerpAdapter | explicit linear/category/position index·V5 서명·조회/체결/펀딩 매핑 | inverse/hedge·USD 평가액을 USDT balance로 간주, 실제 account/stream |
| HyperliquidPerpAdapter | canonical first-dex USDC Perp·SDK signing composition·cloid/status/cancel·account address 조회 | Spot/HIP-3·검증 없는 signer/account 혼용·실제 SDK/transport/stream 운영 증거 |

account의 position/margin mode를 instrument metadata에서 `net/cross`로 추정하지 않는다. 해당 필드는 null 및 `account_configuration_verified=false`다. UNKNOWN과 불완전한 paging은 reserve release/재제출의 근거가 아니다. 실제 mutation 직전 single-use owner permit를 확인하는 transport seam을 요구한다.

`SignerNonceStore`는 보호된 동일 root의 환경+signer wallet별 watermark를 FULL commit한 뒤 반환한다. 계좌/subaccount별로 nonce를 따로 발급하지 않는다. 페이지 크기·checkpoint pin·commit headroom을 검사하며, commit 후 실패도 nonce를 재사용하지 않는다. application byte guard는 OS quota가 아니고 다른 host/root를 fence하지 못한다. 실제 강제 종료 및 장기 파일시스템 장애 증거는 아직 없다.
