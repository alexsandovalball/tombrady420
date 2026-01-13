# handlers/admin.py
"""
Admin-related handlers: ingestion, preview sending, admin callbacks (category assignment).

This module provides:
 - ingest_message: save incoming media posts (one row per file).
 - token store (Redis preferred, in-memory fallback) with helpers _store_token / _get_token_payload.
 - compact admin preview UI: truncated caption + paginated category keyboard (tokenized callbacks).
 - admin callback router (ADM:TK:<token>) for SETCAT/SETSUB/SKIP/DELETE/CANCEL.
 - legacy compatibility for a few ADM:... payloads.
 - cb_nuke_router to handle ADM:NUKE_* callbacks (registered by main.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from typing import Optional, List, Tuple, Dict, Any

from telegram import Message, Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import ADMIN_IDS, PREVIEW_TTL_SECONDS, REDIS_URL
from config.taxonomy import CATEGORIES as TAXONOMY, DISPLAY_NAMES
from database import db
from utils.taxonomy_utils import validate_category_and_subcategory, canonicalize_key, get_display_name

logger = logging.getLogger(__name__)

# -------------------------
# Token store (Redis preferred, fallback to in-memory)
# -------------------------
_redis_client = None
_use_redis = False
try:
    if REDIS_URL:
        import redis.asyncio as aioredis  # type: ignore
        _redis_client = aioredis.from_url(REDIS_URL)
        _use_redis = True
        logger.debug("Using Redis callback token store")
except Exception:
    _redis_client = None
    _use_redis = False
    logger.debug("Redis not configured or unavailable; using in-memory callback token store")

# In-memory fallback: token -> (payload_json, expiry_ts)
_in_memory_store: Dict[str, Tuple[str, float]] = {}
_in_memory_lock = asyncio.Lock()


async def _store_token(token: str, payload: dict, ttl: int) -> None:
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if _use_redis and _redis_client is not None:
        try:
            await _redis_client.setex(f"cb:{token}", ttl, data)
            return
        except Exception:
            logger.exception("Redis setex failed; falling back to in-memory store")
    expiry = time.time() + ttl
    async with _in_memory_lock:
        _in_memory_store[token] = (data, expiry)


async def _get_token_payload(token: str) -> Optional[dict]:
    if _use_redis and _redis_client is not None:
        try:
            raw = await _redis_client.get(f"cb:{token}")
            if raw is None:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        except Exception:
            logger.exception("Redis get failed; falling back to in-memory lookup")
    async with _in_memory_lock:
        entry = _in_memory_store.get(token)
        if not entry:
            return None
        data, expiry = entry
        if time.time() > expiry:
            _in_memory_store.pop(token, None)
            return None
        try:
            return json.loads(data)
        except Exception:
            logger.exception("Failed to decode in-memory token payload for token=%s", token)
            return None


# Best-effort cleanup task for in-memory store
async def _start_in_memory_cleanup_task() -> None:
    async def _loop():
        while True:
            await asyncio.sleep(60)
            now = time.time()
            async with _in_memory_lock:
                expired = [k for k, (_, e) in _in_memory_store.items() if e <= now]
                for k in expired:
                    _in_memory_store.pop(k, None)

    try:
        asyncio.create_task(_loop())
    except Exception:
        pass


if not _use_redis:
    try:
        asyncio.get_event_loop().call_soon_threadsafe(lambda: asyncio.create_task(_start_in_memory_cleanup_task()))
    except Exception:
        pass


# -------------------------
# Small helpers
# -------------------------
async def _safe_reply_text(update: Update, text: str) -> None:
    try:
        if update.message:
            await update.message.reply_text(text)
        elif update.effective_chat:
            await update.effective_chat.send_message(text)
    except Exception:
        logger.exception("Failed to send reply text")


async def _safe_edit_message(query: CallbackQuery, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        if reply_markup is not None:
            await query.edit_message_text(text, reply_markup=reply_markup)
        else:
            await query.edit_message_text(text)
    except BadRequest as e:
        # Log callback_data lengths to diagnose invalid callback payloads
        try:
            if reply_markup and getattr(reply_markup, "inline_keyboard", None):
                for row in reply_markup.inline_keyboard:
                    for btn in row:
                        cb = getattr(btn, "callback_data", "")
                        try:
                            length = len(cb.encode("utf-8"))
                        except Exception:
                            length = len(cb)
                        logger.error("BadRequest editing message: callback_data length=%s data=%r", length, cb)
        except Exception:
            logger.exception("Failed to inspect reply_markup after BadRequest")
        logger.exception("BadRequest editing message: %s", e)
        try:
            await query.answer()
        except Exception:
            logger.debug("Failed to answer callback after BadRequest")
    except Exception:
        logger.debug("Failed to edit message (non-fatal)", exc_info=True)
        try:
            await query.answer()
        except Exception:
            logger.debug("Failed to answer callback after edit failure")


# -------------------------
# Ingest and preview
# -------------------------
async def ingest_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Ingest channel posts (or direct uploads). Persist one product row per file and send a compact admin preview.
    """
    try:
        msg: Optional[Message] = update.effective_message
        if not msg:
            return

        chat_id = getattr(msg.chat, "id", None)
        message_id = getattr(msg, "message_id", None)
        media_group_id = getattr(msg, "media_group_id", None)

        files: List[Tuple[str, str, Optional[str]]] = []
        if getattr(msg, "photo", None):
            photo = msg.photo[-1]
            files.append(("photo", photo.file_id, getattr(photo, "file_unique_id", None)))
        if getattr(msg, "video", None):
            files.append(("video", msg.video.file_id, getattr(msg.video, "file_unique_id", None)))
        if getattr(msg, "document", None):
            files.append(("document", msg.document.file_id, getattr(msg.document, "file_unique_id", None)))
        if getattr(msg, "animation", None):
            files.append(("animation", msg.animation.file_id, getattr(msg.animation, "file_unique_id", None)))
        if getattr(msg, "audio", None):
            files.append(("audio", msg.audio.file_id, getattr(msg.audio, "file_unique_id", None)))
        if getattr(msg, "voice", None):
            files.append(("voice", msg.voice.file_id, getattr(msg.voice, "file_unique_id", None)))

        if not files:
            return

        saved_ids: List[int] = []
        for file_type, file_id, file_unique in files:
            try:
                pid = await db.add_product(
                    file_id=file_id,
                    file_type=file_type,
                    caption=msg.caption or "",
                    message_id=message_id,
                    chat_id=chat_id,
                    media_group_id=media_group_id,
                    file_unique_id=file_unique,
                )
                saved_ids.append(pid)
            except Exception:
                logger.exception("Failed to add product row for file in message %s/%s", chat_id, message_id)

        logger.info("Ingested message %s/%s -> saved ids=%s", chat_id, message_id, saved_ids)

        # Resolve canonical media_group id (prefer actual media_group_id)
        canonical_mg: Optional[str] = media_group_id or None
        if not canonical_mg:
            for pid in saved_ids:
                prod = await db.get_product(pid)
                if prod and prod.get("media_group_id"):
                    canonical_mg = prod.get("media_group_id")
                    break
        if not canonical_mg and message_id is not None and chat_id is not None:
            mg_from_db = await db.get_media_group_for_message(message_id, chat_id)
            if mg_from_db:
                canonical_mg = mg_from_db
        if not canonical_mg:
            canonical_mg = f"{chat_id}:{message_id}"

        logger.debug("Canonical mg for %s/%s -> %s", chat_id, message_id, canonical_mg)

        # Choose preview caption
        preview_caption = (msg.caption or "").strip()
        try:
            group_products = await db.get_products_by_media_group(canonical_mg, chat_id)
            for p in group_products:
                cap = (p.get("caption") or "").strip()
                if cap:
                    preview_caption = cap
                    break
            try:
                await db.delete_products_with_empty_caption(canonical_mg, chat_id)
            except Exception:
                logger.debug("delete_products_with_empty_caption failed (non-fatal)")
        except Exception:
            logger.debug("Failed to compute preview caption (non-fatal)", exc_info=True)

        try:
            should_send = await db.try_mark_media_group_preview_sent(canonical_mg, chat_id)
        except Exception:
            logger.exception("try_mark_media_group_preview_sent failed for %s/%s", canonical_mg, chat_id)
            should_send = False

        if should_send:
            await send_admin_preview_short(msg, canonical_mg, chat_id, context, caption_override=preview_caption)
        else:
            logger.debug("Preview already sent for media_group %s chat %s", canonical_mg, chat_id)
    except Exception:
        logger.exception("ingest_message failed")


