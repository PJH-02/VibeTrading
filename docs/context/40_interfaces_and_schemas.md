# 40) Interfaces and Schemas

## Scope
This document defines canonical schema/port targets for V2 and maps them to observed code in `vibetrading_V2` and legacy references in `vibetrading_V1`.

## Canonical Schemas (Target)

### Bar (1-minute OHLCV)
| Field | Type | Meaning |
|---|---|---|
| `ts` | `datetime` | **Bar close time** (timezone-aware, UTC canonical) |
| `symbol` | `str` | Canonical instrument identifier |
| `open` | `float` | First traded price in the minute |
| `high` | `float` | Max traded price in the minute |
| `low` | `float` | Min traded price in the minute |
| `close` | `float` | Last traded price in the minute |
| `volume` | `float` | Traded quantity during the minute |
| `timeframe` | `Literal["1m"]` | Fixed timeframe contract |
| `is_closed` | `bool` | Whether this minute is final/closed |
| `source` | `str \| None` | Adapter/source provenance |

### Signal
| Field | Type | Meaning |
|---|---|---|
| `signal_id` | `str` | Stable signal identifier |
| `ts` | `datetime` | Signal timestamp |
| `symbol` | `str` | Target symbol |
| `action` | `Literal["enter_long","exit_long","enter_short","exit_short","hold"]` | Strategy intent |
| `strength` | `float` | Confidence in `[0,1]` |
| `strategy_name` | `str` | Origin strategy |
| `metadata` | `dict[str, Any]` | Extra strategy context |

### TargetWeights
| Field | Type | Meaning |
|---|---|---|
| `ts` | `datetime` | Rebalance decision timestamp |
| `weights` | `dict[str, float]` | Target portfolio weights by symbol |
| `rebalance` | `bool` | Whether to rebalance now |
| `reason` | `str \| None` | Optional rebalance rationale |

### Order
| Field | Type | Meaning |
|---|---|---|
| `order_id` | `str` | Internal order identifier |
| `idempotency_key` | `str` | Request dedup key |
| `created_at` | `datetime` | Creation time |
| `symbol` | `str` | Target symbol |
| `side` | `Literal["buy","sell"]` | Order side |
| `order_type` | `Literal["market","limit","stop","stop_limit"]` | Order type |
| `qty` | `float` | Requested quantity |
| `limit_price` | `float \| None` | Limit price |
| `stop_price` | `float \| None` | Stop trigger |
| `status` | `Literal["created","submitted","accepted","rejected","partially_filled","filled","cancelled","expired"]` | Lifecycle state |
| `filled_qty` | `float` | Accumulated filled quantity |
| `venue_order_id` | `str \| None` | Broker/exchange order id |
| `reject_reason` | `str \| None` | Rejection details |
| `metadata` | `dict[str, Any]` | Adapter-specific details |

### Fill
| Field | Type | Meaning |
|---|---|---|
| `fill_id` | `str` | Internal fill id |
| `order_id` | `str` | Parent order id |
| `ts` | `datetime` | Fill time |
| `symbol` | `str` | Filled symbol |
| `side` | `Literal["buy","sell"]` | Fill side |
| `qty` | `float` | Filled quantity |
| `price` | `float` | Fill price |
| `commission` | `float` | Fee amount |
| `slippage_bps` | `float` | Effective slippage |
| `venue_fill_id` | `str \| None` | External fill id |
| `metadata` | `dict[str, Any]` | Extra execution metadata |

### PortfolioState
| Field | Type | Meaning |
|---|---|---|
| `ts` | `datetime` | Snapshot time |
| `cash` | `float` | Cash balance |
| `equity` | `float` | Total equity |
| `positions` | `dict[str, dict[str, float]]` | Per-symbol position state (`qty`, `avg_price`, `mark_price`, `unrealized_pnl`, `realized_pnl`) |
| `gross_exposure` | `float` | Sum abs notional exposure |
| `net_exposure` | `float` | Signed net exposure |
| `pending_orders` | `int` | Open order count |

