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
    current_lang_text = get_translated_string(
        "current_language", 
        current_lang,
        language=LANGUAGE_DISPLAY[current_lang]
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
    confirmation = get_translated_string(
        "language_changed",
        lang_code,
        language=LANGUAGE_DISPLAY[lang_code]
    )
    
    await query.answer(confirmation, show_alert=True)
    
    # Update the keyboard to reflect the new selection
    keyboard_buttons = []
    for code, display_name in LANGUAGE_DISPLAY.items():
        if code == lang_code:
            display_name = f"✓ {display_name}"
        keyboard_buttons.append([
            InlineKeyboardButton(display_name, callback_data=f"setlang|{code}")
        ])
    
    keyboard = InlineKeyboardMarkup(keyboard_buttons)
    
    # Update message
    message_text = get_translated_string("language_settings", lang_code)
    current_lang_text = get_translated_string(
        "current_language",
        lang_code,
        language=LANGUAGE_DISPLAY[lang_code]
    )
    message_text += f"\n\n{current_lang_text}"
    
    await query.edit_message_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    
    logger.info(f"User {user_id} changed language to {lang_code}")
