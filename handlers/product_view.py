# handlers/product_view.py
"""
User-facing handlers: categories, browsing, product view, pagination.

Simplified version to address media display issues:
 - Never auto-open a product when entering a subcategory (user must click "View").
 - Products are displayed as text-only (caption) without media files.
 - Safeguards added to prevent "Message is not modified" errors during navigation.
 - Per-user asyncio.Lock to avoid race conditions.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Dict, Any, List, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
)
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest

from config.taxonomy import CATEGORIES
from database import db
from utils.helpers import build_pagination_keyboard, short_caption, delete_last_displayed_messages
from utils.taxonomy_utils import canonicalize_key, get_display_name, list_subcategories
from utils.formatting import escape_markdown_v2

logger = logging.getLogger(__name__)

_user_locks: Dict[int, asyncio.Lock] = {}
PAGE_SIZE = 8


def _ensure_user_lock(user_id: int) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


async def _safe_edit_message(query: CallbackQuery, text: str, reply_markup=None) -> bool:
    """
    Safely edit a message, catching "Message is not modified" errors.
    Returns True if edit succeeded, False if message was not modified or error occurred.
    """
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup)
        return True
    except BadRequest as e:
        error_msg = str(e).lower()
        if "message is not modified" in error_msg or "message_not_modified" in error_msg:
            logger.debug("Message not modified, skipping edit")
            return False
        else:
            # Re-raise other BadRequest exceptions
            logger.exception("Failed to edit message")
            raise


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message:
            await update.message.reply_text("Welcome. Use /categories to browse the catalog.")
    except Exception:
        logger.exception("cmd_start failed")


async def cmd_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        keyboard = [
            [InlineKeyboardButton(text=get_display_name(cat), callback_data=f"CAT:OPEN_CAT:{cat}")]
            for cat in CATEGORIES.keys()
        ]
        if update.message:
            await update.message.reply_text("Select a category:", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        logger.exception("cmd_categories failed")


async def cb_catalog_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Router for catalog-related callback queries.
    Accepts callback_data starting with 'CAT:' and dispatches to handlers.
    """
    query: Optional[CallbackQuery] = update.callback_query
    if not query:
        return

    try:
        await query.answer()
    except Exception:
        pass

    data = (query.data or "").strip()
    if not data.startswith("CAT:"):
        try:
            await query.answer("Invalid catalog action", show_alert=True)
        except Exception:
            pass
        return

    # Remove prefix and split into action and payload
    _, _, rest = data.partition(":")
    action, _, payload = rest.partition(":")
    action = action or ""
    payload = payload or ""

    try:
        if action == "OPEN_CAT":
            cat = canonicalize_key(payload)
            subs = list_subcategories(cat)
            keyboard = [
                [InlineKeyboardButton(text=get_display_name(sub), callback_data=f"CAT:OPEN_SUB:{cat}::{sub}")]
                for sub in subs
            ]
            keyboard.append([InlineKeyboardButton(text="Back", callback_data="CAT:BACK")])
            await _safe_edit_message(query, f"Subcategories for {get_display_name(cat)}:", reply_markup=InlineKeyboardMarkup(keyboard))

        elif action == "OPEN_SUB":
            # IMPORTANT: show the product list only. Do NOT auto-open a product.
            if "::" not in payload:
                await query.answer("Malformed subcategory payload", show_alert=True)
                return
            cat_raw, sub_raw = payload.split("::", 1)
            cat = canonicalize_key(cat_raw)
            sub = canonicalize_key(sub_raw)
            await show_products_for_category(query, context, cat, sub, page=1)

        elif action == "PAGE":
            # payload: "<SCOPE>::<PAGE>"
            if "::" not in payload:
                await query.answer("Malformed page payload", show_alert=True)
                return
            scope, page_str = payload.rsplit("::", 1)
            try:
                page = int(page_str)
            except Exception:
                page = 1
            if "::" in scope:
                cat_raw, sub_raw = scope.split("::", 1)
                cat = canonicalize_key(cat_raw)
                sub = canonicalize_key(sub_raw)
                await show_products_for_category(query, context, cat, sub, page=page)
            else:
                await show_products_for_category(query, context, canonicalize_key(scope), None, page=page)

        elif action == "VIEW":
            # payload is product id
            try:
                pid = int(payload)
            except Exception:
                await query.answer("Invalid product id", show_alert=True)
                return
            await show_product_detail(query, context, pid)

        elif action == "OPEN_ALBUM":
            mg = payload
            try:
                items = await db.get_products_by_media_group(mg, None)
                if items:
                    pid = items[0].get("id")
                    if pid:
                        await show_product_detail(query, context, int(pid))
                        return
            except Exception:
                logger.exception("OPEN_ALBUM: DB lookup failed for media_group %s", mg)
            await query.answer("Album not found", show_alert=True)

        elif action == "BACK":
            # show top-level categories again
            keyboard = [
                [InlineKeyboardButton(text=get_display_name(cat), callback_data=f"CAT:OPEN_CAT:{cat}")]
                for cat in CATEGORIES.keys()
            ]
            await _safe_edit_message(query, "Select a category:", reply_markup=InlineKeyboardMarkup(keyboard))

        else:
            try:
                await query.answer("Unknown action", show_alert=True)
            except Exception:
                pass

    except Exception:
        logger.exception("cb_catalog_router failed")


