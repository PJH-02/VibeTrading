"""
Signal Generation Engine
Black-box strategy executor that processes candles and emits signals.
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

try:
    from nats.aio.msg import Msg
except ImportError:
    Msg = Any  # type: ignore

from shared.config import get_settings
from shared.database import get_postgres
from shared.models import (
    Candle,
    Market,
    Position,
    Signal,
    StrategyContext,
    TradingMode,
    TeamType,
)

from .strategy_loader import StrategyWrapper, get_strategy

logger = logging.getLogger(__name__)

try:
    from shared.messaging import Subjects, deserialize_message, ensure_connected
except ImportError:
    Subjects = None  # type: ignore
    deserialize_message = None  # type: ignore
    ensure_connected = None  # type: ignore


class SignalGenerationEngine:
    """
    Engine that runs strategies against incoming candle data.
    
    Responsibilities:
    - Subscribe to candle streams from NATS
    - Execute strategy logic (black box)
    - Publish generated signals
    - Track positions for context
    
    The engine does NOT contain any strategy logic itself.
    """
    
    def __init__(
        self,
        market: Market,
        mode: TradingMode,
        strategy_name: str,
        team: TeamType = TeamType.TRADING,
    ) -> None:
        """
        Initialize signal generation engine.
        
        Args:
            market: Market scope
            mode: Trading mode (backtest/paper/live)
            strategy_name: Name of strategy to load
        """
        self._market = market
        self._mode = mode
        self._strategy_name = strategy_name
        self._team = team
        self._strategy: Optional[StrategyWrapper] = None
        self._running = False
        self._positions: Dict[str, Position] = {}  # symbol -> position
        self._last_prices: Dict[str, Decimal] = {}  # symbol -> last price
    
    @property
    def market(self) -> Market:
        """Get market scope."""
        return self._market
    
    @property
    def mode(self) -> TradingMode:
        """Get trading mode."""
        return self._mode
    
    @property
    def is_running(self) -> bool:
        """Check if engine is running."""
        return self._running
    
    async def start(self) -> None:
        """Start the signal generation engine."""
        logger.info(f"Starting signal gen engine: market={self._market}, strategy={self._strategy_name}")

        if Subjects is None or ensure_connected is None:
            raise RuntimeError("NATS dependencies are not installed")
        
        # Load strategy
        self._strategy = get_strategy(self._strategy_name, expected_team=self._team)
        self._strategy.initialize()
        
        # Subscribe to candle stream
        messaging = await ensure_connected()
        await messaging.subscribe(
            subject=Subjects.candles(self._market.value),
            handler=self._on_candle_message,
            durable=f"signal_gen_{self._market.value}_{self._strategy_name}",
            queue="signal_gen_workers",
        )
        
        self._running = True
        logger.info("Signal generation engine started")
    
    async def stop(self) -> None:
        """Stop the signal generation engine."""
        self._running = False
        logger.info("Signal generation engine stopped")
    
    async def _on_candle_message(self, msg: Msg) -> None:
        """Handle incoming candle message."""
        if deserialize_message is None:
            raise RuntimeError("NATS dependencies are not installed")

        try:
            candle = deserialize_message(msg.data, Candle)
            await self._process_candle(candle)
            await msg.ack()
        except Exception as e:
            logger.error(f"Error processing candle: {e}")
            await msg.nak(delay=5)
    
    async def _process_candle(self, candle: Candle) -> None:
        """
        Process a candle through the strategy.
        
        Args:
            candle: Incoming candle data
        """
        if not self._strategy:
            return
        
        # Update last price
        self._last_prices[candle.symbol] = candle.close
        
        # Build context
        context = StrategyContext(
            market=self._market,
            mode=self._mode,
            symbol=candle.symbol,
            current_time=candle.timestamp,
            current_price=candle.close,
            position=self._positions.get(candle.symbol),
        )
        
        # Execute strategy (black box)
        result = self._strategy.on_candle(candle, context)
        
        # Publish any signals
        for signal in result.signals:
            await self._publish_signal(signal)
    
    async def _publish_signal(self, signal: Signal) -> None:
        """Publish signal to NATS."""
        if ensure_connected is None or Subjects is None:
            return

        try:
            messaging = await ensure_connected()
            await messaging.publish(
                subject=Subjects.signals(self._market.value),
                data=signal,
                msg_id=str(signal.id),  # Deduplication
            )
            logger.info(f"Published signal: {signal.action} {signal.symbol}")
            
            # Also persist to database
            await self._persist_signal(signal)
            
        except Exception as e:
            logger.error(f"Error publishing signal: {e}")
    
    async def _persist_signal(self, signal: Signal) -> None:
        """Persist signal to PostgreSQL."""
        try:
            postgres = get_postgres()
            async with postgres.session() as session:
                from sqlalchemy import text
                
                await session.execute(
                    text("""
                        INSERT INTO signals (id, market, mode, symbol, side, strength, 
                                           strategy_name, signal_time, metadata)
                        VALUES (:id, :market, :mode, :symbol, :side, :strength,
                                :strategy_name, :signal_time, :metadata)
                    """),
                    {
                        "id": str(signal.id),
                        "market": signal.market.value,
                        "mode": signal.mode.value,
                        "symbol": signal.symbol,
                        "side": "buy" if "long" in signal.action.value.lower() else "sell",
                        "strength": float(signal.strength),
                        "strategy_name": signal.strategy_name,
                        "signal_time": signal.timestamp,
                        "metadata": signal.metadata,
                    }
                )
        except Exception as e:
            logger.error(f"Error persisting signal: {e}")
    
    def update_position(self, position: Position) -> None:
        """
        Update tracked position.
        
        Called by external position tracker to keep
        strategy context updated.
        """
        if position.is_open:
            self._positions[position.symbol] = position
        else:
            self._positions.pop(position.symbol, None)
    
    def process_candle_sync(self, candle: Candle) -> List[Signal]:
        """
        Synchronously process a candle (for backtesting).
        
        Unlike the async message handler, this returns
        signals directly for the backtesting engine.
        
        Args:
            candle: Candle to process
            
        Returns:
            List of generated signals
        """
        if not self._strategy:
            self._strategy = get_strategy(self._strategy_name, expected_team=self._team)
            self._strategy.initialize()
        
        self._last_prices[candle.symbol] = candle.close
        
        context = StrategyContext(
            market=self._market,
            mode=self._mode,
            symbol=candle.symbol,
            current_time=candle.timestamp,
            current_price=candle.close,
            position=self._positions.get(candle.symbol),
        )
        
        result = self._strategy.on_candle(candle, context)
        return result.signals
    
    def reset(self) -> None:
        """Reset engine state (for backtesting)."""
        self._positions.clear()
        self._last_prices.clear()
        if self._strategy:
            self._strategy.reset()
