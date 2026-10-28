# STR-02 — source-owned full order actions and factual feedback

Base 52ade034c3c3b2a234743911ea3a021e1b714411; STR-01 accepted. Authority final02 STR-02 + final03 ARCH-ACTION.
LOCAL/PAPER/FAKE only. Lead owns architecture/security/acceptance. No live
accounts/API, credentials/.env/provider storage, network, installs, Git mutation,
status/authority/Golden edits or recursive delegation. Main Codex auth unchanged.
OpenCode first; user authorized gpt-6-luna native fallback when route unavailable
or OpenCode repair allowance exhausted. Announce/record every route change.

## Approved contract — existing owners, no new trading engine/registry
1. Reuse ExecutionAction/ActionKind/OrderIntent/AccountScope. Add explicit
TradingMode.KIS_MOCK='mock', KIS_MOCK2='mock2', allowed ONLY venue kis/product cash
and exact scope environment; no other mode admission changes. Paper host/matcher
still PAPER-only. No extra clients/credentials/automatic route activation.
Add frozen StrategyActionPlan(plan_id,strategy_id,config_version,event_ts_ns,
actions:tuple[ExecutionAction,...]) to existing execution.trading_contracts.
Validate bounded IDs, immutable tuple, finite causal bounds per owner, exact
source strategy/config and explicit scopes; use existing ExecutionAction fields
for NEW/CANCEL/REPLACE/price/qty/maker/tif/reduce/deadline. No parallel Action DTO.
2. Add frozen StrategyOrderEvent(event_id,strategy_id,account_scope,order_id,
status,event_ts_ns,quantity,filled_quantity,remaining_quantity,fill_id=None,
fill_quantity=None,fill_price=None,fee=None,fee_currency=None,metadata=frozen).
Statuses ACCEPTED/REJECTED/PARTIAL_FILL/FILLED/CANCEL_PENDING/CANCELLED/UNKNOWN
(and EXPIRED if factual); preserve raw ledger CANCELED in metadata when mapped.
Source timestamp unavailable stays null explicitly (owner observation time is
separate event_ts_ns). Quantities/IDs/metadata validated bounded, no fabricated
fills. Add frozen StrategyOrderContext(data:StrategyDataView|None,execution:
Mapping,now_ns:int,cursor:tuple|None,quality:str); recursively immutable facts.
Reuse existing _freeze; no new store/coordinator. data=None explicitly means
market data unavailable for legacy KIS factual callbacks.
3. Optional ACTION_PROTOCOL_VERSION=1 + on_order_event(event,context) on real
strategy instance; callback result same union as on_event: existing list[Signal]
backward-compatible OR new StrategyActionPlan. Legacy on_event signature stays.
Module contract validates opt-in/sync hook; no source ID branch in core. Legacy
runner that cannot consume plans explicitly rejects opt-in files at startup.
New sample strategies/crypto/example_order_policy.py is explicit local fixture,
never auto-registered; all quote/price/qty/reprice/hedge/inventory math in file.
4. Existing PortfolioLedger optionally enables feedback per explicit strategy
(`enable_order_feedback(strategy)`), stored in existing metadata; default jobs
produce no additional feedback records or behavior. Append transactional
order-feedback receipts in existing portfolio_receipts on admit ACCEPTED,
request_cancel CANCEL_PENDING, confirmed terminal/UNKNOWN, each actual fill
PARTIAL_FILL/FILLED incl late fill fee/cumulative facts. No new DB/registry.
Read-only `order_feedback(strategy, after=0, limit=64)` returns ordered bounded
receipt rowid cursor + StrategyOrderEvent; no other strategy rows. Repeated
native fill/outcome IDs yield no duplicate feedback. Local before-send rejection
has explicit LOCAL_NOT_SENT provenance via `record_order_rejection`, never
pretend broker ACK/reject. Existing max_events/docs/transaction rollback apply;
no silent eviction. Return exact source timestamps or null, not fake vendor time.
5. Paper runtime reuses _dispatch/_execute/claims/checkpoint. Convert plans only
by validating source fields and existing owner. Persist per-job/account feedback
cursor in coupled runtime checkpoint, deliver only original strategy after owner
facts durable. Order callback has immutable working orders/remaining/cancel/
late fills/fees/physical vs virtual positions/available+reserved/version/time/
quality. Claim before callback, no duplicate action/replay; fault contained to
source job. Drain after market/submit/cancel and advance, bounded count+bytes+
callback/action budgets; no new runner. Queue cap stops/defers with facts kept.
Unsupported FOK yields durable LOCAL_NOT_SENT REJECTED callback; source may
choose IOC, core NEVER substitutes. NEW after UNKNOWN/reconcile blocks at owner.
REPLACE only against same-strategy/account/instrument confirmed CANCELED target,
quantity<=current factual remaining; process as explicit newly admitted intent
with replacement provenance, no implicit cancel or automatic alternative.
Open/UNKNOWN/CANCEL_PENDING replace rejected to source; source selects CANCEL
then confirmed REPLACE/NEW. Live/sandbox unsupported by this Paper host.
6. Existing ETF/KIS owner returns ACCEPTED/REJECTED/cumulative PARTIAL/FILLED/
CANCEL_PENDING/CANCELLED/UNKNOWN observations to c3/kp/kd module-level
on_order_event(event,context); FileEtfStrategy optional method routes same source
function. Source chooses cancel/confirmed remainder using original5sec rule;
legacy reconcile consumes plans, never independently chooses follow-up. Keep
initial source sizing/prices/types/Mock2 namespaces and all journal permits/
UNKNOWN/checkpoint/ownership/assumed-position recovery unchanged. Pure existing
source follow-up functions shared; no duplicate policy. Feedback claims/facts
bounded/durable in EXISTING EtfJournal (one small feedback table allowed),
callbacks outside transactions, no new owner. Only existing Mock/Mock2 broker
instances accepted, validate exact account identity + mode before effects. KIS
mode enums do NOT enable any new route or admit through Paper owner.

