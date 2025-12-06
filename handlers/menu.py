# handlers/menu.py
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
            pass
    context.user_data.pop("last_displayed_messages", None)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Show the first page of the catalog as selectable buttons.
    Delete any previously displayed product media first.
    """
    # Delete previous product display messages (if any)
    await _delete_last_displayed_messages(context)

    page = 1
    context.user_data["catalog_page"] = page

    offset = (page - 1) * PAGE_SIZE
    products = await db.get_all_products(limit=PAGE_SIZE, offset=offset)
    if not products:
        await update.message.reply_text("📦 Catalog is empty.")
        return

    keyboard = []
    for p in products:
        label = _short_caption(p.get("caption", "")) or f"Product {p['id']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"product|{p['id']}")])

    nav_row = [InlineKeyboardButton(f"Page {page}", callback_data="noop"),
               InlineKeyboardButton("➡️ Next", callback_data=f"catalog_page|{page+1}")]
    keyboard.append(nav_row)

    await update.message.reply_text("Catalog — select an item to view its media:", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_catalog_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    """
    Show a specific page of the catalog as buttons.
    Delete any previously displayed product media first.
    This handler prefers editing the original catalog message; if editing fails, it sends a new message.
    """
    query = update.callback_query
    await query.answer()

    # Delete previous product display messages (if any)
    await _delete_last_displayed_messages(context)

    if page < 1:
        page = 1
    context.user_data["catalog_page"] = page

    offset = (page - 1) * PAGE_SIZE
    products = await db.get_all_products(limit=PAGE_SIZE, offset=offset)
    if not products:
        try:
            await query.message.reply_text("No more products.")
        except Exception:
            pass
        return

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

    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "Catalog — select an item to view its media:"

    try:
        if query.message and query.message.text and query.message.text.startswith("Catalog"):
            await query.message.edit_text(text, reply_markup=reply_markup)
        else:
            await query.message.reply_text(text, reply_markup=reply_markup)
    except Exception:
        try:
            await query.message.reply_text(text, reply_markup=reply_markup)
        except Exception:
            pass
