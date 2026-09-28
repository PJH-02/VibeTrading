from __future__ import annotations

import shutil
from dataclasses import dataclass

from .settings import RuntimeSettings, get_settings


@dataclass(frozen=True)
class StorageGuardDecision:
    used_ratio: float
    free_bytes: int
    warning: bool
    upload_priority: bool
    direct_upload: bool
    fail_closed: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "used_ratio": round(self.used_ratio, 4),
            "free_bytes": self.free_bytes,
            "warning": self.warning,
            "upload_priority": self.upload_priority,
            "direct_upload": self.direct_upload,
            "fail_closed": self.fail_closed,
        }


class StorageGuard:
    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or get_settings()

    def evaluate(self) -> StorageGuardDecision:
        self.settings.data_root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(self.settings.data_root)
        used_ratio = 1 - usage.free / usage.total
        return StorageGuardDecision(
            used_ratio=used_ratio,
            free_bytes=usage.free,
            warning=used_ratio >= self.settings.storage_guard.warning_used_ratio,
            upload_priority=used_ratio >= self.settings.storage_guard.warning_used_ratio,
            direct_upload=used_ratio >= self.settings.storage_guard.direct_upload_used_ratio,
            fail_closed=(
                used_ratio >= self.settings.storage_guard.fail_closed_used_ratio
                or usage.free <= int(self.settings.storage_guard.reserve_gb * 1024**3)
            ),
        )

    def should_pause_collection(self) -> bool:
        decision = self.evaluate()
        return decision.fail_closed

    def can_resume_collection(self) -> bool:
        decision = self.evaluate()
        cfg = self.settings.storage_guard
        return decision.used_ratio < cfg.resume_used_ratio and decision.free_bytes >= int(cfg.resume_free_gb * 1024**3)

    def assert_can_collect(self) -> None:
        decision = self.evaluate()
        if decision.fail_closed:
            raise RuntimeError(f"storage fail-closed: {decision.as_dict()}")

