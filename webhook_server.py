"""
Webhook server for Telegram bot using FastAPI and uvicorn.
This allows the bot to receive updates via webhooks instead of polling,
which is more suitable for serverless deployments like Render.com.
"""
import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import time as _time
import urllib.parse
from collections import OrderedDict
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from telegram import Update
from telegram.constants import FileSizeLimit
from telegram.ext import Application
from telegram.request import HTTPXRequest
import telegram.error

from configs.config import Config, ConfigError
# Import the bot setup functions
from main import (
    post_init,
    setup_bot_commands,
)
from utils.webapp_security import verify_init_data, parse_init_data, TelegramUser
from database import Database
from utils.helpers import get_channel_id, get_channel_username, get_file_id_cache_size, get_bot_specific_file_id, register_bot_handlers
from utils.categories import get_all_categories, get_category_display_name, get_subcategory_display_name, get_subcategories, EXCLUDED_FROM_ALL_PRODUCTS
from utils.fuzzy_search import fuzzy_search_products, normalize_text
from translations.translator import get_translated_string_async, translation_service
from utils.metrics import metrics
from utils.monitoring import monitor as _webhook_monitor, record_webhook_result
from utils.resource_manager import (
    APP_CACHE_DIR,
    APP_TMP_DIR,
    DEFAULT_TTL_SECONDS,
    DISK_CACHE_MAX_BYTES,
    DISK_CACHE_TTL_SECONDS,
    TMP_WARNING_BYTES,
    TMP_FILE_MAX_AGE_SECONDS,
    TMP_MAX_SIZE_BYTES,
    cleanup_owned_dir,
    emergency_cleanup,
    ensure_resource_dirs,
    get_cache_usage,
    get_tmp_usage,
    startup_cleanup,
)
from middleware.request_deduplicator import RequestDeduplicator
from configs.telegram_ip_whitelist import is_telegram_ip

# Configure logging
from configs.logging_config import configure_logging
configure_logging()
logger = logging.getLogger(__name__)

# Global registry for bot applications
# This allows the notification service to access all bot instances
_bot_applications = []
_webhook_secret_tokens: List[Optional[str]] = []
_catalog_db = Database()

# Per-process counter for webhook 401 rejections (in-memory, not persisted).
# Useful for setting alerts: check logs for [WEBHOOK_401] or watch this counter
# via the /_internal/metrics endpoint (recorded under "webhook_401" endpoint name).
_webhook_401_counter: int = 0

# Media cache configuration. Disk cache is app-owned, bounded, and image-only.
MEDIA_CACHE_DIR = Path(os.getenv("MEDIA_CACHE_DIR", APP_CACHE_DIR / "media"))
MEDIA_CACHE_ENABLED = os.getenv("MEDIA_CACHE_ENABLED", "true").lower() not in {"0", "false", "no"}
MEDIA_CACHE_TTL_SECONDS = int(os.getenv("MEDIA_CACHE_TTL_SECONDS", "300"))
MEDIA_CACHE_CLEANUP_INTERVAL_SECONDS = int(os.getenv("MEDIA_CACHE_CLEANUP_INTERVAL_SECONDS", "60"))
MEDIA_CACHE_MAX_SIZE_MB = min(int(os.getenv("MEDIA_CACHE_MAX_SIZE_MB", "250")), 250)
MEDIA_CACHE_TTL_HOURS = min(int(os.getenv("MEDIA_CACHE_TTL_HOURS", "1")), 1)
MEDIA_CACHE_MAX_FILE_BYTES = int(os.getenv("MEDIA_CACHE_MAX_FILE_MB", "10")) * 1024 * 1024
MEDIA_TEMP_MAX_BYTES = MEDIA_CACHE_MAX_SIZE_MB * 1024 * 1024

ensure_resource_dirs()
MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class WebhookIPRateLimiter:
    """Token-bucket rate limiter keyed per client IP for webhook endpoints.

    Each unique IP address gets its own token bucket.  Tokens refill at
    ``max_requests / window_seconds`` per second up to a maximum of ``max_requests``
    (burst equals the steady-state window quota, so a single IP can burst up to the
    full window allowance before being rate-limited).
    ``is_allowed`` is non-blocking: it returns True immediately if a token is
    available, or False if the bucket is empty (caller should respond 429).

    Old buckets are evicted when the number of tracked IPs exceeds ``_MAX_BUCKETS``
    to prevent unbounded memory growth.
    """

    _MAX_BUCKETS = 10_000  # evict oldest entries beyond this count
    # Default: 100 requests per 60-second window per IP
    _DEFAULT_MAX_REQUESTS = 100
    _DEFAULT_WINDOW_SECONDS = 60

    def __init__(
        self,
        max_requests: int = _DEFAULT_MAX_REQUESTS,
        window_seconds: int = _DEFAULT_WINDOW_SECONDS,
    ) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._rate = max_requests / window_seconds  # tokens per second
        self._burst = max_requests
        # ip -> [tokens: float, last_refill: float] ordered by insertion/access
        self._buckets: OrderedDict = OrderedDict()
        self._lock = asyncio.Lock()

    async def is_allowed(self, ip: str) -> bool:
        """Return True if the request is within limits, False if rate limited."""
        async with self._lock:
            now = _time.monotonic()
            if ip not in self._buckets:
                # Evict oldest entry if at capacity
                if len(self._buckets) >= self._MAX_BUCKETS:
                    self._buckets.popitem(last=False)
                self._buckets[ip] = [float(self._burst) - 1, now]
                return True
            tokens, last_refill = self._buckets[ip]
            elapsed = now - last_refill
            tokens = min(self._burst, tokens + elapsed * self._rate)
            # Move to end (most-recently-used) for LRU ordering
            self._buckets.move_to_end(ip)
            if tokens >= 1:
                self._buckets[ip] = [tokens - 1, now]
                return True
            self._buckets[ip] = [tokens, now]
            return False

class MediaCache:
    """Simple bounded LRU file cache for tiny reusable non-video media."""

    def __init__(self, cache_dir: Path, max_size_mb: int, ttl_hours: int):
        self.cache_dir = cache_dir
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.ttl_seconds = MEDIA_CACHE_TTL_SECONDS
        self.lock = asyncio.Lock()

    def _get_cache_path(self, file_id: str) -> Path:
        """Get cache file path for a file_id."""
        # Use hash to avoid filesystem issues with special characters
        hash_name = hashlib.sha256(file_id.encode()).hexdigest()
        return self.cache_dir / hash_name

    async def get(self, file_id: str) -> Optional[Path]:
        """Get cached file path if it exists and is valid."""
        cache_path = self._get_cache_path(file_id)

        if not cache_path.exists():
            return None

        # Check if file is still within TTL
        try:
            file_age = asyncio.get_event_loop().time() - cache_path.stat().st_mtime
            if file_age > self.ttl_seconds:
                # File is expired, delete it
                cache_path.unlink()
                logger.debug(f"Cache expired for file_id {file_id[:20]}...")
                return None
        except (OSError, FileNotFoundError):
            return None

        # Update access time for LRU tracking
        cache_path.touch()
        logger.debug(f"Cache HIT for file_id {file_id[:20]}...")
        return cache_path

    async def set(self, file_id: str, source_path: Path, *, allow: bool = True) -> Optional[Path]:
        """Store a file in the cache."""
        if not allow:
            return None
        async with self.lock:
            cache_path = self._get_cache_path(file_id)
            try:
                source_size = source_path.stat().st_size
            except (OSError, FileNotFoundError):
                return None
            if source_size > MEDIA_CACHE_MAX_FILE_BYTES:
                logger.info(
                    "[resource] cache_skip reason=file_too_large file_id=%s bytes=%d",
                    file_id[:20],
                    source_size,
                )
                return None

            # Copy file to cache
            shutil.copy2(source_path, cache_path)

            # Evict old files if cache is too large
            await self._evict_if_needed()

            logger.info("[resource] cache_set file_id=%s bytes=%d", file_id[:20], source_size)
            return cache_path

    async def _evict_if_needed(self):
        """Evict oldest files if cache exceeds max size."""
        try:
            # Collect stats once per file to avoid redundant syscalls
            cache_files = []
            for f in self.cache_dir.iterdir():
                if f.is_file():
                    try:
                        st = f.stat()
                        cache_files.append((f, st.st_atime, st.st_size))
                    except (OSError, FileNotFoundError):
                        pass

            # Calculate total cache size
            total_size = sum(size for _, _, size in cache_files)

            # If under limit, nothing to do
            if total_size <= self.max_size_bytes:
                return

            # Sort by access time (oldest first)
            cache_files.sort(key=lambda x: x[1])

            # Evict oldest files until under limit
            for file_path, _, size in cache_files:
                if total_size <= self.max_size_bytes:
                    break
                is_in_use = globals().get("_is_media_temp_in_use")
                if callable(is_in_use) and is_in_use(file_path):
                    logger.info("[media_cache] cleanup_skipped_in_use path=%s", file_path.name)
                    continue

                try:
                    file_path.unlink()
                    total_size -= size
                    logger.info("[resource] cache_evicted path=%s bytes=%d", file_path.name, size)
                except (OSError, FileNotFoundError):
                    pass
        except Exception as e:
            logger.warning(f"Error during cache eviction: {e}")

    async def clear(self):
        """Clear all cached files."""
        async with self.lock:
            try:
                for file_path in self.cache_dir.iterdir():
                    if file_path.is_file():
                        file_path.unlink()
                logger.info("Media cache cleared")
            except Exception as e:
                logger.warning(f"Error clearing cache: {e}")

    async def prune_expired(self):
        """Delete files whose mtime exceeds the configured TTL."""
        async with self.lock:
            try:
                now = _time.time()
                pruned = 0
                for file_path in self.cache_dir.iterdir():
                    if file_path.is_file():
                        try:
                            is_in_use = globals().get("_is_media_temp_in_use")
                            if callable(is_in_use) and is_in_use(file_path):
                                logger.info("[media_cache] cleanup_skipped_in_use path=%s", file_path.name)
                                continue
                            if now - file_path.stat().st_mtime > self.ttl_seconds:
                                file_path.unlink()
                                pruned += 1
                        except (OSError, FileNotFoundError):
                            pass
                if pruned:
                    logger.info("[resource] cache_pruned count=%d", pruned)
            except Exception as e:
                logger.warning(f"Error during media cache pruning: {e}")

# Global media cache instance
_media_cache = MediaCache(MEDIA_CACHE_DIR, MEDIA_CACHE_MAX_SIZE_MB, MEDIA_CACHE_TTL_HOURS)

# Lightweight in-memory cache for resolved bot-specific file_ids.
# Keyed by (source_chat_id, source_message_id, file_index, bot_username) → resolved_file_id.
# Avoids repeated DB lookups across consecutive HTTP Range 206 requests for the same media.
# Bounded to _RESOLVED_FILE_ID_CACHE_MAX entries with LRU eviction.
_resolved_file_id_cache: OrderedDict = OrderedDict()
_RESOLVED_FILE_ID_CACHE_MAX = 500

# In-memory cache for parsed media info, keyed by product id.
# Avoids repeated json.loads() on additional_file_ids across requests.
_media_info_cache: OrderedDict = OrderedDict()
_MEDIA_INFO_CACHE_MAX = 500

# In-memory result cache for expensive API list responses.
# Keyed by a string describing the request parameters.
# Each entry is (payload_bytes, timestamp).
_api_result_cache: OrderedDict = OrderedDict()
_API_RESULT_CACHE_MAX = 100
_API_RESULT_CACHE_TTL = 30  # seconds


def _media_temp_path(file_id: str, suffix: str) -> Path:
    safe_suffix = suffix if suffix.startswith(".") else f".{suffix}" if suffix else ".bin"
    return APP_TMP_DIR / f"media_{hashlib.sha256(file_id.encode()).hexdigest()}{safe_suffix}"


_media_temp_lock = asyncio.Lock()
_media_generation_locks: Dict[str, asyncio.Lock] = {}
_media_active_streams: Dict[Path, int] = {}
_media_last_access: Dict[Path, float] = {}


async def _get_media_generation_lock(cache_key: str) -> asyncio.Lock:
    async with _media_temp_lock:
        lock = _media_generation_locks.get(cache_key)
        if lock is None:
            lock = asyncio.Lock()
            _media_generation_locks[cache_key] = lock
        return lock


async def _mark_media_access(path: Path) -> tuple[float, float, int]:
    """Refresh last access so navigation bursts can reuse the same temp media."""
    now = _time.time()
    try:
        stat = path.stat()
        age = now - stat.st_mtime
    except (OSError, FileNotFoundError):
        return 0.0, 0.0, 0
    async with _media_temp_lock:
        previous_access = _media_last_access.get(path, stat.st_mtime)
        _media_last_access[path] = now
        active = _media_active_streams.get(path, 0)
    try:
        path.touch()
    except OSError:
        pass
    return age, now - previous_access, active


