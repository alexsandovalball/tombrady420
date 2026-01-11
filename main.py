# main.py
"""
Application builder for the Telegram media catalog bot.
Creates and configures the python-telegram-bot Application instance,
registers handlers, and exposes a build_application() function.
"""

from __future__ import annotations

import logging
import asyncio
from typing import Optional

from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler

from config import BOT_TOKEN, ADMIN_IDS, LOG_LEVEL, USE_WEBHOOK
from database import db, init_db
from handlers import admin as admin_handlers
from handlers import product_view as product_handlers
from utils.helpers import setup_logging

logger = logging.getLogger(__name__)
setup_logging(LOG_LEVEL)


def build_application(token: Optional[str] = None) -> Application:
    """
    Build and return a configured Application instance.
    This function does not start the bot; the caller should call application.initialize/start/run.
    """
    token = token or BOT_TOKEN
    if not token:
        raise RuntimeError("BOT_TOKEN is required in environment")

    app = Application.builder().token(token).concurrent_updates(True).build()

    # Register handlers
    # Admin commands (category assignment, preview actions)
    app.add_handler(CommandHandler("start", product_handlers.cmd_start))
    app.add_handler(CommandHandler("categories", product_handlers.cmd_categories))

    # Admin-only commands
    app.add_handler(CommandHandler("admin_stats", admin_handlers.cmd_stats))
    app.add_handler(CommandHandler("admin_cleanup", admin_handlers.cmd_cleanup))

    # Callback queries for product browsing and admin actions
    app.add_handler(CallbackQueryHandler(product_handlers.cb_catalog_router, pattern=r"^CAT:"))
    app.add_handler(CallbackQueryHandler(admin_handlers.cb_admin_router, pattern=r"^ADM:"))

    # Message handler for channel ingestion (photos/videos)
    # We accept messages from channels or admins; ingestion logic checks chat_id and message.media_group_id
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, admin_handlers.ingest_message))

    # Error handler
    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.exception("Unhandled exception in update: %s", update)

    app.add_error_handler(on_error)

    return app


async def startup_tasks(app: Application) -> None:
    """Tasks to run on startup: init DB and any other warmups."""
    logger.info("Running startup tasks")
    await init_db()
    # attach db instance to app for convenience
    app.bot_data["db"] = db
    logger.info("Startup tasks complete")


async def shutdown_tasks(app: Application) -> None:
    """Tasks to run on shutdown."""
    logger.info("Running shutdown tasks")
    # nothing special for now
    logger.info("Shutdown tasks complete")