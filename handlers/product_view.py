"""
Product view and callback handlers for the catalog bot.
"""

import asyncio
import logging
from typing import List, Tuple, Optional, Dict, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
)
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from utils.helpers import is_admin
from utils.categories import extract_category_and_subcategory
from utils.taxonomy_utils import validate_category_and_subcategory
from database import db  # use module-level db instance (ensures migrations/init run once)

logger = logging.getLogger(__name__)

# In-memory locks to serialize ingestion for the same message/media_group
_ingest_locks: Dict[str, asyncio.Lock] = {}


def _lock_key_for_item(chat_id: Optional[int], message_id: Optional[int], media_group_id: Optional[str], file_id: Optional[str]) -> str:
    """
    Build a stable lock key for an incoming item.
    Prefer media_group_id when present (serialize per album), otherwise use message_id+file_id.
    """
    if media_group_id:
        return f"mg:{chat_id}:{media_group_id}"
    # message_id may be None for some updates; include file_id to reduce accidental collisions
    return f"msg:{chat_id}:{message_id}:{file_id}"


def _get_ingest_lock(key: str) -> asyncio.Lock:
    """
    Return an asyncio.Lock for the given key, creating it if necessary.
    This uses a simple dict; it's fine for a single-process bot.
    """
    lock = _ingest_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _ingest_locks[key] = lock
    return lock