async def _stream_started(path: Path) -> int:
    async with _media_temp_lock:
        active = _media_active_streams.get(path, 0) + 1
        _media_active_streams[path] = active
        return active


async def _stream_finished(path: Path) -> int:
    async with _media_temp_lock:
        active = max(0, _media_active_streams.get(path, 0) - 1)
        if active:
            _media_active_streams[path] = active
        else:
            _media_active_streams.pop(path, None)
        _media_last_access[path] = _time.time()
        return active


def _is_media_temp_in_use(path: Path) -> bool:
    return _media_active_streams.get(path, 0) > 0


async def _cleanup_media_temp_files(force: bool = False) -> tuple[int, int]:
    """Evict expired reusable media temps without touching active streams."""
    ensure_resource_dirs()
    now = _time.time()
    removed = 0
    freed = 0
    candidates = []
    async with _media_temp_lock:
        active_paths = {path for path, count in _media_active_streams.items() if count > 0}

    for path in APP_TMP_DIR.glob("media_*"):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
            last_access = _media_last_access.get(path, stat.st_mtime)
            age = now - stat.st_mtime
            idle = now - last_access
            size = stat.st_size
            if path in active_paths:
                logger.info(
                    "[media_cache] cleanup_skipped_in_use path=%s active_streams=%d age=%.1f idle=%.1f",
                    path.name,
                    _media_active_streams.get(path, 0),
                    age,
                    idle,
                )
                continue
            candidates.append((path, last_access, size, age, idle))
        except (OSError, FileNotFoundError):
            continue

    total = sum(size for _, _, size, _, _ in candidates)
    for path, last_access, size, age, idle in sorted(candidates, key=lambda item: item[1]):
        expired = idle > MEDIA_CACHE_TTL_SECONDS
        oversized = total > MEDIA_TEMP_MAX_BYTES
        if not force and not expired and not oversized:
            continue
        try:
            path.unlink()
            removed += 1
            freed += size
            total -= size
            async with _media_temp_lock:
                _media_last_access.pop(path, None)
            logger.info(
                "[media_cache] cache_expired path=%s bytes=%d age=%.1f idle=%.1f reason=%s",
                path.name,
                size,
                age,
                idle,
                "oversize" if oversized and not expired else "ttl",
            )
        except (OSError, FileNotFoundError) as exc:
            logger.debug("[media_cache] cleanup_failed path=%s error=%s", path.name, exc)
    return removed, freed


def _cleanup_path(path: Optional[Path]) -> None:
    if not path:
        return
    try:
        path.unlink()
        logger.info("[resource] temp_deleted path=%s", path.name)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("[resource] temp_delete_failed path=%s error=%s", path, exc)


def _cache_resolved_file_id(key: tuple, file_id: str) -> None:
    """Insert a resolved file_id into the in-memory cache with LRU eviction."""
    if key in _resolved_file_id_cache:
        # Move to end (most recently used)
        _resolved_file_id_cache.move_to_end(key)
        _resolved_file_id_cache[key] = file_id
    else:
        # Add new entry
        _resolved_file_id_cache[key] = file_id
        # Evict oldest if over capacity (LRU)
        if len(_resolved_file_id_cache) > _RESOLVED_FILE_ID_CACHE_MAX:
            _resolved_file_id_cache.popitem(last=False)


def _api_result_cache_set(key: str, payload: str) -> None:
    """Insert an API result into the shared LRU cache with eviction."""
    _api_result_cache[key] = (payload, _time.monotonic())
    _api_result_cache.move_to_end(key)
    if len(_api_result_cache) > _API_RESULT_CACHE_MAX:
        _api_result_cache.popitem(last=False)


def clear_in_memory_caches() -> int:
    """Clear all in-memory caches. Returns total entries cleared."""
    global _resolved_file_id_cache, _media_info_cache, _api_result_cache

    total = len(_resolved_file_id_cache) + len(_media_info_cache) + len(_api_result_cache)

    _resolved_file_id_cache.clear()
    _media_info_cache.clear()
    _api_result_cache.clear()

    logger.info(f"Cleared {total} in-memory cache entries")
    return total

def get_bot_applications() -> List[Application]:
    """Get all bot application instances."""
    return [bot_app for bot_app in _bot_applications if bot_app is not None]

# ---------------------------------------------------------------------------
# Webhook-secret history (in-memory tolerance window for token rotations)
# ---------------------------------------------------------------------------
# Keeps one previous webhook secret per bot index so requests signed with the
# old token are still accepted for the duration of the current process (i.e.,
# until the next restart).  History is never persisted to disk — on restart the
# previous token is simply gone, which is safe because Telegram will always use
# the new token after set_webhook() succeeds.
#
# Structure: { bot_index: [previous_secret_or_None, current_secret_or_None] }
# history[bot_index][0] = previous token (for tolerance window)
# history[bot_index][1] = current token at time of last reinit (informational)
_webhook_secret_history: Dict[int, List[Optional[str]]] = {}


# ---------------------------------------------------------------------------
# Bot-config hot-reload (configs/bots.json)
# ---------------------------------------------------------------------------
# Operators can override the runtime-generated secret tokens by placing a
# configs/bots.json file (git-ignored; see configs/bots.example.json).
# The server reloads this file every 30 s so that bot replacements or
# secret-token rotations take effect without a full container restart.
#
# Schema (array under "bots" key):
#   { "bot_index": 0, "bot_username": "ExampleBot", "secret_token": "…" }
#
# When an entry is present for a given bot_index, its secret_token OVERRIDES
# the runtime-generated token that was registered with Telegram at startup.
# If the file does not exist or an entry is missing, the runtime token is used.

_BOTS_CONFIG_PATH = Path(__file__).parent / "configs" / "bots.json"

# Populated by _load_bots_config() and refreshed by the background loop.
# Maps bot_index (int) → secret_token (str | None)
_bots_config_tokens: Dict[int, Optional[str]] = {}
_bots_config_lock = asyncio.Lock()


def _load_bots_config() -> Dict[int, Optional[str]]:
    """Synchronously load configs/bots.json; return mapping of bot_index → token.

    Returns an empty dict if the file does not exist (fall-through to runtime tokens).
    Errors are logged and an empty dict is returned so callers degrade gracefully.
    """
    if not _BOTS_CONFIG_PATH.exists():
        return {}
    try:
        with _BOTS_CONFIG_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        mapping: Dict[int, Optional[str]] = {}
        for entry in data.get("bots", []):
            idx = entry.get("bot_index")
            tok = entry.get("secret_token") or None
            if isinstance(idx, int) and tok:
                mapping[idx] = tok
        return mapping
    except Exception as exc:
        logger.error("[bots_config] Failed to load %s: %s", _BOTS_CONFIG_PATH, exc)
        return {}


async def _bots_config_reload_loop() -> None:
    """Background task: reload configs/bots.json every 30 s.

    File I/O is dispatched to a thread pool via asyncio.to_thread() to avoid
    blocking the event loop during the synchronous open() call.
    """
    global _bots_config_tokens
    while True:
        await asyncio.sleep(30)
        try:
            new_mapping = await asyncio.to_thread(_load_bots_config)
        except Exception as exc:
            logger.error("[bots_config] Reload failed: %s", exc)
            continue
        async with _bots_config_lock:
            _bots_config_tokens = new_mapping
        if new_mapping:
            logger.debug("[bots_config] Reloaded %d bot token override(s)", len(new_mapping))


def _get_expected_secret_token(
    bot_index: int,
    runtime_tokens: List[Optional[str]],
) -> Optional[str]:
    """Return the expected secret token for *bot_index*.

    Priority:
      1. Override from configs/bots.json (hot-reloadable, no restart required)
      2. Runtime token generated at startup by setup_application()
    """
    # Check hot-reloadable override first
    override = _bots_config_tokens.get(bot_index)
    if override:
        return override
    # Fall back to runtime-generated token
    if bot_index < len(runtime_tokens):
        return runtime_tokens[bot_index]
    return None


def _secret_fingerprint(value: Optional[str]) -> str:
    """Return a short irreversible fingerprint for webhook-secret diagnostics."""
    if not isinstance(value, str) or not value:
        return "none"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def get_bot_usernames() -> List[str]:
    """Get list of bot usernames for all registered bots (normalized to lowercase)."""
    usernames = []
    for bot in _bot_applications:
        if bot is None:
            continue
        if hasattr(bot, 'bot') and hasattr(bot.bot, 'username') and bot.bot.username is not None:
            usernames.append(bot.bot.username.lower())
        else:
            # Log if a bot is missing username for debugging
            if hasattr(bot, 'bot'):
                logger.warning(f"Bot application has no username or username is None")
            else:
                logger.warning(f"Bot application has no 'bot' attribute")
    return usernames


# ---------------------------------------------------------------------------
# Bot reinitialisation helpers
# ---------------------------------------------------------------------------

async def _stop_existing_bots() -> None:
    """Gracefully stop all running Application objects in _bot_applications.

    Clears both the module-level registry and FastAPI app.state.bot_apps so
    the server can start fresh. Safe to call even when no bots are running.
    """
    global _bot_applications
    if not _bot_applications:
        return
    for bot_app in list(_bot_applications):
        if bot_app is None:
            continue
        try:
            if hasattr(bot_app, "stop"):
                await bot_app.stop()
            if hasattr(bot_app, "shutdown"):
                await bot_app.shutdown()
        except Exception:
            logger.exception("Error stopping bot application during reinitialisation")
    _bot_applications = []


async def reinitialize_bots(fastapi_app=None) -> dict:
    """Stop existing bot Applications and recreate them from Config.BOT_TOKENS.

    This is the primary runtime reinitialisation entry point.  It is called by:
    - the admin /reinitbots command
    - optionally the _bots_config_reload_loop when AUTO_REINIT_ON_BOTS_CONFIG is set

    Args:
        fastapi_app: Optional FastAPI application instance.  When provided,
                     app.state.bot_apps and app.state.webhook_secret_tokens are
                     kept in sync with the newly created applications.

    Returns:
        A dict with keys ``created`` (list of indices), ``failed`` (list of
        indices) and ``errors`` (dict mapping str(index) → error message).
    """
    import traceback
    global _bot_applications, _webhook_secret_tokens, _webhook_secret_history

    summary: dict = {"created": [], "failed": [], "errors": {}}
    logger.info("Starting bot reinitialisation (stop → setup)")

    # Snapshot the current runtime tokens before teardown so we can preserve
    # them as "previous" tokens in the history map.
    old_runtime_tokens: List[Optional[str]] = list(_webhook_secret_tokens)

    try:
        await _stop_existing_bots()
    except Exception:
        logger.exception("Unexpected error while stopping existing bot applications")

    tokens = getattr(Config, "BOT_TOKENS", []) or []
    if not tokens:
        logger.warning("No BOT_TOKENS found in Config during reinitialisation")
        return {"created": [], "failed": [], "errors": {"no_tokens": "No BOT_TOKENS configured"}}

    new_secret_tokens: List[Optional[str]] = []
    for idx, token in enumerate(tokens):
        # Preserve the previous webhook secret in history so in-flight requests
        # using the old token are not immediately rejected.
        prev_token: Optional[str] = old_runtime_tokens[idx] if idx < len(old_runtime_tokens) else None
        # history[idx] = [previous, current]; store prev before we know the new current
        _webhook_secret_history[idx] = [prev_token, None]

        try:
            bot_app, webhook_secret_token = await setup_application(token, bot_index=idx)
            # Now rotate: previous = what was current, current = new token
            _webhook_secret_history[idx] = [prev_token, webhook_secret_token]
            _bot_applications.append(bot_app)
            new_secret_tokens.append(webhook_secret_token)
            logger.info(
                "Re-initialised bot %d (webhook secret prefix=%s)",
                idx,
                (webhook_secret_token or "")[:8],
            )
            summary["created"].append(idx)
        except Exception as exc:
            logger.exception("Failed to reinitialise bot %d: %s", idx, exc)
            new_secret_tokens.append(None)
            summary["failed"].append(idx)
            summary["errors"][str(idx)] = str(exc)[:300]

    # Update module-level global so process_webhook_request always uses fresh tokens.
    _webhook_secret_tokens = new_secret_tokens

    # Sync FastAPI app.state so webhook verification uses the new tokens.
    if fastapi_app is not None:
        try:
            fastapi_app.state.bot_apps = _bot_applications
            fastapi_app.state.webhook_secret_tokens = new_secret_tokens
        except Exception:
            logger.exception("Could not update FastAPI app.state after reinitialisation")

    logger.info("Bot reinitialisation complete: %s", summary)
    return summary


