"""
Menu and catalog handlers.
"""
import asyncio
import logging
import math
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from database import Database
from utils.pagination import create_pagination_keyboard
from utils.helpers import is_admin, get_user_lang_async, is_media_message
from utils.categories import (
    canonicalize_subcategory,
    get_all_categories,
    get_catalog_ui_text,
    get_category_display_name,
    get_subcategories,
    get_subcategory_display_name,
    CATEGORIES,
    EXCLUDED_FROM_ALL_PRODUCTS,
)
from utils.safe_send import safe_edit_message_text, safe_update_message, safe_send_message, handle_handler_error
from utils.access_control import is_user_blocked_cached

logger = logging.getLogger(__name__)
db = Database()

_MENU_CACHE_TTL_SECONDS = 20
_category_summary_cache = {"expires_at": 0.0, "value": None}
_subcategory_summary_cache: dict[str, dict] = {}


def _new_menu_render_token() -> str:
    """Small per-render suffix to avoid stale client-side callback reuse."""
    return f"r{int(time.monotonic() * 1000) % 65536:04x}"


def _menu_callback(callback_data: str, render_token: str) -> str:
    tagged = f"{callback_data}|{render_token}"
    return tagged if len(tagged) <= 64 else callback_data


async def _delete_old_menu_message(bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramError as exc:
        logger.debug(
            "Could not delete superseded menu message chat_id=%s message_id=%s: %s",
            chat_id,
            message_id,
            exc,
        )


async def _update_menu_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, keyboard: InlineKeyboardMarkup):
    """Update a menu message, falling back to a fresh send if Telegram refuses the edit."""
    user_id = getattr(update.effective_user, "id", None)
    chat_id = getattr(update.effective_chat, "id", None)
    callback_data = None
    message_id = None
    message_has_media = False
    operation = "send_command_reply"
    should_send_fresh = False

    if update.callback_query:
        query = update.callback_query
        callback_data = query.data
        message_id = getattr(query.message, "message_id", None)
        message_has_media = is_media_message(query.message)
        should_send_fresh = message_has_media
        if message_has_media:
            operation = "send_after_media_callback"
        else:
            operation = "edit_callback_message"

    started = time.monotonic()
    if should_send_fresh and update.callback_query and update.effective_chat:
        result = await safe_send_message(
            context.bot,
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=keyboard,
            parse_mode=None,
        )
    else:
        result = await safe_update_message(update, context, text, reply_markup=keyboard, parse_mode=None)
    api_ms = (time.monotonic() - started) * 1000
    logger.info(
        "Menu Telegram update user_id=%s chat_id=%s operation=%s callback_data=%r message_id=%s api_ms=%.1f result=%s result_message_id=%s",
        user_id,
        chat_id,
        operation,
        callback_data,
        message_id,
        api_ms,
        "ok" if result is not None else "none",
        getattr(result, "message_id", None),
    )
    if result is not None:
        return result

    if update.callback_query and update.effective_chat:
        logger.warning(
            "Menu update returned no message; sending fresh menu fallback user_id=%s chat_id=%s callback_data=%r message_id=%s",
            user_id,
            chat_id,
            callback_data,
            message_id,
        )
        fallback_started = time.monotonic()
        fallback_result = await safe_send_message(
            context.bot,
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=keyboard,
            parse_mode=None,
        )
        logger.info(
            "Menu Telegram fallback user_id=%s chat_id=%s callback_data=%r api_ms=%.1f result=%s result_message_id=%s",
            user_id,
            chat_id,
            callback_data,
            (time.monotonic() - fallback_started) * 1000,
            "ok" if fallback_result is not None else "none",
            getattr(fallback_result, "message_id", None),
        )
        if fallback_result is not None and message_id and not message_has_media:
            asyncio.create_task(
                _delete_old_menu_message(context.bot, update.callback_query.message.chat_id, message_id)
            )
        return fallback_result

    return result


async def _get_cached_category_summary():
    now = time.monotonic()
    cached = _category_summary_cache.get("value")
    if cached is not None and now < _category_summary_cache.get("expires_at", 0.0):
        return cached

    value = await db.get_category_summary(EXCLUDED_FROM_ALL_PRODUCTS)
    _category_summary_cache["value"] = value
    _category_summary_cache["expires_at"] = now + _MENU_CACHE_TTL_SECONDS
    return value


