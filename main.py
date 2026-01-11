"""
Main entry point for the Telegram bot.
Initializes the application, registers handlers, and performs startup checks.
"""

import logging
import asyncio
from telegram.ext import Application, CommandHandler
from config.config import BOT_TOKEN, DEBUG
from database import init_db
from handlers.product_view import register_handlers as register_product_view
from handlers.admin import register_handlers as register_admin

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

async def startup_checks():
    """
    Perform startup checks: database, config, etc.
    """
    logger.info("Initializing database...")
    await init_db()
    logger.info("Startup checks complete.")

async def main():
    """
    Main async entry point.
    """
    await startup_checks()
    application = Application.builder().token(BOT_TOKEN).build()
    # Register handlers
    register_product_view(application)
    register_admin(application)
    # Add /start command
    application.add_handler(CommandHandler("start", handlers.product_view.start))
    logger.info("Bot started.")
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())