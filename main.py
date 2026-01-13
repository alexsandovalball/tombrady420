# main.py
from __future__ import annotations

# Ensure stdout logging is configured early and consistently for Render
import logging as _logging
import sys as _sys

_root_logger = _logging.getLogger()
if not _root_logger.handlers:
    _handler = _logging.StreamHandler(stream=_sys.stdout)
    _formatter = _logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    _handler.setFormatter(_formatter)
    _root_logger.addHandler(_handler)
# Default to INFO unless overridden by config later
_root_logger.setLevel(_logging.INFO)

# Make sure uvicorn loggers propagate to root logger
for _name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    _lg = _logging.getLogger(_name)
    _lg.propagate = True
    if _lg.level == _logging.NOTSET:
        _lg.setLevel(_logging.INFO)

import logging
import asyncio
from typing import Optional, Callable, Awaitable

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN, LOG_LEVEL, REDIS_URL
# DEBUG_LOG_UPDATES is optional in config; default to False if not present
try:
    from config import DEBUG_LOG_UPDATES  # type: ignore
except Exception:
    DEBUG_LOG_UPDATES = False

from database import db, init_db
from handlers import admin as admin_handlers
from handlers import product_view as product_handlers
from handlers import search as search_handlers
from utils.helpers import setup_logging

logger = logging.getLogger(__name__)
setup_logging(LOG_LEVEL)

# Background cleanup task handle
_cleanup_task: Optional[asyncio.Task] = None


def ensure_async(fn: Callable) -> Callable[..., Awaitable]:
    """
    Wrap a synchronous handler into an async function using asyncio.to_thread.
    If fn is already a coroutine function, return it unchanged.
    """
    if asyncio.iscoroutinefunction(fn):
        return fn

    async def _wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            return await asyncio.to_thread(fn, update, context)
        except Exception:
            # Re-raise so the Application error handler can catch and log it
            raise

    return _wrapper


def _register_handlers(app: Application) -> None:
    """
    Register all bot handlers on the provided Application instance.
    Keep handlers compact and defensive; use ensure_async for sync callables.
    """
    # User commands
    app.add_handler(CommandHandler("start", ensure_async(product_handlers.cmd_start)))
    app.add_handler(CommandHandler("categories", ensure_async(product_handlers.cmd_categories)))
    # search command (fuzzy search)
    app.add_handler(CommandHandler("search", ensure_async(search_handlers.handle_search)))

    # Admin-only commands
    app.add_handler(CommandHandler("admin_stats", ensure_async(admin_handlers.cmd_stats)))
    app.add_handler(CommandHandler("admin_cleanup", ensure_async(admin_handlers.cmd_cleanup)))
    app.add_handler(CommandHandler("list_products", ensure_async(admin_handlers.list_products_command)))
    app.add_handler(CommandHandler("setcategory", ensure_async(admin_handlers.set_category_command)))
    app.add_handler(CommandHandler("listcategories", ensure_async(admin_handlers.list_categories_command)))
    app.add_handler(CommandHandler("category_counts", ensure_async(admin_handlers.category_counts_command)))
    app.add_handler(CommandHandler("backfill_db", ensure_async(admin_handlers.backfill_db_command)))
    app.add_handler(CommandHandler("nuke", ensure_async(admin_handlers.nuke_command)))

    # Callback queries for product browsing, search pagination and admin actions
    app.add_handler(CallbackQueryHandler(ensure_async(product_handlers.cb_catalog_router), pattern=r"^CAT:"))
    app.add_handler(CallbackQueryHandler(ensure_async(search_handlers.cb_search_router), pattern=r"^search_page\|"))
    app.add_handler(CallbackQueryHandler(ensure_async(admin_handlers.cb_admin_router), pattern=r"^ADM:"))
    # Nuke confirmation router (separate)
    app.add_handler(CallbackQueryHandler(ensure_async(admin_handlers.cb_nuke_router), pattern=r"^ADM:NUKE"))

    # Message handler for ingestion: accept channel posts and non-command messages
    # filters.ALL covers channel_post and message; exclude bot commands to avoid double-handling
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, ensure_async(admin_handlers.ingest_message)))

    # Optional debug handler to log every update (controlled by config.DEBUG_LOG_UPDATES)
    if DEBUG_LOG_UPDATES:
        async def _debug_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                logger.debug("DEBUG UPDATE: %s", update.to_dict())
            except Exception:
                logger.exception("Failed to serialize debug update")

        app.add_handler(MessageHandler(filters.ALL, _debug_all), group=0)


