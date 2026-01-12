# config/__init__.py
"""
Configuration package.

Exports all configuration variables and taxonomy definitions.
"""
from .config import (
    BOT_TOKEN,
    CHANNEL_ID,
    CHANNEL_USERNAME,
    ADMIN_IDS,
    DATABASE_URL,
    DB_PATH,
    LOG_LEVEL,
    REDIS_URL,
    DEBUG_LOG_UPDATES,
    USE_WEBHOOK,
    WEBHOOK_URL,
    CATEGORIES,
    DISPLAY_NAMES,
)

__all__ = [
    "BOT_TOKEN",
    "CHANNEL_ID",
    "CHANNEL_USERNAME",
    "ADMIN_IDS",
    "DATABASE_URL",
    "DB_PATH",
    "LOG_LEVEL",
    "REDIS_URL",
    "DEBUG_LOG_UPDATES",
    "USE_WEBHOOK",
    "WEBHOOK_URL",
    "CATEGORIES",
    "DISPLAY_NAMES",
]