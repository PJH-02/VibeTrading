# 10) V1 System Map (Observed Current State)

## Entry points and runtime orchestration
- Primary orchestrator is `StrategyRunner` in `run_strategy.py`, building the full pipeline:
  - `DataFeed -> SignalGenerationEngine -> OrderManager -> Broker`
  - Starts components in order, then runs the main async candle loop (`vibetrading_V1/run_strategy.py:113-223`).
- Main runtime loop consumes market candles and synchronously generates signals before async order submission (`vibetrading_V1/run_strategy.py:184-200`).
- V1 backtest and walk-forward entrypoint is script CLI (`vibetrading_V1/scripts/run_backtest.py:54-136`, `vibetrading_V1/scripts/run_backtest.py:139-197`), routing team-specific engines through `resolve_backtest_engine(...)` (`vibetrading_V1/backtest/engine_router.py:11-17`).
- `BacktestEngine.run(...)` enforces chronological one-bar-at-a-time processing to avoid look-ahead (`vibetrading_V1/backtest/engine.py:131-171`, `vibetrading_V1/backtest/engine.py:184-223`).

## Data path (OHLCV ingestion, frequency, persistence, schemas)
### Live/feed path
- All market feed adapters implement a common `DataFeedProvider` with `subscribe_candles(..., interval="1m")`, `stream_candles`, and historical fetch (`vibetrading_V1/services/data_feed/base.py:14-117`).
- Crypto feed parses exchange kline payloads into `Candle` and pushes to queue, persists closed bars to QuestDB, then publishes to NATS (`vibetrading_V1/services/data_feed/crypto_feed.py:199-217`, `vibetrading_V1/services/data_feed/crypto_feed.py:188-193`, `vibetrading_V1/services/data_feed/crypto_feed.py:303-352`).
- KIS KR/US historical fetch maps `1m` requests to daily period codes (`vibetrading_V1/services/data_feed/kr_feed.py:354-364`, `vibetrading_V1/services/data_feed/us_feed.py:358-369`), so minute semantics are not uniformly native across adapters.
- Kiwoom feed supports minute tick-unit mapping (`1m->1`, `3m->3`, etc.) and sorts parsed candles (`vibetrading_V1/services/data_feed/kiwoom_feed.py:440-452`, `vibetrading_V1/services/data_feed/kiwoom_feed.py:382-385`).

### Historical/backtest path
- `BacktestDataLoader` loads candles from QuestDB first and orders by ascending timestamp in SQL; CSV fallback sorts rows by timestamp (`vibetrading_V1/backtest/data_loader.py:21-24`, `vibetrading_V1/backtest/data_loader.py:82-89`, `vibetrading_V1/backtest/data_loader.py:133-148`).
- Backtest CLI default interval is `1d`, not `1m` (`vibetrading_V1/scripts/run_backtest.py:162`, `vibetrading_V1/backtest/data_loader.py:40`).

### Storage and schema
- Candle schema is defined in shared Pydantic models (`symbol/open/high/low/close/volume/interval/is_closed`) (`vibetrading_V1/shared/models.py:92-104`).
- QuestDB `candles` table DDL includes OHLCV + dedup upsert keys `(timestamp, market, symbol)` (`vibetrading_V1/scripts/init_db.py:51-66`).
- Runtime persistence split:
  - market bars -> QuestDB (`vibetrading_V1/services/data_feed/crypto_feed.py:314-331`)
  - orders/fills/positions/risk snapshots -> PostgreSQL via service-layer SQL calls (`vibetrading_V1/services/execution/order_manager.py:260-363`, `vibetrading_V1/services/risk_engine/position_tracker.py:221-265`, `vibetrading_V1/services/risk_engine/risk_manager.py:238-305`).

