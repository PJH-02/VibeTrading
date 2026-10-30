from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from runtime.settings import PROJECT_ROOT
from core.models import AssetClass


@dataclass(frozen=True)
class HyperliquidSecurity:
    symbol: str
    provider_symbol: str
    asset_class: AssetClass
    security_kind: str
    underlying: str | None = None
    currency: str = "USDC"
    exchange: str = "HYPERLIQUID"


@dataclass(frozen=True)
class BinanceSecurity:
    symbol: str
    provider_symbol: str
    asset_class: AssetClass
    security_kind: str
    underlying: str | None = None
    currency: str = "USDT"
    exchange: str = "BINANCE_FUTURES"


# The pair is deliberately validated.  A symbol spelling convention is never
# accepted as evidence that a product is a security.
_HYPERLIQUID_SECURITY_KIND_ASSET_CLASSES: dict[str, frozenset[AssetClass]] = {
    "tokenized_security": frozenset({AssetClass.TOKENIZED_EQUITY}),
    "tokenized_equity": frozenset({AssetClass.TOKENIZED_EQUITY}),
    "stock_derivative": frozenset({AssetClass.FUTURE}),
    "equity_derivative": frozenset({AssetClass.FUTURE}),
    "index_derivative": frozenset({AssetClass.FUTURE, AssetClass.INDEX}),
}


@lru_cache(maxsize=16)
def _universe_snapshot(target: str, mtime_ns: int, size: int, inode: int) -> dict[str, Any]:
    # Metadata forms the cache identity so edits/atomic replacements are visible.
    # Never expose this mutable snapshot to callers.
    if yaml is None:
        raise RuntimeError("PyYAML is required for universe.yaml")
    payload = yaml.safe_load(Path(target).read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {"instruments": []}


def load_universe_config(path: Path | None = None) -> dict[str, Any]:
    target = (path or (PROJECT_ROOT / "config" / "universe.yaml")).resolve()
    try:
        stat = target.stat()
    except FileNotFoundError:
        return {"instruments": []}
    return deepcopy(_universe_snapshot(str(target), stat.st_mtime_ns, stat.st_size, stat.st_ino))


def configured_symbols(provider: str, path: Path | None = None) -> list[str]:
    provider = provider.strip().lower()
    if provider == "hyperliquid":
        return list(configured_hyperliquid_securities(path))
    if provider == "binance":
        return list(configured_binance_securities(path))
    values: list[str] = []
    for item in load_universe_config(path).get("instruments", []):
        if not isinstance(item, dict):
            continue
        providers = {str(value).strip().lower() for value in item.get("providers", [])}
        if provider not in providers:
            continue
        symbol = str(item.get("symbol") or "").strip()
        asset_class = str(item.get("asset_class") or "").strip().lower()
        market = str(item.get("market") or "").strip().lower()
        if provider == "toss":
            # Toss is intentionally the US ETF feed only.  Domestic ETFs are
            # collected once, by Kiwoom.
            if asset_class != AssetClass.ETF.value or not (symbol.upper().startswith("US:") or market == "us"):
                continue
        if provider == "kiwoom" and (symbol.upper().startswith("US:") or market == "us"):
            continue
        if symbol:
            values.append(symbol)
    return list(dict.fromkeys(values))


def configured_hyperliquid_securities(path: Path | None = None) -> dict[str, HyperliquidSecurity]:
    securities: dict[str, HyperliquidSecurity] = {}
    for item in load_universe_config(path).get("instruments", []):
        if not isinstance(item, dict):
            continue
        providers = {str(value).strip().lower() for value in item.get("providers", [])}
        if "hyperliquid" not in providers:
            continue
        symbol = str(item.get("symbol") or "").strip()
        provider_symbol = str(item.get("provider_symbol") or symbol).strip()
        raw_asset_class = str(item.get("asset_class") or "").strip().lower()
        security_kind = str(item.get("security_kind") or "").strip().lower()
        if not symbol or not provider_symbol:
            raise ValueError("Hyperliquid allowlist entry is missing symbol")
        try:
            asset_class = AssetClass(raw_asset_class)
        except ValueError as exc:
            raise ValueError(f"Hyperliquid {symbol!r} has unsupported asset_class={raw_asset_class!r}") from exc
        accepted = _HYPERLIQUID_SECURITY_KIND_ASSET_CLASSES.get(security_kind)
        if accepted is None or asset_class not in accepted:
            raise ValueError(
                f"Hyperliquid {symbol!r} must have an explicit compatible security_kind and asset_class; "
                f"got security_kind={security_kind!r} asset_class={asset_class.value!r}"
            )
        normalized = provider_symbol.upper()
        if normalized in securities:
            raise ValueError(f"Duplicate Hyperliquid provider_symbol={provider_symbol!r}")
        securities[normalized] = HyperliquidSecurity(
            symbol=symbol,
            provider_symbol=provider_symbol,
            asset_class=asset_class,
            security_kind=security_kind,
            underlying=str(item.get("underlying") or "").strip() or None,
            currency=str(item.get("currency") or "USDC").strip().upper(),
            exchange=str(item.get("exchange") or "HYPERLIQUID").strip().upper(),
        )
    return securities


def configured_binance_securities(path: Path | None = None) -> dict[str, BinanceSecurity]:
    """Return only explicitly classified Binance TradFi perpetuals."""
    securities: dict[str, BinanceSecurity] = {}
    for item in load_universe_config(path).get("instruments", []):
        if not isinstance(item, dict):
            continue
        providers = {str(value).strip().lower() for value in item.get("providers", [])}
        if "binance" not in providers:
            continue
        symbol = str(item.get("symbol") or "").strip()
        provider_symbol = str(item.get("provider_symbol") or symbol).strip().upper()
        security_kind = str(item.get("security_kind") or "").strip().lower()
        raw_asset_class = str(item.get("asset_class") or "").strip().lower()
        if not symbol or not provider_symbol:
            raise ValueError("Binance allowlist entry is missing symbol")
        try:
            asset_class = AssetClass(raw_asset_class)
        except ValueError as exc:
            raise ValueError(f"Binance {symbol!r} has unsupported asset_class={raw_asset_class!r}") from exc
        if asset_class is not AssetClass.FUTURE or security_kind not in {"stock_derivative", "equity_derivative"}:
            raise ValueError(
                f"Binance {symbol!r} must be an explicitly classified stock/equity derivative"
            )
        if provider_symbol in securities:
            raise ValueError(f"Duplicate Binance provider_symbol={provider_symbol!r}")
        securities[provider_symbol] = BinanceSecurity(
            symbol=symbol,
            provider_symbol=provider_symbol,
            asset_class=asset_class,
            security_kind=security_kind,
            underlying=str(item.get("underlying") or "").strip() or None,
            currency=str(item.get("currency") or "USDT").strip().upper(),
            exchange=str(item.get("exchange") or "BINANCE_FUTURES").strip().upper(),
        )
    return securities


def configured_instrument(provider: str, symbol: str, path: Path | None = None) -> dict[str, Any] | None:
    """Return metadata for one explicitly configured provider/symbol pair."""

    provider = provider.strip().lower()
    wanted = symbol.strip().upper()
    for item in load_universe_config(path).get("instruments", []):
        if not isinstance(item, dict):
            continue
        providers = {str(value).strip().lower() for value in item.get("providers", [])}
        configured = str(item.get("symbol") or "").strip().upper()
        comparable = configured.removeprefix("US:").removeprefix("KR:").removeprefix("A")
        if provider in providers and wanted.removeprefix("A") == comparable:
            return dict(item)
    return None
