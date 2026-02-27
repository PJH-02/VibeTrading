"""
Health Monitoring Service
Service heartbeat publishing and health status tracking.

Extension point for: Telegram, Web dashboards
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional

from shared.messaging import Subjects, ensure_connected
from shared.models import HealthStatus, Market

logger = logging.getLogger(__name__)


class HealthMonitor:
    """
    Service health monitoring.
    
    Publishes heartbeats to SYSTEM.HEALTH.*
    for consumption by external dashboards.
    """
    
    def __init__(self, service_name: str) -> None:
        """
        Initialize health monitor.
        
        Args:
            service_name: Name of this service
        """
        self._service_name = service_name
        self._running = False
        self._start_time: Optional[datetime] = None
        self._last_activity: Optional[datetime] = None
        self._status = "healthy"
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._heartbeat_interval = 10  # seconds
    
    @property
    def service_name(self) -> str:
        """Get service name."""
        return self._service_name
    
    @property
    def status(self) -> str:
        """Get current status."""
        return self._status
    
    @property
    def uptime_seconds(self) -> int:
        """Get uptime in seconds."""
        if self._start_time is None:
            return 0
        return int((datetime.utcnow() - self._start_time).total_seconds())
    
    async def start(self) -> None:
        """Start health monitor."""
        self._start_time = datetime.utcnow()
        self._running = True
        self._status = "healthy"
        
        # Start heartbeat task
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        
        logger.info(f"Health monitor started for {self._service_name}")
    
    async def stop(self) -> None:
        """Stop health monitor."""
        self._running = False
        
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        logger.info(f"Health monitor stopped for {self._service_name}")
    
    async def _heartbeat_loop(self) -> None:
        """Background heartbeat publishing."""
        while self._running:
            try:
                await self._publish_heartbeat()
                await asyncio.sleep(self._heartbeat_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                await asyncio.sleep(self._heartbeat_interval)
    
    async def _publish_heartbeat(self) -> None:
        """Publish health status heartbeat."""
        try:
            messaging = await ensure_connected()
            
            status = HealthStatus(
                service_name=self._service_name,
                status=self._status,
                uptime_seconds=self.uptime_seconds,
                last_activity=self._last_activity,
            )
            
            await messaging.publish(
                subject=f"SYSTEM.HEALTH.{self._service_name.upper()}",
                data=status,
            )
            
        except Exception as e:
            logger.error(f"Error publishing heartbeat: {e}")
    
    def record_activity(self) -> None:
        """Record activity timestamp."""
        self._last_activity = datetime.utcnow()
    
    def set_status(self, status: str) -> None:
        """
        Set service status.
        
        Args:
            status: healthy, degraded, or unhealthy
        """
        if status != self._status:
            logger.info(f"Service status changed: {self._status} -> {status}")
        self._status = status
    
    def set_degraded(self, reason: str) -> None:
        """Set status to degraded with reason."""
        self._status = "degraded"
        logger.warning(f"Service degraded: {reason}")
    
    def set_unhealthy(self, reason: str) -> None:
        """Set status to unhealthy with reason."""
        self._status = "unhealthy"
        logger.error(f"Service unhealthy: {reason}")


# Global health monitors per service
_health_monitors: Dict[str, HealthMonitor] = {}


def get_health_monitor(service_name: str) -> HealthMonitor:
    """Get or create health monitor for service."""
    if service_name not in _health_monitors:
        _health_monitors[service_name] = HealthMonitor(service_name)
    return _health_monitors[service_name]