# -------------------------
# Ingest helper
# -------------------------
async def ingest_and_save_product(
    message: Any,
    context: ContextTypes.DEFAULT_TYPE,
    file_id: str,
    file_type: str,
    msg_id: int = None,
    chat_id: int = None,
    media_group_id: str = None,
) -> int:
    """
    Normalize tags and save a single product row for this file.
    This version inserts one row per file (the DB unique constraints will
    prevent exact duplicates). For media_group posts we still insert one
    row per file so the album's files are preserved; catalog listing
    logic chooses a representative row for display.

    To avoid duplicate inserts caused by concurrent processing of the same
    update, we serialize ingestion per media_group (or per message/file).
    Returns the product id on success, or -1 on failure.
    """
    try:
        # Diagnostic log to confirm each file processed
        logger.debug(
            "ingest_and_save_product called: file_id=%s file_type=%s media_group_id=%s msg=%s chat=%s",
            file_id,
            file_type,
            media_group_id,
            msg_id,
            chat_id,
        )

        # Capture caption/text from the incoming message
        caption_raw = getattr(message, "caption", None) or getattr(message, "text", None) or ""
        clean_caption, cat_raw, sub_raw = extract_category_and_subcategory(caption_raw)

        # Validate and normalize category/subcategory
        cat_valid, sub_valid = validate_category_and_subcategory(cat_raw, sub_raw)
        category = cat_valid.strip().upper() if cat_valid else None
        subcategory = (
            sub_valid.strip().upper().replace(" ", "").replace("_", "") if sub_valid else None
        )

        logger.info(
            "Parsed caption=%r -> category=%r subcategory=%r media_group_id=%r chat=%s msg=%s file_id=%s",
            caption_raw,
            category,
            subcategory,
            media_group_id,
            chat_id,
            msg_id,
            file_id,
        )

        # Extract file_unique_id if available on the Telegram file object
        file_unique_id = None
        try:
            msg_obj = message
            if getattr(msg_obj, "photo", None):
                file_unique_id = getattr(msg_obj.photo[-1], "file_unique_id", None)
            elif getattr(msg_obj, "video", None):
                file_unique_id = getattr(msg_obj.video, "file_unique_id", None)
            elif getattr(msg_obj, "document", None):
                file_unique_id = getattr(msg_obj.document, "file_unique_id", None)
            elif getattr(msg_obj, "animation", None):
                file_unique_id = getattr(msg_obj.animation, "file_unique_id", None)
            elif getattr(msg_obj, "audio", None):
                file_unique_id = getattr(msg_obj.audio, "file_unique_id", None)
            elif getattr(msg_obj, "voice", None):
                file_unique_id = getattr(msg_obj.voice, "file_unique_id", None)
        except Exception:
            logger.debug("Could not extract file_unique_id from message (non-fatal)")

        # Debug: show what we will pass to the DB
        logger.debug(
            "DBG ingest: file_id=%s file_unique_id=%s file_type=%s media_group_id=%s message_id=%s chat_id=%s caption_len=%d",
            file_id,
            file_unique_id,
            file_type,
            media_group_id,
            msg_id,
            chat_id,
            len(caption_raw or ""),
        )

        # Build lock key and acquire lock to avoid concurrent duplicate inserts
        lock_key = _lock_key_for_item(chat_id, msg_id, media_group_id, file_id)
        lock = _get_ingest_lock(lock_key)
        logger.debug("Acquiring ingest lock for key=%s", lock_key)
        async with lock:
            logger.debug("Acquired ingest lock for key=%s", lock_key)

            # Insert (or find existing) product row for this file.
            # db.add_product uses INSERT OR IGNORE and returns existing id when appropriate.
            product_id = await db.add_product(
                file_id=file_id,
                file_type=file_type,
                caption=clean_caption,
                message_id=int(msg_id) if msg_id is not None else None,
                chat_id=int(chat_id) if chat_id is not None else None,
                media_group_id=media_group_id,
                category=category,
                subcategory=subcategory,
                file_unique_id=file_unique_id,
            )

            # Debug: show DB result
            logger.debug("DBG db.add_product returned product_id=%s for file_id=%s file_unique_id=%s", product_id, file_id, file_unique_id)

            if not product_id or int(product_id) <= 0:
                logger.warning("Product save returned non-positive id for file_id=%s", file_id)
                return -1

            # If the DB returned an existing id and the DB caption is empty but incoming caption is present,
            # update the DB caption so the representative row gets the caption.
            try:
                if clean_caption:
                    existing = await db.get_product(int(product_id))
                    if existing:
                        existing_caption = (existing.get("caption") or "").strip()
                        if not existing_caption:
                            await db.update_product_caption(int(product_id), clean_caption)
                            logger.info("Updated caption for existing product id=%s from incoming message", product_id)
            except Exception:
                logger.exception("Failed to update caption for product id=%s (non-fatal)", product_id)

        # lock released here
        logger.debug("Released ingest lock for key=%s", lock_key)

        logger.info(
            "ingest_and_save_product: saved id=%s file_id=%s category=%s subcategory=%s chat=%s msg=%s",
            product_id,
            file_id,
            category,
            subcategory,
            chat_id,
            msg_id,
        )

        # Send admin preview using context.bot
        try:
            # Replace ADMIN_IDS with your admin Telegram user ids
            ADMIN_IDS = {8589411468}
            admin_buttons = [
                [InlineKeyboardButton("Set category", callback_data=f"admin_setcat|{product_id}")],
                [InlineKeyboardButton("Delete", callback_data=f"delete|{product_id}")],
            ]
            admin_markup = InlineKeyboardMarkup(admin_buttons)
            preview_text = (clean_caption if clean_caption else (getattr(message, "caption", "") or "")) + f"\n\nProduct ID: {product_id}"
            for admin in ADMIN_IDS:
                try:
                    await context.bot.send_message(chat_id=admin, text=preview_text, reply_markup=admin_markup)
                except Exception:
                    logger.exception("Failed to send admin product message for product %s to admin %s", product_id, admin)
        except Exception:
            logger.exception("Failed to prepare/send admin preview for product %s", product_id)

        # If media_group_id present and we have a category, propagate to all group rows
        if media_group_id and category:
            try:
                await db.update_products_by_media_group(media_group_id, chat_id, category, subcategory)
            except Exception:
                logger.exception("Failed to bulk-update media_group categories for %s", media_group_id)

        return int(product_id) if isinstance(product_id, int) else -1
    except Exception:
        logger.exception("ingest_and_save_product failed for file_id=%s chat=%s msg=%s", file_id, chat_id, msg_id)
        return -1


# -------------------------
# Product display and callbacks
# -------------------------
def _parse_product_id_from_callback(data: str) -> Optional[int]:
    """Parse callback data like 'product|38' or 'product_38' or just '38'."""
    if not data:
        return None
    data = data.strip()
    parts = data.replace("_", "|").split("|")
    if parts and parts[0].lower().startswith("product") and len(parts) >= 2:
        try:
            return int(parts[-1])
        except (ValueError, TypeError):
            return None
    try:
        return int(parts[-1])
    except Exception:
        return None


