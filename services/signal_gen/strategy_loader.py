"""
Strategy Loader
Direct import mechanism for trading strategies.

Rules (from trading_spec.md):
- Direct import only
- No registries, factories, or discovery layers
- Strategy treated as a black box
- Strategy import occurs at process start
- Strategy change requires process restart
"""

import importlib
import logging
from typing import Any, Callable, Dict, Optional, Protocol

from shared.models import Candle, Position, Signal, StrategyContext, StrategyResult, TeamType

logger = logging.getLogger(__name__)


class StrategyProtocol(Protocol):
    """Protocol defining the strategy interface."""
    
    name: str
    
    def on_candle(
        self,
        candle: Candle,
        context: StrategyContext,
    ) -> StrategyResult:
        """
        Process a new candle and generate signals.
        
        Args:
            candle: New candle data
            context: Strategy execution context
            
        Returns:
            StrategyResult with any generated signals
        """
        ...
    
    def initialize(self) -> None:
        """Initialize strategy state (called once at start)."""
        ...
    
    def reset(self) -> None:
        """Reset strategy state (for backtesting)."""
        ...


class StrategyWrapper:
    """
    Wrapper around a loaded strategy module.
    
    Provides a consistent interface regardless of the
    underlying strategy implementation details.
    """
    
    def __init__(self, strategy: Any, name: str) -> None:
        self._strategy = strategy
        self._name = name
        self._initialized = False
    
    @property
    def name(self) -> str:
        """Get strategy name."""
        return self._name
    
    def initialize(self) -> None:
        """Initialize the strategy."""
        if hasattr(self._strategy, "initialize"):
            self._strategy.initialize()
        self._initialized = True
        logger.info(f"Strategy '{self._name}' initialized")
    
    def reset(self) -> None:
        """Reset strategy state."""
        if hasattr(self._strategy, "reset"):
            self._strategy.reset()
        logger.info(f"Strategy '{self._name}' reset")
    
    def on_candle(
        self,
        candle: Candle,
        context: StrategyContext,
    ) -> StrategyResult:
        """
        Process candle through strategy.
        
        Args:
            candle: Candle data
            context: Execution context
            
        Returns:
            StrategyResult with signals
        """
        if not self._initialized:
            self.initialize()
        
        if hasattr(self._strategy, "on_candle"):
            result = self._strategy.on_candle(candle, context)
            if isinstance(result, StrategyResult):
                return result
            elif isinstance(result, list):
                # Handle strategies returning list of signals
                return StrategyResult(signals=result)
            elif result is None:
                return StrategyResult()
            else:
                logger.warning(f"Unexpected return type from strategy: {type(result)}")
                return StrategyResult()
        else:
            raise AttributeError(f"Strategy '{self._name}' has no 'on_candle' method")


def _resolve_strategy_object(module: Any) -> Any:
    """Resolve strategy object from imported module."""
    if hasattr(module, "strategy"):
        return module.strategy
    if hasattr(module, "Strategy"):
        return module.Strategy()
    return module


def _resolve_declared_team(strategy_obj: Any) -> TeamType:
    """Resolve declared strategy team, defaulting to trading."""
    declared_team = getattr(strategy_obj, "TEAM_TYPE", TeamType.TRADING)
    if isinstance(declared_team, str):
        declared_team = TeamType(declared_team)
    return declared_team


def load_strategy(strategy_name: str, expected_team: TeamType | None = None) -> StrategyWrapper:
    """
    Load a strategy by name via direct import.
    
    Strategy modules are expected at: strategies/<strategy_name>.py
    
    Args:
        strategy_name: Name of the strategy module (without .py)
        expected_team: Optional team type contract for validation
        
    Returns:
        StrategyWrapper instance
        
    Raises:
        ImportError: If strategy cannot be imported
        AttributeError: If strategy doesn't implement required interface
    """
    module_path = f"strategies.{strategy_name}"
    
    try:
        logger.info(f"Loading strategy from '{module_path}'")
        module = importlib.import_module(module_path)
        strategy_obj = _resolve_strategy_object(module)
        declared_team = _resolve_declared_team(strategy_obj)
        if expected_team is not None and declared_team != expected_team:
            raise ValueError(
                f"Strategy '{strategy_name}' declares team '{declared_team.value}' but expected '{expected_team.value}'"
            )

        # Get name
        name = getattr(strategy_obj, "name", strategy_name)
        
        wrapper = StrategyWrapper(strategy_obj, name)
        logger.info(f"Successfully loaded strategy: {name}")
        
        return wrapper
        
    except ImportError as e:
        logger.error(f"Failed to import strategy '{strategy_name}': {e}")
        raise
    except Exception as e:
        logger.error(f"Error loading strategy '{strategy_name}': {e}")
        raise


# Cache for loaded strategies
_loaded_strategies: Dict[str, StrategyWrapper] = {}
_strategy_declared_teams: Dict[str, TeamType] = {}


def get_strategy(strategy_name: str, expected_team: TeamType | None = None) -> StrategyWrapper:
    """
    Get a strategy, loading it if necessary.
    
    Args:
        strategy_name: Strategy module name
        
    Returns:
        Cached or newly loaded StrategyWrapper
    """
    cache_key = f"{strategy_name}:{expected_team.value if expected_team else 'auto'}"
    if cache_key not in _loaded_strategies:
        _loaded_strategies[cache_key] = load_strategy(strategy_name, expected_team=expected_team)
    return _loaded_strategies[cache_key]


def clear_strategy_cache() -> None:
    """Clear the strategy cache (for testing)."""
    _loaded_strategies.clear()
    _strategy_declared_teams.clear()


def get_strategy_team(strategy_name: str) -> TeamType:
    """
    Read the strategy's declared team.

    Strategy may declare:
    - TEAM_TYPE = TeamType.TRADING
    - TEAM_TYPE = "trading"
    """
    if strategy_name in _strategy_declared_teams:
        return _strategy_declared_teams[strategy_name]

    module = importlib.import_module(f"strategies.{strategy_name}")
    strategy_obj = _resolve_strategy_object(module)
    team = _resolve_declared_team(strategy_obj)
    _strategy_declared_teams[strategy_name] = team
    return team


def resolve_strategy_team(
    strategy_name: str,
    requested_team: TeamType | None = None,
) -> TeamType:
    """
    Resolve effective team for a strategy.

    If requested_team is provided, validates against strategy TEAM_TYPE.
    Otherwise, uses strategy TEAM_TYPE (default trading).
    """
    declared_team = get_strategy_team(strategy_name)
    if requested_team is not None and declared_team != requested_team:
        raise ValueError(
            f"Strategy '{strategy_name}' declares team '{declared_team.value}' "
            f"but requested '{requested_team.value}'"
        )
    return declared_team
