# handlers/menu.py
from typing import Optional, Tuple, List, Dict, Any

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from database import db
from config.taxonomy import CATEGORIES, DISPLAY_NAMES

logger = logging.getLogger(__name__)
PAGE_SIZE = 5


def _short_caption(caption: Optional[str], length: int = 40) -> str:
    if not caption:
        return "No caption"
    caption = caption.strip().replace("\n", " ")
    return caption if len(caption) <= length else caption[: length - 1].rstrip() + "…"


def _display_label(key: Optional[str]) -> str:
    """
    Return a human-friendly UPPERCASE label for taxonomy key.
    Uses DISPLAY_NAMES mapping first, then falls back to replacing underscores
    and uppercasing. Case-insensitive fallback for safety.
    """
    if not key:
        return ""
    # exact match
    if key in DISPLAY_NAMES:
        return DISPLAY_NAMES[key]
    # case-insensitive match
    lk = key.lower()
    for k, v in DISPLAY_NAMES.items():
        if k.lower() == lk:
            return v
    # fallback: replace underscores and uppercase
    return key.replace("_", " ").upper()


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
            # ignore failures; message may already be gone or not deletable
            logger.debug("Could not delete displayed message %s/%s", chat_id, message_id)
    context.user_data.pop("last_displayed_messages", None)