# -------------------------
# Token helpers
# -------------------------
async def _make_token_for_action(action: str, payload: dict, ttl: Optional[int] = None) -> str:
    ttl = ttl or PREVIEW_TTL_SECONDS or 3600
    token = secrets.token_urlsafe(8)
    await _store_token(token, {"action": action, **payload}, ttl)
    return token


# -------------------------
# Admin UI: short preview + paged category keyboard
# -------------------------
ADMIN_CAT_PAGE_SIZE = 8
ADMIN_CAPTION_WORDS = 8


def _admin_truncate_caption(caption: Optional[str], words: int = ADMIN_CAPTION_WORDS) -> str:
    if not caption:
        return "(No caption)"
    parts = caption.strip().split()
    if len(parts) <= words:
        return " ".join(parts)
    return " ".join(parts[:words]).rstrip() + " …"


def _all_category_keys_ordered() -> List[str]:
    try:
        keys = sorted(TAXONOMY.keys())
        for special in ("DATEDPROOFS", "CLIENTTOUCHDOWNS"):
            if special in keys:
                keys.remove(special)
                keys.append(special)
        return keys
    except Exception:
        return []


async def _build_subcategory_keyboard(media_group_id: str, chat_id: Optional[int], cat_canon: str) -> InlineKeyboardMarkup:
    subs = TAXONOMY.get(cat_canon, []) or []
    keyboard: List[List[InlineKeyboardButton]] = []
    for sub in subs:
        sub_canon = canonicalize_key(sub)
        display_sub = get_display_name(sub_canon)
        token_payload = {"mg": media_group_id, "cat": cat_canon, "sub": sub_canon}
        if chat_id is not None:
            token_payload["chat"] = int(chat_id)
        token = await _make_token_for_action("SETSUB", token_payload)
        keyboard.append([InlineKeyboardButton(text=display_sub, callback_data=f"ADM:TK:{token}")])
    cancel_token = await _make_token_for_action("CANCEL", {"mg": media_group_id, "chat": chat_id} if chat_id is not None else {"mg": media_group_id})
    keyboard.append([InlineKeyboardButton(text="Cancel", callback_data=f"ADM:TK:{cancel_token}")])
    return InlineKeyboardMarkup(keyboard)


