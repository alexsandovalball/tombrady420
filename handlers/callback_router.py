"""
Callback query routing module.

Implements the command-pattern dispatch for all inline keyboard button callbacks,
replacing the monolithic callback_query_handler with focused handler functions
organized by domain.

Handler signature:
    async def _handle_xxx(update, context, query, user_id, parts) -> None
"""
import asyncio
import logging
import aiosqlite

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from database import Database
from handlers.admin import (
    delete_product,
    show_users_page,
    build_users_bot_selection_menu,
    show_bot_users_page,
)
from handlers.menu import show_catalog_page, show_subcategory_menu, handle_catalog_pagination
from handlers.product_view import handle_product_callback
from handlers.language import handle_language_callback
from translations.language_config import is_valid_language, LANGUAGE_DISPLAY
from translations.translator import get_translated_string_async
from utils.categories import (
    get_all_categories,
    get_category_display_name,
    get_subcategories,
    get_subcategory_display_name,
)
try:
    from utils.cache_invalidation import invalidate_webapp_caches
except Exception as exc:
    logger = logging.getLogger(__name__)
    logger.warning("Cache invalidation unavailable; using no-op fallback: %s", exc)

    def invalidate_webapp_caches(reason: str = "") -> int:
        return 0
from utils.decorators import admin_required
from utils.helpers import is_admin, get_user_display_name, escape_markdown_v1
from utils.notification_manager import get_notification_service
from utils.safe_send import safe_send_message, safe_edit_message_text
import emojis

logger = logging.getLogger(__name__)
db = Database()

# Maximum allowed length for callback_data (Telegram's hard limit is 64 bytes)
_MAX_CALLBACK_DATA_LEN = 64


# ---------------------------------------------------------------------------
# Language callbacks
# ---------------------------------------------------------------------------

async def _handle_setlang(update, context, query, user_id, parts):
    if len(parts) >= 2:
        await handle_language_callback(update, context, parts[1])


