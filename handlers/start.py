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
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    logger.info(f"User {user.id} started the bot")


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /subscribe command - enable product notifications."""
    user_id = update.effective_user.id
    
    # Track user
    await db.track_user(
        user_id=user_id,
        username=update.effective_user.username,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name
    )
    
    # Enable notifications
    await db.set_user_notifications(user_id, True)
    
    await update.message.reply_text(
        "🔔 **Notifications Enabled!**\n\n"
        "You will now receive notifications when new products are added to the catalog.\n\n"
        "Use /unsubscribe to stop receiving notifications.",
        parse_mode="Markdown"
    )
    logger.info(f"User {user_id} subscribed to notifications")


async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /unsubscribe command - disable product notifications."""
    user_id = update.effective_user.id
    
    # Disable notifications
    await db.set_user_notifications(user_id, False)
    
    await update.message.reply_text(
        "🔕 **Notifications Disabled**\n\n"
        "You will no longer receive notifications about new products.\n\n"
        "Use /subscribe to enable notifications again.",
        parse_mode="Markdown"
    )
    logger.info(f"User {user_id} unsubscribed from notifications")