async def _build_catalog_page(
    context: ContextTypes.DEFAULT_TYPE,
    page: int,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
) -> Tuple[Optional[str], Optional[InlineKeyboardMarkup]]:
    """
    Build the catalog text and InlineKeyboardMarkup for the given page.
    If category is provided, filter products by category (and optional subcategory).
    Returns (text, reply_markup).
    """
    offset = (page - 1) * PAGE_SIZE

    try:
        if category:
            sub = subcategory if subcategory else None
            products = await db.get_products_by_category(category, subcategory=sub, limit=PAGE_SIZE, offset=offset)
        else:
            products = await db.get_catalog_items(limit=PAGE_SIZE, offset=offset)
    except Exception:
        logger.exception("Failed to fetch products for catalog page (page=%s category=%s sub=%s)", page, category, subcategory)
        return None, None

    if not products:
        return None, None

    keyboard: List[List[InlineKeyboardButton]] = []
    for p in products:
        label = _short_caption(p.get("caption", "")) or f"Product {p.get('id')}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"product|{p.get('id')}")])

    nav_row: List[InlineKeyboardButton] = []
    if page > 1:
        if category:
            # encode category/subcategory into callback so pagination can preserve filter
            if subcategory:
                nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"category_page|{category}|{subcategory}|{page-1}"))
            else:
                nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"category_page|{category}||{page-1}"))
        else:
            nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"catalog_page|{page-1}"))

    # Current page button (noop)
    nav_row.append(InlineKeyboardButton(f"Page {page}", callback_data="noop"))

    if category:
        if subcategory:
            nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"category_page|{category}|{subcategory}|{page+1}"))
        else:
            nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"category_page|{category}||{page+1}"))
    else:
        nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"catalog_page|{page+1}"))

    keyboard.append(nav_row)

    if category:
        cat_label = _display_label(category)
        sub_label = f" — {_display_label(subcategory)}" if subcategory else ""
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
        keyboard.append([InlineKeyboardButton(label, callback_data=f"category|{cat}")])

    text = "CATEGORIES — CHOOSE A CATEGORY TO BROWSE:"
    reply_markup = InlineKeyboardMarkup(keyboard)
    return text, reply_markup


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show the categories menu as the first screen. Delete any previously displayed product media first.
    """
    # Delete previous product display messages (if any)
    await _delete_last_displayed_messages(context)

    # When user explicitly requests /menu, clear any recorded origin so we start fresh
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
    page: Optional[int] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
) -> None:
    """
    Show a specific page of the catalog as buttons.
    """
    query = update.callback_query

    # Parse page if not provided
    if page is None:
        parsed_page = 1
        try:
            data = (query.data or "").strip() if query else ""
            parts = data.split("|") if data else []
            # Expect patterns like: category_page|CATEGORY|SUBCATEGORY|<page> or catalog_page|<page>
            if parts:
                # find last numeric token
                for token in reversed(parts):
                    if token.isdigit():
                        parsed_page = int(token)
                        break
        except Exception:
            logger.exception("Failed to parse page from callback data; defaulting to 1")
            parsed_page = 1
        page = parsed_page

    # Acknowledge early if we have a query
    if query:
        try:
            await query.answer()
        except Exception:
            logger.exception("Failed to answer catalog pagination callback (non-fatal)")

    # Delete previous product display messages (if any)
    try:
        await _delete_last_displayed_messages(context)
    except Exception:
        logger.exception("Failed to delete last displayed messages before pagination")

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
        context.user_data["selected_category"] = category
    else:
        context.user_data.pop("selected_category", None)
    if subcategory:
        context.user_data["selected_subcategory"] = subcategory
    else:
        context.user_data.pop("selected_subcategory", None)

    text, reply_markup = await _build_catalog_page(context, page, category=category, subcategory=subcategory)
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
        if query and query.message and getattr(query.message.from_user, "is_bot", False) and query.message.text:
            msg_text = (query.message.text or "").lower()
            if any(k in msg_text for k in ("catalog", "categories")):
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
async def handle_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        logger.exception("Failed to answer category callback (non-fatal)")

    data = (query.data or "").strip()
    parts = data.split("|", 1)
    if len(parts) < 2:
        try:
            await query.answer("Invalid category", show_alert=True)
        except Exception:
            logger.exception("Failed to notify user about invalid category")
        return

    cat = parts[1].strip().upper()
    if cat not in CATEGORIES:
        try:
            await query.answer("Unknown category", show_alert=True)
        except Exception:
            logger.exception("Failed to notify user about unknown category")
        return

    subs = CATEGORIES.get(cat, []) or []
    display_subs = [s for s in subs if s and s.strip() and s.strip().upper() != "ALL"]

    if not display_subs:
        context.user_data["selected_category"] = cat
        context.user_data.pop("selected_subcategory", None)
        try:
            await handle_catalog_pagination(update, context, page=1, category=cat)
        except Exception:
            try:
                await handle_catalog_pagination(update, context, page=1)
            except Exception:
                logger.exception("Failed to open category page for %s", cat)
        return

    buttons: List[List[InlineKeyboardButton]] = []
    buttons.append([InlineKeyboardButton("ALL", callback_data=f"category_sub|{cat}|")])
    for s in subs:
        buttons.append([InlineKeyboardButton(_display_label(s), callback_data=f"category_sub|{cat}|{s}")])

    buttons.append([InlineKeyboardButton("⬅️ BACK TO CATEGORIES", callback_data="menu_categories")])

    try:
        await query.message.edit_text(f"{_display_label(cat)} — CHOOSE SUBCATEGORY:", reply_markup=InlineKeyboardMarkup(buttons))
    except BadRequest as e:
        if "Message is not modified" in str(e):
            logger.debug("Category message already up to date")
        else:
            logger.exception("Failed to edit message to show subcategories")
    except Exception:
        logger.exception("Unexpected error while showing subcategories")


async def handle_category_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Callback data: category_sub|<CAT>|<SUB>
    If SUB is empty, show all products in CAT. Otherwise show products filtered by CAT/SUB.
    """
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        logger.exception("Failed to answer category_sub callback (non-fatal)")

    data = (query.data or "").strip()
    parts = data.split("|")
    if len(parts) < 2:
        try:
            await query.answer("Invalid request", show_alert=True)
        except Exception:
            logger.exception("Failed to notify user about invalid category_sub request")
        return

    cat = parts[1].strip().upper()
    sub = parts[2].strip().upper() if len(parts) >= 3 and parts[2] else None

    if cat not in CATEGORIES:
        try:
            await query.answer("Unknown category", show_alert=True)
        except Exception:
            logger.exception("Failed to notify user about unknown category")
        return

    if sub and sub not in CATEGORIES.get(cat, []):
        sub = None

    context.user_data["selected_category"] = cat
    if sub:
        context.user_data["selected_subcategory"] = sub.strip().upper().replace(" ", "").replace("_", "")
    else:
        context.user_data.pop("selected_subcategory", None)

    await handle_catalog_pagination(update, context, page=1, category=cat, subcategory=context.user_data.get("selected_subcategory"))


async def handle_menu_categories_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        logger.exception("Failed to answer menu_categories callback (non-fatal)")

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