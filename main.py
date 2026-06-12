"""
Main entry point for the Telegram media catalog bot.
"""
import logging
import asyncio
import json
import re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat, MenuButtonWebApp, WebAppInfo
from telegram.ext import (
    Application,
    ContextTypes
)
from telegram.error import BadRequest, TelegramError

from configs.config import Config, ConfigError
from database import Database
import emojis
from handlers.start import start_command, subscribe_command, unsubscribe_command
from handlers.menu import menu_command, show_catalog_page, handle_catalog_pagination
from handlers.search import handle_search, show_search_results, handle_search_pagination
from handlers.product_view import show_product_media, handle_product_callback
from handlers.language import language_command, handle_language_callback
from handlers.admin import (
    delete_product, nuke_command, recategorize_command,
    users_command, show_users_page, build_users_bot_selection_menu,
    block_command, unblock_command, send_command, broadcast_command,
    setcontact_command, handle_setcontact_input, setlink_command, handle_setlink_input,
    clearcache_command, botusers_command, prunebots_command
)
from handlers.inline import handle_inline_query, handle_chosen_inline_result
from handlers.callback_router import route_callback
from translations.language_config import is_valid_language, LANGUAGE_DISPLAY
from translations.translator import get_translated_string_async, translation_service
from utils.helpers import (
    get_file_id_and_type,
    has_media,
    get_channel_id,
    get_channel_username,
    get_admin_ids,
    is_admin,
    get_user_display_name,
    escape_markdown_v1,
    register_bot_handlers
)
from utils.categories import get_all_categories, get_category_display_name, get_subcategories, get_subcategory_display_name, CATEGORIES
try:
    from utils.cache_invalidation import invalidate_webapp_caches
except Exception as exc:
    logging.getLogger(__name__).warning("Cache invalidation unavailable; using no-op fallback: %s", exc)

    def invalidate_webapp_caches(reason: str = "") -> int:
        return 0
from utils.notification_manager import notification_service
from utils.safe_send import safe_send_message, safe_edit_message_text
from utils.primary_instance import is_primary_instance

# Configure logging
from configs.logging_config import configure_logging
configure_logging()
logger = logging.getLogger(__name__)

# Initialize database
db = Database()

# Rate limiting (simple in-memory store)
user_last_message = {}

# Media group collection (temporary storage for grouping messages)
media_group_messages = {}
media_group_timers = {}
media_group_locks = {}  # Locks to prevent race conditions in media group processing

# Callback query configuration
LONG_RUNNING_CALLBACKS = ["broadcast_confirm_all"]  # Callbacks that should not answer query upfront to avoid timeouts


# _is_primary_instance is kept as a thin alias so existing call-sites within
# this module continue to work without modification.
_is_primary_instance = is_primary_instance


def _json_message_ids(message_ids: list[int], product_id: int | None = None) -> str:
    """Return validated JSON for additional message ids."""
    clean_ids = []
    for message_id in message_ids:
        try:
            clean_ids.append(int(message_id))
        except (TypeError, ValueError):
            logger.warning(
                "Rejected malformed message-id payload",
                extra={"product_id": product_id, "message_id": message_id},
            )
    return json.dumps(clean_ids)


def _json_additional_media(file_ids: list[str], file_types: list[str], product_id: int | None = None) -> str:
    media = []
    for file_id, file_type in zip(file_ids, file_types):
        if not file_id or not file_type:
            logger.warning(
                "Rejected malformed additional media payload",
                extra={"product_id": product_id, "file_id": file_id, "file_type": file_type},
            )
            continue
        media.append((file_id, file_type))
    return json.dumps(media)


