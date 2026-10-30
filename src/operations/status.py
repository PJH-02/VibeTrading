from __future__ import annotations

import shutil
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

from runtime.settings import RuntimeSettings, get_settings
from storage.sqlite_store import RuntimeStore
from storage.storage_guard import StorageGuard
from .health import check as health_check


def disk_status(settings: RuntimeSettings) -> dict[str, Any]:
    root = settings.data_root
    root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(root)
    reserved = int(settings.storage_guard.reserve_gb * 1024 * 1024 * 1024)
    data_budget = max(0, usage.free - reserved)
    guard = StorageGuard(settings).evaluate()
    orphans = list(settings.parquet.data_root.glob("**/*.parquet.orphan"))
    orphan_bytes = sum(path.stat().st_size for path in orphans if path.exists())
    return {
        "root": str(root),
        "total_gb": round(usage.total / 1024 / 1024 / 1024, 2),
        "free_gb": round(usage.free / 1024 / 1024 / 1024, 2),
        "used_ratio": round(guard.used_ratio, 4),
        "reserved_gb": settings.storage_guard.reserve_gb,
        "data_budget_gb": round(data_budget / 1024 / 1024 / 1024, 2),
        "fail_closed": guard.fail_closed,
        "upload_priority": guard.upload_priority,
        "direct_upload": guard.direct_upload,
        "warning": guard.warning,
        "quarantined_files": len(orphans),
        "quarantined_gb": round(orphan_bytes / 1024**3, 2),
    }


def runtime_status(settings: RuntimeSettings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    store = RuntimeStore(settings)
    try:
        disk = disk_status(settings)
        summary = store.status_summary()
        hourly = int(summary.get("recent_local_write_bytes_1h") or 0)
        free_budget = max(0.0, float(disk["data_budget_gb"]) * 1024**3)
        exhaustion_hours = round(free_budget / hourly, 2) if hourly > 0 else None
        return {
            "config": settings.config_report(),
            "disk": {**disk, "estimated_exhaustion_hours_at_recent_write_rate": exhaustion_hours},
            "store": summary,
            "nats": nats_status(settings),
            "workers": {name: health_check(settings, name, 120) for name in ("collectors", "storage", "maintenance")},
            "collector_providers": {
                path.stem.removeprefix("collectors-"): health_check(settings, path.stem, 120)
                for path in sorted((settings.state_dir / "health").glob("collectors-*.json"))
            },
            "paths": {
                "project_root": str(settings.project_root),
                "parquet_root": str(settings.parquet.data_root),
                "state_dir": str(settings.state_dir),
            },
        }
    finally:
        store.close()


def nats_status(settings: RuntimeSettings) -> dict[str, Any]:
    if settings.env.get('VIBETRADING_RUNTIME')=='local':
        return {'required':False,'enabled':False,'runtime':'local-spool-uds-parquet'}
    host = settings.nats.url.split("//", 1)[-1].split(":", 1)[0] or "nats"
    url = f"http://{host}:8222/jsz?consumers=true"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            payload = json.load(response)
        for account in payload.get("account_details", []):
            for stream in account.get("stream_detail", []):
                if stream.get("name") != settings.nats.stream_name:
                    continue
                state = stream.get("state") or {}
                consumer = next((item for item in stream.get("consumer_detail", []) if item.get("name") == "parquet-storage"), {})
                return {
                    "stream": settings.nats.stream_name,
                    "messages": int(state.get("messages") or 0),
                    "bytes": int(state.get("bytes") or 0),
                    "max_bytes": int(settings.nats.max_bytes),
                    "used_ratio": round(int(state.get("bytes") or 0) / max(1, int(settings.nats.max_bytes)), 4),
                    "first_ts": state.get("first_ts"), "last_ts": state.get("last_ts"),
                    "consumer_pending": int(consumer.get("num_pending") or 0),
                    "ack_pending": int(consumer.get("num_ack_pending") or 0),
                    "redelivered": int(consumer.get("num_redelivered") or 0),
                }
        return {"error": "market_stream_not_found"}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
