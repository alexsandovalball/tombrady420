"""
Main entry point for the Telegram media catalog bot.
"""
import os
import logging
import re
import asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

from database import Database
from handlers.start import start_command
from handlers.menu import menu_command, handle_catalog_pagination
from handlers.search import handle_search, handle_search_pagination
from handlers.admin import stats_command, delete_product, nuke_command
from handlers.product_view import show_product, handle_product_callback
from utils.helpers import get_file_id_and_type, has_media, get_channel_id, get_channel_username

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

db = Database()
user_last_message = {}


async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new posts in the monitored channel and save each media item with media_group_id.

    Improvements:
    - Prefer message.caption for media messages.
    - If a media item belongs to a media_group and has no caption, attempt to reuse a non-empty
      caption already saved for that media_group + chat_id (covers out-of-order album updates).
    - Log caption and related fields for easier debugging.
    """
    try:
        message = update.channel_post
        if not message:
            return

        chat_id = message.chat.id
        msg_id = message.message_id
        media_group_id = getattr(message, "media_group_id", None)

        # Debug logging to inspect what Telegram actually sent
        logger.debug(
            "Channel post received: chat_id=%s message_id=%s media_group_id=%s",
            chat_id,
            msg_id,
            media_group_id,
        )
        logger.debug("caption: %r", getattr(message, "caption", None))
        logger.debug("caption_entities: %r", getattr(message, "caption_entities", None))
        logger.debug("text: %r", getattr(message, "text", None))

        # Skip if this message was explicitly ignored
        if await db.is_message_ignored(msg_id, chat_id):
            logger.debug("Message ignored: %s/%s", chat_id, msg_id)
            return

        # Skip non-media messages
        if not has_media(update):
            logger.debug("No media in message %s/%s", chat_id, msg_id)
            return

        # Extract file id and type
        file_id, file_type = get_file_id_and_type(update)
        if not file_id:
            logger.debug("No file_id found for message %s/%s", chat_id, msg_id)
            return

        # Prefer message.caption for media; fallback to message.text only if caption missing
        caption = message.caption if getattr(message, "caption", None) else (message.text or "")
        caption = caption.strip() if isinstance(caption, str) else ""

        # If this item has no caption but belongs to a media_group, try to reuse an existing caption
        if media_group_id and not caption:
            try:
                existing = await db.get_products_by_media_group(media_group_id, chat_id)
                # find first non-empty caption among existing items
                for ex in existing:
                    ex_caption = (ex.get("caption") or "").strip()
                    if ex_caption:
                        caption = ex_caption
                        logger.debug("Reused caption from existing group: %r", caption)
                        break
            except Exception:
                logger.exception("Error while looking up existing media_group captions")

        # Log what we're saving for easier debugging
        logger.info(
            "Saving product from channel post: message_id=%s chat_id=%s media_group_id=%s type=%s caption_len=%s",
            msg_id,
            chat_id,
            media_group_id,
            file_type,
            len(caption) if caption else 0,
        )

        # Save product including media_group_id so albums can be reconstructed
        product_id = await db.add_product(
            file_id=file_id,
            file_type=file_type,
            caption=caption,
            message_id=msg_id,
            chat_id=chat_id,
            media_group_id=media_group_id,
        )

        if product_id > 0:
            logger.info(f"New product saved: ID={product_id}, type={file_type}")
    except Exception as e:
        logger.error(f"Error handling channel post: {e}", exc_info=True)


def _split_callback(data: str):
    """
    Robustly split callback data into (action, value).
    Accepts separators '|' or '_' and ignores extra whitespace.
    The last token is treated as the value (page or id); the preceding tokens form the action.
    Examples:
      "catalog_page|2" -> ("catalog_page", "2")
      "product_123"    -> ("product", "123")
      "menu|1"         -> ("menu", "1")
      "noop"           -> ("noop", None)
    """
    if not data:
        return None, None
    data = data.strip()
    parts = re.split(r"[|_]", data)
    if len(parts) == 1:
        return parts[0], None
    action = "_".join(parts[:-1])
    value = parts[-1]
    return action, value


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks with robust parsing and safe error handling."""
    query = update.callback_query
    if not query:
        return

    # Answer early to remove spinner
    try:
        await query.answer()
    except Exception:
        # ignore answer errors
        pass

    data = (query.data or "").strip()
    action, value = _split_callback(data)

    try:
        if action in ("catalog_page", "catalogpage", "catalog"):
            page = 1
            if value:
                page = int(value)
            await handle_catalog_pagination(update, context, page)
            return

        if action in ("search_page", "searchpage", "search"):
            page = 1
            if value:
                page = int(value)
            await handle_search_pagination(update, context, page)
            return

        if action in ("product", "productview", "item"):
            await handle_product_callback(update, context)
            return

        if action in ("delete", "remove"):
            if not value:
                await query.answer("Invalid product id.", show_alert=True)
                return
            product_id = int(value)
            await delete_product(update, context, product_id)
            return

        if action in ("menu", "catalog_menu"):
            page = 1
            if value:
                try:
                    page = int(value)
                except ValueError:
                    page = 1
            await handle_catalog_pagination(update, context, page)
            return

        if action == "noop":
            return

        await query.answer("Unknown action", show_alert=True)

    except ValueError:
        logger.error(f"Invalid callback data format: {data}")
        try:
            await query.answer("Invalid action format", show_alert=True)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Error handling callback query: {e}", exc_info=True)
        try:
            await query.answer("An error occurred", show_alert=True)
        except Exception:
            pass


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Treat all non-command messages as search queries (private chats)."""
    # Ignore commands
    if update.message and update.message.text and update.message.text.startswith("/"):
        return

    # Ignore channel posts here
    if update.channel_post:
        return

    # Rate limiting (simple)
    user = update.effective_user
    if not user:
        return

    user_id = user.id
    current_ts = asyncio.get_event_loop().time()
    last_ts = user_last_message.get(user_id, 0)
    if current_ts - last_ts < 1.0:
        try:
            await update.message.reply_text("⏳ Please wait a moment before sending another message.")
        except Exception:
            pass
        return
    user_last_message[user_id] = current_ts

    # Treat as search query
    if update.message and update.message.text:
        await handle_search(update, context)


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}", exc_info=True)
    if update and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ An unexpected error occurred. Please try again later.",
            )
        except Exception:
            pass


async def post_init(application: Application):
    """Initialize DB on startup; raise if it fails so the app logs the error."""
    try:
        await db.init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.exception("Failed to initialize database in post_init: %s", e)
        raise


def main():
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        logger.error("BOT_TOKEN not found in .env or environment. Please set BOT_TOKEN and restart.")
        print("ERROR: BOT_TOKEN not found in .env or environment. Please set BOT_TOKEN and restart.")
        return

    application = Application.builder().token(bot_token).post_init(post_init).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("nuke", nuke_command))

    # Callback queries and message handlers
    application.add_handler(CallbackQueryHandler(callback_query_handler))
    # Only accept text messages from private chats for search
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, message_handler)
    )
    application.add_error_handler(error_handler)

    # Channel monitoring (only add if CHANNEL_ID or CHANNEL_USERNAME is set)
    channel_id = get_channel_id()
    channel_username = get_channel_username()
    if channel_id:
        application.add_handler(
            MessageHandler(filters.Chat(chat_id=channel_id) & filters.ChatType.CHANNEL, channel_post_handler)
        )
        logger.info(f"Channel monitoring enabled for {channel_id}")
    elif channel_username:
        application.add_handler(
            MessageHandler(filters.Chat(username=channel_username.lstrip("@")) & filters.ChatType.CHANNEL, channel_post_handler)
        )
        logger.info(f"Channel monitoring enabled for {channel_username}")
    else:
        logger.warning("No CHANNEL_ID or CHANNEL_USERNAME set. Channel monitoring disabled.")

    logger.info("Starting bot...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except Exception as e:
        logger.exception("Application run_polling exited with exception: %s", e)
        raise


if __name__ == "__main__":
    main()
