# handlers/search.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from database import db  # use the shared async DB instance
logger = logging.getLogger(__name__)

PAGE_SIZE = 5


def _short_caption(caption: str, length: int = 40) -> str:
    if not caption:
        return "No caption"
    caption = caption.strip().replace("\n", " ")
    return caption if len(caption) <= length else caption[: length - 1].rstrip() + "…"


async def _delete_last_displayed_messages(context: ContextTypes.DEFAULT_TYPE):
    """Delete product display messages recorded in user_data."""
    bot = context.bot
    last = context.user_data.get("last_displayed_messages") or []
    if not last:
        return
    for chat_id, message_id in last:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            logger.debug("Could not delete displayed message %s/%s", chat_id, message_id)
    context.user_data.pop("last_displayed_messages", None)


def _dedupe_products(products):
    """
    Deduplicate products by media_group_id (album) or message_id (single),
    keeping the first occurrence as the representative entry.
    """
    unique = {}
    for p in products or []:
        key = p.get("media_group_id") or p.get("message_id")
        if key is None:
            # Fallback: use product id if both are missing
            key = p.get("id")
        if key not in unique:
            unique[key] = p
    return list(unique.values())


async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle a search query typed by the user.
    Sends a search results message with product buttons and records it in user_data.
    """
    try:
        text = (update.message.text or "").strip() if update.message else ""
        logger.debug("handle_search called: user=%s text=%s", getattr(update.effective_user, "id", None), text)
    except Exception:
        logger.exception("Failed to log entry into handle_search")
        text = (update.message.text or "").strip() if update.message else ""

    query_text = text
    if not query_text:
        try:
            await update.message.reply_text("❌ Please enter a search term.")
        except Exception:
            logger.debug("Failed to send empty-query reply")
        return

    # Delete previous product display messages
    try:
        await _delete_last_displayed_messages(context)
    except Exception:
        logger.exception("Failed to delete last displayed messages before search")

    try:
        products = await db.search_products(query_text, limit=PAGE_SIZE, offset=0)
    except Exception:
        logger.exception("Database search_products failed")
        try:
            await update.message.reply_text("⚠️ Search failed due to an internal error. Please try again later.")
        except Exception:
            logger.debug("Failed to notify user about search failure")
        return

    products = _dedupe_products(products)
    if not products:
        try:
            await update.message.reply_text(f"No results found for '{query_text}'.")
        except Exception:
            logger.debug("Failed to send no-results reply")
        return

    keyboard = []
    for p in products:
        label = _short_caption(p.get("caption", "")) or f"Product {p.get('id')}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"product|{p.get('id')}")])

    nav_row = [
        InlineKeyboardButton("Page 1", callback_data="noop"),
        InlineKeyboardButton("➡️ Next", callback_data=f"search_page|{query_text}|2"),
    ]
    keyboard.append(nav_row)

    try:
        msg = await update.message.reply_text(
            f"Search results for '{query_text}':",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        logger.exception("Failed to send search results message")
        try:
            await update.message.reply_text("⚠️ Failed to send search results. Please try again.")
        except Exception:
            logger.debug("Fallback send after search results failure also failed")
        return

    # Record origin search message
    context.user_data["search_message"] = {
        "chat_id": msg.chat.id,
        "message_id": msg.message_id,
        "query": query_text,
        "page": 1,
    }


async def handle_search_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, query_text: str, page: int):
    """Show a specific page of search results."""
    q = update.callback_query
    if q:
        try:
            await q.answer()
        except Exception:
            logger.debug("Failed to answer callback query in search pagination")

    try:
        await _delete_last_displayed_messages(context)
    except Exception:
        logger.exception("Failed to delete last displayed messages before pagination")

    if page < 1:
        page = 1

    offset = (page - 1) * PAGE_SIZE
    try:
        products = await db.search_products(query_text, limit=PAGE_SIZE, offset=offset)
    except Exception:
        logger.exception("Database search_products failed during pagination")
        try:
            if q and q.message:
                await q.message.reply_text("⚠️ Search failed. Please try again later.")
        except Exception:
            logger.debug("Failed to notify user about pagination DB failure")
        return

    products = _dedupe_products(products)
    if not products:
        try:
            if q and q.message:
                await q.message.reply_text("No more results.")
        except Exception:
            logger.debug("Failed to send 'No more results.' message")
        return

    keyboard = []
    for p in products:
        label = _short_caption(p.get("caption", "")) or f"Product {p.get('id')}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"product|{p.get('id')}")])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"search_page|{query_text}|{page-1}"))
    nav_row.append(InlineKeyboardButton(f"Page {page}", callback_data="noop"))
    nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"search_page|{query_text}|{page+1}"))
    keyboard.append(nav_row)

    text = f"Search results for '{query_text}':"
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if q and q.message:
            await q.message.edit_text(text, reply_markup=reply_markup)
            context.user_data["search_message"] = {
                "chat_id": q.message.chat.id,
                "message_id": q.message.message_id,
                "query": query_text,
                "page": page,
            }
        else:
            # Fallback: send a new message if callback context is missing
            sent = await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)
            context.user_data["search_message"] = {
                "chat_id": sent.chat.id,
                "message_id": sent.message_id,
                "query": query_text,
                "page": page,
            }
    except BadRequest as e:
        if "Message is not modified" in str(e):
            logger.debug("Search message already up to date")
        else:
            logger.exception("Failed to edit search pagination message")
            try:
                if q and q.message:
                    await q.message.reply_text(text, reply_markup=reply_markup)
            except Exception:
                logger.debug("Fallback send after edit failure also failed")
    except Exception:
        logger.exception("Unexpected error while updating search pagination message")


async def handle_back_to_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Return to the original search results message by editing it in place.
    Deletes product display messages to keep chat tidy.
    """
    q = update.callback_query
    if q:
        try:
            await q.answer()
        except Exception:
            logger.debug("Failed to answer callback query in back_to_search")

    try:
        await _delete_last_displayed_messages(context)
    except Exception:
        logger.exception("Failed to delete last displayed messages before returning to search")

    origin = context.user_data.get("search_message")
    if origin:
        query_text = origin.get("query")
        page = origin.get("page", 1)
        offset = (page - 1) * PAGE_SIZE
        try:
            products = await db.search_products(query_text, limit=PAGE_SIZE, offset=offset)
        except Exception:
            logger.exception("Database search_products failed in back_to_search")
            try:
                await context.bot.edit_message_text(
                    chat_id=origin["chat_id"],
                    message_id=origin["message_id"],
                    text="⚠️ Failed to restore search results. Please try again.",
                )
            except Exception:
                logger.debug("Failed to notify user about back_to_search DB failure")
            return

        products = _dedupe_products(products)

        if not products:
            try:
                await context.bot.edit_message_text(
                    chat_id=origin["chat_id"],
                    message_id=origin["message_id"],
                    text=f"No results found for '{query_text}'.",
                )
            except BadRequest as e:
                if "Message is not modified" in str(e):
                    logger.debug("Search message already shows empty results")
                else:
                    logger.exception("Failed to edit empty search message")
            except Exception:
                logger.exception("Unexpected error editing empty search message")
            return

        keyboard = []
        for p in products:
            label = _short_caption(p.get("caption", "")) or f"Product {p.get('id')}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"product|{p.get('id')}")])

        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"search_page|{query_text}|{page-1}"))
        nav_row.append(InlineKeyboardButton(f"Page {page}", callback_data="noop"))
        nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"search_page|{query_text}|{page+1}"))
        keyboard.append(nav_row)

        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await context.bot.edit_message_text(
                chat_id=origin["chat_id"],
                message_id=origin["message_id"],
                text=f"Search results for '{query_text}':",
                reply_markup=reply_markup,
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                logger.debug("Search message already up to date")
            else:
                logger.exception("Failed to edit search message; fallback to sending new one")
                try:
                    await context.bot.send_message(
                        chat_id=origin["chat_id"],
                        text=f"Search results for '{query_text}':",
                        reply_markup=reply_markup,
                    )
                except Exception:
                    logger.exception("Fallback send after edit failure also failed")
    else:
        # Fallback: no origin recorded, just rerun search
        await handle_search(update, context)