async def show_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    """
    Show a product by ID. If it's part of a media_group, send the whole album.
    Record every message the bot sends for this product in context.user_data["last_displayed_messages"]
    so they can be deleted when the user returns to the catalog.
    """
    # Ensure a per-user lock to avoid concurrent duplicate sends
    lock = context.user_data.get("_display_lock")
    if lock is None:
        lock = asyncio.Lock()
        context.user_data["_display_lock"] = lock

    async with lock:
        # Determine destination chat id (always send to the user who invoked)
        user = update.effective_user
        if not user:
            logger.warning("show_product: no effective_user in update")
            return
        dest_chat_id = user.id

        # Verify recorded displayed messages still exist before skipping redisplay
        last_shown = context.user_data.get("_last_shown_product")
        last_displayed = context.user_data.get("last_displayed_messages") or []

        if last_shown == product_id and last_displayed:
            valid_refs: List[Tuple[int, int]] = []
            for (c_id, m_id) in last_displayed:
                try:
                    # copy_message will raise if the original message is inaccessible.
                    copied = await context.bot.copy_message(chat_id=dest_chat_id, from_chat_id=c_id, message_id=m_id)
                    # delete the temporary copy to avoid clutter
                    try:
                        await context.bot.delete_message(chat_id=copied.chat.id, message_id=copied.message_id)
                    except Exception:
                        pass
                    valid_refs.append((c_id, m_id))
                except BadRequest as e:
                    logger.debug("Recorded message missing or inaccessible: %s/%s (%s)", c_id, m_id, e)
                    continue
                except Exception:
                    logger.exception("Unexpected error while validating recorded message %s/%s", c_id, m_id)
                    continue

            # Update stored refs to only valid ones
            if valid_refs:
                context.user_data["last_displayed_messages"] = valid_refs
                logger.debug(
                    "Found %d valid recorded message refs for product %s; skipping redisplay",
                    len(valid_refs),
                    product_id,
                )
                return
            else:
                # No valid recorded messages remain; clear the record and allow redisplay
                context.user_data.pop("last_displayed_messages", None)
                logger.debug("No valid recorded messages remain for product %s; allowing redisplay", product_id)

        # Mark as shown (we'll update recorded messages below)
        context.user_data["_last_shown_product"] = product_id

        # Clear any previously recorded displayed messages for this user (we'll replace below)
        context.user_data.pop("last_displayed_messages", None)

        # If this product was opened from a catalog inline message, record the origin so Back can edit it.
        try:
            if update.callback_query and update.callback_query.message:
                origin_msg = update.callback_query.message
                origin_page = context.user_data.get("catalog_page", 1)
                context.user_data["origin_catalog_message"] = {
                    "chat_id": origin_msg.chat.id,
                    "message_id": origin_msg.message_id,
                    "page": origin_page,
                }
                logger.debug("Recorded origin_catalog_message: %s", context.user_data["origin_catalog_message"])
        except Exception:
            logger.exception("Failed to record origin_catalog_message (non-fatal)")

        # Fetch product from DB
        try:
            product = await db.get_product(product_id)
        except Exception:
            logger.exception("Database error fetching product id=%s", product_id)
            try:
                await context.bot.send_message(chat_id=dest_chat_id, text="❌ Error fetching item from database.")
            except Exception:
                logger.exception("Failed to notify user about DB error")
            return

        if not product:
            try:
                await context.bot.send_message(chat_id=dest_chat_id, text="❌ Product not found.")
            except Exception:
                logger.exception("Failed to notify user that product was not found")
            return

        sent_message_refs: List[Tuple[int, int]] = []  # list of (chat_id, message_id)

        media_group_id = product.get("media_group_id")
        try:
            if media_group_id:
                items = await db.get_products_by_media_group(media_group_id, product.get("chat_id"))
                if items:
                    refs = await _send_media_group_and_record(context, dest_chat_id, items)
                    sent_message_refs.extend(refs)
                else:
                    msg = await _send_single_and_record(context, dest_chat_id, product)
                    if msg:
                        sent_message_refs.append((msg.chat.id, msg.message_id))
            else:
                msg = await _send_single_and_record(context, dest_chat_id, product)
                if msg:
                    sent_message_refs.append((msg.chat.id, msg.message_id))
        except Exception:
            logger.exception("Error while sending product media for product_id=%s", product_id)
            try:
                await context.bot.send_message(chat_id=dest_chat_id, text="❌ Failed to send media for this item.")
            except Exception:
                logger.exception("Failed to notify user after send error")

        # Build action buttons; show delete only to admins
        origin_info = context.user_data.get("origin_catalog_message")
        origin_page = origin_info.get("page") if isinstance(origin_info, dict) else context.user_data.get("catalog_page", 1)
        buttons = [InlineKeyboardButton("🧾 Back to catalog", callback_data=f"menu|{origin_page}")]
        try:
            if user and is_admin(user.id):
                buttons.append(InlineKeyboardButton("🗑️ Delete", callback_data=f"delete|{product_id}"))
        except Exception:
            logger.exception("is_admin check failed; continuing without admin button")

        try:
            actions_msg = await context.bot.send_message(
                chat_id=dest_chat_id,
                text="ACTIONS : DM @FLYAWAYPEP",
                reply_markup=InlineKeyboardMarkup([buttons]),
            )
            sent_message_refs.append((actions_msg.chat.id, actions_msg.message_id))
        except Exception:
            logger.exception("Failed to send actions message for product %s", product_id)

        # Save the list so menu/search handlers can delete them when user goes back
        if sent_message_refs:
            context.user_data["last_displayed_messages"] = sent_message_refs
        else:
            context.user_data.pop("last_displayed_messages", None)


