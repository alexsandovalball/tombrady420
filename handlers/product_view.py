# product_view.py
"""
User-facing handlers: categories, browsing, product view, pagination.
Callback data format: CAT:<action>:<payload>
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import db
from utils.helpers import build_pagination_keyboard

logger = logging.getLogger(__name__)

# Category structure (same as admin)
CATEGORIES = {
    "FLOWER": ["TOPSHELF CANDY", "PREMIUM EXOTICS", "EXOTICS", "PREMIUM LIGHT DEPS", "LIGHT DEPS", "LIGHT ASSIST", "LOWS"],
    "EDIBLES": ["GUMMIES", "CHOCOLATES"],
    "CONCENTRATES": ["LIVE RESIN", "SHATTER"],
}


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Welcome. Use /categories to browse.")


async def cmd_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton(text=cat, callback_data=f"CAT:OPEN_CAT:{cat}")]
        for cat in CATEGORIES.keys()
    ]
    await update.message.reply_text("Select a category:", reply_markup=InlineKeyboardMarkup(keyboard))


async def cb_catalog_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split(":", 3)
    action = parts[1] if len(parts) > 1 else None

    try:
        if action == "OPEN_CAT":
            cat = parts[2]
            subs = CATEGORIES.get(cat, [])
            keyboard = [
                [InlineKeyboardButton(text=sub, callback_data=f"CAT:OPEN_SUB:{cat}:{sub}")]
                for sub in subs
            ]
            keyboard.append([InlineKeyboardButton(text="Back", callback_data="CAT:BACK")])
            await query.edit_message_text(f"Subcategories for {cat}:", reply_markup=InlineKeyboardMarkup(keyboard))

        elif action == "OPEN_SUB":
            cat, sub = parts[2].split(":", 1)
            await show_products_for_category(query, cat, sub, page=0)

        elif action == "PAGE":
            scope, page = parts[2].rsplit(":", 1)
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
            keyboard = [
                [InlineKeyboardButton(text=cat, callback_data=f"CAT:OPEN_CAT:{cat}")]
                for cat in CATEGORIES.keys()
            ]
            await query.edit_message_text("Select a category:", reply_markup=InlineKeyboardMarkup(keyboard))

        else:
            await query.answer("Unknown action", show_alert=True)

    except Exception:
        logger.exception("cb_catalog_router failed")


async def show_products_for_category(query, category: str, subcategory: Optional[str], page: int = 0, per_page: int = 8):
    try:
        items = await db.get_catalog_items()
        filtered = [
            it for it in items
            if (it.get("category") or "").upper() == category.upper()
            and (subcategory is None or (it.get("subcategory") or "").upper() == subcategory.upper())
        ]

        total = len(filtered)
        start = page * per_page
        page_items = filtered[start:start + per_page]

        if not page_items:
            await query.edit_message_text("No items found in this category.")
            return

        text_lines = []
        keyboard = []
        for it in page_items:
            title = (it.get("caption") or "").strip() or f"Item {it.get('id')}"
            text_lines.append(f"{title}")
            keyboard.append([InlineKeyboardButton(text=title[:40], callback_data=f"CAT:VIEW:{it['id']}")])

        scope = f"{category}::{subcategory}" if subcategory else category
        pagination = build_pagination_keyboard(scope=scope, page=page, per_page=per_page, total=total)
        keyboard.extend(pagination)

        await query.edit_message_text("\n".join(text_lines), reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception:
        logger.exception("show_products_for_category failed")


async def show_product_detail(query, product_id: int):
    """
    Show product media and caption. For media_group, show representative row and allow viewing all files.
    """
    try:
        p = await db.get_product(product_id)
        if not p:
            await query.edit_message_text("Product not found.")
            return

        caption = p.get("caption") or "(No caption)"
        media_group_id = p.get("media_group_id")
        message_id = p.get("message_id")

        keyboard = [
            [InlineKeyboardButton(text="Open album", callback_data=f"CAT:OPEN_ALBUM:{media_group_id or message_id}")],
            [InlineKeyboardButton(text="Back", callback_data="CAT:BACK")]
        ]

        await query.edit_message_text(text=caption, reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception:
        logger.exception("show_product_detail failed")