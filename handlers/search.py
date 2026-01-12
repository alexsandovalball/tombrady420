 url=https://github.com/alexsandovalball/tombrady420/blob/main/handlers/search.py
﻿# handlers/search.py
import logging
from typing import Optional, List, Dict, Any

from urllib.parse import quote_plus, unquote_plus

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from database import db  # use the shared async DB instance
from utils.helpers import short_caption, delete_last_displayed_messages

logger = logging.getLogger(__name__)

PAGE_SIZE = 5


def _dedupe_products(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Deduplicate products by file_unique_id, then media_group_id, then message_id,
    keeping the first occurrence as the representative entry.
    """
    unique = {}
    for p in products or []:
        key = None
        fu = p.get("file_unique_id")
        mg = p.get("media_group_id")
        mid = p.get("message_id")
        pid = p.get("id")
        if fu:
            key = f"fu:{fu}"
        elif mg:
            key = f"mg:{mg}"
        elif mid:
            key = f"msg:{mid}"
        else:
            key = f"id:{pid}"
        if key not in unique:
            unique[key] = p
    return list(unique.values())


async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = ""
    try:
        if update.message and update.message.text:
            text = update.message.text.strip()
    except Exception:
        logger.exception("Failed to read incoming search text")

    if not text:
        try:
            if update.message:
                await update.message.reply_text("❌ Please enter a search term.")
        except Exception:
            logger.debug("Failed to send empty-query reply")
        return

    try:
        await delete_last_displayed_messages(context)
    except Exception:
        logger.exception("Failed to delete last displayed messages before search")

    try:
        products = await db.search_products(text, limit=PAGE_SIZE, offset=0)
    except Exception:
        logger.exception("Database search_products failed")
        try:
            if update.message:
                await update.message.reply_text("⚠️ Search failed due to an internal error. Please try again later.")
        except Exception:
            logger.debug("Failed to notify user about search failure")
        return

    products = _dedupe_products(products)
    if not products:
        try:
            if update.message:
                await update.message.reply_text(f"No results found for '{text}'.")
        except Exception:
            logger.debug("Failed to send no-results reply")
        return

    keyboard = []
    for p in products:
        label = short_caption(p.get("caption", "")) or f"Product {p.get('id')}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"CAT:VIEW:{p.get('id')}")])

    encoded_q = quote_plus(text)
    nav_row = [
        InlineKeyboardButton("Page 1", callback_data="noop"),
        InlineKeyboardButton("➡️ Next", callback_data=f"search_page|{encoded_q}|2"),
    ]
    keyboard.append(nav_row)

    try:
        if update.message:
            msg = await update.message.reply_text(
                f"Search results for '{text}':",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            dest = update.effective_chat.id if update.effective_chat else None
            if dest:
                msg = await context.bot.send_message(chat_id=dest, text=f"Search results for '{text}':", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                logger.error("handle_search: no destination to send search results")
                return
    except Exception:
        logger.exception("Failed to send search results message")
        try:
            if update.message:
                await update.message.reply_text("⚠️ Failed to send search results. Please try again.")
        except Exception:
            logger.debug("Fallback send after search results failure also failed")
        return

    try:
        context.user_data["search_message"] = {
            "chat_id": msg.chat.id,
            "message_id": msg.message_id,
            "query": text,
            "page": 1,
        }
    except Exception:
        logger.exception("Failed to record search_message in user_data")