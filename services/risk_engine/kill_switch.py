"""
Kill Switch
Emergency halt mechanism for all trading activity.
"""

import logging
from datetime import datetime

from shared.messaging import Subjects, ensure_connected
from shared.models import KillSwitchEvent, Market, TradingMode

logger = logging.getLogger(__name__)


class KillSwitch:
    """
    Kill switch for emergency trading halt.
    
    When triggered:
    - Broadcasts RISK.KILL_SWITCH to all services
    - All order managers stop accepting new orders
    - All pending orders are cancelled
    """
    
    def __init__(self, market: Market, mode: TradingMode = TradingMode.PAPER) -> None:
        """
        Initialize kill switch.
        
        Args:
            market: Market scope
            mode: Trading mode
        """
        self._market = market
        self._mode = mode
        self._triggered = False
        self._triggered_at: datetime | None = None
        self._triggered_reason: str | None = None
    
    @property
    def is_triggered(self) -> bool:
        """Check if kill switch is active."""
        return self._triggered
    
    @property
    def triggered_at(self) -> datetime | None:
        """Get trigger time."""
        return self._triggered_at
    
    @property
    def triggered_reason(self) -> str | None:
        """Get trigger reason."""
        return self._triggered_reason
    
    async def trigger(
        self,
        reason: str,
        triggered_by: str = "manual",
    ) -> None:
        """
        Trigger the kill switch.
        
        Args:
            reason: Human-readable reason for trigger
            triggered_by: What triggered it (manual, drawdown, daily_loss, etc.)
        """
        if self._triggered:
            logger.warning("Kill switch already triggered")
            return
        
        self._triggered = True
        self._triggered_at = datetime.utcnow()
        self._triggered_reason = reason
        
        logger.critical(f"KILL SWITCH TRIGGERED: {reason} (by {triggered_by})")
        
        # Broadcast to all services
        try:
            messaging = await ensure_connected()
            
            event = KillSwitchEvent(
                market=self._market,
                mode=self._mode,
                reason=reason,
                triggered_by=triggered_by,
            )
            
            await messaging.publish(
                subject=Subjects.KILL_SWITCH,
                data=event,
            )
            
            logger.info("Kill switch broadcast sent to all services")
            
        except Exception as e:
            logger.error(f"Error broadcasting kill switch: {e}")
    
    def reset(self) -> None:
        """
        Reset the kill switch.
        
        WARNING: Only call this after manual review and confirmation.
        """
        if not self._triggered:
            return
        
        logger.warning("Kill switch reset - trading will resume")
        self._triggered = False
        self._triggered_at = None
        self._triggered_reason = None


# Global kill switch instances per market
_kill_switches: dict[Market, KillSwitch] = {}


def get_kill_switch(market: Market) -> KillSwitch:
    """Get or create kill switch for market."""
    if market not in _kill_switches:
        _kill_switches[market] = KillSwitch(market)
    return _kill_switches[market]


async def trigger_global_kill_switch(reason: str) -> None:
    """Trigger kill switch for all markets."""
    for market in Market:
        kill_switch = get_kill_switch(market)
        await kill_switch.trigger(reason, "global")
