# handlers/search.py
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import Database

db = Database()
PAGE_SIZE = 5


def _short_caption(caption: str, length: int = 40) -> str:
    if not caption:
        return "No caption"
    caption = caption.strip().replace("\n", " ")
    return caption if len(caption) <= length else caption[: length - 1].rstrip() + "…"


async def _delete_last_displayed_messages(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    last = context.user_data.get("last_displayed_messages") or []
    if not last:
        return
    for chat_id, message_id in last:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass
    context.user_data.pop("last_displayed_messages", None)


async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Treat the incoming private text message as a search query.
    Show results as a list of buttons (one button per product).
    Delete any previously displayed product media first.
    """
    # Delete previous product display messages (if any)
    await _delete_last_displayed_messages(context)

    query_text = (update.message.text or "").strip()
    if not query_text:
        await update.message.reply_text("❌ Please enter a search term.")
        return

    context.user_data["last_query"] = query_text
    context.user_data["search_page"] = 1

    offset = 0
    results = await db.search_products(query_text, limit=PAGE_SIZE, offset=offset)
    if not results:
        await update.message.reply_text("🔍 No results found.")
        return

    keyboard = []
    for p in results:
        label = _short_caption(p.get("caption", "")) or f"Product {p['id']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"product|{p['id']}")])

    keyboard.append([InlineKeyboardButton("➡️ Next", callback_data=f"search_page|{2}")])
    await update.message.reply_text(f"Search results for: {query_text}", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_search_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    """
    Show a specific page of search results as buttons.
    Delete any previously displayed product media first.
    """
    query = update.callback_query
    await query.answer()

    # Delete previous product display messages (if any)
    await _delete_last_displayed_messages(context)

    last_query = context.user_data.get("last_query", "")
    if not last_query:
        await query.message.reply_text("No active search. Send a query first.")
        return

    if page < 1:
        page = 1
    context.user_data["search_page"] = page

    offset = (page - 1) * PAGE_SIZE
    results = await db.search_products(last_query, limit=PAGE_SIZE, offset=offset)
    if not results:
        await query.message.reply_text("No more results.")
        return

    keyboard = []
    for p in results:
        label = _short_caption(p.get("caption", "")) or f"Product {p['id']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"product|{p['id']}")])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"search_page|{page-1}"))
    nav_row.append(InlineKeyboardButton(f"Page {page}", callback_data="noop"))
    nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"search_page|{page+1}"))
    keyboard.append(nav_row)

    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"Search results for: {last_query}"

    try:
        if query.message and query.message.text and query.message.text.startswith("Search results for:"):
            await query.message.edit_text(text, reply_markup=reply_markup)
        else:
            await query.message.reply_text(text, reply_markup=reply_markup)
    except Exception:
        try:
            await query.message.reply_text(text, reply_markup=reply_markup)
        except Exception:
            pass
