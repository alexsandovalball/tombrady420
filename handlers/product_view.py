"""
Product view handler.
"""
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, InputMediaAnimation, InputMediaDocument
from telegram.ext import ContextTypes
from database import Database
from configs.config import Config
from utils.deeplinks import build_webapp_product_url
from utils.helpers import send_media_message, is_admin, get_user_lang_async
from utils.safe_send import safe_send_message
from utils.media_resolver import MediaResolver, collect_product_media
from utils.pagination import paginate_items
from utils.categories import format_category_info
from translations.translator import translate_text_async, get_translated_string_async

logger = logging.getLogger(__name__)
db = Database()


def _build_media_unavailable_text(product: dict, caption: str) -> str:
    title = (caption or product.get("caption") or f"Product #{product.get('id')}").strip()
    snippet = title[:240] + ("..." if len(title) > 240 else "")
    return (
        "⚠️ Media temporarily unavailable for this bot.\n\n"
        f"Product: {snippet}\n"
        "You can still open this item from the shop or try again."
    )


def _build_admin_action_buttons(product_id: int, user_id: int) -> list[list[InlineKeyboardButton]]:
    if not is_admin(user_id):
        return []
    return [[
        InlineKeyboardButton(
            "🔄 Recategorize",
            callback_data=f"recategorize|{product_id}"
        ),
        InlineKeyboardButton(
            "🗑 Delete this product",
            callback_data=f"delete|{product_id}"
        )
    ]]


def _build_navigation_buttons(back_callback: str, back_label: str) -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(back_label, callback_data=back_callback)]]


def _build_media_fallback_keyboard(
    product_id: int,
    bot_username: str,
    navigation_buttons: list[list[InlineKeyboardButton]],
    user_id: int,
):
    buttons = []
    webapp_deep_link = build_webapp_product_url(Config.WEB_APP_URL, product_id)
    if webapp_deep_link:
        buttons.append([InlineKeyboardButton("Open in WebApp", url=webapp_deep_link)])
    if bot_username:
        buttons.append([InlineKeyboardButton("Open inline search", switch_inline_query_current_chat=str(product_id))])
    buttons.append([InlineKeyboardButton("Retry media", callback_data=f"product|{product_id}")])
    buttons.extend(_build_admin_action_buttons(product_id, user_id))
    buttons.extend(navigation_buttons)
    return InlineKeyboardMarkup(buttons)


