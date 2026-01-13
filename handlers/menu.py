"""
Menu and catalog handlers.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import Database
from utils.pagination import create_pagination_keyboard, paginate_items
from utils.helpers import is_admin
from utils.categories import get_all_categories, get_category_display_name, CATEGORIES

logger = logging.getLogger(__name__)
db = Database()


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /menu command - show category selection."""
    await show_category_menu(update, context)


async def show_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show category selection menu."""
    try:
        # Get category counts
        category_buttons = []
        
        for category in get_all_categories():
            count = await db.count_products_by_category(category)
            display_name = get_category_display_name(category)
            button_text = f"{display_name} ({count})"
            category_buttons.append([
                InlineKeyboardButton(button_text, callback_data=f"category|{category}|1")
            ])
        
        # Add "All Products" option
        total_count = await db.count_products()
        category_buttons.insert(0, [
            InlineKeyboardButton(f"📦 All Products ({total_count})", callback_data="menu|1")
        ])
        
        keyboard = InlineKeyboardMarkup(category_buttons)
        
        text = (
            "📂 **Product Categories**\n\n"
            "Select a category to browse products:"
        )
        
        if update.callback_query:
            message = update.callback_query.message
            if message.photo or message.video or message.document or message.animation or message.audio:
                try:
                    await message.delete()
                except:
                    pass
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            else:
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
        logger.error(f"Error showing category menu: {e}")
        error_text = "❌ An error occurred. Please try again."
        if update.callback_query:
            await update.callback_query.answer(error_text, show_alert=True)
        else:
            await update.message.reply_text(error_text)


async def show_catalog_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1, category: str = None):
    """Show a page of the catalog, optionally filtered by category."""
    try:
        # Get products (filtered by category if specified)
        if category:
            all_products = await db.get_products_by_category(category)
            title = f"📋 {get_category_display_name(category)}"
        else:
            all_products = await db.get_all_products_for_search()
            title = "📋 All Products"
        
        if not all_products:
            text = f"📭 No products in this {('category' if category else 'catalog')}."
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Categories", callback_data="categories")],
                [InlineKeyboardButton("🔄 Refresh", callback_data=f"{'category|' + category + '|' if category else 'menu|'}1")]
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
        state_key = f"category_{category}" if category else "catalog"
        await db.save_pagination_state(user_id, state_key, category or "", page)
        
        # Create keyboard with product buttons
        keyboard_buttons = []
        
        # Add product buttons
        for product in products_page:
            caption = product.get("caption", "No caption") or "No caption"
            button_text = caption[:47] + "..." if len(caption) > 50 else caption
            if not button_text.strip():
                button_text = f"Product #{product['id']}"
            
            callback_data = f"product|{product['id']}"
            keyboard_buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        # Add pagination controls
        if total_pages > 1:
            nav_buttons = []
            
            if page > 1:
                prev_data = f"{'category|' + category + '|' if category else 'page|catalog|'}{page - 1}"
                nav_buttons.append(InlineKeyboardButton("← Previous", callback_data=prev_data))
            
            nav_buttons.append(InlineKeyboardButton(f"Page {page}/{total_pages}", callback_data="noop"))
            
            if page < total_pages:
                next_data = f"{'category|' + category + '|' if category else 'page|catalog|'}{page + 1}"
                nav_buttons.append(InlineKeyboardButton("Next →", callback_data=next_data))
            
            keyboard_buttons.append(nav_buttons)
        
        # Add back to categories button
        keyboard_buttons.append([InlineKeyboardButton("🔙 Back to Categories", callback_data="categories")])
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        text = f"{title}\n\nShowing {len(products_page)} of {len(all_products)} products"
        
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


async def handle_catalog_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, category: str = None):
    """Handle catalog pagination callback."""
    await show_catalog_page(update, context, page, category)

