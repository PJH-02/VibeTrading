from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = PROJECT_ROOT / "runtime_data"
SECRET_WORDS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PRIVATE", "ACCOUNT", "PW")


def _load_env(env_file: Path | None = None) -> dict[str, str]:
    values: dict[str, str] = {}
    dotenv_path = env_file or PROJECT_ROOT / ".env"
    if dotenv_path.exists():
        for key, value in dotenv_values(dotenv_path).items():
            if key and value is not None:
                values[str(key)] = str(value)
    for key, value in os.environ.items():
        values[key] = value
    for source_key, target_key in (("ARCHIVE_ACCESS_KEY_FILE", "ARCHIVE_ACCESS_KEY"), ("ARCHIVE_SECRET_KEY_FILE", "ARCHIVE_SECRET_KEY")):
        secret_file = values.get(source_key)
        if not secret_file:
            continue
        try:
            secret_value = Path(secret_file).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if secret_value:
            values[target_key] = secret_value
    return values


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _float(value: str | None, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _path(value: str | os.PathLike[str] | None, base: Path) -> Path:
    path = Path(value) if value is not None else base
    path = path.expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _secret(env: dict[str, str], primary: str, aliases: Iterable[str] = ()) -> tuple[str, str | None]:
    if env.get(primary):
        return primary, env[primary]
    for alias in aliases:
        if env.get(alias):
            return alias, env[alias]
    return primary, None


def is_sensitive_key(key: str) -> bool:
    upper = key.upper()
    return any(word in upper for word in SECRET_WORDS)


def mask_value(key: str, value: str | None) -> str:
    if value is None or value == "":
        return "<missing>"
    if not is_sensitive_key(key):
        return value
    if len(value) <= 4:
        return "***"
    return f"{value[:2]}***{value[-2:]}"


@dataclass(frozen=True)
class NatsSettings:
    url: str = "nats://localhost:4222"
    stream_name: str = "MARKET_DATA"
    operations_stream_name: str = "OPERATIONS"
    # Market data waits for Parquet confirmation before acknowledgement.  It
    # therefore needs a bounded but meaningful disk-backed replay window.
    max_bytes: int = 1024 * 1024 * 1024
    operations_max_bytes: int = 64 * 1024 * 1024
    storage: str = "file"
    ack_wait_seconds: int = 20 * 60
    max_ack_pending: int = 65536


@dataclass(frozen=True)
class ParquetSettings:
    data_root: Path = DEFAULT_DATA_ROOT / "market_data"
    flush_seconds: float = 15 * 60.0
    flush_bytes: int = 64 * 1024 * 1024
    compression: str = "zstd"
    compression_level: int = 3
    target_compact_bytes: int = 64 * 1024 * 1024
    min_file_age_minutes: int = 5
    max_buffer_bytes: int = 128 * 1024 * 1024
    compactor_max_input_bytes: int = 128 * 1024 * 1024
    compactor_interval_seconds: int = 5 * 60
    rss_flush_bytes: int = 650 * 1024 * 1024
    rss_pause_bytes: int = 800 * 1024 * 1024


@dataclass(frozen=True)
class ArchiveSettings:
    backend: str = ""
    endpoint: str = ""
    region: str = "kr"
    bucket: str = ""
    prefix: str = "vibetrading"
    access_key: str | None = None
    secret_key: str | None = None
    upload_after_seconds: int = 3600
    local_retention_seconds: int = 24 * 60 * 60
    cache_max_bytes: int = 512 * 1024 * 1024
    sqlite_backup_interval_seconds: int = 3600

    @property
    def enabled(self) -> bool:
        backend = self.backend.strip().lower()
        if backend == "local":
            return True
        return backend == "ncloud_storage" and bool(self.endpoint and self.bucket and self.access_key and self.secret_key)


@dataclass(frozen=True)
class StorageGuardSettings:
    reserve_gb: float = 4.0
    warning_used_ratio: float = 0.60
    direct_upload_used_ratio: float = 0.75
    fail_closed_used_ratio: float = 0.80
    cleanup_target_used_ratio: float = 0.40
    resume_used_ratio: float = 0.50
    resume_free_gb: float = 6.0
    resume_stable_seconds: int = 60


@dataclass(frozen=True)
class ProviderSettings:
    name: str
    present: dict[str, bool] = field(default_factory=dict)
    values: dict[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def missing_required(self, required: Iterable[str]) -> list[str]:
        return [name for name in required if not self.present.get(name, False)]


@dataclass(frozen=True)
class TimefolioAccountSettings:
    account_id: str
    username: str | None
    password: str | None

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)


@dataclass(frozen=True)
class RuntimeSettings:
    env: dict[str, str]
    project_root: Path
    data_root: Path
    state_dir: Path
    log_dir: Path
    external_config_dir: Path
    nats: NatsSettings
    parquet: ParquetSettings
    archive: ArchiveSettings
    storage_guard: StorageGuardSettings
    providers: dict[str, ProviderSettings]
    max_strategy_jobs: int
    live_order_enabled: bool
    timefolio_live_submit_enabled: bool
    telegram_user_id: str | None

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "RuntimeSettings":
        env = _load_env(env_file)
        root = PROJECT_ROOT
        data_root = _path(env.get("VIBETRADING_DATA_ROOT"), DEFAULT_DATA_ROOT)
        state_dir = _path(env.get("VIBETRADING_STATE_DIR"), data_root / "state")
        log_dir = _path(env.get("VIBETRADING_LOG_DIR"), data_root / "logs")
        configured_external = env.get("SKHY_ARBITRAGE_CONFIG_DIR")
        if configured_external:
            external_config_dir = _path(configured_external, root / "config" / "hyperliquid")
        else:
            windows_external = Path(r"E:\SKarb\home\azureuser\SKHY_arbitrage\config")
            external_config_dir = windows_external if windows_external.exists() else root / "config" / "hyperliquid"

        providers = _provider_settings(env)
        nats = NatsSettings(
            url=env.get("NATS_URL", "nats://localhost:4222"),
            stream_name=env.get("NATS_MARKET_STREAM_NAME", "MARKET_DATA"),
            operations_stream_name=env.get("NATS_OPERATIONS_STREAM_NAME", "OPERATIONS"),
            max_bytes=_int(env.get("NATS_MAX_BYTES"), 1024 * 1024 * 1024),
            operations_max_bytes=_int(env.get("NATS_OPERATIONS_MAX_BYTES"), 64 * 1024 * 1024),
            ack_wait_seconds=_int(env.get("NATS_ACK_WAIT_SECONDS"), 20 * 60),
            max_ack_pending=_int(env.get("NATS_MAX_ACK_PENDING"), 65536),
        )
        parquet = ParquetSettings(
            data_root=_path(env.get("PARQUET_DATA_ROOT"), data_root / "market_data"),
            flush_seconds=_float(env.get("PARQUET_FLUSH_SECONDS"), 15 * 60.0),
            flush_bytes=_int(env.get("PARQUET_FLUSH_BYTES"), 64 * 1024 * 1024),
            compression=env.get("PARQUET_COMPRESSION", "zstd"),
            compression_level=_int(env.get("PARQUET_COMPRESSION_LEVEL"), 3),
            target_compact_bytes=_int(env.get("PARQUET_TARGET_COMPACT_BYTES"), 64 * 1024 * 1024),
            min_file_age_minutes=_int(env.get("PARQUET_MIN_FILE_AGE_MINUTES"), 5),
            max_buffer_bytes=_int(env.get("PARQUET_MAX_BUFFER_BYTES"), 128 * 1024 * 1024),
            compactor_max_input_bytes=_int(env.get("COMPACTOR_MAX_INPUT_BYTES"), 128 * 1024 * 1024),
            compactor_interval_seconds=_int(env.get("COMPACTOR_INTERVAL_SECONDS"), 5 * 60),
            rss_flush_bytes=_int(env.get("PARQUET_RSS_FLUSH_BYTES"), 650 * 1024 * 1024),
            rss_pause_bytes=_int(env.get("PARQUET_RSS_PAUSE_BYTES"), 800 * 1024 * 1024),
        )
        archive = ArchiveSettings(
            backend=env.get("ARCHIVE_BACKEND", ""),
            endpoint=env.get("ARCHIVE_ENDPOINT", ""),
            region=env.get("ARCHIVE_REGION", "kr"),
            bucket=env.get("ARCHIVE_BUCKET", ""),
            prefix=env.get("ARCHIVE_PREFIX", "vibetrading").strip("/"),
            access_key=env.get("ARCHIVE_ACCESS_KEY") or None,
            secret_key=env.get("ARCHIVE_SECRET_KEY") or None,
            upload_after_seconds=_int(env.get("ARCHIVE_UPLOAD_AFTER_SECONDS"), 3600),
            local_retention_seconds=_int(env.get("ARCHIVE_LOCAL_RETENTION_SECONDS"), 24 * 60 * 60),
            cache_max_bytes=_int(env.get("ARCHIVE_CACHE_MAX_BYTES"), 512 * 1024 * 1024),
            sqlite_backup_interval_seconds=_int(env.get("ARCHIVE_SQLITE_BACKUP_INTERVAL_SECONDS"), 3600),
        )
        storage_guard = StorageGuardSettings(
            reserve_gb=_float(env.get("STORAGE_RESERVE_GB"), 4.0),
            warning_used_ratio=_float(env.get("STORAGE_WARNING_USED_RATIO"), 0.60),
            direct_upload_used_ratio=_float(env.get("STORAGE_DIRECT_UPLOAD_USED_RATIO"), 0.75),
            fail_closed_used_ratio=_float(env.get("STORAGE_FAIL_CLOSED_USED_RATIO"), 0.80),
            cleanup_target_used_ratio=_float(env.get("STORAGE_CLEANUP_TARGET_USED_RATIO"), 0.40),
            resume_used_ratio=_float(env.get("STORAGE_RESUME_USED_RATIO"), 0.50),
            resume_free_gb=_float(env.get("STORAGE_RESUME_FREE_GB"), 6.0),
            resume_stable_seconds=_int(env.get("STORAGE_RESUME_STABLE_SECONDS"), 60),
        )
        return cls(
            env=env,
            project_root=root,
            data_root=data_root,
            state_dir=state_dir,
            log_dir=log_dir,
            external_config_dir=external_config_dir,
            nats=nats,
            parquet=parquet,
            archive=archive,
            storage_guard=storage_guard,
            providers=providers,
            max_strategy_jobs=_int(env.get("MAX_STRATEGY_JOBS"), 5),
            live_order_enabled=_bool(env.get("LIVE_ORDER_ENABLED"), False),
            timefolio_live_submit_enabled=_bool(env.get("TIMEFOLIO_LIVE_SUBMIT_ENABLED"), False),
            telegram_user_id=env.get("TELEGRAM_USER_ID") or env.get("TELEGRAM_CHAT_ID") or None,
        )

    def ensure_dirs(self) -> None:
        for path in (self.data_root, self.state_dir, self.log_dir, self.parquet.data_root):
            path.mkdir(parents=True, exist_ok=True)

    def timefolio_account(self, account_id: str) -> TimefolioAccountSettings:
        prefix = f"TIMEFOLIO_{account_id.upper()}_"
        username = (
            self.env.get(prefix + "USERNAME")
            or self.env.get(prefix + "ID")
            or self.env.get("TIMEFOLIO_ID")
        )
        password = (
            self.env.get(prefix + "PASSWORD")
            or self.env.get(prefix + "PW")
            or self.env.get("TIMEFOLIO_PW")
        )
        return TimefolioAccountSettings(
            account_id=account_id,
            username=username,
            password=password,
        )

    def config_report(self) -> dict[str, object]:
        report: dict[str, object] = {
            "data_root": str(self.data_root),
            "state_dir": str(self.state_dir),
            "external_config_dir": str(self.external_config_dir),
            "nats_url": self.nats.url,
            "archive": {
                # Archive credentials are mounted only into maintenance. Other
                # services report the deployment flag without receiving those
                # secrets, preserving least privilege while keeping /status
                # operationally accurate.
                "enabled": self.archive.enabled or _bool(self.env.get("ARCHIVE_WORKER_ENABLED"), False),
                "credentials_available_in_process": self.archive.enabled,
                "backend": self.archive.backend,
                "endpoint": self.archive.endpoint,
                "region": self.archive.region,
                "bucket": self.archive.bucket,
                "prefix": self.archive.prefix,
            },
            "max_strategy_jobs": self.max_strategy_jobs,
            "live_order_enabled": self.live_order_enabled,
            "timefolio_live_submit_enabled": self.timefolio_live_submit_enabled,
            "telegram_enabled": bool(self.telegram_user_id),
            "timefolio_credentials": {
                "generic": bool(self.env.get("TIMEFOLIO_ID") and self.env.get("TIMEFOLIO_PW")),
            },
            "providers": {},
        }
        provider_report: dict[str, object] = {}
        for name, provider in self.providers.items():
            provider_report[name] = {
                "present": provider.present,
                "warnings": list(provider.warnings),
            }
        report["providers"] = provider_report
        return report


def _provider_settings(env: dict[str, str]) -> dict[str, ProviderSettings]:
    warnings: list[str] = []
    kiwoom_secret_source, kiwoom_secret = _secret(env, "KIWOOM_APP_SECRET", ("KIWOON_APP_SECRET",))
    if kiwoom_secret_source == "KIWOON_APP_SECRET":
        warnings.append("KIWOON_APP_SECRET is deprecated; use KIWOOM_APP_SECRET.")

    def provider(name: str, keys: list[str], values: dict[str, str | None], item_warnings: list[str] | None = None) -> ProviderSettings:
        present = {key: bool(values.get(key)) for key in keys}
        clean_values = {key: value for key, value in values.items() if value is not None}
        return ProviderSettings(name=name, present=present, values=clean_values, warnings=tuple(item_warnings or ()))

    return {
        "kis": provider(
            "kis",
            ["KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NUMBER", "KIS_ACCOUNT_PRODUCT_CODE"],
            {
                "KIS_APP_KEY": env.get("KIS_APP_KEY"),
                "KIS_APP_SECRET": env.get("KIS_APP_SECRET"),
                "KIS_ACCOUNT_NUMBER": env.get("KIS_ACCOUNT_NUMBER"),
                "KIS_ACCOUNT_PRODUCT_CODE": env.get("KIS_ACCOUNT_PRODUCT_CODE"),
                "KIS_USE_MOCK": env.get("KIS_USE_MOCK", "true"),
            },
        ),
        "kiwoom": provider(
            "kiwoom",
            ["KIWOOM_APP_KEY", "KIWOOM_APP_SECRET", "KIWOOM_ACCOUNT_NUMBER"],
            {
                "KIWOOM_APP_KEY": env.get("KIWOOM_APP_KEY"),
                "KIWOOM_APP_SECRET": kiwoom_secret,
                "KIWOOM_ACCOUNT_NUMBER": env.get("KIWOOM_ACCOUNT_NUMBER"),
                "KIWOOM_USE_MOCK": env.get("KIWOOM_USE_MOCK", "true"),
            },
            warnings,
        ),
        "toss": provider(
            "toss",
            ["TOSS_API_KEY", "TOSS_API_SECRET"],
            {
                "TOSS_API_KEY": env.get("TOSS_API_KEY") or env.get("TOSS_CLIENT_ID"),
                "TOSS_API_SECRET": env.get("TOSS_API_SECRET") or env.get("TOSS_CLIENT_SECRET"),
                "TOSS_ACCOUNT_SEQ": env.get("TOSS_ACCOUNT_SEQ"),
            },
        ),
        "binance": provider(
            "binance",
            ["BINANCE_API_KEY", "BINANCE_API_SECRET"],
            {"BINANCE_API_KEY": env.get("BINANCE_API_KEY"), "BINANCE_API_SECRET": env.get("BINANCE_API_SECRET")},
        ),
        "hyperliquid": provider(
            "hyperliquid",
            ["HYPERLIQUID_API_KEY"],
            {"HYPERLIQUID_API_KEY": env.get("HYPERLIQUID_API_KEY")},
        ),
    }


def get_settings() -> RuntimeSettings:
    settings = RuntimeSettings.from_env()
    settings.ensure_dirs()
    return settings
