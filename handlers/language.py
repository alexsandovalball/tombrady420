"""
Language selection and translation handlers.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import Database
from utils.translation import SUPPORTED_LANGUAGES, translate_text, get_language_name

logger = logging.getLogger(__name__)
db = Database()


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /language command - show language selection menu."""
    try:
        user_id = update.effective_user.id
        current_lang = await db.get_user_language(user_id)
        current_lang_name = get_language_name(current_lang)
        
        # Create language selection keyboard
        keyboard_buttons = []
        for lang_code, lang_name in SUPPORTED_LANGUAGES.items():
            # Add checkmark to current language
            display_text = f"{'✅ ' if lang_code == current_lang else ''}{lang_name}"
            keyboard_buttons.append([
                InlineKeyboardButton(display_text, callback_data=f"setlang|{lang_code}")
            ])
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        text = (
            "🌐 **Language Settings**\n\n"
            f"Current language: {current_lang_name}\n\n"
            "Select your preferred language:\n"
            "All product captions and bot messages will be automatically translated."
        )
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            
    except Exception as e:
        logger.error(f"Error showing language menu: {e}")
        error_text = "❌ An error occurred. Please try again."
        if update.callback_query:
            await update.callback_query.answer(error_text, show_alert=True)
        else:
            await update.message.reply_text(error_text)


async def handle_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, lang_code: str):
    """Handle language selection callback."""
    try:
        user_id = update.effective_user.id
        
        # Save language preference
        await db.set_user_language(user_id, lang_code)
        
        # Show success message in the selected language
        lang_name = get_language_name(lang_code)
        success_msg = f"✅ Language set to {lang_name}!"
        
        # Translate the success message to the selected language
        if lang_code != 'en':
            translated_msg = translate_text(success_msg, lang_code)
            if translated_msg:
                success_msg = translated_msg
        
        await update.callback_query.answer(success_msg, show_alert=True)
        
        # Refresh the language menu to show the updated selection
        await language_command(update, context)
        
    except Exception as e:
        logger.error(f"Error setting language: {e}")
        await update.callback_query.answer(
            "❌ An error occurred while setting language.",
            show_alert=True
        )


async def translate_caption_for_user(caption: str, user_id: int) -> str:
    """
    Translate caption based on user's language preference.
    
    Args:
        caption: Original caption text
        user_id: User ID to get language preference
        
    Returns:
        Translated caption or original if translation fails or language is English
    """
    try:
        user_lang = await db.get_user_language(user_id)
        
        # No translation needed for English
        if user_lang == 'en':
            return caption
        
        translated = translate_text(caption, user_lang)
        
        # If translation fails, return original
        if not translated:
            logger.warning(f"Translation failed for user {user_id}, returning original caption")
            return caption
        
        # Add note that this is a translation
        return f"{translated}\n\n🌐 [Auto-translated from original]"
        
    except Exception as e:
        logger.error(f"Error translating caption for user {user_id}: {e}")
        return caption


async def translate_message_for_user(message: str, user_id: int) -> str:
    """
    Translate bot message based on user's language preference.
    
    Args:
        message: Original message text
        user_id: User ID to get language preference
        
    Returns:
        Translated message or original if translation fails or language is English
    """
    try:
        user_lang = await db.get_user_language(user_id)
        
        # No translation needed for English
        if user_lang == 'en':
            return message
        
        translated = translate_text(message, user_lang)
        
        # If translation fails, return original
        if not translated:
            logger.warning(f"Translation failed for user {user_id}, returning original message")
            return message
        
        return translated
        
    except Exception as e:
        logger.error(f"Error translating message for user {user_id}: {e}")
        return message
