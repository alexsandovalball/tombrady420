# config/config.py
"""
Centralized configuration parsing and validation.
Raises RuntimeError on invalid configuration to fail fast.
"""
from __future__ import annotations

import os
from dotenv import load_dotenv

# Import taxonomy configuration
from .taxonomy import CATEGORIES, DISPLAY_NAMES

load_dotenv()


def _get_env(name: str, default=None):
    """Get environment variable or return default."""
    val = os.getenv(name, default)
    if val is None:
        return None
    return val


def _get_int(name: str, default=None):
    """Get environment variable as integer."""
    val = _get_env(name, None)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer: got {val!r}")


def _get_int_list(name: str):
    """Get environment variable as list of integers (comma-separated)."""
    val = _get_env(name, "")
    if not val:
        return []
    parts = [p.strip() for p in val.split(",") if p.strip()]
    try:
        return [int(p) for p in parts]
    except ValueError:
        raise RuntimeError(f"{name} must be a comma-separated list of integers")


def _get_bool(name: str, default: bool = False) -> bool:
    """Get environment variable as boolean."""
    val = _get_env(name, "")
    if not val:
        return default
    return val.lower() in ("true", "1", "yes")


# Required configuration
BOT_TOKEN = _get_env("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required. Set it in the environment or .env file")

# Channel configuration (optional)
CHANNEL_ID = _get_int("CHANNEL_ID", None)
CHANNEL_USERNAME = _get_env("CHANNEL_USERNAME", None)

# Admin configuration
ADMIN_IDS_LIST = _get_int_list("ADMIN_IDS")
ADMIN_IDS = set(ADMIN_IDS_LIST) if ADMIN_IDS_LIST else {8589411468}  # Fallback default

# Database configuration
DATABASE_URL = _get_env("DATABASE_URL", None)
DB_PATH = _get_env("DB_PATH", "data/catalog.db")

# Logging configuration
LOG_LEVEL = _get_env("LOG_LEVEL", "INFO")
DEBUG_LOG_UPDATES = _get_bool("DEBUG_LOG_UPDATES", False)

# Redis configuration (optional)
REDIS_URL = _get_env("REDIS_URL", None)

# Webhook configuration
USE_WEBHOOK = _get_bool("USE_WEBHOOK", False)
WEBHOOK_URL = _get_env("WEBHOOK_URL", None)

# Preview configuration
PREVIEW_TTL_SECONDS = _get_int("PREVIEW_TTL_SECONDS", 3600)