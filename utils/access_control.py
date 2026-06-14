"""Fast access-control helpers for hot bot and WebApp paths."""
import asyncio
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

_BLOCK_CACHE_TTL_SECONDS = float(os.getenv("ACCESS_BLOCK_CACHE_TTL_SECONDS", "8"))
_TRACK_DEBOUNCE_SECONDS = float(os.getenv("USER_TRACK_DEBOUNCE_SECONDS", "60"))
_IP_RECORD_DEBOUNCE_SECONDS = float(os.getenv("USER_IP_RECORD_DEBOUNCE_SECONDS", "300"))

_block_cache: dict[tuple[int, str, str, str], tuple[bool, float]] = {}
_block_cache_lock = asyncio.Lock()

_track_last_seen: dict[tuple[int, str], float] = {}
_track_lock = asyncio.Lock()

_ip_last_seen: dict[tuple[int, str], float] = {}
_ip_lock = asyncio.Lock()


def _normalize_username(username: Optional[str]) -> str:
    return (username or "").strip().lower().lstrip("@")


def _normalize_ip(ip_address: Optional[str]) -> str:
    value = (ip_address or "").strip()
    return "" if value == "unknown" else value


def invalidate_block_cache(
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Invalidate cached block decisions after admin block/unblock changes."""
    username_key = _normalize_username(username)
    ip_key = _normalize_ip(ip_address)

    if user_id is None and not username_key and not ip_key:
        _block_cache.clear()
        return

    for key in list(_block_cache):
        key_user_id, key_username, key_ip, _fingerprint = key
        if user_id is not None and key_user_id != int(user_id):
            continue
        if username_key and key_username != username_key:
            continue
        if ip_key and key_ip != ip_key:
            continue
        _block_cache.pop(key, None)


async def is_user_blocked_cached(
    db,
    user_id: int,
    username: Optional[str] = None,
    ip_address: Optional[str] = None,
    device_fingerprint: Optional[str] = None,
) -> bool:
    """Return a short-lived cached block decision using one SQLite connection."""
    uid = int(user_id)
    key = (
        uid,
        _normalize_username(username),
        _normalize_ip(ip_address),
        (device_fingerprint or "").strip(),
    )
    now = time.monotonic()

    async with _block_cache_lock:
        cached = _block_cache.get(key)
        if cached and now < cached[1]:
            return cached[0]

    clauses = ["telegram_user_id = ?"]
    params: list[object] = [uid]
    if key[1]:
        clauses.append("LOWER(username) = LOWER(?)")
        params.append(key[1])
    if key[2]:
        clauses.append("ip_address = ?")
        params.append(key[2])
    if key[3]:
        clauses.append("device_fingerprint = ?")
        params.append(key[3])

    blocked = False
    async with db.get_connection() as conn:
        async with conn.execute(
            f"SELECT 1 FROM blocked_entities WHERE is_permanent = 1 AND ({' OR '.join(clauses)}) LIMIT 1",
            params,
        ) as cursor:
            blocked = await cursor.fetchone() is not None

        if not blocked:
            async with conn.execute(
                "SELECT is_blocked FROM bot_users WHERE user_id = ?",
                (uid,),
            ) as cursor:
                row = await cursor.fetchone()
                blocked = bool(row and row[0] == 1)

    async with _block_cache_lock:
        _block_cache[key] = (blocked, now + _BLOCK_CACHE_TTL_SECONDS)
        if len(_block_cache) > 4096:
            expired = [cache_key for cache_key, (_value, expires_at) in _block_cache.items() if expires_at <= now]
            for cache_key in expired[:512]:
                _block_cache.pop(cache_key, None)
            while len(_block_cache) > 4096:
                _block_cache.pop(next(iter(_block_cache)))

    return blocked


async def track_user_debounced(
    db,
    *,
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    bot_username: Optional[str] = None,
    force: bool = False,
) -> bool:
    """Avoid writing the same user's activity row on every rapid interaction."""
    key = (int(user_id), (bot_username or "").strip().lower())
    now = time.monotonic()
    async with _track_lock:
        last_seen = _track_last_seen.get(key)
        if not force and last_seen is not None and now - last_seen < _TRACK_DEBOUNCE_SECONDS:
            return False
        _track_last_seen[key] = now

    try:
        await db.track_user(
            user_id=user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            bot_username=bot_username,
        )
        return True
    except Exception:
        async with _track_lock:
            _track_last_seen.pop(key, None)
        raise


async def record_user_ip_debounced(db, user_id: int, ip_address: str) -> bool:
    """Throttle repetitive user/IP refresh writes from WebApp API calls."""
    ip = _normalize_ip(ip_address)
    if not ip:
        return False
    key = (int(user_id), ip)
    now = time.monotonic()
    async with _ip_lock:
        last_seen = _ip_last_seen.get(key)
        if last_seen is not None and now - last_seen < _IP_RECORD_DEBOUNCE_SECONDS:
            return False
        _ip_last_seen[key] = now

    try:
        await db.record_user_ip(int(user_id), ip)
        return True
    except Exception:
        async with _ip_lock:
            _ip_last_seen.pop(key, None)
        raise
