"""
Webhook server for Telegram bot using FastAPI and uvicorn.
This allows the bot to receive updates via webhooks instead of polling,
which is more suitable for serverless deployments like Render.com.
"""
import asyncio
import hashlib
import html
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import time as _time
import urllib.parse
import weakref
from collections import OrderedDict
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse, HTMLResponse, RedirectResponse, JSONResponse
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
from utils.helpers import (
    get_channel_id,
    get_channel_username,
    get_file_id_cache_size,
    get_bot_specific_file_id,
    is_admin,
    is_transient_telegram_error,
    register_bot_handlers,
)
from utils.categories import get_all_categories, get_category_display_name, get_subcategory_display_name, get_subcategories, EXCLUDED_FROM_ALL_PRODUCTS
from utils.fuzzy_search import normalize_text, rank_products_for_query
from utils.seo_terms import (
    build_query_variants,
    build_taxonomy_intro,
    get_terms_for_taxonomy,
    match_query_mappings,
    normalize_term,
)
from utils.seo_autolearn import (
    build_taxonomy_profiles,
    has_cannabis_search_intent,
    has_mushroom_search_intent,
    has_search_audience_intent,
    infer_seo_query_mapping,
    is_bad_auto_seo_term,
    is_explicit_short_seo_term,
)
from utils.google_trends import fetch_trending_queries_for_supported_locales
from utils.external_seo_signals import load_external_seo_signal_file
from utils.search_console import describe_search_console_setup, fetch_search_console_data, submit_sitemap
from translations.language_config import SUPPORTED_LANGUAGES
from translations.translator import translation_service
from utils.metrics import metrics
from utils.monitoring import monitor as _webhook_monitor, record_webhook_result
from utils.media_resolver import collect_product_media
from utils.resource_manager import (
    APP_CACHE_DIR,
    APP_TMP_DIR,
    DEFAULT_TTL_SECONDS,
    TMP_WARNING_BYTES,
    TMP_FILE_MAX_AGE_SECONDS,
    TMP_MAX_SIZE_BYTES,
    cleanup_owned_dir,
    clear_bot_temp_dir,
    emergency_cleanup,
    ensure_resource_dirs,
    get_cache_usage,
    get_tmp_usage,
    startup_cleanup,
)
from utils.webhook_identity import BotSlot, build_bot_slot, build_registry_webhook_secret
from middleware.request_deduplicator import RequestDeduplicator
from configs.telegram_ip_whitelist import is_telegram_ip
from utils.deeplinks import build_bot_product_url, build_bot_start_url, build_webapp_product_url
from utils.telegram_fetch import fetch_telegram_file
from utils.access_control import (
    is_user_blocked_cached,
    record_user_ip_debounced,
    track_user_debounced,
)
from utils.traffic_classifier import classify_user_agent, is_crawler_user_agent

# Configure logging
from configs.logging_config import configure_logging
configure_logging()
logger = logging.getLogger(__name__)


def _json_no_store(payload: Any, status_code: int = status.HTTP_200_OK) -> JSONResponse:
    """Return private JSON without allowing browser/proxy caching."""
    return JSONResponse(
        payload,
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using %d", name, raw, default)
        value = default
    return max(minimum, min(value, maximum))


# Global registry for bot applications
# This allows the notification service to access all bot instances
_bot_applications = []
_webhook_secret_tokens: List[Optional[str]] = []
_bot_slots: Dict[int, BotSlot] = {}
_catalog_db = Database()
_WEBHOOK_UPDATE_CONCURRENCY = _bounded_int_env("WEBHOOK_UPDATE_CONCURRENCY", 4, 1, 16)
_webhook_processing_semaphores: Dict[str, asyncio.Semaphore] = {}
_bot_applications_by_registry_id: Dict[str, Application] = {}
_runtime_index_to_registry_id: Dict[int, str] = {}
_VISITOR_EVENT_QUEUE_MAX = _bounded_int_env("VISITOR_EVENT_QUEUE_MAX", 512, 100, 5000)
_VISITOR_EVENT_LOG_EVERY = _bounded_int_env("VISITOR_EVENT_LOG_EVERY", 500, 50, 10000)
_visitor_event_queue: Optional[asyncio.Queue] = None
_visitor_events_written = 0
_visitor_events_dropped = 0
_crawler_event_sample: OrderedDict[tuple[str, str, str], float] = OrderedDict()
_CRAWLER_EVENT_SAMPLE_SECONDS = 3600
_CRAWLER_EVENT_SAMPLE_MAX = 1024
_VISITOR_ANALYTICS_RETENTION_DAYS = _bounded_int_env("VISITOR_ANALYTICS_RETENTION_DAYS", 45, 7, 365)
_VISITOR_ANALYTICS_PRUNE_INTERVAL_SECONDS = _bounded_int_env(
    "VISITOR_ANALYTICS_PRUNE_INTERVAL_SECONDS",
    86400,
    3600,
    7 * 86400,
)
_SEO_AUTO_LEARN_STARTUP_DELAY_SECONDS = _bounded_int_env(
    "SEO_AUTO_LEARN_STARTUP_DELAY_SECONDS",
    60,
    60,
    3600,
)
_SEO_TRANSLATION_WARMUP_STARTUP_SECONDS = 240
_SEO_TRANSLATION_WARMUP_INTERVAL_SECONDS = 1800
_SEO_TRANSLATION_WARMUP_BATCH_SIZE = 4
_SEO_ROBOTS_CACHE_SECONDS = _bounded_int_env("SEO_ROBOTS_CACHE_SECONDS", 3600, 60, 86400)
_SEO_SITEMAP_CACHE_SECONDS = _bounded_int_env("SEO_SITEMAP_CACHE_SECONDS", 300, 60, 3600)
_SEO_PAGE_CACHE_SECONDS = _bounded_int_env("SEO_PAGE_CACHE_SECONDS", 300, 30, 3600)
_SEO_PUBLIC_MEDIA_CACHE_SECONDS = _bounded_int_env("SEO_PUBLIC_MEDIA_CACHE_SECONDS", 86400, 300, 604800)
_SEO_PUBLIC_MEDIA_RATE_LIMIT_REQUESTS = _bounded_int_env("SEO_PUBLIC_MEDIA_RATE_LIMIT_REQUESTS", 120, 10, 2000)
_ADMIN_CACHE_CLEANUP_INTERVAL_SECONDS = _bounded_int_env("ADMIN_CACHE_CLEANUP_INTERVAL_SECONDS", 45, 10, 600)
_ADMIN_CACHE_CLEANUP_BATCH_SIZE = _bounded_int_env("ADMIN_CACHE_CLEANUP_BATCH_SIZE", 50, 5, 500)
_ADMIN_CACHE_CLEANUP_MAX_ATTEMPTS = _bounded_int_env("ADMIN_CACHE_CLEANUP_MAX_ATTEMPTS", 12, 1, 100)
_ADMIN_CACHE_CLEANUP_RETRY_BASE_SECONDS = _bounded_int_env("ADMIN_CACHE_CLEANUP_RETRY_BASE_SECONDS", 60, 10, 3600)
_SEO_PUBLIC_MEDIA_RATE_LIMIT_WINDOW = _bounded_int_env("SEO_PUBLIC_MEDIA_RATE_LIMIT_WINDOW", 60, 10, 3600)
_SEO_RESPONSE_CACHE_MAX = _bounded_int_env("SEO_RESPONSE_CACHE_MAX", 128, 16, 1000)
_seo_response_cache: OrderedDict[str, tuple[float, bytes, str, str, int]] = OrderedDict()
_seo_auto_learn_lock: Optional[asyncio.Lock] = None
_seo_trends_lock: Optional[asyncio.Lock] = None
_webapp_search_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_WEBAPP_SEARCH_CACHE_TTL_SECONDS = _bounded_int_env("WEBAPP_SEARCH_CACHE_TTL_SECONDS", 90, 15, 600)
_WEBAPP_SEARCH_CACHE_MAX = _bounded_int_env("WEBAPP_SEARCH_CACHE_MAX", 128, 32, 1000)
_ADMIN_STATUS_CACHE_SECONDS = _bounded_int_env("ADMIN_STATUS_CACHE_SECONDS", 15, 5, 120)
_ADMIN_STATUS_CACHE_MAX = _bounded_int_env("ADMIN_STATUS_CACHE_MAX", 16, 1, 64)
_admin_status_cache: OrderedDict[int, tuple[float, dict[str, Any]]] = OrderedDict()
_SEARCH_CONSOLE_IMPORT_INTERVAL_SECONDS = _bounded_int_env(
    "SEARCH_CONSOLE_IMPORT_INTERVAL_SECONDS",
    3600,
    900,
    12 * 3600,
)
_search_console_import_last_attempt = 0.0
_PROCESS_STARTED_AT = datetime.utcnow()
_PROCESS_STARTED_MONOTONIC = _time.monotonic()

# Per-process counter for webhook 401 rejections (in-memory, not persisted).
# Useful for setting alerts: check logs for [WEBHOOK_401] or watch this counter
# via the /_internal/metrics endpoint (recorded under "webhook_401" endpoint name).
_webhook_401_counter: int = 0
_orphan_legacy_secret_cache: OrderedDict[tuple[str, str], float] = OrderedDict()
_orphan_legacy_log_last_seen: Dict[tuple[str, str], float] = {}
_inactive_webhook_log_last_seen: Dict[str, float] = {}
_ORPHAN_LEGACY_CACHE_MAX = 2048
_ORPHAN_LEGACY_CACHE_TTL_SECONDS = 10 * 60
_ORPHAN_LEGACY_LOG_INTERVAL_SECONDS = 15 * 60
_INACTIVE_WEBHOOK_LOG_INTERVAL_SECONDS = 15 * 60

WEBAPP_SESSION_COOKIE = "brady_webapp_session"
WEBAPP_SESSION_TTL_SECONDS = _bounded_int_env("WEBAPP_SESSION_TTL_SECONDS", 3600, 300, 24 * 3600)
_webapp_session_tokens: OrderedDict[str, tuple[str, float]] = OrderedDict()
_WEBAPP_SESSION_MAX = _bounded_int_env("WEBAPP_SESSION_MAX", 2048, 128, 8192)
_PRIMARY_BOT_CONNECTION_POOL_SIZE = _bounded_int_env("PRIMARY_BOT_CONNECTION_POOL_SIZE", 10, 2, 16)
_SECONDARY_BOT_CONNECTION_POOL_SIZE = _bounded_int_env("SECONDARY_BOT_CONNECTION_POOL_SIZE", 4, 1, 8)
_MEDIA_STREAM_CONCURRENCY = _bounded_int_env("MEDIA_STREAM_CONCURRENCY", 8, 2, 32)
_media_stream_semaphore = asyncio.Semaphore(_MEDIA_STREAM_CONCURRENCY)

# Media cache configuration. Disk cache is app-owned, bounded, and self-pruning.
_DEFAULT_MEDIA_STORAGE_MODE = "persistent" if Path("/data").exists() else "temp"
MEDIA_STORAGE_MODE = os.getenv("MEDIA_STORAGE_MODE", _DEFAULT_MEDIA_STORAGE_MODE).strip().lower()
if MEDIA_STORAGE_MODE not in {"temp", "persistent"}:
    logger.warning("Invalid MEDIA_STORAGE_MODE=%s; falling back to temp", MEDIA_STORAGE_MODE)
    MEDIA_STORAGE_MODE = "temp"
_DEFAULT_MEDIA_CACHE_DIR = Path("/data/brady_app_cache/media") if MEDIA_STORAGE_MODE == "persistent" and Path("/data").exists() else APP_CACHE_DIR / "media"
MEDIA_CACHE_DIR = Path(os.getenv("MEDIA_CACHE_DIR", _DEFAULT_MEDIA_CACHE_DIR))
MEDIA_CACHE_ENABLED = (
    MEDIA_STORAGE_MODE == "persistent"
    and os.getenv("MEDIA_CACHE_ENABLED", "true").lower() not in {"0", "false", "no"}
)
MEDIA_CACHE_VIDEO_ENABLED = MEDIA_CACHE_ENABLED and os.getenv("MEDIA_CACHE_VIDEO_ENABLED", "true").lower() not in {"0", "false", "no"}
MEDIA_CACHE_TTL_SECONDS = _bounded_int_env("MEDIA_CACHE_TTL_SECONDS", 300, 60, 3600)
MEDIA_CACHE_CLEANUP_INTERVAL_SECONDS = _bounded_int_env("MEDIA_CACHE_CLEANUP_INTERVAL_SECONDS", 60, 15, 600)
MEDIA_CACHE_MAX_SIZE_MB = _bounded_int_env("MEDIA_CACHE_MAX_SIZE_MB", 700, 25, 900)
MEDIA_CACHE_MAX_SIZE_BYTES = MEDIA_CACHE_MAX_SIZE_MB * 1024 * 1024
MEDIA_CACHE_TTL_HOURS = _bounded_int_env("MEDIA_CACHE_TTL_HOURS", 12, 1, 168)
MEDIA_CACHE_MAX_FILE_BYTES = _bounded_int_env("MEDIA_CACHE_MAX_FILE_MB", 25, 1, 50) * 1024 * 1024
MEDIA_TEMP_MAX_BYTES = _bounded_int_env("MEDIA_TEMP_MAX_MB", 250, 50, 1200) * 1024 * 1024

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

    _MAX_BUCKETS = 2_048  # evict oldest entries beyond this count
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

_seo_public_media_rate_limiter = WebhookIPRateLimiter(
    max_requests=_SEO_PUBLIC_MEDIA_RATE_LIMIT_REQUESTS,
    window_seconds=_SEO_PUBLIC_MEDIA_RATE_LIMIT_WINDOW,
)

_database_init_lock: Optional[asyncio.Lock] = None
_database_initialized = False

class MediaCache:
    """Simple bounded LRU file cache for reusable catalog media."""

    def __init__(self, cache_dir: Path, max_size_mb: int, ttl_hours: int):
        self.cache_dir = cache_dir
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.ttl_seconds = ttl_hours * 3600
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
            file_age = _time.time() - cache_path.stat().st_mtime
            if file_age > self.ttl_seconds:
                # File is expired, delete it
                cache_path.unlink()
                forget_tracking = globals().get("_forget_media_tracking")
                if callable(forget_tracking):
                    forget_tracking(cache_path)
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
                    forget_tracking = globals().get("_forget_media_tracking")
                    if callable(forget_tracking):
                        forget_tracking(file_path)
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
        """Delete expired files and trim oldest entries if the cache is over budget."""
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
                                forget_tracking = globals().get("_forget_media_tracking")
                                if callable(forget_tracking):
                                    forget_tracking(file_path)
                                pruned += 1
                        except (OSError, FileNotFoundError):
                            pass
                if pruned:
                    logger.info("[resource] cache_pruned count=%d", pruned)
                await self._evict_if_needed()
            except Exception as e:
                logger.warning(f"Error during media cache pruning: {e}")

# Global media cache instance
_media_cache = MediaCache(MEDIA_CACHE_DIR, MEDIA_CACHE_MAX_SIZE_MB, MEDIA_CACHE_TTL_HOURS)

# Lightweight in-memory cache for resolved bot-specific file_ids.
# Keyed by (source_chat_id, source_message_id, file_index, bot_username) → resolved_file_id.
# Avoids repeated DB lookups across consecutive HTTP Range 206 requests for the same media.
# Bounded to _RESOLVED_FILE_ID_CACHE_MAX entries with LRU eviction.
_resolved_file_id_cache: OrderedDict = OrderedDict()
_RESOLVED_FILE_ID_CACHE_MAX = 300

# In-memory cache for parsed media info, keyed by product id.
# Avoids repeated json.loads() on additional_file_ids across requests.
_media_info_cache: OrderedDict = OrderedDict()
_MEDIA_INFO_CACHE_MAX = 300

# In-memory result cache for expensive API list responses.
# Keyed by a string describing the request parameters.
# Each entry is (payload_bytes, timestamp).
_api_result_cache: OrderedDict = OrderedDict()
_API_RESULT_CACHE_MAX = 100
_API_RESULT_CACHE_TTL = 30  # seconds

# Short cache for approved SEO mappings used by public search expansion.
# Auto-learning invalidates it whenever new mappings are approved.
_seo_query_mapping_cache: Optional[tuple[float, List[Dict[str, Any]]]] = None
_SEO_QUERY_MAPPING_CACHE_TTL = 300  # seconds

# Browser video playback can trigger several Range requests for the same file.
# Keep an in-process LRU of media registry mappings we have already written so
# repeated range requests do not generate identical SQLite writes.
_media_registry_write_cache: OrderedDict = OrderedDict()
_MEDIA_REGISTRY_WRITE_CACHE_MAX = 2000


def _media_temp_path(file_id: str, suffix: str) -> Path:
    safe_suffix = suffix if suffix.startswith(".") else f".{suffix}" if suffix else ".bin"
    return APP_TMP_DIR / f"media_{hashlib.sha256(file_id.encode()).hexdigest()}{safe_suffix}"


_media_temp_lock = asyncio.Lock()
_media_generation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_media_active_streams: Dict[Path, int] = {}
_media_last_access: Dict[Path, float] = {}


def _forget_media_tracking(path: Path) -> None:
    """Release bookkeeping after an owned media file is removed."""
    if _media_active_streams.get(path, 0) <= 0:
        _media_active_streams.pop(path, None)
    _media_last_access.pop(path, None)


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


def _resolve_product_media_message_id(product: dict[str, Any], product_id: int, media_index: int) -> Any:
    """Return the Telegram message id that owns the requested media item."""
    source_message_id = product.get("message_id")
    if media_index <= 0:
        return source_message_id

    additional_message_ids_json = product.get("additional_message_ids")
    if not additional_message_ids_json:
        return source_message_id

    try:
        additional_message_ids = json.loads(additional_message_ids_json)
        if media_index - 1 < len(additional_message_ids):
            return additional_message_ids[media_index - 1]
    except (json.JSONDecodeError, TypeError, IndexError) as exc:
        logger.warning(
            "Failed to parse additional_message_ids JSON for product %s, media_index %s. Data: %s. Error: %s",
            product_id,
            media_index,
            additional_message_ids_json,
            exc,
        )
    return source_message_id


async def _resolve_bot_specific_media_file_id(
    *,
    bot: Any,
    source_chat_id: Any,
    source_message_id: Any,
    file_type: str,
    file_id: str,
    product_id: int,
    media_index: int,
) -> str:
    """Resolve the current bot's compatible file_id when possible."""
    if not source_chat_id or not source_message_id:
        logger.debug(
            "Product %s missing chat_id or message_id, cannot resolve bot-specific file_id",
            product_id,
        )
        return file_id

    try:
        bot_username = bot.username or ""
        cache_key = (source_chat_id, source_message_id, 0, bot_username)
        bot_specific_file_id = _resolved_file_id_cache.get(cache_key)
        if bot_specific_file_id is None:
            bot_specific_file_id = await get_bot_specific_file_id(
                bot,
                source_chat_id,
                source_message_id,
                file_type,
                file_index=0,
            )
            if bot_specific_file_id:
                _cache_resolved_file_id(cache_key, bot_specific_file_id)

        if bot_specific_file_id:
            logger.debug(
                "Using bot-specific file_id for product %s, media %s: original=%s..., bot_specific=%s...",
                product_id,
                media_index,
                file_id[:20],
                bot_specific_file_id[:20],
            )
            return bot_specific_file_id

        logger.debug(
            "Bot-specific file_id not available for product %s, media %s, using original file_id",
            product_id,
            media_index,
        )
    except Exception as exc:
        logger.warning(
            "Error getting bot-specific file_id for product %s, media %s: %s. Falling back to original file_id",
            product_id,
            media_index,
            exc,
        )

    return file_id


def _guess_stream_media_type(file_path: Path, media_kind: str) -> str:
    """Return a browser-friendly MIME type for streamed media files."""
    media_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    if media_kind == "photo" and media_type == "application/octet-stream":
        return "image/jpeg"
    if media_kind == "video" and media_type == "application/octet-stream":
        return "video/mp4"
    return media_type


def _build_media_range_response(
    *,
    file_path: Path,
    media_type: str,
    start: int,
    end: int,
    content_length: int,
) -> StreamingResponse:
    async def stream_range():
        async with _media_stream_semaphore:
            active = await _stream_started(file_path)
            logger.info(
                "[media_cache] active_streams path=%s count=%d range=%s-%s",
                file_path.name,
                active,
                start,
                end,
            )
            try:
                with open(file_path, "rb") as file_handle:
                    file_handle.seek(start)
                    remaining = end - start + 1
                    chunk_size = 8192
                    while remaining > 0:
                        chunk = file_handle.read(min(chunk_size, remaining))
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
            "Cache-Control": "public, max-age=3600",
        },
    )


def _build_media_full_response(*, file_path: Path, media_type: str, content_length: int) -> StreamingResponse:
    async def stream_file():
        async with _media_stream_semaphore:
            active = await _stream_started(file_path)
            logger.info("[media_cache] active_streams path=%s count=%d range=full", file_path.name, active)
            try:
                with open(file_path, "rb") as file_handle:
                    while chunk := file_handle.read(8192):
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
            "Cache-Control": "public, max-age=3600",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
        },
    )


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


async def media_gc_worker() -> tuple[int, int]:
    """Delete expired media files selected from media_objects DB state."""
    removed = 0
    freed = 0
    for row in await _catalog_db.list_expired_media_objects():
        path = Path(row["storage_path"])
        try:
            size = path.stat().st_size if path.exists() else 0
            if path.exists() and not _is_media_temp_in_use(path):
                path.unlink()
                _forget_media_tracking(path)
                removed += 1
                freed += size
            await _catalog_db.mark_media_object_deleted(row["media_id"])
        except Exception as exc:
            logger.warning("[media_gc] delete_failed media_id=%s path=%s error=%s", row.get("media_id"), path, exc)
    if removed:
        logger.info("[media_gc] removed=%d freed=%d", removed, freed)
    return removed, freed


def _cleanup_path(path: Optional[Path]) -> None:
    if not path:
        return
    try:
        path.unlink()
        _forget_media_tracking(path)
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


def _api_result_cache_get(key: str) -> Optional[str]:
    """Return a fresh API cache payload, or None when missing/expired."""
    cached_entry = _api_result_cache.get(key)
    if cached_entry is None:
        return None
    payload, ts = cached_entry
    if _time.monotonic() - ts >= _API_RESULT_CACHE_TTL:
        _api_result_cache.pop(key, None)
        return None
    _api_result_cache.move_to_end(key)
    return payload


def _media_registry_cache_key(bot_identifier: str, file_unique_id: str, resolved_file_id: str) -> tuple:
    return (bot_identifier, file_unique_id, hashlib.sha256(resolved_file_id.encode()).hexdigest())


def _has_media_registry_write(bot_identifier: str, file_unique_id: str, resolved_file_id: str) -> bool:
    key = _media_registry_cache_key(bot_identifier, file_unique_id, resolved_file_id)
    if key not in _media_registry_write_cache:
        return False
    _media_registry_write_cache.move_to_end(key)
    return True


def _remember_media_registry_write(bot_identifier: str, file_unique_id: str, resolved_file_id: str) -> None:
    key = _media_registry_cache_key(bot_identifier, file_unique_id, resolved_file_id)
    _media_registry_write_cache[key] = _time.monotonic()
    _media_registry_write_cache.move_to_end(key)
    while len(_media_registry_write_cache) > _MEDIA_REGISTRY_WRITE_CACHE_MAX:
        _media_registry_write_cache.popitem(last=False)


def clear_in_memory_caches() -> int:
    """Clear all in-memory caches. Returns total entries cleared."""
    global _resolved_file_id_cache, _media_info_cache, _api_result_cache, _media_registry_write_cache, _seo_query_mapping_cache, _admin_status_cache

    total = (
        len(_resolved_file_id_cache)
        + len(_media_info_cache)
        + len(_api_result_cache)
        + len(_media_registry_write_cache)
        + (len(_seo_query_mapping_cache[1]) if _seo_query_mapping_cache else 0)
    )

    _resolved_file_id_cache.clear()
    _media_info_cache.clear()
    _api_result_cache.clear()
    _media_registry_write_cache.clear()
    _seo_query_mapping_cache = None
    _admin_status_cache.clear()

    logger.info(f"Cleared {total} in-memory cache entries")
    return total


def _invalidate_seo_query_mapping_cache() -> None:
    global _seo_query_mapping_cache, _admin_status_cache
    _seo_query_mapping_cache = None
    _admin_status_cache.clear()


def _invalidate_admin_status_cache() -> None:
    _admin_status_cache.clear()


async def _set_runtime_setting(key: str, value: Any) -> None:
    try:
        await _catalog_db.set_bot_setting(key, str(value if value is not None else ""))
        _invalidate_admin_status_cache()
    except Exception as exc:
        logger.debug("[runtime_status] setting_write_failed key=%s error=%s", key, exc)


async def _set_runtime_settings(values: dict[str, Any]) -> None:
    try:
        await _catalog_db.set_bot_settings(values)
        _invalidate_admin_status_cache()
    except Exception as exc:
        logger.debug("[runtime_status] settings_write_failed count=%d error=%s", len(values), exc)


async def _get_cached_approved_seo_query_mappings() -> List[Dict[str, Any]]:
    """Return approved SEO mappings without hitting SQLite on every search."""
    global _seo_query_mapping_cache
    now = _time.monotonic()
    if (
        _seo_query_mapping_cache is not None
        and now - _seo_query_mapping_cache[0] < _SEO_QUERY_MAPPING_CACHE_TTL
    ):
        return _seo_query_mapping_cache[1]

    mappings = await _catalog_db.get_approved_seo_query_mappings()
    _seo_query_mapping_cache = (now, mappings)
    return mappings


async def _record_seo_mapping_hits(mappings: List[Dict[str, Any]]) -> None:
    terms = [str(item.get("term") or "") for item in mappings or []]
    if not terms:
        return
    try:
        await _catalog_db.increment_seo_mapping_hits(terms)
    except Exception as exc:
        logger.debug("Failed to record SEO mapping hits: %s", exc)


def clear_bot_runtime_state() -> None:
    """Clear all in-memory bot identity/secret routing state before a rebuild."""
    _webhook_secret_tokens.clear()
    _webhook_secret_history.clear()
    _bot_slots.clear()
    _webhook_processing_semaphores.clear()
    _bot_applications_by_registry_id.clear()
    _runtime_index_to_registry_id.clear()
    clear_in_memory_caches()
    logger.info("[bot_identity] cleared_runtime_state")


async def rebuild_all_webhook_secrets(force: bool = True) -> Dict[str, str]:
    """Reconcile stored registry webhook secrets from canonical Telegram bot IDs."""
    rebuilt: Dict[str, str] = {}
    for bot_id in list(_bot_applications_by_registry_id.keys()):
        row = await _catalog_db.get_bot_registry_by_id(bot_id)
        if not row:
            continue
        expected = build_registry_webhook_secret(int(row["telegram_bot_id"]))
        if force or row.get("webhook_secret") != expected:
            await _catalog_db.resolve_bot_id_from_registry(
                int(row["telegram_bot_id"]),
                row.get("bot_username") or "",
                row.get("token_hash") or "",
                lambda expected=expected: expected,
                force_webhook_secret=True,
            )
        rebuilt[bot_id] = expected
    logger.info("[webhook_secret] rebuilt_registry_secrets count=%d force=%s", len(rebuilt), force)
    return rebuilt

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
_webhook_secret_history: Dict[object, List[Optional[str]]] = {}


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


async def _get_expected_secret_token_for_registry_bot(bot_id: str) -> Optional[str]:
    """Load the expected webhook secret from bot_registry only."""
    row = await _catalog_db.get_bot_registry_by_id(bot_id)
    return row.get("webhook_secret") if row else None


async def _resolve_registry_bot_from_incoming_secret(request: Request) -> Optional[str]:
    """Resolve webhook owner from Telegram's secret header for legacy paths."""
    incoming_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if not isinstance(incoming_token, str) or not incoming_token:
        return None
    row = await _catalog_db.get_bot_registry_by_webhook_secret(incoming_token)
    return row.get("bot_id") if row else None