async def setup_application(bot_token: str, bot_index: int = 0):
    """Initialize and configure a bot application.
    
    Args:
        bot_token: The Telegram bot token
        bot_index: Index of the bot (0 for primary, 1+ for additional bots)

    Returns:
        A tuple of (Application, secret_token) where secret_token is the
        deterministic value derived from bot_token registered with Telegram for
        this bot, or None if webhook mode is not active.
    """
    token_suffix = bot_token[-6:] if bot_token else "empty"
    logger.info("Bot startup begin index=%d token_suffix=%s", bot_index, token_suffix)
    
    # Create application with optimized timeout settings
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=10.0,
        read_timeout=10.0,
        write_timeout=10.0,
        pool_timeout=5.0
    )
    
    bot_app = Application.builder().token(bot_token).request(request).build()
    
    # Register handlers
    register_bot_handlers(bot_app, bot_index)
    
    # Initialize the application
    # IMPORTANT: Must be called before any bot API methods (like set_my_commands)
    try:
        await bot_app.initialize()
        await bot_app.start()
    except telegram.error.TelegramError as exc:
        logger.warning(
            "Bot startup failure index=%d token_suffix=%s during initialize/start: %s",
            bot_index,
            token_suffix,
            exc,
        )
        try:
            await bot_app.shutdown()
        except Exception:
            pass
        raise
    except Exception as exc:
        logger.warning(
            "Bot startup failure index=%d token_suffix=%s during initialize/start: %s",
            bot_index,
            token_suffix,
            exc,
        )
        try:
            await bot_app.shutdown()
        except Exception:
            pass
        raise

    # Detect token changes and clear stale file ID cache if needed
    try:
        bot_username = bot_app.bot.username
        bot_id = bot_app.bot.id
        if not bot_username or not bot_id:
            me = await bot_app.bot.get_me()
            bot_username = getattr(me, "username", bot_username)
            bot_id = getattr(me, "id", bot_id)
        token_hash = hashlib.sha256(bot_token.encode()).hexdigest()[:16]

        from database import Database
        _db = Database()
        if await _db.has_bot_token_changed(bot_username, token_hash):
            logger.warning(
                f"Bot token changed for @{bot_username}! Clearing stale file ID cache..."
            )
            cleared = await _db.clear_bot_file_id_cache_for_bot(bot_username)
            logger.info(f"Cleared {cleared} stale cache entries for @{bot_username}")

        await _db.register_bot_instance(bot_username, bot_id, token_hash)
    except telegram.error.TelegramError as e:
        logger.warning("Skipping startup metadata/cache registration for bot index=%d: %s", bot_index, e)
    except Exception as e:
        logger.warning("Skipping startup metadata/cache registration for bot index=%d: %s", bot_index, e)
    
    # Initialize database and set bot commands BEFORE the webhook is registered.
    # Once set_webhook() is called, PTB marks the bot as frozen and subsequent
    # API calls (like set_my_commands) will raise Frozen_method_invalid.
    if bot_index == 0:
        # post_init() handles DB init and cleanup tasks; command setup is guarded below.
        await post_init(bot_app, skip_commands=True)
        try:
            await setup_bot_commands(bot_app.bot)
        except telegram.error.TelegramError as e:
            logger.warning("Partial startup for bot index=%d: command setup skipped: %s", bot_index, e)
        except Exception as e:
            logger.warning("Partial startup for bot index=%d: command setup skipped: %s", bot_index, e)
    else:
        # Secondary bots only need command setup (DB already initialised by bot 0)
        try:
            await setup_bot_commands(bot_app.bot)
        except telegram.error.TelegramError as e:
            logger.warning("Partial startup for bot index=%d: command setup skipped: %s", bot_index, e)
        except Exception as e:
            logger.warning("Partial startup for bot index=%d: command setup skipped: %s", bot_index, e)

    # Set webhook with a freshly generated secret token so every deployment
    # automatically authenticates Telegram's requests without any env-var setup.
    webhook_secret_token: Optional[str] = None
    webhook_url = Config.WEBHOOK_URL
    if webhook_url:
        # Primary bot uses /webhook, additional bots use /webhook/1, /webhook/2, etc.
        webhook_path = "/webhook" if bot_index == 0 else f"/webhook/{bot_index}"
        # Generate a deterministic secret token from the bot token so the same
        # token is used across redeploys and survives bot replacements — the
        # secret only changes when the bot token itself changes.
        webhook_secret_token = hashlib.sha256(bot_token.encode('utf-8')).hexdigest()[:64]
        try:
            await _validate_or_repair_webhook(
                bot_app.bot,
                bot_index,
                webhook_url,
                webhook_path,
                webhook_secret_token,
            )
        except telegram.error.TelegramError as e:
            logger.warning("Partial startup for bot index=%d: webhook setup skipped: %s", bot_index, e)
            webhook_secret_token = None
        except Exception as e:
            logger.warning("Partial startup for bot index=%d: webhook setup skipped: %s", bot_index, e)
            webhook_secret_token = None
    else:
        logger.warning("WEBHOOK_URL not set. Webhook may not work properly.")

    logger.info("Bot startup success index=%d username=%s", bot_index, getattr(bot_app.bot, "username", None) or "unknown")
    return bot_app, webhook_secret_token


async def _validate_or_repair_webhook(bot, bot_index: int, webhook_url: str, webhook_path: str, secret_token: str) -> None:
    """Validate webhook URL/state and repair safely without dropping pending updates."""
    expected_url = f"{webhook_url}{webhook_path}"
    try:
        info = await bot.get_webhook_info()
        current_url = getattr(info, "url", "") or ""
        pending = getattr(info, "pending_update_count", 0)
        last_error = getattr(info, "last_error_message", None)
        has_cert = bool(getattr(info, "has_custom_certificate", False))
        mismatch = current_url != expected_url
        needs_secret_refresh = os.getenv("WEBHOOK_REFRESH_SECRET_ON_STARTUP", "true").lower() not in {"0", "false", "no"}
        if mismatch:
            logger.warning(
                "[webhook] mismatch_detected bot=%d current_url=%s expected_url=%s pending=%s cert=%s",
                bot_index, current_url, expected_url, pending, has_cert,
            )
        elif last_error:
            logger.warning("[webhook] existing_error bot=%d pending=%s error=%s", bot_index, pending, last_error)
        else:
            logger.info("[webhook] validated bot=%d pending=%s cert=%s", bot_index, pending, has_cert)

        if mismatch or needs_secret_refresh:
            await bot.set_webhook(
                url=expected_url,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
                secret_token=secret_token,
            )
            logger.info(
                "[webhook] repaired bot=%d reason=%s secret_fp=%s",
                bot_index,
                "url_mismatch" if mismatch else "secret_refresh",
                _secret_fingerprint(secret_token),
            )
    except telegram.error.TelegramError as exc:
        logger.warning("[webhook] validation_failed bot=%d error=%s", bot_index, exc)
        raise


async def _media_cache_prune_loop():
    """Background task: sweep app-owned cache/temp directories."""
    while True:
        await asyncio.sleep(MEDIA_CACHE_CLEANUP_INTERVAL_SECONDS)
        try:
            await _media_cache.prune_expired()
            removed, freed = await _cleanup_media_temp_files()
            if removed:
                logger.info("[media_cache] cleanup_completed removed=%d freed=%d", removed, freed)
            cleanup_owned_dir(
                APP_TMP_DIR,
                TMP_FILE_MAX_AGE_SECONDS,
                max_bytes=TMP_MAX_SIZE_BYTES,
                skip_file=_is_media_temp_in_use,
            )
            cleanup_owned_dir(APP_CACHE_DIR, DISK_CACHE_TTL_SECONDS, max_bytes=DISK_CACHE_MAX_BYTES)
            tmp_usage = get_tmp_usage()
            cache_usage = get_cache_usage()
            if tmp_usage["used_bytes"] >= TMP_WARNING_BYTES:
                logger.warning("[resource] tmp_usage_warning used=%d", tmp_usage["used_bytes"])
            if cache_usage["app_cache_bytes"] > DISK_CACHE_MAX_BYTES:
                logger.warning("[resource] cache_usage_warning app_cache=%d", cache_usage["app_cache_bytes"])
        except Exception as e:
            logger.warning(f"Media cache prune loop error: {e}")