async def _build_admin_category_keyboard(media_group_id: str, page: int = 1, page_size: int = ADMIN_CAT_PAGE_SIZE, chat_id: Optional[int] = None) -> InlineKeyboardMarkup:
    keys = _all_category_keys_ordered()
    total = len(keys)
    if page < 1:
        page = 1
    start = (page - 1) * page_size
    end = start + page_size
    page_items = keys[start:end]

    keyboard: List[List[InlineKeyboardButton]] = []
    for k in page_items:
        try:
            label = get_display_name(k)
        except Exception:
            label = k.replace("_", " ").title()
        token_payload = {"mg": media_group_id, "cat": canonicalize_key(k)}
        if chat_id is not None:
            token_payload["chat"] = int(chat_id)
        token = await _make_token_for_action("SETCAT", token_payload)
        keyboard.append([InlineKeyboardButton(text=label, callback_data=f"ADM:TK:{token}")])

    nav_row: List[InlineKeyboardButton] = []
    if start > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"ADM:CATPAGE:{media_group_id}::{page-1}"))
    nav_row.append(InlineKeyboardButton(f"Page {page}", callback_data="noop"))
    if end < total:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"ADM:CATPAGE:{media_group_id}::{page+1}"))
    keyboard.append(nav_row)

    skip_token = await _make_token_for_action("SKIP", {"mg": media_group_id, "chat": chat_id} if chat_id is not None else {"mg": media_group_id})
    del_token = await _make_token_for_action("DELETE", {"mg": media_group_id, "chat": chat_id} if chat_id is not None else {"mg": media_group_id})
    keyboard.append([InlineKeyboardButton("Skip", callback_data=f"ADM:TK:{skip_token}"),
                     InlineKeyboardButton("Delete", callback_data=f"ADM:TK:{del_token}")])

    return InlineKeyboardMarkup(keyboard)


