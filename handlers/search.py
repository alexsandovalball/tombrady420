# handlers/search.py
"""
Search handlers using fuzzy matching.

- /search <terms> to search catalog captions (fuzzy)
- Callback pagination uses callback_data: search_page|{encoded_query}|{page}
"""

from __future__ import annotations

import logging
from typing import List, Dict, Any
from urllib.parse import quote_plus, unquote_plus

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from database import db
from utils.helpers import short_caption, delete_last_displayed_messages
from utils.fuzzy_search import fuzzy_search_products

logger = logging.getLogger(__name__)

PAGE_SIZE = 5


async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /search <query> command handler or text search handler.
    Performs a fuzzy search over recent catalog items and displays paginated buttons.
    """
    # Extract search query: prefer command args else full message
    text = ""
    try:
        if context.args:
            text = " ".join(context.args).strip()
        elif update.message and update.message.text:
            # If invoked as plain message, assume whole text is query
            text = update.message.text.strip()
    except Exception:
        logger.exception("Failed to read incoming search text")

    if not text:
        try:
            if update.message:
                await update.message.reply_text("❌ Usage: /search <terms> — enter some search words.")
        except Exception:
            logger.debug("Failed to send empty-query reply")
        return

    # Delete previous displayed media
    try:
        await delete_last_displayed_messages(context)
    except Exception:
        logger.exception("Failed to delete last displayed messages before search")

    # Fetch a reasonably sized catalog to search in-memory (tune limit as needed)
    try:
        products = await db.get_catalog_items(limit=2000)
    except Exception:
        logger.exception("Database get_catalog_items failed for search")
        try:
            if update.message:
                await update.message.reply_text("⚠️ Search failed due to an internal error. Please try again later.")
        except Exception:
            logger.debug("Failed to notify user about search failure")
        return

    # Run fuzzy search (falls back to simple heuristics if rapidfuzz missing)
    try:
        matches = fuzzy_search_products(products, text, score_cutoff=60, limit=100)
    except Exception:
        logger.exception("fuzzy_search_products failed; falling back to simple substring match")
        # Fallback: simple case-insensitive substring match on caption
        try:
            q = text.lower()
            matches = [p for p in products if q in (p.get("caption") or "").lower()]
        except Exception:
            logger.exception("Fallback substring search also failed")
            matches = []

    if not matches:
        try:
            if update.message:
                await update.message.reply_text(f"No results found for '{text}'.")
        except Exception:
            logger.debug("Failed to send no-results reply")
        return

    # Show first page
    await _render_search_page(update, context, matches, text, page=1)


async def _render_search_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    matches: List[Dict[str, Any]],
    query_text: str,
    page: int = 1,
) -> None:
    # Paginate matches
    total = len(matches)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, pages))
    offset = (page - 1) * PAGE_SIZE
    page_items = matches[offset : offset + PAGE_SIZE]

    keyboard: List[List[InlineKeyboardButton]] = []
    for p in page_items:
        label = short_caption(p.get("caption", "")) or f"Product {p.get('id')}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"CAT:VIEW:{p.get('id')}")])

    # Navigation row
    nav_row: List[InlineKeyboardButton] = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"search_page|{quote_plus(query_text)}|{page-1}"))
    nav_row.append(InlineKeyboardButton(f"Page {page}/{pages}", callback_data="noop"))
    if page < pages:
        nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"search_page|{quote_plus(query_text)}|{page+1}"))
    keyboard.append(nav_row)

    text = f"Search results for '{query_text}':"
    reply_markup = InlineKeyboardMarkup(keyboard)

    q = update.callback_query
    try:
        if q and q.message:
            try:
                await q.message.edit_text(text, reply_markup=reply_markup)
            except BadRequest as e:
                # Message not modified or other BadRequest: handle gracefully
                if "Message is not modified" in str(e):
                    logger.debug("Search message already up to date")
                else:
                    logger.exception("Failed to edit existing search message; sending a new message instead")
                    try:
                        sent = await q.message.reply_text(text, reply_markup=reply_markup)
                        context.user_data["search_message"] = {"chat_id": sent.chat.id, "message_id": sent.message_id, "query": query_text, "page": page}
                    except Exception:
                        logger.exception("Failed to send fallback search message")
                    return
            # store context for navigation
            context.user_data["search_message"] = {"chat_id": q.message.chat.id, "message_id": q.message.message_id, "query": query_text, "page": page}
        elif update.message:
            try:
                msg = await update.message.reply_text(text, reply_markup=reply_markup)
                context.user_data["search_message"] = {"chat_id": msg.chat.id, "message_id": msg.message_id, "query": query_text, "page": page}
            except Exception:
                logger.exception("Failed to send search results message")
        else:
            # fallback: send to effective chat if available
            dest = update.effective_chat.id if update.effective_chat else None
            if dest:
                try:
                    sent = await context.bot.send_message(chat_id=dest, text=text, reply_markup=reply_markup)
                    context.user_data["search_message"] = {"chat_id": sent.chat.id, "message_id": sent.message_id, "query": query_text, "page": page}
                except Exception:
                    logger.exception("Failed to send fallback search message to effective chat")
    except BadRequest as e:
        if "Message is not modified" in str(e):
            logger.debug("Search message already up to date")
        else:
            logger.exception("Failed to render search page")
    except Exception:
        logger.exception("Unexpected error rendering search page")


async def cb_search_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Parse search pagination callback_data like:
        search_page|{encoded_query}|{page}
    """
    q = update.callback_query
    if not q or not q.data:
        return
    try:
        await q.answer()
    except Exception:
        pass

    data = q.data
    if not data.startswith("search_page|"):
        try:
            await q.answer("Invalid search action", show_alert=True)
        except Exception:
            pass
        return

    try:
        # data format: search_page|{encoded_query}|{page}
        _, rest = data.split("|", 1)
        encoded_q, page_str = rest.rsplit("|", 1)
        query_text = unquote_plus(encoded_q)
        page = int(page_str)
    except Exception:
        try:
            await q.answer("Malformed search pagination", show_alert=True)
        except Exception:
            pass
        return

    # Re-run matching on fresh data (or optionally cache earlier results in user_data)
    try:
        products = await db.get_catalog_items(limit=2000)
    except Exception:
        logger.exception("DB fetch failed during search pagination")
        try:
            if q.message:
                await q.message.reply_text("Search failed; please try again later.")
        except Exception:
            pass
        return

    try:
        matches = fuzzy_search_products(products, query_text, score_cutoff=60, limit=100)
    except Exception:
        logger.exception("fuzzy_search_products failed during pagination; using substring fallback")
        try:
            ql = query_text.lower()
            matches = [p for p in products if ql in (p.get("caption") or "").lower()]
        except Exception:
            logger.exception("Fallback substring search failed during pagination")
            matches = []

    if not matches:
        try:
            if q.message:
                await q.message.edit_text(f"No results found for '{query_text}'.")
        except Exception:
            logger.debug("Failed to edit search message to show empty results")
        return

    await _render_search_page(update, context, matches, query_text, page=page)