### RiskState
| Field | Type | Meaning |
|---|---|---|
| `ts` | `datetime` | Snapshot time |
| `max_leverage` | `float` | Risk limit |
| `current_leverage` | `float` | Current leverage |
| `max_position_notional` | `float` | Position notional cap |
| `max_drawdown` | `float` | Drawdown limit |
| `current_drawdown` | `float` | Current drawdown |
| `kill_switch_dd` | `float` | Kill-switch drawdown threshold |
| `breached_rules` | `list[str]` | Active rule violations |
| `kill_switch_active` | `bool` | Kill-switch state |

## Canonical Ports (Target Protocol Signatures)

```python
from datetime import datetime
from typing import AsyncIterator, Protocol, Sequence

class BarDataSource(Protocol):
    def get_historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1m",
    ) -> Sequence[Bar]: ...

    def stream_live_bars(
        self,
        symbols: Sequence[str],
        timeframe: str = "1m",
    ) -> AsyncIterator[Bar]: ...

class Broker(Protocol):
    def submit_order(self, order: Order) -> Order: ...
    def cancel_order(self, order_id: str) -> Order: ...
    def get_order(self, order_id: str) -> Order: ...
    def list_open_orders(self, symbol: str | None = None) -> Sequence[Order]: ...
    def get_fills(self, order_id: str | None = None) -> Sequence[Fill]: ...

class Clock(Protocol):
    def now(self) -> datetime: ...

class StateStore(Protocol):
    def load_portfolio_state(self) -> PortfolioState | None: ...
    def save_portfolio_state(self, state: PortfolioState) -> None: ...
    def load_risk_state(self) -> RiskState | None: ...
    def save_risk_state(self, state: RiskState) -> None: ...
```

## Mapping to Existing Code and Mismatches

| Canonical item | Observed in V2 | Legacy reference in V1 | Mismatch/Gaps |
|---|---|---|---|
| Bar | `core.types.Bar(symbol,timestamp,open,high,low,close,volume)` (`vibetrading_V2/core/types.py:10-17`) | `shared.models.Candle` includes `interval`, `is_closed`, `market` (`vibetrading_V1/shared/models.py:92-104`) | V2 bar omits `timeframe`, `is_closed`, `market/source`; timestamp semantic (`open` vs `close`) is undocumented |
| Signal | No V2 `Signal` model; strategies return `OrderIntent` directly (`vibetrading_V2/strategies/my_strategy_a.py:38-44`) | Typed `Signal` exists (`vibetrading_V1/shared/models.py:119-128`) | Missing signal layer between strategy and order execution |
| TargetWeights | No V2 schema | `TeamPortfolioWeights` exists (`vibetrading_V1/shared/strategy_contracts.py:13-20`) | Portfolio rebalancing contract missing in V2 |
| Order | `OrderIntent(symbol, side, quantity)` only (`vibetrading_V2/core/types.py:21-25`) | Rich mutable `Order` + status lifecycle (`vibetrading_V1/shared/models.py:134-169`) | No order id, idempotency key, state machine, cancel/expiry fields in V2 |
| Fill | Minimal `Fill(symbol,side,quantity,price,timestamp)` (`vibetrading_V2/core/types.py:28-34`) | Fill has `order_id`, fee/slippage metadata (`vibetrading_V1/shared/models.py:181-197`) | No order linkage/fees/slippage in V2 fill |
| PortfolioState | No explicit V2 schema; `RunnerResult` only aggregates counts/fills (`vibetrading_V2/runner/runtime.py:22-30`) | `Position` + `AccountSnapshot` models exist (`vibetrading_V1/shared/models.py:203-249`) | Missing canonical portfolio snapshot surface in V2 |
| RiskState | V2 has static risk policy defaults (`vibetrading_V2/policies/risk.py:10-16`) | `RiskAlert` events and `RiskManager` monitoring exist (`vibetrading_V1/shared/models.py:256-264`, `vibetrading_V1/services/risk_engine/risk_manager.py:149-175`) | No runtime risk-state snapshot type/port in V2 |
| BarDataSource | V2 `DataSource.get_bars(symbol)` only (`vibetrading_V2/core/ports.py:10-12`) | `DataFeedProvider` supports historical + stream (`vibetrading_V1/services/data_feed/base.py:58-117`) | V2 cannot express range-bounded historical fetch vs live stream in one port |
| Broker | V2 `ExecutionPort.execute(OrderIntent)->Fill` (`vibetrading_V2/core/ports.py:15-17`) | `BrokerAdapter` has submit/cancel/status/open-orders/balance (`vibetrading_V1/services/execution/base.py:71-134`) | V2 execution port is too narrow for broker lifecycle reconciliation |
| Clock | `Clock.now()->object` (`vibetrading_V2/core/ports.py:20-22`) | Datetime usage is explicit across V1 models (`vibetrading_V1/shared/models.py:79-85`) | Return type should be tightened to `datetime` |
| StateStore | Not present in V2 (no `StateStore` matches) | State persisted/queryable in V1 via DB-backed services (`vibetrading_V1/services/monitoring/state_query.py`) | Missing optional persistence port in V2 core |

