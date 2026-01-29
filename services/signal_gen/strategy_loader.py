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

from shared.models import Candle, Position, Signal, StrategyContext, StrategyResult

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


def load_strategy(strategy_name: str) -> StrategyWrapper:
    """
    Load a strategy by name via direct import.
    
    Strategy modules are expected at: strategies/<strategy_name>.py
    
    Args:
        strategy_name: Name of the strategy module (without .py)
        
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
        
        # Look for strategy class or instance
        strategy_obj = None
        
        # Check for explicit strategy attribute
        if hasattr(module, "strategy"):
            strategy_obj = module.strategy
        elif hasattr(module, "Strategy"):
            # Instantiate class
            strategy_obj = module.Strategy()
        else:
            # Use the module itself as the strategy
            strategy_obj = module
        
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


def get_strategy(strategy_name: str) -> StrategyWrapper:
    """
    Get a strategy, loading it if necessary.
    
    Args:
        strategy_name: Strategy module name
        
    Returns:
        Cached or newly loaded StrategyWrapper
    """
    if strategy_name not in _loaded_strategies:
        _loaded_strategies[strategy_name] = load_strategy(strategy_name)
    return _loaded_strategies[strategy_name]


def clear_strategy_cache() -> None:
    """Clear the strategy cache (for testing)."""
    _loaded_strategies.clear()
