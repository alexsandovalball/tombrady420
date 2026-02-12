"""
Main entry point for the Telegram media catalog bot.
"""
import logging
import asyncio
import json
import aiosqlite
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)
from telegram.error import TelegramError, BadRequest

from configs.config import Config, ConfigError
from database import Database
from handlers.start import start_command, subscribe_command, unsubscribe_command
from handlers.menu import menu_command, show_catalog_page, handle_catalog_pagination
from handlers.search import handle_search, show_search_results, handle_search_pagination
from handlers.product_view import show_product, handle_product_callback
from handlers.language import language_command, handle_language_callback
from handlers.admin import (
    delete_product, nuke_command, recategorize_command, 
    users_command, show_users_page, build_users_bot_selection_menu,
    block_command, unblock_command, send_command, broadcast_command,
    setcontact_command, handle_setcontact_input, clearcache_command,
    botusers_command, prunebots_command
)
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

# Configure logging with structured format for better visibility on Render
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s',
    level=logging.INFO,
    force=True  # Ensure this configuration takes precedence
)
logger = logging.getLogger(__name__)

# Ensure all loggers use INFO level
logging.getLogger('telegram').setLevel(logging.INFO)
logging.getLogger('httpx').setLevel(logging.WARNING)  # Reduce noise from HTTP library

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
            await context.bot.send_message(
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
    """Process collected media group messages after a short delay."""
    await asyncio.sleep(0.5)  # Wait for all messages in the group to arrive
    
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
    
    # Create new product with first file as primary (NO automatic categorization)
    additional_files = json.dumps(list(zip(file_ids[1:], file_types[1:]))) if len(file_ids) > 1 else None
    additional_msg_ids = json.dumps(message_ids[1:]) if len(message_ids) > 1 else None
    
    product_id = await db.add_product(
        file_id=file_ids[0],
        file_type=file_types[0],
        caption=caption,
        message_id=first_message.message_id,
        chat_id=chat_id,
        media_group_id=media_group_id,
        additional_file_ids=additional_files,
        additional_message_ids=additional_msg_ids,
        category=None,  # No automatic categorization
        subcategory=None,
        bot_username=context.bot.username
    )
    
    if product_id > 0:
        # Register this media group
        await db.register_media_group(media_group_id, chat_id, product_id)
        logger.info(
            f"New media group product saved: ID={product_id}, files={len(file_ids)}"
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
        
        # Save product to database WITHOUT automatic categorization
        product_id = await db.add_product(
            file_id=file_id,
            file_type=file_type,
            caption=caption,
            message_id=message.message_id,
            chat_id=message.chat.id,
            category=None,  # No automatic categorization
            subcategory=None,
            bot_username=context.bot.username
        )
        
        if product_id > 0:
            logger.info(
                f"New product saved: ID={product_id}, "
                f"message_id={message.message_id}, "
                f"type={file_type}, "
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
    """Handle all callback queries (buttons)."""
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
    
    # Handle language selection callbacks from /language command
    if callback_data.startswith("setlang|"):
        parts = callback_data.split("|")
        if len(parts) >= 2:
            lang_code = parts[1]
            await handle_language_callback(update, context, lang_code)
            return
    
    # Handle opening language settings from start page
    if callback_data == "open_language_settings":
        # Get user's current language
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
        message_text = await get_translated_string_async("language_settings", current_lang)
        current_lang_name = LANGUAGE_DISPLAY.get(current_lang, LANGUAGE_DISPLAY["en"])
        current_lang_text = await get_translated_string_async(
            "current_language", 
            current_lang,
            language=current_lang_name
        )
        
        message_text += f"\n\n{current_lang_text}"
        
        # Check if we need to send new message or edit existing
        message = query.message
        if message.photo or message.video or message.document or message.animation or message.audio:
            # Media message - send new text message
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=message_text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        else:
            # Text message - edit it
            await query.edit_message_text(
                message_text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        
        logger.info(f"User {user_id} opened language settings from start page (current: {current_lang})")
        return
    
    # Handle language selection callbacks from /start command (new users)
    if callback_data.startswith("setlang_start|"):
        parts = callback_data.split("|")
        if len(parts) >= 2:
            lang_code = parts[1]
            
            # Validate and set language
            if is_valid_language(lang_code):
                await db.set_user_language(user_id, lang_code)
                # Answer the callback
                await query.answer()
                # Delete the language selection message
                await query.delete_message()
                
                # Show welcome message in selected language
                is_subscribed = await db.is_user_subscribed(user_id)
                
                # Get user display name and order contact
                display_name = get_user_display_name(update.effective_user, escaped=True)
                order_contact = await db.get_order_contact()
                escaped_contact = escape_markdown_v1(order_contact)
                
                welcome_text = await get_translated_string_async("welcome_with_contact", lang_code, name=display_name, contact=escaped_contact)
                view_catalog_text = await get_translated_string_async("view_catalog", lang_code)
                change_language_text = await get_translated_string_async("change_language", lang_code)
                keyboard_buttons = [
                    [InlineKeyboardButton(view_catalog_text, callback_data="categories")],
                    [InlineKeyboardButton(change_language_text, callback_data="open_language_settings")]
                ]
                
                if not is_subscribed:
                    resubscribe_text = await get_translated_string_async("resubscribe_notifications", lang_code)
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
                logger.info(f"New user {user_id} selected language: {lang_code}")
            else:
                await query.answer("❌ Invalid language", show_alert=True)
            return
    
    # Handle noop (page indicator button)
    if callback_data == "noop":
        return
    
    # Handle broadcast confirmations
    if callback_data == "broadcast_cancel":
        await query.edit_message_text("❌ Broadcast cancelled.")
        context.user_data.clear()
        return
    
    if callback_data.startswith("broadcast_confirm_single|"):
        # Confirm and send single user broadcast
        parts = callback_data.split("|")
        if len(parts) >= 2:
            target_user_id = int(parts[1])
            message_text = context.user_data.get('broadcast_message')
            
            if not message_text:
                await query.answer("❌ Message not found. Please start over.", show_alert=True)
                context.user_data.clear()
                return
            
            # Send the message
            notification_service = NotificationService(db)
            
            success = await notification_service.send_custom_message_to_user(
                context,
                target_user_id,
                message_text
            )
            
            if success:
                await query.edit_message_text(
                    f"✅ **Message Sent Successfully!**\n\n"
                    f"🆔 User ID: `{target_user_id}`\n"
                    f"📝 Message: {message_text[:100]}{'...' if len(message_text) > 100 else ''}",
                    parse_mode="Markdown"
                )
                logger.info(f"Admin {user_id} sent message to user {target_user_id}")
            else:
                await query.edit_message_text("❌ Failed to send message. Check logs for details.")
            
            context.user_data.clear()
        return
    
    if callback_data == "broadcast_confirm_all":
        # Confirm and send broadcast to all users
        message_text = context.user_data.get('broadcast_message')
        
        if not message_text:
            await query.answer("❌ Message not found. Please start over.", show_alert=True)
            context.user_data.clear()
            return
        
        # Answer query immediately to prevent timeout during long operation
        await query.answer()
        
        # Send status message
        await query.edit_message_text(
            "📡 **Broadcasting message...**\n\n"
            "Please wait while the message is queued and delivered to all users.",
            parse_mode="Markdown"
        )
        
        # Broadcast using notification service
        notification_service = NotificationService(db)
        
        stats = await notification_service.broadcast_custom_message(
            context,
            message_text,
            exclude_blocked=True,
            admin_user_id=user_id
        )
        
        # Note: The detailed summary is sent separately by the notification service
        # Failure count = permanent failures (blocked + not_found + unexpected_errors)
        # Excludes markdown_errors (successfully sent as plain text) and rate_limited (queued for later)
        total_failures = stats.get('blocked', 0) + stats.get('not_found', 0) + stats.get('unexpected_errors', 0)
        logger.info(f"Admin {user_id} broadcast complete: {stats.get('sent', 0)} sent, {total_failures} failed")
        
        context.user_data.clear()
        return
    
    # Handle categories menu
    if callback_data == "categories":
        from handlers.menu import show_category_menu
        await show_category_menu(update, context)
        return
    
    # Handle nuke confirmations
    if callback_data == "nuke_cancel":
        await query.edit_message_text("✅ Nuke cancelled. No products were deleted.")
        return
    
    if callback_data == "nuke_confirm1":
        # Second confirmation
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💥 YES, NUKE EVERYTHING", callback_data="nuke_confirm2")],
            [InlineKeyboardButton("❌ Cancel", callback_data="nuke_cancel")]
        ])
        
        await query.edit_message_text(
            f"⚠️ **FINAL WARNING**\n\n"
            f"This is your last chance to cancel!\n\n"
            f"Are you ABSOLUTELY SURE you want to delete ALL products?\n\n"
            f"Type /nuke again to start over if you're unsure.",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        return
    
    if callback_data == "nuke_confirm2":
        # Execute nuke
        if not is_admin(query.from_user.id):
            await query.answer("❌ Only admins can nuke.", show_alert=True)
            return
        
        try:
            # Get all products before deletion
            all_products = await db.get_all_products_for_search()
            total_count = len(all_products)
            
            if total_count == 0:
                await query.edit_message_text("📭 The catalog is already empty. Nothing to delete.")
                logger.info(f"Admin {query.from_user.id} attempted nuke on empty catalog")
                return
            
            logger.warning(f"Admin {query.from_user.id} executing nuke - deleting {total_count} products")
            
            # Delete all products from database in a transaction (channel messages will remain)
            async with aiosqlite.connect(db.db_path) as conn:
                try:
                    cursor = await conn.execute("DELETE FROM products")
                    await conn.commit()
                    deleted_count = cursor.rowcount
                    logger.info(f"Deleted {deleted_count} products from database")
                except Exception as e:
                    await conn.rollback()
                    logger.error(f"Failed to delete products from database: {e}")
                    raise
            
            # Build detailed completion message
            text = (
                f"💥 **NUKE COMPLETE**\n\n"
                f"🗑️ Deleted {deleted_count} product(s) from database\n"
                f"📝 Channel messages remain intact (manual cleanup required)"
            )
            
            await query.edit_message_text(text, parse_mode="Markdown")
            logger.warning(f"Nuke complete - DB: {deleted_count}, Channel messages preserved")
            
        except Exception as e:
            logger.error(f"Error executing nuke: {e}", exc_info=True)
            await query.edit_message_text(
                "❌ An error occurred while deleting products. Check logs for details."
            )
        
        return
    
    try:
        parts = callback_data.split("|")
        
        # Handle category setting (admin categorization)
        if parts[0] == "setcat":
            if len(parts) >= 3:
                product_id = int(parts[1])
                category = parts[2]
                
                # Check if user is admin
                if not is_admin(update.effective_user.id):
                    await query.answer("❌ Only admins can categorize products.", show_alert=True)
                    return
                
                # Check if this is a main category or we need subcategory
                subcategories = get_subcategories(category)
                
                # Get user language preference
                user_id = update.effective_user.id
                user_lang = await db.get_user_language(user_id)
                
                if subcategories:
                    # Show subcategory selection
                    keyboard_buttons = []
                    for subcat in subcategories:
                        # Get translated subcategory name
                        translated_subcat = get_subcategory_display_name(subcat, user_lang)
                        keyboard_buttons.append([
                            InlineKeyboardButton(translated_subcat, callback_data=f"setsubcat|{product_id}|{category}|{subcat}")
                        ])
                    
                    # Add "No Subcategory" option
                    save_without_text = await get_translated_string_async("save_without_subcategory", user_lang)
                    keyboard_buttons.append([
                        InlineKeyboardButton(save_without_text, callback_data=f"savecat|{product_id}|{category}|")
                    ])
                    
                    keyboard = InlineKeyboardMarkup(keyboard_buttons)
                    
                    # Translate category name
                    category_key = f"category_{category.lower()}"
                    translated_category = await get_translated_string_async(category_key, user_lang)
                    if translated_category == category_key:
                        translated_category = get_category_display_name(category)
                    
                    category_label = await get_translated_string_async("category_label", user_lang, category=translated_category)
                    select_or_save = await get_translated_string_async("select_subcategory_or_save", user_lang)
                    
                    await query.edit_message_text(
                        f"{category_label}\n\n"
                        f"{select_or_save}",
                        reply_markup=keyboard
                    )
                else:
                    # No subcategories, save directly and ask for notification confirmation
                    await db.update_product_category(product_id, category, None)
                    
                    # Translate category name
                    category_key = f"category_{category.lower()}"
                    translated_category = await get_translated_string_async(category_key, user_lang)
                    if translated_category == category_key:
                        translated_category = get_category_display_name(category)
                    
                    # Ask admin if they want to send notifications
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Yes, send notifications", callback_data=f"send_notif_yes|{product_id}")],
                        [InlineKeyboardButton("❌ No, skip notifications", callback_data=f"send_notif_no|{product_id}")]
                    ])
                    
                    await query.edit_message_text(
                        f"✅ Product #{product_id} categorized as:\n"
                        f"📂 {translated_category}\n\n"
                        f"📢 **Send notifications to subscribed users?**\n"
                        f"Choose whether to notify users about this product:",
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                    logger.info(f"Product {product_id} categorized as {category} by admin {update.effective_user.id}, awaiting notification decision")
            else:
                await query.answer("Invalid category data", show_alert=True)
        
        # Handle subcategory setting
        elif parts[0] == "setsubcat":
            if len(parts) >= 4:
                product_id = int(parts[1])
                category = parts[2]
                subcategory = parts[3]
                
                # Get user language preference
                user_id = update.effective_user.id
                user_lang = await db.get_user_language(user_id)
                
                # Translate category name
                category_key = f"category_{category.lower()}"
                translated_category = await get_translated_string_async(category_key, user_lang)
                if translated_category == category_key:
                    translated_category = get_category_display_name(category)
                
                # Translate strings
                translated_subcat = get_subcategory_display_name(subcategory, user_lang)
                category_label = await get_translated_string_async("category_label", user_lang, category=translated_category)
                subcategory_label = await get_translated_string_async("subcategory_label", user_lang, subcategory=translated_subcat)
                confirm_text = await get_translated_string_async("confirm_categorization", user_lang)
                
                # Confirmation message
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Confirm", callback_data=f"savecat|{product_id}|{category}|{subcategory}")],
                    [InlineKeyboardButton("🔙 Back", callback_data=f"setcat|{product_id}|{category}")]
                ])
                
                await query.edit_message_text(
                    f"{category_label}\n"
                    f"{subcategory_label}\n\n"
                    f"{confirm_text}",
                    reply_markup=keyboard
                )
            else:
                await query.answer("Invalid subcategory data", show_alert=True)
        
        # Handle save category
        elif parts[0] == "savecat":
            if len(parts) >= 3:
                product_id = int(parts[1])
                category = parts[2]
                subcategory = parts[3] if len(parts) > 3 and parts[3] else None
                
                # Get user language preference
                user_id = update.effective_user.id
                user_lang = await db.get_user_language(user_id)
                
                # Save categorization
                await db.update_product_category(product_id, category, subcategory)
                
                # Translate category name
                category_key = f"category_{category.lower()}"
                translated_category = await get_translated_string_async(category_key, user_lang)
                if translated_category == category_key:
                    translated_category = get_category_display_name(category)
                
                category_text = translated_category
                if subcategory:
                    translated_subcat = get_subcategory_display_name(subcategory, user_lang)
                    category_text += f" • {translated_subcat}"
                
                success_msg = await get_translated_string_async("product_categorized_successfully", user_lang, product_id=product_id)
                
                # Ask admin if they want to send notifications for this product
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Yes, send notifications", callback_data=f"send_notif_yes|{product_id}")],
                    [InlineKeyboardButton("❌ No, skip notifications", callback_data=f"send_notif_no|{product_id}")]
                ])
                
                await query.edit_message_text(
                    f"{success_msg}\n\n"
                    f"📂 {category_text}\n\n"
                    f"📢 **Send notifications to subscribed users?**\n"
                    f"Choose whether to notify users about this product:",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
                logger.info(f"Product {product_id} categorized as {category}/{subcategory} by admin {update.effective_user.id}, awaiting notification decision")
            else:
                await query.answer("Invalid save data", show_alert=True)
        
        # Handle notification confirmation - YES
        elif parts[0] == "send_notif_yes":
            if len(parts) >= 2:
                product_id = int(parts[1])
                
                # Check if user is admin
                if not is_admin(update.effective_user.id):
                    await query.answer("❌ Only admins can manage notifications.", show_alert=True)
                    return
                
                # Send notifications
                logger.info(f"Admin approved notifications for product {product_id}")
                await query.edit_message_text(
                    f"✅ **Notifications sent!**\n\n"
                    f"📢 Subscribed users are being notified about product #{product_id}.\n\n"
                    f"Notifications are being sent in batches to avoid spam limits.",
                    parse_mode="Markdown"
                )
                
                # Trigger notifications
                notification_service = NotificationService(db)
                await notification_service.notify_users_about_product(context, product_id)
                logger.info(f"Notification service triggered for product {product_id}")
            else:
                await query.answer("Invalid notification data", show_alert=True)
        
        # Handle notification confirmation - NO
        elif parts[0] == "send_notif_no":
            if len(parts) >= 2:
                product_id = int(parts[1])
                
                # Check if user is admin
                if not is_admin(update.effective_user.id):
                    await query.answer("❌ Only admins can manage notifications.", show_alert=True)
                    return
                
                # Skip notifications
                logger.info(f"Admin skipped notifications for product {product_id}")
                await query.edit_message_text(
                    f"✅ **Categorization complete**\n\n"
                    f"📂 Product #{product_id} has been categorized.\n"
                    f"🔕 No notifications will be sent for this product.",
                    parse_mode="Markdown"
                )
            else:
                await query.answer("Invalid notification data", show_alert=True)
        
        # Handle category browsing
        elif parts[0] == "category":
            if len(parts) >= 3:
                category = parts[1]
                page = int(parts[2])
                await handle_catalog_pagination(update, context, page, category)
            else:
                await query.answer("Invalid category data", show_alert=True)
        
        # Handle browse_category (show subcategory menu)
        elif parts[0] == "browse_category":
            if len(parts) >= 2:
                category = parts[1]
                from handlers.menu import show_subcategory_menu
                await show_subcategory_menu(update, context, category)
            else:
                await query.answer("Invalid category data", show_alert=True)
        
        # Handle subcategory browsing
        elif parts[0] == "subcategory":
            if len(parts) >= 4:
                category = parts[1]
                subcategory = parts[2]
                page = int(parts[3])
                await handle_catalog_pagination(update, context, page, category, subcategory)
            else:
                await query.answer("Invalid subcategory data", show_alert=True)
        
        # Handle pagination
        elif parts[0] == "page":
            if len(parts) == 3:  # page|catalog|2
                state_type = parts[1]
                page = int(parts[2])
                if state_type == "catalog":
                    await handle_catalog_pagination(update, context, page)
                else:
                    await query.answer("Invalid pagination", show_alert=True)
            elif len(parts) == 4:  # page|search|query|2
                state_type = parts[1]
                search_query = parts[2]
                page = int(parts[3])
                if state_type == "search":
                    await handle_search_pagination(update, context, search_query, page)
                else:
                    await query.answer("Invalid pagination", show_alert=True)
            else:
                await query.answer("Invalid callback data", show_alert=True)
        
        # Handle menu
        elif parts[0] == "menu":
            page = int(parts[1]) if len(parts) > 1 else 1
            await show_catalog_page(update, context, page)
        
        # Handle product view
        elif parts[0] == "product":
            await handle_product_callback(update, context)
        
        # Handle delete
        elif parts[0] == "delete":
            product_id = int(parts[1])
            await delete_product(update, context, product_id)
        
        # Handle recategorize
        elif parts[0] == "recategorize":
            product_id = int(parts[1])
            
            # Check if user is admin
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can recategorize products.", show_alert=True)
                return
            
            # Show category selection menu (same as initial categorization)
            keyboard_buttons = []
            
            for category in get_all_categories():
                display_name = get_category_display_name(category)
                keyboard_buttons.append([
                    InlineKeyboardButton(display_name, callback_data=f"setcat|{product_id}|{category}")
                ])
            
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
            
            await query.edit_message_text(
                f"📂 **Recategorize Product #{product_id}**\n\n"
                f"Please select a new category:",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        
        # Handle user notification toggle (admin only)
        elif parts[0] == "toggle_notif":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can manage notifications.", show_alert=True)
                return
            
            if len(parts) >= 2:
                target_user_id = int(parts[1])
                page = int(parts[2]) if len(parts) > 2 else 1
                bot_username = parts[3] if len(parts) > 3 else None
                
                # Get current status
                is_subscribed = await db.is_user_subscribed(target_user_id)
                
                # Show confirmation prompt
                action_text = "disable" if is_subscribed else "enable"
                await query.answer(
                    f"⚠️ Are you sure you want to {action_text} notifications for this user?",
                    show_alert=True
                )
                
                # Replace with confirmation buttons
                try:
                    bot_param = f"|{bot_username}" if bot_username else ""
                    confirmation_keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton(f"✅ Yes, {action_text.title()} Notifications", callback_data=f"confirm_toggle_notif|{target_user_id}|{page}|{int(is_subscribed)}{bot_param}")],
                        [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_toggle_notif|{page}{bot_param}")]
                    ])
                    await query.edit_message_reply_markup(reply_markup=confirmation_keyboard)
                except Exception as e:
                    logger.error(f"Error showing notification toggle confirmation: {e}")
                    # Fallback to just toggling
                    await db.set_user_notifications(target_user_id, not is_subscribed)
                    await show_users_page(update, context, page, bot_username)
                    status_text = "disabled" if is_subscribed else "enabled"
                    await query.answer(f"✅ Notifications {status_text} for user", show_alert=False)
                    logger.info(f"Admin {update.effective_user.id} toggled notifications for user {target_user_id} to {not is_subscribed}")
            else:
                await query.answer("Invalid data", show_alert=True)
        
        # Handle confirm toggle notifications
        elif parts[0] == "confirm_toggle_notif":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can manage notifications.", show_alert=True)
                return
            
            if len(parts) >= 3:
                target_user_id = int(parts[1])
                page = int(parts[2])
                was_subscribed = bool(int(parts[3])) if len(parts) > 3 else False
                bot_username = parts[4] if len(parts) > 4 else None
                
                # Toggle notifications
                await db.set_user_notifications(target_user_id, not was_subscribed)
                
                # Refresh the users page
                await show_users_page(update, context, page, bot_username)
                
                status_text = "disabled" if was_subscribed else "enabled"
                await query.answer(f"✅ Notifications {status_text} for user successfully", show_alert=True)
                logger.info(f"Admin {update.effective_user.id} toggled notifications for user {target_user_id} to {not was_subscribed}")
            else:
                await query.answer("Invalid data", show_alert=True)
        
        # Handle cancel toggle notifications
        elif parts[0] == "cancel_toggle_notif":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can manage notifications.", show_alert=True)
                return
            
            if len(parts) >= 1:
                page = int(parts[1]) if len(parts) > 1 else 1
                bot_username = parts[2] if len(parts) > 2 else None
                
                # Refresh the users page
                await show_users_page(update, context, page, bot_username)
                
                await query.answer("✅ Toggle cancelled", show_alert=False)
            else:
                await query.answer("Invalid data", show_alert=True)
        
        # Handle users pagination
        elif parts[0] == "users_page":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can view users.", show_alert=True)
                return
            
            page = int(parts[1]) if len(parts) > 1 else 1
            bot_username = parts[2] if len(parts) > 2 else None
            await show_users_page(update, context, page, bot_username)
        
        # Handle viewusers callback (view users for specific bot)
        elif parts[0] == "viewusers":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can view users.", show_alert=True)
                return
            
            if len(parts) >= 2:
                bot_username = parts[1]
                page = int(parts[2]) if len(parts) > 2 else 1
                
                await query.answer()
                await show_users_page(update, context, page, bot_username)
            else:
                await query.answer("Invalid data", show_alert=True)
        
        # Handle back to bot list from users view
        elif callback_data == "users_back_to_bots":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can view users.", show_alert=True)
                return
            
            await query.answer()
            
            # Build bot selection menu using helper function
            keyboard, message, message_no_md, has_bots, bot_list = await build_users_bot_selection_menu()
            
            if not has_bots:
                await query.edit_message_text("No bot usernames found.")
                return
            
            try:
                await query.edit_message_text(
                    message,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            except BadRequest as e:
                logger.warning(f"Markdown parse error in users_back_to_bots, retrying without parse_mode: {e}")
                await query.edit_message_text(
                    message_no_md,
                    reply_markup=keyboard
                )
        
        # Handle notification toggle (subscribe/unsubscribe)
        elif callback_data == "toggle_notifications":
            user_id = update.effective_user.id
            is_subscribed = await db.is_user_subscribed(user_id)
            
            if is_subscribed:
                # Show confirmation before unsubscribing
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Yes, Unsubscribe", callback_data="confirm_unsubscribe")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_unsubscribe")]
                ])
                
                await query.edit_message_reply_markup(reply_markup=keyboard)
                await query.answer(
                    "⚠️ Are you sure you want to unsubscribe from notifications?",
                    show_alert=True
                )
            else:
                # Re-subscribe
                await db.set_user_notifications(user_id, True)
                
                # Replace with unsubscribe option and keep View Catalog visible
                try:
                    unsubscribe_keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("📋 View Catalog", callback_data="categories")],
                        [InlineKeyboardButton("🔕 Unsubscribe", callback_data="toggle_notifications")]
                    ])
                    await query.edit_message_reply_markup(reply_markup=unsubscribe_keyboard)
                except Exception as e:
                    logger.debug(f"Could not edit message markup: {e}")
                    pass  # Message may not have markup to edit
                
                await query.answer("✅ You have been subscribed to notifications successfully! You will now receive updates about new products.", show_alert=True)
                logger.info(f"User {user_id} subscribed via button")
        
        # Handle confirmation of unsubscribe
        elif callback_data == "confirm_unsubscribe":
            user_id = update.effective_user.id
            await db.set_user_notifications(user_id, False)
            
            # Replace buttons with a resubscribe option and keep View Catalog visible
            try:
                resubscribe_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 View Catalog", callback_data="categories")],
                    [InlineKeyboardButton("🔔 Resubscribe to Notifications", callback_data="toggle_notifications")]
                ])
                await query.edit_message_reply_markup(reply_markup=resubscribe_keyboard)
            except Exception as e:
                logger.debug(f"Could not edit message markup: {e}")
                pass  # Message may not have markup to edit
            
            await query.answer("✅ You have been unsubscribed from notifications successfully. You can resubscribe anytime using the 'Resubscribe to Notifications' button or /subscribe command.", show_alert=True)
            logger.info(f"User {user_id} unsubscribed via button")
        
        # Handle cancellation of unsubscribe
        elif callback_data == "cancel_unsubscribe":
            # Restore original buttons with View Catalog and Unsubscribe
            try:
                original_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 View Catalog", callback_data="categories")],
                    [InlineKeyboardButton("🔕 Unsubscribe", callback_data="toggle_notifications")]
                ])
                await query.edit_message_reply_markup(reply_markup=original_keyboard)
            except Exception as e:
                logger.debug(f"Could not edit message markup: {e}")
                pass  # Message may not have markup to edit
            
            await query.answer("✅ Unsubscribe cancelled", show_alert=False)
        
        # Handle legacy unsubscribe button (for backwards compatibility)
        elif callback_data == "unsubscribe_notifications":
            user_id = update.effective_user.id
            # Show confirmation before unsubscribing
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Yes, Unsubscribe", callback_data="confirm_unsubscribe")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_unsubscribe")]
            ])
            
            await query.edit_message_reply_markup(reply_markup=keyboard)
            await query.answer(
                "⚠️ Are you sure you want to unsubscribe from notifications?",
                show_alert=True
            )
        
        # Handle block user
        elif parts[0] == "block_user":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can block users.", show_alert=True)
                return
            
            if len(parts) >= 2:
                target_user_id = int(parts[1])
                page = int(parts[2]) if len(parts) > 2 else 1
                bot_username = parts[3] if len(parts) > 3 else None
                
                # Prevent blocking admins
                if is_admin(target_user_id):
                    await query.answer("❌ Cannot block an admin user.", show_alert=True)
                    return
                
                # Show confirmation prompt
                await query.answer(
                    "⚠️ Are you sure you want to block this user?",
                    show_alert=True
                )
                
                # Replace the block button with confirmation buttons
                try:
                    # Update the message to show confirmation buttons
                    bot_param = f"|{bot_username}" if bot_username else ""
                    confirmation_keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Yes, Block User", callback_data=f"confirm_block|{target_user_id}|{page}{bot_param}")],
                        [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_block|{page}{bot_param}")]
                    ])
                    await query.edit_message_reply_markup(reply_markup=confirmation_keyboard)
                except Exception as e:
                    logger.error(f"Error showing block confirmation: {e}")
                    # Fallback to just blocking
                    await db.block_user(target_user_id)
                    await show_users_page(update, context, page, bot_username)
                    await query.answer("🚫 User blocked", show_alert=False)
                    logger.info(f"Admin {update.effective_user.id} blocked user {target_user_id}")
            else:
                await query.answer("Invalid data", show_alert=True)
        
        # Handle confirm block user
        elif parts[0] == "confirm_block":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can block users.", show_alert=True)
                return
            
            if len(parts) >= 2:
                target_user_id = int(parts[1])
                page = int(parts[2]) if len(parts) > 2 else 1
                bot_username = parts[3] if len(parts) > 3 else None
                
                # Block the user
                await db.block_user(target_user_id)
                
                # Refresh the users page
                await show_users_page(update, context, page, bot_username)
                
                await query.answer("✅ User has been blocked successfully", show_alert=True)
                logger.info(f"Admin {update.effective_user.id} blocked user {target_user_id}")
            else:
                await query.answer("Invalid data", show_alert=True)
        
        # Handle cancel block user
        elif parts[0] == "cancel_block":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can manage users.", show_alert=True)
                return
            
            if len(parts) >= 1:
                page = int(parts[1]) if len(parts) > 1 else 1
                bot_username = parts[2] if len(parts) > 2 else None
                
                # Refresh the users page
                await show_users_page(update, context, page, bot_username)
                
                await query.answer("✅ Block cancelled", show_alert=False)
            else:
                await query.answer("Invalid data", show_alert=True)
        
        # Handle unblock user
        elif parts[0] == "unblock_user":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can unblock users.", show_alert=True)
                return
            
            if len(parts) >= 2:
                target_user_id = int(parts[1])
                page = int(parts[2]) if len(parts) > 2 else 1
                bot_username = parts[3] if len(parts) > 3 else None
                
                # Unblock the user
                await db.unblock_user(target_user_id)
                
                # Refresh the users page
                await show_users_page(update, context, page, bot_username)
                
                await query.answer("✅ User has been unblocked successfully", show_alert=True)
                logger.info(f"Admin {update.effective_user.id} unblocked user {target_user_id}")
            else:
                await query.answer("Invalid data", show_alert=True)
        
        # Handle view bot users
        elif parts[0] == "viewbotusers":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can view users.", show_alert=True)
                return
            
            if len(parts) >= 2:
                bot_username = parts[1]
                page = int(parts[2]) if len(parts) > 2 else 1
                
                from handlers.admin import show_bot_users_page
                await show_bot_users_page(update, context, bot_username, page)
            else:
                await query.answer("Invalid data", show_alert=True)
        
        # Handle back to bot list
        elif parts[0] == "backto_botlist":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can view users.", show_alert=True)
                return
            
            # Display the bot list inline (duplicates botusers_command logic for callback context)
            await query.answer()
            
            bot_usernames = await db.get_bot_usernames()
            keyboard_buttons = []
            
            for bot_username in bot_usernames:
                count = await db.get_users_count_by_bot(bot_username)
                display_text = f"@{bot_username} ({count} users)"
                keyboard_buttons.append([
                    InlineKeyboardButton(display_text, callback_data=f"viewbotusers|{bot_username}|1")
                ])
            
            untracked_count = await db.get_users_count_by_bot("_untracked_")
            if untracked_count > 0:
                keyboard_buttons.append([
                    InlineKeyboardButton(f"Untracked Users ({untracked_count})", callback_data="viewbotusers|_untracked_|1")
                ])
            
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
            total_users = await db.get_users_count_by_bot()
            
            await query.edit_message_text(
                f"📊 **Bot Users by Instance**\n\n"
                f"Total Users: {total_users}\n"
                f"Bot Instances: {len(bot_usernames)}\n\n"
                f"Select a bot to view its users:",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        
        # Handle delete single user
        elif parts[0] == "deleteuser":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can delete users.", show_alert=True)
                return
            
            if len(parts) >= 4:
                target_user_id = int(parts[1])
                bot_username = parts[2]
                page = int(parts[3])
                
                # Get user info before deletion
                user = await db.get_user_by_id(target_user_id)
                
                if not user:
                    await query.answer("User not found", show_alert=True)
                    return
                
                username = user.get("username", "N/A")
                first_name = user.get("first_name", "Unknown")
                
                # Create confirmation keyboard
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_deleteuser|{target_user_id}|{bot_username}|{page}")],
                    [InlineKeyboardButton("❌ Cancel", callback_data=f"viewbotusers|{bot_username}|{page}")]
                ])
                
                await query.edit_message_text(
                    f"⚠️ **Delete User Confirmation**\n\n"
                    f"User ID: `{target_user_id}`\n"
                    f"Username: @{username if username != 'N/A' else 'none'}\n"
                    f"Name: {first_name}\n\n"
                    f"This will completely remove the user from the database.\n"
                    f"The user can restart the bot to be re-added as a fresh user.\n\n"
                    f"Are you sure?",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
                await query.answer()
            else:
                await query.answer("Invalid data", show_alert=True)
        
        # Handle confirm delete user
        elif parts[0] == "confirm_deleteuser":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can delete users.", show_alert=True)
                return
            
            if len(parts) >= 2:
                target_user_id = int(parts[1])
                bot_username = parts[2] if len(parts) > 2 else None
                page = int(parts[3]) if len(parts) > 3 else 1
                
                # Delete the user
                await db.delete_user(target_user_id)
                
                await query.answer("✅ User deleted successfully", show_alert=True)
                logger.info(f"Admin {update.effective_user.id} deleted user {target_user_id}")
                
                # If we have bot_username, go back to that page, otherwise close
                if bot_username:
                    from handlers.admin import show_bot_users_page
                    await show_bot_users_page(update, context, bot_username, page)
                else:
                    await query.edit_message_text(
                        f"✅ User `{target_user_id}` has been deleted successfully.",
                        parse_mode="Markdown"
                    )
            else:
                await query.answer("Invalid data", show_alert=True)
        
        # Handle delete all users from a bot
        elif parts[0] == "deleteallbot":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can delete users.", show_alert=True)
                return
            
            if len(parts) >= 2:
                bot_username = parts[1]
                
                # Get count
                if bot_username == "_untracked_":
                    count = await db.get_users_count_by_bot("_untracked_")
                    display_name = "Untracked Users"
                else:
                    count = await db.get_users_count_by_bot(bot_username)
                    display_name = f"@{bot_username}"
                
                # Create confirmation keyboard
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Yes, Delete All", callback_data=f"confirm_deleteallbot|{bot_username}")],
                    [InlineKeyboardButton("❌ Cancel", callback_data=f"viewbotusers|{bot_username}|1")]
                ])
                
                await query.edit_message_text(
                    f"⚠️ **BULK DELETE WARNING**\n\n"
                    f"You are about to delete **{count}** user(s) from {display_name}.\n\n"
                    f"This will completely remove all these users from the database.\n"
                    f"Users can restart the bot to be re-added as fresh users.\n\n"
                    f"This action cannot be undone!\n\n"
                    f"Are you sure?",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
                await query.answer()
            else:
                await query.answer("Invalid data", show_alert=True)
        
        # Handle confirm delete all users from bot
        elif parts[0] == "confirm_deleteallbot":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can delete users.", show_alert=True)
                return
            
            if len(parts) >= 2:
                bot_username = parts[1]
                
                # Delete all users from this bot
                deleted_count = await db.delete_users_by_bot(bot_username)
                
                await query.answer(f"✅ Deleted {deleted_count} users", show_alert=True)
                logger.info(f"Admin {update.effective_user.id} deleted {deleted_count} users from bot {bot_username}")
                
                # Go back to bot list
                bot_usernames = await db.get_bot_usernames()
                keyboard_buttons = []
                
                for bot_username_item in bot_usernames:
                    count = await db.get_users_count_by_bot(bot_username_item)
                    display_text = f"@{bot_username_item} ({count} users)"
                    keyboard_buttons.append([
                        InlineKeyboardButton(display_text, callback_data=f"viewbotusers|{bot_username_item}|1")
                    ])
                
                untracked_count = await db.get_users_count_by_bot("_untracked_")
                if untracked_count > 0:
                    keyboard_buttons.append([
                        InlineKeyboardButton(f"Untracked Users ({untracked_count})", callback_data="viewbotusers|_untracked_|1")
                    ])
                
                keyboard = InlineKeyboardMarkup(keyboard_buttons)
                total_users = await db.get_users_count_by_bot()
                
                await query.edit_message_text(
                    f"📊 **Bot Users by Instance**\n\n"
                    f"Total Users: {total_users}\n"
                    f"Bot Instances: {len(bot_usernames)}\n\n"
                    f"✅ Deleted {deleted_count} users successfully.\n\n"
                    f"Select a bot to view its users:",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            else:
                await query.answer("Invalid data", show_alert=True)
        
        # Handle confirm prune bots
        elif parts[0] == "confirm_prunebots":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can prune bots.", show_alert=True)
                return
            
            # Get active bot usernames from webhook server
            from webhook_server import get_bot_usernames
            active_bots = get_bot_usernames()
            
            if not active_bots:
                await query.answer("⚠️ No active bots found!", show_alert=True)
                return
            
            # Perform pruning
            stats = await db.prune_inactive_bot_users(active_bots, dry_run=False)
            
            if stats['users'] == 0:
                await query.edit_message_text(
                    "✅ **No Changes Made**\n\n"
                    "No users found associated with inactive bots.",
                    parse_mode="Markdown"
                )
            else:
                await query.edit_message_text(
                    f"✅ **Pruning Complete**\n\n"
                    f"Deleted:\n"
                    f"• Users: {stats['users']}\n"
                    f"• Products: {stats['products']}\n"
                    f"• Pending Notifications: {stats['notifications']}\n"
                    f"• Custom Messages: {stats['custom_messages']}\n\n"
                    f"Remaining active bots ({len(active_bots)}):\n"
                    f"• " + "\n• ".join(f"@{bot}" for bot in sorted(active_bots)),
                    parse_mode="Markdown"
                )
                logger.info(f"Admin {update.effective_user.id} pruned inactive bot users: {stats}")
            
            await query.answer("✅ Pruning complete", show_alert=False)
        
        # Handle cancel prune bots
        elif parts[0] == "cancel_prunebots":
            await query.edit_message_text(
                "❌ **Pruning Cancelled**\n\n"
                "No changes were made.",
                parse_mode="Markdown"
            )
            await query.answer("Cancelled", show_alert=False)
        
        else:
            await query.answer("Unknown action", show_alert=True)
            
    except ValueError as e:
        logger.error(f"Invalid callback data format: {callback_data}, error: {e}")
        await query.answer("Invalid action", show_alert=True)
    except BadRequest as e:
        # Handle "Message is not modified" error silently
        if "message is not modified" in str(e).lower():
            logger.debug(f"Message not modified for callback {callback_data}: {e}")
            await query.answer()  # Silent acknowledgment
        else:
            logger.error(f"BadRequest error handling callback query: {e}", exc_info=True)
            await query.answer("Unable to process this action. Please try again.", show_alert=True)
    except Exception as e:
        logger.error(f"Error handling callback query: {e}", exc_info=True)
        await query.answer("An error occurred", show_alert=True)


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
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ An unexpected error occurred. Please try again later."
            )
        except Exception as e:
            logger.debug(f"Could not send error message to user: {e}")
            pass


async def cleanup_task(context: ContextTypes.DEFAULT_TYPE):
    """Periodic cleanup task for old pagination states.
    Only runs on the primary bot instance to avoid duplicate cleanup.
    """
    # Check if this is the primary bot instance
    if not _is_primary_instance(context):
        logger.info("Cleanup task disabled - not primary instance")
        return
    
    while True:
        try:
            await asyncio.sleep(600)  # Run every 10 minutes
            await db.cleanup_old_pagination_states(minutes=10)
            logger.debug("Cleaned up old pagination states")
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
    # Define user commands (available to all users)
    # These appear in the command menu for all users
    user_commands = [
        BotCommand("start", "Start the bot and select language"),
        BotCommand("menu", "Browse the product catalog"),
    ]
    
    # Define admin commands (available to admins only)
    # These appear in the command menu ONLY for admin users
    admin_commands = user_commands + [
        BotCommand("users", "View and manage bot users"),
        BotCommand("botusers", "View users grouped by bot instance"),
        BotCommand("send", "Send a message to a specific user"),
        BotCommand("broadcast", "Send a message to all users"),
        BotCommand("setcontact", "Set order contact username"),
        BotCommand("recategorize", "Categorize uncategorized products"),
        BotCommand("clearcache", "Clear file ID cache"),
        BotCommand("prunebots", "Prune users from inactive bots"),
        BotCommand("nuke", "Delete all products (use with caution)"),
    ]
    
    # Set default commands for all users
    # This updates the global command menu seen by regular users
    await bot.set_my_commands(user_commands)
    logger.info(f"Set user commands globally: {[cmd.command for cmd in user_commands]}")
    
    # Set admin-specific commands for each admin user
    # This overrides the global commands for admin chat scopes
    admin_ids = get_admin_ids()
    if not admin_ids:
        logger.warning("No admin IDs configured - admin commands will not be set")
    
    for admin_id in admin_ids:
        try:
            await bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=admin_id)
            )
            logger.info(f"Set admin commands for user {admin_id}: {[cmd.command for cmd in admin_commands]}")
        except Exception as e:
            logger.warning(f"Could not set admin commands for {admin_id}: {e}")
    
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

