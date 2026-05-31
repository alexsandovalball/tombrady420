"""
Menu and catalog handlers.
"""
import logging
import math
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from database import Database
from utils.pagination import create_pagination_keyboard
from utils.helpers import is_admin, get_user_lang_async
from utils.categories import (
    get_all_categories,
    get_catalog_ui_text,
    get_category_display_name,
    get_subcategories,
    get_subcategory_display_name,
    CATEGORIES,
    EXCLUDED_FROM_ALL_PRODUCTS,
)
from utils.safe_send import safe_edit_message_text, safe_update_message, handle_handler_error

logger = logging.getLogger(__name__)
db = Database()


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /menu command - show category selection."""
    user_id = update.effective_user.id
    if not is_admin(user_id) and await db.is_user_blocked(user_id):
        await update.message.reply_text(
            "🚫 You have been blocked from using this bot.\n\n"
            "If you believe this is an error, please contact an administrator."
        )
        return

    await show_category_menu(update, context)


async def show_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show category selection menu."""
    user_id = update.effective_user.id

    try:
        # Get user's language preference
        user_lang = await get_user_lang_async(user_id)

        # Get category counts and the storefront total in one connection.
        category_counts, _, total_count = await db.get_category_summary(EXCLUDED_FROM_ALL_PRODUCTS)
        category_buttons = []

        for category in get_all_categories():
            count = category_counts.get(category, 0)
            display_name = get_category_display_name(category, user_lang)

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
        all_products_text = get_catalog_ui_text("all_products", user_lang)
        category_buttons.insert(0, [
            InlineKeyboardButton(f"{all_products_text} ({total_count})", callback_data="menu|1")
        ])

        keyboard = InlineKeyboardMarkup(category_buttons)

        text = get_catalog_ui_text("product_categories", user_lang)

        # Use safe_update_message to handle media/text messages
        await safe_update_message(update, context, text, reply_markup=keyboard)

    except Exception as e:
        await handle_handler_error(update, user_id, e, "Error showing category menu", logger)


async def show_subcategory_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str):
    """Show subcategory selection menu for a given category."""
    user_id = update.effective_user.id

    try:
        # Get user's language preference
        user_lang = await get_user_lang_async(user_id)

        # Get subcategories for this category
        subcategories = get_subcategories(category)

        if not subcategories:
            # No subcategories, go directly to catalog
            await show_catalog_page(update, context, page=1, category=category)
            return

        # Get subcategory counts and the all-in-category total in one connection.
        subcategory_counts, all_count = await db.get_subcategory_summary(category)
        count_dict = {item["subcategory"]: item["count"] for item in subcategory_counts}

        # Build subcategory buttons
        subcategory_buttons = []

        for subcat in subcategories:
            count = count_dict.get(subcat, 0)
            # Get translated subcategory name
            translated_subcat = get_subcategory_display_name(subcat, user_lang)
            button_text = f"{translated_subcat} ({count})"
            subcategory_buttons.append([
                InlineKeyboardButton(button_text, callback_data=f"subcategory|{category}|{subcat}|1")
            ])

        translated_category = get_category_display_name(category, user_lang)

        all_in_category_text = (
            f"{get_catalog_ui_text('button_all_in_category', user_lang, category=translated_category)} "
            f"({all_count})"
        )

        subcategory_buttons.insert(0, [
            InlineKeyboardButton(all_in_category_text, callback_data=f"category|{category}|1")
        ])

        # Add back button
        back_to_cats_text = get_catalog_ui_text("button_back_to_categories", user_lang)
        subcategory_buttons.append([
            InlineKeyboardButton(back_to_cats_text, callback_data="categories")
        ])

        keyboard = InlineKeyboardMarkup(subcategory_buttons)

        select_subcat_text = get_catalog_ui_text("select_subcategory", user_lang)
        text = (
            f"📂 **{translated_category}**\n\n"
            f"{select_subcat_text}"
        )

        # Use safe_update_message to handle media/text messages
        await safe_update_message(update, context, text, reply_markup=keyboard)

    except Exception as e:
        await handle_handler_error(update, user_id, e, "Error showing subcategory menu", logger)