async def show_product_media(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    """Show all product media at once (as media group if multiple files)."""
    try:
        product = await db.get_product(product_id)
        
        if not product:
            if update.callback_query:
                await update.callback_query.answer(
                    "❌ Product not found.",
                    show_alert=True
                )
            return
        
        # Get user's pagination state to determine back button behavior
        user_id = update.effective_user.id
        
        # Get the most recent pagination state to determine where user was browsing
        latest_state = await db.get_latest_pagination_state(user_id)
        
        # Determine appropriate back button based on context
        back_callback = "menu|1"  # Default fallback
        back_label = "🔙 Back to catalog"
        
        if latest_state and "state_type" in latest_state and "page" in latest_state:
            state_type = latest_state["state_type"]
            page = latest_state["page"]
            query = latest_state.get("query", "")
            
            # Check if user was browsing a subcategory
            if state_type.startswith("subcategory_"):
                # Extract category and subcategory from state_type
                # Format: subcategory_{category}_{subcategory}
                parts = state_type.split("_", 2)  # Split into at most 3 parts
                if len(parts) >= 3:
                    category = parts[1]
                    subcategory = parts[2]
                    back_callback = f"subcategory|{category}|{subcategory}|{page}"
                    back_label = "🔙 Back to subcategory"
                else:
                    # Fallback if format is unexpected
                    back_callback = "menu|1"
                    back_label = "🔙 Back to catalog"
            # Check if user was browsing a category
            elif state_type.startswith("category_"):
                # Extract category from state_type: category_{categoryname}
                parts = state_type.split("_", 1)
                if len(parts) >= 2:
                    category = parts[1]
                    back_callback = f"category|{category}|{page}"
                    back_label = "🔙 Back to category"
                else:
                    # Fallback
                    back_callback = "menu|1"
                    back_label = "🔙 Back to catalog"
            elif state_type == "search":
                # User was searching - use the query from the state
                if query:
                    # Replace pipe characters to avoid breaking callback data
                    safe_query = query.replace("|", "_PIPE_")
                    # Truncate if needed (Telegram callback_data limit is 64 bytes)
                    max_query_len = 64 - 16
                    if len(safe_query) > max_query_len:
                        safe_query = safe_query[:max_query_len]
                    back_callback = f"page|search|{safe_query}|{page}"
                    back_label = "🔙 Back to search results"
                else:
                    back_callback = "menu|1"
                    back_label = "🔙 Back to catalog"
            elif state_type == "catalog":
                # User was browsing all products
                back_callback = f"menu|{page}"
                back_label = "🔙 Back to catalog"
            else:
                # Default fallback
                back_callback = "menu|1"
                back_label = "🔙 Back to catalog"
        else:
            # No pagination state found - user likely came from notification
            # Use product's category/subcategory to determine back button
            product_category = product.get("category")
            product_subcategory = product.get("subcategory")
            
            if product_category and product_subcategory:
                # Product has both category and subcategory - go to subcategory view
                back_callback = f"subcategory|{product_category}|{product_subcategory}|1"
                back_label = "🔙 Back to subcategory"
            elif product_category:
                # Product has only category - go to category view
                back_callback = f"category|{product_category}|1"
                back_label = "🔙 Back to category"
            # else: keep default "menu|1"

        navigation_buttons = _build_navigation_buttons(back_callback, back_label)
        
        # Get user's language preference
        user_lang = await get_user_lang_async(user_id)
        
        # Get caption and translate it
        caption = product.get("caption") or "No description"
        
        # Translate caption if user language is not English
        if user_lang and user_lang not in ["en", "en-US"]:
            try:
                caption = await translate_text_async(caption, user_lang)
            except Exception as e:
                logger.error(f"Error translating caption: {e}")
                # Keep original caption if translation fails
        
        category_info = format_category_info(product.get("category"), product.get("subcategory"), user_lang)
        
        # Collect all media information using the shared parser so historical
        # products and newer media-group products resolve the same way.
        media_list = collect_product_media(product)
        if not media_list:
            logger.warning("Product %s has no deliverable media entries", product_id)
            await safe_send_message(
                context.bot,
                update.effective_chat.id,
                text=_build_media_unavailable_text(product, caption),
                parse_mode=None,
                reply_markup=_build_media_fallback_keyboard(
                    product_id,
                    context.bot.username or "",
                    navigation_buttons,
                    user_id,
                ),
            )
            return
        
        total_media = len(media_list)
        media_group_id = product.get("media_group_id")
        logger.info(
            f"show_product_media: product_id={product_id}, "
            f"raw_media_count={total_media}, "
            f"media_group_id={media_group_id}"
        )
        
        product_bot = (product.get("bot_username") or "").lower()
        current_bot = (context.bot.username or "").lower()
        resolver = MediaResolver(context.bot, db)
        resolution_results = await asyncio.gather(*[
            resolver.resolve_media_for_bot(product, current_bot, idx)
            for idx, _media in enumerate(media_list)
        ], return_exceptions=True)

        resolved_media_list = []
        resolution_failures = []
        for result, (_file_id, file_type) in zip(resolution_results, media_list):
            if isinstance(result, Exception):
                resolution_failures.append(result)
            elif result.success and result.file_id:
                resolved_media_list.append((result.file_id, file_type))
            else:
                resolution_failures.append(result)

        using_copy_path = bool(product_bot and current_bot and product_bot != current_bot)
        if resolution_failures:
            logger.warning(
                "Product media fallback used",
                extra={
                    "product_id": product_id,
                    "current_bot": current_bot,
                    "original_bot": product_bot,
                    "failures": [getattr(f, "error", str(f)) for f in resolution_failures],
                    "fallback_used": not resolved_media_list,
                    "partial_media_count": len(resolved_media_list),
                },
            )
            if not resolved_media_list:
                await safe_send_message(
                    context.bot,
                    update.effective_chat.id,
                    text=_build_media_unavailable_text(product, caption),
                    parse_mode=None,
                    reply_markup=_build_media_fallback_keyboard(
                        product_id,
                        current_bot,
                        navigation_buttons,
                        user_id,
                    ),
                )
                return

        media_list = resolved_media_list
        
        logger.debug(
            f"show_product_media: product_id={product_id}, "
            f"deduped_media_count={len(media_list)}, "
            f"copy_path={using_copy_path}"
        )
        
        # Build full caption
        full_caption = f"{caption}\n\n📂 {category_info}"
        if resolution_failures:
            full_caption += (
                f"\n\n⚠️ {len(resolution_failures)} of {total_media} media file(s) "
                "could not be delivered by this bot."
            )
        
        # Create keyboard with back button and admin buttons
        keyboard_buttons = []
        
        # Add navigation buttons
        keyboard_buttons.extend(navigation_buttons)
        
        # Add delete and recategorize buttons for admins
        keyboard_buttons.extend(_build_admin_action_buttons(product_id, user_id))
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        # Send all media at once
        if len(media_list) == 1:
            # Single media - send with caption
            file_id, file_type = media_list[0]
            await send_media_message(
                context,
                update.effective_chat.id,
                file_id,
                file_type,
                caption=full_caption,
                reply_markup=None
            )
        else:
            # Multiple media - send as media group
            try:
                media_group = []
                unsupported_media = []  # Track media that can't be in groups
                caption_used = False  # Track if caption has been added to any media
                
                # Helper to get caption (only for first media item)
                def get_caption():
                    nonlocal caption_used
                    if not caption_used:
                        caption_used = True
                        return full_caption
                    return None
                
                for idx, (file_id, file_type) in enumerate(media_list):
                    if file_type == "photo":
                        media_group.append(InputMediaPhoto(media=file_id, caption=get_caption()))
                    elif file_type == "video":
                        media_group.append(InputMediaVideo(media=file_id, caption=get_caption()))
                    elif file_type == "animation":
                        media_group.append(InputMediaAnimation(media=file_id, caption=get_caption()))
                    elif file_type == "document":
                        # Documents can be in media groups (unlike audio/voice/video_note)
                        media_group.append(InputMediaDocument(media=file_id, caption=get_caption()))
                    elif file_type in ["audio", "voice", "video_note"]:
                        # These types cannot be in media groups - send separately
                        # Caption will be added to first media item encountered
                        unsupported_media.append((file_id, file_type, get_caption()))
                    else:
                        # Unknown type - log and skip
                        logger.warning(f"Unknown media type '{file_type}' for product {product_id}, skipping")
                
                # Send media group if we have any supported media
                if media_group:
                    logger.info(
                        f"show_product_media: product_id={product_id}, "
                        f"sending media_group with {len(media_group)} items"
                    )
                    sent = await context.bot.send_media_group(
                        chat_id=update.effective_chat.id,
                        media=media_group
                    )
                    if not sent:
                        raise RuntimeError("send_media_group returned no messages")
                
                # Send unsupported media types individually
                for file_id, file_type, item_caption in unsupported_media:
                    await send_media_message(
                        context,
                        update.effective_chat.id,
                        file_id,
                        file_type,
                        caption=item_caption,
                        reply_markup=None
                    )
                    
            except Exception as e:
                logger.exception(f"Error sending media group: {e}")
                logger.info(
                    f"show_product_media: product_id={product_id}, "
                    f"using album fallback (individual send) for {len(media_list)} items"
                )
                # Fallback: send all media individually
                for idx, (file_id, file_type) in enumerate(media_list):
                    # Add caption only to first media
                    item_caption = full_caption if idx == 0 else None
                    try:
                        await send_media_message(
                            context,
                            update.effective_chat.id,
                            file_id,
                            file_type,
                            caption=item_caption,
                            reply_markup=None
                        )
                    except Exception as send_error:
                        logger.error(f"Failed to send media {idx + 1}/{total_media}: {send_error}", exc_info=True)
        
        # Get order contact
        order_contact = await db.get_order_contact()
        
        # Get translated DM to order message
        dm_message = await get_translated_string_async("dm_to_order", user_lang, contact=order_contact)
        
        # Send DM message with keyboard separately
        await safe_send_message(
            context.bot,
            update.effective_chat.id,
            text=dm_message,
            parse_mode=None,
            reply_markup=keyboard
        )

    except Exception as e:
        logger.exception(f"Error showing product media: {e}")
        if update.callback_query:
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
        await show_product_media(update, context, product_id)
    else:
        await update.callback_query.answer("Invalid callback data", show_alert=True)

