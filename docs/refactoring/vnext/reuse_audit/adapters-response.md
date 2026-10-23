# Read-only adapters reuse audit — HEAD `4977ab509490173e312183f32d47f862d94ee0bf`

## 0. Method, commands, and evidence limits

**Commands run:** none. No shell, no network, no SDK/account calls, no tests, no writes. Evidence comes only from `Read` calls on the ticket-named files:

- `docs/refactoring/vnext/reuse_audit/function_inventory_adapters.json`
- `docs/tickets/REUSE-current-TX-contract.md`
- `src/vibetrading/brokers.py`
- `src/vibetrading/extended_brokers.py`
- `src/vibetrading/execution.py`
- `src/vibetrading/perp_execution.py`
- `src/vibetrading/providers/extended_perp.py`

**Test results:** none executed. This is static evidence only; it cannot prove live, testnet, private-stream, account, or owner integration.

**Inventory caveat:** `function_inventory_adapters.json` is a single-line JSON document and was truncated by the reader at its 2,000-character line limit. All `path:line` references below were verified directly from the five source files, not inferred from the truncated inventory tail.

**Legend:** `KEEP` preserve; `REUSE` call existing code from the new path; `ADAPT` local bridge/safety/env fix only; `COMPLETE_MISSING` only where a required closed-scope gap is proven; `REPLACE_PROVEN_DEFECT` concrete defect with source evidence; `RETIRE` plan-only, none recommended here.

---

## 1. Execution path chains

| # | Venue / product / env | Exact chain and caller evidence | Classification |
|---|---|---|---|
| 1 | Binance **spot**, testnet/live | `vibetrading.brokers.broker_for_name` (`brokers.py:300`) → `BinanceBrokerAdapter` (`:38`) → `submit_order` (`:70`) → `_signed_request` (`:90`) → `aiohttp` (`:100-105`) → `api.binance.com` or `testnet.binance.vision` (`:45-49`). `BINANCE_USE_TESTNET` default **false** (`:41`). | Legacy live-HTTP path. Caller of `broker_for_name` is **dynamic UNKNOWN** in this excerpt. |
| 2 | Kiwoom **KRX equity/ETF**, mock/real | `broker_for_name` (`:300`) → `KiwoomBrokerAdapter` (`:115`) → `_post_api` (`:162`) → `_access_token` (`:178`) → token cache (`:200-224`). Mock default **true** (`:120`). | Legacy live-HTTP path; caller **dynamic UNKNOWN**. |
| 3 | KIS **cash equity/ETF**, mock/real | `broker_for_name` (`:300`) → `KisBrokerAdapter` (`:230`) → inherited `KisCashBroker` imported at `:227`. Mode defaults to `KIS_USE_MOCK` true (`:235`). | Direct subclass of existing owner; `KisCashBroker` body is outside excerpt, so behavior **UNKNOWN**. |
| 4 | Toss **DATA_ONLY** | `broker_for_name` (`:300`) → `TossBrokerAdapter` (`:271`) → `TossAuthClient` (`:273`) → `account_state` (`:282`) / `order_status` (`:288`). `submit_order` always rejects (`:285-286`). | Legacy read-only path; mutation is correctly fail-closed. Caller **dynamic UNKNOWN**. |
| 5 | Hyperliquid **perp/read-only**, mainnet hardcoded | `broker_for_name` (`:300`) → `HyperliquidBrokerAdapter` (`:241`) → public `POST https://api.hyperliquid.xyz/info` (`:253`). `submit_order` always raises (`:260-262`); `order_status` is a stub (`:264-265`). | Legacy data-only path; no env toggle in this class. Caller **dynamic UNKNOWN**. |
| 6 | Arcus **linear perp**, testnet/live | `broker_for_name` (`:300`) → lazy import (`:301`) → `ArcusBrokerAdapter` (`extended_brokers.py:112`) → `submit_order` (`:187`) → `_rpc` (`:70`) → `websockets.connect` (`:78`) → `wss://api[.testnet].arcus.xyz/v1/ws` (`:118-120`). Signing: `prepare_order` (`:148`) → `_signer` (`:132`). | Legacy live-WS path. Caller **dynamic UNKNOWN**. |
| 7 | GRVT **linear perp**, testnet/live | `broker_for_name` (`:300`) → `GrvtBrokerAdapter` (`extended_brokers.py:216`) → `connect` auth (`:237`) → `submit_order` (`:306`) → `_rpc` (`:70`) → `wss://trades.../ws/full` (`:227`). Signing: `prepare_order` (`:262`). | Legacy live-WS path. Caller **dynamic UNKNOWN**. |
| 8 | 5 perp venues, explicit sandbox/live | `vibetrading.perp_execution.make_perp_adapter` (`perp_execution.py:1308`) → `BinancePerpAdapter` / `BybitPerpAdapter` / `GrvtPerpAdapter` / `ArcusPerpAdapter` / `HyperliquidPerpAdapter` (`:1312-1313`). Requires injected `Transport` (`:131`). | Canonical perp factory. Caller **dynamic UNKNOWN**; no CLI/runtime caller in excerpt. |
| 9 | 5 perp venues, signer composition | `perp_execution.GrvtSigningComposition.prepare` (`:181`) → `GrvtBrokerAdapter.prepare_order` (`extended_brokers.py:262` via `:188`). `ArcusSigningComposition.prepare/cancel` (`:834`, `:839`) → `ArcusBrokerAdapter.prepare_order/_signer` (`extended_brokers.py:148`, `:132` via `:837`, `:850`). `HyperliquidSdkSigningComposition.sign` (`:1098`) → external `sign_l1_action`. | **Direct composition**, not duplicate live transport. Fix GRVT ID mismatch before use. |
| 10 | Arcus/GRVT **public perp market data** | `ExtendedPerpCollector` (`providers/extended_perp.py:21`) → `run` (`:34`) → `_arcus` (`:44`) / `_grvt` (`:61`) → mainnet WS (`:46`, `:53`, `:62`) → `bus.publish_market_event` (`:117`, `:159`). | Direct subclass of `CryptoPerpCollector` (`:17`). Caller **dynamic UNKNOWN**. |
| 11 | Generic intent/admission seam | `execution.OrderIntentFactory.from_signal` (`execution.py:54`) → `RuntimeStore.record_order_intent` (`:85`); `ExecutionSafetyGate.validate` (`:33`); `BrokerAdapter` imported by `brokers.py:16`. | Direct import/structural implementation. No external caller in excerpt. |

