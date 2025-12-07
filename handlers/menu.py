# handlers/menu.py
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import Database
import logging

logger = logging.getLogger(__name__)
db = Database()
PAGE_SIZE = 5


def _short_caption(caption: str, length: int = 40) -> str:
    if not caption:
        return "No caption"
    caption = caption.strip().replace("\n", " ")
    return caption if len(caption) <= length else caption[: length - 1].rstrip() + "…"


async def _delete_last_displayed_messages(context: ContextTypes.DEFAULT_TYPE):
    """
    Delete messages recorded in context.user_data['last_displayed_messages'].
    Clears the list afterwards.
    """
    bot = context.bot
    last = context.user_data.get("last_displayed_messages") or []
    if not last:
        return
    for chat_id, message_id in last:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            # ignore failures; message may already be gone or not deletable
            logger.debug("Could not delete displayed message %s/%s", chat_id, message_id)
    context.user_data.pop("last_displayed_messages", None)


async def _build_catalog_page(context: ContextTypes.DEFAULT_TYPE, page: int):
    """
    Build the catalog text and InlineKeyboardMarkup for the given page.
    Returns (text, reply_markup).
    """
    offset = (page - 1) * PAGE_SIZE
    products = await db.get_catalog_items(limit=PAGE_SIZE, offset=offset)
    if not products:
        return None, None

    keyboard = []
    for p in products:
        label = _short_caption(p.get("caption", "")) or f"Product {p['id']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"product|{p['id']}")])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"catalog_page|{page-1}"))
    nav_row.append(InlineKeyboardButton(f"Page {page}", callback_data="noop"))
    nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"catalog_page|{page+1}"))
    keyboard.append(nav_row)

    text = "Catalog — select an item to view its media:"
    reply_markup = InlineKeyboardMarkup(keyboard)
    return text, reply_markup


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Show the first page of the catalog as selectable buttons.
    Delete any previously displayed product media first.
    """
    # Delete previous product display messages (if any)
    await _delete_last_displayed_messages(context)

    page = 1
    context.user_data["catalog_page"] = page

    text, reply_markup = await _build_catalog_page(context, page)
    if not text:
        await update.message.reply_text("📦 Catalog is empty.")
        return

    # When user explicitly requests /menu, clear any recorded origin so we start fresh
    context.user_data.pop("origin_catalog_message", None)

    msg = await update.message.reply_text(text, reply_markup=reply_markup)
    # Save reference to the catalog message so product_view can record origin if needed
    try:
        context.user_data["catalog_message"] = {"chat_id": msg.chat.id, "message_id": msg.message_id}
    except Exception:
        logger.exception("Failed to save catalog_message reference")


async def handle_catalog_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    """
    Show a specific page of the catalog as buttons.
    Delete any previously displayed product media first.
    Prefer editing the original catalog message (origin_catalog_message) when available;
    otherwise edit the callback message if it is a catalog message; fallback to sending a new message.
    """
    query = update.callback_query
    # Acknowledge early if we have a query
    if query:
        try:
            await query.answer()
        except Exception:
            logger.exception("Failed to answer catalog pagination callback (non-fatal)")

    # Delete previous product display messages (if any)
    await _delete_last_displayed_messages(context)

    if page < 1:
        page = 1
    context.user_data["catalog_page"] = page

    text, reply_markup = await _build_catalog_page(context, page)
    if not text:
        # No products on this page
        try:
            if query and query.message:
                await query.message.reply_text("No more products.")
            else:
                # fallback: send to user
                dest = update.effective_chat.id if update.effective_chat else None
                if dest:
                    await context.bot.send_message(chat_id=dest, text="No more products.")
        except Exception:
            logger.exception("Failed to notify user about empty catalog page")
        return

    # Try to edit the origin catalog message if one was recorded
    origin = context.user_data.get("origin_catalog_message")
    if origin and isinstance(origin, dict):
        try:
            await context.bot.edit_message_text(
                chat_id=origin["chat_id"],
                message_id=origin["message_id"],
                text=text,
                reply_markup=reply_markup,
            )
            # Save the catalog message reference and clear origin (we've restored it)
            context.user_data["catalog_message"] = {"chat_id": origin["chat_id"], "message_id": origin["message_id"]}
            context.user_data.pop("origin_catalog_message", None)
            return
        except Exception:
            logger.exception("Failed to edit origin_catalog_message; will try other fallbacks")
            # fall through to other options

    # If the callback came from a catalog message the bot owns, try to edit that message
    try:
        if query and query.message and getattr(query.message.from_user, "is_bot", False) and query.message.text and query.message.text.startswith("Catalog"):
            try:
                await query.message.edit_text(text, reply_markup=reply_markup)
                context.user_data["catalog_message"] = {"chat_id": query.message.chat.id, "message_id": query.message.message_id}
                return
            except Exception:
                logger.exception("Failed to edit callback catalog message; will send a new one")
    except Exception:
        logger.exception("Error while attempting to edit callback message (non-fatal)")

    # Fallback: send a new catalog message to the user who invoked the action
    try:
        dest_chat = None
        if query and query.from_user:
            dest_chat = query.from_user.id
        elif update.effective_chat:
            dest_chat = update.effective_chat.id

        if dest_chat:
            msg = await context.bot.send_message(chat_id=dest_chat, text=text, reply_markup=reply_markup)
            context.user_data["catalog_message"] = {"chat_id": msg.chat.id, "message_id": msg.message_id}
    except Exception:
        logger.exception("Failed to send fallback catalog message")