async def _handle_open_language_settings(update, context, query, user_id, parts):
    current_lang = await db.get_user_language(user_id)
    keyboard_buttons = []
    for lang_code, display_name in LANGUAGE_DISPLAY.items():
        if lang_code == current_lang:
            display_name = f"✓ {display_name}"
        keyboard_buttons.append([
            InlineKeyboardButton(display_name, callback_data=f"setlang|{lang_code}")
        ])
    keyboard = InlineKeyboardMarkup(keyboard_buttons)
    message_text = await get_translated_string_async("language_settings", current_lang)
    current_lang_name = LANGUAGE_DISPLAY.get(current_lang, LANGUAGE_DISPLAY["en"])
    current_lang_text = await get_translated_string_async(
        "current_language", current_lang, language=current_lang_name
    )
    message_text += f"\n\n{current_lang_text}"
    message = query.message
    if message.photo or message.video or message.document or message.animation or message.audio:
        await safe_send_message(
            context.bot,
            chat_id=update.effective_chat.id,
            text=message_text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    else:
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=message_text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    logger.info(f"User {user_id} opened language settings from start page (current: {current_lang})")


async def _handle_setlang_start(update, context, query, user_id, parts):
    if len(parts) >= 2:
        lang_code = parts[1]
        if is_valid_language(lang_code):
            await db.set_user_language(user_id, lang_code)
            await query.answer()
            await query.delete_message()
            is_subscribed = await db.is_user_subscribed(user_id)
            display_name = get_user_display_name(update.effective_user, escaped=True)
            order_contact = await db.get_order_contact()
            escaped_contact = escape_markdown_v1(order_contact)
            welcome_text = await get_translated_string_async(
                "welcome_with_contact", lang_code, name=display_name, contact=escaped_contact
            )
            view_catalog_text = await get_translated_string_async("view_catalog", lang_code)
            change_language_text = await get_translated_string_async("change_language", lang_code)
            keyboard_buttons = [
                [InlineKeyboardButton(view_catalog_text, callback_data="categories")],
                [InlineKeyboardButton(change_language_text, callback_data="open_language_settings")],
            ]
            if not is_subscribed:
                resubscribe_text = await get_translated_string_async(
                    "resubscribe_notifications", lang_code
                )
                keyboard_buttons.append(
                    [InlineKeyboardButton(resubscribe_text, callback_data="toggle_notifications")]
                )
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
            await safe_send_message(
                context.bot,
                chat_id=update.effective_chat.id,
                text=welcome_text,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
            logger.info(f"New user {user_id} selected language: {lang_code}")
        else:
            await query.answer("❌ Invalid language", show_alert=True)


# ---------------------------------------------------------------------------
# No-op
# ---------------------------------------------------------------------------

async def _handle_noop(update, context, query, user_id, parts):
    pass  # Intentional no-op (page indicator button)


# ---------------------------------------------------------------------------
# Broadcast callbacks
# ---------------------------------------------------------------------------

async def _handle_broadcast_cancel(update, context, query, user_id, parts):
    await safe_edit_message_text(
        context.bot,
        chat_id=query.message.chat_id,
        message_id=query.message.message_id,
        text="❌ Broadcast cancelled.",
    )
    context.user_data.clear()


async def _handle_broadcast_confirm_single(update, context, query, user_id, parts):
    if len(parts) >= 2:
        target_user_id = int(parts[1])
        message_text = context.user_data.get("broadcast_message")
        if not message_text:
            await query.answer("❌ Message not found. Please start over.", show_alert=True)
            context.user_data.clear()
            return
        svc = get_notification_service()
        success = await svc.send_custom_message_to_user(context, target_user_id, message_text)
        if success:
            await safe_edit_message_text(
                context.bot,
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text=(
                    f"✅ **Message Sent Successfully!**\n\n"
                    f"🆔 User ID: `{target_user_id}`\n"
                    f"📝 Message: {message_text[:100]}{'...' if len(message_text) > 100 else ''}"
                ),
                parse_mode="Markdown",
            )
            logger.info(f"Admin {user_id} sent message to user {target_user_id}")
        else:
            await safe_edit_message_text(
                context.bot,
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text="❌ Failed to send message. Check logs for details.",
            )
        context.user_data.clear()


async def _handle_broadcast_confirm_all(update, context, query, user_id, parts):
    message_text = context.user_data.get("broadcast_message")
    if not message_text:
        await query.answer("❌ Message not found. Please start over.", show_alert=True)
        context.user_data.clear()
        return
    await query.answer()
    await safe_edit_message_text(
        context.bot,
        chat_id=query.message.chat_id,
        message_id=query.message.message_id,
        text=(
            "📡 **Broadcasting message...**\n\n"
            "Please wait while the message is queued and delivered to all users."
        ),
        parse_mode="Markdown",
    )
    svc = get_notification_service()
    stats = await svc.broadcast_custom_message(
        context, message_text, exclude_blocked=True, admin_user_id=user_id
    )
    total_failures = (
        stats.get("blocked", 0) + stats.get("not_found", 0) + stats.get("unexpected_errors", 0)
    )
    logger.info(
        f"Admin {user_id} broadcast complete: {stats.get('sent', 0)} sent, {total_failures} failed"
    )
    context.user_data.clear()


# ---------------------------------------------------------------------------
# Catalog callbacks
# ---------------------------------------------------------------------------

async def _handle_categories(update, context, query, user_id, parts):
    from handlers.menu import show_category_menu
    await show_category_menu(update, context)


async def _handle_category(update, context, query, user_id, parts):
    if len(parts) >= 3:
        category = parts[1]
        page = int(parts[2])
        await handle_catalog_pagination(update, context, page, category)
    else:
        await query.answer("Invalid category data", show_alert=True)


async def _handle_browse_category(update, context, query, user_id, parts):
    if len(parts) >= 2:
        await show_subcategory_menu(update, context, parts[1])
    else:
        await query.answer("Invalid category data", show_alert=True)


async def _handle_subcategory(update, context, query, user_id, parts):
    if len(parts) >= 4:
        category = parts[1]
        subcategory = parts[2]
        page = int(parts[3])
        await handle_catalog_pagination(update, context, page, category, subcategory)
    else:
        await query.answer("Invalid subcategory data", show_alert=True)


async def _handle_page(update, context, query, user_id, parts):
    if len(parts) == 3:  # page|catalog|2
        state_type = parts[1]
        page = int(parts[2])
        if state_type == "catalog":
            await handle_catalog_pagination(update, context, page)
        else:
            await query.answer("Invalid pagination", show_alert=True)
    elif len(parts) == 4:  # page|search|query|2
        state_type = parts[1]
        search_query = parts[2]
        page = int(parts[3])
        if state_type == "search":
            from handlers.search import handle_search_pagination
            await handle_search_pagination(update, context, search_query, page)
        else:
            await query.answer("Invalid pagination", show_alert=True)
    else:
        await query.answer("Invalid callback data", show_alert=True)


async def _handle_menu(update, context, query, user_id, parts):
    page = int(parts[1]) if len(parts) > 1 else 1
    await show_catalog_page(update, context, page)


async def _handle_product(update, context, query, user_id, parts):
    await handle_product_callback(update, context)


@admin_required
async def _handle_delete(update, context, query, user_id, parts):
    product_id = int(parts[1])
    await delete_product(update, context, product_id)


# ---------------------------------------------------------------------------
# Nuke callbacks (admin)
# ---------------------------------------------------------------------------

async def _handle_nuke_cancel(update, context, query, user_id, parts):
    await safe_edit_message_text(
        context.bot,
        chat_id=query.message.chat_id,
        message_id=query.message.message_id,
        text="✅ Nuke cancelled. No products were deleted.",
    )


async def _handle_nuke_confirm1(update, context, query, user_id, parts):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💥 YES, NUKE EVERYTHING", callback_data="nuke_confirm2")],
        [InlineKeyboardButton("❌ Cancel", callback_data="nuke_cancel")],
    ])
    await safe_edit_message_text(
        context.bot,
        chat_id=query.message.chat_id,
        message_id=query.message.message_id,
        text=(
            "⚠️ **FINAL WARNING**\n\n"
            "This is your last chance to cancel!\n\n"
            "Are you ABSOLUTELY SURE you want to delete ALL products?\n\n"
            "Type /nuke again to start over if you're unsure."
        ),
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


@admin_required
async def _handle_nuke_confirm2(update, context, query, user_id, parts):
    try:
        async with aiosqlite.connect(db.db_path) as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM products")
            total_count = (await cursor.fetchone())[0]
        if total_count == 0:
            await safe_edit_message_text(
                context.bot,
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text="📭 The catalog is already empty. Nothing to delete.",
            )
            logger.info(f"Admin {user_id} attempted nuke on empty catalog")
            return
        logger.warning(f"Admin {user_id} executing nuke - deleting {total_count} products")
        async with aiosqlite.connect(db.db_path) as conn:
            try:
                cursor = await conn.execute("DELETE FROM products")
                await conn.commit()
                deleted_count = cursor.rowcount
                logger.info(f"Deleted {deleted_count} products from database")
                invalidate_webapp_caches("catalog nuked")
            except Exception as e:
                await conn.rollback()
                logger.error(f"Failed to delete products from database: {e}")
                raise
        text = (
            f"💥 **NUKE COMPLETE**\n\n"
            f"🗑️ Deleted {deleted_count} product(s) from database\n"
            f"📝 Channel messages remain intact (manual cleanup required)"
        )
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=text,
            parse_mode="Markdown",
        )
        logger.warning(f"Nuke complete - DB: {deleted_count}, Channel messages preserved")
    except Exception as e:
        logger.error(f"Error executing nuke: {e}", exc_info=True)
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text="❌ An error occurred while deleting products. Check logs for details.",
        )


