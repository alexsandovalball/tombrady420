# config/config.py
"""
Runtime configuration loaded from environment variables and .env (via python-dotenv).
Keep secrets in environment variables (Render dashboard).
"""

from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = set(int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()) or {8589411468}
DB_PATH = os.getenv("DB_PATH", "data/catalog.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "false").lower() in ("1", "true", "yes")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", None)
PREVIEW_TTL_SECONDS = int(os.getenv("PREVIEW_TTL_SECONDS", "60"))

import os

# existing config...
PREVIEW_TTL_SECONDS = int(os.getenv("PREVIEW_TTL_SECONDS", "3600"))
REDIS_URL = os.getenv("REDIS_URL", "")  # e.g. redis://:password@host:6379/0