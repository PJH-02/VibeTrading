# 60) Order and Safety Semantics

## Observed baseline (v1 vs v2)

### v1 (legacy) observed order lifecycle shape
- v1 defines mutable `Order` with statuses `pending`, `submitted`, `partial`, `filled`, `cancelled`, `rejected` (`vibetrading_V1/shared/models.py:57-65,134-169`).
- `OrderManager` creates orders from signals, persists them, submits via broker, and marks rejected on submission exceptions (`vibetrading_V1/services/execution/order_manager.py:153-185`).
- `BrokerStub` transitions `pending -> submitted -> filled` (market) or `cancelled` via cancel path (`vibetrading_V1/services/execution/broker_stub.py:69-163`).
- Kiwoom adapter can emit `partial` before `filled` on incremental chejan fills (`vibetrading_V1/services/execution/kiwoom_broker.py:355-363`).

### v2 (target-in-progress) observed state
- v2 currently uses `OrderIntent(symbol, side, quantity)` and directly returns `Fill`; there is no explicit order record/state machine yet (`vibetrading_V2/core/types.py:21-34`, `vibetrading_V2/runner/runtime.py:74-77`).
- Paper/live adapters are immediate-fill scaffolds, not lifecycle brokers (`vibetrading_V2/execution/paper_adapter.py:18-25`, `vibetrading_V2/execution/live_adapter.py:22-29`).

## Canonical broker-agnostic order lifecycle (proposed)

Required by constitution: `Created → Submitted → Accepted/Rejected → PartiallyFilled → Filled/Cancelled/Expired` (`AGENTS.md:71-75`).

### Canonical states and transitions
1. **Created**
   - order object validated, idempotency key attached, not sent yet.
2. **Submitted**
   - adapter submit request emitted to venue.
3. **Accepted** or **Rejected**
   - accepted when venue acknowledges order id; rejected on terminal submit failure.
4. **PartiallyFilled** (optional repeating)
   - fill events increment cumulative quantity.
5. Terminal:
   - **Filled** (cum_qty == qty)
   - **Cancelled** (explicit cancel)
   - **Expired** (venue timeout/day-end expiration)
   - **Rejected** (terminal failure)

### Mapping to current code
- v1 has close equivalents for `Submitted`, `Partial`, `Filled`, `Cancelled`, `Rejected`, but no explicit `Accepted`/`Expired` enum values (`vibetrading_V1/shared/models.py:57-65`).
- v2 has none of these lifecycle entities yet (only intent/fill) (`vibetrading_V2/core/types.py:21-34`).

## Idempotency strategy

## Observed
- v1 `Order` has UUID `id` but no dedicated idempotency field (`vibetrading_V1/shared/models.py:138-159`).
- Binance adapter forwards `order.id` as `newClientOrderId`, which helps exchange-side dedupe for that adapter only (`vibetrading_V1/services/execution/crypto_binance.py:149-156`).
- `OrderManager` does not check duplicate `signal_id` before creating/submitting an order (`vibetrading_V1/services/execution/order_manager.py:167-220`).
- v2 `OrderIntent` has no idempotency key (`vibetrading_V2/core/types.py:21-25`).

## Proposed canonical rule
- **Key format**: `strategy_name:symbol:side:bar_ts:sequence` (or external signal UUID if present).
- **Enforcement points**:
  1. App/engine layer: reject duplicate keys before adapter call.
  2. Broker adapter: pass key to venue client-order-id field where supported.
  3. State store: persist key→order mapping for restart safety.
- **Conflict behavior**:
  - Same key + semantically same payload => return existing order (idempotent replay).
  - Same key + different payload => reject as invariant violation.

## Retry / rate-limit policy

## Observed
- `OrderManager` message-consumption failures issue `msg.nak(delay=5)` (retry at messaging layer) (`vibetrading_V1/services/execution/order_manager.py:130-139`).
- NATS client has reconnect/time-wait/max-reconnect controls in settings (`vibetrading_V1/shared/config.py:58-66`, `vibetrading_V1/shared/messaging.py:89-97`).
- Broker submit/cancel paths generally fail-fast on adapter exceptions; explicit exponential backoff policy is not implemented in execution adapters (`vibetrading_V1/services/execution/crypto_binance.py:134-207`, `vibetrading_V1/services/execution/kiwoom_broker.py:378-475`).

## Proposed policy
- Retry **only** transient transport/5xx/network disconnect errors.
- Do **not** retry semantic rejections (insufficient funds, invalid symbol/order type, compliance reject).
- Backoff baseline: 250ms → 500ms → 1s (jittered), max 3 attempts for submit/cancel.
- If rate-limit hit (429/venue limit), transition to `Rejected` with explicit reason and activate execution cool-down circuit.

## Kill-switch semantics

