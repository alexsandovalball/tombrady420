"""
Start command handler.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import Database

logger = logging.getLogger(__name__)
db = Database()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    
    # Track user
    await db.track_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    welcome_text = (
        f"👋 Welcome, {user.first_name}!\n\n"
        "I'm your media product catalog bot. I help you browse and search "
        "products from our catalog.\n\n"
        "Use /menu to view all products or simply type what you're looking for!  DM @FLYAWAYPEP TO ORDER"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 View Catalog", callback_data="categories")]
    ])
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=keyboard
    )
    logger.info(f"User {user.id} started the bot")

