from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .settings import RuntimeSettings


def _path(settings: RuntimeSettings, component: str) -> Path:
    return settings.state_dir / "health" / f"{component}.json"


def touch(settings: RuntimeSettings, component: str, state: str = "running", detail: dict[str, Any] | None = None) -> None:
    path = _path(settings, component)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    payload = {"component": component, "state": state, "ts_ns": time.time_ns(), **(detail or {})}
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def check(settings: RuntimeSettings, component: str, max_age_seconds: int = 90) -> dict[str, Any]:
    if component == "crypto-collectors":
        providers=[name.strip() for name in settings.env.get(
            "CRYPTO_COLLECTOR_PROVIDERS","binance-perp,bitget-perp,bybit-perp"
        ).split(",") if name.strip()]
        details={name:check(settings,f"collectors-{name}",max_age_seconds) for name in providers}
        age=max((float(item.get("age_seconds",1e9)) for item in details.values()),default=1e9)
        return {"component":component,"state":"running" if all(item.get("ok") for item in details.values()) else "degraded",
                "providers":providers,"providers_health":details,
                "age_seconds":age,"ok":bool(providers) and all(item.get("ok") for item in details.values())}
    path = _path(settings, component)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "component": component, "reason": "heartbeat_missing", "path": str(path)}
    age = max(0.0, (time.time_ns() - int(payload.get("ts_ns", 0))) / 1_000_000_000)
    paused = payload.get("phase") in {"paused","retrying"}
    payload.update({"ok": age <= max_age_seconds and payload.get("state") == "running" and not paused, "age_seconds": round(age, 1), "path": str(path)})
    if paused:
        payload["reason"] = str(payload.get("reason") or "collection_paused")
    if component in {"collectors", "crypto-collectors", "ls-collectors"}:
        providers = [str(name) for name in payload.get("providers", [])]
        details = {name: check(settings, f"collectors-{name}", max_age_seconds) for name in providers}
        payload["providers_health"] = details
        payload["ok"] = bool(providers) and payload["ok"] and all(value["ok"] for value in details.values())
    return payload

