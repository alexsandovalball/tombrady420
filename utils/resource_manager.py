"""Bounded local resource management for Render-sized deployments."""
import logging
import os
import shutil
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Optional


logger = logging.getLogger(__name__)

MB = 1024 * 1024

MEMORY_CACHE_MAX_BYTES = 64 * MB
DEFAULT_TTL_SECONDS = 10 * 60
DISK_CACHE_MAX_BYTES = 250 * MB
DISK_CACHE_TTL_SECONDS = DEFAULT_TTL_SECONDS
TMP_WARNING_BYTES = 1200 * MB
TMP_EMERGENCY_BYTES = 1500 * MB
PERSISTENT_WARNING_BYTES = 700 * MB
PERSISTENT_EMERGENCY_BYTES = 850 * MB
EMERGENCY_CLEANUP_COOLDOWN_SECONDS = int(os.getenv("EMERGENCY_CLEANUP_COOLDOWN_SECONDS", "120"))

APP_TMP_DIR = Path(os.getenv("APP_TMP_DIR", Path(tempfile.gettempdir()) / "brady_app_tmp"))
APP_CACHE_DIR = Path(os.getenv("APP_CACHE_DIR", Path(tempfile.gettempdir()) / "brady_app_cache"))
_last_emergency_cleanup = 0.0


class MemoryLRUCache:
    """Small byte-counted LRU cache with TTL."""

    def __init__(self, max_bytes: int = MEMORY_CACHE_MAX_BYTES, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, tuple[bytes, float]] = OrderedDict()
        self._size = 0

    def get(self, key: str) -> Optional[bytes]:
        item = self._items.get(key)
        if item is None:
            return None
        value, ts = item
        if time.time() - ts > self.ttl_seconds:
            self._delete(key)
            return None
        self._items.move_to_end(key)
        return value

    def set(self, key: str, value: bytes) -> None:
        if len(value) > self.max_bytes:
            return
        self._delete(key)
        self._items[key] = (value, time.time())
        self._size += len(value)
        while self._size > self.max_bytes and self._items:
            old_key, _ = self._items.popitem(last=False)
            logger.info("[resource] memory_cache_evicted key=%s", old_key)
            self._recount()

    def clear(self) -> int:
        count = len(self._items)
        self._items.clear()
        self._size = 0
        return count

    def _delete(self, key: str) -> None:
        item = self._items.pop(key, None)
        if item is not None:
            self._size -= len(item[0])

    def _recount(self) -> None:
        self._size = sum(len(value) for value, _ in self._items.values())


def ensure_resource_dirs() -> None:
    APP_TMP_DIR.mkdir(parents=True, exist_ok=True)
    APP_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except (OSError, FileNotFoundError):
            continue
    return total


def get_tmp_usage() -> dict:
    tmp_root = Path(tempfile.gettempdir())
    usage = shutil.disk_usage(tmp_root)
    return {
        "path": str(tmp_root),
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "app_tmp_bytes": _dir_size(APP_TMP_DIR),
    }


def get_cache_usage() -> dict:
    usage = shutil.disk_usage(Path.cwd())
    return {
        "path": str(APP_CACHE_DIR),
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "app_cache_bytes": _dir_size(APP_CACHE_DIR),
    }


def cleanup_owned_dir(
    path: Path,
    ttl_seconds: int,
    max_bytes: Optional[int] = None,
    *,
    skip_file: Optional[Callable[[Path], bool]] = None,
) -> tuple[int, int]:
    """Delete expired files and trim oldest files in an app-owned directory."""
    ensure_resource_dirs()
    now = time.time()
    removed = 0
    freed = 0
    files: list[tuple[Path, float, int]] = []

    if not path.exists():
        return removed, freed

    for item in path.rglob("*"):
        try:
            if not item.is_file():
                continue
            if skip_file and skip_file(item):
                logger.debug("[resource] cleanup_skipped_in_use path=%s", item.name)
                continue
            stat = item.stat()
            if now - stat.st_mtime > ttl_seconds:
                size = stat.st_size
                item.unlink()
                removed += 1
                freed += size
                logger.info("[resource] temp_deleted path=%s bytes=%d", item.name, size)
            else:
                files.append((item, stat.st_mtime, stat.st_size))
        except (OSError, FileNotFoundError):
            continue

    if max_bytes is not None:
        total = sum(size for _, _, size in files)
        for item, _, size in sorted(files, key=lambda entry: entry[1]):
            if total <= max_bytes:
                break
            try:
                item.unlink()
                total -= size
                removed += 1
                freed += size
                logger.info("[resource] cache_evicted path=%s bytes=%d", item.name, size)
            except (OSError, FileNotFoundError):
                continue

    return removed, freed


