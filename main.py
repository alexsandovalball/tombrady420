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
from telegram.error import TelegramError

from configs.config import Config, ConfigError
from database import Database
from handlers.start import start_command, subscribe_command, unsubscribe_command
from handlers.menu import menu_command, show_catalog_page, handle_catalog_pagination
from handlers.search import handle_search, show_search_results, handle_search_pagination
from handlers.product_view import show_product, handle_product_callback
from handlers.language import language_command, handle_language_callback
from handlers.admin import (
    delete_product, nuke_command, recategorize_command, 
    users_command, show_users_page,
    block_command, unblock_command, send_command, broadcast_command,
    setcontact_command, handle_setcontact_input
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


async def notify_admins_for_categorization(context: ContextTypes.DEFAULT_TYPE, product_id: int):
    """Send categorization request to all admins for a new product."""
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
                # Send the product media with categorization keyboard
                from utils.helpers import send_media_message
                
                await context.bot.send_message(
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
    """Process collected media group messages after a short delay."""
    await asyncio.sleep(0.5)  # Wait for all messages in the group to arrive
    
    if media_group_id not in media_group_messages:
        return
    
    messages = media_group_messages.pop(media_group_id, [])
    media_group_timers.pop(media_group_id, None)
    
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
    
    # Collect all file IDs
    file_ids = []
    file_types = []
    
    for msg in messages:
        # Create a temporary update object for each message
        temp_update = Update(0, channel_post=msg)
        file_id, file_type = get_file_id_and_type(temp_update)
        if file_id:
            file_ids.append(file_id)
            file_types.append(file_type)
    
    if not file_ids:
        logger.warning(f"No file IDs extracted from media group {media_group_id}")
        return
    
    # Check if this media group already has a product
    existing_product_id = await db.get_or_create_media_group_product(media_group_id, chat_id)
    
    if existing_product_id:
        # Update existing product with new file IDs
        additional_files = json.dumps(list(zip(file_ids[1:], file_types[1:])))
        await db.update_product_media(existing_product_id, additional_files)
        logger.info(f"Updated media group product {existing_product_id} with {len(file_ids)} files")
        return
    
    # Create new product with first file as primary (NO automatic categorization)
    additional_files = json.dumps(list(zip(file_ids[1:], file_types[1:]))) if len(file_ids) > 1 else None
    
    product_id = await db.add_product(
        file_id=file_ids[0],
        file_type=file_types[0],
        caption=caption,
        message_id=first_message.message_id,
        chat_id=chat_id,
        media_group_id=media_group_id,
        additional_file_ids=additional_files,
        category=None,  # No automatic categorization
        subcategory=None
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
            subcategory=None
        )
        
        if product_id > 0:
            logger.info(
                f"New product saved: ID={product_id}, "
                f"message_id={message.message_id}, "
                f"type={file_type}"
            )
            
            # Notify admins for categorization
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
    
    await query.answer()  # Answer callback to prevent loading spinner
    
    callback_data = query.data
    
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
            from utils.notifications import NotificationService
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
        
        # Send status message
        await query.edit_message_text(
            "📡 **Broadcasting message...**\n\n"
            "Please wait while the message is queued for all users.",
            parse_mode="Markdown"
        )
        
        # Broadcast using notification service
        from utils.notifications import NotificationService
        notification_service = NotificationService(db)
        
        queued_count = await notification_service.broadcast_custom_message(
            context,
            message_text,
            exclude_blocked=True
        )
        
        await query.edit_message_text(
            f"✅ **Broadcast Complete**\n\n"
            f"📨 Messages queued: {queued_count}\n"
            f"📝 Message: {message_text[:100]}{'...' if len(message_text) > 100 else ''}\n\n"
            f"Messages will be delivered with rate-limiting intervals.",
            parse_mode="Markdown"
        )
        logger.info(f"Admin {user_id} broadcast message to {queued_count} users")
        
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
                    # No subcategories, save directly
                    await db.update_product_category(product_id, category, None)
                    await query.edit_message_text(
                        f"✅ Product #{product_id} categorized as:\n"
                        f"📂 {get_category_display_name(category)}"
                    )
                    logger.info(f"Product {product_id} categorized as {category} by admin {update.effective_user.id}")
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
                
                await query.edit_message_text(
                    f"{success_msg}\n\n"
                    f"📂 {category_text}"
                )
                logger.info(f"Product {product_id} categorized as {category}/{subcategory} by admin {update.effective_user.id}")
                
                # Notify subscribed users about the new categorized product
                notification_service = NotificationService(db)
                await notification_service.notify_users_about_product(context, product_id)
            else:
                await query.answer("Invalid save data", show_alert=True)
        
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
                    confirmation_keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton(f"✅ Yes, {action_text.title()} Notifications", callback_data=f"confirm_toggle_notif|{target_user_id}|{page}|{int(is_subscribed)}")],
                        [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_toggle_notif|{page}")]
                    ])
                    await query.edit_message_reply_markup(reply_markup=confirmation_keyboard)
                except Exception as e:
                    logger.error(f"Error showing notification toggle confirmation: {e}")
                    # Fallback to just toggling
                    await db.set_user_notifications(target_user_id, not is_subscribed)
                    await show_users_page(update, context, page)
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
                
                # Toggle notifications
                await db.set_user_notifications(target_user_id, not was_subscribed)
                
                # Refresh the users page
                await show_users_page(update, context, page)
                
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
                
                # Refresh the users page
                await show_users_page(update, context, page)
                
                await query.answer("✅ Toggle cancelled", show_alert=False)
            else:
                await query.answer("Invalid data", show_alert=True)
        
        # Handle users pagination
        elif parts[0] == "users_page":
            if not is_admin(update.effective_user.id):
                await query.answer("❌ Only admins can view users.", show_alert=True)
                return
            
            page = int(parts[1]) if len(parts) > 1 else 1
            await show_users_page(update, context, page)
        
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
                except:
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
            except:
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
            except:
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
                    confirmation_keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Yes, Block User", callback_data=f"confirm_block|{target_user_id}|{page}")],
                        [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_block|{page}")]
                    ])
                    await query.edit_message_reply_markup(reply_markup=confirmation_keyboard)
                except Exception as e:
                    logger.error(f"Error showing block confirmation: {e}")
                    # Fallback to just blocking
                    await db.block_user(target_user_id)
                    await show_users_page(update, context, page)
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
                
                # Block the user
                await db.block_user(target_user_id)
                
                # Refresh the users page
                await show_users_page(update, context, page)
                
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
                
                # Refresh the users page
                await show_users_page(update, context, page)
                
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
                
                # Unblock the user
                await db.unblock_user(target_user_id)
                
                # Refresh the users page
                await show_users_page(update, context, page)
                
                await query.answer("✅ User has been unblocked successfully", show_alert=True)
                logger.info(f"Admin {update.effective_user.id} unblocked user {target_user_id}")
            else:
                await query.answer("Invalid data", show_alert=True)
        
        else:
            await query.answer("Unknown action", show_alert=True)
            
    except ValueError as e:
        logger.error(f"Invalid callback data format: {callback_data}, error: {e}")
        await query.answer("Invalid action", show_alert=True)
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
    await db.track_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
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
    logger.error(f"Update {update} caused error {context.error}")
    
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
        except:
            pass


