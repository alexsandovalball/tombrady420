# handlers/menu.py
"""
Catalog menu and pagination handlers.

This module builds category/subcategory menus and paginated product lists.
It intentionally does not auto-open product details — product media is shown
only when the user clicks a product's "View" button.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple, List, Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from database import db
from config.taxonomy import CATEGORIES
from utils.taxonomy_utils import canonicalize_key, get_display_name, list_subcategories
from utils.helpers import short_caption, delete_last_displayed_messages

logger = logging.getLogger(__name__)
PAGE_SIZE = 5


def _display_label(key: Optional[str]) -> str:
    return get_display_name(key)


async def _delete_last_displayed_messages(context: ContextTypes.DEFAULT_TYPE) -> None:
    await delete_last_displayed_messages(context)


async def _build_catalog_page(
    context: ContextTypes.DEFAULT_TYPE,
    page: int,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
) -> Tuple[Optional[str], Optional[InlineKeyboardMarkup]]:
    """
    Build the text and InlineKeyboardMarkup for a catalog page.
    Returns (text, reply_markup) or (None, None) when nothing to show.
    """
    if page < 1:
        page = 1
    offset = (page - 1) * PAGE_SIZE

    # Fetch a window larger than a single page to allow filtering and pagination
    fetch_limit = offset + (PAGE_SIZE * 8 if category else PAGE_SIZE * 4)
    try:
        raw_items = await db.get_catalog_items(limit=fetch_limit)
    except Exception:
        logger.exception("Failed to fetch catalog items from DB")
        raw_items = []

    if not raw_items:
        return None, None

    cat_key = canonicalize_key(category) if category else None
    sub_key = canonicalize_key(subcategory) if subcategory else None

    filtered_all: List[Dict[str, Any]] = []
    for p in raw_items:
        p_cat = (p.get("category") or "").strip().upper() or None
        p_sub = (p.get("subcategory") or "").strip().upper() or None
        if cat_key:
            if not p_cat or p_cat != (cat_key.upper() if isinstance(cat_key, str) else cat_key):
                continue
        if sub_key:
            if not p_sub or p_sub != (sub_key.upper() if isinstance(sub_key, str) else sub_key):
                continue
        filtered_all.append(p)

    if not filtered_all:
        return None, None

    total_filtered = len(filtered_all)
    page_items = filtered_all[offset : offset + PAGE_SIZE]
    if not page_items:
        return None, None

    keyboard: List[List[InlineKeyboardButton]] = []
    for p in page_items:
        label = short_caption(p.get("caption", "")) or f"Product {p['id']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"CAT:VIEW:{p['id']}")])

    nav_row: List[InlineKeyboardButton] = []
    scope = ""
    if cat_key:
        scope = f"{cat_key}"
        if sub_key:
            scope = f"{cat_key}::{sub_key}"

    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"CAT:PAGE:{scope}::{page-1}"))
    nav_row.append(InlineKeyboardButton(f"Page {page}", callback_data=f"CAT:PAGE:{scope}::{page}"))
    if offset + PAGE_SIZE < total_filtered:
        nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"CAT:PAGE:{scope}::{page+1}"))
    keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("⬅️ BACK TO CATEGORIES", callback_data="CAT:BACK")])

    if category:
        cat_label = _display_label(cat_key)
        sub_label = f" — {_display_label(sub_key)}" if sub_key else ""
        header = f"{cat_label}{sub_label}"
        text = f"{header}\n\nSELECT AN ITEM TO VIEW ITS MEDIA:"
    else:
        text = "CATALOG — SELECT AN ITEM TO VIEW ITS MEDIA:"

    reply_markup = InlineKeyboardMarkup(keyboard)
    return text, reply_markup


async def _build_categories_menu() -> Tuple[str, InlineKeyboardMarkup]:
    keyboard: List[List[InlineKeyboardButton]] = []

    cats = sorted(CATEGORIES.keys())

    bottom_first = "DATEDPROOFS"
    bottom_last = "CLIENTTOUCHDOWNS"

    # Move special categories to the bottom if present
    if bottom_first in cats:
        cats.remove(bottom_first)
    if bottom_last in cats:
        cats.remove(bottom_last)

    if bottom_first in CATEGORIES:
        cats.append(bottom_first)
    if bottom_last in CATEGORIES:
        cats.append(bottom_last)

    for cat in cats:
        label = _display_label(cat)
        keyboard.append([InlineKeyboardButton(label, callback_data=f"CAT:OPEN_CAT:{cat}")])

    text = "CATEGORIES — CHOOSE A CATEGORY TO BROWSE:"
    reply_markup = InlineKeyboardMarkup(keyboard)
    return text, reply_markup


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Command to show the top-level categories menu.
    Clears previous catalog navigation state and last displayed messages.
    """
    await _delete_last_displayed_messages(context)

    for k in ("origin_catalog_message", "catalog_page", "catalog_message", "selected_category", "selected_subcategory"):
        context.user_data.pop(k, None)

    text, reply_markup = await _build_categories_menu()
    try:
        if update.message:
            msg = await update.message.reply_text(text, reply_markup=reply_markup)
        else:
            dest = update.effective_chat.id if update.effective_chat else None
            if dest:
                msg = await context.bot.send_message(chat_id=dest, text=text, reply_markup=reply_markup)
            else:
                logger.error("menu_command: no destination to send categories menu")
                return
        try:
            context.user_data["catalog_message"] = {"chat_id": msg.chat.id, "message_id": msg.message_id}
        except Exception:
            logger.exception("Failed to save catalog_message reference")
    except Exception:
        logger.exception("Failed to send categories menu")


