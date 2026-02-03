"""
Centralized Configuration for Trading System
Uses Pydantic Settings with .env loading.
"""

import os
from decimal import Decimal
from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.models import Market, TradingMode


class DatabaseSettings(BaseSettings):
    """PostgreSQL database settings."""
    model_config = SettingsConfigDict(env_prefix="POSTGRES_", extra="ignore")
    
    host: str = "localhost"
    port: int = 5432
    user: str = "trading"
    password: str = "trading_dev"
    db: str = "trading_db"
    
    @property
    def url(self) -> str:
        """Build database URL."""
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"
    
    @property
    def sync_url(self) -> str:
        """Build synchronous database URL."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"


class QuestDBSettings(BaseSettings):
    """QuestDB time-series database settings."""
    model_config = SettingsConfigDict(env_prefix="QUESTDB_", extra="ignore")
    
    host: str = "localhost"
    http_port: int = 9000
    ilp_port: int = 9009
    pg_port: int = 8812
    
    @property
    def http_url(self) -> str:
        """HTTP API URL."""
        return f"http://{self.host}:{self.http_port}"
    
    @property
    def ilp_address(self) -> tuple[str, int]:
        """ILP (InfluxDB Line Protocol) address."""
        return (self.host, self.ilp_port)


class NatsSettings(BaseSettings):
    """NATS JetStream settings."""
    model_config = SettingsConfigDict(env_prefix="NATS_", extra="ignore")
    
    url: str = "nats://localhost:4222"
    connect_timeout: int = 10
    reconnect_time_wait: int = 2
    max_reconnect_attempts: int = 60


class RedisSettings(BaseSettings):
    """Redis settings."""
    model_config = SettingsConfigDict(env_prefix="REDIS_", extra="ignore")
    
    url: str = "redis://localhost:6379/0"


class BinanceSettings(BaseSettings):
    """Binance API settings."""
    model_config = SettingsConfigDict(env_prefix="BINANCE_", extra="ignore")
    
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True
    
    @property
    def base_url(self) -> str:
        """Get appropriate base URL."""
        if self.testnet:
            return "https://testnet.binance.vision"
        return "https://api.binance.com"
    
    @property
    def ws_url(self) -> str:
        """Get WebSocket URL."""
        if self.testnet:
            return "wss://testnet.binance.vision/ws"
        return "wss://stream.binance.com:9443/ws"


class KISSettings(BaseSettings):
    """Korea Investment & Securities (KIS) API settings."""
    model_config = SettingsConfigDict(env_prefix="KIS_", extra="ignore")
    
    app_key: str = ""
    app_secret: str = ""
    account_number: str = ""
    account_product_code: str = ""
    use_mock: bool = True  # True for virtual account, False for real account
    
    @property
    def base_url(self) -> str:
        """Get appropriate base URL."""
        if self.use_mock:
            return "https://openapivts.koreainvestment.com:29443"
        return "https://openapi.koreainvestment.com:9443"


class KiwoomSettings(BaseSettings):
    """Kiwoom Securities API settings."""
    model_config = SettingsConfigDict(env_prefix="KIWOOM_", extra="ignore")
    
    account_number: str = ""
    account_password: str = ""
    cert_password: str = ""
    use_mock: bool = True  # True for simulated trading, False for real account


class RiskSettings(BaseSettings):
    """Risk control settings."""
    model_config = SettingsConfigDict(extra="ignore")
    
    max_drawdown_pct: Decimal = Field(default=Decimal("10.0"), alias="MAX_DRAWDOWN_PCT")
    max_position_size_pct: Decimal = Field(default=Decimal("5.0"), alias="MAX_POSITION_SIZE_PCT")
    daily_loss_limit_pct: Decimal = Field(default=Decimal("3.0"), alias="DAILY_LOSS_LIMIT_PCT")


class FillLogicSettings(BaseSettings):
    """Fill simulation settings."""
    model_config = SettingsConfigDict(extra="ignore")
    
    slippage_bps_crypto: int = Field(default=10, alias="SLIPPAGE_BPS_CRYPTO")
    slippage_bps_kr: int = Field(default=5, alias="SLIPPAGE_BPS_KR")
    slippage_bps_us: int = Field(default=3, alias="SLIPPAGE_BPS_US")
    min_latency_ms: int = Field(default=50, alias="MIN_LATENCY_MS")
    
    def get_slippage_bps(self, market: Market) -> int:
        """Get slippage in basis points for a market."""
        mapping = {
            Market.CRYPTO: self.slippage_bps_crypto,
            Market.KR: self.slippage_bps_kr,
            Market.US: self.slippage_bps_us,
        }
        return mapping[market]


class LoggingSettings(BaseSettings):
    """Logging settings."""
    model_config = SettingsConfigDict(extra="ignore")
    
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="json", alias="LOG_FORMAT")


class TradingSettings(BaseSettings):
    """Main trading system settings."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # Core settings
    mode: TradingMode = Field(default=TradingMode.PAPER, alias="TRADING_MODE")
    market: Market = Field(default=Market.CRYPTO, alias="TRADING_MARKET")
    
    # Sub-settings (loaded from same .env)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    questdb: QuestDBSettings = Field(default_factory=QuestDBSettings)
    nats: NatsSettings = Field(default_factory=NatsSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    binance: BinanceSettings = Field(default_factory=BinanceSettings)
    kis: KISSettings = Field(default_factory=KISSettings)
    kiwoom: KiwoomSettings = Field(default_factory=KiwoomSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    fill_logic: FillLogicSettings = Field(default_factory=FillLogicSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    
    @field_validator("mode", mode="before")
    @classmethod
    def validate_mode(cls, v: str) -> TradingMode:
        """Validate and convert trading mode."""
        if isinstance(v, TradingMode):
            return v
        return TradingMode(v.lower())
    
    @field_validator("market", mode="before")
    @classmethod
    def validate_market(cls, v: str) -> Market:
        """Validate and convert market."""
        if isinstance(v, Market):
            return v
        return Market(v.lower())


@lru_cache()
def get_settings() -> TradingSettings:
    """Get cached settings instance."""
    return TradingSettings()


def reload_settings() -> TradingSettings:
    """Force reload settings (clears cache)."""
    get_settings.cache_clear()
    return get_settings()
