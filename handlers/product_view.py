# handlers/product_view.py
"""
User-facing handlers: categories, browsing, product view, pagination.

This module provides:
- /categories menu
- category -> subcategory -> paginated product lists
- product detail view that prefers copying original messages for albums,
  falls back to send_media_group or per-item sends, and records displayed
  messages for later cleanup.

Design choices:
- Pagination is 1-based for user-facing callbacks; build_pagination_keyboard
  is called with zero-based page index (page - 1).
- Defensive logging and fallbacks to avoid partial albums, duplicate copies,
  and "Message is not modified" errors.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Dict, Any, List, Tuple

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
from telegram.constants import ParseMode

from config.taxonomy import CATEGORIES
from database import db
from utils.helpers import build_pagination_keyboard, short_caption, delete_last_displayed_messages
from utils.taxonomy_utils import canonicalize_key, get_display_name, list_subcategories
from utils.formatting import escape_markdown_v2

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
    Router for catalog-related callback queries. Accepts callback_data starting with 'CAT:'.
    Supported actions: OPEN_CAT, OPEN_SUB, PAGE, VIEW, OPEN_ALBUM, BACK.
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
            # Show product list for page 1 (user-facing)
            await show_products_for_category(query, context, cat, sub, page=1)

        elif action == "PAGE":
            # payload format: "<SCOPE>::<PAGE>"
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
    page: int = 1,
    per_page: int = PAGE_SIZE,
) -> None:
    """
    Show a paginated list of products for a category (and optional subcategory).
    Pagination is 1-based for user-facing callbacks; build_pagination_keyboard expects zero-based page index.
    """
    try:
        if page < 1:
            page = 1
        cat_key = canonicalize_key(category) if category else ""
        sub_key = canonicalize_key(subcategory) if subcategory else None

        # Fetch a window larger than a single page to allow filtering
        fetch_limit = max(page * per_page * 3, per_page)
        items = await db.get_catalog_items(limit=fetch_limit)
        if not items:
            await query.edit_message_text("No items available in the catalog.")
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

        # build_pagination_keyboard expects zero-based page index
        pagination = build_pagination_keyboard(scope=scope, page=page - 1, per_page=per_page, total=total)
        keyboard.extend(pagination)

        header = f"{_display_name(category)}"
        if subcategory:
            header = f"{header} — {_display_name(subcategory)}"

        await query.edit_message_text(f"{header}\n\n" + "\n".join(text_lines), reply_markup=InlineKeyboardMarkup(keyboard))

        # Persist navigation context for Back behavior
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


async def _copy_media_group_to_user(
    dest_chat: int,
    products: List[Dict[str, Any]],
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[List[Tuple[int, int]]]:
    """
    Attempt to copy all original messages for a media_group into dest_chat.
    If any original reference is missing or any copy fails, remove any partial copies
    already made (best-effort) and return None so the caller falls back to sending
    via file_ids. On success returns list of (chat_id, message_id).
    """
    sent: List[Tuple[int, int]] = []
    seen = set()
    pairs: List[Tuple[int, int]] = []
    for p in products:
        mid = p.get("message_id")
        orig_chat = p.get("chat_id")
        if mid is None or orig_chat is None:
            logger.debug("media_group missing original reference (message_id/chat_id): %r", p)
            return None
        key = (int(orig_chat), int(mid))
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)

    for from_chat_id, message_id in pairs:
        try:
            msg = await context.bot.copy_message(chat_id=dest_chat, from_chat_id=from_chat_id, message_id=message_id)
            sent.append((msg.chat.id, msg.message_id))
        except Exception:
            logger.debug("copy_message failed for %s/%s; aborting copy attempt and deleting partial copies", from_chat_id, message_id)
            # Best-effort cleanup of previously copied messages to avoid duplicates
            for (c_id, m_id) in sent:
                try:
                    await context.bot.delete_message(chat_id=c_id, message_id=m_id)
                except Exception:
                    logger.debug("Failed to delete partial copied message %s/%s (non-fatal)", c_id, m_id)
            return None

    return sent


async def show_product_detail(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    """
    Show product media and caption. For media_group, prefer copying original messages into user's chat;
    otherwise assemble and send media from stored file_ids. After media, send a small control message
    with navigation buttons (Back / Categories) and record displayed messages for cleanup.
    """
    try:
        p = await db.get_product(product_id)
        if not p:
            try:
                await query.edit_message_text("Product not found.")
            except Exception:
                logger.exception("Failed to notify product not found")
            return

        # Destination (prefer user who clicked)
        dest_chat: Optional[int] = None
        if query.from_user:
            dest_chat = query.from_user.id
        elif getattr(query, "message", None) and getattr(query.message, "chat", None):
            dest_chat = query.message.chat.id

        if dest_chat is None:
            logger.error("show_product_detail: no destination to send product media")
            try:
                await query.answer("Cannot show product (no destination).", show_alert=True)
            except Exception:
                pass
            return

        user_lock = _ensure_user_lock(dest_chat)
        async with user_lock:
            # clean up prior product display
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

            media_group_id = p.get("media_group_id")
            file_id = p.get("file_id")
            file_type = (p.get("file_type") or "").lower()

            sent_records: List[Tuple[int, int]] = []

            if media_group_id:
                media_items = await db.get_products_by_media_group(media_group_id, p.get("chat_id"))
                logger.debug("show_product_detail: media_group_id=%s media_items=%d", media_group_id, len(media_items))

                # Attempt to copy all originals; fallback to assembling media by file_id
                copied = await _copy_media_group_to_user(dest_chat, media_items, context)
                if copied is not None:
                    logger.debug("show_product_detail: copied %d original messages for media_group %s", len(copied), media_group_id)
                    sent_records.extend(copied)
                else:
                    # Build InputMedia list from available file_ids
                    input_media: List[Any] = []
                    first = True
                    for mi in media_items:
                        fid = mi.get("file_id")
                        if not fid:
                            logger.debug("show_product_detail: missing file_id for product row %r", mi)
                            continue
                        ftype = (mi.get("file_type") or "").lower()
                        item_caption = caption_for_api if first else None
                        first = False
                        if ftype.startswith("video"):
                            input_media.append(InputMediaVideo(media=fid, caption=item_caption))
                        elif ftype.startswith("audio") or ftype.startswith("voice"):
                            input_media.append(InputMediaDocument(media=fid, caption=item_caption))
                        else:
                            input_media.append(InputMediaPhoto(media=fid, caption=item_caption))

                    logger.debug("show_product_detail: assembled %d InputMedia items for media_group %s", len(input_media), media_group_id)

                    try:
                        if len(input_media) == 0:
                            logger.warning("No media available to send for product %s", product_id)
                            await query.edit_message_text("No media available for this product.")
                        elif len(input_media) == 1:
                            im = input_media[0]
                            try:
                                if isinstance(im, InputMediaVideo):
                                    m = await context.bot.send_video(chat_id=dest_chat, video=im.media, caption=im.caption, parse_mode=parse_mode)
                                elif isinstance(im, InputMediaDocument):
                                    m = await context.bot.send_document(chat_id=dest_chat, document=im.media, caption=im.caption, parse_mode=parse_mode)
                                else:
                                    m = await context.bot.send_photo(chat_id=dest_chat, photo=im.media, caption=im.caption, parse_mode=parse_mode)
                                sent_records.append((m.chat.id, m.message_id))
                            except Exception:
                                logger.exception("Failed to send single media fallback for product %s", product_id)
                        else:
                            msgs = await context.bot.send_media_group(chat_id=dest_chat, media=input_media)
                            for m in msgs:
                                sent_records.append((m.chat.id, m.message_id))
                    except Exception:
                        logger.exception("send_media_group fallback failed; trying per-item sends")
                        # last resort: send items one-by-one
                        first = True
                        for im in input_media:
                            try:
                                if isinstance(im, InputMediaVideo):
                                    m = await context.bot.send_video(chat_id=dest_chat, video=im.media, caption=im.caption if first else None, parse_mode=parse_mode)
                                else:
                                    m = await context.bot.send_photo(chat_id=dest_chat, photo=im.media, caption=im.caption if first else None, parse_mode=parse_mode)
                                sent_records.append((m.chat.id, m.message_id))
                                first = False
                            except Exception:
                                logger.exception("Failed to send media item %s", getattr(im, "media", "<unknown>"))
            else:
                # Single-file product
                try:
                    if file_type.startswith("video"):
                        m = await context.bot.send_video(chat_id=dest_chat, video=file_id, caption=caption_for_api, parse_mode=parse_mode)
                    elif file_type.startswith("audio") or file_type.startswith("voice"):
                        m = await context.bot.send_audio(chat_id=dest_chat, audio=file_id, caption=caption_for_api, parse_mode=parse_mode)
                    else:
                        m = await context.bot.send_photo(chat_id=dest_chat, photo=file_id, caption=caption_for_api, parse_mode=parse_mode)
                    sent_records.append((m.chat.id, m.message_id))
                except Exception:
                    logger.exception("Failed to send product media for product_id=%s", product_id)
                    try:
                        await query.edit_message_text(text=caption_for_api)
                    except Exception:
                        logger.exception("Failed to edit message as fallback")

            # Send a control message with navigation buttons (Back to page / Back to categories)
            try:
                cat = context.user_data.get("selected_category")
                sub = context.user_data.get("selected_subcategory")
                page = context.user_data.get("catalog_page", 1) or 1
                scope = ""
                if cat:
                    scope = cat
                    if sub:
                        scope = f"{cat}::{sub}"

                nav_buttons: List[InlineKeyboardButton] = []
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
                logger.exception("Failed to send control message after product media")

            # Record displayed messages for future cleanup
            try:
                if sent_records:
                    context.user_data["last_displayed_messages"] = [(c, m) for (c, m) in sent_records]
            except Exception:
                logger.exception("Failed to record last_displayed_messages in user_data")

    except Exception:
        logger.exception("show_product_detail failed")