async def show_catalog_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1, category: str = None, subcategory: str = None):
    """Show a page of the catalog, optionally filtered by category and subcategory."""
    user_id = update.effective_user.id

    try:
        # Get user's language preference
        user_lang = await get_user_lang_async(user_id)

        per_page = 5
        page = max(1, page)
        offset = (page - 1) * per_page

        # Get only the requested page from SQLite. The old path fetched every
        # matching product and then sliced five items in Python, which made
        # category navigation feel sluggish as the catalog grew.
        if category and subcategory:
            products_page, total_products = await db.get_products_page(
                category=category,
                subcategory=subcategory,
                limit=per_page,
                offset=offset,
            )
            translated_category = get_category_display_name(category, user_lang)
            translated_subcategory = get_subcategory_display_name(subcategory, user_lang)
            title = f"📋 {translated_category} • {translated_subcategory}"
        elif category:
            products_page, total_products = await db.get_products_page(
                category=category,
                limit=per_page,
                offset=offset,
            )
            translated_category = get_category_display_name(category, user_lang)
            title = f"📋 {translated_category}"
        else:
            # When showing all products, exclude specified categories
            products_page, total_products = await db.get_products_page(
                excluded_categories=EXCLUDED_FROM_ALL_PRODUCTS,
                limit=per_page,
                offset=offset,
            )
            all_products_text = get_catalog_ui_text("all_products", user_lang)
            title = f"📋 {all_products_text}"

        if not products_page:
            context_str = 'subcategory' if subcategory else 'category' if category else 'catalog'
            text = get_catalog_ui_text("no_products_in_category", user_lang, context=context_str)

            # Build back button based on context
            back_buttons = []
            back_to_subcats_text = get_catalog_ui_text("button_back_to_subcategories", user_lang)
            back_to_cats_text = get_catalog_ui_text("button_back_to_categories", user_lang)
            refresh_text = get_catalog_ui_text("button_refresh", user_lang)

            if subcategory and category:
                back_buttons.append([InlineKeyboardButton(back_to_subcats_text, callback_data=f"browse_category|{category}")])
            elif category:
                back_buttons.append([InlineKeyboardButton(back_to_cats_text, callback_data="categories")])
            else:
                back_buttons.append([InlineKeyboardButton(back_to_cats_text, callback_data="categories")])

            # Add refresh button
            if subcategory and category:
                refresh_data = f"subcategory|{category}|{subcategory}|1"
            elif category:
                refresh_data = f"category|{category}|1"
            else:
                refresh_data = "menu|1"
            back_buttons.append([InlineKeyboardButton(refresh_text, callback_data=refresh_data)])

            keyboard = InlineKeyboardMarkup(back_buttons)

            if update.callback_query:
                query = update.callback_query
                await safe_edit_message_text(
                    context.bot,
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    text=text,
                    reply_markup=keyboard
                )
            else:
                await update.message.reply_text(text, reply_markup=keyboard)
            return

        total_pages = max(1, math.ceil(total_products / per_page))

        # Save pagination state
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

            previous_text = get_catalog_ui_text("button_previous_page", user_lang)
            next_text = get_catalog_ui_text("button_next_page", user_lang)

            if page > 1:
                if subcategory and category:
                    prev_data = f"subcategory|{category}|{subcategory}|{page - 1}"
                elif category:
                    prev_data = f"category|{category}|{page - 1}"
                else:
                    prev_data = f"page|catalog|{page - 1}"
                nav_buttons.append(InlineKeyboardButton(previous_text, callback_data=prev_data))

            page_text = get_catalog_ui_text("page_indicator", user_lang, page=page, total_pages=total_pages)
            nav_buttons.append(InlineKeyboardButton(page_text, callback_data="noop"))

            if page < total_pages:
                if subcategory and category:
                    next_data = f"subcategory|{category}|{subcategory}|{page + 1}"
                elif category:
                    next_data = f"category|{category}|{page + 1}"
                else:
                    next_data = f"page|catalog|{page + 1}"
                nav_buttons.append(InlineKeyboardButton(next_text, callback_data=next_data))

            keyboard_buttons.append(nav_buttons)

        # Add appropriate back button
        back_to_subcats_text = get_catalog_ui_text("button_back_to_subcategories", user_lang)
        back_to_cats_text = get_catalog_ui_text("button_back_to_categories", user_lang)

        if subcategory and category:
            keyboard_buttons.append([InlineKeyboardButton(back_to_subcats_text, callback_data=f"browse_category|{category}")])
        else:
            keyboard_buttons.append([InlineKeyboardButton(back_to_cats_text, callback_data="categories")])

        keyboard = InlineKeyboardMarkup(keyboard_buttons)

        showing_text = get_catalog_ui_text(
            "showing_products",
            user_lang,
            current=len(products_page),
            total=total_products,
        )

        text = f"{title}\n\n{showing_text}"

        # Use safe_update_message to handle media/text messages
        await safe_update_message(update, context, text, reply_markup=keyboard)

    except Exception as e:
        await handle_handler_error(update, user_id, e, "Error showing catalog page", logger)


async def handle_catalog_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, category: str = None, subcategory: str = None):
    """Handle catalog pagination callback."""
    await show_catalog_page(update, context, page, category, subcategory)

