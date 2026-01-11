# handlers/admin.py
"""
Admin-related handlers: ingestion, preview sending, admin callbacks (category assignment).
This module handles messages from the monitored channel and sends a single preview to admins.
"""

from __future__ import annotations

import logging
from typing import Optional, List

from telegram import Message, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import ADMIN_IDS, PREVIEW_TTL_SECONDS
from config.taxonomy import CATEGORIES as TAXONOMY
from database import db
from utils.categories import apply_admin_categorization

logger = logging.getLogger(__name__)


async def ingest_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Ingest messages from the monitored channel or admins.
    Save each media file as a product row and, for media groups, attempt to send a single preview to admins.
    """
    try:
        msg: Message = update.effective_message
        if not msg:
            return

        chat_id = msg.chat_id
        message_id = msg.message_id
        media_group_id = getattr(msg, "media_group_id", None)
        # Determine file(s) in message: photo, video, document, etc.
        files = []
        if msg.photo:
            # photo is a list of sizes; take the largest
            photo = msg.photo[-1]
            files.append(("photo", photo.file_id, photo.file_unique_id))
        if msg.video:
            files.append(("video", msg.video.file_id, msg.video.file_unique_id))
        if msg.document:
            files.append(("document", msg.document.file_id, msg.document.file_unique_id))

        if not files:
            # nothing to ingest
            return

        # Save each file to DB
        saved_ids = []
        for file_type, file_id, file_unique_id in files:
            pid = await db.add_product(
                file_id=file_id,
                file_type=file_type,
                caption=msg.caption or "",
                message_id=message_id,
                chat_id=chat_id,
                media_group_id=media_group_id,
                file_unique_id=file_unique_id,
            )
            saved_ids.append(pid)

        logger.info("Ingested message %s/%s -> saved ids %s", chat_id, message_id, saved_ids)

        # If this message is part of a media_group, attempt to atomically mark preview sent
        if media_group_id:
            # try_mark returns True only for the first caller that inserted the row
            should_send = await db.try_mark_media_group_preview_sent(media_group_id, chat_id)
            if should_send:
                # Build preview message for admins
                await send_admin_preview(msg, media_group_id, chat_id, context)
            else:
                logger.debug("Preview already sent for media_group %s chat %s", media_group_id, chat_id)
        else:
            # For single-file messages, we can treat message_id as unique group
            # Use a synthetic media_group_id = f"{chat_id}:{message_id}"
            synthetic_mg = f"{chat_id}:{message_id}"
            should_send = await db.try_mark_media_group_preview_sent(synthetic_mg, chat_id)
            if should_send:
                await send_admin_preview(msg, synthetic_mg, chat_id, context)
    except Exception:
        logger.exception("ingest_message failed")


async def send_admin_preview(msg: Message, media_group_id: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Send a single preview to all admins with inline buttons to set category/subcategory.
    The preview includes the caption (if any) and buttons for categories.
    """
    try:
        caption = msg.caption or "(No caption)"
        keyboard = []
        # top-level categories: use canonical keys from TAXONOMY
        for cat in TAXONOMY.keys():
            # Display label: replace underscores with spaces and title-case for readability
            display = cat.replace("_", " ").title()
            # callback_data format: ADM:SETCAT:<media_group_id>:<chat_id>:<category_key>
            callback = f"ADM:SETCAT:{media_group_id}:{chat_id}:{cat}"
            keyboard.append([InlineKeyboardButton(text=display, callback_data=callback)])
        # Add a "Skip" button
        keyboard.append([InlineKeyboardButton(text="Skip", callback_data=f"ADM:SKIP:{media_group_id}:{chat_id}")])
        markup = InlineKeyboardMarkup(keyboard)

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"Preview for media_group {media_group_id}\n\n{caption}",
                    reply_markup=markup,
                )
            except Exception:
                logger.exception("Failed to send preview to admin %s", admin_id)
    except Exception:
        logger.exception("send_admin_preview failed")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command: show simple stats."""
    try:
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            if update.message:
                await update.message.reply_text("Unauthorized")
            return
        # simple stats
        items = await db.get_catalog_items(limit=1)
        await update.message.reply_text(f"Catalog items (sample): {len(items)}")
    except Exception:
        logger.exception("cmd_stats failed")


async def cmd_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command: run a quick DB normalization (dangerous; admin-only)."""
    try:
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            if update.message:
                await update.message.reply_text("Unauthorized")
            return
        # For safety, we do not run destructive cleanup here; instruct admin to run offline script
        await update.message.reply_text("Cleanup is a manual operation. Please run the DB normalization script offline.")
    except Exception:
        logger.exception("cmd_cleanup failed")


