# handlers/admin.py
"""
Admin-related handlers: ingestion, preview sending, admin callbacks (category assignment).

Key improvements applied:
- Stateless admin callbacks (all necessary identifiers are encoded in callback_data).
- Defensive parsing of callback payloads with robust validation.
- Canonicalization and validation of category/subcategory via utils.taxonomy_utils.
- Clear success/failure handling for DB operations and informative admin messages.
- Safer logging (structured, no secrets), and comprehensive exception handling.
- Avoids direct SQL or DB_PATH usage in handlers; expects DB helper functions on `db`.
"""

from __future__ import annotations

import json
import logging
from typing import Optional, List, Sequence

from telegram import Message, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import ADMIN_IDS, PREVIEW_TTL_SECONDS
from config.taxonomy import CATEGORIES as TAXONOMY, DISPLAY_NAMES
from database import db
from utils.taxonomy_utils import validate_category_and_subcategory, canonicalize_key

logger = logging.getLogger(__name__)


# -------------------------
# Helpers
# -------------------------
def _display_label(key: str) -> str:
    """Return a human-friendly label for a taxonomy key."""
    if not key:
        return ""
    try:
        return DISPLAY_NAMES.get(key.upper(), key.replace("_", " ").title())
    except Exception:
        return key.replace("_", " ").title()


async def _safe_reply_text(update: Update, text: str) -> None:
    """Reply to a message if present, otherwise send to chat."""
    try:
        if update.message:
            await update.message.reply_text(text)
        elif update.effective_chat:
            await update.effective_chat.send_message(text)
    except Exception:
        logger.exception("Failed to send reply text")