def _secret_fingerprint(value: Optional[str]) -> str:
    """Return a short irreversible fingerprint for webhook-secret diagnostics."""
    if not isinstance(value, str) or not value:
        return "none"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def get_client_ip(request: Request) -> str:
    """Return the original client IP when the app is behind a proxy."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return (
        request.headers.get("X-Real-IP")
        or (request.client.host if request.client else "unknown")
    )


def _first_header(request: Request, names: List[str]) -> str:
    for name in names:
        value = request.headers.get(name)
        if value:
            return urllib.parse.unquote(str(value).split(",")[0].strip())
    return ""


def _visitor_hash(request: Request, telegram_user_id: Optional[Any] = None) -> str:
    salt = Config.BOT_TOKEN or Config.WEBHOOK_URL or "bradycadia"
    if telegram_user_id is not None:
        material = f"telegram:{telegram_user_id}"
    else:
        material = f"anonymous:{get_client_ip(request)}:{request.headers.get('user-agent', '')[:300]}"
    return hashlib.sha256(f"{salt}:{material}".encode("utf-8")).hexdigest()[:32]


def _user_agent_hash(request: Request) -> str:
    user_agent = request.headers.get("user-agent", "")
    if not user_agent:
        return ""
    salt = Config.BOT_TOKEN or Config.WEBHOOK_URL or "bradycadia"
    return hashlib.sha256(f"{salt}:ua:{user_agent}".encode("utf-8")).hexdigest()[:24]


def _visitor_device_type(request: Request) -> str:
    user_agent = (request.headers.get("user-agent") or "").lower()
    if is_crawler_user_agent(user_agent):
        return "bot"
    if "tablet" in user_agent or "ipad" in user_agent:
        return "tablet"
    if any(token in user_agent for token in ("mobile", "iphone", "android")):
        return "mobile"
    if user_agent:
        return "desktop"
    return "unknown"


def _visitor_language(request: Request, user_data: Optional[TelegramUser] = None) -> str:
    if user_data and user_data.language_code:
        return str(user_data.language_code)[:40]
    accept_language = request.headers.get("accept-language", "")
    return accept_language.split(",")[0].strip()[:40] if accept_language else ""


def _visitor_referrer(request: Request) -> str:
    referrer = request.headers.get("referer") or request.headers.get("referrer") or ""
    if not referrer:
        return ""
    try:
        parsed = urllib.parse.urlparse(referrer)
        return parsed.netloc[:200] or referrer[:200]
    except Exception:
        return referrer[:200]


def _visitor_location(request: Request) -> Dict[str, str]:
    country = _first_header(request, [
        "CF-IPCountry",
        "CloudFront-Viewer-Country",
        "X-Vercel-IP-Country",
        "X-Appengine-Country",
        "X-Country-Code",
        "X-Geo-Country",
    ])
    region = _first_header(request, [
        "CF-Region",
        "CloudFront-Viewer-Country-Region",
        "X-Vercel-IP-Country-Region",
        "X-Appengine-Region",
        "X-Geo-Region",
    ])
    city = _first_header(request, [
        "CF-IPCity",
        "CloudFront-Viewer-City",
        "X-Vercel-IP-City",
        "X-Appengine-City",
        "X-Geo-City",
    ])
    return {
        "country": country.upper()[:80] if country else "Unknown",
        "region": (region or "Unknown")[:120],
        "city": (city or "Unknown")[:120],
    }


def _visitor_user_from_init_data(init_data: str) -> Optional[TelegramUser]:
    if not init_data:
        return None
    try:
        parsed = parse_init_data(init_data)
        user_data = parsed.get("user")
        return user_data if isinstance(user_data, TelegramUser) else None
    except Exception:
        return None


async def _visitor_event_writer_loop() -> None:
    """Serialize visitor analytics writes so public traffic cannot fan out DB commits."""
    global _visitor_events_written

    queue = _visitor_event_queue
    if queue is None:
        logger.warning("[visitor_analytics] writer_not_started reason=queue_missing")
        return

    logger.info("[visitor_analytics] writer_started queue_max=%d", _VISITOR_EVENT_QUEUE_MAX)
    while True:
        payload = await queue.get()
        try:
            await _catalog_db.log_visitor_event(**payload)
            _visitor_events_written += 1
            if _visitor_events_written % _VISITOR_EVENT_LOG_EVERY == 0:
                logger.info(
                    "[visitor_analytics] written=%d queued=%d dropped=%d",
                    _visitor_events_written,
                    queue.qsize(),
                    _visitor_events_dropped,
                )
        except Exception as exc:
            logger.debug("Failed to log visitor event: %s", exc)
        finally:
            queue.task_done()


def _queue_visitor_event(
    request: Request,
    *,
    source: str,
    page_type: str,
    product_id: Optional[int] = None,
    category: str = "",
    subcategory: str = "",
    init_data: str = "",
    language: str = "",
    is_pageview: bool = True,
) -> None:
    """Record a page/app open in the background without storing raw IP."""
    if request.method == "HEAD":
        return

    user_data = _visitor_user_from_init_data(init_data)
    telegram_user_id = user_data.id if user_data else None
    location = _visitor_location(request)
    traffic = classify_user_agent(request.headers.get("user-agent", ""))
    if traffic.is_crawler:
        now = _time.monotonic()
        sample_key = (traffic.traffic_class, traffic.crawler_name, request.url.path)
        last_seen = _crawler_event_sample.get(sample_key)
        if last_seen is not None and now - last_seen < _CRAWLER_EVENT_SAMPLE_SECONDS:
            return
        _crawler_event_sample[sample_key] = now
        _crawler_event_sample.move_to_end(sample_key)
        while len(_crawler_event_sample) > _CRAWLER_EVENT_SAMPLE_MAX:
            _crawler_event_sample.popitem(last=False)
    visitor_hash = _visitor_hash(request, telegram_user_id)
    session_material = f"session:{telegram_user_id}" if telegram_user_id is not None else f"session:{visitor_hash}"
    session_hash = hashlib.sha256(session_material.encode("utf-8")).hexdigest()[:32]
    payload = {
        "visitor_hash": visitor_hash,
        "telegram_user_id": telegram_user_id,
        "username": user_data.username if user_data else "",
        "source": source,
        "page_type": page_type,
        "path": request.url.path,
        "product_id": product_id,
        "category": category,
        "subcategory": subcategory,
        "language": str(language or _visitor_language(request, user_data))[:40],
        "device_type": _visitor_device_type(request),
        "user_agent_hash": _user_agent_hash(request),
        "referrer": _visitor_referrer(request),
        "is_authenticated": user_data is not None,
        "traffic_class": traffic.traffic_class,
        "crawler_name": traffic.crawler_name,
        "is_pageview": is_pageview,
        "session_hash": session_hash,
        **location,
    }

    queue = _visitor_event_queue
    if queue is not None:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            global _visitor_events_dropped
            _visitor_events_dropped += 1
            if _visitor_events_dropped == 1 or _visitor_events_dropped % 100 == 0:
                logger.warning(
                    "[visitor_analytics] event_dropped dropped=%d queue_max=%d source=%s page_type=%s",
                    _visitor_events_dropped,
                    _VISITOR_EVENT_QUEUE_MAX,
                    source,
                    page_type,
                )
        return

    async def _write_event() -> None:
        try:
            await _catalog_db.log_visitor_event(**payload)
        except Exception as exc:
            logger.debug("Failed to log visitor event: %s", exc)

    try:
        asyncio.create_task(_write_event())
    except RuntimeError:
        logger.debug("Could not schedule visitor analytics write")


def _is_cached_orphan_legacy_secret(legacy_index: str, incoming_fp: str, now: Optional[float] = None) -> bool:
    """Return True for a recently seen unknown Telegram legacy webhook secret."""
    now = now if now is not None else _time.monotonic()
    key = (legacy_index, incoming_fp)
    seen_at = _orphan_legacy_secret_cache.get(key)
    if seen_at is None:
        return False
    if now - seen_at > _ORPHAN_LEGACY_CACHE_TTL_SECONDS:
        _orphan_legacy_secret_cache.pop(key, None)
        _orphan_legacy_log_last_seen.pop(key, None)
        return False
    _orphan_legacy_secret_cache.move_to_end(key)
    return True


def _remember_orphan_legacy_secret(legacy_index: str, incoming_fp: str, now: Optional[float] = None) -> None:
    """Cache an unknown Telegram legacy webhook secret to avoid repeated DB lookups."""
    now = now if now is not None else _time.monotonic()
    key = (legacy_index, incoming_fp)
    _orphan_legacy_secret_cache[key] = now
    _orphan_legacy_secret_cache.move_to_end(key)
    while len(_orphan_legacy_secret_cache) > _ORPHAN_LEGACY_CACHE_MAX:
        old_key, _ = _orphan_legacy_secret_cache.popitem(last=False)
        _orphan_legacy_log_last_seen.pop(old_key, None)


def _should_log_orphan_legacy_secret(legacy_index: str, incoming_fp: str, now: Optional[float] = None) -> bool:
    """Throttle orphan webhook logs while still leaving periodic proof of drain."""
    now = now if now is not None else _time.monotonic()
    key = (legacy_index, incoming_fp)
    last_seen = _orphan_legacy_log_last_seen.get(key)
    if last_seen is not None and now - last_seen < _ORPHAN_LEGACY_LOG_INTERVAL_SECONDS:
        return False
    _orphan_legacy_log_last_seen[key] = now
    return True


def _should_log_inactive_webhook_drain(registry_id: str, now: Optional[float] = None) -> bool:
    """Throttle inactive bot webhook drain logs without changing the ack/drop behavior."""
    now = now if now is not None else _time.monotonic()
    last_seen = _inactive_webhook_log_last_seen.get(registry_id)
    if last_seen is not None and now - last_seen < _INACTIVE_WEBHOOK_LOG_INTERVAL_SECONDS:
        return False
    _inactive_webhook_log_last_seen[registry_id] = now
    for key, seen_at in list(_inactive_webhook_log_last_seen.items()):
        if now - seen_at > _INACTIVE_WEBHOOK_LOG_INTERVAL_SECONDS * 4:
            _inactive_webhook_log_last_seen.pop(key, None)
    return True


def _slot_id_for_bot_index(bot_index: int) -> int:
    """Canonical mapping: bot_token_N, route bot_index N, and DB slot_id N."""
    return bot_index


def _get_webhook_processing_semaphore(bot_id: str) -> asyncio.Semaphore:
    semaphore = _webhook_processing_semaphores.get(bot_id)
    if semaphore is None:
        semaphore = asyncio.Semaphore(_WEBHOOK_UPDATE_CONCURRENCY)
        _webhook_processing_semaphores[bot_id] = semaphore
    return semaphore


def _validate_bot_slot_registry(bot_count: int) -> None:
    """Log slot registry inconsistencies without blocking partial startup."""
    seen_slot_ids: Dict[int, int] = {}
    for bot_index, slot in _bot_slots.items():
        if slot.slot_id in seen_slot_ids:
            logger.error(
                "[bot_identity] duplicate_slot_id slot=%s first_bot=%s duplicate_bot=%s",
                slot.slot_id,
                seen_slot_ids[slot.slot_id],
                bot_index,
            )
        seen_slot_ids[slot.slot_id] = bot_index
        expected_slot_id = _slot_id_for_bot_index(bot_index)
        expected_slot_name = f"bot_token_{expected_slot_id}"
        if slot.slot_id != expected_slot_id or slot.slot_name != expected_slot_name:
            logger.error(
                "[bot_identity] slot_mapping_mismatch bot=%s slot_id=%s expected_slot_id=%s slot_name=%s expected_slot_name=%s",
                bot_index,
                slot.slot_id,
                expected_slot_id,
                slot.slot_name,
                expected_slot_name,
            )
        if not slot.identity_hash or not slot.webhook_secret:
            logger.error("[bot_identity] missing_identity_material bot=%s slot=%s", bot_index, slot.slot_id)

    if bot_count > 0 and 0 not in seen_slot_ids:
        logger.error("[bot_identity] missing_slot_0")


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


def _get_bot_application_by_username(bot_username: Optional[str]) -> Optional[Application]:
    normalized = (bot_username or "").strip().lower()
    if not normalized:
        return None
    apps = _bot_applications or getattr(app.state, "bot_apps", []) or []
    for bot_app in apps:
        if bot_app is None:
            continue
        bot = getattr(bot_app, "bot", None)
        username = getattr(bot, "username", None)
        if username and username.lower() == normalized:
            return bot_app
    return None


def _admin_cache_cleanup_retry_delay(attempt_count: int, exc: Optional[BaseException] = None) -> int:
    if isinstance(exc, telegram.error.RetryAfter):
        retry_after = int(getattr(exc, "retry_after", 0) or 0)
        return max(10, min(3600, retry_after + 5))
    exponent = max(0, min(int(attempt_count) - 1, 5))
    return min(3600, _ADMIN_CACHE_CLEANUP_RETRY_BASE_SECONDS * (2 ** exponent))


def _get_admin_dashboard_token() -> str:
    """Read the dashboard token dynamically so Render env updates are respected."""
    return (os.getenv("ADMIN_DASHBOARD_TOKEN") or getattr(Config, "ADMIN_DASHBOARD_TOKEN", "") or "").strip()


def _is_admin_dashboard_enabled() -> bool:
    """Return whether the browser admin dashboard should be exposed."""
    env_value = os.getenv("ADMIN_DASHBOARD_ENABLED")
    if env_value is not None:
        return env_value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(getattr(Config, "ADMIN_DASHBOARD_ENABLED", False))


def _extract_admin_dashboard_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("X-Admin-Token", "").strip()


def _require_admin_dashboard_auth(request: Request) -> None:
    if not _is_admin_dashboard_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    expected = _get_admin_dashboard_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin dashboard disabled. Set ADMIN_DASHBOARD_TOKEN.",
        )
    supplied = _extract_admin_dashboard_token(request)
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin dashboard token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _runtime_bot_snapshots() -> List[Dict[str, object]]:
    apps = _bot_applications or getattr(app.state, "bot_apps", [])
    max_count = max(len(Config.BOT_TOKENS), len(apps))
    snapshots: List[Dict[str, object]] = []
    for idx in range(max_count):
        bot_app = apps[idx] if idx < len(apps) else None
        bot = getattr(bot_app, "bot", None) if bot_app is not None else None
        slot = _bot_slots.get(idx)
        bot_data = getattr(bot_app, "bot_data", {}) if bot_app is not None else {}
        snapshots.append({
            "index": idx,
            "running": bool(getattr(bot_app, "running", False)) if bot_app is not None else False,
            "username": getattr(bot, "username", None),
            "telegram_bot_id": getattr(bot, "id", None),
            "registry_bot_id": bot_data.get("bot_registry_id") if isinstance(bot_data, dict) else None,
            "slot_id": slot.slot_id if slot else None,
            "slot_name": slot.slot_name if slot else None,
            "identity_hash": slot.identity_hash if slot else None,
            "webhook_secret_fp": _secret_fingerprint(slot.webhook_secret if slot else None),
        })
    return snapshots


def _cache_state_snapshot() -> Dict[str, int]:
    return {
        "resolved_file_ids": len(_resolved_file_id_cache),
        "media_info": len(_media_info_cache),
        "api_results": len(_api_result_cache),
        "media_registry_write_keys": len(_media_registry_write_cache),
        "orphan_legacy_secrets": len(_orphan_legacy_secret_cache),
        "webhook_processing_locks": len(_webhook_processing_semaphores),
        "webhook_update_concurrency": _WEBHOOK_UPDATE_CONCURRENCY,
    }


def _runtime_process_snapshot() -> Dict[str, object]:
    uptime_seconds = max(0, int(_time.monotonic() - _PROCESS_STARTED_MONOTONIC))
    snapshot: Dict[str, object] = {
        "started_at": _PROCESS_STARTED_AT.isoformat(timespec="seconds") + "Z",
        "uptime_seconds": uptime_seconds,
        "uptime_minutes": round(uptime_seconds / 60, 1),
        "visitor_queue_size": _visitor_event_queue.qsize() if _visitor_event_queue else 0,
        "visitor_queue_max": _VISITOR_EVENT_QUEUE_MAX,
        "visitor_events_written": _visitor_events_written,
        "visitor_events_dropped": _visitor_events_dropped,
        "seo_response_cache_entries": len(_seo_response_cache),
        "webapp_search_cache_entries": len(_webapp_search_cache),
    }
    try:
        status_text = Path("/proc/self/status").read_text(encoding="utf-8")
        memory_fields = {}
        for line in status_text.splitlines():
            key, _, value = line.partition(":")
            if key in {"VmRSS", "VmHWM", "VmSize"}:
                memory_fields[key] = int(value.strip().split()[0]) * 1024
        snapshot.update({
            "memory_rss_bytes": memory_fields.get("VmRSS"),
            "memory_peak_rss_bytes": memory_fields.get("VmHWM"),
            "memory_virtual_bytes": memory_fields.get("VmSize"),
        })
    except (OSError, ValueError, IndexError):
        pass
    return snapshot


def _media_runtime_snapshot() -> Dict[str, object]:
    active_streams = sum(_media_active_streams.values())
    return {
        "storage_mode": MEDIA_STORAGE_MODE,
        "cache_enabled": MEDIA_CACHE_ENABLED,
        "video_cache_enabled": MEDIA_CACHE_VIDEO_ENABLED,
        "cache_dir": str(MEDIA_CACHE_DIR),
        "cache_max_mb": MEDIA_CACHE_MAX_SIZE_MB,
        "cache_ttl_hours": MEDIA_CACHE_TTL_HOURS,
        "cache_cleanup_interval_seconds": MEDIA_CACHE_CLEANUP_INTERVAL_SECONDS,
        "temp_ttl_seconds": MEDIA_CACHE_TTL_SECONDS,
        "max_file_mb": int(MEDIA_CACHE_MAX_FILE_BYTES / (1024 * 1024)),
        "active_streams": active_streams,
        "active_files": len(_media_active_streams),
        "tracked_temp_files": len(_media_last_access),
        "generation_locks": len(_media_generation_locks),
    }


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


async def reset_bot_identity(slot_id: int) -> dict:
    """Reset runtime/cache state for one permanent bot slot only."""
    db = Database()
    file_cache_deleted = await db.clear_bot_file_id_cache_for_slot(slot_id)
    resolver_invalidated = await db.clear_bot_media_file_ids_for_slot(slot_id)
    temp_removed, temp_freed = clear_bot_temp_dir(slot_id)
    logger.warning(
        "[bot_identity] slot_reset",
        extra={
            "bot_slot": slot_id,
            "bot_name": f"bot_token_{slot_id}",
            "file_cache_deleted": file_cache_deleted,
            "resolver_invalidated": resolver_invalidated,
            "temp_removed": temp_removed,
            "temp_freed": temp_freed,
        },
    )
    return {
        "file_cache_deleted": file_cache_deleted,
        "resolver_invalidated": resolver_invalidated,
        "temp_removed": temp_removed,
        "temp_freed": temp_freed,
    }


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
    global _bot_applications, _webhook_secret_tokens, _webhook_secret_history, _bot_slots
    global _bot_applications_by_registry_id, _runtime_index_to_registry_id

    summary: dict = {"created": [], "failed": [], "errors": {}}
    logger.info("Starting bot reinitialisation (stop → setup)")

    # Snapshot the current runtime tokens before teardown so we can preserve
    # them as "previous" tokens in the history map.
    old_runtime_tokens: List[Optional[str]] = list(_webhook_secret_tokens)

    try:
        await _stop_existing_bots()
    except Exception:
        logger.exception("Unexpected error while stopping existing bot applications")

    clear_bot_runtime_state()

    tokens = getattr(Config, "BOT_TOKENS", []) or []
    if not tokens:
        logger.warning("No BOT_TOKENS found in Config during reinitialisation")
        return {"created": [], "failed": [], "errors": {"no_tokens": "No BOT_TOKENS configured"}}

    new_secret_tokens: List[Optional[str]] = []
    _bot_slots = {}
    _bot_applications_by_registry_id = {}
    _runtime_index_to_registry_id = {}
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
            registry_id = _runtime_index_to_registry_id.get(idx)
            if registry_id:
                _webhook_secret_history[registry_id] = [prev_token, webhook_secret_token]
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
    slot_id = _slot_id_for_bot_index(bot_index)
    slot_name = f"bot_token_{slot_id}"
    token_suffix = bot_token[-6:] if bot_token else "empty"
    logger.info(
        "Bot startup begin index=%d slot=%s token_suffix=%s",
        bot_index,
        slot_name,
        token_suffix,
        extra={"bot_slot": slot_id, "bot_name": slot_name},
    )
    
    # Create application with optimized timeout settings
    pool_size = (
        _PRIMARY_BOT_CONNECTION_POOL_SIZE
        if bot_index == 0
        else _SECONDARY_BOT_CONNECTION_POOL_SIZE
    )
    request = HTTPXRequest(
        connection_pool_size=pool_size,
        connect_timeout=10.0,
        read_timeout=10.0,
        write_timeout=10.0,
        pool_timeout=10.0
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

    slot: Optional[BotSlot] = None
    identity_rotated = False
    registry_bot_id: Optional[str] = None
    registry_webhook_secret: Optional[str] = None
    try:
        me = await bot_app.bot.get_me()
        bot_username = getattr(me, "username", None) or bot_app.bot.username
        bot_id = getattr(me, "id", None) or bot_app.bot.id

        from database import Database
        _db = Database()
        await _ensure_database_initialized()
        registry_row = await _db.resolve_bot_id_from_registry(
            int(bot_id or 0),
            bot_username or "",
            build_bot_slot(slot_id, bot_token, telegram_bot_id=bot_id).token_hash,
            lambda: build_registry_webhook_secret(int(bot_id or 0)),
            force_webhook_secret=True,
        )
        registry_bot_id = registry_row["bot_id"]
        registry_webhook_secret = registry_row["webhook_secret"]
        logger.info(
            "[bot_identity] registry_resolved bot_index=%s bot_id=%s telegram_bot_id=%s token_hash=%s identity_source=bot_registry webhook_expected_fp=%s",
            bot_index,
            registry_bot_id,
            bot_id,
            (registry_row.get("token_hash") or "")[:12],
            _secret_fingerprint(registry_webhook_secret),
        )
        previous_slot = await _db.get_bot_slot_identity(slot_id)
        previous_version = int((previous_slot or {}).get("identity_version") or 1)
        provisional_slot = build_bot_slot(
            slot_id,
            bot_token,
            telegram_username=bot_username,
            telegram_bot_id=bot_id,
            identity_version=previous_version,
        )
        if previous_slot and previous_slot.get("token_hash") != provisional_slot.token_hash:
            identity_rotated = True
            previous_version += 1
            provisional_slot = build_bot_slot(
                slot_id,
                bot_token,
                telegram_username=bot_username,
                telegram_bot_id=bot_id,
                identity_version=previous_version,
            )
            logger.warning(
                "[bot_identity] token_rotation_detected",
                extra={
                    "bot_slot": slot_id,
                    "bot_name": slot_name,
                    "bot_username": f"@{bot_username}" if bot_username else None,
                    "identity_version": previous_version,
                },
            )
            if previous_slot.get("identity_hash") != provisional_slot.identity_hash:
                try:
                    await bot_app.bot.delete_webhook(drop_pending_updates=False)
                except telegram.error.TelegramError as exc:
                    logger.warning("[bot_identity] delete_webhook_failed slot=%s error=%s", slot_id, exc)
                await reset_bot_identity(slot_id)
            else:
                logger.info(
                    "[bot_identity] token_refreshed_identity_preserved",
                    extra={
                        "bot_slot": slot_id,
                        "bot_name": slot_name,
                        "bot_username": f"@{bot_username}" if bot_username else None,
                        "identity_version": previous_version,
                    },
                )

        slot = provisional_slot
        _bot_slots[bot_index] = slot
        _runtime_index_to_registry_id[bot_index] = registry_bot_id
        _bot_applications_by_registry_id[registry_bot_id] = bot_app
        bot_app.bot_data["bot_slot"] = slot.slot_id
        bot_app.bot_data["bot_name"] = slot.slot_name
        bot_app.bot_data["identity_hash"] = slot.identity_hash
        bot_app.bot_data["bot_registry_id"] = registry_bot_id
        await _db.register_bot_slot(
            slot.slot_id,
            slot.slot_name,
            bot_username or "",
            int(bot_id or 0),
            slot.token_hash,
            slot.identity_version,
            slot.identity_hash,
            registry_webhook_secret or slot.webhook_secret,
        )
        try:
            from utils.bot_health import validate_bot_readiness

            if getattr(Config, "ADMIN_IDS", None):
                await validate_bot_readiness(bot_app.bot, Config.ADMIN_IDS[:1])
        except Exception:
            logger.warning("Bot readiness validation failed", exc_info=True)
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
        if not registry_bot_id:
            logger.warning("Partial startup for bot index=%d: registry unavailable; webhook setup skipped", bot_index)
            return bot_app, None
        webhook_path = f"/webhook/{registry_bot_id}"
        if slot is None:
            slot = build_bot_slot(slot_id, bot_token)
            _bot_slots[bot_index] = slot
            bot_app.bot_data["bot_slot"] = slot.slot_id
            bot_app.bot_data["bot_name"] = slot.slot_name
            bot_app.bot_data["identity_hash"] = slot.identity_hash
        webhook_secret_token = registry_webhook_secret
        try:
            await _validate_or_repair_webhook(
                bot_app.bot,
                bot_index,
                webhook_url,
                webhook_path,
                webhook_secret_token,
                slot=slot,
            )
        except telegram.error.TelegramError as e:
            logger.warning("Partial startup for bot index=%d: webhook setup skipped: %s", bot_index, e)
            webhook_secret_token = None
        except Exception as e:
            logger.warning("Partial startup for bot index=%d: webhook setup skipped: %s", bot_index, e)
            webhook_secret_token = None
    else:
        logger.warning("WEBHOOK_URL not set. Webhook may not work properly.")

    logger.info(
        "Bot startup success index=%d username=%s rotated=%s",
        bot_index,
        getattr(bot_app.bot, "username", None) or "unknown",
        identity_rotated,
        extra={
            "bot_slot": slot_id,
            "bot_name": slot_name,
            "bot_username": f"@{getattr(bot_app.bot, 'username', None)}" if getattr(bot_app.bot, "username", None) else None,
        },
    )
    return bot_app, webhook_secret_token


async def _validate_or_repair_webhook(
    bot,
    bot_index: int,
    webhook_url: str,
    webhook_path: str,
    secret_token: str,
    *,
    slot: Optional[BotSlot] = None,
) -> None:
    """Validate webhook URL/state and repair safely without dropping pending updates."""
    slot_id = slot.slot_id if slot else _slot_id_for_bot_index(bot_index)
    slot_name = slot.slot_name if slot else f"bot_token_{slot_id}"
    lock_owner = f"{os.getenv('RENDER_INSTANCE_ID') or os.getenv('HOSTNAME') or os.getpid()}:{slot_name}"
    expected_url = f"{webhook_url.rstrip('/')}{webhook_path}"
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
                extra={"bot_slot": slot_id, "bot_name": slot_name, "bot_username": f"@{getattr(bot, 'username', '')}" if getattr(bot, "username", None) else None},
            )
        elif last_error:
            logger.warning("[webhook] existing_error bot=%d pending=%s error=%s", bot_index, pending, last_error)
        else:
            logger.info("[webhook] validated bot=%d pending=%s cert=%s", bot_index, pending, has_cert)

        if mismatch or needs_secret_refresh:
            db = Database()
            acquired = await db.acquire_bot_slot_lock(slot_id, lock_owner, ttl_seconds=60)
            if not acquired:
                logger.info(
                    "[webhook] repair_skipped_lock_held bot=%d",
                    bot_index,
                    extra={"bot_slot": slot_id, "bot_name": slot_name},
                )
                return
            try:
                await bot.set_webhook(
                    url=expected_url,
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=False,
                    secret_token=secret_token,
                )
            finally:
                await db.release_bot_slot_lock(slot_id, lock_owner)
            logger.info(
                "[webhook] repaired bot=%d reason=%s secret_fp=%s",
                bot_index,
                "url_mismatch" if mismatch else "secret_refresh",
                _secret_fingerprint(secret_token),
                extra={"bot_slot": slot_id, "bot_name": slot_name},
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
            await media_gc_worker()
            removed, freed = await _cleanup_media_temp_files()
            if removed:
                logger.info("[media_cache] cleanup_completed removed=%d freed=%d", removed, freed)
            cleanup_owned_dir(
                APP_TMP_DIR,
                TMP_FILE_MAX_AGE_SECONDS,
                max_bytes=TMP_MAX_SIZE_BYTES,
                skip_file=_is_media_temp_in_use,
            )
            cleanup_owned_dir(APP_CACHE_DIR, MEDIA_CACHE_TTL_HOURS * 3600, max_bytes=MEDIA_CACHE_MAX_SIZE_BYTES)
            tmp_usage = get_tmp_usage()
            cache_usage = get_cache_usage()
            if tmp_usage["used_bytes"] >= TMP_WARNING_BYTES:
                logger.warning("[resource] tmp_usage_warning used=%d", tmp_usage["used_bytes"])
            if cache_usage["app_cache_bytes"] > MEDIA_CACHE_MAX_SIZE_BYTES:
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


async def _admin_cache_cleanup_loop():
    """Background task: retry deletion of temporary admin-chat cache copies."""
    await asyncio.sleep(20)
    while True:
        try:
            entries = await _catalog_db.get_due_admin_cache_cleanup_entries(
                limit=_ADMIN_CACHE_CLEANUP_BATCH_SIZE
            )
            if entries:
                processed = 0
                deleted = 0
                rescheduled = 0
                dropped = 0
                for entry in entries:
                    processed += 1
                    bot_username = (entry.get("bot_username") or "").strip().lower()
                    admin_chat_id = entry.get("admin_chat_id")
                    message_id = entry.get("message_id")
                    attempt_count = int(entry.get("attempt_count") or 0)

                    if not bot_username or not admin_chat_id or not message_id:
                        if bot_username and admin_chat_id and message_id:
                            await _catalog_db.delete_admin_cache_cleanup(
                                bot_username=bot_username,
                                admin_chat_id=int(admin_chat_id),
                                message_id=int(message_id),
                            )
                        dropped += 1
                        continue

                    if attempt_count >= _ADMIN_CACHE_CLEANUP_MAX_ATTEMPTS:
                        await _catalog_db.delete_admin_cache_cleanup(
                            bot_username=bot_username,
                            admin_chat_id=int(admin_chat_id),
                            message_id=int(message_id),
                        )
                        dropped += 1
                        logger.warning(
                            "[admin_cache_cleanup] dropped bot=%s chat=%s message_id=%s attempts=%s",
                            bot_username,
                            admin_chat_id,
                            message_id,
                            attempt_count,
                        )
                        continue

                    bot_app = _get_bot_application_by_username(bot_username)
                    if bot_app is None:
                        await _catalog_db.reschedule_admin_cache_cleanup(
                            bot_username=bot_username,
                            admin_chat_id=int(admin_chat_id),
                            message_id=int(message_id),
                            error="runtime_bot_unavailable",
                            delay_seconds=_admin_cache_cleanup_retry_delay(attempt_count + 1),
                        )
                        rescheduled += 1
                        continue

                    try:
                        await bot_app.bot.delete_message(
                            chat_id=int(admin_chat_id),
                            message_id=int(message_id),
                        )
                        await _catalog_db.delete_admin_cache_cleanup(
                            bot_username=bot_username,
                            admin_chat_id=int(admin_chat_id),
                            message_id=int(message_id),
                        )
                        deleted += 1
                    except telegram.error.BadRequest as exc:
                        error_msg = str(exc).lower()
                        if "message to delete not found" in error_msg:
                            await _catalog_db.delete_admin_cache_cleanup(
                                bot_username=bot_username,
                                admin_chat_id=int(admin_chat_id),
                                message_id=int(message_id),
                            )
                            deleted += 1
                        else:
                            await _catalog_db.reschedule_admin_cache_cleanup(
                                bot_username=bot_username,
                                admin_chat_id=int(admin_chat_id),
                                message_id=int(message_id),
                                error=str(exc),
                                delay_seconds=_admin_cache_cleanup_retry_delay(attempt_count + 1, exc),
                            )
                            rescheduled += 1
                    except telegram.error.RetryAfter as exc:
                        await _catalog_db.reschedule_admin_cache_cleanup(
                            bot_username=bot_username,
                            admin_chat_id=int(admin_chat_id),
                            message_id=int(message_id),
                            error=str(exc),
                            delay_seconds=_admin_cache_cleanup_retry_delay(attempt_count + 1, exc),
                        )
                        rescheduled += 1
                    except telegram.error.TelegramError as exc:
                        await _catalog_db.reschedule_admin_cache_cleanup(
                            bot_username=bot_username,
                            admin_chat_id=int(admin_chat_id),
                            message_id=int(message_id),
                            error=str(exc),
                            delay_seconds=_admin_cache_cleanup_retry_delay(attempt_count + 1, exc),
                        )
                        rescheduled += 1
                    except Exception as exc:
                        retry_delay = _admin_cache_cleanup_retry_delay(attempt_count + 1)
                        if not is_transient_telegram_error(exc):
                            retry_delay = max(retry_delay, 300)
                        await _catalog_db.reschedule_admin_cache_cleanup(
                            bot_username=bot_username,
                            admin_chat_id=int(admin_chat_id),
                            message_id=int(message_id),
                            error=str(exc),
                            delay_seconds=retry_delay,
                        )
                        rescheduled += 1

                logger.info(
                    "[admin_cache_cleanup] processed=%d deleted=%d rescheduled=%d dropped=%d",
                    processed,
                    deleted,
                    rescheduled,
                    dropped,
                )
        except Exception as exc:
            logger.warning("[admin_cache_cleanup] loop_error error=%s", exc, exc_info=True)
        await asyncio.sleep(_ADMIN_CACHE_CLEANUP_INTERVAL_SECONDS)


async def _ensure_database_initialized() -> None:
    """Initialize the database schema only once per process."""
    global _database_initialized, _database_init_lock
    if _database_initialized:
        return
    if _database_init_lock is None:
        _database_init_lock = asyncio.Lock()
    async with _database_init_lock:
        if _database_initialized:
            return
        await _catalog_db.init_db()
        _database_initialized = True


def _is_useful_auto_seo_term(term: str) -> bool:
    normalized = normalize_term(term)
    if len(normalized) < 2 or len(normalized) > 80:
        return False
    if len(normalized) == 2 and not is_explicit_short_seo_term(normalized):
        return False
    if normalized.isdigit():
        return False
    if len(normalized.split()) > 7:
        return False
    if is_bad_auto_seo_term(term):
        return False
    return True


async def _expand_auto_learn_mapping_variants(term: str, source_language: str = "") -> List[Dict[str, str]]:
    """Return the original query and language-tagged translated variants."""
    source_language = str(source_language or "").lower()
    if source_language not in SUPPORTED_LANGUAGES:
        source_language = "en"
    terms = [{"term": term, "language": source_language}]
    # Product/brand names should not be blindly translated. That created noise
    # such as "other mommy" from name-like searches. Only expand terms that
    # clearly look like cannabis/mushroom community vocabulary.
    if has_search_audience_intent(term):
        for lang in SUPPORTED_LANGUAGES:
            if lang == source_language:
                continue
            try:
                translated = await translation_service.translate(term, lang, source_lang="auto")
            except Exception as exc:
                logger.debug("[seo_auto_learn] translation_skipped term=%r lang=%s error=%s", term, lang, exc)
                continue
            if translated and translated != term:
                terms.append({"term": translated, "language": lang})

    seen = set()
    cleaned = []
    for item in terms:
        candidate = " ".join(str(item.get("term") or "").split())
        normalized = normalize_term(candidate)
        if normalized in seen or not _is_useful_auto_seo_term(candidate):
            continue
        seen.add(normalized)
        cleaned.append({"term": candidate, "language": item.get("language") or "en"})
    return cleaned


async def _publish_seo_mapping_changes(mappings: List[Dict[str, Any]]) -> int:
    """Invalidate rendered SEO pages and announce every affected public URL."""
    if not mappings:
        return 0
    _seo_response_cache.clear()
    _invalidate_admin_status_cache()
    if not _indexnow_enabled():
        return 0
    urls: List[str] = []
    taxonomies = {
        (
            str(mapping.get("category") or "").strip(),
            str(mapping.get("subcategory") or "").strip(),
        )
        for mapping in mappings
        if str(mapping.get("category") or "").strip()
    }
    for category, subcategory in taxonomies:
        urls.extend(
            _indexnow_taxonomy_urls(category, subcategory)
        )
        try:
            if subcategory:
                products = await _catalog_db.get_products_by_category_and_subcategory(
                    category,
                    subcategory,
                )
            else:
                products = await _catalog_db.get_products_by_category(category)
            for product in products:
                urls.extend(_indexnow_product_urls(product))
        except Exception as exc:
            logger.debug(
                "[seo_auto_learn] affected_products_unavailable category=%s subcategory=%s error=%s",
                category,
                subcategory,
                exc,
            )
    if not urls:
        return 0
    return await _catalog_db.enqueue_indexnow_urls(
        list(dict.fromkeys(urls)),
        reason="seo_mapping_change",
    )


def _external_signal_file_path() -> str:
    return Config.EXTERNAL_SEO_SIGNALS_FILE or str(Path(__file__).parent / "configs" / "external_seo_signals.json")


def _get_seo_trends_lock() -> asyncio.Lock:
    global _seo_trends_lock
    if _seo_trends_lock is None:
        _seo_trends_lock = asyncio.Lock()
    return _seo_trends_lock


async def _import_google_trends_signals(
    products: list[dict[str, Any]],
    profiles: dict[tuple[str, str], dict[str, Any]],
) -> int:
    """Import a bounded set of public Google Trends queries into external signals."""
    if not Config.SEO_TRENDS_ENABLED:
        return 0

    trend_rows = await asyncio.to_thread(
        fetch_trending_queries_for_supported_locales,
        limit_per_geo=Config.SEO_TRENDS_LIMIT_PER_GEO,
        max_terms=Config.SEO_TRENDS_MAX_TERMS_PER_RUN,
    )
    if not trend_rows:
        logger.info("[seo_trends] rows=0 imported=0 promoted=0")
        await _set_runtime_settings({
            "seo_trends_last_fetch_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
            "seo_trends_last_rows": 0,
            "seo_trends_last_candidates": 0,
            "seo_trends_last_imported": 0,
        })
        return 0

    candidate_rows: List[Dict[str, Any]] = []
    seen_candidates: set[tuple[str, str]] = set()
    for row in trend_rows:
        parent_term = str(row.get("term") or "").strip()
        notes = row.get("notes") or {}
        entries = [(parent_term, "google_trends_trending", 0)]
        entries.extend(
            (str(term or "").strip(), "google_trends_related", rank)
            for rank, term in enumerate((notes.get("related_queries") or [])[:3], start=1)
        )
        for term, term_type, related_rank in entries:
            language = str(row.get("language") or "en").lower()
            key = (language, normalize_term(term))
            if not key[1] or key in seen_candidates:
                continue
            seen_candidates.add(key)
            candidate = dict(row)
            candidate.update({
                "term": term,
                "term_type": term_type,
                "language": language,
                "confidence": max(
                    1,
                    int(row.get("confidence") or 1) - (8 + related_rank * 2 if related_rank else 0),
                ),
                "evidence_count": max(
                    1,
                    int(row.get("evidence_count") or 1) - (related_rank * 3 if related_rank else 0),
                ),
                "notes": {
                    **notes,
                    "parent_term": parent_term if related_rank else "",
                    "related_rank": related_rank,
                },
            })
            candidate_rows.append(candidate)

    signals = []
    for row in candidate_rows:
        term = str(row.get("term") or "").strip()
        if not _is_useful_auto_seo_term(term):
            continue
        cannabis_intent = has_cannabis_search_intent(term)
        mushroom_intent = has_mushroom_search_intent(term)
        if not (cannabis_intent or mushroom_intent):
            continue
        is_related = str(row.get("term_type") or "") == "google_trends_related"
        inferred = await asyncio.to_thread(
            infer_seo_query_mapping,
            term,
            products,
            min_score=Config.SEO_EXTERNAL_SIGNAL_SCORE - (2 if cannabis_intent and not is_related else 0),
            profiles=profiles,
        )
        if not inferred:
            continue
        confidence = min(100, max(int(row.get("confidence") or 0), int(float(inferred.get("score") or 0))))
        if cannabis_intent:
            confidence = min(100, confidence + 4)
        signals.append({
            "term": term,
            "term_type": str(row.get("term_type") or "google_trends_trending"),
            "provider": "google_trends",
            "source": str(row.get("source") or "")[:120],
            "language": str(row.get("language") or "en").lower(),
            "category": inferred["category"],
            "subcategory": inferred["subcategory"],
            "confidence": confidence,
            "approved": True,
            "evidence_count": max(1, int(row.get("evidence_count") or 1)),
            "notes": {
                "geo": (row.get("notes") or {}).get("geo"),
                "hl": (row.get("notes") or {}).get("hl"),
                "rank": (row.get("notes") or {}).get("rank"),
                "parent_term": (row.get("notes") or {}).get("parent_term") or "",
                "related_rank": (row.get("notes") or {}).get("related_rank") or 0,
                "cannabis_boosted": cannabis_intent,
                "mushroom_relevant": mushroom_intent,
            },
        })

    if not signals:
        logger.info(
            "[seo_trends] rows=%d candidates=%d imported=0 promoted=0",
            len(trend_rows),
            len(candidate_rows),
        )
        await _set_runtime_settings({
            "seo_trends_last_fetch_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
            "seo_trends_last_rows": len(trend_rows),
            "seo_trends_last_candidates": len(candidate_rows),
            "seo_trends_last_imported": 0,
        })
        return 0

    imported = await _catalog_db.upsert_external_seo_signals(
        signals,
        provider="google_trends",
        source="google_trends",
        approved=True,
    )
    if imported:
        _invalidate_admin_status_cache()
    logger.info(
        "[seo_trends] rows=%d candidates=%d relevant=%d imported=%d",
        len(trend_rows),
        len(candidate_rows),
        len(signals),
        imported,
    )
    await _set_runtime_settings({
        "seo_trends_last_fetch_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "seo_trends_last_rows": len(trend_rows),
        "seo_trends_last_candidates": len(candidate_rows),
        "seo_trends_last_imported": imported,
    })
    return imported


async def _promote_approved_external_signals() -> int:
    """Promote approved external signals into normal SEO mappings."""
    candidates = await _catalog_db.get_approved_external_seo_signals_without_mapping(
        limit=Config.SEO_AUTO_LEARN_MAX_PER_RUN,
    )
    if not candidates:
        return 0

    approvals = []
    for item in candidates:
        term = str(item.get("term") or "").strip()
        if not _is_useful_auto_seo_term(term):
            continue
        if not has_search_audience_intent(term):
            continue
        for variant in await _expand_auto_learn_mapping_variants(term, item.get("language") or "en"):
            approvals.append({
                "term": variant["term"],
                "language": variant["language"],
                "category": item.get("category") or "",
                "subcategory": item.get("subcategory") or "",
            })

    if not approvals:
        return 0

    promoted = await _catalog_db.upsert_seo_query_mappings(
        approvals,
        source="external_signal",
        approved=True,
    )
    if promoted:
        _invalidate_seo_query_mapping_cache()
        await _publish_seo_mapping_changes(approvals)
    logger.info("[seo_external] promoted=%d signals=%d", promoted, len(candidates))
    return promoted


async def _import_external_signal_file(
    products: list[dict[str, Any]],
    profiles: dict[tuple[str, str], dict[str, Any]],
) -> int:
    """Import optional approved taxonomy terms from a local/provider JSON export."""
    raw_signals = load_external_seo_signal_file(_external_signal_file_path())
    if not raw_signals:
        return 0

    signals = []
    for raw in raw_signals[:500]:
        term = str(raw.get("term") or "").strip()
        if not _is_useful_auto_seo_term(term):
            continue

        category = str(raw.get("category") or "").strip()
        subcategory = str(raw.get("subcategory") or "").strip()
        approved = bool(raw.get("approved"))
        confidence = int(raw.get("confidence") or 0)

        if not category:
            inferred = infer_seo_query_mapping(
                term,
                products,
                min_score=Config.SEO_EXTERNAL_SIGNAL_SCORE,
                profiles=profiles,
            )
            if not inferred:
                approved = False
            else:
                category = inferred["category"]
                subcategory = inferred["subcategory"]
                confidence = max(confidence, int(float(inferred.get("score") or 0)))
                approved = True
        elif confidence >= Config.SEO_EXTERNAL_SIGNAL_SCORE:
            approved = True

        signals.append({
            **raw,
            "term": term,
            "category": category,
            "subcategory": subcategory,
            "confidence": confidence,
            "approved": approved,
            "source": raw.get("source") or "external_signal_file",
        })

    imported = await _catalog_db.upsert_external_seo_signals(
        signals,
        provider="external_file",
        source="external_signal_file",
        approved=False,
    )
    if imported:
        _invalidate_admin_status_cache()
        logger.info("[seo_external] file_signals_imported=%d", imported)
    return imported


def _search_console_page_context(
    page_url: str,
    products_by_id: Dict[int, Dict[str, Any]],
) -> Dict[str, str]:
    """Resolve language and catalog taxonomy from a ranked canonical URL."""
    try:
        path = urllib.parse.urlparse(str(page_url or "")).path
    except ValueError:
        path = ""
    segments = [urllib.parse.unquote(part) for part in path.strip("/").split("/") if part]
    language = "en"
    if segments and segments[0].lower() in SUPPORTED_LANGUAGES:
        language = segments.pop(0).lower()

    if len(segments) >= 2 and segments[0] == "product" and segments[1].isdigit():
        product = products_by_id.get(int(segments[1])) or {}
        return {
            "language": language,
            "category": str(product.get("category") or "").strip(),
            "subcategory": str(product.get("subcategory") or "").strip(),
            "page_type": "product",
        }

    if len(segments) >= 2 and segments[0] == "category":
        category = next(
            (value for value in get_all_categories() if _taxonomy_slug(value) == segments[1]),
            "",
        )
        subcategory = ""
        if category and len(segments) >= 3:
            subcategory = next(
                (value for value in get_subcategories(category) if _taxonomy_slug(value) == segments[2]),
                "",
            )
        return {
            "language": language,
            "category": category,
            "subcategory": subcategory,
            "page_type": "subcategory" if subcategory else "category",
        }

    return {"language": language, "category": "", "subcategory": "", "page_type": "other"}


def _build_search_console_signals(
    rows: List[Dict[str, Any]],
    products: List[Dict[str, Any]],
    profiles: Dict[tuple[str, str], Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], int, int]:
    """Aggregate Search Console evidence and build page-consistent signals."""
    products_by_id = {
        int(product.get("id") or product.get("product_id")): product
        for product in products
        if str(product.get("id") or product.get("product_id") or "").isdigit()
    }
    aggregates: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        query = str(row.get("query") or "").strip()
        normalized = normalize_term(query)
        page = str(row.get("page") or "").strip()
        source_date = str(row.get("date") or "").strip()
        if not normalized or not source_date:
            continue
        key = (normalized, page)
        item = aggregates.setdefault(key, {
            "query": query,
            "page": page,
            "clicks": 0,
            "impressions": 0,
            "position_weight": 0.0,
            "position_samples": [],
            "dates": set(),
        })
        impressions = max(0, int(row.get("impressions") or 0))
        position = max(0.0, float(row.get("position") or 0))
        item["clicks"] += max(0, int(row.get("clicks") or 0))
        item["impressions"] += impressions
        item["position_weight"] += position * impressions
        item["position_samples"].append(position)
        item["dates"].add(source_date)

    signal_groups: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    page_mismatches = 0
    for item in aggregates.values():
        query = item["query"]
        if not _is_useful_auto_seo_term(query):
            continue
        inferred = infer_seo_query_mapping(
            query,
            products,
            min_score=Config.SEO_EXTERNAL_SIGNAL_SCORE,
            profiles=profiles,
        )
        if not inferred:
            continue

        clicks = int(item["clicks"])
        impressions = int(item["impressions"])
        active_days = len(item["dates"])
        score = float(inferred.get("score") or 0)
        reason = str(inferred.get("reason") or "")
        strong_catalog_match = reason in {
            "catalog",
            "seo_term",
            "category",
            "explicit_short_term",
        } and score >= 94
        if not (clicks > 0 or impressions >= 3 or active_days >= 2 or (impressions > 0 and strong_catalog_match)):
            continue

        page_context = _search_console_page_context(item["page"], products_by_id)
        page_category = page_context["category"]
        page_subcategory = page_context["subcategory"]
        inferred_category = str(inferred.get("category") or "").strip()
        inferred_subcategory = str(inferred.get("subcategory") or "").strip()
        if page_category and inferred_category != page_category:
            page_mismatches += 1
            continue
        category = page_category or inferred_category
        subcategory = page_subcategory or inferred_subcategory
        language = page_context["language"]
        average_position = (
            item["position_weight"] / impressions
            if impressions > 0
            else sum(item["position_samples"]) / max(1, len(item["position_samples"]))
        )
        confidence = min(
            100,
            int(score)
            + min(3, clicks)
            + min(2, max(0, active_days - 1))
            + (1 if impressions >= 10 else 0),
        )
        key = (normalize_term(query), category, subcategory)
        signal = signal_groups.get(key)
        if signal is None:
            signal = {
                "term": query,
                "term_type": "search_console_query",
                "provider": "google_search_console",
                "source": item["page"][:120],
                "language": language,
                "category": category,
                "subcategory": subcategory,
                "confidence": confidence,
                "approved": True,
                "evidence_count": 0,
                "notes": {
                    "clicks": 0,
                    "impressions": 0,
                    "active_days": 0,
                    "position": average_position,
                    "pages": [],
                    "mapping_reason": reason,
                    "page_type": page_context["page_type"],
                },
            }
            signal_groups[key] = signal
        signal["confidence"] = max(int(signal["confidence"]), confidence)
        signal["evidence_count"] += max(1, clicks * 5 + impressions + active_days * 2)
        notes = signal["notes"]
        notes["clicks"] += clicks
        notes["impressions"] += impressions
        notes["active_days"] = max(int(notes["active_days"]), active_days)
        if item["page"] and item["page"] not in notes["pages"] and len(notes["pages"]) < 5:
            notes["pages"].append(item["page"])

    return list(signal_groups.values()), len(aggregates), page_mismatches


async def _import_search_console_signals(
    products: list[dict[str, Any]],
    profiles: dict[tuple[str, str], dict[str, Any]],
) -> int:
    """Import Search Console queries and convert confident matches into approved signals."""
    global _search_console_import_last_attempt

    now = _time.monotonic()
    if (
        _search_console_import_last_attempt
        and now - _search_console_import_last_attempt < _SEARCH_CONSOLE_IMPORT_INTERVAL_SECONDS
    ):
        logger.debug(
            "[search_console] skipped reason=throttled elapsed=%.0f interval=%d",
            now - _search_console_import_last_attempt,
            _SEARCH_CONSOLE_IMPORT_INTERVAL_SECONDS,
        )
        return 0

    setup = describe_search_console_setup(
        enabled=Config.SEARCH_CONSOLE_ENABLED,
        site_url=Config.SEARCH_CONSOLE_SITE_URL,
        service_account_json=Config.SEARCH_CONSOLE_SERVICE_ACCOUNT_JSON,
        service_account_file=Config.SEARCH_CONSOLE_SERVICE_ACCOUNT_FILE,
        oauth_client_json=Config.SEARCH_CONSOLE_OAUTH_CLIENT_JSON,
        oauth_client_file=Config.SEARCH_CONSOLE_OAUTH_CLIENT_FILE,
        oauth_token_json=Config.SEARCH_CONSOLE_OAUTH_TOKEN_JSON,
        oauth_token_file=Config.SEARCH_CONSOLE_OAUTH_TOKEN_FILE,
    )
    if not setup["configured"]:
        logger.info(
            "[search_console] skipped reason=not_configured enabled=%s site_url=%s auth=%s oauth_client_file=%s oauth_token_file=%s service_account_file=%s",
            setup["enabled"],
            setup["site_url"],
            setup["auth_source"],
            setup["oauth_client_file_exists"],
            setup["oauth_token_file_exists"],
            setup["service_account_file_exists"],
        )
        await _set_runtime_settings({
            "search_console_import_last_attempt_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
            "search_console_import_last_status": "not_configured",
            "search_console_import_last_rows": 0,
            "search_console_import_last_signals": 0,
            "search_console_import_last_media_rows": 0,
            "search_console_import_last_requests": 0,
            "search_console_import_last_property": "",
            "search_console_import_last_aggregates": 0,
            "search_console_import_last_page_mismatches": 0,
            "search_console_import_last_error": "not configured",
            "search_console_import_last_auth_source": setup["auth_source"],
        })
        return 0

    _search_console_import_last_attempt = now

    fetch_result = await fetch_search_console_data(
        enabled=Config.SEARCH_CONSOLE_ENABLED,
        site_url=Config.SEARCH_CONSOLE_SITE_URL,
        service_account_json=Config.SEARCH_CONSOLE_SERVICE_ACCOUNT_JSON,
        service_account_file=Config.SEARCH_CONSOLE_SERVICE_ACCOUNT_FILE,
        oauth_client_json=Config.SEARCH_CONSOLE_OAUTH_CLIENT_JSON,
        oauth_client_file=Config.SEARCH_CONSOLE_OAUTH_CLIENT_FILE,
        oauth_token_json=Config.SEARCH_CONSOLE_OAUTH_TOKEN_JSON,
        oauth_token_file=Config.SEARCH_CONSOLE_OAUTH_TOKEN_FILE,
        lookback_days=Config.SEARCH_CONSOLE_LOOKBACK_DAYS,
        row_limit=Config.SEARCH_CONSOLE_ROW_LIMIT,
    )
    rows = list(fetch_result.get("rows") or [])
    media_rows = list(fetch_result.get("media_rows") or [])
    fetch_status = str(fetch_result.get("status") or "api_error")
    fetch_error = str(fetch_result.get("error") or "")[:500]
    fetch_property = str(fetch_result.get("property_url") or "")[:300]
    fetch_requests = int(fetch_result.get("requests") or 0)
    if fetch_status not in {"ok", "ok_empty"}:
        logger.warning(
            "[search_console] import_failed status=%s auth=%s property=%s requests=%d error=%s",
            fetch_status,
            fetch_result.get("auth_source") or setup["auth_source"],
            fetch_property or setup["site_url"],
            fetch_requests,
            fetch_error,
        )
        await _set_runtime_settings({
            "search_console_import_last_attempt_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
            "search_console_import_last_status": fetch_status,
            "search_console_import_last_rows": 0,
            "search_console_import_last_signals": 0,
            "search_console_import_last_media_rows": 0,
            "search_console_import_last_requests": fetch_requests,
            "search_console_import_last_property": fetch_property,
            "search_console_import_last_error": fetch_error,
            "search_console_import_last_auth_source": fetch_result.get("auth_source") or setup["auth_source"],
        })
        return 0

    replace_since = (datetime.utcnow().date() - timedelta(days=Config.SEARCH_CONSOLE_LOOKBACK_DAYS)).isoformat()
    imported_media_rows = await _catalog_db.replace_search_console_media_events(
        media_rows,
        replace_since=replace_since,
    )
    if imported_media_rows or media_rows:
        _invalidate_admin_status_cache()
    if not rows:
        await _catalog_db.upsert_search_console_query_events([], replace_since=replace_since)
        logger.info(
            "[search_console] rows=0 stored=0 approved_signals=0 site_url=%s auth=%s lookback_days=%s row_limit=%s",
            setup["site_url"],
            setup["auth_source"],
            Config.SEARCH_CONSOLE_LOOKBACK_DAYS,
            Config.SEARCH_CONSOLE_ROW_LIMIT,
        )
        await _set_runtime_settings({
            "search_console_import_last_attempt_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
            "search_console_import_last_status": "ok_empty",
            "search_console_import_last_rows": 0,
            "search_console_import_last_signals": 0,
            "search_console_import_last_media_rows": imported_media_rows,
            "search_console_import_last_requests": fetch_requests,
            "search_console_import_last_property": fetch_property,
            "search_console_import_last_error": fetch_error,
            "search_console_import_last_auth_source": fetch_result.get("auth_source") or setup["auth_source"],
        })
        return 0

    imported_rows = await _catalog_db.upsert_search_console_query_events(
        rows,
        replace_since=replace_since,
    )
    if imported_rows:
        _invalidate_admin_status_cache()
    signals, aggregate_count, page_mismatches = await asyncio.to_thread(
        _build_search_console_signals,
        rows,
        products,
        profiles,
    )

    imported_signals = await _catalog_db.upsert_external_seo_signals(
        signals,
        provider="google_search_console",
        source="search_console",
        approved=True,
    )
    if imported_signals:
        _invalidate_admin_status_cache()
    logger.info(
        "[search_console] rows=%d stored=%d aggregates=%d approved_signals=%d media_rows=%d page_mismatches=%d property=%s auth=%s requests=%d partial=%s",
        len(rows),
        imported_rows,
        aggregate_count,
        imported_signals,
        imported_media_rows,
        page_mismatches,
        fetch_property or setup["site_url"],
        fetch_result.get("auth_source") or setup["auth_source"],
        fetch_requests,
        bool(fetch_result.get("partial")),
    )
    await _set_runtime_settings({
        "search_console_import_last_attempt_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "search_console_import_last_status": "ok",
        "search_console_import_last_rows": imported_rows,
        "search_console_import_last_signals": imported_signals,
        "search_console_import_last_media_rows": imported_media_rows,
        "search_console_import_last_requests": fetch_requests,
        "search_console_import_last_property": fetch_property,
        "search_console_import_last_aggregates": aggregate_count,
        "search_console_import_last_page_mismatches": page_mismatches,
        "search_console_import_last_error": fetch_error,
        "search_console_import_last_auth_source": fetch_result.get("auth_source") or setup["auth_source"],
    })
    return imported_signals


async def _seo_auto_learn_once() -> int:
    """Approve high-confidence low/no-result search mappings."""
    if not Config.SEO_AUTO_LEARN_ENABLED:
        return 0

    products = await _catalog_db.get_all_products_for_search()
    profiles = build_taxonomy_profiles(products)
    external_imports = 0
    external_promoted = 0
    retired_noise = 0
    try:
        retired_noise = await _catalog_db.retire_off_audience_seo_mappings()
        if retired_noise:
            _invalidate_seo_query_mapping_cache()
            _invalidate_admin_status_cache()
            logger.info("[seo_auto_learn] retired_off_audience_mappings=%d", retired_noise)
        external_imports += await _import_external_signal_file(products, profiles)
        external_imports += await _import_search_console_signals(products, profiles)
        external_promoted = await _promote_approved_external_signals()
    except Exception as exc:
        logger.warning("[seo_external] import_or_promote_error error=%s", exc)

    candidates = await _catalog_db.get_seo_auto_learning_candidates(
        limit=Config.SEO_AUTO_LEARN_MAX_PER_RUN,
        min_count=Config.SEO_AUTO_LEARN_MIN_COUNT,
        lookback_hours=Config.SEO_AUTO_LEARN_LOOKBACK_HOURS,
        max_matched_count=Config.SEO_AUTO_LEARN_MAX_MATCHED_COUNT,
    )
    if not candidates:
        logger.info(
            "[seo_auto_learn] candidates=0 max_matched=%d external_imports=%d external_promoted=%d",
            Config.SEO_AUTO_LEARN_MAX_MATCHED_COUNT,
            external_imports,
            external_promoted,
        )
        await _set_runtime_settings({
            "seo_auto_learn_last_candidates": 0,
            "seo_auto_learn_last_skipped": 0,
            "seo_auto_learn_last_suppressed": 0,
            "seo_auto_learn_last_retired_noise": retired_noise,
        })
        return external_imports + external_promoted + retired_noise

    approvals = []
    suppressed = []
    skipped = 0

    for item in candidates:
        query = str(item.get("query") or item.get("normalized_query") or "").strip()
        if not query:
            continue
        inferred = infer_seo_query_mapping(
            query,
            products,
            min_score=Config.SEO_AUTO_LEARN_SCORE,
            profiles=profiles,
        )
        if not inferred:
            skipped += 1
            suppressed.append({**item, "query": query, "reason": "no_confident_mapping"})
            continue
        if not has_search_audience_intent(query) and inferred.get("reason") != "catalog":
            skipped += 1
            suppressed.append({**item, "query": query, "reason": "off_audience"})
            continue

        # One-off queries are approved only if they match the explicit SEO
        # vocabulary. Catalog-only fuzzy guesses need repeated evidence.
        if int(item.get("count") or 0) < 2 and inferred.get("reason") == "catalog":
            skipped += 1
            suppressed.append({**item, "query": query, "reason": "single_catalog_guess"})
            continue

        learned_terms = await _expand_auto_learn_mapping_variants(query, item.get("language") or "en")
        for learned_term in learned_terms:
            approvals.append({
                "term": learned_term["term"],
                "language": learned_term["language"],
                "category": inferred["category"],
                "subcategory": inferred["subcategory"],
            })
        logger.info(
            "[seo_auto_learn] approve query=%r variants=%d category=%s subcategory=%s score=%s reason=%s count=%s best_matched=%s",
            query,
            len(learned_terms),
            inferred["category"],
            inferred["subcategory"] or "",
            inferred.get("score"),
            inferred.get("reason"),
            item.get("count"),
            item.get("best_matched_count"),
        )

    if not approvals:
        if skipped:
            suppressed_count = await _catalog_db.suppress_seo_auto_learning_candidates(
                suppressed,
                cooldown_hours=Config.SEO_AUTO_LEARN_SUPPRESSION_HOURS,
            )
            logger.info(
                "[seo_auto_learn] skipped=%d suppressed=%d reason=no_confident_mapping external_imports=%d external_promoted=%d",
                skipped,
                suppressed_count,
                external_imports,
                external_promoted,
            )
            await _set_runtime_settings({
                "seo_auto_learn_last_candidates": len(candidates),
                "seo_auto_learn_last_skipped": skipped,
                "seo_auto_learn_last_suppressed": suppressed_count,
                "seo_auto_learn_last_retired_noise": retired_noise,
            })
        return external_imports + external_promoted + retired_noise

    approved = await _catalog_db.upsert_seo_query_mappings(
        approvals,
        source="auto_learn",
        approved=True,
    )
    if approved:
        _invalidate_seo_query_mapping_cache()
        await _publish_seo_mapping_changes(approvals)
    suppressed_count = 0
    if suppressed:
        suppressed_count = await _catalog_db.suppress_seo_auto_learning_candidates(
            suppressed,
            cooldown_hours=Config.SEO_AUTO_LEARN_SUPPRESSION_HOURS,
        )
    logger.info(
        "[seo_auto_learn] approved=%d candidates=%d skipped=%d suppressed=%d max_matched=%d external_imports=%d external_promoted=%d",
        approved,
        len(candidates),
        skipped,
        suppressed_count,
        Config.SEO_AUTO_LEARN_MAX_MATCHED_COUNT,
        external_imports,
        external_promoted,
    )
    await _set_runtime_settings({
        "seo_auto_learn_last_candidates": len(candidates),
        "seo_auto_learn_last_skipped": skipped,
        "seo_auto_learn_last_suppressed": suppressed_count,
        "seo_auto_learn_last_retired_noise": retired_noise,
    })
    return approved + external_imports + external_promoted + retired_noise


def _get_seo_auto_learn_lock() -> asyncio.Lock:
    global _seo_auto_learn_lock
    if _seo_auto_learn_lock is None:
        _seo_auto_learn_lock = asyncio.Lock()
    return _seo_auto_learn_lock


async def _seo_auto_learn_loop():
    """Background task: learn safe SEO mappings from repeated low/no-result searches."""
    if not Config.SEO_AUTO_LEARN_ENABLED:
        logger.info("[seo_auto_learn] disabled")
        return

    await asyncio.sleep(_SEO_AUTO_LEARN_STARTUP_DELAY_SECONDS)
    while True:
        try:
            lock = _get_seo_auto_learn_lock()
            if lock.locked():
                logger.info("[seo_auto_learn] skipped reason=previous_run_active")
            else:
                started = _time.monotonic()
                async with lock:
                    learned = await _seo_auto_learn_once()
                duration_ms = (_time.monotonic() - started) * 1000
                logger.info(
                    "[seo_auto_learn] run_complete changed=%d duration_ms=%.1f lookback_hours=%d score=%d max_matched=%d external_score=%d",
                    learned,
                    duration_ms,
                    Config.SEO_AUTO_LEARN_LOOKBACK_HOURS,
                    Config.SEO_AUTO_LEARN_SCORE,
                    Config.SEO_AUTO_LEARN_MAX_MATCHED_COUNT,
                    Config.SEO_EXTERNAL_SIGNAL_SCORE,
                )
                await _set_runtime_settings({
                    "seo_auto_learn_last_run_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
                    "seo_auto_learn_last_status": "ok",
                    "seo_auto_learn_last_changed": learned,
                    "seo_auto_learn_last_duration_ms": round(duration_ms, 1),
                })
        except Exception as e:
            logger.warning("[seo_auto_learn] loop_error error=%s", e)
            await _set_runtime_settings({
                "seo_auto_learn_last_run_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
                "seo_auto_learn_last_status": "error",
                "seo_auto_learn_last_error": str(e)[:300],
            })
        await asyncio.sleep(Config.SEO_AUTO_LEARN_INTERVAL_SECONDS)


async def _seo_trends_loop():
    """Background task: ingest a small batch of Google Trends signals."""
    if not Config.SEO_TRENDS_ENABLED:
        logger.info("[seo_trends] disabled")
        return

    await asyncio.sleep(30)
    while True:
        try:
            lock = _get_seo_trends_lock()
            if lock.locked():
                logger.info("[seo_trends] skipped reason=previous_run_active")
            else:
                started = _time.monotonic()
                async with lock:
                    products = await _catalog_db.get_all_products_for_search()
                    profiles = build_taxonomy_profiles(products)
                    imported = await _import_google_trends_signals(products, profiles)
                    promoted = await _promote_approved_external_signals()
                duration_ms = (_time.monotonic() - started) * 1000
                logger.info(
                    "[seo_trends] run_complete imported=%d promoted=%d duration_ms=%.1f limit_per_geo=%d max_terms=%d",
                    imported,
                    promoted,
                    duration_ms,
                    Config.SEO_TRENDS_LIMIT_PER_GEO,
                    Config.SEO_TRENDS_MAX_TERMS_PER_RUN,
                )
                await _set_runtime_settings({
                    "seo_trends_last_run_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
                    "seo_trends_last_status": "ok",
                    "seo_trends_last_imported": imported,
                    "seo_trends_last_promoted": promoted,
                    "seo_trends_last_duration_ms": round(duration_ms, 1),
                })
        except Exception as exc:
            logger.warning("[seo_trends] loop_error error=%s", exc)
            await _set_runtime_settings({
                "seo_trends_last_run_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
                "seo_trends_last_status": "error",
                "seo_trends_last_error": str(exc)[:300],
            })
        await asyncio.sleep(Config.SEO_TRENDS_INTERVAL_SECONDS)


async def _seo_translation_warmup_once() -> int:
    """Warm a small rotating batch of persistent product-page translations."""
    raw_offset = await _catalog_db.get_bot_setting("seo_translation_warmup_offset")
    try:
        offset = max(0, int(raw_offset or 0))
    except (TypeError, ValueError):
        offset = 0
    products = await _catalog_db.get_all_products_excluding_categories(
        EXCLUDED_FROM_ALL_PRODUCTS,
        limit=_SEO_TRANSLATION_WARMUP_BATCH_SIZE,
        offset=offset,
    )
    if not products and offset:
        offset = 0
        products = await _catalog_db.get_all_products_excluding_categories(
            EXCLUDED_FROM_ALL_PRODUCTS,
            limit=_SEO_TRANSLATION_WARMUP_BATCH_SIZE,
            offset=0,
        )
    if not products:
        return 0

    texts: List[str] = []
    warmed_taxonomies = set()
    for product in products:
        _title, _desc, lines = _caption_parts(product.get("caption") or "")
        texts.extend(lines)
        taxonomy = (product.get("category") or "", product.get("subcategory") or "")
        if taxonomy not in warmed_taxonomies and taxonomy[0]:
            warmed_taxonomies.add(taxonomy)
            texts.extend(get_terms_for_taxonomy(*taxonomy, limit=Config.SEO_SIDE_NOTE_LIMIT))
            texts.append(build_taxonomy_intro(*taxonomy))
    texts = list(dict.fromkeys(text for text in texts if text))
    if texts:
        await asyncio.gather(*(
            translation_service.translate_batch(texts, language, source_lang="en")
            for language in SUPPORTED_LANGUAGES
            if language != "en"
        ))
    await _catalog_db.set_bot_setting(
        "seo_translation_warmup_offset",
        str(offset + len(products)),
    )
    return len(products)


async def _seo_translation_warmup_loop():
    await asyncio.sleep(_SEO_TRANSLATION_WARMUP_STARTUP_SECONDS)
    while True:
        try:
            warmed = await _seo_translation_warmup_once()
            logger.info("[seo_translation] warmed_products=%d", warmed)
        except Exception as exc:
            logger.warning("[seo_translation] warmup_error error=%s", exc)
        await asyncio.sleep(_SEO_TRANSLATION_WARMUP_INTERVAL_SECONDS)


async def _visitor_analytics_prune_loop():
    await asyncio.sleep(300)
    while True:
        try:
            result = await _catalog_db.prune_visitor_analytics(
                raw_retention_days=_VISITOR_ANALYTICS_RETENTION_DAYS,
            )
            if result.get("raw_removed"):
                logger.info("[visitor_analytics] pruned=%s", result)
        except Exception as exc:
            logger.warning("[visitor_analytics] prune_failed error=%s", exc)
        await asyncio.sleep(_VISITOR_ANALYTICS_PRUNE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - startup and shutdown."""
    global _bot_applications, _webhook_secret_tokens
    global _bot_applications_by_registry_id, _runtime_index_to_registry_id
    global _visitor_event_queue

    # Startup
    logger.info("Starting webhook server...")
    startup_cleanup()
    clear_bot_runtime_state()
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
    _bot_applications_by_registry_id = {}
    _runtime_index_to_registry_id = {}
    
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
    _validate_bot_slot_registry(len(bot_tokens))
    
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

    logger.debug("[bots_config] Secret-token overrides disabled; bot_registry is the only webhook-secret source")

    # Retire stale bot-specific database records after the runtime list is known.
    # This keeps admin user menus tied to the current 10 loaded bots instead of
    # old usernames left behind by token switches or removed bot slots.
    try:
        cleanup_stats = await _catalog_db.forget_inactive_bot_memory(get_bot_usernames())
        if any(value for key, value in cleanup_stats.items() if key != "active_bots"):
            logger.info("[bot_cleanup] startup retired inactive bot memory stats=%s", cleanup_stats)
    except Exception as exc:
        logger.warning("[bot_cleanup] startup cleanup skipped: %s", exc, exc_info=True)
    
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

    await _log_setup_check()

    # Start background maintenance tasks
    _visitor_event_queue = asyncio.Queue(maxsize=_VISITOR_EVENT_QUEUE_MAX)
    _bg_tasks = [
        asyncio.create_task(_visitor_event_writer_loop(), name="visitor_event_writer"),
        asyncio.create_task(_visitor_analytics_prune_loop(), name="visitor_analytics_prune"),
        asyncio.create_task(_media_cache_prune_loop(), name="media_cache_prune"),
        asyncio.create_task(_admin_cache_cleanup_loop(), name="admin_cache_cleanup"),
        asyncio.create_task(_wal_checkpoint_loop(), name="wal_checkpoint"),
        asyncio.create_task(_seo_auto_learn_loop(), name="seo_auto_learn"),
        asyncio.create_task(_seo_trends_loop(), name="seo_trends"),
        asyncio.create_task(_seo_translation_warmup_loop(), name="seo_translation_warmup"),
        asyncio.create_task(_indexnow_loop(), name="indexnow"),
        asyncio.create_task(_search_console_sitemap_loop(), name="search_console_sitemap"),
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
    _visitor_event_queue = None

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
        path = request.url.path
        
        # Add cache headers for static assets
        if path.startswith('/assets/'):
            # Cache CSS and JS for 1 hour to reduce backend load and decrease latency
            if path.endswith(('.js', '.css')):
                response.headers['Cache-Control'] = 'public, max-age=3600'
            # Cache images for 1 day (can be cached longer as they rarely change)
            elif path.endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico')):
                response.headers['Cache-Control'] = 'public, max-age=86400'
            elif path.endswith('.html'):
                response.headers['Cache-Control'] = 'no-cache'
        elif path in ('/webapp', '/webapp/', '/webapp/index.html'):
            # Allow conditional revalidation for the webapp entry point
            response.headers['Cache-Control'] = 'no-cache'
        elif path == '/admin' or path.startswith('/admin/'):
            response.headers['Cache-Control'] = 'no-store'

        if path.startswith(('/api/', '/admin', '/admin-assets/', '/webhook/', '/webapp')):
            response.headers['X-Robots-Tag'] = 'noindex, nofollow'

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
if _is_admin_dashboard_enabled():
    app.mount("/admin-assets", StaticFiles(directory="admin"), name="admin_assets")

_STOREFRONT_BOT_USERNAME = os.getenv("BOT_USERNAME", "").lstrip("@")
_PUBLIC_PRODUCT_BOT_INDEX = 1
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

# Default Open Graph image with the same standalone football emoji used by the home page.
# Must be an absolute HTTPS URL — data: URIs are rejected by all social media crawlers
# (WhatsApp, iMessage, Twitter/X, Facebook, Discord, Slack, Telegram, etc.).
_SITE_ICON_VERSION = "20260723-football-emoji-v5"
_DEFAULT_OG_IMAGE = f"{_STOREFRONT_DOMAIN}/og-image.png?v={_SITE_ICON_VERSION}"
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


def _taxonomy_slug(value: str) -> str:
    """Generate a stable lowercase slug for categories and subcategories."""
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or "all"


def _absolute_storefront_url(path: str = "") -> str:
    if not path:
        return _STOREFRONT_DOMAIN or ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{_STOREFRONT_DOMAIN}{normalized}" if _STOREFRONT_DOMAIN else normalized


def _canonical_product_path(product_id: int, caption: str) -> str:
    return f"/product/{product_id}/{_generate_slug(caption)}"


def _canonical_product_url(product_id: int, caption: str) -> str:
    return _absolute_storefront_url(_canonical_product_path(product_id, caption))


def _canonical_category_path(category: str) -> str:
    return f"/category/{_taxonomy_slug(category)}"


def _canonical_subcategory_path(category: str, subcategory: str) -> str:
    return f"{_canonical_category_path(category)}/{_taxonomy_slug(subcategory)}"


def _canonical_category_url(category: str) -> str:
    return _absolute_storefront_url(_canonical_category_path(category))


def _canonical_subcategory_url(category: str, subcategory: str) -> str:
    return _absolute_storefront_url(_canonical_subcategory_path(category, subcategory))


_SEO_OG_LOCALES = {
    "en": "en_US",
    "de": "de_DE",
    "nl": "nl_NL",
    "it": "it_IT",
    "es": "es_ES",
    "fr": "fr_FR",
}

_SEO_UI = {
    "en": {
        "home": "Home", "menu": "Tom Brady Menu",
        "products_available": "products available", "order_via": "Order via Telegram",
        "dm_order": "DM to Order", "open_bot": "Open Product in Bot",
        "browse_bot": "Browse Full Catalog in Telegram", "related_products": "Related products",
        "new_products": "New products",
        "related_searches": "Related searches", "back_home": "Back to Homepage",
        "previous": "Previous", "next": "Next", "page": "Page",
        "media_preview": "Product media preview", "all_media": "All product media",
        "open_video": "Open video", "open_media": "Open media", "media": "media",
        "catalog_title": "Premium Cannabis Storefront",
        "catalog_desc": "Browse our premium cannabis catalog and current product listings.",
        "browse_related": "Browse Related Products", "related": "Related",
        "search": "Search", "search_placeholder": "Search products",
        "search_results": "Search results", "searching": "Searching...",
        "no_results": "No matching products found.", "search_error": "Search is temporarily unavailable.",
    },
    "de": {
        "home": "Startseite", "menu": "Tom Brady Menü",
        "products_available": "Produkte verfügbar", "order_via": "Bestellung über Telegram",
        "dm_order": "Zum Bestellen schreiben", "open_bot": "Produkt im Bot öffnen",
        "browse_bot": "Vollständigen Katalog in Telegram öffnen", "related_products": "Ähnliche Produkte",
        "new_products": "Neue Produkte",
        "related_searches": "Ähnliche Suchanfragen", "back_home": "Zurück zur Startseite",
        "previous": "Zurück", "next": "Weiter", "page": "Seite",
        "media_preview": "Produktmedien", "all_media": "Alle Produktmedien",
        "open_video": "Video öffnen", "open_media": "Medium öffnen", "media": "Medium",
        "catalog_title": "Premium-Cannabis-Katalog", "catalog_desc": "Entdecke unseren aktuellen Premium-Cannabis-Katalog.",
        "browse_related": "Ähnliche Produkte ansehen", "related": "Verwandt",
        "search": "Suchen", "search_placeholder": "Produkte suchen",
        "search_results": "Suchergebnisse", "searching": "Suche...",
        "no_results": "Keine passenden Produkte gefunden.", "search_error": "Die Suche ist vorübergehend nicht verfügbar.",
    },
    "nl": {
        "home": "Home", "menu": "Tom Brady Menu",
        "products_available": "producten beschikbaar", "order_via": "Bestellen via Telegram",
        "dm_order": "Stuur bericht om te bestellen", "open_bot": "Open product in bot",
        "browse_bot": "Volledige catalogus in Telegram", "related_products": "Vergelijkbare producten",
        "new_products": "Nieuwe producten",
        "related_searches": "Gerelateerde zoekopdrachten", "back_home": "Terug naar home",
        "previous": "Vorige", "next": "Volgende", "page": "Pagina",
        "media_preview": "Productmedia", "all_media": "Alle productmedia",
        "open_video": "Video openen", "open_media": "Media openen", "media": "media",
        "catalog_title": "Premium cannabiscatalogus", "catalog_desc": "Bekijk onze actuele premium cannabiscatalogus.",
        "browse_related": "Gerelateerde producten bekijken", "related": "Gerelateerd",
        "search": "Zoeken", "search_placeholder": "Producten zoeken",
        "search_results": "Zoekresultaten", "searching": "Zoeken...",
        "no_results": "Geen overeenkomende producten gevonden.", "search_error": "Zoeken is tijdelijk niet beschikbaar.",
    },
    "it": {
        "home": "Home", "menu": "Menu Tom Brady",
        "products_available": "prodotti disponibili", "order_via": "Ordina tramite Telegram",
        "dm_order": "Messaggio per ordinare", "open_bot": "Apri prodotto nel bot",
        "browse_bot": "Apri il catalogo completo su Telegram", "related_products": "Prodotti correlati",
        "new_products": "Nuovi prodotti",
        "related_searches": "Ricerche correlate", "back_home": "Torna alla home",
        "previous": "Precedente", "next": "Successiva", "page": "Pagina",
        "media_preview": "Media del prodotto", "all_media": "Tutti i media del prodotto",
        "open_video": "Apri video", "open_media": "Apri media", "media": "media",
        "catalog_title": "Catalogo cannabis premium", "catalog_desc": "Esplora il nostro catalogo cannabis premium aggiornato.",
        "browse_related": "Sfoglia prodotti correlati", "related": "Correlato",
        "search": "Cerca", "search_placeholder": "Cerca prodotti",
        "search_results": "Risultati di ricerca", "searching": "Ricerca...",
        "no_results": "Nessun prodotto corrispondente trovato.", "search_error": "La ricerca non è temporaneamente disponibile.",
    },
    "es": {
        "home": "Inicio", "menu": "Menú Tom Brady",
        "products_available": "productos disponibles", "order_via": "Pedidos por Telegram",
        "dm_order": "Mensaje para pedir", "open_bot": "Abrir producto en el bot",
        "browse_bot": "Abrir catálogo completo en Telegram", "related_products": "Productos relacionados",
        "new_products": "Nuevos productos",
        "related_searches": "Búsquedas relacionadas", "back_home": "Volver al inicio",
        "previous": "Anterior", "next": "Siguiente", "page": "Página",
        "media_preview": "Contenido del producto", "all_media": "Todo el contenido del producto",
        "open_video": "Abrir vídeo", "open_media": "Abrir contenido", "media": "contenido",
        "catalog_title": "Catálogo de cannabis premium", "catalog_desc": "Explora nuestro catálogo actualizado de cannabis premium.",
        "browse_related": "Ver productos relacionados", "related": "Relacionado",
        "search": "Buscar", "search_placeholder": "Buscar productos",
        "search_results": "Resultados de búsqueda", "searching": "Buscando...",
        "no_results": "No se encontraron productos coincidentes.", "search_error": "La búsqueda no está disponible temporalmente.",
    },
    "fr": {
        "home": "Accueil", "menu": "Menu Tom Brady",
        "products_available": "produits disponibles", "order_via": "Commander via Telegram",
        "dm_order": "Message pour commander", "open_bot": "Ouvrir le produit dans le bot",
        "browse_bot": "Ouvrir le catalogue complet sur Telegram", "related_products": "Produits similaires",
        "new_products": "Nouveaux produits",
        "related_searches": "Recherches associées", "back_home": "Retour à l'accueil",
        "previous": "Précédent", "next": "Suivant", "page": "Page",
        "media_preview": "Médias du produit", "all_media": "Tous les médias du produit",
        "open_video": "Ouvrir la vidéo", "open_media": "Ouvrir le média", "media": "média",
        "catalog_title": "Catalogue de cannabis premium", "catalog_desc": "Découvrez notre catalogue actuel de cannabis premium.",
        "browse_related": "Voir les produits associés", "related": "Associé",
        "search": "Rechercher", "search_placeholder": "Rechercher des produits",
        "search_results": "Résultats de recherche", "searching": "Recherche...",
        "no_results": "Aucun produit correspondant trouvé.", "search_error": "La recherche est temporairement indisponible.",
    },
}


def _normalize_seo_language(language: str) -> str:
    language = str(language or "en").split("-", 1)[0].lower()
    return language if language in SUPPORTED_LANGUAGES else "en"


def _require_localized_language(language: str) -> str:
    clean = str(language or "").split("-", 1)[0].lower()
    if clean not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Language not found")
    return clean


def _seo_ui(language: str) -> Dict[str, str]:
    return _SEO_UI[_normalize_seo_language(language)]


def _localized_path(path: str, language: str) -> str:
    """Prefix a public crawlable path while preserving query parameters."""
    language = _normalize_seo_language(language)
    parsed = urllib.parse.urlsplit(path or "/")
    clean_path = parsed.path or "/"
    for code in SUPPORTED_LANGUAGES:
        if code != "en" and (clean_path == f"/{code}" or clean_path.startswith(f"/{code}/")):
            clean_path = clean_path[len(code) + 1:] or "/"
            if not clean_path.startswith("/"):
                clean_path = f"/{clean_path}"
            break
    if language != "en":
        clean_path = f"/{language}" if clean_path == "/" else f"/{language}{clean_path}"
    return urllib.parse.urlunsplit(("", "", clean_path, parsed.query, ""))


def _localized_url(path: str, language: str) -> str:
    return _absolute_storefront_url(_localized_path(path, language))


def _paginated_path(path: str, page: int) -> str:
    return path if page <= 1 else f"{path}?page={page}"


def _seo_page_number(request: Request) -> int:
    try:
        return max(1, min(int(request.query_params.get("page") or 1), 10000))
    except (TypeError, ValueError):
        return 1


def _webapp_search_cache_key(query: str, language: str, limit: int, offset: int) -> str:
    return f"{_normalize_seo_language(language)}|{limit}|{offset}|{normalize_text(query)}"


def _get_webapp_search_cache(key: str) -> Optional[dict]:
    now = _time.monotonic()
    cached = _webapp_search_cache.get(key)
    if not cached:
        return None
    cached_at, payload = cached
    if now - cached_at > _WEBAPP_SEARCH_CACHE_TTL_SECONDS:
        _webapp_search_cache.pop(key, None)
        return None
    _webapp_search_cache.move_to_end(key)
    return dict(payload)


def _set_webapp_search_cache(key: str, payload: dict) -> None:
    _webapp_search_cache[key] = (_time.monotonic(), dict(payload))
    _webapp_search_cache.move_to_end(key)
    while len(_webapp_search_cache) > _WEBAPP_SEARCH_CACHE_MAX:
        _webapp_search_cache.popitem(last=False)


def _seo_alternate_links(path: str) -> List[Dict[str, str]]:
    links = [
        {"language": language, "href": _localized_url(path, language)}
        for language in SUPPORTED_LANGUAGES
    ]
    links.append({"language": "x-default", "href": _localized_url(path, "en")})
    return links


def _sitemap_alternate_xml(path: str) -> str:
    return "".join(
        f'    <xhtml:link rel="alternate" hreflang="{html.escape(item["language"])}" href="{html.escape(item["href"])}" />\n'
        for item in _seo_alternate_links(path)
    )


async def _translate_seo_text(text: str, language: str) -> str:
    language = _normalize_seo_language(language)
    if language == "en" or not str(text or "").strip():
        return text
    return await translation_service.translate(str(text), language, source_lang="en")


async def _translate_seo_lines(lines: Iterable[str], language: str) -> List[str]:
    values = [str(line) for line in lines if str(line or "").strip()]
    if _normalize_seo_language(language) == "en" or not values:
        return values
    return await translation_service.translate_batch(values, language, source_lang="en")


def _configured_bot_username(bot_index: int) -> str:
    """Return the current runtime username for a configured bot slot."""
    apps = _bot_applications or getattr(app.state, "bot_apps", []) or []
    if 0 <= bot_index < len(apps):
        bot_app = apps[bot_index]
        bot = getattr(bot_app, "bot", None) if bot_app is not None else None
        username = getattr(bot, "username", None)
        if username:
            return str(username).lstrip("@")

    slot = _bot_slots.get(bot_index)
    if slot and slot.telegram_username:
        return str(slot.telegram_username).lstrip("@")

    return ""


def _public_product_bot_username() -> str:
    """Use the second configured bot for public product Telegram deep links."""
    return _configured_bot_username(_PUBLIC_PRODUCT_BOT_INDEX) or _STOREFRONT_BOT_USERNAME


def _public_product_bot_url(product_id: int) -> Optional[str]:
    return build_bot_product_url(product_id, _public_product_bot_username())


def _public_catalog_bot_url() -> str:
    return build_bot_start_url(_public_product_bot_username(), "start") or f"https://t.me/{_STOREFRONT_BOT_USERNAME}"


def _caption_parts(caption: str) -> tuple[str, str, list[str]]:
    lines = [line.strip() for line in (caption or "").splitlines() if line.strip()]
    title = lines[0] if lines else "Product"
    body_lines = lines[1:] or lines[:1]
    desc = " ".join(body_lines).strip() or title
    if len(desc) > 160:
        desc = desc[:157].rstrip() + "..."
    return title, desc, body_lines


def _card_preview_title(caption: str, *, limit: int = 54) -> str:
    """Shorten grid-card labels without changing product detail copy."""
    title = _caption_parts(caption or "")[0].strip()
    if not title:
        return "Product"
    if len(title) <= limit:
        return title
    cut = title[:limit].rsplit(" ", 1)[0].strip()
    return f"{cut or title[:limit].rstrip()}..."


def _dedupe_seo_phrases(values: Iterable[str], limit: int = 12) -> list[str]:
    seen = set()
    phrases: list[str] = []
    for value in values:
        clean = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n-_|,.")
        key = normalize_term(clean)
        if not key or key in seen:
            continue
        seen.add(key)
        phrases.append(clean[:120])
        if len(phrases) >= limit:
            break
    return phrases


def _product_search_phrases(
    *,
    title: str,
    caption: str,
    category: str = "",
    subcategory: str = "",
    seo_terms: Optional[Iterable[str]] = None,
    language: str = "en",
) -> list[str]:
    """Build visible, product-specific search phrases for crawlable product pages."""
    lines = [line.strip() for line in (caption or "").splitlines() if line.strip()]
    first_line = lines[0] if lines else title
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", first_line)
    first_line_short = " ".join(words[:10])
    language = _normalize_seo_language(language)
    category_display = get_category_display_name(category, language) if category else ""
    subcategory_display = get_subcategory_display_name(subcategory, language) if subcategory else ""
    taxonomy_label = " ".join(part for part in [subcategory_display, category_display] if part).strip()

    candidates = [
        first_line,
        first_line_short,
        title,
        f"{title} {taxonomy_label}",
        f"{first_line_short} {taxonomy_label}",
    ]
    for term in list(seo_terms or [])[:4]:
        candidates.append(f"{title} {term}")
    return _dedupe_seo_phrases(candidates, limit=10)


_SEO_BRAND_NAME = "Top Shelf Cali Plug"
_SEO_TITLE_MAX_LENGTH = 68


def _clean_seo_title_part(value: str) -> str:
    """Normalize a short phrase for use in search result titles."""
    text = re.sub(r"\s+", " ", value or "").strip()
    text = re.sub(r"\s+[|/-]\s+Top\s+Shelf\s+Cali\s+Plug\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"_+", " ", text)
    text = re.sub(r"[^\w\s&+.'/-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(" \t\r\n-_|,.")
    return text or "Product"


def _title_already_contains(title: str, part: str) -> bool:
    title_norm = normalize_term(title)
    part_norm = normalize_term(part)
    if not part_norm:
        return False
    variants = {part_norm}
    if len(part_norm) > 3 and part_norm.endswith("s"):
        variants.add(part_norm[:-1])
    return any(variant in title_norm for variant in variants)


def _fit_branded_seo_title(prefix: str) -> str:
    prefix = _clean_seo_title_part(prefix)
    suffix = f" | {_SEO_BRAND_NAME}"
    max_prefix = max(24, _SEO_TITLE_MAX_LENGTH - len(suffix))
    if len(prefix) > max_prefix:
        prefix = prefix[: max_prefix - 3].rstrip(" -_|,.") + "..."
    return f"{prefix}{suffix}"


def _build_product_seo_title(title: str, category: str = "", subcategory: str = "", language: str = "en") -> str:
    parts = [_clean_seo_title_part(title)]
    language = _normalize_seo_language(language)
    for value in (get_subcategory_display_name(subcategory, language) if subcategory else "",
                  get_category_display_name(category, language) if category else ""):
        cleaned = _clean_seo_title_part(value)
        if cleaned and cleaned != "Product" and not _title_already_contains(" ".join(parts), cleaned):
            parts.append(cleaned)
    return _fit_branded_seo_title(" ".join(parts))


def _build_product_seo_description(title: str, desc: str, category: str = "", subcategory: str = "", language: str = "en") -> str:
    """Create a concise, product-specific meta description without keyword stuffing."""
    parts: list[str] = []
    language = _normalize_seo_language(language)
    category_display = get_category_display_name(category, language) if category else ""
    subcategory_display = get_subcategory_display_name(subcategory, language) if subcategory else ""
    taxonomy_label = " ".join(part for part in [subcategory_display, category_display] if part).strip()
    localized_openers = {
        "en": f"{title} in our {taxonomy_label} menu." if taxonomy_label else f"{title} on the Top Shelf Cali Plug menu.",
        "de": f"{title} in unserem {taxonomy_label}-Menü." if taxonomy_label else f"{title} im Top Shelf Cali Plug Menü.",
        "nl": f"{title} in ons {taxonomy_label}-menu." if taxonomy_label else f"{title} in het Top Shelf Cali Plug-menu.",
        "it": f"{title} nel nostro menu {taxonomy_label}." if taxonomy_label else f"{title} nel menu Top Shelf Cali Plug.",
        "es": f"{title} en nuestro menú de {taxonomy_label}." if taxonomy_label else f"{title} en el menú Top Shelf Cali Plug.",
        "fr": f"{title} dans notre menu {taxonomy_label}." if taxonomy_label else f"{title} dans le menu Top Shelf Cali Plug.",
    }
    parts.append(localized_openers[language])
    if desc and not _title_already_contains(desc, title):
        parts.append(desc)
    parts.append({
        "en": "View current product details and related listings.",
        "de": "Aktuelle Produktdetails und ähnliche Angebote ansehen.",
        "nl": "Bekijk actuele productdetails en vergelijkbare aanbiedingen.",
        "it": "Visualizza i dettagli attuali e i prodotti correlati.",
        "es": "Consulta los detalles actuales y productos relacionados.",
        "fr": "Consultez les détails actuels et les produits similaires.",
    }[language])
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if len(text) > 160:
        text = text[:157].rstrip(" -.,") + "..."
    return text


def _build_category_seo_title(category_display: str) -> str:
    return _fit_branded_seo_title(f"{_clean_seo_title_part(category_display)} Menu")


def _build_subcategory_seo_title(category_display: str, subcategory_display: str) -> str:
    prefix = f"{_clean_seo_title_part(subcategory_display)} {_clean_seo_title_part(category_display)} Menu"
    return _fit_branded_seo_title(prefix)


def _sitemap_lastmod(value, fallback: str) -> str:
    if isinstance(value, str) and value:
        normalized = value.replace("T", " ").split(" ", 1)[0]
        return normalized if re.match(r"^\d{4}-\d{2}-\d{2}$", normalized) else fallback
    return fallback


def _product_lastmod(product: dict, fallback: str) -> str:
    return _sitemap_lastmod(product.get("updated_at") or product.get("created_at"), fallback)


def _newest_lastmod(products: list[dict], fallback: str, *, category: str = "", subcategory: str = "") -> str:
    dates: list[str] = []
    for product in products:
        if category and (product.get("category") or "") != category:
            continue
        if subcategory and (product.get("subcategory") or "") != subcategory:
            continue
        dates.append(_product_lastmod(product, fallback))
    return max(dates) if dates else fallback


def _static_seo_lastmod(fallback: str) -> str:
    candidates = [
        Path(__file__).parent / "configs" / "seo_terms.json",
        Path(__file__).parent / "webapp" / "learn.html",
        Path(__file__).parent / "webapp" / "learn_index.html",
    ]
    mtimes = [path.stat().st_mtime for path in candidates if path.exists()]
    if not mtimes:
        return fallback
    return datetime.fromtimestamp(max(mtimes)).date().isoformat()


def _seo_taxonomy_key(category: str = "", subcategory: str = "") -> tuple[str, str]:
    return (str(category or "").strip().casefold(), str(subcategory or "").strip().casefold())


async def _seo_mapping_freshness(
    fallback: str,
) -> tuple[str, Dict[str, str], Dict[tuple[str, str], str]]:
    """Return global, category, and exact-taxonomy mapping lastmod dates."""
    latest = ""
    by_category: Dict[str, str] = {}
    by_taxonomy: Dict[tuple[str, str], str] = {}
    try:
        rows = await _catalog_db.get_seo_mapping_lastmods()
    except Exception as exc:
        logger.debug("Could not load SEO mapping freshness: %s", exc)
        return latest, by_category, by_taxonomy

    for row in rows:
        category_key, subcategory_key = _seo_taxonomy_key(
            row.get("category") or "",
            row.get("subcategory") or "",
        )
        if not category_key:
            continue
        updated_at = _sitemap_lastmod(row.get("updated_at"), fallback)
        latest = max(latest, updated_at)
        by_category[category_key] = max(by_category.get(category_key, ""), updated_at)
        by_taxonomy[(category_key, subcategory_key)] = max(
            by_taxonomy.get((category_key, subcategory_key), ""),
            updated_at,
        )
    return latest, by_category, by_taxonomy


def _product_public_media_path(product_id: int, media_index: int = 0) -> str:
    return f"/media/products/{product_id}/{media_index}"


def _product_public_media_url(product_id: int, media_index: int = 0) -> str:
    return _absolute_storefront_url(_product_public_media_path(product_id, media_index))


def _product_primary_image_url(product: dict, *, absolute: bool = True) -> str:
    """Return the first photo media URL for SEO/social previews."""
    product_id = product.get("id")
    if product_id is None:
        return ""
    for idx, media in enumerate(_collect_media_info(product)):
        if media.get("type") == "photo":
            return _product_public_media_url(product_id, idx) if absolute else _product_public_media_path(product_id, idx)
    return ""


def _product_card_preview_media(product: dict, *, absolute: bool = False) -> dict[str, str]:
    """Return the best lightweight media URL for product cards.

    Cards should show a real photo when possible. For video-only products, use
    the Mini App media stream directly so the browser can render the media
    instead of caching a crawler-oriented fallback image as the card preview.
    """
    product_id = product.get("id") or product.get("product_id")
    if product_id is None:
        return {"type": "", "url": ""}

    media_info = _collect_media_info(product)
    for idx, media in enumerate(media_info):
        if media.get("type") == "photo":
            path = f"/api/catalog/{product_id}/media/{idx}"
            return {"type": "photo", "url": _absolute_storefront_url(path) if absolute else path}

    for idx, media in enumerate(media_info):
        if media.get("type") == "video":
            path = f"/api/catalog/{product_id}/media/{idx}"
            return {"type": "video", "url": _absolute_storefront_url(path) if absolute else path}

    return {"type": "", "url": ""}


def _product_photo_image_urls(product: dict, *, absolute: bool = True, limit: int = 8) -> list[str]:
    """Return crawlable photo URLs for product image sitemaps and Product schema."""
    product_id = product.get("id")
    if product_id is None:
        return []

    urls: list[str] = []
    for idx, media in enumerate(_collect_media_info(product)):
        if media.get("type") != "photo":
            continue
        urls.append(
            _product_public_media_url(product_id, idx)
            if absolute else _product_public_media_path(product_id, idx)
        )
        if len(urls) >= limit:
            break
    return urls


def _product_video_urls(product: dict, *, absolute: bool = True, limit: int = 8) -> list[tuple[int, str]]:
    """Return stable, public video content URLs for structured data."""
    product_id = product.get("id")
    if product_id is None:
        return []

    urls: list[tuple[int, str]] = []
    for idx, media in enumerate(_collect_media_info(product)):
        if media.get("type") != "video":
            continue
        path = _product_public_media_path(product_id, idx)
        urls.append((idx, _absolute_storefront_url(path) if absolute else path))
        if len(urls) >= limit:
            break
    return urls


def _product_video_thumbnail_url(product: dict) -> str:
    """Return the best crawlable thumbnail URL for a product video."""
    return _product_primary_image_url(product, absolute=True) or _DEFAULT_OG_IMAGE


def _seo_preferred_fallback_media_index(product: dict, media_index: int) -> Optional[int]:
    """Pick an alternate media index for public SEO fallbacks.

    Prefer a photo first, then any other available media on the product.
    """
    media_info = _collect_media_info(product)
    if not media_info:
        return None

    preferred_types = ("photo",)
    for media_type in preferred_types:
        typed_indices = [idx for idx, media in enumerate(media_info) if media.get("type") == media_type and idx != media_index]
        if typed_indices:
            return typed_indices[0]

    for idx, _media in enumerate(media_info):
        if idx != media_index:
            return idx
    return None


def _schema_date(value: Any, fallback: str = "") -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, str) and value:
        normalized = value.replace("T", " ").split(" ", 1)[0]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", normalized):
            return normalized
    return fallback


_VISIBLE_PRICE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![\w.])€\s*([0-9]+(?:[.,][0-9]{1,2})?)"), "EUR"),
    (re.compile(r"(?<![\w.])£\s*([0-9]+(?:[.,][0-9]{1,2})?)"), "GBP"),
    (re.compile(r"(?<![\w.])\$\s*([0-9]+(?:[.,][0-9]{1,2})?)"), "USD"),
    (re.compile(r"(?<![\w.])([0-9]+(?:[.,][0-9]{1,2})?)\s*(?:eur|euro|euros)\b", re.IGNORECASE), "EUR"),
    (re.compile(r"(?<![\w.])([0-9]+(?:[.,][0-9]{1,2})?)\s*(?:gbp|pounds?)\b", re.IGNORECASE), "GBP"),
    (re.compile(r"(?<![\w.])([0-9]+(?:[.,][0-9]{1,2})?)\s*(?:usd|dollars?)\b", re.IGNORECASE), "USD"),
)