# ---------------------------------------------------------------------------
# Categorization callbacks (admin)
# ---------------------------------------------------------------------------

@admin_required
async def _handle_setcat(update, context, query, user_id, parts):
    if len(parts) >= 3:
        product_id = int(parts[1])
        category = parts[2]
        subcategories = get_subcategories(category)
        user_lang = await db.get_user_language(user_id)
        if subcategories:
            keyboard_buttons = []
            for subcat in subcategories:
                translated_subcat = get_subcategory_display_name(subcat, user_lang)
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        translated_subcat,
                        callback_data=f"setsubcat|{product_id}|{category}|{subcat}",
                    )
                ])
            save_without_text = await get_translated_string_async(
                "save_without_subcategory", user_lang
            )
            keyboard_buttons.append([
                InlineKeyboardButton(
                    save_without_text, callback_data=f"confirmcat|{product_id}|{category}|"
                )
            ])
            keyboard_buttons.append([
                InlineKeyboardButton(
                    "🔙 Back to Categories", callback_data=f"catback|{product_id}"
                )
            ])
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
            category_key = f"category_{category.lower()}"
            translated_category = await get_translated_string_async(category_key, user_lang)
            if translated_category == category_key:
                translated_category = get_category_display_name(category)
            category_label = await get_translated_string_async(
                "category_label", user_lang, category=translated_category
            )
            select_or_save = await get_translated_string_async(
                "select_subcategory_or_save", user_lang
            )
            await safe_edit_message_text(
                context.bot,
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text=f"{category_label}\n\n{select_or_save}",
                reply_markup=keyboard,
            )
        else:
            category_key = f"category_{category.lower()}"
            translated_category = await get_translated_string_async(category_key, user_lang)
            if translated_category == category_key:
                translated_category = get_category_display_name(category)
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "✅ Confirm", callback_data=f"savecat|{product_id}|{category}|"
                )],
                [InlineKeyboardButton(
                    "🔙 Back to Categories", callback_data=f"catback|{product_id}"
                )],
            ])
            await safe_edit_message_text(
                context.bot,
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text=(
                    f"📂 Categorize Product #{product_id} as:\n\n"
                    f"**{translated_category}**\n\n"
                    f"Please confirm this categorization:"
                ),
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
    else:
        await query.answer("Invalid category data", show_alert=True)


@admin_required
async def _handle_catback(update, context, query, user_id, parts):
    if len(parts) >= 2:
        product_id = int(parts[1])
        keyboard_buttons = []
        for category in get_all_categories():
            display_name = get_category_display_name(category)
            keyboard_buttons.append([
                InlineKeyboardButton(display_name, callback_data=f"setcat|{product_id}|{category}")
            ])
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=f"📂 **Categorize Product #{product_id}**\n\nPlease select a category:",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    else:
        await query.answer("Invalid data", show_alert=True)


@admin_required
async def _handle_confirmcat(update, context, query, user_id, parts):
    if len(parts) >= 3:
        product_id = int(parts[1])
        category = parts[2]
        user_lang = await db.get_user_language(user_id)
        category_key = f"category_{category.lower()}"
        translated_category = await get_translated_string_async(category_key, user_lang)
        if translated_category == category_key:
            translated_category = get_category_display_name(category)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✅ Confirm", callback_data=f"savecat|{product_id}|{category}|"
            )],
            [InlineKeyboardButton(
                "🔙 Back", callback_data=f"setcat|{product_id}|{category}"
            )],
        ])
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=(
                f"📂 Categorize Product #{product_id} as:\n\n"
                f"**{translated_category}** (no subcategory)\n\n"
                f"Please confirm this categorization:"
            ),
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    else:
        await query.answer("Invalid data", show_alert=True)


