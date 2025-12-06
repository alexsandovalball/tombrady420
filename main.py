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

    try:
        await query.answer()
    except Exception:
        pass

    data = (query.data or "").strip()
    action, value = _split_callback(data)

    try:
        if action in ("catalog_page", "catalogpage", "catalog"):
            page = int(value) if value else 1
            await handle_catalog_pagination(update, context, page)
            return

        if action in ("search_page", "searchpage", "search"):
            page = int(value) if value else 1
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
    if update.message and update.message.text and update.message.text.startswith("/"):
