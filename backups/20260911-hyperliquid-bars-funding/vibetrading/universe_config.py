from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from .settings import PROJECT_ROOT
from .models import AssetClass


@dataclass(frozen=True)
class HyperliquidSecurity:
    symbol: str
    asset_class: AssetClass
    security_kind: str
    underlying: str | None = None
    currency: str = "USDC"
    exchange: str = "HYPERLIQUID"


# The pair is deliberately validated.  A symbol spelling convention is never
# accepted as evidence that a product is a security.
_HYPERLIQUID_SECURITY_KIND_ASSET_CLASSES: dict[str, frozenset[AssetClass]] = {
    "tokenized_security": frozenset({AssetClass.TOKENIZED_EQUITY}),
    "tokenized_equity": frozenset({AssetClass.TOKENIZED_EQUITY}),
    "stock_derivative": frozenset({AssetClass.FUTURE}),
    "equity_derivative": frozenset({AssetClass.FUTURE}),
    "index_derivative": frozenset({AssetClass.FUTURE, AssetClass.INDEX}),
}


def load_universe_config(path: Path | None = None) -> dict[str, Any]:
    target = path or (PROJECT_ROOT / "config" / "universe.yaml")
    if not target.exists():
        return {"instruments": []}
    if yaml is None:
        raise RuntimeError("PyYAML is required for universe.yaml")
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {"instruments": []}


def configured_symbols(provider: str, path: Path | None = None) -> list[str]:
    provider = provider.strip().lower()
    if provider == "hyperliquid":
        return list(configured_hyperliquid_securities(path))
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
        raw_asset_class = str(item.get("asset_class") or "").strip().lower()
        security_kind = str(item.get("security_kind") or "").strip().lower()
        if not symbol:
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
        normalized = symbol.upper()
        securities[normalized] = HyperliquidSecurity(
            symbol=symbol,
            asset_class=asset_class,
            security_kind=security_kind,
            underlying=str(item.get("underlying") or "").strip() or None,
            currency=str(item.get("currency") or "USDC").strip().upper(),
            exchange=str(item.get("exchange") or "HYPERLIQUID").strip().upper(),
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
