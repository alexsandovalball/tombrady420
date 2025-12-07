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
from handlers.product_view import show_product, handle_product_callback, handle_delete_callback
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
    """Handle new posts in the monitored channel and save each media item with media_group_id."""
    try:
        message = update.channel_post
        if not message:
            return

        chat_id = message.chat.id
        msg_id = message.message_id
        media_group_id = getattr(message, "media_group_id", None)

        logger.debug(
            "Channel post received: chat_id=%s message_id=%s media_group_id=%s",
            chat_id,
            msg_id,
            media_group_id,
        )
        logger.debug("caption: %r", getattr(message, "caption", None))
        logger.debug("caption_entities: %r", getattr(message, "caption_entities", None))
        logger.debug("text: %r", getattr(message, "text", None))

        if await db.is_message_ignored(msg_id, chat_id):
            logger.debug("Message ignored: %s/%s", chat_id, msg_id)
            return

        if not has_media(update):
            logger.debug("No media in message %s/%s", chat_id, msg_id)
            return

        file_id, file_type = get_file_id_and_type(update)
        if not file_id:
            logger.debug("No file_id found for message %s/%s", chat_id, msg_id)
            return

        caption = message.caption if getattr(message, "caption", None) else (message.text or "")
        caption = caption.strip() if isinstance(caption, str) else ""

        if media_group_id and not caption:
            try:
                existing = await db.get_products_by_media_group(media_group_id, chat_id)
                for ex in existing:
                    ex_caption = (ex.get("caption") or "").strip()
                    if ex_caption:
                        caption = ex_caption
                        logger.debug("Reused caption from existing group: %r", caption)
                        break
            except Exception:
                logger.exception("Error while looking up existing media_group captions")

        logger.info(
            "Saving product from channel post: message_id=%s chat_id=%s media_group_id=%s type=%s caption_len=%s",
            msg_id,
            chat_id,
            media_group_id,
            file_type,
            len(caption) if caption else 0,
        )

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
    """Split callback data into (action, value)."""
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

    # Always try to stop the client spinner immediately and log if that fails
    try:
        await query.answer()
    except Exception:
        logger.exception("Failed to answer callback query (non-fatal)")

    # Log full callback context for debugging
    try:
        q_msg = query.message
        logger.info(
            "Callback received: data=%s from_user=%s user_id=%s chat_id=%s message_id=%s",
            query.data,
            getattr(query.from_user, "username", None),
            getattr(query.from_user, "id", None),
            getattr(q_msg.chat, "id", None) if q_msg else None,
            getattr(q_msg, "message_id", None) if q_msg else None,
        )
    except Exception:
        logger.exception("Failed to log callback query context")

    data = (query.data or "").strip()
    action, value = _split_callback(data)

    try:
        # Catalog pagination
        if action in ("catalog_page", "catalogpage", "catalog"):
            page = int(value) if value else 1
            await handle_catalog_pagination(update, context, page)
            return

        # Search pagination
        if action in ("search_page", "searchpage", "search"):
            page = int(value) if value else 1
            await handle_search_pagination(update, context, page)
            return

        # Product view - delegate to product handler
        if action in ("product", "productview", "item"):
            # product handler should be resilient; if it raises, we catch below
            await handle_product_callback(update, context)
            return

        # Delete/remove product
        if action in ("delete", "remove"):
            await handle_delete_callback(update, context)
            return

        # Menu shortcut
        if action in ("menu", "catalog_menu"):
            page = 1
            if value:
                try:
                    page = int(value)
                except ValueError:
                    page = 1
            await handle_catalog_pagination(update, context, page)
            return

        # No-op button
        if action == "noop":
            return

        # Unknown action fallback
        logger.warning("Unknown callback action: %s (raw=%s)", action, data)
        try:
            await query.answer("Unknown action", show_alert=True)
        except Exception:
            logger.exception("Failed to notify user about unknown action")

    except ValueError:
        logger.error("Invalid callback data format: %s", data)
        try:
            await query.answer("Invalid action format", show_alert=True)
        except Exception:
            logger.exception("Failed to answer invalid format")
    except Exception:
        # Catch everything so the client never hangs; log full traceback
        logger.exception("Unhandled exception in callback_query_handler for data=%s", data)
        try:
            # Prefer editing the inline message if bot owns it, otherwise send a new message
            if query.message and getattr(query.message.from_user, "is_bot", False):
                try:
                    await query.edit_message_text("❌ An internal error occurred. Please try again.")
                except Exception:
                    # If edit fails, send a new message
                    await context.bot.send_message(chat_id=query.from_user.id, text="❌ An internal error occurred. Please try again.")
            else:
                await context.bot.send_message(chat_id=query.from_user.id, text="❌ An internal error occurred. Please try again.")
        except Exception:
            logger.exception("Failed to notify user after exception in callback handler")


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
            if update.message:
                await update.message.reply_text("⏳ Please wait a moment before sending another message.")
        except Exception:
            logger.exception("Failed to send rate-limit reply")
        return

    user_last_message[user_id] = current_ts

    # Treat as search query
    if update.message and update.message.text:
        try:
            await handle_search(update, context)
        except Exception:
            logger.exception("Error while handling search command from message")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}", exc_info=True)
    if update and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ An unexpected error occurred. Please try again later.",
            )
        except Exception:
            logger.exception("Failed to notify user about an unexpected error")


async def post_init(application: Application):
    """Initialize DB on startup; raise if it fails so the app logs the error."""
    try:
        await db.init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.exception("Failed to initialize database in post_init: %s", e)
        raise


def build_application():
    """
    Build and return the telegram Application instance without starting it.
    This allows a webhook server to import `application` and push updates into its queue.
    """
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        logger.error("BOT_TOKEN not found in environment.")
        raise RuntimeError("BOT_TOKEN not set")

    application = Application.builder().token(bot_token).post_init(post_init).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("nuke", nuke_command))

    # Callback queries and message handlers
    application.add_handler(CallbackQueryHandler(callback_query_handler))
    # Private text messages (non-commands) -> message_handler
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

    return application


# Build the application object so it can be imported by a webhook server (webhook_server.py)
application = build_application()


if __name__ == "__main__":
    # When executed directly, run polling (local/dev mode).
    logger.info("Starting bot in polling mode...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except Exception as e:
        logger.exception("Application run_polling exited with exception: %s", e)
        raise