@admin_required
async def _handle_setsubcat(update, context, query, user_id, parts):
    if len(parts) >= 4:
        product_id = int(parts[1])
        category = parts[2]
        subcategory = parts[3]
        user_lang = await db.get_user_language(user_id)
        category_key = f"category_{category.lower()}"
        translated_category = await get_translated_string_async(category_key, user_lang)
        if translated_category == category_key:
            translated_category = get_category_display_name(category)
        translated_subcat = get_subcategory_display_name(subcategory, user_lang)
        category_label = await get_translated_string_async(
            "category_label", user_lang, category=translated_category
        )
        subcategory_label = await get_translated_string_async(
            "subcategory_label", user_lang, subcategory=translated_subcat
        )
        confirm_text = await get_translated_string_async("confirm_categorization", user_lang)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✅ Confirm",
                callback_data=f"savecat|{product_id}|{category}|{subcategory}",
            )],
            [InlineKeyboardButton(
                "🔙 Back", callback_data=f"setcat|{product_id}|{category}"
            )],
        ])
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=f"{category_label}\n{subcategory_label}\n\n{confirm_text}",
            reply_markup=keyboard,
        )
    else:
        await query.answer("Invalid subcategory data", show_alert=True)


@admin_required
async def _handle_savecat(update, context, query, user_id, parts):
    if len(parts) >= 3:
        product_id = int(parts[1])
        category = parts[2]
        subcategory = parts[3] if len(parts) > 3 and parts[3] else None
        user_lang = await db.get_user_language(user_id)
        await db.update_product_category(product_id, category, subcategory)
        invalidate_webapp_caches(f"product {product_id} categorized")
        category_key = f"category_{category.lower()}"
        translated_category = await get_translated_string_async(category_key, user_lang)
        if translated_category == category_key:
            translated_category = get_category_display_name(category)
        category_text = translated_category
        if subcategory:
            translated_subcat = get_subcategory_display_name(subcategory, user_lang)
            category_text += f" • {translated_subcat}"
        success_msg = await get_translated_string_async(
            "product_categorized_successfully", user_lang, product_id=product_id
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✅ Yes, send notifications",
                callback_data=f"send_notif_yes|{product_id}",
            )],
            [InlineKeyboardButton(
                "❌ No, skip notifications",
                callback_data=f"send_notif_no|{product_id}",
            )],
        ])
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=(
                f"{success_msg}\n\n"
                f"📂 {category_text}\n\n"
                f"📢 **Send notifications to subscribed users?**\n"
                f"Choose whether to notify users about this product:"
            ),
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        logger.info(
            f"Product {product_id} categorized as {category}/{subcategory} "
            f"by admin {user_id}, awaiting notification decision"
        )
    else:
        await query.answer("Invalid save data", show_alert=True)


@admin_required
async def _handle_recategorize(update, context, query, user_id, parts):
    product_id = int(parts[1])
    keyboard_buttons = []
    for category in get_all_categories():
        display_name = get_category_display_name(category)
        keyboard_buttons.append([
            InlineKeyboardButton(display_name, callback_data=f"setcat|{product_id}|{category}")
        ])
    keyboard_buttons.append([
        InlineKeyboardButton("❌ Cancel", callback_data=f"recat_cancel|{product_id}")
    ])
    keyboard = InlineKeyboardMarkup(keyboard_buttons)
    await safe_edit_message_text(
        context.bot,
        chat_id=query.message.chat_id,
        message_id=query.message.message_id,
        text=f"📂 **Recategorize Product #{product_id}**\n\nPlease select a new category:",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


@admin_required
async def _handle_recat_cancel(update, context, query, user_id, parts):
    if len(parts) < 2:
        await query.answer("Invalid data", show_alert=True)
        return
    product_id = int(parts[1])
    await safe_edit_message_text(
        context.bot,
        chat_id=query.message.chat_id,
        message_id=query.message.message_id,
        text=f"❌ Recategorization cancelled for Product #{product_id}.",
    )


# ---------------------------------------------------------------------------
# Admin notification decision callbacks
# ---------------------------------------------------------------------------

@admin_required
async def _handle_send_notif_yes(update, context, query, user_id, parts):
    if len(parts) >= 2:
        product_id = int(parts[1])
        logger.info(f"Admin approved notifications for product {product_id}")
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=(
                f"✅ **Notifications sent!**\n\n"
                f"📢 Subscribed users are being notified about product #{product_id}.\n\n"
                f"Notifications are being sent in batches to avoid spam limits."
            ),
            parse_mode="Markdown",
        )
        svc = get_notification_service()
        asyncio.create_task(svc.notify_users_about_product(context, product_id))
        logger.info(f"Notification service triggered for product {product_id}")
    else:
        await query.answer("Invalid notification data", show_alert=True)


@admin_required
async def _handle_send_notif_no(update, context, query, user_id, parts):
    if len(parts) >= 2:
        product_id = int(parts[1])
        logger.info(f"Admin skipped notifications for product {product_id}")
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=(
                f"✅ **Categorization complete**\n\n"
                f"📂 Product #{product_id} has been categorized.\n"
                f"🔕 No notifications will be sent for this product."
            ),
            parse_mode="Markdown",
        )
    else:
        await query.answer("Invalid notification data", show_alert=True)


# ---------------------------------------------------------------------------
# User-facing notification subscription callbacks
# ---------------------------------------------------------------------------

async def _handle_toggle_notifications(update, context, query, user_id, parts):
    is_subscribed = await db.is_user_subscribed(user_id)
    if is_subscribed:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, Unsubscribe", callback_data="confirm_unsubscribe")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_unsubscribe")],
        ])
        await query.edit_message_reply_markup(reply_markup=keyboard)
        await query.answer(
            "⚠️ Are you sure you want to unsubscribe from notifications?",
            show_alert=True,
        )
    else:
        await db.set_user_notifications(user_id, True)
        try:
            unsubscribe_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 View Catalog", callback_data="categories")],
                [InlineKeyboardButton("🔕 Unsubscribe", callback_data="toggle_notifications")],
            ])
            await query.edit_message_reply_markup(reply_markup=unsubscribe_keyboard)
        except Exception as e:
            logger.debug(f"Could not edit message markup: {e}")
        await query.answer(
            "✅ You have been subscribed to notifications successfully! "
            "You will now receive updates about new products.",
            show_alert=True,
        )
        logger.info(f"User {user_id} subscribed via button")