async def show_products_for_category(
    query: CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    category: str,
    subcategory: Optional[str],
    page: int = 1,
    per_page: int = PAGE_SIZE,
) -> None:
    """
    Show a paginated list of products for a category (and optional subcategory).
    Builds an inline keyboard of product "View" buttons (user clicks to see media).
    """
    try:
        if page < 1:
            page = 1
        cat_key = canonicalize_key(category) if category else ""
        sub_key = canonicalize_key(subcategory) if subcategory else None

        fetch_limit = page * per_page * 3
        items = await db.get_catalog_items(limit=fetch_limit)
        if not items:
            await _safe_edit_message(query, "No items available in the catalog.")
            return

        filtered: List[Dict[str, Any]] = []
        for it in items:
            it_cat = (it.get("category") or "").strip().upper()
            it_sub = (it.get("subcategory") or "").strip().upper()
            if cat_key and it_cat != cat_key.upper():
                continue
            if sub_key and it_sub != sub_key.upper():
                continue
            filtered.append(it)

        total = len(filtered)
        start = (page - 1) * per_page
        page_items = filtered[start : start + per_page]

        if not page_items:
            await _safe_edit_message(query, "No items found in this category.")
            return

        text_lines: List[str] = []
        keyboard: List[List[InlineKeyboardButton]] = []
        for it in page_items:
            title = (it.get("caption") or "").strip() or f"Item {it.get('id')}"
            btn_label = short_caption(title, length=40)
            keyboard.append([InlineKeyboardButton(text=btn_label, callback_data=f"CAT:VIEW:{it['id']}")])
            text_lines.append(f"• {title}")

        scope = ""
        if cat_key:
            scope = cat_key
            if sub_key:
                scope = f"{cat_key}::{sub_key}"

        pagination = build_pagination_keyboard(scope=scope, page=page - 1, per_page=per_page, total=total)
        keyboard.extend(pagination)

        header = f"{get_display_name(category)}"
        if subcategory:
            header = f"{header} — {get_display_name(subcategory)}"

        await _safe_edit_message(query, f"{header}\n\n" + "\n".join(text_lines), reply_markup=InlineKeyboardMarkup(keyboard))

        # Persist navigation context so product detail can show proper "Back" behavior
        try:
            if query.message and query.message.chat:
                context.user_data["origin_catalog_message"] = {
                    "chat_id": query.message.chat.id,
                    "message_id": query.message.message_id,
                }
            context.user_data["selected_category"] = cat_key or None
            context.user_data["selected_subcategory"] = sub_key or None
            context.user_data["catalog_page"] = page
        except Exception:
            logger.debug("Failed to persist catalog navigation context")
    except Exception:
        logger.exception("show_products_for_category failed")




async def show_product_detail(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    """
    Show product caption as text only (no media). Simplified to avoid media display issues.
    """
    try:
        p = await db.get_product(product_id)
        if not p:
            try:
                await _safe_edit_message(query, "Product not found.")
            except Exception:
                logger.exception("Failed to notify product not found")
            return

        dest_chat: Optional[int] = None
        if query.from_user:
            dest_chat = query.from_user.id
        elif getattr(query, "message", None) and getattr(query.message, "chat", None):
            dest_chat = query.message.chat.id

        if dest_chat is None:
            logger.error("show_product_detail: no destination to send product")
            try:
                await query.answer("Cannot show product (no destination).", show_alert=True)
            except Exception:
                pass
            return

        user_lock = _ensure_user_lock(dest_chat)
        async with user_lock:
            # Clean up prior product display
            try:
                await delete_last_displayed_messages(context)
            except Exception:
                logger.exception("Failed to delete last displayed messages before showing product detail")

            raw_caption = p.get("caption") or "(No caption)"
            caption_parse_mode = (p.get("caption_parse_mode") or "").upper() or None
            parse_mode = None
            caption_for_api = raw_caption
            if caption_parse_mode == "MARKDOWN_V2":
                caption_for_api = escape_markdown_v2(raw_caption)
                parse_mode = ParseMode.MARKDOWN_V2
            elif caption_parse_mode == "HTML":
                caption_for_api = raw_caption
                parse_mode = ParseMode.HTML

            sent_records: List[Tuple[int, int]] = []

            # Send only the caption as a text message (no media)
            try:
                m = await context.bot.send_message(
                    chat_id=dest_chat,
                    text=caption_for_api,
                    parse_mode=parse_mode
                )
                sent_records.append((m.chat.id, m.message_id))
                logger.info("Successfully sent caption for product %s", product_id)
            except Exception:
                logger.exception("Failed to send product caption for product_id=%s", product_id)

            # Send navigation control message
            try:
                cat = context.user_data.get("selected_category")
                sub = context.user_data.get("selected_subcategory")
                page = context.user_data.get("catalog_page", 1) or 1
                scope = ""
                if cat:
                    scope = cat
                    if sub:
                        scope = f"{cat}::{sub}"

                nav_buttons = []
                if scope:
                    nav_buttons.append(InlineKeyboardButton("⬅️ Back", callback_data=f"CAT:PAGE:{scope}::{page}"))
                else:
                    nav_buttons.append(InlineKeyboardButton("⬅️ Back", callback_data="CAT:BACK"))
                nav_buttons.append(InlineKeyboardButton("🏠 Categories", callback_data="CAT:BACK"))

                ctrl = await context.bot.send_message(
                    chat_id=dest_chat,
                    text="Use the buttons below to return to the catalog.",
                    reply_markup=InlineKeyboardMarkup([nav_buttons]),
                )
                sent_records.append((ctrl.chat.id, ctrl.message_id))
            except Exception:
                logger.exception("Failed to send control message after product caption")

            # Record displayed messages for cleanup
            try:
                if sent_records:
                    # Remove duplicates
                    uniq = []
                    seen = set()
                    for c, m in sent_records:
                        if (c, m) not in seen:
                            seen.add((c, m))
                            uniq.append((c, m))
                    context.user_data["last_displayed_messages"] = uniq
            except Exception:
                logger.exception("Failed to record last_displayed_messages in user_data")
    except Exception:
        logger.exception("show_product_detail failed")