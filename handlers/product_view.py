# handlers/product_view.py
"""
User-facing handlers: categories, browsing, product view, pagination.

Refactored to use utils.helpers.short_caption for consistent label truncation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Dict, Any, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import ContextTypes

from config.taxonomy import CATEGORIES
from database import db
from utils.helpers import build_pagination_keyboard, short_caption
from utils.taxonomy_utils import canonicalize_key, get_display_name, list_subcategories

logger = logging.getLogger(__name__)

_user_locks: Dict[int, asyncio.Lock] = {}
PAGE_SIZE = 8


def _display_name(key: Optional[str]) -> str:
    return get_display_name(key)


def _ensure_user_lock(user_id: int) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message:
            await update.message.reply_text("Welcome. Use /categories to browse the catalog.")
    except Exception:
        logger.exception("cmd_start failed")


async def cmd_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        keyboard = [
            [InlineKeyboardButton(text=_display_name(cat), callback_data=f"CAT:OPEN_CAT:{cat}")]
            for cat in CATEGORIES.keys()
        ]
        if update.message:
            await update.message.reply_text("Select a category:", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        logger.exception("cmd_categories failed")


async def cb_catalog_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    # Remove the leading "CAT:" and parse the rest
    _, _, rest = data.partition(":")
    action, _, payload = rest.partition(":")
    action = action or ""
    payload = payload or ""

    try:
        if action == "OPEN_CAT":
            cat = canonicalize_key(payload)
            subs = list_subcategories(cat)
            keyboard = [
                [InlineKeyboardButton(text=_display_name(sub), callback_data=f"CAT:OPEN_SUB:{cat}::{sub}")]
                for sub in subs
            ]
            keyboard.append([InlineKeyboardButton(text="Back", callback_data="CAT:BACK")])
            await query.edit_message_text(f"Subcategories for {_display_name(cat)}:", reply_markup=InlineKeyboardMarkup(keyboard))

        elif action == "OPEN_SUB":
            if "::" not in payload:
                await query.answer("Malformed subcategory payload", show_alert=True)
                return
            cat_raw, sub_raw = payload.split("::", 1)
            cat = canonicalize_key(cat_raw)
            sub = canonicalize_key(sub_raw)
            await show_products_for_category(query, cat, sub, page=0)

        elif action == "PAGE":
            if "::" not in payload:
                await query.answer("Malformed page payload", show_alert=True)
                return
            scope, page_str = payload.rsplit("::", 1)
            try:
                page = int(page_str)
            except Exception:
                page = 0
            if "::" in scope:
                cat_raw, sub_raw = scope.split("::", 1)
                cat = canonicalize_key(cat_raw)
                sub = canonicalize_key(sub_raw)
                await show_products_for_category(query, cat, sub, page=page)
            else:
                await show_products_for_category(query, canonicalize_key(scope), None, page=page)

        elif action == "VIEW":
            try:
                pid = int(payload)
            except Exception:
                await query.answer("Invalid product id", show_alert=True)
                return
            await show_product_detail(query, pid)

        elif action == "OPEN_ALBUM":
            mg = payload
            try:
                items = await db.get_products_by_media_group(mg, None)
                if items:
                    pid = items[0].get("id")
                    if pid:
                        await show_product_detail(query, int(pid))
                        return
            except Exception:
                logger.exception("OPEN_ALBUM: DB lookup failed for media_group %s", mg)
            await query.answer("Album not found", show_alert=True)

        elif action == "BACK":
            keyboard = [
                [InlineKeyboardButton(text=_display_name(cat), callback_data=f"CAT:OPEN_CAT:{cat}")]
                for cat in CATEGORIES.keys()
            ]
            await query.edit_message_text("Select a category:", reply_markup=InlineKeyboardMarkup(keyboard))

        else:
            try:
                await query.answer("Unknown action", show_alert=True)
            except Exception:
                pass

    except Exception:
        logger.exception("cb_catalog_router failed")


async def show_products_for_category(
    query: CallbackQuery,
    category: str,
    subcategory: Optional[str],
    page: int = 0,
    per_page: int = PAGE_SIZE,
) -> None:
    try:
        cat_key = canonicalize_key(category) if category else ""
        sub_key = canonicalize_key(subcategory) if subcategory else None

        # Fetch a reasonable window and filter in memory (DB filtering can be added later)
        fetch_limit = max(per_page * (page + 1) * 3, per_page)
        items = await db.get_catalog_items(limit=fetch_limit)
        if not items:
            await query.edit_message_text("No items available in the catalog.")
            return

        filtered: List[Dict[str, Any]] = []
        for it in items:
            it_cat = (it.get("category") or "").strip().upper()
            it_sub = (it.get("subcategory") or "").strip().upper()
            if (cat_key or "").upper() and it_cat != (cat_key or "").upper():
                continue
            if sub_key and it_sub != sub_key.upper():
                continue
            filtered.append(it)

        total = len(filtered)
        start = max(0, page) * per_page
        page_items = filtered[start : start + per_page]

        if not page_items:
            await query.edit_message_text("No items found in this category.")
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

        pagination = build_pagination_keyboard(scope=scope, page=page, per_page=per_page, total=total)
        keyboard.extend(pagination)

        header = f"{_display_name(category)}"
        if subcategory:
            header = f"{header} — {_display_name(subcategory)}"

        await query.edit_message_text(f"{header}\n\n" + "\n".join(text_lines), reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception:
        logger.exception("show_products_for_category failed")


async def show_product_detail(query: CallbackQuery, product_id: int) -> None:
    try:
        p = await db.get_product(product_id)
        if not p:
            try:
                await query.edit_message_text("Product not found.")
            except Exception:
                logger.exception("Failed to notify product not found")
            return

        caption = p.get("caption") or "(No caption)"
        media_group_id = p.get("media_group_id")
        message_id = p.get("message_id")
        album_payload = media_group_id if media_group_id else (str(message_id) if message_id is not None else "")

        keyboard = [
            [InlineKeyboardButton(text="Open album", callback_data=f"CAT:OPEN_ALBUM:{album_payload}")],
            [InlineKeyboardButton(text="Back", callback_data="CAT:BACK")],
        ]

        await query.edit_message_text(text=caption, reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception:
        logger.exception("show_product_detail failed")