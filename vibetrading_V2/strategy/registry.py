"""Strategy registry/loader with sandbox and schema validation."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Optional

from vibetrading_V2.core.errors import (
    StrategyLoadError,
    StrategySchemaError,
    StrategyValidationError,
)
from vibetrading_V2.strategy.bundle import (
    CostOverride,
    PolicyOverrides,
    RiskOverride,
    SizingOverride,
    StrategyBundle,
)
from vibetrading_V2.strategy.sandbox import validate_strategy_imports


def _resolve_strategy_path(
    strategy: str | Path,
    *,
    strategies_dir: str | Path = "strategies",
    strategies_root: Optional[str | Path] = None,
) -> Path:
    candidate = Path(strategy)
    if candidate.is_file():
        return candidate.resolve()

    root = Path(strategies_root) if strategies_root is not None else Path(strategies_dir)
    name = str(strategy)
    filename = name if name.endswith(".py") else f"{name}.py"
    fallback = root / filename
    if fallback.is_file():
        return fallback.resolve()

    raise StrategyLoadError(f"Strategy not found: {strategy}")


def _load_module_from_path(module_path: Path) -> ModuleType:
    module_name = f"_v2_strategy_{module_path.stem}_{abs(hash(module_path))}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise StrategyLoadError(f"Unable to create import spec for {module_path}")

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:  # pragma: no cover - defensive wrapper
        raise StrategyLoadError(
            f"Failed to import strategy module {module_path}: {exc}"
        ) from exc
    return module


def _extract_bundle(module: ModuleType) -> StrategyBundle:
    if hasattr(module, "get_bundle") and callable(module.get_bundle):
        bundle = module.get_bundle()
    elif hasattr(module, "BUNDLE"):
        bundle = module.BUNDLE
    else:
        raise StrategyValidationError(
            "Strategy must export get_bundle() or BUNDLE: StrategyBundle"
        )

    if not isinstance(bundle, StrategyBundle):
        raise StrategyValidationError("Strategy export is not a StrategyBundle")
    return bundle


def _validate_bundle(bundle: StrategyBundle) -> None:
    if not bundle.meta.universe:
        raise StrategySchemaError("Strategy meta.universe must be non-empty")
    if not bundle.meta.required_fields:
        raise StrategySchemaError("Strategy meta.required_fields must be non-empty")

    overrides = bundle.overrides
    if overrides is None:
        return
    if not isinstance(overrides, PolicyOverrides):
        raise StrategyValidationError("Strategy overrides must be PolicyOverrides")
    if overrides.cost is not None and not isinstance(overrides.cost, CostOverride):
        raise StrategyValidationError("Strategy overrides.cost must be CostOverride")
    if overrides.risk is not None and not isinstance(overrides.risk, RiskOverride):
        raise StrategyValidationError("Strategy overrides.risk must be RiskOverride")
    if overrides.sizing is not None and not isinstance(overrides.sizing, SizingOverride):
        raise StrategyValidationError(
            "Strategy overrides.sizing must be SizingOverride"
        )


def load_strategy_bundle(
    strategy: str | Path,
    *,
    strategies_dir: str | Path = "strategies",
    strategies_root: Optional[str | Path] = None,
) -> StrategyBundle:
    """Load and validate a strategy bundle from a path or strategy name."""
    strategy_path = _resolve_strategy_path(
        strategy,
        strategies_dir=strategies_dir,
        strategies_root=strategies_root,
    )
    validate_strategy_imports(strategy_path)
    module = _load_module_from_path(strategy_path)
    bundle = _extract_bundle(module)
    _validate_bundle(bundle)
    return bundle


# Backward-compatible alias for parallel worker test variants.
load_bundle = load_strategy_bundle


def validate_all_strategies(*, strategies_dir: str | Path = "strategies") -> int:
    """Validate all strategy files in a directory. Returns process exit code."""
    root = Path(strategies_dir)
    if not root.exists():
        raise StrategyLoadError(f"Strategies directory not found: {root}")

    had_error = False
    for strategy_file in sorted(root.glob("*.py")):
        if strategy_file.name.startswith("__"):
            continue
        try:
            load_strategy_bundle(strategy_file)
            print(f"[OK] {strategy_file}")
        except Exception as exc:
            had_error = True
            print(f"[FAIL] {strategy_file}: {exc}")
    return 1 if had_error else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate V2 strategy bundles")
    parser.add_argument("--validate-all", action="store_true")
    parser.add_argument("--strategy")
    parser.add_argument("--strategies-dir", default="strategies")
    args = parser.parse_args()

    if args.validate_all:
        return validate_all_strategies(strategies_dir=args.strategies_dir)
    if args.strategy:
        load_strategy_bundle(args.strategy, strategies_dir=args.strategies_dir)
        print(f"[OK] {args.strategy}")
        return 0
    parser.error("Pass --validate-all or --strategy <name|path>")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