async def handle_catalog_pagination(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    page: int,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
) -> None:
    """
    Handle pagination callbacks and send or edit the catalog page message.
    """
    query = update.callback_query
    if query:
        try:
            await query.answer()
        except Exception:
            logger.debug("Failed to answer catalog pagination callback (non-fatal)")

    await _delete_last_displayed_messages(context)

    if page < 1:
        page = 1
    context.user_data["catalog_page"] = page

    if category is None:
        category = context.user_data.get("selected_category")
    if subcategory is None:
        subcategory = context.user_data.get("selected_subcategory")

    if category:
        context.user_data["selected_category"] = canonicalize_key(category)
    else:
        context.user_data.pop("selected_category", None)
    if subcategory:
        context.user_data["selected_subcategory"] = canonicalize_key(subcategory)
    else:
        context.user_data.pop("selected_subcategory", None)

    text, reply_markup = await _build_catalog_page(
        context,
        page,
        category=category,
        subcategory=context.user_data.get("selected_subcategory"),
    )
    if not text:
        try:
            if query and query.message:
                await query.message.reply_text("No more products.")
            else:
                dest = update.effective_chat.id if update.effective_chat else None
                if dest:
                    await context.bot.send_message(chat_id=dest, text="No more products.")
        except Exception:
            logger.exception("Failed to notify user about empty catalog page")
        return

    origin = context.user_data.get("origin_catalog_message")
    if origin and isinstance(origin, dict):
        try:
            await context.bot.edit_message_text(
                chat_id=origin["chat_id"],
                message_id=origin["message_id"],
                text=text,
                reply_markup=reply_markup,
            )
            context.user_data["catalog_message"] = {"chat_id": origin["chat_id"], "message_id": origin["message_id"]}
            context.user_data.pop("origin_catalog_message", None)
            return
        except BadRequest as e:
            if "Message is not modified" in str(e):
                logger.debug("Catalog message already up to date, no edit needed")
                context.user_data.pop("origin_catalog_message", None)
                return
            else:
                logger.exception("Failed to edit origin_catalog_message; will try other fallbacks")
        except Exception:
            logger.exception("Unexpected error editing origin_catalog_message")

    # Try editing the callback message if it looks like a bot/catalog message
    try:
        if query and query.message and getattr(query.message.from_user, "is_bot", False) and query.message.text and query.message.text.upper().startswith(("CATALOG", "CATEGORIES", "CAT:")):
            try:
                await query.message.edit_text(text, reply_markup=reply_markup)
                context.user_data["catalog_message"] = {"chat_id": query.message.chat.id, "message_id": query.message.message_id}
                return
            except BadRequest as e:
                if "Message is not modified" in str(e):
                    logger.debug("Callback catalog message already up to date, no edit needed")
                    return
                else:
                    logger.exception("Failed to edit callback catalog message; will send a new one")
            except Exception:
                logger.exception("Unexpected error editing callback catalog message")
    except Exception:
        logger.exception("Error while attempting to edit callback message (non-fatal)")

    # Fallback: send a new message to the user
    try:
        dest_chat = None
        if query and query.from_user:
            dest_chat = query.from_user.id
        elif update.effective_chat:
            dest_chat = update.effective_chat.id

        if dest_chat:
            msg = await context.bot.send_message(chat_id=dest_chat, text=text, reply_markup=reply_markup)
            context.user_data["catalog_message"] = {"chat_id": msg.chat.id, "message_id": msg.message_id}
    except Exception:
        logger.exception("Failed to send fallback catalog message")