"""
Main entry point for the Telegram media catalog bot.
"""
import logging
import asyncio
import json
import aiosqlite
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat, MenuButtonWebApp, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ChosenInlineResultHandler,
    filters,
    ContextTypes
)
from telegram.error import TelegramError, BadRequest

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
    escape_markdown_v1
)
from utils.categories import get_all_categories, get_category_display_name, get_subcategories, get_subcategory_display_name, CATEGORIES
from utils.notifications import NotificationService
from utils.safe_send import safe_send_message, safe_edit_message_text

# Configure logging
from configs.logging_config import configure_logging
configure_logging()
logger = logging.getLogger(__name__)

# Initialize database
db = Database()

# Shared notification service singleton (ensures duplicate-task guard works across callbacks)
notification_service = NotificationService(db)

# Rate limiting (simple in-memory store)
user_last_message = {}

# Media group collection (temporary storage for grouping messages)
media_group_messages = {}
media_group_timers = {}
media_group_locks = {}  # Locks to prevent race conditions in media group processing

# Callback query configuration
LONG_RUNNING_CALLBACKS = ["broadcast_confirm_all"]  # Callbacks that should not answer query upfront to avoid timeouts


def _is_primary_instance(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Check if this is the primary bot instance.
    Only the primary bot (first token in BOT_TOKENS) should create notification queues.
    
    Args:
        context: Telegram context with bot information
        
    Returns:
        True if this is the primary instance, False otherwise
    """
    if not hasattr(context, 'bot') or not hasattr(context.bot, 'token'):
        # If we can't determine, assume it's primary to maintain backward compatibility
        logger.warning("Unable to determine bot instance, assuming primary")
        return True
    
    current_token = context.bot.token
    bot_tokens = Config.BOT_TOKENS
    
    # Validate BOT_TOKENS is a non-empty list
    if not bot_tokens or not isinstance(bot_tokens, list) or len(bot_tokens) == 0:
        # No tokens configured or invalid configuration, assume primary
        return True
    
    # Primary bot is the first token in the list (index 0)
    is_primary = current_token == bot_tokens[0]
    
    if not is_primary:
        logger.debug(f"Skipping operation - not primary instance (bot token: ...{current_token[-5:]})")
    
    return is_primary



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
        
        # Get primary admin ID (use PRIMARY_ADMIN_ID if configured, otherwise use first admin)
        from configs.config import Config
        primary_admin = Config.PRIMARY_ADMIN_ID or (admin_ids[0] if admin_ids else None)

        if not primary_admin:
            logger.warning("No primary admin configured. Product will remain uncategorized.")
            return

        # Send notification only to primary admin
        try:
            await safe_send_message(context.bot,
                chat_id=primary_admin,
                text=message_text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
            
            logger.info(f"Sent categorization request to primary admin {primary_admin} for product {product_id}")
            
        except Exception as e:
            logger.error(f"Failed to notify primary admin {primary_admin}: {e}")
        
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
        additional_files = json.dumps(list(zip(file_ids[1:], file_types[1:])))
        additional_msg_ids = json.dumps(message_ids[1:]) if len(message_ids) > 1 else None
        await db.update_product_media(existing_product_id, additional_files, additional_msg_ids)
        logger.info(f"Updated media group product {existing_product_id} with {len(file_ids)} files")
        return
    
    # Create new product with first file as primary (with automatic categorization)
    additional_files = json.dumps(list(zip(file_ids[1:], file_types[1:]))) if len(file_ids) > 1 else None
    additional_msg_ids = json.dumps(message_ids[1:]) if len(message_ids) > 1 else None
    
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
    if callback_data not in LONG_RUNNING_CALLBACKS:
        await query.answer()  # Answer callback to prevent loading spinner

    await route_callback(update, context, query, callback_data, user_id)


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


async def post_init(application: Application):
    """Initialize database and start background tasks."""
    await db.init_db()
    logger.info("Database initialized")
    
    # Set up translation service with database
    translation_service.set_database(db)
    logger.info("Translation service configured with database caching")
    
    # Set bot commands for the command menu
    await setup_bot_commands(application.bot)
    
    # Start cleanup task
    asyncio.create_task(cleanup_task(application))


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
    # Configure Menu Button to open webapp (if WEB_APP_URL is configured)
    if Config.WEB_APP_URL:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="🛒 SHOP",  # Text shown on menu button
                    web_app=WebAppInfo(url=Config.WEB_APP_URL)
                )
            )
            logger.info(f"Menu button configured to open webapp: {Config.WEB_APP_URL}")
        except Exception as e:
            logger.error(f"Failed to set menu button: {e}")
    
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
        BotCommand("users", "View and manage bot users"),
        BotCommand("botusers", "View users grouped by bot instance"),
        BotCommand("send", "Send a message to a specific user"),
        BotCommand("broadcast", "Send a message to all users"),
        BotCommand("setcontact", "Set order contact username"),
        BotCommand("setlink", "Set contacts link URL"),
        BotCommand("recategorize", "Categorize uncategorized products"),
        BotCommand("clearcache", "Clear file ID cache"),
        BotCommand("prunebots", "Prune users from inactive bots"),
        BotCommand("nuke", "Delete all products (use with caution)"),
    ]
    
    # Set default commands for all users
    # This updates the global command menu seen by regular users
    await bot.set_my_commands(user_commands)
    logger.info(f"Set user commands globally: {[cmd.command for cmd in user_commands]}")
    
    # Set admin-specific commands for each admin user (batched for performance)
    # This overrides the global commands for admin chat scopes
    admin_ids = get_admin_ids()
    if not admin_ids:
        logger.warning("No admin IDs configured - admin commands will not be set")
    else:
        # Batch admin command setup using asyncio.gather for parallel execution
        async def set_admin_commands_for_user(admin_id):
            try:
                await bot.set_my_commands(
                    admin_commands,
                    scope=BotCommandScopeChat(chat_id=admin_id)
                )
                logger.info(f"Set admin commands for user {admin_id}: {[cmd.command for cmd in admin_commands]}")
                return True
            except Exception as e:
                logger.warning(f"Could not set admin commands for {admin_id}: {e}")
                return False
        
        # Execute all admin command setups concurrently
        await asyncio.gather(*[set_admin_commands_for_user(admin_id) for admin_id in admin_ids])
    
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
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("language", language_command))
    application.add_handler(CommandHandler("nuke", nuke_command))
    application.add_handler(CommandHandler("recategorize", recategorize_command))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("botusers", botusers_command))
    application.add_handler(CommandHandler("prunebots", prunebots_command))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("block", block_command))
    application.add_handler(CommandHandler("unblock", unblock_command))
    application.add_handler(CommandHandler("send", send_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("setcontact", setcontact_command))
    application.add_handler(CommandHandler("setlink", setlink_command))
    application.add_handler(CommandHandler("clearcache", clearcache_command))
    
    # Channel post handler (for monitoring channel)
    channel_id = get_channel_id()
    channel_username = get_channel_username()
    
    if channel_id:
        # Use channel ID filter
        channel_filter = filters.Chat(chat_id=channel_id)
    elif channel_username:
        # Use channel username filter
        channel_filter = filters.Chat(username=channel_username.lstrip("@"))
    else:
        logger.warning("No CHANNEL_ID or CHANNEL_USERNAME set. Channel monitoring disabled.")
        channel_filter = None
    
    if channel_filter:
        application.add_handler(
            MessageHandler(
                channel_filter & filters.ChatType.CHANNEL,
                channel_post_handler
            )
        )
        logger.info(f"Channel monitoring enabled for: {channel_id or channel_username}")
    
    # Callback query handler (for inline buttons)
    application.add_handler(CallbackQueryHandler(callback_query_handler))
    
    # Inline query handler (for @bot search in any chat)
    application.add_handler(InlineQueryHandler(handle_inline_query))
    application.add_handler(ChosenInlineResultHandler(handle_chosen_inline_result))

    # Message handler (for search queries)
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            message_handler
        )
    )
    
    # Error handler
    application.add_error_handler(error_handler)
    
    # Start the bot in polling mode
    logger.info("Starting bot in polling mode...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