def build_application(token: Optional[str] = None) -> Application:
    """
    Build and return a configured Application instance.
    This function does not start the bot; the caller should run startup_tasks(app)
    and then start the application (run_polling or run_webhook) from the run script.
    """
    token = token or BOT_TOKEN
    if not token:
        raise RuntimeError("BOT_TOKEN is required in environment")

    app = Application.builder().token(token).build()

    # Register handlers
    _register_handlers(app)

    # Error handler
    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Log with both module logger and application logger for maximum visibility
        try:
            logger.exception("Unhandled exception in update: %s", update)
        except Exception:
            pass
        try:
            # context may be None in some error cases
            if context and getattr(context, "application", None):
                context.application.logger.exception("Unhandled exception in update: %s", update)
        except Exception:
            logger.exception("Failed to log to context.application.logger")

    app.add_error_handler(on_error)

    return app


async def startup_tasks(app: Application) -> None:
    """
    Tasks to run on startup: init DB and any other warmups.
    Call this before starting the Application (e.g., before run_polling or run_webhook).
    """
    global _cleanup_task
    logger.info("Running startup tasks")
    # Initialize DB (idempotent)
    await init_db()
    # Expose DB instance to handlers via bot_data for convenience
    app.bot_data["db"] = db

    # Initialize Redis client if configured and attach to app.bot_data["redis"]
    if REDIS_URL:
        try:
            import redis.asyncio as aioredis  # type: ignore
            redis_client = aioredis.from_url(REDIS_URL)
            # test connection
            try:
                await redis_client.ping()
                app.bot_data["redis"] = redis_client
                logger.info("Connected to Redis at startup for callback token store")
            except Exception:
                # If ping fails, still attach client but log the issue
                app.bot_data["redis"] = redis_client
                logger.exception("Connected to Redis but ping failed; continuing with client attached")
        except Exception:
            logger.exception("Failed to initialize Redis client; continuing without Redis (in-memory fallback will be used)")

    # Import and initialize handlers now that DB is ready (idempotent)
    try:
        import handlers  # local import to avoid circulars at module import time
        # run_db_init is False because init_db() already ran above; enable cleanup here
        await handlers.initialize_handlers(run_db_init=False, run_cleanup=True, cleanup_older_than_days=30)
        logger.info("Handlers initialized during startup tasks")
    except Exception:
        logger.exception("Failed to initialize handlers during startup tasks")

    # Start a periodic cleanup task to keep processed_media_groups compact (non-fatal)
    try:
        async def _periodic_cleanup():
            while True:
                try:
                    await db.cleanup_processed_media_groups(older_than_days=30)
                    logger.info("Periodic processed_media_groups cleanup completed")
                except Exception:
                    logger.exception("Periodic cleanup failed")
                await asyncio.sleep(24 * 3600)  # 24 hours

        _cleanup_task = asyncio.create_task(_periodic_cleanup())
        logger.info("Started periodic processed_media_groups cleanup task")
    except Exception:
        logger.exception("Failed to start periodic cleanup task (non-fatal)")

    # If DEBUG_LOG_UPDATES is enabled, log that debug mode is active
    if DEBUG_LOG_UPDATES:
        logger.info("DEBUG_LOG_UPDATES is enabled; all updates will be logged at DEBUG level")

    logger.info("Startup tasks complete")


async def shutdown_tasks(app: Application) -> None:
    """
    Tasks to run on shutdown.
    """
    global _cleanup_task
    logger.info("Running shutdown tasks")
    # Cancel periodic cleanup task if running
    try:
        if _cleanup_task:
            _cleanup_task.cancel()
            try:
                await _cleanup_task
            except Exception:
                pass
            _cleanup_task = None
            logger.info("Periodic cleanup task cancelled")
    except Exception:
        logger.exception("Failed to cancel periodic cleanup task")

    # Close Redis client if present
    try:
        redis_client = app.bot_data.get("redis")
        if redis_client is not None:
            try:
                # redis.asyncio client exposes close() and connection_pool.disconnect()
                await redis_client.close()
                try:
                    # best-effort disconnect of pool
                    pool = getattr(redis_client, "connection_pool", None)
                    if pool is not None and hasattr(pool, "disconnect"):
                        await pool.disconnect()
                except Exception:
                    pass
                logger.info("Redis client closed during shutdown")
            except Exception:
                logger.exception("Error while closing Redis client during shutdown")
    except Exception:
        logger.exception("Unexpected error while attempting to close Redis client")

    # Place any cleanup logic here (close connections, flush caches)
    try:
        # If the DB exposes a close method, attempt to close it gracefully
        if hasattr(db, "close"):
            await db.close()
            logger.info("Database connection closed during shutdown")
    except Exception:
        logger.exception("Error while closing database during shutdown")

    logger.info("Shutdown tasks complete")


# Helper to run startup tasks from a run script
async def run_startup_tasks(app: Application) -> None:
    try:
        await startup_tasks(app)
    except Exception:
        logger.exception("startup_tasks failed")
        raise