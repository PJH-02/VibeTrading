# TX07A — existing identity/fill fact reuse contract

Status: read-only field-mapping/authority contract. This document names fields
that already exist in the selected same-account PAPER/offline paths. It adds no
production schema, projection runtime/API, universal lifecycle enum, or
reconciliation engine. The companion test file is
`tests/refactoring/test_tx07a_fact_contract.py`; production/config changes are
zero.

## 1. Immutable intent, account, instrument and strategy identity

`OrderIntent` is frozen (`vibetrading/models.py`). The same object is stored as
JSON where a ledger has no dedicated column.

| Existing source field | Existing stored fact(s) | Notes |
| --- | --- | --- |
| `OrderIntent.intent_id` | `ExecutionLedger.execution_intents.intent_id`; `PortfolioLedger.portfolio_orders.intent_id`; `EtfJournal.orders.intent_id` | Primary order identity in every native store. |
| `OrderIntent.client_order_id` | `execution_intents.client_order_id`; `portfolio_orders.client_order_id`; `orders.payload` (ETF) | Execution/paper stores have a column; ETF keeps it inside the immutable payload. |
| `OrderIntent.job_id` | JSON `payload.job_id` in paper/execution stores; ETF `orders.strategy` | Paper attribution is the separate explicit `admit_order(strategy=...)` argument (configured runtime uses the module STRATEGY_ID); it is not inferred from job_id. ETF uses intent.job_id. Offline owner uses metadata.strategy_id when present, otherwise job_id. |
| `OrderIntent.signal_id` | `execution_intents.payload`; `portfolio_orders.payload`; `orders.payload` (ETF) | No separate signal column is invented. |
| `OrderIntent.destination` | JSON `payload` in all three stores | Not promoted to a new lifecycle field. |
| `OrderIntent.instrument` (full `InstrumentId`) | Full object in `payload`; `portfolio_orders.instrument` = `instrument.key`; `orders.symbol` = `instrument.raw_symbol` (ETF) | `instrument.key` is `venue:asset_class:std_symbol` plus optional contract/expiry/right/strike. |
| `OrderIntent.side`, `quantity`, `limit_price` | `portfolio_orders.side/quantity`; ETF `orders.side/quantity`; execution JSON `payload` | ETF `orders.price` is the stored reservation/limit unit; `payload` retains the original `limit_price`. |
| `OrderIntent.metadata` | JSON `payload`; `execution_intents.payload`; paper admission fingerprint/evidence | `account_id`/`environment` are checked against the owning scope by the offline owner. |
| `AccountScope.venue/account/product/environment` | `ExecutionLedger.owner_metadata` key `scope`; `PortfolioLedger.portfolio_metadata` key `scope`; process lease identity | `AccountScope.key` is `canonical([venue, account, product, environment])`; no alias key is added. |
| Strategy attribution | Paper `portfolio_orders.strategy`; ETF `orders.strategy`; execution evidence `epochs["strategy:<name>"]` | Attributes are explicit; there is no inferred account-level strategy. |

## 2. Offline `ExecutionLedger` / `ExecutionOwner`

Existing tables and columns (`vibetrading/execution_ledger.py`):

| Table | Existing columns / authority |
| --- | --- |
| `execution_intents` | `intent_id`, `client_order_id`, `fingerprint`, `payload`, `evidence`, `expiry_ns`, `epochs`, `state`, `reservation`, `reserve_unit`, `filled`, `notional`, `fees`, `response`, `updated_ns` |
| `execution_attempts` | `intent_id`, `state`, `updated_ns` |
| `execution_fills` | `fill_id`, `intent_id`, `fingerprint` = `canonical([intent_id, qty, price, fee])` |
| `execution_positions` | `instrument` = `OrderIntent.instrument.key`, `quantity` |
| `owner_metadata` | `scope` = `AccountScope.key`, `account_version` = count of known fills increment |

Native/current state names are stored raw: `AUTHORIZED`, `HANDOFF`, `UNKNOWN`,
`LEGACY_UNKNOWN`, `SUBMITTED`, `BLOCKED`, `PRE_SEND_FAILED`, `FILLED`. They are
not a universal lifecycle shared with the other stores.

Offline handoff facts:

- `ExecutionOwner.supports_offline_handoff()` requires environment `offline`,
  adapter `offline_only is True`, protocol `offline-permit-v1`, and callable
  `submit_order_with_permit`.