async def _safe_edit_message(query, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> None:
    """Edit message text safely, catching common Telegram errors."""
    try:
        if reply_markup is not None:
            await query.edit_message_text(text, reply_markup=reply_markup)
        else:
            await query.edit_message_text(text)
    except Exception:
        # Many reasons editing can fail (MessageNotModified, message deleted, etc.)
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
    Ingest messages from the monitored channel or admins.
    Save each media file as a product row and, for media groups, attempt to send a single preview to admins.
    """
    try:
        msg: Optional[Message] = update.effective_message
        if not msg:
            return

        chat_id = getattr(msg.chat, "id", None)
        message_id = getattr(msg, "message_id", None)
        media_group_id = getattr(msg, "media_group_id", None)

        # Collect files present in the message
        files: List[tuple] = []
        if getattr(msg, "photo", None):
            photo = msg.photo[-1]
            files.append(("photo", photo.file_id, photo.file_unique_id))
        if getattr(msg, "video", None):
            files.append(("video", msg.video.file_id, msg.video.file_unique_id))
        if getattr(msg, "document", None):
            files.append(("document", msg.document.file_id, msg.document.file_unique_id))
        if getattr(msg, "animation", None):
            files.append(("animation", msg.animation.file_id, getattr(msg.animation, "file_unique_id", None)))
        if getattr(msg, "audio", None):
            files.append(("audio", msg.audio.file_id, getattr(msg.audio, "file_unique_id", None)))
        if getattr(msg, "voice", None):
            files.append(("voice", msg.voice.file_id, getattr(msg.voice, "file_unique_id", None)))

        if not files:
            # Nothing to ingest
            return

        # Persist each file as a product row
        saved_ids: List[int] = []
        for file_type, file_id, file_unique_id in files:
            try:
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
            except Exception:
                logger.exception("Failed to add product row for file in message %s/%s", chat_id, message_id)

        logger.info("Ingested message %s/%s -> saved ids=%s", chat_id, message_id, saved_ids)

        # Determine canonical media_group_id for this logical group.
        # Order of preference:
        # 1) explicit media_group_id from the incoming message
        # 2) any saved product row that has a media_group_id (propagated by add_product)
        # 3) a media_group_id already present in DB for this message (get_media_group_for_message)
        # 4) synthetic id (chat:message) for single-file messages
        canonical_mg: Optional[str] = None
        if media_group_id:
            canonical_mg = media_group_id

        if not canonical_mg:
            # Check saved product rows for a propagated media_group_id
            for pid in saved_ids:
                try:
                    prod = await db.get_product(pid)
                    if prod:
                        mg = prod.get("media_group_id")
                        if mg:
                            canonical_mg = mg
                            break
                except Exception:
                    logger.debug("Failed to inspect saved product %s for media_group", pid, exc_info=True)

        if not canonical_mg and message_id is not None and chat_id is not None:
            try:
                mg_from_db = await db.get_media_group_for_message(message_id, chat_id)
                if mg_from_db:
                    canonical_mg = mg_from_db
            except Exception:
                logger.debug("get_media_group_for_message failed for %s/%s", message_id, chat_id, exc_info=True)

        # If still no canonical group, use synthetic id for single-message items
        if not canonical_mg:
            canonical_mg = f"{chat_id}:{message_id}"

        # Prefer a caption from any product in the group (if available)
        preview_caption = msg.caption or ""
        try:
            if canonical_mg and canonical_mg != f"{chat_id}:{message_id}":
                # fetch group products and prefer any non-empty caption
                group_products = await db.get_products_by_media_group(canonical_mg, chat_id)
                for p in group_products:
                    cap = (p.get("caption") or "").strip()
                    if cap:
                        preview_caption = cap
                        break
            else:
                # For synthetic group, check saved products for caption
                for pid in saved_ids:
                    prod = await db.get_product(pid)
                    if prod:
                        cap = (prod.get("caption") or "").strip()
                        if cap:
                            preview_caption = cap
                            break
        except Exception:
            logger.debug("Failed to determine preview caption for %s", canonical_mg, exc_info=True)

        # Decide whether to send admin preview (first writer wins for a media_group)
        try:
            should_send = await db.try_mark_media_group_preview_sent(canonical_mg, chat_id)
        except Exception:
            logger.exception("try_mark_media_group_preview_sent failed for %s/%s", canonical_mg, chat_id)
            should_send = False

        if should_send:
            await send_admin_preview(msg, canonical_mg, chat_id, context, caption_override=preview_caption)
        else:
            logger.debug("Preview already sent for media_group %s chat %s", canonical_mg, chat_id)

    except Exception:
        logger.exception("ingest_message failed")


async def send_admin_preview(
    msg: Message,
    media_group_id: str,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    caption_override: Optional[str] = None,
) -> None:
    """
    Send a single preview to all admins with inline buttons to set category/subcategory.
    Callback payloads are stateless and include media_group_id and chat_id.
    """
    try:
        caption = caption_override if caption_override is not None else (msg.caption or "(No caption)")
        keyboard = []

        # Build top-level category buttons
        for cat in TAXONOMY.keys():
            display = _display_label(cat)
            # callback_data: ADM:SETCAT:<mg>:<chat>:<cat>
            callback = f"ADM:SETCAT:{media_group_id}:{chat_id}:{cat}"
            keyboard.append([InlineKeyboardButton(text=display, callback_data=callback)])

        # Skip and Delete actions
        keyboard.append([InlineKeyboardButton(text="Skip", callback_data=f"ADM:SKIP:{media_group_id}:{chat_id}")])
        keyboard.append([InlineKeyboardButton(text="Delete", callback_data=f"ADM:DELETE:{media_group_id}:{chat_id}")])

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


# -------------------------
# Admin commands
# -------------------------
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command: show simple stats."""
    try:
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            await _safe_reply_text(update, "Unauthorized")
            return

        stats = await db.get_stats()
        total = stats.get("total", 0)
        today = stats.get("today", 0)
        await _safe_reply_text(update, f"📊 Total products: {total}\n📅 Added today: {today}")
    except Exception:
        logger.exception("cmd_stats failed")
        await _safe_reply_text(update, "❌ Failed to fetch stats.")


async def cmd_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command: run a quick DB normalization (non-destructive guidance)."""
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
# Admin callback router (stateless)
# -------------------------
async def cb_admin_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Router for admin callback queries. Supported patterns (stateless):
      ADM:SETCAT:<mg>:<chat>:<CAT>
      ADM:SETSUB:<mg>:<chat>:<CAT>:<SUB>
      ADM:SKIP:<mg>:<chat>
      ADM:DELETE:<mg>:<chat>
      ADM:CANCEL:<mg>:<chat>
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
        if not data.startswith("ADM:"):
            await query.answer("Invalid admin action", show_alert=True)
            return

        # Split into parts; allow media_group_id to contain colons by limiting splits
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

            # Validate category key
            cat_canon = canonicalize_key(cat)
            if cat_canon not in TAXONOMY:
                await query.answer("Invalid category selected", show_alert=True)
                return

            subs = TAXONOMY.get(cat_canon, []) or []
            if subs:
                # Present subcategory choices; include sub in payload statelessly
                keyboard = []
                for sub in subs:
                    sub_canon = canonicalize_key(sub)
                    display_sub = _display_label(sub_canon)
                    # callback: ADM:SETSUB:<mg>:<chat>:<cat>:<sub>
                    cb = f"ADM:SETSUB:{mg}:{chat}:{cat_canon}:{sub_canon}"
                    keyboard.append([InlineKeyboardButton(text=display_sub, callback_data=cb)])
                keyboard.append([InlineKeyboardButton(text="Cancel", callback_data=f"ADM:CANCEL:{mg}:{chat}")])
                await query.edit_message_text(
                    text=f"Choose subcategory for {_display_label(cat_canon)}",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                return
            else:
                # No subcategories: apply category immediately
                try:
                    success = await db.update_products_by_media_group(mg, int(chat), cat_canon, None)
                    if success:
                        await query.edit_message_text(text=f"✅ Set category {_display_label(cat_canon)} for media_group {mg}")
                    else:
                        await query.edit_message_text(text="❌ Failed to set category (DB returned failure).")
                except Exception:
                    logger.exception("Failed to set category for media_group %s", mg)
                    await query.edit_message_text(text="❌ Failed to set category; see server logs.")
                return

        elif action == "SETSUB":
            # Expect parts: ["ADM","SETSUB","<mg>","<chat>:<cat>:<sub>"] or similar; robust parsing below
            raw_parts = data.split(":")
            if len(raw_parts) < 6:
                await query.answer("Malformed SETSUB data", show_alert=True)
                return
            mg = raw_parts[2]
            chat = raw_parts[3]
            cat = raw_parts[4]
            sub = raw_parts[5]

            cat_canon = canonicalize_key(cat)
            sub_canon = canonicalize_key(sub)

            # Validate category/subcategory pair
            cat_valid, sub_valid = validate_category_and_subcategory(cat_canon, sub_canon)
            if not cat_valid:
                await query.answer("Invalid category", show_alert=True)
                return

            try:
                success = await db.update_products_by_media_group(mg, int(chat), cat_valid, sub_valid)
                if success:
                    await query.edit_message_text(text=f"✅ Set category {_display_label(cat_valid)} / {_display_label(sub_valid)} for media_group {mg}")
                else:
                    await query.edit_message_text(text="❌ Failed to set category/subcategory (DB returned failure).")
            except Exception:
                logger.exception("Failed to set subcategory for media_group %s", mg)
                await query.edit_message_text(text="❌ Failed to set category/subcategory; see server logs.")
            return

        elif action == "SKIP":
            # Expect parts: ["ADM","SKIP","<mg>","<chat>"]
            if len(parts) < 4:
                await query.answer("Malformed SKIP data", show_alert=True)
                return
            mg = parts[2]
            await query.edit_message_text(text=f"Skipped categorization for {mg}")
            return

        elif action == "DELETE":
            # Expect parts: ["ADM","DELETE","<mg>","<chat>"]
            if len(parts) < 4:
                await query.answer("Malformed DELETE data", show_alert=True)
                return
            mg = parts[2]
            chat = parts[3]
            try:
                deleted = await db.delete_products_by_media_group(mg, int(chat))
                count = len(deleted) if isinstance(deleted, (list, tuple)) else (deleted or 0)
                await query.edit_message_text(text=f"Deleted {count} products for {mg}")
            except Exception:
                logger.exception("Failed to delete products for media_group %s", mg)
                await query.edit_message_text(text="❌ Failed to delete products; see server logs.")
            return

        elif action == "CANCEL":
            # Expect parts: ["ADM","CANCEL","<mg>","<chat>"]
            if len(parts) < 4:
                await query.answer("Malformed CANCEL data", show_alert=True)
                return
            mg = parts[2]
            await query.edit_message_text(text=f"Cancelled categorization for {mg}")
            return

        else:
            await query.answer("Unknown admin action", show_alert=True)
            return

    except Exception:
        logger.exception("cb_admin_router failed")


# -------------------------
# Convenience admin commands
# -------------------------
async def list_products_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List recent products for admins."""
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
        # Chunk to avoid Telegram message size limits
        for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)]:
            await _safe_reply_text(update, chunk)
    except Exception:
        logger.exception("list_products_command failed")
        await _safe_reply_text(update, "❌ Failed to list products.")