def emergency_cleanup(
    extra_cleanup: Optional[Callable[[], None]] = None,
    *,
    high_watermark_bytes: int = TMP_EMERGENCY_BYTES,
    low_watermark_bytes: int = TMP_WARNING_BYTES,
    cooldown_seconds: int = EMERGENCY_CLEANUP_COOLDOWN_SECONDS,
    skip_file: Optional[Callable[[Path], bool]] = None,
) -> dict:
    """Best-effort app-owned cleanup when disk usage approaches platform limits."""
    global _last_emergency_cleanup
    ensure_resource_dirs()
    tmp_before = get_tmp_usage()
    cache_before = get_cache_usage()

    now = time.monotonic()
    if tmp_before["used_bytes"] < high_watermark_bytes and cache_before["used_bytes"] < PERSISTENT_EMERGENCY_BYTES:
        logger.debug(
            "[resource] emergency_cleanup_skipped reason=below_high_watermark tmp_used=%d cache_used=%d",
            tmp_before["used_bytes"],
            cache_before["used_bytes"],
        )
        return {"skipped": True, "reason": "below_high_watermark"}
    if now - _last_emergency_cleanup < cooldown_seconds:
        logger.info(
            "[resource] emergency_cleanup_skipped reason=cooldown tmp_used=%d cache_used=%d",
            tmp_before["used_bytes"],
            cache_before["used_bytes"],
        )
        return {"skipped": True, "reason": "cooldown"}

    _last_emergency_cleanup = now
    tmp_removed, tmp_freed = cleanup_owned_dir(
        APP_TMP_DIR,
        ttl_seconds=DEFAULT_TTL_SECONDS,
        max_bytes=low_watermark_bytes,
        skip_file=skip_file,
    )
    cache_removed, cache_freed = cleanup_owned_dir(APP_CACHE_DIR, ttl_seconds=DISK_CACHE_TTL_SECONDS, max_bytes=DISK_CACHE_MAX_BYTES)
    if extra_cleanup:
        try:
            extra_cleanup()
        except Exception as exc:
            logger.warning("[resource] emergency_extra_cleanup_failed error=%s", exc)
    result = {
        "tmp_used_before": tmp_before["used_bytes"],
        "cache_used_before": cache_before["used_bytes"],
        "tmp_removed": tmp_removed,
        "tmp_freed": tmp_freed,
        "cache_removed": cache_removed,
        "cache_freed": cache_freed,
    }
    logger.warning("[resource] emergency_cleanup result=%s", result)
    return result


def startup_cleanup() -> None:
    """Clean app-owned stale files and log coarse disk usage."""
    ensure_resource_dirs()
    tmp_removed, tmp_freed = cleanup_owned_dir(APP_TMP_DIR, DEFAULT_TTL_SECONDS, max_bytes=TMP_WARNING_BYTES)
    cache_removed, cache_freed = cleanup_owned_dir(APP_CACHE_DIR, DISK_CACHE_TTL_SECONDS, max_bytes=DISK_CACHE_MAX_BYTES)
    tmp = get_tmp_usage()
    cache = get_cache_usage()
    logger.info(
        "[resource] startup_cleanup tmp_used=%d app_tmp=%d cache_used=%d app_cache=%d removed=%d freed=%d",
        tmp["used_bytes"],
        tmp["app_tmp_bytes"],
        cache["used_bytes"],
        cache["app_cache_bytes"],
        tmp_removed + cache_removed,
        tmp_freed + cache_freed,
    )
    if tmp["used_bytes"] >= TMP_EMERGENCY_BYTES or cache["used_bytes"] >= PERSISTENT_EMERGENCY_BYTES:
        emergency_cleanup()
    elif tmp["used_bytes"] >= TMP_WARNING_BYTES or cache["used_bytes"] >= PERSISTENT_WARNING_BYTES:
        logger.warning("[resource] disk_usage_warning tmp_used=%d persistent_used=%d", tmp["used_bytes"], cache["used_bytes"])
