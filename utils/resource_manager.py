"""Bounded local resource management for Render-sized deployments."""
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional


logger = logging.getLogger(__name__)

MB = 1024 * 1024

MEMORY_CACHE_MAX_BYTES = 64 * MB
DEFAULT_TTL_SECONDS = 10 * 60
DISK_CACHE_MAX_BYTES = int(os.getenv("DISK_CACHE_MAX_MB", "700")) * MB
# Keep the persistent cache warm for much longer than temp cache files.
DISK_CACHE_TTL_SECONDS = int(os.getenv("DISK_CACHE_TTL_SECONDS", str(12 * 3600)))
TMP_WARNING_BYTES = 1200 * MB
TMP_EMERGENCY_BYTES = 1500 * MB
PERSISTENT_WARNING_BYTES = 700 * MB
PERSISTENT_EMERGENCY_BYTES = 850 * MB
EMERGENCY_CLEANUP_COOLDOWN_SECONDS = int(os.getenv("EMERGENCY_CLEANUP_COOLDOWN_SECONDS", "120"))
TMP_MAX_SIZE_GB = float(os.getenv("TMP_MAX_SIZE_GB", "1.2"))
TMP_FILE_MAX_AGE_HOURS = float(os.getenv("TMP_FILE_MAX_AGE_HOURS", "1"))
TMP_MAX_SIZE_BYTES = int(TMP_MAX_SIZE_GB * 1024 * MB)
TMP_FILE_MAX_AGE_SECONDS = int(TMP_FILE_MAX_AGE_HOURS * 3600)

APP_TMP_DIR = Path(os.getenv("APP_TMP_DIR", Path(tempfile.gettempdir()) / "brady_app_tmp"))
_DEFAULT_APP_CACHE_DIR = Path("/data/brady_app_cache") if Path("/data").exists() else Path(tempfile.gettempdir()) / "brady_app_cache"
APP_CACHE_DIR = Path(os.getenv("APP_CACHE_DIR", _DEFAULT_APP_CACHE_DIR))
BOT_TMP_ROOT = Path(os.getenv("BOT_TMP_ROOT", Path(tempfile.gettempdir()) / "bradycadia"))
_last_emergency_cleanup = 0.0


def ensure_resource_dirs() -> None:
    APP_TMP_DIR.mkdir(parents=True, exist_ok=True)
    APP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    BOT_TMP_ROOT.mkdir(parents=True, exist_ok=True)


def get_bot_temp_dir(slot_id: int) -> Path:
    return BOT_TMP_ROOT / f"bot_{slot_id}"


def clear_bot_temp_dir(slot_id: int) -> tuple[int, int]:
    path = get_bot_temp_dir(slot_id)
    removed = 0
    freed = 0
    if not path.exists():
        return removed, freed
    for item in path.rglob("*"):
        try:
            if item.is_file():
                size = item.stat().st_size
                item.unlink()
                removed += 1
                freed += size
        except (OSError, FileNotFoundError):
            continue
    for item in sorted(path.rglob("*"), reverse=True):
        try:
            if item.is_dir():
                item.rmdir()
        except OSError:
            continue
    return removed, freed


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
        "used_bytes": _dir_size(APP_TMP_DIR) + _dir_size(BOT_TMP_ROOT),
        "filesystem_used_bytes": usage.used,
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "app_tmp_bytes": _dir_size(APP_TMP_DIR),
        "bot_tmp_bytes": _dir_size(BOT_TMP_ROOT),
    }


def get_cache_usage() -> dict:
    usage_root = APP_CACHE_DIR if APP_CACHE_DIR.exists() else Path.cwd()
    usage = shutil.disk_usage(usage_root)
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


def _cache_entry_age_seconds(path: Path) -> float:
    """Return file age using epoch time so TTL checks are stable."""
    try:
        return time.time() - path.stat().st_mtime
    except (OSError, FileNotFoundError):
        return float("inf")


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
    cache_removed, cache_freed = cleanup_owned_dir(
        APP_CACHE_DIR,
        ttl_seconds=DISK_CACHE_TTL_SECONDS,
        max_bytes=DISK_CACHE_MAX_BYTES,
    )
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
    started = time.monotonic()
    tmp_removed, tmp_freed = cleanup_owned_dir(APP_TMP_DIR, TMP_FILE_MAX_AGE_SECONDS, max_bytes=TMP_MAX_SIZE_BYTES)
    cache_removed, cache_freed = cleanup_owned_dir(APP_CACHE_DIR, DISK_CACHE_TTL_SECONDS, max_bytes=DISK_CACHE_MAX_BYTES)
    tmp = get_tmp_usage()
    cache = get_cache_usage()
    logger.info(
        "[resource] startup_cleanup tmp_used=%d app_tmp=%d cache_used=%d app_cache=%d removed=%d freed=%d duration_ms=%.1f",
        tmp["used_bytes"],
        tmp["app_tmp_bytes"],
        cache["used_bytes"],
        cache["app_cache_bytes"],
        tmp_removed + cache_removed,
        tmp_freed + cache_freed,
        (time.monotonic() - started) * 1000,
    )
    if tmp["used_bytes"] >= TMP_EMERGENCY_BYTES or cache["used_bytes"] >= PERSISTENT_EMERGENCY_BYTES:
        emergency_cleanup()
    elif tmp["used_bytes"] >= TMP_WARNING_BYTES or cache["used_bytes"] >= PERSISTENT_WARNING_BYTES:
        logger.warning("[resource] disk_usage_warning tmp_used=%d persistent_used=%d", tmp["used_bytes"], cache["used_bytes"])
