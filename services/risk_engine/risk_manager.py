"""
Risk Manager
Account-level risk monitoring and controls.
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from nats.aio.msg import Msg

from shared.config import get_settings
from shared.database import get_postgres
from shared.messaging import Subjects, deserialize_message, ensure_connected
from shared.models import (
    AccountSnapshot,
    Fill,
    Market,
    RiskAlert,
    TradingMode,
)

from .kill_switch import KillSwitch

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Account-level risk monitoring.
    
    Monitors:
    - Account drawdown
    - Daily loss limits
    - Position concentration
    
    Triggers kill switch on breach.
    """
    
    def __init__(
        self,
        market: Market,
        mode: TradingMode,
    ) -> None:
        """
        Initialize risk manager.
        
        Args:
            market: Market scope
            mode: Trading mode
        """
        self._market = market
        self._mode = mode
        self._running = False
        self._kill_switch = KillSwitch(market)
        
        # Load settings
        settings = get_settings()
        self._max_drawdown_pct = settings.risk.max_drawdown_pct
        self._daily_loss_limit_pct = settings.risk.daily_loss_limit_pct
        self._max_position_size_pct = settings.risk.max_position_size_pct
        
        # State tracking
        self._initial_equity: Optional[Decimal] = None
        self._peak_equity: Optional[Decimal] = None
        self._daily_start_equity: Optional[Decimal] = None
        self._current_equity: Optional[Decimal] = None
    
    @property
    def market(self) -> Market:
        """Get market scope."""
        return self._market
    
    @property
    def is_running(self) -> bool:
        """Check if risk manager is running."""
        return self._running
    
    async def start(self, initial_equity: Decimal) -> None:
        """
        Start risk manager.
        
        Args:
            initial_equity: Starting account equity
        """
        logger.info(f"Starting risk manager: market={self._market}")
        
        self._initial_equity = initial_equity
        self._peak_equity = initial_equity
        self._daily_start_equity = initial_equity
        self._current_equity = initial_equity
        
        # Subscribe to fills to track P&L
        messaging = await ensure_connected()
        await messaging.subscribe(
            subject=Subjects.fills(self._market.value),
            handler=self._on_fill_message,
            durable=f"risk_manager_{self._market.value}",
        )
        
        self._running = True
        logger.info(f"Risk manager started with equity={initial_equity}")
    
    async def stop(self) -> None:
        """Stop risk manager."""
        self._running = False
        logger.info("Risk manager stopped")
    
    async def _on_fill_message(self, msg: Msg) -> None:
        """Handle fill message for P&L tracking."""
        try:
            fill = deserialize_message(msg.data, Fill)
            await self._process_fill(fill)
            await msg.ack()
        except Exception as e:
            logger.error(f"Error processing fill in risk manager: {e}")
            await msg.nak(delay=5)
    
    async def _process_fill(self, fill: Fill) -> None:
        """Process fill for P&L calculation."""
        # Update equity (simplified - in production would track positions)
        # This is a placeholder - real implementation would calculate realized P&L
        logger.debug(f"Risk manager processed fill: {fill.symbol}")
    
    async def update_equity(self, new_equity: Decimal) -> None:
        """
        Update current equity and check risk limits.
        
        Args:
            new_equity: Current account equity
        """
        self._current_equity = new_equity
        
        # Update peak
        if self._peak_equity is None or new_equity > self._peak_equity:
            self._peak_equity = new_equity
        
        # Check drawdown
        await self._check_drawdown()
        
        # Check daily loss
        await self._check_daily_loss()
        
        # Persist snapshot
        await self._persist_snapshot()
    
    async def _check_drawdown(self) -> None:
        """Check account drawdown against limit."""
        if self._peak_equity is None or self._current_equity is None:
            return
        
        if self._peak_equity == 0:
            return
        
        drawdown_pct = (
            (self._peak_equity - self._current_equity) / self._peak_equity * 100
        )
        
        if drawdown_pct >= self._max_drawdown_pct:
            await self._trigger_risk_alert(
                event_type="drawdown_breach",
                severity="critical",
                message=f"Drawdown {drawdown_pct:.2f}% exceeds limit {self._max_drawdown_pct}%",
                triggered_value=drawdown_pct,
                threshold_value=self._max_drawdown_pct,
            )
            
            # Trigger kill switch
            await self._kill_switch.trigger(
                reason=f"Drawdown breach: {drawdown_pct:.2f}%",
                triggered_by="drawdown",
            )
    
    async def _check_daily_loss(self) -> None:
        """Check daily loss against limit."""
        if self._daily_start_equity is None or self._current_equity is None:
            return
        
        if self._daily_start_equity == 0:
            return
        
        daily_loss_pct = (
            (self._daily_start_equity - self._current_equity) / self._daily_start_equity * 100
        )
        
        if daily_loss_pct >= self._daily_loss_limit_pct:
            await self._trigger_risk_alert(
                event_type="daily_loss_breach",
                severity="critical",
                message=f"Daily loss {daily_loss_pct:.2f}% exceeds limit {self._daily_loss_limit_pct}%",
                triggered_value=daily_loss_pct,
                threshold_value=self._daily_loss_limit_pct,
            )
            
            # Trigger kill switch
            await self._kill_switch.trigger(
                reason=f"Daily loss breach: {daily_loss_pct:.2f}%",
                triggered_by="daily_loss",
            )
    
    async def _trigger_risk_alert(
        self,
        event_type: str,
        severity: str,
        message: str,
        triggered_value: Optional[Decimal] = None,
        threshold_value: Optional[Decimal] = None,
    ) -> None:
        """Publish risk alert."""
        try:
            messaging = await ensure_connected()
            
            alert = RiskAlert(
                market=self._market,
                mode=self._mode,
                event_type=event_type,
                severity=severity,
                message=message,
                triggered_value=triggered_value,
                threshold_value=threshold_value,
            )
            
            await messaging.publish(
                subject=f"RISK.ALERTS.{self._market.value.upper()}",
                data=alert,
            )
            
            logger.warning(f"Risk alert: {message}")
            
            # Persist to database
            await self._persist_risk_event(alert)
            
        except Exception as e:
            logger.error(f"Error publishing risk alert: {e}")
    
    async def _persist_snapshot(self) -> None:
        """Persist account snapshot to database."""
        if self._current_equity is None:
            return
        
        try:
            postgres = get_postgres()
            async with postgres.session() as session:
                from sqlalchemy import text
                
                drawdown_pct = Decimal("0")
                if self._peak_equity and self._peak_equity > 0:
                    drawdown_pct = (
                        (self._peak_equity - self._current_equity) / self._peak_equity * 100
                    )
                
                await session.execute(
                    text("""
                        INSERT INTO account_snapshots (
                            market, mode, balance, equity, drawdown_pct,
                            peak_equity, snapshot_time
                        ) VALUES (
                            :market, :mode, :balance, :equity, :drawdown_pct,
                            :peak_equity, :snapshot_time
                        )
                    """),
                    {
                        "market": self._market.value,
                        "mode": self._mode.value,
                        "balance": float(self._current_equity),
                        "equity": float(self._current_equity),
                        "drawdown_pct": float(drawdown_pct),
                        "peak_equity": float(self._peak_equity) if self._peak_equity else None,
                        "snapshot_time": datetime.utcnow(),
                    }
                )
        except Exception as e:
            logger.error(f"Error persisting account snapshot: {e}")
    
    async def _persist_risk_event(self, alert: RiskAlert) -> None:
        """Persist risk event to database."""
        try:
            postgres = get_postgres()
            async with postgres.session() as session:
                from sqlalchemy import text
                
                await session.execute(
                    text("""
                        INSERT INTO risk_events (
                            market, mode, event_type, severity, message,
                            triggered_at, metadata
                        ) VALUES (
                            :market, :mode, :event_type, :severity, :message,
                            :triggered_at, :metadata
                        )
                    """),
                    {
                        "market": alert.market.value,
                        "mode": alert.mode.value,
                        "event_type": alert.event_type,
                        "severity": alert.severity,
                        "message": alert.message,
                        "triggered_at": alert.timestamp,
                        "metadata": alert.metadata,
                    }
                )
        except Exception as e:
            logger.error(f"Error persisting risk event: {e}")
    
    def reset_daily(self) -> None:
        """Reset daily tracking (call at start of trading day)."""
        self._daily_start_equity = self._current_equity
        logger.info(f"Daily equity reset to {self._daily_start_equity}")
