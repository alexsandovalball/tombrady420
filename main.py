"""
Main entry point for the Telegram media catalog bot.
"""
import os
import logging
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)
from telegram.error import TelegramError

from database import Database
from handlers.start import start_command
from handlers.menu import menu_command, show_catalog_page, handle_catalog_pagination
from handlers.search import handle_search, show_search_results, handle_search_pagination
from handlers.product_view import show_product, handle_product_callback
from handlers.admin import stats_command, delete_product, nuke_command
from utils.helpers import (
    get_file_id_and_type,
    has_media,
    get_channel_id,
    get_channel_username
)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize database
db = Database()

# Rate limiting (simple in-memory store)
user_last_message = {}


async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle new posts in the monitored channel.
    Detects media posts and saves them as products.
    Supports photo albums (media groups).
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
            # For media groups, we'll store the first photo as the main product
            # and store all file_ids in the caption or as a separate field
            # For simplicity, we store the first media item
            file_id, file_type = get_file_id_and_type(update)
            
            if not file_id:
                logger.warning(f"Could not extract file_id from media group message {message.message_id}")
                return
            
            # Get caption (usually on the first message of the group)
            caption = message.caption or message.text or ""
            
            # Check if we already processed this media group
            existing = await db.get_product_by_message(message.message_id, message.chat.id)
            if existing:
                logger.debug(f"Media group already processed: {message.media_group_id}")
                return
            
            # Save the first media item as the product
            # Note: In a full implementation, you might want to store all media_group items
            # For now, we store the first one and note it's part of a group in the caption
            product_id = await db.add_product(
                file_id=file_id,
                file_type=file_type,
                caption=caption or f"[Photo Album - {message.media_group_id}]",
                message_id=message.message_id,
                chat_id=message.chat.id
            )
            
            if product_id > 0:
                logger.info(
                    f"New product saved from media group: ID={product_id}, "
                    f"message_id={message.message_id}, "
                    f"media_group_id={message.media_group_id}, "
                    f"type={file_type}"
                )
            return
        
        # Handle single media messages
        file_id, file_type = get_file_id_and_type(update)
        
        if not file_id:
            logger.warning(f"Could not extract file_id from message {message.message_id}")
            return
        
        # Get caption
        caption = message.caption or message.text or ""
        
        # Save product to database
        product_id = await db.add_product(
            file_id=file_id,
            file_type=file_type,
            caption=caption,
            message_id=message.message_id,
            chat_id=message.chat.id
        )
        
        if product_id > 0:
            logger.info(
                f"New product saved: ID={product_id}, "
                f"message_id={message.message_id}, "
                f"type={file_type}"
            )
        else:
            logger.debug(f"Product already exists for message {message.message_id}")
            
    except Exception as e:
        logger.error(f"Error handling channel post: {e}", exc_info=True)


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback queries (buttons)."""
    query = update.callback_query
    await query.answer()  # Always answer callback to prevent loading spinner
    
    callback_data = query.data
    
    # Handle noop (page indicator button)
    if callback_data == "noop":
        return
    
    try:
        parts = callback_data.split("|")
        
        # Handle pagination
        if parts[0] == "page":
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
    
    # Rate limiting (simple)
    user_id = update.effective_user.id
    current_time = datetime.now().timestamp()
    
    if user_id in user_last_message:
        time_diff = current_time - user_last_message[user_id]
        if time_diff < 1:  # 1 second between messages
            await update.message.reply_text(
                "⏳ Please wait a moment before sending another message."
            )
            return
    
    user_last_message[user_id] = current_time
    
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
    
    # Start cleanup task
    asyncio.create_task(cleanup_task(application))


def main():
    """Main function to start the bot."""
    # Check if we should use webhook mode
    use_webhook = os.getenv("USE_WEBHOOK", "false").lower() == "true"
    
    if use_webhook:
        logger.info("Webhook mode detected. Please run webhook_server.py instead.")
        logger.info("Example: python webhook_server.py")
        logger.info("Or use: uvicorn webhook_server:app --host 0.0.0.0 --port 8000")
        return
    
    # Get bot token
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        logger.error("BOT_TOKEN not found in environment variables!")
        return
    
    # Create application
    application = Application.builder().token(bot_token).post_init(post_init).build()
    
    # Register handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("nuke", nuke_command))
    
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