- Readiness is the existing `ExecutionReadiness` contract: exact `scope_key`,
  verified account/risk/feed, live lease, exact `account_version`,
  `allowed_instruments`, and `market_price_caps`. Tests use the existing
  `resume()` path; no production gate is bypassed.
- The offline fake returns `{"status": "ACCEPTED", "order_id": "fake-<client>"}`.
  `ExecutionOwner` stores that JSON in `execution_intents.response` and maps it
  to `SUBMITTED` only when that exact fake response shape is present.
- The fake `order_id` is **not** a venue order ID or venue proof. Live/private
  native order identity and venue acknowledgement are unavailable in this
  offline path and remain so; they are not recorded as zero or verified.
- There is no cash or full account-balance column in `execution_intents`.
  Cash, actual positions, and venue-side fill status remain unavailable here.
- `ExecutionLedger` fill facts: duplicate exact `fill_id` is inert; the same
  `fill_id` with changed contents raises; a fill over the intent quantity raises;
  each newly committed fill increments `account_version`; a late ACK after a
  fill preserves `FILLED` and `reservation = 0`.

## 3. Paper `PortfolioLedger`

Existing tables and columns (`vibetrading/portfolio_ledger.py`):

| Table | Existing columns / authority |
| --- | --- |
| `portfolio_orders` | `intent_id`, `client_order_id`, `strategy`, `fingerprint`, `payload`, `evidence`, `rules`, `expiry_ns`, `state`, `quantity`, `filled`, `reservation`, `reserve_unit`, `reserve_currency`, `instrument`, `side`, `reference_price`, `margin_rate` |
| `portfolio_receipts` | `kind`, `id`, `fingerprint`, `document` |
| `portfolio_balances` | `currency`, `amount` (simulated seed/credits only) |
| `portfolio_positions` | `strategy`, `instrument`, `quantity`, `average`, `margin` |
| `portfolio_strategy_cash` | `strategy`, `currency`, `amount`, `fees`, `funding`, `realized` |
| `portfolio_gaps` | `instrument`, `document` (explicit unresolved deviation) |

Paper order state names are the existing raw facts `OPEN`, `CANCEL_PENDING`,
`UNKNOWN`, `CANCELED`, `EXPIRED`, `REJECTED`, `FILLED`. They are not the
execution-owner states or the ETF statuses.

Paper fill receipt mapping:

- Caller `fill_id` becomes `portfolio_receipts.id` with `kind='fill'`.
- The receipt `document` contains `intent_id`, `quantity`, `price`, `fee`,
  `fee_currency`, `order_id`, `position_side`, `group_id`, `strategy`,
  `instrument`, and `side`.
- `order_id` in the receipt is an optional caller-supplied document value; it is
  **not** an ACK API or venue proof. Paper has no invented ACK API.
- Exact duplicate receipt is inert (`False`); conflicting document under the same
  `fill_id` raises `Immutable fill receipt collision`.
- Terminal outcome receipts use `kind='outcome'`, `id='<intent_id>:<STATE>'`.
- `cancel(intent_id)` without confirmation produces `UNKNOWN` and leaves the
  reservation in place. A confirmed terminal outcome writes an evidence receipt
  and releases reservation only when the order is not `FILLED`.
- A late fill after cancel/unknown remains a fill fact; it does not reopen a
  terminal `FILLED` state or recreate reservation.
- `reconcile_positions(snapshot_id, positions, evidence, account_version=...)`
  accepts only the exact current `account_version`; stale versions are rejected
  before any gap change is retained.
- Simulated cash is `portfolio_balances`/`portfolio_strategy_cash`, seeded by
  explicit test evidence. It is not venue cash or venue equity.

## 4. Existing `EtfJournal`

Existing tables/columns (`vibetrading/etf_journal.py`); no new math,
normalization, or reconciliation is added by TX07A.

| Table | Existing columns / authority |
| --- | --- |
| `orders` | `intent_id`, `strategy`, `symbol`, `side`, `quantity`, `price`, `reserve`, `status`, `order_number`, `organization`, `day`, `filled`, `notional`, `created_ns`, `payload`, `cancel_requested`, `order_type`, `accepted_ns`, `fallback_requested`, `fallback_done`, `account_scope`, `permission_failure`, `rejection_reason` |
| `books` | `strategy`, `cash`, `day`, `entries`, `direction`, `safety_stop` |
| `positions` | `strategy`, `symbol`, `quantity` (integer shares) |
| `notifications` | `id`, `kind`, `payload`, `created_ns`, `sent_ns` |
| `dispatch_attempts` | `intent_id`, `evidence`, `state`, `prepared_ns`, `handoff_ns`, `updated_ns`, `reason` |

