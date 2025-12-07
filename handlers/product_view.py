# handlers/product_view.py
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
from telegram.ext import ContextTypes
from database import Database
from utils.helpers import is_admin

db = Database()


async def show_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    """
    Show a product by ID. If it's part of a media_group, send the whole album.
    Record every message the bot sends for this product in context.user_data["last_displayed_messages"]
    so they can be deleted when the user returns to the catalog.

    This function uses a per-user asyncio.Lock stored in context.user_data to prevent
    concurrent duplicate displays when the same callback/update is processed more than once.
    It also stores the last shown product id to avoid re-displaying the same product
    if the handler is invoked repeatedly for the same product.
    """
    # Ensure a per-user lock to avoid concurrent duplicate sends
    lock = context.user_data.get("_display_lock")
    if lock is None:
        lock = asyncio.Lock()
        context.user_data["_display_lock"] = lock

    async with lock:
        # Prevent re-displaying the same product repeatedly
        last_shown = context.user_data.get("_last_shown_product")
        if last_shown == product_id:
            # Already displayed this product for this user recently; skip to avoid duplicates
            return
        context.user_data["_last_shown_product"] = product_id

        product = await db.get_product(product_id)
        target_msg = (update.message or (update.callback_query and update.callback_query.message))
        if not target_msg:
            return

        # Clear any previously recorded displayed messages for this user (we'll replace below)
        context.user_data.pop("last_displayed_messages", None)

        if not product:
            await target_msg.reply_text("❌ Product not found.")
            return

        sent_message_refs = []  # list of (chat_id, message_id)

        media_group_id = product.get("media_group_id")
        if media_group_id:
            items = await db.get_products_by_media_group(media_group_id, product.get("chat_id"))
            if items:
                refs = await _send_media_group_and_record(target_msg, items)
                sent_message_refs.extend(refs)
            else:
                msg = await _send_single_and_record(target_msg, product)
                if msg:
                    sent_message_refs.append((msg.chat.id, msg.message_id))
        else:
            msg = await _send_single_and_record(target_msg, product)
            if msg:
                sent_message_refs.append((msg.chat.id, msg.message_id))

        # Build action buttons; show delete only to admins
        user = update.effective_user
        buttons = [InlineKeyboardButton("⬅️ Back to catalog", callback_data="menu|1")]
        if user and is_admin(user.id):
            buttons.append(InlineKeyboardButton("🗑️ Delete", callback_data=f"delete|{product_id}"))

        # Updated actions text as requested
        actions_msg = await target_msg.reply_text(
            "ACTIONS : DM @FLYAWAYPEP",
            reply_markup=InlineKeyboardMarkup([buttons])
        )
        sent_message_refs.append((actions_msg.chat.id, actions_msg.message_id))

        # Save the list so menu/search handlers can delete them when user goes back
        context.user_data["last_displayed_messages"] = sent_message_refs


async def handle_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Parse callback data and show the referenced product.
    Acknowledge the callback_query to prevent the client from retrying or showing a spinner.
    """
    query = update.callback_query
    if not query:
        return

    # Acknowledge the callback immediately to stop client-side retries/spinner
    try:
        await query.answer()
    except Exception:
        # ignore answer errors (already answered or network hiccup)
        pass

    data = query.data or ""
    product_id = None
    if "|" in data:
        parts = data.split("|")
        if parts[0] == "product" and len(parts) >= 2:
            try:
                product_id = int(parts[1])
            except ValueError:
                product_id = None
    elif data.startswith("product_"):
        try:
            product_id = int(data.split("_")[-1])
        except ValueError:
            product_id = None

    if product_id is None:
        # Provide a short alert to the user
        try:
            await query.answer("Invalid product reference", show_alert=True)
        except Exception:
            pass
        return

    await show_product(update, context, product_id)


async def _send_single_and_record(target_msg, product: dict):
    """Send a single media item and return the sent Message (or None)."""
    file_id = product.get("file_id")
    caption = product.get("caption") or ""
    file_type = (product.get("file_type") or "").lower()
    try:
        if file_type == "photo":
            return await target_msg.reply_photo(file_id, caption=caption)
        elif file_type == "video":
            return await target_msg.reply_video(file_id, caption=caption)
        elif file_type in ("document", "file"):
            return await target_msg.reply_document(file_id, caption=caption)
        elif file_type in ("animation", "gif"):
            return await target_msg.reply_animation(file_id, caption=caption)
        else:
            return await target_msg.reply_document(
                file_id,
                caption=f"{caption}\n\n(type: {file_type or 'unknown'})"
            )
    except Exception:
        return None


async def _send_media_group_and_record(target_msg, items: list):
    """
    Send a media group (album) and return a list of (chat_id, message_id) for the messages created.
    For non-photo/video items we send them individually and include their ids too.
    """
    media = []
    non_media = []
    for p in items:
        ft = (p.get("file_type") or "").lower()
        fid = p.get("file_id")
        cap = p.get("caption") or ""
        if ft == "photo":
            media.append(InputMediaPhoto(media=fid, caption=cap if not media else None))
        elif ft == "video":
            media.append(InputMediaVideo(media=fid, caption=cap if not media else None))
        else:
            non_media.append((ft, fid, cap))

    sent_refs = []
    if media:
        try:
            msgs = await target_msg.reply_media_group(media)
            for m in msgs:
                sent_refs.append((m.chat.id, m.message_id))
        except Exception:
            for m in media:
                try:
                    if isinstance(m, InputMediaPhoto):
                        sent = await target_msg.reply_photo(m.media, caption=m.caption or "")
                    else:
                        sent = await target_msg.reply_video(m.media, caption=m.caption or "")
                    if sent:
                        sent_refs.append((sent.chat.id, sent.message_id))
                except Exception:
                    pass

    for ft, fid, cap in non_media:
        try:
            if ft in ("document", "file"):
                sent = await target_msg.reply_document(fid, caption=cap)
            elif ft in ("animation", "gif"):
                sent = await target_msg.reply_animation(fid, caption=cap)
            else:
                sent = await target_msg.reply_document(fid, caption=cap)
            if sent:
                sent_refs.append((sent.chat.id, sent.message_id))
        except Exception:
            pass

    return sent_refs