async def _handle_confirm_unsubscribe(update, context, query, user_id, parts):
    await db.set_user_notifications(user_id, False)
    try:
        resubscribe_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 View Catalog", callback_data="categories")],
            [InlineKeyboardButton(
                "🔔 Resubscribe to Notifications", callback_data="toggle_notifications"
            )],
        ])
        await query.edit_message_reply_markup(reply_markup=resubscribe_keyboard)
    except Exception as e:
        logger.debug(f"Could not edit message markup: {e}")
    await query.answer(
        "✅ You have been unsubscribed from notifications successfully. "
        "You can resubscribe anytime using the 'Resubscribe to Notifications' "
        "button or /subscribe command.",
        show_alert=True,
    )
    logger.info(f"User {user_id} unsubscribed via button")


async def _handle_cancel_unsubscribe(update, context, query, user_id, parts):
    try:
        original_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 View Catalog", callback_data="categories")],
            [InlineKeyboardButton("🔕 Unsubscribe", callback_data="toggle_notifications")],
        ])
        await query.edit_message_reply_markup(reply_markup=original_keyboard)
    except Exception as e:
        logger.debug(f"Could not edit message markup: {e}")
    await query.answer("✅ Unsubscribe cancelled", show_alert=False)


async def _handle_unsubscribe_notifications(update, context, query, user_id, parts):
    # Legacy handler kept for backwards compatibility
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, Unsubscribe", callback_data="confirm_unsubscribe")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_unsubscribe")],
    ])
    await query.edit_message_reply_markup(reply_markup=keyboard)
    await query.answer(
        "⚠️ Are you sure you want to unsubscribe from notifications?",
        show_alert=True,
    )


# ---------------------------------------------------------------------------
# Admin notification management callbacks
# ---------------------------------------------------------------------------

@admin_required
async def _handle_toggle_notif(update, context, query, user_id, parts):
    if len(parts) >= 2:
        target_user_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 1
        bot_username = parts[3] if len(parts) > 3 else None
        is_subscribed = await db.is_user_subscribed(target_user_id)
        action_text = "disable" if is_subscribed else "enable"
        await query.answer(
            f"⚠️ Are you sure you want to {action_text} notifications for this user?",
            show_alert=True,
        )
        try:
            bot_param = f"|{bot_username}" if bot_username else ""
            confirmation_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    f"✅ Yes, {action_text.title()} Notifications",
                    callback_data=(
                        f"confirm_toggle_notif|{target_user_id}|{page}"
                        f"|{int(is_subscribed)}{bot_param}"
                    ),
                )],
                [InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data=f"cancel_toggle_notif|{page}{bot_param}",
                )],
            ])
            await query.edit_message_reply_markup(reply_markup=confirmation_keyboard)
        except Exception as e:
            logger.error(f"Error showing notification toggle confirmation: {e}")
            await db.set_user_notifications(target_user_id, not is_subscribed)
            await show_users_page(update, context, page, bot_username)
            status_text = "disabled" if is_subscribed else "enabled"
            await query.answer(
                f"✅ Notifications {status_text} for user", show_alert=False
            )
            logger.info(
                f"Admin {user_id} toggled notifications for user {target_user_id} "
                f"to {not is_subscribed}"
            )
    else:
        await query.answer("Invalid data", show_alert=True)


@admin_required
async def _handle_confirm_toggle_notif(update, context, query, user_id, parts):
    if len(parts) >= 3:
        target_user_id = int(parts[1])
        page = int(parts[2])
        was_subscribed = bool(int(parts[3])) if len(parts) > 3 else False
        bot_username = parts[4] if len(parts) > 4 else None
        await db.set_user_notifications(target_user_id, not was_subscribed)
        await show_users_page(update, context, page, bot_username)
        status_text = "disabled" if was_subscribed else "enabled"
        await query.answer(
            f"✅ Notifications {status_text} for user successfully", show_alert=True
        )
        logger.info(
            f"Admin {user_id} toggled notifications for user {target_user_id} "
            f"to {not was_subscribed}"
        )
    else:
        await query.answer("Invalid data", show_alert=True)


@admin_required
async def _handle_cancel_toggle_notif(update, context, query, user_id, parts):
    page = int(parts[1]) if len(parts) > 1 else 1
    bot_username = parts[2] if len(parts) > 2 else None
    await show_users_page(update, context, page, bot_username)
    await query.answer("✅ Toggle cancelled", show_alert=False)


# ---------------------------------------------------------------------------
# User management callbacks (admin)
# ---------------------------------------------------------------------------

@admin_required
async def _handle_users_page(update, context, query, user_id, parts):
    page = int(parts[1]) if len(parts) > 1 else 1
    bot_username = parts[2] if len(parts) > 2 else None
    await show_users_page(update, context, page, bot_username)


