"""
Start command handler.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import Database
from translations.translator import get_translated_string

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
    
    # Get user's language preference
    user_lang = await db.get_user_language(user.id)
    
    # Check if user is subscribed to notifications
    is_subscribed = await db.is_user_subscribed(user.id)
    
    # Get translated welcome message
    welcome_text = get_translated_string("welcome", user_lang, name=user.first_name)
    
    # Build keyboard based on subscription status
    view_catalog_text = get_translated_string("view_catalog", user_lang)
    keyboard_buttons = [
        [InlineKeyboardButton(view_catalog_text, callback_data="categories")]
    ]
    
    # Add resubscribe button only for users who have unsubscribed
    if not is_subscribed:
        resubscribe_text = get_translated_string("resubscribe_notifications", user_lang)
        keyboard_buttons.append(
            [InlineKeyboardButton(resubscribe_text, callback_data="toggle_notifications")]
        )
    
    keyboard = InlineKeyboardMarkup(keyboard_buttons)
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    logger.info(f"User {user.id} started the bot (language: {user_lang})")


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
    
    # Get user's language preference
    user_lang = await db.get_user_language(user_id)
    
    # Enable notifications
    await db.set_user_notifications(user_id, True)
    
    # Get translated message
    message_text = get_translated_string("notifications_enabled", user_lang)
    
    await update.message.reply_text(
        message_text,
        parse_mode="Markdown"
    )
    logger.info(f"User {user_id} subscribed to notifications")


async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /unsubscribe command - disable product notifications."""
    user_id = update.effective_user.id
    
    # Get user's language preference
    user_lang = await db.get_user_language(user_id)
    
    # Disable notifications
    await db.set_user_notifications(user_id, False)
    
    # Get translated message
    message_text = get_translated_string("notifications_disabled", user_lang)
    
    await update.message.reply_text(
        message_text,
        parse_mode="Markdown"
    )
    logger.info(f"User {user_id} unsubscribed from notifications")