## Strategy path (signal generation, portfolio/position representation, risk constraints)
- Strategy loading is dynamic module import (`strategies.<name>`) with optional team contract validation (`vibetrading_V1/services/signal_gen/strategy_loader.py:135-177`, `vibetrading_V1/services/signal_gen/strategy_loader.py:225-240`).
- `SignalGenerationEngine` builds `StrategyContext`, executes strategy black-box, publishes and persists signals (`vibetrading_V1/services/signal_gen/engine.py:141-170`, `vibetrading_V1/services/signal_gen/engine.py:171-227`).
- Example strategy (`turtle_breakout`) declares config, uses 1m interval in config, and emits `ENTER_LONG/EXIT_LONG` based on breakout rules (`vibetrading_V1/strategies/turtle_breakout.py:37-77`, `vibetrading_V1/strategies/turtle_breakout.py:132-213`).
- Team backtest split exists at routing level (`trading/portfolio/arbitrage`), but portfolio/arbitrage engines are thin wrappers over base engine (`vibetrading_V1/backtest/engines/portfolio_engine.py:1-25`, `vibetrading_V1/backtest/engines/arbitrage_engine.py:1-25`).
- Position and account representations are shared mutable models (`Position`, `AccountSnapshot`) (`vibetrading_V1/shared/models.py:203-249`).
- Risk constraints are config-backed (`max_drawdown_pct`, `daily_loss_limit_pct`, `max_position_size_pct`) and loaded into `RiskManager` (`vibetrading_V1/shared/config.py:145-152`, `vibetrading_V1/services/risk_engine/risk_manager.py:60-64`).

## Execution path (orders, idempotency/retries, error handling, kill-switch)
1. Signal arrives (NATS or direct) and is converted to `Order` in `OrderManager` (`vibetrading_V1/services/execution/order_manager.py:130-220`).
2. Order is persisted, sent to broker adapter, and order events are published (`vibetrading_V1/services/execution/order_manager.py:170-185`, `vibetrading_V1/services/execution/order_manager.py:365-399`).
3. Fill callbacks update persistence and pending order map (`vibetrading_V1/services/execution/order_manager.py:222-259`).

### Order lifecycle shape (observed)
- `OrderStatus` enum: `PENDING -> SUBMITTED -> PARTIAL/FILLED/CANCELLED/REJECTED` (`vibetrading_V1/shared/models.py:57-65`).
- Paper adapter (`BrokerStub`) immediately fills market orders and updates status/filled fields (`vibetrading_V1/services/execution/broker_stub.py:69-141`).

### Idempotency/retry/rate-limit notes
- Signal publish uses NATS dedupe header via `msg_id=str(signal.id)` (`vibetrading_V1/services/signal_gen/engine.py:183-188`).
- Orders use UUID `id` but there is no explicit idempotency key contract in `Order` or `OrderManager` (`vibetrading_V1/shared/models.py:138-159`, `vibetrading_V1/services/execution/order_manager.py:187-220`).
- Error handling patterns are mostly catch/log + NATS `nak(delay=5)` in message consumers (`vibetrading_V1/services/signal_gen/engine.py:133-140`, `vibetrading_V1/services/execution/order_manager.py:130-139`, `vibetrading_V1/services/risk_engine/risk_manager.py:111-119`).
- Kiwoom feed documents TR request rate limit (5/sec) but centralized retry/backoff policy for brokers is not present in `OrderManager` submit path (`vibetrading_V1/services/data_feed/kiwoom_feed.py:57-59`, `vibetrading_V1/services/execution/order_manager.py:174-186`).

### Kill-switch flow
- `RiskManager` checks drawdown/daily loss and triggers kill switch on breach (`vibetrading_V1/services/risk_engine/risk_manager.py:149-201`).
- `KillSwitch` broadcasts `RISK.KILL_SWITCH` event (`vibetrading_V1/services/risk_engine/kill_switch.py:54-93`).
- `OrderManager` subscribes to kill switch, flips `_killed`, and cancels pending orders (`vibetrading_V1/services/execution/order_manager.py:112-117`, `vibetrading_V1/services/execution/order_manager.py:140-152`).

## Coupling hotspots (observed)
- **Global/singleton state**:
  - `get_settings()` cache (`vibetrading_V1/shared/config.py:253-262`)
  - DB singletons (`PostgresDatabase._instance`, `QuestDBDatabase._instance`) (`vibetrading_V1/shared/database.py:35-57`, `vibetrading_V1/shared/database.py:101-115`)
  - NATS singleton (`NatsMessaging._instance`) (`vibetrading_V1/shared/messaging.py:56-69`)
  - strategy cache globals (`_loaded_strategies`) (`vibetrading_V1/services/signal_gen/strategy_loader.py:180-204`)
  - global kill-switch map (`_kill_switches`) (`vibetrading_V1/services/risk_engine/kill_switch.py:112-127`).