def _extract_visible_schema_price(product: dict) -> Optional[tuple[str, str]]:
    """Extract a schema-safe price only when a currency price is visible."""
    for field_name in ("price", "amount"):
        raw_value = product.get(field_name)
        if raw_value in (None, ""):
            continue
        currency = str(product.get("currency") or product.get("price_currency") or "").upper()
        if currency in {"EUR", "GBP", "USD"}:
            try:
                return (f"{float(str(raw_value).replace(',', '.')):.2f}", currency)
            except (TypeError, ValueError):
                pass

    caption = str(product.get("caption") or "")
    for pattern, currency in _VISIBLE_PRICE_PATTERNS:
        match = pattern.search(caption)
        if not match:
            continue
        raw_price = match.group(1).replace(",", ".")
        try:
            price_value = float(raw_price)
        except ValueError:
            continue
        if price_value <= 0:
            continue
        return (f"{price_value:.2f}", currency)
    return None


def _schema_offer_for_product(product: dict, product_url: str) -> Optional[dict]:
    visible_price = _extract_visible_schema_price(product)
    if not visible_price:
        return None
    price, currency = visible_price
    return {
        "@type": "Offer",
        "url": product_url,
        "price": price,
        "priceCurrency": currency,
        "availability": "https://schema.org/InStock",
        "itemCondition": "https://schema.org/NewCondition",
    }