## Acceptance required for complete STR02
- One new explicit crypto file, same generic runtime: Maker bid+ask/PARK GTC,
partial fill→source inventory/working facts→source cancel/reprice, CANCEL_PENDING
not terminal, late fill once, restart restored source chooses next action.
- FOK explicit local reject→source callback chooses IOC, real matcher fill;
unsupported post-only/FOK/capability rejects, no core policy fallback.
- NEW/CANCEL/confirmed REPLACE complete fields/scopes, other-strategy cancel
rejected, UNKNOWN no blind risk retry, duplicate callback/action dedup, raising
callback isolation and count/byte backpressure, state/cursor coupled restore.
- Actual existing KIS Mock/Mock2 concrete adapter with credential-free scripted
transport, callbacks in ORIGINAL three files and old side/price/qty39 oracle.
Only sample success without KIS callback integration is PARTIAL.
- Independent whole Linux + guarded leases + installed source/wheel import;
no original Golden/accepted status rewrite and no product external mutation.

## Planned bounded writer phases
A contracts + optional PortfolioLedger feedback; B Paper runtime + explicit
sample + coupled tests; C source KIS callbacks/journal plumbing + regression.
Only one writer/repo, isolate independent writers. Partial work reviewed before
repair/fallback; Codex integrates/accepts. No self-approval or status edits.

## Resume amendment 2026-10-28
Base 52ade034c3c3b2a234743911ea3a021e1b714411. Preserve accepted CAP02 reconciliation and account_fees.py cost policy. User-configured account rate owns pre-order reservations, PAPER costs and strategy P&L. No broker fee reception/subscription/query/reconciliation work. Do not auto-load actual account config or activate routes. Existing default contracts/schema and legacy tests remain unchanged. Prior external attempts exhausted; use pinned gpt-6-luna in credential-free isolated snapshot; no Git mutation or status edits.

Final execution evidence: see `docs/tickets/STR-02-acceptance.md`. Root independent full/lease/focused/wheel checks passed. Worker snapshots are retained privately; workers did not approve status.