async def set_category_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command to set or override category/subcategory for a product.
    Usage: /setcategory <product_id> <category> [subcategory]
    """
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
    """Show the hardwired taxonomy to admins."""
    try:
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            await _safe_reply_text(update, "❌ You are not authorized to use this command.")
            return

        lines: List[str] = []
        for cat, subs in TAXONOMY.items():
            if subs:
                lines.append(f"{_display_label(cat)} -> {', '.join([_display_label(s) for s in subs])}")
            else:
                lines.append(f"{_display_label(cat)} -> (no subcategories)")
        await _safe_reply_text(update, "Allowed categories and subcategories:\n" + "\n".join(lines))
    except Exception:
        logger.exception("list_categories_command failed")
        await _safe_reply_text(update, "❌ Failed to list categories.")


# -------------------------
# Backfill / nuke utilities (call DB-layer helpers where possible)
# -------------------------
async def backfill_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin-only command to normalize category/subcategory values and propagate tags
    to media_group representative rows. Usage: /backfill_db
    """
    try:
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            await _safe_reply_text(update, "❌ You are not authorized to run this command.")
            return

        await _safe_reply_text(update, "⚙️ Running DB backfill. This may take a moment...")

        # Prefer DB-layer helpers if available
        changed_normalize = 0
        changed_propagate = 0
        if hasattr(db, "backfill_normalize") and hasattr(db, "backfill_propagate_media_group"):
            try:
                changed_normalize = await db.backfill_normalize()
                changed_propagate = await db.backfill_propagate_media_group()
            except Exception:
                logger.exception("DB backfill helpers failed; falling back to no-op")
        else:
            # If DB helpers are not present, inform admin to run offline script
            await _safe_reply_text(update, "DB backfill helpers not available on this deployment. Run offline script.")
            return

        msg = (
            f"✅ Backfill complete.\n"
            f"Rows changed by normalization: {changed_normalize}\n"
            f"Rows changed by media_group propagation: {changed_propagate}"
        )
        await _safe_reply_text(update, msg)
    except Exception:
        logger.exception("backfill_db_command failed")
        await _safe_reply_text(update, "❌ Backfill failed due to an internal error.")


