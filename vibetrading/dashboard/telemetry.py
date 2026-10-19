"""Strategy lifecycle telemetry, separate from execution and market storage."""
from __future__ import annotations

import asyncio
import time


async def heartbeat(bus, *, job_id: str, name: str, account: str, section: str, mode: str, currency: str) -> None:
    body = {"id": job_id, "name": name, "account": account, "section": section,
            "mode": mode, "currency": currency, "running": True, "status": "RUNNING",
            "realized_pnl": None, "unrealized_pnl": None}
    try:
        while True:
            try:
                await bus.publish_system("dashboard_strategy", {**body, "updated_ms": int(time.time() * 1000)})
            except Exception:
                pass  # Monitoring never blocks or changes strategy execution.
            await asyncio.sleep(10)
    finally:
        try:
            await bus.publish_system("dashboard_strategy", {**body, "running": False, "status": "STOPPED", "updated_ms": int(time.time() * 1000)})
        except Exception:
            pass