## Observed
- v1 `KillSwitch.trigger()` publishes `RISK.KILL_SWITCH` broadcast (`vibetrading_V1/services/risk_engine/kill_switch.py:54-93`, `vibetrading_V1/shared/messaging.py:283-286`).
- `OrderManager` subscribes to kill switch, flips `_killed=True`, rejects new signals, and attempts cancel of pending orders (`vibetrading_V1/services/execution/order_manager.py:112-163,146-151`).
- `RiskManager` triggers kill switch on drawdown or daily-loss breach (`vibetrading_V1/services/risk_engine/risk_manager.py:149-201`).
- v2 only exposes `kill_switch_dd` config in policy defaults; no runtime enforcement currently (`vibetrading_V2/policies/risk.py:10-47`, `vibetrading_V2/runner/runtime.py:53-94`).

## Canonical action on trigger
1. Stop new submissions immediately.
2. Cancel all cancellable open orders.
3. Optionally flatten positions (configurable by mode).
4. Persist kill-switch state and emit auditable event.
5. Require explicit manual reset to resume.

## Live safety gates and default behavior

## Required
- Live paths must require `LIVE_API=1` and `CONFIRM_LIVE=YES`; otherwise force paper or fail (`AGENTS.md:58-64,81-84`).

## Observed
- v2 `cli/live.py` has no gate check; it directly builds `LiveExecutionAdapter` and runs (`cli/live.py:30-47`).
- v2 `run_live` is a thin wrapper without guard logic (`vibetrading_V2/runner/live.py:8-14`).
- v1 defaults `TRADING_MODE` to `paper` (`vibetrading_V1/shared/config.py:203-205`), but explicit dual env gate (`LIVE_API`/`CONFIRM_LIVE`) is absent from code search (`rg -n "LIVE_API|CONFIRM_LIVE" ...` output).

## Canonical enforcement location
- **Primary**: live composition root (`apps/trade_live.py`/`cli/live.py`) must hard-gate startup.
- **Secondary**: live broker adapter init must re-check gate as defense-in-depth.
- **Fallback**: if not gated, run paper adapter by default and log explicit downgrade reason.

## Evidence
- Commands run:
  - `nl -ba vibetrading_V1/shared/models.py | sed -n '1,420p'`
  - `nl -ba vibetrading_V1/services/execution/order_manager.py | sed -n '130,520p'`
  - `nl -ba vibetrading_V1/services/execution/broker_stub.py | sed -n '1,360p'`
  - `nl -ba vibetrading_V1/services/execution/crypto_binance.py | sed -n '1,420p'`
  - `nl -ba vibetrading_V1/services/execution/kiwoom_broker.py | sed -n '248,560p'`
  - `nl -ba vibetrading_V1/services/risk_engine/kill_switch.py | sed -n '1,320p'`
  - `nl -ba vibetrading_V1/services/risk_engine/risk_manager.py | sed -n '149,240p'`
  - `nl -ba vibetrading_V1/shared/config.py | sed -n '58,240p'`
  - `nl -ba vibetrading_V2/core/types.py | sed -n '1,260p'`
  - `nl -ba vibetrading_V2/execution/paper_adapter.py | sed -n '1,260p'`
  - `nl -ba vibetrading_V2/execution/live_adapter.py | sed -n '1,260p'`
  - `nl -ba vibetrading_V2/runner/runtime.py | sed -n '1,300p'`
  - `nl -ba vibetrading_V2/runner/live.py | sed -n '1,260p'`
  - `nl -ba cli/live.py | sed -n '1,260p'`
  - `nl -ba AGENTS.md | sed -n '22,110p'`
  - `rg -n "LIVE_API|CONFIRM_LIVE" -S vibetrading_V1 vibetrading_V2 cli AGENTS.md`
- Key output excerpts:
  - `rg` found `LIVE_API`/`CONFIRM_LIVE` only in `AGENTS.md`; no implementation hits in `vibetrading_V1`, `vibetrading_V2`, or `cli`.
  - `cli/live.py` and `vibetrading_V2/runner/live.py` show direct live run path with no env gate checks.
- Concrete references:
  - `vibetrading_V1/shared/models.py:57-65,134-169`
  - `vibetrading_V1/services/execution/order_manager.py:130-185`
  - `vibetrading_V1/services/execution/broker_stub.py:69-163`
  - `vibetrading_V1/services/execution/crypto_binance.py:149-156,276-287`
  - `vibetrading_V1/services/execution/kiwoom_broker.py:355-363,378-427`
  - `vibetrading_V1/services/risk_engine/kill_switch.py:54-93`
  - `vibetrading_V1/services/risk_engine/risk_manager.py:149-201`
  - `vibetrading_V1/shared/config.py:58-66,203-205`
  - `vibetrading_V2/core/types.py:21-34`
  - `vibetrading_V2/runner/runtime.py:74-77`
  - `vibetrading_V2/execution/live_adapter.py:22-29`
  - `cli/live.py:30-47`
  - `AGENTS.md:58-64,71-75,81-84`