async def handle_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback entrypoint used by main.build_application() to route product callbacks.
    Delegates to show_product after parsing product id.
    """
    query = update.callback_query
    if not query:
        return

    # Answer early; ignore expired-query BadRequest but log it
    try:
        await query.answer()
    except BadRequest as e:
        logger.info("Callback answer expired or invalid (ignored): %s", e)
    except Exception:
        logger.exception("Unexpected error while answering callback query")

    data = (query.data or "").strip()
    product_id = _parse_product_id_from_callback(data)
    if product_id is None:
        logger.warning("handle_product_callback: could not parse product id from data=%r", data)
        try:
            await query.answer("Invalid product reference", show_alert=True)
        except Exception:
            logger.exception("Failed to notify user about invalid product reference")
        return

    # Delegate to show_product which handles sending and recording messages
    await show_product(update, context, product_id)


async def handle_back_to_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE, page: Optional[int] = None):
    """
    Attempt to return the user to the original catalog message by editing it in-place.
    If editing fails or no origin is recorded, fall back to calling the menu pagination handler
    to send a fresh catalog message.

    Also deletes any product display messages recorded in context.user_data['last_displayed_messages'].
    """
    query = update.callback_query
    # Acknowledge callback early
    if query:
        try:
            await query.answer()
        except Exception:
            logger.exception("Failed to answer back-to-catalog callback (non-fatal)")

    origin = context.user_data.get("origin_catalog_message")
    # Prefer explicit page argument, then origin, then stored catalog_page, then default 1
    page_to_show = page or (origin.get("page") if isinstance(origin, dict) else None) or context.user_data.get("catalog_page", 1)

    # First, delete any product display messages we created
    last_displayed = context.user_data.pop("last_displayed_messages", []) or []
    for (c_id, m_id) in last_displayed:
        try:
            await context.bot.delete_message(chat_id=c_id, message_id=m_id)
        except Exception:
            logger.debug("Could not delete displayed message %s/%s (may already be gone)", c_id, m_id)

    # Try to edit the origin catalog message if we have one
    if origin and isinstance(origin, dict):
        try:
            from handlers import menu as menu_module  # local import to avoid circular import at module load
            # Call the menu handler; it will parse page if needed and edit the origin if possible
            await menu_module.handle_catalog_pagination(update, context, page=page_to_show)
            # Clear origin reference after returning to catalog
            context.user_data.pop("origin_catalog_message", None)
            return
        except Exception:
            logger.exception("Failed to edit origin catalog message; falling back to sending a new catalog message")

    # Fallback: call the menu handler to send a new catalog message
    try:
        from handlers import menu as menu_module  # local import to avoid circular import at module load
        await menu_module.handle_catalog_pagination(update, context, page=page_to_show)
    except Exception:
        logger.exception("Failed to send fallback catalog page; user may not see catalog")


async def handle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle delete|<product_id> callback.
    """
    query = update.callback_query
    if not query:
        return

    # Acknowledge immediately; ignore expired query errors
    try:
        await query.answer()
    except BadRequest:
        logger.info("Delete callback answer expired (ignored)")
    except Exception:
        logger.exception("Failed to answer delete callback")

    data = (query.data or "").strip()
    parts = data.replace("_", "|").split("|")
    if len(parts) < 2:
        try:
            await query.answer("Invalid delete request", show_alert=True)
        except Exception:
            logger.exception("Failed to notify user about invalid delete request")
        return

    try:
        product_id = int(parts[-1])
    except Exception:
        try:
            await query.answer("Invalid product id", show_alert=True)
        except Exception:
            logger.exception("Failed to notify user about invalid product id")
        return

    # Load product to determine media_group or message_id
    try:
        product = await db.get_product(product_id)
    except Exception:
        logger.exception("DB error while loading product for delete id=%s", product_id)
        try:
            await query.message.reply_text("❌ Error accessing database.")
        except Exception:
            logger.exception("Failed to notify user after DB error")
        return

    if not product:
        try:
            await query.message.reply_text("Product not found or already deleted.")
        except Exception:
            logger.exception("Failed to notify user that product was not found")
        return

    chat_id = product.get("chat_id")
    media_group_id = product.get("media_group_id")
    message_id = product.get("message_id")

    deleted_ids: List[int] = []
    try:
        if media_group_id:
            deleted_ids = await db.delete_products_by_media_group(media_group_id, chat_id)
        else:
            deleted_ids = await db.delete_products_by_message(message_id, chat_id)
    except Exception:
        logger.exception("Error deleting product(s) for id=%s", product_id)
        try:
            await query.message.reply_text("❌ Error deleting item(s).")
        except Exception:
            logger.exception("Failed to notify user after delete error")
        return

    # Clean up any bot-sent messages that were recorded for this user
    last_displayed = context.user_data.get("last_displayed_messages") or []
    if last_displayed:
        bot = context.bot
        remaining = []
        for (c_id, m_id) in last_displayed:
            try:
                await bot.delete_message(chat_id=c_id, message_id=m_id)
            except Exception:
                remaining.append((c_id, m_id))
        if remaining:
            context.user_data["last_displayed_messages"] = remaining
        else:
            context.user_data.pop("last_displayed_messages", None)

    # Inform the user
    try:
        if deleted_ids:
            await query.message.reply_text(f"Deleted {len(deleted_ids)} item(s) from the catalog.")
        else:
            await query.message.reply_text("Nothing to delete (already removed).")
    except Exception:
        logger.exception("Failed to send delete confirmation message")

    # Try to refresh the catalog UI if the callback came from a catalog message
    try:
        if query.message and query.message.text and query.message.text.lower().startswith("catalog"):
            page = context.user_data.get("catalog_page", 1)
            from handlers import menu as menu_module  # local import to avoid circular import at module load
            await menu_module.handle_catalog_pagination(update, context, page)
    except Exception:
        logger.exception("Failed to refresh catalog UI after deletion")


