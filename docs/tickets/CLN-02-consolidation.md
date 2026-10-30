# CLN-02 bounded execution packet

Base: 2fdf1927a7098e077530698c1e29ab28c6297bac

Consolidate redundant KIS subclass identity into canonical KisCashBroker without changing signing, TR IDs, ETF or permit behavior. Exact source reduction required; preserve original CRLF on brokers.py.

Allowed source/test paths:
- `src/execution/brokers.py`
- `src/execution/venues/registry.py`
- `tests/refactoring/test_ven01_registry.py`
- `tests/refactoring/test_cln02_consolidation.py`

Implementation:
1. Remove class KisBrokerAdapter. Keep imported canonical KisCashBroker. In broker_for_name normalize as before; handle kis specifically by resolving settings or get_settings() and invoking KisCashBroker(settings,mode="mock" if _bool(settings.env.get("KIS_USE_MOCK"),default=True) else "real"). All other factories and unsupported behavior unchanged. No compatibility alias: static callers only factory and test below.
2. registry.py: remove duplicate execution.brokers.KisBrokerAdapter identity from route allowlist, EXISTING_ADAPTER_MAPS and _CASH_INTENT_OWNERS. Replace remaining both-owner check with equality to canonical base; preserve all validation and existing gap strings for base.
3. test_ven01_registry.py: mechanical remove retired wrapper import and tuple case, retain all canonical/Kiwoom/Binance assertions. Do not weaken any test.
4. Add test_cln02_consolidation.py: mock KisCashBroker constructor to observe factory mode for missing/true/false KIS_USE_MOCK, normalize name, default get_settings path. Check canonical registry mapping unique and retired identity rejected; explicit canonical Mock/Mock2/real TR IDs unchanged using __new__ where possible. No credentials and no account/client connection.
5. Do not explore beyond listed files or run full tests here; lead runs independently in netless environment. Run compile-only syntax validation if available and report real exit, exact changes and token usage if available. Stop if blocked; no model switch.

Independent offline acceptance: full pytest (live_data deselected), strict fork leases separately, wheel/import/no-legacy namespace checks. Also CLN-02 supplemental matrix for all 286 old functions, exact archive/math hashes, active callers/owners/expiry, five deletion-evidence classes. Source LOC and duplicate identity deltas required. No authority changes by worker, no network/accounts/deploy/push/packages.

Worker accounting: private run CLN02-SIM01-WEB01-20261030/worker-route-pilot.json. External worker stopped for excessive cumulative cache-inclusive tokens; partial source retained for independent review and bounded native Luna continuation.

## Supplemental bounded continuation (lead-approved)

Remove four unused frozen S1-S4 implementations from the production package,
byte-for-byte to `docs/archive/CLN-02-legacy-oracles/fair_value/`. Keep canonical
contracts and current state/research algorithms unchanged. `tests/conftest.py`
exposes the real archived modules only for historical Golden/ownership tests;
ordinary source/wheel/image processes have no such path. Explicit historical
reproduction requires an operator to opt into that archive package path; this is
not a supported production strategy mode. No automatic archive fallback.

Allowed continuation: those four source removals/archive additions,
`tests/conftest.py`, and `tests/refactoring/test_cln02_consolidation.py`.
The source/class retirement has five evidence classes in the supplemental JSON.
No test assertion weakened; the old MOV maps/receipts remain historical snapshots.

## Canonical owners retained

| Owner / exact functions | Active caller / distinct contract | Retention / owner |
|---|---|---|
| `strategies.legacy.models` event/enums, candle loader `load_strategy/get_strategy`, candle turtle `Strategy` | services feed/execution/signal-gen and backtest loaders require shared type/cache identity; no substitute canonical owner | legacy strategy owner; until all callers migrate with identity and Golden proof |
| `strategies.legacy.fair_value.contracts` ActivityWindow/Signal/quote_intents and research/state features/policy | current research state strategies use shared Signal and quote policy; these 25 moved functions are not the retired coefficient bundles | strategy owner; until caller closure replaced with exact math |
| `execution.kis_execution.KisCashBroker` submit/cancel/order_status/daily_fills/account_state/TR/account permission | runtime ETF Mock/Mock2/real namespace separation, C3/KP/KD and common owner; wrapper supplied only factory default mode, now factory does that | execution owner; supported KIS lifetime |
| `execution.bybit_execution` request/probe/audit/kill/reconciliation | runtime CLI and recovery tests; request mutation guard already rejects old create/cancel route, so file is a retained probe/testnet tool rather than second permitted submit owner | operations/execution owner; retire only after probe/audit callers replaced and recovery drill |
| `execution.perp_execution` Binance/Bybit/GRVT/Arcus/HL adapters | selected native dispatcher/registry permit-aware product/environment scopes; each venue has different endpoint/signature/private sequencing | execution owner; supported venue lifetime |
| `execution.extended_brokers` Arcus/GRVT native request and signed WS preparation | composed by corresponding perp adapters; transport/preparation is not a second common-owner permit authority | execution owner; until native signer/private feed replacement proved |
| `execution.brokers` Kiwoom/Binance and DATA_ONLY Toss | selected cash factory/registry and feed tools; KIS redundant subclass retired, Kiwoom REST and Binance spot are distinct from KIS/perp APIs; Toss mutation remains blocked | execution/feed owner; route support lifetime |

No whole venue client merge is justified by its venue label alone. The canonical
`execution/venues/registry.py` retains one registered identity per actual native
implementation. Moving every implementation body into that directory adds no
ownership guarantee, so distinctive native implementations remain in their
current modules with concrete callers above. No UNKNOWN/DEFER production bundle
is carried forward: old 286 functions resolve to 261 historical-only and 25
active canonical functions. The 4 newly retired files have their own function map.

`src/strategies` is the sole packaged strategy namespace; root `strategies/` is
absent. Root `strategy/` is a non-packaged research compatibility alias, not an
alternative mutation owner. Root/service NATS workers and rollback inputs are
preserved outside the selected NATS-free runtime dependency closure; do not
activate them or claim a new server state. No cron/systemd/Timefolio execution
entrypoint is changed by this ticket.

SIM-01 handoff: `operations/dashboard/pnl.py::_exact_add/_exact_product` are
forwarding wrappers over already imported canonical Decimal helpers; candidate
INLINE (low-medium arithmetic regression risk), preserve nesting and precision,
cover exact PnL/fee/funding/replay/changed Decimal context. Freeze/thaw and rounded
reservation calculations are KEEP, not candidates for validation removal.