async def notify_admins_for_categorization(context: ContextTypes.DEFAULT_TYPE, product_id: int):
    """Send categorization request to all admins for a new product.
    Only runs on the primary bot instance to avoid duplicate notifications.
    """
    # Check if this is the primary bot instance
    if not _is_primary_instance(context):
        logger.info(f"Skipping categorization notification for product {product_id} - not primary instance")
        return
    
    try:
        product = await db.get_product(product_id)
        if not product:
            return
        
        # Get all admin IDs
        admin_ids = get_admin_ids()
        
        if not admin_ids:
            logger.warning("No admin IDs configured. Product will remain uncategorized.")
            return
        
        # Create category selection keyboard
        keyboard_buttons = []
        
        for category in get_all_categories():
            display_name = get_category_display_name(category)
            keyboard_buttons.append([
                InlineKeyboardButton(display_name, callback_data=f"setcat|{product_id}|{category}")
            ])
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        # Prepare notification message
        caption = product.get("caption", "No caption")
        caption_preview = caption[:100] + "..." if len(caption) > 100 else caption
        
        message_text = (
            f"🆕 **New Product Added - Needs Categorization**\n\n"
            f"📝 Caption: {caption_preview}\n"
            f"🆔 Product ID: {product_id}\n\n"
            f"Please select a category:"
        )
        
        # Send notification to each admin
        for admin_id in admin_ids:
            try:
                await safe_send_message(context.bot,
                    chat_id=admin_id,
                    text=message_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )

                logger.info(f"Sent categorization request to admin {admin_id} for product {product_id}")

            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
        
        # Mark as pending categorization
        await db.add_pending_categorization(product_id)
        
    except Exception as e:
        logger.error(f"Error notifying admins for categorization: {e}")


