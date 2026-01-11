# config/__init__.py
"""
Package-level config shim.
This file re-exports the runtime configuration values so callers can do:
    from config import ADMIN_IDS, PREVIEW_TTL_SECONDS
"""

# Import the real config implementation (file: config.py) and re-export selected names.
from .config import *  # noqa: F401,F403

# Optionally, explicitly list what this package exports
__all__ = [
    "BOT_TOKEN",
    "CHANNEL_ID",
    "CHANNEL_USERNAME",
    "ADMIN_IDS",
    "DATABASE_URL",
    "LOG_LEVEL",
    "PREVIEW_TTL_SECONDS",
]