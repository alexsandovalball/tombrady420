"""
Menu and catalog handlers.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import Database
from utils.pagination import create_pagination_keyboard, paginate_items
from utils.helpers import is_admin

logger = logging.getLogger(__name__)
db = Database()


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /menu command."""
    await show_catalog_page(update, context, page=1)


async def show_catalog_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """Show a page of the catalog."""
    try:
        all_products = await db.get_all_products_for_search()
        
        if not all_products:
            text = "📭 The catalog is empty. No products available yet."
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data="menu|1")]
            ])
            
            if update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=keyboard)
            else:
                await update.message.reply_text(text, reply_markup=keyboard)
            return
        
        # Paginate products
        products_page, total_pages = paginate_items(all_products, page, per_page=5)
        
        # Save pagination state
        user_id = update.effective_user.id
        await db.save_pagination_state(user_id, "catalog", None, page)
        
        # Create keyboard
        keyboard = create_pagination_keyboard(
            products_page,
            page,
            total_pages,
            "catalog"
        )
        
        text = f"📋 **Catalog**\n\nShowing {len(products_page)} of {len(all_products)} products"
        
        if update.callback_query:
            # Check if the message is a media message (can't edit media messages)
            message = update.callback_query.message
            if message.photo or message.video or message.document or message.animation or message.audio:
                # Delete the media message and send a new text message
                try:
                    await message.delete()
                except:
                    pass  # If deletion fails, continue anyway
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
        logger.error(f"Error showing catalog page: {e}")
        error_text = "❌ An error occurred while loading the catalog. Please try again."
        if update.callback_query:
            await update.callback_query.answer(error_text, show_alert=True)
        else:
            await update.message.reply_text(error_text)


async def handle_catalog_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    """Handle catalog pagination callback."""
    await show_catalog_page(update, context, page)

