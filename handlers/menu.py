# handlers/menu.py
"""
Catalog menu and pagination handlers.

Changes applied:
- Use canonical taxonomy helpers from utils.taxonomy_utils for normalization and display names.
- Use db.get_catalog_items and filter in-Python to avoid relying on DB-specific category queries.
- Use the same callback formats as product_view (CAT:...): CAT:VIEW, CAT:PAGE, CAT:OPEN_CAT, CAT:OPEN_SUB, CAT:BACK.
- Safer handling of missing data and defensive logging.
- Chunk long messages and keep keyboard labels short.
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

logger = logging.getLogger(__name__)
PAGE_SIZE = 5


def _short_caption(caption: str, length: int = 40) -> str:
    if not caption:
        return "No caption"
    caption = caption.strip().replace("\n", " ")
    return caption if len(caption) <= length else caption[: length - 1].rstrip() + "…"


def _display_label(key: Optional[str]) -> str:
    """
    Return a human-friendly label for taxonomy key using taxonomy utilities.
    """
    return get_display_name(key)


async def _delete_last_displayed_messages(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Delete messages recorded in context.user_data['last_displayed_messages'].
    Clears the list afterwards.
    """
    bot = context.bot
    last = context.user_data.get("last_displayed_messages") or []
    if not last:
        return
    for chat_id, message_id in last:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            logger.debug("Could not delete displayed message %s/%s", chat_id, message_id)
    context.user_data.pop("last_displayed_messages", None)


