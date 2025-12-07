# handlers/search.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from database import Database

logger = logging.getLogger(__name__)
db = Database()
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


async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle a search query typed by the user.
    Sends a search results message with product buttons and records it in user_data.
    """
    query_text = update.message.text.strip()
    if not query_text:
        await update.message.reply_text("❌ Please enter a search term.")
        return

    # Delete previous product display messages
    await _delete_last_displayed_messages(context)

    products = await db.search_products(query_text, limit=PAGE_SIZE, offset=0)
    if not products:
        await update.message.reply_text(f"No results found for '{query_text}'.")
        return

    keyboard = []
    for p in products:
        label = _short_caption(p.get("caption", "")) or f"Product {p['id']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"product|{p['id']}")])

    nav_row = [
        InlineKeyboardButton(f"Page 1", callback_data="noop"),
        InlineKeyboardButton("➡️ Next", callback_data=f"search_page|{query_text}|2"),
    ]
    keyboard.append(nav_row)

    msg = await update.message.reply_text(
        f"Search results for '{query_text}':",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

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
            pass

    await _delete_last_displayed_messages(context)

    if page < 1:
        page = 1

    offset = (page - 1) * PAGE_SIZE
    products = await db.search_products(query_text, limit=PAGE_SIZE, offset=offset)
    if not products:
        await q.message.reply_text("No more results.")
        return

    keyboard = []
    for p in products:
        label = _short_caption(p.get("caption", "")) or f"Product {p['id']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"product|{p['id']}")])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"search_page|{query_text}|{page-1}"))
    nav_row.append(InlineKeyboardButton(f"Page {page}", callback_data="noop"))
    nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"search_page|{query_text}|{page+1}"))
    keyboard.append(nav_row)

    text = f"Search results for '{query_text}':"
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await q.message.edit_text(text, reply_markup=reply_markup)
        context.user_data["search_message"] = {
            "chat_id": q.message.chat.id,
            "message_id": q.message.message_id,
            "query": query_text,
            "page": page,
        }
    except BadRequest as e:
        if "Message is not modified" in str(e):
            logger.debug("Search message already up to date")
        else:
            logger.exception("Failed to edit search pagination message")
            await q.message.reply_text(text, reply_markup=reply_markup)


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
            pass

    await _delete_last_displayed_messages(context)

    origin = context.user_data.get("search_message")
    if origin:
        query_text = origin.get("query")
        page = origin.get("page", 1)
        offset = (page - 1) * PAGE_SIZE
        products = await db.search_products(query_text, limit=PAGE_SIZE, offset=offset)

        if not products:
            await context.bot.edit_message_text(
                chat_id=origin["chat_id"],
                message_id=origin["message_id"],
                text=f"No results found for '{query_text}'.",
            )
            return

        keyboard = []
        for p in products:
            label = _short_caption(p.get("caption", "")) or f"Product {p['id']}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"product|{p['id']}")])

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
                await context.bot.send_message(
                    chat_id=origin["chat_id"],
                    text=f"Search results for '{query_text}':",
                    reply_markup=reply_markup,
                )
    else:
        # Fallback: no origin recorded, just rerun search
        await handle_search(update, context)