async def _get_cached_subcategory_summary(category: str):
    now = time.monotonic()
    cached = _subcategory_summary_cache.get(category)
    if cached and now < cached.get("expires_at", 0.0):
        return cached["value"]

    value = await db.get_subcategory_summary(category)
    _subcategory_summary_cache[category] = {
        "value": value,
        "expires_at": now + _MENU_CACHE_TTL_SECONDS,
    }
    return value


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /menu command - show category selection."""
    user_id = update.effective_user.id
    if not is_admin(user_id) and await is_user_blocked_cached(
        db,
        user_id,
        username=getattr(update.effective_user, "username", None),
    ):
        await update.message.reply_text(
            "🚫 You have been blocked from using this bot.\n\n"
            "If you believe this is an error, please contact an administrator."
        )
        return

    await show_category_menu(update, context)


async def show_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show category selection menu."""
    user_id = update.effective_user.id
    started = time.monotonic()

    try:
        # Get user's language preference
        user_lang = await get_user_lang_async(user_id)

        # Get category counts and the storefront total in one connection.
        category_counts, _, total_count = await _get_cached_category_summary()
        category_buttons = []
        render_token = _new_menu_render_token()

        for category in get_all_categories():
            count = category_counts.get(category, 0)
            display_name = get_category_display_name(category, user_lang)

            button_text = f"{display_name} ({count})"

            # Check if category has subcategories
            subcategories = get_subcategories(category)

            if subcategories:
                # Use browse_category to show subcategory menu
                callback_data = _menu_callback(f"browse_category|{category}", render_token)
            else:
                # No subcategories, go directly to products
                callback_data = _menu_callback(f"category|{category}|1", render_token)

            category_buttons.append([
                InlineKeyboardButton(button_text, callback_data=callback_data)
            ])

        # Add "All Products" option
        all_products_text = get_catalog_ui_text("all_products", user_lang)
        category_buttons.insert(0, [
            InlineKeyboardButton(
                f"{all_products_text} ({total_count})",
                callback_data=_menu_callback("menu|1", render_token),
            )
        ])

        keyboard = InlineKeyboardMarkup(category_buttons)

        text = get_catalog_ui_text("product_categories", user_lang)

        await _update_menu_message(update, context, text, keyboard)
        logger.info(
            "Category menu rendered user_id=%s token=%s duration_ms=%.1f buttons=%d",
            user_id,
            render_token,
            (time.monotonic() - started) * 1000,
            len(category_buttons),
        )

    except Exception as e:
        await handle_handler_error(update, user_id, e, "Error showing category menu", logger)


async def show_subcategory_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str):
    """Show subcategory selection menu for a given category."""
    user_id = update.effective_user.id
    started = time.monotonic()

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
        subcategory_counts, all_count = await _get_cached_subcategory_summary(category)
        count_dict = {item["subcategory"]: item["count"] for item in subcategory_counts}

        # Build subcategory buttons
        subcategory_buttons = []
        render_token = _new_menu_render_token()

        for subcat in subcategories:
            count = count_dict.get(subcat, 0)
            # Get translated subcategory name
            translated_subcat = get_subcategory_display_name(subcat, user_lang)
            button_text = f"{translated_subcat} ({count})"
            subcategory_buttons.append([
                InlineKeyboardButton(
                    button_text,
                    callback_data=_menu_callback(f"subcategory|{category}|{subcat}|1", render_token),
                )
            ])

        translated_category = get_category_display_name(category, user_lang)

        all_in_category_text = (
            f"{get_catalog_ui_text('button_all_in_category', user_lang, category=translated_category)} "
            f"({all_count})"
        )

        subcategory_buttons.insert(0, [
            InlineKeyboardButton(
                all_in_category_text,
                callback_data=_menu_callback(f"category|{category}|1", render_token),
            )
        ])

        # Add back button
        back_to_cats_text = get_catalog_ui_text("button_back_to_categories", user_lang)
        subcategory_buttons.append([
            InlineKeyboardButton(back_to_cats_text, callback_data=_menu_callback("categories", render_token))
        ])

        keyboard = InlineKeyboardMarkup(subcategory_buttons)

        select_subcat_text = get_catalog_ui_text("select_subcategory", user_lang)
        text = f"📂 {translated_category}\n\n{select_subcat_text}"

        await _update_menu_message(update, context, text, keyboard)
        logger.info(
            "Subcategory menu rendered user_id=%s category=%s token=%s duration_ms=%.1f buttons=%d",
            user_id,
            category,
            render_token,
            (time.monotonic() - started) * 1000,
            len(subcategory_buttons),
        )

    except Exception as e:
        await handle_handler_error(update, user_id, e, "Error showing subcategory menu", logger)


