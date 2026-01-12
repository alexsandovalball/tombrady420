# handlers/product_view.py
"""
User-facing handlers: categories, browsing, product view, pagination.

Refactored to:
 - send media (photo/album) to the user when viewing a product, rather than editing text.
 - record displayed messages in context.user_data['last_displayed_messages'] so they can be cleaned up.
 - keep previous pagination and category behavior.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Dict, Any, List

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    CallbackQuery,
)
from telegram.ext import ContextTypes

from config.taxonomy import CATEGORIES
from database import db
from utils.helpers import build_pagination_keyboard, short_caption, delete_last_displayed_messages
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
            # payload is the category key (canonical or raw)
            cat = canonicalize_key(payload)
            subs = list_subcategories(cat)
            keyboard = [
                [InlineKeyboardButton(text=_display_name(sub), callback_data=f"CAT:OPEN_SUB:{cat}::{sub}")]
                for sub in subs
            ]
            keyboard.append([InlineKeyboardButton(text="Back", callback_data="CAT:BACK")])
            await query.edit_message_text(f"Subcategories for {_display_name(cat)}:", reply_markup=InlineKeyboardMarkup(keyboard))

        elif action == "OPEN_SUB":
            # payload format: "<CATEGORY>::<SUBCATEGORY>"
            if "::" not in payload:
                await query.answer("Malformed subcategory payload", show_alert=True)
                return
            cat_raw, sub_raw = payload.split("::", 1)
            cat = canonicalize_key(cat_raw)
            sub = canonicalize_key(sub_raw)
            await show_products_for_category(query, context, cat, sub, page=0)

        elif action == "PAGE":
            # payload format: "<SCOPE>::<PAGE>"
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
            # payload is media_group_id or message_id; delegate to product detail flow that can handle albums
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
    context: ContextTypes.DEFAULT_TYPE,
    category: str,
    subcategory: Optional[str],
    page: int = 0,
    per_page: int = PAGE_SIZE,
) -> None:
    """
    Show a paginated list of products for a category (and optional subcategory).
    `query` is expected to be a CallbackQuery object so we can edit the message in-place.
    """
    try:
        # Normalize keys for comparison
        cat_key = canonicalize_key(category) if category else ""
        sub_key = canonicalize_key(subcategory) if subcategory else None

        # Fetch a reasonably sized window and filter in Python
        # We fetch more than needed to allow pagination after filtering
        fetch_limit = max((page + 1) * per_page * 3, per_page)
        items = await db.get_catalog_items(limit=fetch_limit)
        if not items:
            await query.edit_message_text("No items available in the catalog.")
            return

        # Filter by category/subcategory (case-insensitive canonical)
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

        # Build message text and keyboard
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


async def show_product_detail(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    """
    Show product media and caption. For media_group, send the whole album (once).
    Records sent message ids in context.user_data['last_displayed_messages'] so they can
    be deleted by catalog navigation.
    """
    try:
        p = await db.get_product(product_id)
        if not p:
            try:
                await query.edit_message_text("Product not found.")
            except Exception:
                logger.exception("Failed to notify product not found")
            return

        # Determine destination chat id: prefer user who clicked the callback
        dest_chat: Optional[int] = None
        if query.from_user:
            dest_chat = query.from_user.id
        elif getattr(query, "message", None) and getattr(query.message, "chat", None):
            dest_chat = query.message.chat.id
        else:
            logger.error("show_product_detail: no destination to send product media")
            try:
                await query.answer("Cannot show product (no destination).", show_alert=True)
            except Exception:
                pass
            return

        # Delete previously displayed product messages to keep chat tidy
        try:
            await delete_last_displayed_messages(context)
        except Exception:
            logger.exception("Failed to delete last displayed messages before showing product detail")

        caption = p.get("caption") or "(No caption)"
        media_group_id = p.get("media_group_id")
        message_id = p.get("message_id")
        file_id = p.get("file_id")
        file_type = (p.get("file_type") or "").lower()

        sent_records: List[Dict[str, int]] = []

        if media_group_id:
            # Fetch all media for the media_group
            media_items = await db.get_media_group_media(media_group_id, p.get("chat_id"))
            if media_items:
                # Build InputMedia list
                input_media = []
                first = True
                for mi in media_items:
                    fid = mi.get("file_id")
                    ftype = (mi.get("file_type") or "").lower()
                    # Use caption only on first item (Telegram API allows only one caption per album)
                    item_caption = caption if first else None
                    first = False
                    if ftype.startswith("video"):
                        input_media.append(InputMediaVideo(media=fid, caption=item_caption))
                    elif ftype.startswith("audio") or ftype.startswith("voice"):
                        # audio/voice can't be part of send_media_group with photos; fall back to document
                        input_media.append(InputMediaDocument(media=fid, caption=item_caption))
                    else:
                        input_media.append(InputMediaPhoto(media=fid, caption=item_caption))

                try:
                    msgs = await context.bot.send_media_group(chat_id=dest_chat, media=input_media)
                    # record sent message ids so we can delete them later
                    for m in msgs:
                        sent_records.append({"chat_id": m.chat.id, "message_id": m.message_id})
                except Exception:
                    # Fallback: send items one by one
                    logger.exception("send_media_group failed; falling back to sending items individually")
                    first_caption = caption
                    for mi in media_items:
                        fid = mi.get("file_id")
                        try:
                            if (mi.get("file_type") or "").lower().startswith("video"):
                                m = await context.bot.send_video(chat_id=dest_chat, video=fid, caption=first_caption)
                            else:
                                m = await context.bot.send_photo(chat_id=dest_chat, photo=fid, caption=first_caption)
                            sent_records.append({"chat_id": m.chat.id, "message_id": m.message_id})
                        except Exception:
                            logger.exception("Failed to send media item %s", fid)
                        first_caption = None
        else:
            # Single-file product
            try:
                if file_type.startswith("video"):
                    m = await context.bot.send_video(chat_id=dest_chat, video=file_id, caption=caption)
                elif file_type.startswith("audio") or file_type.startswith("voice"):
                    m = await context.bot.send_audio(chat_id=dest_chat, audio=file_id, caption=caption)
                else:
                    m = await context.bot.send_photo(chat_id=dest_chat, photo=file_id, caption=caption)
                sent_records.append({"chat_id": m.chat.id, "message_id": m.message_id})
            except Exception:
                logger.exception("Failed to send product media for product_id=%s", product_id)
                # fallback: edit callback message with caption if we can't send media
                try:
                    await query.edit_message_text(text=caption)
                except Exception:
                    logger.exception("Failed to edit message as fallback")

        # Save references to last displayed messages for cleanup later
        try:
            if sent_records:
                context.user_data["last_displayed_messages"] = [(r["chat_id"], r["message_id"]) for r in sent_records]
        except Exception:
            logger.exception("Failed to record last_displayed_messages in user_data")

    except Exception:
        logger.exception("show_product_detail failed")