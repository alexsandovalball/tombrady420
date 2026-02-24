"""
Search handlers.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, filters
from database import Database
from utils.pagination import create_pagination_keyboard, paginate_items
from utils.fuzzy_search import fuzzy_search_products
from translations.translator import get_translated_string_async
from utils.safe_send import safe_update_message, handle_handler_error
from utils.helpers import get_user_lang_async

logger = logging.getLogger(__name__)
db = Database()


async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle natural language search."""
    query = update.message.text.strip()

    # Get user's language preference
    user_id = update.effective_user.id
    user_lang = await get_user_lang_async(user_id)

    if not query or len(query) < 2:
        search_min_chars_text = await get_translated_string_async("search_min_chars", user_lang)
        await update.message.reply_text(search_min_chars_text)
        return

    try:
        # Get all products for fuzzy search
        all_products = await db.get_all_products_for_search()

        if not all_products:
            catalog_empty_text = await get_translated_string_async("catalog_empty", user_lang)
            await update.message.reply_text(catalog_empty_text)
            return

        # Perform fuzzy search
        matched_products = fuzzy_search_products(all_products, query, score_cutoff=75)

        if not matched_products:
            no_products_found_text = await get_translated_string_async("no_products_found", user_lang, query=query)
            await update.message.reply_text(no_products_found_text)
            return

        # Show first page of results
        await show_search_results(update, context, query, matched_products, page=1)

    except Exception as e:
        await handle_handler_error(update, user_id, e, "Error handling search", logger)


async def show_search_results(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
    matched_products: list,
    page: int = 1
):
    """Show search results with pagination."""
    user_id = update.effective_user.id

    try:
        # Get user's language preference
        user_lang = await get_user_lang_async(user_id)

        # Paginate results
        products_page, total_pages = paginate_items(matched_products, page, per_page=5)

        # Save pagination state
        await db.save_pagination_state(user_id, "search", query, page)
        await db.save_last_search(user_id, query, page)

        # Create keyboard (use safe query for callback data)
        safe_query = query.replace("|", "_PIPE_")
        keyboard = create_pagination_keyboard(
            products_page,
            page,
            total_pages,
            "search",
            query=safe_query
        )

        # Use translated search results text
        text = await get_translated_string_async("search_results", user_lang, count=len(matched_products), query=query)
        text += f"\nShowing {len(products_page)} on this page"

        # Use safe_update_message to handle media/text messages
        await safe_update_message(update, context, text, reply_markup=keyboard)

    except Exception as e:
        await handle_handler_error(update, user_id, e, "Error showing search results", logger)


async def handle_search_pagination(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
    page: int
):
    """Handle search pagination callback."""
    try:
        # Restore pipe characters in query
        original_query = query.replace("_PIPE_", "|")
        
        # Get all products and re-run search
        all_products = await db.get_all_products_for_search()
        matched_products = fuzzy_search_products(all_products, original_query, score_cutoff=75)
        
        if not matched_products:
            await update.callback_query.answer(
                "No products found for this query.",
                show_alert=True
            )
            return
        
        await show_search_results(update, context, original_query, matched_products, page)
        
    except Exception as e:
        logger.error(f"Error handling search pagination: {e}")
        await update.callback_query.answer(
            "An error occurred. Please try again.",
            show_alert=True
        )

