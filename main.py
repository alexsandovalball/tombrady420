"""
Main entry point for the Telegram media catalog bot.
"""
import os
import logging
import re
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
    ContextTypes,
)

from database import Database
from handlers.start import start_command
from handlers.menu import menu_command, handle_catalog_pagination
from handlers.search import handle_search, handle_search_pagination, handle_back_to_search
from handlers.admin import (
    stats_command,
    delete_product,
    nuke_command,
    list_users_command,
    set_category_command,
    handle_nuke_step1,
    handle_nuke_final,
    handle_nuke_cancel,
)
from handlers.product_view import show_product, handle_product_callback, handle_delete_callback
from utils.helpers import get_file_id_and_type, has_media, get_channel_id, get_channel_username
from utils.categories import extract_category_and_subcategory

# Debug handlers (temporary; remove in production)
from handlers.debug import dbcheck_command, dump_user_data

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

        if await db.is_message_ignored(msg_id, chat_id):
            return
        if not has_media(update):
            return

        file_id, file_type = get_file_id_and_type(update)
        if not file_id:
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
                        break
            except Exception:
                logger.exception("Error while looking up existing media_group captions")

        clean_caption, category, subcategory = extract_category_and_subcategory(caption)

        product_id = await db.add_product(
            file_id=file_id,
            file_type=file_type,
            caption=clean_caption,
            message_id=msg_id,
            chat_id=chat_id,
            media_group_id=media_group_id,
            category=category,
            subcategory=subcategory,
        )

        if product_id > 0:
            logger.info(
                f"New product saved: ID={product_id}, type={file_type}, category={category}, subcategory={subcategory}"
            )
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

    user = update.effective_user
    if user:
        try:
            await db.save_user(
                user_id=user.id,
                username=user.username or None,
                first_name=user.first_name or None,
                last_name=user.last_name or None,
                last_seen=datetime.utcnow(),
            )
        except Exception:
            logger.debug("Failed to save user info")

    try:
        await query.answer()
    except Exception:
        logger.exception("Failed to answer callback query (non-fatal)")

    data = (query.data or "").strip()
    action, value = _split_callback(data)

    try:
        if action in ("catalog_page", "catalogpage", "catalog"):
            page = int(value) if value else 1
            await handle_catalog_pagination(update, context, page)
            return

        if action in ("search_page", "searchpage", "search"):
            parts = data.split("|")
            if len(parts) >= 3:
                query_text = parts[1]
                page = int(parts[2])
                await handle_search_pagination(update, context, query_text, page)
            return

        if action in ("search_back", "backsearch"):
            await handle_back_to_search(update, context)
            return

        if action in ("product", "productview", "item"):
            await handle_product_callback(update, context)
            return

        if action in ("delete", "remove"):
            await handle_delete_callback(update, context)
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

        logger.warning("Unknown callback action: %s (raw=%s)", action, data)
        try:
            await query.answer("Unknown action", show_alert=True)
        except Exception:
            logger.exception("Failed to notify user about unknown action")

    except Exception:
        logger.exception("Unhandled exception in callback_query_handler for data=%s", data)
        try:
            await context.bot.send_message(
                chat_id=query.from_user.id, text="❌ An internal error occurred. Please try again."
            )
        except Exception:
            logger.exception("Failed to notify user after exception in callback handler")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Treat all non-command messages as search queries (private chats)."""
    if update.message and update.message.text and update.message.text.startswith("/"):
        return
    if update.channel_post:
        return

    user = update.effective_user
    if user:
        try:
            await db.save_user(
                user_id=user.id,
                username=user.username or None,
                first_name=user.first_name or None,
                last_name=user.last_name or None,
                last_seen=datetime.utcnow(),
            )
        except Exception:
            logger.debug("Failed to save user info")

    if not user:
        return

    user_id = user.id
    current_ts = asyncio.get_event_loop().time()
    last_ts = user_last_message.get(user_id, 0)
    if current_ts - last_ts < 1.0:
        if update.message:
            await update.message.reply_text("⏳ Please wait a moment before sending another message.")
        return

    user_last_message[user_id] = current_ts

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
    try:
        await db.init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.exception("Failed to initialize database in post_init: %s", e)
        raise


def build_application():
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
    application.add_handler(CommandHandler("users", list_users_command))
    application.add_handler(CommandHandler("setcategory", set_category_command))

    # Debug commands
    application.add_handler(CommandHandler("dbcheck", dbcheck_command))
    application.add_handler(CommandHandler("dumpuserdata", dump_user_data))

    # Callback queries
    application.add_handler(CallbackQueryHandler(callback_query_handler))
    application.add_handler(CallbackQueryHandler(handle_nuke_step1, pattern="^nuke_step1_yes$"))
    application.add_handler(CallbackQueryHandler(handle_nuke_final, pattern="^nuke_final_yes$"))
    application.add_handler(CallbackQueryHandler(handle_nuke_cancel, pattern="^nuke_cancel$"