"""
Start command handler.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    
    welcome_text = (
        f"👋 Welcome, {user.first_name}!\n\n"
        "I'm your media product catalog bot. I help you browse and search "
        "products from our catalog.\n\n"
        "Use /menu to view all products or simply type what you're looking for!"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 View Catalog", callback_data="menu|1")]
    ])
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=keyboard
    )
    logger.info(f"User {user.id} started the bot")