- **IO tightly coupled with domain processing**:
  - Data feed message processing both parses domain objects and persists/publishes in same method (`vibetrading_V1/services/data_feed/crypto_feed.py:180-193`).
  - Signal engine both strategy evaluation and NATS/DB publishing (`vibetrading_V1/services/signal_gen/engine.py:164-227`).
  - Order manager handles transformation, broker IO, DB writes, and event publish (`vibetrading_V1/services/execution/order_manager.py:153-399`).
- **Circular imports check**:
  - Static SCC scan over `vibetrading_V1/**/*.py` found `SCC cycles: 0` in this snapshot (command in Evidence).

## Key files/symbols (>=15) and one-line roles
- `vibetrading_V1/run_strategy.py:113-223 (StrategyRunner)` — end-to-end live/paper pipeline orchestrator.
- `vibetrading_V1/scripts/run_backtest.py:54-136 (run_backtest/run_walk_forward)` — backtest/walk-forward CLI entry dispatch.
- `vibetrading_V1/backtest/engine_router.py:11-17 (resolve_backtest_engine)` — team-based engine routing.
- `vibetrading_V1/backtest/engine.py:95-171 (BacktestEngine.run)` — chronological event-driven backtest loop.
- `vibetrading_V1/backtest/data_loader.py:17-108 (BacktestDataLoader)` — historical candle loader (QuestDB/CSV).
- `vibetrading_V1/backtest/walk_forward.py:77-186 (WalkForwardValidator.run)` — rolling IS/OOS validation loop.
- `vibetrading_V1/services/broker_factory.py:23-105 (create_broker/create_data_feed)` — market adapter selection point.
- `vibetrading_V1/services/data_feed/base.py:14-117 (DataFeedProvider)` — market data adapter contract.
- `vibetrading_V1/services/data_feed/crypto_feed.py:35-413 (CryptoDataFeed)` — crypto kline ingest/persist/publish implementation.
- `vibetrading_V1/services/data_feed/kr_feed.py:25-365 (KRDataFeed)` — KIS KR feed (realtime + historical).
- `vibetrading_V1/services/data_feed/us_feed.py:23-369 (USDataFeed)` — KIS US feed (realtime + historical).
- `vibetrading_V1/services/data_feed/kiwoom_feed.py:48-464 (KiwoomDataFeed)` — Windows COM-based KR feed.
- `vibetrading_V1/services/signal_gen/engine.py:41-277 (SignalGenerationEngine)` — strategy execution and signal publication.
- `vibetrading_V1/services/signal_gen/strategy_loader.py:135-240 (load_strategy/resolve_strategy_team)` — dynamic strategy import and team contract validation.
- `vibetrading_V1/services/execution/order_manager.py:36-400 (OrderManager)` — signal-to-order lifecycle manager.
- `vibetrading_V1/services/execution/broker_stub.py:21-207 (BrokerStub)` — paper broker + fill simulation integration.
- `vibetrading_V1/services/execution/crypto_binance.py:28-298 (CryptoBinanceAdapter)` — Binance live/testnet broker adapter.
- `vibetrading_V1/services/risk_engine/risk_manager.py:30-310 (RiskManager)` — account risk checks and kill-switch triggers.
- `vibetrading_V1/services/risk_engine/kill_switch.py:15-127 (KillSwitch)` — emergency halt broadcaster.
- `vibetrading_V1/services/risk_engine/position_tracker.py:20-281 (PositionTracker)` — fill aggregation into positions.
- `vibetrading_V1/shared/models.py:92-312 (Candle/Signal/Order/Fill/Position/StrategyContext)` — canonical runtime data contracts.
- `vibetrading_V1/shared/fill_logic.py:28-308 (FillSimulator/get_fill_simulator)` — unified slippage/latency/fee simulator.
- `vibetrading_V1/shared/database.py:32-242 (PostgresDatabase/QuestDBDatabase)` — DB connection/persistence primitives.
- `vibetrading_V1/shared/messaging.py:53-326 (NatsMessaging/Subjects)` — NATS transport and subject taxonomy.
- `vibetrading_V1/strategies/turtle_breakout.py:95-216 (Strategy)` — reference single-strategy signal logic.

