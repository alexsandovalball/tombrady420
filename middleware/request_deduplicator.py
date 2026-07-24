"""
Request deduplication middleware for Telegram webhook callbacks.

Prevents accidental double-processing of identical callback queries that
arrive within a short time window (e.g., due to Telegram retries or a user
double-tapping a button before the first response is acknowledged).

Deduplication key: callback query id when available, otherwise
``(bot_index, user_id, callback_data)``.
TTL: configurable, defaults to 5 seconds

Usage example::

    from middleware.request_deduplicator import RequestDeduplicator

    deduplicator = RequestDeduplicator()

    # In webhook handler:
    if deduplicator.is_duplicate(bot_index, user_id, callback_data, callback_query_id):
        return Response(status_code=200)  # Silently acknowledge duplicate
    ...

Thread/async safety: the deduplicator uses a plain dict with monotonic
timestamps.  All mutations happen inside a single async lock so it is safe
to use from a single-process asyncio server.

Multi-process note: in deployments with multiple uvicorn workers each process
maintains its own independent deduplication cache.  This means a duplicate
callback served by a different worker than the original will not be suppressed.
For single-worker deployments (the typical Render.com setup) this is not an
issue.
"""

import asyncio
import logging
import os
import time as _time
from collections import OrderedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# How long (seconds) an identical request is considered a duplicate.
_DEDUP_TTL: float = float(os.getenv("REQUEST_DEDUP_TTL", "5"))

# The TTL is only five seconds, so a very large ceiling wastes RAM without
# improving normal duplicate suppression.
try:
    _MAX_ENTRIES: int = int(os.getenv("REQUEST_DEDUP_MAX_ENTRIES", "5000"))
except (TypeError, ValueError):
    _MAX_ENTRIES = 5000
_MAX_ENTRIES = max(500, min(_MAX_ENTRIES, 50_000))


class RequestDeduplicator:
    """In-memory TTL cache that detects duplicate webhook callback queries.

    Two requests are considered duplicates when they share the same
    the same Telegram ``callback_query.id`` and arrive within ``ttl`` seconds
    of each other.  When the callback query id is not available, it falls back
    to ``bot_index``, ``user_id``, and ``callback_data``.

    Navigation-style callbacks that differ only in a page number
    (``callback_data`` contains a different value) are treated as distinct
    requests and are never deduplicated.

    Attributes:
        ttl: Number of seconds within which an identical request is a duplicate.
    """

    def __init__(self, ttl: float = _DEDUP_TTL) -> None:
        self.ttl = ttl
        # key -> monotonic timestamp of first seen
        self._seen: OrderedDict = OrderedDict()
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def is_duplicate(
        self,
        bot_index: int,
        user_id: int,
        callback_data: str,
        callback_query_id: str | None = None,
    ) -> bool:
        """Return True if an identical request was seen within ``self.ttl`` seconds.

        Side-effect: if this is *not* a duplicate, the request is recorded so
        future calls within the TTL window will return True.

        Args:
            bot_index: Index of the bot that received the callback.
            user_id: Telegram user ID who sent the callback.
            callback_data: The ``data`` field of the Telegram CallbackQuery.
            callback_query_id: Telegram's unique CallbackQuery id.  Passing this
                avoids suppressing legitimate repeated navigation taps that
                intentionally reuse the same callback_data.

        Returns:
            True if the request appears to be a duplicate; False otherwise.
        """
        key = ("callback_query", callback_query_id) if callback_query_id else (bot_index, user_id, callback_data)
        now = _time.monotonic()

        async with self._lock:
            self._evict_expired(now)

            if key in self._seen:
                age = now - self._seen[key]
                if age < self.ttl:
                    logger.info(
                        "[dedup] Duplicate callback suppressed: "
                        "bot=%s user_id=%d data=%r age=%.2fs",
                        bot_index,
                        user_id,
                        callback_data,
                        age,
                    )
                    return True
                # Entry exists but has expired – update it
                self._seen.move_to_end(key)
                self._seen[key] = now
                return False

            # New request – record it
            if len(self._seen) >= _MAX_ENTRIES:
                self._seen.popitem(last=False)  # evict oldest
            self._seen[key] = now
            return False

    def clear(self) -> None:
        """Remove all cached entries (primarily useful for testing)."""
        self._seen.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict_expired(self, now: float) -> None:
        """Remove entries whose TTL has elapsed (called under lock)."""
        cutoff = now - self.ttl
        # OrderedDict is insertion-ordered; oldest entries are first.
        while self._seen:
            _, ts = next(iter(self._seen.items()))
            if ts <= cutoff:
                self._seen.popitem(last=False)
            else:
                break