async def nuke_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Two-step confirmation for nuking all products. This handler only starts the flow;
    the actual destructive operation should be implemented in the DB layer and require
    an explicit confirmation token to execute.
    """
    try:
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            await _safe_reply_text(update, "❌ You are not authorized to use this command.")
            return

        # Build a simple confirmation keyboard with stateless payloads
        # ADM:NUKE_CONFIRM and ADM:NUKE_CANCEL
        keyboard = [
            [InlineKeyboardButton("✅ Yes, delete all", callback_data="ADM:NUKE_CONFIRM:1")],
            [InlineKeyboardButton("❌ Cancel", callback_data="ADM:NUKE_CANCEL:1")],
        ]
        if update.message:
            await update.message.reply_text("⚠️ Are you sure you want to delete ALL products? This is irreversible.", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await _safe_reply_text(update, "⚠️ Are you sure you want to delete ALL products? This is irreversible.")
    except Exception:
        logger.exception("nuke_command failed")
        await _safe_reply_text(update, "❌ Failed to initiate nuke.")


async def cb_nuke_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the nuke confirmation callbacks (separate router to keep responsibilities clear)."""
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
            # Perform nuke via DB-layer helper if available
            if hasattr(db, "nuke_all_products"):
                try:
                    count = await db.nuke_all_products()
                    await query.edit_message_text(f"✅ Nuke complete. Deleted {count} products.")
                    logger.warning("NUKE executed by admin=%s; deleted=%s", user.id, count)
                except Exception:
                    logger.exception("NUKE failed during execution")
                    await query.edit_message_text("❌ Nuke failed due to an internal error.")
            else:
                await query.edit_message_text("Nuke helper not available on this deployment. Run offline script.")
        elif data.startswith("ADM:NUKE_CANCEL"):
            await query.edit_message_text("❌ Nuke cancelled.")
        else:
            await query.answer("Unknown nuke action", show_alert=True)
    except Exception:
        logger.exception("cb_nuke_router failed")