async def admin_catpage_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    try:
        await q.answer()
    except Exception:
        pass

    try:
        prefix = "ADM:CATPAGE:"
        rest = q.data[len(prefix) :]
        if "::" in rest:
            mgid, page_str = rest.split("::", 1)
            page = int(page_str or 1)
        else:
            mgid = rest
            page = 1
    except Exception:
        logger.exception("admin_catpage_router: malformed payload %r", q.data)
        try:
            await q.answer("Invalid pagination request", show_alert=True)
        except Exception:
            pass
        return

    try:
        header = q.message.text if q.message and q.message.text else f"Preview {mgid}"
        chat_id: Optional[int] = None
        try:
            rows = await db.get_products_by_media_group(mgid, None)
            if rows:
                chat_id = rows[0].get("chat_id")
        except Exception:
            logger.debug("Could not determine chat_id for media_group %s", mgid)
        markup = await _build_admin_category_keyboard(mgid, page=page, chat_id=chat_id)
        try:
            await q.message.edit_text(header, reply_markup=markup)
        except Exception:
            logger.exception("admin_catpage_router: failed to edit message; attempting to send new one")
            try:
                await context.bot.send_message(chat_id=q.from_user.id, text=header, reply_markup=markup)
            except Exception:
                logger.exception("admin_catpage_router: failed to send fallback message")
    except Exception:
        logger.exception("admin_catpage_router failed")


async def send_admin_preview_short(
    msg: Message,
    media_group_id: str,
    chat_id: Optional[int],
    context: ContextTypes.DEFAULT_TYPE,
    caption_override: Optional[str] = None,
) -> None:
    try:
        full_caption = caption_override if caption_override is not None else (msg.caption or "(No caption)")
        short_text = _admin_truncate_caption(full_caption, words=ADMIN_CAPTION_WORDS)
        header = f"Preview for media_group {media_group_id}\n\n{short_text}"
        for admin_id in ADMIN_IDS:
            try:
                kb = await _build_admin_category_keyboard(media_group_id, page=1, chat_id=chat_id)
                await context.bot.send_message(chat_id=admin_id, text=header, reply_markup=kb)
            except Exception:
                logger.exception("Failed to send preview to admin %s", admin_id)
    except Exception:
        logger.exception("send_admin_preview_short failed")


# -------------------------
# Admin command handlers
# -------------------------
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            await _safe_reply_text(update, "Unauthorized")
            return
        stats = await db.get_stats()
        await _safe_reply_text(update, f"📊 Total products: {stats.get('total',0)}\n📅 Added today: {stats.get('today',0)}")
    except Exception:
        logger.exception("cmd_stats failed")
        await _safe_reply_text(update, "❌ Failed to fetch stats.")


async def cmd_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            await _safe_reply_text(update, "Unauthorized")
            return
        await _safe_reply_text(update, "Cleanup is a manual operation. Please run the DB normalization script offline.")
    except Exception:
        logger.exception("cmd_cleanup failed")
        await _safe_reply_text(update, "❌ Failed to initiate cleanup.")


