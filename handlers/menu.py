"""
Menu and catalog handlers.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from database import Database
from utils.pagination import create_pagination_keyboard, paginate_items
from utils.helpers import is_admin
from utils.categories import get_all_categories, get_category_display_name, get_subcategories, CATEGORIES, EXCLUDED_FROM_ALL_PRODUCTS

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
            
            # Check if category has subcategories
            subcategories = get_subcategories(category)
            
            if subcategories:
                # Use browse_category to show subcategory menu
                callback_data = f"browse_category|{category}"
            else:
                # No subcategories, go directly to products
                callback_data = f"category|{category}|1"
            
            category_buttons.append([
                InlineKeyboardButton(button_text, callback_data=callback_data)
            ])
        
        # Add "All Products" option
        total_count = await db.count_products_excluding_categories(EXCLUDED_FROM_ALL_PRODUCTS)
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
                # Don't delete media message - send new message to preserve product view
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


async def show_subcategory_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str):
    """Show subcategory selection menu for a given category."""
    try:
        # Get subcategories for this category
        subcategories = get_subcategories(category)
        
        if not subcategories:
            # No subcategories, go directly to catalog
            await show_catalog_page(update, context, page=1, category=category)
            return
        
        # Get subcategory counts from database
        subcategory_counts = await db.get_subcategories_with_counts(category)
        count_dict = {item["subcategory"]: item["count"] for item in subcategory_counts}
        
        # Build subcategory buttons
        subcategory_buttons = []
        
        for subcat in subcategories:
            count = count_dict.get(subcat, 0)
            button_text = f"{subcat.title()} ({count})"
            subcategory_buttons.append([
                InlineKeyboardButton(button_text, callback_data=f"subcategory|{category}|{subcat}|1")
            ])
        
        # Add "All in Category" option
        all_count = await db.count_products_by_category(category)
        subcategory_buttons.insert(0, [
            InlineKeyboardButton(f"📦 All {get_category_display_name(category)} ({all_count})", 
                                callback_data=f"category|{category}|1")
        ])
        
        # Add back button
        subcategory_buttons.append([
            InlineKeyboardButton("🔙 Back to Categories", callback_data="categories")
        ])
        
        keyboard = InlineKeyboardMarkup(subcategory_buttons)
        
        text = (
            f"📂 **{get_category_display_name(category)}**\n\n"
            f"Select a subcategory to browse products:"
        )
        
        if update.callback_query:
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
        logger.error(f"Error showing subcategory menu: {e}")
        error_text = "❌ An error occurred. Please try again."
        if update.callback_query:
            await update.callback_query.answer(error_text, show_alert=True)
        else:
            await update.message.reply_text(error_text)


async def show_catalog_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1, category: str = None, subcategory: str = None):
    """Show a page of the catalog, optionally filtered by category and subcategory."""
    try:
        # Get products (filtered by category and/or subcategory if specified)
        if category and subcategory:
            all_products = await db.get_products_by_category_and_subcategory(category, subcategory)
            title = f"📋 {get_category_display_name(category)} • {subcategory.title()}"
        elif category:
            all_products = await db.get_products_by_category(category)
            title = f"📋 {get_category_display_name(category)}"
        else:
            # When showing all products, exclude specified categories
            all_products = await db.get_all_products_excluding_categories(EXCLUDED_FROM_ALL_PRODUCTS)
            title = "📋 All Products"
        
        if not all_products:
            text = f"📭 No products in this {('subcategory' if subcategory else 'category' if category else 'catalog')}."
            
            # Build back button based on context
            back_buttons = []
            if subcategory and category:
                back_buttons.append([InlineKeyboardButton("🔙 Back to Subcategories", callback_data=f"browse_category|{category}")])
            elif category:
                back_buttons.append([InlineKeyboardButton("🔙 Back to Categories", callback_data="categories")])
            else:
                back_buttons.append([InlineKeyboardButton("🔙 Back to Categories", callback_data="categories")])
            
            # Add refresh button
            if subcategory and category:
                refresh_data = f"subcategory|{category}|{subcategory}|1"
            elif category:
                refresh_data = f"category|{category}|1"
            else:
                refresh_data = "menu|1"
            back_buttons.append([InlineKeyboardButton("🔄 Refresh", callback_data=refresh_data)])
            
            keyboard = InlineKeyboardMarkup(back_buttons)
            
            if update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=keyboard)
            else:
                await update.message.reply_text(text, reply_markup=keyboard)
            return
        
        # Paginate products
        products_page, total_pages = paginate_items(all_products, page, per_page=5)
        
        # Save pagination state
        user_id = update.effective_user.id
        if subcategory and category:
            state_key = f"subcategory_{category}_{subcategory}"
            # Store the state_key in query field for proper back navigation
            await db.save_pagination_state(user_id, state_key, state_key, page)
        elif category:
            state_key = f"category_{category}"
            # Store category in query field for back navigation
            await db.save_pagination_state(user_id, state_key, category, page)
        else:
            state_key = "catalog"
            await db.save_pagination_state(user_id, state_key, "", page)
        
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
                if subcategory and category:
                    prev_data = f"subcategory|{category}|{subcategory}|{page - 1}"
                elif category:
                    prev_data = f"category|{category}|{page - 1}"
                else:
                    prev_data = f"page|catalog|{page - 1}"
                nav_buttons.append(InlineKeyboardButton("← Previous", callback_data=prev_data))
            
            nav_buttons.append(InlineKeyboardButton(f"Page {page}/{total_pages}", callback_data="noop"))
            
            if page < total_pages:
                if subcategory and category:
                    next_data = f"subcategory|{category}|{subcategory}|{page + 1}"
                elif category:
                    next_data = f"category|{category}|{page + 1}"
                else:
                    next_data = f"page|catalog|{page + 1}"
                nav_buttons.append(InlineKeyboardButton("Next →", callback_data=next_data))
            
            keyboard_buttons.append(nav_buttons)
        
        # Add appropriate back button
        if subcategory and category:
            keyboard_buttons.append([InlineKeyboardButton("🔙 Back to Subcategories", callback_data=f"browse_category|{category}")])
        else:
            keyboard_buttons.append([InlineKeyboardButton("🔙 Back to Categories", callback_data="categories")])
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        text = f"{title}\n\nShowing {len(products_page)} of {len(all_products)} products"
        
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
        logger.error(f"Error showing catalog page: {e}")
        error_text = "❌ An error occurred while loading the catalog. Please try again."
        if update.callback_query:
            await update.callback_query.answer(error_text, show_alert=True)
        else:
            await update.message.reply_text(error_text)


async def handle_catalog_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, category: str = None, subcategory: str = None):
    """Handle catalog pagination callback."""
    await show_catalog_page(update, context, page, category, subcategory)

