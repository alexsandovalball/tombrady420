"""
Search handlers.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, filters
from database import Database
from utils.pagination import create_pagination_keyboard, paginate_items
from utils.fuzzy_search import fuzzy_search_products
from translations.translator import get_translated_string

logger = logging.getLogger(__name__)
db = Database()


async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle natural language search."""
    query = update.message.text.strip()
    
    # Get user's language preference
    user_id = update.effective_user.id
    user_lang = await db.get_user_language(user_id)
    
    if not query or len(query) < 2:
        search_min_chars_text = get_translated_string("search_min_chars", user_lang)
        await update.message.reply_text(search_min_chars_text)
        return
    
    try:
        # Get all products for fuzzy search
        all_products = await db.get_all_products_for_search()
        
        if not all_products:
            catalog_empty_text = get_translated_string("catalog_empty", user_lang)
            await update.message.reply_text(catalog_empty_text)
            return
        
        # Perform fuzzy search
        matched_products = fuzzy_search_products(all_products, query, score_cutoff=75)
        
        if not matched_products:
            no_products_found_text = get_translated_string("no_products_found", user_lang, query=query)
            await update.message.reply_text(no_products_found_text)
            return
        
        # Show first page of results
        await show_search_results(update, context, query, matched_products, page=1)
        
    except Exception as e:
        logger.error(f"Error handling search: {e}")
        error_text = get_translated_string("error_occurred", user_lang)
        await update.message.reply_text(error_text)


async def show_search_results(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
    matched_products: list,
    page: int = 1
):
    """Show search results with pagination."""
    try:
        # Get user's language preference
        user_id = update.effective_user.id
        user_lang = await db.get_user_language(user_id)
        
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
        text = get_translated_string("search_results", user_lang, count=len(matched_products), query=query)
        text += f"\nShowing {len(products_page)} on this page"
        
        if update.callback_query:
            # Check if the message is a media message (can't edit media messages)
            message = update.callback_query.message
            if message.photo or message.video or message.document or message.animation or message.audio:
                # Don't delete media message - send new message to preserve product view
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            else:
                # Regular text message, can be edited
                await update.callback_query.edit_message_text(
                    text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
        else:
            await update.message.reply_text(
                text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            
    except Exception as e:
        logger.error(f"Error showing search results: {e}")
        # Get user's language preference for error message
        user_id = update.effective_user.id
        user_lang = await db.get_user_language(user_id)
        error_text = get_translated_string("error_occurred", user_lang)
        if update.callback_query:
            await update.callback_query.answer(error_text, show_alert=True)
        else:
            await update.message.reply_text(error_text)


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

