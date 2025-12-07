# handlers/product_view.py
import asyncio
from typing import List, Tuple
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
    Uses a per-user lock and last-shown guard to avoid duplicate displays.
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

        sent_message_refs: List[Tuple[int, int]] = []  # list of (chat_id, message_id)

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

    try:
        await query.answer()
    except Exception:
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
        try:
            await query.answer("Invalid product reference", show_alert=True)
        except Exception:
            pass
        return

    await show_product(update, context, product_id)


async def handle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle delete|<product_id> callback.
    If the product belongs to a media_group, delete all rows for that group.
    Otherwise delete all rows for the message_id (covers multi-file single-message cases).
    Also delete any bot-sent messages recorded in context.user_data['last_displayed_messages']
    that correspond to the deleted product ids.
    """
    query = update.callback_query
    if not query:
        return

    # Acknowledge immediately
    try:
        await query.answer()
    except Exception:
        pass

    data = query.data or ""
    parts = data.split("|")
    if len(parts) < 2:
        try:
            await query.answer("Invalid delete request", show_alert=True)
        except Exception:
            pass
        return

    try:
        product_id = int(parts[1])
    except ValueError:
        try:
            await query.answer("Invalid product id", show_alert=True)
        except Exception:
            pass
        return

    # Load product to determine media_group or message_id
    product = await db.get_product(product_id)
    if not product:
        try:
            await query.message.reply_text("Product not found or already deleted.")
        except Exception:
            pass
        return

    chat_id = product.get("chat_id")
    media_group_id = product.get("media_group_id")
    message_id = product.get("message_id")

    deleted_ids: List[int] = []
    if media_group_id:
        deleted_ids = await db.delete_products_by_media_group(media_group_id, chat_id)
    else:
        # Delete all rows that share the same message_id (covers multi-file single message)
        deleted_ids = await db.delete_products_by_message(message_id, chat_id)

    # Clean up any bot-sent messages that were recorded for this user
    last_displayed = context.user_data.get("last_displayed_messages") or []
    if last_displayed:
        # last_displayed is list of (chat_id, message_id) for messages the bot sent when showing product
        # We attempt to delete those messages if they exist and if they correspond to the deleted product(s).
        bot = context.bot
        remaining = []
        for (c_id, m_id) in last_displayed:
            try:
                await bot.delete_message(chat_id=c_id, message_id=m_id)
            except Exception:
                # If deletion fails, keep it in remaining so we don't lose the record
                remaining.append((c_id, m_id))
        # Replace with remaining (if any)
        if remaining:
            context.user_data["last_displayed_messages"] = remaining
        else:
            context.user_data.pop("last_displayed_messages", None)

    # Optionally inform the user
    try:
        if deleted_ids:
            await query.message.reply_text(f"Deleted {len(deleted_ids)} item(s) from the catalog.")
        else:
            await query.message.reply_text("Nothing to delete (already removed).")
    except Exception:
        pass

    # If the catalog message exists in this chat, edit it to remove the deleted item(s)
    # (menu handler uses db.get_catalog_items so it will reflect deletions on next pagination)
    try:
        # Try to update the catalog in-place if the callback came from a catalog message
        if query.message and query.message.text and query.message.text.startswith("Catalog"):
            # Rebuild the same page the user was on
            page = context.user_data.get("catalog_page", 1)
            # Call the menu pagination handler logic by importing the menu module to rebuild UI
            # We avoid circular imports by importing locally
            from handlers import menu as menu_module  # ensure handlers package has __init__.py
            await menu_module.handle_catalog_pagination(update, context, page)
    except Exception:
        # ignore UI update errors
        pass


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