async def _send_single_and_record(context: ContextTypes.DEFAULT_TYPE, chat_id: int, product: Dict[str, Any]):
    """Send a single media item to chat_id and return the sent Message (or None)."""
    file_id = product.get("file_id")
    caption = product.get("caption") or ""
    file_type = (product.get("file_type") or "").lower()
    try:
        if file_type == "photo":
            return await context.bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption)
        elif file_type == "video":
            return await context.bot.send_video(chat_id=chat_id, video=file_id, caption=caption)
        elif file_type in ("document", "file"):
            return await context.bot.send_document(chat_id=chat_id, document=file_id, caption=caption)
        elif file_type in ("animation", "gif"):
            return await context.bot.send_animation(chat_id=chat_id, animation=file_id, caption=caption)
        elif file_type == "audio":
            return await context.bot.send_audio(chat_id=chat_id, audio=file_id, caption=caption)
        elif file_type == "voice":
            return await context.bot.send_voice(chat_id=chat_id, voice=file_id, caption=caption)
        else:
            # Fallback to document for unknown types
            return await context.bot.send_document(
                chat_id=chat_id,
                document=file_id,
                caption=f"{caption}\n\n(type: {file_type or 'unknown'})",
            )
    except Exception:
        logger.exception("Failed to send single media for product (file_type=%s)", file_type)
        return None


