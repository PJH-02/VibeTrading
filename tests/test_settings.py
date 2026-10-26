from pathlib import Path

from runtime.settings import RuntimeSettings


def test_kiwoon_secret_alias_warns(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("KIWOON_APP_SECRET=legacy\nKIWOOM_APP_KEY=key\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env_file)
    assert settings.providers["kiwoom"].present["KIWOOM_APP_SECRET"] is True
    assert any("KIWOON_APP_SECRET" in item for item in settings.providers["kiwoom"].warnings)
