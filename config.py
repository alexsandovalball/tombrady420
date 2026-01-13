"""
Centralized configuration parsing and validation.
Raises RuntimeError on invalid configuration to fail fast.
"""
import os
from dotenv import load_dotenv

load_dotenv()

def _get_env(name, default=None):
    val = os.getenv(name, default)
    if val is None:
        return None
    return val

def _get_int(name, default=None):
    val = _get_env(name, None)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer: got {val!r}")

def _get_int_list(name):
    val = _get_env(name, "")
    if not val:
        return []
    parts = [p.strip() for p in val.split(",") if p.strip()]
    try:
        return [int(p) for p in parts]
    except ValueError:
        raise RuntimeError(f"{name} must be a comma-separated list of integers")

BOT_TOKEN = _get_env("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required. Set it in the environment or .env file")

CHANNEL_ID = _get_int("CHANNEL_ID", None)
CHANNEL_USERNAME = _get_env("CHANNEL_USERNAME", None)
ADMIN_IDS = _get_int_list("ADMIN_IDS")
DATABASE_URL = _get_env("DATABASE_URL", None)
LOG_LEVEL = _get_env("LOG_LEVEL", "INFO")