async def _build_catalog_page(
    context: ContextTypes.DEFAULT_TYPE, page: int, category: Optional[str] = None, subcategory: Optional[str] = None
) -> Tuple[Optional[str], Optional[InlineKeyboardMarkup]]:
    """
    Build the catalog text and InlineKeyboardMarkup for the given page.
    If category is provided, filter products by category (and optional subcategory).
    Returns (text, reply_markup).

    Category and subcategory labels in the header are human-friendly via DISPLAY_NAMES.
    """
    if page < 1:
        page = 1
    offset = (page - 1) * PAGE_SIZE

    # Fetch a reasonably sized window from DB and filter in Python.
    # We request a larger limit to allow filtering; if category filter is present,
    # we may need to scan more rows to fill the page. Use a safety multiplier.
    fetch_limit = PAGE_SIZE * 8 if category else PAGE_SIZE
    try:
        raw_items = await db.get_catalog_items(limit=fetch_limit, offset=offset)
    except Exception:
        logger.exception("Failed to fetch catalog items from DB")
        raw_items = []

    if not raw_items:
        return None, None

    # Normalize filter keys
    cat_key = canonicalize_key(category) if category else None
    sub_key = canonicalize_key(subcategory) if subcategory else None

    # Filter items by category/subcategory
    filtered = []
    for p in raw_items:
        p_cat = (p.get("category") or "").strip().upper() or None
        p_sub = (p.get("subcategory") or "").strip().upper() or None
        if cat_key:
            if not p_cat or p_cat != cat_key.upper():
                continue
        if sub_key:
            if not p_sub or p_sub != sub_key.upper():
                continue
        filtered.append(p)

    if not filtered:
        return None, None

    # Build keyboard rows for the page (limit to PAGE_SIZE)
    keyboard: List[List[InlineKeyboardButton]] = []
    for p in filtered[:PAGE_SIZE]:
        label = _short_caption(p.get("caption", "")) or f"Product {p['id']}"
        # Use product_view's callback format
        keyboard.append([InlineKeyboardButton(label, callback_data=f"CAT:VIEW:{p['id']}")])

    # Navigation row
    nav_row: List[InlineKeyboardButton] = []
    # We don't know total count cheaply here; provide Prev/Next based on whether we fetched enough items
    if page > 1:
        scope = f"{cat_key}::{sub_key}" if sub_key else (cat_key or "")
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"CAT:PAGE:{scope}::{page-1}"))
    # Current page indicator
    scope = f"{cat_key}::{sub_key}" if sub_key else (cat_key or "")
    nav_row.append(InlineKeyboardButton(f"Page {page}", callback_data=f"CAT:PAGE:{scope}::{page}"))
    # If we fetched more than PAGE_SIZE, assume there is a next page
    if len(filtered) > PAGE_SIZE or len(raw_items) > PAGE_SIZE:
        nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"CAT:PAGE:{scope}::{page+1}"))
    keyboard.append(nav_row)

    # Back button
    keyboard.append([InlineKeyboardButton("⬅️ BACK TO CATEGORIES", callback_data="CAT:BACK")])

    # Header
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
    """
    Build the top-level categories menu from the hardwired taxonomy.
    Returns (text, reply_markup).

    Categories are shown using display labels. Special categories (if present)
    are placed at the bottom for UX consistency.
    """
    keyboard: List[List[InlineKeyboardButton]] = []

    # Prepare ordered category list: alphabetical except DATEDPROOFS and CLIENTTOUCHDOWNS last
    cats = sorted(CATEGORIES.keys())

    bottom_first = "DATEDPROOFS"
    bottom_last = "CLIENTTOUCHDOWNS"

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
        # Use product_view's callback to open category
        keyboard.append([InlineKeyboardButton(label, callback_data=f"CAT:OPEN_CAT:{cat}")])

    text = "CATEGORIES — CHOOSE A CATEGORY TO BROWSE:"
    reply_markup = InlineKeyboardMarkup(keyboard)
    return text, reply_markup


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show the categories menu as the first screen. Delete any previously displayed product media first.
    """
    # Delete previous product display messages (if any)
    await _delete_last_displayed_messages(context)

    # Clear stored navigation state
    context.user_data.pop("origin_catalog_message", None)
    context.user_data.pop("catalog_page", None)
    context.user_data.pop("catalog_message", None)
    context.user_data.pop("selected_category", None)
    context.user_data.pop("selected_subcategory", None)

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
):
    """
    Show a specific page of the catalog as buttons.
    If category is provided, show products filtered by that category/subcategory.
    Delete any previously displayed product media first.
    Prefer editing the original catalog message (origin_catalog_message) when available;
    otherwise edit the callback message if it is a catalog message; fallback to sending a new message.
    """
    query = update.callback_query
    if query:
        try:
            await query.answer()
        except Exception:
            logger.debug("Failed to answer catalog pagination callback (non-fatal)")

    # Delete previous product display messages (if any)
    await _delete_last_displayed_messages(context)

    if page < 1:
        page = 1
    context.user_data["catalog_page"] = page

    # If caller didn't pass explicit category/subcategory, prefer stored selected filters
    if category is None:
        category = context.user_data.get("selected_category")
    if subcategory is None:
        subcategory = context.user_data.get("selected_subcategory")

    # Persist selected filters
    if category:
        context.user_data["selected_category"] = canonicalize_key(category)
    else:
        context.user_data.pop("selected_category", None)
    if subcategory:
        context.user_data["selected_subcategory"] = canonicalize_key(subcategory)
    else:
        context.user_data.pop("selected_subcategory", None)

    text, reply_markup = await _build_catalog_page(context, page, category=category, subcategory=context.user_data.get("selected_subcategory"))
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

    # Try to edit the origin catalog message if one was recorded
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

    # If the callback came from a catalog message the bot owns, try to edit that message
    try:
        if query and query.message and getattr(query.message.from_user, "is_bot", False) and query.message.text and query.message.text.startswith(("Catalog", "Categories", "CATEGORIES", "CATALOG")):
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

    # Fallback: send a new catalog message to the user who invoked the action
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


# -------------------------
# Category and subcategory callbacks
# -------------------------
async def handle_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback data: category|<CAT>
    Show subcategories for the chosen category and an 'All' button.
    """
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        logger.debug("Failed to answer category callback (non-fatal)")

    data = (query.data or "").strip()
    parts = data.split("|", 1)
    if len(parts) < 2:
        try:
            await query.answer("Invalid category", show_alert=True)
        except Exception:
            logger.debug("Failed to notify user about invalid category")
        return

    cat_raw = parts[1].strip()
    cat = canonicalize_key(cat_raw)
    if cat not in CATEGORIES:
        try:
            await query.answer("Unknown category", show_alert=True)
        except Exception:
            logger.debug("Failed to notify user about unknown category")
        return

    subs = list_subcategories(cat)
    display_subs = [s for s in subs if s and s.strip() and s.strip().upper() != "ALL"]

    if not display_subs:
        context.user_data["selected_category"] = cat
        context.user_data.pop("selected_subcategory", None)
        try:
            await handle_catalog_pagination(update, context, page=1, category=cat)
        except Exception:
            logger.exception("Failed to open category page for %s", cat)
        return

    buttons: List[List[InlineKeyboardButton]] = []
    buttons.append([InlineKeyboardButton("ALL", callback_data=f"CAT:PAGE:{cat}::1")])
    for s in display_subs:
        buttons.append([InlineKeyboardButton(_display_label(s), callback_data=f"CAT:OPEN_SUB:{cat}::{s}")])

    buttons.append([InlineKeyboardButton("⬅️ BACK TO CATEGORIES", callback_data="CAT:BACK")])

    try:
        await query.message.edit_text(f"{_display_label(cat)} — CHOOSE SUBCATEGORY:", reply_markup=InlineKeyboardMarkup(buttons))
    except BadRequest as e:
        if "Message is not modified" in str(e):
            logger.debug("Category message already up to date")
        else:
            logger.exception("Failed to edit message to show subcategories")
    except Exception:
        logger.exception("Unexpected error while showing subcategories")