# -------------------------
# Admin callback router (tokenized + legacy fallback)
# -------------------------
async def cb_admin_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        if not query:
            return

        user = query.from_user
        if not user or user.id not in ADMIN_IDS:
            await query.answer("Unauthorized", show_alert=True)
            return

        data = (query.data or "").strip()
        if not data.startswith("ADM:"):
            await query.answer("Invalid admin action", show_alert=True)
            return

        # Pagination (legacy) handled first
        if data.startswith("ADM:CATPAGE:"):
            await admin_catpage_router(update, context)
            return

        # Tokenized flow
        if data.startswith("ADM:TK:"):
            token = data.split(":", 2)[2]
            payload = await _get_token_payload(token)
            if not payload:
                await query.answer("This action expired or is invalid. Please try again.", show_alert=True)
                return

            action = payload.get("action", "").upper()
            mg = payload.get("mg")
            chat = payload.get("chat")
            if action == "SETCAT":
                cat = payload.get("cat")
                if not cat:
                    await query.answer("Malformed token payload", show_alert=True)
                    return
                cat_canon = canonicalize_key(cat)
                if cat_canon not in TAXONOMY:
                    await query.answer("Invalid category selected", show_alert=True)
                    return
                subs = TAXONOMY.get(cat_canon, []) or []
                if subs:
                    markup = await _build_subcategory_keyboard(mg, chat, cat_canon)
                    await _safe_edit_message(query, text=f"Choose subcategory for {get_display_name(cat_canon)}", reply_markup=markup)
                    return
                else:
                    try:
                        success = await db.update_products_by_media_group(mg, int(chat) if chat is not None else None, cat_canon, None)
                        if success:
                            await _safe_edit_message(query, text=f"✅ Set category {get_display_name(cat_canon)} for media_group {mg}")
                        else:
                            await _safe_edit_message(query, text="❌ Failed to set category (DB returned failure).")
                    except Exception:
                        logger.exception("Failed to set category for media_group %s", mg)
                        await _safe_edit_message(query, text="❌ Failed to set category; see server logs.")
                    return

            if action == "SETSUB":
                cat = payload.get("cat")
                sub = payload.get("sub")
                if not cat or not sub:
                    await query.answer("Malformed token payload", show_alert=True)
                    return
                cat_canon = canonicalize_key(cat)
                sub_canon = canonicalize_key(sub)
                cat_valid, sub_valid = validate_category_and_subcategory(cat_canon, sub_canon)
                if not cat_valid:
                    await query.answer("Invalid category", show_alert=True)
                    return
                try:
                    success = await db.update_products_by_media_group(mg, int(chat) if chat is not None else None, cat_valid, sub_valid)
                    if success:
                        await _safe_edit_message(query, text=f"✅ Set category {get_display_name(cat_valid)} / {get_display_name(sub_valid)} for media_group {mg}")
                    else:
                        await _safe_edit_message(query, text="❌ Failed to set category/subcategory (DB returned failure).")
                except Exception:
                    logger.exception("Failed to set subcategory for media_group %s", mg)
                    await _safe_edit_message(query, text="❌ Failed to set category/subcategory; see server logs.")
                return

            if action == "SKIP":
                try:
                    await _safe_edit_message(query, text=f"Skipped categorization for {mg}")
                except Exception:
                    logger.exception("Failed to mark skip for %s", mg)
                return

            if action == "DELETE":
                try:
                    deleted = await db.delete_products_by_media_group(mg, int(chat) if chat is not None else None)
                    count = len(deleted) if isinstance(deleted, (list, tuple)) else (deleted or 0)
                    await _safe_edit_message(query, text=f"Deleted {count} products for {mg}")
                except Exception:
                    logger.exception("Failed to delete products for media_group %s", mg)
                    await _safe_edit_message(query, text="❌ Failed to delete products; see server logs.")
                return

            if action == "CANCEL":
                try:
                    await _safe_edit_message(query, text=f"Cancelled categorization for {mg}")
                except Exception:
                    logger.exception("Failed to cancel categorization for %s", mg)
                return

            await query.answer("Unknown admin action", show_alert=True)
            return

        # Legacy fallback (keep minimal legacy support)
        rest = data[4:]
        action, _, payload = rest.partition(":")
        action = (action or "").upper()

        def _split_payload_from_right(payload_str: str, expected_tail_tokens: int):
            if expected_tail_tokens <= 0:
                return payload_str, []
            parts = payload_str.rsplit(":", expected_tail_tokens)
            if len(parts) < expected_tail_tokens + 1:
                return None, None
            head = parts[0]
            tail = parts[1:]
            return head, tail

        if action == "SETCAT":
            head, tail = _split_payload_from_right(payload, 2)
            if head is None:
                await query.answer("Malformed SETCAT data", show_alert=True)
                return
            mg = head
            chat = tail[0]
            cat = tail[1]
            cat_canon = canonicalize_key(cat)
            if cat_canon not in TAXONOMY:
                await query.answer("Invalid category selected", show_alert=True)
                return
            subs = TAXONOMY.get(cat_canon, []) or []
            if subs:
                keyboard = []
                for sub in subs:
                    sub_canon = canonicalize_key(sub)
                    display_sub = get_display_name(sub_canon)
                    cb = f"ADM:SETSUB:{mg}:{chat}:{cat_canon}:{sub_canon}"
                    keyboard.append([InlineKeyboardButton(text=display_sub, callback_data=cb)])
                keyboard.append([InlineKeyboardButton(text="Cancel", callback_data=f"ADM:CANCEL:{mg}:{chat}")])
                try:
                    await _safe_edit_message(query, text=f"Choose subcategory for {get_display_name(cat_canon)}", reply_markup=InlineKeyboardMarkup(keyboard))
                except Exception:
                    logger.exception("Failed to present subcategory choices for %s", mg)
                return
            else:
                try:
                    success = await db.update_products_by_media_group(mg, int(chat), cat_canon, None)
                    if success:
                        await _safe_edit_message(query, text=f"✅ Set category {get_display_name(cat_canon)} for media_group {mg}")
                    else:
                        await _safe_edit_message(query, text="❌ Failed to set category (DB returned failure).")
                except Exception:
                    logger.exception("Failed to set category for media_group %s", mg)
                    await _safe_edit_message(query, text="❌ Failed to set category; see server logs.")
                return

        await query.answer("Unsupported legacy admin action (use tokenized UI).", show_alert=True)
    except Exception:
        logger.exception("cb_admin_router failed")