async def _wal_checkpoint_loop():
    """Background task: checkpoint the SQLite WAL file every 15 minutes."""
    while True:
        await asyncio.sleep(900)  # 15 minutes
        try:
            await _catalog_db.checkpoint_wal()
        except Exception as e:
            logger.warning(f"WAL checkpoint loop error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - startup and shutdown."""
    global _bot_applications, _webhook_secret_tokens

    # Startup
    logger.info("Starting webhook server...")
    startup_cleanup()
    try:
        Config.validate()
    except ConfigError as e:
        logger.error(str(e))
        raise
    
    bot_tokens = Config.BOT_TOKENS
    
    if not bot_tokens:
        logger.error("No bot tokens configured!")
        raise ConfigError("At least one bot token is required")

    bot_apps: List[Optional[Application]] = [None] * len(bot_tokens)
    
    logger.info("Initializing %d bot instance(s) sequentially...", len(bot_tokens))

    webhook_secret_tokens: List[Optional[str]] = []
    startup_summary = {"successful": [], "failed": [], "skipped": []}
    for idx, token in enumerate(bot_tokens):
        if not token:
            logger.warning("Bot startup skipped index=%d reason=empty_token", idx)
            startup_summary["skipped"].append(idx)
            webhook_secret_tokens.append(None)
            continue
        try:
            bot_app, webhook_secret_token = await setup_application(token, idx)
            bot_apps[idx] = bot_app
            webhook_secret_tokens.append(webhook_secret_token)
            startup_summary["successful"].append(idx)
        except telegram.error.TelegramError as exc:
            logger.warning("Bot startup failure index=%d token_suffix=%s: %s", idx, token[-6:], exc)
            startup_summary["failed"].append(idx)
            webhook_secret_tokens.append(None)
        except Exception as exc:
            logger.warning("Bot startup failure index=%d token_suffix=%s: %s", idx, token[-6:], exc, exc_info=True)
            startup_summary["failed"].append(idx)
            webhook_secret_tokens.append(None)

    successful_bot_apps = [bot_app for bot_app in bot_apps if bot_app is not None]
    if not successful_bot_apps:
        logger.error("No bot applications initialized successfully; startup_summary=%s", startup_summary)
        raise ConfigError("At least one bot must initialize successfully")

    logger.info(
        "Bot startup complete successful=%s failed=%s skipped=%s",
        startup_summary["successful"],
        startup_summary["failed"],
        startup_summary["skipped"],
    )
    
    # Store in global registry for notification service access
    _bot_applications = bot_apps
    _webhook_secret_tokens = webhook_secret_tokens
    
    app.state.bot_apps = bot_apps

    # Store the per-bot secret tokens generated during set_webhook
    app.state.webhook_secret_tokens = webhook_secret_tokens

    # Initialize the per-IP rate limiter with hardcoded defaults (100 req/min)
    app.state.webhook_rate_limiter = WebhookIPRateLimiter()

    # Initialize per-callback request deduplicator to prevent double-processing
    # of identical callback queries (e.g., from Telegram retries or rapid taps).
    app.state.request_deduplicator = RequestDeduplicator()

    # Load bot-config overrides from configs/bots.json (if present) at startup
    global _bots_config_tokens
    _bots_config_tokens = _load_bots_config()
    if _bots_config_tokens:
        logger.info(
            "[bots_config] Loaded %d bot token override(s) from %s",
            len(_bots_config_tokens),
            _BOTS_CONFIG_PATH,
        )
        for idx, override in _bots_config_tokens.items():
            runtime_token = webhook_secret_tokens[idx] if idx < len(webhook_secret_tokens) else None
            if runtime_token and override != runtime_token:
                logger_security.warning(
                    "[webhook_secret] configured override differs from runtime token bot=%d override_fp=%s runtime_fp=%s",
                    idx,
                    _secret_fingerprint(override),
                    _secret_fingerprint(runtime_token),
                )
    else:
        logger.debug("[bots_config] No bots.json overrides found; using runtime tokens")
    
    # Log bot usernames for debugging
    for idx, bot_app in enumerate(bot_apps):
        if bot_app is None:
            logger.info("  Bot %d: disabled during startup", idx)
            continue
        if hasattr(bot_app, 'bot') and hasattr(bot_app.bot, 'username'):
            logger.info(f"  Bot {idx}: @{bot_app.bot.username}")
    
    # Log cache status for multi-bot setups
    if len(bot_apps) > 1:
        try:
            cache_size = await get_file_id_cache_size()
            if cache_size == 0:
                logger.warning(
                    f"File ID cache is empty. For secondary bots (bot 1-{len(bot_apps)-1}) to display media correctly, "
                    f"admins must start a conversation with each bot by sending /start. "
                    f"This allows the bots to cache bot-specific file IDs."
                )
            else:
                logger.info(f"File ID cache contains {cache_size} entries")
        except Exception as exc:
            logger.warning("Could not read file ID cache size during startup: %s", exc)

    # Start background maintenance tasks
    _bg_tasks = [
        asyncio.create_task(_media_cache_prune_loop(), name="media_cache_prune"),
        asyncio.create_task(_wal_checkpoint_loop(), name="wal_checkpoint"),
        asyncio.create_task(_bots_config_reload_loop(), name="bots_config_reload"),
    ]

    # Load permanently blocked IPs from the database into the in-memory registry
    try:
        blocked_ips = await _catalog_db.get_all_blocked_ips()
        if blocked_ips:
            load_permanent_ip_blocks(blocked_ips)
            logger.info("Loaded %d permanently blocked IP(s) from database", len(blocked_ips))
    except Exception as _e:
        logger.warning("Could not load blocked IPs from database: %s", _e)

    yield

    # Cancel background maintenance tasks
    for task in _bg_tasks:
        task.cancel()
    await asyncio.gather(*_bg_tasks, return_exceptions=True)

    # Shutdown
    logger.info("Shutting down bot applications...")
    for idx, bot_app in enumerate(_bot_applications):
        if bot_app is None:
            continue
        try:
            await bot_app.stop()
            await bot_app.shutdown()
            logger.info(f"Bot {idx} shut down successfully")
        except Exception as e:
            logger.error(f"Error shutting down bot {idx}: {e}")
    
    # Clear global registry
    _bot_applications.clear()
    logger.info("All bot applications shut down successfully")


# Create FastAPI app with lifespan
app = FastAPI(
    title="Telegram Bot Webhook Server",
    lifespan=lifespan
)

# Add middleware to set cache headers for static files
from fastapi.middleware import Middleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from middleware.rate_limiter import GlobalRateLimiter, get_client_ip, load_permanent_ip_blocks, add_permanent_ip_block, remove_permanent_ip_block

# Dedicated security logger – separate from the normal access log
logger_security = logging.getLogger("security")

# WordPress / CMS vulnerability scanner path patterns.
# Checked via str.startswith() or substring match depending on the pattern.
_WP_PREFIX_PATTERNS = (
    "/wp-admin",
    "/wp-includes",
    "/wp-content",
    "/wp-login.php",
    "/wp-config.php",
    "/wp-json",
    "/wordpress",
    "/wp1",
    "/wp2",
    "/xmlrpc.php",
    "/setup-config.php",
    "/.well-known/wp-",
    "/blog/wp-",
    "/web/wp-",
    "/website/wp-",
    "/news/wp-",
    "/shop/wp-",
    "/site/wp-",
    "/cms/wp-",
    "/sito/wp-",
    "/test/wp-",
    "/2019/wp-",
    "/2020/wp-",
    "/2021/wp-",
    "/2022/wp-",
)

# Substring patterns that can appear anywhere in the path
_WP_SUBSTR_PATTERNS = (
    "wlwmanifest.xml",
)

# Metrics counter – approximate (per-process, not shared across workers).
# In a single asyncio event loop the increment is safe; in multi-worker
# deployments each process maintains its own count, which is acceptable for
# monitoring purposes.
_wp_block_counter: int = 0


class SecurityMiddleware(BaseHTTPMiddleware):
    """Block WordPress/CMS vulnerability scanner requests at the earliest middleware layer.

    Returns ``410 Gone`` (instead of ``404 Not Found``) to signal permanent removal,
    which causes well-behaved scanners to drop this host from their target list.
    All blocked requests are logged to the dedicated ``security`` logger.
    """

    async def dispatch(self, request, call_next):
        global _wp_block_counter
        path = request.url.path
        path_lower = path.lower()

        blocked = path_lower.startswith(_WP_PREFIX_PATTERNS) or any(
            s in path_lower for s in _WP_SUBSTR_PATTERNS
        )

        if blocked:
            _wp_block_counter += 1
            client_ip = (
                request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or request.headers.get("X-Real-IP")
                or (request.client.host if request.client else "unknown")
            )
            ua = request.headers.get("User-Agent", "-")
            logger_security.warning(
                f"WordPress scan blocked – ip={client_ip} path={path} "
                f"ua={ua!r} total_blocked={_wp_block_counter}"
            )
            return Response(status_code=410)

        return await call_next(request)


class CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        
        # Add cache headers for static assets
        if request.url.path.startswith('/assets/'):
            # Cache CSS and JS for 1 hour to reduce backend load and decrease latency
            if request.url.path.endswith(('.js', '.css')):
                response.headers['Cache-Control'] = 'public, max-age=3600'
            # Cache images for 1 day (can be cached longer as they rarely change)
            elif request.url.path.endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico')):
                response.headers['Cache-Control'] = 'public, max-age=86400'
            elif request.url.path.endswith('.html'):
                response.headers['Cache-Control'] = 'no-cache'
        elif request.url.path in ('/webapp', '/webapp/', '/webapp/index.html'):
            # Allow conditional revalidation for the webapp entry point
            response.headers['Cache-Control'] = 'no-cache'

        # Security headers for HTML responses
        content_type = response.headers.get('content-type', '')
        if 'text/html' in content_type:
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        return response

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all incoming requests for debugging and monitoring."""
    async def dispatch(self, request, call_next):
        # Log incoming request at DEBUG level to avoid flooding production logs
        client_host = request.client.host if request.client else "unknown"
        logger.debug(f"Incoming request: {request.method} {request.url.path} from {client_host}")
        
        # Process request
        response = await call_next(request)
        
        # Log response status at DEBUG level
        logger.debug(f"Response: {request.method} {request.url.path} - Status: {response.status_code}")
        
        return response

# Middleware stack – order matters: FastAPI applies these in reverse registration
# order, so the last registered runs first.  We want:
#   SecurityMiddleware (outermost – reject scans before any other work)
#   GlobalRateLimiter
#   CacheControlMiddleware
#   RequestLoggingMiddleware
#   GZipMiddleware (innermost)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(CacheControlMiddleware)
app.add_middleware(GlobalRateLimiter)
app.add_middleware(SecurityMiddleware)

# Jinja2 templates for SSR/SEO pages
templates = Jinja2Templates(directory="webapp")

# Mount static assets (CSS, JS, images) under /assets.
# The root-relative path /assets works regardless of whether a custom domain is configured,
# because it is resolved server-side relative to the server's own origin — no absolute URL
# or CUSTOM_DOMAIN setting is required for static file serving to work correctly.
app.mount("/assets", StaticFiles(directory="webapp"), name="assets")

_STOREFRONT_BOT_USERNAME = os.getenv("BOT_USERNAME", "").lstrip("@")
_STOREFRONT_DOMAIN = Config.STOREFRONT_DOMAIN or ""
if _STOREFRONT_DOMAIN:
    _domain_source = (
        "STOREFRONT_DOMAIN env var" if os.getenv("STOREFRONT_DOMAIN")
        else ("CUSTOM_DOMAIN env var" if os.getenv("CUSTOM_DOMAIN")
              else "auto-derived from WEBHOOK_URL/WEB_APP_URL")
    )
    logger.info(f"Storefront domain: {_STOREFRONT_DOMAIN} (source: {_domain_source})")
else:
    logger.info(
        "Storefront domain not configured; sitemap/OG tags will use relative paths. "
        "Set WEBHOOK_URL or STOREFRONT_DOMAIN to enable absolute URLs."
    )

# Default Open Graph image with football emoji 🏈 for link previews.
# Must be an absolute HTTPS URL — data: URIs are rejected by all social media crawlers
# (WhatsApp, iMessage, Twitter/X, Facebook, Discord, Slack, Telegram, etc.).
_DEFAULT_OG_IMAGE = f"{_STOREFRONT_DOMAIN}/og-image.png"
_OG_IMAGE_WIDTH = 600
_OG_IMAGE_HEIGHT = 315


def _generate_slug(caption: str) -> str:
    """Generate a URL-safe slug from a product caption."""
    if not caption:
        return "product"
    first_line = caption.split('\n')[0].strip()
    slug = re.sub(r'[^a-z0-9]+', '-', first_line.lower()).strip('-')
    slug = slug[:60].rstrip('-')
    return slug or "product"


def _build_seo_context(*, title: str, desc: str, image: str = "", url: str = "",
                        product: Optional[dict] = None, deep_link: str = "",
                        keywords: str = "", category: str = "", og_type: str = "website") -> dict:
    """Build template context dict for SSR SEO pages."""
    if product:
        product_url = url or _STOREFRONT_DOMAIN
        product_schema: dict = {
            "@context": "https://schema.org/",
            "@type": "Product",
            "name": title,
            "description": desc,
            "image": image,
            "url": product_url,
            "brand": {
                "@type": "Brand",
                "name": "Top Shelf Cali Plug",
            },
            "offers": {
                "@type": "Offer",
                "url": product_url,
                "availability": "https://schema.org/InStock",
                "seller": {
                    "@type": "Organization",
                    "name": "Top Shelf Cali Plug",
                },
            },
        }
        breadcrumb: dict = {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "name": "Home",
                    "item": f"{_STOREFRONT_DOMAIN}/",
                },
                {
                    "@type": "ListItem",
                    "position": 2,
                    "name": category or "Products",
                    "item": f"{_STOREFRONT_DOMAIN}/?category={urllib.parse.quote(category)}" if category else f"{_STOREFRONT_DOMAIN}/",
                },
                {
                    "@type": "ListItem",
                    "position": 3,
                    "name": title,
                    "item": product_url,
                },
            ],
        }
        schema = json.dumps({
            "@context": "https://schema.org/",
            "@graph": [product_schema, breadcrumb],
        })
    else:
        schema = json.dumps({
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "WebSite",
                    "name": "Top Shelf Cali Plug",
                    "url": _STOREFRONT_DOMAIN,
                    "description": desc,
                    "potentialAction": {
                        "@type": "SearchAction",
                        "target": f"{_STOREFRONT_DOMAIN}/?search={{search_term_string}}",
                        "query-input": "required name=search_term_string",
                    },
                },
                {
                    "@type": "Store",
                    "name": title,
                    "description": desc,
                    "url": _STOREFRONT_DOMAIN,
                    "image": image,
                    "contactPoint": {
                        "@type": "ContactPoint",
                        "contactType": "customer service",
                        "url": f"https://t.me/{_STOREFRONT_BOT_USERNAME}",
                    },
                },
            ],
        })
    default_keywords = "premium catalog, online shop, Telegram store, Top Shelf Cali Plug, buy online, product catalog"
    resolved_image = image or _DEFAULT_OG_IMAGE
    # Include image dimensions only for the default OG image (product images have unknown dimensions)
    is_default_image = not image
    return {
        "seo_title": title,
        "seo_desc": desc,
        "seo_image": resolved_image,
        "seo_image_width": _OG_IMAGE_WIDTH if is_default_image else None,
        "seo_image_height": _OG_IMAGE_HEIGHT if is_default_image else None,
        "seo_url": url,
        "seo_schema": schema,
        "seo_keywords": keywords or default_keywords,
        "og_type": og_type,
        "telegram_deep_link": deep_link or f"https://t.me/{_STOREFRONT_BOT_USERNAME}",
    }



@app.get("/og-image.png", response_class=Response)
async def serve_og_image():
    """Serve the football emoji OG image for social media link previews.

    Returns a static PNG file with football branding. Social media crawlers
    (WhatsApp, iMessage, Twitter/X, Facebook, Discord, Slack, Telegram) require
    a real HTTP/HTTPS URL — data: URIs are never fetched by these crawlers.
    """
    import pathlib
    image_path = pathlib.Path(__file__).parent / "webapp" / "og-football.png"
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="OG image not found")
    image_bytes = image_path.read_bytes()
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=86400",
            "Content-Length": str(len(image_bytes)),
        },
    )


@app.get("/robots.txt", response_class=Response)
async def serve_robots_txt():
    """Serve robots.txt to guide crawlers."""
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /webhook\n"
        "Disallow: /webhook/\n"
        "Disallow: /webapp\n"
        "Disallow: /webapp/\n"
        "\n"
        f"Sitemap: {_STOREFRONT_DOMAIN}/sitemap.xml\n"
    )
    return Response(content=content, media_type="text/plain")


@app.get("/sitemap.xml", response_class=Response)
async def serve_sitemap_xml():
    """Dynamically generate a sitemap.xml including homepage, category, and all product pages."""
    from datetime import date
    today = date.today().isoformat()
    products = await _catalog_db.get_all_products_excluding_categories(EXCLUDED_FROM_ALL_PRODUCTS)

    urls = [
        (
            f"  <url>\n"
            f"    <loc>{_STOREFRONT_DOMAIN}/</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            f"    <changefreq>weekly</changefreq>\n"
            f"    <priority>1.0</priority>\n"
            f"  </url>"
        )
    ]
    for category in get_all_categories():
        urls.append(
            f"  <url>\n"
            f"    <loc>{_STOREFRONT_DOMAIN}/category/{urllib.parse.quote(category)}</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            f"    <changefreq>weekly</changefreq>\n"
            f"    <priority>0.9</priority>\n"
            f"  </url>"
        )
    for p in products:
        pid = p.get("id") or p.get("product_id")
        if pid is None:
            continue
        lastmod = p.get("created_at") or today
        if isinstance(lastmod, str) and "T" in lastmod:
            lastmod = lastmod.split("T")[0]
        elif not isinstance(lastmod, str):
            lastmod = today
        slug = _generate_slug(p.get("caption") or "")
        urls.append(
            f"  <url>\n"
            f"    <loc>{_STOREFRONT_DOMAIN}/product/{pid}/{slug}</loc>\n"
            f"    <lastmod>{lastmod}</lastmod>\n"
            f"    <changefreq>monthly</changefreq>\n"
            f"    <priority>0.8</priority>\n"
            f"  </url>"
        )

    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>"
    )
    return Response(content=sitemap, media_type="application/xml")


def _serve_storefront_index(request: Request) -> HTMLResponse:
    """Render the main storefront index.html with default SEO metadata."""
    ctx = _build_seo_context(
        title="Top Shelf Cali Plug – Premium Cannabis Storefront",
        desc="Browse our premium cannabis catalog. Order directly via Telegram.",
        url=_STOREFRONT_DOMAIN,
    )
    ctx["request"] = request
    return templates.TemplateResponse(request, "index.html", context=ctx)


@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
async def serve_homepage(request: Request):
    """Serve the homepage with default Storefront SEO metadata."""
    return _serve_storefront_index(request)


@app.get("/webapp/index.html", response_class=HTMLResponse)
@app.get("/webapp/", response_class=HTMLResponse)
@app.get("/webapp", response_class=HTMLResponse)
async def serve_webapp(request: Request):
    """Serve the WebApp for Telegram Mini Apps.

    This renders the same index.html template as the homepage, but at the
    /webapp/index.html path that Telegram bots reference via WEB_APP_URL.
    Keeps backward compatibility with the original webapp path while the
    clearnet storefront is served at /.
    """
    return _serve_storefront_index(request)


@app.get("/product/{product_id}", response_class=HTMLResponse)
async def serve_product_page(product_id: int, request: Request):
    """Serve an individual product page with dynamic SEO metadata."""
    product = await _catalog_db.get_product(product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    # Build SEO fields from product data
    caption: str = product.get("caption") or ""
    # First line of caption as title, rest as description
    lines = [l.strip() for l in caption.splitlines() if l.strip()]
    seo_title = lines[0] if lines else f"Product #{product_id}"
    seo_desc = " ".join(lines[1:]) if len(lines) > 1 else seo_title

    # Prefer photo file_id represented as a media URL served by this server
    seo_image = f"{_STOREFRONT_DOMAIN}/api/media/{product_id}/0" if product.get("file_id") else ""

    product_url = f"{_STOREFRONT_DOMAIN}/product/{product_id}/{_generate_slug(caption)}"
    deep_link = f"https://t.me/{_STOREFRONT_BOT_USERNAME}?start=product_{product_id}"

    ctx = _build_seo_context(
        title=seo_title,
        desc=seo_desc,
        image=seo_image,
        url=product_url,
        product=product,
        deep_link=deep_link,
        og_type="product",
        keywords=", ".join(filter(None, [
            product.get("category", ""),
            product.get("subcategory", ""),
            " ".join(caption.split()[:6]),
            "Top Shelf Cali Plug",
            "buy online",
            "Telegram store",
        ])),
        category=product.get("category", ""),
    )
    ctx["request"] = request
    return templates.TemplateResponse(request, "index.html", context=ctx)


@app.get("/product/{product_id}/{slug}", response_class=HTMLResponse)
async def serve_product_page_with_slug(product_id: int, slug: str, request: Request):
    """Serve an individual product page using a slug URL — delegates to serve_product_page."""
    return await serve_product_page(product_id, request)


@app.get("/category/{category_name}", response_class=HTMLResponse)
async def serve_category_page(category_name: str, request: Request):
    """Serve a server-rendered category landing page for SEO crawlability."""
    # Case-insensitive match against canonical category names
    all_categories = get_all_categories()
    matched_category = next(
        (c for c in all_categories if c.lower() == category_name.lower()),
        None,
    )
    if matched_category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    display_name = get_category_display_name(matched_category)
    subcategories = get_subcategories(matched_category)

    # Fetch products for this category (limit 50 for the page)
    products = await _catalog_db.get_products_by_category(matched_category, limit=50)
    total = await _catalog_db.count_products_by_category(matched_category)

    # Get subcategory counts
    all_subcategories_data = await _catalog_db.get_all_subcategories_with_counts()
    subcategory_counts = {
        item["subcategory"]: item["count"]
        for item in all_subcategories_data.get(matched_category, [])
    }

    category_url = f"{_STOREFRONT_DOMAIN}/category/{urllib.parse.quote(matched_category)}"

    # Build keyword list from category + subcategory names
    subcat_names = ", ".join(subcategories) if subcategories else ""
    keywords = ", ".join(filter(None, [
        display_name,
        subcat_names,
        "Top Shelf Cali Plug",
        "buy online",
    ]))

    ctx = _build_seo_context(
        title=f"{display_name} | Top Shelf Cali Plug",
        desc=f"Browse our {display_name} collection. {total} products available. Order via Telegram.",
        url=category_url,
        keywords=keywords,
    )

    # Attach extra context for the category template
    ctx["request"] = request
    ctx["category_name"] = matched_category
    ctx["category_display"] = display_name
    ctx["subcategories"] = [
        {"name": s, "display": get_subcategory_display_name(s), "count": subcategory_counts.get(s, 0)}
        for s in subcategories
    ]
    ctx["products"] = [
        {
            "id": p.get("id") or p.get("product_id"),
            "caption": p.get("caption") or "",
            "category": p.get("category") or "",
            "subcategory": p.get("subcategory") or "",
            "subcategory_display": get_subcategory_display_name(p.get("subcategory")) if p.get("subcategory") else "",
            "slug": _generate_slug(p.get("caption") or ""),
        }
        for p in products
    ]
    ctx["total"] = total
    ctx["telegram_bot_url"] = f"https://t.me/{_STOREFRONT_BOT_USERNAME}"
    ctx["storefront_domain"] = _STOREFRONT_DOMAIN

    # Build ItemList + BreadcrumbList schema
    item_list_schema = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": display_name,
        "url": category_url,
        "numberOfItems": total,
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "url": f"{_STOREFRONT_DOMAIN}/product/{p['id']}/{p['slug']}",
                "name": (p["caption"].split("\n")[0].strip() if p["caption"] else f"Product #{p['id']}"),
            }
            for i, p in enumerate(ctx["products"])
        ],
    }
    breadcrumb_schema = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{_STOREFRONT_DOMAIN}/"},
            {"@type": "ListItem", "position": 2, "name": display_name, "item": category_url},
        ],
    }
    ctx["seo_schema"] = json.dumps({"@context": "https://schema.org", "@graph": [item_list_schema, breadcrumb_schema]})

    return templates.TemplateResponse(request, "category.html", context=ctx)


def _get_primary_bot(request: Request, required: bool = True):
    """
    Return the primary bot instance or raise a 503 if unavailable and required.

    Reads from the global _bot_applications list first (updated by reinitialize_bots),
    falling back to request.app.state.bot_apps only if the global list is empty.
    This ensures media delivery and other endpoints always use the freshest bot
    instance after a hot-reload via /reinitbots.

    Args:
        request: FastAPI request object
        required: If True, raises HTTPException when bot is not available. If False, returns None.

    Returns:
        Bot instance or None if not required and not available
    """
    global _bot_applications

    # Prioritize the global module-level list (updated by reinitialize_bots)
    if _bot_applications:
        for bot_app in _bot_applications:
            if bot_app is not None:
                return bot_app.bot

    # Fallback to request state if global list is empty
    bot_apps = getattr(request.app.state, "bot_apps", [])
    if not bot_apps:
        if required:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bot not initialized")
        return None
    for bot_app in bot_apps:
        if bot_app is not None:
            return bot_app.bot
    if required:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bot not initialized")
    return None


def _get_validated_init_data(request: Request, allow_preview: bool = False) -> tuple:
    """
    Validate Telegram WebApp initData from header or query params.
    
    Args:
        request: FastAPI request object
        allow_preview: If True, allows missing/invalid initData for preview mode
    
    Returns:
        Tuple of (validated initData string, bot_index that matched).
        In preview mode with no valid auth returns ("", -1).
    """
    init_data = request.headers.get("X-Telegram-Init-Data")
    
    # If not in header, try query params with robust extraction
    if not init_data:
        # Use urllib to parse raw query string to avoid FastAPI/Starlette auto-decoding quirks
        # that might break the hash validation
        if request.url.query:
            try:
                qs = urllib.parse.parse_qs(request.url.query)
                if "init_data" in qs:
                    init_data = qs["init_data"][0]
            except (ValueError, KeyError, IndexError) as e:
                logger.debug(f"Failed to parse init_data from raw query string: {e}")
        
        # Fallback to standard FastAPI extraction if manual parsing failed
        if not init_data:
            init_data = request.query_params.get("init_data")
    
    if not init_data:
        if allow_preview:
            logger.debug("No initData provided, allowing preview mode access")
            return "", -1
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing init data")
    
    # Validate using any configured bot token
    bot_tokens = Config.BOT_TOKENS
    if not bot_tokens:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bot token not configured")
    
    # Try to validate against each bot token
    for idx, token in enumerate(bot_tokens):
        if verify_init_data(init_data, token):
            if len(bot_tokens) > 1:
                logger.debug(f"InitData validated successfully with bot token {idx}")
            return init_data, idx

    # If none of the tokens validated successfully, handle the failure
    # Log a single warning with context about how many tokens were tried
    if allow_preview:
        logger.warning(
            "InitData validation failed for all %d configured bot token(s), allowing preview mode access",
            len(bot_tokens)
        )
        return "", -1

    logger.warning(
        "InitData validation failed: no match among %d configured bot token(s)",
        len(bot_tokens)
    )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid init data")


async def _check_user_not_blocked(init_data: str, client_ip: str = "") -> None:
    """
    Raise HTTP 403 if the user identified by init_data is blocked.
    No-op when init_data is empty (unauthenticated / preview mode).

    When client_ip is provided and the user is authenticated, the IP is
    recorded in user_ips so it can be blocked alongside the user account if an
    admin later issues a /block command.
    """
    if not init_data:
        return
    try:
        parsed = parse_init_data(init_data)
    except Exception:
        return
    user_data = parsed.get("user")
    if not isinstance(user_data, TelegramUser):
        return
    user_id = user_data.id
    if await _catalog_db.is_entity_blocked(
        telegram_user_id=user_id,
        username=user_data.username,
        ip_address=client_ip if client_ip and client_ip != "unknown" else None,
    ) or await _catalog_db.is_user_blocked(user_id):
        logger_security.warning(
            "[WEBAPP_BLOCKED] user_id=%d attempted WebApp API access while blocked",
            user_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    # Record IP → user mapping for future block enforcement
    if client_ip and client_ip != "unknown":
        try:
            await _catalog_db.record_user_ip(user_id, client_ip)
        except Exception as _e:
            logger.debug("Failed to record user IP: %s", _e)



def _collect_media_info(product: dict) -> List[dict]:
    """Collect media information (id and type) for a product."""
    product_id = product.get("id")
    if product_id is not None:
        cached = _media_info_cache.get(product_id)
        if cached is not None:
            _media_info_cache.move_to_end(product_id)
            return cached

    media_info: List[dict] = []
    
    # Add primary media
    primary_id = product.get("file_id")
    primary_type = product.get("file_type", "photo")
    if primary_id:
        media_info.append({"id": primary_id, "type": primary_type})
    
    # Add additional media
    additional_raw = product.get("additional_file_ids")
    if additional_raw:
        try:
            additional_ids = json.loads(additional_raw)
            if isinstance(additional_ids, list):
                for item in additional_ids:
                    if isinstance(item, str):
                        # Just file_id, assume photo
                        media_info.append({"id": item, "type": "photo"})
                    elif isinstance(item, (list, tuple)) and len(item) >= 2:
                        # (file_id, file_type) tuple
                        media_info.append({"id": item[0], "type": item[1]})
                    elif isinstance(item, (list, tuple)) and len(item) == 1:
                        # Just file_id in tuple
                        media_info.append({"id": item[0], "type": "photo"})
        except Exception:
            logger.warning("Failed to parse additional_file_ids for product %s", product.get("id"))

    if product_id is not None:
        _media_info_cache[product_id] = media_info
        if len(_media_info_cache) > _MEDIA_INFO_CACHE_MAX:
            _media_info_cache.popitem(last=False)
    
    return media_info


def _normalize_contact(contact: str) -> str:
    """Normalize @username contact for linking."""
    if not contact:
        return ""
    contact = contact.strip()
    return contact[1:] if contact.startswith("@") else contact


@app.get("/api/categories")
async def get_categories(request: Request, lang: str = "en"):
    """
    Get all categories with subcategories and product counts.
    Returns the hierarchical category structure matching the bot's menu.
    Supports translations via lang parameter.
    """
    _t0 = _time.monotonic()
    init_data, _ = _get_validated_init_data(request, allow_preview=True)
    await _check_user_not_blocked(init_data, get_client_ip(request))

    # Check in-memory result cache
    cache_key = f"categories:{lang}"
    cached_entry = _api_result_cache.get(cache_key)
    if cached_entry is not None:
        payload, ts = cached_entry
        if _time.monotonic() - ts < _API_RESULT_CACHE_TTL:
            _api_result_cache.move_to_end(cache_key)
            metrics.record("api_categories", (_time.monotonic() - _t0) * 1000)
            return Response(
                content=payload,
                media_type="application/json",
                headers={"Cache-Control": "public, max-age=60"}
            )
    
    # Get product counts for all categories
    category_counts = await _catalog_db.get_all_category_counts()
    
    # Get all subcategories with counts in a single query (optimization)
    all_subcategories = await _catalog_db.get_all_subcategories_with_counts()
    
    # Build category structure
    categories = []
    for category in get_all_categories():
        subcategories_list = get_subcategories(category)
        
        # Translate category display name
        # Translation keys follow pattern: "category_{category_name_lowercase}"
        # e.g., "category_cartridges", "category_edibles"
        category_display = get_category_display_name(category)
        if lang and lang != "en":
            try:
                # Try to get translation for category
                category_key = f"category_{category.lower()}"
                category_display = await get_translated_string_async(
                    category_key, 
                    lang,
                    default=category_display
                )
            except Exception as e:
                logger.debug(f"Could not translate category {category}: {e}")
                # Fall back to original display name
        
        category_data = {
            "name": category,
            "display_name": category_display,
            "count": category_counts.get(category, 0),
            "has_subcategories": len(subcategories_list) > 0,
            "subcategories": []
        }
        
        # Add subcategory counts if applicable
        if subcategories_list:
            subcategory_data = all_subcategories.get(category, [])
            count_dict = {item["subcategory"]: item["count"] for item in subcategory_data}
            
            for subcat in subcategories_list:
                # Use the same helper function that the bot uses for consistent translation
                subcat_display = get_subcategory_display_name(subcat, lang)
                
                category_data["subcategories"].append({
                    "name": subcat,
                    "display_name": subcat_display,
                    "count": count_dict.get(subcat, 0)
                })
        
        categories.append(category_data)
    
    # Add "All Products" count
    all_products_count = await _catalog_db.count_products_excluding_categories(EXCLUDED_FROM_ALL_PRODUCTS)
    
    payload = json.dumps({
        "categories": categories,
        "all_products_count": all_products_count
    })

    _api_result_cache_set(cache_key, payload)

    # Return with a short cache hint: categories change infrequently so clients
    # can reuse the response for up to 60 seconds before revalidating.
    metrics.record("api_categories", (_time.monotonic() - _t0) * 1000)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=60"}
    )


@app.get("/api/products")
async def get_products(
    request: Request, 
    category: str = None, 
    subcategory: str = None, 
    limit: int = 24, 
    offset: int = 0,
    lang: str = "en"
):
    """
    Get products filtered by category and/or subcategory.
    If no filters provided, returns all products (excluding EXCLUDED_FROM_ALL_PRODUCTS).
    Supports translations via lang parameter.
    """
    _t0 = _time.monotonic()
    init_data, _ = _get_validated_init_data(request, allow_preview=True)
    await _check_user_not_blocked(init_data, get_client_ip(request))
    bot = _get_primary_bot(request, required=False)
    
    # Bound limit for performance
    limit = max(1, min(limit, 50))
    offset = max(0, offset)

    # Check in-memory result cache (keyed by all request params including lang)
    cache_key = f"products:{category}:{subcategory}:{limit}:{offset}:{lang}"
    cached_entry = _api_result_cache.get(cache_key)
    if cached_entry is not None:
        payload, ts = cached_entry
        if _time.monotonic() - ts < _API_RESULT_CACHE_TTL:
            _api_result_cache.move_to_end(cache_key)
            metrics.record("api_products", (_time.monotonic() - _t0) * 1000)
            return Response(
                content=payload,
                media_type="application/json",
                headers={"Cache-Control": "public, max-age=30"}
            )
    
    # Fetch products based on filters
    if category and subcategory:
        products = await _catalog_db.get_products_by_category_and_subcategory(
            category, subcategory, limit=limit, offset=offset
        )
        total = await _catalog_db.count_products_by_category_and_subcategory(category, subcategory)
    elif category:
        products = await _catalog_db.get_products_by_category(category, limit=limit, offset=offset)
        total = await _catalog_db.count_products_by_category(category)
    else:
        # All products (excluding certain categories)
        products = await _catalog_db.get_all_products_excluding_categories(
            EXCLUDED_FROM_ALL_PRODUCTS, limit=limit, offset=offset
        )
        total = await _catalog_db.count_products_excluding_categories(EXCLUDED_FROM_ALL_PRODUCTS)

    order_contact = await _catalog_db.get_order_contact()
    order_contact_username = _normalize_contact(order_contact)
    order_link = await _catalog_db.get_order_link()

    # Batch-translate all captions in a single pass to reduce overhead
    captions = [p.get("caption") or "" for p in products]
    if lang and lang != "en":
        try:
            captions = await translation_service.translate_batch(captions, lang, source_lang="en")
        except Exception as e:
            logger.debug(f"Batch caption translation failed: {e}")
            # Fall back to original captions

    items = []
    for product, caption in zip(products, captions):
        media_info = _collect_media_info(product)
        items.append({
            "id": product.get("id"),
            "caption": caption,
            "category": product.get("category") or "",
            "subcategory": product.get("subcategory") or "",
            "subcategory_display": get_subcategory_display_name(product.get("subcategory"), lang) if product.get("subcategory") else "",
            "created_at": product.get("created_at"),
            "media_count": len(media_info),
            "media_info": media_info,  # Include full media info with types
        })
    
    payload = json.dumps({
        "items": items,
        "total": total,
        "bot": bot.username if bot and getattr(bot, "username", None) else None,
        "order_contact": order_contact,
        "order_contact_username": order_contact_username,
        "order_link": order_link
    })

    _api_result_cache_set(cache_key, payload)

    metrics.record("api_products", (_time.monotonic() - _t0) * 1000)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=30"}
    )


@app.get("/api/products/{product_id}")
async def get_product_detail(request: Request, product_id: int, lang: str = "en"):
    """Return JSON details for a single product by ID.

    Used by the webapp to load a product from a deep link (e.g. when the user
    opens the bot via ``?start=product_<id>`` or taps "View in Web App").
    """
    init_data, _ = _get_validated_init_data(request, allow_preview=True)
    await _check_user_not_blocked(init_data, get_client_ip(request))
    bot = _get_primary_bot(request, required=False)

    product = await _catalog_db.get_product(product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    caption = product.get("caption") or ""
    if lang and lang != "en":
        try:
            caption = await translation_service.translate(caption, lang, source_lang="en")
        except Exception:
            pass  # Fall back to original caption

    media_info = _collect_media_info(product)
    order_contact = await _catalog_db.get_order_contact()
    order_link = await _catalog_db.get_order_link()

    storefront_url = (
        f"{_STOREFRONT_DOMAIN}/product/{product_id}"
        if _STOREFRONT_DOMAIN
        else None
    )
    bot_username = _STOREFRONT_BOT_USERNAME or (bot.username if bot else None) or ""
    bot_deep_link = f"https://t.me/{bot_username}?start=product_{product_id}" if bot_username else None

    payload = json.dumps({
        "id": product.get("id"),
        "caption": caption,
        "category": product.get("category") or "",
        "subcategory": product.get("subcategory") or "",
        "subcategory_display": get_subcategory_display_name(product.get("subcategory"), lang) if product.get("subcategory") else "",
        "created_at": product.get("created_at"),
        "media_count": len(media_info),
        "media_info": media_info,
        "bot": bot.username if bot and getattr(bot, "username", None) else None,
        "order_contact": order_contact,
        "order_link": order_link,
        "storefront_url": storefront_url,
        "bot_deep_link": bot_deep_link,
    })

    return Response(
        content=payload,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=30"},
    )


@app.get("/api/catalog/{product_id}/media/{media_index}")
async def stream_catalog_media(request: Request, product_id: int, media_index: int):
    """
    Stream a single media file for a product without exposing the bot token.
    Uses file caching and efficient streaming to optimize performance.
    Returns metadata for files that cannot be streamed (too large).

    Args:
        product_id: Product ID
        media_index: Index of media in product's media array
    """
    init_data, _ = _get_validated_init_data(request, allow_preview=True)
    await _check_user_not_blocked(init_data, get_client_ip(request))
    bot = _get_primary_bot(request)

    # Define size limits
    WEB_STREAMING_LIMIT = 50 * 1024 * 1024  # 50MB - absolute max

    product = await _catalog_db.get_product(product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    media_info = _collect_media_info(product)
    if media_index < 0 or media_index >= len(media_info):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")

    media_type_info = media_info[media_index]
    file_id = media_type_info["id"]
    is_video = media_type_info.get('type') == 'video'

    # Resolve bot-specific file_id for multi-bot scenarios
    # This ensures the file_id works with the current bot instance even if
    # the file was originally indexed by a different bot
    source_chat_id = product.get('chat_id')
    source_message_id = product.get('message_id')

    # For additional media in media groups, we need to get the correct message_id
    if media_index > 0:
        additional_message_ids_json = product.get('additional_message_ids')
        if additional_message_ids_json:
            try:
                additional_message_ids = json.loads(additional_message_ids_json)
                if media_index - 1 < len(additional_message_ids):
                    source_message_id = additional_message_ids[media_index - 1]
            except (json.JSONDecodeError, TypeError, IndexError) as e:
                logger.warning(
                    f"Failed to parse additional_message_ids JSON for product {product_id}, "
                    f"media_index {media_index}. Data: {additional_message_ids_json}. Error: {e}"
                )

    if source_chat_id and source_message_id:
        try:
            # Try to get bot-specific file_id
            file_type = media_type_info.get('type', 'document')
            bot_username = bot.username or ""
            _res_key = (source_chat_id, source_message_id, 0, bot_username)
            # Check lightweight in-memory cache first to avoid DB lookup on repeated
            # Range 206 requests for the same media item.
            bot_specific_file_id = _resolved_file_id_cache.get(_res_key)
            if bot_specific_file_id is None:
                bot_specific_file_id = await get_bot_specific_file_id(
                    bot,
                    source_chat_id,
                    source_message_id,
                    file_type,
                    file_index=0  # file_index within this specific message (always 0 since each Telegram message contains exactly one media item)
                )
                if bot_specific_file_id:
                    _cache_resolved_file_id(_res_key, bot_specific_file_id)

            if bot_specific_file_id:
                logger.debug(
                    f"Using bot-specific file_id for product {product_id}, media {media_index}: "
                    f"original={file_id[:20]}..., bot_specific={bot_specific_file_id[:20]}..."
                )
                file_id = bot_specific_file_id
            else:
                logger.debug(
                    f"Bot-specific file_id not available for product {product_id}, media {media_index}, "
                    f"using original file_id"
                )
        except Exception as e:
            logger.warning(
                f"Error getting bot-specific file_id for product {product_id}, media {media_index}: {e}. "
                f"Falling back to original file_id"
            )
    else:
        logger.debug(
            f"Product {product_id} missing chat_id or message_id, cannot resolve bot-specific file_id"
        )

    # Structured logging for diagnostics
    logger.debug(
        f"Media request: product_id={product_id}, media_index={media_index}, "
        f"is_video={is_video}, file_id={file_id[:20]}..."
    )

    media_cache_key = hashlib.sha256(file_id.encode()).hexdigest()
    # Check cache first for tiny reusable non-video media only.
    cached_file_path = None if (is_video or not MEDIA_CACHE_ENABLED) else await _media_cache.get(file_id)
    file_path = None
    file_size = 0
    temp_file_to_cleanup: Optional[Path] = None

    if cached_file_path:
        age, idle, active = await _mark_media_access(cached_file_path)
        logger.info(
            "[media_cache] cache_hit layer=disk product_id=%s media_index=%s age=%.1f idle=%.1f active_streams=%d",
            product_id,
            media_index,
            age,
            idle,
            active,
        )
        file_path = cached_file_path
        file_size = file_path.stat().st_size
    else:
        logger.info(
            "[media_cache] cache_miss layer=disk product_id=%s media_index=%s is_video=%s enabled=%s",
            product_id,
            media_index,
            is_video,
            MEDIA_CACHE_ENABLED,
        )
        # Not in cache, need to download from Telegram
        try:
            # Try to get file info - this can fail for files >20MB
            try:
                tg_file = await bot.get_file(file_id)
                file_size = tg_file.file_size or 0
                file_unique_id = getattr(tg_file, "file_unique_id", None)
                if file_unique_id:
                    bot_identifier = (getattr(bot, "username", None) or f"bot_{0}").lower()
                    await _catalog_db.upsert_canonical_media(
                        product_id=str(product_id),
                        media_type=media_type_info.get("type", "document"),
                        file_unique_id=file_unique_id,
                        origin_chat_id=source_chat_id,
                        origin_message_id=source_message_id,
                        original_file_id=file_id,
                        file_size=file_size,
                    )
                    await _catalog_db.upsert_bot_media_file_id(bot_identifier, file_unique_id, file_id)

                # Log file size retrieved
                logger.debug(
                    f"File info retrieved: product_id={product_id}, media_index={media_index}, "
                    f"file_id={file_id[:20]}..., file_size={file_size} bytes "
                    f"({file_size / (1024*1024):.2f} MB)"
                )
            except telegram.error.BadRequest as e:
                if "file is too big" in str(e).lower():
                    # File >20MB - cannot use getFile API
                    logger.warning(
                        f"File too large for getFile API "
                        f"(product {product_id}, media {media_index})"
                    )
                    return Response(
                        content=json.dumps({
                            "error": "file_too_large",
                            "message": "File exceeds Telegram Bot API limit.",
                            "can_download": False
                        }),
                        media_type="application/json",
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                    )
                else:
                    # Re-raise other BadRequest errors
                    raise

            # If file is too large for web streaming, return error
            if file_size > WEB_STREAMING_LIMIT:
                logger.warning(
                    "File too large for web streaming - product %s, media %s, size %s bytes",
                    product_id, media_index, file_size
                )
                return Response(
                    content=json.dumps({
                        "error": "file_too_large",
                        "file_size": file_size,
                        "message": "File is too large to stream. Please request it in the bot chat.",
                        "can_download": False
                    }),
                    media_type="application/json",
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                )

            # Download the file via Bot API to an app-owned bounded temp location.
            suffix = ".mp4" if is_video else Path(urllib.parse.urlparse(getattr(tg_file, "file_path", "") or "").path).suffix
            temp_input_path = _media_temp_path(file_id, suffix)
            generation_lock = await _get_media_generation_lock(media_cache_key)
            async with generation_lock:
                # A concurrent request may have completed the Telegram download while
                # this request was waiting. Re-check before generating a new temp file.
                ensure_resource_dirs()
                if temp_input_path.exists():
                    stat = temp_input_path.stat()
                    last_access = _media_last_access.get(temp_input_path, stat.st_mtime)
                    age = _time.time() - stat.st_mtime
                    idle = _time.time() - last_access
                    if idle <= MEDIA_CACHE_TTL_SECONDS:
                        file_path = temp_input_path
                        file_size = stat.st_size
                        age, idle, active = await _mark_media_access(temp_input_path)
                        logger.info(
                            "[media_cache] cache_reused layer=temp product_id=%s media_index=%s bytes=%d age=%.1f idle=%.1f active_streams=%d",
                            product_id,
                            media_index,
                            file_size,
                            age,
                            idle,
                            active,
                        )
                    else:
                        logger.info(
                            "[media_cache] cache_expired layer=temp product_id=%s media_index=%s age=%.1f idle=%.1f ttl=%d",
                            product_id,
                            media_index,
                            age,
                            idle,
                            MEDIA_CACHE_TTL_SECONDS,
                        )

                if file_path is None:
                    tmp_usage = get_tmp_usage()
                    if tmp_usage["used_bytes"] >= TMP_WARNING_BYTES:
                        await _cleanup_media_temp_files()
                        emergency_cleanup(skip_file=_is_media_temp_in_use)
                    logger.info(
                        "[media_cache] generation_started product_id=%s media_index=%s is_video=%s",
                        product_id,
                        media_index,
                        is_video,
                    )
                    await tg_file.download_to_drive(custom_path=temp_input_path)
                    await _mark_media_access(temp_input_path)
                    logger.info(
                        "[media_cache] generation_completed path=%s product_id=%s media_index=%s bytes=%d",
                        temp_input_path.name,
                        product_id,
                        media_index,
                        temp_input_path.stat().st_size,
                    )

                    if is_video:
                        file_path = temp_input_path
                        temp_file_to_cleanup = temp_input_path
                    else:
                        cached_path = await _media_cache.set(
                            file_id,
                            temp_input_path,
                            allow=MEDIA_CACHE_ENABLED and temp_input_path.stat().st_size <= MEDIA_CACHE_MAX_FILE_BYTES,
                        )
                        file_path = cached_path or temp_input_path
                        temp_file_to_cleanup = temp_input_path
                        if cached_path:
                            await _mark_media_access(cached_path)
                            logger.info("[media_cache] cache_reused layer=disk product_id=%s media_index=%s", product_id, media_index)

        except telegram.error.BadRequest as e:
            # Cleanup partial temp file only when generation failed.
            if 'temp_input_path' in locals() and temp_input_path:
                _cleanup_path(temp_input_path)
            if "File is too big" not in str(e):
                logger.exception("Unable to fetch file for product %s: %s", product_id, e)
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Media unavailable")
            # If it's "File is too big", it should have been handled above
            raise
        except Exception as e:
            logger.exception("Unable to fetch file for product %s: %s", product_id, e)
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Media unavailable")

    # Determine media type
    media_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    # For videos, ensure correct content type for browser compatibility
    if is_video and media_type == "application/octet-stream":
        media_type = "video/mp4"

    content_length = file_size if file_size > 0 else file_path.stat().st_size

    # Final log: summarize what we're serving
    logger.debug(
        f"Serving media: product_id={product_id}, media_index={media_index}, "
        f"is_video={is_video}, file_size={content_length} bytes "
        f"({content_length / (1024*1024):.2f} MB), media_type={media_type}"
    )

    # Handle HTTP Range requests for proper video seeking/streaming
    range_header = request.headers.get("range")
    if range_header:
        try:
            # Parse range header (e.g., "bytes=0-1024")
            range_spec = range_header.replace("bytes=", "")
            range_parts = range_spec.split("-")
            start = int(range_parts[0]) if range_parts[0] else 0
            end = int(range_parts[1]) if range_parts[1] else content_length - 1
            end = min(end, content_length - 1)

            # Stream the requested range efficiently
            async def stream_range():
                active = await _stream_started(file_path)
                logger.info(
                    "[media_cache] active_streams path=%s count=%d range=%s-%s",
                    file_path.name,
                    active,
                    start,
                    end,
                )
                try:
                    with open(file_path, 'rb') as f:
                        f.seek(start)
                        remaining = end - start + 1
                        chunk_size = 8192
                        while remaining > 0:
                            chunk = f.read(min(chunk_size, remaining))
                            if not chunk:
                                break
                            yield chunk
                            remaining -= len(chunk)
                finally:
                    remaining_streams = await _stream_finished(file_path)
                    logger.info(
                        "[media_cache] active_streams path=%s count=%d event=finished",
                        file_path.name,
                        remaining_streams,
                    )

            return StreamingResponse(
                stream_range(),
                status_code=206,
                media_type=media_type,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{content_length}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(end - start + 1),
                    "Cache-Control": "public, max-age=3600"  # Extended cache for better performance
                }
            )
        except (ValueError, IndexError) as e:
            logger.warning(f"Invalid range header: {range_header}, error: {e}")
            pass  # Fall through to full response

    # Stream the full file efficiently (no loading into memory)
    async def stream_file():
        active = await _stream_started(file_path)
        logger.info("[media_cache] active_streams path=%s count=%d range=full", file_path.name, active)
        try:
            with open(file_path, 'rb') as f:
                while chunk := f.read(8192):
                    yield chunk
        finally:
            remaining_streams = await _stream_finished(file_path)
            logger.info(
                "[media_cache] active_streams path=%s count=%d event=finished",
                file_path.name,
                remaining_streams,
            )

    return StreamingResponse(
        stream_file(),
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=3600",  # Extended cache for better performance
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length)
        }
    )


@app.get("/api/search")
async def search_products(
    request: Request,
    q: str = "",
    limit: int = 24,
    offset: int = 0
):
    """
    Search products by query string (searches caption, category, and subcategory).
    Excludes products from Dated Proofs, Client Touchdowns, and Announcements categories.
    Optimized server-side search to reduce client-side processing.
    """
    init_data, _ = _get_validated_init_data(request, allow_preview=True)
    await _check_user_not_blocked(init_data, get_client_ip(request))
    
    query = q.strip()
    if not query or len(query) < 2:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Query must be at least 2 characters")
    
    # Bound limit for performance
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    try:
        all_products = await _catalog_db.get_all_products_for_search()
        matched_products = fuzzy_search_products(all_products, query, score_cutoff=75)
        total = len(matched_products)
        products = matched_products[offset:offset + limit]

        if total == 0:
            normalized_query = normalize_text(query)
            field_matches = {
                "caption": 0,
                "category": 0,
                "subcategory": 0,
            }
            for product in all_products:
                if normalized_query and normalized_query in normalize_text(product.get("caption", "") or ""):
                    field_matches["caption"] += 1
                if normalized_query and normalized_query in normalize_text(product.get("category", "") or ""):
                    field_matches["category"] += 1
                if normalized_query and normalized_query in normalize_text(product.get("subcategory", "") or ""):
                    field_matches["subcategory"] += 1
            logger.info(
                "[webapp_search_empty] query=%r normalized=%r total_indexed=%d matched_fields=%s",
                query,
                normalized_query,
                len(all_products),
                field_matches,
            )

        order_contact = await _catalog_db.get_order_contact()
        order_contact_username = _normalize_contact(order_contact)
        
        items = []
        for product in products:
            media_info = _collect_media_info(product)
            items.append({
                "id": product.get("id"),
                "caption": product.get("caption") or "",
                "category": product.get("category") or "",
                "subcategory": product.get("subcategory") or "",
                "subcategory_display": get_subcategory_display_name(product.get("subcategory")) if product.get("subcategory") else "",
                "created_at": product.get("created_at"),
                "media_count": len(media_info),
                "media_info": media_info,
            })
        
        return {
            "items": items,
            "total": total,
            "query": q,
            "order_contact": order_contact,
            "order_contact_username": order_contact_username
        }
        
    except Exception as e:
        logger.exception(f"Search failed for query '{q}': {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Search failed")

@app.get("/api/user/subscription")
async def get_user_subscription(request: Request, user_id: int):
    """Get user subscription status."""
    _get_validated_init_data(request, allow_preview=False)  # Require auth for user data
    
    try:
        result = await _catalog_db.get_user_subscription(user_id)
        # User not in database → default to subscribed
        return {"is_subscribed": result if result is not None else True}
    except Exception as e:
        logger.exception(f"Error getting user subscription: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get subscription status")


@app.post("/api/user/subscription")
async def update_user_subscription(request: Request):
    """Update user subscription status."""
    _get_validated_init_data(request, allow_preview=False)  # Require auth for user data
    
    try:
        data = await request.json()
        user_id = data.get("user_id")
        is_subscribed = data.get("is_subscribed", True)
        
        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id required")
        
        await _catalog_db.update_user_subscription(int(user_id), bool(is_subscribed))
        logger.info(f"User {user_id} subscription updated to: {is_subscribed}")
        return {"success": True, "is_subscribed": is_subscribed}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error updating user subscription: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update subscription")


@app.post("/api/webapp-logs")
async def webapp_logs(request: Request):
    """
    Accept and log webapp events for diagnostics and analytics.
    Logs include user interactions, API calls, video playback events, and errors.
    """
    try:
        # Allow preview mode for logging (doesn't require full auth)
        _get_validated_init_data(request, allow_preview=True)
        
        data = await request.json()
        logs = data.get("logs", [])
        
        if not logs:
            return {"success": True, "message": "No logs to process"}
        
        # Log each event with structured format
        for log_entry in logs:
            timestamp = log_entry.get("timestamp", "")
            user_id = log_entry.get("user_id", "unknown")
            username = log_entry.get("username", "unknown")
            event_type = log_entry.get("event_type", "unknown")
            action = log_entry.get("action", "")
            status = log_entry.get("status", "")
            error = log_entry.get("error", "")
            details = log_entry.get("details", {})
            
            # Format log message with full context
            log_msg = f"[WebApp] user_id={user_id} username={username} event={event_type} action={action} status={status}"
            if error:
                log_msg += f" error={error}"
            if details:
                log_msg += f" details={json.dumps(details)}"
            
            # Log at appropriate level based on status
            if status == "error" or error:
                logger.error(log_msg)
            elif status == "warning":
                logger.warning(log_msg)
            else:
                logger.info(log_msg)
        
        return {"success": True, "processed": len(logs)}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error processing webapp logs: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to process logs")


@app.get("/api/user/info")
async def get_user_info(request: Request):
    """
    Get user information from Telegram initData.
    This endpoint extracts user_id and other info from the initData header,
    providing a reliable way for the webapp to get user details even when
    tg.initDataUnsafe is not available in the frontend.
    """
    try:
        # Allow preview mode but prefer authenticated data
        init_data, _ = _get_validated_init_data(request, allow_preview=True)
        
        if not init_data:
            # No initData available
            return {
                "authenticated": False,
                "user_id": None,
                "username": None,
                "first_name": None,
                "last_name": None
            }
        
        # Parse user info from initData
        parsed_data = parse_init_data(init_data)
        user_data = parsed_data.get("user")

        if not isinstance(user_data, TelegramUser):
            return {
                "authenticated": False,
                "user_id": None,
                "username": None,
                "first_name": None,
                "last_name": None
            }

        # Extract user information
        user_id = user_data.id
        username = user_data.username
        first_name = user_data.first_name
        last_name = user_data.last_name
        language_code = user_data.language_code
        
        return {
            "authenticated": True,
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "language_code": language_code
        }
    except Exception as e:
        logger.exception(f"Error getting user info: {e}")
        # Return unauthenticated response instead of error
        return {
            "authenticated": False,
            "user_id": None,
            "username": None,
            "first_name": None,
            "last_name": None
        }


@app.post("/api/user/track")
async def track_webapp_user(request: Request):
    """
    Track a webapp user in the bot_users database.
    Called when the webapp opens to ensure user is registered in the bot's
    user database for analytics and tracking purposes.
    """
    try:
        init_data, bot_index = _get_validated_init_data(request, allow_preview=True)
        
        data = await request.json()
        user_id = data.get("user_id")
        username = data.get("username")
        first_name = data.get("first_name")
        last_name = data.get("last_name")
        
        # Try to extract user info from initData if not in request body
        if not user_id and init_data:
            parsed_data = parse_init_data(init_data)
            user_data = parsed_data.get("user")
            if isinstance(user_data, TelegramUser):
                user_id = user_data.id
                username = username or user_data.username
                first_name = first_name or user_data.first_name
                last_name = last_name or user_data.last_name
        
        if not user_id:
            return {"success": False, "message": "No user_id available"}
        
        # Reject blocked users — do not re-register them in the database
        if await _catalog_db.is_entity_blocked(
            telegram_user_id=int(user_id),
            username=username,
            ip_address=get_client_ip(request),
        ) or await _catalog_db.is_user_blocked(int(user_id)):
            logger_security.warning(
                "[WEBAPP_BLOCKED] user_id=%s attempted /api/user/track while blocked",
                user_id,
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

        # Track user in the bot_users database, using the bot that validated the request
        bot_apps = getattr(request.app.state, "bot_apps", [])
        bot_username = None
        if bot_apps:
            if 0 <= bot_index < len(bot_apps):
                # Use the specific bot that validated this request
                bot_app = bot_apps[bot_index]
            else:
                # bot_index == -1: preview/unauthenticated mode, fall back to primary bot
                bot_app = next((candidate for candidate in bot_apps if candidate is not None), None)
            if hasattr(bot_app, 'bot') and hasattr(bot_app.bot, 'username'):
                bot_username = bot_app.bot.username
        
        await _catalog_db.track_user(
            user_id=int(user_id),
            username=username,
            first_name=first_name,
            last_name=last_name,
            bot_username=bot_username
        )

        # Record the client IP so it can be blocked alongside the account later
        client_ip = get_client_ip(request)
        if client_ip and client_ip != "unknown":
            try:
                await _catalog_db.record_user_ip(int(user_id), client_ip)
            except Exception as _ip_err:
                logger.debug("Failed to record user IP in /api/user/track: %s", _ip_err)
        
        logger.info(f"[WebApp] Tracked user {user_id} (username={username}) in bot_users database")
        
        return {
            "success": True,
            "user_id": user_id,
            "tracked": True
        }
        
    except Exception as e:
        logger.exception(f"Error tracking webapp user: {e}")
        return {"success": False, "message": "Failed to track user"}


async def process_webhook_request(request: Request, bot_index: int):
    """Process an incoming Telegram webhook update for the given bot index.

    Shared implementation used by both the /webhook and /webhook/{bot_index} endpoints
    to eliminate duplicated parsing, logging, and error-handling logic.

    Args:
        request: The incoming FastAPI Request object.
        bot_index: Index into _bot_applications for the target bot.

    Returns:
        A fastapi.Response with an appropriate HTTP status code.
    """
    # --- Unique request ID for log correlation across the processing pipeline ---
    request_id = secrets.token_hex(8)

    # --- Resolve real client IP ---
    # When the server sits behind a reverse proxy or CDN (common with custom domains),
    # request.client.host is the proxy's IP.  Use X-Forwarded-For / X-Real-IP to
    # recover the originating IP so each real caller gets its own rate-limit bucket.
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For may be a comma-separated list; the leftmost entry is the client
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = (
            request.headers.get("X-Real-IP")
            or (request.client.host if request.client else "unknown")
        )

    # --- Rate limiting: skip for Telegram IPs, apply for everyone else ---
    # Telegram-originating IPs are whitelisted and never rate-limited on the
    # webhook path — they are already authenticated by the secret-token check below.
    # Requests from non-Telegram IPs (including scanners and curious humans) are
    # subject to the per-IP rate limiter to limit abuse.
    from_telegram = is_telegram_ip(client_ip)
    if not from_telegram:
        rate_limiter: Optional[WebhookIPRateLimiter] = getattr(
            request.app.state, "webhook_rate_limiter", None
        )
        if rate_limiter is not None:
            if not await rate_limiter.is_allowed(client_ip):
                logger.warning(
                    "[%s] Rate limit exceeded for non-Telegram IP %s on /webhook bot %d",
                    request_id,
                    client_ip,
                    bot_index,
                )
                return Response(status_code=status.HTTP_429_TOO_MANY_REQUESTS)

    # --- Secret token verification ---
    # Use _get_expected_secret_token() so that configs/bots.json overrides are
    # applied (hot-reloadable every 30 s) before falling back to runtime tokens.
    runtime_tokens: List[Optional[str]] = list(_webhook_secret_tokens)
    expected_token: Optional[str] = _get_expected_secret_token(bot_index, runtime_tokens)

    if expected_token:
        incoming_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        malformed_header = incoming_token is not None and not isinstance(incoming_token, str)
        # compare_digest requires both operands to be the same type; guard against
        # missing / non-string values before encoding to avoid TypeErrors.
        token_valid = isinstance(incoming_token, str) and secrets.compare_digest(
            incoming_token.encode(), expected_token.encode()
        )
        # Tolerance window: also accept the previous token for this bot index so
        # in-flight requests signed with the old secret during a reinitialisation
        # are not immediately rejected.  History is in-memory only and expires on
        # restart, so this does not weaken long-term security.
        if not token_valid:
            prev_entry: Optional[List[Optional[str]]] = _webhook_secret_history.get(bot_index)
            prev_token: Optional[str] = prev_entry[0] if (prev_entry and len(prev_entry) > 0) else None
            if prev_token and isinstance(incoming_token, str):
                token_valid = secrets.compare_digest(
                    incoming_token.encode(), prev_token.encode()
                )
                if token_valid:
                    logger.debug(
                        "bot=%d accepted request using previous webhook secret (tolerance window)",
                        bot_index,
                    )
        if not token_valid:
            # Log a header-level summary for easy searching without exposing the token.
            # Tag: [WEBHOOK_401] — grep-friendly for alerting pipelines.
            header_present = isinstance(incoming_token, str)
            header_length = len(incoming_token) if header_present else 0
            if incoming_token is None:
                reason = "missing_secret"
            elif malformed_header:
                reason = "malformed_header"
            else:
                reason = "incorrect_secret"
            logger_security.warning(
                "[WEBHOOK_401] req=%s bot=%d ip=%s telegram_ip=%s "
                "header_present=%s header_length=%d "
                "reason=%s expected_fp=%s incoming_fp=%s",
                request_id,
                bot_index,
                client_ip,
                from_telegram,
                header_present,
                header_length,
                reason,
                _secret_fingerprint(expected_token),
                _secret_fingerprint(incoming_token if header_present else None),
            )
            # Increment per-process 401 counter for observability / alerting.
            global _webhook_401_counter
            _webhook_401_counter += 1
            metrics.record("webhook_401", 0)
            return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    else:
        logger.debug(
            "No secret token configured for bot %d; skipping header verification.",
            bot_index,
        )

    if not any(_bot_applications):
        logger.error("[%s] Bot applications not initialized", request_id)
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    if bot_index >= len(_bot_applications):
        logger.error(
            "[%s] Invalid bot index: %d. Available bots: 0-%d",
            request_id,
            bot_index,
            len(_bot_applications) - 1,
        )
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if _bot_applications[bot_index] is None:
        logger.warning("[%s] Bot index %d is disabled due to startup failure", request_id, bot_index)
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    _t0 = _time.monotonic()
    try:
        json_data = await request.json()
        bot_app = _bot_applications[bot_index]
        update = Update.de_json(json_data, bot_app.bot)

        if update.message:
            logger.info(
                "[%s] Bot %d: Processing message update from user %s",
                request_id, bot_index, update.message.from_user.id,
            )
        elif update.callback_query:
            user_id = update.callback_query.from_user.id
            callback_data = update.callback_query.data or ""
            logger.info(
                "[%s] Bot %d: Processing callback query from user %s: %s",
                request_id, bot_index, user_id, callback_data,
            )
            # --- Request deduplication for callback queries ---
            # Prevents accidental double-processing of the same callback when
            # Telegram retries or the user taps a button twice in quick succession.
            deduplicator: Optional[RequestDeduplicator] = getattr(
                request.app.state, "request_deduplicator", None
            )
            if deduplicator is not None and await deduplicator.is_duplicate(
                bot_index, user_id, callback_data
            ):
                logger.info(
                    "[%s] Bot %d: Duplicate callback query suppressed (user=%s data=%r)",
                    request_id, bot_index, user_id, callback_data,
                )
                return Response(status_code=status.HTTP_200_OK)
        elif update.channel_post:
            logger.info(
                "[%s] Bot %d: Processing channel post from chat %s",
                request_id, bot_index, update.channel_post.chat.id,
            )
        else:
            logger.info(
                "[%s] Bot %d: Processing update type: %s",
                request_id, bot_index, update.update_id,
            )

        await bot_app.process_update(update)

        duration_ms = (_time.monotonic() - _t0) * 1000
        record_webhook_result(bot_index=bot_index, success=True, duration_ms=duration_ms)
        logger.info("[%s] Bot %d: Webhook request processed successfully", request_id, bot_index)
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        duration_ms = (_time.monotonic() - _t0) * 1000
        record_webhook_result(bot_index=bot_index, success=False, duration_ms=duration_ms)
        logger.error(
            "[%s] Error processing webhook update for bot %d: %s",
            request_id, bot_index, e, exc_info=True,
        )
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@app.post("/webhook")
async def webhook(request: Request):
    """Handle incoming webhook updates from Telegram for the primary bot (bot 0)."""
    logger.info("Received webhook request for primary bot")
    return await process_webhook_request(request, 0)


@app.post("/webhook/{bot_index}")
async def webhook_indexed(request: Request, bot_index: int):
    """Handle incoming webhook updates from Telegram for additional bot instances.
    
    Note: The primary bot (bot 0) is accessed via /webhook, not /webhook/0.
    This endpoint handles bots 1, 2, 3, etc.
    """
    logger.info(f"Received webhook request for bot {bot_index}")
    if bot_index == 0:
        logger.warning("Bot 0 should use /webhook endpoint, not /webhook/0. Redirecting...")
    return await process_webhook_request(request, bot_index)


@app.get("/health")
@app.head("/health")
async def health():
    """Health check endpoint for deployment platforms."""
    return {"status": "healthy"}


@app.get("/_internal/metrics")
async def internal_metrics():
    """Return per-endpoint latency metrics (count, avg_ms, p95_ms).

    This endpoint is intended for internal monitoring and alerting only.
    The data is per-process and best-effort in multi-worker deployments.

    Example response::

        {
            "uptime_seconds": 3600.5,
            "webhook_401_total": 3,
            "endpoints": {
                "api_products": {"count": 120, "avg_ms": 245.3, "p95_ms": 498.7},
                "api_categories": {"count": 45, "avg_ms": 180.1, "p95_ms": 390.2},
                "webhook_401": {"count": 3, "avg_ms": 0.0, "p95_ms": 0.0}
            },
            "webhook_monitor": {
                "uptime_seconds": 3600.5,
                "bots": {
                    "0": {"success": 100, "failure": 2, "success_rate": 0.98, ...}
                }
            }
        }
    """
    snap = metrics.snapshot()
    snap["webhook_401_total"] = _webhook_401_counter
    snap["webhook_monitor"] = _webhook_monitor.snapshot()
    return snap


if __name__ == "__main__":
    import uvicorn
    
    # Get port from environment or default to 8000
    port = int(os.getenv("PORT", "8000"))
    
    logger.info(f"Starting webhook server on port {port}")
    
    # Run the server with access logs enabled
    uvicorn.run(
        "webhook_server:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True  # Explicitly enable access logs
    )
