"""
Best-effort cache invalidation helpers.

Webhook/API caches live in webhook_server.py. Bot handlers can run in polling
mode too, so this module only clears those caches when webhook_server is already
loaded in the current process.
"""
import logging
import sys


logger = logging.getLogger(__name__)


def invalidate_webapp_caches(reason: str = "") -> int:
    """Clear webhook/API in-memory caches if they are available."""
    webhook_server = sys.modules.get("webhook_server")
    clear_func = getattr(webhook_server, "clear_in_memory_caches", None)
    if not callable(clear_func):
        logger.debug("WebApp cache invalidation skipped; webhook_server is not loaded")
        return 0
    cleared = clear_func()
    logger.info("Invalidated %d WebApp cache entries%s", cleared, f" ({reason})" if reason else "")
    return cleared
