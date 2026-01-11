# main.py
from __future__ import annotations

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

from config import BOT_TOKEN, LOG_LEVEL
from database import db, init_db
from handlers import admin as admin_handlers
from handlers import product_view as product_handlers
from utils.helpers import setup_logging

logger = logging.getLogger(__name__)
setup_logging(LOG_LEVEL)


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

    # Register handlers (wrap sync handlers automatically)
    app.add_handler(CommandHandler("start", ensure_async(product_handlers.cmd_start)))
    app.add_handler(CommandHandler("categories", ensure_async(product_handlers.cmd_categories)))

    # Admin-only commands
    app.add_handler(CommandHandler("admin_stats", ensure_async(admin_handlers.cmd_stats)))
    app.add_handler(CommandHandler("admin_cleanup", ensure_async(admin_handlers.cmd_cleanup)))

    # Callback queries for product browsing and admin actions
    app.add_handler(CallbackQueryHandler(ensure_async(product_handlers.cb_catalog_router), pattern=r"^CAT:"))
    app.add_handler(CallbackQueryHandler(ensure_async(admin_handlers.cb_admin_router), pattern=r"^ADM:"))

    # Message handler for channel ingestion (photos/videos)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, ensure_async(admin_handlers.ingest_message)))

    # Error handler
    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Log with both module logger and application logger for maximum visibility
        logger.exception("Unhandled exception in update: %s", update)
        try:
            context.application.logger.exception("Unhandled exception in update: %s", update)
        except Exception:
            # context or application may be None in some error cases
            logger.exception("Failed to log to context.application.logger")

    app.add_error_handler(on_error)

    return app


async def startup_tasks(app: Application) -> None:
    """
    Tasks to run on startup: init DB and any other warmups.
    Call this before starting the Application (e.g., before run_polling or run_webhook).
    """
    logger.info("Running startup tasks")
    await init_db()
    app.bot_data["db"] = db
    logger.info("Startup tasks complete")


async def shutdown_tasks(app: Application) -> None:
    """
    Tasks to run on shutdown.
    """
    logger.info("Running shutdown tasks")
    # add cleanup logic here if needed
    logger.info("Shutdown tasks complete")


# Helper to run startup tasks from a run script
async def run_startup_tasks(app: Application) -> None:
    try:
        await startup_tasks(app)
    except Exception:
        logger.exception("startup_tasks failed")
        raise