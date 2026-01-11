"""
Package-level config shim.
Re-exports runtime configuration values from config/config.py so callers can do:
    from config import ADMIN_IDS, PREVIEW_TTL_SECONDS
"""
from .config import *  # noqa: F401,F403

__all__ = [
    "BOT_TOKEN",
    "CHANNEL_ID",
    "CHANNEL_USERNAME",
    "ADMIN_IDS",
    "DATABASE_URL",
    "LOG_LEVEL",
    "PREVIEW_TTL_SECONDS",
]