**Cloud transport:** no `storage_backend`, cloud uploader, archive, or transport-storage symbol appears in the five assigned source files. Cloud preservation is **not in this excerpt**; no cloud uploader should be added from this audit.

---

## 2. Function-level reuse matrix

### 2.1 `execution.py` contract

| Path:line | Class.method | Venue/product | Role | Caller/evidence | Decision |
|---|---|---|---|---|---|
| `src/vibetrading/execution.py:11` | `BrokerAdapter` | all legacy | 4-method protocol | imported at `brokers.py:16`; implementations are structural | **KEEP**; do not add `cancel` to this protocol |
| `src/vibetrading/execution.py:20` | `AccountExecutionConfig` | all | config | internal | **KEEP** |
| `src/vibetrading/execution.py:33` | `ExecutionSafetyGate.validate` | all | admission gate | no caller in excerpt | **REUSE**; ADAPT only to canonical scope/permit |
| `src/vibetrading/execution.py:51` | `OrderIntentFactory.__init__` | all | intent store wiring | internal | **KEEP** |
| `src/vibetrading/execution.py:54` | `OrderIntentFactory.from_signal` | all | intent seam | no external caller in excerpt | **REUSE** (TX01 partial) |
| `src/vibetrading/execution.py:90` | `UnimplementedBrokerAdapter.*` | none | test placeholder | no caller in excerpt | **KEEP/UNKNOWN** |

### 2.2 `brokers.py` legacy adapters

| Path:line | Class.method | Venue/product | Role | Caller/evidence | Decision |
|---|---|---|---|---|---|
| `src/vibetrading/brokers.py:23` | `_bool`; `:29 _json_default` | — | helpers | internal | **KEEP** |
| `src/vibetrading/brokers.py:38` | `BinanceBrokerAdapter.__init__` | Binance spot; testnet default false | env/credentials | `broker_for_name` | **KEEP** |
| `src/vibetrading/brokers.py:51` | `BinanceBrokerAdapter.connect` | Binance spot | auth probe | internal | **KEEP** |
| `src/vibetrading/brokers.py:56` | `BinanceBrokerAdapter.account_state` | Binance spot | balance summary | internal | **KEEP** |
| `src/vibetrading/brokers.py:70` | `BinanceBrokerAdapter.submit_order` | Binance spot | mutation | `broker_for_name` | **ADAPT** client-ID handling; see defect 3 |
| `src/vibetrading/brokers.py:87` | `BinanceBrokerAdapter.order_status` | Binance spot | query | internal | **ADAPT** to use same ID as submit |
| `src/vibetrading/brokers.py:90` | `BinanceBrokerAdapter._signed_request` | Binance spot | signed HTTP | internal | **KEEP**; new session per call |
| `src/vibetrading/brokers.py:115` | `KiwoomBrokerAdapter.__init__`; `:123 connect` | Kiwoom equity/ETF; mock default true | env/auth | `broker_for_name` | **KEEP** |
| `src/vibetrading/brokers.py:126` | `KiwoomBrokerAdapter.account_state` | Kiwoom | summary | internal | **KEEP** |
| `src/vibetrading/brokers.py:143` | `KiwoomBrokerAdapter.submit_order` | Kiwoom equity/ETF | mutation | internal | **KEEP** |
| `src/vibetrading/brokers.py:159` | `KiwoomBrokerAdapter.order_status` | Kiwoom | status | internal | **COMPLETE_MISSING**: stub always returns `NOT_IMPLEMENTED` |
| `src/vibetrading/brokers.py:162` | `_post_api`; `:178 _access_token`; `:200 _cache_path`; `:204 _read_token_cache`; `:219 _write_token_cache` | Kiwoom | HTTP/auth/cache | internal | **KEEP**; **ADAPT** token file permissions |
| `src/vibetrading/brokers.py:233` | `KisBrokerAdapter.__init__` | KIS cash; mock/real | mode selection | `broker_for_name` | **REUSE**; underlying `KisCashBroker` **UNKNOWN** |
| `src/vibetrading/brokers.py:241` | `HyperliquidBrokerAdapter.__init__`; `:245 connect` | Hyperliquid read-only | env/user check | `broker_for_name` | **KEEP** |
| `src/vibetrading/brokers.py:249` | `HyperliquidBrokerAdapter.account_state` | Hyperliquid perp read | public `/info` | internal | **ADAPT** env URL; mainnet hardcoded at `:253` |
| `src/vibetrading/brokers.py:260` | `HyperliquidBrokerAdapter.submit_order`; `:264 order_status` | Hyperliquid | mutation/status stubs | `broker_for_name` | **REUSE canonical** perp adapter; do not treat these stubs as venue support |
| `src/vibetrading/brokers.py:271` | `TossBrokerAdapter.__init__`; `:276 connect` | Toss DATA_ONLY | auth/account seq | `broker_for_name` | **KEEP** |
| `src/vibetrading/brokers.py:282` | `TossBrokerAdapter.account_state`; `:285 submit_order` | Toss | summary/mutation-reject | internal | **KEEP**; mutation correctly rejected |
| `src/vibetrading/brokers.py:288` | `TossBrokerAdapter.order_status` | Toss | order query | internal | **ADAPT** pagination/absence semantics |
| `src/vibetrading/brokers.py:300` | `broker_for_name`; `:317 _safe_json` | all legacy | factory/response helper | no caller in excerpt | **KEEP** for spot/equity/data; **do not** use for perp canonical path |