async def cb_admin_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Router for admin callback queries. Patterns:
    ADM:SETCAT:<mg>:<chat>:<CAT>
    ADM:SKIP:<mg>:<chat>
    ADM:SETSUB:<mg>:<chat>:<CAT>:<SUB>
    ADM:DELETE:<mg>:<chat>
    """
    try:
        query = update.callback_query
        if not query:
            return

        user = query.from_user
        # Only allow admins to act
        if not user or user.id not in ADMIN_IDS:
            await query.answer("Unauthorized", show_alert=True)
            return

        data = query.data or ""
        # Basic safety: ensure prefix
        if not data.startswith("ADM:"):
            await query.answer("Invalid admin action", show_alert=True)
            return

        # Split into at most 5 parts so media_group_id can contain colons if needed
        parts = data.split(":", 4)
        action = parts[1] if len(parts) > 1 else None

        if action == "SETCAT":
            # Expect parts: ["ADM","SETCAT","<mg>","<chat>","<cat>"]
            if len(parts) < 5:
                await query.answer("Malformed SETCAT data", show_alert=True)
                return
            mg = parts[2]
            chat = parts[3]
            cat = parts[4]
            # Get subcategories for this canonical category key
            subs = TAXONOMY.get(cat, [])
            keyboard = []
            # If there are subcategories, present them
            if subs:
                for sub in subs:
                    display_sub = sub.replace("_", " ").title()
                    # callback: ADM:SETSUB:<mg>:<chat>:<cat>:<sub>
                    cb = f"ADM:SETSUB:{mg}:{chat}:{cat}:{sub}"
                    keyboard.append([InlineKeyboardButton(text=display_sub, callback_data=cb)])
            else:
                # No subcategories: apply category immediately
                try:
                    await db.update_products_by_media_group(mg, int(chat), cat, None)
                    await query.edit_message_text(text=f"Set category {cat} for media_group {mg}")
                except Exception:
                    logger.exception("Failed to set category for media_group %s", mg)
                    await query.edit_message_text(text="Failed to set category; see logs.")
                return

            # Add cancel button
            keyboard.append([InlineKeyboardButton(text="Cancel", callback_data=f"ADM:CANCEL:{mg}:{chat}")])
            await query.edit_message_text(text=f"Choose subcategory for {cat.replace('_',' ').title()}", reply_markup=InlineKeyboardMarkup(keyboard))

        elif action == "SETSUB":
            # Expect parts: ["ADM","SETSUB","<mg>","<chat>","<cat>:<sub>"] OR we used maxsplit so last part contains "<cat>:<sub>"
            if len(parts) < 5:
                await query.answer("Malformed SETSUB data", show_alert=True)
                return
            mg = parts[2]
            chat = parts[3]
            rest = parts[4]
            # rest should be "<cat>:<sub>" — split once
            if ":" not in rest:
                await query.answer("Malformed SETSUB payload", show_alert=True)
                return
            cat, sub = rest.split(":", 1)
            try:
                await db.update_products_by_media_group(mg, int(chat), cat, sub)
                await query.edit_message_text(text=f"Set category {cat} / {sub} for media_group {mg}")
            except Exception:
                logger.exception("Failed to set subcategory for media_group %s", mg)
                await query.edit_message_text(text="Failed to set category/subcategory; see logs.")

        elif action == "SKIP":
            # Expect parts: ["ADM","SKIP","<mg>","<chat>"]
            if len(parts) < 4:
                await query.answer("Malformed SKIP data", show_alert=True)
                return
            mg = parts[2]
            # chat may be parts[3] (we don't need it here)
            await query.edit_message_text(text=f"Skipped categorization for {mg}")

        elif action == "DELETE":
            # Expect parts: ["ADM","DELETE","<mg>","<chat>"]
            if len(parts) < 4:
                await query.answer("Malformed DELETE data", show_alert=True)
                return
            mg = parts[2]
            chat = parts[3]
            try:
                deleted = await db.delete_products_by_media_group(mg, int(chat))
                await query.edit_message_text(text=f"Deleted {len(deleted)} products for {mg}")
            except Exception:
                logger.exception("Failed to delete products for media_group %s", mg)
                await query.edit_message_text(text="Failed to delete products; see logs.")

        elif action == "CANCEL":
            # Expect parts: ["ADM","CANCEL","<mg>","<chat>"]
            if len(parts) < 4:
                await query.answer("Malformed CANCEL data", show_alert=True)
                return
            mg = parts[2]
            await query.edit_message_text(text=f"Cancelled categorization for {mg}")

        else:
            await query.answer("Unknown admin action", show_alert=True)

    except Exception:
        logger.exception("cb_admin_router failed")