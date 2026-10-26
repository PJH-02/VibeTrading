from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from feeds.providers.universe import ListedContract, select_option_atm_band
from core.models import AssetClass, Venue
from vibetrading.settings import RuntimeSettings
from vibetrading.storage_guard import StorageGuard


def test_option_default_is_atm_plus_minus_one() -> None:
    contracts = [
        ListedContract(strike=Decimal(str(strike)), raw_symbol=str(strike), std_symbol=str(strike),
                       asset_class=AssetClass.OPTION, venue=Venue.KIS, expiry="2026-09", contract_month="202609")
        for strike in (90, 95, 100, 105, 110)
    ]

    # This tests strike selection, with contracts valid on a fixed date;
    # advancing the host calendar must not turn it into an expiry test.
    selected = select_option_atm_band(contracts, Decimal("100"), today="20260915")

    assert [item.strike for item in selected] == [Decimal("95"), Decimal("100"), Decimal("105")]


def test_storage_guard_uses_byte_reserve_instead_of_used_percentage(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env)
    usage = type("Usage", (), {"total": 20 * 1024**3, "used": 15 * 1024**3, "free": 5 * 1024**3})()
    monkeypatch.setattr("vibetrading.storage_guard.shutil.disk_usage", lambda _: usage)

    decision = StorageGuard(settings).evaluate()

    assert decision.warning is True
    assert decision.direct_upload is True
    assert decision.fail_closed is False
    assert StorageGuard(settings).should_pause_collection() is False


def test_storage_guard_fails_closed_when_volume_probe_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env)

    def unavailable(_):
        raise OSError("volume unavailable")

    monkeypatch.setattr("vibetrading.storage_guard.shutil.disk_usage", unavailable)
    decision = StorageGuard(settings).evaluate()

    assert decision.fail_closed is True
    assert decision.probe_error == "OSError"


def test_storage_reserve_includes_declared_nats_volume_and_resume_margin(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n"
        "NATS_FILE_STORE_MAX_BYTES=6442450944\n"
        "STORAGE_RESUME_MARGIN_GB=0.5\n",
        encoding="utf-8",
    )
    settings = RuntimeSettings.from_env(env)
    usage = type("Usage", (), {"total": 20 * 1024**3, "used": 10 * 1024**3,
                               "free": 10 * 1024**3})()
    monkeypatch.setattr("vibetrading.storage_guard.shutil.disk_usage", lambda _: usage)

    decision = StorageGuard(settings).evaluate()

    assert decision.reserve_bytes == 6 * 1024**3 + 2 * 256 * 1024**2 + 512 * 1024**2 + 256 * 1024**2
    assert decision.resume_margin_bytes == int(0.5 * 1024**3)  # max(0.5 GiB, 2% of 20 GiB)