## Compatibility Notes
- **Kiwoom constraints (observed):** Kiwoom feed and broker explicitly require Windows + COM/OCX + Qt bindings (`vibetrading_V1/services/data_feed/kiwoom_feed.py:6-12`, `vibetrading_V1/services/execution/kiwoom_broker.py:3-12`).
- **Kiwoom minute data support (observed):** historical minute retrieval uses `opt10080` with tick-unit mapping (1m/3m/5m...) (`vibetrading_V1/services/data_feed/kiwoom_feed.py:320-322`, `:440-452`).
- **KIS KR/US minute limitation (observed):** both KR and US feeds map `"1m"` to daily period codes (`vibetrading_V1/services/data_feed/kr_feed.py:354-364`, `vibetrading_V1/services/data_feed/us_feed.py:358-369`).
- **KIS execution in V1 (observed):** KR `kis` route currently returns `BrokerStub`; comment says real KIS order adapter is not implemented (`vibetrading_V1/services/broker_factory.py:50-55`).
- **V2 adapter status (observed):** no Kiwoom/KIS references are present under `vibetrading_V2` (`rg -n "Kiwoom|KIS|kiwoom|kis" vibetrading_V2` returned no matches).

## Evidence
- `nl -ba vibetrading_V2/core/types.py | sed -n '1,120p'`  
  - `Bar`, `OrderIntent`, `Fill` data contracts (`vibetrading_V2/core/types.py:10-34`).
- `nl -ba vibetrading_V2/core/ports.py | sed -n '1,120p'`  
  - `DataSource`, `ExecutionPort`, `Clock` signatures (`vibetrading_V2/core/ports.py:10-22`).
- `nl -ba vibetrading_V2/runner/runtime.py | sed -n '14,94p'`  
  - `RunnerResult` fields and direct `get_bars/execute` call chain (`vibetrading_V2/runner/runtime.py:22-30`, `:70-77`).
- `nl -ba vibetrading_V1/shared/models.py | sed -n '92,270p'`  
  - `Candle`, `Signal`, `Order`, `Fill`, `Position`, `AccountSnapshot`, `RiskAlert` schemas.
- `nl -ba vibetrading_V1/shared/strategy_contracts.py | sed -n '13,90p'`  
  - `TeamPortfolioWeights` and strategy contracts.
- `nl -ba vibetrading_V1/services/data_feed/base.py | sed -n '58,117p'` and `nl -ba vibetrading_V1/services/execution/base.py | sed -n '71,134p'`  
  - Historical/live feed interface and broker lifecycle interface.
- `nl -ba vibetrading_V1/services/data_feed/kiwoom_feed.py | sed -n '1,120p;308,452p'` and `nl -ba vibetrading_V1/services/execution/kiwoom_broker.py | sed -n '1,90p'`  
  - Kiwoom platform requirements and minute data API mapping.
- `nl -ba vibetrading_V1/services/data_feed/kr_feed.py | sed -n '353,364p'` and `nl -ba vibetrading_V1/services/data_feed/us_feed.py | sed -n '358,369p'`  
  - KIS interval code conversion where `1m` maps to daily.
- `nl -ba vibetrading_V1/services/broker_factory.py | sed -n '42,56p'`  
  - KIS broker path currently wired to `BrokerStub`.
- `rg -n "Kiwoom|KIS|kiwoom|kis" vibetrading_V2`  
  - No matches in V2 for broker/datafeed-specific Kiwoom/KIS adapters.