Raw ETF order statuses are `submitting`, `unknown`, `pending`, `complete`,
`rejected`, `blocked`; dispatch attempts separately use `PREPARED`, `HANDOFF`,
`NOT_SENT`, `UNKNOWN`, `ACKED`, `REJECTED`. Neither is a universal state machine.

Native broker/status mapping (`apply_status` / `accepted`):

| Native input | Existing stored fact |
| --- | --- |
| `tot_ccld_qty` | `orders.filled` (cumulative integer) |
| `tot_ccld_amt` | `orders.notional` (cumulative exact decimal string) |
| `avg_prvs` / `avg_ccld_unpr` | fallback only when `tot_ccld_amt` is absent/zero; used to derive `orders.notional` |
| `rmn_qty` | Not persisted as a separate column; used with `quantity - filled` to derive `orders.reserve` and terminal status |
| `cncl_yn == 'Y'` | Contributes to terminal `orders.status = 'complete'`; raw flag is not stored |
| `ODNO` / `odno` | `orders.order_number` (bounded native order identity) |
| `KRX_FWDG_ORD_ORGNO` / lower-case alias | `orders.organization` |
| ACK receipt time | `orders.accepted_ns` first-set via `COALESCE`; duplicate ACK cannot extend it |
| Accepted notification | `notifications.id = '<intent_id>:accepted'`, `kind='mock_order'` |
| Cumulative fill notification | `notifications.id = '<intent_id>:filled:<cumulative_filled>'`, `kind='mock_fill'` (`notify` is `INSERT OR IGNORE`) |

ETF `books.cash` is the existing per-strategy book cash, not verified account
cash. Cumulative regressions, fills beyond owned quantity, and conflicting native
order identity raise and roll back without mutation. A duplicate/late ACK after
terminal completion preserves `complete`, quantity, cash, reservation, and the
notification identity.

## 5. Authority, mode and unavailable external facts

- Offline `ExecutionOwner` is only enabled for its explicit offline fake-adapter
  handoff. It is not a live venue adapter and cannot arm production.
- `PortfolioLedger` is PAPER only. Its balances/positions are simulated; its
  `readiness()` reports `external_positions_verified: False`, and gaps remain
  explicit rather than being silently zeroed.
- `EtfJournal` records broker facts supplied by its caller; it is not by itself
  proof of a private-stream or venue reconciliation.
- The following remain unavailable in these selected paths and must be reported
  as unavailable, never as zero or verified: live/private venue ACK, venue-side
  cash/position balances, native exchange order lifecycle, and private-stream
  reconciliation. TX07A closes only selected local identity/fact reuse; it does
  not promote native live/private-stream reconciliation.

## 6. Existing behavior proof values

The companion tests assert real current fields/amounts rather than generated
snapshots:

| Path | Seeded/initial fact | Existing facts asserted |
| --- | --- | --- |
| Execution owner | offline fake handoff, `2 @ 10`, fee `0.1` | partial `filled=1`/`reservation=10`/`SUBMITTED`; full `filled=2`/`reservation=0`/`FILLED`; duplicate inert; changed same fill ID raises; late ACK preserves the fill fact; `account_version` increments once per fill |
| Paper full fill | `USDT 1000`, CASH multiplier `1`, buy `1 @ 100`, fee `0.1` | cash `899.9`; physical/strategy position `1`; `FILLED`; unconfirmed and confirmed cancel facts leave `FILLED`/reserve `0` |
| Paper partial fill | same seed, buy `1 @ 100` | fill `0.4 @ 100` fee `0.04` leaves reserve `60.12`; request cancel then unconfirmed cancel = `UNKNOWN` with reserve `60.12`; final `0.6 @ 100` fee `0.06` = `FILLED`, cash `899.9`, position `1`; duplicates inert; stale snapshot rejected; exact current snapshot accepted; unknown intent/conflicting identity roll back |
| ETF | `6000` initial cash, `C3/KP/KD` `2000` each, C3 buy `10 @ 100` | cumulative `4/396` before ACK then ACK `ODNO=native-one`/org `org`: cash `1603.9406`, position `4`, reserve `603`; cumulative `10/990`: cash `1009.8515`, position `10`, reserve `0`; duplicate/late ACK and duplicate status inert; cumulative regression/native conflict fail without mutation |

Production behavior changes: **0**.
