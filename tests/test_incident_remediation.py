from __future__ import annotations

import json
from pathlib import Path

from vibetrading.health import check, touch
from vibetrading.providers import universe
from vibetrading.providers.universe import InstrumentUniverseResolver
from vibetrading.settings import RuntimeSettings


def _settings(tmp_path: Path) -> RuntimeSettings:
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n", encoding="utf-8")
    return RuntimeSettings.from_env(env)


def test_configured_symbols_do_not_open_or_write_the_runtime_store(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)

    def unexpected_store(*_args, **_kwargs):
        raise AssertionError("configured universe must not open SQLite")

    monkeypatch.setattr(universe, "RuntimeStore", unexpected_store)

    assert InstrumentUniverseResolver(settings=settings).configured_symbols("toss") == ["US:SPY", "US:QQQ"]


def test_paused_collector_fails_health_check(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    touch(settings, "collectors-toss", detail={"phase": "paused", "reason": "disk_pressure"})

    result = check(settings, "collectors-toss")

    assert result["ok"] is False
    assert result["reason"] == "disk_pressure"


def test_connecting_and_retrying_collectors_are_not_healthy(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for phase in ("connecting", "retrying", "recovering"):
        touch(settings, "collectors-binance-perp", detail={"phase": phase})
        assert check(settings, "collectors-binance-perp")["ok"] is False
    touch(settings, "collectors-binance-perp", detail={"phase": "publishing"})
    assert check(settings, "collectors-binance-perp")["ok"] is True


def test_health_reports_image_and_configuration_provenance(tmp_path: Path, monkeypatch) -> None:
    from vibetrading import health

    monkeypatch.setenv("VIBETRADING_BUILD_REVISION", "r1-abc")
    monkeypatch.setenv("VIBETRADING_BUILD_CONFIG_HASH", "config-hash")
    monkeypatch.setenv("VIBETRADING_IMAGE_ID", "sha256:image-id")
    monkeypatch.setenv("ARCHIVE_ACCESS_KEY", "do-not-expose")
    settings = _settings(tmp_path)
    touch(settings, "storage")
    payload = json.loads((settings.state_dir / "health" / "storage.json").read_text(encoding="utf-8"))

    assert payload["build"]["build_revision"] == "r1-abc"
    assert payload["build"]["build_config_hash"] == "config-hash"
    assert payload["build"]["image_id"] == "sha256:image-id"
    assert "do-not-expose" not in json.dumps(payload)


def test_disk_resume_uses_reserve_plus_margin(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert settings.storage_guard.reserve_gb == 4.0
    assert settings.storage_guard.resume_margin_gb == 0.5


def test_delivery_history_pruning_is_disabled_by_default(tmp_path: Path) -> None:
    assert _settings(tmp_path).archive.delivery_history_prune_enabled is False


def test_sqlite_full_backup_default_is_daily(tmp_path: Path) -> None:
    assert _settings(tmp_path).archive.sqlite_backup_interval_seconds == 24 * 60 * 60
