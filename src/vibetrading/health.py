from __future__ import annotations

import json
import hashlib
import os
import time
from pathlib import Path
from typing import Any

from .settings import RuntimeSettings


def _path(settings: RuntimeSettings, component: str) -> Path:
    return settings.state_dir / "health" / f"{component}.json"


def _build_provenance() -> dict[str, str]:
    runtime_keys = ("VIBETRADING_", "NATS_", "CRYPTO_", "STORAGE_", "ARCHIVE_",
                    "PARQUET_", "COLLECTOR_")
    secret_words = ("KEY", "SECRET", "TOKEN", "PASSWORD", "AUTH", "CREDENTIAL")
    config = {
        key: value for key, value in os.environ.items()
        if key.startswith(runtime_keys) and not any(word in key.upper() for word in secret_words)
    }
    config_root = Path("/app/config")
    if not config_root.is_dir():
        package_root = Path(__file__).resolve().parent
        project_root = (
            package_root.parent.parent
            if package_root.parent.name == "src" and (package_root.parent.parent / "pyproject.toml").is_file()
            else package_root.parent
        )
        config_root = project_root / "config"
    config_files = {}
    if config_root.is_dir():
        for path in sorted(config_root.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml", ".json", ".toml"}:
                config_files[path.relative_to(config_root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    runtime_hash = hashlib.sha256(json.dumps(
        {"environment": config, "config_files": config_files},
        sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    return {
        "build_revision": os.getenv("VIBETRADING_BUILD_REVISION", "unknown"),
        "build_config_hash": os.getenv("VIBETRADING_BUILD_CONFIG_HASH", "unknown"),
        "image_id": os.getenv("VIBETRADING_IMAGE_ID", "unknown"),
        "runtime_config_hash": runtime_hash,
    }


def touch(settings: RuntimeSettings, component: str, state: str = "running", detail: dict[str, Any] | None = None) -> None:
    path = _path(settings, component)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    payload = {"component": component, "state": state, "ts_ns": time.time_ns(),
               "build": _build_provenance(), **(detail or {})}
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def configured_crypto_providers(settings: RuntimeSettings) -> list[str]:
    allowed = {'binance-perp','arcus-perp','grvt-perp'}
    value = settings.env.get('CRYPTO_COLLECTOR_PROVIDERS','binance-perp,arcus-perp,grvt-perp')
    return list(dict.fromkeys(p.strip() for p in value.split(',') if p.strip() in allowed))


def check(settings: RuntimeSettings, component: str, max_age_seconds: int = 90) -> dict[str, Any]:
    if component == "crypto-collectors":
        providers=configured_crypto_providers(settings)
        if not any(_path(settings,f'collectors-{p}').exists() for p in providers):
            return {'component':component,'ok':False,'reason':'heartbeat_missing','providers':providers}
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
    phase = payload.get("phase")
    not_ready = phase in {"starting", "connecting", "recovering", "paused", "retrying", "error"}
    payload.update({"ok": age <= max_age_seconds and payload.get("state") == "running" and not not_ready,
                    "age_seconds": round(age, 1), "path": str(path)})
    if not_ready:
        payload["reason"] = str(payload.get("reason") or phase or "collection_not_ready")
    if component in {"collectors", "crypto-collectors", "ls-collectors"}:
        providers = [str(name) for name in payload.get("providers", [])]
        details = {name: check(settings, f"collectors-{name}", max_age_seconds) for name in providers}
        payload["providers_health"] = details
        payload["ok"] = bool(providers) and payload["ok"] and all(value["ok"] for value in details.values())
    return payload