# -------------------------
# Nuke confirmation router (separate)
# -------------------------
async def cb_nuke_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle ADM:NUKE_CONFIRM and ADM:NUKE_CANCEL callbacks.
    Registered in main.py as a separate router for clarity.
    """
    try:
        query = update.callback_query
        if not query:
            return
        user = query.from_user
        if not user or user.id not in ADMIN_IDS:
            await query.answer("Unauthorized", show_alert=True)
            return

        data = (query.data or "").strip()
        if data.startswith("ADM:NUKE_CONFIRM"):
            if hasattr(db, "nuke_all_products"):
                try:
                    count = await db.nuke_all_products()
                    await _safe_edit_message(query, text=f"✅ Nuke complete. Deleted {count} products.")
                    logger.warning("NUKE executed by admin=%s; deleted=%s", user.id, count)
                except Exception:
                    logger.exception("NUKE failed during execution")
                    await _safe_edit_message(query, text="❌ Nuke failed due to an internal error.")
            else:
                await _safe_edit_message(query, text="Nuke helper not available on this deployment. Run offline script.")
        elif data.startswith("ADM:NUKE_CANCEL"):
            await _safe_edit_message(query, text="❌ Nuke cancelled.")
        else:
            await query.answer("Unknown nuke action", show_alert=True)
    except Exception:
        logger.exception("cb_nuke_router failed")


# -------------------------
# Convenience admin commands
# -------------------------
async def list_products_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            await _safe_reply_text(update, "❌ You are not authorized to use this command.")
            return
        rows = await db.get_all_products()
        if not rows:
            await _safe_reply_text(update, "ℹ️ No products found.")
            return
        lines: List[str] = []
        for r in rows[:50]:
            pid = r.get("id")
            cat = r.get("category") or "None"
            sub = r.get("subcategory") or "None"
            chat = r.get("chat_id")
            msg = r.get("message_id")
            cap = (r.get("caption") or "").replace("\n", " ")[:80]
            lines.append(f"{pid} | {cat} / {sub} | chat={chat} msg={msg} | {cap}")
        text = "Recent products:\n" + "\n".join(lines)
        for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)]:
            await _safe_reply_text(update, chunk)
    except Exception:
        logger.exception("list_products_command failed")
        await _safe_reply_text(update, "❌ Failed to list products.")


async def set_category_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            await _safe_reply_text(update, "❌ You are not authorized to use this command.")
            return
        args = context.args
        if len(args) < 2:
            await _safe_reply_text(update, "Usage: /setcategory <product_id> <category> [subcategory]")
            return
        try:
            product_id = int(args[0])
        except ValueError:
            await _safe_reply_text(update, "Product ID must be an integer.")
            return
        category_input = args[1]
        subcategory_input = " ".join(args[2:]) if len(args) > 2 else None
        cat_valid, sub_valid = validate_category_and_subcategory(category_input, subcategory_input)
        if not cat_valid:
            await _safe_reply_text(update, "❌ Invalid category. Use /listcategories to see allowed values.")
            return
        success = await db.set_product_category(product_id, cat_valid, sub_valid)
        if success:
            await _safe_reply_text(update, f"✅ Product {product_id} categorized as '{cat_valid}/{sub_valid or '—'}'.")
        else:
            await _safe_reply_text(update, "❌ Failed to set category (DB returned failure).")
    except Exception:
        logger.exception("set_category_command failed")
        await _safe_reply_text(update, "❌ Failed to set category due to an internal error.")


async def list_categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            await _safe_reply_text(update, "❌ You are not authorized to use this command.")
            return
        lines: List[str] = []
        for cat, subs in TAXONOMY.items():
            if subs:
                lines.append(f"{get_display_name(cat)} -> {', '.join([get_display_name(s) for s in subs])}")
            else:
                lines.append(f"{get_display_name(cat)} -> (no subcategories)")
        text = "Categories:\n" + "\n".join(lines)
        for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)]:
            await _safe_reply_text(update, chunk)
    except Exception:
        logger.exception("list_categories_command failed")
        await _safe_reply_text(update, "❌ Failed to list categories.")


async def category_counts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command to show product counts by category and subcategory.
    Useful for debugging "No items found in this category" issues.
    """
    try:
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            await _safe_reply_text(update, "❌ You are not authorized to use this command.")
            return
        
        await _safe_reply_text(update, "Fetching category counts...")
        counts = await db.get_category_counts()
        
        lines: List[str] = [
            f"📊 Product Distribution:",
            f"Total products: {counts['total']}",
            f"Uncategorized: {counts['uncategorized']}",
            "",
        ]
        
        for cat, cat_data in sorted(counts["by_category"].items()):
            lines.append(f"📁 {get_display_name(cat)} ({cat_data['total']} total)")
            for sub, sub_count in sorted(cat_data["by_subcategory"].items()):
                if sub == "(no subcategory)":
                    lines.append(f"   • {sub}: {sub_count}")
                else:
                    lines.append(f"   • {get_display_name(sub)}: {sub_count}")
            lines.append("")
        
        text = "\n".join(lines)
        # Split into chunks if too long
        for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)]:
            await _safe_reply_text(update, chunk)
    except Exception:
        logger.exception("category_counts_command failed")
        await _safe_reply_text(update, "❌ Failed to get category counts.")