## Evidence
### Commands run
```bash
nl -ba vibetrading_V1/run_strategy.py | sed -n '1,360p'
nl -ba vibetrading_V1/scripts/run_backtest.py | sed -n '1,320p'
nl -ba vibetrading_V1/backtest/engine.py | sed -n '1,620p'
nl -ba vibetrading_V1/backtest/data_loader.py | sed -n '1,360p'
nl -ba vibetrading_V1/backtest/walk_forward.py | sed -n '1,360p'
nl -ba vibetrading_V1/services/signal_gen/engine.py | sed -n '1,380p'
nl -ba vibetrading_V1/services/signal_gen/strategy_loader.py | sed -n '1,360p'
nl -ba vibetrading_V1/services/execution/order_manager.py | sed -n '1,420p'
nl -ba vibetrading_V1/services/execution/broker_stub.py | sed -n '1,360p'
nl -ba vibetrading_V1/services/risk_engine/risk_manager.py | sed -n '1,420p'
nl -ba vibetrading_V1/services/risk_engine/kill_switch.py | sed -n '1,260p'
nl -ba vibetrading_V1/services/data_feed/base.py | sed -n '1,320p'
nl -ba vibetrading_V1/services/data_feed/crypto_feed.py | sed -n '1,520p'
nl -ba vibetrading_V1/services/data_feed/kr_feed.py | sed -n '300,470p'
nl -ba vibetrading_V1/services/data_feed/us_feed.py | sed -n '300,430p'
nl -ba vibetrading_V1/services/data_feed/kiwoom_feed.py | sed -n '300,470p'
nl -ba vibetrading_V1/shared/models.py | sed -n '1,420p'
nl -ba vibetrading_V1/shared/config.py | sed -n '1,460p'
nl -ba vibetrading_V1/shared/fill_logic.py | sed -n '1,360p'
nl -ba vibetrading_V1/shared/database.py | sed -n '1,360p'
nl -ba vibetrading_V1/shared/messaging.py | sed -n '1,360p'
python - <<'PY'
# static import SCC scan for vibetrading_V1
# output observed: SCC cycles: 0
PY
```

### Key outputs/findings (short excerpts)
- Static import SCC scan printed: `SCC cycles: 0`.
- Consumer handlers consistently show `msg.nak(delay=5)` on exception in signal/order/risk services.
- KR/US KIS interval conversion maps `1m` to daily-equivalent codes (`kr_feed.py:362`, `us_feed.py:367`).

### Concrete file/symbol references
- `vibetrading_V1/run_strategy.py:113-223 (StrategyRunner.start/stop)`
- `vibetrading_V1/run_strategy.py:184-200 (main candle loop)`
- `vibetrading_V1/scripts/run_backtest.py:54-103 (run_backtest)`
- `vibetrading_V1/scripts/run_backtest.py:139-197 (main CLI)`
- `vibetrading_V1/backtest/engine.py:131-171 (run)`
- `vibetrading_V1/backtest/data_loader.py:59-107 (_load_from_questdb)`
- `vibetrading_V1/services/data_feed/crypto_feed.py:188-193 (_process_message)`
- `vibetrading_V1/services/data_feed/crypto_feed.py:303-352 (_persist_candle/_publish_candle)`
- `vibetrading_V1/services/signal_gen/engine.py:183-188 (_publish_signal msg_id dedupe)`
- `vibetrading_V1/services/execution/order_manager.py:153-185 (_process_signal)`
- `vibetrading_V1/services/execution/order_manager.py:140-152 (_on_kill_switch)`
- `vibetrading_V1/services/execution/broker_stub.py:69-141 (submit_order/_execute_fill)`
- `vibetrading_V1/services/risk_engine/risk_manager.py:149-201 (_check_drawdown/_check_daily_loss)`
- `vibetrading_V1/services/risk_engine/kill_switch.py:54-93 (trigger)`
- `vibetrading_V1/shared/models.py:57-65 (OrderStatus)`
- `vibetrading_V1/shared/models.py:138-169 (Order + terminal states)`
- `vibetrading_V1/shared/config.py:145-152 (RiskSettings)`
- `vibetrading_V1/scripts/init_db.py:51-66 (candles table DDL + dedup keys)`
