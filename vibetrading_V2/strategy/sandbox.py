"""Static sandbox checks for strategy imports."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

from vibetrading_V2.core.errors import StrategyImportViolationError, StrategySandboxError

DEFAULT_ALLOWED_IMPORT_PREFIXES: tuple[str, ...] = (
    "vibetrading_V2.core",
    "vibetrading_V2.strategy.base",
    "vibetrading_V2.strategy.bundle",
    "__future__",
    "typing",
    "dataclasses",
    "math",
    "numpy",
    "pandas",
)

DEFAULT_DENIED_IMPORT_PREFIXES: tuple[str, ...] = (
    "vibetrading_V2.runner",
    "vibetrading_V2.execution",
    "vibetrading_V2.data",
    "cli",
    "os",
    "pathlib",
    "io",
    "socket",
    "asyncio",
    "sqlalchemy",
    "psycopg",
    "redis",
    "nats",
    "requests",
    "httpx",
    "websockets",
    "aiohttp",
    "binance",
)


def _matches_prefix(module_name: str, prefixes: Iterable[str]) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in prefixes
    )


def _iter_imports(tree: ast.AST) -> list[tuple[int, str]]:
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                imports.append((node.lineno, f"relative(level={node.level})"))
            elif node.module:
                imports.append((node.lineno, node.module))
    return imports


def validate_strategy_imports(
    strategy_path: str | Path,
    *,
    allowed_prefixes: tuple[str, ...] = DEFAULT_ALLOWED_IMPORT_PREFIXES,
    denied_prefixes: tuple[str, ...] = DEFAULT_DENIED_IMPORT_PREFIXES,
) -> None:
    """Validate strategy imports against strict allow/deny lists."""
    path = Path(strategy_path)
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StrategySandboxError(f"Unable to read strategy file: {path}") from exc

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise StrategySandboxError(
            f"Strategy contains invalid syntax at line {exc.lineno}: {exc.msg}"
        ) from exc

    forbidden: list[tuple[int, str]] = []
    unsupported: list[tuple[int, str]] = []

    for line_no, module_name in _iter_imports(tree):
        if _matches_prefix(module_name, denied_prefixes):
            forbidden.append((line_no, module_name))
        elif not _matches_prefix(module_name, allowed_prefixes):
            unsupported.append((line_no, module_name))

    if forbidden or unsupported:
        details: list[str] = []
        if forbidden:
            details.append(
                "forbidden imports: "
                + ", ".join(f"{name} (line {line})" for line, name in forbidden)
            )
        if unsupported:
            details.append(
                "imports outside allowlist: "
                + ", ".join(f"{name} (line {line})" for line, name in unsupported)
            )
        raise StrategyImportViolationError(
            f"Strategy import policy violation in {path}: " + "; ".join(details)
        )