async def cleanup_task(context: ContextTypes.DEFAULT_TYPE):
    """Periodic cleanup task for old pagination states."""
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
    """Set up bot commands that appear in the Telegram command menu."""
    # Define user commands (available to all users)
    # Removed: language, subscribe, unsubscribe per requirements
    user_commands = [
        BotCommand("start", "Start the bot and select language"),
        BotCommand("menu", "Browse the product catalog"),
    ]
    
    # Define admin commands (available to admins only)
    # Removed: block, unblock per requirements
    admin_commands = user_commands + [
        BotCommand("users", "View and manage bot users"),
        BotCommand("send", "Send a message to a specific user"),
        BotCommand("broadcast", "Send a message to all users"),
        BotCommand("setcontact", "Set order contact username"),
        BotCommand("recategorize", "Categorize uncategorized products"),
        BotCommand("nuke", "Delete all products (use with caution)"),
    ]
    
    # Set default commands for all users
    await bot.set_my_commands(user_commands)
    
    # Set admin commands for each admin
    admin_ids = get_admin_ids()
    for admin_id in admin_ids:
        try:
            await bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=admin_id)
            )
            logger.info(f"Set admin commands for user {admin_id}")
        except Exception as e:
            logger.warning(f"Could not set admin commands for {admin_id}: {e}")
    
    logger.info("Bot commands configured successfully")


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
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("block", block_command))
    application.add_handler(CommandHandler("unblock", unblock_command))
    application.add_handler(CommandHandler("send", send_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("setcontact", setcontact_command))
    
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

