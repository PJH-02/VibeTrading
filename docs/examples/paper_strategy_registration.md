# Registering an explicit Paper strategy file

`PaperStrategyRuntime.register_configured(document, *, action_builder)` is a
small local bridge between one explicitly named Python strategy file and the
existing `PaperStrategyRuntime.register` contract. Placing a file in the
package does **not** discover, enable, or trade it. An operator must construct
the complete document and call the method; there is no folder scan, auto-enable,
plugin manager, or live promotion here.

## Strategy file contract

The canonical `load_strategy` loader imports exactly one module from
`vibetrading.strategies`. The file must define `STRATEGY_ID`, `SUBSCRIPTIONS`,
`WARMUP`, and `create_strategy`; instances return `list[Signal]` from
`on_event(event, view)`.

```python
from decimal import Decimal
from vibetrading.models import Signal, SignalAction

STRATEGY_ID = "example_breakout"
SUBSCRIPTIONS = [{"instrument_key": "bybit:crypto_perp:BTCUSDT", "event_type": "order_book"}]
WARMUP = {"bars": 0}

class Strategy:
    def on_event(self, event, view):
        return [Signal(strategy_id=STRATEGY_ID, instrument=event.instrument,
            action=SignalAction.ENTER_LONG, event_ts_ns=event.receive_ts_ns,
            reference_price=Decimal("100"), valid_until_ts_ns=event.receive_ts_ns + 100_000,
            source_event_id=event.event_id)]

def create_strategy():
    return Strategy()
```

Supported module spellings are `strategies/example_breakout.py`,
`strategies.example_breakout`, and `vibetrading.strategies.example_breakout`.
Absolute paths, traversal, other namespaces, and `legacy`/`research` packages
are rejected by the existing loader.

## Explicit configuration document

The example uses an existing `host` and its bound `paper_account`
(`PortfolioLedger`), plus the existing `plan`/`settings`. The account key is
read from that same ledger scope. No account or runtime factory is added.

All registration fields are required, with no unknown fields. Risk, research,
permission, feeds, accounts, warmup, and versions are supplied explicitly; the
bridge never infers rates, limits, receipts, mode, or permissions.

```python
document = {
    "job_id": "paper-example-job",
    "module": "strategies/example_breakout.py",
    "strategy_version": "strategy-1",
    "config_version": "config-1",
    "config": {"quantity": "1", "limit_price": "99"},
    "research_receipt": {"research_id": "explicit-local-fixture"},
    "paper_permission": True,
    "required_feeds": ["bybit:crypto_perp:BTCUSDT"],
    "warmup_bars": 0,
    "accounts": [paper_account.scope.key],
    "risk": {
        paper_account.scope.key: {
            "funding_buffer": "0",
            "account_notional_limit": "100000",
            "strategy_notional_limit": "100000",
            "account_capital_limit": "100000",
            "strategy_capital_limit": "100000",
            "margin_rate": "0.1",
        },
    },
}

```

`config` and `research_receipt` must be mappings. `required_feeds` and
`accounts` must be lists or tuples of explicit strings. `paper_permission` must
be a bool; `False` registers the job as `BLOCKED` and never promotes it. The
document is bounded by the runtime's existing `max_config_bytes` before any
import or durable claim. Existing `register` validation still owns IDs,
versions, warmup, subscriptions, feed/account dependencies, uniqueness,
capacity, and the sticky permission/restart gates.

## Injected action builder

`action_builder(signal, view, config)` is a trusted callable supplied by the
caller. It returns `list[ExecutionAction]`; the runtime reuses its existing
signal/action validation, dedup claims, and Paper sizing path. It contains no
strategy-ID branches in the runtime.

```python
from decimal import Decimal
from vibetrading.models import OrderDestination, OrderIntent, Side
from vibetrading.trading_contracts import ActionKind, ExecutionAction, TradingMode

# paper_account is the existing PaperExecution bound to the configured account.
def build_actions(signal, view, config):
    intent = OrderIntent(
        intent_id="paper-example-job-" + signal.signal_id,
        job_id="paper-example-job",
        signal_id=signal.signal_id,
        destination=OrderDestination.REAL,
        instrument=signal.instrument,
        side=Side.BUY,
        created_ts_ns=signal.event_ts_ns,
        client_order_id="client-paper-example-job-" + signal.signal_id,
        quantity=Decimal(config["quantity"]),
        limit_price=Decimal(config["limit_price"]),
    )
    return [ExecutionAction("action-paper-example-job-" + signal.signal_id,
        ActionKind.NEW, paper_account.scope, "example_breakout", "config-1",
        TradingMode.PAPER, signal.valid_until_ts_ns, intent=intent, post_only=True)]
```

After defining the builder, call:

```python
host.register_configured(document, action_builder=build_actions)
```

## Existing runtime seam

The same host is passed to the existing local engine seam; it is not a new
runtime or server composition:

```python
await run_engine(plan, settings, paper_runtime=host)
```

A fresh process or latest-ledger restart remains
`strategy_state_restore_unverified` and blocks the job until the existing
coupled checkpoint proves strategy state. Claimed callbacks are not
automatically replayed. This is a cooperative local Python plugin API; process
isolation, live promotion, exchange/account integration, and server composition
remain separate and are not provided by this bridge.