async def _send_media_group_and_record(context: ContextTypes.DEFAULT_TYPE, chat_id: int, items: List[Dict[str, Any]]):
    """
    Send a media group (album) to chat_id and return a list of (chat_id, message_id) for the messages created.
    Non-photo/video items are sent individually and included in the returned list.
    """
    media = []
    non_media = []
    for p in items:
        ft = (p.get("file_type") or "").lower()
        fid = p.get("file_id")
        cap = p.get("caption") or ""
        if not fid:
            logger.debug("Skipping product id=%s with no file_id", p.get("id"))
            continue
        if ft == "photo":
            # Only the first media in the group should carry the caption
            media.append(InputMediaPhoto(media=fid, caption=cap if not media else None))
        elif ft == "video":
            media.append(InputMediaVideo(media=fid, caption=cap if not media else None))
        else:
            non_media.append((ft, fid, cap))

    sent_refs: List[Tuple[int, int]] = []

    logger.debug("Prepared media_group: %d media items, %d non-media items for chat=%s", len(media), len(non_media), chat_id)

    # If we have more than one media item, use send_media_group
    if len(media) > 1:
        try:
            msgs = await context.bot.send_media_group(chat_id=chat_id, media=media)
            for m in msgs:
                sent_refs.append((m.chat.id, m.message_id))
        except Exception:
            logger.exception("send_media_group failed; falling back to individual sends for media items")
            # Fallback: send individually
            for m in media:
                try:
                    if isinstance(m, InputMediaPhoto):
                        sent = await context.bot.send_photo(chat_id=chat_id, photo=m.media, caption=m.caption or "")
                    else:
                        sent = await context.bot.send_video(chat_id=chat_id, video=m.media, caption=m.caption or "")
                    if sent:
                        sent_refs.append((sent.chat.id, sent.message_id))
                except Exception:
                    logger.exception("Fallback individual send failed for media item")
    elif len(media) == 1:
        # Single media item: send as single message to preserve caption reliably
        m = media[0]
        try:
            if isinstance(m, InputMediaPhoto):
                sent = await context.bot.send_photo(chat_id=chat_id, photo=m.media, caption=m.caption or "")
            else:
                sent = await context.bot.send_video(chat_id=chat_id, video=m.media, caption=m.caption or "")
            if sent:
                sent_refs.append((sent.chat.id, sent.message_id))
        except Exception:
            logger.exception("Failed to send single media item for media_group fallback")

    # Send non-media items individually
    for ft, fid, cap in non_media:
        try:
            if ft in ("document", "file"):
                sent = await context.bot.send_document(chat_id=chat_id, document=fid, caption=cap)
            elif ft in ("animation", "gif"):
                sent = await context.bot.send_animation(chat_id=chat_id, animation=fid, caption=cap)
            elif ft == "audio":
                sent = await context.bot.send_audio(chat_id=chat_id, audio=fid, caption=cap)
            elif ft == "voice":
                sent = await context.bot.send_voice(chat_id=chat_id, voice=fid, caption=cap)
            else:
                sent = await context.bot.send_document(chat_id=chat_id, document=fid, caption=cap)
            if sent:
                sent_refs.append((sent.chat.id, sent.message_id))
        except Exception:
            logger.exception("Failed to send non-media item of type %s", ft)

    return sent_refs