async def backfill_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            await _safe_reply_text(update, "❌ Unauthorized")
            return
        await _safe_reply_text(update, "Starting DB backfill (normalize + propagate). This may take a while.")
        normalized = await db.backfill_normalize()
        propagated = await db.backfill_propagate_media_group()
        await _safe_reply_text(update, f"Backfill complete. Normalized rows: {normalized}, propagated rows: {propagated}")
    except Exception:
        logger.exception("backfill_db_command failed")
        await _safe_reply_text(update, "❌ Backfill failed; see server logs.")


async def nuke_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            await _safe_reply_text(update, "❌ Unauthorized")
            return
        # Send confirmation buttons (tokenized)
        token = await _make_token_for_action("NUKE_CONFIRM", {"who": user.id})
        cancel_token = await _make_token_for_action("CANCEL", {"who": user.id})
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Confirm Nuke", callback_data=f"ADM:TK:{token}"),
                                   InlineKeyboardButton("Cancel", callback_data=f"ADM:TK:{cancel_token}")]])
        await _safe_reply_text(update, "Are you sure you want to nuke the DB? This will delete many rows. Confirm below.")
        await context.bot.send_message(chat_id=user.id, text="Confirm nuke:", reply_markup=kb)
    except Exception:
        logger.exception("nuke_command failed")
        await _safe_reply_text(update, "❌ Failed to initiate nuke confirmation.")