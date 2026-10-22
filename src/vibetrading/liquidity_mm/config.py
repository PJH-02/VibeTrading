from __future__ import annotations

import re
import hashlib
import json
from dataclasses import asdict, dataclass, fields
from decimal import Decimal
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Config:
    symbol: str = "SOLUSDT"
    depth: int = 50
    unit_qty: Decimal = Decimal("0.1")
    max_units: int = 3
    unit_notional_usdt: Decimal = Decimal("30")
    max_notional_usdt: Decimal = Decimal("90")
    warmup_seconds: float = 120
    horizon_seconds: float = 60
    evaluation_seconds: float = 5
    entry_ttl_seconds: float = 20
    max_holding_seconds: float = 120
    cooldown_seconds: float = 30
    soft_grace_seconds: float = 10
    entry_latency_seconds: float = 5
    cancel_latency_seconds: float = 5
    private_latency_seconds: float = 3
    exit_latency_seconds: float = 5
    max_source_age_seconds: float = 5
    max_book_age_seconds: float = 3
    max_private_age_seconds: float = 45
    stop_bps: Decimal = Decimal("35")
    liquidation_slippage_bps: Decimal = Decimal("20")
    latency_buffer_bps: Decimal = Decimal("2")
    model_buffer_bps: Decimal = Decimal("2")
    daily_loss_fraction: Decimal = Decimal("0.01")
    drawdown_fraction: Decimal = Decimal("0.05")
    max_testnet_loss_usdt: Decimal = Decimal("5")
    min_available_usdt: Decimal = Decimal("20")
    max_margin_rate: Decimal = Decimal("0.25")
    funding_guard_seconds: float = 180
    flow_stress_multiplier: Decimal = Decimal("0.75")
    supply_stress_multiplier: Decimal = Decimal("1.5")
    depth_haircut: Decimal = Decimal("0.5")
    reserve_fraction: Decimal = Decimal("0.1")
    evidence_mode: str = "testnet_pilot"
    evidence_file: str | None = None
    state_dir: str = "runtime_data/liquidity_mm"

    @classmethod
    def load(cls, path: Path) -> "Config":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        defaults = cls()
        known = {f.name for f in fields(cls)}
        if set(data) - known:
            raise ValueError(f"Unknown configuration keys: {sorted(set(data) - known)}")
        for key, value in data.items():
            default = getattr(defaults, key)
            if isinstance(default, Decimal):
                data[key] = Decimal(str(value))
            elif isinstance(default, float):
                data[key] = float(value)
        result = cls(**data)
        result.validate()
        return result

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Z0-9]+USDT", self.symbol):
            raise ValueError("v1 supports USDT linear perpetuals only")
        if self.depth != 50 or not 1 <= self.max_units <= 3:
            raise ValueError("v1 requires depth=50 and at most 3 inventory units")
        positive = [f.name for f in fields(self) if isinstance(getattr(self, f.name), (Decimal, float, int))]
        if any(not Decimal(str(getattr(self, k))).is_finite() or getattr(self, k) <= 0 for k in positive):
            raise ValueError("Numeric configuration values must be finite and positive")
        if self.evaluation_seconds != 5 or self.horizon_seconds not in (30, 60, 120):
            raise ValueError("Use 5-second decisions and a predeclared 30/60/120-second horizon")
        if self.entry_latency_seconds < 5 or self.horizon_seconds < max(
            30, 6 * self.entry_latency_seconds, 3 * self.cancel_latency_seconds
        ):
            raise ValueError("Horizon does not cover the conservative latency budget")
        if self.max_holding_seconds > 120 or self.warmup_seconds < 30:
            raise ValueError("Invalid holding or warmup limit")
        for name in ("flow_stress_multiplier", "depth_haircut", "daily_loss_fraction", "drawdown_fraction", "max_margin_rate"):
            if getattr(self, name) > 1:
                raise ValueError(f"{name} must be <= 1")
        if self.evidence_mode not in {"testnet_pilot", "calibrated"}:
            raise ValueError("Unknown evidence mode")
        if self.evidence_mode == "calibrated" and not self.evidence_file:
            raise ValueError("Calibrated mode requires an evidence file")

    def policy_digest(self) -> str:
        policy = asdict(self)
        for key in ("state_dir", "evidence_mode", "evidence_file"):
            policy.pop(key)
        return hashlib.sha256(json.dumps(policy, sort_keys=True, default=str).encode()).hexdigest()


def duration_seconds(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(s|m|h|d)?", value.strip())
    if not match:
        raise ValueError("Duration examples: 60s, 30m, 2h, 1d")
    seconds = float(match[1]) * {None: 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]
    if not 1 <= seconds <= 7 * 86400:
        raise ValueError("Duration must be between 1 second and 7 days")
    return seconds
