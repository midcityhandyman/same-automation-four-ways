"""Lead follow up automation: the tested Python implementation."""

from .config import Config
from .engine import Engine, Result
from .store import Store

__all__ = ["Config", "Engine", "Result", "Store"]
__version__ = "0.1.0"