async def process_media_group(media_group_id: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Process collected media group messages after a delay to ensure all media is received.

    Increased delay to 5000ms (5 seconds) to prevent partial uploads when Telegram
    sends messages with network latency jitter. This ensures all photos in an album
    are captured before processing, preventing incomplete media groups.
    """
    await asyncio.sleep(5.0)
    
    # Get or create a lock for this media group to prevent race conditions
    if media_group_id not in media_group_locks:
        media_group_locks[media_group_id] = asyncio.Lock()
    
    async with media_group_locks[media_group_id]:
        if media_group_id not in media_group_messages:
            return
        
        messages = media_group_messages.pop(media_group_id, [])
        media_group_timers.pop(media_group_id, None)
        media_group_locks.pop(media_group_id, None)  # Clean up the lock
    
    if not messages:
        return
    
    # Sort messages by message_id to ensure consistent ordering
    messages.sort(key=lambda m: m.message_id)
    
    # Use the first message for primary data
    first_message = messages[0]
    
    # Get caption from the first message that has one
    caption = None
    for msg in messages:
        if msg.caption or msg.text:
            caption = msg.caption or msg.text
            break
    
    if not caption:
        caption = f"[Album - {len(messages)} items]"
    
    # Collect all file IDs and message IDs
    file_ids = []
    file_types = []
    message_ids = []
    
    for msg in messages:
        # Create a temporary update object for each message
        temp_update = Update(0, channel_post=msg)
        file_id, file_type = get_file_id_and_type(temp_update)
        if file_id:
            file_ids.append(file_id)
            file_types.append(file_type)
            message_ids.append(msg.message_id)
    
    if not file_ids:
        logger.warning(f"No file IDs extracted from media group {media_group_id}")
        return
    
    # Check if this media group already has a product
    existing_product_id = await db.get_or_create_media_group_product(media_group_id, chat_id)
    
    if existing_product_id:
        # Update existing product with new file IDs and message IDs
        additional_files = _json_additional_media(file_ids[1:], file_types[1:], existing_product_id)
        additional_msg_ids = _json_message_ids(message_ids[1:], existing_product_id)
        await db.update_product_media(existing_product_id, additional_files, additional_msg_ids)
        logger.info(f"Updated media group product {existing_product_id} with {len(file_ids)} files")
        return
    
    # Create new product with first file as primary (with automatic categorization)
    additional_files = _json_additional_media(file_ids[1:], file_types[1:])
    additional_msg_ids = _json_message_ids(message_ids[1:])
    
    # Extract category from caption hashtags
    category, subcategory = None, None
    
    product_id = await db.add_product(
        file_id=file_ids[0],
        file_type=file_types[0],
        caption=caption,
        message_id=first_message.message_id,
        chat_id=chat_id,
        media_group_id=media_group_id,
        additional_file_ids=additional_files,
        additional_message_ids=additional_msg_ids,
        category=category,
        subcategory=subcategory,
        bot_username=context.bot.username
    )
    
    if product_id > 0:
        # Register this media group
        await db.register_media_group(media_group_id, chat_id, product_id)
        category_info = f"category={category}, subcategory={subcategory}" if category else "uncategorized"
        logger.info(
            f"New media group product saved: ID={product_id}, files={len(file_ids)}, "
            f"media_group_id={media_group_id}, {category_info}"
        )
        invalidate_webapp_caches(f"media group product {product_id} added")
        
        # Notify admins for categorization
        await notify_admins_for_categorization(context, product_id)



async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle new posts in the monitored channel.
    Detects media posts and saves them as products.
    Properly handles photo albums (media groups) as single entries.
    """
    try:
        message = update.channel_post
        if not message:
            return
        
        # Check if this message is in the ignore list (deleted product)
        if await db.is_message_ignored(message.message_id, message.chat.id):
            logger.debug(f"Message {message.message_id} is in ignore list, skipping")
            return
        
        # Check if message has media
        if not has_media(update):
            logger.debug(f"Message {message.message_id} has no media, skipping")
            return
        
        # Handle media groups (photo albums)
        if message.media_group_id:
            # Collect messages from the same media group
            media_group_id = message.media_group_id
            
            if media_group_id not in media_group_messages:
                media_group_messages[media_group_id] = []
            
            media_group_messages[media_group_id].append(message)
            
            # Cancel existing timer if any
            if media_group_id in media_group_timers:
                media_group_timers[media_group_id].cancel()
            
            # Schedule processing of this media group (pass context)
            timer = asyncio.create_task(
                process_media_group(media_group_id, message.chat.id, context)
            )
            media_group_timers[media_group_id] = timer
            
            return
        
        # Handle single media messages
        file_id, file_type = get_file_id_and_type(update)
        
        if not file_id:
            logger.warning(f"Could not extract file_id from message {message.message_id}")
            return
        
        # Get caption
        caption = message.caption or message.text or ""
        
        # Extract category from caption hashtags
        category, subcategory = None, None
        
        # Save product to database with automatic categorization
        product_id = await db.add_product(
            file_id=file_id,
            file_type=file_type,
            caption=caption,
            message_id=message.message_id,
            chat_id=message.chat.id,
            additional_file_ids=json.dumps([]),
            additional_message_ids=json.dumps([]),
            category=category,
            subcategory=subcategory,
            bot_username=context.bot.username
        )
        
        if product_id > 0:
            category_info = f"category={category}, subcategory={subcategory}" if category else "uncategorized"
            logger.info(
                f"New product saved: ID={product_id}, "
                f"message_id={message.message_id}, "
                f"type={file_type}, "
                f"{category_info}, "
                f"bot={context.bot.username}"
            )
            invalidate_webapp_caches(f"product {product_id} added")
            
            # Notify admins for categorization
            logger.info(f"Sending categorization request for product {product_id} (bot: {context.bot.username})")
            await notify_admins_for_categorization(context, product_id)
        else:
            logger.debug(f"Product already exists for message {message.message_id}")
            
    except Exception as e:
        logger.error(f"Error handling channel post: {e}", exc_info=True)


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback queries (buttons).

    Acts as a thin entry point: validates the user is not blocked, answers the
    Telegram query immediately (except for long-running operations), then
    delegates to the domain-specific router in handlers/callback_router.py.
    """
    query = update.callback_query

    # Check if user is blocked (except for admins)
    user_id = update.effective_user.id
    if not is_admin(user_id):
        is_blocked = await db.is_user_blocked(user_id)
        if is_blocked:
            await query.answer(
                "🚫 You have been blocked from using this bot.",
                show_alert=True
            )
            return

    callback_data = query.data

    # Don't answer callback query upfront for long-running operations
    # These callbacks will answer the query themselves to avoid timeout errors
    callback_answered = False
    if callback_data not in LONG_RUNNING_CALLBACKS:
        try:
            await query.answer()  # Answer callback to prevent loading spinner
            callback_answered = True
        except BadRequest as exc:
            if "query is too old" not in str(exc).lower():
                raise
            logger.info("Ignoring expired callback query answer for data=%r", callback_data)

    await route_callback(
        update,
        context,
        query,
        callback_data,
        user_id,
        already_answered=callback_answered,
    )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle regular messages from users.
    Treats all non-command messages as search queries.
    """
    # Skip if it's a command
    if update.message and update.message.text and update.message.text.startswith("/"):
        return
    
    # Skip channel posts (handled separately)
    if update.channel_post:
        return
    
    # Track user
    user = update.effective_user
    bot_username = context.bot.username if hasattr(context.bot, 'username') else None
    await db.track_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        bot_username=bot_username
    )
    
    # Check if user is blocked
    user_id = update.effective_user.id
    if not is_admin(user_id):
        is_blocked = await db.is_user_blocked(user_id)
        if is_blocked:
            await update.message.reply_text(
                "🚫 You have been blocked from using this bot.\n\n"
                "If you believe this is an error, please contact an administrator."
            )
            return
    
    # Rate limiting (simple)
    current_time = datetime.now().timestamp()
    
    if user_id in user_last_message:
        time_diff = current_time - user_last_message[user_id]
        if time_diff < 1:  # 1 second between messages
            await update.message.reply_text(
                "⏳ Please wait a moment before sending another message."
            )
            return
    
    user_last_message[user_id] = current_time
    
    # Check if admin is setting contact
    if is_admin(user_id) and context.user_data.get('awaiting_contact'):
        await handle_setcontact_input(update, context)
        return

    # Check if admin is setting link
    if is_admin(user_id) and context.user_data.get('awaiting_link'):
        await handle_setlink_input(update, context)
        return

    # Check if admin is in broadcast workflow
    if is_admin(user_id) and 'broadcast_mode' in context.user_data:
        from handlers.admin import handle_broadcast_workflow
        await handle_broadcast_workflow(update, context)
        return
    
    # Handle as search query
    if update.message and update.message.text:
        await handle_search(update, context)



