from __future__ import annotations

import shutil
from dataclasses import dataclass

from .settings import RuntimeSettings, get_settings


@dataclass(frozen=True)
class StorageGuardDecision:
    used_ratio: float
    free_bytes: int
    reserve_bytes: int
    resume_margin_bytes: int
    warning: bool
    upload_priority: bool
    direct_upload: bool
    fail_closed: bool
    probe_error: str | None = None
    state_free_bytes: int | None = None
    state_reserve_bytes: int = 0
    state_resume_margin_bytes: int = 0
    nats_free_bytes: int | None = None
    nats_reserve_bytes: int = 0
    nats_resume_margin_bytes: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "used_ratio": round(self.used_ratio, 4),
            "free_bytes": self.free_bytes,
            "reserve_bytes": self.reserve_bytes,
            "resume_margin_bytes": self.resume_margin_bytes,
            "warning": self.warning,
            "upload_priority": self.upload_priority,
            "direct_upload": self.direct_upload,
            "fail_closed": self.fail_closed,
            "probe_error": self.probe_error,
            "state_free_bytes": self.state_free_bytes,
            "state_reserve_bytes": self.state_reserve_bytes,
            "state_resume_margin_bytes": self.state_resume_margin_bytes,
            "nats_free_bytes": self.nats_free_bytes,
            "nats_reserve_bytes": self.nats_reserve_bytes,
            "nats_resume_margin_bytes": self.nats_resume_margin_bytes,
        }


class StorageGuard:
    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or get_settings()

    def evaluate(self) -> StorageGuardDecision:
        gib = 1024**3
        crypto_cap = int(self.settings.env.get("CRYPTO_NATS_MAX_BYTES", str(self.settings.nats.max_bytes)))
        configured_nats_budget = (int(self.settings.nats.max_bytes)
                                  + int(self.settings.nats.operations_max_bytes)
                                  + max(0, crypto_cap))
        declared_nats_budget = int(self.settings.env.get("NATS_FILE_STORE_MAX_BYTES", "0") or 0)
        local=self.settings.env.get('VIBETRADING_RUNTIME')=='local'
        if local:
            configured_nats_budget=int(self.settings.env['LOCAL_SPOOL_DISK_BUDGET_BYTES'])
            declared_nats_budget=0
        wal_budget = int(self.settings.env.get("STORAGE_WAL_BUDGET_BYTES", str(2 * 256 * 1024**2)))
        flush_budget = int(self.settings.env.get("STORAGE_FLUSH_TEMP_BUDGET_BYTES", str(512 * 1024**2)))
        recovery_budget = int(self.settings.env.get("STORAGE_RECOVERY_MARGIN_BYTES", str(256 * 1024**2)))
        reserve_bytes = max(
            int(self.settings.storage_guard.reserve_gb * gib),
            max(configured_nats_budget, declared_nats_budget) + wal_budget + flush_budget + recovery_budget,
        )
        resume_margin_bytes = int(self.settings.storage_guard.resume_margin_gb * gib)
        try:
            # Pressure checks run in collector hot paths. Do not create the
            # data directory here: a probe must not itself mutate the volume.
            usage = shutil.disk_usage(self.settings.env.get('STORAGE_DATA_DISK_ROOT') or self.settings.data_root)
        except OSError as exc:
            return StorageGuardDecision(
                used_ratio=1.0, free_bytes=0, reserve_bytes=reserve_bytes,
                resume_margin_bytes=resume_margin_bytes, warning=True,
                upload_priority=True, direct_upload=True, fail_closed=True,
                probe_error=type(exc).__name__,
            )
        used_ratio = 1 - usage.free / usage.total
        resume_margin_bytes = max(resume_margin_bytes, int(usage.total * 0.02))
        state_free=None
        state_reserve=state_margin=0
        state_error=None
        nats_free=None
        nats_reserve=nats_margin=0
        nats_error=None
        nats_root=None if local else self.settings.env.get('STORAGE_NATS_DISK_ROOT')
        if nats_root:
            nats_reserve=max(configured_nats_budget,declared_nats_budget)+wal_budget+recovery_budget
            nats_margin=int(self.settings.env.get('STORAGE_NATS_RESUME_MARGIN_BYTES',str(512*1024**2)))
            try:
                nats_free=shutil.disk_usage(nats_root).free
            except OSError as exc:
                nats_free=0
                nats_error='nats_'+type(exc).__name__
        state_root=self.settings.env.get('STORAGE_STATE_DISK_ROOT')
        if state_root:
            state_reserve=int(self.settings.env.get('STORAGE_STATE_RESERVE_BYTES',str(gib)))
            state_margin=int(self.settings.env.get('STORAGE_STATE_RESUME_MARGIN_BYTES',str(256*1024**2)))
            try:
                state_free=shutil.disk_usage(state_root).free
            except OSError as exc:
                state_free=0
                state_error='state_'+type(exc).__name__
        return StorageGuardDecision(
            used_ratio=used_ratio,
            free_bytes=usage.free,
            reserve_bytes=reserve_bytes,
            resume_margin_bytes=resume_margin_bytes,
            warning=used_ratio >= self.settings.storage_guard.warning_used_ratio,
            upload_priority=used_ratio >= self.settings.storage_guard.warning_used_ratio,
            direct_upload=used_ratio >= self.settings.storage_guard.direct_upload_used_ratio,
            # Collection safety follows a byte budget with hysteresis. A
            # percentage threshold can pause a small volume while it still
            # has enough room, or permit a large volume below its real need.
            fail_closed=(usage.free <= reserve_bytes or
                         (state_free is not None and state_free<=state_reserve) or
                         (nats_free is not None and nats_free<=nats_reserve)),
            probe_error=state_error or nats_error,state_free_bytes=state_free,
            state_reserve_bytes=state_reserve,state_resume_margin_bytes=state_margin,
            nats_free_bytes=nats_free,nats_reserve_bytes=nats_reserve,nats_resume_margin_bytes=nats_margin,
        )

    def should_pause_collection(self) -> bool:
        decision = self.evaluate()
        return decision.fail_closed

    def can_resume_collection(self) -> bool:
        decision = self.evaluate()
        return (decision.probe_error is None
                and decision.free_bytes >= decision.reserve_bytes + decision.resume_margin_bytes
                and (decision.state_free_bytes is None or decision.state_free_bytes >=
                     decision.state_reserve_bytes+decision.state_resume_margin_bytes)
                and (decision.nats_free_bytes is None or decision.nats_free_bytes >=
                     decision.nats_reserve_bytes+decision.nats_resume_margin_bytes))

    def assert_can_collect(self) -> None:
        decision = self.evaluate()
        if decision.fail_closed:
            raise RuntimeError(f"storage fail-closed: {decision.as_dict()}")


