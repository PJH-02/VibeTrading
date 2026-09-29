from __future__ import annotations

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

    assert InstrumentUniverseResolver(settings=settings).configured_symbols("toss") == ["US:SPY"]


def test_paused_collector_fails_health_check(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    touch(settings, "collectors-toss", detail={"phase": "paused", "reason": "disk_pressure"})

    result = check(settings, "collectors-toss")

    assert result["ok"] is False
    assert result["reason"] == "disk_pressure"


def test_default_disk_resume_threshold_is_65_percent(tmp_path: Path) -> None:
    assert _settings(tmp_path).storage_guard.resume_used_ratio == 0.65


def test_delivery_history_pruning_is_disabled_by_default(tmp_path: Path) -> None:
    assert _settings(tmp_path).archive.delivery_history_prune_enabled is False
