"""
Database Initialization Script
Creates QuestDB tables for market data storage.

Usage:
    python scripts/init_db.py
"""

import logging
import os
import socket
from typing import Optional

import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_questdb_host() -> str:
    """Get QuestDB host from environment or default."""
    return os.getenv("QUESTDB_HOST", "localhost")


def get_questdb_port() -> int:
    """Get QuestDB HTTP port from environment or default."""
    return int(os.getenv("QUESTDB_HTTP_PORT", "9000"))


def execute_questdb_query(query: str, host: str, port: int) -> bool:
    """Execute a query against QuestDB via REST API."""
    url = f"http://{host}:{port}/exec"
    try:
        response = requests.get(url, params={"query": query}, timeout=30)
        if response.status_code == 200:
            return True
        else:
            logger.error(f"QuestDB query failed: {response.text}")
            return False
    except requests.RequestException as e:
        logger.error(f"QuestDB connection error: {e}")
        return False


def init_questdb_tables(host: str, port: int) -> None:
    """
    Initialize QuestDB tables for market data.
    Tables are partitioned by DAY for optimal time-series performance.
    """
    
    # Candles table - OHLCV data partitioned by day
    candles_query = """
    CREATE TABLE IF NOT EXISTS candles (
        timestamp TIMESTAMP,
        market SYMBOL,
        symbol SYMBOL,
        open DOUBLE,
        high DOUBLE,
        low DOUBLE,
        close DOUBLE,
        volume DOUBLE,
        quote_volume DOUBLE,
        trades LONG
    ) TIMESTAMP(timestamp) PARTITION BY DAY WAL
    DEDUP UPSERT KEYS(timestamp, market, symbol);
    """
    
    # Ticks table - Raw tick data for high-frequency analysis
    ticks_query = """
    CREATE TABLE IF NOT EXISTS ticks (
        timestamp TIMESTAMP,
        market SYMBOL,
        symbol SYMBOL,
        price DOUBLE,
        quantity DOUBLE,
        side SYMBOL,
        trade_id STRING
    ) TIMESTAMP(timestamp) PARTITION BY DAY WAL;
    """
    
    # Orderbook snapshots - For slippage analysis
    orderbook_query = """
    CREATE TABLE IF NOT EXISTS orderbook_snapshots (
        timestamp TIMESTAMP,
        market SYMBOL,
        symbol SYMBOL,
        bid_price_1 DOUBLE,
        bid_qty_1 DOUBLE,
        bid_price_2 DOUBLE,
        bid_qty_2 DOUBLE,
        bid_price_3 DOUBLE,
        bid_qty_3 DOUBLE,
        bid_price_4 DOUBLE,
        bid_qty_4 DOUBLE,
        bid_price_5 DOUBLE,
        bid_qty_5 DOUBLE,
        ask_price_1 DOUBLE,
        ask_qty_1 DOUBLE,
        ask_price_2 DOUBLE,
        ask_qty_2 DOUBLE,
        ask_price_3 DOUBLE,
        ask_qty_3 DOUBLE,
        ask_price_4 DOUBLE,
        ask_qty_4 DOUBLE,
        ask_price_5 DOUBLE,
        ask_qty_5 DOUBLE
    ) TIMESTAMP(timestamp) PARTITION BY DAY WAL;
    """
    
    # Corporate actions table - For bias-safe backtesting
    corporate_actions_query = """
    CREATE TABLE IF NOT EXISTS corporate_actions (
        timestamp TIMESTAMP,
        market SYMBOL,
        symbol SYMBOL,
        action_type SYMBOL,
        factor DOUBLE,
        description STRING
    ) TIMESTAMP(timestamp) PARTITION BY MONTH WAL;
    """
    
    # Symbol metadata - For survivorship tracking
    symbol_metadata_query = """
    CREATE TABLE IF NOT EXISTS symbol_metadata (
        timestamp TIMESTAMP,
        market SYMBOL,
        symbol SYMBOL,
        name STRING,
        is_active BOOLEAN,
        listed_date TIMESTAMP,
        delisted_date TIMESTAMP,
        sector STRING,
        metadata STRING
    ) TIMESTAMP(timestamp) PARTITION BY YEAR WAL
    DEDUP UPSERT KEYS(timestamp, market, symbol);
    """
    
    tables = [
        ("candles", candles_query),
        ("ticks", ticks_query),
        ("orderbook_snapshots", orderbook_query),
        ("corporate_actions", corporate_actions_query),
        ("symbol_metadata", symbol_metadata_query),
    ]
    
    for table_name, query in tables:
        logger.info(f"Creating table '{table_name}'...")
        if execute_questdb_query(query, host, port):
            logger.info(f"Table '{table_name}' created/verified successfully")
        else:
            logger.error(f"Failed to create table '{table_name}'")


def check_questdb_connection(host: str, port: int) -> bool:
    """Check if QuestDB is accessible."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except socket.error:
        return False


def main() -> None:
    """Main entry point for database initialization."""
    host = get_questdb_host()
    port = get_questdb_port()
    
    logger.info(f"Checking QuestDB connection at {host}:{port}")
    
    if not check_questdb_connection(host, port):
        logger.error(f"Cannot connect to QuestDB at {host}:{port}")
        logger.error("Ensure QuestDB is running: docker-compose up -d questdb")
        return
    
    logger.info("QuestDB connection successful")
    init_questdb_tables(host, port)
    
    logger.info("\n" + "=" * 60)
    logger.info("QuestDB Initialization Complete")
    logger.info("=" * 60)
    logger.info(f"Web Console: http://{host}:{port}")
    logger.info(f"ILP Port: {os.getenv('QUESTDB_ILP_PORT', '9009')}")
    logger.info(f"PostgreSQL Port: {os.getenv('QUESTDB_PG_PORT', '8812')}")


if __name__ == "__main__":
    main()
