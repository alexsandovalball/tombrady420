"""
User-facing handlers for catalog browsing: categories, subcategories, paginated product listings.
"""

from telegram import Update, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
from telegram.ext import ContextTypes, CallbackQueryHandler, Application
from database import get_categories, get_db
from utils.helpers import (
    build_category_keyboard,
    build_subcategory_keyboard,
    build_product_pagination_keyboard,
    paginate,
    total_pages,
    select_representative_media,
)
from config.config import PRODUCTS_PER_PAGE

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start command handler: show main menu (categories).
    """
    categories = await get_categories()
    keyboard = build_category_keyboard(categories)
    await update.message.reply_text("Browse our catalog by category:", reply_markup=keyboard)

async def category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle category selection: show subcategories.
    """
    query = update.callback_query
    await query.answer()
    category = query.data.split(":", 1)[1]
    keyboard = build_subcategory_keyboard(category)
    await query.edit_message_text(
        f"Select a subcategory in {category}:", reply_markup=keyboard
    )

async def subcategory_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle subcategory selection: show paginated product listings.
    """
    query = update.callback_query
    await query.answer()
    _, category, subcategory = query.data.split(":")
    page = 1
    await show_product_page(query, category, subcategory, page)

async def show_product_page(query, category, subcategory, page):
    """
    Display a page of products for the given category/subcategory.
    """
    async with await get_db() as db:
        async with db.execute(
            """
            SELECT * FROM products
            WHERE category = ? AND subcategory = ? AND is_deleted = 0
            ORDER BY created_at DESC
            """,
            (category, subcategory)
        ) as cursor:
            products = [dict(row) for row in await cursor.fetchall()]
    total = len(products)
    pages = total_pages(products)
    page = max(1, min(page, pages))
    page_items = paginate(products, page)
    if not page_items:
        await query.edit_message_text("No products found in this subcategory.")
        return
    # Show first product as a preview, with navigation
    prod = page_items[0]
    media_type = prod["media_type"]
    caption = prod["caption"] or "No description."
    if media_type == "photo":
        await query.edit_message_media(
            InputMediaPhoto(media=prod["file_id"], caption=caption)
        )
    elif media_type == "video":
        await query.edit_message_media(
            InputMediaVideo(media=prod["file_id"], caption=caption)
        )
    else:
        await query.edit_message_text(caption)
    keyboard = build_product_pagination_keyboard(category, subcategory, page, pages)
    await query.edit_message_reply_markup(reply_markup=keyboard)

async def page_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle pagination navigation.
    """
    query = update.callback_query
    await query.answer()
    _, category, subcategory, page_str = query.data.split(":")
    page = int(page_str)
    await show_product_page(query, category, subcategory, page)

def register_handlers(application: Application):
    """
    Register product view handlers with the application.
    """
    application.add_handler(CallbackQueryHandler(category_selected, pattern=r"^cat:"))
    application.add_handler(CallbackQueryHandler(subcategory_selected, pattern=r"^sub:"))
    application.add_handler(CallbackQueryHandler(page_navigation, pattern=r"^page:"))
    application.add_handler(CallbackQueryHandler(start, pattern=r"^start$"))