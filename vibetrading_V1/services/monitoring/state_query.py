"""
State Query Interface
Read-only query interface for trading state.

Extension point for: Telegram bots, Web dashboards
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

from shared.database import get_postgres, get_questdb
from shared.models import Market, OrderStatus, Position, TradingMode

logger = logging.getLogger(__name__)


@dataclass
class AccountState:
    """Account state summary."""
    market: Market
    mode: TradingMode
    balance: Decimal
    equity: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    drawdown_pct: Decimal
    open_positions: int
    pending_orders: int


@dataclass
class PositionSummary:
    """Position summary for display."""
    symbol: str
    side: str
    quantity: Decimal
    avg_entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    pnl_pct: Decimal


@dataclass
class OrderSummary:
    """Order summary for display."""
    id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    price: Optional[Decimal]
    status: str
    created_at: str


class StateQueryInterface:
    """
    Read-only interface for querying trading state.
    
    This interface is designed to be consumed by:
    - Telegram bot commands
    - Web dashboard APIs
    - CLI status commands
    
    No business logic here - pure data retrieval.
    """
    
    def __init__(self, market: Market, mode: TradingMode) -> None:
        """
        Initialize state query interface.
        
        Args:
            market: Market scope
            mode: Trading mode
        """
        self._market = market
        self._mode = mode
    
    async def get_account_state(self) -> Optional[AccountState]:
        """
        Get current account state.
        
        Returns:
            AccountState or None if unavailable
        """
        try:
            postgres = get_postgres()
            async with postgres.session() as session:
                from sqlalchemy import text
                
                # Get latest snapshot
                result = await session.execute(
                    text("""
                        SELECT * FROM account_snapshots
                        WHERE market = :market AND mode = :mode
                        ORDER BY snapshot_time DESC
                        LIMIT 1
                    """),
                    {"market": self._market.value, "mode": self._mode.value}
                )
                
                row = result.mappings().first()
                if not row:
                    return None
                
                # Count open positions
                pos_result = await session.execute(
                    text("""
                        SELECT COUNT(*) FROM positions
                        WHERE market = :market AND mode = :mode AND closed_at IS NULL
                    """),
                    {"market": self._market.value, "mode": self._mode.value}
                )
                open_positions = pos_result.scalar() or 0
                
                # Count pending orders
                order_result = await session.execute(
                    text("""
                        SELECT COUNT(*) FROM orders
                        WHERE market = :market AND mode = :mode 
                          AND status IN ('pending', 'submitted', 'partial')
                    """),
                    {"market": self._market.value, "mode": self._mode.value}
                )
                pending_orders = order_result.scalar() or 0
                
                return AccountState(
                    market=self._market,
                    mode=self._mode,
                    balance=Decimal(str(row["balance"])),
                    equity=Decimal(str(row["equity"])),
                    unrealized_pnl=Decimal(str(row.get("unrealized_pnl", 0))),
                    realized_pnl=Decimal(str(row.get("realized_pnl", 0))),
                    drawdown_pct=Decimal(str(row.get("drawdown_pct", 0))),
                    open_positions=open_positions,
                    pending_orders=pending_orders,
                )
                
        except Exception as e:
            logger.error(f"Error getting account state: {e}")
            return None
    
    async def get_positions(self) -> List[PositionSummary]:
        """
        Get all open positions.
        
        Returns:
            List of position summaries
        """
        try:
            postgres = get_postgres()
            async with postgres.session() as session:
                from sqlalchemy import text
                
                result = await session.execute(
                    text("""
                        SELECT * FROM positions
                        WHERE market = :market AND mode = :mode AND closed_at IS NULL
                        ORDER BY opened_at DESC
                    """),
                    {"market": self._market.value, "mode": self._mode.value}
                )
                
                positions = []
                for row in result.mappings().all():
                    entry = Decimal(str(row["avg_entry_price"]))
                    current = Decimal(str(row["current_price"])) if row["current_price"] else entry
                    pnl_pct = ((current - entry) / entry * 100) if entry > 0 else Decimal("0")
                    
                    if row["side"] == "sell":
                        pnl_pct = -pnl_pct
                    
                    positions.append(PositionSummary(
                        symbol=row["symbol"],
                        side=row["side"],
                        quantity=Decimal(str(row["quantity"])),
                        avg_entry_price=entry,
                        current_price=current,
                        unrealized_pnl=Decimal(str(row["unrealized_pnl"])),
                        pnl_pct=pnl_pct,
                    ))
                
                return positions
                
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return []
    
    async def get_pending_orders(self) -> List[OrderSummary]:
        """
        Get all pending orders.
        
        Returns:
            List of order summaries
        """
        try:
            postgres = get_postgres()
            async with postgres.session() as session:
                from sqlalchemy import text
                
                result = await session.execute(
                    text("""
                        SELECT * FROM orders
                        WHERE market = :market AND mode = :mode
                          AND status IN ('pending', 'submitted', 'partial')
                        ORDER BY created_at DESC
                    """),
                    {"market": self._market.value, "mode": self._mode.value}
                )
                
                orders = []
                for row in result.mappings().all():
                    orders.append(OrderSummary(
                        id=str(row["id"])[:8],
                        symbol=row["symbol"],
                        side=row["side"],
                        order_type=row["order_type"],
                        quantity=Decimal(str(row["quantity"])),
                        price=Decimal(str(row["price"])) if row["price"] else None,
                        status=row["status"],
                        created_at=row["created_at"].isoformat() if row["created_at"] else "",
                    ))
                
                return orders
                
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return []
    
    async def get_recent_fills(self, limit: int = 10) -> List[Dict]:
        """
        Get recent fills.
        
        Args:
            limit: Number of fills to return
            
        Returns:
            List of fill dictionaries
        """
        try:
            postgres = get_postgres()
            async with postgres.session() as session:
                from sqlalchemy import text
                
                result = await session.execute(
                    text("""
                        SELECT * FROM fills
                        WHERE market = :market AND mode = :mode
                        ORDER BY filled_at DESC
                        LIMIT :limit
                    """),
                    {"market": self._market.value, "mode": self._mode.value, "limit": limit}
                )
                
                return [dict(row) for row in result.mappings().all()]
                
        except Exception as e:
            logger.error(f"Error getting fills: {e}")
            return []
    
    async def get_risk_status(self) -> Dict:
        """
        Get current risk status.
        
        Returns:
            Risk status dictionary
        """
        try:
            postgres = get_postgres()
            async with postgres.session() as session:
                from sqlalchemy import text
                
                # Get recent risk events
                result = await session.execute(
                    text("""
                        SELECT * FROM risk_events
                        WHERE market = :market AND mode = :mode
                        ORDER BY triggered_at DESC
                        LIMIT 5
                    """),
                    {"market": self._market.value, "mode": self._mode.value}
                )
                
                events = [dict(row) for row in result.mappings().all()]
                
                # Check for active kill switch
                kill_active = any(
                    e.get("event_type") == "kill_switch" and e.get("resolved_at") is None
                    for e in events
                )
                
                return {
                    "kill_switch_active": kill_active,
                    "recent_events": events,
                }
                
        except Exception as e:
            logger.error(f"Error getting risk status: {e}")
            return {"kill_switch_active": False, "recent_events": []}
