"""
Start command handler.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import Database
from translations.translator import get_translated_string_async
from translations.language_config import LANGUAGE_DISPLAY, DEFAULT_LANGUAGE
from utils.helpers import is_admin, get_user_display_name, ADMIN_COMMANDS_FALLBACK, escape_markdown_v1

logger = logging.getLogger(__name__)
db = Database()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    
    # Check if this is a new user (doesn't exist in database)
    is_new_user = not await db.user_exists(user.id)
    
    # Track user
    await db.track_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    # For new users, show language selection menu
    if is_new_user:
        await show_language_selection(update, context)
        return
    
    # For existing users, show normal welcome message
    await show_welcome_message(update, context)


async def show_language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show language selection menu for new users."""
    user = update.effective_user
    
    # Get user's full display name - escaped for markdown
    display_name = get_user_display_name(user, escaped=True)
    
    # Create language selection keyboard
    keyboard_buttons = []
    for lang_code, display_lang in LANGUAGE_DISPLAY.items():
        keyboard_buttons.append([
            InlineKeyboardButton(display_lang, callback_data=f"setlang_start|{lang_code}")
        ])
    
    keyboard = InlineKeyboardMarkup(keyboard_buttons)
    
    # Use English as default for initial message
    welcome_text = f"👋 Welcome, {display_name}!\n\n"
    welcome_text += "🌐 **Select Your Language / Choisissez votre langue / Wählen Sie Ihre Sprache**\n\n"
    welcome_text += "Please select your preferred language to continue:"
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    logger.info(f"New user {user.id} - showing language selection")


async def show_welcome_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show welcome message for existing users."""
    user = update.effective_user
    
    # Get user's full display name - escaped for markdown
    display_name = get_user_display_name(user, escaped=True)
    
    # Get user's language preference
    user_lang = await db.get_user_language(user.id)
    
    # Check if user is subscribed to notifications
    is_subscribed = await db.is_user_subscribed(user.id)
    
    # Get order contact
    order_contact = await db.get_order_contact()
    
    # Get translated welcome message with contact - name and contact are already escaped
    escaped_contact = escape_markdown_v1(order_contact)
    welcome_text = await get_translated_string_async("welcome_with_contact", user_lang, name=display_name, contact=escaped_contact)
    
    # Add admin command info for admins
    if is_admin(user.id):
        admin_info = await get_translated_string_async("admin_commands_info", user_lang)
        if admin_info != "admin_commands_info":  # Only add if translation exists
            welcome_text += f"\n\n{admin_info}"
        else:
            # Use fallback constant
            welcome_text += ADMIN_COMMANDS_FALLBACK
    
    # Build keyboard based on subscription status
    view_catalog_text = await get_translated_string_async("view_catalog", user_lang)
    change_language_text = await get_translated_string_async("change_language", user_lang)
    keyboard_buttons = [
        [InlineKeyboardButton(view_catalog_text, callback_data="categories")],
        [InlineKeyboardButton(change_language_text, callback_data="open_language_settings")]
    ]
    
    # Add resubscribe button only for users who have unsubscribed
    if not is_subscribed:
        resubscribe_text = await get_translated_string_async("resubscribe_notifications", user_lang)
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
    message_text = await get_translated_string_async("notifications_enabled", user_lang)
    
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
    message_text = await get_translated_string_async("notifications_disabled", user_lang)
    
    await update.message.reply_text(
        message_text,
        parse_mode="Markdown"
    )
    logger.info(f"User {user_id} unsubscribed from notifications")