async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors."""
    # Log error with full context and traceback
    update_info = "unknown"
    if update:
        if update.effective_user:
            update_info = f"user {update.effective_user.id}"
        elif update.effective_chat:
            update_info = f"chat {update.effective_chat.id}"
        update_info += f" (update_id: {update.update_id})"
    
    logger.error(f"Update from {update_info} caused error: {context.error}", exc_info=True)
    
    if isinstance(context.error, TelegramError):
        if "message is not modified" in str(context.error).lower():
            # Ignore this common error
            return
        elif "chat not found" in str(context.error).lower():
            logger.warning("Chat not found - user may have blocked the bot")
            return
    
    # Try to send error message to user if possible
    if update and update.effective_chat:
        try:
            await safe_send_message(context.bot,
                chat_id=update.effective_chat.id,
                text="❌ An unexpected error occurred. Please try again later."
            )
        except Exception as e:
            logger.debug(f"Could not send error message to user: {e}")
            pass


async def cleanup_task(context: ContextTypes.DEFAULT_TYPE):
    """Periodic cleanup task for old pagination states and in-memory dictionaries.
    Only runs on the primary bot instance to avoid duplicate cleanup.
    """
    # Check if this is the primary bot instance
    if not _is_primary_instance(context):
        logger.info("Cleanup task disabled - not primary instance")
        return

    # TTL for in-memory rate-limit tracking entries (seconds)
    USER_LAST_MESSAGE_TTL = 300  # 5 minutes

    while True:
        try:
            await asyncio.sleep(600)  # Run every 10 minutes
            await db.cleanup_old_pagination_states(minutes=10)
            logger.debug("Cleaned up old pagination states")

            # TTL-based cleanup of user_last_message to prevent unbounded growth
            now = datetime.now().timestamp()
            cutoff = now - USER_LAST_MESSAGE_TTL
            stale_users = [uid for uid, ts in list(user_last_message.items()) if ts < cutoff]
            for uid in stale_users:
                user_last_message.pop(uid, None)
            if stale_users:
                logger.debug(f"Evicted {len(stale_users)} stale entries from user_last_message")

            # Clean up any lingering media group state (should be empty by now, but guard anyway)
            stale_groups = list(media_group_messages.keys())
            for mgid in stale_groups:
                media_group_messages.pop(mgid, None)
                media_group_timers.pop(mgid, None)
                media_group_locks.pop(mgid, None)
            if stale_groups:
                logger.debug(
                    f"Evicted {len(stale_groups)} stale entries from media_group dicts"
                )
        except Exception as e:
            logger.error(f"Error in cleanup task: {e}")


async def post_init(application: Application, skip_commands: bool = False):
    """Initialize database and start background tasks."""
    await db.init_db()
    logger.info("Database initialized")
    
    # Set up translation service with database
    translation_service.set_database(db)
    logger.info("Translation service configured with database caching")
    
    # Set bot commands for the command menu (skip if called from webhook setup)
    if not skip_commands:
        await setup_bot_commands(application.bot)
    
    # Start cleanup task
    asyncio.create_task(cleanup_task(application))


def _validate_bot_commands(commands):
    """Return Telegram-safe bot commands without changing valid command contracts."""
    valid = []
    for cmd in commands:
        name = getattr(cmd, "command", "")
        if not isinstance(name, str):
            logger.warning("Skipping invalid bot command with non-string name: %r", name)
            continue
        safe_name = name.strip().lower()
        if safe_name != name:
            logger.warning("Skipping invalid bot command %r; command names must be lowercase", name)
            continue
        if not re.fullmatch(r"[a-z0-9_]{1,32}", safe_name):
            logger.warning("Skipping invalid bot command %r; name is not Telegram-safe", name)
            continue
        valid.append(cmd)
        if len(valid) >= 100:
            logger.warning("Bot command list capped at Telegram limit of 100 commands")
            break
    return valid


async def setup_bot_commands(bot):
    """
    Set up bot commands that appear in the Telegram command menu.
    
    This function configures the command menu that users see when they click the menu
    button next to the message input field in Telegram. It sets different commands for
    regular users vs admins.
    
    **IMPORTANT**: This function is called automatically during bot initialization in
    both polling mode (main.py) and webhook mode (webhook_server.py) via post_init().
    This ensures that ALL bot instances (primary and secondary) have consistent command
    menus regardless of which bot token is used.
    
    Args:
        bot: The Telegram bot instance
    """
    bot_label = getattr(bot, "username", None) or "unknown"
    # Configure Menu Button to open webapp (if WEB_APP_URL is configured)
    if Config.WEB_APP_URL:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="🛒 SHOP",  # Text shown on menu button
                    web_app=WebAppInfo(url=Config.WEB_APP_URL)
                )
            )
            logger.info("Menu button configured for bot=%s", bot_label)
        except TelegramError as e:
            logger.warning("Skipping menu button setup for bot=%s: %s", bot_label, e)
        except Exception as e:
            logger.warning("Skipping menu button setup for bot=%s: %s", bot_label, e)
    
    # Define user commands (available to all users)
    # These appear in the command menu for all users
    user_commands = [
        BotCommand("start", "Start the bot and select language"),
        BotCommand("menu", "Browse the product catalog"),
        BotCommand("language", "Change bot language"),
        BotCommand("subscribe", "Enable product notifications"),
        BotCommand("unsubscribe", "Disable product notifications"),
    ]

    # Define admin commands (available to admins only)
    # These appear in the command menu ONLY for admin users
    # Note: subscribe/unsubscribe are excluded as admins don't need notifications
    admin_commands = [
        BotCommand("start", "Start the bot and select language"),
        BotCommand("menu", "Browse the product catalog"),
        BotCommand("language", "Change bot language"),
        BotCommand("adminstatus", "View bot and WebApp status"),
        BotCommand("seostatus", "View automatic SEO search learning"),
        BotCommand("users", "View and manage bot users"),
        BotCommand("botusers", "View users grouped by bot instance"),
        BotCommand("send", "Send a message to a specific user"),
        BotCommand("broadcast", "Send a message to all users"),
        BotCommand("setcontact", "Set order contact username"),
        BotCommand("setlink", "Set contacts link URL"),
        BotCommand("recategorize", "Categorize uncategorized products"),
        BotCommand("clearcache", "Clear file ID cache"),
        BotCommand("prunebots", "Prune users from inactive bots"),
        BotCommand("reinitbots", "Reinitialise all bot instances"),
        BotCommand("list_broadcasts", "List group/channel broadcast destinations"),
        BotCommand("nuke", "Delete all products (use with caution)"),
    ]
    
    # Set default commands for all users
    # This updates the global command menu seen by regular users
    user_commands = _validate_bot_commands(user_commands)
    admin_commands = _validate_bot_commands(admin_commands)

    if user_commands:
        try:
            await bot.set_my_commands(user_commands)
            logger.info("Set %d user command(s) globally for bot=%s", len(user_commands), bot_label)
        except TelegramError as e:
            logger.warning("Skipping global command setup for bot=%s: %s", bot_label, e)
        except Exception as e:
            logger.warning("Skipping global command setup for bot=%s: %s", bot_label, e)
    else:
        logger.warning("Skipping global command setup for bot=%s: no valid commands", bot_label)
    
    # Set admin-specific commands sequentially so one Telegram API failure cannot
    # abort all startup command registration.
    # This overrides the global commands for admin chat scopes
    admin_ids = get_admin_ids()
    if not admin_ids:
        logger.warning("No admin IDs configured - admin commands will not be set")
    else:
        async def set_admin_commands_for_user(admin_id):
            try:
                await bot.set_my_commands(
                    admin_commands,
                    scope=BotCommandScopeChat(chat_id=admin_id)
                )
                logger.info("Set %d admin command(s) for admin=%s bot=%s", len(admin_commands), admin_id, bot_label)
                return True
            except TelegramError as e:
                logger.warning("Skipping admin command setup for admin=%s bot=%s: %s", admin_id, bot_label, e)
                return False
            except Exception as e:
                logger.warning("Skipping admin command setup for admin=%s bot=%s: %s", admin_id, bot_label, e)
                return False
        
        for admin_id in admin_ids:
            await set_admin_commands_for_user(admin_id)
    
    logger.info("Bot commands configured successfully - menu will be consistent across all bot instances")



def main():
    """Main function to start the bot."""
    # Validate configuration on startup
    try:
        Config.validate()
    except ConfigError as e:
        logger.error(str(e))
        return
    
    # Check if we should use webhook mode
    if Config.USE_WEBHOOK:
        logger.info("Webhook mode detected. Please run webhook_server.py instead.")
        logger.info("Example: python webhook_server.py")
        logger.info("Or use: uvicorn webhook_server:app --host 0.0.0.0 --port 8000")
        return
    
    # Get bot token from config
    bot_token = Config.BOT_TOKEN
    
    # Create application
    application = Application.builder().token(bot_token).post_init(post_init).build()
    
    # Register handlers
    register_bot_handlers(application)
    
    # Start the bot in polling mode
    logger.info("Starting bot in polling mode...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