### 2.3 `extended_brokers.py` live Arcus/GRVT

| Path:line | Class.method | Venue/product | Role | Caller/evidence | Decision |
|---|---|---|---|---|---|
| `src/vibetrading/extended_brokers.py:24` | `OrderOutcomeUnknown`; `:28 _enabled` | Arcus/GRVT | exception/env | internal | **KEEP** |
| `src/vibetrading/extended_brokers.py:32` | `_integer_units`; `:41 _validate` | Arcus/GRVT perp | validation/allowlist | `prepare_order` | **KEEP** |
| `src/vibetrading/extended_brokers.py:53` | `_WebSocketBroker.__init__` | Arcus/GRVT; testnet default true | env/state | subclasses | **KEEP** |
| `src/vibetrading/extended_brokers.py:61` | `_assert_submit_enabled`; `:65 close` | Arcus/GRVT | live mutation gate | `submit_order` | **KEEP** double gate |
| `src/vibetrading/extended_brokers.py:70` | `_WebSocketBroker._rpc` | Arcus/GRVT | live WS RPC | submit/account/status | **ADAPT**: drops unmatched push frames; auth refresh race |
| `src/vibetrading/extended_brokers.py:112` | `ArcusBrokerAdapter.__init__`; `:123 connect` | Arcus perp; testnet/live | env/markets | `broker_for_name` | **KEEP** |
| `src/vibetrading/extended_brokers.py:132` | `ArcusBrokerAdapter._signer` | Arcus | Ed25519 key check | `prepare_order` | **KEEP** public/private match |
| `src/vibetrading/extended_brokers.py:148` | `ArcusBrokerAdapter.prepare_order` | Arcus | signing | submit/composition | **REUSE** as signer composition; ADAPT TIF contract |
| `src/vibetrading/extended_brokers.py:187` | `ArcusBrokerAdapter.submit_order` | Arcus | live WS mutation | `broker_for_name` | **REUSE**; canonical permit path preferred |
| `src/vibetrading/extended_brokers.py:192` | `account_state`; `:197 order_status` | Arcus | REST/read history | internal | **KEEP**; `complete_history: False` is correct |
| `src/vibetrading/extended_brokers.py:208` | `GRVT_ORDER_TYPES`; `:216 __init__`; `:237 connect` | GRVT perp; testnet chain | EIP-712/auth | `broker_for_name` | **KEEP** |
| `src/vibetrading/extended_brokers.py:233` | `GrvtBrokerAdapter.native_client_id` | GRVT | client-ID mapping | `prepare_order` | **REPLACE_PROVEN_DEFECT**; see defect 1 |
| `src/vibetrading/extended_brokers.py:262` | `prepare_order`; `:306 submit_order`; `:312 account_state`; `:316 order_status` | GRVT | sign/live WS/read | `broker_for_name` | **REUSE**; **ADAPT** client ID and auth refresh |

### 2.4 `perp_execution.py` shared canonical layer

| Path:line | Class.method | Venue/product | Role | Caller/evidence | Decision |
|---|---|---|---|---|---|
| `src/vibetrading/perp_execution.py:29` | `CapabilityBlocked`; `:33 OutcomeUnknown`; `:37 VenueRejected`; `:41 IncompleteHistory` | all perp | fail-closed exceptions | internal | **KEEP** |
| `src/vibetrading/perp_exec
