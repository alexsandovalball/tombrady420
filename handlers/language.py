"""
Language settings handler.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import Database
from translations.language_config import LANGUAGE_DISPLAY, is_valid_language, DEFAULT_LANGUAGE
from translations.translator import get_translated_string

logger = logging.getLogger(__name__)
db = Database()


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /language command - show language selection menu."""
    user_id = update.effective_user.id
    
    # Get current language
    current_lang = await db.get_user_language(user_id)
    
    # Create language selection keyboard
    keyboard_buttons = []
    
    for lang_code, display_name in LANGUAGE_DISPLAY.items():
        # Add checkmark for current language
        if lang_code == current_lang:
            display_name = f"✓ {display_name}"
        
        keyboard_buttons.append([
            InlineKeyboardButton(display_name, callback_data=f"setlang|{lang_code}")
        ])
    
    keyboard = InlineKeyboardMarkup(keyboard_buttons)
    
    # Get translated message
    message_text = get_translated_string("language_settings", current_lang)
    current_lang_name = LANGUAGE_DISPLAY.get(current_lang, LANGUAGE_DISPLAY[DEFAULT_LANGUAGE])
    current_lang_text = get_translated_string(
        "current_language", 
        current_lang,
        language=current_lang_name
    )
    
    message_text += f"\n\n{current_lang_text}"
    
    await update.message.reply_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    logger.info(f"User {user_id} opened language settings (current: {current_lang})")


async def handle_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, lang_code: str):
    """Handle language selection callback."""
    user_id = update.effective_user.id
    query = update.callback_query
    
    # Validate language code
    if not is_valid_language(lang_code):
        await query.answer("❌ Invalid language", show_alert=True)
        return
    
    # Update user's language preference
    await db.set_user_language(user_id, lang_code)
    
    # Get confirmation message in the new language
    lang_display_name = LANGUAGE_DISPLAY.get(lang_code, LANGUAGE_DISPLAY[DEFAULT_LANGUAGE])
    confirmation = get_translated_string(
        "language_changed",
        lang_code,
        language=lang_display_name
    )
    
    await query.answer(confirmation, show_alert=True)
    
    # Delete the language selection message
    try:
        await query.delete_message()
    except:
        pass
    
    # Show welcome message in the new language (return to start page)
    user = update.effective_user
    
    # Get user's full display name
    from telegram.helpers import escape_markdown
    if user.first_name and user.last_name:
        raw_display_name = f"{user.first_name} {user.last_name}"
    elif user.first_name:
        raw_display_name = user.first_name
    elif user.username:
        raw_display_name = user.username
    else:
        raw_display_name = "there"
    
    # Escape the display name for markdown
    display_name = escape_markdown(raw_display_name)
    
    is_subscribed = await db.is_user_subscribed(user_id)
    
    # Get order contact
    order_contact = await db.get_order_contact()
    
    # Get translated welcome message with contact
    welcome_text = get_translated_string("welcome_with_contact", lang_code, name=display_name, contact=order_contact)
    
    # Add admin command info for admins (import is_admin)
    from utils.helpers import is_admin
    if is_admin(user_id):
        admin_info = get_translated_string("admin_commands_info", lang_code)
        if admin_info != "admin_commands_info":  # Only add if translation exists
            welcome_text += f"\n\n{admin_info}"
        else:
            # Fallback if translation doesn't exist
            welcome_text += "\n\n👨‍💼 Admin Commands:\n• /users - Manage users\n• /setcontact - Set order contact\n• /nuke - Delete all products\n• /recategorize - Recategorize products\n• /send - Send message to user\n• /broadcast - Broadcast to all users"
    
    view_catalog_text = get_translated_string("view_catalog", lang_code)
    change_language_text = get_translated_string("change_language", lang_code)
    
    keyboard_buttons = [
        [InlineKeyboardButton(view_catalog_text, callback_data="categories")],
        [InlineKeyboardButton(change_language_text, callback_data="open_language_settings")]
    ]
    
    if not is_subscribed:
        resubscribe_text = get_translated_string("resubscribe_notifications", lang_code)
        keyboard_buttons.append(
            [InlineKeyboardButton(resubscribe_text, callback_data="toggle_notifications")]
        )
    
    keyboard = InlineKeyboardMarkup(keyboard_buttons)
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=welcome_text,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    
    logger.info(f"User {user_id} changed language to {lang_code}")
