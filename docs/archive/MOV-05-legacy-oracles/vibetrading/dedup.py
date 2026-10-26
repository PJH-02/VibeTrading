import sys as _sys
from core import dedup as _canonical
_sys.modules[__name__] = _canonical