@admin_required
async def _handle_viewusers(update, context, query, user_id, parts):
    if len(parts) >= 2:
        bot_username = parts[1]
        page = int(parts[2]) if len(parts) > 2 else 1
        await query.answer()
        await show_users_page(update, context, page, bot_username)
    else:
        await query.answer("Invalid data", show_alert=True)


@admin_required
async def _handle_users_back_to_bots(update, context, query, user_id, parts):
    await query.answer()
    keyboard, message, message_no_md, has_bots, bot_list = await build_users_bot_selection_menu()
    if not has_bots:
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text="No bot usernames found.",
        )
        return
    try:
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=message,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    except BadRequest as e:
        logger.warning(
            f"Markdown parse error in users_back_to_bots, retrying without parse_mode: {e}"
        )
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=message_no_md,
            reply_markup=keyboard,
        )


@admin_required
async def _handle_block_user(update, context, query, user_id, parts):
    if len(parts) >= 2:
        target_user_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 1
        bot_username = parts[3] if len(parts) > 3 else None
        if is_admin(target_user_id):
            await query.answer("❌ Cannot block an admin user.", show_alert=True)
            return
        await query.answer("⚠️ Are you sure you want to block this user?", show_alert=True)
        try:
            bot_param = f"|{bot_username}" if bot_username else ""
            confirmation_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "✅ Yes, Block User",
                    callback_data=f"confirm_block|{target_user_id}|{page}{bot_param}",
                )],
                [InlineKeyboardButton(
                    "❌ Cancel", callback_data=f"cancel_block|{page}{bot_param}"
                )],
            ])
            await query.edit_message_reply_markup(reply_markup=confirmation_keyboard)
        except Exception as e:
            logger.error(f"Error showing block confirmation: {e}")
            await db.block_user(target_user_id)
            await show_users_page(update, context, page, bot_username)
            await query.answer("🚫 User blocked", show_alert=False)
            logger.info(f"Admin {user_id} blocked user {target_user_id}")
    else:
        await query.answer("Invalid data", show_alert=True)


@admin_required
async def _handle_confirm_block(update, context, query, user_id, parts):
    if len(parts) >= 2:
        target_user_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 1
        bot_username = parts[3] if len(parts) > 3 else None
        await db.block_user(target_user_id)
        await show_users_page(update, context, page, bot_username)
        await query.answer("✅ User has been blocked successfully", show_alert=True)
        logger.info(f"Admin {user_id} blocked user {target_user_id}")
    else:
        await query.answer("Invalid data", show_alert=True)


@admin_required
async def _handle_cancel_block(update, context, query, user_id, parts):
    page = int(parts[1]) if len(parts) > 1 else 1
    bot_username = parts[2] if len(parts) > 2 else None
    await show_users_page(update, context, page, bot_username)
    await query.answer("✅ Block cancelled", show_alert=False)


@admin_required
async def _handle_unblock_user(update, context, query, user_id, parts):
    if len(parts) >= 2:
        target_user_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 1
        bot_username = parts[3] if len(parts) > 3 else None
        await db.unblock_user(target_user_id)
        await show_users_page(update, context, page, bot_username)
        await query.answer("✅ User has been unblocked successfully", show_alert=True)
        logger.info(f"Admin {user_id} unblocked user {target_user_id}")
    else:
        await query.answer("Invalid data", show_alert=True)


@admin_required
async def _handle_viewbotusers(update, context, query, user_id, parts):
    if len(parts) >= 2:
        bot_username = parts[1]
        page = int(parts[2]) if len(parts) > 2 else 1
        await show_bot_users_page(update, context, bot_username, page)
    else:
        await query.answer("Invalid data", show_alert=True)


@admin_required
async def _handle_backto_botlist(update, context, query, user_id, parts):
    await query.answer()
    bot_usernames = await db.get_bot_usernames()
    keyboard_buttons = []
    for bot_username in bot_usernames:
        count = await db.get_users_count_by_bot(bot_username)
        display_text = f"@{bot_username} ({count} users)"
        keyboard_buttons.append([
            InlineKeyboardButton(display_text, callback_data=f"viewbotusers|{bot_username}|1")
        ])
    untracked_count = await db.get_users_count_by_bot("_untracked_")
    if untracked_count > 0:
        keyboard_buttons.append([
            InlineKeyboardButton(
                f"Untracked Users ({untracked_count})",
                callback_data="viewbotusers|_untracked_|1",
            )
        ])
    keyboard = InlineKeyboardMarkup(keyboard_buttons)
    total_users = await db.get_users_count_by_bot()
    await safe_edit_message_text(
        context.bot,
        chat_id=query.message.chat_id,
        message_id=query.message.message_id,
        text=(
            f"📊 **Bot Users by Instance**\n\n"
            f"Total Users: {total_users}\n"
            f"Bot Instances: {len(bot_usernames)}\n\n"
            f"Select a bot to view its users:"
        ),
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


@admin_required
async def _handle_deleteuser(update, context, query, user_id, parts):
    if len(parts) >= 4:
        target_user_id = int(parts[1])
        bot_username = parts[2]
        page = int(parts[3])
        user = await db.get_user_by_id(target_user_id)
        if not user:
            await query.answer("User not found", show_alert=True)
            return
        username = user.get("username", "N/A")
        first_name = user.get("first_name", "Unknown")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✅ Yes, Delete",
                callback_data=f"confirm_deleteuser|{target_user_id}|{bot_username}|{page}",
            )],
            [InlineKeyboardButton(
                "❌ Cancel", callback_data=f"viewbotusers|{bot_username}|{page}"
            )],
        ])
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=(
                f"⚠️ **Delete User Confirmation**\n\n"
                f"User ID: `{target_user_id}`\n"
                f"Username: @{username if username != 'N/A' else 'none'}\n"
                f"Name: {first_name}\n\n"
                f"This will completely remove the user from the database.\n"
                f"The user can restart the bot to be re-added as a fresh user.\n\n"
                f"Are you sure?"
            ),
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        await query.answer()
    else:
        await query.answer("Invalid data", show_alert=True)


