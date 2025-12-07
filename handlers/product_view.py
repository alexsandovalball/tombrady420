# handlers/product_view.py
"""
Product view and callback handlers for the catalog bot.

Changes made:
- Answer callback queries early and handle expired-query BadRequest gracefully.
- Avoid the "do nothing" behavior on repeated clicks by only suppressing redisplay
  when the product is already shown and the bot still has recorded displayed messages.
- Send media directly to the user's chat via context.bot.send_* (safer than relying on
  reply/edit permissions) and record sent message ids in context.user_data["last_displayed_messages"].
- Add robust logging and exception handling throughout.
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

from database import Database
from utils.helpers import is_admin

logger = logging.getLogger(__name__)
db = Database()


def _parse_product_id_from_callback(data: str) -> Optional[int]:
    """Parse callback data like 'product|38' or 'product_38' or just '38'."""
    if not data:
        return None
    data = data.strip()
    # Normalize separators
    parts = data.replace("_", "|").split("|")
    # If it starts with 'product' return last part
    if parts and parts[0].lower().startswith("product") and len(parts) >= 2:
        try:
            return int(parts[-1])
        except (ValueError, TypeError):
            return None
    # If last part is numeric, return it
    try:
        return int(parts[-1])
    except Exception:
        return None


async def show_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    """
    Show a product by ID. If it's part of a media_group, send the whole album.
    Record every message the bot sends for this product in context.user_data["last_displayed_messages"]
    so they can be deleted when the user returns to the catalog.

    Guard against duplicate displays: only suppress redisplay when the same product was
    shown previously AND the bot still has recorded messages for that display.
    """
    # Ensure a per-user lock to avoid concurrent duplicate sends
    lock = context.user_data.get("_display_lock")
    if lock is None:
        lock = asyncio.Lock()
        context.user_data["_display_lock"] = lock

    async with lock:
        # Prevent re-displaying the same product repeatedly only if we still have recorded messages
        last_shown = context.user_data.get("_last_shown_product")
        last_displayed = context.user_data.get("last_displayed_messages")
        if last_shown == product_id and last_displayed:
            logger.debug("Skipping redisplay for product %s because it was just shown and messages still exist", product_id)
            return

        # Mark as shown (we'll update recorded messages below)
        context.user_data["_last_shown_product"] = product_id

        # Determine destination chat id (always send to the user who invoked)
        user = update.effective_user
        if not user:
            logger.warning("show_product: no effective_user in update")
            return
        dest_chat_id = user.id

        # Clear any previously recorded displayed messages for this user (we'll replace below)
        context.user_data.pop("last_displayed_messages", None)

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
        buttons = [InlineKeyboardButton("⬅️ Back to catalog", callback_data="menu|1")]
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
            # Ensure key is absent if nothing was sent
            context.user_data.pop("last_displayed_messages", None)


async def handle_product_callback(update: Update,