"""
Start command handler.
"""
import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import Database
from configs.config import Config
from translations.translator import get_translated_string_async
from translations.language_config import LANGUAGE_DISPLAY, DEFAULT_LANGUAGE
from utils.helpers import is_admin, get_user_display_name, ADMIN_COMMANDS_FALLBACK, escape_markdown_v1, get_bot_username, get_user_lang_async
from utils.access_control import is_user_blocked_cached, track_user_debounced

logger = logging.getLogger(__name__)
db = Database()


async def _catalog_keyboard(user_lang: str) -> InlineKeyboardMarkup:
    view_catalog_text = await get_translated_string_async("view_catalog", user_lang)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(view_catalog_text, callback_data="categories")]
    ])


async def _handle_product_deeplink(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    payload: str,
) -> None:
    """Open a product view directly from a /start product_<id> deep link."""
    try:
        product_id = int(payload[len("product_"):])
    except (ValueError, IndexError):
        user_lang = await get_user_lang_async(update.effective_user.id)
        await update.message.reply_text(
            "❌ Invalid product link.",
            reply_markup=await _catalog_keyboard(user_lang),
        )
        return

    product = await db.get_product(product_id)
    if not product:
        user_lang = await get_user_lang_async(update.effective_user.id)
        text = await get_translated_string_async("deeplink_product_not_found", user_lang)
        await update.message.reply_text(
            text,
            reply_markup=await _catalog_keyboard(user_lang),
        )
        return

    # Re-use the existing product view handler
    from handlers.product_view import show_product_media
    await show_product_media(update, context, product_id)


async def _handle_inline_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show a short inline-mode usage guide."""
    bot_username = get_bot_username(context) or "this_bot"
    user_lang = await get_user_lang_async(update.effective_user.id)
    text = await get_translated_string_async(
        "deeplink_inline_help",
        user_lang,
        bot_username=bot_username,
    )
    await update.message.reply_text(
        text,
        reply_markup=await _catalog_keyboard(user_lang),
        parse_mode="Markdown",
    )


async def _reply_if_blocked(update: Update, user_id: int) -> bool:
    """Send a blocked message if user is non-admin and blocked. Returns True if blocked."""
    if is_admin(user_id):
        return False
    if await is_user_blocked_cached(
        db,
        user_id,
        username=getattr(update.effective_user, "username", None),
    ):
        await update.message.reply_text(
            "🚫 You have been blocked from using this bot.\n\n"
            "If you believe this is an error, please contact an administrator."
        )
        return True
    return False


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user

    # Block check — must be before track_user and before showing any UI
    if await _reply_if_blocked(update, user.id):
        return

    # Handle deep-link payloads passed via /start <payload>
    if context.args:
        payload = context.args[0]

        if payload.startswith("product_"):
            await _handle_product_deeplink(update, context, payload)
            return

        if payload in ("inline", "inline_help"):
            await _handle_inline_help(update, context)
            return

        if payload in ("categories", "webapp"):
            # Fall through to normal welcome – the payload is informational only
            pass

    # Check if this is a new user (doesn't exist in database)
    is_new_user = not await db.user_exists(user.id)

    # Track user
    bot_username = get_bot_username(context)
    await track_user_debounced(
        db,
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        bot_username=bot_username,
        force=is_new_user,
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

    # Get user's full display name - escaped for HTML
    display_name = html.escape(get_user_display_name(user, escaped=False))

    # Get user's language preference
    user_lang = await get_user_lang_async(user.id)

    # Check if user is subscribed to notifications
    is_subscribed = await db.is_user_subscribed(user.id)

    # Get order contact
    order_contact = await db.get_order_contact()

    # Get translated welcome message with contact - name and contact are HTML-escaped
    escaped_contact = html.escape(order_contact)
    welcome_text = await get_translated_string_async("welcome_with_contact", user_lang, name=display_name, contact=escaped_contact)

    # Add commands info based on user role
    if is_admin(user.id):
        # For admins, only show admin commands (not user commands)
        admin_info = await get_translated_string_async("admin_commands_info", user_lang)
        if admin_info != "admin_commands_info":  # Only add if translation exists
            welcome_text += f"\n\n{admin_info}"
        else:
            # Use fallback constant
            welcome_text += ADMIN_COMMANDS_FALLBACK
    else:
        # For regular users, show user commands
        user_commands_info = await get_translated_string_async("user_commands_info", user_lang)
        if user_commands_info != "user_commands_info":  # Only add if translation exists
            welcome_text += f"\n\n{user_commands_info}"

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
        parse_mode="HTML"
    )

    logger.info(f"User {user.id} started the bot (language: {user_lang})")


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /subscribe command - enable product notifications."""
    user_id = update.effective_user.id

    # Block check
    if await _reply_if_blocked(update, user_id):
        return

    # Track user
    bot_username = get_bot_username(context)
    await track_user_debounced(
        db,
        user_id=user_id,
        username=update.effective_user.username,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name,
        bot_username=bot_username,
    )

    # Get user's language preference
    user_lang = await get_user_lang_async(user_id)

    # Enable notifications
    await db.set_user_notifications(user_id, True)

    # Get translated message
    message_text = await get_translated_string_async("notifications_enabled", user_lang)

    await update.message.reply_text(
        message_text,
        reply_markup=await _catalog_keyboard(user_lang),
        parse_mode="Markdown"
    )
    logger.info(f"User {user_id} subscribed to notifications")


async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /unsubscribe command - disable product notifications."""
    user_id = update.effective_user.id

    # Block check
    if await _reply_if_blocked(update, user_id):
        return

    # Get user's language preference
    user_lang = await get_user_lang_async(user_id)

    # Disable notifications
    await db.set_user_notifications(user_id, False)

    # Get translated message
    message_text = await get_translated_string_async("notifications_disabled", user_lang)

    await update.message.reply_text(
        message_text,
        reply_markup=await _catalog_keyboard(user_lang),
        parse_mode="Markdown"
    )
    logger.info(f"User {user_id} unsubscribed from notifications")