@admin_required
async def _handle_confirm_deleteuser(update, context, query, user_id, parts):
    if len(parts) >= 2:
        target_user_id = int(parts[1])
        bot_username = parts[2] if len(parts) > 2 else None
        page = int(parts[3]) if len(parts) > 3 else 1
        await db.delete_user(target_user_id)
        await query.answer("✅ User deleted successfully", show_alert=True)
        logger.info(f"Admin {user_id} deleted user {target_user_id}")
        if bot_username:
            await show_bot_users_page(update, context, bot_username, page)
        else:
            await safe_edit_message_text(
                context.bot,
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text=f"✅ User `{target_user_id}` has been deleted successfully.",
                parse_mode="Markdown",
            )
    else:
        await query.answer("Invalid data", show_alert=True)


@admin_required
async def _handle_deleteallbot(update, context, query, user_id, parts):
    if len(parts) >= 2:
        bot_username = parts[1]
        if bot_username == "_untracked_":
            count = await db.get_users_count_by_bot("_untracked_")
            display_name = "Untracked Users"
        else:
            count = await db.get_users_count_by_bot(bot_username)
            display_name = f"@{bot_username}"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"{emojis.OK} Yes, Delete All",
                callback_data=f"confirm_deleteallbot|{bot_username}",
            )],
            [InlineKeyboardButton(
                f"{emojis.ERROR} Cancel",
                callback_data=f"viewbotusers|{bot_username}|1",
            )],
        ])
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=(
                f"{emojis.WARNING} **BULK DELETE WARNING**\n\n"
                f"You are about to delete **{count}** user(s) from {display_name}.\n\n"
                f"This will completely remove all these users from the database.\n"
                f"Users can restart the bot to be re-added as fresh users.\n\n"
                f"This action cannot be undone!\n\n"
                f"Are you sure?"
            ),
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        await query.answer()
    else:
        await query.answer("Invalid data", show_alert=True)


@admin_required
async def _handle_confirm_deleteallbot(update, context, query, user_id, parts):
    if len(parts) >= 2:
        bot_username = parts[1]
        deleted_count = await db.delete_users_by_bot(bot_username)
        await query.answer(f"✅ Deleted {deleted_count} users", show_alert=True)
        logger.info(f"Admin {user_id} deleted {deleted_count} users from bot {bot_username}")
        bot_usernames = await db.get_bot_usernames()
        keyboard_buttons = []
        for bot_username_item in bot_usernames:
            count = await db.get_users_count_by_bot(bot_username_item)
            display_text = f"@{bot_username_item} ({count} users)"
            keyboard_buttons.append([
                InlineKeyboardButton(
                    display_text, callback_data=f"viewbotusers|{bot_username_item}|1"
                )
            ])
        untracked_count = await db.get_users_count_by_bot("_untracked_")
        if untracked_count > 0:
            keyboard_buttons.append([
                InlineKeyboardButton(
                    f"Untracked Users ({untracked_count})",
                    callback_data="viewbotusers|_untracked_|1",
                )
            ])
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        total_users = await db.get_users_count_by_bot()
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=(
                f"📊 **Bot Users by Instance**\n\n"
                f"Total Users: {total_users}\n"
                f"Bot Instances: {len(bot_usernames)}\n\n"
                f"✅ Deleted {deleted_count} users successfully.\n\n"
                f"Select a bot to view its users:"
            ),
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    else:
        await query.answer("Invalid data", show_alert=True)


# ---------------------------------------------------------------------------
# Bot pruning callbacks (admin)
# ---------------------------------------------------------------------------

@admin_required
async def _handle_confirm_prunebots(update, context, query, user_id, parts):
    from webhook_server import get_bot_usernames
    active_bots = get_bot_usernames()
    if not active_bots:
        await query.answer("⚠️ No active bots found!", show_alert=True)
        return
    stats = await db.prune_inactive_bot_users(active_bots, dry_run=False)
    if stats["users"] == 0:
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text="✅ **No Changes Made**\n\nNo users found associated with inactive bots.",
            parse_mode="Markdown",
        )
    else:
        await safe_edit_message_text(
            context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=(
                f"✅ **Pruning Complete**\n\n"
                f"Deleted:\n"
                f"• Users: {stats['users']}\n"
                f"• Products: {stats['products']}\n"
                f"• Pending Notifications: {stats['notifications']}\n"
                f"• Custom Messages: {stats['custom_messages']}\n\n"
                f"Remaining active bots ({len(active_bots)}):\n"
                f"• " + "\n• ".join(f"@{bot}" for bot in sorted(active_bots))
            ),
            parse_mode="Markdown",
        )
        logger.info(f"Admin {user_id} pruned inactive bot users: {stats}")
    await query.answer("✅ Pruning complete", show_alert=False)


