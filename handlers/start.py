"""
Handler for the /start command.
"""
from telegram import Update
from telegram.ext import ContextTypes
import logging

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle the /start command.
    Sends a welcome message to the user.
    """
    try:
        if not update.message:
            logger.warning("start_command called without a message")
            return
        
        await update.message.reply_text(
            "👋 Welcome to the Media Catalog Bot!\n\n"
            "Use /menu to browse the catalog or just type keywords to search."
        )
        logger.info("Sent /start reply to user_id=%s", update.effective_user.id if update.effective_user else "unknown")
    except Exception as e:
        logger.exception("Error in start_command: %s", e)
        # Try to send a simple error message
        try:
            if update.message:
                await update.message.reply_text("❌ An error occurred. Please try again.")
        except Exception:
            pass