async def show_catalog_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1, category: str = None, subcategory: str = None):
    """Show a page of the catalog, optionally filtered by category and subcategory."""
    user_id = update.effective_user.id
    started = time.monotonic()
    subcategory = canonicalize_subcategory(category, subcategory)

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
            render_token = _new_menu_render_token()

            # Build back button based on context
            back_buttons = []
            back_to_subcats_text = get_catalog_ui_text("button_back_to_subcategories", user_lang)
            back_to_cats_text = get_catalog_ui_text("button_back_to_categories", user_lang)
            refresh_text = get_catalog_ui_text("button_refresh", user_lang)

            if subcategory and category:
                back_buttons.append([
                    InlineKeyboardButton(
                        back_to_subcats_text,
                        callback_data=_menu_callback(f"browse_category|{category}", render_token),
                    )
                ])
            elif category:
                back_buttons.append([
                    InlineKeyboardButton(
                        back_to_cats_text,
                        callback_data=_menu_callback("categories", render_token),
                    )
                ])
            else:
                back_buttons.append([
                    InlineKeyboardButton(
                        back_to_cats_text,
                        callback_data=_menu_callback("categories", render_token),
                    )
                ])

            # Add refresh button
            if subcategory and category:
                refresh_data = f"subcategory|{category}|{subcategory}|1"
            elif category:
                refresh_data = f"category|{category}|1"
            else:
                refresh_data = "menu|1"
            back_buttons.append([
                InlineKeyboardButton(refresh_text, callback_data=_menu_callback(refresh_data, render_token))
            ])

            keyboard = InlineKeyboardMarkup(back_buttons)
            await _update_menu_message(update, context, text, keyboard)
            logger.info(
                "Empty catalog page rendered user_id=%s category=%s subcategory=%s token=%s duration_ms=%.1f",
                user_id,
                category or "",
                subcategory or "",
                render_token,
                (time.monotonic() - started) * 1000,
            )
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
        render_token = _new_menu_render_token()

        # Add product buttons
        for product in products_page:
            caption = product.get("caption", "No caption") or "No caption"
            button_text = caption[:47] + "..." if len(caption) > 50 else caption
            if not button_text.strip():
                button_text = f"Product #{product['id']}"
            if str(product.get("stock_status") or "active").lower() == "out_of_stock":
                button_text = f"[SOLD OUT] {button_text}"

            callback_data = f"product|{product['id']}"
            keyboard_buttons.append([
                InlineKeyboardButton(button_text, callback_data=_menu_callback(callback_data, render_token))
            ])

        # Add pagination controls
        if total_pages > 1:
            nav_buttons = []

            previous_text = get_catalog_ui_text("button_previous_page", user_lang)
            next_text = get_catalog_ui_text("button_next_page", user_lang)

            if page > 1:
                if subcategory and category:
                    prev_data = _menu_callback(f"subcategory|{category}|{subcategory}|{page - 1}", render_token)
                elif category:
                    prev_data = _menu_callback(f"category|{category}|{page - 1}", render_token)
                else:
                    prev_data = _menu_callback(f"page|catalog|{page - 1}", render_token)
                nav_buttons.append(InlineKeyboardButton(previous_text, callback_data=prev_data))

            page_text = get_catalog_ui_text("page_indicator", user_lang, page=page, total_pages=total_pages)
            nav_buttons.append(InlineKeyboardButton(page_text, callback_data=_menu_callback("noop", render_token)))

            if page < total_pages:
                if subcategory and category:
                    next_data = _menu_callback(f"subcategory|{category}|{subcategory}|{page + 1}", render_token)
                elif category:
                    next_data = _menu_callback(f"category|{category}|{page + 1}", render_token)
                else:
                    next_data = _menu_callback(f"page|catalog|{page + 1}", render_token)
                nav_buttons.append(InlineKeyboardButton(next_text, callback_data=next_data))

            keyboard_buttons.append(nav_buttons)

        # Add appropriate back button
        back_to_subcats_text = get_catalog_ui_text("button_back_to_subcategories", user_lang)
        back_to_cats_text = get_catalog_ui_text("button_back_to_categories", user_lang)

        if subcategory and category:
            keyboard_buttons.append([
                InlineKeyboardButton(
                    back_to_subcats_text,
                    callback_data=_menu_callback(f"browse_category|{category}", render_token),
                )
            ])
        else:
            keyboard_buttons.append([
                InlineKeyboardButton(back_to_cats_text, callback_data=_menu_callback("categories", render_token))
            ])

        keyboard = InlineKeyboardMarkup(keyboard_buttons)

        showing_text = get_catalog_ui_text(
            "showing_products",
            user_lang,
            current=len(products_page),
            total=total_products,
        )

        text = f"{title}\n\n{showing_text}"

        await _update_menu_message(update, context, text, keyboard)
        logger.info(
            "Catalog page rendered user_id=%s category=%s subcategory=%s page=%s token=%s duration_ms=%.1f buttons=%d",
            user_id,
            category or "",
            subcategory or "",
            page,
            render_token,
            (time.monotonic() - started) * 1000,
            len(keyboard_buttons),
        )

    except Exception as e:
        await handle_handler_error(update, user_id, e, "Error showing catalog page", logger)


async def handle_catalog_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, category: str = None, subcategory: str = None):
    """Handle catalog pagination callback."""
    await show_catalog_page(update, context, page, category, subcategory)

