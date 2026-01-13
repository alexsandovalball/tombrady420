"""
Product view handler.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import Database
from utils.helpers import send_media_message, is_admin
from utils.pagination import paginate_items

logger = logging.getLogger(__name__)
db = Database()


async def show_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    """Show a single product with media and details."""
    try:
        product = await db.get_product(product_id)
        
        if not product:
            await update.callback_query.answer(
                "❌ Product not found.",
                show_alert=True
            )
            return
        
        # Get user's pagination state to determine back button behavior
        user_id = update.effective_user.id
        
        # Check catalog pagination first (if user is browsing catalog)
        catalog_page = await db.get_pagination_state(user_id, "catalog", None)
        if catalog_page:
            back_callback = f"menu|{catalog_page}"
        else:
            # Check last search (if user came from search)
            last_search = await db.get_last_search(user_id)
            if last_search and last_search.get("query"):
                # Use the stored query and page for search results
                query = last_search['query']
                page = last_search['page']
                # Replace pipe characters in query to avoid breaking callback data
                # Also truncate if too long (Telegram callback_data limit is 64 bytes)
                safe_query = query.replace("|", "_PIPE_")
                # Estimate: "page|search|" = 12, "|" + page (max 3 digits) = 4, so ~16 bytes overhead
                max_query_len = 64 - 16
                if len(safe_query) > max_query_len:
                    safe_query = safe_query[:max_query_len]
                back_callback = f"page|search|{safe_query}|{page}"
            else:
                back_callback = "menu|1"  # Default to catalog page 1
        
        # Create keyboard
        keyboard_buttons = [
            [InlineKeyboardButton("🔙 Back to results", callback_data=back_callback)]
        ]
        
        # Add delete button for admins
        if is_admin(user_id):
            keyboard_buttons.append([
                InlineKeyboardButton(
                    "🗑 Delete this product",
                    callback_data=f"delete|{product_id}"
                )
            ])
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        # Get caption
        caption = product.get("caption") or "No description"
        
        # Send media with caption
        await send_media_message(
            context,
            update.effective_chat.id,
            product["file_id"],
            product["file_type"],
            caption=caption,
            reply_markup=keyboard
        )
        
        await update.callback_query.answer()
        
    except Exception as e:
        logger.error(f"Error showing product: {e}")
        await update.callback_query.answer(
            "❌ An error occurred while loading the product.",
            show_alert=True
        )


async def handle_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle product button callback."""
    callback_data = update.callback_query.data
    parts = callback_data.split("|")
    
    if len(parts) == 2 and parts[0] == "product":
        product_id = int(parts[1])
        await show_product(update, context, product_id)
    else:
        await update.callback_query.answer("Invalid callback data", show_alert=True)