async def handle_category_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback data: category_sub|<CAT>|<SUB> (legacy) or CAT:OPEN_SUB:<CAT>::<SUB>
    If SUB is empty, show all products in CAT. Otherwise show products filtered by CAT/SUB.
    """
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        logger.debug("Failed to answer category_sub callback (non-fatal)")

    data = (query.data or "").strip()

    # Support both legacy "category_sub|CAT|SUB" and new "CAT:OPEN_SUB:CAT::SUB"
    if data.startswith("category_sub|"):
        parts = data.split("|")
        if len(parts) < 2:
            await query.answer("Invalid request", show_alert=True)
            return
        cat = canonicalize_key(parts[1])
        sub = canonicalize_key(parts[2]) if len(parts) >= 3 and parts[2] else None
    elif data.startswith("CAT:OPEN_SUB:"):
        # payload format: CAT:OPEN_SUB:<CAT>::<SUB>
        _, _, rest = data.partition(":")
        # rest starts with "OPEN_SUB:..."
        _, _, payload = rest.partition(":")
        if "::" not in payload:
            await query.answer("Invalid request", show_alert=True)
            return
        cat_raw, sub_raw = payload.split("::", 1)
        cat = canonicalize_key(cat_raw)
        sub = canonicalize_key(sub_raw)
    else:
        await query.answer("Invalid request", show_alert=True)
        return

    if cat not in CATEGORIES:
        await query.answer("Unknown category", show_alert=True)
        return

    if sub and sub not in CATEGORIES.get(cat, []):
        sub = None

    context.user_data["selected_category"] = cat
    if sub:
        context.user_data["selected_subcategory"] = sub
    else:
        context.user_data.pop("selected_subcategory", None)

    await handle_catalog_pagination(update, context, page=1, category=cat, subcategory=context.user_data.get("selected_subcategory"))


# Helper to show categories again (callback data "menu_categories" or CAT:BACK)
async def handle_menu_categories_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        logger.debug("Failed to answer menu_categories callback (non-fatal)")

    context.user_data.pop("selected_category", None)
    context.user_data.pop("selected_subcategory", None)

    text, reply_markup = await _build_categories_menu()
    try:
        await query.message.edit_text(text, reply_markup=reply_markup)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            logger.debug("Categories message already up to date")
        else:
            logger.exception("Failed to edit message to show categories")
    except Exception:
        logger.exception("Unexpected error while showing categories")