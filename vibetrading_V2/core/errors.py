"""Typed errors for V2 strategy loading/validation."""


class V2Error(Exception):
    """Base class for V2 errors."""


class StrategyLoadError(V2Error):
    """Raised when a strategy module cannot be loaded."""


class StrategySandboxError(V2Error):
    """Raised when a strategy violates sandbox import rules."""


class StrategyValidationError(V2Error):
    """Raised when a strategy bundle fails schema validation."""


class StrategyImportViolationError(StrategySandboxError):
    """Backward-compatible alias for import violations."""


class StrategySchemaError(StrategyValidationError):
    """Backward-compatible alias for bundle/schema validation failures."""


# Backward-compatible name alias used by some callers/tests.
StrategyImportError = StrategySandboxError