async def _handle_cancel_prunebots(update, context, query, user_id, parts):
    await safe_edit_message_text(
        context.bot,
        chat_id=query.message.chat_id,
        message_id=query.message.message_id,
        text="❌ **Pruning Cancelled**\n\nNo changes were made.",
        parse_mode="Markdown",
    )
    await query.answer("Cancelled", show_alert=False)


# ---------------------------------------------------------------------------
# Dispatch tables
# ---------------------------------------------------------------------------

# Exact-match callbacks: callback_data == key
_EXACT_HANDLERS = {
    "noop": _handle_noop,
    "broadcast_cancel": _handle_broadcast_cancel,
    "broadcast_confirm_all": _handle_broadcast_confirm_all,
    "categories": _handle_categories,
    "nuke_cancel": _handle_nuke_cancel,
    "nuke_confirm1": _handle_nuke_confirm1,
    "nuke_confirm2": _handle_nuke_confirm2,
    "open_language_settings": _handle_open_language_settings,
    "toggle_notifications": _handle_toggle_notifications,
    "confirm_unsubscribe": _handle_confirm_unsubscribe,
    "cancel_unsubscribe": _handle_cancel_unsubscribe,
    "unsubscribe_notifications": _handle_unsubscribe_notifications,
    "users_back_to_bots": _handle_users_back_to_bots,
}

# Prefix-match callbacks: callback_data.split("|")[0] == key
_PREFIX_HANDLERS = {
    "setlang": _handle_setlang,
    "setlang_start": _handle_setlang_start,
    "broadcast_confirm_single": _handle_broadcast_confirm_single,
    "setcat": _handle_setcat,
    "catback": _handle_catback,
    "confirmcat": _handle_confirmcat,
    "setsubcat": _handle_setsubcat,
    "savecat": _handle_savecat,
    "send_notif_yes": _handle_send_notif_yes,
    "send_notif_no": _handle_send_notif_no,
    "category": _handle_category,
    "browse_category": _handle_browse_category,
    "subcategory": _handle_subcategory,
    "page": _handle_page,
    "menu": _handle_menu,
    "product": _handle_product,
    "delete": _handle_delete,
    "recategorize": _handle_recategorize,
    "recat_cancel": _handle_recat_cancel,
    "toggle_notif": _handle_toggle_notif,
    "confirm_toggle_notif": _handle_confirm_toggle_notif,
    "cancel_toggle_notif": _handle_cancel_toggle_notif,
    "users_page": _handle_users_page,
    "viewusers": _handle_viewusers,
    "block_user": _handle_block_user,
    "confirm_block": _handle_confirm_block,
    "cancel_block": _handle_cancel_block,
    "unblock_user": _handle_unblock_user,
    "viewbotusers": _handle_viewbotusers,
    "backto_botlist": _handle_backto_botlist,
    "deleteuser": _handle_deleteuser,
    "confirm_deleteuser": _handle_confirm_deleteuser,
    "deleteallbot": _handle_deleteallbot,
    "confirm_deleteallbot": _handle_confirm_deleteallbot,
    "confirm_prunebots": _handle_confirm_prunebots,
    "cancel_prunebots": _handle_cancel_prunebots,
}


async def route_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query,
    callback_data: str,
    user_id: int,
) -> None:
    """
    Route a callback query to the appropriate handler.

    Validates callback_data format and dispatches to the matching handler using
    an exact-match table first, then a prefix-match (pipe-delimited) table.

    Args:
        update: Telegram Update object.
        context: PTB context.
        query: The callback_query object from the update.
        callback_data: The raw callback_data string.
        user_id: The effective user's Telegram ID.
    """
    # Input validation: reject obviously malformed or oversized callback data
    if not callback_data or len(callback_data) > _MAX_CALLBACK_DATA_LEN:
        logger.warning(
            f"Invalid callback_data from user {user_id}: "
            f"length={len(callback_data or '')}"
        )
        await query.answer("Invalid action", show_alert=True)
        return

    parts = callback_data.split("|")
    prefix = parts[0]

    # Exact match takes precedence (avoids ambiguity for bare keywords)
    handler = _EXACT_HANDLERS.get(callback_data) or _PREFIX_HANDLERS.get(prefix)

    if handler is None:
        logger.warning(f"Unknown callback_data '{callback_data}' from user {user_id}")
        await query.answer("Unknown action", show_alert=True)
        return

    answered = False
    try:
        await query.answer()
        answered = True
    except BadRequest as e:
        if "query is too old" not in str(e).lower():
            logger.debug("Callback already answered or unavailable: %s", e)
    except Exception:
        logger.debug("Callback pre-answer failed", exc_info=True)

    try:
        await handler(update, context, query, user_id, parts)
    except ValueError as e:
        logger.error(f"Invalid callback data format: {callback_data}, error: {e}")
        if not answered:
            await query.answer("Invalid action", show_alert=True)
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            logger.debug(f"Message not modified for callback {callback_data}: {e}")
            if not answered:
                await query.answer()
        else:
            logger.error(
                f"BadRequest error handling callback '{callback_data}': {e}", exc_info=True
            )
            if not answered:
                await query.answer(
                    "Unable to process this action. Please try again.", show_alert=True
                )
    except Exception as e:
        logger.error(f"Error handling callback '{callback_data}': {e}", exc_info=True)
        if not answered:
            await query.answer("An error occurred", show_alert=True)