def _indexnow_enabled() -> bool:
    return bool(Config.INDEXNOW_ENABLED and _STOREFRONT_DOMAIN)


def _indexnow_key(*, create: bool = True) -> str:
    """Load or create the persistent IndexNow key used for domain verification."""
    if not _indexnow_enabled():
        return ""

    key_path = Path(Config.INDEXNOW_KEY_FILE or "indexnow.key")
    try:
        if key_path.exists():
            key = key_path.read_text(encoding="utf-8").strip()
            if re.fullmatch(r"[A-Za-z0-9-]{8,128}", key):
                return key
            logger.warning("[indexnow] invalid_key_file path=%s", key_path)
        if not create:
            return ""

        key = secrets.token_hex(16)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(key, encoding="utf-8")
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        logger.info("[indexnow] key_generated path=%s", key_path)
        return key
    except Exception as exc:
        logger.warning("[indexnow] key_unavailable path=%s error=%s", key_path, exc)
        return ""


def _indexnow_key_location(key: str) -> str:
    return _absolute_storefront_url(f"/{key}.txt")


def _indexnow_host() -> str:
    parsed = urllib.parse.urlparse(_STOREFRONT_DOMAIN)
    return parsed.netloc or parsed.path


def _indexnow_public_url(url: str) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    absolute = _absolute_storefront_url(url)
    parsed_domain = urllib.parse.urlparse(_STOREFRONT_DOMAIN)
    parsed_url = urllib.parse.urlparse(absolute)
    if parsed_url.scheme not in {"http", "https"}:
        return ""
    if parsed_domain.netloc and parsed_url.netloc != parsed_domain.netloc:
        return ""
    return absolute


