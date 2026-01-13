# handlers/__init__.py
"""
Handlers package initializer.

This module intentionally avoids performing heavy work at import time (such as
running DB migrations or importing all handler submodules). Instead it exposes
an async initializer that the application startup code should call once the
event loop is running.

Usage (recommended):
    # in your startup routine (async)
    import handlers
    await handlers.initialize_handlers()

Why:
- Import-time side effects (running asyncio.run or touching the event loop)
  cause problems under ASGI servers (uvicorn/uvloop). Deferring initialization
  to an explicit async function keeps startup deterministic and testable.
- Importing handler submodules is done inside initialize_handlers so that any
  DB initialization can run first and handlers can rely on the DB being ready.
"""

from __future__ import annotations

import importlib
import logging
from typing import Iterable, List, Optional

from database import init_db, db

logger = logging.getLogger(__name__)

# List of handler module names (relative to this package) to import at startup.
# Keep this list in sync with the actual modules present in the handlers package.
_HANDLER_MODULES: List[str] = [
    "admin",
    "product_view",
    "search",
]


# Internal flag to ensure initialize_handlers is idempotent.
_initialized: bool = False


async def initialize_handlers(
    import_modules: Optional[Iterable[str]] = None,
    run_db_init: bool = True,
    run_cleanup: bool = False,
    cleanup_older_than_days: int = 30,
) -> None:
    """
    Initialize the handlers package.

    This function will:
      - Optionally run database initialization (init_db).
      - Optionally run a cleanup of processed media-group dedupe entries.
      - Import handler submodules listed in _HANDLER_MODULES (or the provided iterable).

    Call this once during your application's async startup (for example, from
    your ASGI startup event or from main.startup_tasks).

    Args:
      import_modules: Optional iterable of module basenames to import instead of the default list.
      run_db_init: If True (default), call database.init_db() before importing handlers.
                   Set to False if DB initialization is handled elsewhere.
      run_cleanup: If True, call db.cleanup_processed_media_groups(cleanup_older_than_days)
                   after DB init to keep the processed_media_groups table compact.
      cleanup_older_than_days: Age threshold in days for cleanup_processed_media_groups.
    """
    global _initialized
    if _initialized:
        logger.debug("handlers.initialize_handlers called but handlers already initialized; skipping")
        return

    modules_to_import = list(import_modules) if import_modules is not None else list(_HANDLER_MODULES)

    # Run DB initialization first if requested
    if run_db_init:
        try:
            logger.info("Running handlers package DB initialization (init_db)")
            await init_db()
            logger.info("Database initialization complete")
        except Exception:
            logger.exception("Database init_db() failed during handlers initialization; continuing to import handlers")

    # Optional cleanup of processed media-group dedupe table to avoid unbounded growth
    if run_cleanup:
        try:
            logger.info("Running processed_media_groups cleanup (older_than_days=%s)", cleanup_older_than_days)
            # Ensure DB connection is available; init_db() above should have connected
            await db.cleanup_processed_media_groups(older_than_days=cleanup_older_than_days)
            logger.info("Processed media-groups cleanup complete")
        except Exception:
            logger.exception("Processed media-groups cleanup failed (non-fatal)")

    # Import handler modules so their registration code runs.
    # Use importlib.import_module to avoid relative import pitfalls at import time.
    for mod in modules_to_import:
        try:
            importlib.import_module(f"{__package__}.{mod}")
            logger.info("Imported handlers.%s", mod)
        except ModuleNotFoundError:
            # Module is optional; log at debug level so missing optional modules don't alarm
            logger.debug("Optional handler module handlers.%s not found; skipping", mod)
        except Exception:
            logger.exception("Failed to import handler module handlers.%s", mod)

    _initialized = True
    logger.info("Handlers package initialization complete")


def import_handler_modules_sync(import_modules: Optional[Iterable[str]] = None) -> None:
    """
    Synchronous helper to import handler modules. This is provided for scripts or
    contexts where an event loop is not available and you only need to import
    modules (it does NOT run init_db).

    Note: prefer initialize_handlers() in async startup code.
    """
    modules_to_import = list(import_modules) if import_modules is not None else list(_HANDLER_MODULES)
    for mod in modules_to_import:
        try:
            importlib.import_module(f"{__package__}.{mod}")
            logger.info("Synchronously imported handlers.%s", mod)
        except ModuleNotFoundError:
            logger.debug("Optional handler module handlers.%s not found; skipping", mod)
        except Exception:
            logger.exception("Failed to synchronously import handler module handlers.%s", mod)


# Backwards compatibility: do not import submodules at package import time.
# If other code expects handlers.* submodules to be available immediately,
# call import_handler_modules_sync() from that code path (synchronously).
#
# Example (ASGI startup):
#   import handlers
#   await handlers.initialize_handlers()
#
# Avoid calling asyncio.run() here; ASGI servers already manage the event loop.