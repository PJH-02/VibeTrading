# Observability & Control Interface Documentation

## Overview

This document defines the interface boundaries for external monitoring, control, and reporting systems. These interfaces are designed as extension points for future integrations (Telegram bots, web dashboards, mobile apps) without modifying core trading logic.

---

## 1. Health Monitoring Interface

### NATS Subject Pattern
```
SYSTEM.HEALTH.<SERVICE_NAME>
```

### Message Schema
```json
{
  "service_name": "string",
  "status": "healthy | degraded | unhealthy",
  "uptime_seconds": "integer",
  "last_activity": "ISO8601 datetime",
  "timestamp": "ISO8601 datetime"
}
```

### Available Services
| Service | Subject | Description |
|---------|---------|-------------|
| Data Feed | `SYSTEM.HEALTH.DATA_FEED_CRYPTO` | Crypto market data collector |
| Signal Gen | `SYSTEM.HEALTH.SIGNAL_GEN_CRYPTO` | Strategy signal generator |
| Execution | `SYSTEM.HEALTH.EXECUTION_CRYPTO` | Order manager |
| Risk Engine | `SYSTEM.HEALTH.RISK_ENGINE_CRYPTO` | Risk monitoring |

### Implementation
```python
from services.monitoring.health import get_health_monitor

# Create or get health monitor
monitor = get_health_monitor("my_service")
await monitor.start()

# Update status
monitor.set_status("degraded")
monitor.record_activity()
```

---

## 2. Trading State Query Interface

### Read-Only Query API
The `StateQueryInterface` provides read-only access to trading state:

```python
from services.monitoring.state_query import StateQueryInterface

interface = StateQueryInterface(market=Market.CRYPTO, mode=TradingMode.PAPER)

# Query methods
account = await interface.get_account_state()
positions = await interface.get_positions()
orders = await interface.get_pending_orders()
fills = await interface.get_recent_fills(limit=10)
risk = await interface.get_risk_status()
```

### Account State Schema
```python
@dataclass
class AccountState:
    market: Market
    mode: TradingMode
    balance: Decimal
    equity: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    drawdown_pct: Decimal
    open_positions: int
    pending_orders: int
```

### Position Summary Schema
```python
@dataclass
class PositionSummary:
    symbol: str
    side: str
    quantity: Decimal
    avg_entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    pnl_pct: Decimal
```

---

## 3. Risk Status Interface

### NATS Subject Pattern
```
RISK.ALERTS.<MARKET>
RISK.KILL_SWITCH
```

### Risk Alert Schema
```json
{
  "market": "crypto | kr | us",
  "mode": "backtest | paper | live",
  "event_type": "drawdown_breach | daily_loss_breach | kill_switch",
  "severity": "info | warning | critical",
  "message": "string",
  "triggered_value": "decimal",
  "threshold_value": "decimal",
  "timestamp": "ISO8601 datetime"
}
```

### Kill Switch Control
```python
from services.risk_engine.kill_switch import get_kill_switch, trigger_global_kill_switch

# Market-specific kill switch
kill_switch = get_kill_switch(Market.CRYPTO)
await kill_switch.trigger(reason="Manual intervention", triggered_by="telegram_bot")

# Reset (requires manual confirmation)
kill_switch.reset()

# Global kill switch (all markets)
await trigger_global_kill_switch("Emergency shutdown")
```

---

## 4. Extension Points

### Telegram Bot Integration

```python
# Example: /status command handler
async def handle_status_command(update):
    interface = StateQueryInterface(Market.CRYPTO, TradingMode.PAPER)
    account = await interface.get_account_state()
    
    message = f"""
📊 Account Status
Balance: ${account.balance:,.2f}
Equity: ${account.equity:,.2f}
Drawdown: {account.drawdown_pct:.1f}%
Positions: {account.open_positions}
    """
    await update.reply(message)

# Example: /kill command handler
async def handle_kill_command(update):
    await trigger_global_kill_switch(f"Telegram user {update.user_id}")
    await update.reply("⚠️ Kill switch activated")
```

### Web Dashboard Integration

```python
from fastapi import FastAPI
from services.monitoring.state_query import StateQueryInterface

app = FastAPI()

@app.get("/api/v1/account/{market}/{mode}")
async def get_account(market: str, mode: str):
    interface = StateQueryInterface(Market(market), TradingMode(mode))
    return await interface.get_account_state()

@app.get("/api/v1/positions/{market}/{mode}")
async def get_positions(market: str, mode: str):
    interface = StateQueryInterface(Market(market), TradingMode(mode))
    return await interface.get_positions()
```

### WebSocket Streaming

```python
# Subscribe to real-time updates via NATS
from shared.messaging import ensure_connected

async def stream_to_websocket(websocket):
    messaging = await ensure_connected()
    
    async def handler(msg):
        await websocket.send(msg.data.decode())
    
    await messaging.subscribe("MARKET.CANDLES.>", handler)
    await messaging.subscribe("RISK.ALERTS.>", handler)
```

---

## 5. NATS Subject Reference

| Pattern | Description | Consumers |
|---------|-------------|-----------|
| `MARKET.CANDLES.<market>` | Real-time candle data | Signal Gen, Dashboards |
| `STRATEGY.SIGNALS.<market>` | Trading signals | Execution, Dashboards |
| `TRADE.ORDERS.<market>` | Order status updates | Risk Engine, Dashboards |
| `TRADE.FILLS.<market>` | Fill notifications | Position Tracker, Dashboards |
| `RISK.ALERTS.<market>` | Risk events | Telegram, Dashboards |
| `RISK.KILL_SWITCH` | Emergency halt | All Execution services |
| `SYSTEM.HEALTH.<service>` | Service heartbeats | Monitoring systems |

---

## 6. Database Schema Reference

### PostgreSQL Tables (State)
- `orders` - Order lifecycle and status
- `fills` - Execution records
- `positions` - Open/closed positions
- `signals` - Signal history
- `account_snapshots` - Equity snapshots
- `risk_events` - Risk alerts and breaches

### QuestDB Tables (Time-series)
- `candles` - OHLCV data (partitioned by day)
- `ticks` - Tick data (partitioned by day)
- `orderbook_snapshots` - Order book depth

---

## 7. Security Notes

1. **Read-Only Queries**: `StateQueryInterface` is purely read-only
2. **Kill Switch Auth**: Implement authentication before exposing kill switch
3. **Rate Limiting**: Add rate limits to external API endpoints
4. **Secrets**: Never expose API keys through monitoring interfaces