def _indexnow_taxonomy_urls(category: str = "", subcategory: str = "") -> list[str]:
    paths = ["/"]
    category = str(category or "").strip()
    subcategory = str(subcategory or "").strip()
    if category:
        paths.append(_canonical_category_path(category))
        if subcategory:
            paths.append(_canonical_subcategory_path(category, subcategory))
    urls = [
        _indexnow_public_url(_localized_path(path, language))
        for path in paths
        for language in SUPPORTED_LANGUAGES
    ]
    return [url for url in urls if url]


def _indexnow_product_urls(product: dict) -> list[str]:
    product_id = product.get("id") or product.get("product_id")
    caption = product.get("caption") or ""
    urls = []
    if product_id is not None:
        product_path = _canonical_product_path(int(product_id), caption)
        urls.extend(
            _indexnow_public_url(_localized_path(product_path, language))
            for language in SUPPORTED_LANGUAGES
        )
    urls.extend(_indexnow_taxonomy_urls(product.get("category") or "", product.get("subcategory") or ""))
    return [url for url in dict.fromkeys(urls) if url]


async def _indexnow_bootstrap_once() -> int:
    """Queue a modest one-time set of currently important URLs when IndexNow first starts."""
    if not (_indexnow_enabled() and Config.INDEXNOW_BOOTSTRAP_ON_STARTUP):
        return 0
    if await _catalog_db.get_indexnow_submission_count() > 0:
        return 0

    urls = _indexnow_taxonomy_urls()
    for category in get_all_categories():
        urls.append(_indexnow_public_url(_canonical_category_path(category)))
        for subcategory in get_subcategories(category):
            urls.append(_indexnow_public_url(_canonical_subcategory_path(category, subcategory)))
    if Config.INDEXNOW_BOOTSTRAP_LIMIT:
        products = await _catalog_db.get_all_products_excluding_categories(
            EXCLUDED_FROM_ALL_PRODUCTS,
            limit=Config.INDEXNOW_BOOTSTRAP_LIMIT,
        )
        for product in products:
            urls.extend(_indexnow_product_urls(product))

    queued = await _catalog_db.enqueue_indexnow_urls(
        [url for url in dict.fromkeys(urls) if url],
        reason="bootstrap",
    )
    if queued:
        logger.info("[indexnow] bootstrap_queued=%d", queued)
    return queued


async def _indexnow_scan_catalog_changes_once() -> int:
    """Queue recently changed catalog URLs for IndexNow without touching page-view paths."""
    if not _indexnow_enabled():
        return 0

    scan_started = datetime.now()
    raw_since = await _catalog_db.get_bot_setting("indexnow_last_scan_at")
    if raw_since:
        try:
            since = datetime.fromisoformat(raw_since)
        except ValueError:
            since = scan_started - timedelta(hours=Config.INDEXNOW_CHANGE_LOOKBACK_HOURS)
    else:
        since = scan_started - timedelta(hours=Config.INDEXNOW_CHANGE_LOOKBACK_HOURS)

    urls = []
    changed_products = await _catalog_db.get_recent_indexnow_product_changes(
        since,
        limit=Config.INDEXNOW_BATCH_SIZE * 3,
    )
    for product in changed_products:
        urls.extend(_indexnow_product_urls(product))

    deleted_products = await _catalog_db.get_recent_indexnow_deleted_products(
        since,
        limit=Config.INDEXNOW_BATCH_SIZE * 3,
    )
    for item in deleted_products:
        product_id = item.get("product_id")
        if product_id is not None:
            deleted_path = _canonical_product_path(int(product_id), item.get("caption") or "")
            urls.extend(
                _indexnow_public_url(_localized_path(deleted_path, language))
                for language in SUPPORTED_LANGUAGES
            )
        urls.extend(_indexnow_taxonomy_urls(item.get("category") or "", item.get("subcategory") or ""))

    queued = 0
    if urls:
        queued = await _catalog_db.enqueue_indexnow_urls(
            [url for url in dict.fromkeys(urls) if url],
            reason="catalog_change",
        )
        logger.info(
            "[indexnow] changes_queued=%d products=%d deleted=%d since=%s",
            queued,
            len(changed_products),
            len(deleted_products),
            since.isoformat(sep=" ", timespec="seconds"),
        )

    await _catalog_db.set_bot_setting(
        "indexnow_last_scan_at",
        scan_started.isoformat(sep=" ", timespec="seconds"),
    )
    return queued


async def _indexnow_submit_pending_once() -> int:
    if not _indexnow_enabled():
        return 0
    key = _indexnow_key(create=True)
    host = _indexnow_host()
    if not key or not host:
        return 0

    pending = await _catalog_db.get_pending_indexnow_submissions(
        limit=Config.INDEXNOW_BATCH_SIZE,
    )
    if not pending:
        return 0

    urls = [item["url"] for item in pending]
    payload = {
        "host": host,
        "key": key,
        "keyLocation": _indexnow_key_location(key),
        "urlList": urls,
    }

    def _post_indexnow():
        import requests
        return requests.post(
            Config.INDEXNOW_ENDPOINT,
            json=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=12,
        )

    try:
        response = await asyncio.to_thread(_post_indexnow)
    except Exception as exc:
        await _catalog_db.mark_indexnow_submissions_failed(
            urls,
            error=str(exc),
            retry=True,
        )
        logger.warning("[indexnow] submit_error urls=%d error=%s", len(urls), exc)
        return 0

    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code in {200, 202}:
        await _catalog_db.mark_indexnow_submissions_submitted(urls, http_status=status_code)
        logger.info("[indexnow] submitted urls=%d status=%d", len(urls), status_code)
        return len(urls)

    error_text = (getattr(response, "text", "") or "")[:300]
    retry = status_code in {0, 408, 425, 429, 500, 502, 503, 504}
    await _catalog_db.mark_indexnow_submissions_failed(
        urls,
        http_status=status_code,
        error=error_text or f"HTTP {status_code}",
        retry=retry,
    )
    logger.warning(
        "[indexnow] submit_failed urls=%d status=%d retry=%s error=%s",
        len(urls),
        status_code,
        retry,
        error_text,
    )
    return 0


async def _indexnow_loop():
    """Background task: notify participating search engines about real URL changes."""
    if not _indexnow_enabled():
        logger.info("[indexnow] disabled")
        return

    await asyncio.sleep(90)
    while True:
        try:
            await _indexnow_bootstrap_once()
            await _indexnow_scan_catalog_changes_once()
            await _indexnow_submit_pending_once()
        except Exception as exc:
            logger.warning("[indexnow] loop_error error=%s", exc)
        await asyncio.sleep(Config.INDEXNOW_INTERVAL_SECONDS)


def _search_console_sitemap_enabled() -> bool:
    return bool(
        Config.SEARCH_CONSOLE_ENABLED
        and Config.SEARCH_CONSOLE_SITEMAP_SUBMIT_ENABLED
        and Config.SEARCH_CONSOLE_SITE_URL
        and Config.SEARCH_CONSOLE_SITEMAP_URL
    )


async def _record_search_console_sitemap_result(result: dict[str, Any], *, attempted_at: datetime) -> None:
    status_value = "submitted" if result.get("ok") else ("skipped" if result.get("skipped") else "failed")
    await _catalog_db.set_bot_setting(
        "search_console_sitemap_last_attempt_at",
        attempted_at.isoformat(sep=" ", timespec="seconds"),
    )
    await _catalog_db.set_bot_setting("search_console_sitemap_last_status", status_value)
    await _catalog_db.set_bot_setting("search_console_sitemap_last_url", str(result.get("sitemap_url") or Config.SEARCH_CONSOLE_SITEMAP_URL or ""))
    await _catalog_db.set_bot_setting("search_console_sitemap_last_site_url", str(result.get("site_url") or Config.SEARCH_CONSOLE_SITE_URL or ""))
    await _catalog_db.set_bot_setting("search_console_sitemap_last_auth_source", str(result.get("auth_source") or ""))
    await _catalog_db.set_bot_setting("search_console_sitemap_last_http_status", str(result.get("status") or ""))
    await _catalog_db.set_bot_setting("search_console_sitemap_last_error", str(result.get("error") or "")[:500])
    if result.get("ok"):
        await _catalog_db.set_bot_setting(
            "search_console_sitemap_last_submitted_at",
            attempted_at.isoformat(sep=" ", timespec="seconds"),
        )
    _invalidate_admin_status_cache()


async def _search_console_submit_sitemap_once(*, reason: str = "scheduled", force: bool = False) -> bool:
    if not _search_console_sitemap_enabled():
        return False

    attempted_at = datetime.now()
    if not force:
        raw_last_attempt = await _catalog_db.get_bot_setting("search_console_sitemap_last_attempt_at")
        if raw_last_attempt:
            try:
                last_attempt = datetime.fromisoformat(str(raw_last_attempt))
                elapsed = (attempted_at - last_attempt).total_seconds()
                if elapsed < Config.SEARCH_CONSOLE_SITEMAP_INTERVAL_SECONDS:
                    logger.debug(
                        "[search_console] sitemap_submit_skipped reason=interval elapsed=%.0f interval=%d",
                        elapsed,
                        Config.SEARCH_CONSOLE_SITEMAP_INTERVAL_SECONDS,
                    )
                    return False
            except ValueError:
                pass

    result = await submit_sitemap(
        enabled=Config.SEARCH_CONSOLE_ENABLED,
        site_url=Config.SEARCH_CONSOLE_SITE_URL,
        sitemap_url=Config.SEARCH_CONSOLE_SITEMAP_URL,
        service_account_json=Config.SEARCH_CONSOLE_SERVICE_ACCOUNT_JSON,
        service_account_file=Config.SEARCH_CONSOLE_SERVICE_ACCOUNT_FILE,
        oauth_client_json=Config.SEARCH_CONSOLE_OAUTH_CLIENT_JSON,
        oauth_client_file=Config.SEARCH_CONSOLE_OAUTH_CLIENT_FILE,
        oauth_token_json=Config.SEARCH_CONSOLE_OAUTH_TOKEN_JSON,
        oauth_token_file=Config.SEARCH_CONSOLE_OAUTH_TOKEN_FILE,
    )
    await _record_search_console_sitemap_result(result, attempted_at=attempted_at)

    if result.get("ok"):
        logger.info(
            "[search_console] sitemap_submitted reason=%s site_url=%s sitemap=%s status=%s auth=%s",
            reason,
            result.get("site_url"),
            result.get("sitemap_url"),
            result.get("status"),
            result.get("auth_source"),
        )
        return True

    logger.warning(
        "[search_console] sitemap_submit_not_completed reason=%s skipped=%s error=%s",
        reason,
        result.get("skipped", False),
        result.get("error"),
    )
    return False


async def _search_console_sitemap_loop():
    """Background task: safely tell Google where the current sitemap is."""
    if not _search_console_sitemap_enabled():
        logger.info("[search_console] sitemap_submit disabled")
        return

    await asyncio.sleep(120)
    if Config.SEARCH_CONSOLE_SITEMAP_SUBMIT_ON_STARTUP:
        try:
            await _search_console_submit_sitemap_once(reason="startup", force=True)
        except Exception as exc:
            logger.warning("[search_console] sitemap_startup_error error=%s", exc)

    while True:
        try:
            await _search_console_submit_sitemap_once(reason="scheduled", force=False)
        except Exception as exc:
            logger.warning("[search_console] sitemap_loop_error error=%s", exc)
        await asyncio.sleep(Config.SEARCH_CONSOLE_SITEMAP_INTERVAL_SECONDS)


