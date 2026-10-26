"""Historical test/rollback identity alias; excluded from production packages."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module('strategies.research.state.policy')
