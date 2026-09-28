from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .settings import RuntimeSettings, get_settings
from .sqlite_store import RuntimeStore
from .storage_guard import StorageGuard


def disk_status(settings: RuntimeSettings) -> dict[str, Any]:
    root = settings.data_root
    root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(root)
    reserved = int(settings.storage_guard.reserve_gb * 1024 * 1024 * 1024)
    data_budget = max(0, usage.free - reserved)
    guard = StorageGuard(settings).evaluate()
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
    }


def runtime_status(settings: RuntimeSettings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    store = RuntimeStore(settings)
    try:
        return {
            "config": settings.config_report(),
            "disk": disk_status(settings),
            "store": store.status_summary(),
            "paths": {
                "project_root": str(settings.project_root),
                "parquet_root": str(settings.parquet.data_root),
                "state_dir": str(settings.state_dir),
            },
        }
    finally:
        store.close()