async def _seo_side_note_terms(category: str, subcategory: str = "", language: str = "en") -> list[str]:
    """Return visible related terms from file + learned SEO mappings."""
    category = str(category or "").strip()
    language = _normalize_seo_language(language)
    if not category:
        return []

    limit = max(1, Config.SEO_SIDE_NOTE_LIMIT)
    file_terms = list(get_terms_for_taxonomy(category, subcategory, limit=limit))
    static_terms = file_terms if language == "en" else await _translate_seo_lines(file_terms, language)
    notes: list[dict[str, Any]] = []
    try:
        notes = await _catalog_db.get_seo_side_notes(
            category,
            subcategory,
            language=language,
            limit=min(25, limit * 2),
        )
    except Exception as exc:
        logger.debug("Failed to load SEO side notes for %s/%s: %s", category, subcategory, exc)

    # Static vocabulary provides a stable topical baseline. Reserve roughly
    # half of the visible slots for concise, evidence-backed learned terms so
    # Search Console and internal-search learning can actually change HTML.
    dynamic_limit = min(5, max(2, limit // 2)) if limit > 2 else 1
    primary_static_count = min(3, max(1, limit - dynamic_limit))
    static_normalized = {normalize_term(term) for term in static_terms}
    dynamic_rows = []
    for item in notes:
        term = str(item.get("term") or "").strip()
        normalized = normalize_term(term)
        if (
            not normalized
            or normalized in static_normalized
            or len(normalized.split()) > 5
            or not _is_useful_auto_seo_term(term)
        ):
            continue
        dynamic_rows.append(item)
    dynamic_rows.sort(
        key=lambda item: (
            0 if len(normalize_term(item.get("term") or "").split()) <= 3 else 1,
            -max(0, int(item.get("evidence_count") or 0)),
            len(normalize_term(item.get("term") or "").split()),
            len(normalize_term(item.get("term") or "")),
        )
    )
    dynamic_terms = [str(item.get("term") or "").strip() for item in dynamic_rows[:dynamic_limit]]
    terms = [
        *static_terms[:primary_static_count],
        *dynamic_terms,
        *static_terms[primary_static_count:],
    ]

    seen = set()
    cleaned: list[str] = []
    for term in terms:
        normalized = normalize_term(term)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(term)
        if len(cleaned) >= limit:
            break
    return cleaned


def _build_visible_taxonomy_intro(
    category: str,
    subcategory: str,
    *,
    total: int,
    terms: Iterable[str],
    language: str,
) -> str:
    """Build a short, localized intro from the terms visible on this page."""
    language = _normalize_seo_language(language)
    category_display = get_category_display_name(category, language) if category else "products"
    subcategory_display = get_subcategory_display_name(subcategory, language) if subcategory else ""
    label = " ".join(part for part in (subcategory_display, category_display) if part).strip()
    concise_terms = _dedupe_seo_phrases(
        [term for term in terms if len(normalize_term(term).split()) <= 4],
        limit=4,
    )
    joined = ", ".join(concise_terms)
    templates = {
        "en": f"Browse {total} {label} listing{'s' if total != 1 else ''}" + (f", including {joined}." if joined else "."),
        "de": f"Entdecke {total} Angebote für {label}" + (f", darunter {joined}." if joined else "."),
        "nl": f"Bekijk {total} aanbiedingen voor {label}" + (f", waaronder {joined}." if joined else "."),
        "it": f"Scopri {total} prodotti {label}" + (f", tra cui {joined}." if joined else "."),
        "es": f"Explora {total} productos de {label}" + (f", incluidos {joined}." if joined else "."),
        "fr": f"Découvrez {total} produits {label}" + (f", notamment {joined}." if joined else "."),
    }
    return templates[language]


def _resolve_category_name(category_slug: str) -> Optional[str]:
    category_slug = _taxonomy_slug(category_slug)
    return next((category for category in get_all_categories() if _taxonomy_slug(category) == category_slug), None)


def _resolve_subcategory_name(category: str, subcategory_slug: str) -> Optional[str]:
    subcategory_slug = _taxonomy_slug(subcategory_slug)
    return next((subcategory for subcategory in get_subcategories(category) if _taxonomy_slug(subcategory) == subcategory_slug), None)


async def _missing_product_redirect(
    product_id: int,
    *,
    slug: str = "",
    permanent_when_known: bool = True,
    language: str = "en",
) -> RedirectResponse:
    """Redirect unavailable public product links to the closest useful storefront page."""
    try:
        if hasattr(_catalog_db, "get_deleted_product_redirect"):
            redirect_meta = await _catalog_db.get_deleted_product_redirect(product_id)
        else:
            redirect_meta = None
    except Exception as exc:
        logger.debug("Failed to load deleted product redirect for product_id=%s: %s", product_id, exc)
        redirect_meta = None

    if redirect_meta:
        category = redirect_meta.get("category") or ""
        subcategory = redirect_meta.get("subcategory") or ""
        matched_category = _resolve_category_name(category) if category else None
        if matched_category and subcategory:
            matched_subcategory = _resolve_subcategory_name(matched_category, subcategory)
            if matched_subcategory:
                return RedirectResponse(
                    url=_localized_path(_canonical_subcategory_path(matched_category, matched_subcategory), language),
                    status_code=status.HTTP_301_MOVED_PERMANENTLY if permanent_when_known else status.HTTP_302_FOUND,
                )
        if matched_category:
            return RedirectResponse(
                url=_localized_path(_canonical_category_path(matched_category), language),
                status_code=status.HTTP_301_MOVED_PERMANENTLY if permanent_when_known else status.HTTP_302_FOUND,
            )

    inferred_path = await _infer_missing_product_redirect_path(slug)
    if inferred_path:
        return RedirectResponse(url=_localized_path(inferred_path, language), status_code=status.HTTP_302_FOUND)

    return RedirectResponse(url=_localized_path("/", language), status_code=status.HTTP_302_FOUND)


async def _infer_missing_product_redirect_path(slug: str) -> str:
    """Infer a useful category fallback for old product URLs missing redirect metadata."""
    query = normalize_term((slug or "").replace("-", " "))
    if len(query) < 4:
        return ""
    try:
        products = await _catalog_db.get_all_products_for_search()
        inferred = infer_seo_query_mapping(query, products, min_score=88)
    except Exception as exc:
        logger.debug("Failed to infer deleted product redirect for slug=%r: %s", slug, exc)
        return ""
    if not inferred:
        return ""
    category = _resolve_category_name(inferred.get("category", ""))
    subcategory = inferred.get("subcategory") or ""
    if not category:
        return ""
    if subcategory:
        matched_subcategory = _resolve_subcategory_name(category, subcategory)
        if matched_subcategory:
            return _canonical_subcategory_path(category, matched_subcategory)
    return _canonical_category_path(category)


def _build_seo_context(*, title: str, desc: str, image: str = "", url: str = "",
                        product: Optional[dict] = None, deep_link: str = "",
                        keywords: str = "", category: str = "", og_type: str = "website",
                        schema_title: str = "",
                        language: str = "en", alternate_path: str = "") -> dict:
    """Build template context dict for SSR SEO pages."""
    language = _normalize_seo_language(language)
    ui = _seo_ui(language)
    if not alternate_path:
        parsed_url = urllib.parse.urlsplit(url or "/")
        alternate_path = urllib.parse.urlunsplit(("", "", parsed_url.path or "/", parsed_url.query, ""))
    if product:
        product_url = url or _STOREFRONT_DOMAIN
        schema_name = schema_title or title
        product_schema_id = f"{product_url}#product"
        product_category = category or product.get("category", "")
        product_subcategory = product.get("subcategory") or ""
        product_images = _product_photo_image_urls(product, absolute=True)
        if image and image not in product_images:
            product_images.insert(0, image)
        primary_image = product_images[0] if product_images else image or _DEFAULT_OG_IMAGE
        product_videos = _product_video_urls(product, absolute=True, limit=6)
        date_published = _schema_date(product.get("created_at"))
        date_modified = _schema_date(product.get("updated_at") or product.get("created_at"), date_published)
        product_schema: dict = {
            "@context": "https://schema.org/",
            "@type": "Product",
            "@id": product_schema_id,
            "name": schema_name,
            "description": desc,
            "url": product_url,
            "mainEntityOfPage": {"@id": product_url},
            "sku": str(product.get("id") or product.get("product_id") or ""),
            "category": product_category,
            "inLanguage": language,
            "brand": {
                "@type": "Brand",
                "name": "Top Shelf Cali Plug",
            },
        }
        if date_published:
            product_schema["datePublished"] = date_published
        if date_modified:
            product_schema["dateModified"] = date_modified
        additional_properties = []
        if product_subcategory:
            additional_properties.append({
                "@type": "PropertyValue",
                "name": "Subcategory",
                "value": product_subcategory,
            })
        media_count = len(_collect_media_info(product))
        if media_count:
            additional_properties.append({
                "@type": "PropertyValue",
                "name": "Media count",
                "value": str(media_count),
            })
        if additional_properties:
            product_schema["additionalProperty"] = additional_properties
        if product_images:
            product_schema["image"] = product_images
        product_schema["primaryImageOfPage"] = {
            "@type": "ImageObject",
            "@id": f"{product_url}#primaryimage",
            "url": primary_image,
            "contentUrl": primary_image,
            "caption": schema_name,
            "representativeOfPage": True,
        }
        offer = _schema_offer_for_product(product, product_url)
        if offer:
            product_schema["offers"] = offer
        video_nodes = []
        if product_videos:
            thumbnail_url = _product_video_thumbnail_url(product)
            for idx, video_url in product_videos:
                video_node: dict = {
                    "@type": "VideoObject",
                    "@id": f"{product_url}#video-{idx + 1}",
                    "name": f"{schema_name} video {idx + 1}",
                    "description": desc,
                    "thumbnailUrl": [thumbnail_url],
                    "contentUrl": video_url,
                    "url": product_url,
                    "isPartOf": {"@id": product_url},
                    "about": {"@id": product_schema_id},
                }
                if date_published:
                    video_node["uploadDate"] = date_published
                video_nodes.append(video_node)
            product_schema["subjectOf"] = video_nodes
        breadcrumb_items = [
            {
                "@type": "ListItem",
                "position": 1,
                "name": ui["home"],
                "item": _localized_url("/", language),
            }
        ]
        if product_category:
            breadcrumb_items.append({
                "@type": "ListItem",
                "position": len(breadcrumb_items) + 1,
                "name": get_category_display_name(product_category, language),
                "item": _localized_url(_canonical_category_path(product_category), language),
            })
        if product_category and product_subcategory:
            breadcrumb_items.append({
                "@type": "ListItem",
                "position": len(breadcrumb_items) + 1,
                "name": get_subcategory_display_name(product_subcategory, language),
                "item": _localized_url(_canonical_subcategory_path(product_category, product_subcategory), language),
            })
        breadcrumb_items.append({
            "@type": "ListItem",
            "position": len(breadcrumb_items) + 1,
            "name": schema_name,
            "item": product_url,
        })
        breadcrumb: dict = {
            "@type": "BreadcrumbList",
            "itemListElement": breadcrumb_items,
        }
        webpage_schema: dict = {
            "@type": "WebPage",
            "@id": product_url,
            "url": product_url,
            "name": schema_name,
            "description": desc,
            "inLanguage": language,
            "primaryImageOfPage": {
                "@type": "ImageObject",
                "@id": f"{product_url}#primaryimage",
                "url": primary_image,
                "contentUrl": primary_image,
                "caption": schema_name,
            },
            "mainEntity": {"@id": product_schema_id},
        }
        schema = json.dumps({
            "@context": "https://schema.org/",
            "@graph": [webpage_schema, product_schema, *video_nodes, breadcrumb],
        }, ensure_ascii=False)
    else:
        schema = json.dumps({
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "WebSite",
                    "name": "Top Shelf Cali Plug",
                    "url": _STOREFRONT_DOMAIN,
                    "description": desc,
                    "inLanguage": language,
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
                    "inLanguage": language,
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
        "seo_image_width": _OG_IMAGE_WIDTH if is_default_image else 1200,
        "seo_image_height": _OG_IMAGE_HEIGHT if is_default_image else 630,
        "seo_url": url,
        "seo_schema": schema,
        "seo_keywords": keywords or default_keywords,
        "og_type": og_type,
        "telegram_deep_link": deep_link or f"https://t.me/{_STOREFRONT_BOT_USERNAME}",
        "seo_lang": language,
        "seo_locale": _SEO_OG_LOCALES.get(language, "en_US"),
        "seo_alternates": _seo_alternate_links(alternate_path),
        "language_switch": _seo_alternate_links(alternate_path)[:-1],
        "ui": ui,
    }


def _seo_etag(content: bytes) -> str:
    return f'"{hashlib.sha256(content).hexdigest()[:24]}"'


def _seo_etag_from_key(key: str) -> str:
    return f'"{hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]}"'


def _request_matches_etag(request: Request, etag: str) -> bool:
    raw = request.headers.get("if-none-match") or ""
    if not raw:
        return False
    candidates = {part.strip() for part in raw.split(",") if part.strip()}
    return "*" in candidates or etag in candidates or f"W/{etag}" in candidates


def _seo_headers(etag: str, max_age: int, *, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {
        "Cache-Control": f"public, max-age={max_age}",
        "ETag": etag,
    }
    if extra:
        headers.update(extra)
    return headers


def _seo_content_response(
    request: Request,
    content: bytes | str,
    *,
    media_type: str,
    max_age: int,
    etag: str = "",
    extra_headers: Optional[Dict[str, str]] = None,
) -> Response:
    body = content.encode("utf-8") if isinstance(content, str) else content
    resolved_etag = etag or _seo_etag(body)
    headers = _seo_headers(resolved_etag, max_age, extra=extra_headers)
    if _request_matches_etag(request, resolved_etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=body, media_type=media_type, headers=headers)


def _seo_cached_response(request: Request, cache_key: str) -> Optional[Response]:
    entry = _seo_response_cache.get(cache_key)
    if not entry:
        return None
    expires_at, content, media_type, etag, max_age = entry
    if expires_at <= _time.time():
        _seo_response_cache.pop(cache_key, None)
        return None
    _seo_response_cache.move_to_end(cache_key)
    return _seo_content_response(
        request,
        content,
        media_type=media_type,
        max_age=max_age,
        etag=etag,
    )


def _seo_store_and_respond(
    request: Request,
    cache_key: str,
    content: bytes | str,
    *,
    media_type: str,
    max_age: int,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Response:
    body = content.encode("utf-8") if isinstance(content, str) else content
    etag = _seo_etag(body)
    _seo_response_cache[cache_key] = (_time.time() + max_age, body, media_type, etag, max_age)
    _seo_response_cache.move_to_end(cache_key)
    while len(_seo_response_cache) > _SEO_RESPONSE_CACHE_MAX:
        _seo_response_cache.popitem(last=False)
    return _seo_content_response(
        request,
        body,
        media_type=media_type,
        max_age=max_age,
        etag=etag,
        extra_headers=extra_headers,
    )


def _render_seo_template(
    request: Request,
    template_name: str,
    context: dict,
    *,
    cache_key: str,
    max_age: int = _SEO_PAGE_CACHE_SECONDS,
) -> Response:
    cached = _seo_cached_response(request, cache_key)
    if cached:
        return cached
    ctx = dict(context)
    ctx["request"] = request
    content = templates.env.get_template(template_name).render(ctx)
    return _seo_store_and_respond(
        request,
        cache_key,
        content,
        media_type="text/html; charset=utf-8",
        max_age=max_age,
    )


def _is_crawler_user_agent(user_agent: str) -> bool:
    return is_crawler_user_agent(user_agent)


def _log_seo_public_request(request: Request, route: str, **fields: Any) -> None:
    user_agent = request.headers.get("user-agent", "-")
    if not _is_crawler_user_agent(user_agent) and route not in {"robots", "sitemap"}:
        return
    details = " ".join(f"{key}={value}" for key, value in fields.items() if value not in (None, ""))
    logger.info(
        "[seo_public] route=%s path=%s ip=%s crawler=%s ua=%r%s%s",
        route,
        request.url.path,
        get_client_ip(request),
        _is_crawler_user_agent(user_agent),
        user_agent[:180],
        " " if details else "",
        details,
    )


def _read_brand_icon_png() -> bytes:
    # Raster counterpart of site-icon.svg for consumers that reject SVG images.
    image_path = Path(__file__).parent / "webapp" / "assets" / "site-icon-emoji.png"
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Brand image not found")
    return image_path.read_bytes()


def _read_site_icon_svg() -> bytes:
    image_path = Path(__file__).parent / "webapp" / "site-icon.svg"
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Site icon not found")
    return image_path.read_bytes()


def _svg_response(svg_bytes: bytes, *, max_age: int = 604800, request: Optional[Request] = None) -> Response:
    etag = _seo_etag(svg_bytes)
    headers = {
        "Cache-Control": f"public, max-age={max_age}",
        "Content-Length": str(len(svg_bytes)),
        "ETag": etag,
    }
    if request is not None and _request_matches_etag(request, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(
        content=svg_bytes,
        media_type="image/svg+xml",
        headers=headers,
    )


def _png_response(image_bytes: bytes, *, max_age: int = 86400, request: Optional[Request] = None) -> Response:
    etag = _seo_etag(image_bytes)
    headers = {
        "Cache-Control": f"public, max-age={max_age}",
        "Content-Length": str(len(image_bytes)),
        "ETag": etag,
    }
    if request is not None and _request_matches_etag(request, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers=headers,
    )


@app.get("/og-image.png", response_class=Response)
@app.head("/og-image.png", response_class=Response)
async def serve_og_image(request: Request):
    """Serve the storefront brand image for social media and crawler previews.

    Returns a static PNG file. Social media crawlers
    (WhatsApp, iMessage, Twitter/X, Facebook, Discord, Slack, Telegram) require
    a real HTTP/HTTPS URL — data: URIs are never fetched by these crawlers.
    """
    _log_seo_public_request(request, "og_image")
    cache_key = f"og-image:{_SITE_ICON_VERSION}"
    cached = _seo_cached_response(request, cache_key)
    if cached:
        return cached
    return _seo_store_and_respond(
        request,
        cache_key,
        _read_brand_icon_png(),
        media_type="image/png",
        max_age=86400,
        extra_headers={"Content-Type": "image/png"},
    )


@app.get("/favicon.ico", response_class=Response)
@app.head("/favicon.ico", response_class=Response)
async def serve_favicon(request: Request):
    """Serve the canonical SVG football for clients that probe /favicon.ico."""
    return _svg_response(_read_site_icon_svg(), max_age=604800, request=request)


@app.get("/apple-touch-icon.png", response_class=Response)
@app.get("/apple-touch-icon-precomposed.png", response_class=Response)
@app.head("/apple-touch-icon.png", response_class=Response)
@app.head("/apple-touch-icon-precomposed.png", response_class=Response)
async def serve_apple_touch_icon(request: Request):
    """Serve an Apple touch icon fallback to avoid crawler/mobile browser 404 noise."""
    return _png_response(_read_brand_icon_png(), max_age=604800, request=request)


@app.get("/robots.txt", response_class=Response)
@app.head("/robots.txt", response_class=Response)
async def serve_robots_txt(request: Request):
    """Serve robots.txt to guide crawlers."""
    _log_seo_public_request(request, "robots")
    cached = _seo_cached_response(request, "robots")
    if cached:
        return cached
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /media/products/\n"
        "Disallow: /api/\n"
        "Disallow: /admin\n"
        "Disallow: /admin/\n"
        "Disallow: /webhook\n"
        "Disallow: /webhook/\n"
        "Disallow: /webapp\n"
        "Disallow: /webapp/\n"
        "\n"
        "User-agent: AhrefsBot\n"
        "Allow: /\n"
        "Allow: /media/products/\n"
        "Disallow: /api/\n"
        "Crawl-delay: 10\n"
        "\n"
        "User-agent: DotBot\n"
        "Allow: /\n"
        "Allow: /media/products/\n"
        "Disallow: /api/\n"
        "Crawl-delay: 10\n"
        "\n"
        "User-agent: Amazonbot\n"
        "Allow: /\n"
        "Allow: /media/products/\n"
        "Disallow: /api/\n"
        "Crawl-delay: 10\n"
        "\n"
        f"Sitemap: {_STOREFRONT_DOMAIN}/sitemap.xml\n"
    )
    return _seo_store_and_respond(
        request,
        "robots",
        content,
        media_type="text/plain; charset=utf-8",
        max_age=_SEO_ROBOTS_CACHE_SECONDS,
    )


@app.get("/{indexnow_key}.txt", response_class=Response)
async def serve_indexnow_key(indexnow_key: str):
    """Serve the root IndexNow key file used by participating search engines."""
    if not re.fullmatch(r"[A-Za-z0-9-]{8,128}", indexnow_key or ""):
        raise HTTPException(status_code=404, detail="Not found")
    key = _indexnow_key(create=True)
    if not key or indexnow_key != key:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(
        content=key,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/sitemap.xml", response_class=Response)
@app.head("/sitemap.xml", response_class=Response)
async def serve_sitemap_xml(request: Request):
    """Dynamically generate a sitemap.xml including homepage, category, and all product pages."""
    _log_seo_public_request(request, "sitemap")
    cached = _seo_cached_response(request, "sitemap")
    if cached:
        return cached
    from datetime import date
    today = date.today().isoformat()
    products = await _catalog_db.get_all_products_excluding_categories(EXCLUDED_FROM_ALL_PRODUCTS)
    newest_product_lastmod = _newest_lastmod(products, today)
    static_seo_lastmod = _static_seo_lastmod(today)
    mapping_lastmod, mapping_by_category, mapping_by_taxonomy = await _seo_mapping_freshness(today)
    home_lastmod = max(newest_product_lastmod, static_seo_lastmod, mapping_lastmod)
    active_categories = {
        str(product.get("category") or "").strip()
        for product in products
        if str(product.get("category") or "").strip()
    }
    active_subcategories = {
        (
            str(product.get("category") or "").strip(),
            str(product.get("subcategory") or "").strip(),
        )
        for product in products
        if str(product.get("category") or "").strip()
        and str(product.get("subcategory") or "").strip()
    }

    urls = []
    for language in SUPPORTED_LANGUAGES:
        urls.append(
            f"  <url>\n"
            f"    <loc>{html.escape(_localized_url('/', language))}</loc>\n"
            f"{_sitemap_alternate_xml('/')}"
            f"    <lastmod>{home_lastmod}</lastmod>\n"
            f"    <changefreq>weekly</changefreq>\n"
            f"    <priority>1.0</priority>\n"
            f"  </url>"
        )
    for category in get_all_categories():
        if category not in active_categories:
            continue
        category_key, _ = _seo_taxonomy_key(category)
        category_lastmod = max(
            _newest_lastmod(products, static_seo_lastmod, category=category),
            mapping_by_category.get(category_key, static_seo_lastmod),
        )
        category_path = _canonical_category_path(category)
        for language in SUPPORTED_LANGUAGES:
            urls.append(
                f"  <url>\n"
                f"    <loc>{html.escape(_localized_url(category_path, language))}</loc>\n"
                f"{_sitemap_alternate_xml(category_path)}"
                f"    <lastmod>{category_lastmod}</lastmod>\n"
                f"    <changefreq>weekly</changefreq>\n"
                f"    <priority>0.9</priority>\n"
                f"  </url>"
            )
        for subcategory in get_subcategories(category):
            if (category, subcategory) not in active_subcategories:
                continue
            taxonomy_key = _seo_taxonomy_key(category, subcategory)
            subcategory_lastmod = max(
                _newest_lastmod(
                    products,
                    static_seo_lastmod,
                    category=category,
                    subcategory=subcategory,
                ),
                mapping_by_taxonomy.get(taxonomy_key, static_seo_lastmod),
            )
            subcategory_path = _canonical_subcategory_path(category, subcategory)
            for language in SUPPORTED_LANGUAGES:
                urls.append(
                    f"  <url>\n"
                    f"    <loc>{html.escape(_localized_url(subcategory_path, language))}</loc>\n"
                    f"{_sitemap_alternate_xml(subcategory_path)}"
                    f"    <lastmod>{subcategory_lastmod}</lastmod>\n"
                    f"    <changefreq>weekly</changefreq>\n"
                    f"    <priority>0.85</priority>\n"
                    f"  </url>"
                )
    for p in products:
        pid = p.get("id") or p.get("product_id")
        if pid is None:
            continue
        product_lastmod = _product_lastmod(p, today)
        product_mapping_lastmod = mapping_by_taxonomy.get(
            _seo_taxonomy_key(p.get("category") or "", p.get("subcategory") or ""),
            product_lastmod,
        )
        lastmod = max(product_lastmod, product_mapping_lastmod)
        product_path = _canonical_product_path(pid, p.get("caption") or "")
        title = _caption_parts(p.get("caption") or "")[0]
        image_xml = "".join(
            f"    <image:image>\n"
            f"      <image:loc>{html.escape(image_url)}</image:loc>\n"
            f"      <image:title>{html.escape(title)}</image:title>\n"
            f"    </image:image>\n"
            for image_url in _product_photo_image_urls(p, absolute=True, limit=4)
        )
        thumbnail_url = _product_video_thumbnail_url(p)
        for language in SUPPORTED_LANGUAGES:
            loc = _localized_url(product_path, language)
            video_xml = "".join(
                f"    <video:video>\n"
                f"      <video:thumbnail_loc>{html.escape(thumbnail_url)}</video:thumbnail_loc>\n"
                f"      <video:title>{html.escape(title[:100])}</video:title>\n"
                f"      <video:description>{html.escape((_caption_parts(p.get('caption') or '')[1] or title)[:2048])}</video:description>\n"
                f"      <video:content_loc>{html.escape(video_url)}</video:content_loc>\n"
                f"      <video:publication_date>{html.escape(_product_lastmod(p, today))}</video:publication_date>\n"
                f"    </video:video>\n"
                for _idx, video_url in _product_video_urls(p, absolute=True, limit=4)
            )
            urls.append(
                f"  <url>\n"
                f"    <loc>{html.escape(loc)}</loc>\n"
                f"{_sitemap_alternate_xml(product_path)}"
                f"    <lastmod>{lastmod}</lastmod>\n"
                f"{image_xml}"
                f"{video_xml}"
                f"    <changefreq>monthly</changefreq>\n"
                f"    <priority>0.8</priority>\n"
                f"  </url>"
            )

    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml" '
        'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1" '
        'xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">\n'
        + "\n".join(urls)
        + "\n</urlset>"
    )
    return _seo_store_and_respond(
        request,
        "sitemap",
        sitemap,
        media_type="application/xml",
        max_age=_SEO_SITEMAP_CACHE_SECONDS,
    )


async def _crawlable_page_snapshot(limit: int = 5000) -> Dict[str, Any]:
    """Return the current sitemap-like URL set for the admin WebApp summary."""
    from datetime import date

    limit = max(1, min(int(limit or 5000), 10000))
    today = date.today().isoformat()
    products = await _catalog_db.get_all_products_excluding_categories(EXCLUDED_FROM_ALL_PRODUCTS)
    static_seo_lastmod = _static_seo_lastmod(today)
    mapping_lastmod, mapping_by_category, mapping_by_taxonomy = await _seo_mapping_freshness(today)
    newest_product_lastmod = _newest_lastmod(products, today)
    home_lastmod = max(newest_product_lastmod, static_seo_lastmod, mapping_lastmod)
    pages: List[Dict[str, Any]] = []

    def append_localized(path: str, page_type: str, title: str, lastmod: str) -> None:
        for language in SUPPORTED_LANGUAGES:
            localized_path = _localized_path(path, language)
            pages.append({
                "url": _absolute_storefront_url(localized_path),
                "path": localized_path,
                "type": page_type,
                "language": language,
                "title": title,
                "lastmod": lastmod,
            })

    append_localized("/", "home", "Homepage", home_lastmod)

    active_categories = {
        str(product.get("category") or "").strip()
        for product in products
        if str(product.get("category") or "").strip()
    }
    active_subcategories = {
        (
            str(product.get("category") or "").strip(),
            str(product.get("subcategory") or "").strip(),
        )
        for product in products
        if str(product.get("category") or "").strip()
        and str(product.get("subcategory") or "").strip()
    }

    for category in get_all_categories():
        if category not in active_categories:
            continue
        category_key, _ = _seo_taxonomy_key(category)
        category_lastmod = max(
            _newest_lastmod(products, static_seo_lastmod, category=category),
            mapping_by_category.get(category_key, static_seo_lastmod),
        )
        append_localized(
            _canonical_category_path(category),
            "category",
            get_category_display_name(category),
            category_lastmod,
        )
        for subcategory in get_subcategories(category):
            if (category, subcategory) not in active_subcategories:
                continue
            taxonomy_key = _seo_taxonomy_key(category, subcategory)
            subcategory_lastmod = max(
                _newest_lastmod(
                    products,
                    static_seo_lastmod,
                    category=category,
                    subcategory=subcategory,
                ),
                mapping_by_taxonomy.get(taxonomy_key, static_seo_lastmod),
            )
            append_localized(
                _canonical_subcategory_path(category, subcategory),
                "subcategory",
                f"{get_category_display_name(category)} / {get_subcategory_display_name(subcategory)}",
                subcategory_lastmod,
            )

    for product in products:
        pid = product.get("id") or product.get("product_id")
        if pid is None:
            continue
        caption = product.get("caption") or ""
        title = _caption_parts(caption)[0] or f"Product {pid}"
        product_lastmod = _product_lastmod(product, today)
        product_mapping_lastmod = mapping_by_taxonomy.get(
            _seo_taxonomy_key(product.get("category") or "", product.get("subcategory") or ""),
            product_lastmod,
        )
        append_localized(
            _canonical_product_path(int(pid), caption),
            "product",
            title,
            max(product_lastmod, product_mapping_lastmod),
        )

    return {
        "total": len(pages),
        "shown": min(len(pages), limit),
        "pages": pages[:limit],
    }


async def _build_storefront_index_context(
    request: Request,
    *,
    include_seo_links: bool = False,
    language: str = "en",
) -> tuple[dict, list[dict[str, Any]]]:
    """Build shared storefront homepage context for both public and Mini App shells."""
    language = _normalize_seo_language(language)
    ui = _seo_ui(language)
    home_path = "/"
    ctx = _build_seo_context(
        title=f"Top Shelf Cali Plug - {ui['catalog_title']}",
        desc=ui["catalog_desc"],
        url=_localized_url(home_path, language),
        language=language,
        alternate_path=home_path,
    )
    ctx["request"] = request
    category_counts: dict[str, int] = {}
    recent_products: list[dict[str, Any]] = []
    if include_seo_links:
        try:
            category_counts, _, _ = await _catalog_db.get_category_summary(EXCLUDED_FROM_ALL_PRODUCTS)
            recent_products = await _catalog_db.get_all_products_excluding_categories(
                EXCLUDED_FROM_ALL_PRODUCTS,
                limit=12,
            )
        except Exception as exc:
            logger.debug("Failed to load homepage SEO links: %s", exc)

    ctx["seo_categories"] = [
        {
            "url": _localized_path(_canonical_category_path(category), language),
            "label": get_category_display_name(category, language),
            "count": category_counts.get(category, 0),
        }
        for category in get_all_categories()
        if not include_seo_links or category_counts.get(category, 0) > 0
    ]
    public_catalog_bot_url = _public_catalog_bot_url()
    ctx["seo_recent_products"] = [
        {
            "id": product.get("id") or product.get("product_id"),
            "title": _caption_parts(product.get("caption") or "")[0],
            "url": _localized_path(_canonical_product_path(product.get("id") or product.get("product_id"), product.get("caption") or ""), language),
            "category": get_category_display_name(product.get("category"), language) if product.get("category") else "",
            "subcategory": get_subcategory_display_name(product.get("subcategory"), language) if product.get("subcategory") else "",
            "image_url": _product_primary_image_url(product, absolute=True),
            "media_url": preview_media.get("url") or "",
            "media_type": preview_media.get("type") or "",
            "media_count": len(media_info),
            "media_info": media_info,
        }
        for product in recent_products
        if (media_info := _collect_media_info(product)) and (product.get("id") or product.get("product_id"))
        for preview_media in [_product_card_preview_media(product, absolute=True)]
    ]
    ctx.update(await _public_order_context(telegram_bot_url=public_catalog_bot_url))
    if include_seo_links:
        try:
            schema = json.loads(ctx.get("seo_schema") or "{}")
            graph = schema.setdefault("@graph", [])
            if ctx["seo_categories"]:
                graph.append({
                    "@type": "ItemList",
                    "name": ui["catalog_title"],
                    "inLanguage": language,
                    "itemListElement": [
                        {
                            "@type": "ListItem",
                            "position": idx + 1,
                            "name": item["label"],
                            "url": _absolute_storefront_url(item["url"]),
                        }
                        for idx, item in enumerate(ctx["seo_categories"])
                    ],
                })
            if ctx["seo_recent_products"]:
                graph.append({
                    "@type": "ItemList",
                    "name": ui["related_products"],
                    "inLanguage": language,
                    "itemListElement": [
                        {
                            "@type": "ListItem",
                            "position": idx + 1,
                            "item": {
                                "@type": "Product",
                                "name": item["title"],
                                "url": _absolute_storefront_url(item["url"]),
                                **({"image": _absolute_storefront_url(item["image_url"])} if item.get("image_url") else {}),
                                "category": item["category"],
                            },
                        }
                        for idx, item in enumerate(ctx["seo_recent_products"])
                    ],
                })
            ctx["seo_schema"] = json.dumps(schema, ensure_ascii=False)
        except Exception as exc:
            logger.debug("Failed to build homepage SEO ItemList schema: %s", exc)
    ctx["localized_home_url"] = _localized_path("/", language)
    return ctx, recent_products


def _render_uncached_template(
    request: Request,
    template_name: str,
    context: dict,
) -> HTMLResponse:
    """Render dynamic Mini App templates without public SEO caching."""
    ctx = dict(context)
    ctx["request"] = request
    content = templates.env.get_template(template_name).render(ctx)
    return HTMLResponse(
        content,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


async def _serve_storefront_index(
    request: Request,
    *,
    include_seo_links: bool = False,
    language: str = "en",
) -> HTMLResponse:
    """Render the public SEO-first storefront homepage."""
    ctx, recent_products = await _build_storefront_index_context(
        request,
        include_seo_links=include_seo_links,
        language=language,
    )
    return _render_seo_template(
        request,
        "localized_home.html",
        ctx,
        cache_key=f"localized-home-public-search-v3:{language}:{len(ctx['seo_recent_products'])}:{_newest_lastmod(recent_products, datetime.now().date().isoformat())}",
    )


async def _serve_webapp_index(
    request: Request,
    *,
    include_seo_links: bool = True,
    language: str = "en",
) -> HTMLResponse:
    """Render the Telegram Mini App shell with the shared storefront data/styling."""
    ctx, recent_products = await _build_storefront_index_context(
        request,
        include_seo_links=include_seo_links,
        language=language,
    )
    return _render_uncached_template(request, "index.html", ctx)


def _product_id_from_query(request: Request) -> Optional[int]:
    raw = request.query_params.get("product_id") or request.query_params.get("product")
    if raw and raw.isdigit():
        return int(raw)
    return None


def _looks_like_telegram_webapp_request(request: Request) -> bool:
    """Detect Telegram Mini App launches that should stay in the shell."""
    user_agent = (request.headers.get("user-agent") or "").lower()
    if "telegram" in user_agent:
        return True
    if request.query_params.get("tgWebAppStartParam") or request.query_params.get("start_param"):
        return True
    return any(key.lower().startswith("tgwebapp") for key in request.query_params.keys())


async def _maybe_serve_webapp_shell(
    request: Request,
    *,
    language: str = "en",
    include_seo_links: bool = True,
) -> Optional[HTMLResponse]:
    """Serve the Mini App shell for stale or root-based Telegram launch URLs.

    Public browser traffic still gets crawlable SEO pages, while Telegram clients
    that open an older root URL do not get stranded on the public storefront.
    """
    if not _looks_like_telegram_webapp_request(request):
        return None

    product_id = _product_id_from_query(request)
    _queue_visitor_event(
        request,
        source="webapp",
        page_type="webapp_shell",
        product_id=product_id,
        language=language,
    )
    return await _serve_webapp_index(
        request,
        include_seo_links=include_seo_links,
        language=language,
    )


@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
async def serve_homepage(request: Request):
    """Serve the homepage with default Storefront SEO metadata."""
    webapp_response = await _maybe_serve_webapp_shell(request, include_seo_links=True)
    if webapp_response is not None:
        return webapp_response

    product_id = _product_id_from_query(request)
    if product_id:
        product = await _catalog_db.get_product(product_id)
        if product:
            return RedirectResponse(
                url=_canonical_product_path(product_id, product.get("caption") or ""),
                status_code=status.HTTP_301_MOVED_PERMANENTLY,
            )
        return await _missing_product_redirect(product_id, permanent_when_known=True)
    _queue_visitor_event(request, source="website", page_type="home")
    return await _serve_storefront_index(request, include_seo_links=True)


@app.get("/de", response_class=HTMLResponse)
@app.get("/de/", response_class=HTMLResponse)
@app.get("/nl", response_class=HTMLResponse)
@app.get("/nl/", response_class=HTMLResponse)
@app.get("/it", response_class=HTMLResponse)
@app.get("/it/", response_class=HTMLResponse)
@app.get("/es", response_class=HTMLResponse)
@app.get("/es/", response_class=HTMLResponse)
@app.get("/fr", response_class=HTMLResponse)
@app.get("/fr/", response_class=HTMLResponse)
async def serve_localized_homepage(request: Request):
    language = _normalize_seo_language(request.url.path.strip("/").split("/", 1)[0])
    webapp_response = await _maybe_serve_webapp_shell(
        request,
        include_seo_links=True,
        language=language,
    )
    if webapp_response is not None:
        return webapp_response

    _queue_visitor_event(request, source="website", page_type="home", language=language)
    return await _serve_storefront_index(request, include_seo_links=True, language=language)


@app.get("/webapp/index.html", response_class=HTMLResponse)
@app.get("/webapp/", response_class=HTMLResponse)
@app.get("/webapp", response_class=HTMLResponse)
async def serve_webapp(request: Request):
    """Serve the Telegram Mini App shell."""
    product_id = _product_id_from_query(request)
    _queue_visitor_event(
        request,
        source="webapp",
        page_type="webapp_shell",
        product_id=product_id,
    )
    return await _serve_webapp_index(request, include_seo_links=True)


@app.get("/learn", response_class=HTMLResponse)
async def serve_learn_index(request: Request):
    return RedirectResponse(url="/", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@app.get("/{language}/learn", response_class=HTMLResponse)
async def serve_localized_learn_index(language: str, request: Request):
    language = _require_localized_language(language)
    return RedirectResponse(
        url=_localized_path("/", language),
        status_code=status.HTTP_301_MOVED_PERMANENTLY,
    )


@app.get("/learn/{slug}", response_class=HTMLResponse)
async def serve_learn_page(slug: str, request: Request):
    return RedirectResponse(url="/", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@app.get("/guides-feed.xml", response_class=Response)
async def serve_learn_feed():
    return RedirectResponse(url="/", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@app.get("/{language}/learn/{slug}", response_class=HTMLResponse)
async def serve_localized_learn_page(language: str, slug: str, request: Request):
    language = _require_localized_language(language)
    return RedirectResponse(
        url=_localized_path("/", language),
        status_code=status.HTTP_301_MOVED_PERMANENTLY,
    )


@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/", response_class=HTMLResponse)
async def serve_admin_dashboard():
    """Serve the read-only admin monitoring dashboard shell."""
    if not _is_admin_dashboard_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    dashboard_path = Path(__file__).parent / "admin" / "index.html"
    if not dashboard_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin dashboard not found")
    return HTMLResponse(
        dashboard_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/admin/api/overview")
async def admin_dashboard_overview(request: Request):
    """Return token-protected monitoring data for the admin dashboard."""
    _require_admin_dashboard_auth(request)
    db_snapshot = await _catalog_db.get_admin_dashboard_snapshot()
    metrics_snapshot = metrics.snapshot()
    webhook_snapshot = _webhook_monitor.snapshot()
    return _json_no_store({
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "configured_bot_count": len(Config.BOT_TOKENS),
        "runtime_bots": _runtime_bot_snapshots(),
        "cache_state": _cache_state_snapshot(),
        "process": _runtime_process_snapshot(),
        "media_runtime": _media_runtime_snapshot(),
        "resource_usage": {
            "tmp": get_tmp_usage(),
            "cache": get_cache_usage(),
        },
        "webhook_401_total": _webhook_401_counter,
        "metrics": metrics_snapshot,
        "webhook_monitor": webhook_snapshot,
        **db_snapshot,
    })


@app.get("/api/admin/status")
@app.get("/api/webapp-admin/status")
async def webapp_admin_status(request: Request):
    """Return admin monitoring data only to signed Telegram WebApp admins."""
    user, _, init_data = _get_authenticated_webapp_user(request)
    await _check_user_not_blocked(init_data, get_client_ip(request))
    if int(user.id) not in {int(admin_id) for admin_id in Config.ADMIN_IDS}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    admin_user_id = int(user.id)
    now = _time.monotonic()
    cached = _admin_status_cache.get(admin_user_id)
    if cached and now - cached[0] < _ADMIN_STATUS_CACHE_SECONDS:
        payload = dict(cached[1])
        payload["cache_age_seconds"] = round(now - cached[0], 1)
        return _json_no_store(payload)

    db_snapshot = await _catalog_db.get_admin_dashboard_snapshot(recent_limit=10)
    seo_snapshot = await _catalog_db.get_search_intelligence_snapshot(limit=150)
    seo_snapshot["search_console_setup"] = describe_search_console_setup(
        enabled=Config.SEARCH_CONSOLE_ENABLED,
        site_url=Config.SEARCH_CONSOLE_SITE_URL,
        service_account_json=Config.SEARCH_CONSOLE_SERVICE_ACCOUNT_JSON,
        service_account_file=Config.SEARCH_CONSOLE_SERVICE_ACCOUNT_FILE,
        oauth_client_json=Config.SEARCH_CONSOLE_OAUTH_CLIENT_JSON,
        oauth_client_file=Config.SEARCH_CONSOLE_OAUTH_CLIENT_FILE,
        oauth_token_json=Config.SEARCH_CONSOLE_OAUTH_TOKEN_JSON,
        oauth_token_file=Config.SEARCH_CONSOLE_OAUTH_TOKEN_FILE,
    )
    crawlable_snapshot = await _crawlable_page_snapshot(limit=5000)
    indexnow_snapshot = dict(db_snapshot.get("indexnow") or {})
    indexnow_snapshot.update({
        "enabled": _indexnow_enabled(),
        "endpoint": Config.INDEXNOW_ENDPOINT,
        "key_file_available": bool(_indexnow_key(create=False)),
    })
    payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "admin_user_id": admin_user_id,
        "cache_age_seconds": 0,
        "configured_bot_count": len(Config.BOT_TOKENS),
        "runtime_bots": _runtime_bot_snapshots(),
        "cache_state": _cache_state_snapshot(),
        "process": _runtime_process_snapshot(),
        "media_runtime": _media_runtime_snapshot(),
        "resource_usage": {
            "tmp": get_tmp_usage(),
            "cache": get_cache_usage(),
        },
        "webhook_401_total": _webhook_401_counter,
        "metrics": metrics.snapshot(),
        "webhook_monitor": _webhook_monitor.snapshot(),
        "seo": seo_snapshot,
        "crawlable_pages": crawlable_snapshot,
        **db_snapshot,
        "indexnow": indexnow_snapshot,
    }
    _admin_status_cache[admin_user_id] = (now, payload)
    _admin_status_cache.move_to_end(admin_user_id)
    while len(_admin_status_cache) > _ADMIN_STATUS_CACHE_MAX:
        _admin_status_cache.popitem(last=False)
    return _json_no_store(payload)




@app.get("/product/{product_id}", response_class=HTMLResponse)
@app.head("/product/{product_id}", response_class=HTMLResponse)
async def serve_product_page(product_id: int, request: Request):
    """Redirect short product URLs to their canonical slug URL."""
    return await _serve_product_short(product_id, "en")


async def _serve_product_short(product_id: int, language: str):
    product = await _catalog_db.get_product(product_id)
    if not product:
        return await _missing_product_redirect(product_id, language=language)
    return RedirectResponse(
        url=_localized_path(_canonical_product_path(product_id, product.get("caption") or ""), language),
        status_code=301,
    )


@app.get("/{language}/product/{product_id}", response_class=HTMLResponse)
@app.head("/{language}/product/{product_id}", response_class=HTMLResponse)
async def serve_localized_product_page(language: str, product_id: int, request: Request):
    language = _require_localized_language(language)
    if language == "en":
        return RedirectResponse(url=f"/product/{product_id}", status_code=301)
    return await _serve_product_short(product_id, language)


@app.get("/product/{product_id}/{slug}", response_class=HTMLResponse)
@app.head("/product/{product_id}/{slug}", response_class=HTMLResponse)
async def serve_product_page_with_slug(product_id: int, slug: str, request: Request):
    """Serve an individual product page with crawlable HTML and dynamic SEO metadata."""
    return await _serve_product_page_with_slug(product_id, slug, request, "en")


async def _serve_product_page_with_slug(product_id: int, slug: str, request: Request, language: str):
    language = _normalize_seo_language(language)
    product = await _catalog_db.get_product(product_id)
    if not product:
        return await _missing_product_redirect(product_id, slug=slug, language=language)

    caption: str = product.get("caption") or ""
    canonical_slug = _generate_slug(caption)
    if slug != canonical_slug:
        return RedirectResponse(
            url=_localized_path(_canonical_product_path(product_id, caption), language),
            status_code=301,
        )
    _log_seo_public_request(request, "product", product_id=product_id, slug=slug)

    category = product.get("category") or ""
    subcategory = product.get("subcategory") or ""
    product_title, raw_seo_desc, description_lines = _caption_parts(caption)
    localized_description_lines = await _translate_seo_lines(description_lines, language)
    localized_raw_desc = " ".join(localized_description_lines)[:160] or raw_seo_desc
    seo_desc = _build_product_seo_description(product_title, localized_raw_desc, category, subcategory, language)
    seo_title = _build_product_seo_title(product_title, category, subcategory, language)
    seo_image = _product_primary_image_url(product)
    product_path = _canonical_product_path(product_id, caption)
    product_url = _localized_url(product_path, language)
    deep_link = _public_product_bot_url(product_id)
    public_product_bot_username = _public_product_bot_username()
    webapp_link = build_webapp_product_url(Config.WEB_APP_URL, product_id)
    _queue_visitor_event(
        request,
        source="website",
        page_type="product",
        product_id=product_id,
        category=category,
        subcategory=subcategory,
        language=language,
    )
    seo_side_notes = await _seo_side_note_terms(category, subcategory, language) if category else []
    product_search_phrases = _product_search_phrases(
        title=product_title,
        caption=caption,
        category=category,
        subcategory=subcategory,
        seo_terms=seo_side_notes,
        language=language,
    )
    visible_search_terms = _dedupe_seo_phrases([*product_search_phrases, *seo_side_notes], limit=18)
    order_context = await _public_order_context(telegram_bot_url=deep_link)
    product_media = [
        {
            "index": idx,
            "type": media.get("type") or "document",
            "url": f"/api/catalog/{product_id}/media/{idx}",
            "seo_url": _product_public_media_path(product_id, idx) if media.get("type") == "photo" else "",
            "label": f"{product_title} {_seo_ui(language)['media']} {idx + 1}",
        }
        for idx, media in enumerate(_collect_media_info(product))
    ]
    related_products = []
    if category:
        try:
            if hasattr(_catalog_db, "get_related_products"):
                candidates = await _catalog_db.get_related_products(
                    product_id,
                    category,
                    subcategory=subcategory,
                    limit=6,
                )
            else:
                candidates = await _catalog_db.get_products_by_category(category, limit=12)
            related_products = [
                {
                    "id": p.get("id") or p.get("product_id"),
                    "title": _caption_parts(p.get("caption") or "")[0],
                    "url": _localized_path(_canonical_product_path(p.get("id") or p.get("product_id"), p.get("caption") or ""), language),
                    "media_type": preview_media.get("type") or "",
                    "media_url": preview_media.get("url") or "",
                }
                for p in candidates
                if (p.get("id") or p.get("product_id")) != product_id
                for preview_media in [_product_card_preview_media(p, absolute=True)]
            ][:6]
        except Exception as exc:
            logger.debug("Failed to load related products for SEO page: %s", exc)

    ctx = _build_seo_context(
        title=seo_title,
        desc=seo_desc,
        image=seo_image,
        url=product_url,
        product=product,
        deep_link=deep_link,
        og_type="product",
        schema_title=product_title,
        keywords=", ".join(filter(None, [
            product_title,
            ", ".join(product_search_phrases),
            product.get("category", ""),
            product.get("subcategory", ""),
            ", ".join(seo_side_notes[:8]),
            " ".join(caption.split()[:6]),
            "Top Shelf Cali Plug",
        ])),
        category=category,
        language=language,
        alternate_path=product_path,
    )
    ctx["request"] = request
    ctx["product"] = product
    ctx["product_id"] = product_id
    ctx["product_title"] = product_title
    ctx["description_lines"] = localized_description_lines
    ctx["category"] = category
    ctx["category_display"] = get_category_display_name(category, language) if category else ""
    ctx["category_url"] = _localized_path(_canonical_category_path(category), language) if category else ""
    ctx["subcategory"] = subcategory
    ctx["subcategory_display"] = get_subcategory_display_name(subcategory, language) if subcategory else ""
    ctx["subcategory_url"] = _localized_path(_canonical_subcategory_path(category, subcategory), language) if category and subcategory else ""
    ctx["product_image"] = _product_primary_image_url(product, absolute=False)
    ctx["product_media"] = product_media
    ctx["telegram_bot_url"] = deep_link
    ctx["webapp_product_url"] = webapp_link
    ctx["related_products"] = related_products
    ctx["seo_side_notes"] = visible_search_terms
    ctx["product_search_phrases"] = product_search_phrases
    ctx["localized_home_url"] = _localized_path("/", language)
    ctx.update(order_context)
    cache_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "caption": caption,
                "category": category,
                "subcategory": subcategory,
                "search_phrases": product_search_phrases,
                "media": [m.get("type") for m in _collect_media_info(product)],
                "public_product_bot": public_product_bot_username,
                "order": order_context.get("order_fingerprint"),
                "updated_at": product.get("updated_at") or product.get("created_at"),
                "language": language,
            },
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return _render_seo_template(
        request,
        "product.html",
        ctx,
        cache_key=f"product:{language}:{product_id}:{slug}:{cache_fingerprint}",
    )


@app.get("/{language}/product/{product_id}/{slug}", response_class=HTMLResponse)
@app.head("/{language}/product/{product_id}/{slug}", response_class=HTMLResponse)
async def serve_localized_product_page_with_slug(language: str, product_id: int, slug: str, request: Request):
    language = _require_localized_language(language)
    if language == "en":
        return RedirectResponse(url=f"/product/{product_id}/{slug}", status_code=301)
    return await _serve_product_page_with_slug(product_id, slug, request, language)


@app.get("/category/{category_name}", response_class=HTMLResponse)
@app.head("/category/{category_name}", response_class=HTMLResponse)
async def serve_category_page(category_name: str, request: Request):
    """Serve a server-rendered category landing page for SEO crawlability."""
    return await _serve_category_page(category_name, request, "en")


async def _serve_category_page(category_name: str, request: Request, language: str):
    language = _normalize_seo_language(language)
    matched_category = _resolve_category_name(category_name)
    if matched_category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    base_path = _canonical_category_path(matched_category)
    canonical_path = _localized_path(base_path, language)
    if request.url.path != canonical_path:
        return RedirectResponse(url=canonical_path, status_code=301)
    _log_seo_public_request(request, "category", category=matched_category)
    _queue_visitor_event(request, source="website", page_type="category", category=matched_category, language=language)

    display_name = get_category_display_name(matched_category, language)
    subcategories = get_subcategories(matched_category)

    total = await _catalog_db.count_products_by_category(matched_category)
    page_size = 24
    total_pages = max(1, (total + page_size - 1) // page_size)
    current_page = min(_seo_page_number(request), total_pages)
    products = await _catalog_db.get_products_by_category(
        matched_category,
        limit=page_size,
        offset=(current_page - 1) * page_size,
    )

    # Get subcategory counts
    all_subcategories_data = await _catalog_db.get_all_subcategories_with_counts()
    subcategory_counts = {
        item["subcategory"]: item["count"]
        for item in all_subcategories_data.get(matched_category, [])
    }

    page_path = _paginated_path(base_path, current_page)
    category_url = _localized_url(page_path, language)

    # Build keyword list from category + subcategory names
    subcat_names = ", ".join(subcategories) if subcategories else ""
    seo_terms = await _seo_side_note_terms(matched_category, language=language)
    public_catalog_bot_url = _public_catalog_bot_url()
    order_context = await _public_order_context(telegram_bot_url=public_catalog_bot_url)
    keywords = ", ".join(filter(None, [
        display_name,
        subcat_names,
        ", ".join(seo_terms),
        "Top Shelf Cali Plug",
        "buy online",
    ]))

    ctx = _build_seo_context(
        title=_build_category_seo_title(display_name),
        desc=f"{_seo_ui(language)['catalog_desc']} {total} {_seo_ui(language)['products_available']}. {_seo_ui(language)['order_via']}.",
        url=category_url,
        keywords=keywords,
        language=language,
        alternate_path=page_path,
    )

    # Attach extra context for the category template
    ctx["request"] = request
    ctx["category_name"] = matched_category
    ctx["category_display"] = display_name
    ctx["subcategories"] = [
        {
            "name": s,
            "display": get_subcategory_display_name(s, language),
            "count": subcategory_counts.get(s, 0),
            "url": _localized_path(_canonical_subcategory_path(matched_category, s), language),
        }
        for s in subcategories
    ]
    ctx["products"] = [
        {
            "id": p.get("id") or p.get("product_id"),
            "caption": p.get("caption") or "",
            "title": _card_preview_title(p.get("caption") or ""),
            "category": get_category_display_name(p.get("category"), language) if p.get("category") else "",
            "subcategory": p.get("subcategory") or "",
            "subcategory_display": get_subcategory_display_name(p.get("subcategory"), language) if p.get("subcategory") else "",
            "slug": _generate_slug(p.get("caption") or ""),
            "url": _localized_path(_canonical_product_path(p.get("id") or p.get("product_id"), p.get("caption") or ""), language),
            "image_url": _product_primary_image_url(p, absolute=False),
            "media_url": preview_media.get("url") or "",
            "media_type": preview_media.get("type") or "",
        }
        for p in products
        for preview_media in [_product_card_preview_media(p, absolute=False)]
    ]
    ctx["total"] = total
    ctx["current_subcategory"] = ""
    ctx["current_subcategory_display"] = ""
    ctx["parent_category_url"] = ""
    ctx["seo_intro"] = _build_visible_taxonomy_intro(
        matched_category,
        "",
        total=total,
        terms=seo_terms,
        language=language,
    )
    ctx["seo_terms"] = seo_terms
    ctx["telegram_bot_url"] = public_catalog_bot_url
    ctx["storefront_domain"] = _STOREFRONT_DOMAIN
    ctx["localized_home_url"] = _localized_path("/", language)
    ctx["current_page"] = current_page
    ctx["total_pages"] = total_pages
    ctx["previous_url"] = _localized_path(_paginated_path(base_path, current_page - 1), language) if current_page > 1 else ""
    ctx["next_url"] = _localized_path(_paginated_path(base_path, current_page + 1), language) if current_page < total_pages else ""
    ctx.update(order_context)

    # Build ItemList + BreadcrumbList schema
    item_list_schema = {
        "@type": "ItemList",
        "name": display_name,
        "url": category_url,
        "inLanguage": language,
        "numberOfItems": total,
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "item": {
                    "@type": "Product",
                    "name": p["title"],
                    "url": _absolute_storefront_url(p["url"]),
                    **({"image": _absolute_storefront_url(p["image_url"])} if p.get("image_url") else {}),
                    "category": matched_category,
                    "inLanguage": language,
                },
            }
            for i, p in enumerate(ctx["products"])
        ],
    }
    breadcrumb_schema = {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": _seo_ui(language)["home"], "item": _localized_url("/", language)},
            {"@type": "ListItem", "position": 2, "name": display_name, "item": category_url},
        ],
    }
    collection_schema = {
        "@type": "CollectionPage",
        "name": _build_category_seo_title(display_name),
        "description": ctx["seo_desc"],
        "url": category_url,
        "inLanguage": language,
        "mainEntity": item_list_schema,
    }
    ctx["seo_schema"] = json.dumps({
        "@context": "https://schema.org",
        "@graph": [collection_schema, item_list_schema, breadcrumb_schema],
    }, ensure_ascii=False)

    return _render_seo_template(
        request,
        "category.html",
        ctx,
        cache_key=f"category-card-media-v2:{language}:{matched_category}:{current_page}:{total}:{_newest_lastmod(products, datetime.now().date().isoformat())}:{public_catalog_bot_url}:{order_context.get('order_fingerprint')}",
    )


@app.get("/{language}/category/{category_name}", response_class=HTMLResponse)
@app.head("/{language}/category/{category_name}", response_class=HTMLResponse)
async def serve_localized_category_page(language: str, category_name: str, request: Request):
    language = _require_localized_language(language)
    if language == "en":
        return RedirectResponse(url=_canonical_category_path(category_name), status_code=301)
    return await _serve_category_page(category_name, request, language)


@app.get("/category/{category_name}/{subcategory_name}", response_class=HTMLResponse)
@app.head("/category/{category_name}/{subcategory_name}", response_class=HTMLResponse)
async def serve_subcategory_page(category_name: str, subcategory_name: str, request: Request):
    """Serve a server-rendered subcategory page for stronger crawlable category depth."""
    return await _serve_subcategory_page(category_name, subcategory_name, request, "en")


async def _serve_subcategory_page(category_name: str, subcategory_name: str, request: Request, language: str):
    language = _normalize_seo_language(language)
    matched_category = _resolve_category_name(category_name)
    if matched_category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    matched_subcategory = _resolve_subcategory_name(matched_category, subcategory_name)
    if matched_subcategory is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subcategory not found")

    base_path = _canonical_subcategory_path(matched_category, matched_subcategory)
    canonical_path = _localized_path(base_path, language)
    if request.url.path != canonical_path:
        return RedirectResponse(url=canonical_path, status_code=301)
    _log_seo_public_request(
        request,
        "subcategory",
        category=matched_category,
        subcategory=matched_subcategory,
    )
    _queue_visitor_event(
        request,
        source="website",
        page_type="subcategory",
        category=matched_category,
        subcategory=matched_subcategory,
        language=language,
    )

    category_display = get_category_display_name(matched_category, language)
    subcategory_display = get_subcategory_display_name(matched_subcategory, language)
    total = await _catalog_db.count_products_by_category_and_subcategory(matched_category, matched_subcategory)
    page_size = 24
    total_pages = max(1, (total + page_size - 1) // page_size)
    current_page = min(_seo_page_number(request), total_pages)
    products = await _catalog_db.get_products_by_category_and_subcategory(
        matched_category,
        matched_subcategory,
        limit=page_size,
        offset=(current_page - 1) * page_size,
    )
    page_path = _paginated_path(base_path, current_page)
    page_url = _localized_url(page_path, language)
    seo_terms = await _seo_side_note_terms(matched_category, matched_subcategory, language)
    public_catalog_bot_url = _public_catalog_bot_url()
    order_context = await _public_order_context(telegram_bot_url=public_catalog_bot_url)

    ctx = _build_seo_context(
        title=_build_subcategory_seo_title(category_display, subcategory_display),
        desc=f"{total} {subcategory_display} {category_display}. {_seo_ui(language)['catalog_desc']} {_seo_ui(language)['order_via']}.",
        url=page_url,
        keywords=", ".join(filter(None, [
            subcategory_display,
            category_display,
            ", ".join(seo_terms),
            "Top Shelf Cali Plug",
            "Telegram store",
        ])),
        language=language,
        alternate_path=page_path,
    )
    ctx["request"] = request
    ctx["category_name"] = matched_category
    ctx["category_display"] = category_display
    ctx["subcategories"] = []
    ctx["products"] = [
        {
            "id": p.get("id") or p.get("product_id"),
            "caption": p.get("caption") or "",
            "title": _card_preview_title(p.get("caption") or ""),
            "category": get_category_display_name(p.get("category"), language) if p.get("category") else "",
            "subcategory": p.get("subcategory") or "",
            "subcategory_display": subcategory_display,
            "slug": _generate_slug(p.get("caption") or ""),
            "url": _localized_path(_canonical_product_path(p.get("id") or p.get("product_id"), p.get("caption") or ""), language),
            "image_url": _product_primary_image_url(p, absolute=False),
            "media_url": preview_media.get("url") or "",
            "media_type": preview_media.get("type") or "",
        }
        for p in products
        for preview_media in [_product_card_preview_media(p, absolute=False)]
    ]
    ctx["total"] = total
    ctx["current_subcategory"] = matched_subcategory
    ctx["current_subcategory_display"] = subcategory_display
    ctx["parent_category_url"] = _localized_path(_canonical_category_path(matched_category), language)
    ctx["seo_intro"] = _build_visible_taxonomy_intro(
        matched_category,
        matched_subcategory,
        total=total,
        terms=seo_terms,
        language=language,
    )
    ctx["seo_terms"] = seo_terms
    ctx["telegram_bot_url"] = public_catalog_bot_url
    ctx["storefront_domain"] = _STOREFRONT_DOMAIN
    ctx["localized_home_url"] = _localized_path("/", language)
    ctx["current_page"] = current_page
    ctx["total_pages"] = total_pages
    ctx["previous_url"] = _localized_path(_paginated_path(base_path, current_page - 1), language) if current_page > 1 else ""
    ctx["next_url"] = _localized_path(_paginated_path(base_path, current_page + 1), language) if current_page < total_pages else ""
    ctx.update(order_context)

    item_list_schema = {
        "@type": "ItemList",
        "name": f"{subcategory_display} {category_display}",
        "url": page_url,
        "inLanguage": language,
        "numberOfItems": total,
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "item": {
                    "@type": "Product",
                    "name": p["title"],
                    "url": _absolute_storefront_url(p["url"]),
                    **({"image": _absolute_storefront_url(p["image_url"])} if p.get("image_url") else {}),
                    "category": matched_category,
                    "inLanguage": language,
                },
            }
            for i, p in enumerate(ctx["products"])
        ],
    }
    breadcrumb_schema = {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": _seo_ui(language)["home"], "item": _localized_url("/", language)},
            {"@type": "ListItem", "position": 2, "name": category_display, "item": _localized_url(_canonical_category_path(matched_category), language)},
            {"@type": "ListItem", "position": 3, "name": subcategory_display, "item": page_url},
        ],
    }
    collection_schema = {
        "@type": "CollectionPage",
        "name": _build_subcategory_seo_title(category_display, subcategory_display),
        "description": ctx["seo_desc"],
        "url": page_url,
        "inLanguage": language,
        "mainEntity": item_list_schema,
    }
    ctx["seo_schema"] = json.dumps({
        "@context": "https://schema.org",
        "@graph": [collection_schema, item_list_schema, breadcrumb_schema],
    }, ensure_ascii=False)
    return _render_seo_template(
        request,
        "category.html",
        ctx,
        cache_key=f"subcategory-card-media-v2:{language}:{matched_category}:{matched_subcategory}:{current_page}:{total}:{_newest_lastmod(products, datetime.now().date().isoformat())}:{public_catalog_bot_url}:{order_context.get('order_fingerprint')}",
    )


@app.get("/{language}/category/{category_name}/{subcategory_name}", response_class=HTMLResponse)
@app.head("/{language}/category/{category_name}/{subcategory_name}", response_class=HTMLResponse)
async def serve_localized_subcategory_page(language: str, category_name: str, subcategory_name: str, request: Request):
    language = _require_localized_language(language)
    if language == "en":
        return RedirectResponse(url=_canonical_subcategory_path(category_name, subcategory_name), status_code=301)
    return await _serve_subcategory_page(category_name, subcategory_name, request, language)


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


def _remember_webapp_session(init_data: str) -> str:
    """Create a short-lived same-origin token for media URLs/cookies."""
    now = _time.time()
    expires_at = now + WEBAPP_SESSION_TTL_SECONDS
    token = secrets.token_urlsafe(24)
    _webapp_session_tokens[token] = (init_data, expires_at)
    _webapp_session_tokens.move_to_end(token)

    while _webapp_session_tokens:
        oldest_token, (_, oldest_expiry) = next(iter(_webapp_session_tokens.items()))
        if oldest_expiry > now and len(_webapp_session_tokens) <= _WEBAPP_SESSION_MAX:
            break
        _webapp_session_tokens.pop(oldest_token, None)
    return token


def _get_webapp_session_init_data(request: Request) -> str:
    """Resolve initData from the short-lived WebApp session cookie/query token."""
    token = request.cookies.get(WEBAPP_SESSION_COOKIE) or request.query_params.get("session")
    if not token:
        return ""

    entry = _webapp_session_tokens.get(token)
    if not entry:
        return ""

    init_data, expires_at = entry
    now = _time.time()
    if expires_at <= now:
        _webapp_session_tokens.pop(token, None)
        return ""

    _webapp_session_tokens.move_to_end(token)
    return init_data


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

    if not init_data:
        init_data = _get_webapp_session_init_data(request)
    
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


def _get_authenticated_webapp_user(request: Request) -> tuple[TelegramUser, int, str]:
    """Validate WebApp initData and return the signed Telegram user."""
    init_data, bot_index = _get_validated_init_data(request, allow_preview=False)
    parsed = parse_init_data(init_data)
    user_data = parsed.get("user")
    if not user_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Telegram user")
    return TelegramUser.model_validate(user_data), bot_index, init_data


def _require_same_webapp_user(target_user_id: int, user: TelegramUser) -> None:
    """Reject WebApp requests attempting to mutate another user's state."""
    if int(target_user_id) != int(user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User mismatch")


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
    if await is_user_blocked_cached(
        _catalog_db,
        user_id,
        username=user_data.username,
        ip_address=client_ip,
    ):
        logger_security.warning(
            "[WEBAPP_BLOCKED] user_id=%d attempted WebApp API access while blocked",
            user_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    # Record IP → user mapping for future block enforcement
    if client_ip and client_ip != "unknown":
        try:
            await record_user_ip_debounced(_catalog_db, user_id, client_ip)
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

    media_info: List[dict] = [
        {"id": file_id, "type": file_type}
        for file_id, file_type in collect_product_media(product)
    ]

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


def _detect_contact_kind(label: str, url: str) -> str:
    """Best-effort classification for contact links."""
    label_l = (label or "").strip().lower()
    url_l = (url or "").strip().lower()
    if "signal" in label_l or "signal.me" in url_l or "signal.org" in url_l:
        return "signal"
    if "telegram" in label_l or "t.me/" in url_l or "telegram.me" in url_l:
        return "telegram"
    if "bot" in label_l or "t.me/" in url_l:
        return "bot"
    return "other"


def _build_contact_channels(
    order_contact: str,
    order_links: list[dict],
    telegram_bot_url: str = "",
) -> Dict[str, Any]:
    """Normalize contact settings into the public channels the UI needs."""
    order_contact_username = _normalize_contact(order_contact)
    telegram_contact_url = f"https://t.me/{order_contact_username}" if order_contact_username else ""

    signal_contact = next(
        (
            {
                "label": str(item.get("label") or "Signal")[:40],
                "url": str(item.get("url") or "")[:500],
            }
            for item in order_links or []
            if item.get("url") and _detect_contact_kind(str(item.get("label") or ""), str(item.get("url") or "")) == "signal"
        ),
        None,
    )
    telegram_link = next(
        (
            {
                "label": str(item.get("label") or "Telegram")[:40],
                "url": str(item.get("url") or "")[:500],
            }
            for item in order_links or []
            if item.get("url") and _detect_contact_kind(str(item.get("label") or ""), str(item.get("url") or "")) == "telegram"
        ),
        None,
    )

    bot_link = {
        "label": "Open in Telegram",
        "url": telegram_bot_url or "",
    }

    cards: list[dict[str, str]] = []
    if telegram_link and telegram_link["url"]:
        cards.append({"kind": "telegram", **telegram_link})
    elif telegram_contact_url:
        cards.append({"kind": "telegram", "label": f"Telegram @{order_contact_username}", "url": telegram_contact_url})
    if signal_contact and signal_contact["url"]:
        cards.append({"kind": "signal", **signal_contact})
    if bot_link["url"]:
        cards.append({"kind": "bot", **bot_link})

    # Preserve any additional non-duplicate links for the home page footer.
    extra_links = []
    seen_urls = {item["url"] for item in cards if item.get("url")}
    for item in order_links or []:
        label = str(item.get("label") or "Contact")[:40]
        url = str(item.get("url") or "")[:500]
        if not url or url in seen_urls:
            continue
        extra_links.append({"label": label, "url": url})
        seen_urls.add(url)

    return {
        "order_contact_username": order_contact_username,
        "telegram_contact_url": telegram_contact_url,
        "signal_contact": signal_contact,
        "telegram_link": telegram_link,
        "bot_link": bot_link,
        "cards": cards,
        "extra_links": extra_links,
    }


async def _public_order_context(telegram_bot_url: str = "") -> Dict[str, Any]:
    """Return public order/contact links while keeping /setcontact separate from /setlink."""
    order_contact = await _catalog_db.get_order_contact()
    order_links = await _catalog_db.get_order_links()
    channels = _build_contact_channels(order_contact, order_links, telegram_bot_url=telegram_bot_url)
    clean_links = [
        {
            "label": str(item.get("label") or "Contact")[:40],
            "url": str(item.get("url") or "")[:500],
        }
        for item in channels["extra_links"]
    ]
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "order_contact": order_contact,
                "order_links": clean_links,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()[:12]
    return {
        "order_contact": order_contact,
        "order_contact_username": channels["order_contact_username"],
        "order_contact_url": channels["telegram_contact_url"],
        "contact_channels": channels["cards"],
        "telegram_contact_url": channels["telegram_contact_url"],
        "signal_contact": channels["signal_contact"],
        "telegram_link": channels["telegram_link"],
        "bot_link": channels["bot_link"],
        "order_links": clean_links,
        "order_fingerprint": fingerprint,
    }


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
    cached_payload = _api_result_cache_get(cache_key)
    if cached_payload is not None:
        metrics.record("api_categories", (_time.monotonic() - _t0) * 1000)
        return Response(
            content=cached_payload,
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=60"}
        )
    
    category_counts, all_subcategories, all_products_count = await _catalog_db.get_category_summary(
        EXCLUDED_FROM_ALL_PRODUCTS
    )
    
    # Build category structure
    categories = []
    for category in get_all_categories():
        subcategories_list = get_subcategories(category)
        
        category_display = get_category_display_name(category, lang)
        
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
    cached_payload = _api_result_cache_get(cache_key)
    if cached_payload is not None:
        metrics.record("api_products", (_time.monotonic() - _t0) * 1000)
        return Response(
            content=cached_payload,
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=30"}
        )
    
    products, total = await _catalog_db.get_products_page(
        category=category,
        subcategory=subcategory,
        excluded_categories=EXCLUDED_FROM_ALL_PRODUCTS,
        limit=limit,
        offset=offset,
    )

    order_contact = await _catalog_db.get_order_contact()
    order_contact_username = _normalize_contact(order_contact)
    order_link = await _catalog_db.get_order_link()
    order_links = await _catalog_db.get_order_links()

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
            "image_url": _product_primary_image_url(product, absolute=True) or _DEFAULT_OG_IMAGE,
        })
    
    payload = json.dumps({
        "items": items,
        "total": total,
        "bot": bot.username if bot and getattr(bot, "username", None) else None,
        "order_contact": order_contact,
        "order_contact_username": order_contact_username,
        "order_link": order_link,
        "order_links": order_links
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
    bot_username = _STOREFRONT_BOT_USERNAME or (bot.username if bot else None) or ""
    public_product_bot_username = _public_product_bot_username()

    cache_key = f"product_detail:{product_id}:{lang}:{bot_username}:{public_product_bot_username}"
    cached_payload = _api_result_cache_get(cache_key)
    if cached_payload is not None:
        return Response(
            content=cached_payload,
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=30"},
        )

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
    order_links = await _catalog_db.get_order_links()

    storefront_url = (
        _canonical_product_url(product_id, product.get("caption") or "")
        if _STOREFRONT_DOMAIN
        else None
    )
    bot_deep_link = _public_product_bot_url(product_id)

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
        "order_links": order_links,
        "storefront_url": storefront_url,
        "bot_deep_link": bot_deep_link,
    })

    _api_result_cache_set(cache_key, payload)

    return Response(
        content=payload,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=30"},
    )


def _seo_media_fallback_response(request: Request, reason: str, *, image_url: str = "") -> Response:
    fallback_body = _read_site_icon_svg()
    fallback_type = "image/svg+xml"
    fallback_etag_key = f"seo-media-fallback:{reason}:{_SITE_ICON_VERSION}"
    if image_url:
        fallback_etag_key = f"seo-media-fallback:{reason}:{hashlib.sha256(image_url.encode('utf-8')).hexdigest()[:12]}"
    return _seo_content_response(
        request,
        fallback_body,
        media_type=fallback_type,
        max_age=_SEO_PUBLIC_MEDIA_CACHE_SECONDS,
        etag=_seo_etag_from_key(fallback_etag_key),
        extra_headers={
            "X-SEO-Media-Fallback": reason[:80],
        },
    )


@app.get("/media/products/{product_id}/{media_index}")
@app.head("/media/products/{product_id}/{media_index}")
async def stream_public_product_media(request: Request, product_id: int, media_index: int):
    """Public crawlable media URL used by SSR SEO/social preview pages.

    Photos and videos use the same bounded cache/streaming path as the Mini App.
    Bursts are rate-limited so crawlers cannot starve normal WebApp delivery.
    """
    _log_seo_public_request(request, "public_media", product_id=product_id, media_index=media_index)

    if not await _seo_public_media_rate_limiter.is_allowed(get_client_ip(request)):
        logger.info(
            "[seo_public_media] fallback reason=rate_limited product_id=%s media_index=%s",
            product_id,
            media_index,
        )
        return _seo_media_fallback_response(request, "rate_limited")

    try:
        product = await _catalog_db.get_product(product_id)
        if not product:
            return _seo_media_fallback_response(request, "product_missing")

        media_info = _collect_media_info(product)
        product_fallback_image = _product_primary_image_url(product, absolute=True)
        if media_index < 0 or media_index >= len(media_info):
            return _seo_media_fallback_response(request, "media_missing", image_url=product_fallback_image)

        media_type_info = media_info[media_index]
        media_kind = str(media_type_info.get("type") or "document")
        if media_kind not in {"photo", "video"}:
            fallback_index = _seo_preferred_fallback_media_index(product, media_index)
            if fallback_index is not None and fallback_index != media_index:
                fallback_response = await stream_catalog_media(request, product_id, fallback_index)
                if getattr(fallback_response, "status_code", 200) < 400:
                    fallback_response.headers["Cache-Control"] = f"public, max-age={_SEO_PUBLIC_MEDIA_CACHE_SECONDS}"
                    fallback_response.headers["X-Robots-Tag"] = "index, follow"
                    fallback_response.headers["Vary"] = "Accept"
                    return fallback_response
            return _seo_media_fallback_response(request, "non_photo", image_url=product_fallback_image)

        file_id = str(media_type_info.get("id") or "")
        etag = _seo_etag_from_key(f"seo-media:{product_id}:{media_index}:{file_id}")
        if _request_matches_etag(request, etag):
            return Response(
                status_code=status.HTTP_304_NOT_MODIFIED,
                headers=_seo_headers(etag, _SEO_PUBLIC_MEDIA_CACHE_SECONDS),
            )
        if request.method == "HEAD":
            return Response(
                status_code=status.HTTP_200_OK,
                headers=_seo_headers(
                    etag,
                    _SEO_PUBLIC_MEDIA_CACHE_SECONDS,
                    extra={
                        "Content-Type": "video/mp4" if media_kind == "video" else "image/jpeg",
                        "X-Robots-Tag": "index, follow",
                        "Accept-Ranges": "bytes" if media_kind == "video" else "none",
                        "Vary": "Accept",
                    },
                ),
            )

        response = await stream_catalog_media(request, product_id, media_index)
        if getattr(response, "status_code", 200) >= 400:
            if media_kind == "video":
                return response
            fallback_index = _seo_preferred_fallback_media_index(product, media_index)
            if fallback_index is not None and fallback_index != media_index:
                fallback_response = await stream_catalog_media(request, product_id, fallback_index)
                if getattr(fallback_response, "status_code", 200) < 400:
                    fallback_response.headers["Cache-Control"] = f"public, max-age={_SEO_PUBLIC_MEDIA_CACHE_SECONDS}"
                    fallback_response.headers["ETag"] = _seo_etag_from_key(f"seo-media:{product_id}:{fallback_index}")
                    fallback_response.headers["X-Robots-Tag"] = "index, follow"
                    fallback_response.headers["Vary"] = "Accept"
                    return fallback_response
            return _seo_media_fallback_response(
                request,
                f"upstream_{getattr(response, 'status_code', 0)}",
                image_url=product_fallback_image,
            )

        response.headers["Cache-Control"] = f"public, max-age={_SEO_PUBLIC_MEDIA_CACHE_SECONDS}"
        response.headers["ETag"] = etag
        response.headers["X-Robots-Tag"] = "index, follow"
        response.headers["Vary"] = "Accept"
        return response
    except Exception as exc:
        logger.info(
            "[seo_public_media] fallback reason=error product_id=%s media_index=%s error=%s",
            product_id,
            media_index,
            exc,
        )
        return _seo_media_fallback_response(request, "error", image_url=product_fallback_image if 'product_fallback_image' in locals() else "")


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
    if (
        init_data is None
        and _is_crawler_user_agent(request.headers.get("user-agent", ""))
        and not request.url.path.startswith("/media/products/")
    ):
        logger.info(
            "[media_cache] crawler_api_media_blocked product_id=%s media_index=%s ip=%s",
            product_id,
            media_index,
            get_client_ip(request),
        )
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers={
                "Cache-Control": "public, max-age=3600",
                "X-Robots-Tag": "noindex, nofollow",
            },
        )
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
    source_message_id = _resolve_product_media_message_id(product, product_id, media_index)
    file_id = await _resolve_bot_specific_media_file_id(
        bot=bot,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        file_type=media_type_info.get('type', 'document'),
        file_id=file_id,
        product_id=product_id,
        media_index=media_index,
    )

    # Structured logging for diagnostics
    logger.debug(
        f"Media request: product_id={product_id}, media_index={media_index}, "
        f"is_video={is_video}, file_id={file_id[:20]}..."
    )

    media_cache_key = hashlib.sha256(file_id.encode()).hexdigest()
    can_use_disk_cache = MEDIA_CACHE_ENABLED and (not is_video or MEDIA_CACHE_VIDEO_ENABLED)
    cached_file_path = await _media_cache.get(file_id) if can_use_disk_cache else None
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
            can_use_disk_cache,
        )
        # Not in cache, need to download from Telegram
        try:
            # Try to get file info - this can fail for files >20MB
            try:
                tg_file = await fetch_telegram_file(
                    bot,
                    file_id,
                    context=f"stream_catalog_media:{product_id}:{media_index}",
                    retries=1,
                    timeout_seconds=8.0,
                )
                file_size = tg_file.file_size or 0
                file_unique_id = getattr(tg_file, "file_unique_id", None)
                if file_unique_id:
                    bot_identifier = (getattr(bot, "username", None) or f"bot_{0}").lower()
                    if not _has_media_registry_write(bot_identifier, file_unique_id, file_id):
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
                        _remember_media_registry_write(bot_identifier, file_unique_id, file_id)
                    else:
                        logger.debug(
                            "[media_registry] duplicate_write_skipped product_id=%s bot=%s",
                            product_id,
                            bot_identifier,
                        )

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
                    owner_id = _runtime_index_to_registry_id.get(0)
                    if owner_id:
                        await _catalog_db.record_media_object(
                            media_id=hashlib.sha256(str(temp_input_path).encode("utf-8")).hexdigest(),
                            media_owner_id=owner_id,
                            storage_path=str(temp_input_path),
                            file_unique_id=file_unique_id if "file_unique_id" in locals() else None,
                            product_id=str(product_id),
                            ttl_expiry=datetime.now() + timedelta(seconds=MEDIA_CACHE_TTL_SECONDS),
                        )
                    logger.info(
                        "[media_cache] generation_completed path=%s product_id=%s media_index=%s bytes=%d",
                        temp_input_path.name,
                        product_id,
                        media_index,
                        temp_input_path.stat().st_size,
                    )

                    cached_path = await _media_cache.set(
                        file_id,
                        temp_input_path,
                        allow=can_use_disk_cache and temp_input_path.stat().st_size <= MEDIA_CACHE_MAX_FILE_BYTES,
                    )
                    file_path = cached_path or temp_input_path
                    temp_file_to_cleanup = temp_input_path
                    if cached_path:
                        await _mark_media_access(cached_path)
                        logger.info(
                            "[media_cache] cache_reused layer=disk product_id=%s media_index=%s is_video=%s",
                            product_id,
                            media_index,
                            is_video,
                        )

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
    media_type = _guess_stream_media_type(file_path, media_type_info.get("type", "document"))

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

            response = _build_media_range_response(
                file_path=file_path,
                media_type=media_type,
                start=start,
                end=end,
                content_length=content_length,
            )
            if request.url.path.startswith("/api/catalog/"):
                response.headers["X-Robots-Tag"] = "noindex, nofollow"
            return response
        except (ValueError, IndexError) as e:
            logger.warning(f"Invalid range header: {range_header}, error: {e}")
            pass  # Fall through to full response

    response = _build_media_full_response(
        file_path=file_path,
        media_type=media_type,
        content_length=content_length,
    )
    if request.url.path.startswith("/api/catalog/"):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.get("/api/search")
async def search_products(
    request: Request,
    q: str = "",
    limit: int = 24,
    offset: int = 0,
    lang: str = "en",
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

    cache_key = _webapp_search_cache_key(query, lang, limit, offset)
    cached_response = _get_webapp_search_cache(cache_key)
    if cached_response is not None:
        return cached_response

    try:
        try:
            db_mappings = await _get_cached_approved_seo_query_mappings()
        except Exception as exc:
            logger.debug("SEO query mappings unavailable for search expansion: %s", exc)
            db_mappings = []

        matched_mappings = match_query_mappings(query, db_mappings=db_mappings, limit=5)
        if matched_mappings:
            asyncio.create_task(
                _record_seo_mapping_hits(matched_mappings),
                name="seo_mapping_hits",
            )

        query_variants = build_query_variants(query, db_mappings=db_mappings, limit=8)
        candidate_limit = min(max(offset + limit, min(100, limit * 3)), 300)
        candidate_map: "OrderedDict[int, dict]" = OrderedDict()
        for variant in query_variants:
            rows = await _catalog_db.search_products_filtered(
                variant,
                EXCLUDED_FROM_ALL_PRODUCTS,
                limit=candidate_limit,
                offset=0,
            )
            for row in rows:
                product_key = row.get("id") or row.get("product_id")
                if product_key is not None and product_key not in candidate_map:
                    candidate_map[product_key] = row

        matched_products = list(candidate_map.values())
        if not matched_products:
            all_products = await _catalog_db.get_all_products_for_search()
            matched_products = rank_products_for_query(
                all_products,
                " ".join(query_variants[:4]),
                score_cutoff=70,
                limit=limit,
            )

        total = len(matched_products)
        products = matched_products[offset:offset + limit]
        expanded_query = ", ".join(query_variants)

        if total == 0:
            normalized_query = normalize_text(query)
            logger.info("[webapp_search_empty] query=%r normalized=%r expanded=%r", query, normalized_query, expanded_query)

        user_id = None
        try:
            user_obj = init_data.get("user") if isinstance(init_data, dict) else getattr(init_data, "user", None)
            user_id = user_obj.get("id") if isinstance(user_obj, dict) else getattr(user_obj, "id", None)
        except Exception:
            user_id = None
        try:
            asyncio.create_task(
                _catalog_db.log_search_query(
                    query=query,
                    expanded_query=expanded_query,
                    matched_count=total,
                    source="webapp",
                    language=lang if lang in SUPPORTED_LANGUAGES else "en",
                    user_id=user_id,
                    ip_address=get_client_ip(request),
                ),
                name="webapp_search_log",
            )
        except Exception as exc:
            logger.debug("Failed to log search query: %s", exc)

        order_contact = await _catalog_db.get_order_contact()
        order_contact_username = _normalize_contact(order_contact)
        order_link = await _catalog_db.get_order_link()
        order_links = await _catalog_db.get_order_links()
        
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
                "url": _localized_path(
                    _canonical_product_path(
                        product.get("id"),
                        product.get("caption") or "",
                    ),
                    lang,
                ),
            })
        
        payload = {
            "items": items,
            "total": total,
            "query": q,
            "expanded_query": expanded_query,
            "order_contact": order_contact,
            "order_contact_username": order_contact_username,
            "order_link": order_link,
            "order_links": order_links
        }
        _set_webapp_search_cache(cache_key, payload)
        return payload
        
    except Exception as e:
        logger.exception(f"Search failed for query '{q}': {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Search failed")


@app.post("/api/webapp-session")
async def create_webapp_session(request: Request):
    """Exchange signed Telegram initData for a short-lived session token."""
    init_data, _ = _get_validated_init_data(request, allow_preview=False)
    await _check_user_not_blocked(init_data, get_client_ip(request))
    _queue_visitor_event(
        request,
        source="webapp",
        page_type="session",
        init_data=init_data,
        is_pageview=False,
    )

    token = _remember_webapp_session(init_data)
    response = Response(
        content=json.dumps({"ok": True, "expires_in": WEBAPP_SESSION_TTL_SECONDS, "session": token}),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )
    return response


@app.get("/api/user/subscription")
async def get_user_subscription(request: Request, user_id: int):
    """Get user subscription status."""
    user, _, init_data = _get_authenticated_webapp_user(request)
    await _check_user_not_blocked(init_data, get_client_ip(request))
    _require_same_webapp_user(user_id, user)
    
    try:
        result = await _catalog_db.get_user_subscription(user_id)
        # User not in database → default to subscribed
        return _json_no_store({"is_subscribed": result if result is not None else True})
    except Exception as e:
        logger.exception(f"Error getting user subscription: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get subscription status")


@app.post("/api/user/subscription")
async def update_user_subscription(request: Request):
    """Update user subscription status."""
    user, _, init_data = _get_authenticated_webapp_user(request)
    await _check_user_not_blocked(init_data, get_client_ip(request))
    
    try:
        data = await request.json()
        user_id = data.get("user_id")
        is_subscribed = data.get("is_subscribed", True)
        
        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id required")

        _require_same_webapp_user(int(user_id), user)
        
        await _catalog_db.update_user_subscription(int(user_id), bool(is_subscribed))
        logger.info(f"User {user_id} subscription updated to: {is_subscribed}")
        return _json_no_store({"success": True, "is_subscribed": is_subscribed})
        
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
        init_data, _ = _get_validated_init_data(request, allow_preview=True)
        await _check_user_not_blocked(init_data, get_client_ip(request))
        
        data = await request.json()
        logs = data.get("logs", [])
        
        if not logs:
            return _json_no_store({"success": True, "message": "No logs to process"})

        persisted = 0
        try:
            persisted = await _catalog_db.log_webapp_events(logs)
        except Exception as persist_error:
            logger.warning("[WebApp] Failed to persist %d event log(s): %s", len(logs), persist_error)
        
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
        
        return _json_no_store({"success": True, "processed": len(logs), "persisted": persisted})
        
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
        await _check_user_not_blocked(init_data, get_client_ip(request))
        
        if not init_data:
            # No initData available
            return _json_no_store({
                "authenticated": False,
                "user_id": None,
                "username": None,
                "first_name": None,
                "last_name": None
            })
        
        # Parse user info from initData
        parsed_data = parse_init_data(init_data)
        user_data = parsed_data.get("user")

        if not isinstance(user_data, TelegramUser):
            return _json_no_store({
                "authenticated": False,
                "user_id": None,
                "username": None,
                "first_name": None,
                "last_name": None
            })

        # Extract user information
        user_id = user_data.id
        username = user_data.username
        first_name = user_data.first_name
        last_name = user_data.last_name
        telegram_language_code = user_data.language_code
        user_row = await _catalog_db.get_user_by_id(user_id)
        stored_language = (user_row or {}).get("language") if user_row else None
        preferred_language = _normalize_seo_language(stored_language or telegram_language_code or "en")
        
        return _json_no_store({
            "authenticated": True,
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "language_code": preferred_language,
            "preferred_language": preferred_language,
            "telegram_language_code": telegram_language_code,
            "language_source": "bot" if stored_language else "telegram"
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting user info: {e}")
        # Return unauthenticated response instead of error
        return _json_no_store({
            "authenticated": False,
            "user_id": None,
            "username": None,
            "first_name": None,
            "last_name": None
        })


@app.post("/api/user/track")
async def track_webapp_user(request: Request):
    """
    Track a webapp user in the bot_users database.
    Called when the webapp opens to ensure user is registered in the bot's
    user database for analytics and tracking purposes.
    """
    try:
        init_data, bot_index = _get_validated_init_data(request, allow_preview=True)
        await _check_user_not_blocked(init_data, get_client_ip(request))
        
        data = await request.json()
        user_id = data.get("user_id")
        username = data.get("username")
        first_name = data.get("first_name")
        last_name = data.get("last_name")
        raw_language = str(data.get("language_code") or "").split("-", 1)[0].strip().lower()
        requested_language = raw_language if raw_language in SUPPORTED_LANGUAGES else ""
        language_source = str(data.get("language_source") or "").strip().lower()
        
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
            return _json_no_store({"success": False, "message": "No user_id available"})
        
        # Reject blocked users — do not re-register them in the database
        if await is_user_blocked_cached(
            _catalog_db,
            int(user_id),
            username=username,
            ip_address=get_client_ip(request),
        ):
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
        
        await track_user_debounced(
            _catalog_db,
            user_id=int(user_id),
            username=username,
            first_name=first_name,
            last_name=last_name,
            bot_username=bot_username
        )

        if init_data and language_source in {"manual", "top_switch", "language_screen"} and requested_language in SUPPORTED_LANGUAGES:
            await _catalog_db.set_user_language(int(user_id), requested_language)
            logger.info(
                "[WebApp] Synced user %s language preference to %s from %s",
                user_id,
                requested_language,
                language_source,
            )

        _queue_visitor_event(
            request,
            source="webapp",
            page_type="user_track",
            init_data=init_data,
            is_pageview=False,
        )

        # Record the client IP so it can be blocked alongside the account later
        client_ip = get_client_ip(request)
        if client_ip and client_ip != "unknown":
            try:
                await record_user_ip_debounced(_catalog_db, int(user_id), client_ip)
            except Exception as _ip_err:
                logger.debug("Failed to record user IP in /api/user/track: %s", _ip_err)
        
        logger.info(f"[WebApp] Tracked user {user_id} (username={username}) in bot_users database")
        
        return _json_no_store({
            "success": True,
            "user_id": user_id,
            "tracked": True,
            "language_code": requested_language if requested_language in SUPPORTED_LANGUAGES else None
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error tracking webapp user: {e}")
        return _json_no_store({"success": False, "message": "Failed to track user"})


async def process_webhook_request(request: Request, bot_registry_id: str):
    """Process an incoming Telegram webhook update for the given registry bot ID.

    Shared implementation used by both the /webhook and /webhook/{bot_index} endpoints
    to eliminate duplicated parsing, logging, and error-handling logic.

    Args:
        request: The incoming FastAPI Request object.
        bot_registry_id: bot_registry.bot_id for the target bot.

    Returns:
        A fastapi.Response with an appropriate HTTP status code.
    """
    # --- Unique request ID for log correlation across the processing pipeline ---
    request_id = secrets.token_hex(8)

    # --- Resolve real client IP ---
    # When the server sits behind a reverse proxy or CDN (common with custom domains),
    # request.client.host is the proxy's IP.  Use X-Forwarded-For / X-Real-IP to
    # recover the originating IP so each real caller gets its own rate-limit bucket.
    client_ip = get_client_ip(request)

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
                    "[%s] Rate limit exceeded for non-Telegram IP %s on /webhook bot %s",
                    request_id,
                    client_ip,
                    bot_registry_id,
                )
                return Response(status_code=status.HTTP_429_TOO_MANY_REQUESTS)

    # --- Secret token verification ---
    expected_token: Optional[str] = await _get_expected_secret_token_for_registry_bot(bot_registry_id)

    if expected_token:
        incoming_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        logger.debug(
            "[webhook_secret] validation_start bot_id=%s source_of_truth=bot_registry expected_fp=%s incoming_fp=%s",
            bot_registry_id,
            _secret_fingerprint(expected_token),
            _secret_fingerprint(incoming_token if isinstance(incoming_token, str) else None),
        )
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
            prev_entry: Optional[List[Optional[str]]] = _webhook_secret_history.get(bot_registry_id)
            prev_token: Optional[str] = prev_entry[0] if (prev_entry and len(prev_entry) > 0) else None
            if prev_token and isinstance(incoming_token, str):
                token_valid = secrets.compare_digest(
                    incoming_token.encode(), prev_token.encode()
                )
                if token_valid:
                    logger.debug(
                        "bot=%s accepted request using previous webhook secret (tolerance window)",
                        bot_registry_id,
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
                "[WEBHOOK_401] req=%s bot=%s ip=%s telegram_ip=%s "
                "header_present=%s header_length=%d "
                "reason=%s expected_fp=%s incoming_fp=%s source_of_truth=bot_registry",
                request_id,
                bot_registry_id,
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
            "No secret token configured for registry bot %s; skipping header verification.",
            bot_registry_id,
        )

    if not _bot_applications_by_registry_id:
        logger.error("[%s] Bot applications not initialized", request_id)
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    bot_app = _bot_applications_by_registry_id.get(bot_registry_id)
    if bot_app is None:
        logger.warning("[%s] Registry bot %s is unavailable or disabled", request_id, bot_registry_id)
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    _t0 = _time.monotonic()
    try:
        json_data = await request.json()
        update = Update.de_json(json_data, bot_app.bot)
        update_kind = "other"
        callback_data_for_log = None
        effective_user = update.effective_user
        if effective_user and not is_admin(effective_user.id):
            is_blocked = await is_user_blocked_cached(
                _catalog_db,
                effective_user.id,
                username=effective_user.username,
            )
            if is_blocked:
                logger_security.warning(
                    "[WEBHOOK_BLOCKED] bot=%s user_id=%s username=%s action=ack_drop",
                    bot_registry_id,
                    effective_user.id,
                    effective_user.username,
                )
                return Response(status_code=status.HTTP_200_OK)

        if update.message:
            update_kind = "message"
            logger.info(
                "[%s] Bot %s: Processing message update from user %s",
                request_id, bot_registry_id, update.message.from_user.id,
            )
        elif update.callback_query:
            update_kind = "callback"
            user_id = update.callback_query.from_user.id
            callback_data = update.callback_query.data or ""
            callback_data_for_log = callback_data
            logger.info(
                "[%s] Bot %s: Processing callback query from user %s: %s",
                request_id, bot_registry_id, user_id, callback_data,
            )
            # --- Request deduplication for callback queries ---
            # Prevents accidental double-processing of the same callback when
            # Telegram retries or the user taps a button twice in quick succession.
            deduplicator: Optional[RequestDeduplicator] = getattr(
                request.app.state, "request_deduplicator", None
            )
            if deduplicator is not None and await deduplicator.is_duplicate(
                bot_registry_id,
                user_id,
                callback_data,
                getattr(update.callback_query, "id", None),
            ):
                logger.info(
                    "[%s] Bot %s: Duplicate callback query suppressed (user=%s data=%r)",
                    request_id, bot_registry_id, user_id, callback_data,
                )
                return Response(status_code=status.HTTP_200_OK)
        elif update.channel_post:
            update_kind = "channel_post"
            logger.info(
                "[%s] Bot %s: Processing channel post from chat %s",
                request_id, bot_registry_id, update.channel_post.chat.id,
            )
        else:
            logger.info(
                "[%s] Bot %s: Processing update type: %s",
                request_id, bot_registry_id, update.update_id,
            )

        queue_started = _time.monotonic()
        async with _get_webhook_processing_semaphore(bot_registry_id):
            queue_wait_ms = (_time.monotonic() - queue_started) * 1000
            if queue_wait_ms > 250:
                logger.warning(
                    "[WEBHOOK_QUEUE_WAIT] req=%s bot=%s kind=%s wait_ms=%.1f concurrency=%d callback_data=%r",
                    request_id,
                    bot_registry_id,
                    update_kind,
                    queue_wait_ms,
                    _WEBHOOK_UPDATE_CONCURRENCY,
                    callback_data_for_log,
                )
            await bot_app.process_update(update)

        duration_ms = (_time.monotonic() - _t0) * 1000
        record_webhook_result(bot_index=0, success=True, duration_ms=duration_ms)
        if duration_ms > 1500:
            logger.warning(
                "[WEBHOOK_SLOW] req=%s bot=%s kind=%s duration_ms=%.1f callback_data=%r",
                request_id,
                bot_registry_id,
                update_kind,
                duration_ms,
                callback_data_for_log,
            )
        logger.info("[%s] Bot %s: Webhook request processed successfully", request_id, bot_registry_id)
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        duration_ms = (_time.monotonic() - _t0) * 1000
        record_webhook_result(bot_index=0, success=False, duration_ms=duration_ms)
        logger.error(
            "[%s] Error processing webhook update for bot %s: %s",
            request_id, bot_registry_id, e, exc_info=True,
        )
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@app.post("/webhook")
async def webhook(request: Request):
    """Legacy primary webhook path. Re-registers should use /webhook/{bot_registry_id}."""
    registry_id = await _resolve_registry_bot_from_incoming_secret(request)
    if registry_id:
        logger.warning("Resolved legacy /webhook request by secret owner bot_id=%s", registry_id)
    else:
        registry_id = _runtime_index_to_registry_id.get(0)
    if not registry_id:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    logger.warning("Received legacy /webhook request; migrate Telegram webhook to /webhook/%s", registry_id)
    return await process_webhook_request(request, registry_id)


@app.post("/webhook/{bot_id}")
async def webhook_indexed(request: Request, bot_id: str):
    """Handle incoming webhook updates addressed by bot_registry.bot_id."""
    registry_id = bot_id
    if bot_id.isdigit() and bot_id not in _bot_applications_by_registry_id:
        client_ip = get_client_ip(request)
        incoming_fp = _secret_fingerprint(request.headers.get("X-Telegram-Bot-Api-Secret-Token"))
        from_telegram = is_telegram_ip(client_ip)
        if from_telegram and _is_cached_orphan_legacy_secret(bot_id, incoming_fp):
            if _should_log_orphan_legacy_secret(bot_id, incoming_fp):
                logger_security.warning(
                    "[WEBHOOK_ORPHAN_DRAINED] legacy_bot_index=%s incoming_fp=%s action=ack_drop cache=hit",
                    bot_id,
                    incoming_fp,
                )
            return Response(status_code=status.HTTP_200_OK)

        secret_owner_id = await _resolve_registry_bot_from_incoming_secret(request)
        if secret_owner_id:
            logger.warning(
                "Resolved legacy positional webhook path bot_index=%s by secret owner bot_id=%s",
                bot_id,
                secret_owner_id,
            )
            registry_id = secret_owner_id
        else:
            if from_telegram:
                _remember_orphan_legacy_secret(bot_id, incoming_fp)
                if _should_log_orphan_legacy_secret(bot_id, incoming_fp):
                    logger_security.warning(
                        "[WEBHOOK_ORPHAN_DRAINED] legacy_bot_index=%s incoming_fp=%s action=ack_drop cache=stored",
                        bot_id,
                        incoming_fp,
                    )
                return Response(status_code=status.HTTP_200_OK)
            logger_security.warning(
                "[WEBHOOK_ORPHAN] legacy_bot_index=%s reason=unknown_secret_or_missing_header incoming_fp=%s",
                bot_id,
                incoming_fp,
            )
            return Response(status_code=status.HTTP_410_GONE)
    elif bot_id.isdigit():
        secret_owner_id = await _resolve_registry_bot_from_incoming_secret(request)
        if secret_owner_id and secret_owner_id != _runtime_index_to_registry_id.get(int(bot_id)):
            logger.warning(
                "Legacy positional webhook path bot_index=%s does not match secret owner; routing to bot_id=%s",
                bot_id,
                secret_owner_id,
            )
            registry_id = secret_owner_id
        else:
            registry_id = _runtime_index_to_registry_id.get(int(bot_id), "")
    elif registry_id not in _bot_applications_by_registry_id:
        client_ip = get_client_ip(request)
        if is_telegram_ip(client_ip):
            inactive_row = await _catalog_db.get_bot_registry_by_id_any_status(registry_id)
            if inactive_row and inactive_row.get("status") != "active":
                if _should_log_inactive_webhook_drain(registry_id):
                    logger_security.warning(
                        "[WEBHOOK_INACTIVE_DRAINED] bot_id=%s status=%s action=ack_drop",
                        registry_id,
                        inactive_row.get("status"),
                    )
                return Response(status_code=status.HTTP_200_OK)
    if bot_id.isdigit() and registry_id:
        legacy_index = int(bot_id)
        logger.warning(
            "Received legacy positional webhook path bot_index=%s; migrate Telegram webhook to registry path bot_id=%s",
            bot_id,
            registry_id or "unknown",
        )
    if not registry_id:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    logger.info("Received webhook request for registry bot %s", registry_id)
    return await process_webhook_request(request, registry_id)


async def _build_setup_check() -> Dict[str, Any]:
    """Return a safe, token-free deployment readiness report for reused installs."""
    warnings: List[str] = []
    notes: List[str] = []
    running_bots = get_bot_usernames()
    product_count = 0
    public_product_count = 0
    active_categories = 0
    order_contact = ""

    try:
        product_count = await _catalog_db.count_products()
        public_product_count = await _catalog_db.count_products_excluding_categories(EXCLUDED_FROM_ALL_PRODUCTS)
        category_counts, _, _ = await _catalog_db.get_category_summary(EXCLUDED_FROM_ALL_PRODUCTS)
        active_categories = sum(1 for count in category_counts.values() if count > 0)
        order_contact = await _catalog_db.get_order_contact()
    except Exception as exc:
        warnings.append(f"Database check failed: {exc}")

    webhook_url = (Config.WEBHOOK_URL or "").rstrip("/")
    parsed_webhook = urllib.parse.urlparse(webhook_url)
    if not running_bots:
        warnings.append("No bot applications are running. Check BOT_TOKEN/BOT_TOKENS.")
    if Config.USE_WEBHOOK and not webhook_url:
        warnings.append("USE_WEBHOOK is true but WEBHOOK_URL is missing.")
    if webhook_url and parsed_webhook.scheme != "https":
        warnings.append("WEBHOOK_URL should be an HTTPS public Render URL.")
    if webhook_url and parsed_webhook.path not in {"", "/"}:
        warnings.append("WEBHOOK_URL should be only the origin, not a path. Example: https://your-app.onrender.com")
    if Config.ADMIN_IDS:
        notes.append("Each admin must open the bot and press Start before Telegram accepts admin command setup.")
    else:
        warnings.append("ADMIN_IDS is empty; no Telegram admin account is configured.")
    if not Config.CHANNEL_ID and not Config.CHANNEL_USERNAME:
        warnings.append("No channel is configured, so automatic product ingestion cannot monitor a source channel.")
    if product_count == 0:
        warnings.append("Catalog database has zero products. The WebApp will open, but there is nothing to display yet.")
    if not order_contact:
        warnings.append("Order contact is not set. Use /setcontact from the admin bot.")
    if Path("/data").exists() and not str(Config.DB_PATH).startswith("/data/"):
        warnings.append("Render persistent disk exists, but DB_PATH is not under /data; data may reset on deploy.")

    return {
        "status": "ok" if running_bots and Config.WEB_APP_URL and not warnings else "check_warnings",
        "bots": {
            "configured_count": len(Config.BOT_TOKENS),
            "running_count": len(running_bots),
            "running_usernames": running_bots,
        },
        "admin": {
            "configured_count": len(Config.ADMIN_IDS),
            "must_start_bot_first": True,
        },
        "catalog": {
            "total_products": product_count,
            "public_products": public_product_count,
            "active_categories": active_categories,
            "order_contact_configured": bool(order_contact),
        },
        "urls": {
            "webhook_url": webhook_url or None,
            "web_app_url": Config.WEB_APP_URL,
            "storefront_domain": _STOREFRONT_DOMAIN or Config.STOREFRONT_DOMAIN,
        },
        "render": {
            "persistent_disk_available": Path("/data").exists(),
            "database_on_persistent_disk": str(Config.DB_PATH).startswith("/data/"),
        },
        "warnings": warnings,
        "notes": notes,
    }


async def _log_setup_check() -> None:
    try:
        report = await _build_setup_check()
    except Exception as exc:
        logger.warning("[setup_check] failed: %s", exc, exc_info=True)
        return
    logger.info(
        "[setup_check] bots=%s/%s products=%s public_products=%s active_categories=%s warnings=%s notes=%s",
        report["bots"]["running_count"],
        report["bots"]["configured_count"],
        report["catalog"]["total_products"],
        report["catalog"]["public_products"],
        report["catalog"]["active_categories"],
        len(report["warnings"]),
        len(report["notes"]),
    )
    for warning in report["warnings"]:
        logger.warning("[setup_check] %s", warning)
    for note in report["notes"]:
        logger.info("[setup_check] %s", note)


@app.get("/setup-check")
async def setup_check():
    """Token-free deployment readiness report for first-time Render installs."""
    return await _build_setup_check()


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
