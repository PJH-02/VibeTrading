"""
Position Tracker
Real-time position aggregation and P&L calculation.
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional

from nats.aio.msg import Msg

from shared.database import get_postgres
from shared.messaging import Subjects, deserialize_message, ensure_connected
from shared.models import Fill, Market, OrderSide, Position, TradingMode

logger = logging.getLogger(__name__)


class PositionTracker:
    """
    Tracks positions across fills.
    
    Aggregates fills into positions and calculates:
    - Average entry price
    - Unrealized P&L
    - Realized P&L
    """
    
    def __init__(
        self,
        market: Market,
        mode: TradingMode,
    ) -> None:
        """
        Initialize position tracker.
        
        Args:
            market: Market scope
            mode: Trading mode
        """
        self._market = market
        self._mode = mode
        self._running = False
        self._positions: Dict[str, Position] = {}  # symbol -> position
        self._last_prices: Dict[str, Decimal] = {}  # symbol -> last price
    
    @property
    def market(self) -> Market:
        """Get market scope."""
        return self._market
    
    @property
    def positions(self) -> Dict[str, Position]:
        """Get current positions."""
        return self._positions.copy()
    
    async def start(self) -> None:
        """Start position tracker."""
        logger.info(f"Starting position tracker: market={self._market}")
        
        # Load existing positions from database
        await self._load_positions()
        
        # Subscribe to fills
        messaging = await ensure_connected()
        await messaging.subscribe(
            subject=Subjects.fills(self._market.value),
            handler=self._on_fill_message,
            durable=f"position_tracker_{self._market.value}",
        )
        
        self._running = True
        logger.info(f"Position tracker started with {len(self._positions)} positions")
    
    async def stop(self) -> None:
        """Stop position tracker."""
        self._running = False
        logger.info("Position tracker stopped")
    
    async def _load_positions(self) -> None:
        """Load open positions from database."""
        try:
            postgres = get_postgres()
            async with postgres.session() as session:
                from sqlalchemy import text
                
                result = await session.execute(
                    text("""
                        SELECT * FROM positions
                        WHERE market = :market
                          AND mode = :mode
                          AND closed_at IS NULL
                    """),
                    {"market": self._market.value, "mode": self._mode.value}
                )
                
                rows = result.mappings().all()
                for row in rows:
                    position = Position(
                        id=row["id"],
                        market=Market(row["market"]),
                        mode=TradingMode(row["mode"]),
                        symbol=row["symbol"],
                        side=OrderSide(row["side"]),
                        quantity=Decimal(str(row["quantity"])),
                        avg_entry_price=Decimal(str(row["avg_entry_price"])),
                        current_price=Decimal(str(row["current_price"])) if row["current_price"] else None,
                        unrealized_pnl=Decimal(str(row["unrealized_pnl"])),
                        realized_pnl=Decimal(str(row["realized_pnl"])),
                        strategy_name=row["strategy_name"],
                        opened_at=row["opened_at"],
                    )
                    self._positions[position.symbol] = position
                    
        except Exception as e:
            logger.error(f"Error loading positions: {e}")
    
    async def _on_fill_message(self, msg: Msg) -> None:
        """Handle fill message."""
        try:
            fill = deserialize_message(msg.data, Fill)
            await self._process_fill(fill)
            await msg.ack()
        except Exception as e:
            logger.error(f"Error processing fill: {e}")
            await msg.nak(delay=5)
    
    async def _process_fill(self, fill: Fill) -> None:
        """
        Process fill and update position.
        
        Args:
            fill: Fill to process
        """
        symbol = fill.symbol
        existing = self._positions.get(symbol)
        
        if existing is None:
            # New position
            position = Position(
                market=self._market,
                mode=self._mode,
                symbol=symbol,
                side=fill.side,
                quantity=fill.quantity,
                avg_entry_price=fill.price,
                current_price=fill.price,
                strategy_name=fill.metadata.get("strategy_name", "unknown"),
            )
            self._positions[symbol] = position
            
        else:
            # Update existing position
            if fill.side == existing.side:
                # Adding to position
                total_cost = existing.quantity * existing.avg_entry_price + fill.quantity * fill.price
                new_quantity = existing.quantity + fill.quantity
                existing.avg_entry_price = total_cost / new_quantity
                existing.quantity = new_quantity
            else:
                # Reducing/closing position
                if fill.quantity >= existing.quantity:
                    # Close position
                    realized_pnl = self._calculate_realized_pnl(existing, fill)
                    existing.realized_pnl += realized_pnl
                    existing.quantity = Decimal("0")
                    existing.closed_at = datetime.utcnow()
                    del self._positions[symbol]
                else:
                    # Partial close
                    realized_pnl = self._calculate_realized_pnl_partial(existing, fill)
                    existing.realized_pnl += realized_pnl
                    existing.quantity -= fill.quantity
            
            existing.current_price = fill.price
            existing.updated_at = datetime.utcnow()
        
        # Persist position
        if symbol in self._positions:
            await self._persist_position(self._positions[symbol])
        
        logger.info(f"Position updated: {symbol} qty={self._positions.get(symbol, existing).quantity if symbol in self._positions else 0}")
    
    def _calculate_realized_pnl(self, position: Position, fill: Fill) -> Decimal:
        """Calculate realized P&L for closing trade."""
        if position.side == OrderSide.BUY:
            # Long position closed
            return (fill.price - position.avg_entry_price) * position.quantity
        else:
            # Short position closed
            return (position.avg_entry_price - fill.price) * position.quantity
    
    def _calculate_realized_pnl_partial(self, position: Position, fill: Fill) -> Decimal:
        """Calculate realized P&L for partial close."""
        if position.side == OrderSide.BUY:
            return (fill.price - position.avg_entry_price) * fill.quantity
        else:
            return (position.avg_entry_price - fill.price) * fill.quantity
    
    def update_price(self, symbol: str, price: Decimal) -> None:
        """
        Update current price for a symbol.
        
        Args:
            symbol: Symbol to update
            price: Current market price
        """
        self._last_prices[symbol] = price
        
        if symbol in self._positions:
            position = self._positions[symbol]
            position.current_price = price
            
            # Calculate unrealized P&L
            if position.side == OrderSide.BUY:
                position.unrealized_pnl = (price - position.avg_entry_price) * position.quantity
            else:
                position.unrealized_pnl = (position.avg_entry_price - price) * position.quantity
    
    async def _persist_position(self, position: Position) -> None:
        """Persist position to database."""
        try:
            postgres = get_postgres()
            async with postgres.session() as session:
                from sqlalchemy import text
                
                await session.execute(
                    text("""
                        INSERT INTO positions (
                            id, market, mode, symbol, side, quantity,
                            avg_entry_price, current_price, unrealized_pnl,
                            realized_pnl, strategy_name, opened_at, updated_at, closed_at
                        ) VALUES (
                            :id, :market, :mode, :symbol, :side, :quantity,
                            :avg_entry_price, :current_price, :unrealized_pnl,
                            :realized_pnl, :strategy_name, :opened_at, :updated_at, :closed_at
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            quantity = EXCLUDED.quantity,
                            current_price = EXCLUDED.current_price,
                            unrealized_pnl = EXCLUDED.unrealized_pnl,
                            realized_pnl = EXCLUDED.realized_pnl,
                            updated_at = EXCLUDED.updated_at,
                            closed_at = EXCLUDED.closed_at
                    """),
                    {
                        "id": str(position.id),
                        "market": position.market.value,
                        "mode": position.mode.value,
                        "symbol": position.symbol,
                        "side": position.side.value,
                        "quantity": float(position.quantity),
                        "avg_entry_price": float(position.avg_entry_price),
                        "current_price": float(position.current_price) if position.current_price else None,
                        "unrealized_pnl": float(position.unrealized_pnl),
                        "realized_pnl": float(position.realized_pnl),
                        "strategy_name": position.strategy_name,
                        "opened_at": position.opened_at,
                        "updated_at": position.updated_at,
                        "closed_at": position.closed_at,
                    }
                )
        except Exception as e:
            logger.error(f"Error persisting position: {e}")
    
    def get_total_equity(self, balance: Decimal) -> Decimal:
        """
        Calculate total equity including positions.
        
        Args:
            balance: Cash balance
            
        Returns:
            Total equity
        """
        return balance + sum(p.unrealized_pnl for p in self._positions.values())
    
    def get_total_unrealized_pnl(self) -> Decimal:
        """Get total unrealized P&L across all positions."""
        return sum(p.unrealized_pnl for p in self._positions.values())
