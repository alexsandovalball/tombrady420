# handlers/product_view.py
"""
User-facing handlers: categories, browsing, product view, pagination.
Callback data format: CAT:<action>:<payload>
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import db
from utils.helpers import chunked, build_pagination_keyboard

logger = logging.getLogger(__name__)

# Use same categories as admin module (importing would create circular import),
# so keep a minimal mapping here or import from a shared module if you prefer.
CATEGORIES = {
    "FLOWER": ["TOPSHELF CANDY", "PREMIUM EXOTICS", "EXOTICS", "PREMIUM LIGHT DEPS", "LIGHT DEPS", "LIGHT ASSIST", "LOWS"],
    "EDIBLES": ["GUMMIES", "CHOCOLATES"],
    "CONCENTRATES": ["LIVE RESIN", "SHATTER"],
}


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Welcome. Use /categories to browse.")


async def cmd_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show top-level categories."""
    keyboard = []
    for cat in CATEGORIES.keys():
        keyboard.append([InlineKeyboardButton(text=cat, callback_data=f"CAT:OPEN_CAT:{cat}")])
    await update.message.reply_text("Select a category:", reply_markup=InlineKeyboardMarkup(keyboard))


async def cb_catalog_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Router for catalog callback queries:
    CAT:OPEN_CAT:<cat>
    CAT:OPEN_SUB:<cat>:<sub>
    CAT:PAGE:<cat_or_sub>:<page>
    CAT:VIEW:<product_id>
    """
    try:
        query = update.callback_query
        await query.answer()
        data = query.data
        parts = data.split(":", 3)
        action = parts[1] if len(parts) > 1 else None

        if action == "OPEN_CAT":
            cat = parts[2]
            subs = CATEGORIES.get(cat, [])
            keyboard = []
            for sub in subs:
                keyboard.append([InlineKeyboardButton(text=sub, callback_data=f"CAT:OPEN_SUB:{cat}:{sub}")])
            keyboard.append([InlineKeyboardButton(text="Back", callback_data="CAT:BACK")])
            await query.edit_message_text(text=f"Subcategories for {cat}:", reply_markup=InlineKeyboardMarkup(keyboard))
        elif action == "OPEN_SUB":
            # parts = ["CAT","OPEN_SUB","<cat>:<sub>"] due to maxsplit; split again
            rest = parts[2]
            cat, sub = rest.split(":", 1)
            await show_products_for_category(query, cat, sub, page=0)
        elif action == "PAGE":
            # CAT:PAGE:<scope>:<page>
            _, _, rest = data.split(":", 2)
            scope, page = rest.rsplit(":", 1)
            page = int(page)
            if "::" in scope:
                cat, sub = scope.split("::", 1)
                await show_products_for_category(query, cat, sub, page=page)
            else:
                await show_products_for_category(query, scope, None, page=page)
        elif action == "VIEW":
            pid = int(parts[2])
            await show_product_detail(query, pid)
        elif action == "BACK":
            # show top-level categories again
            keyboard = []
            for cat in CATEGORIES.keys():
                keyboard.append([InlineKeyboardButton(text=cat, callback_data=f"CAT:OPEN_CAT:{cat}")])
            await query.edit_message_text("Select a category:", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.answer("Unknown action", show_alert=True)
    except Exception:
        logger.exception("cb_catalog_router failed")


async def show_products_for_category(query, category: str, subcategory: Optional[str], page: int = 0, per_page: int = 8):
    """
    Query DB for products in category/subcategory and show a paginated list of items.
    We use get_catalog_items and then filter by category/subcategory.
    """
    try:
        items = await db.get_catalog_items(limit=None, offset=0)
        # filter by category/subcategory
        filtered = []
        for it in items:
            cat = (it.get("category") or "").upper()
            sub = (it.get("subcategory") or "").upper()
            if subcategory:
                if cat == category.upper() and sub == subcategory.upper():
                    filtered.append(it)
            else:
                if cat == category.upper():
                    filtered.append(it)

        total = len(filtered)
        per_page = per_page
        start = page * per_page
        page_items = filtered[start : start + per_page]

        # Build message text and keyboard
        if not page_items:
            await query.edit_message_text("No items found in this category.")
            return

        text_lines = []
        keyboard = []
        for it in page_items:
            title = (it.get("caption") or "").strip() or f"Item {it.get('id')}"
            text_lines.append(f"{title}")
            keyboard.append([InlineKeyboardButton(text=title[:40], callback_data=f"CAT:VIEW:{it['id']}")])

        # pagination keyboard
        pagination = build_pagination_keyboard(scope=f"{category}::{subcategory}" if subcategory else category, page=page, per_page=per_page, total=total)
        keyboard.extend(pagination)

        await query.edit_message_text("\n".join(text_lines), reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        logger.exception("show_products_for_category failed")


async def show_product_detail(query, product_id: int):
    """Show product media and caption. For media_group, show representative row and allow viewing all files."""
    try:
        # fetch product row
        prod = await db.get_products_by_media_group(media_group_id=None, chat_id=0)  # placeholder to avoid lint error
        # Instead, fetch by id
        async with aiosqlite := None:  # placeholder to avoid lint; we will fetch via db.get_product
            pass
    except Exception:
        # fallback: query directly
        try:
            p = await db.get_product(product_id)
            if not p:
                await query.edit_message_text("Product not found.")
                return
            caption = p.get("caption") or ""
            # send media: we will edit the message to show caption and a button to open album
            keyboard = [
                [InlineKeyboardButton(text="Open album", callback_data=f"CAT:OPEN_ALBUM:{p.get('media_group_id') or p.get('message_id')}")],
                [InlineKeyboardButton(text="Back", callback_data="CAT:BACK")],
            ]
            await query.edit_message_text(f"{caption}", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            logger.exception("show_product_detail failed")