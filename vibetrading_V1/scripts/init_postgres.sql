-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- PostgreSQL Schema Initialization
-- Trading System - Transactional State
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ──────────────────────────────────────────────────────────────────────────
-- Enum Types
-- ──────────────────────────────────────────────────────────────────────────
CREATE TYPE market_type AS ENUM ('kr', 'us', 'crypto');
CREATE TYPE order_side AS ENUM ('buy', 'sell');
CREATE TYPE order_type AS ENUM ('market', 'limit', 'stop', 'stop_limit');
CREATE TYPE order_status AS ENUM ('pending', 'submitted', 'partial', 'filled', 'cancelled', 'rejected');
CREATE TYPE trading_mode AS ENUM ('backtest', 'paper', 'live');

-- ──────────────────────────────────────────────────────────────────────────
-- Orders Table
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_id VARCHAR(128),  -- Broker order ID
    market market_type NOT NULL,
    mode trading_mode NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    side order_side NOT NULL,
    order_type order_type NOT NULL,
    quantity DECIMAL(20, 8) NOT NULL,
    filled_quantity DECIMAL(20, 8) DEFAULT 0,
    price DECIMAL(20, 8),  -- NULL for market orders
    stop_price DECIMAL(20, 8),
    status order_status NOT NULL DEFAULT 'pending',
    strategy_name VARCHAR(64) NOT NULL,
    signal_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_at TIMESTAMPTZ,
    filled_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    error_message TEXT,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_orders_market_status ON orders(market, status);
CREATE INDEX idx_orders_symbol ON orders(symbol);
CREATE INDEX idx_orders_strategy ON orders(strategy_name);
CREATE INDEX idx_orders_created_at ON orders(created_at);

-- ──────────────────────────────────────────────────────────────────────────
-- Fills Table
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS fills (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id UUID NOT NULL REFERENCES orders(id),
    external_id VARCHAR(128),
    market market_type NOT NULL,
    mode trading_mode NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    side order_side NOT NULL,
    quantity DECIMAL(20, 8) NOT NULL,
    price DECIMAL(20, 8) NOT NULL,
    commission DECIMAL(20, 8) NOT NULL DEFAULT 0,
    commission_asset VARCHAR(16),
    slippage_bps DECIMAL(10, 4) DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    filled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_fills_order_id ON fills(order_id);
CREATE INDEX idx_fills_market ON fills(market);
CREATE INDEX idx_fills_symbol ON fills(symbol);
CREATE INDEX idx_fills_filled_at ON fills(filled_at);

-- ──────────────────────────────────────────────────────────────────────────
-- Positions Table
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS positions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    market market_type NOT NULL,
    mode trading_mode NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    side order_side NOT NULL,
    quantity DECIMAL(20, 8) NOT NULL,
    avg_entry_price DECIMAL(20, 8) NOT NULL,
    current_price DECIMAL(20, 8),
    unrealized_pnl DECIMAL(20, 8) DEFAULT 0,
    realized_pnl DECIMAL(20, 8) DEFAULT 0,
    strategy_name VARCHAR(64) NOT NULL,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'
);

CREATE UNIQUE INDEX idx_positions_active ON positions(market, mode, symbol, strategy_name) 
    WHERE closed_at IS NULL;
CREATE INDEX idx_positions_strategy ON positions(strategy_name);

-- ──────────────────────────────────────────────────────────────────────────
-- Signals Table (Audit Trail)
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    market market_type NOT NULL,
    mode trading_mode NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    side order_side NOT NULL,
    strength DECIMAL(5, 4),  -- Signal strength 0-1
    strategy_name VARCHAR(64) NOT NULL,
    signal_time TIMESTAMPTZ NOT NULL,
    processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMPTZ,
    order_id UUID REFERENCES orders(id),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_signals_market_processed ON signals(market, processed);
CREATE INDEX idx_signals_strategy ON signals(strategy_name);
CREATE INDEX idx_signals_signal_time ON signals(signal_time);

-- ──────────────────────────────────────────────────────────────────────────
-- Account Snapshots Table
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS account_snapshots (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    market market_type NOT NULL,
    mode trading_mode NOT NULL,
    balance DECIMAL(20, 8) NOT NULL,
    equity DECIMAL(20, 8) NOT NULL,
    unrealized_pnl DECIMAL(20, 8) DEFAULT 0,
    realized_pnl DECIMAL(20, 8) DEFAULT 0,
    daily_pnl DECIMAL(20, 8) DEFAULT 0,
    drawdown_pct DECIMAL(10, 4) DEFAULT 0,
    peak_equity DECIMAL(20, 8),
    snapshot_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_account_snapshots_market ON account_snapshots(market, mode);
CREATE INDEX idx_account_snapshots_time ON account_snapshots(snapshot_time);

-- ──────────────────────────────────────────────────────────────────────────
-- Risk Events Table
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS risk_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    market market_type NOT NULL,
    mode trading_mode NOT NULL,
    event_type VARCHAR(32) NOT NULL,  -- drawdown_breach, daily_loss_breach, kill_switch, etc.
    severity VARCHAR(16) NOT NULL,  -- info, warning, critical
    message TEXT NOT NULL,
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_risk_events_market ON risk_events(market, mode);
CREATE INDEX idx_risk_events_type ON risk_events(event_type);
CREATE INDEX idx_risk_events_triggered ON risk_events(triggered_at);

-- ──────────────────────────────────────────────────────────────────────────
-- Configuration Versions Table
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS config_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    config_name VARCHAR(64) NOT NULL,
    config_data JSONB NOT NULL,
    version INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by VARCHAR(64) DEFAULT 'system',
    UNIQUE(config_name, version)
);

CREATE INDEX idx_config_versions_name ON config_versions(config_name);

-- ──────────────────────────────────────────────────────────────────────────
-- Update Triggers
-- ──────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER orders_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER positions_updated_at
    BEFORE UPDATE ON positions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
